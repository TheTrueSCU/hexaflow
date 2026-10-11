"""Unit tests for fluent Workflow builder and decorator DSL.

Notes/Architectural Intent:
    Verifies that @wf.stage and @wf.step decorator syntaxes correctly build
    validated WorkflowDefinitions and delegate execution to the engine seamlessly.
"""

from pathlib import Path

from hexaflow.adapters.engines.local_async import AsyncioWorkflowEngine
from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.models import ExecutionPool, StageExecutionMode, TriggerRule
from hexaflow.domain.state import StepStatus, WorkflowStatus
from hexaflow.dsl.builder import Workflow


def test_fluent_workflow_decorator_dsl() -> None:
    """Validate full lifecycle using @wf.stage and @wf.step decorators."""
    store = InMemoryStateStore()
    wf = Workflow("order_fulfillment", version="2.0.0", state_store=store)

    @wf.stage("cart_validation")
    @wf.step("validate_items")
    def validate(ctx):
        return {"items_valid": True, "count": 2}

    @wf.stage("payment_and_inventory", execution_mode=StageExecutionMode.CONCURRENT_ALL)
    @wf.step("charge_card", depends_on=["validate_items"])
    def charge(ctx):
        return "charge_ok"

    @wf.stage("payment_and_inventory")
    @wf.step("reserve_stock", depends_on=["validate_items"])
    def reserve(ctx):
        return "stock_ok"

    @wf.stage("shipping")
    @wf.step("ship_order", depends_on=["charge_card", "reserve_stock"])
    def ship(ctx):
        return "shipped_123"

    definition = wf.to_definition()
    assert definition.name == "order_fulfillment"
    assert len(definition.stages) == 3
    assert len(definition.stages[1].steps) == 2

    # Execute workflow
    result = wf.run()
    assert result.status == WorkflowStatus.COMPLETED
    assert result.step_checkpoints["ship_order"].output_payload == "shipped_123"


def test_workflow_resume_and_abort_via_dsl() -> None:
    """Validate resume and abort methods exposed directly on Workflow instance."""
    store = InMemoryStateStore()
    wf = Workflow("faulty_flow", state_store=store)
    fail_step_2 = True

    @wf.stage("s1")
    @wf.step("step_1")
    def s1(ctx):
        return "s1_ok"

    @wf.stage("s2")
    @wf.step("step_2", depends_on=["step_1"])
    def s2(ctx):
        if fail_step_2:
            raise RuntimeError("Failure in s2")
        return "s2_ok"

    res = wf.run()
    assert res.status == WorkflowStatus.SUSPENDED

    # Test abort
    abort_res = wf.abort(res.run_id)
    assert abort_res.status == WorkflowStatus.CANCELLED

    # Reset fail flag and run fresh run that fails first
    fail_step_2 = True
    res_2 = wf.run()
    assert res_2.status == WorkflowStatus.SUSPENDED

    # Fix error and resume
    fail_step_2 = False
    resumed = wf.resume(res_2.run_id)
    assert resumed.status == WorkflowStatus.COMPLETED
    assert resumed.step_checkpoints["step_2"].output_payload == "s2_ok"


def test_dsl_step_metadata() -> None:
    """Validate @wf.step attaches metadata to compiled StepDefinition."""
    wf = Workflow("meta_wf")

    @wf.stage("gpu_stage")
    @wf.step("gpu_task", metadata={"gpus": 1, "queue": "high-priority"})
    def task(ctx):
        return "gpu_done"

    defn = wf.to_definition()
    step_defn = defn.get_step("gpu_task")
    assert step_defn.metadata["gpus"] == 1
    assert step_defn.metadata["queue"] == "high-priority"


def test_dsl_trigger_rule_and_skipping() -> None:
    """Validate trigger_rule and skip_steps work seamlessly through Workflow DSL."""
    store = InMemoryStateStore()
    wf = Workflow("skip_wf", state_store=store)

    @wf.step("step_1")
    def s1():
        return "s1_val"

    @wf.step("step_2", depends_on=["step_1"], trigger_rule=TriggerRule.ALL_SUCCESS_OR_SKIPPED)
    def s2():
        return "s2_val"

    definition = wf.to_definition()
    assert definition.get_step("step_2").trigger_rule == TriggerRule.ALL_SUCCESS_OR_SKIPPED

    # Run skipping step_1
    res = wf.run(skip_steps=["step_1"])
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["step_1"].status == StepStatus.SKIPPED
    assert res.step_checkpoints["step_2"].status == StepStatus.COMPLETED
    assert res.step_checkpoints["step_2"].output_payload == "s2_val"


def test_dsl_create_cli_binder() -> None:
    """Validate Workflow.create_cli_binder returns a bound WorkflowCliBinder."""
    wf = Workflow("binder_wf")

    @wf.step("lint")
    def _lint():
        return "lint_ok"

    binder = wf.create_cli_binder(aliases={"lint": ["--skip-l"]})
    assert binder.step_names == ["lint"]
    spec = binder.get_spec("lint")
    assert spec is not None
    assert "--skip-l" in spec.cli_flags


def test_dsl_map_step_and_dependency_inference() -> None:
    """Validate @wf.map_step decorator and automatic upstream dependency inference."""
    store = InMemoryStateStore()
    wf = Workflow("mapped_pipeline", state_store=store)

    @wf.step("generate_numbers")
    def gen():
        return [1, 2, 3, 4]

    @wf.map_step("square", over="generate_numbers", concurrency_limit=2)
    def sq(item: int) -> int:
        return item * item

    definition = wf.to_definition()
    step_defn = definition.get_step("square")
    assert step_defn.is_mapped is True
    assert step_defn.map_over == "generate_numbers"
    assert step_defn.concurrency_limit == 2
    assert "generate_numbers" in step_defn.depends_on

    res = wf.run()
    assert res.status == WorkflowStatus.COMPLETED
    assert res.step_checkpoints["square"].output_payload == [1, 4, 9, 16]


def test_dsl_to_mermaid_and_to_ascii_delegation() -> None:
    """Validate Workflow to_mermaid and to_ascii delegate to definition."""
    wf = Workflow("diagram_wf")

    @wf.step("init")
    def _init():
        return "ok"

    mermaid_str = wf.to_mermaid()
    assert mermaid_str.startswith("graph TD")
    assert "init" in mermaid_str

    ascii_str = wf.to_ascii()
    assert "Workflow: diagram_wf" in ascii_str
    assert "init" in ascii_str


def test_dsl_execution_pool_compilation_and_workers() -> None:
    """Validate @wf.step and @wf.map_step compile execution pool configurations."""
    wf = Workflow(
        "pool_wf",
        max_process_workers=3,
        max_thread_workers=6,
    )

    @wf.step("cpu_task", pool=ExecutionPool.PROCESS)
    def _cpu(ctx):
        return "cpu_res"

    @wf.step("io_task", pool=ExecutionPool.THREAD)
    def _io(ctx):
        return "io_res"

    @wf.map_step("batch_cpu", over="cpu_task", pool=ExecutionPool.PROCESS)
    def _map_cpu(item):
        return item

    defn = wf.to_definition()
    cpu_step = defn.get_step("cpu_task")
    assert cpu_step.pool == ExecutionPool.PROCESS

    io_step = defn.get_step("io_task")
    assert io_step.pool == ExecutionPool.THREAD

    map_step = defn.get_step("batch_cpu")
    assert map_step.pool == ExecutionPool.PROCESS

    # Verify workers are forwarded to default engine
    engine = wf._engine
    assert isinstance(engine, AsyncioWorkflowEngine)
    p_workers = engine._max_process_workers
    assert p_workers == 3
    t_workers = engine._max_thread_workers
    assert t_workers == 6


async def test_dsl_context_managers() -> None:
    """Validate sync and async context managers gracefully close worker pools."""
    with Workflow("ctx_sync", max_process_workers=2) as wf_sync:
        status = wf_sync.name
        assert status == "ctx_sync"

    async with Workflow("ctx_async", max_thread_workers=2) as wf_async:
        name = wf_async.name
        assert name == "ctx_async"


def test_dsl_bind_store(tmp_path: Path) -> None:
    """Validate bind_store reconfigures state store on workflow and engine."""
    from hexaflow.adapters.storage.sqlite import SqliteStateStore

    wf = Workflow("bind_store_wf")
    new_store = SqliteStateStore(db_path=tmp_path / "bind.db")
    wf.bind_store(new_store)

    assert wf._store is new_store
    engine = wf._engine
    assert isinstance(engine, AsyncioWorkflowEngine)
    assert engine._store is new_store


def test_dsl_bind_store_invalid_engine() -> None:
    """Validate bind_store raises TypeError when engine does not support store binding."""
    from unittest.mock import MagicMock

    import pytest

    from hexaflow.adapters.storage.in_memory import InMemoryStateStore
    from hexaflow.ports.engine import WorkflowEnginePort

    engine = MagicMock(spec=WorkflowEnginePort)
    wf = Workflow("dummy_wf", engine=engine)
    with pytest.raises(TypeError, match="does not support binding a state store"):
        wf.bind_store(InMemoryStateStore())


def test_workflow_dsl_graph_and_renderers() -> None:
    """Validate to_graph, to_dot, render, and render_image via Workflow builder."""
    wf = Workflow("graph_dsl_wf")

    @wf.step("extract", estimated_duration_seconds=2.5)
    def extract(ctx):
        return [1, 2, 3]

    @wf.step("transform", depends_on=["extract"], side_effects=True, estimated_duration_seconds=5.0)
    def transform(ctx):
        return [x * 2 for x in ctx.get("extract")]

    graph = wf.to_graph()
    assert graph.workflow_name == "graph_dsl_wf"
    assert len(graph.nodes) == 2

    # Test DOT rendering
    dot_out = wf.to_dot()
    assert 'digraph "graph_dsl_wf"' in dot_out

    # Test generic render
    mermaid_out = wf.render("mermaid")
    assert "flowchart TD" in mermaid_out

    json_out = wf.render("json")
    assert '"workflow_id": "graph_dsl_wf"' in json_out

    # Test image rendering (ascii fallback provides svg)
    svg_bytes = wf.render_image("ascii", "svg")
    assert svg_bytes.startswith(b"<svg")


def test_workflow_dsl_simulate_and_fault_injection() -> None:
    """Validate wf.simulate() and async simulate with mock and fault injection."""
    wf = Workflow("sim_wf")

    executed_side_effect = False

    @wf.step("query_records")
    def query_records(ctx):
        return {"users": ["alice", "bob"]}

    @wf.step(
        "notify_users", depends_on=["query_records"], side_effects=True, dry_run={"delivered": 2}
    )
    def notify_users(ctx):
        nonlocal executed_side_effect
        executed_side_effect = True
        return {"delivered": 100}

    # Run simulation
    sim_state = wf.simulate()
    assert sim_state.status == WorkflowStatus.COMPLETED
    assert executed_side_effect is False
    assert sim_state.step_checkpoints["notify_users"].output_payload == {"delivered": 2}

    # Test simulate with fault injection
    fault_state = wf.simulate(fault_injection={"query_records": TimeoutError("Simulated timeout")})
    assert fault_state.status == WorkflowStatus.SUSPENDED
    assert fault_state.step_checkpoints["query_records"].status == StepStatus.FAILED
