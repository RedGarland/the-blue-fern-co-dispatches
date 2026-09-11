"""Bounded, non-public reconciliation for retained Food/Care discoveries."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

TERMINAL = {"retained_for_review", "duplicate_with_reason", "rejected_with_reason", "deferred_with_reason", "invalid_source_with_reason"}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _in_window(value: str, date_from: str, date_to: str) -> bool:
    try:
        current = date.fromisoformat(value[:10])
        return date.fromisoformat(date_from) <= current <= date.fromisoformat(date_to)
    except ValueError:
        return False


def reconcile_food(root: Path, run_ids: set[str], date_from: str, date_to: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    intake_root = root / "data/dispatches/food-line/agent-intake"
    if not intake_root.exists():
        return rows
    for path in intake_root.glob("*/**/*.json"):
        if not _in_window(path.parent.name, date_from, date_to):
            continue
        payload = _load(path)
        run_id = str((payload or {}).get("agent_run_id") or path.stem) if isinstance(payload, dict) else path.stem
        if run_ids and run_id not in run_ids:
            continue
        for row in _rows(payload, "candidate_rows"):
            disposition = str(row.get("candidate_disposition") or row.get("review_retention_disposition") or "")
            rows.append({"dispatch": "food-line", "run_id": run_id, "url": str(row.get("source_url") or row.get("canonical_source_url") or ""), "title": str(row.get("title") or ""), "detection_date": path.parent.name, "expected_outcome": "reviewable source-backed finding", "actual_disposition": disposition, "failure_stage": "food_current_intake", "reason_code": str(row.get("candidate_disposition_reason") or row.get("exclusion_reason") or ""), "corrected_disposition": disposition if disposition in TERMINAL else "", "duplicate_linkage": row.get("duplicate_linkage") or {}, "resolution_status": "accounted" if disposition in TERMINAL else "unaccounted"})
    return rows


def reconcile_care(root: Path, run_ids: set[str], date_from: str, date_to: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    run_root = root / "data/dispatches/care-line/collection-runs"
    if not run_root.exists():
        return rows
    for date_dir in run_root.iterdir():
        if not date_dir.is_dir() or not _in_window(date_dir.name, date_from, date_to):
            continue
        for run_dir in date_dir.iterdir():
            if not run_dir.is_dir() or (run_ids and run_dir.name not in run_ids):
                continue
            raw_ids: set[str] = set()
            for path in run_dir.glob("*.raw-items.json"):
                for row in _rows(_load(path), "raw_items"):
                    if row.get("raw_item_id"):
                        raw_ids.add(str(row["raw_item_id"]))
            terminal_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
            for pattern, disposition in (("*.qualified-candidates.json", "retained_for_review"), ("*.exclusions.json", "rejected_with_reason"), ("*.failed-extractions.json", "deferred_with_reason"), ("*.prefilter.json", "deferred_with_reason")):
                for path in run_dir.glob(pattern):
                    key = "qualified_candidates" if "qualified" in path.name else "exclusions" if "exclusions" in path.name else "failed_extractions" if "failed" in path.name else "items"
                    for row in _rows(_load(path), key):
                        raw_id = str(row.get("raw_item_id") or "")
                        if raw_id and raw_id not in terminal_by_id:
                            terminal_by_id[raw_id] = (disposition, row)
            for raw_id in sorted(raw_ids):
                disposition, row = terminal_by_id.get(raw_id, ("", {}))
                rows.append({"dispatch": "care-line", "run_id": run_dir.name, "url": str(row.get("item_url") or ""), "title": str(row.get("title") or ""), "detection_date": date_dir.name, "expected_outcome": "reviewable source-backed finding", "actual_disposition": disposition, "failure_stage": "care_collection_handoff" if disposition else "care_post_discovery_reconciliation", "reason_code": str(row.get("exclusion_reason") or row.get("normalized_reason") or ""), "corrected_disposition": disposition if disposition in TERMINAL else "", "duplicate_linkage": row.get("duplicate_linkage") or {}, "resolution_status": "accounted" if disposition in TERMINAL else "unaccounted"})
    return rows


def reconcile(root: Path, *, dispatch: str = "", agent_run_id: str = "", edition_date: str = "") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "data/private-agent-handoff/receipts").glob("*/**/*.json")):
        receipt = _load(path)
        if not isinstance(receipt, dict) or (dispatch and receipt.get("dispatch") != dispatch) or (agent_run_id and receipt.get("agent_run_id") != agent_run_id) or (edition_date and path.parent.name != edition_date):
            continue
        for item in receipt.get("reconciliation", []):
            if isinstance(item, dict):
                rows.append({"dispatch": receipt.get("dispatch"), "agent_run_id": receipt.get("agent_run_id"), "edition_date": path.parent.name, **item})
    return {"schema_version": "bluefern.food_care_post_discovery_reconciliation.v1", "filters": {"dispatch": dispatch, "agent_run_id": agent_run_id, "edition_date": edition_date}, "row_count": len(rows), "unaccounted": sum(1 for row in rows if row.get("unaccounted")), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", "--runner-root", dest="root", type=Path, default=Path.cwd())
    parser.add_argument("--dispatch", choices=("food-line", "care-line", "both"), default="both")
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--agent-run-id", default="")
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--edition-date", default="")
    parser.add_argument("--output", type=Path, default=Path("data/dispatches/private/post-discovery-reconciliation.json"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.edition_date:
        date.fromisoformat(args.edition_date)
        result = reconcile(root, dispatch=args.dispatch if args.dispatch != "both" else "", agent_run_id=args.agent_run_id, edition_date=args.edition_date)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["unaccounted"] == 0 else 2
    if not args.date_from or not args.date_to:
        parser.error("--date-from and --date-to are required unless --edition-date is used")
    run_ids = {str(value) for value in args.run_id if str(value).strip()}
    rows: list[dict[str, Any]] = []
    if args.dispatch in {"food-line", "both"}:
        rows.extend(reconcile_food(root, run_ids, args.date_from, args.date_to))
    if args.dispatch in {"care-line", "both"}:
        rows.extend(reconcile_care(root, run_ids, args.date_from, args.date_to))
    payload = {"schema_version": "bluefern.post_discovery_reconciliation.v1", "read_only": not args.write, "source_fetch_performed": False, "publication_performed": False, "date_from": args.date_from, "date_to": args.date_to, "run_ids": sorted(run_ids), "rows": rows, "unaccounted_count": sum(row["resolution_status"] == "unaccounted" for row in rows)}
    if args.write:
        destination = root / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        payload["output"] = destination.as_posix()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["unaccounted_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
