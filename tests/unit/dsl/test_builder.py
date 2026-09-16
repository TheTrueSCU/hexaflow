"""Unit tests for fluent Workflow builder and decorator DSL.

Notes/Architectural Intent:
    Verifies that @wf.stage and @wf.step decorator syntaxes correctly build
    validated WorkflowDefinitions and delegate execution to the engine seamlessly.
"""

from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.models import StageExecutionMode, TriggerRule
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
