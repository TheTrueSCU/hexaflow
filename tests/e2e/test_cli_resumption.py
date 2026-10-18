"""User Journey HF-04: Memento Checkpoint Resumption & Crash Recovery demo test.

Notes/Architectural Intent:
    Demonstrates durable Memento pattern checkpointing in Hexaflow.
    When a long-running, multi-stage workflow experiences an environmental or worker crash,
    prior completed steps remain safely committed in the SQLite state store.
    Upon recovery, operators run `hexaflow resume` to advance only uncompleted/failed
    steps without redundantly re-executing expensive upstream operations.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from hexastack_cli.testing.narrator import CliNarrator

from hexaflow.cli.main import app


@pytest.mark.demo
def test_cli_resumption_journey(tmp_path: Path) -> None:
    """Execute and record the Memento Checkpoint Resumption & Crash Recovery demo."""
    db_file = tmp_path / "memento_state.db"
    crash_flag = tmp_path / "crash_active.flag"
    pipeline_script = tmp_path / "etl_pipeline.py"
    execution_log = tmp_path / "execution_steps.log"

    crash_flag.write_text("CRASH_ACTIVE", encoding="utf-8")

    pipeline_code = f"""from __future__ import annotations
from pathlib import Path
from hexaflow.dsl.builder import Workflow
from hexaflow.domain.state import StepContext

CRASH_FLAG = Path({str(crash_flag)!r})
LOG_FILE = Path({str(execution_log)!r})

def log_step(name: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(name + "\\n")

def download_dataset(ctx: StepContext) -> dict[str, int]:
    log_step("download_dataset")
    return {{"records_ingested": 100000, "dataset_size_mb": 240}}

def normalize_features(ctx: StepContext) -> dict[str, str]:
    log_step("normalize_features")
    if CRASH_FLAG.exists():
        raise IOError("WorkerDiskExhaustion: /var/scratch partition reached 100% capacity")
    return {{"features_extracted": "128_dims", "scaling": "standard_normal"}}

def train_classifier(ctx: StepContext) -> dict[str, float]:
    log_step("train_classifier")
    return {{"epoch_loss": 0.0124, "val_accuracy": 0.988}}

ml_pipeline = Workflow("resilient-ml-pipeline")

ml_pipeline.stage("data_ingestion")
@ml_pipeline.step("download_dataset")
def step_download(ctx: StepContext) -> dict[str, int]:
    return download_dataset(ctx)

ml_pipeline.stage("feature_engineering")
@ml_pipeline.step("normalize_features", depends_on=("download_dataset",))
def step_normalize(ctx: StepContext) -> dict[str, str]:
    return normalize_features(ctx)

ml_pipeline.stage("model_training")
@ml_pipeline.step("train_classifier", depends_on=("normalize_features",))
def step_train(ctx: StepContext) -> dict[str, float]:
    return train_classifier(ctx)
"""
    pipeline_script.write_text(pipeline_code, encoding="utf-8")
    pipeline_target = f"{pipeline_script}:ml_pipeline"

    repo_root = Path(__file__).resolve().parent.parent.parent
    output_dir = repo_root / "docs" / "assets" / "demos"

    narrator = CliNarrator(
        app,
        output_name="hexaflow-checkpoint-resume",
        output_dir=output_dir,
        width=120,
    )

    # Step 1: Initial run triggers simulated worker crash
    narrator.step("1. Starting Multi-Stage ML Pipeline: Worker disk crash suspends workflow")
    res_run = narrator.run_command(["run", pipeline_target, "--db", str(db_file)])
    run_code = res_run.exit_code
    assert run_code == 1
    assert "Workflow suspended at stage 'feature_engineering'" in res_run.stdout

    match = re.search(r"Run ID:\s*([a-f0-9-]+)", res_run.stdout)
    assert match is not None
    run_id = match.group(1)

    # Step 2: Inspect checkpoints in SQLite Memento store
    narrator.step("2. Inspecting Checkpointed Memento State: Verifying completed upstream stages")
    res_status = narrator.run_command(
        ["status", run_id, "--db", str(db_file)],
        env={"COLUMNS": "120"},
    )
    status_code = res_status.exit_code
    assert status_code == 0
    assert "SUSPENDED" in res_status.stdout
    assert "download_dataset" in res_status.stdout
    assert "COMPLETED" in res_status.stdout
    assert "normalize_features" in res_status.stdout
    assert "FAILED" in res_status.stdout

    # Step 3: Inspect failure traceback
    narrator.step("3. Inspecting Failure Diagnostics and Worker Exception Traceback")
    res_inspect = narrator.run_command(
        ["inspect", run_id, "normalize_features", "--db", str(db_file)]
    )
    inspect_code = res_inspect.exit_code
    assert inspect_code == 0
    assert "WorkerDiskExhaustion" in res_inspect.stdout

    # Step 4: Heal environmental issue and resume from checkpoint
    narrator.step("4. Healing Environment and Resuming Workflow from Checkpoint (Skipping Stage 1)")
    if crash_flag.exists():
        crash_flag.unlink()

    res_resume = narrator.run_command(["resume", run_id, pipeline_target, "--db", str(db_file)])
    resume_code = res_resume.exit_code
    assert resume_code == 0
    assert "Workflow completed successfully" in res_resume.stdout

    # Step 5: Validate terminal completion state
    narrator.step("5. Validating Final Terminal Completion State Across All Stages")
    res_final = narrator.run_command(
        ["status", run_id, "--db", str(db_file)],
        env={"COLUMNS": "120"},
    )
    final_code = res_final.exit_code
    assert final_code == 0
    assert "COMPLETED" in res_final.stdout

    # Verify that download_dataset was only executed once (skipped on resume)
    executed_steps = execution_log.read_text(encoding="utf-8").strip().splitlines()
    assert executed_steps.count("download_dataset") == 1
    assert executed_steps.count("normalize_features") == 2  # 1 failed + 1 successful on resume
    assert executed_steps.count("train_classifier") == 1

    artifacts = narrator.finish()

    if os.environ.get("RECORD_DEMO") == "1":
        vtt = artifacts.get("vtt")
        vtt_exists = vtt is not None and vtt.is_file()
        assert vtt_exists is True

        webm = artifacts.get("webm")
        webm_exists = webm is not None and webm.is_file()
        assert webm_exists is True
