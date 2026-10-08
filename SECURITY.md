# Security Policy

## Supported Versions

Only the **latest minor release** of Hexaflow receives security fixes.
Older minor versions are not backported.

| Package | Supported |
|---|---|
| `hexaflow` (latest minor) | ✅ |
| Any older minor version | ❌ |

## Reporting a Vulnerability

> [!CAUTION]
> **Do not open a public GitHub issue for security vulnerabilities.** Public disclosure before a fix is available puts all users at risk.

Please report vulnerabilities using **GitHub's private vulnerability reporting**:

👉 [Open a private security advisory](https://github.com/TheTrueSCU/hexaflow/security/advisories/new)

Your report will be visible only to repository maintainers until a coordinated disclosure is agreed upon. Provide as much detail as possible:

- Affected package(s) and version(s)
- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept (PoC)
- Any suggested mitigations you have identified

## Scope

### In scope

- The core `hexaflow` orchestrator, engine adapters, DSL parser, and CLI binders at their latest minor release
- Third-party dependencies **directly introduced into a user's environment by Hexaflow** (i.e. listed in Hexaflow's own `dependencies` in `pyproject.toml`)
- Workflow DAG integrity, state transitions, step timeout barriers, and retry backoff policies

### Out of scope

- Arbitrary code or business logic executed inside step callables authored by users
- Third-party libraries not in Hexaflow's direct dependency graph
- Denial-of-service issues requiring physical access to the host
- Reports against unsupported older minor versions

## Response Timeline & SLA

| Milestone | Target |
|---|---|
| Initial Acknowledgement of report | Within **48 hours** |
| Triage and severity assessment | Within **7 business days** of acknowledgement |
| Coordinated fix & CVE assignment | Agreed with reporter; typically within **30 to 60 days** for critical issues |

We will keep you informed of progress at each milestone. If you believe a critical issue warrants an accelerated timeline, please state so in your report.

## Credit & Vulnerability Acknowledgment

We believe in giving credit where credit is due. Unless you request to remain anonymous, we will publicly credit you in our:
1. Release notes and `CHANGELOG.md`
2. GitHub Security Advisory release page
3. CVE metadata / attribution records

## Safe Harbour

Hexaflow maintainers commit to working in **good faith** with security researchers who:

- Report vulnerabilities privately before any public disclosure
- Avoid accessing, modifying, or destroying data that does not belong to them
- Do not degrade the availability of Hexaflow services or infrastructure
- Do not violate the privacy of other users

Researchers who follow these principles will not be subject to legal action related to their research. We will work with you to understand and resolve the issue promptly.

## Security Assurance Case

Hexaflow maintains a formal security assurance case to demonstrate why its security requirements and architectural guarantees are met.

### 1. Threat Model & Asset Identification
* **Primary Assets**: DAG cycle validation and barrier enforcement, deterministic state transitions (`PENDING` -> `RUNNING` -> `COMPLETED`/`FAILED`/`SKIPPED`), step timeout and cancellation isolation, and backoff jitter distribution.
* **Threat Vectors**:
  * *Infinite Execution / Resource Starvation*: Malicious or circular DAG definitions causing unbounded loops.
  * *Silent Error Suppression / State Corruption*: Exception handling suppressing runtime cancellations or masking fatal step failures.
  * *Thundering Herd Cascades*: Retry policies with deterministic lockstep causing downstream service exhaustion.
  * *Supply Chain Vulnerabilities*: Vulnerabilities introduced via third-party dependencies.

### 2. Trust Boundaries
* **DAG Definition Boundary**: Workflow definitions are strictly validated upfront at compilation time for acyclicity, dependency validity, and duplicate step names prior to execution.
* **Engine Execution Boundary**: Step execution occurs within explicit timeout envelopes; exceptions are trapped into structured StepResult objects without masking base process interrupts.
* **Dynamic CLI Boundary**: Skip switches and option injectors strictly validate parameter formats and sanitize flag names against workflow definitions.

### 3. Secure Design Principles Applied
* **Deterministic DAG Validation**: Cyclical dependencies or invalid stage references are rejected immediately (`InvalidWorkflowDAGError`).
* **Jittered Backoff by Default**: Exponential and linear backoff algorithms support randomized full jitter to prevent synchronized retry spikes.
* **Hexagonal Architectural Separation**: Pure business and domain workflow logic is fully isolated from async engines and storage ports.

### 4. Implementation Security Weakness Countermeasures
* **Automated Static Analysis (SAST)**: Enforced via `Ruff` (security rules `S`), `ty check`, and GitHub `CodeQL`.
* **Automated Dependency Auditing (SCA)**: Continuous `Dependabot` vulnerability monitoring and pre-commit `pip-audit` scans.
* **Secret Detection**: `detect-secrets` hook in pre-commit prevents accidental credential check-ins.

---

## Preferred Disclosure Language

Please submit all reports in **English** to ensure the fastest possible triage and response.

---

*This policy follows coordinated disclosure best practices and OpenSSF Gold standards. Last reviewed: 2026-10.*
