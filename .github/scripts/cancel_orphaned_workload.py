"""Best-effort cancellation of orphaned Hexaqueue workloads from CI cleanup steps.

Notes/Architectural Intent:
    Invoked by GitHub Actions ``if: cancelled() || failure()`` hooks prior to
    closing the cluster SSH tunnel. Reads an active run or test session ID
    written to ``$RUNNER_TEMP`` by ``hexaqual`` or ``pytest-hexaqueue`` and
    issues an HTTP POST cancellation request using only the Python standard library.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _build_cancel_request(
    kind: str,
    workload_id: str,
    controller_url: str,
) -> urllib.request.Request:
    """Build an authenticated HTTP POST cancellation Request for ``kind``.

    Args:
        kind: Workload type (``"run"`` or ``"test-session"``).
        workload_id: Target remote run ID or distributed test session ID.
        controller_url: Base HTTP/HTTPS URL of the cluster controller.

    Returns:
        Configured ``urllib.request.Request`` targeting the cancel endpoint.

    Raises:
        ValueError: If ``controller_url`` uses a non-HTTP(S) scheme.
    """
    base_url = controller_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https"):
        msg = f"Unsupported controller URL scheme: {parsed.scheme!r}"
        raise ValueError(msg)

    encoded_id = urllib.parse.quote(workload_id, safe="")
    if kind == "run":
        url = f"{base_url}/v1/runs/{encoded_id}/cancel"
    else:
        url = f"{base_url}/api/v1/test/runs/{encoded_id}/cancel"

    headers: dict[str, str] = {"Accept": "application/json"}
    bearer = (
        os.environ.get("HEXAQUEUE_CLUSTER_TOKEN") or os.environ.get("HEXAQUEUE_API_TOKEN") or ""
    ).strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    if kind == "run":
        headers["X-Hexaqueue-Elevate"] = "true"
        admin_token = os.environ.get("HEXAQUEUE_ADMIN_TOKEN", "").strip()
        if admin_token:
            headers["X-Hexaqueue-Admin-Token"] = admin_token

    return urllib.request.Request(url, data=b"", headers=headers, method="POST")


def cancel_from_file(
    kind: str,
    id_file: Path,
    controller_url: str,
    timeout: float = 5.0,
) -> bool:
    """Read workload ID from ``id_file`` and post a best-effort cancel request.

    Args:
        kind: Workload type (``"run"`` or ``"test-session"``).
        id_file: File storing the active workload identifier.
        controller_url: Base HTTP/HTTPS URL of the cluster controller.
        timeout: Socket timeout in seconds for the HTTP POST call.

    Returns:
        True if cancellation succeeded or workload was already absent (404),
        False otherwise.

    Notes/Architectural Intent:
        Leaves ``id_file`` in place on transient errors for diagnostics while
        unlinking it once cancellation is acknowledged (200/202/204/404).
    """
    if not id_file.is_file():
        sys.stderr.write(f"No active {kind} ID file at {id_file}; nothing orphaned to cancel.\n")
        return True

    workload_id = id_file.read_text(encoding="utf-8").strip()
    if not workload_id:
        sys.stderr.write(f"Active {kind} ID file {id_file} is empty; skipping.\n")
        return True

    req = _build_cancel_request(
        kind=kind,
        workload_id=workload_id,
        controller_url=controller_url,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
        if code in (200, 202, 204):
            id_file.unlink(missing_ok=True)
            sys.stderr.write(f"Cancelled orphaned {kind} '{workload_id}' (HTTP {code}).\n")
            return True
    except urllib.error.HTTPError as err:
        if err.code == 404:
            id_file.unlink(missing_ok=True)
            sys.stderr.write(
                f"Orphaned {kind} '{workload_id}' already absent on controller (HTTP 404).\n"
            )
            return True
        sys.stderr.write(f"Warning: cancel for {kind} '{workload_id}' returned HTTP {err.code}.\n")
        return False
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        sys.stderr.write(f"Warning: failed to cancel {kind} '{workload_id}': {exc}.\n")
        return False

    sys.stderr.write(f"Warning: unexpected cancel status for {kind} '{workload_id}'.\n")
    return False


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for best-effort CI orphan cancellation.

    Args:
        argv: Optional command-line arguments override for testing.

    Returns:
        Always returns ``0`` so cleanup hooks never mask real step outcomes.
    """
    parser = argparse.ArgumentParser(
        description="Cancel an orphaned Hexaqueue run or test session on CI failure/cancel."
    )
    parser.add_argument(
        "--kind",
        choices=("run", "test-session"),
        required=True,
        help="Workload type represented in --id-file.",
    )
    parser.add_argument(
        "--id-file",
        required=True,
        help="Path to the temporary file containing the active workload ID.",
    )
    parser.add_argument(
        "--controller-url",
        default=os.environ.get("HEXAQUEUE_CONTROLLER_URL", "http://127.0.0.1:8000"),
        help="Base HTTP URL of the tunneled cluster controller.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout in seconds.",
    )
    args = parser.parse_args(argv)

    try:
        cancel_from_file(
            kind=args.kind,
            id_file=Path(args.id_file),
            controller_url=args.controller_url,
            timeout=args.timeout,
        )
    except Exception as exc:
        sys.stderr.write(f"Warning: orphan cleanup skipped due to error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
