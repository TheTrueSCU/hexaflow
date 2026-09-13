"""End-to-end integration tests for multi-stage workflow execution with SQLite.

Notes/Architectural Intent:
    Tests the complete integration lifecycle: disk-backed SQLite checkpointing,
    split/join execution, simulated failure, process resumption without step re-execution,
    and CLI inspection.
"""

from pathlib import Path

from hexaflow import RetryPolicy, StageExecutionMode, Workflow
from hexaflow.adapters.storage.sqlite import SqliteStateStore
from hexaflow.domain.state import WorkflowStatus


def test_e2e_resumable_pipeline(tmp_path: Path) -> None:
    """End-to-end integration test proving resumption durability across SQLite persistence."""
    db_file = tmp_path / "integration_state.db"
    artifacts = tmp_path / "artifacts"
    store = SqliteStateStore(db_path=db_file, artifacts_dir=artifacts)

    wf = Workflow("e2e_etl", version="1.0.0", state_store=store)

    step_1_runs = 0
    fail_step_2 = True

    @wf.stage("ingest")
    @wf.step("fetch_records", retries=RetryPolicy(max_attempts=2))
    def fetch(ctx):
        nonlocal step_1_runs
        step_1_runs += 1
        return {"records": [10, 20, 30]}

    @wf.stage("process", execution_mode=StageExecutionMode.CONCURRENT_ALL)
    @wf.step("transform_data", depends_on=["fetch_records"])
    def transform(ctx):
        if fail_step_2:
            raise RuntimeError("Simulated processing crash")
        records = ctx.inputs["fetch_records"]["records"]
        return [r * 2 for r in records]

    @wf.stage("process")
    @wf.step("enrich_metadata", depends_on=["fetch_records"])
    def enrich(ctx):
        return {"enriched": True}

    @wf.stage("export")
    @wf.step("write_summary", depends_on=["transform_data", "enrich_metadata"])
    def export(ctx):
        data = ctx.inputs["transform_data"]
        return {"sum": sum(data)}

    # 1. First execution: should fail at transform_data and suspend
    initial_res = wf.run()
    assert initial_res.status == WorkflowStatus.SUSPENDED
    assert step_1_runs == 1

    # Verify SQLite persisted the state
    saved_run = store.get_run(initial_res.run_id)
    assert saved_run is not None
    assert saved_run.status == WorkflowStatus.SUSPENDED

    # 2. Fix failure condition and resume
    fail_step_2 = False
    resumed_res = wf.resume(initial_res.run_id)

    assert resumed_res.status == WorkflowStatus.COMPLETED
    assert resumed_res.step_checkpoints["write_summary"].output_payload == {"sum": 120}

    # CRITICAL INVARIANT: Step 1 was durable in SQLite and never re-executed!
    assert step_1_runs == 1
