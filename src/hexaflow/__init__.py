"""Hexaflow - Lightweight, embeddable Python workflow engine.

Notes/Architectural Intent:
    Provides a zero-daemon, localhost-first workflow engine supporting stages, steps,
    splits (fan-out), joins (barriers), and checkpointed state resumption.
"""

from hexaflow.adapters.engines.local_async import (
    AsyncioWorkflowEngine,
    LocalAsyncWorkflowEngine,
)
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.cli.binder import CliOptionSpec, WorkflowCliBinder
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
    ExecutionPool,
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
    evaluate_trigger_rule,
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
from hexaflow.dsl.builder import Workflow
from hexaflow.ports.engine import WorkflowEnginePort
from hexaflow.ports.storage import WorkflowStateStorePort

__version__ = "0.4.0"


__all__ = [
    "__version__",
    "AsyncioWorkflowEngine",
    "BackoffType",
    "CheckpointCorruptError",
    "CheckpointRecord",
    "CliOptionSpec",
    "DuplicateStepError",
    "evaluate_trigger_rule",
    "ExecutionPool",
    "InMemoryStateStore",
    "InvalidWorkflowDAGError",
    "LocalAsyncWorkflowEngine",
    "RetryPolicy",
    "SqliteStateStore",
    "StageDefinition",
    "StageExecutionMode",
    "StepContext",
    "StepDefinition",
    "StepFailedError",
    "StepNotFoundError",
    "StepStatus",
    "TriggerRule",
    "Workflow",
    "WorkflowAborted",
    "WorkflowCliBinder",
    "WorkflowDefinition",
    "WorkflowEnginePort",
    "WorkflowError",
    "WorkflowExecutionState",
    "WorkflowStateStorePort",
    "WorkflowStatus",
    "WorkflowSuspended",
]
