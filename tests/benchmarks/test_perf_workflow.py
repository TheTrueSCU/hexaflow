"""Performance microbenchmarks for Hexaflow workflow DAG construction and execution.

Notes/Architectural Intent:
    Measures sub-millisecond DAG assembly and in-memory execution throughput.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hexaflow import Workflow

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


def test_perf_workflow_construction(benchmark: BenchmarkFixture) -> None:
    """Benchmark Workflow DAG declaration latency.

    Args:
        benchmark: pytest-benchmark fixture.

    Notes/Architectural Intent:
        Quantifies baseline workflow and stage/step registration overhead.
    """

    def construct_dag() -> Workflow:
        wf = Workflow("benchmark_pipeline")

        @wf.stage("stage_1")
        @wf.step("step_1")
        def step_1(ctx):
            return 1

        @wf.stage("stage_2")
        @wf.step("step_2", depends_on=["step_1"])
        def step_2(ctx):
            return 2

        return wf

    wf = benchmark(construct_dag)
    assert wf.name == "benchmark_pipeline"


def test_perf_workflow_execution_sync(benchmark: BenchmarkFixture) -> None:
    """Benchmark in-memory DAG step dispatch latency.

    Args:
        benchmark: pytest-benchmark fixture.

    Notes/Architectural Intent:
        Quantifies workflow execution and context propagation overhead.
    """
    wf = Workflow("bench_exec")

    @wf.stage("compute")
    @wf.step("task")
    def task(ctx):
        return {"result": 42}

    def run_pipeline():
        return wf.run()

    res = benchmark(run_pipeline)
    assert res is not None
