"""Unit tests for GitHub Actions workflow loader in hexaflow.adapters.loaders.github_actions.

Notes/Architectural Intent:
    Verifies that GitHub Actions YAML workflows are correctly translated into
    Workflow DAG definitions with accurate dependency edges and step names.
"""

from pathlib import Path

import pytest

from hexaflow.adapters.loaders.github_actions import load_github_actions_workflow


def test_load_github_actions_from_yaml_string() -> None:
    yaml_content = """
name: Sample Pipeline
jobs:
  lint:
    runs-on: ubuntu-latest
  test:
    needs: lint
    runs-on: ubuntu-latest
  deploy:
    needs:
      - lint
      - test
    runs-on: ubuntu-latest
"""
    wf = load_github_actions_workflow(yaml_content)
    assert wf.name == "Sample Pipeline"

    graph = wf.to_graph()
    assert set(graph.nodes.keys()) == {"lint", "test", "deploy"}
    assert graph.predecessors("test") == ["lint"]
    assert graph.predecessors("deploy") == ["lint", "test"]

    mermaid = wf.render("mermaid")
    assert "lint --> test" in mermaid
    assert "test --> deploy" in mermaid


def test_load_github_actions_from_real_file(tmp_path: Path) -> None:
    workflow_file = tmp_path / "ci.yml"
    workflow_file.write_text(
        """
name: Minimal CI
jobs:
  build:
    steps: []
  check:
    needs: build
    steps: []
""",
        encoding="utf-8",
    )

    wf = load_github_actions_workflow(workflow_file)
    assert wf.name == "Minimal CI"
    assert "build" in wf.to_graph().nodes
    assert "check" in wf.to_graph().nodes


def test_load_github_actions_nonexistent_file() -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        load_github_actions_workflow("non_existent_ci_workflow.yml")


def test_load_github_actions_invalid_yaml() -> None:
    with pytest.raises(ValueError, match="Failed to parse GitHub Actions YAML"):
        load_github_actions_workflow("jobs: [this is invalid yaml: :::")


def test_load_github_actions_missing_jobs() -> None:
    with pytest.raises(ValueError, match="missing or empty 'jobs'"):
        load_github_actions_workflow("name: No Jobs Workflow\non: push\n")


def test_load_github_actions_directory_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not a regular file"):
        load_github_actions_workflow(tmp_path)


def test_load_github_actions_non_dict_root() -> None:
    with pytest.raises(ValueError, match="root must be a YAML mapping"):
        load_github_actions_workflow("- item1\n- item2\n")
