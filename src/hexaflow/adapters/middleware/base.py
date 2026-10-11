"""Base InOutStepMiddleware implementing lifecycle hooks matching Hexastack InOutMiddleware.

Notes/Architectural Intent:
    Implements the Template Method pattern over StepMiddlewarePort. Subclasses
    override before(), after(), and/or on_error() hooks without needing to
    manage synchronous vs. asynchronous coroutine unwrapping and closure wrapping.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import (
    Any,
    cast,
)

from hexaflow.domain.state import StepContext
from hexaflow.ports.middleware import StepMiddlewarePort


class InOutStepMiddleware(StepMiddlewarePort):
    """Base class for step middleware with before, after, and on_error lifecycle hooks.

    Notes/Architectural Intent:
        Implements the Template Method pattern over StepMiddlewarePort. Subclasses
        override before(), after(), and/or on_error() hooks without needing to
        manage synchronous vs. asynchronous coroutine unwrapping and closure wrapping.
        Mirrors Hexastack InOutMiddleware.
    """

    def before(self, ctx: StepContext) -> Any:
        """Pre-execution hook executed before downstream step processing.

        Args:
            ctx: Active StepContext.

        Returns:
            Optional context object passed to subsequent after() and on_error() hooks.

        Notes/Architectural Intent:
            Subclasses override this to initialize state, start timers, or record telemetry.
        """
        return None

    def after(self, ctx: StepContext, result: Any, context: Any) -> Any:
        """Post-execution hook executed after successful step completion.

        Args:
            ctx: Active StepContext.
            result: Result returned by downstream step action.
            context: Context object returned by before().

        Returns:
            The final result returned to the caller (can transform result if needed).

        Notes/Architectural Intent:
            Subclasses override this to record success metrics or transform payload output.
        """
        return result

    def on_error(self, ctx: StepContext, exc: Exception, context: Any) -> None:
        """Error hook executed when downstream execution raises an unhandled exception.

        Args:
            ctx: Active StepContext.
            exc: Exception raised by downstream step action.
            context: Context object returned by before().

        Notes/Architectural Intent:
            Subclasses override this to log exceptions or emit error diagnostic telemetry.
        """

    def __call__[R](
        self,
        ctx: StepContext,
        next_call: Callable[[StepContext], R],
    ) -> R:
        """Execute template lifecycle wrapping next_call.

        Args:
            ctx: Active StepContext.
            next_call: Next middleware or step action in the chain.

        Returns:
            Result returned by after() or an async coroutine wrapping the execution.

        Raises:
            Exception: Propagates unhandled exceptions raised during execution.
        """
        context = self.before(ctx)
        try:
            result = next_call(ctx)
        except Exception as exc:
            self.on_error(ctx, exc, context)
            raise

        if inspect.isawaitable(result):

            async def _async_wrap() -> Any:
                try:
                    res = await result
                    return self.after(ctx, res, context)
                except Exception as async_exc:
                    self.on_error(ctx, async_exc, context)
                    raise

            return cast("R", _async_wrap())

        return self.after(ctx, result, context)


__all__ = [
    "InOutStepMiddleware",
]
