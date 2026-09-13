"""Unit tests for SqliteStateStore adapter and automatic payload spillover.

Notes/Architectural Intent:
    Tests embedded SQLite operations, WAL mode, persistence durability, and
    automatic spillover to disk files when payloads exceed byte thresholds.
"""

from pathlib import Path

from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.domain.state import (
    CheckpointRecord,
    StepStatus,
    WorkflowExecutionState,
    WorkflowStatus,
)


def test_sqlite_run_persistence(tmp_path: Path) -> None:
    """Validate saving, updating, and rehydrating run state from SQLite."""
    db_file = tmp_path / "state.db"
    artifacts = tmp_path / "artifacts"
    store = SqliteStateStore(db_path=db_file, artifacts_dir=artifacts)

    state = WorkflowExecutionState(workflow_name="order_wf")
    store.save_run(state)

    retrieved = store.get_run(state.run_id)
    assert retrieved is not None
    assert retrieved.run_id == state.run_id
    assert retrieved.status == WorkflowStatus.PENDING

    # Update run state
    state.status = WorkflowStatus.RUNNING
    state.current_stage = "checkout"
    store.save_run(state)

    updated = store.get_run(state.run_id)
    assert updated is not None
    assert updated.status == WorkflowStatus.RUNNING
    assert updated.current_stage == "checkout"

    missing = store.get_run("phantom_run_id")
    assert missing is None


def test_sqlite_checkpoint_lifecycle_and_inlining(tmp_path: Path) -> None:
    """Validate standard small payload inlining into SQLite."""
    db_file = tmp_path / "state.db"
    artifacts = tmp_path / "artifacts"
    store = SqliteStateStore(db_path=db_file, artifacts_dir=artifacts)

    chk = CheckpointRecord(
        run_id="run-sqlite-1",
        stage_name="billing",
        step_name="charge_card",
        status=StepStatus.COMPLETED,
        input_payload={"amount": 100},
        output_payload={"tx_id": "tx_999"},
    )
    store.save_checkpoint(chk)

    retrieved = store.get_checkpoint("run-sqlite-1", "charge_card")
    assert retrieved is not None
    assert retrieved.step_name == "charge_card"
    assert retrieved.output_payload == {"tx_id": "tx_999"}

    all_chks = store.get_checkpoints("run-sqlite-1")
    assert len(all_chks) == 1

    missing = store.get_checkpoint("run-sqlite-1", "non_existent")
    assert missing is None


def test_sqlite_large_payload_disk_spillover(tmp_path: Path) -> None:
    """Validate that payloads exceeding threshold spill to disk and resolve transparently."""
    db_file = tmp_path / "state.db"
    artifacts = tmp_path / "artifacts"
    # Set threshold low (50 bytes) to force spillover
    store = SqliteStateStore(db_path=db_file, artifacts_dir=artifacts, spillover_threshold_bytes=50)

    large_data = {"key_" + str(i): "value_" * 10 for i in range(10)}

    chk = CheckpointRecord(
        run_id="run-spill-1",
        stage_name="data_prep",
        step_name="generate_matrix",
        status=StepStatus.COMPLETED,
        input_payload={"seed": 42},
        output_payload=large_data,
    )
    store.save_checkpoint(chk)

    # Verify that a physical spillover artifact file was written
    spill_dir = artifacts / "run-spill-1" / "data_prep"
    assert spill_dir.exists() is True
    spill_files = list(spill_dir.glob("*.bin"))
    assert len(spill_files) == 1

    # Verify that reading from SQLite resolves the spillover file transparently
    retrieved = store.get_checkpoint("run-spill-1", "generate_matrix")
    assert retrieved is not None
    assert retrieved.output_payload == large_data
    assert retrieved.input_payload == {"seed": 42}
