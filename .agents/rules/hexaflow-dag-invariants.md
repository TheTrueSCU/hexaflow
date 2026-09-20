---
trigger: always_on
description: DAG structural invariants and checkpoint durability rules for hexaflow.
---

## Hexaflow DAG Invariants

- Graphs must be Directed and Acyclic: cycle detection runs on every workflow build via Kahn's algorithm.
- Step outcomes are immutable once terminal (`COMPLETED`, `FAILED`, `SKIPPED`). No in-place mutation.
- Checkpoint records must be persisted before a step is marked complete (write-ahead semantics).
- On failure, reverse compensation handlers must unwind in reverse topological order.
- `@wf.map_step` sub-steps use isolated checkpoint keys (`<step>[<i>]`) and may be retried independently.
