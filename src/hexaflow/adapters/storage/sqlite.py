"""SQLite state store adapter with automatic disk spillover for large payloads.

Notes/Architectural Intent:
    Provides an embedded, zero-daemon SQLite persistence engine. Stores step
    checkpoints locally on disk. Payloads exceeding spillover_threshold_bytes (default 64KB)
    automatically spill to artifact files on the local filesystem to avoid SQLite bloat.
"""

import json
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from hexaflow.domain.state import CheckpointRecord, StepStatus, WorkflowExecutionState, WorkflowStatus
from hexaflow.ports.storage import WorkflowStateStorePort


class _StateJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling datetimes and domain models."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, "model_dump"):
            return o.model_dump()
        return super().default(o)


class SqliteStateStore(WorkflowStateStorePort):
    """Embedded SQLite state store with automatic large-payload disk spillover.

    Notes/Architectural Intent:
        Implements WorkflowStateStorePort for localhost execution. Keeps step outputs
        and execution state durable across process restarts, enabling resumption.
    """

    SPILLOVER_KEY = "@hexaflow_spillover"
    DEFAULT_THRESHOLD = 65536  # 64 KB

    def __init__(
        self,
        db_path: str | Path = ".hexaflow/state.db",
        artifacts_dir: str | Path = ".hexaflow/artifacts",
        spillover_threshold_bytes: int = DEFAULT_THRESHOLD,
    ) -> None:
        """Initialize the SQLite state store.

        Args:
            db_path: Path to the SQLite database file.
            artifacts_dir: Directory where large spilled payloads are stored.
            spillover_threshold_bytes: Maximum payload size in bytes before spilling to disk.
        """
        self._db_path = Path(db_path)
        self._artifacts_dir = Path(artifacts_dir)
        self._threshold = spillover_threshold_bytes
        self._lock = threading.RLock()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a managed SQLite connection with WAL mode and automatic cleanup."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database tables and indexes."""
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    workflow_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage TEXT,
                    error_summary TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS step_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    input_payload TEXT,
                    output_payload TEXT,
                    error_traceback TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_seconds REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id),
                    UNIQUE(run_id, step_name)
                );
                """
            )
            conn.commit()

    def _serialize_and_spill(self, run_id: str, stage_name: str, step_name: str, payload_type: str, data: Any) -> str | None:
        """Serialize data to JSON, spilling to disk if size exceeds threshold.

        Args:
            run_id: Workflow execution ID.
            stage_name: Stage name.
            step_name: Step name.
            payload_type: 'input' or 'output'.
            data: Raw Python object to serialize.

        Returns:
            JSON-serialized string or spillover reference pointer.
        """
        if data is None:
            return None

        raw_str = json.dumps(data, cls=_StateJSONEncoder)
        raw_bytes = raw_str.encode("utf-8")

        if len(raw_bytes) > self._threshold:
            step_dir = self._artifacts_dir / run_id / stage_name
            step_dir.mkdir(parents=True, exist_ok=True)
            spill_file = step_dir / f"{step_name}_{payload_type}.bin"
            spill_file.write_bytes(raw_bytes)
            ref_dict = {self.SPILLOVER_KEY: str(spill_file.resolve())}
            return json.dumps(ref_dict)

        return raw_str

    def _deserialize_and_resolve(self, raw_str: str | None) -> Any:
        """Deserialize JSON string, resolving disk spillover if present.

        Args:
            raw_str: Stored payload string from SQLite.

        Returns:
            Deserialized Python payload object.
        """
        if not raw_str:
            return None

        parsed = json.loads(raw_str)
        if isinstance(parsed, dict) and self.SPILLOVER_KEY in parsed:
            spill_path = Path(parsed[self.SPILLOVER_KEY])
            if spill_path.exists():
                spill_bytes = spill_path.read_bytes()
                return json.loads(spill_bytes.decode("utf-8"))
            return None
        return parsed

    def save_run(self, state: WorkflowExecutionState) -> None:
        """Persist or update aggregate workflow execution run state.

        Args:
            state: The current WorkflowExecutionState to store.
        """
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, workflow_name, status, current_stage, error_summary, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    current_stage=excluded.current_stage,
                    error_summary=excluded.error_summary,
                    finished_at=excluded.finished_at;
                """,
                (
                    state.run_id,
                    state.workflow_name,
                    state.status.value,
                    state.current_stage,
                    state.error_summary,
                    state.started_at.isoformat(),
                    state.finished_at.isoformat() if state.finished_at else None,
                ),
            )
            conn.commit()

    def get_run(self, run_id: str) -> WorkflowExecutionState | None:
        """Retrieve aggregate workflow run state by ID.

        Args:
            run_id: Unique identifier of the workflow run.

        Returns:
            Rehydrated WorkflowExecutionState with all step checkpoints, or None.
        """
        with self._lock, self._connection() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None

            checkpoints = self.get_checkpoints(run_id)
            step_map = {chk.step_name: chk for chk in checkpoints}

            return WorkflowExecutionState(
                run_id=row["run_id"],
                workflow_name=row["workflow_name"],
                status=WorkflowStatus(row["status"]),
                current_stage=row["current_stage"],
                error_summary=row["error_summary"],
                step_checkpoints=step_map,
                started_at=datetime.fromisoformat(row["started_at"]),
                finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            )

    def save_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        """Persist an individual step execution checkpoint with automatic spillover.

        Args:
            checkpoint: The CheckpointRecord snapshot to store.
        """
        serialized_input = self._serialize_and_spill(
            checkpoint.run_id, checkpoint.stage_name, checkpoint.step_name, "input", checkpoint.input_payload
        )
        serialized_output = self._serialize_and_spill(
            checkpoint.run_id, checkpoint.stage_name, checkpoint.step_name, "output", checkpoint.output_payload
        )

        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO step_checkpoints (
                    checkpoint_id, run_id, stage_name, step_name, status, attempt_number,
                    input_payload, output_payload, error_traceback, started_at, completed_at, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, step_name) DO UPDATE SET
                    checkpoint_id=excluded.checkpoint_id,
                    stage_name=excluded.stage_name,
                    status=excluded.status,
                    attempt_number=excluded.attempt_number,
                    input_payload=excluded.input_payload,
                    output_payload=excluded.output_payload,
                    error_traceback=excluded.error_traceback,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    duration_seconds=excluded.duration_seconds;
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.run_id,
                    checkpoint.stage_name,
                    checkpoint.step_name,
                    checkpoint.status.value,
                    checkpoint.attempt_number,
                    serialized_input,
                    serialized_output,
                    checkpoint.error_traceback,
                    checkpoint.started_at.isoformat(),
                    checkpoint.completed_at.isoformat() if checkpoint.completed_at else None,
                    checkpoint.duration_seconds,
                ),
            )
            conn.commit()

    def get_checkpoint(self, run_id: str, step_name: str) -> CheckpointRecord | None:
        """Retrieve the checkpoint for a specific step in a run.

        Args:
            run_id: Parent workflow execution ID.
            step_name: Unique step identifier.

        Returns:
            The rehydrated CheckpointRecord, or None.
        """
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM step_checkpoints WHERE run_id = ? AND step_name = ?",
                (run_id, step_name),
            ).fetchone()
            if not row:
                return None
            return self._row_to_checkpoint(row)

    def get_checkpoints(self, run_id: str) -> list[CheckpointRecord]:
        """Retrieve all recorded step checkpoints for a given workflow run.

        Args:
            run_id: Parent workflow execution ID.

        Returns:
            List of rehydrated CheckpointRecords.
        """
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM step_checkpoints WHERE run_id = ? ORDER BY started_at ASC",
                (run_id,),
            ).fetchall()
            return [self._row_to_checkpoint(row) for row in rows]

    def _row_to_checkpoint(self, row: sqlite3.Row) -> CheckpointRecord:
        """Convert a database row into a domain CheckpointRecord."""
        return CheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            run_id=row["run_id"],
            stage_name=row["stage_name"],
            step_name=row["step_name"],
            status=StepStatus(row["status"]),
            attempt_number=row["attempt_number"],
            input_payload=self._deserialize_and_resolve(row["input_payload"]),
            output_payload=self._deserialize_and_resolve(row["output_payload"]),
            error_traceback=row["error_traceback"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            duration_seconds=row["duration_seconds"],
        )


__all__ = [
    "SqliteStateStore",
]
