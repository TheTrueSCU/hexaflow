"""Fluent workflow builder and decorator DSL for hexaflow.

Notes/Architectural Intent:
    Provides intuitive @wf.stage and @wf.step decorator APIs for constructing
    and executing multi-stage DAG workflows.
"""

from hexaflow.dsl.builder import Workflow

__all__ = [
    "Workflow",
]
