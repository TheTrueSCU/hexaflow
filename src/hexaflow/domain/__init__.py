"""Domain models, retry policies, state tracking, and exceptions for hexaflow.

Notes/Architectural Intent:
    Exports pure domain building blocks for defining workflows, managing state,
    handling retries, and raising domain exceptions. Contains zero adapter concerns.
"""

from hexaflow.domain.exceptions import (
    CheckpointCorruptError,
    DuplicateStepError,
    InvalidWorkflowDAGError,
    StepFailedError,
    StepNotFoundError,
    WorkflowAborted,
    WorkflowError,
    WorkflowSuspended,
)
from hexaflow.domain.models import (
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    WorkflowDefinition,
)
from hexaflow.domain.retry import (
    BackoffType,
    RetryPolicy,
)
from hexaflow.domain.state import (
    CheckpointRecord,
    StepContext,
    StepStatus,
    WorkflowExecutionState,
    WorkflowStatus,
)

__all__ = [
    "BackoffType",
    "CheckpointCorruptError",
    "CheckpointRecord",
    "DuplicateStepError",
    "InvalidWorkflowDAGError",
    "RetryPolicy",
    "StageDefinition",
    "StageExecutionMode",
    "StepContext",
    "StepDefinition",
    "StepFailedError",
    "StepNotFoundError",
    "StepStatus",
    "WorkflowAborted",
    "WorkflowDefinition",
    "WorkflowError",
    "WorkflowExecutionState",
    "WorkflowStatus",
    "WorkflowSuspended",
]
