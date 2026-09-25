# Getting Started with Hexaflow

This guide walks through creating, executing, and checkpointing your first workflow with Hexaflow.

---

## 📦 Installation

Install Hexaflow from PyPI:

```bash
# Using uv (recommended)
uv add hexaflow

# Using pip
pip install hexaflow
```

Hexaflow requires Python 3.13+ and has minimal dependencies (Pydantic and Typer/Rich for CLI).

---

## 🛠️ Your First Workflow

Create a Python script `pipeline.py`:

```python
from hexaflow import Workflow, RetryPolicy

# 1. Instantiate the workflow
wf = Workflow(
    name="data_ingestion",
    version="1.0.0",
    description="Fetch, transform, and persist customer records.",
)


# 2. Define sequential steps
@wf.step("fetch_data", retries=RetryPolicy(max_attempts=3, backoff_seconds=1.0))
def fetch_data(ctx) -> dict:
    print("Fetching remote customer data...")
    return {"raw_records": 1500, "source": "s3://raw-bucket/customers.json"}


@wf.step("clean_records", depends_on=["fetch_data"])
def clean_records(ctx) -> dict:
    raw = ctx.inputs["fetch_data"]
    print(f"Cleaning {raw['raw_records']} records from {raw['source']}...")
    return {"cleaned_records": 1482, "dropped": 18}


@wf.step("persist_output", depends_on=["clean_records"])
def persist_output(ctx) -> dict:
    cleaned = ctx.inputs["clean_records"]
    print(f"Persisting {cleaned['cleaned_records']} records to warehouse...")
    return {"status": "SUCCESS", "destination": "analytics.customers"}


if __name__ == "__main__":
    result = wf.run()
    print(f"\nExecution finished with status: {result.status.value}")
    print(f"Workflow Run ID: {result.run_id}")
```

Execute the script:

```bash
python pipeline.py
```

Output:
```text
Fetching remote customer data...
Cleaning 1500 records from s3://raw-bucket/customers.json...
Persisting 1482 records to warehouse...

Execution finished with status: COMPLETED
Workflow Run ID: run_8f31b2e4
```

---

## 💾 Durable State & Checkpointing

By default, workflows execute in memory. To survive process restarts or container failures, configure an **`SqliteStateStore`**:

```python
from pathlib import Path
from hexaflow import Workflow
from hexaflow.adapters.storage.sqlite import SqliteStateStore

# Configure SQLite state persistence
store = SqliteStateStore(db_path=Path("workflows.db"))

wf = Workflow(
    name="resumable_etl",
    state_store=store,
)
```

Each step's completion status, output payload, and timing metadata are atomically committed to SQLite.

---

## 🔄 Resuming from Process Failures

If a step raises an uncaught exception, the workflow transitions to `FAILED`, recording all successful step checkpoints:

```python
# Later, after fixing network or downstream credentials:
resumed_state = wf.resume("run_8f31b2e4")
```

When resumed:
1. Steps that previously **`COMPLETED`** are skipped, preserving their cached outputs.
2. The failed step is retried from its input dependencies.
3. Downstream unexecuted steps run normally to completion.

---

## 🔀 Concurrent Fan-Out (Splits) and Join Barriers

Workflows frequently need to perform parallel operations before synchronizing:

```python
from hexaflow import Workflow, StageExecutionMode

wf = Workflow(name="parallel_fanout")


@wf.stage("setup")
@wf.step("init_batch")
def init_batch(ctx) -> dict:
    return {"batch_id": "b_100", "chunks": 3}


# Concurrent execution across multiple worker coroutines
@wf.stage("processing", execution_mode=StageExecutionMode.CONCURRENT_ALL)
@wf.step("process_chunk_a", depends_on=["init_batch"])
def process_chunk_a(ctx) -> dict:
    return {"chunk": "A", "count": 500}


@wf.stage("processing")
@wf.step("process_chunk_b", depends_on=["init_batch"])
def process_chunk_b(ctx) -> dict:
    return {"chunk": "B", "count": 500}


# Synchronization barrier: only runs once both A and B succeed
@wf.stage("aggregation")
@wf.step("merge_results", depends_on=["process_chunk_a", "process_chunk_b"])
def merge_results(ctx) -> dict:
    a = ctx.inputs["process_chunk_a"]
    b = ctx.inputs["process_chunk_b"]
    return {"total": a["count"] + b["count"]}
```

---

## 🖥️ Command Line Interface

Hexaflow provides a built-in Typer CLI:

```bash
# Execute a workflow from module path
hexaflow run pipeline:wf

# Inspect past execution runs stored in SQLite
hexaflow inspect --db workflows.db

# Resume a failed workflow run
hexaflow resume run_8f31b2e4 --db workflows.db
```

---

## Next Steps

- Explore [Architecture & Design](architecture.md) to understand state machine lifecycles and ports.
- Review the complete [API & DSL Reference](api-reference.md).
- Follow the [Order Fulfillment Tutorial](tutorials/01-order-fulfillment-pipeline.md).
