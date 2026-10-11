"""Port contract for step execution middleware interceptors.

Notes/Architectural Intent:
    Follows the onion-layered interceptor pattern matching Hexastack MiddlewarePort.
    Each middleware wraps downstream middlewares and ultimately the target step action.
    Enables cross-cutting concerns like structured logging, OpenTelemetry tracing,
    timeout guards, audit records, synthetic mock data injection, and performance profiling.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import (
    Protocol,
    runtime_checkable,
)

from hexaflow.domain.state import StepContext


@runtime_checkable
class StepMiddlewarePort(Protocol):
    """Protocol defining workflow step execution middleware interceptors.

    Notes/Architectural Intent:
        Establishes an onion-layered interceptor contract for workflow steps.
        Decouples the workflow execution engine from concrete middleware implementations.
        Using Protocol enables functions, lambdas, and classes with __call__ to seamlessly
        satisfy the port contract.
    """

    def __call__[R](
        self,
        ctx: StepContext,
        next_call: Callable[[StepContext], R],
    ) -> R:
        """Invoke middleware logic and pass execution to next_call.

        Args:
            ctx: Active StepContext providing step identity, metadata, and resolved inputs.
            next_call: Callable representing the remaining middleware/step action chain.

        Returns:
            The outcome payload of step execution or an awaitable coroutine.

        Raises:
            Exception: Any exception raised by the step action or downstream middlewares.
        """
        ...


__all__ = [
    "StepMiddlewarePort",
]
