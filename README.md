# 🌊 `hexaflow`

> Lightweight, embeddable Python workflow engine with stages, steps, splits, joins, and checkpointed resumption.

[![PyPI: hexaflow](https://img.shields.io/pypi/v/hexaflow.svg)](https://pypi.org/project/hexaflow/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## 🎯 Vision & Architectural Intent

**`hexaflow`** is a zero-daemon, localhost-first workflow engine for Python developers who need resilient, multi-stage, multi-step execution graphs without the operational overhead of running heavy external schedulers (Airflow, Temporal, Prefect).

### Key Features
* **Hierarchical Execution**: Workflows structured cleanly into **Stages** and **Steps**.
* **DAG Splits & Joins**: Concurrently fan-out (split) and synchronize at barrier dependencies (join).
* **Fault Partitioning**: Transient retries with exponential backoff vs. permanent failure suspension (`SUSPENDED`).
* **Checkpointed Resumption**: Automatically saves step inputs and outputs into an embedded SQLite store. Resume failed workflows from the point of failure without re-running completed steps.
* **Dual DSL**: Decorator-based syntax for Pythonic simplicity (`@wf.stage`, `@wf.step`) alongside declarative classes for programmatic pipelines.

---

## 📦 Installation

```bash
pip install hexaflow
```

Or using `uv`:

```bash
uv add hexaflow
```

---

## ⚡ Quickstart

```python
from hexaflow import RetryPolicy, StageExecutionMode, Workflow

wf = Workflow(name="order_pipeline", version="1.0.0")


# Stage 1: Validation
@wf.stage("validation")
@wf.step("validate_cart", retries=RetryPolicy(max_attempts=3))
def validate_cart(ctx) -> dict:
    return {"order_id": "ord_101", "total_usd": 150.00}


# Stage 2: Parallel Processing (Split / Fan-Out)
@wf.stage("processing", execution_mode=StageExecutionMode.CONCURRENT_ALL)
@wf.step("authorize_payment", depends_on=["validate_cart"])
def authorize_payment(ctx) -> dict:
    cart = ctx.inputs["validate_cart"]
    return {"status": "PAID", "amount": cart["total_usd"]}


@wf.stage("processing")
@wf.step("reserve_inventory", depends_on=["validate_cart"])
def reserve_inventory(ctx) -> dict:
    return {"warehouse": "US-EAST-1", "reserved": True}


# Stage 3: Fulfillment (Join Barrier)
@wf.stage("fulfillment")
@wf.step("create_shipping_label", depends_on=["authorize_payment", "reserve_inventory"])
def create_shipping_label(ctx) -> dict:
    return {"tracking_id": "TRK-9812739"}


if __name__ == "__main__":
    state = wf.run()
    print(f"Workflow {state.run_id} completed with status: {state.status.value}")
```

---

## 🖥️ Command Line Interface (CLI)

`hexaflow` includes a fast, zero-daemon CLI (`hexaflow` or `hf`):

```bash
# Run a workflow directly
hf run examples/order_pipeline.py:wf

# Check run status & step timing
hf status <run_id>

# Inspect step inputs, outputs, or error tracebacks
hf inspect <run_id> create_shipping_label

# Resume a suspended workflow from its latest checkpoint
hf resume <run_id>

# Clear checkpoints and restart a workflow from scratch
hf restart <run_id>

# Abort a workflow and execute compensating rollback actions
hf abort <run_id>
```

---

## 🏛️ Ecosystem Alignment

`hexaflow` is part of the **Hexa** architectural ecosystem:
- **`hexaflow`** *(this repository)*: Lightweight, zero-daemon, localhost-first DAG workflow engine.
- **`hexastack`**: Monorepo framework providing CQRS, Event Sourcing, FastAPI, and out-of-the-box DevTools.
- **`hexaqueue`**: Flagship distributed batch & HPC cluster scheduler.

---

## 📄 License

Apache 2.0. See [LICENSE](LICENSE) for details.
