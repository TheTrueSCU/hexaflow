# Memento Checkpoint Resumption & Crash Recovery

> **Architectural Pattern**: Durable, zero-overhead checkpointing into embedded SQLite stores enabling seamless failure recovery and resumption.

---

## 🏛️ Architecture & Resumption Flow

In long-running data pipelines and multi-stage orchestration, failures (disk full, transient network drops, spot instance preemption) must not force re-execution of expensive upstream stages.

Hexaflow implements the **Memento Pattern**:
1. After every successful step execution, step inputs, outputs, timestamps, and execution durations are committed to the configured state store (`SqliteStateStore`).
2. If an exception halts execution, the run is marked `SUSPENDED`, preserving completed checkpoints.
3. Operators inspect the failure reason and traceback using `hexaflow inspect <run_id> <step>`.
4. Resuming with `hexaflow resume <run_id> <target>` loads all prior checkpoints from SQLite, bypassing completed steps and continuing directly from the failed stage.

```mermaid
flowchart TD
    subgraph Initial Run ["Initial Execution Run"]
        S1["Stage 1: download_dataset"] -->|Success| C1[("SQLite Checkpoint<br/>COMPLETED")]
        C1 --> S2["Stage 2: normalize_features"]
        S2 -->|Disk Full Failure| C2[("SQLite Checkpoint<br/>FAILED")]
        C2 --> SUSP["Status: SUSPENDED"]
    end

    subgraph Resumption ["Resumption Run (hf resume)"]
        SUSP -.-> RES["Operator heals disk partition<br/><code>hf resume &lt;run_id&gt;</code>"]
        RES --> LOAD["Load Checkpoints from SQLite"]
        LOAD --> SKIP["Stage 1 (download_dataset)<br/><b>SKIPPED (Already Completed)</b>"]
        SKIP --> S2_RETRY["Stage 2 (normalize_features)<br/><b>EXECUTED</b>"]
        S2_RETRY --> S3["Stage 3: train_classifier<br/><b>EXECUTED</b>"]
        S3 --> DONE["Status: COMPLETED"]
    end
```

---

## 💻 CLI Commands

```bash
# 1. Run workflow and observe suspension on worker crash
hf run pipeline.py:ml_pipeline --db .hexaflow/state.db

# 2. Check current status across stages
hf status <run_id> --db .hexaflow/state.db

# 3. Inspect failure exception and traceback
hf inspect <run_id> normalize_features --db .hexaflow/state.db

# 4. Resume execution after fixing worker environment
hf resume <run_id> pipeline.py:ml_pipeline --db .hexaflow/state.db
```

---

## 🎬 Verified Interactive Demo

| Asset | Link |
|---|---|
| **Recording (.webm)** | [`hexaflow-checkpoint-resume.webm`](../assets/demos/hexaflow-checkpoint-resume.webm) |
| **WebVTT Captions** | [`hexaflow-checkpoint-resume.vtt`](../assets/demos/hexaflow-checkpoint-resume.vtt) |
| **Test Suite** | [`tests/e2e/test_cli_resumption.py`](../../tests/e2e/test_cli_resumption.py) |
