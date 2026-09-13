"""Order Fulfillment Pipeline - Reference Hexaflow Workflow.

Notes/Architectural Intent:
    Demonstrates a complete multi-stage workflow with concurrent fan-out (split),
    join barrier synchronization, transient retries, and checkpointed resumption.
"""

from hexaflow import RetryPolicy, StageExecutionMode, Workflow

# Initialize the workflow
wf = Workflow(
    name="order_fulfillment",
    version="1.0.0",
    description="E-commerce checkout, payment processing, inventory reservation, and shipping.",
)


# Stage 1: Validation (Sequential)
@wf.stage("validation", description="Validate cart items and pricing.")
@wf.step("validate_cart", retries=RetryPolicy(max_attempts=3))
def validate_cart(ctx) -> dict:
    """Validate cart contents and calculate order subtotal."""
    print("  [Step 1] Validating cart items...")
    return {
        "order_id": "ord_9901",
        "items": ["laptop", "docking_station"],
        "total_usd": 1850.00,
    }


# Stage 2: Concurrent Processing (Split / Fan-Out)
@wf.stage(
    "processing",
    execution_mode=StageExecutionMode.CONCURRENT_ALL,
    description="Parallel processing.",
)
@wf.step("authorize_payment", depends_on=["validate_cart"])
def authorize_payment(ctx) -> dict:
    """Authorize credit card payment."""
    cart = ctx.inputs["validate_cart"]
    print(f"  [Step 2A] Authorizing payment for ${cart['total_usd']}...")
    return {
        "transaction_id": "tx_auth_4412",
        "status": "APPROVED",
    }


@wf.stage("processing")
@wf.step("reserve_inventory", depends_on=["validate_cart"])
def reserve_inventory(ctx) -> dict:
    """Reserve inventory warehouse allocations."""
    cart = ctx.inputs["validate_cart"]
    print(f"  [Step 2B] Reserving stock for items: {cart['items']}...")
    return {
        "reservation_id": "res_wh_88",
        "warehouse": "US-EAST-1",
    }


# Stage 3: Fulfillment (Join Barrier)
@wf.stage("fulfillment", description="Generate shipment label after payment and inventory.")
@wf.step("create_shipment_label", depends_on=["authorize_payment", "reserve_inventory"])
def create_shipment_label(ctx) -> dict:
    """Create courier shipping label requiring both payment and inventory confirmation."""
    payment = ctx.inputs["authorize_payment"]
    inventory = ctx.inputs["reserve_inventory"]

    print(
        f"  [Step 3] Issuing shipping label from {inventory['warehouse']} (Payment: {payment['transaction_id']})..."
    )
    return {
        "tracking_number": "TRK-98127391823",
        "courier": "HexaExpress",
    }


if __name__ == "__main__":
    print(f"Executing workflow '{wf.name}'...")
    state = wf.run()
    print(f"Workflow Status: {state.status.value} (Run ID: {state.run_id})")

    for step_name, chk in state.step_checkpoints.items():
        print(f"  - {step_name}: {chk.status.value} (took {chk.duration_seconds:.2f}s)")
