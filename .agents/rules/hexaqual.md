---
trigger: always_on
description: Enforce quality gates, test parity, cognitive complexity limits, and hexagonal boundaries using the hexaqual toolsuite.
---

## hexaqual Quality Guardrails

This project enforces strict hexagonal architecture boundaries, quality gates, and code conventions using `hexaqual`.

### Invariants to Maintain
1. **Architecture & Hexagonal Boundaries**:
   - `domain/`: Pure workflow models, state machines, and DAG execution rules. Zero external framework dependencies.
   - `ports/`: Abstract ABC interfaces (`@abstractmethod`) specifying storage, execution, and presentation contracts.
   - `adapters/`: Concrete implementations (never import from `infra/` or `cli/`).
   - `dsl/` & `cli/`: Workflow builders and Typer CLI driving adapters.
2. **Strict Test Parity**: Every `src/hexaflow/<path>.py` requires a matching `tests/unit/<path>/test_<name>.py` and `__init__.py`. Verified by `uv run hexaqual parity test`.
3. **`__all__` Integrity**: Every `__all__` list must be strictly sorted casefold and deduplicated. Enforced by `uv run hexaqual statements check` (auto-fix with `uv run hexaqual statements fix`).
4. **Cognitive Complexity**: Every function and method must maintain cognitive complexity $\le 25$ as measured by `complexipy`. Decompose branching logic into private helper methods.
5. **Architecture Diagrams**: If `docs/assets/pydeps/` exists, diagrams must stay in sync with code changes. Check with `uv run hexaqual deps pydeps --check` (auto-fix with `uv run hexaqual deps pydeps --fix`).
6. **Side-Effect Free Assertions**: In tests, assign method return values to variables first (e.g. `res = func(); assert res is True`) to prevent CodeQL side-effect warnings.
7. **Quality Gate Verification**: Run `uv run hexaqual sanity -a --skip-tests` before finalizing commits.
