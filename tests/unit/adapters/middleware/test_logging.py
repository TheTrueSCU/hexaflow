"""Unit tests for LoggingStepMiddleware.

Notes/Architectural Intent:
    Verifies that LoggingStepMiddleware emits structured log entries before and after
    step execution, as well as on errors, with configurable options like input inclusion.
"""

import logging
from typing import Any

import pytest

from hexaflow.adapters.middleware.logging import LoggingStepMiddleware
from hexaflow.domain.state import StepContext


def test_logging_step_middleware_success(caplog: pytest.LogCaptureFixture) -> None:
    """Validate standard success logging before and after step execution."""
    custom_logger = logging.getLogger("test.hexaflow.step")
    mw = LoggingStepMiddleware(logger=custom_logger, level=logging.INFO, include_inputs=True)
    ctx = StepContext(
        run_id="run-1",
        stage_name="stage-1",
        step_name="step-1",
        inputs={"param": "value"},
    )

    def target(c: StepContext) -> str:
        return "success"

    with caplog.at_level(logging.DEBUG, logger="test.hexaflow.step"):
        res = mw(ctx, target)

    assert res == "success"
    log_messages = [record.message for record in caplog.records]
    assert any("Step starting: stage-1.step-1" in m for m in log_messages)
    assert any("Step completed: stage-1.step-1" in m for m in log_messages)
    assert any("inputs=['param']" in m for m in log_messages)


@pytest.mark.asyncio
async def test_logging_step_middleware_async_success(caplog: pytest.LogCaptureFixture) -> None:
    """Validate async step execution logging."""
    custom_logger = logging.getLogger("test.hexaflow.async")
    mw = LoggingStepMiddleware(logger=custom_logger, level=logging.INFO)
    ctx = StepContext(
        run_id="run-2",
        stage_name="stage-2",
        step_name="step-async",
    )

    async def target(c: StepContext) -> int:
        return 99

    with caplog.at_level(logging.DEBUG, logger="test.hexaflow.async"):
        res = await mw(ctx, target)

    assert res == 99
    log_messages = [record.message for record in caplog.records]
    assert any("Step starting: stage-2.step-async" in m for m in log_messages)
    assert any("Step completed: stage-2.step-async" in m for m in log_messages)


def test_logging_step_middleware_error(caplog: pytest.LogCaptureFixture) -> None:
    """Validate error logging hook when step action raises."""
    custom_logger = logging.getLogger("test.hexaflow.error")
    mw = LoggingStepMiddleware(logger=custom_logger)
    ctx = StepContext(
        run_id="run-3",
        stage_name="stage-3",
        step_name="step-error",
    )

    def target(c: StepContext) -> Any:
        raise ValueError("simulated step crash")

    with (
        caplog.at_level(logging.ERROR, logger="test.hexaflow.error"),
        pytest.raises(ValueError, match="simulated step crash"),
    ):
        mw(ctx, target)

    log_messages = [record.message for record in caplog.records]
    matches = any(
        "Step failed: stage-3.step-error" in m and "ValueError: simulated step crash" in m
        for m in log_messages
    )
    assert matches is True
