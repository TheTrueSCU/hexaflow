"""Dynamic module import helpers and optional dependency guards.

Notes/Architectural Intent:
    Provides standardized runtime verification for optional package extras
    without introducing third-party dependencies to the hexaflow core engine.
"""

from __future__ import annotations

import importlib
from typing import Any

from hexaflow.domain.exceptions import MissingDependencyError


def optional_dependency(
    module_name: str,
    *,
    default: Any = None,
) -> Any:
    """Import an optional third-party module, returning a default value if unavailable.

    Args:
        module_name: Python module import path (e.g. 'sentry_sdk').
        default: Fallback value returned if the module is not installed (defaults to None).

    Returns:
        The imported module object, or `default` if ModuleNotFoundError is encountered.

    Notes/Architectural Intent:
        Complements `require_dependency` by providing a safe, clean mechanism for
        progressive feature enhancement in telemetry, tracing, and diagnostics
        without boilerplate try/except blocks.
    """
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        return default


def require_dependency(
    module_name: str,
    *,
    extra: str,
    package: str = "hexaflow",
    feature_name: str | None = None,
) -> Any:
    """Import an optional third-party module or raise MissingDependencyError.

    Args:
        module_name: Python module import path (e.g. 'sentry_sdk').
        extra: Subpackage optional extra name (e.g. 'sentry').
        package: PyPI package name (defaults to 'hexaflow').
        feature_name: Optional human-readable feature/component name for error context.

    Returns:
        The imported module object.

    Raises:
        MissingDependencyError: If the requested module is not installed.

    Notes/Architectural Intent:
        Standardizes optional dependency verification across all workflow adapters,
        ensuring actionable error messages directing users to the exact `pip install <package>[<extra>]`
        command required.
    """
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        context = f" for {feature_name}" if feature_name else ""
        raise MissingDependencyError(
            f"Optional dependency '{module_name}' is required{context}. "
            f"Install with: pip install {package}[{extra}]"
        ) from exc


__all__ = [
    "optional_dependency",
    "require_dependency",
]
