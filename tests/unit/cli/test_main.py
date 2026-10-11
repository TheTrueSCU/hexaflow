"""Unit tests for the hexaflow CLI.

Notes/Architectural Intent:
    Tests Typer CLI commands: status, inspect, run, resume, restart, and abort
    using CliRunner in an isolated SQLite database.
"""

from pathlib import Path

import pytest
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

    # 3. Test hexaflow resume (set to SUSPENDED to simulate resumption)
    with store._connection() as conn:
        conn.execute("UPDATE workflow_runs SET status = 'SUSPENDED' WHERE run_id = ?", (run_id,))
        conn.commit()

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


def test_cli_graph_render_yaml(tmp_path: Path) -> None:
    yaml_file = tmp_path / "pipeline.yml"
    yaml_file.write_text(
        """
name: Build and Test
jobs:
  build:
    steps: []
  test:
    needs: build
    steps: []
""",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["graph", "render", str(yaml_file), "--format", "mermaid"])
    assert res.exit_code == 0
    assert "build --> test" in res.stdout


def test_cli_graph_render_python_target_and_factory(tmp_path: Path) -> None:
    pipeline_file = tmp_path / "dynamic_pipeline.py"
    pipeline_file.write_text(
        """
from hexaflow import Workflow

def make_workflow(affected_packages=None):
    wf = Workflow("dynamic_wf")
    pkgs = affected_packages or ["core"]

    @wf.step("root")
    def root(ctx): return "root"

    for p in pkgs:
        @wf.step(f"test_{p}", depends_on=["root"])
        def test_pkg(ctx, p=p): return f"tested_{p}"

    return wf

static_wf = make_workflow(["fixed"])
""",
        encoding="utf-8",
    )

    # 1. Static Workflow object
    static_res = runner.invoke(
        app,
        ["graph", "render", f"{pipeline_file}:static_wf", "--format", "ascii"],
    )
    assert static_res.exit_code == 0
    assert "Workflow: dynamic_wf" in static_res.stdout
    assert "test_fixed" in static_res.stdout

    # 2. Dynamic factory with --params
    factory_res = runner.invoke(
        app,
        [
            "graph",
            "render",
            f"{pipeline_file}:make_workflow",
            "--params",
            '{"affected_packages": ["pkg_a", "pkg_b"]}',
            "--format",
            "mermaid",
        ],
    )
    assert factory_res.exit_code == 0
    assert "test_pkg_a" in factory_res.stdout
    assert "test_pkg_b" in factory_res.stdout


def test_cli_graph_render_dry_run_and_output(tmp_path: Path) -> None:
    pipeline_file = tmp_path / "sim_pipeline.py"
    pipeline_file.write_text(
        """
from hexaflow import Workflow

sim_wf = Workflow("sim_demo")

@sim_wf.step("extract")
def extract(ctx): return 42

@sim_wf.step("deploy", depends_on=["extract"], side_effects=True, dry_run="mock_deploy")
def deploy(ctx): return "live_deploy"
""",
        encoding="utf-8",
    )

    out_file = tmp_path / "output.mmd"
    res = runner.invoke(
        app,
        [
            "graph",
            "render",
            f"{pipeline_file}:sim_wf",
            "--dry-run",
            "--output",
            str(out_file),
        ],
    )
    assert res.exit_code == 0
    assert "Simulation complete" in res.stdout
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "extract" in content
    assert "deploy" in content


def test_cli_graph_render_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_file = tmp_path / "summary_ci.yml"
    yaml_file.write_text("name: Summary CI\njobs:\n  gate:\n    steps: []\n", encoding="utf-8")
    summary_md = tmp_path / "STEP_SUMMARY.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_md))

    res = runner.invoke(app, ["graph", "render", str(yaml_file), "--summary"])
    assert res.exit_code == 0
    assert summary_md.exists()
    summary_content = summary_md.read_text(encoding="utf-8")
    assert "Planned Hexaflow Workflow" in summary_content
    assert "```mermaid" in summary_content


def test_cli_graph_info(tmp_path: Path) -> None:
    yaml_file = tmp_path / "info_ci.yml"
    yaml_file.write_text(
        """
name: Info Test
jobs:
  step_a: {}
  step_b: {needs: step_a}
""",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["graph", "info", str(yaml_file)])
    assert res.exit_code == 0
    assert "Total Steps (Nodes)" in res.stdout
    assert "Total Dependencies (Edges)" in res.stdout
