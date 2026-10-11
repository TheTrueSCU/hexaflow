"""Graphviz DOT adapter for rendering workflow graphs.

Notes/Architectural Intent:
    Renders WorkflowGraph into Graphviz DOT syntax. Supports subgraphs for execution pools,
    distinct node styling for trigger rules, blast-radius / critical path highlighting,
    and optional PNG/SVG compilation via the system 'dot' executable.
"""

import shutil
import subprocess
from typing import Literal

from hexaflow.domain.exceptions import RendererToolNotFoundError
from hexaflow.domain.graph import (
    GraphNode,
    WorkflowGraph,
)
from hexaflow.domain.models import TriggerRule
from hexaflow.ports.renderer import (
    GraphRendererPort,
    RenderOptions,
)


class DotGraphRendererAdapter(GraphRendererPort):
    """Adapter for rendering WorkflowGraph into Graphviz DOT format.

    Notes/Architectural Intent:
        Implements GraphRendererPort. Generates clean DOT syntax with HTML-like or record
        labels, node styling, and edge connections. When compiling to image, invokes
        `dot -T<format>`.
    """

    @property
    def format_name(self) -> str:
        """Returns format name.

        Returns:
            The string 'dot'.
        """
        return "dot"

    def render(
        self,
        graph: WorkflowGraph,
        options: RenderOptions | None = None,
    ) -> str:
        """Renders the workflow graph as a Graphviz DOT digraph.

        Args:
            graph: The WorkflowGraph to render.
            options: Optional RenderOptions.

        Returns:
            Valid DOT digraph specification as a string.
        """
        opts = options or RenderOptions()
        rankdir = "LR" if opts.direction in ("LR", "RL") else "TB"
        critical_path_edges = self._extract_critical_path_edges(graph, opts)

        lines: list[str] = [
            f'digraph "{graph.workflow_id}" {{',
            f'    rankdir="{rankdir}";',
            '    node [shape="box", style="rounded,filled", fontname="Helvetica", fontsize=10];',
            '    edge [fontname="Helvetica", fontsize=8];',
            "",
        ]

        if opts.include_pools:
            lines.extend(self._render_pools(graph, opts))
        else:
            for node in graph.nodes.values():
                lines.append(f"    {self._render_node(node, opts)};")

        lines.append("")
        for source_id, target_id in graph.edges:
            edge_attrs: list[str] = []
            if (source_id, target_id) in critical_path_edges:
                edge_attrs.extend(['color="#e63946"', 'penwidth="2.5"'])
            else:
                edge_attrs.extend(['color="#6c757d"', 'penwidth="1.0"'])

            attr_str = f" [{', '.join(edge_attrs)}]" if edge_attrs else ""
            lines.append(f'    "{source_id}" -> "{target_id}"{attr_str};')

        if opts.include_legend:
            lines.append("")
            lines.extend(self._render_legend())

        lines.append("}")
        return "\n".join(lines) + "\n"

    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        """Compiles the workflow graph into an image binary (PNG or SVG) via 'dot'.

        Args:
            graph: The WorkflowGraph to render.
            image_format: Target image format ('png' or 'svg').
            options: Optional RenderOptions configuration.

        Returns:
            Raw image bytes.

        Raises:
            RendererToolNotFoundError: If 'dot' is not found in PATH.
            RuntimeError: If 'dot' command fails.
        """
        if not shutil.which("dot"):
            raise RendererToolNotFoundError(
                "Graphviz 'dot' executable was not found in system PATH. "
                "Please install Graphviz (e.g., 'apt-get install graphviz' or 'brew install graphviz')."
            )

        dot_source = self.render(graph, options)
        cmd = ["dot", f"-T{image_format}"]
        result = subprocess.run(
            cmd,
            input=dot_source.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            error_msg = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Graphviz 'dot' failed with exit code {result.returncode}: {error_msg}"
            )

        return result.stdout

    def _render_pools(self, graph: WorkflowGraph, opts: RenderOptions) -> list[str]:
        """Groups nodes into subgraphs by execution pool."""
        pool_nodes: dict[str, list[GraphNode]] = {}
        for node in graph.nodes.values():
            pool_name = node.step.pool.name if node.step.pool else "default"
            pool_nodes.setdefault(pool_name, []).append(node)

        lines: list[str] = []
        for cluster_idx, (pool_name, nodes) in enumerate(pool_nodes.items()):
            lines.append(f'    subgraph "cluster_{cluster_idx}" {{')
            lines.append(f'        label="Pool: {pool_name}";')
            lines.append('        style="dashed,rounded";')
            lines.append('        color="#b0bec5";')
            lines.append('        bgcolor="#f8f9fa";')
            for node in nodes:
                lines.append(f"        {self._render_node(node, opts)};")
            lines.append("    }")
        return lines

    def _render_node(self, node: GraphNode, opts: RenderOptions) -> str:
        """Formats a single node definition with colors and label."""
        is_highlighted = node.step_id in opts.highlight_node_ids or (
            opts.highlight_critical_path and node.is_critical_path
        )

        fillcolor = "#e3f2fd"
        color = "#1976d2"
        if is_highlighted:
            fillcolor = "#ffecb3"
            color = "#f57c00"

        rule_label = (
            f"\\n[{node.trigger_rule.value}]"
            if node.trigger_rule != TriggerRule.ALL_SUCCESS
            else ""
        )
        effects = " ⚡" if node.step.side_effects else ""
        duration = (
            f" ({node.step.estimated_duration_seconds:.1f}s)"
            if node.step.estimated_duration_seconds > 0
            else ""
        )
        label = f"{node.step_id}{effects}{rule_label}{duration}"

        return f'"{node.step_id}" [label="{label}", fillcolor="{fillcolor}", color="{color}", penwidth={"2.0" if is_highlighted else "1.0"}]'

    def _extract_critical_path_edges(
        self,
        graph: WorkflowGraph,
        opts: RenderOptions,
    ) -> set[tuple[str, str]]:
        """Identifies edges on the critical path if highlighting is requested."""
        if not opts.highlight_critical_path:
            return set()

        crit_path = graph.critical_path()
        edges: set[tuple[str, str]] = set()
        for idx in range(len(crit_path) - 1):
            src, dst = crit_path[idx], crit_path[idx + 1]
            if (src, dst) in graph.edges:
                edges.add((src, dst))
        return edges

    def _render_legend(self) -> list[str]:
        """Renders a legend subgraph explaining visual conventions."""
        return [
            '    subgraph "cluster_legend" {',
            '        label="Legend";',
            '        style="dotted";',
            '        color="#cfd8dc";',
            "        node [fontsize=8];",
            '        "leg_std" [label="Standard Step", fillcolor="#e3f2fd", color="#1976d2"];',
            '        "leg_crit" [label="Critical Path / Highlight", fillcolor="#ffecb3", color="#f57c00", penwidth="2.0"];',
            '        "leg_std" -> "leg_crit" [style="invis"];',
            "    }",
        ]
