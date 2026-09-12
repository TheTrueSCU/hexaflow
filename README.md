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

---

## 📄 License

Apache 2.0. See [LICENSE](LICENSE) for details.
