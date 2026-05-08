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


def format_coverage_label(week_start: str | date, week_end: str | date) -> str:
    start = parse_local_date(week_start) if isinstance(week_start, str) else week_start
    end = parse_local_date(week_end) if isinstance(week_end, str) else week_end
    start_month = start.strftime("%b")
    end_month = end.strftime("%b")
    if start.year == end.year and start.month == end.month:
        return f"{start_month} {start.day}\u2013{end.day}, {start.year}"
    if start.year == end.year:
        return f"{start_month} {start.day}\u2013{end_month} {end.day}, {start.year}"
    return f"{start_month} {start.day}, {start.year}\u2013{end_month} {end.day}, {end.year}"


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


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _existing_edition_dirs(root: Path) -> list[Path]:
    dirs: list[Path] = []
    for base in [
        root / "output" / "site" / "cascadia" / "editions",
        root / "output" / "dispatches" / "cascadia" / "editions",
    ]:
        if base.exists():
            dirs.extend(path for path in base.iterdir() if path.is_dir() and len(path.name) == 10)
    return sorted(dirs, key=lambda path: (path.name, str(path)))


def _source_record_date(record: dict[str, Any], edition_date: str) -> tuple[date | None, str | None]:
    published = _published_date(record.get("published_at"))
    if published is not None:
        return published, "published_at"
    try:
        return date.fromisoformat(edition_date), "derived_from_edition_date"
    except ValueError:
        return None, None


def _derived_source_record(source: dict[str, Any], edition_dir: Path, sources_manifest_path: Path, week_start: date, week_end: date) -> dict[str, Any] | None:
    source_url = source.get("url") or source.get("source_url") or source.get("canonical_url")
    title = source.get("title") or source.get("source_title")
    edition_date = edition_dir.name
    basis_date, basis = _source_record_date(source, edition_date)
    if basis_date is None or not (week_start <= basis_date <= week_end):
        return None
    original_id = source.get("original_source_record_id") or source.get("source_record_id")
    source_record_id = f"derived-{edition_date}-{original_id}" if original_id else f"derived-{edition_date}-{stable_manifest_id(source_url, title)}"
    return {
        **source,
        "source_record_id": source_record_id,
        "original_source_record_id": original_id,
        "canonical_url": canonicalize_url(source_url or ""),
        "url": source_url,
        "source_url": source_url,
        "title": title,
        "source_title": title,
        "publisher": source.get("publisher") or source.get("source_name"),
        "published_at": source.get("published_at"),
        "retrieved_at": source.get("retrieved_at"),
        "source_id": "existing-cascadia-manifest",
        "source_type": "existing_cascadia_manifest",
        "derived_from_edition_date": edition_date,
        "derived_from_edition_path": str(edition_dir),
        "derived_from_manifest_path": str(sources_manifest_path),
        "weekly_date_basis": basis,
        "traceability_note": "Derived from prior Cascadia edition manifest; original source URL preserved.",
        "trace": {
            "original_source_record_id": original_id,
            "derived_from_manifest_path": str(sources_manifest_path),
        },
    }


def stable_manifest_id(url: str | None, title: str | None) -> str:
    import hashlib

    raw = "|".join([canonicalize_url(url or ""), " ".join((title or "").lower().split())])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _derived_story(
    story: dict[str, Any],
    source_by_original_id: dict[str, dict[str, Any]],
    source_by_url: dict[str, dict[str, Any]],
    edition_dir: Path,
    curation_manifest_path: Path,
) -> dict[str, Any] | None:
    source_records: list[dict[str, Any]] = []
    for source_id in story.get("source_record_ids") or []:
        record = source_by_original_id.get(source_id)
        if record and record not in source_records:
            source_records.append(record)
    for url in story.get("source_urls") or []:
        record = source_by_url.get(canonicalize_url(url))
        if record and record not in source_records:
            source_records.append(record)
    if not source_records:
        return None
    source_urls = []
    source_record_ids = []
    for record in source_records:
        if record.get("source_url") and record["source_url"] not in source_urls:
            source_urls.append(record["source_url"])
        if record.get("source_record_id") and record["source_record_id"] not in source_record_ids:
            source_record_ids.append(record["source_record_id"])
    item = dict(story)
    item["story_id"] = f"derived-{edition_dir.name}-{story.get('story_id') or stable_manifest_id(source_urls[0] if source_urls else '', story.get('title'))}"
    item["source_record_ids"] = source_record_ids
    item["source_urls"] = source_urls
    item["source_records"] = source_records
    item["source_type"] = "existing_cascadia_manifest"
    item["derived_from_edition_date"] = edition_dir.name
    item["derived_from_edition_path"] = str(edition_dir)
    item["derived_from_manifest_path"] = str(curation_manifest_path)
    item["traceability_note"] = "Derived from prior Cascadia edition manifest; original source URL preserved."
    return item


def _story_from_source(record: dict[str, Any]) -> dict[str, Any]:
    title = record.get("title") or record.get("source_title") or "Source-backed Cascadia signal"
    return {
        "story_id": f"story-{record['source_record_id']}",
        "title": title,
        "summary": record.get("text") or record.get("summary") or title,
        "category": record.get("category_hint") or "Cascadia signals",
        "score": 50,
        "scoring_reasons": ["source_type=existing_cascadia_manifest"],
        "source_record_ids": [record["source_record_id"]],
        "source_urls": [record["source_url"]] if record.get("source_url") else [],
        "included_in_public_summary": bool(record.get("source_url")),
        "included_in_detail_dataset": True,
        "excluded_reason": None,
        "source_records": [record],
        "source_type": "existing_cascadia_manifest",
        "derived_from_edition_date": record.get("derived_from_edition_date"),
        "derived_from_edition_path": record.get("derived_from_edition_path"),
        "derived_from_manifest_path": record.get("derived_from_manifest_path"),
        "traceability_note": record.get("traceability_note"),
    }


def backfill_weekly_from_existing_editions(
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
    coverage_label = format_coverage_label(week_start, week_end)
    warnings: list[str] = []
    errors: list[str] = []
    written: list[str] = []
    normalized_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    stories_by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for edition_dir in _existing_edition_dirs(root):
        try:
            source_edition_date = date.fromisoformat(edition_dir.name)
        except ValueError:
            continue
        if not (week_start <= source_edition_date <= week_end):
            continue
        sources_manifest_path = edition_dir / "sources_manifest.json"
        curation_manifest_path = edition_dir / "curation_manifest.json"
        if not sources_manifest_path.exists():
            warnings.append(f"missing existing Cascadia source manifest: {sources_manifest_path}")
            continue
        sources = _load_json_list(sources_manifest_path)
        if not sources:
            warnings.append(f"empty existing Cascadia source manifest: {sources_manifest_path}")
            continue
        source_by_original_id: dict[str, dict[str, Any]] = {}
        source_by_url: dict[str, dict[str, Any]] = {}
        for source in sources:
            derived = _derived_source_record(source, edition_dir, sources_manifest_path, week_start, week_end)
            if not derived or not derived.get("source_url"):
                continue
            key = (canonicalize_url(derived["source_url"]), " ".join((derived.get("title") or "").lower().split()))
            if key not in normalized_by_key:
                normalized_by_key[key] = derived
            stored = normalized_by_key[key]
            original_id = source.get("original_source_record_id") or source.get("source_record_id")
            if original_id:
                source_by_original_id[original_id] = stored
            source_by_url[canonicalize_url(derived["source_url"])] = stored
        if not curation_manifest_path.exists():
            warnings.append(f"missing existing Cascadia curation manifest: {curation_manifest_path}")
            continue
        for story in _load_json_list(curation_manifest_path):
            derived_story = _derived_story(story, source_by_original_id, source_by_url, edition_dir, curation_manifest_path)
            if not derived_story:
                continue
            key = _dedupe_key(derived_story)
            if key in stories_by_key:
                stories_by_key[key] = _merge_story(stories_by_key[key], derived_story)
            else:
                stories_by_key[key] = derived_story

    normalized = sorted(normalized_by_key.values(), key=lambda item: item["source_record_id"])
    if not stories_by_key:
        for record in normalized:
            story = _story_from_source(record)
            stories_by_key[_dedupe_key(story)] = story
    curated = sorted(stories_by_key.values(), key=lambda item: item.get("score") or 0, reverse=True)
    curated_out = data_root / "curated" / edition_date / "curation_manifest.json"
    normalized_out = data_root / "normalized" / edition_date / "normalized_sources.json"
    raw_out = data_root / "raw" / edition_date / "raw_sources.json"
    run_manifest = {
        "dispatch_slug": "cascadia",
        "public_name": "The Cascadia Briefing",
        "briefing_type": "weekly",
        "source_type": "existing_cascadia_manifest",
        "run_date": run_date,
        "edition_date": edition_date,
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "coverage_label": coverage_label,
        "week_label": week_label(week_start),
        "source_record_ids": [record["source_record_id"] for record in normalized],
        "source_urls": sorted({record.get("source_url") for record in normalized if record.get("source_url")}),
        "curated_count": len(curated),
        "warnings": warnings,
        "errors": errors,
    }
    if not dry_run:
        for path, payload in [(raw_out, normalized), (normalized_out, normalized), (curated_out, curated)]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (curated_out.parent / "weekly_run_manifest.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
    written.extend([str(raw_out), str(normalized_out), str(curated_out), str(curated_out.parent / "weekly_run_manifest.json")])
    return {
        "ok": not errors,
        "edition_date": edition_date,
        "run_date": run_date,
        "coverage_start": week_start.isoformat(),
        "coverage_end": week_end.isoformat(),
        "coverage_label": coverage_label,
        "week_label": week_label(week_start),
        "curated_count": len(curated),
        "normalized_count": len(normalized),
        "source_record_ids": run_manifest["source_record_ids"],
        "source_urls": run_manifest["source_urls"],
        "curation_path": str(curated_out),
        "normalized_path": str(normalized_out),
        "raw_path": str(raw_out),
        "run_manifest_path": str(curated_out.parent / "weekly_run_manifest.json"),
        "written": written,
        "warnings": warnings,
        "errors": errors,
    }


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
        "coverage_label": format_coverage_label(week_start, week_end),
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
        "coverage_label": format_coverage_label(week_start, week_end),
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
