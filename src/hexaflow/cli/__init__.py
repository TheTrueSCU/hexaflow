"""Command-line interface for hexaflow.

Notes/Architectural Intent:
    Exports the Typer CLI application for terminal workflow inspection and orchestration.
"""

from hexaflow.cli.main import app

__all__ = [
    "app",
]
