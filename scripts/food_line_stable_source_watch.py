"""Run the first bounded Food source-watch batch through the stable ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_discovery_expansion import build_food_line_discovery_query_plan  # noqa: E402
from scripts.food_line_continuation_ledger import (  # noqa: E402
    ContinuationLedgerError,
    checkpoint_in_flight_outcome,
    load_ledger,
    stable_query_id,
)
from scripts.run_food_line_continuation import execute_stable_claimed_batch  # noqa: E402
from scripts.run_food_line_discovery_expansion import (  # noqa: E402
    _export_agent_inbox,
    _export_allowed,
    _write_state_files,
)


DIAGNOSTIC_FAILURE_CLASSES = frozenset(
    {
        "blocked_401",
        "blocked_403",
        "not_found_404",
        "timeout",
        "paywall",
        "script_blocked",
        "parse_failure",
        "empty_response",
        "blocked_domain_mismatch",
        "blocked_listing_url",
        "service_unavailable_503",
        "tls_error",
        "connection_error",
        "fetch_failed",
        "unknown",
    }
)
MAX_DIAGNOSTIC_ROWS = 2048
MAX_DIAGNOSTIC_TEXT = 512
MAX_DIAGNOSTIC_COUNT = 1_000_000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_text(value: Any, *, limit: int) -> tuple[str, bool]:
    if value in (None, ""):
        return "", True
    if type(value) is not str or len(value) > limit:
        return "", False
    return value.strip().lower(), True


def _diagnostic_failure_class(row: dict[str, Any]) -> tuple[str, bool]:
    status, status_valid = _bounded_text(row.get("direct_fetch_status"), limit=64)
    if not status_valid:
        return "unknown", False
    if status:
        return (status, True) if status in DIAGNOSTIC_FAILURE_CLASSES else ("unknown", False)
    error, error_valid = _bounded_text(
        row.get("query_error") or row.get("direct_fetch_error"),
        limit=MAX_DIAGNOSTIC_TEXT,
    )
    if not error_valid:
        return "unknown", False
    if not error:
        return "unknown", True
    anchored_prefixes = (
        ("httperror: http error 401", "blocked_401"),
        ("httperror: http error 403", "blocked_403"),
        ("httperror: http error 404", "not_found_404"),
        ("httperror: http error 503", "service_unavailable_503"),
        ("sslerror:", "tls_error"),
        ("ssl:", "tls_error"),
        ("certificate_verify_failed:", "tls_error"),
        ("connectionerror:", "connection_error"),
        ("connectionreseterror:", "connection_error"),
        ("timeouterror:", "timeout"),
        ("timeout:", "timeout"),
        ("parseerror:", "parse_failure"),
        ("emptyresponseerror:", "empty_response"),
    )
    for prefix, label in anchored_prefixes:
        if prefix.startswith("httperror: http error "):
            if error == prefix or error.startswith((prefix + ":", prefix + " ")):
                return label, True
        elif error.startswith(prefix):
            return label, True
    return "unknown", False


def _diagnostic_failure_counts(rows: Any) -> tuple[dict[str, int], dict[str, Any]]:
    counts: dict[str, int] = {}
    rejected_count = 0
    truncated = False
    saturated = False
    if not isinstance(rows, list):
        return {}, {"failure_class_input_rejected": True}
    for index, row in enumerate(rows):
        if index >= MAX_DIAGNOSTIC_ROWS:
            truncated = True
            break
        if not isinstance(row, dict):
            rejected_count += 1
            continue
        raw_status = row.get("direct_fetch_status")
        raw_error = row.get("query_error") or row.get("direct_fetch_error")
        if raw_status in (None, "") and raw_error in (None, ""):
            continue
        status, status_valid = _bounded_text(raw_status, limit=64)
        if status_valid and status in {"", "ok", "manual_fallback"} and raw_error in (None, ""):
            continue
        label, valid = _diagnostic_failure_class(row)
        if not valid:
            rejected_count += 1
            continue
        previous = counts.get(label, 0)
        if previous >= MAX_DIAGNOSTIC_COUNT:
            saturated = True
        else:
            counts[label] = previous + 1
    return dict(sorted(counts.items())), {
        "failure_class_rejected_count": rejected_count,
        "failure_class_counts_truncated": truncated,
        "failure_class_counts_saturated": saturated,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve()
    directory = Path(args.continuation_run_directory).resolve()
    source = SRC / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    plan = json.loads((directory / "continuation-query-plan.json").read_text(encoding="utf-8"))
    binding = {
        "edition_date": args.edition_date,
        "run_id": args.run_id,
        "source_identity": args.source_identity,
        "source_module_sha256": _sha256(source),
        "config_sha256": _sha256(config),
        "plan_sha256": plan["plan_sha256"],
    }
    state = load_ledger(directory, binding=binding)
    claimed = list(state.get("in_flight_query_ids") or [])
    if not claimed:
        raise ContinuationLedgerError("stable source-watch child requires a durable in-flight claim")
    if len(claimed) > args.max_queries:
        raise ContinuationLedgerError("stable source-watch claim exceeds the bounded batch size")
    rows_by_id = dict(zip(plan["query_ids"], plan["query_rows"], strict=True))
    rows = [rows_by_id[query_id] for query_id in claimed]

    def checkpoint(query_id: str, outcome: dict[str, Any]) -> None:
        checkpoint_in_flight_outcome(directory, binding=binding, query_id=query_id, outcome=outcome)

    runtime_deadline = datetime.now(timezone.utc) + timedelta(minutes=max(1.0, float(args.max_run_minutes)))
    task_started_at = _utc_now()
    aggregate = execute_stable_claimed_batch(
        root,
        args.edition_date,
        rows,
        checkpoint,
        runtime_deadline=runtime_deadline,
    )
    task_completed_at = _utc_now()
    failure_counts, failure_metadata = _diagnostic_failure_counts(aggregate.get("query_rows"))
    full_plan = build_food_line_discovery_query_plan(root, args.edition_date)
    result: dict[str, Any] = {
        **aggregate,
        "ok": True,
        "status": "incomplete_capped" if len(full_plan) > len(rows) else "completed",
        "edition_date": args.edition_date,
        "run_id": args.run_id,
        "query_plan_available_count": len(full_plan),
        "query_plan_bounded_count": len(rows),
        "queries_deferred": max(0, len(full_plan) - len(rows)),
        "resumable": False,
        "candidate_count": len(aggregate["candidates"]),
        "public_claim_eligible_count": 0,
        "publication_attempted": False,
        "public_side_effects": {},
        "execution_mode": "collection_only" if args.collection_only else "export_enabled",
        "task_started_at": task_started_at,
        "task_completed_at": task_completed_at,
        "fetch_failure_count_by_type": failure_counts,
        **failure_metadata,
    }
    if not args.collection_only and _export_allowed(root, args.edition_date, args.run_id, result):
        export = _export_agent_inbox(
            root,
            edition_date=args.edition_date,
            run_id=args.run_id,
            result=result,
            agent_inbox_dir=args.agent_inbox_dir,
        )
        result.update(
            {
                "agent_export": export,
                "agent_export_path": export["path"],
                "agent_export_sha256": export["sha256"],
            }
        )
    wrapped = _write_state_files(
        root,
        args.edition_date,
        args.run_id,
        result,
        resume_count=0,
        export_requested=not args.collection_only,
    )
    return {
        **wrapped,
        "schema": "food_line_stable_source_watch_receipt_v1",
        "status": wrapped.get("status"),
        "ok": bool(wrapped.get("ok")),
        "edition_date": args.edition_date,
        "run_id": args.run_id,
        "query_rows": aggregate["query_rows"],
        "candidates": aggregate["candidates"],
        "execution_mode": result["execution_mode"],
        "publication_attempted": False,
        "public_side_effects": {},
        "child_outcome_classification": (
            str(wrapped.get("status") or "collection_failure")
            if not wrapped.get("ok")
            else "success"
        ),
        "task_started_at": task_started_at,
        "task_completed_at": task_completed_at,
        "fetch_failure_count_by_type": failure_counts,
        **failure_metadata,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--date", dest="edition_date", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--continuation-run-directory", required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--max-queries", type=int, required=True)
    parser.add_argument("--max-run-minutes", type=float, default=30.0)
    parser.add_argument("--agent-inbox-dir")
    parser.add_argument("--collection-only", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        print(json.dumps(run(build_parser().parse_args()), sort_keys=True))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "status": "child_process_failure", "error": str(exc)}), file=sys.stdout)
        raise SystemExit(1)
