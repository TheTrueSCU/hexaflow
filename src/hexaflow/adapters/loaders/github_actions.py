"""GitHub Actions workflow YAML parser and adapter loader.

Notes/Architectural Intent:
    Translates GitHub Actions workflow definitions into native hexaflow Workflow DAGs.
    Converts jobs and their `needs:` dependencies into workflow steps and dependency edges,
    enabling visualization, topological analysis, and critical path projection directly
    from CI workflow configuration files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from hexaflow.dsl.builder import Workflow


class GitHubActionsYamlLoader(yaml.SafeLoader):
    """YAML safe loader that preserves unquoted 'on', 'off', 'yes', 'no' as strings.

    Notes/Architectural Intent:
        GitHub Actions workflows use 'on:' as a top-level mapping key for triggers.
        Standard PyYAML 1.1 coerces unquoted 'on', 'off', 'yes', 'no' into booleans.
        This loader removes boolean resolution for these tokens to preserve triggers
        and scalar values correctly while retaining standard 'true'/'false' booleans.
    """


# Isolate custom resolvers on the loader subclass to avoid mutating yaml.SafeLoader
GitHubActionsYamlLoader.yaml_implicit_resolvers = {
    k: list(v) for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

for _ch in ("o", "O", "y", "Y", "n", "N"):
    GitHubActionsYamlLoader.yaml_implicit_resolvers[_ch] = [
        (tag, regexp)
        for tag, regexp in GitHubActionsYamlLoader.yaml_implicit_resolvers[_ch]
        if tag != "tag:yaml.org,2002:bool"
    ]

_TRUE_FALSE_REGEX = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$", re.X)
for _ch in ("t", "T", "f", "F"):
    GitHubActionsYamlLoader.yaml_implicit_resolvers[_ch] = [
        ("tag:yaml.org,2002:bool", _TRUE_FALSE_REGEX)
        if tag == "tag:yaml.org,2002:bool"
        else (tag, regexp)
        for tag, regexp in GitHubActionsYamlLoader.yaml_implicit_resolvers[_ch]
    ]


def _noop_action(ctx: Any) -> str:
    """Default no-op action for parsed CI steps.

    Args:
        ctx: Step execution context.

    Returns:
        Status string 'ok'.
    """
    return "ok"


def _resolve_raw_yaml_and_name(source: str | Path) -> tuple[str, str]:
    """Read raw YAML content and default workflow name from source.

    Args:
        source: File path or raw YAML string.

    Returns:
        Tuple of (raw_text, default_workflow_name).

    Raises:
        FileNotFoundError: If path cannot be found or is not a file.
    """
    path: Path | None = None
    if isinstance(source, Path):
        path = source
    elif isinstance(source, str) and (source.endswith((".yml", ".yaml")) or "\n" not in source):
        candidate = Path(source)
        if candidate.exists():
            path = candidate
        elif not source.startswith(("{", "name:", "on:", "jobs:")) and "\n" not in source:
            raise FileNotFoundError(f"Workflow file '{source}' was not found.")

    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"Path '{path}' is not a regular file.")
        return path.read_text(encoding="utf-8"), path.stem

    return str(source), "github_workflow"


def _extract_job_needs(job_info: dict[str, Any]) -> list[str]:
    """Extract list of dependency job names from job definition.

    Args:
        job_info: Job dictionary mapping.

    Returns:
        List of upstream job name strings.
    """
    raw_needs = job_info.get("needs", [])
    if isinstance(raw_needs, str):
        return [raw_needs]
    if isinstance(raw_needs, list):
        return [str(n) for n in raw_needs]
    return []


def load_github_actions_workflow(source: str | Path) -> Workflow:
    """Parse a GitHub Actions workflow YAML file or string and construct a Workflow.

    Args:
        source: Path to the workflow YAML file, or raw YAML string content.

    Returns:
        A hexaflow Workflow instance modeling the parsed jobs and dependencies.

    Raises:
        FileNotFoundError: If source is a path string that does not exist.
        ValueError: If YAML syntax is invalid or lacks a valid 'jobs' mapping.

    Notes/Architectural Intent:
        Reads job dependencies from the `needs:` directive of each job in the GitHub Actions
        workflow and builds a corresponding hexaflow step with `depends_on`.
    """
    raw_text, wf_default_name = _resolve_raw_yaml_and_name(source)

    try:
        data = yaml.load(raw_text, Loader=GitHubActionsYamlLoader)
    except Exception as exc:
        raise ValueError(f"Failed to parse GitHub Actions YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Invalid GitHub Actions workflow: root must be a YAML mapping.")

    jobs_data = data.get("jobs")
    if not isinstance(jobs_data, dict) or not jobs_data:
        raise ValueError("Invalid GitHub Actions workflow: missing or empty 'jobs' mapping.")

    wf_name = str(data.get("name") or wf_default_name)
    wf = Workflow(wf_name)

    for job_id, job_info in jobs_data.items():
        if not isinstance(job_info, dict):
            raise ValueError(
                f"Invalid GitHub Actions workflow: job '{job_id}' must be a mapping, got {type(job_info).__name__}."
            )
        needs = _extract_job_needs(job_info)
        wf.step(str(job_id), depends_on=needs)(_noop_action)

    return wf


__all__ = [
    "GitHubActionsYamlLoader",
    "load_github_actions_workflow",
]
