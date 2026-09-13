"""Unit tests for AsyncioWorkflowEngine.

Notes/Architectural Intent:
    Verifies forward execution, concurrent stage split/join, transient retries,
    suspension on permanent failure, checkpoint-skipping resumption, restart, and
    compensating rollbacks on abort.
"""

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.models import (
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
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
