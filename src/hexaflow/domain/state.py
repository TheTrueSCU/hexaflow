"""Domain state tracking, status enumerations, and checkpoint models.

Notes/Architectural Intent:
    Represents mutable runtime execution state and immutable point-in-time
    checkpoints for workflow stages and steps. All models are serializable to msgspec
    and JSON formats for SQLite and remote persistence.
"""

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkflowStatus(StrEnum):
    """Lifecycle status of a complete workflow execution.

    Notes/Architectural Intent:
        Governs global workflow execution state transitions from submission through
        completion, suspension on failure, or cancellation.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    """Lifecycle status of an individual step execution.

    Notes/Architectural Intent:
        Represents the state of each atomic action within a stage. Enables skipping
        completed steps during workflow resumption.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class StepContext(BaseModel):
    """Execution context injected into step actions at runtime.

    Notes/Architectural Intent:
        Provides the executing step with ambient runtime metadata, input arguments,
        retry attempt counters, and isolated scratch storage paths without coupling
        to external infrastructure.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    run_id: str = Field(description="Unique identifier for the parent workflow execution run.")
    stage_name: str = Field(description="Name of the stage enclosing this step.")
    step_name: str = Field(description="Identifier name of the currently executing step.")
    attempt_number: int = Field(default=1, description="Current retry attempt number (1-indexed).")
    inputs: dict[str, Any] = Field(
        default_factory=dict, description="Resolved input arguments passed to this step."
    )
    output: Any = Field(
        default=None,
        description="Output payload result of the step, available during compensation.",
    )
    checkpoint_dir: str | None = Field(
        default=None, description="Path to the scratch directory for this step."
    )


class CheckpointRecord(BaseModel):
    """Immutable point-in-time snapshot of an individual step's execution result.

    Notes/Architectural Intent:
        Persisted to SQLite or remote stores. When resuming a suspended workflow,
        the engine reads completed CheckpointRecords to bypass re-execution and
        forward cached output payloads downstream.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    checkpoint_id: str = Field(
        default_factory=lambda: str(uuid4()), description="Unique ID for this checkpoint."
    )
    run_id: str = Field(description="Parent workflow execution identifier.")
    stage_name: str = Field(description="Stage name where the step belongs.")
    step_name: str = Field(description="Unique step identifier.")
    status: StepStatus = Field(description="Execution outcome status.")
    attempt_number: int = Field(
        default=1, description="Attempt number when this state was reached."
    )
    input_payload: Any = Field(default=None, description="Serialized or in-memory input payload.")
    output_payload: Any = Field(default=None, description="Serialized or in-memory output payload.")
    error_traceback: str | None = Field(
        default=None, description="Diagnostic traceback string if failed."
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Execution start timestamp."
    )
    completed_at: datetime | None = Field(
        default=None, description="Execution completion timestamp."
    )
    duration_seconds: float = Field(
        default=0.0, description="Elapsed execution wall-clock time in seconds."
    )


class WorkflowExecutionState(BaseModel):
    """Aggregate execution state of a workflow run across all stages and steps.

    Notes/Architectural Intent:
        Maintains overall run status, active frontier, and mapping of completed step
        checkpoints. Used by orchestrators to determine resumption frontiers.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = Field(default_factory=lambda: str(uuid4()), description="Unique workflow run ID.")
    workflow_name: str = Field(description="Name of the executing workflow definition.")
    status: WorkflowStatus = Field(default=WorkflowStatus.PENDING, description="Global run status.")
    current_stage: str | None = Field(
        default=None, description="Name of the currently active stage."
    )
    step_checkpoints: dict[str, CheckpointRecord] = Field(
        default_factory=dict,
        description="Map of step_name to its latest CheckpointRecord.",
    )
    initial_inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Initial inputs supplied to the workflow run.",
    )
    error_summary: str | None = Field(
        default=None, description="High-level error summary if suspended or failed."
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Run start timestamp."
    )
    finished_at: datetime | None = Field(default=None, description="Run terminal timestamp.")
    is_dry_run: bool = Field(
        default=False, description="Whether this run was executed in dry_run simulation mode."
    )

    def to_memento(self) -> "WorkflowMemento":
        """Capture an immutable point-in-time snapshot memento of this execution state.

        Returns:
            WorkflowMemento capturing the current status, checkpoints, and stage.
        """
        import copy

        return WorkflowMemento(
            run_id=self.run_id,
            workflow_name=self.workflow_name,
            status=self.status,
            current_stage=self.current_stage,
            step_checkpoints=copy.deepcopy(self.step_checkpoints),
            initial_inputs=copy.deepcopy(self.initial_inputs),
            started_at=self.started_at,
            finished_at=self.finished_at,
            error_summary=self.error_summary,
            created_at=datetime.now(UTC),
        )

    def restore_from_memento(self, memento: "WorkflowMemento") -> None:
        """Restore this execution state from a previous snapshot memento.

        Args:
            memento: The WorkflowMemento snapshot to restore.

        Raises:
            ValueError: If memento run_id does not match this state's run_id.
        """
        import copy

        if memento.run_id != self.run_id:
            raise ValueError(
                f"Cannot restore memento with run_id '{memento.run_id}' onto state with run_id '{self.run_id}'."
            )

        self.status = memento.status
        self.current_stage = memento.current_stage
        self.step_checkpoints = copy.deepcopy(memento.step_checkpoints)
        self.initial_inputs = copy.deepcopy(memento.initial_inputs)
        self.started_at = memento.started_at
        self.finished_at = memento.finished_at
        self.error_summary = memento.error_summary

    def rewind_to(
        self, step_name: str, downstream_steps: set[str] | list[str] | None = None
    ) -> set[str]:
        """Rewind workflow execution state to before a specific step.

        Invalidates and purges the checkpoint for `step_name` and all its transitive
        `downstream_steps`, setting the workflow status back to SUSPENDED or RUNNING.

        Args:
            step_name: The target step to rewind before.
            downstream_steps: Transitive downstream dependent step names to invalidate.
                If None, only `step_name` is purged.

        Returns:
            Set of all step names whose checkpoints were cleared.

        Notes/Architectural Intent:
            Enables time-travel workflow debugging and partial replay without requiring
            a full workflow restart from stage 0.
        """
        purged: set[str] = set()
        steps_to_clear = {step_name} | set(downstream_steps or ())

        for s in steps_to_clear:
            if s in self.step_checkpoints:
                del self.step_checkpoints[s]
                purged.add(s)

        if self.status in (WorkflowStatus.COMPLETED, WorkflowStatus.CANCELLED):
            self.status = WorkflowStatus.SUSPENDED
        self.finished_at = None
        return purged


class WorkflowMemento(BaseModel):
    """Immutable point-in-time snapshot of workflow execution state.

    Notes/Architectural Intent:
        Implements the Gang of Four Memento pattern for workflow state.
        Allows capturing snapshots before milestones and restoring or time-traveling
        back without violating encapsulation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    run_id: str = Field(description="Unique workflow run identifier.")
    workflow_name: str = Field(description="Name of the workflow definition.")
    status: WorkflowStatus = Field(description="Status of workflow at capture time.")
    current_stage: str | None = Field(default=None, description="Active stage at capture time.")
    step_checkpoints: dict[str, CheckpointRecord] = Field(
        default_factory=dict,
        description="Immutable snapshot mapping of step_name to CheckpointRecord.",
    )
    initial_inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Initial inputs supplied to run.",
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Run start timestamp at capture time.",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="Run finished timestamp at capture time.",
    )
    error_summary: str | None = Field(default=None, description="Error summary at capture time.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when memento was created.",
    )

    @field_validator("step_checkpoints", "initial_inputs", mode="before")
    @classmethod
    def _defensive_deepcopy(cls, v: Any) -> Any:
        """Enforces deep immutability by copying input dictionary structures."""
        return copy.deepcopy(v)


__all__ = [
    "CheckpointRecord",
    "StepContext",
    "StepStatus",
    "WorkflowExecutionState",
    "WorkflowMemento",
    "WorkflowStatus",
]
