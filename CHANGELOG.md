# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-14

### Added
- `TriggerRule` enum supporting `ALL_SUCCESS`, `ALL_FAILED`, `ALL_DONE`, `ONE_SUCCESS`, `ONE_FAILED`, `NONE_FAILED`, and `ALL_SUCCESS_OR_SKIPPED`.
- Pure function `evaluate_trigger_rule(rule, parent_statuses)` in domain models.
- Support for `trigger_rule` on `StepDefinition` and fluent builder `Workflow.step(..., trigger_rule=...)`.
- Execution-time step skipping via `skip_steps: set[str] | list[str] | None` on `WorkflowEnginePort` and `AsyncioWorkflowEngine` (`run`, `run_async`, `resume`, `resume_async`).
- Cascading skip propagation: skipped upstream dependencies cascade downstream according to each child step's `trigger_rule`.
- `WorkflowCliBinder` in `hexaflow.cli.binder` for introspecting workflow DAGs and dynamically generating CLI option signatures (`--skip-<step>`) for Typer commands.
- `Workflow.create_cli_binder()` DSL convenience helper.
- Comprehensive unit test coverage across trigger rules, skip cascades, and CLI binder parameter translation.

## [0.1.1] - 2026-09-13

### Added
- Quality gate integration with Hexaqual (`hexaqual[all]>=0.2.0`).
- Test symmetry verification and `__all__` integrity enforcement.
- Automated release workflow with provenance attestations and PyPI publication.

## [0.1.0] - 2026-09-13

### Added
- Lightweight, embeddable Python workflow engine with stages, steps, splits (fan-out), and joins (barriers).
- Pure domain models for workflows, stages, steps, retry policies, and execution state.
- In-memory and SQLite-backed state stores (`InMemoryStateStore`, `SqliteStateStore`) with checkpointed resumption.
- Asynchronous local execution engine (`AsyncioWorkflowEngine`).
- Fluent workflow definition DSL (`Workflow`).
- Command-line interface (`hf`) with commands for inspection, execution, resumption, and status dashboards.
- End-to-end resumable pipeline integration tests and Hypothesis property-based tests.
