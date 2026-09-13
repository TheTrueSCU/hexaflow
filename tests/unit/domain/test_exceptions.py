"""Unit tests for domain exceptions.

Notes/Architectural Intent:
    Verifies domain exception inheritance, formatting, and diagnostic attribute retention.
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


def test_step_failed_error_preserves_context() -> None:
    """Validate StepFailedError preserves step name, attempt number, and cause."""
    cause = RuntimeError("Network timeout")
    err = StepFailedError(
        step_name="fetch_data", message="API unreachable", original_error=cause, attempt_number=3
    )

    is_workflow_err = isinstance(err, WorkflowError)
    assert is_workflow_err is True

    step_name = err.step_name
    assert step_name == "fetch_data"

    attempt = err.attempt_number
    assert attempt == 3

    orig = err.original_error
    assert orig is cause

    msg = str(err)
    assert "Step 'fetch_data' failed at attempt 3: API unreachable" in msg


def test_workflow_suspended_preserves_run_metadata() -> None:
    """Validate WorkflowSuspended preserves run ID and failed step identifier."""
    suspension = WorkflowSuspended(
        run_id="run-123", failed_step="validate_order", reason="DB connection lost"
    )

    is_workflow_err = isinstance(suspension, WorkflowError)
    assert is_workflow_err is True

    run_id = suspension.run_id
    assert run_id == "run-123"

    step = suspension.failed_step
    assert step == "validate_order"

    reason = suspension.reason
    assert reason == "DB connection lost"


def test_hierarchy_of_domain_exceptions() -> None:
    """Validate all custom exceptions inherit from WorkflowError."""
    exceptions = [
        InvalidWorkflowDAGError("Invalid DAG"),
        DuplicateStepError("Duplicate step"),
        StepNotFoundError("Step missing"),
        WorkflowAborted("Aborted by user"),
        CheckpointCorruptError("Bad checkpoint"),
    ]
    for exc in exceptions:
        is_inst = isinstance(exc, WorkflowError)
        assert is_inst is True
