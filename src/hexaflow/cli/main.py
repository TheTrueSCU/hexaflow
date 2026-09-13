"""Terminal command-line interface for hexaflow.

Notes/Architectural Intent:
    Provides an interactive developer and operator console using Typer and Rich.
    Enables inspecting workflow state, step checkpoints, stack traces, and triggering
    run, status, resume, restart, and abort commands from the terminal.
"""

import importlib.util
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.domain.state import StepStatus, WorkflowStatus
from hexaflow.dsl.builder import Workflow

app = typer.Typer(
    name="hexaflow",
    help="Lightweight, embeddable Python workflow engine with checkpointed resumption.",
    add_completion=False,
)
console = Console()


def _load_workflow_from_target(target: str) -> Workflow:
    """Dynamically import a Workflow instance from a 'path/to/file.py:workflow_name' string."""
    if ":" not in target:
        raise typer.BadParameter("Target must be in the format 'path/to/file.py:workflow_variable'")

    file_part, attr_name = target.split(":", 1)
    file_path = Path(file_part).resolve()

    if not file_path.exists():
        raise typer.BadParameter(f"File '{file_path}' does not exist.")

    spec = importlib.util.spec_from_file_location("dynamic_hexaflow_module", file_path)
    if not spec or not spec.loader:
        raise typer.BadParameter(f"Failed to load module spec from '{file_path}'.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, attr_name):
        raise typer.BadParameter(f"Module '{file_path}' does not define '{attr_name}'.")

    wf = getattr(module, attr_name)
    if not isinstance(wf, Workflow):
        raise typer.BadParameter(
            f"Attribute '{attr_name}' is not an instance of hexaflow.Workflow."
        )

    return wf


@app.command()
def status(
    run_id: str = typer.Argument(..., help="Workflow execution run ID."),
    db_path: str = typer.Option(
        ".hexaflow/state.db", "--db", help="Path to SQLite state database."
    ),
) -> None:
    """Display the execution status, stages, and step checkpoints of a workflow run."""
    store = SqliteStateStore(db_path=db_path)
    run = store.get_run(run_id)

    if not run:
        console.print(
            f"[bold red]Error:[/] Workflow run '{run_id}' not found in database '{db_path}'."
        )
        raise typer.Exit(code=1)

    status_color = {
        WorkflowStatus.COMPLETED: "bold green",
        WorkflowStatus.RUNNING: "bold cyan",
        WorkflowStatus.SUSPENDED: "bold yellow",
        WorkflowStatus.CANCELLED: "bold magenta",
        WorkflowStatus.PENDING: "bold white",
    }.get(run.status, "white")

    header_table = Table.grid(padding=(0, 2))
    header_table.add_column(style="bold")
    header_table.add_column()
    header_table.add_row("Run ID:", run.run_id)
    header_table.add_row("Workflow:", run.workflow_name)
    header_table.add_row("Status:", f"[{status_color}]{run.status.value}[/]")
    if run.current_stage:
        header_table.add_row("Current Stage:", run.current_stage)
    header_table.add_row("Started At:", run.started_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    if run.finished_at:
        header_table.add_row("Finished At:", run.finished_at.strftime("%Y-%m-%d %H:%M:%S UTC"))

    console.print(Panel(header_table, title=f"Workflow Run: {run.workflow_name}", expand=False))

    checkpoints = store.get_checkpoints(run_id)
    if not checkpoints:
        console.print("[dim]No step checkpoints recorded yet.[/]")
        return

    table = Table(title="Step Checkpoints", expand=True)
    table.add_column("Stage", style="cyan")
    table.add_column("Step", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Attempt", justify="right")
    table.add_column("Duration (s)", justify="right")
    table.add_column("Completed At")

    for chk in checkpoints:
        chk_color = {
            StepStatus.COMPLETED: "[green]COMPLETED[/]",
            StepStatus.RUNNING: "[cyan]RUNNING[/]",
            StepStatus.FAILED: "[red]FAILED[/]",
            StepStatus.SKIPPED: "[dim]SKIPPED[/]",
            StepStatus.PENDING: "[white]PENDING[/]",
        }.get(chk.status, chk.status.value)

        completed_str = chk.completed_at.strftime("%H:%M:%S") if chk.completed_at else "-"
        table.add_row(
            chk.stage_name,
            chk.step_name,
            chk_color,
            str(chk.attempt_number),
            f"{chk.duration_seconds:.2f}",
            completed_str,
        )

    console.print(table)

    if run.error_summary:
        console.print(
            Panel(
                run.error_summary,
                title="[bold red]Error Diagnostic / Suspension Reason[/]",
                border_style="red",
            )
        )


@app.command()
def inspect(
    run_id: str = typer.Argument(..., help="Workflow execution run ID."),
    step_name: str = typer.Argument(..., help="Step identifier to inspect."),
    db_path: str = typer.Option(
        ".hexaflow/state.db", "--db", help="Path to SQLite state database."
    ),
) -> None:
    """Inspect input and output payloads or error tracebacks for a specific step."""
    store = SqliteStateStore(db_path=db_path)
    chk = store.get_checkpoint(run_id, step_name)

    if not chk:
        console.print(
            f"[bold red]Error:[/] Checkpoint for step '{step_name}' in run '{run_id}' not found."
        )
        raise typer.Exit(code=1)

    console.print(f"[bold]Step:[/] [cyan]{chk.step_name}[/] (Stage: [cyan]{chk.stage_name}[/])")
    console.print(f"[bold]Status:[/] {chk.status.value} (Attempt: {chk.attempt_number})")

    if chk.input_payload is not None:
        input_json = json.dumps(chk.input_payload, indent=2, default=str)
        console.print(Panel(Syntax(input_json, "json", theme="monokai"), title="Input Payload"))

    if chk.output_payload is not None:
        output_json = json.dumps(chk.output_payload, indent=2, default=str)
        console.print(Panel(Syntax(output_json, "json", theme="monokai"), title="Output Payload"))

    if chk.error_traceback:
        console.print(
            Panel(
                Syntax(chk.error_traceback, "pytb", theme="monokai"),
                title="[bold red]Error Traceback[/]",
                border_style="red",
            )
        )


@app.command()
def run(
    target: str = typer.Argument(..., help="Workflow target (e.g. 'pipeline.py:my_workflow')."),
    db_path: str = typer.Option(
        ".hexaflow/state.db", "--db", help="Path to SQLite state database."
    ),
) -> None:
    """Execute a workflow definition locally."""
    wf = _load_workflow_from_target(target)
    store = SqliteStateStore(db_path=db_path)
    wf._store = store
    wf._engine = AsyncioWorkflowEngine(state_store=store)

    console.print(f"[bold green]Starting workflow:[/] [cyan]{wf.name}[/]")
    state = wf.run()

    if state.status == WorkflowStatus.COMPLETED:
        console.print(
            f"[bold green]✓ Workflow completed successfully![/] (Run ID: [cyan]{state.run_id}[/])"
        )
    elif state.status == WorkflowStatus.SUSPENDED:
        console.print(
            f"[bold yellow]! Workflow suspended at stage '{state.current_stage}'[/] (Run ID: [cyan]{state.run_id}[/])"
        )
        console.print(
            f"Run [bold cyan]hexaflow status {state.run_id}[/] for diagnostics, or [bold cyan]hexaflow resume {state.run_id} {target}[/] after fixing."
        )
        raise typer.Exit(code=1)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run ID of the suspended workflow."),
    target: str = typer.Argument(..., help="Workflow target (e.g. 'pipeline.py:my_workflow')."),
    db_path: str = typer.Option(
        ".hexaflow/state.db", "--db", help="Path to SQLite state database."
    ),
) -> None:
    """Resume a suspended workflow from its latest checkpoints without re-running completed steps."""
    wf = _load_workflow_from_target(target)
    store = SqliteStateStore(db_path=db_path)
    wf._store = store
    wf._engine = AsyncioWorkflowEngine(state_store=store)

    console.print(f"[bold cyan]Resuming workflow:[/] {wf.name} (Run ID: [cyan]{run_id}[/])")
    state = wf.resume(run_id)

    if state.status == WorkflowStatus.COMPLETED:
        console.print(
            f"[bold green]✓ Workflow completed successfully after resumption![/] (Run ID: [cyan]{state.run_id}[/])"
        )
    elif state.status == WorkflowStatus.SUSPENDED:
        console.print(
            f"[bold yellow]! Workflow suspended again[/] (Run ID: [cyan]{state.run_id}[/])"
        )
        raise typer.Exit(code=1)


@app.command()
def restart(
    run_id: str = typer.Argument(..., help="Run ID of the workflow to restart."),
    target: str = typer.Argument(..., help="Workflow target (e.g. 'pipeline.py:my_workflow')."),
    db_path: str = typer.Option(
        ".hexaflow/state.db", "--db", help="Path to SQLite state database."
    ),
) -> None:
    """Restart a workflow execution run from the beginning."""
    wf = _load_workflow_from_target(target)
    store = SqliteStateStore(db_path=db_path)
    wf._store = store
    wf._engine = AsyncioWorkflowEngine(state_store=store)

    console.print(
        f"[bold yellow]Restarting workflow from start:[/] {wf.name} (Run ID: [cyan]{run_id}[/])"
    )
    state = wf.restart(run_id)

    if state.status == WorkflowStatus.COMPLETED:
        console.print(
            f"[bold green]✓ Workflow completed successfully![/] (Run ID: [cyan]{state.run_id}[/])"
        )


@app.command()
def abort(
    run_id: str = typer.Argument(..., help="Run ID of the workflow to abort."),
    target: str = typer.Argument(..., help="Workflow target (e.g. 'pipeline.py:my_workflow')."),
    db_path: str = typer.Option(
        ".hexaflow/state.db", "--db", help="Path to SQLite state database."
    ),
) -> None:
    """Abort an active or suspended workflow, unwinding any step compensations."""
    wf = _load_workflow_from_target(target)
    store = SqliteStateStore(db_path=db_path)
    wf._store = store
    wf._engine = AsyncioWorkflowEngine(state_store=store)

    console.print(f"[bold magenta]Aborting workflow:[/] {wf.name} (Run ID: [cyan]{run_id}[/])")
    state = wf.abort(run_id)
    console.print(f"[bold magenta]Workflow run cancelled.[/] Status: {state.status.value}")


__all__ = [
    "app",
]
