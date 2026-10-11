"""Registry for workflow graph renderers.

Notes/Architectural Intent:
    Maintains available GraphRendererPort implementations, enabling dynamic lookup
    and pluggable registration of custom renderers by format name.
"""

from hexaflow.adapters.renderers.ascii import AsciiGraphRendererAdapter
from hexaflow.adapters.renderers.dot import DotGraphRendererAdapter
from hexaflow.adapters.renderers.json import JsonGraphRendererAdapter
from hexaflow.adapters.renderers.mermaid import MermaidGraphRendererAdapter
from hexaflow.ports.renderer import GraphRendererPort


class GraphRendererRegistry:
    """Registry managing available graph renderers.

    Notes/Architectural Intent:
        Pre-registers standard built-in renderers (mermaid, dot, ascii, json)
        while allowing third-party adapters to register custom formats dynamically.
    """

    def __init__(self) -> None:
        """Initializes registry with default built-in renderers."""
        self._renderers: dict[str, GraphRendererPort] = {}
        self.register(MermaidGraphRendererAdapter())
        self.register(DotGraphRendererAdapter())
        self.register(AsciiGraphRendererAdapter())
        self.register(JsonGraphRendererAdapter())

    def register(self, renderer: GraphRendererPort) -> None:
        """Registers a renderer instance under its format name.

        Args:
            renderer: The GraphRendererPort instance to register.
        """
        self._renderers[renderer.format_name.lower()] = renderer

    def get(self, format_name: str) -> GraphRendererPort:
        """Retrieves a renderer by format name.

        Args:
            format_name: Name of the format (e.g., 'mermaid', 'dot', 'ascii', 'json').

        Returns:
            The registered GraphRendererPort.

        Raises:
            KeyError: If format_name is not registered.
        """
        key = format_name.lower()
        if key not in self._renderers:
            available = ", ".join(sorted(self._renderers.keys()))
            raise KeyError(
                f"Unsupported graph renderer format '{format_name}'. Available formats: {available}"
            )
        return self._renderers[key]

    def available_formats(self) -> list[str]:
        """Returns a sorted list of registered format names.

        Returns:
            List of supported format names.
        """
        return sorted(self._renderers.keys())


# Global default registry instance
default_renderer_registry = GraphRendererRegistry()
