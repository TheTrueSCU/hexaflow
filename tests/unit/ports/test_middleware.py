"""Unit tests for StepMiddlewarePort in hexaflow.ports.middleware.

Notes/Architectural Intent:
    Verifies that StepMiddlewarePort satisfies structural subtyping via runtime_checkable
    and can be implemented with asynchronous callables or classes.
"""

from collections.abc import Callable
from typing import Any

import pytest

from hexaflow.domain.state import StepContext
from hexaflow.ports.middleware import StepMiddlewarePort


class SampleClassMiddleware:
    """Sample class-based step middleware implementation."""

    async def __call__(
        self,
        ctx: StepContext,
        next_call: Callable[[StepContext], Any],
    ) -> Any:
        ctx.metadata["visited"] = True
        res = await next_call(ctx)
        return res


def test_step_middleware_port_runtime_checkable() -> None:
    """Validate that StepMiddlewarePort detects compliant and non-compliant objects."""
    mw = SampleClassMiddleware()
    is_instance = isinstance(mw, StepMiddlewarePort)
    assert is_instance is True

    class IncompleteMiddleware:
        def not_call(self) -> None:
            pass

    incomplete = IncompleteMiddleware()
    is_incomplete = isinstance(incomplete, StepMiddlewarePort)
    assert is_incomplete is False


@pytest.mark.asyncio
async def test_step_middleware_port_execution() -> None:
    """Validate execution of a compliant middleware implementation."""
    mw = SampleClassMiddleware()
    ctx = StepContext(
        run_id="run-1",
        stage_name="stage-1",
        step_name="step-1",
    )

    async def dummy_next(c: StepContext) -> str:
        return "step_result"

    res = await mw(ctx, dummy_next)
    assert res == "step_result"
    visited_flag = ctx.metadata.get("visited")
    assert visited_flag is True
