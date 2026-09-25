"""Abstract port interfaces for workflow execution engines.

Notes/Architectural Intent:
    Establishes inward-facing contracts for orchestrating workflows, evaluating DAG
    dependencies, handling transient retries, and supporting resume/restart operations.
"""

from abc import ABC, abstractmethod
from typing import Any

from hexaflow.domain.models import WorkflowDefinition
from hexaflow.domain.state import WorkflowExecutionState


class WorkflowEnginePort(ABC):
    """Abstract port for executing and orchestrating workflows.

    Notes/Architectural Intent:
        Enables interchangeable execution engines (asyncio localhost, process pools,
        distributed cluster runners) without altering workflow definitions or business logic.
    """

    @abstractmethod
    def run(
        self,
        workflow: WorkflowDefinition,
        initial_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Synchronously execute a workflow definition from start to finish.

        Args:
            workflow: Immutable specification of the workflow DAG.
            initial_inputs: Optional dictionary of input arguments passed to root steps.
            skip_steps: Optional collection of step names to explicitly skip.

        Returns:
            Final or suspended WorkflowExecutionState outcome.
        """

    @abstractmethod
    async def run_async(
        self,
        workflow: WorkflowDefinition,
        initial_inputs: dict[str, Any] | None = None,
        skip_steps: set[str] | list[str] | None = None,
    ) -> WorkflowExecutionState:
        """Asynchronously execute a workflow definition from start to finish.

        Args:
            workflow: Immutable specification of the workflow DAG.
            initial_inputs: Optional dictionary of input arguments passed to root steps.
            skip_steps: Optional collection of step names to explicitly skip.

        Returns:
            Final or suspended WorkflowExecutionState outcome.
        """

    @abstractmethod
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
            patch_inputs: Optional override inputs to apply to the resuming step frontier.
            skip_steps: Optional collection of step names to explicitly skip during resumption.

        Returns:
            Updated WorkflowExecutionState outcome.
        """

    @abstractmethod
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
            patch_inputs: Optional override inputs to apply to the resuming step frontier.
            skip_steps: Optional collection of step names to explicitly skip during resumption.

        Returns:
            Updated WorkflowExecutionState outcome.
        """

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    def abort(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
    ) -> WorkflowExecutionState:
        """Synchronously abort an active or suspended workflow, unwinding compensations.

        Args:
            run_id: Execution identifier of the workflow run to terminate.
            workflow: WorkflowDefinition containing any optional step compensations.

        Returns:
            Terminal WorkflowExecutionState marked CANCELLED.
        """

    @abstractmethod
    async def abort_async(
        self,
        run_id: str,
        workflow: WorkflowDefinition,
    ) -> WorkflowExecutionState:
        """Asynchronously abort an active or suspended workflow, unwinding compensations.

        Args:
            run_id: Execution identifier of the workflow run to terminate.
            workflow: WorkflowDefinition containing any optional step compensations.

        Returns:
            Terminal WorkflowExecutionState marked CANCELLED.
        """

    @abstractmethod
    def close(self) -> None:
        """Shut down engine worker pools and release background resources.

        Notes/Architectural Intent:
            Provides a synchronous cleanup hook for execution engines that allocate
            process or thread worker pools.
        """

    async def aclose(self) -> None:
        """Asynchronously shut down engine worker pools and release background resources.

        Notes/Architectural Intent:
            Provides an asynchronous cleanup hook for execution engines. By default,
            delegates to synchronous close().
        """
        self.close()


__all__ = [
    "WorkflowEnginePort",
]
