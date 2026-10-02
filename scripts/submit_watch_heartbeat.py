from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.watch_ledger import validate_watch_run  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("watch heartbeat payload must be a JSON object")
    errors = validate_watch_run(value)
    if errors:
        raise ValueError("; ".join(errors))
    return value


def dispatch_heartbeat_workflow(
    payload: dict[str, Any],
    *,
    repo: str,
    workflow: str = "watch-heartbeat-ingest.yml",
    source_ref: str = "add/pages-repo-default",
    ledger_branch: str = "ops/watch-ledger",
    gh: str = "gh",
) -> dict[str, Any]:
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")
    command = [
        gh,
        "workflow",
        "run",
        workflow,
        "--repo",
        repo,
        "--ref",
        source_ref,
        "-f",
        f"heartbeat_json_b64={encoded}",
        "-f",
        f"ledger_branch={ledger_branch}",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "schema_version": "bluefern.watch_heartbeat_submission.v1",
        "submitted_at": _utc_now(),
        "status": "WORKFLOW_DISPATCHED" if completed.returncode == 0 else "FAILED",
        "workflow": workflow,
        "source_ref": source_ref,
        "ledger_branch": ledger_branch,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
        "schedule_mutated": False,
        "direct_ledger_mutation": False,
        "fallback_branch_authoritative": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit a watch heartbeat to the repository-side ingestion workflow.")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--repo", default="RedGarland/the-blue-fern-co-dispatches")
    parser.add_argument("--workflow", default="watch-heartbeat-ingest.yml")
    parser.add_argument("--source-ref", default="add/pages-repo-default")
    parser.add_argument("--ledger-branch", default="ops/watch-ledger")
    parser.add_argument("--gh", default="gh")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dispatch_heartbeat_workflow(
            _load_payload(args.payload),
            repo=args.repo,
            workflow=args.workflow,
            source_ref=args.source_ref,
            ledger_branch=args.ledger_branch,
            gh=args.gh,
        )
    except (OSError, ValueError) as exc:
        result = {
            "schema_version": "bluefern.watch_heartbeat_submission.v1",
            "submitted_at": _utc_now(),
            "status": "FAILED",
            "error": str(exc),
            "schedule_mutated": False,
            "direct_ledger_mutation": False,
            "fallback_branch_authoritative": False,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "WORKFLOW_DISPATCHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
