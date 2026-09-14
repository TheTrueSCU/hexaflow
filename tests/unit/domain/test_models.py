"""Unit tests for WorkflowDefinition, StageDefinition, and StepDefinition DAG validation.

Notes/Architectural Intent:
    Tests graph validation rules: cycle detection, unique naming across stages,
    dependency existence, and topological order computation.
"""

import pytest

from hexaflow.domain.exceptions import (
    DuplicateStepError,
    InvalidWorkflowDAGError,
    StepNotFoundError,
)
from hexaflow.domain.models import (
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
    evaluate_trigger_rule,
)
from hexaflow.domain.state import StepStatus


def test_valid_multi_stage_workflow_construction() -> None:
    """Validate construction and topological order of a valid multi-stage DAG."""
    step_a = StepDefinition(name="fetch_data", action=lambda ctx: [1, 2, 3])
    step_b1 = StepDefinition(name="enrich_a", action=lambda ctx: True, depends_on=("fetch_data",))
    step_b2 = StepDefinition(name="enrich_b", action=lambda ctx: True, depends_on=("fetch_data",))
    step_c = StepDefinition(
        name="aggregate", action=lambda ctx: 42, depends_on=("enrich_a", "enrich_b")
    )

    stage_1 = StageDefinition(name="ingest", steps=(step_a,))
    stage_2 = StageDefinition(
        name="process", steps=(step_b1, step_b2), execution_mode=StageExecutionMode.CONCURRENT_ALL
    )
    stage_3 = StageDefinition(name="report", steps=(step_c,))

    workflow = WorkflowDefinition(
        name="data_pipeline",
        stages=(stage_1, stage_2, stage_3),
        version="1.2.0",
    )

    found_step = workflow.get_step("aggregate")
    assert found_step.name == "aggregate"

    found_stage = workflow.get_stage_for_step("enrich_a")
    assert found_stage.name == "process"

    order = workflow.topological_order()
    assert order.index("fetch_data") < order.index("enrich_a")
    assert order.index("fetch_data") < order.index("enrich_b")
    assert order.index("enrich_a") < order.index("aggregate")
    assert order.index("enrich_b") < order.index("aggregate")


def test_duplicate_step_name_within_stage_raises_error() -> None:
    """Validate duplicate step names within a single stage are rejected."""
    step_1 = StepDefinition(name="duplicate_name", action=lambda ctx: None)
    step_2 = StepDefinition(name="duplicate_name", action=lambda ctx: None)

    with pytest.raises(
        DuplicateStepError, match="Duplicate step name 'duplicate_name' found in stage"
    ):
        StageDefinition(name="bad_stage", steps=(step_1, step_2))


def test_duplicate_step_name_across_stages_raises_error() -> None:
    """Validate duplicate step names across different stages are rejected."""
    step_1 = StepDefinition(name="shared_name", action=lambda ctx: None)
    step_2 = StepDefinition(name="shared_name", action=lambda ctx: None)

    stage_1 = StageDefinition(name="stage_1", steps=(step_1,))
    stage_2 = StageDefinition(name="stage_2", steps=(step_2,))

    with pytest.raises(
        DuplicateStepError, match="Duplicate step 'shared_name' found across stages"
    ):
        WorkflowDefinition(name="bad_workflow", stages=(stage_1, stage_2))


def test_unknown_dependency_raises_step_not_found_error() -> None:
    """Validate dependency pointing to non-existent step raises StepNotFoundError."""
    step = StepDefinition(name="process", action=lambda ctx: None, depends_on=("phantom_step",))
    stage = StageDefinition(name="stage", steps=(step,))

    with pytest.raises(StepNotFoundError, match="depends on unknown step 'phantom_step'"):
        WorkflowDefinition(name="broken_deps", stages=(stage,))


def test_self_dependency_raises_invalid_dag_error() -> None:
    """Validate step depending on itself raises InvalidWorkflowDAGError."""
    step = StepDefinition(
        name="recursive_step", action=lambda ctx: None, depends_on=("recursive_step",)
    )
    stage = StageDefinition(name="stage", steps=(step,))

    with pytest.raises(InvalidWorkflowDAGError, match="cannot depend on itself"):
        WorkflowDefinition(name="self_loop", stages=(stage,))


def test_circular_cycle_detected_raises_invalid_dag_error() -> None:
    """Validate circular cycle between steps is detected and rejected."""
    step_a = StepDefinition(name="step_a", action=lambda ctx: None, depends_on=("step_c",))
    step_b = StepDefinition(name="step_b", action=lambda ctx: None, depends_on=("step_a",))
    step_c = StepDefinition(name="step_c", action=lambda ctx: None, depends_on=("step_b",))

    stage = StageDefinition(name="cycle_stage", steps=(step_a, step_b, step_c))

    with pytest.raises(InvalidWorkflowDAGError, match="circular cycle"):
        WorkflowDefinition(name="cyclic_workflow", stages=(stage,))


def test_get_step_not_found_raises_error() -> None:
    """Validate get_step on missing name raises StepNotFoundError."""
    workflow = WorkflowDefinition(name="empty_wf")
    with pytest.raises(StepNotFoundError, match="Step 'non_existent' not found"):
        workflow.get_step("non_existent")


def test_get_stage_for_step_not_found_raises_error() -> None:
    """Validate get_stage_for_step on missing name raises StepNotFoundError."""
    workflow = WorkflowDefinition(name="empty_wf")
    with pytest.raises(StepNotFoundError, match="Step 'non_existent' not found"):
        workflow.get_stage_for_step("non_existent")


def test_dependencies_map() -> None:
    """Validate dependencies_map returns accurate prerequisite sets."""
    step_a = StepDefinition(name="a", action=lambda ctx: None)
    step_b = StepDefinition(name="b", action=lambda ctx: None, depends_on=("a",))
    workflow = WorkflowDefinition(
        name="dep_map_wf",
        stages=(StageDefinition(name="stage", steps=(step_a, step_b)),),
    )
    dep_map = workflow.dependencies_map()
    assert dep_map["a"] == set()
    assert dep_map["b"] == {"a"}


def test_step_metadata_preservation() -> None:
    """Validate StepDefinition stores and preserves arbitrary execution metadata."""
    meta = {"resources": {"gpus": 2, "gpu_model": "h100", "ram_mb": 65536}, "tags": ["prod"]}
    step = StepDefinition(name="gpu_step", action=lambda ctx: None, metadata=meta)
    assert step.metadata["resources"]["gpus"] == 2
    assert step.metadata["tags"] == ["prod"]


def test_step_definition_trigger_rule() -> None:
    """Validate StepDefinition defaults to ALL_SUCCESS and accepts custom rules."""
    default_step = StepDefinition(name="default_step", action=lambda ctx: None)
    assert default_step.trigger_rule == TriggerRule.ALL_SUCCESS

    custom_step = StepDefinition(
        name="custom_step",
        action=lambda ctx: None,
        trigger_rule=TriggerRule.ALL_SUCCESS_OR_SKIPPED,
    )
    assert custom_step.trigger_rule == TriggerRule.ALL_SUCCESS_OR_SKIPPED


def test_trigger_rules_evaluation() -> None:
    """Validate evaluation logic across all TriggerRule enum options."""
    # 0 parents -> always True
    assert evaluate_trigger_rule(TriggerRule.ALL_SUCCESS, []) is True
    assert evaluate_trigger_rule(TriggerRule.ALL_SUCCESS_OR_SKIPPED, []) is True

    # ALL_SUCCESS
    assert (
        evaluate_trigger_rule(TriggerRule.ALL_SUCCESS, [StepStatus.COMPLETED, StepStatus.COMPLETED])
        is True
    )
    assert (
        evaluate_trigger_rule(TriggerRule.ALL_SUCCESS, [StepStatus.COMPLETED, StepStatus.SKIPPED])
        is False
    )
    assert (
        evaluate_trigger_rule(TriggerRule.ALL_SUCCESS, [StepStatus.COMPLETED, StepStatus.FAILED])
        is False
    )

    # ALL_FAILED
    assert (
        evaluate_trigger_rule(TriggerRule.ALL_FAILED, [StepStatus.FAILED, StepStatus.FAILED])
        is True
    )
    assert (
        evaluate_trigger_rule(TriggerRule.ALL_FAILED, [StepStatus.COMPLETED, StepStatus.FAILED])
        is False
    )

    # ALL_DONE
    assert (
        evaluate_trigger_rule(
            TriggerRule.ALL_DONE, [StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED]
        )
        is True
    )

    # ONE_SUCCESS
    assert (
        evaluate_trigger_rule(TriggerRule.ONE_SUCCESS, [StepStatus.FAILED, StepStatus.COMPLETED])
        is True
    )
    assert (
        evaluate_trigger_rule(TriggerRule.ONE_SUCCESS, [StepStatus.FAILED, StepStatus.SKIPPED])
        is False
    )

    # ONE_FAILED
    assert (
        evaluate_trigger_rule(TriggerRule.ONE_FAILED, [StepStatus.COMPLETED, StepStatus.FAILED])
        is True
    )
    assert (
        evaluate_trigger_rule(TriggerRule.ONE_FAILED, [StepStatus.COMPLETED, StepStatus.SKIPPED])
        is False
    )

    # NONE_FAILED
    assert (
        evaluate_trigger_rule(TriggerRule.NONE_FAILED, [StepStatus.COMPLETED, StepStatus.SKIPPED])
        is True
    )
    assert (
        evaluate_trigger_rule(TriggerRule.NONE_FAILED, [StepStatus.COMPLETED, StepStatus.FAILED])
        is False
    )

    # ALL_SUCCESS_OR_SKIPPED
    assert (
        evaluate_trigger_rule(
            TriggerRule.ALL_SUCCESS_OR_SKIPPED, [StepStatus.COMPLETED, StepStatus.SKIPPED]
        )
        is True
    )
    assert (
        evaluate_trigger_rule(
            TriggerRule.ALL_SUCCESS_OR_SKIPPED, [StepStatus.SKIPPED, StepStatus.SKIPPED]
        )
        is True
    )
    assert (
        evaluate_trigger_rule(
            TriggerRule.ALL_SUCCESS_OR_SKIPPED, [StepStatus.COMPLETED, StepStatus.COMPLETED]
        )
        is True
    )
    assert (
        evaluate_trigger_rule(
            TriggerRule.ALL_SUCCESS_OR_SKIPPED, [StepStatus.COMPLETED, StepStatus.FAILED]
        )
        is False
    )
    assert (
        evaluate_trigger_rule(
            TriggerRule.ALL_SUCCESS_OR_SKIPPED, [StepStatus.SKIPPED, StepStatus.FAILED]
        )
        is False
    )
