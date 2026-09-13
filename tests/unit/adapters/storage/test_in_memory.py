"""Unit tests for InMemoryStateStore adapter.

Notes/Architectural Intent:
    Validates save_run, get_run, save_checkpoint, and get_checkpoints in-memory operations.
"""

from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.state import (
    CheckpointRecord,
    StepStatus,
    WorkflowExecutionState,
    WorkflowStatus,
)


def test_in_memory_run_lifecycle() -> None:
    """Validate saving and retrieving workflow run state in memory."""
    store = InMemoryStateStore()
    state = WorkflowExecutionState(workflow_name="test_wf")

    store.save_run(state)
    retrieved = store.get_run(state.run_id)

    assert retrieved is not None
    assert retrieved.run_id == state.run_id
    assert retrieved.status == WorkflowStatus.PENDING

    # Test missing run returns None
    missing = store.get_run("non_existent_run")
    assert missing is None


def test_in_memory_checkpoints_lifecycle() -> None:
    """Validate saving and querying step checkpoints."""
    store = InMemoryStateStore()
    run_id = "run-100"

    chk_1 = CheckpointRecord(
        run_id=run_id,
        stage_name="stage_a",
        step_name="step_1",
        status=StepStatus.COMPLETED,
        output_payload={"result": 1},
    )
    chk_2 = CheckpointRecord(
        run_id=run_id,
        stage_name="stage_a",
        step_name="step_2",
        status=StepStatus.COMPLETED,
        output_payload={"result": 2},
    )

    store.save_checkpoint(chk_1)
    store.save_checkpoint(chk_2)

    found_1 = store.get_checkpoint(run_id, "step_1")
    assert found_1 is not None
    assert found_1.output_payload == {"result": 1}

    all_chks = store.get_checkpoints(run_id)
    assert len(all_chks) == 2

    missing_chk = store.get_checkpoint(run_id, "phantom_step")
    assert missing_chk is None
