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
  extra:
    needs: 123
    runs-on: ubuntu-latest
"""
    wf = load_github_actions_workflow(yaml_content)
    assert wf.name == "Sample Pipeline"

    graph = wf.to_graph()
    assert set(graph.nodes.keys()) == {"lint", "test", "deploy", "extra"}
    assert graph.predecessors("test") == ["lint"]
    assert graph.predecessors("deploy") == ["lint", "test"]
    assert graph.predecessors("extra") == []

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

    # Also test passing file path as a string
    wf_from_str = load_github_actions_workflow(str(workflow_file))
    assert wf_from_str.name == "Minimal CI"


def test_noop_action() -> None:
    from hexaflow.adapters.loaders.github_actions import _noop_action

    res = _noop_action(None)
    assert res == "ok"


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


def test_load_github_actions_job_not_mapping() -> None:
    yaml_text = """
name: Bad Job
jobs:
  invalid_job: "not a dictionary"
"""
    with pytest.raises(ValueError, match="job 'invalid_job' must be a mapping"):
        load_github_actions_workflow(yaml_text)


def test_github_actions_yaml_loader_preserves_strings() -> None:
    import yaml

    from hexaflow.adapters.loaders.github_actions import GitHubActionsYamlLoader

    sample_yaml = """
name: CI
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      FEATURE_FLAG: on
      LOG_LEVEL: off
      ACCEPT: yes
      DECLINE: no
      IS_ACTIVE: true
      IS_DISABLED: false
"""
    data = yaml.load(sample_yaml, Loader=GitHubActionsYamlLoader)
    assert "on" in data
    assert isinstance(data["on"], dict)

    job_env = data["jobs"]["test"]["env"]
    assert job_env["FEATURE_FLAG"] == "on"
    assert job_env["LOG_LEVEL"] == "off"
    assert job_env["ACCEPT"] == "yes"
    assert job_env["DECLINE"] == "no"
    assert job_env["IS_ACTIVE"] is True
    assert job_env["IS_DISABLED"] is False
