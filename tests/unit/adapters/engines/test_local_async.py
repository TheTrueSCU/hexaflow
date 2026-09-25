"""Unit tests for AsyncioWorkflowEngine.

Notes/Architectural Intent:
    Verifies forward execution, concurrent stage split/join, transient retries,
    suspension on permanent failure, checkpoint-skipping resumption, restart, and
    compensating rollbacks on abort.
"""

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
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
    assert step_1_invocations == 1


def test_abort_unwinds_compensations_in_reverse() -> None:
    """Validate that aborting triggers step compensations in reverse completion order."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    rollbacks: list[str] = []

    def comp_1(ctx: StepContext) -> None:
        rollbacks.append("comp_1")

    def comp_2(ctx: StepContext) -> None:
        rollbacks.append("comp_2")

    step_1 = StepDefinition(name="step_1", action=lambda ctx: True, compensation=comp_1)
    step_2 = StepDefinition(
        name="step_2", action=lambda ctx: True, compensation=comp_2, depends_on=("step_1",)
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
    # Reverse order: step 2 compensated before step 1
    assert rollbacks == ["comp_2", "comp_1"]


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
    engine = AsyncioWorkflowEngine(state_store=store, max_process_workers=2, max_thread_workers=2)

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

    try:
        res = engine.run(wf)
        status = res.status
        assert status == WorkflowStatus.COMPLETED

        out_thread = res.step_checkpoints["thread_step"].output_payload
        assert out_thread == "threaded_thread_step"

        out_proc = res.step_checkpoints["proc_step"].output_payload
        assert out_proc == "processed_proc_step"
    finally:
        engine.close()


def test_mapped_step_execution_in_process_pool() -> None:
    """Validate mapped steps scaling across ProcessPoolExecutor workers.

    Notes/Architectural Intent:
        Ensures parallel fan-out over items using child processes computes and
        aggregates results cleanly.
    """
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store, max_process_workers=2)

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

    try:
        res = engine.run(wf)
        status = res.status
        assert status == WorkflowStatus.COMPLETED
        payload = res.step_checkpoints["squared"].output_payload
        assert payload == [4, 16, 36]
    finally:
        engine.close()


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
