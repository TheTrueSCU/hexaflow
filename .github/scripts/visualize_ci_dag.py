"""Visualize planned CI workflow DAG and append Mermaid diagram to GitHub step summary.

Notes/Architectural Intent:
    This script is invoked by GitHub Actions during CI runs to generate
    a rendered Mermaid execution graph of the CI pipeline stages and write
    it to `$GITHUB_STEP_SUMMARY`. Keeping this logic in a dedicated script
    prevents Bash quote escaping and backtick interpolation issues in CI YAML.
"""

from __future__ import annotations

import os
from typing import Any

from hexaflow import Workflow


def main() -> None:
    """Generate CI workflow DAG and output to GITHUB_STEP_SUMMARY if present.

    Returns:
        None.
    """
    wf: Workflow[Any] = Workflow("ci_pipeline")

    @wf.stage("quality")
    @wf.step("quality_gate")
    def quality_gate(ctx: Any) -> str:
        return "ok"

    @wf.stage("test")
    @wf.step("test_suite", depends_on=["quality_gate"])
    def test_suite(ctx: Any) -> str:
        return "ok"

    @wf.stage("verification")
    @wf.step("hypothesis_fuzzing", depends_on=["test_suite"])
    def hypothesis_fuzzing(ctx: Any) -> str:
        return "ok"

    @wf.step("mutation_verification", depends_on=["test_suite"])
    def mutation_verification(ctx: Any) -> str:
        return "ok"

    @wf.stage("gate")
    @wf.step("ci_success", depends_on=["hypothesis_fuzzing", "mutation_verification"])
    def ci_success(ctx: Any) -> str:
        return "ok"

    mermaid: str = wf.render("mermaid")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("### 🗺️ Planned Hexaflow CI Workflow Execution Plan\n\n")
            f.write(f"```mermaid\n{mermaid}```\n")
    else:
        print(f"```mermaid\n{mermaid}```")


if __name__ == "__main__":
    main()
