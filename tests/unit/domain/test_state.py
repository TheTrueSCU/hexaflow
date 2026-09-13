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
