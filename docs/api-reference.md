# 📚 Hexaflow API & DSL Reference

This document provides reference documentation for the Hexaflow domain models, execution options, and DSL decorators.

---

## 1. `Workflow`

The central container and DAG orchestrator.

```python
from hexaflow import Workflow
from hexaflow.ports.storage import WorkflowStateStorePort
from hexaflow.ports.engine import WorkflowEnginePort

wf = Workflow(
    name="my-workflow",
    version="1.0.0",
    description="Example workflow pipeline",
    state_store=None,  # Defaults to SqliteStateStore()
    engine=None,  # Defaults to AsyncioWorkflowEngine()
)
```

### Methods

#### `wf.step(name, stage=None, depends_on=(), retries=None, compensation=None, ...)`
Decorator registering a function or coroutine as an executable DAG node.

* **`name`** (`str`): Unique identifier for the step within the workflow.
* **`stage`** (`str | None`): Enclosing stage name (defaults to active stage or `"default"`).
* **`depends_on`** (`tuple[str, ...] | list[str]`): Prerequisite step names that must satisfy `trigger_rule` (default `()`) before this step executes.
* **`retries`** / **`retry_policy`** (`RetryPolicy | None`): Optional transient retry policy applied if the step raises an exception.
* **`compensation`** (`Callable[[StepContext], Any] | None`): Optional rollback callable executed in reverse completion order on `wf.abort(run_id)`.
* Also supports `trigger_rule`, `timeout_seconds`, `is_split`, `pool`, `side_effects`, `dry_run`, `estimated_duration_seconds`, `description`, and `metadata`.

#### `wf.map_step(name, over, depends_on=(), concurrency_limit=None, ...)`
Decorator registering a dynamically mapped step that fans out over an iterable returned by upstream step or initial input key `over`.

#### `wf.stage(name, execution_mode=StageExecutionMode.SEQUENTIAL, description="")`
Decorator (also usable as a context manager) grouping steps into a logical stage.

* **`execution_mode`**: `StageExecutionMode.SEQUENTIAL` (default), `StageExecutionMode.CONCURRENT_ALL`, or `StageExecutionMode.CONCURRENT_FAIL_FAST`.
* **`description`** (`str`): Optional summary of stage milestone (default `""`).

#### `wf.use(middleware: StepMiddlewarePort) -> Workflow`
Registers an onion-layered step interceptor (`LoggingStepMiddleware`, `TimingStepMiddleware`, `MockFallbackMiddleware`) on the underlying engine.

#### `wf.run(initial_inputs=None, skip_steps=None, dry_run=False, allow_unsafe=False, fault_injection=None) -> WorkflowExecutionState`
Synchronously executes the workflow from the beginning (async variant: `await wf.run_async(...)`; dry-run convenience: `wf.simulate(...)` / `await wf.simulate_async(...)`).

* **`initial_inputs`** (`dict[str, Any] | None`): Initial inputs supplied to the workflow run.
* **Returns**: A `WorkflowExecutionState` containing the terminal or suspended status, run ID, and step checkpoints.

#### `wf.resume(run_id: str, patch_inputs=None, skip_steps=None) -> WorkflowExecutionState`
Resumes a previously suspended workflow run using checkpoints from the configured state store (async: `await wf.resume_async(...)`).

#### `wf.rewind(run_id: str, to_step: str, patch_inputs=None, skip_steps=None) -> WorkflowExecutionState`
Rewinds execution state to before `to_step` and replays downstream steps (async: `await wf.rewind_async(...)`).

#### `wf.restart(run_id: str) -> WorkflowExecutionState` / `wf.abort(run_id: str) -> WorkflowExecutionState`
Clears checkpoints to restart from scratch (`restart` / `restart_async`), or cancels a run while unwinding declared `compensation` handlers in reverse completion order (`abort` / `abort_async`).

#### `wf.render(format_name="mermaid", options=None)` / `wf.render_image(format_name="mermaid", image_format="svg")`
Renders the materialized `WorkflowGraph` (`to_graph()`) to `mermaid`, `dot`, `ascii`, or `json`, or exports SVG/PNG binaries.

---

## 2. `StepContext`

Passed as the first argument (`ctx`) to all step and compensation functions.

### Attributes

* **`ctx.inputs`** (`dict[str, Any]`): Dictionary mapping upstream step names to their return values.
* **`ctx.output`** (`Any`): Output payload result of the step, available during compensation.
* **`ctx.run_id`** (`str`): Unique execution run identifier.
* **`ctx.stage_name`** (`str`): Name of the stage enclosing this step.
* **`ctx.step_name`** (`str`): The name of the executing step.
* **`ctx.attempt_number`** (`int`): Current retry attempt number (1-indexed).
* **`ctx.checkpoint_dir`** (`str | None`): Optional path to the scratch directory for this step.
* **`ctx.metadata`** (`dict[str, Any]`): Ambient execution and middleware metadata dictionary.

---

## 3. `RetryPolicy`

Configures automatic retry behavior for transient step failures (`BackoffType`: `FIXED`, `LINEAR`, `EXPONENTIAL`).

```python
from hexaflow import BackoffType, RetryPolicy

policy = RetryPolicy(
    max_attempts=3,
    backoff_type=BackoffType.EXPONENTIAL,
    initial_delay_seconds=1.0,
    max_delay_seconds=60.0,
    backoff_factor=2.0,
    jitter=True,
    retry_on=(TimeoutError, ConnectionError),
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_attempts` | `int` | `1` | Maximum number of execution attempts. |
| `backoff_type` | `BackoffType` | `BackoffType.EXPONENTIAL` | Delay progression algorithm (`FIXED`, `LINEAR`, `EXPONENTIAL`). |
| `initial_delay_seconds` | `float` | `0.5` | Initial delay between retry attempts. |
| `max_delay_seconds` | `float` | `60.0` | Upper bound cap on retry delay in seconds. |
| `backoff_factor` | `float` | `2.0` | Multiplier for exponential backoff. |
| `jitter` | `bool` | `True` | Adds randomized jitter to prevent thundering herds. |
| `retry_on` | `tuple[type[BaseException], ...]` | `(Exception,)` | Exception classes that trigger retries. |

---

## 4. `StageExecutionMode`

Enum controlling step scheduling within a stage:

* **`StageExecutionMode.SEQUENTIAL`**: Steps run in strict dependency order, one after another.
* **`StageExecutionMode.CONCURRENT_ALL`**: Independent steps within the stage run concurrently as asyncio tasks, collecting all outcomes before halting.
* **`StageExecutionMode.CONCURRENT_FAIL_FAST`**: Independent steps run concurrently and cancel sibling tasks immediately on the first unhandled failure.

---

## 5. Storage Adapters

### `InMemoryStateStore`
Transient in-memory store suitable for testing and short-lived jobs.

```python
from hexaflow.adapters.storage.in_memory import InMemoryStateStore

store = InMemoryStateStore()
```

### `SqliteStateStore`
Embedded ACID-compliant state storage with WAL journaling.

```python
from hexaflow.adapters.storage.sqlite import SqliteStateStore

store = SqliteStateStore(
    db_path="workflows.db",
    artifacts_dir=".hexaflow/artifacts",
)
```
