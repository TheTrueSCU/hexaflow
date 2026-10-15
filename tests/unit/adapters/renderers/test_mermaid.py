"""Tests for MermaidGraphRendererAdapter in hexaflow.adapters.renderers.mermaid.

Notes/Architectural Intent:
    Verifies that MermaidGraphRendererAdapter generates valid Mermaid flowchart syntax,
    supports subgraphs, class styling for critical path, and handles CLI image compilation.
"""

from unittest.mock import patch

import pytest

from hexaflow.adapters.renderers.mermaid import MermaidGraphRendererAdapter
from hexaflow.domain.exceptions import RendererToolNotFoundError
from hexaflow.domain.graph import WorkflowGraph
from hexaflow.domain.models import (
    StageDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from hexaflow.ports.renderer import RenderOptions


def dummy() -> None:
    pass


def sample_graph() -> WorkflowGraph:
    s1 = StepDefinition(name="fetch", action=dummy)
    s2 = StepDefinition(name="process", action=dummy, depends_on=("fetch",), side_effects=True)
    stage = StageDefinition(name="pipeline", steps=(s1, s2))
    wf = WorkflowDefinition(name="mermaid_wf", stages=(stage,))
    return WorkflowGraph.from_workflow(wf)


def test_mermaid_renderer_format_name() -> None:
    adapter = MermaidGraphRendererAdapter()
    assert adapter.format_name == "mermaid"


def test_mermaid_render_default() -> None:
    adapter = MermaidGraphRendererAdapter()
    graph = sample_graph()
    output = adapter.render(graph)

    assert "flowchart TD" in output
    assert "subgraph pool_0" in output
    assert "fetch --> process" in output
    assert "⚡" in output


def test_mermaid_render_without_pools() -> None:
    adapter = MermaidGraphRendererAdapter()
    graph = sample_graph()
    opts = RenderOptions(include_pools=False, direction="LR")
    output = adapter.render(graph, opts)

    assert "flowchart LR" in output
    assert "subgraph" not in output
    assert "fetch[fetch]" in output or 'fetch["fetch"]' in output


def test_mermaid_render_critical_path_and_highlight() -> None:
    adapter = MermaidGraphRendererAdapter()
    graph = sample_graph()
    opts = RenderOptions(
        highlight_critical_path=True,
        highlight_node_ids=frozenset({"fetch"}),
    )
    output = adapter.render(graph, opts)

    assert "classDef critical" in output
    assert "class" in output


def test_mermaid_render_image_tool_not_found() -> None:
    adapter = MermaidGraphRendererAdapter()
    graph = sample_graph()

    with patch("shutil.which", return_value=None):
        with pytest.raises(RendererToolNotFoundError) as exc_info:
            adapter.render_image(graph, "svg")
        assert "Mermaid CLI executable 'mmdc' was not found" in str(exc_info.value)


def test_mermaid_render_image_success() -> None:
    adapter = MermaidGraphRendererAdapter()
    graph = sample_graph()

    with (
        patch("shutil.which", return_value="/usr/local/bin/mmdc"),
        patch("subprocess.run") as mock_run,
        patch("pathlib.Path.read_bytes", return_value=b"<svg>mermaid</svg>"),
    ):
        mock_run.return_value.returncode = 0
        img = adapter.render_image(graph, "svg")
        assert img == b"<svg>mermaid</svg>"


def test_mermaid_render_image_failure() -> None:
    adapter = MermaidGraphRendererAdapter()
    graph = sample_graph()

    with (
        patch("shutil.which", return_value="/usr/local/bin/mmdc"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = b"CLI error"

        with pytest.raises(RuntimeError) as exc_info:
            adapter.render_image(graph, "png")
        assert "Mermaid CLI 'mmdc' failed with exit code 1" in str(exc_info.value)


def test_mermaid_render_id_sanitization() -> None:
    """Validate that node names with spaces and reserved tokens produce legal Mermaid syntax."""
    s1 = StepDefinition(name='step one "test"', action=dummy)
    s2 = StepDefinition(name="end", action=dummy, depends_on=('step one "test"',))
    wf = WorkflowDefinition(
        name="flow",
        stages=(
            StageDefinition(name="s1", steps=(s1,)),
            StageDefinition(name="s2", steps=(s2,)),
        ),
    )
    graph = WorkflowGraph.from_workflow(wf)
    adapter = MermaidGraphRendererAdapter()
    output = adapter.render(graph)
    assert "step_one__test_" in output
    assert "end" in output
    assert "step_one__test_ --> end" in output
    assert "#quot;" in output
