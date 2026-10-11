"""Tests for GraphRendererPort and RenderOptions in ports/renderer.py.

Notes/Architectural Intent:
    Verifies that RenderOptions maintains immutable defaults and custom options,
    and GraphRendererPort abstract contract behaves properly.
"""

from typing import Literal

from hexaflow.domain.graph import WorkflowGraph
from hexaflow.ports.renderer import (
    GraphRendererPort,
    RenderOptions,
)


class DummyRenderer(GraphRendererPort):
    """Concrete stub implementing GraphRendererPort for contract validation."""

    @property
    def format_name(self) -> str:
        return "dummy"

    def render(self, graph: WorkflowGraph, options: RenderOptions | None = None) -> str:
        return f"rendered:{graph.workflow_id}"

    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        return f"image:{graph.workflow_id}:{image_format}".encode()


def test_render_options_defaults() -> None:
    """Test default values of RenderOptions."""
    opts = RenderOptions()

    direction = opts.direction
    assert direction == "TD"

    pools = opts.include_pools
    assert pools is True

    legend = opts.include_legend
    assert legend is False

    crit = opts.highlight_critical_path
    assert crit is False

    nodes = opts.highlight_node_ids
    assert nodes == frozenset()

    theme = opts.theme
    assert theme == "default"


def test_render_options_custom() -> None:
    """Test custom configuration of RenderOptions."""
    opts = RenderOptions(
        direction="LR",
        include_pools=False,
        include_legend=True,
        highlight_critical_path=True,
        highlight_node_ids=frozenset({"node_1", "node_2"}),
        theme="dark",
    )

    direction = opts.direction
    assert direction == "LR"

    pools = opts.include_pools
    assert pools is False

    legend = opts.include_legend
    assert legend is True

    crit = opts.highlight_critical_path
    assert crit is True

    nodes = opts.highlight_node_ids
    assert nodes == frozenset({"node_1", "node_2"})

    theme = opts.theme
    assert theme == "dark"


def test_dummy_renderer_port_contract() -> None:
    """Test that a concrete implementation of GraphRendererPort fulfills port methods."""
    renderer = DummyRenderer()
    name = renderer.format_name
    assert name == "dummy"

    wf = WorkflowGraph(
        workflow_name="test_wf",
        workflow_version="1.0.0",
        nodes={},
        stages={},
        topological_order=(),
    )

    output = renderer.render(wf)
    assert output == "rendered:test_wf"

    img = renderer.render_image(wf, image_format="png")
    assert img == b"image:test_wf:png"
