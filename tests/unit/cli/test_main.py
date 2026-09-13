"""Unit tests for the hexaflow CLI.

Notes/Architectural Intent:
    Tests Typer CLI commands: status, inspect, run, resume, restart, and abort
    using CliRunner in an isolated SQLite database.
"""

from pathlib import Path

from typer.testing import CliRunner

from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.cli.main import app
from hexaflow.domain.state import (
    CheckpointRecord,
    StepStatus,
    WorkflowExecutionState,
    WorkflowStatus,
)

runner = CliRunner()


def test_cli_help() -> None:
    """Validate top-level CLI help command outputs usage instructions."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Lightweight, embeddable Python workflow engine" in result.stdout


def test_cli_status_not_found(tmp_path: Path) -> None:
    """Validate status command on missing run exits with code 1."""
    db_file = tmp_path / "state.db"
    result = runner.invoke(app, ["status", "missing_id", "--db", str(db_file)])
    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_cli_status_and_inspect_success(tmp_path: Path) -> None:
    """Validate status and inspect commands against a populated SQLite database."""
    db_file = tmp_path / "state.db"
    store = SqliteStateStore(db_path=db_file)

    run = WorkflowExecutionState(
        run_id="run-cli-123",
        workflow_name="etl_pipeline",
        status=WorkflowStatus.COMPLETED,
        current_stage="transform",
    )
    store.save_run(run)

    chk = CheckpointRecord(
        run_id="run-cli-123",
        stage_name="transform",
        step_name="parse_json",
        status=StepStatus.COMPLETED,
        input_payload={"raw": '{"a": 1}'},
        output_payload={"parsed": {"a": 1}},
        duration_seconds=0.45,
    )
    store.save_checkpoint(chk)

    # 1. Test status
    status_res = runner.invoke(app, ["status", "run-cli-123", "--db", str(db_file)])
    assert status_res.exit_code == 0
    assert "etl_pipeline" in status_res.stdout
    assert "parse_json" in status_res.stdout
    assert "COMPLETED" in status_res.stdout

    # 2. Test inspect
    inspect_res = runner.invoke(app, ["inspect", "run-cli-123", "parse_json", "--db", str(db_file)])
    assert inspect_res.exit_code == 0
    assert "parse_json" in inspect_res.stdout
    assert "raw" in inspect_res.stdout

    # 3. Test inspect missing step
    missing_inspect = runner.invoke(
        app, ["inspect", "run-cli-123", "phantom_step", "--db", str(db_file)]
    )
    assert missing_inspect.exit_code == 1


def test_cli_run_resume_restart_abort_lifecycle(tmp_path: Path) -> None:
    """Validate full CLI orchestration: run, resume, restart, and abort via file target."""
    # Create sample workflow script
    script_path = tmp_path / "sample_wf.py"
    script_content = """
from hexaflow import Workflow

my_flow = Workflow("cli_flow")

@my_flow.stage("stage_1")
@my_flow.step("step_1")
def s1(ctx):
    return "ok_1"
"""
    script_path.write_text(script_content)

    db_file = tmp_path / "cli_state.db"
    target = f"{script_path}:my_flow"

    # 1. Test hexaflow run
    run_res = runner.invoke(app, ["run", target, "--db", str(db_file)])
    assert run_res.exit_code == 0
    assert "Workflow completed successfully" in run_res.stdout

    # Get run ID from db
    store = SqliteStateStore(db_path=db_file)
    with store._connection() as conn:
        row = conn.execute("SELECT run_id FROM workflow_runs").fetchone()
        run_id = row["run_id"]

    # 2. Test hexaflow restart
    restart_res = runner.invoke(app, ["restart", run_id, target, "--db", str(db_file)])
    assert restart_res.exit_code == 0
    assert "Restarting workflow from start" in restart_res.stdout

    # 3. Test hexaflow resume
    resume_res = runner.invoke(app, ["resume", run_id, target, "--db", str(db_file)])
    assert resume_res.exit_code == 0
    assert "Resuming workflow" in resume_res.stdout

    # 4. Test hexaflow abort
    abort_res = runner.invoke(app, ["abort", run_id, target, "--db", str(db_file)])
    assert abort_res.exit_code == 0
    assert "Workflow run cancelled" in abort_res.stdout


def test_cli_bad_target_handling(tmp_path: Path) -> None:
    """Validate error handling for malformed target strings."""
    res_no_colon = runner.invoke(app, ["run", "no_colon_path"])
    assert res_no_colon.exit_code != 0

    res_missing_file = runner.invoke(app, ["run", "phantom_file.py:flow"])
    assert res_missing_file.exit_code != 0

    # File exists but missing attr
    script_path = tmp_path / "empty_script.py"
    script_path.write_text("x = 10")
    res_missing_attr = runner.invoke(app, ["run", f"{script_path}:non_existent"])
    assert res_missing_attr.exit_code != 0

    # Attr is not Workflow
    res_bad_type = runner.invoke(app, ["run", f"{script_path}:x"])
    assert res_bad_type.exit_code != 0
