"""Unit tests for require_dependency and optional_dependency utilities."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hexaflow.domain.exceptions import MissingDependencyError, WorkflowError
from hexaflow.utils.imports import optional_dependency, require_dependency


def test_require_dependency_installed_module() -> None:
    """Test successful import of an installed module."""
    mod = require_dependency("json", extra="core", package="hexaflow")
    assert mod is not None
    assert hasattr(mod, "dumps")


def test_require_dependency_missing_module() -> None:
    """Test that importing a missing module raises an informative MissingDependencyError."""
    with (
        patch(
            "importlib.import_module",
            side_effect=ImportError("No module named 'nonexistent_lib'"),
        ),
        pytest.raises(
            MissingDependencyError,
            match=r"Optional dependency 'nonexistent_lib' is required\. Install with: pip install hexaflow\[sentry\]",
        ) as exc_info,
    ):
        require_dependency(
            "nonexistent_lib",
            extra="sentry",
            package="hexaflow",
        )

    err = exc_info.value
    assert isinstance(err, WorkflowError)
    assert isinstance(err, ImportError)
    assert err.__cause__ is not None


def test_require_dependency_missing_module_with_feature_name() -> None:
    """Test that feature_name is included in the error message when provided."""
    with (
        patch(
            "importlib.import_module",
            side_effect=ImportError("No module named 'sentry_sdk'"),
        ),
        pytest.raises(
            MissingDependencyError,
            match=r"Optional dependency 'sentry_sdk' is required for Sentry Tracing\. Install with: pip install hexaflow\[sentry\]",
        ),
    ):
        require_dependency(
            "sentry_sdk",
            extra="sentry",
            package="hexaflow",
            feature_name="Sentry Tracing",
        )


def test_optional_dependency_installed_module() -> None:
    """Test optional_dependency returns module when installed."""
    mod = optional_dependency("json")
    assert mod is not None
    assert hasattr(mod, "loads")


def test_optional_dependency_missing_module_default() -> None:
    """Test optional_dependency returns None or default when module is missing."""
    with patch(
        "importlib.import_module",
        side_effect=ModuleNotFoundError("No module named 'nonexistent_pkg'"),
    ):
        res_none = optional_dependency("nonexistent_pkg")
        assert res_none is None

        sentinel = object()
        res_default = optional_dependency("nonexistent_pkg", default=sentinel)
        assert res_default is sentinel
