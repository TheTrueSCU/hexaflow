"""Unit tests for abstract storage port interface.

Notes/Architectural Intent:
    Verifies ABC inheritance and abstractmethod contracts for WorkflowStateStorePort.
"""

from __future__ import annotations

import pytest

from hexaflow.ports.storage import WorkflowStateStorePort


def test_cannot_instantiate_abstract_storage_port() -> None:
    """Validate that direct instantiation of WorkflowStateStorePort raises TypeError."""
    with pytest.raises(TypeError):
        WorkflowStateStorePort()  # type: ignore[abstract]
