"""Localhost asynchronous workflow execution engine.

Notes/Architectural Intent:
    Implements WorkflowEnginePort using standard asyncio primitives. Coordinates
    stage-by-stage and step-by-step DAG execution, evaluates join barriers, invokes
    transient retry backoffs, writes durable checkpoints, and suspends execution cleanly
    upon permanent failure without corrupting state.
"""

import asyncio
import inspect
import traceback
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.exceptions import StepNotFoundError, WorkflowAborted, WorkflowSuspended
from hexaflow.domain.models import (
    StageDefinition,
    StageExecutionMode,
    StepDefinition,
    WorkflowDefinition,
    evaluate_trigger_rule,
)
from hexaflow.domain.state import (
    CheckpointRecord,
    StepContext,
    StepStatus,
    WorkflowExecutionState,
    WorkflowStatus,
)
from hexaflow.ports.engine import WorkflowEnginePort
from hexaflow.ports.storage import WorkflowStateStorePort


class AsyncioWorkflowEngine(WorkflowEnginePort):
    """Localhost asyncio workflow execution engine supporting splits, joins, and resumption.

    Notes/Architectural Intent:
        Default workflow executor for localhost and embedded environments. Evaluates
        topological frontiers, enforces split/join barriers, and checks state store
        before executing steps to ensure completed steps are skipped during resumption.
    """

    def __init__(self, state_store: WorkflowStateStorePort | None = None) -> None:
        """Initialize the engine with an optional state store.

        Args:
            state_store: Persistence store for checkpoints. Defaults to InMemoryStateStore.
        """
        self._store: WorkflowStateStorePort = state_store or InMemoryStateStore()

    def run(
        self,
        workflow: WorkflowDefinition,
        initial_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Synchronously execute a workflow definition from start to finish.

        Args:
            workflow: Immutable specification of the workflow DAG.
            initial_inputs: Optional dictionary of input arguments.
            skip_steps: Optional collection of step names to explicitly skip.

        Returns:
            Terminal or suspended WorkflowExecutionState.
        """
        return asyncio.run(self.run_async(workflow, initial_inputs, skip_steps=skip_steps))

    async def run_async(
        self,
        workflow: WorkflowDefinition,
        initial_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Asynchronously execute a workflow definition from start to finish.

        Args:
            workflow: Immutable specification of the workflow DAG.
            initial_inputs: Optional dictionary of input arguments.
            skip_steps: Optional collection of step names to explicitly skip.

        Returns:
            Terminal or suspended WorkflowExecutionState.
        """
        run_id = str(uuid4())
        state = WorkflowExecutionState(
            run_id=run_id,
            workflow_name=workflow.name,
            status=WorkflowStatus.RUNNING,
        )
        self._store.save_run(state)
        skipped = set(skip_steps or ())
        return await self._execute_workflow(state, workflow, initial_inputs or {}, skipped)

    def resume(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
        patch_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Synchronously resume a suspended workflow run from its latest checkpoints.

        Args:
            run_id: Execution identifier of the suspended workflow run.
            workflow: WorkflowDefinition specification matching the run.
            patch_inputs: Optional override inputs for the resuming step frontier.
            skip_steps: Optional collection of step names to explicitly skip during resumption.

        Returns:
            Updated WorkflowExecutionState outcome.
        """
        return asyncio.run(self.resume_async(run_id, workflow, patch_inputs, skip_steps=skip_steps))

    async def resume_async(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
        patch_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Asynchronously resume a suspended workflow run from its latest checkpoints.

        Args:
            run_id: Execution identifier of the suspended workflow run.
            workflow: WorkflowDefinition specification matching the run.
            patch_inputs: Optional override inputs for the resuming step frontier.
            skip_steps: Optional collection of step names to explicitly skip during resumption.

        Returns:
            Updated WorkflowExecutionState outcome.
        """
        state = self._store.get_run(run_id)
        if not state:
            raise WorkflowSuspended(
                run_id, "unknown", f"Workflow run '{run_id}' not found in state store."
            )

        state.status = WorkflowStatus.RUNNING
        state.error_summary = None
        self._store.save_run(state)
        skipped = set(skip_steps or ())
        return await self._execute_workflow(state, workflow, patch_inputs or {}, skipped)

    def restart(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
    ) -> WorkflowExecutionState:
        """Synchronously restart a workflow execution run from the beginning.

        Args:
            run_id: Execution identifier of the run to restart.
            workflow: WorkflowDefinition specification to re-execute.

        Returns:
            Fresh WorkflowExecutionState outcome.
        """
        return asyncio.run(self.restart_async(run_id, workflow))

    async def restart_async(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
    ) -> WorkflowExecutionState:
        """Asynchronously restart a workflow execution run from the beginning.

        Args:
            run_id: Execution identifier of the run to restart.
            workflow: WorkflowDefinition specification to re-execute.

        Returns:
            Fresh WorkflowExecutionState outcome.
        """
        state = WorkflowExecutionState(
            run_id=run_id,
            workflow_name=workflow.name,
            status=WorkflowStatus.RUNNING,
        )
        self._store.save_run(state)
        return await self._execute_workflow(state, workflow, {})

    def abort(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
    ) -> WorkflowExecutionState:
        """Synchronously abort a workflow, unwinding compensations in reverse order.

        Args:
            run_id: Execution identifier of the workflow run to terminate.
            workflow: WorkflowDefinition containing any optional step compensations.

        Returns:
            Terminal WorkflowExecutionState marked CANCELLED.
        """
        return asyncio.run(self.abort_async(run_id, workflow))

    async def abort_async(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
    ) -> WorkflowExecutionState:
        """Asynchronously abort a workflow, unwinding compensations in reverse order.

        Args:
            run_id: Execution identifier of the workflow run to terminate.
            workflow: WorkflowDefinition containing any optional step compensations.

        Returns:
            Terminal WorkflowExecutionState marked CANCELLED.
        """
        state = self._store.get_run(run_id)
        if not state:
            raise WorkflowAborted(f"Workflow run '{run_id}' not found.")

        checkpoints = self._store.get_checkpoints(run_id)
        # Execute compensations for completed steps in reverse order
        for chk in reversed(checkpoints):
            if chk.status == StepStatus.COMPLETED:
                try:
                    step = workflow.get_step(chk.step_name)
                    if step.compensation:
                        ctx = StepContext(
                            run_id=run_id,
                            stage_name=chk.stage_name,
                            step_name=chk.step_name,
                            inputs=chk.input_payload if isinstance(chk.input_payload, dict) else {},
                        )
                        await self._invoke_callable(step.compensation, ctx)
                except StepNotFoundError:
                    continue

        state.status = WorkflowStatus.CANCELLED
        state.finished_at = datetime.now(UTC)
        self._store.save_run(state)
        return state

    async def _execute_workflow(
        self,
        state: WorkflowExecutionState,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any],
        skipped_steps: set[str] | None = None,
    ) -> WorkflowExecutionState:
        """Internal execution loop advancing stages and evaluating DAG dependencies."""
        cached_outputs: dict[str, Any] = {}
        active_skips = set(skipped_steps or ())

        # Re-populate cached outputs from already completed checkpoints (resumption path)
        existing_checkpoints = self._store.get_checkpoints(state.run_id)
        for chk in existing_checkpoints:
            state.step_checkpoints[chk.step_name] = chk
            if chk.status == StepStatus.COMPLETED:
                cached_outputs[chk.step_name] = chk.output_payload
            elif chk.status == StepStatus.SKIPPED:
                cached_outputs[chk.step_name] = None

        for stage in workflow.stages:
            state.current_stage = stage.name
            self._store.save_run(state)

            try:
                await self._execute_stage(
                    state, stage, workflow, cached_outputs, inputs, active_skips
                )
            except WorkflowSuspended as suspended_err:
                state.status = WorkflowStatus.SUSPENDED
                state.error_summary = str(suspended_err)
                self._store.save_run(state)
                return state

        state.status = WorkflowStatus.COMPLETED
        state.finished_at = datetime.now(UTC)
        self._store.save_run(state)
        return state

    async def _execute_stage(
        self,
        state: WorkflowExecutionState,
        stage: StageDefinition,
        workflow: WorkflowDefinition,
        cached_outputs: dict[str, Any],
        initial_inputs: dict[str, Any],
        skipped_steps: set[str],
    ) -> None:
        """Execute all steps within a single stage according to its execution mode."""
        if stage.execution_mode == StageExecutionMode.SEQUENTIAL:
            for step in stage.steps:
                res = await self._execute_step(
                    state, stage, step, cached_outputs, initial_inputs, skipped_steps
                )
                cached_outputs[step.name] = res
        else:
            # CONCURRENT execution for steps in this stage
            tasks = [
                self._execute_step(
                    state, stage, step, cached_outputs, initial_inputs, skipped_steps
                )
                for step in stage.steps
            ]
            results = await asyncio.gather(*tasks)
            for step, res in zip(stage.steps, results, strict=True):
                cached_outputs[step.name] = res

    async def _execute_step(
        self,
        state: WorkflowExecutionState,
        stage: StageDefinition,
        step: StepDefinition,
        cached_outputs: dict[str, Any],
        initial_inputs: dict[str, Any],
        skipped_steps: set[str],
    ) -> Any:
        """Execute an individual step with checkpoint caching, barrier check, and retries."""
        # 1. Resumption Check: If step is already COMPLETED or SKIPPED, return cached output
        existing_chk = self._store.get_checkpoint(state.run_id, step.name)
        if existing_chk and existing_chk.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
            return existing_chk.output_payload

        # 2. Explicit Skip: If step is requested to be skipped, checkpoint as SKIPPED immediately
        if step.name in skipped_steps:
            start_time = datetime.now(UTC)
            chk = CheckpointRecord(
                run_id=state.run_id,
                stage_name=stage.name,
                step_name=step.name,
                status=StepStatus.SKIPPED,
                attempt_number=1,
                input_payload=initial_inputs,
                output_payload=None,
                started_at=start_time,
                completed_at=start_time,
                duration_seconds=0.0,
            )
            self._store.save_checkpoint(chk)
            state.step_checkpoints[step.name] = chk
            return None

        # 3. Join Barrier Verification & Trigger Rule Evaluation:
        parent_statuses: list[StepStatus] = []
        for dep in step.depends_on:
            dep_chk = state.step_checkpoints.get(dep) or self._store.get_checkpoint(
                state.run_id, dep
            )
            if not dep_chk:
                raise WorkflowSuspended(
                    run_id=state.run_id,
                    failed_step=step.name,
                    reason=f"Join barrier unsatisfied: parent step '{dep}' not completed or evaluated.",
                )
            parent_statuses.append(dep_chk.status)

        # Evaluate trigger rule against parent statuses
        if not evaluate_trigger_rule(step.trigger_rule, parent_statuses):
            start_time = datetime.now(UTC)
            chk = CheckpointRecord(
                run_id=state.run_id,
                stage_name=stage.name,
                step_name=step.name,
                status=StepStatus.SKIPPED,
                attempt_number=1,
                input_payload=initial_inputs,
                output_payload=None,
                started_at=start_time,
                completed_at=start_time,
                duration_seconds=0.0,
            )
            self._store.save_checkpoint(chk)
            state.step_checkpoints[step.name] = chk
            return None

        # 4. Resolve Step Inputs
        step_inputs: dict[str, Any] = dict(initial_inputs)
        for dep in step.depends_on:
            step_inputs[dep] = cached_outputs.get(dep)

        # 5. Execution & Transient Retry Loop
        attempt = 1
        policy = step.retry_policy
        start_time = datetime.now(UTC)

        while True:
            ctx = StepContext(
                run_id=state.run_id,
                stage_name=stage.name,
                step_name=step.name,
                attempt_number=attempt,
                inputs=step_inputs,
            )

            try:
                if step.timeout_seconds:
                    res = await asyncio.wait_for(
                        self._invoke_callable(step.action, ctx),
                        timeout=step.timeout_seconds,
                    )
                else:
                    res = await self._invoke_callable(step.action, ctx)

                end_time = datetime.now(UTC)
                chk = CheckpointRecord(
                    run_id=state.run_id,
                    stage_name=stage.name,
                    step_name=step.name,
                    status=StepStatus.COMPLETED,
                    attempt_number=attempt,
                    input_payload=step_inputs,
                    output_payload=res,
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=(end_time - start_time).total_seconds(),
                )
                self._store.save_checkpoint(chk)
                state.step_checkpoints[step.name] = chk
                return res

            except Exception as exc:
                if policy and policy.should_retry(attempt, exc):
                    delay = policy.calculate_delay(attempt)
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue

                # Permanent failure: checkpoint as FAILED and suspend workflow
                end_time = datetime.now(UTC)
                tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                chk = CheckpointRecord(
                    run_id=state.run_id,
                    stage_name=stage.name,
                    step_name=step.name,
                    status=StepStatus.FAILED,
                    attempt_number=attempt,
                    input_payload=step_inputs,
                    error_traceback=tb_str,
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=(end_time - start_time).total_seconds(),
                )
                self._store.save_checkpoint(chk)
                state.step_checkpoints[step.name] = chk
                raise WorkflowSuspended(
                    run_id=state.run_id,
                    failed_step=step.name,
                    reason=f"{type(exc).__name__}: {exc}",
                ) from exc

    async def _invoke_callable(self, action: Any, ctx: StepContext) -> Any:
        """Invoke action callable, dynamically injecting StepContext or keyword arguments."""
        sig = inspect.signature(action)
        params = list(sig.parameters.values())

        if len(params) == 1 and (
            params[0].annotation == StepContext or params[0].name in ("ctx", "context")
        ):
            args = (ctx,)
            kwargs = {}
        elif len(params) == 0:
            args = ()
            kwargs = {}
        else:
            # Match parameters from ctx.inputs
            args = ()
            kwargs = {p.name: ctx.inputs[p.name] for p in params if p.name in ctx.inputs}

        if inspect.iscoroutinefunction(action):
            return await action(*args, **kwargs)
        return action(*args, **kwargs)


__all__ = [
    "AsyncioWorkflowEngine",
]
