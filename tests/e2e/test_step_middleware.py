"""User Journey HF-03: Step Middleware Pipeline & Telemetry demo test.

Notes/Architectural Intent:
    Demonstrates the onion-layered step interceptor pipeline in Hexaflow.
    Chains InOutStepMiddleware, TimingStepMiddleware, and TelemetryTraceStepMiddleware
    to intercept step execution, propagate correlation IDs, calculate high-precision
    latencies, and generate structured span traces across workflow stages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from hexaqual.adapters.testing.narrator import CliNarrator

from hexaflow.cli.main import app


@pytest.mark.demo
def test_step_middleware_journey(tmp_path: Path) -> None:
    """Execute and record the Step Middleware Pipeline & Telemetry demo."""
    db_file = tmp_path / "telemetry_state.db"
    trace_log = tmp_path / "spans.log"
    pipeline_script = tmp_path / "telemetry_pipeline.py"

    pipeline_code = f"""from __future__ import annotations
import time
import uuid
from pathlib import Path
from typing import Any
from hexaflow.dsl.builder import Workflow
from hexaflow.adapters.middleware.base import InOutStepMiddleware
from hexaflow.adapters.middleware.timing import TimingStepMiddleware
from hexaflow.domain.state import StepContext

SPAN_LOG = Path({str(trace_log)!r})

def log_span(event: str, details: str) -> None:
    with SPAN_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{{event}}|{{details}}\\n")

class CorrelationStepMiddleware(InOutStepMiddleware):
    def before(self, ctx: StepContext) -> Any:
        corr_id = ctx.metadata.get("correlation_id") or f"corr-{{uuid.uuid4().hex[:8]}}"
        ctx.metadata["correlation_id"] = corr_id
        log_span("CORRELATION", f"step={{ctx.step_name}} corr_id={{corr_id}}")
        return corr_id

class TelemetrySpanStepMiddleware(InOutStepMiddleware):
    def before(self, ctx: StepContext) -> Any:
        span_id = f"span-{{uuid.uuid4().hex[:6]}}"
        start_ts = time.time()
        log_span("SPAN_START", f"step={{ctx.step_name}} stage={{ctx.stage_name}} span_id={{span_id}}")
        return {{"span_id": span_id, "start": start_ts}}

    def after(self, ctx: StepContext, result: Any, context: Any) -> Any:
        span_id = context["span_id"]
        elapsed = (time.time() - context["start"]) * 1000.0
        log_span("SPAN_END", f"step={{ctx.step_name}} span_id={{span_id}} elapsed_ms={{elapsed:.2f}}")
        return result

def on_timing(ctx: StepContext, duration: float) -> None:
    log_span("TIMING", f"step={{ctx.step_name}} duration_s={{duration:.4f}}")

pipeline = Workflow("telemetry-checkout-pipeline")
pipeline.use(CorrelationStepMiddleware())
pipeline.use(TelemetrySpanStepMiddleware())
pipeline.use(TimingStepMiddleware(callback=on_timing))

pipeline.stage("verification")
@pipeline.step("validate_cart")
def validate_cart(ctx: StepContext) -> dict[str, Any]:
    return {{"valid": True, "items_count": 3, "total_usd": 129.50}}

pipeline.stage("settlement")
@pipeline.step("charge_gateway", depends_on=("validate_cart",))
def charge_gateway(ctx: StepContext) -> dict[str, str]:
    return {{"status": "AUTHORIZED", "auth_code": "AUTH-7719"}}

pipeline.stage("fulfillment")
@pipeline.step("dispatch_order", depends_on=("charge_gateway",))
def dispatch_order(ctx: StepContext) -> dict[str, str]:
    return {{"tracking_number": "TRK-99014", "carrier": "HexaExpress"}}
"""
    pipeline_script.write_text(pipeline_code, encoding="utf-8")
    pipeline_target = f"{pipeline_script}:pipeline"

    repo_root = Path(__file__).resolve().parent.parent.parent
    output_dir = repo_root / "docs" / "assets" / "demos"

    narrator = CliNarrator(
        app,
        output_name="hexaflow-step-middleware",
        output_dir=output_dir,
        width=120,
    )

    # Step 1: Inspect Workflow Graph with Middleware Interceptors
    narrator.step(
        "1. Inspecting Workflow Pipeline with Chained Interceptors (Correlation, Span, Timing)"
    )
    res_graph = narrator.run_command(["graph", "render", pipeline_target, "--format", "ascii"])
    assert res_graph.exit_code == 0
    assert "validate_cart" in res_graph.stdout

    # Step 2: Execute Workflow under Interceptor Pipeline
    narrator.step("2. Dispatching Workflow: Interceptor stack captures spans and timing metrics")
    res_run = narrator.run_command(["run", pipeline_target, "--db", str(db_file)])
    assert res_run.exit_code == 0
    assert "Workflow completed successfully" in res_run.stdout

    match = re.search(r"Run ID:\s*([a-f0-9-]+)", res_run.stdout)
    assert match is not None
    run_id = match.group(1)

    # Step 3: Inspect Status & Checkpoint Metrics
    narrator.step("3. Inspecting Completed Stage Checkpoints and Latency Metrics")
    res_status = narrator.run_command(
        ["status", run_id, "--db", str(db_file)],
        env={"COLUMNS": "120"},
    )
    assert res_status.exit_code == 0
    assert "COMPLETED" in res_status.stdout
    assert any(name in res_status.stdout for name in ("validate_cart", "validate_c"))
    assert any(name in res_status.stdout for name in ("charge_gateway", "charge_g"))
    assert any(name in res_status.stdout for name in ("dispatch_order", "dispatch_o"))

    # Step 4: Inspect Step Output Payload
    narrator.step("4. Inspecting Step Checkpoint Payload across Interceptor Layers")
    res_inspect = narrator.run_command(["inspect", run_id, "dispatch_order", "--db", str(db_file)])
    assert res_inspect.exit_code == 0
    assert "HexaExpress" in res_inspect.stdout

    # Step 5: Verify Telemetry Spans and Correlation Tracking
    narrator.step("5. Validating Span Cascades, Correlation IDs, and High-Precision Latencies")
    trace_lines = trace_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(trace_lines) > 0

    span_starts = [line for line in trace_lines if line.startswith("SPAN_START|")]
    span_ends = [line for line in trace_lines if line.startswith("SPAN_END|")]
    timings = [line for line in trace_lines if line.startswith("TIMING|")]

    assert len(span_starts) == 3
    assert len(span_ends) == 3
    assert len(timings) == 3

    artifacts = narrator.finish()

    if os.environ.get("RECORD_DEMO") == "1":
        vtt = artifacts.get("vtt")
        vtt_exists = vtt is not None and vtt.is_file()
        assert vtt_exists is True

        webm = artifacts.get("webm")
        webm_exists = webm is not None and webm.is_file()
        assert webm_exists is True
