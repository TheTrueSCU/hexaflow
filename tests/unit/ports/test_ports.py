"""Unit tests for abstract port interfaces.

Notes/Architectural Intent:
    Verifies ABC inheritance and abstractmethod contracts for engine and storage ports.
"""

import pytest

from hexaflow.ports.engine import WorkflowEnginePort
from hexaflow.ports.storage import WorkflowStateStorePort


def test_cannot_instantiate_abstract_ports() -> None:
    """Validate that direct instantiation of ABC ports raises TypeError."""
    with pytest.raises(TypeError):
        WorkflowStateStorePort()  # type: ignore[abstract]

    with pytest.raises(TypeError):
        WorkflowEnginePort()  # type: ignore[abstract]
