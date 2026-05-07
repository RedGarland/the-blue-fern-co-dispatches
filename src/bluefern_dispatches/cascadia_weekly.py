from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_normalize import canonicalize_url


def parse_local_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def previous_completed_week(run_date: str | date) -> tuple[date, date]:
    current = parse_local_date(run_date) if isinstance(run_date, str) else run_date
    current_week_start = current - timedelta(days=current.weekday())
    week_start = current_week_start - timedelta(days=7)
    week_end = current_week_start - timedelta(days=1)
    return week_start, week_end


def containing_week(day: str | date) -> tuple[date, date]:
    current = parse_local_date(day) if isinstance(day, str) else day
    week_start = current - timedelta(days=current.weekday())
    return week_start, week_start + timedelta(days=6)


def explicit_week(week_start: str, week_end: str) -> tuple[date, date]:
    start = parse_local_date(week_start)
    end = parse_local_date(week_end)
    if start.weekday() != 0:
        raise ValueError(f"week-start must be a Monday: {week_start}")
    if end.weekday() != 6:
        raise ValueError(f"week-end must be a Sunday: {week_end}")
    if end != start + timedelta(days=6):
        raise ValueError("week-start and week-end must describe one Monday-Sunday week")
    return start, end


def week_label(week_start: date) -> str:
    iso = week_start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def dates_in_range(start: date, end: date) -> list[str]:
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _published_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _story_in_window(story: dict[str, Any], start: date, end: date) -> bool:
    records = story.get("source_records") or []
    if not records:
        return False
    for record in records:
        published = _published_date(record.get("published_at"))
        if published is not None and start <= published <= end:
            return True
    return False


def _dedupe_key(story: dict[str, Any]) -> tuple[str, str]:
    urls = sorted(canonicalize_url(url) for url in story.get("source_urls", []) if url)
    title = " ".join(str(story.get("title", "")).lower().split())
    return ("|".join(urls), title)


def _merge_story(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    merged["score"] = max(existing.get("score") or 0, incoming.get("score") or 0)
    for field in ["source_record_ids", "source_urls", "scoring_reasons"]:
        values = []
        for item in list(existing.get(field) or []) + list(incoming.get(field) or []):
            if item not in values:
                values.append(item)
        merged[field] = values
    records_by_id = {record.get("source_record_id"): record for record in existing.get("source_records", [])}
    for record in incoming.get("source_records", []):
        records_by_id[record.get("source_record_id")] = record
    merged["source_records"] = list(records_by_id.values())
    merged["included_in_public_summary"] = bool(existing.get("included_in_public_summary")) or bool(incoming.get("included_in_public_summary"))
    merged["included_in_detail_dataset"] = bool(existing.get("included_in_detail_dataset")) or bool(incoming.get("included_in_detail_dataset"))
    return merged


def aggregate_weekly_curation(
    root: Path,
    run_date: str,
    week_start: date,
    week_end: date,
    edition_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = root.resolve()
    edition_date = edition_date or week_end.isoformat()
    data_root = root / CASCADE_DATA_ROOT
    warnings: list[str] = []
    errors: list[str] = []
    written: list[str] = []
    stories_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    normalized_by_id: dict[str, dict[str, Any]] = {}

    for day in dates_in_range(week_start, week_end):
        curated_path = data_root / "curated" / day / "curation_manifest.json"
        normalized_path = data_root / "normalized" / day / "normalized_sources.json"
        if normalized_path.exists():
            for record in json.loads(normalized_path.read_text(encoding="utf-8")):
                published = _published_date(record.get("published_at"))
                if published is None or week_start <= published <= week_end:
                    normalized_by_id[record["source_record_id"]] = record
        if not curated_path.exists():
            warnings.append(f"missing daily Cascadia curation for weekly window date {day}: {curated_path}")
            continue
        for story in json.loads(curated_path.read_text(encoding="utf-8")):
            if not _story_in_window(story, week_start, week_end):
                continue
            key = _dedupe_key(story)
            if key in stories_by_key:
                stories_by_key[key] = _merge_story(stories_by_key[key], story)
            else:
                item = dict(story)
                item["source_window_date"] = day
                stories_by_key[key] = item

    curated = sorted(stories_by_key.values(), key=lambda item: item.get("score") or 0, reverse=True)
    curated_out = data_root / "curated" / edition_date / "curation_manifest.json"
    normalized_out = data_root / "normalized" / edition_date / "normalized_sources.json"
    run_manifest = {
        "dispatch_slug": "cascadia",
        "public_name": "The Cascadia Briefing",
        "briefing_type": "weekly",
        "run_date": run_date,
        "edition_date": edition_date,
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "week_label": week_label(week_start),
        "source_record_ids": sorted(normalized_by_id),
        "source_urls": sorted({record.get("canonical_url") for record in normalized_by_id.values() if record.get("canonical_url")}),
        "curated_count": len(curated),
        "warnings": warnings,
        "errors": errors,
    }
    if not dry_run:
        curated_out.parent.mkdir(parents=True, exist_ok=True)
        normalized_out.parent.mkdir(parents=True, exist_ok=True)
        curated_out.write_text(json.dumps(curated, indent=2), encoding="utf-8")
        normalized_out.write_text(json.dumps(sorted(normalized_by_id.values(), key=lambda item: item["source_record_id"]), indent=2), encoding="utf-8")
        (curated_out.parent / "weekly_run_manifest.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
    written.extend([str(curated_out), str(normalized_out), str(curated_out.parent / "weekly_run_manifest.json")])
    return {
        "ok": not errors,
        "edition_date": edition_date,
        "run_date": run_date,
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "week_label": week_label(week_start),
        "curated_count": len(curated),
        "normalized_count": len(normalized_by_id),
        "source_record_ids": run_manifest["source_record_ids"],
        "source_urls": run_manifest["source_urls"],
        "curation_path": str(curated_out),
        "normalized_path": str(normalized_out),
        "run_manifest_path": str(curated_out.parent / "weekly_run_manifest.json"),
        "written": written,
        "warnings": warnings,
        "errors": errors,
    }
