"""Dual-implementation Oracle testing for DAG workflow execution and compensation.

Notes/Architectural Intent:
    Implements differential oracle testing comparing the production AsyncioWorkflowEngine
    (concurrent async coroutine coordination, stage barriers, durable state checkpoints)
    against a simple, single-threaded NaiveSequentialDAGOracle. Mathematically validates
    that concurrent async execution produces identical step outputs, preserves topological
    dependencies, and unwinds compensations in identical reverse order.
"""

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.models import (
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    WorkflowDefinition,
)
from hexaflow.domain.state import StepContext, StepStatus, WorkflowStatus


class NaiveSequentialDAGOracle:
    """Naive reference oracle executing workflow DAGs sequentially in topological order.

    Notes/Architectural Intent:
        Executes a workflow without asyncio concurrency, split/join async synchronization,
        or complex storage layers. Serves as the ground-truth reference implementation
        for differential testing against AsyncioWorkflowEngine.
    """

    def __init__(self, initial_inputs: dict[str, Any] | None = None) -> None:
        """Initialize the oracle with starting input parameters.

        Args:
            initial_inputs: Optional dictionary of root input variables.
        """
        self.inputs: dict[str, Any] = dict(initial_inputs or {})
        self.outputs: dict[str, Any] = {}
        self.completed_steps: list[str] = []
        self.failed_step: str | None = None
        self.compensated_steps: list[str] = []
        self.status: WorkflowStatus = WorkflowStatus.PENDING

    def execute(self, workflow: WorkflowDefinition) -> WorkflowStatus:
        """Execute all workflow stages sequentially in topological order.

        Args:
            workflow: The WorkflowDefinition graph to evaluate.

        Returns:
            Terminal WorkflowStatus (COMPLETED or SUSPENDED).
        """
        self.status = WorkflowStatus.RUNNING
        for stage in workflow.stages:
            for step in stage.steps:
                # In naive sequential execution, check if dependencies completed
                for dep in step.depends_on:
                    if dep not in self.completed_steps:
                        self.status = WorkflowStatus.SUSPENDED
                        return self.status

                step_inputs = dict(self.inputs)
                for dep in step.depends_on:
                    step_inputs[dep] = self.outputs.get(dep)

                ctx = StepContext(
                    run_id="oracle-run",
                    stage_name=stage.name,
                    step_name=step.name,
                    inputs=step_inputs,
                )

                try:
                    res = step.action(ctx)
                    self.outputs[step.name] = res
                    self.completed_steps.append(step.name)
                except Exception:
                    self.failed_step = step.name
                    self.status = WorkflowStatus.SUSPENDED
                    return self.status

        self.status = WorkflowStatus.COMPLETED
        return self.status

    def compensate(self, workflow: WorkflowDefinition) -> list[str]:
        """Unwind compensations for completed steps in reverse topological order.

        Args:
            workflow: The WorkflowDefinition containing step compensation definitions.

        Returns:
            List of step names compensated in execution order.
        """
        for step_name in reversed(self.completed_steps):
            step = workflow.get_step(step_name)
            if step.compensation:
                ctx = StepContext(
                    run_id="oracle-run",
                    stage_name=workflow.get_stage_for_step(step_name).name,
                    step_name=step_name,
                    inputs={},
                )
                step.compensation(ctx)
                self.compensated_steps.append(step_name)
        self.status = WorkflowStatus.CANCELLED
        return self.compensated_steps


def _build_fuzzed_dag(
    num_stages: int,
    steps_per_stage: int,
    initial_val: int,
    fail_at_step: str | None = None,
    comp_recorder: list[str] | None = None,
    force_sequential: bool = False,
) -> tuple[WorkflowDefinition, dict[str, Any]]:
    """Construct an arbitrary multi-stage DAG with predictable step actions.

    Args:
        num_stages: Number of stages to generate (1 to 4).
        steps_per_stage: Number of steps per stage (1 to 3).
        initial_val: Seed value for numerical transformations.
        fail_at_step: Optional step name configured to raise a ValueError.
        comp_recorder: Optional shared list recording compensation calls.
        force_sequential: If True, forces all stages to SEQUENTIAL execution.

    Returns:
        Tuple of (WorkflowDefinition, initial_inputs_dict).
    """
    stages: list[StageDefinition] = []
    all_prev_step_names: list[str] = []

    for stage_idx in range(num_stages):
        stage_name = f"stage_{stage_idx + 1}"
        steps: list[StepDefinition] = []

        # Alternate stage execution mode unless forced to sequential
        if force_sequential:
            exec_mode = StageExecutionMode.SEQUENTIAL
        else:
            exec_mode = (
                StageExecutionMode.CONCURRENT_ALL
                if stage_idx % 2 == 1
                else StageExecutionMode.SEQUENTIAL
            )

        for step_idx in range(steps_per_stage):
            step_name = f"s{stage_idx + 1}_{step_idx + 1}"

            # Depend on previous steps
            deps: tuple[str, ...] = ()
            if force_sequential:
                if steps:
                    deps = (steps[-1].name,)
                elif all_prev_step_names:
                    deps = (all_prev_step_names[-1],)
            elif all_prev_step_names:
                deps = (all_prev_step_names[-1],)

            def make_action(s_name: str, multiplier: int):
                def action(ctx: StepContext) -> int:
                    if fail_at_step and s_name == fail_at_step:
                        raise ValueError(f"Injected failure at {s_name}")
                    # Sum inputs and apply multiplier
                    total = 0
                    for dep in ctx.inputs:
                        val = ctx.inputs[dep]
                        if isinstance(val, int):
                            total += val
                    return total + multiplier

                return action

            def make_compensation(s_name: str):
                if comp_recorder is None:
                    return None

                def compensate(ctx: StepContext) -> None:
                    comp_recorder.append(s_name)

                return compensate

            step = StepDefinition(
                name=step_name,
                action=make_action(step_name, (stage_idx + 1) * 10 + step_idx + 1),
                compensation=make_compensation(step_name),
                depends_on=deps,
            )
            steps.append(step)

        stages.append(
            StageDefinition(
                name=stage_name,
                steps=tuple(steps),
                execution_mode=exec_mode,
            )
        )
        all_prev_step_names.extend([s.name for s in steps])

    inputs = {"seed": initial_val}
    workflow = WorkflowDefinition(name="fuzzed_dag_oracle", stages=tuple(stages))
    return workflow, inputs


@settings(max_examples=40, deadline=None)
@given(
    num_stages=st.integers(min_value=1, max_value=4),
    steps_per_stage=st.integers(min_value=1, max_value=3),
    initial_val=st.integers(min_value=-50, max_value=50),
)
def test_dag_execution_oracle_parity(
    num_stages: int,
    steps_per_stage: int,
    initial_val: int,
) -> None:
    """Differential Oracle Invariant: AsyncioWorkflowEngine matches NaiveSequentialDAGOracle outputs."""
    workflow, inputs = _build_fuzzed_dag(
        num_stages=num_stages,
        steps_per_stage=steps_per_stage,
        initial_val=initial_val,
    )

    # 1. Execute Production AsyncioWorkflowEngine
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)
    engine_state = engine.run(workflow, initial_inputs=inputs)

    # 2. Execute Naive Sequential Oracle
    oracle = NaiveSequentialDAGOracle(initial_inputs=inputs)
    oracle_status = oracle.execute(workflow)

    # Invariant 1: Terminal status matches
    eng_status = engine_state.status
    assert eng_status == oracle_status
    assert eng_status == WorkflowStatus.COMPLETED

    # Invariant 2: Output payload equivalence for every completed step
    for step_name in oracle.completed_steps:
        eng_chk = engine_state.step_checkpoints.get(step_name)
        assert eng_chk is not None
        chk_status = eng_chk.status
        assert chk_status == StepStatus.COMPLETED
        eng_output = eng_chk.output_payload
        oracle_output = oracle.outputs[step_name]
        assert eng_output == oracle_output


@settings(max_examples=30, deadline=None)
@given(
    num_stages=st.integers(min_value=2, max_value=3),
    steps_per_stage=st.integers(min_value=1, max_value=2),
    initial_val=st.integers(min_value=1, max_value=20),
)
def test_dag_failure_and_compensation_oracle_parity(
    num_stages: int,
    steps_per_stage: int,
    initial_val: int,
) -> None:
    """Differential Oracle Invariant: Failure suspension & compensation unwinding match oracle."""
    # Target failure at the first step of stage 2
    failing_step_name = "s2_1"
    comp_recorder_engine: list[str] = []
    comp_recorder_oracle: list[str] = []

    workflow_engine, inputs = _build_fuzzed_dag(
        num_stages=num_stages,
        steps_per_stage=steps_per_stage,
        initial_val=initial_val,
        fail_at_step=failing_step_name,
        comp_recorder=comp_recorder_engine,
        force_sequential=True,
    )
    workflow_oracle, _ = _build_fuzzed_dag(
        num_stages=num_stages,
        steps_per_stage=steps_per_stage,
        initial_val=initial_val,
        fail_at_step=failing_step_name,
        comp_recorder=comp_recorder_oracle,
        force_sequential=True,
    )

    # 1. Run engine and oracle: both must suspend on failure
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)
    engine_state = engine.run(workflow_engine, initial_inputs=inputs)

    oracle = NaiveSequentialDAGOracle(initial_inputs=inputs)
    oracle_status = oracle.execute(workflow_oracle)

    eng_status = engine_state.status
    assert eng_status == WorkflowStatus.SUSPENDED
    assert oracle_status == WorkflowStatus.SUSPENDED

    # Invariant: Steps completed prior to failure match exactly
    completed_eng = [
        k for k, v in engine_state.step_checkpoints.items() if v.status == StepStatus.COMPLETED
    ]
    assert set(completed_eng) == set(oracle.completed_steps)

    # 2. Abort and unwind compensations on both
    engine_cancelled = engine.abort(engine_state.run_id, workflow_engine)
    oracle.compensate(workflow_oracle)

    cancelled_status = engine_cancelled.status
    assert cancelled_status == WorkflowStatus.CANCELLED
    assert oracle.status == WorkflowStatus.CANCELLED

    # Invariant: Compensations executed in identical reverse topological order
    assert comp_recorder_engine == comp_recorder_oracle
    assert comp_recorder_engine == list(reversed(oracle.completed_steps))


@settings(max_examples=30, deadline=None)
@given(
    num_stages=st.integers(min_value=2, max_value=3),
    steps_per_stage=st.integers(min_value=1, max_value=2),
    initial_val=st.integers(min_value=1, max_value=20),
)
def test_dag_resumption_state_oracle_parity(
    num_stages: int,
    steps_per_stage: int,
    initial_val: int,
) -> None:
    """Differential Oracle Invariant: Resumed workflow achieves exact completion parity with oracle."""
    failing_step_name = "s2_1"
    execution_counts: dict[str, int] = {}
    should_fail = True

    stages: list[StageDefinition] = []
    all_prev_step_names: list[str] = []

    for stage_idx in range(num_stages):
        stage_name = f"stage_{stage_idx + 1}"
        steps: list[StepDefinition] = []
        for step_idx in range(steps_per_stage):
            step_name = f"s{stage_idx + 1}_{step_idx + 1}"
            deps: tuple[str, ...] = ()
            if steps:
                deps = (steps[-1].name,)
            elif all_prev_step_names:
                deps = (all_prev_step_names[-1],)

            def make_resumable_action(s_name: str, mult: int):
                def action(ctx: StepContext) -> int:
                    execution_counts[s_name] = execution_counts.get(s_name, 0) + 1
                    if s_name == failing_step_name and should_fail:
                        raise ValueError(f"Injected failure at {s_name}")
                    total = 0
                    for dep in ctx.inputs:
                        val = ctx.inputs[dep]
                        if isinstance(val, int):
                            total += val
                    return total + mult

                return action

            step = StepDefinition(
                name=step_name,
                action=make_resumable_action(step_name, (stage_idx + 1) * 10 + step_idx + 1),
                depends_on=deps,
            )
            steps.append(step)

        stages.append(
            StageDefinition(
                name=stage_name,
                steps=tuple(steps),
                execution_mode=StageExecutionMode.SEQUENTIAL,
            )
        )
        all_prev_step_names.extend([s.name for s in steps])

    workflow = WorkflowDefinition(name="fuzzed_dag_resumption", stages=tuple(stages))
    inputs = {"seed": initial_val}

    # 1. Initial run: fails at failing_step_name
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)
    suspended_state = engine.run(workflow, initial_inputs=inputs)
    eng_status = suspended_state.status
    assert eng_status == WorkflowStatus.SUSPENDED

    # Record counts before resumption
    pre_counts = dict(execution_counts)

    # 2. Fix failure condition and resume
    should_fail = False
    resumed_state = engine.resume(suspended_state.run_id, workflow, patch_inputs=inputs)
    res_status = resumed_state.status
    assert res_status == WorkflowStatus.COMPLETED

    # Invariant: Steps completed prior to failure were NOT re-executed during resumption
    post_resume_counts = dict(execution_counts)
    for s_name, count in pre_counts.items():
        if s_name != failing_step_name:
            assert post_resume_counts[s_name] == count

    # 3. Oracle runs to completion from clean state
    oracle = NaiveSequentialDAGOracle(initial_inputs=inputs)
    oracle_status = oracle.execute(workflow)
    assert oracle_status == WorkflowStatus.COMPLETED

    # Invariant: Resumed engine outputs match oracle outputs for all steps
    for step_name in oracle.completed_steps:
        eng_chk = resumed_state.step_checkpoints.get(step_name)
        assert eng_chk is not None
        chk_status = eng_chk.status
        assert chk_status == StepStatus.COMPLETED
        assert eng_chk.output_payload == oracle.outputs[step_name]
