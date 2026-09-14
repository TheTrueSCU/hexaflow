"""Fluent decorator DSL and workflow builder.

Notes/Architectural Intent:
    Provides the primary user-facing Pythonic DSL (@wf.stage, @wf.step) while compiling
    down to the immutable domain models (WorkflowDefinition, StageDefinition, StepDefinition)
    and delegating execution to WorkflowEnginePort.
"""

from collections.abc import Callable
from types import TracebackType
from typing import Any

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.domain.models import (
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    TriggerRule,
    WorkflowDefinition,
)
from hexaflow.domain.retry import RetryPolicy
from hexaflow.domain.state import WorkflowExecutionState
from hexaflow.ports.engine import WorkflowEnginePort
from hexaflow.ports.storage import WorkflowStateStorePort


class _StepBuilder:
    """Internal helper capturing step attributes prior to definition compilation."""

    def __init__(
        self,
        name: str,
        stage_name: str,
        action: Any,
        depends_on: tuple[str, ...] = (),
        trigger_rule: TriggerRule = TriggerRule.ALL_SUCCESS,
        retry_policy: RetryPolicy | None = None,
        retries: RetryPolicy | None = None,
        compensation: Any | None = None,
        timeout_seconds: float | None = None,
        is_split: bool = False,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.stage_name = stage_name
        self.action = action
        self.depends_on = depends_on
        self.trigger_rule = trigger_rule
        self.retry_policy = retry_policy
        self.compensation = compensation
        self.timeout_seconds = timeout_seconds
        self.is_split = is_split
        self.description = description
        self.metadata = metadata or {}

    def to_definition(self) -> StepDefinition:
        """Compile into an immutable StepDefinition."""
        return StepDefinition(
            name=self.name,
            action=self.action,
            compensation=self.compensation,
            depends_on=self.depends_on,
            trigger_rule=self.trigger_rule,
            retry_policy=self.retry_policy,
            timeout_seconds=self.timeout_seconds,
            is_split=self.is_split,
            description=self.description,
            metadata=self.metadata,
        )


class _StageContext:
    """Dual-mode stage helper supporting decorator, context manager, and direct method syntax."""

    def __init__(self, workflow: "Workflow", stage_name: str) -> None:
        self.workflow = workflow
        self.stage_name = stage_name

    def __call__(self, fn: Any) -> Any:
        """Allow @wf.stage(...) as a decorator over @wf.step."""
        return fn

    def __enter__(self) -> "Workflow":
        """Allow with wf.stage(...): syntax."""
        return self.workflow

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass


class Workflow:
    """Fluent workflow builder and runner.

    Notes/Architectural Intent:
        The core ergonomic entrypoint for hexaflow. Enables constructing multi-stage,
        multi-step workflow DAGs using clean decorator syntax and executing them locally
        via AsyncioWorkflowEngine and SqliteStateStore.
    """

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        state_store: WorkflowStateStorePort | None = None,
        engine: WorkflowEnginePort | None = None,
    ) -> None:
        """Initialize a new Workflow builder.

        Args:
            name: Unique workflow identifier.
            version: Semantic version string.
            description: Architectural intent or business summary.
            state_store: Persistence store for checkpoints. Defaults to SqliteStateStore.
            engine: Execution engine. Defaults to AsyncioWorkflowEngine.
        """
        self.name = name
        self.version = version
        self.description = description
        self._stages: dict[str, dict[str, Any]] = {}
        self._steps: list[_StepBuilder] = []
        self._current_stage: str | None = None

        self._store = state_store or SqliteStateStore()
        self._engine = engine or AsyncioWorkflowEngine(state_store=self._store)

    def stage(
        self,
        name: str,
        execution_mode: StageExecutionMode = StageExecutionMode.SEQUENTIAL,
        description: str = "",
    ) -> _StageContext:
        """Declare or set active stage for subsequent step decorators.

        Args:
            name: Unique milestone name for this stage.
            execution_mode: SEQUENTIAL or CONCURRENT_ALL.
            description: Optional summary of stage milestone.

        Returns:
            _StageContext supporting decorator, context manager, and fluent calls.
        """
        if name not in self._stages:
            self._stages[name] = {
                "execution_mode": execution_mode,
                "description": description,
            }
        self._current_stage = name
        return _StageContext(self, name)

    def step(
        self,
        name: str,
        stage: str | None = None,
        depends_on: tuple[str, ...] | list[str] = (),
        trigger_rule: TriggerRule = TriggerRule.ALL_SUCCESS,
        retry_policy: RetryPolicy | None = None,
        retries: RetryPolicy | None = None,
        compensation: Any | None = None,
        timeout_seconds: float | None = None,
        is_split: bool = False,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a function or coroutine as an executable workflow step.

        Args:
            name: Unique identifier for this step.
            stage: Enclosing stage name (defaults to current active stage or 'default').
            depends_on: Prerequisite step names for join barriers.
            trigger_rule: Trigger rule evaluated against direct upstream dependencies.
            retry_policy: Optional transient retry policy.
            compensation: Optional rollback callable.
            timeout_seconds: Maximum execution time.
            is_split: True if step output fans out across workers.
            description: Optional documentation of the step's operation.

        Returns:
            Decorator wrapping the target callable.
        """
        target_stage = stage or self._current_stage or "default"
        if target_stage not in self._stages:
            self.stage(target_stage)

        deps = tuple(depends_on)
        active_policy = retry_policy or retries

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            builder = _StepBuilder(
                name=name,
                stage_name=target_stage,
                action=fn,
                depends_on=deps,
                trigger_rule=trigger_rule,
                retry_policy=active_policy,
                compensation=compensation,
                timeout_seconds=timeout_seconds,
                is_split=is_split,
                description=description,
                metadata=metadata,
            )
            self._steps.append(builder)
            return fn

        return decorator

    def to_definition(self) -> WorkflowDefinition:
        """Compile declared stages and steps into an immutable, validated WorkflowDefinition.

        Returns:
            Immutable WorkflowDefinition.

        Raises:
            DuplicateStepError: If duplicate step names exist.
            InvalidWorkflowDAGError: If circular dependencies are detected.
            StepNotFoundError: If a dependency is missing.
        """
        stage_definitions: list[StageDefinition] = []

        for stage_name, stage_meta in self._stages.items():
            stage_steps = [
                step_b.to_definition() for step_b in self._steps if step_b.stage_name == stage_name
            ]
            stage_definitions.append(
                StageDefinition(
                    name=stage_name,
                    steps=tuple(stage_steps),
                    execution_mode=stage_meta["execution_mode"],
                    description=stage_meta["description"],
                )
            )

        return WorkflowDefinition(
            name=self.name,
            stages=tuple(stage_definitions),
            version=self.version,
            description=self.description,
        )

    def run(
        self,
        initial_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Synchronously execute the workflow from start to finish.

        Args:
            initial_inputs: Optional dictionary of inputs for root steps.
            skip_steps: Optional collection of step names to explicitly skip.

        Returns:
            Final or suspended WorkflowExecutionState.
        """
        definition = self.to_definition()
        return self._engine.run(definition, initial_inputs, skip_steps=skip_steps)

    async def run_async(
        self,
        initial_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Asynchronously execute the workflow from start to finish.

        Args:
            initial_inputs: Optional dictionary of inputs for root steps.
            skip_steps: Optional collection of step names to explicitly skip.

        Returns:
            Final or suspended WorkflowExecutionState.
        """
        definition = self.to_definition()
        return await self._engine.run_async(definition, initial_inputs, skip_steps=skip_steps)

    def resume(
        self,
        run_id: str,
        patch_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Synchronously resume a suspended workflow run from its latest checkpoints.

        Args:
            run_id: Execution identifier of the suspended workflow run.
            patch_inputs: Optional override inputs for resuming step frontier.
            skip_steps: Optional collection of step names to explicitly skip during resumption.

        Returns:
            Updated WorkflowExecutionState outcome.
        """
        definition = self.to_definition()
        return self._engine.resume(run_id, definition, patch_inputs, skip_steps=skip_steps)

    async def resume_async(
        self,
        run_id: str,
        patch_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Asynchronously resume a suspended workflow run from its latest checkpoints.

        Args:
            run_id: Execution identifier of the suspended workflow run.
            patch_inputs: Optional override inputs for resuming step frontier.
            skip_steps: Optional collection of step names to explicitly skip during resumption.

        Returns:
            Updated WorkflowExecutionState outcome.
        """
        definition = self.to_definition()
        return await self._engine.resume_async(
            run_id, definition, patch_inputs, skip_steps=skip_steps
        )

    def restart(self, run_id: str) -> WorkflowExecutionState:
        """Synchronously restart a workflow execution run from the beginning.

        Args:
            run_id: Execution identifier of the run to restart.

        Returns:
            Fresh WorkflowExecutionState outcome.
        """
        definition = self.to_definition()
        return self._engine.restart(run_id, definition)

    async def restart_async(self, run_id: str) -> WorkflowExecutionState:
        """Asynchronously restart a workflow execution run from the beginning.

        Args:
            run_id: Execution identifier of the run to restart.

        Returns:
            Fresh WorkflowExecutionState outcome.
        """
        definition = self.to_definition()
        return await self._engine.restart_async(run_id, definition)

    def abort(self, run_id: str) -> WorkflowExecutionState:
        """Synchronously abort a workflow run, unwinding any step compensations.

        Args:
            run_id: Execution identifier of the workflow run.

        Returns:
            Terminal WorkflowExecutionState marked CANCELLED.
        """
        definition = self.to_definition()
        return self._engine.abort(run_id, definition)

    async def abort_async(self, run_id: str) -> WorkflowExecutionState:
        """Asynchronously abort a workflow run, unwinding any step compensations.

        Args:
            run_id: Execution identifier of the workflow run.

        Returns:
            Terminal WorkflowExecutionState marked CANCELLED.
        """
        definition = self.to_definition()
        return await self._engine.abort_async(run_id, definition)

    def create_cli_binder(self, aliases: dict[str, list[str]] | None = None) -> Any:
        """Create a WorkflowCliBinder introspecting this workflow.

        Args:
            aliases: Optional mapping of step_name to additional CLI flag strings.

        Returns:
            WorkflowCliBinder instance bound to this workflow.

        Notes/Architectural Intent:
            Enables seamless generation of --skip-<step> CLI switches from the workflow DAG.
        """
        from hexaflow.cli.binder import WorkflowCliBinder

        return WorkflowCliBinder(self, aliases=aliases)


__all__ = [
    "Workflow",
]
