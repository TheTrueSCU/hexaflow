# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.1] - 2026-10-22

### Security & Dependencies
- Upgraded `certifi` to `>=2026.10.19` addressing the eMudhra CA advisory.
- Upgraded `virtualenv` to `>=21.14.12` resolving `CVE-2026-113725` and `CVE-2026-113722`.
- Upgraded dev tooling to `hexaqual[all]>=0.9.6`.

### Refactored & Governance
- Moved `importlib` import to module top-level in `src/hexaflow/adapters/engines/local_async.py` enforcing strict domain purity and lazy-import compliance.
- Codified monorepo Architectural Decision Records (`docs/decisions.md`).

### Verification & Testing
- Implemented comprehensive self-verifying end-to-end user journeys:
  - `HF-02`: Resilient Saga Compensation and Rollback (`tests/e2e/test_saga_compensation.py`).
  - `HF-03`: Onion-layered Step Middleware Pipeline & Telemetry (`tests/e2e/test_step_middleware.py`).
  - `HF-04`: Memento Checkpoint Resumption & Crash Recovery (`tests/e2e/test_cli_resumption.py`).
- Integrated dynamic `hexaflow graph render` diagram visualization into CI, documentation, and release workflows.

## [0.5.0] - 2026-10-11

### Added
- **Pluggable Graph Subsystem & Multi-Format Renderers**:
  - `WorkflowGraph` pure domain model with cycle detection, topological sorting, reachability analysis (`ancestors`, `descendants`, `blast_radius`), and critical path latency bottleneck calculation.
  - Multi-format diagram adapters conforming to `GraphRendererPort`:
    - `MermaidGraphRenderer`: Stage-clustered flowcharts with execution pool styling, rule badges, and mapped indicators.
    - `DotGraphRenderer`: Graphviz DOT format with customizable styles and node highlighting.
    - `AsciiGraphRenderer`: Unicode box-drawing visual DAG representation for terminal output.
    - `JsonGraphRenderer`: JSON-serialized structural representation for programmatic integration.
    - `RendererRegistry`: Extensible renderer catalog supporting third-party format plugins.
  - Optional visual artifact export (`render_image(..., image_format="svg" | "png")`) with CLI fallbacks (`mmdc`, `dot`).
- **Dry-Run Simulation Mode & Chaos Fault Injection**:
  - `dry_run=True` simulation execution across scalar and mapped fan-out steps without mutating databases or external APIs.
  - Fail-closed safety guards: steps declaring `side_effects=True` require explicit mock fallbacks (`dry_run=...`) or `allow_unsafe=True`, preventing unintended production mutations.
  - Chaos fault injection: `fault_injection={step_name: Exception}` parameter allows simulating component failures and testing resilient compensating workflows.
- **Workflow Memento Pattern & Time-Travel Rewind**:
  - Immutable state snapshots (`WorkflowMemento`) capturing complete execution state, checkpoints, and inputs.
  - Time-travel execution rewind (`rewind_to(step_name)`): resets execution to before any historical step, clearing downstream checkpoints for selective re-execution.
- **Onion-Layered Step Middleware Pipeline**:
  - Abstract `StepMiddlewarePort` protocol and `InOutStepMiddleware` base class for pre/post step interception across sync and async callables.
  - Built-in middleware implementations:
    - `LoggingStepMiddleware`: Structured logging of step start, completion, and failure events.
    - `TimingStepMiddleware`: High-precision latency measurement in milliseconds stored on step context metadata.
    - `MockFallbackMiddleware`: Automatic synthetic fallback generation for dry-run simulation mode.
  - Fluent registration via `engine.use(middleware)` and `Workflow(..., middleware=[...])`.
- **Enhanced CLI Subcommands (`hf graph render` & `hf graph info`)**:
  - `hf graph render <target> --format <mermaid|dot|ascii|json>`: Visualizes workflow DAGs directly from Python files, functions, or GitHub Actions YAML files.
  - Direct GitHub Actions YAML pipeline parsing (`GitHubActionsYamlLoader`): parses job dependencies (`needs`) into a `WorkflowGraph` without custom scripts.
  - `--dry-run` simulation flag on `hf graph render` to expand and visualize dynamic mapped steps before execution.
  - `--summary`: writes rendered Mermaid diagrams directly to GitHub Actions `$GITHUB_STEP_SUMMARY`.
  - `hf graph info <target>`: Displays topological metadata, estimated latency, bottleneck step, and critical path nodes.

### Changed
- Refactored `AsyncioWorkflowEngine` execution pipeline into an onion-layered middleware dispatch chain.
- Constructor worker limits (`max_process_workers`, `max_thread_workers`) on `Workflow` retain original positional parameter ordering.

## [0.4.0] - 2026-09-25


### Added
- **Multi-Core Worker Pools & Execution Pools (`ExecutionPool`)**:
  - `ExecutionPool` enum supporting `ASYNC` (asyncio event loop coroutine), `THREAD` (`ThreadPoolExecutor`), and `PROCESS` (`ProcessPoolExecutor`).
  - `pool` parameter on `StepDefinition`, `@wf.step(..., pool=...)`, and `@wf.map_step(..., pool=...)` for true multi-core CPU scaling.
  - Safe cross-process parameter binding and trampoline execution (`mp_context="spawn"`) for CPU-bound step and mapped sub-step execution.
  - Granular process and thread worker limit tuning via `max_process_workers` and `max_thread_workers` on `AsyncioWorkflowEngine` and `Workflow`.
  - Resource lifecycle management with `close()`, `aclose()`, sync and async context managers (`with Workflow(...)`, `async with Workflow(...)`).
  - `LocalAsyncWorkflowEngine` alias for `AsyncioWorkflowEngine`.

## [0.3.0] - 2026-09-16

### Added
- **Dynamic Step Mapping (`@wf.map_step`)**:
  - Runtime fan-out decorator allowing a step to dynamically map over a collection produced by an upstream step or context input.
  - Automatic dependency inference linking mapped steps to their upstream targets.
  - Granular concurrency throttling via `concurrency_limit` parameter.
  - Flexible callable signature binding (supporting `(item)`, `(item, ctx)`, `(ctx, item)`, and context keyword arguments).
  - Sub-step checkpoint isolation (`<step>[<i>]`) supporting individual retries, timeouts, and seamless resumption skipping previously completed items.
- **DAG Exporters**:
  - `WorkflowDefinition.to_mermaid(direction="TD")` and `Workflow.to_mermaid()` for rendering valid Mermaid flowcharts clustered by stage with rule and mapped node annotations.
  - `WorkflowDefinition.to_ascii()` and `Workflow.to_ascii()` for rendering clean Unicode hierarchical trees for terminal and dashboard output.
- **Governance**:
  - Upgraded dev dependencies to `hexaqual[all]>=0.3.0` and unified pre-commit hook configuration.

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
