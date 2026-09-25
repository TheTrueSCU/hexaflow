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
from hexaflow.domain.state import StepStatus


class TriggerRule(StrEnum):
    """Execution prerequisite rule evaluated against direct upstream dependencies.

    Notes/Architectural Intent:
        Governs whether a step executes, cascades skip, or halts execution
        based on the terminal states of its direct upstream dependencies.
    """

    ALL_SUCCESS = "ALL_SUCCESS"
    ALL_FAILED = "ALL_FAILED"
    ALL_DONE = "ALL_DONE"
    ONE_SUCCESS = "ONE_SUCCESS"
    ONE_FAILED = "ONE_FAILED"
    NONE_FAILED = "NONE_FAILED"
    ALL_SUCCESS_OR_SKIPPED = "ALL_SUCCESS_OR_SKIPPED"


def evaluate_trigger_rule(rule: TriggerRule, parent_statuses: list[StepStatus]) -> bool:
    """Evaluate whether a trigger rule is satisfied given upstream parent statuses.

    Args:
        rule: The TriggerRule policy to evaluate.
        parent_statuses: List of terminal StepStatus values for all direct parents.

    Returns:
        True if the trigger condition is satisfied and step should execute;
        False if the trigger rule is violated and the step should cascade skip.

    Notes/Architectural Intent:
        Implements deterministic DAG barrier evaluation without side effects.
        If a step has no parents, all rules evaluate to True.
    """
    if not parent_statuses:
        return True

    successes = sum(1 for s in parent_statuses if s == StepStatus.COMPLETED)
    failures = sum(1 for s in parent_statuses if s == StepStatus.FAILED)
    skips = sum(1 for s in parent_statuses if s == StepStatus.SKIPPED)
    total = len(parent_statuses)

    match rule:
        case TriggerRule.ALL_SUCCESS:
            return successes == total
        case TriggerRule.ALL_FAILED:
            return failures == total
        case TriggerRule.ALL_DONE:
            return (successes + failures + skips) == total
        case TriggerRule.ONE_SUCCESS:
            return successes > 0
        case TriggerRule.ONE_FAILED:
            return failures > 0
        case TriggerRule.NONE_FAILED:
            return failures == 0
        case TriggerRule.ALL_SUCCESS_OR_SKIPPED:
            return failures == 0 and (successes + skips) == total


class StageExecutionMode(StrEnum):
    """Execution concurrency mode for steps within a stage.

    Notes/Architectural Intent:
        Controls whether independent steps in a stage run serially or concurrently
        under asyncio/process workers.
    """

    SEQUENTIAL = "SEQUENTIAL"
    CONCURRENT_ALL = "CONCURRENT_ALL"
    CONCURRENT_FAIL_FAST = "CONCURRENT_FAIL_FAST"


class ExecutionPool(StrEnum):
    """Execution strategy determining runtime scheduling and worker isolation.

    Notes/Architectural Intent:
        Governs how step action callables are scheduled: cooperatively on the
        asyncio event loop (ASYNC), offloaded to an OS worker thread for blocking
        I/O (THREAD), or spawned in a separate OS process for CPU-intensive
        computations and true multi-core parallel fan-out (PROCESS).
    """

    ASYNC = "async"
    THREAD = "thread"
    PROCESS = "process"


class StepDefinition(BaseModel):
    """Specification of an individual executable step within a workflow stage.

    Notes/Architectural Intent:
        Pairs an executable action with its optional rollback compensation, retry policy,
        timeout bounds, prerequisite dependency names, and upstream trigger rule for join barriers.
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
    trigger_rule: TriggerRule = Field(
        default=TriggerRule.ALL_SUCCESS,
        description="Rule evaluating direct upstream dependencies before execution.",
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
    is_mapped: bool = Field(
        default=False,
        description="True if this step dynamically maps over a runtime collection.",
    )
    map_over: str | None = Field(
        default=None,
        description="Name of upstream step or context key providing the iterable to map over.",
    )
    concurrency_limit: int | None = Field(
        default=None,
        description="Optional maximum concurrent instances for mapped execution.",
    )
    pool: ExecutionPool = Field(
        default=ExecutionPool.ASYNC,
        description="Execution strategy determining concurrency and worker isolation.",
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

    def to_mermaid(self, direction: str = "TD") -> str:
        """Render the workflow DAG as a Mermaid flowchart definition.

        Args:
            direction: Flowchart orientation ('TD', 'LR', 'BT', 'RL'). Defaults to 'TD'.

        Returns:
            Mermaid flowchart markdown string.

        Notes/Architectural Intent:
            Encloses stages in subgraphs, applies distinct styling for split and dynamically
            mapped steps, and annotates non-default trigger rules on dependency edges.
        """
        lines: list[str] = [f"graph {direction}"]

        for i, stage in enumerate(self.stages):
            stage_id = f"stage_{i}_{_mermaid_node_id(stage.name)}"
            lines.append(f'    subgraph {stage_id} ["{stage.name} ({stage.execution_mode.value})"]')
            for step in stage.steps:
                lines.append(f"        {_format_mermaid_step_node(step)}")
            lines.append("    end")

        for stage in self.stages:
            for step in stage.steps:
                target_id = _mermaid_node_id(step.name)
                for dep in step.depends_on:
                    dep_id = _mermaid_node_id(dep)
                    if step.trigger_rule != TriggerRule.ALL_SUCCESS:
                        lines.append(f"    {dep_id} -->|{step.trigger_rule.value}| {target_id}")
                    else:
                        lines.append(f"    {dep_id} --> {target_id}")

        return "\n".join(lines)

    def to_ascii(self) -> str:
        """Render the workflow DAG as a formatted ASCII/Unicode hierarchical tree.

        Returns:
            Formatted multiline string illustrating stages, steps, dependencies, and mapping.

        Notes/Architectural Intent:
            Provides zero-dependency, human-readable terminal and log output of workflow topology.
        """
        lines: list[str] = [f"Workflow: {self.name} (v{self.version})"]
        total_stages = len(self.stages)

        for stage_idx, stage in enumerate(self.stages):
            is_last_stage = stage_idx == total_stages - 1
            stage_prefix = "└── " if is_last_stage else "├── "
            child_indent = "    " if is_last_stage else "│   "

            lines.append(f"{stage_prefix}Stage: {stage.name} ({stage.execution_mode.value})")
            total_steps = len(stage.steps)

            for step_idx, step in enumerate(stage.steps):
                is_last_step = step_idx == total_steps - 1
                lines.append(_format_ascii_step(step, is_last_step, child_indent))

        return "\n".join(lines)


def _format_ascii_step(step: StepDefinition, is_last_step: bool, child_indent: str) -> str:
    """Format an individual step node into an ASCII tree entry."""
    step_prefix = "└── " if is_last_step else "├── "

    tag = "[step]"
    if step.is_mapped:
        tag = f"[mapped: {step.map_over}]"
    elif step.is_split:
        tag = "[split]"

    extras: list[str] = []
    if step.depends_on:
        extras.append(f"depends on: {', '.join(step.depends_on)}")
    if step.trigger_rule != TriggerRule.ALL_SUCCESS:
        extras.append(f"rule: {step.trigger_rule.value}")
    if step.concurrency_limit is not None:
        extras.append(f"concurrency: {step.concurrency_limit}")
    if step.pool != ExecutionPool.ASYNC:
        extras.append(f"pool: {step.pool.value}")

    extra_str = f" ({'; '.join(extras)})" if extras else ""
    return f"{child_indent}{step_prefix}{tag} {step.name}{extra_str}"


def _mermaid_node_id(name: str) -> str:
    """Sanitize a name into a valid alphanumeric Mermaid node identifier."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def _format_mermaid_step_node(step: StepDefinition) -> str:
    """Format an individual step node definition in Mermaid syntax."""
    node_id = _mermaid_node_id(step.name)
    label = step.name.replace('"', '\\"')
    if step.is_mapped:
        over = step.map_over or "runtime"
        return f'{node_id}[["{label} [mapped: {over}]"]]'
    if step.is_split:
        return f'{node_id}{{"{label} [split]"}}'
    return f'{node_id}["{label}"]'


__all__ = [
    "evaluate_trigger_rule",
    "ExecutionPool",
    "StageDefinition",
    "StageExecutionMode",
    "StepDefinition",
    "TriggerRule",
    "WorkflowDefinition",
]
