"""Timing step middleware interceptor for hexaflow workflows.

Notes/Architectural Intent:
    Measures high-precision execution wall-clock time and writes duration
    and latency metrics directly into `ctx.metadata`, matching Hexastack TimingMiddleware.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

from hexaflow.adapters.middleware.base import InOutStepMiddleware
from hexaflow.domain.state import StepContext


class TimingStepMiddleware(InOutStepMiddleware):
    """Step middleware measuring execution wall-clock duration.

    Notes/Architectural Intent:
        Inherits from InOutStepMiddleware to capture execution duration using perf_counter,
        storing `duration_seconds` and `latency_ms` onto `ctx.metadata`. An optional callback
        can be registered to export metrics to telemetry or profiling systems.
    """

    def __init__(
        self,
        callback: Callable[[StepContext, float], None] | None = None,
    ) -> None:
        """Initialize TimingStepMiddleware.

        Args:
            callback: Optional sync callable invoked with `(ctx, duration_seconds)`.
        """
        self._callback = callback

    def before(self, ctx: StepContext) -> Any:
        """Capture the start timestamp before downstream step execution.

        Args:
            ctx: Active StepContext.

        Returns:
            Float start timestamp from time.perf_counter().

        Notes/Architectural Intent:
            Samples high-precision monotonic clock to guarantee non-decreasing elapsed duration.
        """
        return time.perf_counter()

    def after(self, ctx: StepContext, result: Any, context: Any) -> Any:
        """Calculate elapsed duration and record metrics upon step completion.

        Args:
            ctx: Active StepContext.
            result: Result returned from downstream step execution.
            context: Start timestamp float returned by before().

        Returns:
            Unmodified result from downstream processing.

        Notes/Architectural Intent:
            Populates execution latency onto StepContext metadata and invokes optional metrics callback.
        """
        duration = time.perf_counter() - float(context)
        self._record_metrics(ctx, duration)
        return result

    def on_error(self, ctx: StepContext, exc: Exception, context: Any) -> None:
        """Record elapsed duration when downstream step raises an error.

        Args:
            ctx: Active StepContext.
            exc: Exception raised during execution.
            context: Start timestamp float returned by before().

        Notes/Architectural Intent:
            Guarantees duration metrics are captured for failed steps to maintain telemetry parity.
        """
        if context is not None:
            duration = time.perf_counter() - float(context)
            self._record_metrics(ctx, duration)

    def _record_metrics(self, ctx: StepContext, duration: float) -> None:
        """Helper method to populate context metadata and invoke metric callback.

        Args:
            ctx: Active StepContext.
            duration: Elapsed execution time in seconds.
        """
        ctx.metadata["duration_seconds"] = duration
        ctx.metadata["latency_ms"] = duration * 1000.0
        if self._callback is not None:
            with contextlib.suppress(Exception):
                self._callback(ctx, duration)


__all__ = [
    "TimingStepMiddleware",
]
