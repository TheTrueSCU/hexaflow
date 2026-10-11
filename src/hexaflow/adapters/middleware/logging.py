"""Logging step middleware interceptor for hexaflow workflows.

Notes/Architectural Intent:
    Emits structured diagnostic logging records before and after each step execution,
    matching Hexastack LoggingMiddleware and InOutMiddleware pattern.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from hexaflow.adapters.middleware.base import InOutStepMiddleware
from hexaflow.domain.state import StepContext


class LoggingStepMiddleware(InOutStepMiddleware):
    """Step middleware emitting structured logging around step execution.

    Notes/Architectural Intent:
        Inherits from InOutStepMiddleware to emit structured log records before and
        after step action execution with wall-clock duration.
    """

    def __init__(
        self,
        logger: logging.Logger | None = None,
        level: int = logging.INFO,
        include_inputs: bool = False,
    ) -> None:
        """Initialize LoggingStepMiddleware.

        Args:
            logger: Optional custom standard library Logger instance. Defaults to 'hexaflow.step'.
            level: Logging severity level for success notices. Defaults to logging.INFO.
            include_inputs: Whether to log input dictionary keys.
        """
        self._logger = logger or logging.getLogger("hexaflow.step")
        self._level = level
        self._include_inputs = include_inputs

    def before(self, ctx: StepContext) -> Any:
        """Log step entry before downstream step execution.

        Args:
            ctx: Active StepContext.

        Returns:
            Start timestamp from time.perf_counter().

        Notes/Architectural Intent:
            Emits debug log event announcing step entry and starts monotonic duration timer.
        """
        step_id = f"{ctx.stage_name}.{ctx.step_name}"
        inputs_msg = f" inputs={list(ctx.inputs.keys())}" if self._include_inputs else ""
        self._logger.log(
            logging.DEBUG,
            f"Step starting: {step_id} (run_id={ctx.run_id}, attempt={ctx.attempt_number}){inputs_msg}",
        )
        return time.perf_counter()

    def after(self, ctx: StepContext, result: Any, context: Any) -> Any:
        """Log successful step completion and duration.

        Args:
            ctx: Active StepContext.
            result: Result returned from downstream step execution.
            context: Start timestamp float returned by before().

        Returns:
            Unmodified result from downstream processing.

        Notes/Architectural Intent:
            Emits structured log record at configured severity with measured step execution latency.
        """
        step_id = f"{ctx.stage_name}.{ctx.step_name}"
        duration_ms = (time.perf_counter() - float(context)) * 1000.0 if context else 0.0
        self._logger.log(
            self._level,
            f"Step completed: {step_id} in {duration_ms:.2f}ms (run_id={ctx.run_id})",
        )
        return result

    def on_error(self, ctx: StepContext, exc: Exception, context: Any) -> None:
        """Log execution error and duration.

        Args:
            ctx: Active StepContext.
            exc: Exception raised during execution.
            context: Start timestamp float returned by before().

        Notes/Architectural Intent:
            Emits structured error log with exception stack trace and execution duration prior to failure.
        """
        step_id = f"{ctx.stage_name}.{ctx.step_name}"
        duration_ms = (time.perf_counter() - float(context)) * 1000.0 if context else 0.0
        self._logger.error(
            f"Step failed: {step_id} after {duration_ms:.2f}ms with {type(exc).__name__}: {exc} (run_id={ctx.run_id})",
            exc_info=True,
        )


__all__ = [
    "LoggingStepMiddleware",
]
