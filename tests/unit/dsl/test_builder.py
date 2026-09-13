"""Unit tests for fluent Workflow builder and decorator DSL.

Notes/Architectural Intent:
    Verifies that @wf.stage and @wf.step decorator syntaxes correctly build
    validated WorkflowDefinitions and delegate execution to the engine seamlessly.
"""

from hexaflow.adapters.storage.in_memory import InMemoryStateStore
from hexaflow.domain.models import StageExecutionMode
from hexaflow.domain.state import WorkflowStatus
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
