"""ASCII tree/flowchart adapter for rendering workflow graphs in terminal output.

Notes/Architectural Intent:
    Renders WorkflowGraph into clean, Unicode/ASCII tree representation suitable for CLI
    inspection, log files, and rich console dashboards without requiring any graphics libraries.
"""

from typing import Literal

from hexaflow.domain.graph import (
    GraphNode,
    WorkflowGraph,
)
from hexaflow.domain.models import TriggerRule
from hexaflow.ports.renderer import (
    GraphRendererPort,
    RenderOptions,
)


class AsciiGraphRendererAdapter(GraphRendererPort):
    """Adapter for rendering WorkflowGraph into ASCII/Unicode text trees.

    Notes/Architectural Intent:
        Implements GraphRendererPort. Produces lightweight terminal-friendly representations
        tracing dependencies from roots down to leaves with branch connectors.
    """

    @property
    def format_name(self) -> str:
        """Returns format name.

        Returns:
            The string 'ascii'.
        """
        return "ascii"

    def render(
        self,
        graph: WorkflowGraph,
        options: RenderOptions | None = None,
    ) -> str:
        """Renders the workflow graph as an ASCII tree.

        Args:
            graph: The WorkflowGraph to render.
            options: Optional RenderOptions.

        Returns:
            ASCII diagram as a string.
        """
        opts = options or RenderOptions()
        crit_set = set(graph.critical_path()) if opts.highlight_critical_path else set()

        lines: list[str] = [
            f"Workflow: {graph.workflow_id}",
            "═" * (len(graph.workflow_id) + 10),
        ]

        roots = graph.roots()
        visited: set[str] = set()

        for idx, root in enumerate(roots):
            is_last = idx == len(roots) - 1
            prefix = "└─ " if is_last else "├─ "
            child_prefix = "   " if is_last else "│  "
            self._render_subtree(
                node_id=root.name,
                graph=graph,
                opts=opts,
                crit_set=crit_set,
                lines=lines,
                prefix=prefix,
                child_prefix=child_prefix,
                visited=visited,
            )

        return "\n".join(lines) + "\n"

    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        """Ascii renderer does not compile to binary images directly.

        Args:
            graph: The WorkflowGraph to render.
            image_format: Target image format ('png' or 'svg').
            options: Optional RenderOptions configuration.

        Returns:
            SVG bytes wrapping the ASCII text inside a monospace SVG document.
        """
        ascii_text = self.render(graph, options)
        escaped_text = ascii_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines = escaped_text.splitlines()
        line_height = 18
        svg_height = max(100, len(lines) * line_height + 40)
        svg_width = max(400, max((len(line) for line in lines), default=40) * 9 + 40)

        tspan_lines = "\n".join(
            f'    <tspan x="20" dy="{line_height}">{line}</tspan>' for line in lines
        )

        svg_content = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}">
  <rect width="100%" height="100%" fill="#1e1e1e"/>
  <text font-family="monospace" font-size="12" fill="#d4d4d4" y="10">
{tspan_lines}
  </text>
</svg>"""
        return svg_content.encode("utf-8")

    def _render_subtree(
        self,
        node_id: str,
        graph: WorkflowGraph,
        opts: RenderOptions,
        crit_set: set[str],
        lines: list[str],
        prefix: str,
        child_prefix: str,
        visited: set[str],
    ) -> None:
        """Recursively renders a node and its downstream children."""
        node = graph.nodes[node_id]
        node_label = self._format_node_label(node, opts, crit_set)

        if node_id in visited:
            lines.append(f"{prefix}{node_label} (cyclic/shared ref)")
            return

        lines.append(f"{prefix}{node_label}")
        visited.add(node_id)

        children = graph.successors(node_id)
        for idx, child_id in enumerate(children):
            is_last_child = idx == len(children) - 1
            next_prefix = child_prefix + ("└─ " if is_last_child else "├─ ")
            next_child_prefix = child_prefix + ("   " if is_last_child else "│  ")
            self._render_subtree(
                node_id=child_id,
                graph=graph,
                opts=opts,
                crit_set=crit_set,
                lines=lines,
                prefix=next_prefix,
                child_prefix=next_child_prefix,
                visited=visited,
            )

    def _format_node_label(
        self,
        node: GraphNode,
        opts: RenderOptions,
        crit_set: set[str],
    ) -> str:
        """Formats single node tag with flags."""
        tags: list[str] = []
        if node.step_id in crit_set:
            tags.append("CRITICAL")
        if node.step_id in opts.highlight_node_ids:
            tags.append("HIGHLIGHT")
        if node.step.side_effects:
            tags.append("SIDE-EFFECT")
        if node.trigger_rule != TriggerRule.ALL_SUCCESS:
            tags.append(node.trigger_rule.value)

        pool_str = f" [{node.step.pool.name}]" if node.step.pool else ""
        tag_str = f" ({', '.join(tags)})" if tags else ""
        return f"{node.step_id}{pool_str}{tag_str}"
