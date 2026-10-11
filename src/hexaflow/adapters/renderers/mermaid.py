"""Mermaid flowchart adapter for rendering workflow graphs.

Notes/Architectural Intent:
    Renders WorkflowGraph into Mermaid flowchart syntax (`flowchart TD` or `LR`).
    Supports subgraphs for execution pools, badges for side effects and trigger rules,
    classDefs for critical path / highlighting, and optional PNG/SVG compilation via
    the mermaid-cli (`mmdc`) tool.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from hexaflow.domain.exceptions import RendererToolNotFoundError
from hexaflow.domain.graph import (
    GraphNode,
    WorkflowGraph,
)
from hexaflow.domain.models import TriggerRule, _mermaid_node_id
from hexaflow.ports.renderer import (
    GraphRendererPort,
    RenderOptions,
)


class MermaidGraphRendererAdapter(GraphRendererPort):
    """Adapter for rendering WorkflowGraph into Mermaid flowchart syntax.

    Notes/Architectural Intent:
        Implements GraphRendererPort. Generates clean Mermaid markdown diagrams compatible
        with GitHub, Notion, Mermaid Live Editor, and documentation sites.
        When compiling to image, invokes `mmdc` CLI.
    """

    @property
    def format_name(self) -> str:
        """Returns format name.

        Returns:
            The string 'mermaid'.
        """
        return "mermaid"

    def render(
        self,
        graph: WorkflowGraph,
        options: RenderOptions | None = None,
    ) -> str:
        """Renders the workflow graph as Mermaid flowchart syntax.

        Args:
            graph: The WorkflowGraph to render.
            options: Optional RenderOptions.

        Returns:
            Mermaid flowchart markdown as a string.
        """
        opts = options or RenderOptions()
        direction = opts.direction if opts.direction in ("TD", "LR", "TB", "BT") else "TD"
        critical_path_set = set(graph.critical_path()) if opts.highlight_critical_path else set()

        lines: list[str] = [
            f"flowchart {direction}",
        ]

        if opts.include_pools:
            lines.extend(self._render_pools(graph, opts, critical_path_set))
        else:
            for node in graph.nodes.values():
                lines.append(f"    {self._render_node_shape(node)}")

        lines.append("")
        for source_id, target_id in graph.edges:
            lines.append(f"    {_mermaid_node_id(source_id)} --> {_mermaid_node_id(target_id)}")

        lines.append("")
        lines.extend(self._render_styles(graph, opts, critical_path_set))

        return "\n".join(lines) + "\n"

    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        """Compiles the workflow graph into an image binary (PNG or SVG) via 'mmdc'.

        Args:
            graph: The WorkflowGraph to render.
            image_format: Target image format ('png' or 'svg').
            options: Optional RenderOptions configuration.

        Returns:
            Raw image bytes.

        Raises:
            RendererToolNotFoundError: If 'mmdc' is not found in PATH.
            RuntimeError: If 'mmdc' command fails.
        """
        if not shutil.which("mmdc"):
            raise RendererToolNotFoundError(
                "Mermaid CLI executable 'mmdc' was not found in system PATH. "
                "Please install mermaid-cli (e.g., 'npm install -g @mermaid-js/mermaid-cli')."
            )

        mermaid_source = self.render(graph, options)
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "graph.mmd"
            output_file = Path(tmpdir) / f"graph.{image_format}"

            input_file.write_text(mermaid_source, encoding="utf-8")
            cmd = ["mmdc", "-i", str(input_file), "-o", str(output_file)]
            result = subprocess.run(cmd, capture_output=True, check=False)

            if result.returncode != 0:
                error_msg = result.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(
                    f"Mermaid CLI 'mmdc' failed with exit code {result.returncode}: {error_msg}"
                )

            return output_file.read_bytes()

    def _render_pools(
        self,
        graph: WorkflowGraph,
        opts: RenderOptions,
        critical_path_set: set[str],
    ) -> list[str]:
        """Groups nodes into subgraphs by execution pool."""
        pool_nodes: dict[str, list[GraphNode]] = {}
        for node in graph.nodes.values():
            pool_name = node.step.pool.name if node.step.pool else "default"
            pool_nodes.setdefault(pool_name, []).append(node)

        lines: list[str] = []
        for cluster_idx, (pool_name, nodes) in enumerate(pool_nodes.items()):
            lines.append(f'    subgraph pool_{cluster_idx} ["Pool: {pool_name}"]')
            for node in nodes:
                lines.append(f"        {self._render_node_shape(node)}")
            lines.append("    end")
        return lines

    def _render_node_shape(self, node: GraphNode) -> str:
        """Renders node ID and shape with label badges."""
        effects = " ⚡" if node.step.side_effects else ""
        rule = (
            f"<br/>[{node.trigger_rule.value}]"
            if node.trigger_rule != TriggerRule.ALL_SUCCESS
            else ""
        )
        safe_id = _mermaid_node_id(node.step_id)
        escaped_label = node.step_id.replace('"', "#quot;")
        label = f'"{escaped_label}{effects}{rule}"'
        return f"{safe_id}[{label}]"

    def _render_styles(
        self,
        graph: WorkflowGraph,
        opts: RenderOptions,
        critical_path_set: set[str],
    ) -> list[str]:
        """Adds classDef directives for highlighted and critical path nodes."""
        lines: list[str] = [
            "    classDef default fill:#e3f2fd,stroke:#1976d2,stroke-width:1px,color:#0d47a1;",
            "    classDef highlighted fill:#ffecb3,stroke:#f57c00,stroke-width:2px,color:#e65100;",
            "    classDef critical fill:#ffebee,stroke:#d32f2f,stroke-width:3px,color:#b71c1c;",
        ]

        crit_nodes = [_mermaid_node_id(nid) for nid in graph.nodes if nid in critical_path_set]
        if crit_nodes:
            lines.append(f"    class {','.join(crit_nodes)} critical;")

        highlight_nodes = [
            _mermaid_node_id(nid)
            for nid in graph.nodes
            if nid in opts.highlight_node_ids and nid not in critical_path_set
        ]
        if highlight_nodes:
            lines.append(f"    class {','.join(highlight_nodes)} highlighted;")

        return lines
