from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ALLOWED_REVIEW_STATUSES = ("needs_review", "approved", "rejected", "maybe", "quarantine")


def first_sunday_of_year(year: int) -> date:
    d = date(year, 1, 1)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def week_dates_for_year_week(year: int, week_number: int) -> tuple[date, date]:
    if week_number < 1:
        raise ValueError("week_number must be >= 1")
    start = first_sunday_of_year(year) + timedelta(days=(week_number - 1) * 7)
    end = start + timedelta(days=6)
    if start.year != year and end.year != year:
        raise ValueError("week out of range for year")
    return start, end


def week_label(year: int, week_number: int) -> str:
    start, end = week_dates_for_year_week(year, week_number)
    return f"Week {week_number}: {start.strftime('%B')} {start.day}\u2013{end.strftime('%B')} {end.day}, {end.year}"


def candidate_path(root: Path, day: str) -> Path:
    return root / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"


def week_days(week_end_date: str) -> list[str]:
    end = date.fromisoformat(week_end_date)
    start = end - timedelta(days=6)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def _candidate_key(day: str, row: dict[str, Any], index: int) -> str:
    source_record_id = str(row.get("source_record_id") or "").strip()
    source_key = source_record_id or "missing_source_record_id"
    return f"{day}::{source_key}::{index}"


def load_weekly_candidates(root: Path, week_end_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in week_days(week_end_date):
        path = candidate_path(root, day)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = payload.get("sources", []) if isinstance(payload, dict) else []
        source_ordinals: dict[str, int] = {}
        for idx, row in enumerate(sources):
            if not isinstance(row, dict):
                continue
            source_record_id = str(row.get("source_record_id") or "").strip()
            ordinal = source_ordinals.get(source_record_id, 0)
            source_ordinals[source_record_id] = ordinal + 1
            status = str(row.get("review_status") or "needs_review").strip().lower() or "needs_review"
            if status not in ALLOWED_REVIEW_STATUSES:
                status = "needs_review"
            rows.append(
                {
                    "candidate_key": _candidate_key(day, row, idx),
                    "date": day,
                    "file_path": str(path),
                    "row_index": idx,
                    "source_record_id": source_record_id,
                    "source_record_ordinal": ordinal,
                    "review_status": status,
                    "pillar": str(row.get("pillar") or ""),
                    "publisher_quality": str(row.get("reliability_tier") or ""),
                    "source_publisher": str(row.get("publisher") or row.get("source_id") or ""),
                    "reader_headline": str(row.get("reader_headline") or row.get("title") or ""),
                    "location": str(row.get("location") or ""),
                    "score": row.get("candidate_score"),
                    "reason": str(row.get("editorial_rejection_reason") or ""),
                    "url": str(row.get("url") or ""),
                    "review_notes": str(row.get("review_notes") or ""),
                    "raw": row,
                }
            )
    return rows


def approval_validation_issues(row: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not str(row.get("url") or "").strip():
        issues.append("missing URL")
    if not str(row.get("public_pressure_angle") or "").strip():
        issues.append("missing public pressure angle")
    us_relevance_ok = row.get("us_relevance_ok")
    explicit = bool(row.get("explicit_us_relevance")) or bool(row.get("us_relevance_explicit"))
    if us_relevance_ok is False and not explicit:
        issues.append("missing U.S. relevance")
    if str(row.get("editorial_rejection_reason") or "").strip() == "prose_quality_failed":
        issues.append("prose quality failed")
    if str(row.get("review_status") or "").strip().lower() == "quarantine":
        issues.append("candidate is quarantined")
    return issues


def save_review_decisions(
    root: Path,
    week_end_date: str,
    status_updates: dict[str, str],
    review_notes: dict[str, str] | None = None,
    override_keys: set[str] | None = None,
) -> dict[str, Any]:
    notes_map = review_notes or {}
    overrides = override_keys or set()
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in load_weekly_candidates(root, week_end_date):
        by_day.setdefault(row["date"], []).append(row)

    changed = 0
    for day, entries in by_day.items():
        path = candidate_path(root, day)
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources = payload.get("sources", []) if isinstance(payload, dict) else []
        dirty = False
        for entry in entries:
            key = entry["candidate_key"]
            if key not in status_updates:
                continue
            new_status = str(status_updates[key]).strip().lower()
            if new_status not in ALLOWED_REVIEW_STATUSES:
                continue
            source_record_id = str(entry.get("source_record_id") or "").strip()
            expected_ordinal = int(entry.get("source_record_ordinal") or 0)
            idx = int(entry["row_index"])
            selected_idx: int | None = None

            # Primary: row ordinal in file, guarded by source_record_id check.
            if 0 <= idx < len(sources) and isinstance(sources[idx], dict):
                indexed_source_id = str(sources[idx].get("source_record_id") or "").strip()
                if indexed_source_id == source_record_id:
                    selected_idx = idx

            # Fallback: find this source_record_id by ordinal occurrence.
            if selected_idx is None:
                matched_indices = [
                    i for i, source in enumerate(sources)
                    if isinstance(source, dict) and str(source.get("source_record_id") or "").strip() == source_record_id
                ]
                if expected_ordinal < len(matched_indices):
                    selected_idx = matched_indices[expected_ordinal]
                elif len(matched_indices) == 1:
                    selected_idx = matched_indices[0]

            if selected_idx is None:
                continue

            row = sources[selected_idx]
            if str(row.get("review_status") or "needs_review").strip().lower() != new_status:
                row["review_status"] = new_status
                changed += 1
                dirty = True
            note = notes_map.get(key, "").strip()
            if note:
                row["review_notes"] = note
                dirty = True
            row["user_reviewed_at"] = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if key in overrides:
                row["review_override"] = True
                row["review_override_reason"] = "user_confirmed_gui_override"
            elif "review_override" in row:
                row.pop("review_override", None)
                row.pop("review_override_reason", None)
            dirty = True
        if dirty:
            payload["sources"] = sources
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"ok": True, "changed_count": changed}
