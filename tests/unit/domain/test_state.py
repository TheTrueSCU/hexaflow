"""Unit tests for workflow and step state models and checkpoints.

Notes/Architectural Intent:
    Validates CheckpointRecord, StepContext, and WorkflowExecutionState data models.
"""

from datetime import UTC, datetime

from hexaflow.domain.state import (
    CheckpointRecord,
    StepContext,
    StepStatus,
    WorkflowExecutionState,
    WorkflowStatus,
)


def test_step_context_creation() -> None:
    """Validate StepContext construction and read-only properties."""
    ctx = StepContext(
        run_id="run-456",
        stage_name="etl",
        step_name="extract",
        attempt_number=2,
        inputs={"source": "s3://bucket/data.csv"},
        checkpoint_dir="/tmp/scratch/step1",
    )
    run_id = ctx.run_id
    assert run_id == "run-456"

    attempt = ctx.attempt_number
    assert attempt == 2

    source = ctx.inputs.get("source")
    assert source == "s3://bucket/data.csv"


def test_checkpoint_record_defaults() -> None:
    """Validate CheckpointRecord default values and serialization compatibility."""
    now = datetime.now(UTC)
    record = CheckpointRecord(
        run_id="run-789",
        stage_name="ingest",
        step_name="parse",
        status=StepStatus.COMPLETED,
        output_payload={"count": 42},
        completed_at=now,
        duration_seconds=1.23,
    )
    status = record.status
    assert status == StepStatus.COMPLETED

    payload = record.output_payload
    assert payload == {"count": 42}

    duration = record.duration_seconds
    assert duration == 1.23


def test_workflow_execution_state_lifecycle() -> None:
    """Validate WorkflowExecutionState updates and step checkpoint tracking."""
    state = WorkflowExecutionState(workflow_name="order_pipeline")

    initial_status = state.status
    assert initial_status == WorkflowStatus.PENDING

    record = CheckpointRecord(
        run_id=state.run_id,
        stage_name="checkout",
        step_name="auth_card",
        status=StepStatus.COMPLETED,
        output_payload={"token": "tok_abc"},
    )
    state.step_checkpoints["auth_card"] = record
    state.status = WorkflowStatus.RUNNING
    state.current_stage = "checkout"

    has_step = "auth_card" in state.step_checkpoints
    assert has_step is True

    curr_status = state.status
    assert curr_status == WorkflowStatus.RUNNING

    curr_stage = state.current_stage
    assert curr_stage == "checkout"


def test_workflow_memento_capture_and_restore() -> None:
    """Validate to_memento snapshot capture and restore_from_memento restore."""
    import pytest

    from hexaflow.domain.state import WorkflowMemento

    state = WorkflowExecutionState(workflow_name="checkout_flow")
    chk = CheckpointRecord(
        run_id=state.run_id,
        stage_name="cart",
        step_name="validate_cart",
        status=StepStatus.COMPLETED,
        output_payload={"valid": True},
    )
    state.step_checkpoints["validate_cart"] = chk
    state.status = WorkflowStatus.RUNNING

    memento = state.to_memento()
    assert isinstance(memento, WorkflowMemento)
    assert memento.run_id == state.run_id
    assert "validate_cart" in memento.step_checkpoints

    # Mutate state
    state.step_checkpoints["charge"] = CheckpointRecord(
        run_id=state.run_id,
        stage_name="cart",
        step_name="charge",
        status=StepStatus.FAILED,
    )
    state.status = WorkflowStatus.SUSPENDED

    # Restore
    state.restore_from_memento(memento)
    assert state.status == WorkflowStatus.RUNNING
    assert "charge" not in state.step_checkpoints
    assert "validate_cart" in state.step_checkpoints

    # Mismatch run_id raises ValueError
    foreign_memento = WorkflowMemento(
        run_id="other_run_123",
        workflow_name="checkout_flow",
        status=WorkflowStatus.RUNNING,
    )
    with pytest.raises(ValueError, match="Cannot restore memento"):
        state.restore_from_memento(foreign_memento)


def test_workflow_execution_state_rewind_to() -> None:
    """Validate rewind_to removes target and downstream checkpoints and resets status."""
    state = WorkflowExecutionState(workflow_name="rewind_flow")
    chk1 = CheckpointRecord(
        run_id=state.run_id,
        stage_name="s1",
        step_name="step_a",
        status=StepStatus.COMPLETED,
    )
    chk2 = CheckpointRecord(
        run_id=state.run_id,
        stage_name="s2",
        step_name="step_b",
        status=StepStatus.COMPLETED,
    )
    chk3 = CheckpointRecord(
        run_id=state.run_id,
        stage_name="s3",
        step_name="step_c",
        status=StepStatus.COMPLETED,
    )
    state.step_checkpoints["step_a"] = chk1
    state.step_checkpoints["step_b"] = chk2
    state.step_checkpoints["step_c"] = chk3
    state.status = WorkflowStatus.COMPLETED

    purged = state.rewind_to("step_b", downstream_steps={"step_c"})
    assert purged == {"step_b", "step_c"}
    assert "step_a" in state.step_checkpoints
    assert "step_b" not in state.step_checkpoints
    assert "step_c" not in state.step_checkpoints
    assert state.status == WorkflowStatus.SUSPENDED
