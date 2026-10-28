"""Unit tests for hexaflow.utils package exports."""

from __future__ import annotations

import hexaflow.utils as utils


def test_utils_exports() -> None:
    """Verify that utils exports required helpers."""
    assert hasattr(utils, "optional_dependency")
    assert hasattr(utils, "require_dependency")
