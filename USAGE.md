# Hexaqual Quality Suite & CLI Catalog (`hexaflow` / `hf`)

> Canonical developer command reference and CLI catalog automatically generated from the complete command hierarchy.

---

## 🏛️ Dogfooding Hexagonal Architecture

`hexaqual` is built strictly according to Hexagonal Architecture design principles:
- **`domain/`**: Pure data contracts (`PrSummary`, `CheckRunFinding`, `ReviewThread`, `OutputFormat`).
- **`ports/`**: Clean interface contracts (`GitHubApiPort`, `GovernancePresenterPort`, `ToolRunnerPort`, `PyPiClientPort`).
- **`adapters/`**: Pluggable presenters (`rich`, `json`, `plain`), subcommands, and runners.
- **`cli/`**: Unified Typer CLI driving adapter (`hexaqual`).
- **`infra/`**: Command dispatchers, handlers, and execution orchestration.
- **`utils/`**: Workspace discovery, AST parsing, and package graph resolvers.

---

## ⚙️ Output Presentation Formats

All inspection commands support `--format / -f`:
- **`auto` (default)**: Automatically outputs interactive ANSI tables/panels when attached to a terminal TTY, and switches to clean, tab-delimited plain text (`TSV`) when standard output is piped into Unix filters (`grep`, `awk`, `cut`, `xargs`, etc.).
- **`rich`**: Interactive Rich tables and color-coded status badges.
- **`json`**: Structured JSON for automation, CI scripts, and AI agents.
- **`plain`**: Machine-readable TSV stream.

---

## 🚀 Unified Root Entrypoint (`hexaflow` (alias: `hf`))

```text
Usage: hexaflow [OPTIONS] COMMAND [ARGS]...

 Lightweight, embeddable Python workflow engine with checkpointed resumption.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ status   Display execution status, stages, and step checkpoints.             │
│ inspect  Inspect payloads or error tracebacks for a step.                    │
│ run      Execute a workflow definition locally.                              │
│ resume   Resume suspended workflow from latest checkpoints.                  │
│ restart  Restart a workflow execution run from the beginning.                │
│ abort    Abort an active or suspended workflow, unwinding any step           │
│          compensations.                                                      │
│ graph    Workflow DAG inspection, analysis, and visualization commands.      │
╰──────────────────────────────────────────────────────────────────────────────╯
```

---

## 🛠️ Complete Subcommand Tree Reference

### `hexaflow abort`

```text
Usage: hexaflow abort [OPTIONS] {run_id} {target}

 Abort an active or suspended workflow, unwinding any step compensations.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    run_id      <str>  Run ID of the workflow to abort. [required]          │
│ *    target      <str>  Workflow target (e.g. 'pipeline.py:my_workflow').    │
│                         [required]                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --db          <str>  Path to SQLite state database.                          │
│                      [default: .hexaflow/state.db]                           │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `hexaflow graph`

```text
Usage: hexaflow graph [OPTIONS] COMMAND [ARGS]...

 Workflow DAG inspection, analysis, and visualization commands.

╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --help          Show this message and exit.                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ render  Render a workflow dependency graph to Mermaid, DOT, ASCII, or JSON.  │
│ info    Display topological metrics and critical path.                       │
╰──────────────────────────────────────────────────────────────────────────────╯
```

#### `hexaflow graph info`

```text
Usage: hexaflow graph info [OPTIONS] {target}

 Display topological metrics and critical path.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    target      <str>  Workflow target (e.g. 'pipeline.py:wf',              │
│                         'pipeline.py:factory', or                            │
│                         '.github/workflows/ci.yml').                         │
│                         [required]                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --params  -p      <str>  JSON string of keyword parameters passed to         │
│                          workflow factory functions.                         │
│ --help                   Show this message and exit.                         │
╰──────────────────────────────────────────────────────────────────────────────╯
```

#### `hexaflow graph render`

```text
Usage: hexaflow graph render [OPTIONS] {target}

 Render a workflow dependency graph to Mermaid, DOT, ASCII, or JSON.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    target      <str>  Workflow target (e.g. 'pipeline.py:wf',              │
│                         'pipeline.py:factory', or                            │
│                         '.github/workflows/ci.yml').                         │
│                         [required]                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --format         -f      <str>   Renderer format ('mermaid', 'dot', 'ascii', │
│                                  'json').                                    │
│                                  [default: mermaid]                          │
│ --dry-run                        Execute dry-run simulation in memory and    │
│                                  render materialized dynamic DAG.            │
│ --params         -p      <str>   JSON string of keyword parameters passed to │
│                                  workflow factory functions.                 │
│ --output         -o      <path>  Optional destination file path to write     │
│                                  rendered output to.                         │
│ --summary        -s              Append rendered diagram to                  │
│                                  $GITHUB_STEP_SUMMARY markdown.              │
│ --critical-path  -c              Visually highlight the critical path in the │
│                                  rendered diagram.                           │
│ --help                           Show this message and exit.                 │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `hexaflow inspect`

```text
Usage: hexaflow inspect [OPTIONS] {run_id} {step_name}

 Inspect payloads or error tracebacks for a step.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    run_id         <str>  Workflow execution run ID. [required]             │
│ *    step_name      <str>  Step identifier to inspect. [required]            │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --db          <str>  Path to SQLite state database.                          │
│                      [default: .hexaflow/state.db]                           │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `hexaflow restart`

```text
Usage: hexaflow restart [OPTIONS] {run_id} {target}

 Restart a workflow execution run from the beginning.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    run_id      <str>  Run ID of the workflow to restart. [required]        │
│ *    target      <str>  Workflow target (e.g. 'pipeline.py:my_workflow').    │
│                         [required]                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --db          <str>  Path to SQLite state database.                          │
│                      [default: .hexaflow/state.db]                           │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `hexaflow resume`

```text
Usage: hexaflow resume [OPTIONS] {run_id} {target}

 Resume suspended workflow from latest checkpoints.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    run_id      <str>  Run ID of the suspended workflow. [required]         │
│ *    target      <str>  Workflow target (e.g. 'pipeline.py:my_workflow').    │
│                         [required]                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --db          <str>  Path to SQLite state database.                          │
│                      [default: .hexaflow/state.db]                           │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `hexaflow run`

```text
Usage: hexaflow run [OPTIONS] {target}

 Execute a workflow definition locally.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    target      <str>  Workflow target (e.g. 'pipeline.py:my_workflow').    │
│                         [required]                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --db          <str>  Path to SQLite state database.                          │
│                      [default: .hexaflow/state.db]                           │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### `hexaflow status`

```text
Usage: hexaflow status [OPTIONS] {run_id}

 Display execution status, stages, and step checkpoints.

╭─ Arguments ──────────────────────────────────────────────────────────────────╮
│ *    run_id      <str>  Workflow execution run ID. [required]                │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─ Options ────────────────────────────────────────────────────────────────────╮
│ --db          <str>  Path to SQLite state database.                          │
│                      [default: .hexaflow/state.db]                           │
│ --help               Show this message and exit.                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```
