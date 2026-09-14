"""Unit tests for abstract engine port interface.

Notes/Architectural Intent:
    Verifies ABC inheritance and abstractmethod contracts for WorkflowEnginePort.
"""

from __future__ import annotations

import pytest

from hexaflow.ports.engine import WorkflowEnginePort


def test_cannot_instantiate_abstract_engine_port() -> None:
    """Validate that direct instantiation of WorkflowEnginePort raises TypeError."""
    with pytest.raises(TypeError):
        WorkflowEnginePort()  # type: ignore[abstract]
