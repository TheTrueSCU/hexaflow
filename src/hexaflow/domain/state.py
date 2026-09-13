"""Domain state tracking, status enumerations, and checkpoint models.

Notes/Architectural Intent:
    Represents mutable runtime execution state and immutable point-in-time
    checkpoints for workflow stages and steps. All models are serializable to msgspec
    and JSON formats for SQLite and remote persistence.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


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
    error_summary: str | None = Field(
        default=None, description="High-level error summary if suspended or failed."
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Run start timestamp."
    )
    finished_at: datetime | None = Field(default=None, description="Run terminal timestamp.")


__all__ = [
    "CheckpointRecord",
    "StepContext",
    "StepStatus",
    "WorkflowExecutionState",
    "WorkflowStatus",
]
