"""Tests for GraphRendererRegistry in hexaflow.adapters.renderers.registry.

Notes/Architectural Intent:
    Verifies that GraphRendererRegistry registers and retrieves renderers,
    lists available formats, and raises KeyError on unknown formats.
"""

from typing import Literal

import pytest

from hexaflow.adapters.renderers.registry import (
    GraphRendererRegistry,
    default_renderer_registry,
)
from hexaflow.domain.graph import WorkflowGraph
from hexaflow.ports.renderer import (
    GraphRendererPort,
    RenderOptions,
)


class CustomRenderer(GraphRendererPort):
    @property
    def format_name(self) -> str:
        return "custom_fmt"

    def render(self, graph: WorkflowGraph, options: RenderOptions | None = None) -> str:
        return "custom"

    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        return b"custom_image"


def test_default_registry_contents() -> None:
    formats = default_renderer_registry.available_formats()
    assert "ascii" in formats
    assert "dot" in formats
    assert "json" in formats
    assert "mermaid" in formats

    dot_renderer = default_renderer_registry.get("dot")
    assert dot_renderer.format_name == "dot"

    mermaid_renderer = default_renderer_registry.get("MERMAID")
    assert mermaid_renderer.format_name == "mermaid"


def test_registry_unknown_format() -> None:
    registry = GraphRendererRegistry()
    with pytest.raises(KeyError) as exc_info:
        registry.get("nonexistent")
    assert "Unsupported graph renderer format 'nonexistent'" in str(exc_info.value)


def test_registry_custom_registration() -> None:
    registry = GraphRendererRegistry()
    custom = CustomRenderer()
    registry.register(custom)

    retrieved = registry.get("custom_fmt")
    assert retrieved is custom
    assert "custom_fmt" in registry.available_formats()
