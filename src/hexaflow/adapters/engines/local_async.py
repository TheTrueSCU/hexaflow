"""Localhost asynchronous workflow execution engine.

Notes/Architectural Intent:
    Implements WorkflowEnginePort using standard asyncio primitives. Coordinates
    stage-by-stage and step-by-step DAG execution, evaluates join barriers, invokes
    transient retry backoffs, writes durable checkpoints, and suspends execution cleanly
    upon permanent failure without corrupting state.
"""

import asyncio
import concurrent.futures
import inspect
import multiprocessing
import os
import traceback
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import uuid4

from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.exceptions import StepNotFoundError, WorkflowAborted, WorkflowSuspended
from hexaflow.domain.models import (
    ExecutionPool,
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
        Supports cooperative ASYNC, multi-threaded THREAD, and multi-process PROCESS
        execution strategies.
    """

    def __init__(
        self,
        state_store: WorkflowStateStorePort | None = None,
        max_process_workers: int | None = None,
        max_thread_workers: int | None = None,
    ) -> None:
        """Initialize the engine with an optional state store and worker concurrency bounds.

        Args:
            state_store: Persistence store for checkpoints. Defaults to InMemoryStateStore.
            max_process_workers: Optional maximum worker processes for ExecutionPool.PROCESS.
            max_thread_workers: Optional maximum worker threads for ExecutionPool.THREAD.
        """
        self._store: WorkflowStateStorePort = state_store or InMemoryStateStore()
        self._max_process_workers = max_process_workers
        self._max_thread_workers = max_thread_workers
        self._process_pool: concurrent.futures.ProcessPoolExecutor | None = None
        self._thread_pool: concurrent.futures.ThreadPoolExecutor | None = None

    def _get_process_pool(self) -> concurrent.futures.ProcessPoolExecutor:
        """Get or lazily instantiate the shared process worker pool."""
        if self._process_pool is None:
            ctx = multiprocessing.get_context("spawn")
            self._process_pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=self._max_process_workers,
                mp_context=ctx,
            )
        return self._process_pool

    def _get_thread_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        """Get or lazily instantiate the shared thread worker pool."""
        if self._thread_pool is None:
            self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_thread_workers
            )
        return self._thread_pool

    def close(self) -> None:
        """Shut down background process and thread worker pools gracefully."""
        if self._process_pool is not None:
            self._process_pool.shutdown(wait=True)
            self._process_pool = None
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=True)
            self._thread_pool = None

    async def aclose(self) -> None:
        """Asynchronously shut down background worker pools without blocking the event loop."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.close)

    def __enter__(self) -> "AsyncioWorkflowEngine":
        """Enter context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit context manager, shutting down worker pools."""
        self.close()

    async def __aenter__(self) -> "AsyncioWorkflowEngine":
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context manager, asynchronously shutting down worker pools."""
        await self.aclose()

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

        # 4.5. Dynamic Step Mapping Fan-out
        if step.is_mapped:
            return await self._execute_mapped_step(
                state, stage, step, cached_outputs, initial_inputs, step_inputs
            )

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
                        self._invoke_callable(step.action, ctx, pool=step.pool),
                        timeout=step.timeout_seconds,
                    )
                else:
                    res = await self._invoke_callable(step.action, ctx, pool=step.pool)

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

    async def _invoke_callable(
        self,
        action: Any,
        ctx: StepContext,
        pool: ExecutionPool = ExecutionPool.ASYNC,
    ) -> Any:
        """Invoke action callable according to its execution pool strategy."""
        if pool == ExecutionPool.PROCESS:
            loop = asyncio.get_running_loop()
            ctx_dict = ctx.model_dump()
            return await loop.run_in_executor(
                self._get_process_pool(),
                _process_step_trampoline,
                action,
                ctx_dict,
            )
        if pool == ExecutionPool.THREAD:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._get_thread_pool(),
                _thread_step_trampoline,
                action,
                ctx,
            )

        args, kwargs = _bind_step_args(action, ctx)
        if inspect.iscoroutinefunction(action):
            return await action(*args, **kwargs)
        return action(*args, **kwargs)

    async def _execute_mapped_step(
        self,
        state: WorkflowExecutionState,
        stage: StageDefinition,
        step: StepDefinition,
        cached_outputs: dict[str, Any],
        initial_inputs: dict[str, Any],
        step_inputs: dict[str, Any],
    ) -> list[Any]:
        """Execute a dynamically mapped step fanning out across a runtime iterable.

        Args:
            state: Active mutable workflow execution state.
            stage: StageDefinition containing the step.
            step: StepDefinition marked with is_mapped=True.
            cached_outputs: Current map of evaluated step outputs.
            initial_inputs: Root inputs passed to workflow execution.
            step_inputs: Resolved inputs for this step including upstream dependencies.

        Returns:
            Ordered list of outputs from all executed mapped sub-steps.

        Raises:
            WorkflowSuspended: If the collection cannot be resolved or is not iterable.

        Notes/Architectural Intent:
            Evaluates the collection at runtime without upfront DAG size constraints.
            Persists individual sub-step checkpoints and skips already-completed sub-steps
            during resumption.
        """
        map_key = step.map_over or ""
        collection = step_inputs.get(map_key)
        if collection is None and map_key in initial_inputs:
            collection = initial_inputs[map_key]
        if collection is None and map_key in cached_outputs:
            collection = cached_outputs[map_key]

        if collection is None:
            raise WorkflowSuspended(
                run_id=state.run_id,
                failed_step=step.name,
                reason=f"Mapped step '{step.name}' target '{map_key}' not found in inputs or parent outputs.",
            )

        if not hasattr(collection, "__iter__"):
            raise WorkflowSuspended(
                run_id=state.run_id,
                failed_step=step.name,
                reason=f"Mapped step '{step.name}' target '{map_key}' is not iterable (got {type(collection).__name__}).",
            )

        items = list(collection.values() if isinstance(collection, dict) else collection)
        start_time = datetime.now(UTC)

        if not items:
            chk = CheckpointRecord(
                run_id=state.run_id,
                stage_name=stage.name,
                step_name=step.name,
                status=StepStatus.COMPLETED,
                attempt_number=1,
                input_payload=step_inputs,
                output_payload=[],
                started_at=start_time,
                completed_at=start_time,
                duration_seconds=0.0,
            )
            self._store.save_checkpoint(chk)
            state.step_checkpoints[step.name] = chk
            return []

        limit = step.concurrency_limit
        if limit is None and step.pool == ExecutionPool.PROCESS:
            limit = self._max_process_workers or (
                getattr(os, "process_cpu_count", os.cpu_count)() or 1
            )

        sem = asyncio.Semaphore(limit) if limit and limit > 0 else None

        async def _run_item(idx: int, item_val: Any) -> Any:
            if sem:
                async with sem:
                    return await self._execute_mapped_sub_step(
                        state, stage, step, step_inputs, idx, item_val
                    )
            return await self._execute_mapped_sub_step(
                state, stage, step, step_inputs, idx, item_val
            )

        tasks = [_run_item(i, val) for i, val in enumerate(items)]
        results = await asyncio.gather(*tasks)

        end_time = datetime.now(UTC)
        chk = CheckpointRecord(
            run_id=state.run_id,
            stage_name=stage.name,
            step_name=step.name,
            status=StepStatus.COMPLETED,
            attempt_number=1,
            input_payload=step_inputs,
            output_payload=list(results),
            started_at=start_time,
            completed_at=end_time,
            duration_seconds=(end_time - start_time).total_seconds(),
        )
        self._store.save_checkpoint(chk)
        state.step_checkpoints[step.name] = chk
        return list(results)

    async def _execute_mapped_sub_step(
        self,
        state: WorkflowExecutionState,
        stage: StageDefinition,
        step: StepDefinition,
        step_inputs: dict[str, Any],
        idx: int,
        item: Any,
    ) -> Any:
        """Execute an individual sub-step of a mapped fan-out with retries and checkpoints.

        Args:
            state: Active workflow execution state.
            stage: StageDefinition milestone.
            step: Parent StepDefinition metadata.
            step_inputs: Shared inputs dictionary.
            idx: Index of this item in the mapped collection.
            item: The runtime value being processed.

        Returns:
            The output of the sub-step execution.

        Raises:
            WorkflowSuspended: If the sub-step fails permanently.

        Notes/Architectural Intent:
            Maintains per-item checkpoint isolation, allowing granular retry policies
            and partial resumption of failed items without repeating successful items.
        """
        sub_step_name = f"{step.name}[{idx}]"

        existing_chk = self._store.get_checkpoint(state.run_id, sub_step_name)
        if existing_chk and existing_chk.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
            return existing_chk.output_payload

        sub_inputs = dict(step_inputs)
        sub_inputs["item"] = item
        sub_inputs["index"] = idx

        attempt = 1
        policy = step.retry_policy
        start_time = datetime.now(UTC)

        while True:
            ctx = StepContext(
                run_id=state.run_id,
                stage_name=stage.name,
                step_name=sub_step_name,
                attempt_number=attempt,
                inputs=sub_inputs,
            )

            try:
                if step.timeout_seconds:
                    res = await asyncio.wait_for(
                        self._invoke_mapped_callable(step.action, item, ctx, pool=step.pool),
                        timeout=step.timeout_seconds,
                    )
                else:
                    res = await self._invoke_mapped_callable(step.action, item, ctx, pool=step.pool)

                end_time = datetime.now(UTC)
                chk = CheckpointRecord(
                    run_id=state.run_id,
                    stage_name=stage.name,
                    step_name=sub_step_name,
                    status=StepStatus.COMPLETED,
                    attempt_number=attempt,
                    input_payload={"item": item, "index": idx},
                    output_payload=res,
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=(end_time - start_time).total_seconds(),
                )
                self._store.save_checkpoint(chk)
                state.step_checkpoints[sub_step_name] = chk
                return res

            except Exception as exc:
                if policy and policy.should_retry(attempt, exc):
                    delay = policy.calculate_delay(attempt)
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue

                end_time = datetime.now(UTC)
                tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                chk = CheckpointRecord(
                    run_id=state.run_id,
                    stage_name=stage.name,
                    step_name=sub_step_name,
                    status=StepStatus.FAILED,
                    attempt_number=attempt,
                    input_payload={"item": item, "index": idx},
                    error_traceback=tb_str,
                    started_at=start_time,
                    completed_at=end_time,
                    duration_seconds=(end_time - start_time).total_seconds(),
                )
                self._store.save_checkpoint(chk)
                state.step_checkpoints[sub_step_name] = chk
                raise WorkflowSuspended(
                    run_id=state.run_id,
                    failed_step=sub_step_name,
                    reason=f"{type(exc).__name__}: {exc}",
                ) from exc

    async def _invoke_mapped_callable(
        self,
        action: Any,
        item: Any,
        ctx: StepContext,
        pool: ExecutionPool = ExecutionPool.ASYNC,
    ) -> Any:
        """Invoke a mapped action callable matching its signature and execution pool strategy.

        Args:
            action: Forward callable or coroutine.
            item: Current mapped value.
            ctx: StepContext for this sub-step.
            pool: Execution strategy (ASYNC, THREAD, PROCESS).

        Returns:
            Result returned by the action callable.

        Notes/Architectural Intent:
            Introspects callable parameter names and annotations to bind item, ctx,
            or keyword arguments cleanly without forcing rigid user signatures.
            Offloads execution to ProcessPoolExecutor or ThreadPoolExecutor when
            requested by step pool policy.
        """
        if pool == ExecutionPool.PROCESS:
            loop = asyncio.get_running_loop()
            ctx_dict = ctx.model_dump()
            return await loop.run_in_executor(
                self._get_process_pool(),
                _process_mapped_trampoline,
                action,
                item,
                ctx_dict,
            )
        if pool == ExecutionPool.THREAD:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._get_thread_pool(),
                _thread_mapped_trampoline,
                action,
                item,
                ctx,
            )

        args, kwargs = _bind_mapped_args(action, item, ctx)
        if inspect.iscoroutinefunction(action):
            return await action(*args, **kwargs)
        return action(*args, **kwargs)


def _bind_step_args(action: Any, ctx: StepContext) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Bind arguments for single step invocation matching callable signature."""
    sig = inspect.signature(action)
    params = list(sig.parameters.values())

    if len(params) == 1 and (
        params[0].annotation == StepContext or params[0].name in ("ctx", "context")
    ):
        return (ctx,), {}
    if len(params) == 0:
        return (), {}

    kwargs = {p.name: ctx.inputs[p.name] for p in params if p.name in ctx.inputs}
    return (), kwargs


def _process_step_trampoline(action: Any, ctx_dict: dict[str, Any]) -> Any:
    """Invoked in child process worker for single step."""
    ctx = StepContext.model_validate(ctx_dict)
    args, kwargs = _bind_step_args(action, ctx)
    if inspect.iscoroutinefunction(action):
        return asyncio.run(action(*args, **kwargs))
    return action(*args, **kwargs)


def _thread_step_trampoline(action: Any, ctx: StepContext) -> Any:
    """Invoked in thread worker for single step."""
    args, kwargs = _bind_step_args(action, ctx)
    if inspect.iscoroutinefunction(action):
        return asyncio.run(action(*args, **kwargs))
    return action(*args, **kwargs)


def _process_mapped_trampoline(action: Any, item: Any, ctx_dict: dict[str, Any]) -> Any:
    """Invoked in child process worker for mapped sub-step."""
    ctx = StepContext.model_validate(ctx_dict)
    args, kwargs = _bind_mapped_args(action, item, ctx)
    if inspect.iscoroutinefunction(action):
        return asyncio.run(action(*args, **kwargs))
    return action(*args, **kwargs)


def _thread_mapped_trampoline(action: Any, item: Any, ctx: StepContext) -> Any:
    """Invoked in thread worker for mapped sub-step."""
    args, kwargs = _bind_mapped_args(action, item, ctx)
    if inspect.iscoroutinefunction(action):
        return asyncio.run(action(*args, **kwargs))
    return action(*args, **kwargs)


def _bind_mapped_args(
    action: Any, item: Any, ctx: StepContext
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Bind arguments for mapped step invocation matching callable signature."""
    sig = inspect.signature(action)
    params = list(sig.parameters.values())
    if not params:
        return (), {}

    first = params[0]
    if first.annotation == StepContext or first.name in ("ctx", "context"):
        if len(params) > 1:
            return (ctx, item), {}
        return (ctx,), {}

    if len(params) == 1:
        return (item,), {}

    second = params[1]
    if second.annotation == StepContext or second.name in ("ctx", "context"):
        return (item, ctx), {}

    kwargs: dict[str, Any] = {}
    for p in params[1:]:
        if p.annotation == StepContext or p.name in ("ctx", "context"):
            kwargs[p.name] = ctx
        elif p.name in ctx.inputs:
            kwargs[p.name] = ctx.inputs[p.name]
    return (item,), kwargs


LocalAsyncWorkflowEngine = AsyncioWorkflowEngine

__all__ = [
    "AsyncioWorkflowEngine",
    "LocalAsyncWorkflowEngine",
]
