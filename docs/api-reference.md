# 📚 Hexaflow API & DSL Reference

This document provides reference documentation for the Hexaflow domain models, execution options, and DSL decorators.

---

## 1. `Workflow`

The central container and DAG orchestrator.

```python
from hexaflow import Workflow

wf = Workflow(
    name: str,
    version: str = "1.0.0",
    description: str | None = None,
    state_store: StateStorePort | None = None,
    engine: WorkflowEnginePort | None = None,
)
```

### Methods

#### `wf.step(name, depends_on=None, retries=None)`
Decorator registering a function as an executable DAG node.

* **`name`** (`str`): Unique identifier for the step within the workflow.
* **`depends_on`** (`list[str] | None`): List of upstream step names that must complete successfully before this step executes.
* **`retries`** (`RetryPolicy | None`): Optional retry policy applied if the step raises an exception.

#### `wf.stage(name, execution_mode=StageExecutionMode.SEQUENTIAL, description=None)`
Decorator grouping steps into a logical stage.

* **`execution_mode`**: `StageExecutionMode.SEQUENTIAL` (default) or `StageExecutionMode.CONCURRENT_ALL`.

#### `wf.compensate(target_step_name)`
Decorator registering a rollback compensation handler for a previously executed step.

#### `wf.run(initial_inputs=None) -> WorkflowStateSnapshot`
Executes the workflow from the beginning.

* **`initial_inputs`** (`dict[str, Any] | None`): Global parameters passed to root steps via `ctx.inputs["__initial__"]`.
* **Returns**: A `WorkflowStateSnapshot` containing the terminal execution status, run ID, and step checkpoints.

#### `wf.resume(run_id: str) -> WorkflowStateSnapshot`
Resumes execution of a previously halted or failed workflow run using checkpoints from the configured state store.

---

## 2. `StepContext`

Passed as the first argument (`ctx`) to all step and compensation functions.

### Attributes

* **`ctx.inputs`** (`dict[str, Any]`): Dictionary mapping upstream step names to their return values.
* **`ctx.run_id`** (`str`): Unique execution run identifier.
* **`ctx.step_name`** (`str`): The name of the executing step.
* **`ctx.metadata`** (`dict[str, Any]`): Arbitrary execution metadata.

---

## 3. `RetryPolicy`

Configures automatic retry behavior for transient step failures.

```python
from hexaflow import RetryPolicy

policy = RetryPolicy(
    max_attempts=3,
    backoff_seconds=1.0,
    exponential_base=2.0,
    jitter=True,
    retryable_exceptions=(TimeoutError, ConnectionError),
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_attempts` | `int` | `3` | Maximum number of execution attempts. |
| `backoff_seconds` | `float` | `1.0` | Initial delay between retry attempts. |
| `exponential_base` | `float` | `2.0` | Multiplier for exponential backoff. |
| `jitter` | `bool` | `True` | Adds randomized jitter to prevent thundering herds. |
| `retryable_exceptions` | `tuple` | `(Exception,)` | Exceptions that trigger retries. |

---

## 4. `StageExecutionMode`

Enum controlling step scheduling within a stage:

* **`StageExecutionMode.SEQUENTIAL`**: Steps run in strict dependency order, one after another.
* **`StageExecutionMode.CONCURRENT_ALL`**: Independent steps within the stage run concurrently as asyncio tasks.

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
from pathlib import Path
from hexaflow.adapters.storage.sqlite import SqliteStateStore

store = SqliteStateStore(
    db_path=Path("workflows.db"),
    enable_wal=True,
)
```
