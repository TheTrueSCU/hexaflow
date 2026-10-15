"""Tests for AsciiGraphRendererAdapter in hexaflow.adapters.renderers.ascii.

Notes/Architectural Intent:
    Verifies that AsciiGraphRendererAdapter generates readable Unicode/ASCII tree diagrams,
    handles critical path tags and annotations, and emits standalone SVG.
"""

from hexaflow.adapters.renderers.ascii import AsciiGraphRendererAdapter
from hexaflow.domain.graph import WorkflowGraph
from hexaflow.domain.models import (
    StageDefinition,
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
)
from hexaflow.ports.renderer import RenderOptions


def dummy() -> None:
    pass


def sample_graph() -> WorkflowGraph:
    s1 = StepDefinition(name="root_step", action=dummy)
    s2 = StepDefinition(
        name="child_step",
        action=dummy,
        depends_on=("root_step",),
        trigger_rule=TriggerRule.ALL_FAILED,
        side_effects=True,
    )
    stage = StageDefinition(name="stg", steps=(s1, s2))
    wf = WorkflowDefinition(name="ascii_wf", stages=(stage,))
    return WorkflowGraph.from_workflow(wf)


def test_ascii_renderer_format_name() -> None:
    adapter = AsciiGraphRendererAdapter()
    assert adapter.format_name == "ascii"


def test_ascii_render_tree() -> None:
    adapter = AsciiGraphRendererAdapter()
    graph = sample_graph()
    output = adapter.render(graph)

    assert "Workflow: ascii_wf" in output
    assert "root_step" in output
    assert "child_step" in output
    assert "SIDE-EFFECT" in output
    assert "ALL_FAILED" in output


def test_ascii_render_critical_and_highlight() -> None:
    adapter = AsciiGraphRendererAdapter()
    graph = sample_graph()
    opts = RenderOptions(
        highlight_critical_path=True,
        highlight_node_ids=frozenset({"root_step"}),
    )
    output = adapter.render(graph, opts)

    assert "CRITICAL" in output
    assert "HIGHLIGHT" in output


def test_ascii_render_image_svg() -> None:
    adapter = AsciiGraphRendererAdapter()
    graph = sample_graph()
    img = adapter.render_image(graph, "svg")

    assert img.startswith(b"<svg")
    assert b"ascii_wf" in img


def test_ascii_render_image_png_raises() -> None:
    import pytest

    adapter = AsciiGraphRendererAdapter()
    graph = sample_graph()
    with pytest.raises(ValueError, match="only supports 'svg'"):
        adapter.render_image(graph, "png")  # type: ignore[arg-type]
