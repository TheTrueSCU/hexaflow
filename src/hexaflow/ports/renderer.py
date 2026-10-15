"""Port contract for rendering workflow graphs into text and visual artifacts.

Notes/Architectural Intent:
    Defines the abstract contract for converting a WorkflowGraph into diagrammatic
    and structured textual representations (Mermaid, DOT, ASCII, JSON) as well as
    optional visual formats (SVG, PNG) via external rendering tools.
"""

from abc import (
    ABC,
    abstractmethod,
)
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from hexaflow.domain.graph import WorkflowGraph


class RenderOptions(BaseModel):
    """Configuration options for graph rendering.

    Attributes:
        direction: Flowchart or graph layout direction ('TD', 'LR', 'TB', 'BT').
        include_pools: Whether to render execution pool / worker boundaries as subgraphs.
        include_legend: Whether to display a status and trigger rule legend.
        highlight_critical_path: Whether to visually accentuate the critical path.
        highlight_node_ids: Optional set of step IDs to highlight with distinct colors/styles.
        theme: Color theme or style preset ('light', 'dark', 'default').

    Notes/Architectural Intent:
        Encapsulates rendering visual preferences independently from the underlying graph
        data structure, allowing caller-customized exports across all renderer implementations.
    """

    model_config = ConfigDict(frozen=True)

    direction: Literal["TD", "LR", "TB", "BT"] = "TD"
    include_pools: bool = True
    include_legend: bool = False
    highlight_critical_path: bool = False
    highlight_node_ids: frozenset[str] = Field(default_factory=frozenset)
    theme: Literal["light", "dark", "default"] = "default"


class GraphRendererPort(ABC):
    """Abstract port for rendering workflow graphs.

    Notes/Architectural Intent:
        All diagram and graph formatting adapters must conform to this port.
        Text rendering (`render`) is pure and dependency-free, while image
        rendering (`render_image`) delegates to CLI tool wrappers or binary exporters.
    """

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Returns the format identifier (e.g., 'mermaid', 'dot', 'ascii', 'json').

        Returns:
            The string format identifier.
        """

    @abstractmethod
    def render(
        self,
        graph: WorkflowGraph,
        options: RenderOptions | None = None,
    ) -> str:
        """Renders the workflow graph into a text-based representation.

        Args:
            graph: The WorkflowGraph to render.
            options: Optional RenderOptions configuration. If None, default options are used.

        Returns:
            A string containing the rendered graph specification.
        """

    @abstractmethod
    def render_image(
        self,
        graph: WorkflowGraph,
        image_format: Literal["png", "svg"] = "svg",
        options: RenderOptions | None = None,
    ) -> bytes:
        """Compiles the workflow graph into an image binary (PNG or SVG).

        Args:
            graph: The WorkflowGraph to render.
            image_format: Target image format ('png' or 'svg').
            options: Optional RenderOptions configuration.

        Returns:
            Raw image bytes.

        Raises:
            RendererToolNotFoundError: If the external CLI compiler (e.g., 'dot' or 'mmdc')
                is not available in the system PATH.
            RuntimeError: If image compilation fails.
        """
