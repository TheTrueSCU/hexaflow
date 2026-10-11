"""Tests for DotGraphRendererAdapter in hexaflow.adapters.renderers.dot.

Notes/Architectural Intent:
    Verifies that DotGraphRendererAdapter generates valid Graphviz DOT syntax,
    supports subgraphs, critical path styling, legend, and handles missing 'dot'
    executable during image compilation.
"""

from unittest.mock import patch

import pytest

from hexaflow.adapters.renderers.dot import DotGraphRendererAdapter
from hexaflow.domain.exceptions import RendererToolNotFoundError
from hexaflow.domain.graph import WorkflowGraph
from hexaflow.domain.models import (
    ExecutionPool,
    StageDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from hexaflow.ports.renderer import RenderOptions


def dummy() -> None:
    pass


def sample_graph() -> WorkflowGraph:
    s1 = StepDefinition(name="a", action=dummy, pool=ExecutionPool.ASYNC)
    s2 = StepDefinition(name="b", action=dummy, depends_on=("a",), pool=ExecutionPool.THREAD)
    stage = StageDefinition(name="stage1", steps=(s1, s2))
    wf = WorkflowDefinition(name="dot_wf", stages=(stage,))
    return WorkflowGraph.from_workflow(wf)


def test_dot_renderer_format_name() -> None:
    adapter = DotGraphRendererAdapter()
    assert adapter.format_name == "dot"


def test_dot_render_default() -> None:
    adapter = DotGraphRendererAdapter()
    graph = sample_graph()
    output = adapter.render(graph)

    assert 'digraph "dot_wf"' in output
    assert 'rankdir="TB"' in output
    assert '"a" -> "b"' in output
    assert "Pool: ASYNC" in output or "Pool: THREAD" in output


def test_dot_render_without_pools() -> None:
    adapter = DotGraphRendererAdapter()
    graph = sample_graph()
    opts = RenderOptions(include_pools=False, direction="LR", include_legend=True)
    output = adapter.render(graph, opts)

    assert 'rankdir="LR"' in output
    assert 'subgraph "cluster_0"' not in output
    assert '"a"' in output
    assert '"b"' in output
    assert 'subgraph "cluster_legend"' in output


def test_dot_render_critical_path_and_highlight() -> None:
    adapter = DotGraphRendererAdapter()
    graph = sample_graph()
    opts = RenderOptions(
        highlight_critical_path=True,
        highlight_node_ids=frozenset({"a"}),
    )
    output = adapter.render(graph, opts)
    assert 'fillcolor="#ffecb3"' in output


def test_dot_render_image_tool_not_found() -> None:
    adapter = DotGraphRendererAdapter()
    graph = sample_graph()

    with patch("shutil.which", return_value=None):
        with pytest.raises(RendererToolNotFoundError) as exc_info:
            adapter.render_image(graph, "svg")
        assert "Graphviz 'dot' executable was not found" in str(exc_info.value)


def test_dot_render_image_success() -> None:
    adapter = DotGraphRendererAdapter()
    graph = sample_graph()

    with (
        patch("shutil.which", return_value="/usr/bin/dot"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b"<svg>mock</svg>"

        img = adapter.render_image(graph, "svg")
        assert img == b"<svg>mock</svg>"
        mock_run.assert_called_once()


def test_dot_render_image_failure() -> None:
    adapter = DotGraphRendererAdapter()
    graph = sample_graph()

    with (
        patch("shutil.which", return_value="/usr/bin/dot"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = b"syntax error"

        with pytest.raises(RuntimeError) as exc_info:
            adapter.render_image(graph, "png")
        assert "Graphviz 'dot' failed with exit code 1" in str(exc_info.value)


def test_dot_render_escaping() -> None:
    """Validate that special characters and quotes in step names are properly escaped."""
    s1 = StepDefinition(name='step "one"', action=dummy)
    s2 = StepDefinition(name="step\\two", action=dummy, depends_on=('step "one"',))
    wf = WorkflowDefinition(
        name='flow "main"',
        stages=(
            StageDefinition(name="s1", steps=(s1,)),
            StageDefinition(name="s2", steps=(s2,)),
        ),
    )
    graph = WorkflowGraph.from_workflow(wf)
    adapter = DotGraphRendererAdapter()
    output = adapter.render(graph)
    assert 'digraph "flow \\"main\\""' in output
    assert '"step \\"one\\"" -> "step\\\\two"' in output
