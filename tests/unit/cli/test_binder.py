"""Unit tests for WorkflowCliBinder.

Notes/Architectural Intent:
    Validates dynamic --skip-<step> option generation, argument parsing,
    alias normalization, and Typer decorator integration.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from hexaflow.cli.binder import WorkflowCliBinder
from hexaflow.dsl.builder import Workflow

runner = CliRunner()


def _build_test_workflow() -> Workflow:
    """Construct a sample workflow for testing CLI binding."""
    wf = Workflow("test_wf")

    @wf.step("lint")
    def _lint() -> str:
        return "lint_done"

    @wf.step("type_check", depends_on=["lint"])
    def _type_check() -> str:
        return "type_check_done"

    @wf.step("unit_tests", depends_on=["type_check"])
    def _unit_tests() -> str:
        return "unit_tests_done"

    return wf


def test_binder_specs_and_flags() -> None:
    """Validate that binder introspects steps and formats CLI flags properly."""
    wf = _build_test_workflow()
    binder = WorkflowCliBinder(
        wf,
        aliases={
            "type_check": ["--skip-ty"],
            "unit_tests": ["--skip-tests"],
        },
    )

    names = binder.step_names
    assert names == ["lint", "type_check", "unit_tests"]

    spec_lint = binder.get_spec("lint")
    assert spec_lint is not None
    assert spec_lint.param_name == "skip_lint"
    assert spec_lint.cli_flags == ["--skip-lint"]

    spec_ty = binder.get_spec("type_check")
    assert spec_ty is not None
    assert spec_ty.param_name == "skip_type_check"
    assert "--skip-type-check" in spec_ty.cli_flags
    assert "--skip-ty" in spec_ty.cli_flags

    spec_tests = binder.get_spec("unit_tests")
    assert spec_tests is not None
    assert spec_tests.param_name == "skip_unit_tests"
    assert "--skip-unit-tests" in spec_tests.cli_flags
    assert "--skip-tests" in spec_tests.cli_flags


def test_binder_extract_skips() -> None:
    """Validate extract_skips with various kwargs."""
    wf = _build_test_workflow()
    binder = WorkflowCliBinder(
        wf,
        aliases={"type_check": ["--skip-ty"]},
    )

    # Empty kwargs
    skips = binder.extract_skips()
    assert skips == set()

    # Direct param name
    skips = binder.extract_skips(skip_lint=True)
    assert skips == {"lint"}

    # Alias param name
    skips = binder.extract_skips(skip_ty=True)
    assert skips == {"type_check"}

    # Multiple skips
    skips = binder.extract_skips(skip_lint=True, skip_unit_tests=True)
    assert skips == {"lint", "unit_tests"}


def test_binder_parse_argv() -> None:
    """Validate parse_argv extracts flags directly from argument lists."""
    wf = _build_test_workflow()
    binder = WorkflowCliBinder(
        wf,
        aliases={"type_check": ["--skip-ty"]},
    )

    argv = ["--fix", "--skip-lint", "--skip-ty", "--verbose"]
    skips = binder.parse_argv(argv)
    assert skips == {"lint", "type_check"}


def test_binder_apply_typer_decorator() -> None:
    """Validate @binder.apply injects dynamic options into a Typer command."""
    wf = _build_test_workflow()
    binder = wf.create_cli_binder(
        aliases={"type_check": ["--skip-ty"], "unit_tests": ["--skip-tests"]}
    )

    app = typer.Typer()
    recorded_skips: set[str] = set()

    @app.command()
    @binder.apply
    def run_command(
        target: str = typer.Option("all", "--target"),
        skip_steps: set[str] | None = None,
    ) -> None:
        nonlocal recorded_skips
        recorded_skips = skip_steps or set()
        typer.echo(f"Running target {target} with skips: {sorted(recorded_skips)}")

    # 1. Run without skips
    res = runner.invoke(app, ["--target", "core"])
    assert res.exit_code == 0
    assert recorded_skips == set()
    assert "Running target core with skips: []" in res.stdout

    # 2. Run with standard generated flag
    res = runner.invoke(app, ["--skip-lint"])
    assert res.exit_code == 0
    assert recorded_skips == {"lint"}
    assert "skips: ['lint']" in res.stdout

    # 3. Run with alias flags
    res = runner.invoke(app, ["--skip-ty", "--skip-tests"])
    assert res.exit_code == 0
    assert recorded_skips == {"type_check", "unit_tests"}
    assert "skips: ['type_check', 'unit_tests']" in res.stdout

    # 4. Help text shows injected options
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "--skip-lint" in res.stdout
    assert "--skip-type-check" in res.stdout
    assert "--skip-ty" in res.stdout
    assert "--skip-unit-tests" in res.stdout
    assert "--skip-tests" in res.stdout
