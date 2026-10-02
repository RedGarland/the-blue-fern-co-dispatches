from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.watch_ledger import (  # noqa: E402
    WatchLedgerError,
    commit_and_push_watch_ledger,
    persist_watch_run,
    prepare_watch_ledger_checkout,
    validate_watch_run,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload:
        value = json.loads(args.payload.read_text(encoding="utf-8"))
    elif args.payload_json:
        value = json.loads(args.payload_json)
    elif args.payload_base64:
        value = json.loads(base64.b64decode(args.payload_base64.encode("ascii")).decode("utf-8"))
    else:
        raise WatchLedgerError("one of --payload, --payload-json, or --payload-base64 is required")
    if not isinstance(value, dict):
        raise WatchLedgerError("watch heartbeat payload must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append a scheduled watch heartbeat to the authoritative ops/watch-ledger.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--payload", type=Path, help="Path to the heartbeat JSON payload.")
    source.add_argument("--payload-json", help="Heartbeat JSON payload.")
    source.add_argument("--payload-base64", help="Base64-encoded heartbeat JSON payload.")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--prepare-branch", default="ops/watch-ledger")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--commit-message", default="Append scheduled watch heartbeat")
    parser.add_argument("--commit", action="store_true", help="Commit the appended ledger entry.")
    parser.add_argument("--push", action="store_true", help="Push the ledger commit to --prepare-branch. Requires --commit.")
    parser.add_argument("--no-fetch", action="store_true", help="Skip branch fast-forward preparation.")
    return parser


def ingest_watch_heartbeat(
    payload: dict[str, Any],
    *,
    repo_root: Path,
    prepare_branch: str = "ops/watch-ledger",
    remote: str = "origin",
    commit: bool = False,
    push: bool = False,
    commit_message: str = "Append scheduled watch heartbeat",
    no_fetch: bool = False,
) -> dict[str, Any]:
    errors = validate_watch_run(payload)
    if errors:
        raise WatchLedgerError("; ".join(errors))
    if push and not commit:
        raise WatchLedgerError("--push requires --commit")
    if commit and not push:
        raise WatchLedgerError("--commit requires --push for authoritative heartbeat persistence")
    if commit and not no_fetch:
        prepare_watch_ledger_checkout(repo_root, branch=prepare_branch, remote=remote)
    persisted = persist_watch_run(repo_root, payload)
    result: dict[str, Any] = {
        "schema_version": "bluefern.watch_heartbeat_ingest_result.v1",
        "ingested_at": _utc_now(),
        "status": persisted["status"],
        "ledger_path": persisted["path"],
        "commit": None,
        "push_succeeded": False,
        "schedule_mutated": False,
        "authoritative": True,
    }
    if commit:
        commit_sha = commit_and_push_watch_ledger(
            repo_root,
            paths=[persisted["path"]],
            message=commit_message,
            remote=remote,
            branch=prepare_branch,
        )
        result["commit"] = commit_sha
        result["push_succeeded"] = bool(push and (commit_sha or persisted["status"] == "SAFE_NO_OP"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = ingest_watch_heartbeat(
            _load_payload(args),
            repo_root=args.repo_root.resolve(),
            prepare_branch=args.prepare_branch,
            remote=args.remote,
            commit=args.commit,
            push=args.push,
            commit_message=args.commit_message,
            no_fetch=args.no_fetch,
        )
    except (OSError, ValueError, WatchLedgerError) as exc:
        result = {
            "schema_version": "bluefern.watch_heartbeat_ingest_result.v1",
            "ingested_at": _utc_now(),
            "status": "FAILED",
            "classification": "heartbeat_persistence_failed",
            "error": str(exc),
            "schedule_mutated": False,
            "authoritative": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
