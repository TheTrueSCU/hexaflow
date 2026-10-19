# Architectural Decision Records (ADRs) & Infrastructure Invariants

> **Scope**: Authoritative historical log of foundational architectural decisions, structural constraints, and quality invariants governing **Hexaflow** and its embeddable DAG workflow engine.
>
> This document captures **why** specific paradigms were chosen, **what was rejected**, and the **machine gates** that physically prevent architectural drift. It serves as persistent memory across release cycles and AI pair-programming sessions.

---

## Decision Index

| ADR ID | Title | Date / Milestone | Status | Key Invariant |
|---|---|---|---|---|
| [**ADR-001**](#adr-001-strict-hexagonal-layer-boundaries--pure-domain-workflows) | Strict Hexagonal Layer Boundaries & Pure Domain Workflows | 2026-08-20 (`v0.1.0`) | **Accepted** | `domain/` has 0 external dependencies and 0 I/O; DAG definitions are pure data structures. |
| [**ADR-002**](#adr-002-reversible-step-execution--saga-compensation-pairings) | Reversible Step Execution & Saga Compensation Pairings | 2026-09-01 (`v0.2.0`) | **Accepted** | Steps pair forward execution with backward `on_failure` compensations for reliable saga rollbacks. |
| [**ADR-003**](#adr-003-declarative-workflow-model--lossless-schema-serialization) | Declarative Workflow Model & Lossless Schema Serialization | 2026-09-10 (`v0.3.0`) | **Accepted** | Pydantic v2 domain schemas serialize losslessly to JSON/YAML for cross-process orchestration. |
| [**ADR-004**](#adr-004-anti-fake-pass-exercise-gates--boundary-mutation-auditing) | Anti-Fake-Pass Exercise Gates & Boundary Mutation Auditing | 2026-10-18 (`v0.4.0`) | **Accepted** | Zero test collection is a fatal failure; mutation testing audits workflow failure handler efficacy. |
| [**ADR-005**](#adr-005-topological-dag-pipeline-visualization--critical-path-analysis) | Topological DAG Pipeline Visualization & Critical Path Analysis | 2026-10-14 (`v0.4.0`) | **Accepted** | GitHub Actions and workflow DAGs render directly into Mermaid graphs and compute critical path bottlenecks. |

---

### ADR-001: Strict Hexagonal Layer Boundaries & Pure Domain Workflows

- **Date & Milestone**: 2026-08-20 (`v0.1.0`)
- **Status**: Accepted
- **Context & Problem Statement**:
  Workflow engines frequently couple DAG scheduling, step definition, and business rules directly with external execution runners, multiprocessing pools, or storage backends. This entanglement makes it impossible to run workflows deterministically in memory during tests without spinning up databases or subprocesses.
- **Decision**:
  Enforce strict 4-tier Hexagonal layer boundaries:
  - `domain/`: Pure workflow DAG models, step definitions, transition rules, and state machines with **0** external framework dependencies and **0** disk/network I/O.
  - `ports/`: Pure abstract base interfaces (`abc.ABC`) defining storage adapters, execution engines, and event publishers.
  - `adapters/`: Concrete technological implementations (asyncio runners, disk storage, memory state backends, Rich telemetry).
  - `dsl/`: Declarative builder APIs that construct domain DAGs without leaking implementation details.
  - `cli/`: Typer command dispatchers that render, validate, and execute workflows from terminal entrypoints.
- **The Invariant ("The Why")**:
  A workflow DAG is a pure mathematical graph. Step sequencing, condition branching, and state transitions must be completely testable in microsecond in-memory tests.
- **Alternatives Considered & Rejected**:
  - *Coupling steps to Celery / Airflow / Temporal tasks*: Rejected because an embeddable engine must function within single-process applications, CLI scripts, and hermetic CI pipelines without requiring external infrastructure daemons.
- **Enforcement Mechanism**:
  - Enforced by `[tool.importlinter]` via `uv run hexaqual deps linter`.

---

### ADR-002: Reversible Step Execution & Saga Compensation Pairings

- **Date & Milestone**: 2026-09-01 (`v0.2.0`)
- **Status**: Accepted
- **Context & Problem Statement**:
  Multi-step operations (such as payment processing, order dispatch, or multi-repo deployments) can fail at any stage. In distributed environments without two-phase commit (2PC), partial execution leaves systems in corrupt, half-finished states.
- **Decision**:
  Implement the distributed Saga pattern directly within step definitions. Each forward step execution can optionally declare an `on_failure` compensation hook. When a DAG execution halts due to an unhandled step error, the engine traverses the reverse topological execution history and executes compensations in reverse order.
- **The Invariant ("The Why")**:
  Every side-effecting action modeled in a Hexaflow workflow must have an explicit compensation or declared point-of-no-return idempotency rule.
- **Alternatives Considered & Rejected**:
  - *Distributed transactions (XA/2PC)*: Rejected due to prohibitive latency, database locking, and failure in cross-service REST/gRPC environments.
- **Enforcement Mechanism**:
  - Compensation reversal verification tests in `tests/unit/test_saga.py`.

---

### ADR-003: Declarative Workflow Model & Lossless Schema Serialization

- **Date & Milestone**: 2026-09-10 (`v0.3.0`)
- **Status**: Accepted
- **Context & Problem Statement**:
  Configuring workflows solely in imperative Python code prevents visualization tools, web DevTools, and AI coding assistants from inspecting or validating workflow structures prior to execution.
- **Decision**:
  Standardize the workflow DAG representation on declarative Pydantic v2 models that support lossless round-trip serialization to JSON and YAML. Workflow graphs can be authored either via Python fluent builder DSL or imported directly from YAML/JSON workflow specifications.
- **The Invariant ("The Why")**:
  A workflow's graph structure must be introspectable, statically verifiable, and visualizable without executing any user step code.
- **Alternatives Considered & Rejected**:
  - *Opaque Python generators / coroutines*: Rejected because inspecting execution graphs from arbitrary generator bytecodes is fragile and cannot be shared across polyglot tooling.
- **Enforcement Mechanism**:
  - Property-based schema serialization tests using Hypothesis.

---

### ADR-004: Anti-Fake-Pass Exercise Gates & Boundary Mutation Auditing

- **Date & Milestone**: 2026-10-18 (`v0.4.0`)
- **Status**: Accepted
- **Context & Problem Statement**:
  Workflow engines require high test reliability because subtle bugs in edge-case branching (e.g. split/join concurrency or error compensations) cause silent pipeline deadlocks. Handwritten mocks often result in fake-green tests where assertion coverage is near-zero.
- **Decision**:
  Enforce two strict exercise gates:
  1. `hexaqual test run` treats pytest exit code 5 (0 tests collected) as an immediate fatal error unless `--allow-empty` is explicitly passed.
  2. `hexaqual mutate run` executes mutation testing to verify that step condition assertions and compensation triggers are actively tested.
- **The Invariant ("The Why")**:
  Workflow branching logic and compensation sequences must kill boundary mutants. High code coverage without mutation resistance is invalid.
- **Alternatives Considered & Rejected**:
  - *Relying solely on line coverage*: Rejected because line coverage does not prove that assertions test failure states.
- **Enforcement Mechanism**:
  - Pre-commit quality gate and mutation test suites.

---

### ADR-005: Topological DAG Pipeline Visualization & Critical Path Analysis

- **Date & Milestone**: 2026-10-14 (`v0.4.0`)
- **Status**: Accepted
- **Context & Problem Statement**:
  Complex CI pipelines (such as GitHub Actions workflows) are difficult to comprehend from raw YAML files. Developers and autonomous agents struggle to understand stage dependencies, parallelism, and critical path bottlenecks.
- **Decision**:
  Provide native CLI and library capabilities (`hexaflow graph render <workflow.yml> --summary --critical-path`) that parse GitHub Actions workflows and Hexaflow DAGs, construct topological graphs, compute critical paths, and render Mermaid diagrams and summary tables in CI job summaries.
- **The Invariant ("The Why")**:
  Every CI pipeline in the Hexa- ecosystem must visually document its dependency graph and critical path in every CI run.
- **Alternatives Considered & Rejected**:
  - *External proprietary CI visualization services*: Rejected in favor of an open-source, local-first embeddable CLI command.
- **Enforcement Mechanism**:
  - Step `Visualize Pipeline DAG & Critical Path via Hexaflow` in all ecosystem `.github/workflows/ci.yml` files.
