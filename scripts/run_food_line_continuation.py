from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_discovery_expansion import (  # noqa: E402
    build_food_line_discovery_query_plan,
    run_food_line_discovery_expansion,
)
from scripts.food_line_continuation_ledger import (  # noqa: E402
    ContinuationLedgerError,
    build_plan,
    continue_ledger,
    gate_summary,
    load_ledger,
    prepare_ledger,
    source_stage_handoff_assessment,
    stable_query_id,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_state_path(root: Path, edition_date: str, run_id: str) -> Path:
    return root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id / "run-state.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    from scripts.food_line_continuation_ledger import _write_json_atomic as write_json_atomic

    write_json_atomic(path, value)


def _aggregate_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for query_id in state.get("planned_query_ids") or []:
        outcome = state.get("query_results", {}).get(query_id, {})
        for candidate in outcome.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            identity = str(candidate.get("candidate_id") or "")
            key = f"candidate:{identity}" if identity else "payload:{hashlib.sha256(_canonical(candidate)).hexdigest()}"
            if key in seen:
                continue
            seen.add(key)
            candidates.append(dict(candidate))
    return candidates


def commit_source_stage_handoff(
    root: Path,
    *,
    edition_date: str,
    run_id: str,
    continuation_directory: Path,
    ledger_state: dict[str, Any],
    source_identity: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Commit a qualifying stable-ID run into the existing intake contract.

    The handoff is intentionally private source-stage state: it writes the
    normal run-state and an agent export consumed by current intake, while
    keeping publication/release flags closed.  It is only callable after the
    ledger assessment proves every planned ID is terminal and allowed.
    """
    assessment = source_stage_handoff_assessment(ledger_state)
    if not assessment["intake_eligible"]:
        raise ContinuationLedgerError(
            "source-stage handoff blocked: " + str(assessment["reason"] or "coverage is not intake-eligible")
        )
    state_path = _run_state_path(root, edition_date, run_id)
    if not state_path.is_file():
        raise ContinuationLedgerError(f"expected legacy run-state is missing: {state_path}")
    before = state_path.read_bytes()
    try:
        legacy = json.loads(before.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuationLedgerError(f"legacy run-state is not valid JSON: {state_path}") from exc
    if not isinstance(legacy, dict):
        raise ContinuationLedgerError("legacy run-state must be an object")
    if legacy.get("schema_version") != "food_line_bounded_run_state_v1":
        raise ContinuationLedgerError("legacy run-state schema is not the current intake schema")
    if legacy.get("edition_date") != edition_date or legacy.get("run_id") != run_id:
        raise ContinuationLedgerError("legacy run-state identity does not match continuation binding")
    if legacy.get("status") != "incomplete_capped" or legacy.get("resumable") is not False:
        raise ContinuationLedgerError("handoff requires the original incomplete_capped non-resumable state")

    candidates = _aggregate_candidates(ledger_state)
    run_dir = state_path.parent
    export_path = run_dir / "continuation-agent-export.json"
    export_payload = {
        "schema_version": "food_line_source_watch_agent_export_v1",
        "agent_name": "Food Line Source Watch",
        "agent_run_id": run_id,
        "edition_date": edition_date,
        "started_at": legacy.get("started_at") or "",
        "completed_at": _utc_now(),
        "search_window": {"edition_date": edition_date},
        "findings": candidates,
        "coverage_notes": "Stable-ID continuation source-stage handoff; public publication remains separately gated.",
    }
    _write_json_atomic(export_path, export_payload)
    export_sha256 = _sha256(export_path)

    updated = dict(legacy)
    completed_at = _utc_now()
    updated.update(
        {
            "completed_at": completed_at,
            "status": assessment["collection_status"],
            "resumable": False,
            "partitions_total": 1,
            "partitions_completed": 1,
            "queries_total": assessment["planned_count"],
            "queries_bounded": assessment["planned_count"],
            "queries_completed": assessment["completed_count"],
            "queries_succeeded": assessment["required_success_count"],
            "queries_deferred": 0,
            "queries_remaining": 0,
            "queries_failed": assessment["planned_count"] - assessment["required_success_count"],
            "queries_timed_out": sum(
                1
                for query_id in ledger_state.get("planned_query_ids") or []
                if bool((ledger_state.get("query_results", {}).get(query_id) or {}).get("timed_out"))
            ),
            "candidates_discovered": len(candidates),
            "query_plan_sha256": str(ledger_state.get("binding", {}).get("plan_sha256") or ""),
            "final_error": "",
            "coverage": {
                "required_success_ratio": assessment["required_success_ratio"],
                "direct_success_ratio": assessment["direct_success_ratio"],
            },
            "agent_export": {
                "status": "success" if candidates else "no_exportable_findings",
                "path": str(export_path),
                "sha256": export_sha256,
            },
            "execution_mode": "export_enabled",
            "release_ready": False,
            "intake_eligible": True,
            "publication_attempted": False,
            "publication_side_effects": {},
            "next_action": "Existing current intake path may run; public publication/export remains separately gated.",
            "continuation_handoff": {
                "status": "eligible",
                "source_identity": source_identity,
                "continuation_directory": str(continuation_directory),
                "plan_sha256": ledger_state.get("binding", {}).get("plan_sha256"),
                "previous_state_status": legacy.get("status"),
                "intake_eligible": True,
                "public_export_status": "not_requested",
                "publication_attempted": False,
                "publication_side_effects": {},
                "no_news": assessment["no_news"],
            },
        }
    )
    if state_path.read_bytes() != before:
        raise ContinuationLedgerError("legacy run-state changed during handoff; no state replacement")
    _write_json_atomic(state_path, updated)
    return updated, {**assessment, "agent_export_path": str(export_path), "agent_export_sha256": export_sha256}


def _load_or_prepare(
    args: argparse.Namespace,
    source_identity: str,
    source_module_sha256: str,
    config_sha256: str,
) -> dict[str, Any]:
    directory = Path(args.continuation_run_directory).resolve()
    rows = build_food_line_discovery_query_plan(Path(args.repo_root), args.edition_date)
    plan_path = directory / "continuation-query-plan.json"
    ledger_path = directory / "continuation-ledger.json"
    if plan_path.exists() != ledger_path.exists():
        raise ContinuationLedgerError("continuation plan and ledger must exist together")
    if plan_path.exists() or ledger_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        expected_plan = build_plan(
            edition_date=args.edition_date,
            run_id=args.run_id,
            source_identity=source_identity,
            source_module_sha256=source_module_sha256,
            config_sha256=config_sha256,
            query_rows=rows,
        )
        if plan.get("plan_sha256") != expected_plan["plan_sha256"]:
            raise ContinuationLedgerError("current query plan drifted from the receipt-bound continuation plan")
        binding = {
            "edition_date": args.edition_date,
            "run_id": args.run_id,
            "source_identity": source_identity,
            "source_module_sha256": source_module_sha256,
            "config_sha256": config_sha256,
            "plan_sha256": plan["plan_sha256"],
        }
        return load_ledger(directory, binding=binding)
    return prepare_ledger(
        directory,
        edition_date=args.edition_date,
        run_id=args.run_id,
        source_identity=source_identity,
        source_module_sha256=source_module_sha256,
        config_sha256=config_sha256,
        query_rows=rows,
    )


def run(args: argparse.Namespace) -> int:
    if not args.collection_only:
        raise ContinuationLedgerError("continuation runner is collection-only; explicit --collection-only is required")
    root = Path(args.repo_root).resolve()
    config = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source = SRC / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    source_identity = args.source_identity
    if not source_identity.strip():
        raise ContinuationLedgerError("source identity is required")
    source_module_sha256 = _sha256(source)
    config_sha256 = _sha256(config)
    _load_or_prepare(args, source_identity, source_module_sha256, config_sha256)
    plan = json.loads((Path(args.continuation_run_directory) / "continuation-query-plan.json").read_text(encoding="utf-8"))
    binding = {
        "edition_date": args.edition_date,
        "run_id": args.run_id,
        "source_identity": source_identity,
        "source_module_sha256": source_module_sha256,
        "config_sha256": config_sha256,
        "plan_sha256": plan["plan_sha256"],
    }

    def execute(rows: list[dict[str, Any]]) -> dict[str, Any]:
        result = run_food_line_discovery_expansion(
            root,
            args.edition_date,
            fetcher=None,
            query_plan_override=rows,
            resume_from_query_index=0,
            edition_mode="no_current_update",
            dry_run=True,
        )
        observed_rows = list(result.get("query_rows") or [])
        outcomes: dict[str, Any] = {}
        for index, row in enumerate(rows):
            observed = observed_rows[index] if index < len(observed_rows) else {}
            query_error = str(observed.get("query_error") or "").strip()
            direct_attempted = str(row.get("discovery_channel") or "google_news_rss") != "google_news_rss"
            status = str(result.get("status") or "")
            terminal = status not in {"timed_out", "collection_failure", "child_process_failure", "malformed_child_output"}
            required_ok = bool(result.get("ok")) and terminal and not query_error
            outcomes[stable_query_id(row)] = {
                "status": status,
                "ok": bool(result.get("ok")),
                "terminal": terminal,
                "uncertain": False,
                "required_coverage_success": required_ok,
                "direct_source_attempted": direct_attempted,
                "direct_source_success": direct_attempted and observed.get("direct_fetch_status") == "ok",
                "query_rows": [observed] if observed else [],
                "candidate_count": len(result.get("candidates") or []) if index == 0 else 0,
                "exclusion_count": int(bool(query_error)),
                "candidates": list(result.get("candidates") or []) if index == 0 else [],
                "collection_only": True,
            }
        return outcomes

    state = continue_ledger(
        Path(args.continuation_run_directory).resolve(),
        binding=binding,
        batch_size=args.max_queries,
        batch_executor=execute,
    )
    handoff = source_stage_handoff_assessment(state)
    handoff_state: dict[str, Any] | None = None
    if handoff["intake_eligible"]:
        handoff_state, handoff = commit_source_stage_handoff(
            root,
            edition_date=args.edition_date,
            run_id=args.run_id,
            continuation_directory=Path(args.continuation_run_directory).resolve(),
            ledger_state=state,
            source_identity=source_identity,
        )
    payload = {
        "schema": "food_line_continuation_receipt_v1",
        "status": "completed" if handoff["intake_eligible"] else "incomplete_pending",
        "source_identity": source_identity,
        "config_sha256": config_sha256,
        "execution_mode": "collection_only",
        "gates": gate_summary(state),
        "source_stage_handoff": handoff,
        "legacy_run_state_path": str(_run_state_path(root, args.edition_date, args.run_id)) if handoff_state else None,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "completed" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--continuation-run-directory", required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--max-queries", type=int, default=200)
    parser.add_argument("--collection-only", action="store_true")
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except ContinuationLedgerError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(10)

