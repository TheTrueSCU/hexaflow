"""Unit tests for AsyncioWorkflowEngine.

Notes/Architectural Intent:
    Verifies forward execution, concurrent stage split/join, transient retries,
    suspension on permanent failure, checkpoint-skipping resumption, restart, and
    compensating rollbacks on abort.
"""

import pytest

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.exceptions import WorkflowError
from hexaflow.domain.models import (
    ExecutionPool,
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
)
from hexaflow.domain.retry import BackoffType, RetryPolicy
from hexaflow.domain.state import StepContext, StepStatus, WorkflowStatus


def test_sequential_workflow_golden_path() -> None:
    """Validate linear multi-stage workflow execution."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="step_1", action=lambda ctx: "data_1")
    step_2 = StepDefinition(
        name="step_2", action=lambda ctx: ctx.inputs["step_1"] + "_data_2", depends_on=("step_1",)
    )

    stage_a = StageDefinition(name="stage_a", steps=(step_1,))
    stage_b = StageDefinition(name="stage_b", steps=(step_2,))
    workflow = WorkflowDefinition(name="seq_wf", stages=(stage_a, stage_b))

    result = engine.run(workflow)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.step_checkpoints["step_1"].output_payload == "data_1"
    assert result.step_checkpoints["step_2"].output_payload == "data_1_data_2"


def test_concurrent_split_and_join_barrier() -> None:
    """Validate concurrent stage fan-out and downstream join barrier."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_root = StepDefinition(name="root", action=lambda ctx: 10)
    step_split_1 = StepDefinition(
        name="branch_1", action=lambda ctx: ctx.inputs["root"] * 2, depends_on=("root",)
    )
    step_split_2 = StepDefinition(
        name="branch_2", action=lambda ctx: ctx.inputs["root"] + 5, depends_on=("root",)
    )
    step_join = StepDefinition(
        name="join",
        action=lambda ctx: ctx.inputs["branch_1"] + ctx.inputs["branch_2"],
        depends_on=("branch_1", "branch_2"),
    )

    stage_root = StageDefinition(name="root_stage", steps=(step_root,))
    stage_split = StageDefinition(
        name="split_stage",
        steps=(step_split_1, step_split_2),
        execution_mode=StageExecutionMode.CONCURRENT_ALL,
    )
    stage_join = StageDefinition(name="join_stage", steps=(step_join,))

    wf = WorkflowDefinition(name="split_join_wf", stages=(stage_root, stage_split, stage_join))
    result = engine.run(wf)

    assert result.status == WorkflowStatus.COMPLETED
    assert (
        result.step_checkpoints["join"].output_payload == 35
    )  # (10 * 2) + (10 + 5) = 20 + 15 = 35


def test_transient_retry_success() -> None:
    """Validate that transient failures are retried and eventually succeed."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    attempts = 0

    def flaky_action(ctx: StepContext) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ConnectionError("Temporary network glitch")
        return "success_after_retry"

    step = StepDefinition(
        name="flaky_step",
        action=flaky_action,
        retry_policy=RetryPolicy(
            max_attempts=3,
            backoff_type=BackoffType.CONSTANT,
            initial_delay_seconds=0.01,
            jitter=False,
            retry_on=(ConnectionError,),
        ),
    )
    wf = WorkflowDefinition(name="flaky_wf", stages=(StageDefinition(name="stage", steps=(step,)),))

    result = engine.run(wf)
    assert result.status == WorkflowStatus.COMPLETED
    assert result.step_checkpoints["flaky_step"].output_payload == "success_after_retry"
    assert attempts == 2


def test_permanent_failure_suspends_workflow_and_resumes_without_rerun() -> None:
    """Validate that failure suspends execution, and resume() skips completed steps."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1_invocations = 0
    should_step_2_fail = True

    def action_1(ctx: StepContext) -> str:
        nonlocal step_1_invocations
        step_1_invocations += 1
        return "step_1_done"

    def action_2(ctx: StepContext) -> str:
        if should_step_2_fail:
            raise ValueError("Permanent failure in step 2")
        return "step_2_done"

    step_1 = StepDefinition(name="step_1", action=action_1)
    step_2 = StepDefinition(name="step_2", action=action_2, depends_on=("step_1",))
    step_3 = StepDefinition(name="step_3", action=lambda ctx: "step_3_done", depends_on=("step_2",))

    wf = WorkflowDefinition(
        name="resumable_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_2,)),
            StageDefinition(name="stage_3", steps=(step_3,)),
        ),
    )

    # 1. First run: fails at step 2 and suspends
    initial_res = engine.run(wf)
    assert initial_res.status == WorkflowStatus.SUSPENDED
    assert initial_res.step_checkpoints["step_1"].status == StepStatus.COMPLETED
    assert initial_res.step_checkpoints["step_2"].status == StepStatus.FAILED
    assert "step_3" not in initial_res.step_checkpoints
    assert step_1_invocations == 1

    # 2. Fix step 2 defect
    should_step_2_fail = False

    # 3. Resume the suspended run
    resumed_res = engine.resume(initial_res.run_id, wf)
    assert resumed_res.status == WorkflowStatus.COMPLETED
    assert resumed_res.step_checkpoints["step_2"].status == StepStatus.COMPLETED
    assert resumed_res.step_checkpoints["step_3"].status == StepStatus.COMPLETED

    # 4. Critical Invariant: Step 1 must NOT have been re-executed!
    final_step_1_invocations = step_1_invocations
    assert final_step_1_invocations == 1


def test_abort_unwinds_compensations_in_reverse() -> None:
    """Validate that aborting triggers step compensations in reverse completion order."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    rollbacks: list[str] = []

    def comp_1(ctx: StepContext) -> None:
        rollbacks.append(f"comp_1_{ctx.output}")

    def comp_2(ctx: StepContext) -> None:
        rollbacks.append(f"comp_2_{ctx.output}")

    step_1 = StepDefinition(name="step_1", action=lambda ctx: "val_1", compensation=comp_1)
    step_2 = StepDefinition(
        name="step_2", action=lambda ctx: "val_2", compensation=comp_2, depends_on=("step_1",)
    )
    step_3 = StepDefinition(
        name="step_3",
        action=lambda ctx: (_ for _ in ()).throw(RuntimeError("Abort trigger")),
        depends_on=("step_2",),
    )

    wf = WorkflowDefinition(
        name="compensate_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_2,)),
            StageDefinition(name="stage_3", steps=(step_3,)),
        ),
    )

    res = engine.run(wf)
    assert res.status == WorkflowStatus.SUSPENDED

    abort_res = engine.abort(res.run_id, wf)
    assert abort_res.status == WorkflowStatus.CANCELLED
    # Reverse order with step outputs: step 2 compensated before step 1
    assert rollbacks == ["comp_2_val_2", "comp_1_val_1"]

    # Abort idempotency: re-aborting does not duplicate rollbacks
    second_abort = engine.abort(res.run_id, wf)
    assert second_abort.status == WorkflowStatus.CANCELLED
    assert rollbacks == ["comp_2_val_2", "comp_1_val_1"]

    # Resume on CANCELLED run raises WorkflowError
    with pytest.raises(WorkflowError, match="Cannot resume workflow run"):
        engine.resume(res.run_id, wf)


def test_explicit_step_skipping() -> None:
    """Validate that explicitly skipped steps checkpoint as SKIPPED with 0 duration."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="step_1", action=lambda ctx: "s1_done")
    wf = WorkflowDefinition(
        name="skip_test_wf",
        stages=(StageDefinition(name="stage_1", steps=(step_1,)),),
    )

    res = engine.run(wf, skip_steps=["step_1"])
    assert res.status == WorkflowStatus.COMPLETED
    chk = res.step_checkpoints["step_1"]
    assert chk.status == StepStatus.SKIPPED
    assert chk.duration_seconds == 0.0
    assert chk.output_payload is None


def test_all_success_cascades_skip_when_parent_skipped() -> None:
    """Validate ALL_SUCCESS rule triggers cascade skip when upstream parent is skipped."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="step_1", action=lambda ctx: "s1_done")
    step_2 = StepDefinition(
        name="step_2",
        action=lambda ctx: "s2_done",
        depends_on=("step_1",),
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    wf = WorkflowDefinition(
        name="cascade_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_2,)),
        ),
    )

    res = engine.run(wf, skip_steps=["step_1"])
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["step_1"].status == StepStatus.SKIPPED
    # Cascaded skip on step_2
    assert res.step_checkpoints["step_2"].status == StepStatus.SKIPPED
    assert res.step_checkpoints["step_2"].duration_seconds == 0.0


def test_all_success_or_skipped_runs_when_parent_skipped() -> None:
    """Validate ALL_SUCCESS_OR_SKIPPED allows step to execute when upstream is skipped."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="step_1", action=lambda ctx: "s1_done")
    step_2 = StepDefinition(
        name="step_2",
        action=lambda ctx: "s2_done",
        depends_on=("step_1",),
        trigger_rule=TriggerRule.ALL_SUCCESS_OR_SKIPPED,
    )
    wf = WorkflowDefinition(
        name="pass_through_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_2,)),
        ),
    )

    res = engine.run(wf, skip_steps=["step_1"])
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["step_1"].status == StepStatus.SKIPPED
    # step_2 executes because trigger rule permits upstream skip!
    assert res.step_checkpoints["step_2"].status == StepStatus.COMPLETED
    assert res.step_checkpoints["step_2"].output_payload == "s2_done"


async def test_mapped_step_execution_async_fanout() -> None:
    """Validate async mapped step fanning out over upstream list."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="fetch", action=lambda ctx: ["alpha", "beta", "gamma"])

    async def _process(item: str) -> str:
        return item.upper()

    step_map = StepDefinition(
        name="uppercase",
        action=_process,
        depends_on=("fetch",),
        is_mapped=True,
        map_over="fetch",
    )

    wf = WorkflowDefinition(
        name="mapped_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_map,)),
        ),
    )

    res = await engine.run_async(wf)
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["uppercase"].output_payload == ["ALPHA", "BETA", "GAMMA"]
    assert "uppercase[0]" in res.step_checkpoints
    assert res.step_checkpoints["uppercase[0]"].output_payload == "ALPHA"
    assert "uppercase[1]" in res.step_checkpoints
    assert res.step_checkpoints["uppercase[1]"].output_payload == "BETA"
    assert "uppercase[2]" in res.step_checkpoints
    assert res.step_checkpoints["uppercase[2]"].output_payload == "GAMMA"


async def test_mapped_step_concurrency_limit() -> None:
    """Validate mapped step respects concurrency_limit semaphore."""
    import asyncio

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    active_count = 0
    max_active = 0

    async def _worker(item: int) -> int:
        nonlocal active_count, max_active
        active_count += 1
        max_active = max(max_active, active_count)
        await asyncio.sleep(0.02)
        active_count -= 1
        return item * 10

    step_1 = StepDefinition(name="source", action=lambda ctx: [1, 2, 3, 4, 5])
    step_map = StepDefinition(
        name="parallel_task",
        action=_worker,
        depends_on=("source",),
        is_mapped=True,
        map_over="source",
        concurrency_limit=2,
    )

    wf = WorkflowDefinition(
        name="throttled_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_map,)),
        ),
    )

    res = await engine.run_async(wf)
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["parallel_task"].output_payload == [10, 20, 30, 40, 50]
    assert max_active <= 2


def test_mapped_step_empty_collection() -> None:
    """Validate mapped step gracefully handles an empty collection."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="source", action=lambda ctx: [])
    step_map = StepDefinition(
        name="mapper",
        action=lambda item: item,
        depends_on=("source",),
        is_mapped=True,
        map_over="source",
    )

    wf = WorkflowDefinition(
        name="empty_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_map,)),
        ),
    )

    res = engine.run(wf)
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["mapper"].output_payload == []


def test_mapped_step_missing_and_non_iterable_collection() -> None:
    """Validate mapped step suspends workflow when target collection is missing or invalid."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    # Missing target
    step_missing = StepDefinition(
        name="missing_map",
        action=lambda item: item,
        is_mapped=True,
        map_over="nonexistent",
    )
    wf_missing = WorkflowDefinition(
        name="missing_wf",
        stages=(StageDefinition(name="stage_1", steps=(step_missing,)),),
    )

    res_missing = engine.run(wf_missing)
    assert res_missing.status == WorkflowStatus.SUSPENDED
    assert "not found" in (res_missing.error_summary or "")

    # Non-iterable target
    step_int = StepDefinition(name="source", action=lambda ctx: 12345)
    step_invalid = StepDefinition(
        name="invalid_map",
        action=lambda item: item,
        depends_on=("source",),
        is_mapped=True,
        map_over="source",
    )
    wf_invalid = WorkflowDefinition(
        name="invalid_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_int,)),
            StageDefinition(name="stage_2", steps=(step_invalid,)),
        ),
    )

    res_invalid = engine.run(wf_invalid)
    assert res_invalid.status == WorkflowStatus.SUSPENDED
    assert "not iterable" in (res_invalid.error_summary or "")


def test_mapped_step_retry_and_resumption() -> None:
    """Validate sub-step retry policy and resumption skipping completed sub-steps."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    attempts = {"item_1": 0, "item_2": 0}

    def _flaky_worker(item: str) -> str:
        attempts[item] += 1
        if item == "item_2" and attempts[item] == 1:
            raise ValueError("Transient glitch on item_2")
        return f"{item}_processed"

    policy = RetryPolicy(
        max_attempts=3, backoff_type=BackoffType.CONSTANT, initial_delay_seconds=0.01
    )

    step_1 = StepDefinition(name="source", action=lambda ctx: ["item_1", "item_2"])
    step_map = StepDefinition(
        name="processor",
        action=_flaky_worker,
        depends_on=("source",),
        is_mapped=True,
        map_over="source",
        retry_policy=policy,
    )

    wf = WorkflowDefinition(
        name="retry_map_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_map,)),
        ),
    )

    res = engine.run(wf)
    assert res.status == WorkflowStatus.COMPLETED
    assert attempts["item_2"] == 2
    assert res.step_checkpoints["processor"].output_payload == [
        "item_1_processed",
        "item_2_processed",
    ]


def test_mapped_step_suspension_and_resumption() -> None:
    """Validate mapped step suspends on permanent sub-step failure and skips completed items on resume."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    execution_counts = {"item_1": 0, "item_2": 0}
    should_fail_item_2 = True

    def _worker(item: str) -> str:
        nonlocal should_fail_item_2
        execution_counts[item] += 1
        if item == "item_2" and should_fail_item_2:
            raise RuntimeError("Permanent error on item_2")
        return f"{item}_ok"

    step_1 = StepDefinition(name="source", action=lambda ctx: ["item_1", "item_2"])
    step_map = StepDefinition(
        name="processor",
        action=_worker,
        depends_on=("source",),
        is_mapped=True,
        map_over="source",
    )

    wf = WorkflowDefinition(
        name="resume_map_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_map,)),
        ),
    )

    # First run fails and suspends
    res_1 = engine.run(wf)
    assert res_1.status == WorkflowStatus.SUSPENDED
    assert "item_2" in (res_1.error_summary or "")
    run_id = res_1.run_id

    # Verify item_1 completed and checkpoint was persisted
    chk_1 = store.get_checkpoint(run_id, "processor[0]")
    assert chk_1 is not None
    assert chk_1.status == StepStatus.COMPLETED
    assert execution_counts["item_1"] == 1

    # Fix error and resume
    should_fail_item_2 = False
    res_2 = engine.resume(run_id, wf)
    assert res_2.status == WorkflowStatus.COMPLETED
    # item_1 was skipped on resume and was NOT executed again
    assert execution_counts["item_1"] == 1
    # item_2 was executed on resume
    assert execution_counts["item_2"] == 2
    assert res_2.step_checkpoints["processor"].output_payload == ["item_1_ok", "item_2_ok"]


def test_mapped_step_signature_binding_variants() -> None:
    """Validate argument binding across various callable signatures."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_src = StepDefinition(name="src", action=lambda ctx: [10, 20])

    # Variant 1: (item, ctx)
    step_v1 = StepDefinition(
        name="v1",
        action=lambda item, ctx: f"{item}@{ctx.stage_name}",
        depends_on=("src",),
        is_mapped=True,
        map_over="src",
    )

    # Variant 2: (ctx, item)
    step_v2 = StepDefinition(
        name="v2",
        action=lambda ctx, item: f"{ctx.step_name}:{item}",
        depends_on=("src",),
        is_mapped=True,
        map_over="src",
    )

    # Variant 3: (ctx) only
    step_v3 = StepDefinition(
        name="v3",
        action=lambda ctx: ctx.inputs["item"] + 1,
        depends_on=("src",),
        is_mapped=True,
        map_over="src",
    )

    # Variant 4: () no args
    step_v4 = StepDefinition(
        name="v4",
        action=lambda: "fixed",
        depends_on=("src",),
        is_mapped=True,
        map_over="src",
    )

    # Variant 5: (item, other_param)
    step_v5 = StepDefinition(
        name="v5",
        action=lambda item, custom_val: f"{item}_{custom_val}",
        depends_on=("src",),
        is_mapped=True,
        map_over="src",
    )

    wf = WorkflowDefinition(
        name="variants_wf",
        stages=(
            StageDefinition(
                name="stage_all",
                steps=(step_src, step_v1, step_v2, step_v3, step_v4, step_v5),
            ),
        ),
    )

    res = engine.run(wf, initial_inputs={"custom_val": "hello"})
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["v1"].output_payload == ["10@stage_all", "20@stage_all"]
    assert res.step_checkpoints["v2"].output_payload == ["v2[0]:10", "v2[1]:20"]
    assert res.step_checkpoints["v3"].output_payload == [11, 21]
    assert res.step_checkpoints["v4"].output_payload == ["fixed", "fixed"]
    assert res.step_checkpoints["v5"].output_payload == ["10_hello", "20_hello"]


async def test_mapped_step_timeout_suspends() -> None:
    """Validate mapped sub-step timeout triggers suspension."""
    import asyncio

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    async def _sleepy_worker(item: int) -> int:
        await asyncio.sleep(0.5)
        return item

    step_1 = StepDefinition(name="src", action=lambda ctx: [1, 2])
    step_map = StepDefinition(
        name="slow_task",
        action=_sleepy_worker,
        depends_on=("src",),
        is_mapped=True,
        map_over="src",
        timeout_seconds=0.01,
    )

    wf = WorkflowDefinition(
        name="timeout_wf",
        stages=(
            StageDefinition(name="stage_1", steps=(step_1,)),
            StageDefinition(name="stage_2", steps=(step_map,)),
        ),
    )

    res = await engine.run_async(wf)
    status = res.status
    assert status == WorkflowStatus.SUSPENDED
    summary = res.error_summary or ""
    assert "TimeoutError" in summary


def _standalone_process_step(ctx: StepContext) -> str:
    """Helper for testing step execution in child process.

    Args:
        ctx: Current step context.

    Returns:
        Formatted execution string.
    """
    return f"processed_{ctx.step_name}"


def _standalone_square(item: int) -> int:
    """Helper for testing mapped step execution in child process.

    Args:
        item: Input integer.

    Returns:
        Squared integer.
    """
    return item * item


def test_step_execution_in_thread_and_process_pools() -> None:
    """Validate step execution dispatched to ThreadPoolExecutor and ProcessPoolExecutor.

    Notes/Architectural Intent:
        Guarantees that steps configured with ExecutionPool.THREAD or ExecutionPool.PROCESS
        execute correctly outside the asyncio event loop and return output payloads.
    """
    store = InMemoryStateStore()

    step_thread = StepDefinition(
        name="thread_step",
        action=lambda ctx: f"threaded_{ctx.step_name}",
        pool=ExecutionPool.THREAD,
    )
    step_process = StepDefinition(
        name="proc_step",
        action=_standalone_process_step,
        pool=ExecutionPool.PROCESS,
    )

    stage = StageDefinition(
        name="pool_stage",
        steps=(step_thread, step_process),
        execution_mode=StageExecutionMode.CONCURRENT_ALL,
    )
    wf = WorkflowDefinition(name="pools_wf", stages=(stage,))

    with AsyncioWorkflowEngine(
        state_store=store, max_process_workers=2, max_thread_workers=2
    ) as engine:
        res = engine.run(wf)
        status = res.status
        assert status == WorkflowStatus.COMPLETED

        out_thread = res.step_checkpoints["thread_step"].output_payload
        assert out_thread == "threaded_thread_step"

        out_proc = res.step_checkpoints["proc_step"].output_payload
        assert out_proc == "processed_proc_step"


def test_mapped_step_execution_in_process_pool() -> None:
    """Validate mapped steps scaling across ProcessPoolExecutor workers.

    Notes/Architectural Intent:
        Ensures parallel fan-out over items using child processes computes and
        aggregates results cleanly.
    """
    store = InMemoryStateStore()

    step_src = StepDefinition(name="numbers", action=lambda ctx: [2, 4, 6])
    step_map = StepDefinition(
        name="squared",
        action=_standalone_square,
        depends_on=("numbers",),
        is_mapped=True,
        map_over="numbers",
        pool=ExecutionPool.PROCESS,
    )

    stage_1 = StageDefinition(name="src_stage", steps=(step_src,))
    stage_2 = StageDefinition(name="map_stage", steps=(step_map,))
    wf = WorkflowDefinition(name="mapped_proc_wf", stages=(stage_1, stage_2))

    with AsyncioWorkflowEngine(state_store=store, max_process_workers=2) as engine:
        res = engine.run(wf)
        status = res.status
        assert status == WorkflowStatus.COMPLETED
        payload = res.step_checkpoints["squared"].output_payload
        assert payload == [4, 16, 36]


async def test_engine_pools_lifecycle_and_context_managers() -> None:
    """Validate engine context managers and pool lifecycle shutdown.

    Notes/Architectural Intent:
        Verifies that with/async with blocks properly instantiate and tear down
        underlying thread and process worker pools without resource leaks.
    """
    store = InMemoryStateStore()

    with AsyncioWorkflowEngine(state_store=store, max_process_workers=2) as eng_sync:
        pool_p = eng_sync._get_process_pool()
        assert pool_p is not None

    async with AsyncioWorkflowEngine(state_store=store, max_thread_workers=2) as eng_async:
        pool_t = eng_async._get_thread_pool()
        assert pool_t is not None


def test_engine_sentry_step_failure_hook(monkeypatch) -> None:
    """Verify engine pushes step failure metadata to sentry_sdk when active."""
    import sys
    from unittest.mock import MagicMock

    mock_sentry = MagicMock()
    mock_scope = MagicMock()
    mock_sentry.push_scope.return_value.__enter__.return_value = mock_scope
    monkeypatch.setitem(sys.modules, "sentry_sdk", mock_sentry)

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    def failing_action(ctx):
        raise RuntimeError("simulated pipeline error")

    step_fail = StepDefinition(name="failing_step", action=failing_action)
    stage = StageDefinition(name="fail_stage", steps=(step_fail,))
    wf = WorkflowDefinition(name="sentry_test_wf", stages=(stage,))

    res = engine.run(wf)
    status = res.status
    assert status == WorkflowStatus.SUSPENDED

    mock_scope.set_tag.assert_any_call("workflow_name", "sentry_test_wf")
    mock_scope.set_tag.assert_any_call("stage_name", "fail_stage")
    mock_scope.set_tag.assert_any_call("step_name", "failing_step")
    assert mock_sentry.capture_exception.called


def test_engine_resume_and_abort_missing_run_and_input_merging() -> None:
    """Validate exceptions when resuming or aborting non-existent runs, and input merging."""
    from hexaflow.domain.exceptions import WorkflowAborted, WorkflowSuspended

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(name="step_1", action=lambda ctx: ctx.inputs)
    wf = WorkflowDefinition(
        name="merge_inputs_wf",
        stages=(StageDefinition(name="stage_1", steps=(step_1,)),),
    )

    with pytest.raises(WorkflowSuspended, match="not found in state store"):
        engine.resume("missing_run_id", wf)

    with pytest.raises(WorkflowAborted, match="not found"):
        engine.abort("missing_run_id", wf)

    # Initial inputs merged with patch_inputs
    res = engine.run(wf, initial_inputs={"base": 1, "override": 2})
    assert res.status == WorkflowStatus.COMPLETED

    # Reset state to SUSPENDED to simulate resumption with patch
    state = store.get_run(res.run_id)
    assert state is not None
    state.status = WorkflowStatus.SUSPENDED
    store.save_run(state)
    store.clear_checkpoints(res.run_id)

    resumed = engine.resume(res.run_id, wf, patch_inputs={"override": 99, "new_key": 42})
    assert resumed.status == WorkflowStatus.COMPLETED
    output = resumed.step_checkpoints["step_1"].output_payload
    assert output == {"base": 1, "override": 99, "new_key": 42}


def test_concurrent_stage_step_failure() -> None:
    """Validate exception handling and sibling settling in concurrent stage execution."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_1 = StepDefinition(
        name="s1",
        action=lambda ctx: (_ for _ in ()).throw(RuntimeError("concurrent fail")),
    )
    step_2 = StepDefinition(name="s2", action=lambda ctx: "s2_ok")

    stage = StageDefinition(
        name="conc_stage",
        steps=(step_1, step_2),
        execution_mode=StageExecutionMode.CONCURRENT_ALL,
    )
    wf = WorkflowDefinition(name="fail_conc_wf", stages=(stage,))

    res = engine.run(wf)
    assert res.status == WorkflowStatus.SUSPENDED
    assert "concurrent fail" in (res.error_summary or "")
    assert "s2" in res.step_checkpoints
    assert res.step_checkpoints["s2"].status == StepStatus.COMPLETED


def test_concurrent_stage_returned_exception_object() -> None:
    """Validate returning an Exception instance from a step is treated as a valid output value."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    returned_err = ValueError("returned value, not raised")
    step_1 = StepDefinition(name="s1", action=lambda ctx: returned_err)
    step_2 = StepDefinition(name="s2", action=lambda ctx: "s2_ok")

    stage = StageDefinition(
        name="conc_return_stage",
        steps=(step_1, step_2),
        execution_mode=StageExecutionMode.CONCURRENT_ALL,
    )
    wf = WorkflowDefinition(name="return_exc_wf", stages=(stage,))

    res = engine.run(wf)
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["s1"].output_payload == returned_err


def test_initial_inputs_deepcopied_isolation() -> None:
    """Validate step mutations on inputs do not mutate the persisted initial_inputs."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    def _mutate(ctx: StepContext) -> str:
        ctx.inputs["nested"]["counter"] += 10
        return "done"

    step_1 = StepDefinition(name="s1", action=_mutate)
    stage = StageDefinition(name="stage1", steps=(step_1,))
    wf = WorkflowDefinition(name="mutate_wf", stages=(stage,))

    init = {"nested": {"counter": 1}}
    res = engine.run(wf, initial_inputs=init)
    assert res.status == WorkflowStatus.COMPLETED
    assert res.initial_inputs["nested"]["counter"] == 1


def test_restart_handles_corrupt_checkpoint_store(monkeypatch) -> None:
    """Validate restart clears checkpoints even if get_run raises CheckpointCorruptError."""
    from hexaflow.domain.exceptions import CheckpointCorruptError

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    wf = WorkflowDefinition(
        name="restart_corrupt_wf",
        stages=(
            StageDefinition(name="stg", steps=(StepDefinition(name="s1", action=lambda ctx: 42),)),
        ),
    )
    res = engine.run(wf)
    assert res.status == WorkflowStatus.COMPLETED

    def _corrupt_get_run(run_id: str):
        raise CheckpointCorruptError("Simulated corruption")

    monkeypatch.setattr(store, "get_run", _corrupt_get_run)
    restarted = engine.restart(res.run_id, wf)
    assert restarted.status == WorkflowStatus.COMPLETED


def test_dry_run_simulation_safe_and_mocked() -> None:
    """Validate that dry-run mode executes safe steps and uses mocks for side effects."""

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    executed_side_effect = False

    def real_side_effect(ctx: StepContext) -> str:
        nonlocal executed_side_effect
        executed_side_effect = True
        return "real_db_write"

    step_read = StepDefinition(name="read_data", action=lambda ctx: {"items": [1, 2]})
    step_write = StepDefinition(
        name="write_db",
        action=real_side_effect,
        depends_on=("read_data",),
        side_effects=True,
        dry_run="mock_db_result",
    )

    stage = StageDefinition(name="pipeline", steps=(step_read, step_write))
    wf = WorkflowDefinition(name="dry_run_wf", stages=(stage,))

    res = engine.run(wf, dry_run=True)
    assert res.status == WorkflowStatus.COMPLETED
    assert executed_side_effect is False
    assert res.step_checkpoints["write_db"].output_payload == "mock_db_result"


def test_dry_run_unsafe_step_raises_error() -> None:
    """Validate that dry-run aborts if a side-effect step lacks dry_run mock without allow_unsafe."""

    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_unsafe = StepDefinition(
        name="delete_table",
        action=lambda ctx: "deleted",
        side_effects=True,
        dry_run=None,
    )
    stage = StageDefinition(name="stage1", steps=(step_unsafe,))
    wf = WorkflowDefinition(name="unsafe_wf", stages=(stage,))

    res = engine.run(wf, dry_run=True)
    assert res.status == WorkflowStatus.SUSPENDED
    assert "DryRunUnsafeStepError" in (res.error_summary or "")


def test_dry_run_allow_unsafe_flag() -> None:
    """Validate that allow_unsafe=True permits executing side effects in dry-run mode."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    executed = False

    def action_fn(ctx: StepContext) -> str:
        nonlocal executed
        executed = True
        return "executed"

    step = StepDefinition(name="step_unsafe", action=action_fn, side_effects=True, dry_run=None)
    stage = StageDefinition(name="s", steps=(step,))
    wf = WorkflowDefinition(name="allow_unsafe_wf", stages=(stage,))

    res = engine.run(wf, dry_run=True, allow_unsafe=True)
    assert res.status == WorkflowStatus.COMPLETED
    assert executed is True


def test_fault_injection_simulation() -> None:
    """Validate fault injection triggers synthetic failures during execution."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step1 = StepDefinition(name="s1", action=lambda ctx: "s1_ok")
    step2 = StepDefinition(name="s2", action=lambda ctx: "s2_ok", depends_on=("s1",))
    stage = StageDefinition(name="stg", steps=(step1, step2))
    wf = WorkflowDefinition(name="fault_wf", stages=(stage,))

    injected = RuntimeError("Synthetic chaos failure")
    res = engine.run(wf, fault_injection={"s1": injected})

    assert res.status == WorkflowStatus.SUSPENDED
    assert res.step_checkpoints["s1"].status == StepStatus.FAILED
    assert "Synthetic chaos failure" in (res.error_summary or "")


def test_engine_rewind_and_replay() -> None:
    """Validate engine rewind invalidates target step and downstream dependencies and replays."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    step_counts: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}

    def fn1(ctx: StepContext) -> str:
        step_counts["s1"] += 1
        return "val_s1"

    def fn2(ctx: StepContext) -> str:
        step_counts["s2"] += 1
        return f"{ctx.inputs['s1']}_val_s2"

    def fn3(ctx: StepContext) -> str:
        step_counts["s3"] += 1
        return f"{ctx.inputs['s2']}_val_s3"

    s1 = StepDefinition(name="s1", action=fn1)
    s2 = StepDefinition(name="s2", action=fn2, depends_on=("s1",))
    s3 = StepDefinition(name="s3", action=fn3, depends_on=("s2",))

    stage = StageDefinition(name="stg", steps=(s1, s2, s3))
    wf = WorkflowDefinition(name="rewind_test_wf", stages=(stage,))

    initial_res = engine.run(wf)
    assert initial_res.status == WorkflowStatus.COMPLETED
    assert step_counts == {"s1": 1, "s2": 1, "s3": 1}

    # Rewind to s2: s1 should remain cached, while s2 and s3 are replayed
    rewound_res = engine.rewind(initial_res.run_id, wf, to_step="s2")
    assert rewound_res.status == WorkflowStatus.COMPLETED
    assert step_counts["s1"] == 1  # Unchanged / skipped
    assert step_counts["s2"] == 2  # Re-executed
    assert step_counts["s3"] == 2  # Re-executed downstream
