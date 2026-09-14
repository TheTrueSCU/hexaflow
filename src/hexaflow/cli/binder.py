"""Dynamic CLI argument binding and option generation for Hexaflow workflows.

Notes/Architectural Intent:
    Introspects WorkflowDefinition and Workflow DSL instances to generate dynamic
    `--skip-<step>` CLI switches for Typer and Click applications. Enables fine-grained
    step skipping without manual option boilerplate across quality and orchestration suites.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import typer

from hexaflow.domain.models import WorkflowDefinition

if TYPE_CHECKING:
    from hexaflow.dsl.builder import Workflow


@dataclass(frozen=True)
class CliOptionSpec:
    """Specification of an automagically generated CLI skip option.

    Notes/Architectural Intent:
        Represents the mapping between a DAG step name, its Python parameter name,
        and the command-line flag variants (including aliases).
    """

    step_name: str
    param_name: str
    cli_flags: list[str]
    help_text: str


class WorkflowCliBinder:
    """Introspects workflow step DAGs to bind dynamic skip switches to CLI entrypoints.

    Notes/Architectural Intent:
        Eliminates duplicate boilerplate across CLI tools by extracting step names
        directly from the workflow topology, formatting CLI flags (e.g. `--skip-ruff`),
        supporting shorthand aliases (e.g. `--skip-ty`), and populating `skip_steps` sets.
    """

    def __init__(
        self,
        workflow: Workflow | WorkflowDefinition,
        aliases: dict[str, list[str]] | None = None,
    ) -> None:
        """Initialize WorkflowCliBinder.

        Args:
            workflow: Workflow builder or compiled WorkflowDefinition to inspect.
            aliases: Optional mapping of step_name to additional flag aliases (e.g. `{"typecheck": ["--skip-ty"]}`).
        """
        self._aliases: dict[str, list[str]] = aliases or {}

        # Extract step names in deterministic topological order
        if isinstance(workflow, WorkflowDefinition):
            self._definition: WorkflowDefinition = workflow
        else:
            self._definition = workflow.to_definition()

        self._step_names: list[str] = [
            step.name for stage in self._definition.stages for step in stage.steps
        ]

        self._specs: dict[str, CliOptionSpec] = {}
        for step_name in self._step_names:
            normalized_flag = f"--skip-{step_name.replace('_', '-')}"
            param_name = f"skip_{step_name.replace('-', '_')}"
            extra_flags = self._aliases.get(step_name, [])

            flags = [normalized_flag]
            for extra in extra_flags:
                if extra not in flags:
                    flags.append(extra)

            spec = CliOptionSpec(
                step_name=step_name,
                param_name=param_name,
                cli_flags=flags,
                help_text=f"Skip execution of the '{step_name}' workflow step.",
            )
            self._specs[step_name] = spec

    @property
    def step_names(self) -> list[str]:
        """Return list of all registered step names."""
        return list(self._step_names)

    @property
    def specs(self) -> list[CliOptionSpec]:
        """Return all generated option specifications."""
        return list(self._specs.values())

    def get_spec(self, step_name: str) -> CliOptionSpec | None:
        """Retrieve the CliOptionSpec for a given step name.

        Args:
            step_name: The identifier of the step.

        Returns:
            The CliOptionSpec if found, None otherwise.
        """
        return self._specs.get(step_name)

    def extract_skips(self, **kwargs: Any) -> set[str]:
        """Extract set of step names to skip from keyword arguments.

        Args:
            **kwargs: Arbitrary keyword arguments passed from CLI invocation.

        Returns:
            Set of step names whose skip flags were set to True.

        Notes/Architectural Intent:
            Checks both parameter names (e.g. `skip_ruff`) and flag strings
            (e.g. `--skip-ruff`).
        """
        skipped: set[str] = set()

        for step_name, spec in self._specs.items():
            # Check direct parameter name
            if kwargs.get(spec.param_name) is True:
                skipped.add(step_name)
                continue

            # Check any alias parameter names (e.g. skip_ty from --skip-ty)
            for flag in spec.cli_flags:
                stripped = flag.lstrip("-").replace("-", "_")
                if kwargs.get(stripped) is True or kwargs.get(flag) is True:
                    skipped.add(step_name)
                    break

        return skipped

    def parse_argv(self, argv: list[str]) -> set[str]:
        """Extract set of step names to skip directly from command-line argument lists.

        Args:
            argv: Raw argument strings (such as `sys.argv[1:]`).

        Returns:
            Set of step names matched in the argument list.
        """
        skipped: set[str] = set()
        for step_name, spec in self._specs.items():
            for flag in spec.cli_flags:
                if flag in argv:
                    skipped.add(step_name)
                    break
        return skipped

    def apply(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator dynamically injecting --skip-<step> options into a Typer/Click command.

        Args:
            fn: Target command function.

        Returns:
            Wrapped command function with dynamically appended skip parameters.

        Notes/Architectural Intent:
            Modifies `__signature__` to expose Typer options for each step in the DAG.
            When invoked, parses the skip flags, bundles them into `skip_steps: set[str]`,
            and passes `skip_steps` to the wrapped function while omitting `skip_steps`
            from the Typer CLI parameter list.
        """
        orig_sig = inspect.signature(fn)
        new_params = []

        # Retain original parameters except `skip_steps` which is injected by this decorator
        for p in orig_sig.parameters.values():
            if p.name == "skip_steps":
                continue
            new_params.append(p)

        existing_names = {p.name for p in new_params}
        injected_param_to_step: dict[str, str] = {}

        for spec in self._specs.values():
            if spec.param_name in existing_names:
                injected_param_to_step[spec.param_name] = spec.step_name
                continue

            # Create Typer Option parameter with all flag aliases
            opt = typer.Option(
                False,
                *spec.cli_flags,
                help=spec.help_text,
            )
            param = inspect.Parameter(
                name=spec.param_name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=opt,
                annotation=bool,
            )
            new_params.append(param)
            injected_param_to_step[spec.param_name] = spec.step_name

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract skipped step names from injected kwargs
            detected_skips: set[str] = set()
            for param_name, step_name in injected_param_to_step.items():
                val = kwargs.get(param_name)
                if val is True:
                    detected_skips.add(step_name)
                # If param was not in orig_sig, pop it so fn doesn't receive unexpected kwargs
                if param_name not in orig_sig.parameters:
                    kwargs.pop(param_name, None)

            # Inject skip_steps into the target function if expected
            if "skip_steps" in orig_sig.parameters:
                kwargs["skip_steps"] = detected_skips

            return fn(*args, **kwargs)

        setattr(wrapper, "__signature__", orig_sig.replace(parameters=new_params))  # noqa: B010
        return wrapper


__all__ = [
    "CliOptionSpec",
    "WorkflowCliBinder",
]
