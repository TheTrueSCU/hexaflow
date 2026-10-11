"""Tests for JsonGraphRendererAdapter in hexaflow.adapters.renderers.json.

Notes/Architectural Intent:
    Verifies that JsonGraphRendererAdapter serializes WorkflowGraph into structured JSON
    including node attributes, edges, critical paths, and binary serialization.
"""

import json

from hexaflow.adapters.renderers.json import JsonGraphRendererAdapter
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
    s1 = StepDefinition(name="load", action=dummy, estimated_duration_seconds=3.0)
    s2 = StepDefinition(
        name="transform", action=dummy, depends_on=("load",), estimated_duration_seconds=4.0
    )
    stage = StageDefinition(name="etl", steps=(s1, s2))
    wf = WorkflowDefinition(name="json_wf", stages=(stage,))
    return WorkflowGraph.from_workflow(wf)


def test_json_renderer_format_name() -> None:
    adapter = JsonGraphRendererAdapter()
    assert adapter.format_name == "json"


def test_json_render_structure() -> None:
    adapter = JsonGraphRendererAdapter()
    graph = sample_graph()
    opts = RenderOptions(highlight_critical_path=True, highlight_node_ids=frozenset({"load"}))
    output = adapter.render(graph, opts)

    data = json.loads(output)
    assert data["workflow_id"] == "json_wf"
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["source"] == "load"
    assert data["edges"][0]["target"] == "transform"
    assert data["critical_path"] == ["load", "transform"]

    load_node = next(n for n in data["nodes"] if n["id"] == "load")
    assert load_node["is_critical_path"] is True
    assert load_node["is_highlighted"] is True
    assert load_node["estimated_duration_seconds"] == 3.0


def test_json_render_image_bytes() -> None:
    adapter = JsonGraphRendererAdapter()
    graph = sample_graph()
    img_bytes = adapter.render_image(graph)

    assert isinstance(img_bytes, bytes)
    parsed = json.loads(img_bytes.decode("utf-8"))
    assert parsed["workflow_id"] == "json_wf"
