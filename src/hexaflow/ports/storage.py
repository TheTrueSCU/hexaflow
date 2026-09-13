"""Abstract port interfaces for persisting workflow runs and step checkpoints.

Notes/Architectural Intent:
    Decouples execution engines from storage backends (SQLite, PostgreSQL, Redis, In-Memory).
    Enables atomic checkpoint writes, query of active frontiers, and spillover artifact tracking.
"""

from abc import ABC, abstractmethod

from hexaflow.domain.state import CheckpointRecord, WorkflowExecutionState


class WorkflowStateStorePort(ABC):
    """Abstract storage port for workflow runs and checkpoint snapshots.

    Notes/Architectural Intent:
        Defines the persistence boundary for recording step outcomes, rehydrating
        suspended runs, and retrieving cached outputs during resumption.
    """

    @abstractmethod
    def save_run(self, state: WorkflowExecutionState) -> None:
        """Persist or update aggregate workflow execution run state.

        Args:
            state: The current WorkflowExecutionState to persist.

        Notes/Architectural Intent:
            Upserts run-level status, active stage, and termination metadata.
        """

    @abstractmethod
    def get_run(self, run_id: str) -> WorkflowExecutionState | None:
        """Retrieve aggregate workflow run state by unique execution ID.

        Args:
            run_id: Unique identifier of the workflow run.

        Returns:
            The stored WorkflowExecutionState, or None if not found.
        """

    @abstractmethod
    def save_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        """Persist an individual step execution checkpoint.

        Args:
            checkpoint: The CheckpointRecord snapshot to store.

        Notes/Architectural Intent:
            Must handle atomic upsert of step outputs and handle payload spillover
            transparently if payload exceeds storage engine inline thresholds.
        """

    @abstractmethod
    def get_checkpoint(self, run_id: str, step_name: str) -> CheckpointRecord | None:
        """Retrieve the checkpoint for a specific step in a run.

        Args:
            run_id: Parent workflow execution ID.
            step_name: Unique step identifier within the workflow.

        Returns:
            The CheckpointRecord if found, else None.
        """

    @abstractmethod
    def get_checkpoints(self, run_id: str) -> list[CheckpointRecord]:
        """Retrieve all recorded step checkpoints for a given workflow run.

        Args:
            run_id: Parent workflow execution ID.

        Returns:
            List of all CheckpointRecords recorded for this run, ordered by creation.
        """


__all__ = [
    "WorkflowStateStorePort",
]
