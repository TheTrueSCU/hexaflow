"""Unit tests for middleware adapter base template class.

Notes/Architectural Intent:
    Verifies that InOutStepMiddleware correctly executes before(), after(), and on_error()
    lifecycle hooks for both synchronous and asynchronous step actions without side-effects.
"""

from typing import Any

import pytest

from hexaflow.adapters.middleware.base import InOutStepMiddleware
from hexaflow.domain.state import StepContext


class TrackingMiddleware(InOutStepMiddleware):
    """Test middleware tracking lifecycle execution sequence."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def before(self, ctx: StepContext) -> Any:
        self.events.append(f"before:{ctx.step_name}")
        return {"started_for": ctx.step_name}

    def after(self, ctx: StepContext, result: Any, context: Any) -> Any:
        self.events.append(f"after:{ctx.step_name}:{context['started_for']}")
        return result * 2 if isinstance(result, int) else result

    def on_error(self, ctx: StepContext, exc: Exception, context: Any) -> None:
        self.events.append(f"error:{ctx.step_name}:{type(exc).__name__}:{context['started_for']}")


def test_in_out_step_middleware_sync_success() -> None:
    """Validate synchronous step action execution lifecycle hooks."""
    mw = TrackingMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="s1", step_name="step-1")

    def target(c: StepContext) -> int:
        return 21

    res = mw(ctx, target)
    assert res == 42
    expected_events = ["before:step-1", "after:step-1:step-1"]
    assert mw.events == expected_events


def test_in_out_step_middleware_sync_error() -> None:
    """Validate synchronous step action error hook invocation and exception propagation."""
    mw = TrackingMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="s1", step_name="step-1")

    def target(c: StepContext) -> int:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        mw(ctx, target)

    expected_events = ["before:step-1", "error:step-1:ValueError:step-1"]
    assert mw.events == expected_events


@pytest.mark.asyncio
async def test_in_out_step_middleware_async_success() -> None:
    """Validate asynchronous step action coroutine execution lifecycle hooks."""
    mw = TrackingMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="s1", step_name="step-async")

    async def target(c: StepContext) -> int:
        return 50

    res = await mw(ctx, target)
    assert res == 100
    expected_events = ["before:step-async", "after:step-async:step-async"]
    assert mw.events == expected_events


@pytest.mark.asyncio
async def test_in_out_step_middleware_async_error() -> None:
    """Validate asynchronous step action error hook invocation and exception propagation."""
    mw = TrackingMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="s1", step_name="step-async-err")

    async def target(c: StepContext) -> int:
        raise RuntimeError("async fail")

    with pytest.raises(RuntimeError, match="async fail"):
        await mw(ctx, target)

    expected_events = ["before:step-async-err", "error:step-async-err:RuntimeError:step-async-err"]
    assert mw.events == expected_events


def test_in_out_step_middleware_default_hooks() -> None:
    """Validate base InOutStepMiddleware default no-op lifecycle behavior."""
    base = InOutStepMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="s1", step_name="default-step")

    before_res = base.before(ctx)
    assert before_res is None

    after_res = base.after(ctx, "output", None)
    assert after_res == "output"

    base.on_error(ctx, RuntimeError("noop"), None)

    def target(c: StepContext) -> str:
        return "result"

    res = base(ctx, target)
    assert res == "result"
