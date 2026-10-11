"""Tests for WorkflowGraph and GraphNode structural graph analysis.

Notes/Architectural Intent:
    Verifies that WorkflowGraph faithfully models DAG dependencies, computes
    ancestors and descendants, performs blast-radius failure simulations,
    and identifies critical paths and roots/leaves.
"""

import pytest

from hexaflow.domain.exceptions import StepNotFoundError
from hexaflow.domain.graph import (
    WorkflowGraph,
)
from hexaflow.domain.models import (
    ExecutionPool,
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
)
from hexaflow.domain.state import StepStatus


def dummy_action() -> str:
    """Dummy action for step testing."""
    return "ok"


def create_sample_workflow() -> WorkflowDefinition:
    """Helper to build a sample multi-stage DAG workflow."""
    s1 = StepDefinition(name="step_a", action=dummy_action, estimated_duration_seconds=2.0)
    s2 = StepDefinition(
        name="step_b",
        action=dummy_action,
        depends_on=("step_a",),
        estimated_duration_seconds=5.0,
    )
    s3 = StepDefinition(
        name="step_c",
        action=dummy_action,
        depends_on=("step_a",),
        estimated_duration_seconds=3.0,
    )
    s4 = StepDefinition(
        name="step_d",
        action=dummy_action,
        depends_on=("step_b", "step_c"),
        trigger_rule=TriggerRule.ALL_SUCCESS,
        side_effects=True,
        estimated_duration_seconds=4.0,
        pool=ExecutionPool.THREAD,
    )

    stage1 = StageDefinition(name="prep", steps=(s1,))
    stage2 = StageDefinition(name="compute", steps=(s2, s3))
    stage3 = StageDefinition(
        name="finalize", steps=(s4,), execution_mode=StageExecutionMode.SEQUENTIAL
    )

    return WorkflowDefinition(
        name="sample_pipeline",
        version="1.2.0",
        stages=(stage1, stage2, stage3),
    )


def test_workflow_graph_construction() -> None:
    """Test that WorkflowGraph compiles stages and nodes correctly."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)

    wf_id = graph.workflow_id
    assert wf_id == "sample_pipeline"

    node_count = len(graph.nodes)
    assert node_count == 4

    topo = graph.topological_order
    assert topo[0] == "step_a"
    assert topo[-1] == "step_d"

    edges = graph.edges
    expected_edges = [
        ("step_a", "step_b"),
        ("step_a", "step_c"),
        ("step_b", "step_d"),
        ("step_c", "step_d"),
    ]
    for edge in expected_edges:
        assert edge in edges


def test_graph_node_properties() -> None:
    """Test GraphNode convenience properties and aliases."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)
    node = graph.get_node("step_d")

    step_id = node.step_id
    assert step_id == "step_d"

    step_def = node.step
    assert step_def.name == "step_d"

    rule = node.trigger_rule
    assert rule == TriggerRule.ALL_SUCCESS

    side_eff = node.side_effects
    assert side_eff is True

    crit = node.is_critical_path
    assert crit is False


def test_ancestors_and_descendants() -> None:
    """Test ancestor and descendant reachability calculations."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)

    anc_d = graph.ancestors("step_d")
    assert anc_d == {"step_a", "step_b", "step_c"}

    anc_a = graph.ancestors("step_a")
    assert anc_a == set()

    desc_a = graph.descendants("step_a")
    assert desc_a == {"step_b", "step_c", "step_d"}

    desc_d = graph.descendants("step_d")
    assert desc_d == set()

    succ = graph.successors("step_a")
    assert succ == ["step_b", "step_c"]

    pred = graph.predecessors("step_d")
    assert pred == ["step_b", "step_c"]


def test_get_node_not_found() -> None:
    """Test that get_node raises StepNotFoundError when given an unknown step."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)

    with pytest.raises(StepNotFoundError) as exc_info:
        graph.get_node("unknown_step")
    assert "unknown_step" in str(exc_info.value)

    with pytest.raises(StepNotFoundError):
        graph.ancestors("missing")

    with pytest.raises(StepNotFoundError):
        graph.descendants("missing")


def test_blast_radius_failure_cascade() -> None:
    """Test blast radius computation when a parent fails."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)

    # If step_b fails, step_d (which has ALL_SUCCESS and depends on b and c) should be impacted
    impacted = graph.blast_radius("step_b", StepStatus.FAILED)
    assert impacted == {"step_d"}

    # If step_a fails, both step_b, step_c, and subsequently step_d should be impacted
    impacted_all = graph.blast_radius("step_a", StepStatus.FAILED)
    assert impacted_all == {"step_b", "step_c", "step_d"}


def test_critical_path_and_duration() -> None:
    """Test critical path calculation based on estimated_duration_seconds."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)

    # step_a (2s) -> step_b (5s) -> step_d (4s) = 11s
    # step_a (2s) -> step_c (3s) -> step_d (4s) = 9s
    crit_path = graph.critical_path()
    assert crit_path == ["step_a", "step_b", "step_d"]

    duration = graph.critical_path_duration()
    assert duration == 11.0


def test_critical_path_zero_duration_parents() -> None:
    """Test critical path preservation when parents have 0.0 estimated duration."""
    s1 = StepDefinition(name="root", action=dummy_action, estimated_duration_seconds=0.0)
    s2 = StepDefinition(
        name="child",
        action=dummy_action,
        depends_on=("root",),
        estimated_duration_seconds=0.0,
    )
    wf = WorkflowDefinition(
        name="zero_dur",
        stages=(
            StageDefinition(name="s1", steps=(s1,)),
            StageDefinition(name="s2", steps=(s2,)),
        ),
    )
    graph = WorkflowGraph.from_workflow(wf)
    crit_path = graph.critical_path()
    assert crit_path == ["root", "child"]
    dur = graph.critical_path_duration()
    assert dur == 0.0


def test_roots_leaves_and_topological_steps() -> None:
    """Test querying roots, leaves, and topological step definitions."""
    wf = create_sample_workflow()
    graph = WorkflowGraph.from_workflow(wf)

    root_names = [n.name for n in graph.roots()]
    assert root_names == ["step_a"]

    leaf_names = [n.name for n in graph.leaves()]
    assert leaf_names == ["step_d"]

    steps = graph.topological_steps()
    step_names = [s.name for s in steps]
    assert step_names == ["step_a", "step_b", "step_c", "step_d"]


def test_empty_workflow_graph() -> None:
    """Test WorkflowGraph behavior on an empty workflow."""
    wf = WorkflowDefinition(name="empty", version="0.1.0", stages=())
    graph = WorkflowGraph.from_workflow(wf)

    assert graph.nodes == {}
    assert graph.critical_path() == []
    assert graph.critical_path_duration() == 0.0
    assert graph.roots() == []
    assert graph.leaves() == []
