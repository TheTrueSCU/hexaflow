"""Terminal command-line interface for hexaflow.

Notes/Architectural Intent:
    Provides an interactive developer and operator console using Typer and Rich.
    Enables inspecting workflow state, step checkpoints, stack traces, and triggering
    run, status, resume, restart, and abort commands from the terminal.
"""

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.domain.state import StepStatus, WorkflowStatus
from hexaflow.dsl.builder import Workflow

app = typer.Typer(
    name="hexaflow",
    help="Lightweight, embeddable Python workflow engine with checkpointed resumption.",
    add_completion=False,
)
console = Console()


def _load_workflow_from_target(
    target: str,
    params: dict[str, Any] | None = None,
) -> Workflow:
    """Dynamically import a Workflow instance from a Python target or GitHub Actions YAML file.

    Notes/Architectural Intent:
        Supports:
        - GitHub Actions workflow YAML files (*.yml, *.yaml).
        - Direct Workflow instances in a module (e.g. 'pipeline.py:my_workflow').
        - Factory callables returning a Workflow (e.g. 'pipeline.py:build_pipeline'),
          passing optional keyword parameters.
    """
    if target.endswith((".yml", ".yaml")):
        from hexaflow.adapters.loaders.github_actions import load_github_actions_workflow

        try:
            return load_github_actions_workflow(target)
        except Exception as exc:
            raise typer.BadParameter(
                f"Failed to load GitHub Actions workflow '{target}': {exc}"
            ) from exc

    if ":" not in target:
        raise typer.BadParameter(
            "Target must be in the format 'path/to/file.py:workflow_variable' or a YAML file."
        )

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

    attr = getattr(module, attr_name)
    if isinstance(attr, Workflow):
        return attr

    if callable(attr):
        result = attr(**(params or {}))
        if isinstance(result, Workflow):
            return result
        raise typer.BadParameter(
            f"Callable '{attr_name}' returned {type(result).__name__}, expected hexaflow.Workflow."
        )

    raise typer.BadParameter(
        f"Attribute '{attr_name}' is not an instance or factory of hexaflow.Workflow."
    )


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
    wf.bind_store(store)

    console.print(f"[bold green]Starting workflow:[/] [cyan]{wf.name}[/]")
    with wf:
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
    wf.bind_store(store)

    console.print(f"[bold cyan]Resuming workflow:[/] {wf.name} (Run ID: [cyan]{run_id}[/])")
    with wf:
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
    wf.bind_store(store)

    console.print(
        f"[bold yellow]Restarting workflow from start:[/] {wf.name} (Run ID: [cyan]{run_id}[/])"
    )
    with wf:
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
    wf.bind_store(store)

    console.print(f"[bold magenta]Aborting workflow:[/] {wf.name} (Run ID: [cyan]{run_id}[/])")
    with wf:
        state = wf.abort(run_id)
    console.print(f"[bold magenta]Workflow run cancelled.[/] Status: {state.status.value}")


graph_app = typer.Typer(
    name="graph",
    help="Workflow DAG inspection, analysis, and visualization commands.",
    add_completion=False,
)
app.add_typer(graph_app, name="graph")


@graph_app.command("render")
def graph_render(
    target: str = typer.Argument(
        ...,
        help="Workflow target (e.g. 'pipeline.py:wf', 'pipeline.py:factory', or '.github/workflows/ci.yml').",
    ),
    format_name: str = typer.Option(
        "mermaid",
        "--format",
        "-f",
        help="Renderer format ('mermaid', 'dot', 'ascii', 'json').",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Execute dry-run simulation in memory and render materialized dynamic DAG.",
    ),
    params: str | None = typer.Option(
        None,
        "--params",
        "-p",
        help="JSON string of keyword parameters passed to workflow factory functions.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional destination file path to write rendered output to.",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        "-s",
        help="Append rendered diagram to $GITHUB_STEP_SUMMARY markdown.",
    ),
    critical_path: bool = typer.Option(
        False,
        "--critical-path",
        "-c",
        help="Visually highlight the critical path in the rendered diagram.",
    ),
) -> None:
    """Render a workflow dependency graph to Mermaid, DOT, ASCII, or JSON."""
    kw_params: dict[str, Any] = {}
    if params:
        try:
            parsed = json.loads(params)
            if isinstance(parsed, dict):
                kw_params = parsed
            else:
                raise ValueError("Expected a JSON object mapping.")
        except Exception as exc:
            raise typer.BadParameter(f"Invalid --params JSON string: {exc}") from exc

    wf = _load_workflow_from_target(target, params=kw_params)

    from hexaflow.ports.renderer import RenderOptions

    highlighted_nodes: frozenset[str] = frozenset()
    if dry_run:
        console.print(f"[bold cyan]Simulating workflow:[/] {wf.name} (dry-run mode)")
        sim_state = wf.simulate()
        completed = frozenset(
            k for k, cp in sim_state.step_checkpoints.items() if cp.status == StepStatus.COMPLETED
        )
        highlighted_nodes = completed
        console.print(
            f"[bold green]✓ Simulation complete:[/] {len(completed)} steps executed (Status: {sim_state.status.value})"
        )

    opts = RenderOptions(
        highlight_critical_path=critical_path,
        highlight_node_ids=highlighted_nodes,
    )
    rendered_text = wf.render(format_name=format_name, options=opts)

    if output is not None:
        output.write_text(rendered_text, encoding="utf-8")
        console.print(f"[bold green]✓ Diagram successfully written to:[/] {output}")
        return

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY") if summary else None
    if summary and summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"### 🗺️ Planned Hexaflow Workflow: {wf.name}\n\n")
            f.write(f"```{format_name}\n{rendered_text}```\n")
        console.print(f"[bold green]✓ Rendered diagram appended to:[/] {summary_path}")
        return

    print(rendered_text, end="" if rendered_text.endswith("\n") else "\n")


@graph_app.command("info")
def graph_info(
    target: str = typer.Argument(
        ...,
        help="Workflow target (e.g. 'pipeline.py:wf', 'pipeline.py:factory', or '.github/workflows/ci.yml').",
    ),
    params: str | None = typer.Option(
        None,
        "--params",
        "-p",
        help="JSON string of keyword parameters passed to workflow factory functions.",
    ),
) -> None:
    """Display topological metrics, stage distribution, and critical path for a workflow."""
    kw_params: dict[str, Any] = {}
    if params:
        try:
            parsed = json.loads(params)
            if isinstance(parsed, dict):
                kw_params = parsed
        except Exception:
            pass

    wf = _load_workflow_from_target(target, params=kw_params)
    graph = wf.to_graph()

    table = Table(title=f"Workflow Topological Graph: {wf.name} (v{wf.version})")
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="green")

    crit = list(graph.critical_path())
    table.add_row("Total Steps (Nodes)", str(len(graph.nodes)))
    table.add_row("Total Dependencies (Edges)", str(len(graph.edges)))
    table.add_row("Stages", ", ".join(graph.stages.keys()))
    table.add_row("Critical Path Length", str(len(crit)))
    table.add_row("Critical Path Sequence", " ➔ ".join(crit))
    table.add_row("Estimated Latency", f"{graph.critical_path_duration():.2f}s")

    console.print(table)


__all__ = [
    "app",
    "graph_app",
]
