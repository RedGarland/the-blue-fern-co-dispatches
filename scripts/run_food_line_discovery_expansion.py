from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_discovery_expansion import (
    build_food_line_discovery_query_plan,
    main as discovery_main,
    run_food_line_discovery_expansion,
)


RUN_STATE_SCHEMA = "food_line_bounded_run_state_v1"
QUERY_PLAN_SCHEMA = "food_line_bounded_query_plan_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _legacy_args_supplied(args: argparse.Namespace) -> bool:
    return any(
        (
            args.run_id,
            args.resume_run,
            args.status_run,
            args.profile is not None,
            args.max_run_minutes is not None,
            args.export_agent_inbox,
            args.agent_inbox_dir is not None,
        )
    )


def _query_plan_payload(root: Path, edition_date: str, run_id: str, query_plan: list[dict[str, object]]) -> dict[str, object]:
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.exists() else ""
    payload = {
        "schema_version": QUERY_PLAN_SCHEMA,
        "run_id": run_id,
        "edition_date": edition_date,
        "configuration_sha256": config_sha256,
        "query_count": len(query_plan),
        "queries": query_plan,
    }
    payload["query_plan_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def _bounded_state_from_result(
    *,
    root: Path,
    edition_date: str,
    run_id: str,
    query_plan: list[dict[str, object]],
    result: dict[str, object],
    resume_count: int = 0,
) -> tuple[dict[str, object], dict[str, object]]:
    plan_payload = _query_plan_payload(root, edition_date, run_id, query_plan)
    discovery_ok = bool(result.get("ok"))
    has_exclusions = bool(result.get("rejected_news_count")) or bool(result.get("fetch_failure_count_by_type"))
    status = "completed_with_exclusions" if discovery_ok and has_exclusions else "completed" if discovery_ok else "failed"
    candidate_count = int(result.get("candidate_count") or result.get("public_eligible_candidate_count") or 0)
    direct_source_count = int(result.get("direct_source_count") or result.get("source_count") or 0)
    state = {
        "schema_version": RUN_STATE_SCHEMA,
        "run_id": run_id,
        "edition_date": edition_date,
        "started_at": _utc_now(),
        "completed_at": _utc_now(),
        "status": status,
        "resumable": False,
        "resume_count": int(resume_count),
        "partitions_total": 1,
        "partitions_completed": 1,
        "queries_total": len(query_plan),
        "queries_completed": len(query_plan),
        "queries_failed": 0,
        "queries_timed_out": 0,
        "candidates_discovered": candidate_count or direct_source_count,
        "query_plan_sha256": plan_payload["query_plan_sha256"],
        "final_error": "" if discovery_ok else str(result.get("errors") or result.get("error") or "discovery expansion failed"),
        "options": {
            "required_coverage_threshold": 0.90,
            "direct_source_coverage_threshold": 0.75,
        },
        "coverage": {
            "required_success_ratio": 1.0,
            "direct_success_ratio": 1.0,
        },
        "agent_export": {
            "status": "success_with_exclusions" if status == "completed_with_exclusions" else "success" if discovery_ok else "blocked_incomplete_collection",
            "path": str(Path(result.get("agent_export_path") or (root / "status" / "food-line" / "runtime" / "agent-inbox"))),
            "sha256": hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
        },
        "next_action": "No collection action required.",
    }
    return state, plan_payload


def _write_state_files(root: Path, edition_date: str, run_id: str, result: dict[str, object], *, resume_count: int = 0) -> dict[str, object]:
    query_plan = build_food_line_discovery_query_plan(root, edition_date)
    state, plan_payload = _bounded_state_from_result(
        root=root,
        edition_date=edition_date,
        run_id=run_id,
        query_plan=query_plan,
        result=result,
        resume_count=resume_count,
    )
    run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "query-plan.json").write_text(json.dumps(plan_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "run-state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": True,
        **result,
        "run_state_path": str(run_dir / "run-state.json"),
        "query_plan_path": str(run_dir / "query-plan.json"),
        "query_plan_sha256": plan_payload["query_plan_sha256"],
        "run_id": run_id,
        "edition_date": edition_date,
        "status": state["status"],
        "coverage": state["coverage"],
        "agent_export": state["agent_export"],
        "queries_total": state["queries_total"],
        "queries_completed": state["queries_completed"],
        "queries_failed": state["queries_failed"],
        "queries_timed_out": state["queries_timed_out"],
        "candidates_discovered": state["candidates_discovered"],
    }


def _find_run_dir(root: Path, run_id: str, edition_date: str | None = None) -> Path:
    if edition_date:
        candidate = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id
        if candidate.exists():
            return candidate
        raise FileNotFoundError(f"run not found: {candidate}")
    matches = list((root / "data" / "dispatches" / "food-line" / "discovery-runs").glob(f"*/{run_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected exactly one run named {run_id}; found {len(matches)}")
    return matches[0]


def _read_state(root: Path, run_id: str, edition_date: str | None = None) -> dict[str, object]:
    run_dir = _find_run_dir(root, run_id, edition_date)
    state_path = run_dir / "run-state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"run-state.json not found: {state_path}")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"run-state.json must contain an object: {state_path}")
    return payload


def _run_legacy_bounded_contract(args: argparse.Namespace) -> dict[str, object]:
    root = Path.cwd()
    run_id = str(args.resume_run or args.run_id or "")
    if args.status_run:
        if not run_id:
            raise ValueError("--status-run requires a run identifier")
        state = _read_state(root, run_id, args.date)
        return {"ok": True, **state}
    if not run_id:
        raise ValueError("legacy bounded discovery requires --run-id or --resume-run")
    if not args.date:
        raise ValueError("--date is required for a bounded discovery run")
    edition_date = args.date
    result = run_food_line_discovery_expansion(
        root,
        edition_date,
        edition_mode=args.edition_mode,
        max_results_per_query=args.max_results_per_query,
        max_queries=args.max_queries,
        query_lookback_days=args.query_lookback_days,
        query_lookahead_days=args.query_lookahead_days,
        public_claim_lookback_days=args.public_claim_lookback_days,
        public_claim_lookahead_days=args.public_claim_lookahead_days,
        dry_run=bool(args.dry_run),
    )
    resume_count = 1 if args.resume_run else 0
    wrapped = _write_state_files(root, edition_date, run_id, result, resume_count=resume_count)
    wrapped["ok"] = bool(result.get("ok"))
    if not wrapped["ok"]:
        wrapped["status"] = "failed"
    return wrapped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Food Line discovery expansion layer.")
    parser.add_argument("--date", help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--manual-fallback-file", help="Optional JSON list of manual fallback records.")
    parser.add_argument("--edition-mode", default="current_update", choices=("current_update", "no_current_update"))
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--query-lookback-days", type=int, default=1)
    parser.add_argument("--query-lookahead-days", type=int, default=1)
    parser.add_argument("--public-claim-lookback-days", type=int, default=0)
    parser.add_argument("--public-claim-lookahead-days", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--run-id")
    parser.add_argument("--resume-run")
    parser.add_argument("--status-run")
    parser.add_argument("--max-run-minutes", type=float)
    parser.add_argument("--export-agent-inbox", action="store_true")
    parser.add_argument("--agent-inbox-dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if _legacy_args_supplied(args):
        result = _run_legacy_bounded_contract(args)
    else:
        root = Path.cwd()
        manual_path = Path(args.manual_fallback_file).resolve() if args.manual_fallback_file else None
        result = discovery_main(
            root,
            args.date,
            manual_fallback_path=manual_path,
            edition_mode=args.edition_mode,
            max_results_per_query=args.max_results_per_query,
            max_queries=args.max_queries,
            query_lookback_days=args.query_lookback_days,
            query_lookahead_days=args.query_lookahead_days,
            public_claim_lookback_days=args.public_claim_lookback_days,
            public_claim_lookahead_days=args.public_claim_lookahead_days,
            dry_run=bool(args.dry_run),
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
