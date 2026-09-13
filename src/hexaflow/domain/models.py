"""Immutable domain models for defining workflows, stages, steps, and DAG topology.

Notes/Architectural Intent:
    Defines the structural blueprint of workflows. Enforces acyclicity, unique naming,
    and dependency resolution at construction time. Provides standard Python graphlib
    topological sorting and barrier mappings.
"""

from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hexaflow.domain.exceptions import (
    DuplicateStepError,
    InvalidWorkflowDAGError,
    StepNotFoundError,
)
from hexaflow.domain.retry import RetryPolicy


class StageExecutionMode(StrEnum):
    """Execution concurrency mode for steps within a stage.

    Notes/Architectural Intent:
        Controls whether independent steps in a stage run serially or concurrently
        under asyncio/process workers.
    """

    SEQUENTIAL = "SEQUENTIAL"
    CONCURRENT_ALL = "CONCURRENT_ALL"
    CONCURRENT_FAIL_FAST = "CONCURRENT_FAIL_FAST"


class StepDefinition(BaseModel):
    """Specification of an individual executable step within a workflow stage.

    Notes/Architectural Intent:
        Pairs an executable action with its optional rollback compensation, retry policy,
        timeout bounds, and prerequisite dependency names for join barriers.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(description="Unique identifier for this step within the workflow.")
    action: Any = Field(description="Forward executable callable or coroutine.")
    compensation: Any | None = Field(
        default=None, description="Optional rollback callable if workflow is aborted."
    )
    depends_on: tuple[str, ...] = Field(
        default_factory=tuple, description="Prerequisite step names required before execution."
    )
    retry_policy: RetryPolicy | None = Field(
        default=None, description="Optional transient retry configuration."
    )
    timeout_seconds: float | None = Field(
        default=None, description="Maximum execution duration in seconds."
    )
    is_split: bool = Field(
        default=False,
        description="True if output should be fanned out across parallel worker instances.",
    )
    description: str = Field(
        default="", description="Architectural or business description of the step."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary execution metadata (e.g. cluster resources, hardware requirements, tags).",
    )


class StageDefinition(BaseModel):
    """Logical milestone grouping one or more related steps.

    Notes/Architectural Intent:
        Stages provide high-level boundaries for reporting, operator dashboards, and
        milestone-based execution gates.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(description="Unique milestone name for this stage.")
    steps: tuple[StepDefinition, ...] = Field(
        default_factory=tuple, description="Ordered steps belonging to this stage."
    )
    execution_mode: StageExecutionMode = Field(
        default=StageExecutionMode.SEQUENTIAL,
        description="Concurrency strategy for steps in this stage.",
    )
    description: str = Field(
        default="", description="Description of the stage's business milestone."
    )

    @field_validator("steps")
    @classmethod
    def validate_unique_steps_in_stage(
        cls, steps: tuple[StepDefinition, ...]
    ) -> tuple[StepDefinition, ...]:
        """Verify that step names are unique within the stage.

        Args:
            steps: Tuple of StepDefinitions to validate.

        Returns:
            The validated tuple of steps.

        Raises:
            DuplicateStepError: If two or more steps share the same name.
        """
        seen: set[str] = set()
        for step in steps:
            if step.name in seen:
                raise DuplicateStepError(f"Duplicate step name '{step.name}' found in stage.")
            seen.add(step.name)
        return steps


class WorkflowDefinition(BaseModel):
    """Complete immutable blueprint of a multi-stage, multi-step workflow DAG.

    Notes/Architectural Intent:
        Validates graph acyclicity, dependency integrity, and uniqueness across all
        stages and steps upon instantiation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    name: str = Field(description="Unique name of the workflow.")
    stages: tuple[StageDefinition, ...] = Field(
        default_factory=tuple, description="Ordered stages defining the workflow."
    )
    version: str = Field(
        default="1.0.0", description="Semantic version string for this workflow definition."
    )
    description: str = Field(default="", description="Architectural summary of the workflow.")

    @model_validator(mode="after")
    def validate_workflow_dag(self) -> "WorkflowDefinition":
        """Validate global step uniqueness, dependency resolution, and acyclicity.

        Returns:
            The validated WorkflowDefinition instance.

        Raises:
            DuplicateStepError: If duplicate step names exist across any stages.
            StepNotFoundError: If a step depends on an unregistered step name.
            InvalidWorkflowDAGError: If the dependency graph contains circular cycles.

        Notes/Architectural Intent:
            Uses Python's standard library TopologicalSorter to guarantee cycle-free DAG topology.
        """
        all_steps: dict[str, StepDefinition] = {}
        for stage in self.stages:
            for step in stage.steps:
                if step.name in all_steps:
                    raise DuplicateStepError(
                        f"Duplicate step '{step.name}' found across stages in workflow '{self.name}'."
                    )
                all_steps[step.name] = step

        # Validate that all dependencies exist
        for step_name, step in all_steps.items():
            for dep in step.depends_on:
                if dep not in all_steps:
                    raise StepNotFoundError(f"Step '{step_name}' depends on unknown step '{dep}'.")
                if dep == step_name:
                    raise InvalidWorkflowDAGError(f"Step '{step_name}' cannot depend on itself.")

        # Validate DAG acyclicity using TopologicalSorter
        graph: dict[str, set[str]] = {
            step.name: set(step.depends_on) for step in all_steps.values()
        }
        sorter = TopologicalSorter(graph)
        try:
            sorter.prepare()
        except CycleError as err:
            raise InvalidWorkflowDAGError(f"Workflow DAG contains a circular cycle: {err}") from err

        return self

    def get_step(self, step_name: str) -> StepDefinition:
        """Retrieve a StepDefinition by name.

        Args:
            step_name: The identifier of the step to find.

        Returns:
            The matching StepDefinition.

        Raises:
            StepNotFoundError: If no step matches the given name.
        """
        for stage in self.stages:
            for step in stage.steps:
                if step.name == step_name:
                    return step
        raise StepNotFoundError(f"Step '{step_name}' not found in workflow '{self.name}'.")

    def get_stage_for_step(self, step_name: str) -> StageDefinition:
        """Retrieve the enclosing StageDefinition containing the named step.

        Args:
            step_name: The step identifier.

        Returns:
            The StageDefinition containing the step.

        Raises:
            StepNotFoundError: If the step is not present in the workflow.
        """
        for stage in self.stages:
            for step in stage.steps:
                if step.name == step_name:
                    return stage
        raise StepNotFoundError(f"Step '{step_name}' not found in workflow '{self.name}'.")

    def topological_order(self) -> list[str]:
        """Compute a deterministic topological execution order of all steps.

        Returns:
            List of step names ordered such that dependencies always precede dependents.

        Notes/Architectural Intent:
            Relies on TopologicalSorter for predictable linear or parallel frontier scheduling.
        """
        all_steps = {
            step.name: set(step.depends_on) for stage in self.stages for step in stage.steps
        }
        sorter = TopologicalSorter(all_steps)
        return list(sorter.static_order())

    def dependencies_map(self) -> dict[str, set[str]]:
        """Return the complete dependency graph mapping step names to prerequisite sets.

        Returns:
            Dictionary mapping each step name to the set of step names it depends on.
        """
        return {step.name: set(step.depends_on) for stage in self.stages for step in stage.steps}


__all__ = [
    "StageDefinition",
    "StageExecutionMode",
    "StepDefinition",
    "WorkflowDefinition",
]
