"""Property-based fuzzing for hexaflow DAG validation and resumption invariants.

Notes/Architectural Intent:
    Uses Hypothesis to generate arbitrary directed acyclic graphs and failure injections,
    mathematically proving that topological sorting invariants hold and that completed steps
    are strictly idempotent across resumption boundaries.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.models import StageDefinition, StepDefinition, WorkflowDefinition
from hexaflow.domain.state import StepContext, StepStatus, WorkflowStatus


@settings(max_examples=50, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=5),  # stage_id
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=3, max_size=8),  # step_name
        ),
        min_size=2,
        max_size=10,
        unique_by=lambda x: x[1],  # unique step names
    )
)
def test_topological_order_invariant(step_tuples: list[tuple[int, str]]) -> None:
    """Property Invariant: For any valid DAG, parent steps always precede children in topological order."""
    # Build linear dependency chain to guarantee acyclicity
    stages_dict: dict[int, list[StepDefinition]] = {}
    prev_name: str | None = None

    for stage_id, name in step_tuples:
        deps = (prev_name,) if prev_name else ()
        step = StepDefinition(name=name, action=lambda ctx: None, depends_on=deps)
        stages_dict.setdefault(stage_id, []).append(step)
        prev_name = name

    stages = [StageDefinition(name=f"stage_{sid}", steps=tuple(steps)) for sid, steps in sorted(stages_dict.items())]
    workflow = WorkflowDefinition(name="fuzzed_workflow", stages=tuple(stages))

    order = workflow.topological_order()
    step_names = [t[1] for t in step_tuples]

    # Every step must appear in topological order
    assert set(order) == set(step_names)

    # Invariant: Every step must appear AFTER its dependencies
    dep_map = workflow.dependencies_map()
    for step_name, deps in dep_map.items():
        step_idx = order.index(step_name)
        for dep in deps:
            dep_idx = order.index(dep)
            assert dep_idx < step_idx


@settings(max_examples=25, deadline=None)
@given(st.integers(min_value=1, max_value=4))
def test_resumption_never_reexecutes_completed_steps(fail_step_idx: int) -> None:
    """Property Invariant: Resuming a suspended workflow never re-executes already-completed steps."""
    store = InMemoryStateStore()
    engine = AsyncioWorkflowEngine(state_store=store)

    execution_counts: dict[str, int] = {f"step_{i}": 0 for i in range(1, 6)}
    should_fail = True

    def make_action(step_name: str, step_num: int):
        def action(ctx: StepContext):
            nonlocal execution_counts, should_fail
            execution_counts[step_name] += 1
            if step_num == fail_step_idx and should_fail:
                raise ValueError(f"Injected failure at {step_name}")
            return f"{step_name}_done"

        return action

    steps = [
        StepDefinition(
            name=f"step_{i}",
            action=make_action(f"step_{i}", i),
            depends_on=(f"step_{i-1}",) if i > 1 else (),
        )
        for i in range(1, 6)
    ]

    wf = WorkflowDefinition(
        name="idempotent_fuzz_wf",
        stages=(StageDefinition(name="single_stage", steps=tuple(steps)),),
    )

    # 1. Initial run: should fail at fail_step_idx
    res = engine.run(wf)
    assert res.status == WorkflowStatus.SUSPENDED

    # Steps prior to failure must have completed exactly once
    for i in range(1, fail_step_idx):
        assert execution_counts[f"step_{i}"] == 1
        assert res.step_checkpoints[f"step_{i}"].status == StepStatus.COMPLETED

    # 2. Fix failure and resume
    should_fail = False
    resumed = engine.resume(res.run_id, wf)
    assert resumed.status == WorkflowStatus.COMPLETED

    # CRITICAL INVARIANT: Steps prior to failure must STILL have execution count == 1!
    for i in range(1, fail_step_idx):
        assert execution_counts[f"step_{i}"] == 1
