"""Unit tests for TimingStepMiddleware.

Notes/Architectural Intent:
    Verifies that TimingStepMiddleware correctly measures execution wall-clock time,
    populates duration_seconds and latency_ms on ctx.metadata, and gracefully triggers
    the optional metrics callback without side effects.
"""

from typing import Any

import pytest

from hexaflow.adapters.middleware.timing import TimingStepMiddleware
from hexaflow.domain.state import StepContext


def test_timing_step_middleware_sync_execution() -> None:
    """Validate synchronous execution timing metrics recording."""
    captured: list[tuple[str, float]] = []

    def callback(ctx: StepContext, duration: float) -> None:
        captured.append((ctx.step_name, duration))

    mw = TimingStepMiddleware(callback=callback)
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-sync")

    def target(c: StepContext) -> str:
        return "done"

    res = mw(ctx, target)
    assert res == "done"

    duration = ctx.metadata.get("duration_seconds")
    assert isinstance(duration, float)
    assert duration >= 0.0

    latency_ms = ctx.metadata.get("latency_ms")
    assert isinstance(latency_ms, float)
    assert latency_ms >= 0.0

    captured_len = len(captured)
    assert captured_len == 1
    assert captured[0][0] == "step-sync"


@pytest.mark.asyncio
async def test_timing_step_middleware_async_execution() -> None:
    """Validate asynchronous execution timing metrics recording."""
    captured: list[float] = []

    def callback(ctx: StepContext, duration: float) -> None:
        captured.append(duration)

    mw = TimingStepMiddleware(callback=callback)
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-async")

    async def target(c: StepContext) -> int:
        return 123

    res = await mw(ctx, target)
    assert res == 123

    duration = ctx.metadata.get("duration_seconds")
    assert isinstance(duration, float)
    assert duration >= 0.0

    latency_ms = ctx.metadata.get("latency_ms")
    assert isinstance(latency_ms, float)
    assert latency_ms >= 0.0

    captured_len = len(captured)
    assert captured_len == 1


def test_timing_step_middleware_callback_exception_suppressed() -> None:
    """Validate that faulty callback does not crash step execution."""

    def faulty_callback(ctx: StepContext, duration: float) -> None:
        raise RuntimeError("telemetry error")

    mw = TimingStepMiddleware(callback=faulty_callback)
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-faulty")

    def target(c: StepContext) -> str:
        return "ok"

    res = mw(ctx, target)
    assert res == "ok"
    duration = ctx.metadata.get("duration_seconds")
    assert isinstance(duration, float)


def test_timing_step_middleware_on_error_records_metrics() -> None:
    """Validate that on_error records duration metrics even when step action fails."""
    mw = TimingStepMiddleware()
    ctx = StepContext(run_id="run-1", stage_name="stage-1", step_name="step-failed")

    def target(c: StepContext) -> Any:
        raise ValueError("failing step")

    with pytest.raises(ValueError, match="failing step"):
        mw(ctx, target)

    duration = ctx.metadata.get("duration_seconds")
    assert isinstance(duration, float)
    assert duration >= 0.0
