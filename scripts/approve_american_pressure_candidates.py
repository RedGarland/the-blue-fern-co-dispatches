from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_ROOT = ROOT / "data" / "dispatches" / "american-pressure" / "candidates"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ALLOWED_STATUSES = {"approved", "rejected", "maybe", "needs_review", "quarantine"}


def _validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


def _candidate_path(day: str) -> Path:
    return CANDIDATES_ROOT / day / "candidate_sources.json"


def _load_payload(day: str) -> tuple[Path, dict[str, Any]]:
    path = _candidate_path(day)
    if not path.exists():
        raise FileNotFoundError(f"candidate file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("candidate file must contain a JSON object")
    rows = payload.get("sources")
    if not isinstance(rows, list):
        raise ValueError("candidate file missing list field: sources")
    return path, payload


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    approved_by_pillar: Counter[str] = Counter()
    for row in rows:
        status = str(row.get("review_status") or "needs_review").strip().lower() or "needs_review"
        status_counts[status] += 1
        if status == "approved":
            pillar = str(row.get("pillar") or "").strip() or "unknown"
            approved_by_pillar[pillar] += 1
    return {
        "status_counts": dict(sorted(status_counts.items())),
        "approved_by_pillar": dict(sorted(approved_by_pillar.items())),
    }


def _set_status(rows: list[dict[str, Any]], source_record_id: str, status: str) -> bool:
    for row in rows:
        if str(row.get("source_record_id") or "").strip() == source_record_id:
            row["review_status"] = status
            return True
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Approve/reject/maybe/review American Pressure daily candidates.")
    parser.add_argument("--date", required=True, help="Candidate date YYYY-MM-DD")
    parser.add_argument("--list", action="store_true", help="List candidate statuses and summary counts.")
    parser.add_argument("--approve", dest="approve_id", help="Set review_status=approved for source_record_id")
    parser.add_argument("--reject", dest="reject_id", help="Set review_status=rejected for source_record_id")
    parser.add_argument("--maybe", dest="maybe_id", help="Set review_status=maybe for source_record_id")
    parser.add_argument("--needs-review", dest="needs_review_id", help="Set review_status=needs_review for source_record_id")
    parser.add_argument("--quarantine", dest="quarantine_id", help="Set review_status=quarantine for source_record_id")
    parser.add_argument("--write", action="store_true", help="Persist review_status changes to candidate JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        day = _validate_date(args.date)
        path, payload = _load_payload(day)
        rows = [row for row in payload.get("sources", []) if isinstance(row, dict)]
        operations = [
            ("approved", args.approve_id),
            ("rejected", args.reject_id),
            ("maybe", args.maybe_id),
            ("needs_review", args.needs_review_id),
            ("quarantine", args.quarantine_id),
        ]
        chosen = [(status, record_id) for status, record_id in operations if record_id]
        if len(chosen) > 1:
            raise ValueError("choose only one status operation per run")
        changed = False
        updated_record_id = ""
        updated_status = ""
        if chosen:
            status, record_id = chosen[0]
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"unsupported review_status: {status}")
            if not _set_status(rows, record_id, status):
                raise ValueError(f"source_record_id not found: {record_id}")
            payload["sources"] = rows
            changed = True
            updated_record_id = record_id
            updated_status = status
            if args.write:
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        summary = _summarize(rows)
        result = {
            "ok": True,
            "date": day,
            "candidate_file": str(path),
            "updated": changed,
            "updated_source_record_id": updated_record_id,
            "updated_status": updated_status,
            "written": bool(args.write and changed),
            "summary": summary,
        }
        if args.list:
            result["candidates"] = [
                {
                    "source_record_id": str(row.get("source_record_id") or ""),
                    "pillar": str(row.get("pillar") or ""),
                    "review_status": str(row.get("review_status") or "needs_review"),
                    "title": str(row.get("title") or ""),
                }
                for row in rows
            ]
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
