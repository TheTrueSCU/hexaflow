"""In-memory state store adapter for testing and ephemeral workflow runs.

Notes/Architectural Intent:
    Provides a thread-safe, zero-dependency in-memory implementation of
    WorkflowStateStorePort for rapid unit testing and short-lived in-process workflows.
"""

import threading

from hexaflow.domain.state import CheckpointRecord, WorkflowExecutionState
from hexaflow.ports.storage import WorkflowStateStorePort


class InMemoryStateStore(WorkflowStateStorePort):
    """Thread-safe in-memory store for workflow execution state and checkpoints.

    Notes/Architectural Intent:
        Uses an internal RLock to ensure concurrent step workers can read and write
        checkpoints safely within a single Python process.
    """

    def __init__(self) -> None:
        """Initialize empty in-memory state store."""
        self._lock = threading.RLock()
        self._runs: dict[str, WorkflowExecutionState] = {}
        self._checkpoints: dict[str, dict[str, CheckpointRecord]] = {}

    def save_run(self, state: WorkflowExecutionState) -> None:
        """Persist or update aggregate workflow execution run state.

        Args:
            state: The current WorkflowExecutionState to store.
        """
        with self._lock:
            self._runs[state.run_id] = state.model_copy(deep=True)

    def get_run(self, run_id: str) -> WorkflowExecutionState | None:
        """Retrieve aggregate workflow run state by ID.

        Args:
            run_id: Unique identifier of the workflow run.

        Returns:
            A deep copy of the stored WorkflowExecutionState, or None.
        """
        with self._lock:
            state = self._runs.get(run_id)
            return state.model_copy(deep=True) if state else None

    def save_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        """Persist an individual step execution checkpoint.

        Args:
            checkpoint: The CheckpointRecord snapshot to store.
        """
        with self._lock:
            run_checkpoints = self._checkpoints.setdefault(checkpoint.run_id, {})
            run_checkpoints[checkpoint.step_name] = checkpoint.model_copy(deep=True)
            # Synchronize with the run's aggregate state if present
            if checkpoint.run_id in self._runs:
                self._runs[checkpoint.run_id].step_checkpoints[checkpoint.step_name] = (
                    checkpoint.model_copy(deep=True)
                )

    def get_checkpoint(self, run_id: str, step_name: str) -> CheckpointRecord | None:
        """Retrieve the checkpoint for a specific step in a run.

        Args:
            run_id: Parent workflow execution ID.
            step_name: Unique step identifier.

        Returns:
            The CheckpointRecord if found, else None.
        """
        with self._lock:
            run_checkpoints = self._checkpoints.get(run_id, {})
            rec = run_checkpoints.get(step_name)
            return rec.model_copy(deep=True) if rec else None

    def get_checkpoints(self, run_id: str) -> list[CheckpointRecord]:
        """Retrieve all recorded step checkpoints for a given workflow run.

        Args:
            run_id: Parent workflow execution ID.

        Returns:
            List of CheckpointRecords recorded for this run.
        """
        with self._lock:
            run_checkpoints = self._checkpoints.get(run_id, {})
            return [rec.model_copy(deep=True) for rec in run_checkpoints.values()]


__all__ = [
    "InMemoryStateStore",
]
