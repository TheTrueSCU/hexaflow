"""JSON graph adapter for exporting workflow graphs to UI visualizers and web canvases.

Notes/Architectural Intent:
    Serializes WorkflowGraph into structured JSON format compatible with Cytoscape.js,
    D3.js, NiceGUI, and front-end diagram engines.
"""

import json
from typing import (
    Any,
    Literal,
)

from hexaflow.domain.graph import (
    GraphNode,
    WorkflowGraph,
)
from hexaflow.ports.renderer import (
    GraphRendererPort,
    RenderOptions,
)


class JsonGraphRendererAdapter(GraphRendererPort):
    """Adapter for serializing WorkflowGraph into JSON specifications.

    Notes/Architectural Intent:
        Implements GraphRendererPort. Produces node/edge collections with metadata
        including pool designations, duration estimates, trigger rules, and critical path flags.
    """

    @property
    def format_name(self) -> str:
        """Returns format name.

        Returns:
            The string 'json'.
        """
        return "json"

    def render(
        self,
        graph: WorkflowGraph,
        options: RenderOptions | None = None,
    ) -> str:
        """Renders the workflow graph as structured JSON.

        Args:
            graph: The WorkflowGraph to render.
            options: Optional RenderOptions.

        Returns:
            Formatted JSON string.
        """
        opts = options or RenderOptions()
        crit_set = set(graph.critical_path()) if opts.highlight_critical_path else set()

        nodes_data: list[dict[str, Any]] = []
        for node in graph.nodes.values():
            nodes_data.append(self._serialize_node(node, opts, crit_set))

        edges_data: list[dict[str, Any]] = []
        for source_id, target_id in graph.edges:
            edges_data.append(
                {
                    "source": source_id,
                    "target": target_id,
                    "is_critical": (source_id in crit_set and target_id in crit_set),
                }
            )

        data = {
            "workflow_id": graph.workflow_id,
            "direction": opts.direction,
            "nodes": nodes_data,
            "edges": edges_data,
            "critical_path": list(graph.critical_path()),
        }
        return json.dumps(data, indent=2) + "\n"

    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        """Serializes to raw JSON bytes.

        Args:
            graph: The WorkflowGraph to render.
            image_format: Ignored for JSON renderer.
            options: Optional RenderOptions.

        Returns:
            UTF-8 encoded JSON bytes.
        """
        return self.render(graph, options).encode("utf-8")

    def _serialize_node(
        self,
        node: GraphNode,
        opts: RenderOptions,
        crit_set: set[str],
    ) -> dict[str, Any]:
        """Serializes a GraphNode into a dictionary."""
        is_highlighted = node.step_id in opts.highlight_node_ids or (
            opts.highlight_critical_path and node.step_id in crit_set
        )
        return {
            "id": node.step_id,
            "trigger_rule": node.trigger_rule.value,
            "side_effects": node.step.side_effects,
            "estimated_duration_seconds": node.step.estimated_duration_seconds,
            "pool": node.step.pool.name if node.step.pool else None,
            "is_critical_path": node.step_id in crit_set,
            "is_highlighted": is_highlighted,
        }
