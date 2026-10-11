"""Tests for hexaflow.adapters.renderers package exports and __all__ integrity.

Notes/Architectural Intent:
    Verifies that adapters.renderers.__all__ is correctly exported and sorted.
"""

import hexaflow.adapters.renderers as renderers_pkg


def test_renderers_package_exports() -> None:
    expected_symbols = [
        "AsciiGraphRendererAdapter",
        "default_renderer_registry",
        "DotGraphRendererAdapter",
        "GraphRendererRegistry",
        "JsonGraphRendererAdapter",
        "MermaidGraphRendererAdapter",
    ]
    assert renderers_pkg.__all__ == expected_symbols
    for symbol in expected_symbols:
        assert hasattr(renderers_pkg, symbol)
