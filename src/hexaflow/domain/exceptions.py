"""Domain exceptions for hexaflow workflow execution, DAG validation, and checkpoints.

Notes/Architectural Intent:
    Establishes pure domain exceptions for workflow lifecycle transitions, DAG
    topology validation, execution failures, and checkpoint corruption. Contains
    zero framework dependencies.
"""


class WorkflowError(Exception):
    """Base exception for all domain errors within hexaflow.

    Notes/Architectural Intent:
        Serves as the root exception type so callers can trap any hexaflow-specific
        failure while preserving domain causality.
    """


class InvalidWorkflowDAGError(WorkflowError):
    """Raised when a workflow DAG contains cycles, missing dependencies, or invalid structure.

    Notes/Architectural Intent:
        Enforces DAG invariants at definition and validation time prior to runtime execution.
    """


class DuplicateStepError(InvalidWorkflowDAGError):
    """Raised when a workflow contains duplicate step identifiers.

    Notes/Architectural Intent:
        Guarantees that every step identifier within a workflow is unique for deterministic
        checkpointing and dependency resolution.
    """


class StepNotFoundError(WorkflowError):
    """Raised when an operation references a step identifier that does not exist in the workflow.

    Notes/Architectural Intent:
        Protects against invalid resume requests or missing dependencies during DAG evaluation.
    """


class StepFailedError(WorkflowError):
    """Raised when an individual step execution fails permanently.

    Notes/Architectural Intent:
        Encapsulates step-level failure metadata, including the original exception,
        attempt count, and step identifier.
    """

    def __init__(
        self,
        step_name: str,
        message: str,
        original_error: BaseException | None = None,
        attempt_number: int = 1,
    ) -> None:
        """Initialize the step failure exception.

        Args:
            step_name: Unique identifier of the failing step.
            message: Descriptive error message explaining the failure.
            original_error: The underlying exception that caused the step to fail.
            attempt_number: The retry attempt number during which the failure occurred.

        Notes/Architectural Intent:
            Preserves the original exception traceback and retry context for diagnostics.
        """
        super().__init__(f"Step '{step_name}' failed at attempt {attempt_number}: {message}")
        self.step_name = step_name
        self.original_error = original_error
        self.attempt_number = attempt_number


class WorkflowSuspended(WorkflowError):
    """Signifies that a workflow execution has been suspended awaiting operator fix or resumption.

    Notes/Architectural Intent:
        Represents a controlled pause in execution when a non-transient error occurs,
        preserving all checkpoints without unwinding or terminating destructively.
    """

    def __init__(self, run_id: str, failed_step: str, reason: str) -> None:
        """Initialize workflow suspension.

        Args:
            run_id: Unique execution identifier of the suspended workflow run.
            failed_step: Step identifier where execution was halted.
            reason: Diagnostic explanation of the suspension cause.

        Notes/Architectural Intent:
            Provides actionable diagnostic context for CLI inspection and resumption.
        """
        super().__init__(f"Workflow run '{run_id}' suspended at step '{failed_step}': {reason}")
        self.run_id = run_id
        self.failed_step = failed_step
        self.reason = reason


class WorkflowAborted(WorkflowError):
    """Signifies that a workflow execution was cancelled or aborted.

    Notes/Architectural Intent:
        Raised when an operator or orchestrator explicitly terminates a workflow run,
        initiating any optional compensating actions.
    """


class CheckpointCorruptError(WorkflowError):
    """Raised when a checkpoint payload cannot be deserialized or has invalid schema.

    Notes/Architectural Intent:
        Protects against state corruption, schema skew, or missing spillover artifact files.
    """


__all__ = [
    "CheckpointCorruptError",
    "DuplicateStepError",
    "InvalidWorkflowDAGError",
    "StepFailedError",
    "StepNotFoundError",
    "WorkflowAborted",
    "WorkflowError",
    "WorkflowSuspended",
]
