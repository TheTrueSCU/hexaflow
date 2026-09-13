# 📚 Hexaflow Examples

Practical reference implementations demonstrating the power and ergonomics of `hexaflow`.

---

## 1. E-Commerce Order Fulfillment (`order_pipeline.py`)

A full multi-stage pipeline demonstrating:
* **Stage 1 (Sequential)**: Cart validation and subtotal computation.
* **Stage 2 (Concurrent Fan-Out / Split)**: Parallel credit card authorization and inventory warehouse reservation.
* **Stage 3 (Join Barrier)**: Shipping label generation requiring both upstream parents to complete.
* **Resilience**: Transient retry policy with exponential backoff and persistent SQLite checkpointing.

### Running the Example

```bash
# Run directly with Python
python examples/order_pipeline.py

# Or execute with the hexaflow CLI
hexaflow run examples/order_pipeline.py:wf
# Or using the 'hf' shortcut:
hf run examples/order_pipeline.py:wf
```

### Inspecting Execution State

```bash
# View stages, steps, status badges, and timing
hf status <run_id>

# Inspect inputs, outputs, or error tracebacks for any step
hf inspect <run_id> create_shipment_label
```
