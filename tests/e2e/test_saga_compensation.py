"""User Journey HF-02: Resilient Saga Compensation & Rollback demo test.

Notes/Architectural Intent:
    Demonstrates a distributed multi-step saga orchestrated by Hexaflow.
    When a mid-flight step fails, the workflow enters a SUSPENDED state with
    all completed step checkpoints persisted to SQLite.
    Triggering `hexaflow abort` unwinds step compensations in exact reverse (LIFO)
    completion order, ensuring zero state leaks across distributed boundaries.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from hexaqual.adapters.testing.narrator import CliNarrator

from hexaflow.cli.main import app


@pytest.mark.demo
def test_saga_compensation_journey(tmp_path: Path) -> None:
    """Execute and record the Resilient Saga Compensation & Rollback demo."""
    db_file = tmp_path / "saga_state.db"
    events_log = tmp_path / "saga_compensations.log"
    saga_script = tmp_path / "trip_saga.py"

    saga_code = f"""from __future__ import annotations
from pathlib import Path
from hexaflow.dsl.builder import Workflow
from hexaflow.domain.state import StepContext

LOG_FILE = Path({str(events_log)!r})

def log_event(msg: str) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(msg + "\\n")

def reserve_flight(ctx: StepContext) -> dict[str, str]:
    log_event("ACTION:reserve_flight:FLIGHT-982")
    return {{"reservation_id": "FLIGHT-982", "passenger": "Alice", "route": "SFO-JFK"}}

def cancel_flight(ctx: StepContext) -> None:
    res_id = ctx.output.get("reservation_id", "UNKNOWN") if isinstance(ctx.output, dict) else "UNKNOWN"
    log_event(f"COMPENSATE:cancel_flight:{{res_id}}")

def reserve_hotel(ctx: StepContext) -> dict[str, str]:
    log_event("ACTION:reserve_hotel:HOTEL-411")
    return {{"booking_id": "HOTEL-411", "guest": "Alice", "nights": "3"}}

def cancel_hotel(ctx: StepContext) -> None:
    booking_id = ctx.output.get("booking_id", "UNKNOWN") if isinstance(ctx.output, dict) else "UNKNOWN"
    log_event(f"COMPENSATE:cancel_hotel:{{booking_id}}")

def charge_payment(ctx: StepContext) -> None:
    log_event("ACTION:charge_payment:FAILING")
    raise RuntimeError("PaymentGatewayError: Card balance exceeded, transaction declined")

def refund_payment(ctx: StepContext) -> None:
    log_event("COMPENSATE:refund_payment")

trip_saga = Workflow("trip-booking-saga")

trip_saga.stage("flight_reservation")
@trip_saga.step("reserve_flight", compensation=cancel_flight)
def run_reserve_flight(ctx: StepContext) -> dict[str, str]:
    return reserve_flight(ctx)

trip_saga.stage("hotel_booking")
@trip_saga.step("reserve_hotel", compensation=cancel_hotel, depends_on=("reserve_flight",))
def run_reserve_hotel(ctx: StepContext) -> dict[str, str]:
    return reserve_hotel(ctx)

trip_saga.stage("payment_processing")
@trip_saga.step("charge_payment", compensation=refund_payment, depends_on=("reserve_hotel",))
def run_charge_payment(ctx: StepContext) -> None:
    charge_payment(ctx)
"""
    saga_script.write_text(saga_code, encoding="utf-8")
    saga_target = f"{saga_script}:trip_saga"

    repo_root = Path(__file__).resolve().parent.parent.parent
    output_dir = repo_root / "docs" / "assets" / "demos"

    narrator = CliNarrator(
        app,
        output_name="hexaflow-saga-compensation",
        output_dir=output_dir,
    )

    # Step 1: Inspect Saga DAG
    narrator.step(
        "1. Inspecting Distributed Saga DAG with Forward Actions and Backward Compensations"
    )
    res_graph = narrator.run_command(["graph", "render", saga_target, "--format", "ascii"])
    graph_code = res_graph.exit_code
    assert graph_code == 0
    assert "reserve_flight" in res_graph.stdout

    # Step 2: Execute Saga triggering mid-flight failure
    narrator.step("2. Dispatching Saga Execution: Payment failure triggers safe suspension")
    res_run = narrator.run_command(["run", saga_target, "--db", str(db_file)])
    run_code = res_run.exit_code
    assert run_code == 1
    assert "Workflow suspended at stage 'payment_processing'" in res_run.stdout

    match = re.search(r"Run ID:\s*([a-f0-9-]+)", res_run.stdout)
    assert match is not None
    run_id = match.group(1)

    # Step 3: Inspect Suspended Workflow State
    narrator.step("3. Inspecting Suspended Workflow and Completed Step Checkpoints")
    res_status = narrator.run_command(["status", run_id, "--db", str(db_file)])
    status_code = res_status.exit_code
    assert status_code == 0
    assert "SUSPENDED" in res_status.stdout
    assert "COMPLETED" in res_status.stdout
    assert "FAILED" in res_status.stdout

    # Step 4: Abort Saga and unwind compensations
    narrator.step("4. Aborting Saga: Automatically unwinding compensations in reverse LIFO order")
    res_abort = narrator.run_command(["abort", run_id, saga_target, "--db", str(db_file)])
    abort_code = res_abort.exit_code
    assert abort_code == 0
    assert "Workflow run cancelled" in res_abort.stdout

    # Step 5: Validate zero state leaks
    narrator.step("5. Validating Zero State Leaks across Distributed System Resources")
    res_final = narrator.run_command(["status", run_id, "--db", str(db_file)])
    final_code = res_final.exit_code
    assert final_code == 0
    assert "CANCELLED" in res_final.stdout

    # Verify compensation execution order from log
    log_content = events_log.read_text(encoding="utf-8").strip().splitlines()
    compensations = [line for line in log_content if line.startswith("COMPENSATE:")]
    expected_compensations = [
        "COMPENSATE:cancel_hotel:HOTEL-411",
        "COMPENSATE:cancel_flight:FLIGHT-982",
    ]
    assert compensations == expected_compensations

    artifacts = narrator.finish()

    if os.environ.get("RECORD_DEMO") == "1":
        vtt = artifacts.get("vtt")
        vtt_exists = vtt is not None and vtt.is_file()
        assert vtt_exists is True

        webm = artifacts.get("webm")
        webm_exists = webm is not None and webm.is_file()
        assert webm_exists is True
