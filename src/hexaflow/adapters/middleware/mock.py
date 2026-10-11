"""Simulation and mock fallback step middleware interceptor for hexaflow workflows.

Notes/Architectural Intent:
    Participates in dry-run simulation mode, intercepting execution or DryRunUnsafeStepError
    to supply synthetic or schema-compliant fallback outputs for steps lacking explicit
    mock definitions, facilitating full-system simulations without boilerplate.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import (
    Any,
    cast,
)

from hexaflow.domain.exceptions import DryRunUnsafeStepError
from hexaflow.domain.state import StepContext
from hexaflow.ports.middleware import StepMiddlewarePort


class MockFallbackMiddleware(StepMiddlewarePort):
    """Step middleware supplying mock and synthetic fallback outputs during dry-run simulations.

    Notes/Architectural Intent:
        Enables frictionless whole-system dry-run simulations. When an unmocked side-effecting
        step triggers DryRunUnsafeStepError during dry-run simulation, this middleware intercepts
        the failure and returns a configured fallback result or synthesized dictionary, marking
        `ctx.metadata["mock_applied"] = True`.
    """

    def __init__(
        self,
        fallback_factory: Callable[[StepContext], Any] | None = None,
        step_fallbacks: dict[str, Any] | None = None,
        default_value: Any | None = None,
    ) -> None:
        """Initialize MockFallbackMiddleware.

        Args:
            fallback_factory: Optional callable taking StepContext and returning a mock result.
            step_fallbacks: Optional dictionary mapping step_name to static mock return value.
            default_value: Default static fallback when no specific mapping or factory matches.
        """
        self._fallback_factory = fallback_factory
        self._step_fallbacks = dict(step_fallbacks or {})
        self._default_value = default_value

    def _resolve_fallback(self, ctx: StepContext) -> Any:
        """Resolve the synthetic or mock fallback payload for the given step.

        Args:
            ctx: Active StepContext.

        Returns:
            The resolved mock fallback value.
        """
        if ctx.step_name in self._step_fallbacks:
            return self._step_fallbacks[ctx.step_name]
        if self._fallback_factory is not None:
            return self._fallback_factory(ctx)
        if self._default_value is not None:
            return self._default_value
        return {"mock": True, "step": ctx.step_name}

    def __call__[R](
        self,
        ctx: StepContext,
        next_call: Callable[[StepContext], R],
    ) -> R:
        """Intercept step execution, rescuing unsafe unmocked steps in dry-run mode.

        Args:
            ctx: Active StepContext.
            next_call: Callable representing remaining middleware chain or step action.

        Returns:
            The outcome payload of step execution or the synthesized fallback value.

        Raises:
            Exception: Any exception other than intercepted dry-run simulation errors.
        """
        try:
            result = next_call(ctx)
        except DryRunUnsafeStepError:
            fallback = self._resolve_fallback(ctx)
            ctx.metadata["mock_applied"] = True
            ctx.metadata["fallback_value"] = fallback
            return cast("R", fallback)

        if inspect.isawaitable(result):

            async def _async_wrap() -> Any:
                try:
                    return await result
                except DryRunUnsafeStepError:
                    fb = self._resolve_fallback(ctx)
                    ctx.metadata["mock_applied"] = True
                    ctx.metadata["fallback_value"] = fb
                    return fb

            return cast("R", _async_wrap())

        return result


__all__ = [
    "MockFallbackMiddleware",
]
