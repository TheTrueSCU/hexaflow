"""Unit tests for MockFallbackMiddleware.

Notes/Architectural Intent:
    Verifies that MockFallbackMiddleware intercepts DryRunUnsafeStepError during
    dry-run simulation and injects configured fallback values, custom factory results,
    or default synthetic objects without altering normal step executions.
"""

from typing import Any

import pytest

from hexaflow.adapters.middleware.mock import MockFallbackMiddleware
from hexaflow.domain.exceptions import DryRunUnsafeStepError
from hexaflow.domain.state import StepContext


def test_mock_fallback_middleware_passthrough_on_success() -> None:
    """Validate that normal step outcomes pass through unaltered."""
    mw = MockFallbackMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-1")

    def target(c: StepContext) -> str:
        return "actual_result"

    res = mw(ctx, target)
    assert res == "actual_result"
    assert "mock_applied" not in ctx.metadata


def test_mock_fallback_middleware_default_fallback_on_unsafe() -> None:
    """Validate default synthetic dictionary fallback when DryRunUnsafeStepError is raised."""
    mw = MockFallbackMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-unsafe")

    def target(c: StepContext) -> Any:
        raise DryRunUnsafeStepError("Unsafe side-effecting step")

    res = mw(ctx, target)
    expected_payload = {"mock": True, "step": "step-unsafe"}
    assert res == expected_payload
    mock_applied = ctx.metadata.get("mock_applied")
    assert mock_applied is True
    fallback_val = ctx.metadata.get("fallback_value")
    assert fallback_val == expected_payload


def test_mock_fallback_middleware_step_fallbacks_dictionary() -> None:
    """Validate exact step-name matching fallback from dictionary."""
    step_mocks = {"charge_credit_card": {"tx_id": "tx_mock_123"}}
    mw = MockFallbackMiddleware(step_fallbacks=step_mocks)
    ctx = StepContext(run_id="run-1", stage_name="billing", step_name="charge_credit_card")

    def target(c: StepContext) -> Any:
        raise DryRunUnsafeStepError("Payment gateway call")

    res = mw(ctx, target)
    assert res == {"tx_id": "tx_mock_123"}
    mock_applied = ctx.metadata.get("mock_applied")
    assert mock_applied is True


def test_mock_fallback_middleware_factory_fallback() -> None:
    """Validate fallback factory callable invocation."""

    def factory(c: StepContext) -> dict[str, str]:
        return {"synthesized_for": c.step_name}

    mw = MockFallbackMiddleware(fallback_factory=factory)
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="custom_step")

    def target(c: StepContext) -> Any:
        raise DryRunUnsafeStepError("Database write")

    res = mw(ctx, target)
    assert res == {"synthesized_for": "custom_step"}


def test_mock_fallback_middleware_default_value() -> None:
    """Validate static default_value fallback parameter."""
    mw = MockFallbackMiddleware(default_value="static_fallback")
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="any_step")

    def target(c: StepContext) -> Any:
        raise DryRunUnsafeStepError("External call")

    res = mw(ctx, target)
    assert res == "static_fallback"


@pytest.mark.asyncio
async def test_mock_fallback_middleware_async_interception() -> None:
    """Validate asynchronous coroutine interception and fallback resolution."""
    mw = MockFallbackMiddleware(step_fallbacks={"send_email": True})
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="send_email")

    async def target(c: StepContext) -> Any:
        raise DryRunUnsafeStepError("SMTP connection")

    res = await mw(ctx, target)
    assert res is True
    mock_applied = ctx.metadata.get("mock_applied")
    assert mock_applied is True


def test_mock_fallback_middleware_non_dryrun_error_propagates() -> None:
    """Validate that exceptions other than DryRunUnsafeStepError are not caught."""
    mw = MockFallbackMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-1")

    def target(c: StepContext) -> Any:
        raise ValueError("General bug")

    with pytest.raises(ValueError, match="General bug"):
        mw(ctx, target)
