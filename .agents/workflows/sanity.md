---
name: sanity
description: Run the full Hexaqual sanity suite, auto-fix lint/statements/diagrams, and verify test parity.
---

# Workflow: Hexaqual Sanity Verification & Triage

Follow these steps to validate code changes against the workspace quality gate:

1. **Run Static Sanity Checks**:
   ```bash
   uv run hexaqual sanity -a --skip-tests
   ```

2. **Auto-Fix Drift (if needed)**:
   - If `__all__` integrity failed:
     ```bash
     uv run hexaqual statements fix
     ```
   - If architecture diagrams are stale (and `docs/assets/pydeps` exists):
     ```bash
     uv run hexaqual deps pydeps --fix
     ```
   - If code formatting or lint errors:
     ```bash
     uv run ruff check --fix src tests
     uv run ruff format src tests
     ```

3. **Verify Test Parity & Boundaries**:
   ```bash
   uv run hexaqual parity test
   ```

4. **Run Impacted Tests**:
   ```bash
   uv run hexaqual test impact
   ```

5. **Pre-Commit Gate Validation**:
   ```bash
   uv run pre-commit run --all-files
   ```
