from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT


DISPATCH_SLUG = "cascadia"


SIGNAL_FIELDS = [
    "signal_id",
    "edition_date",
    "dispatch_id",
    "dispatch_slug",
    "first_seen",
    "last_seen",
    "state",
    "county",
    "city",
    "category",
    "subcategory",
    "title",
    "public_summary",
    "detail_summary",
    "severity_score",
    "regional_relevance_score",
    "systems_impact_score",
    "public_consequence_score",
    "recency_score",
    "source_reliability_score",
    "source_count",
    "source_record_ids",
    "source_urls",
    "source_titles",
    "publisher_names",
    "included_public",
    "included_detail",
    "exclusion_reason",
    "movement_label",
    "previous_score",
    "score_delta",
    "trend_direction",
    "generated_at",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_match_key(record: dict[str, Any]) -> str:
    urls = "|".join(sorted(record.get("source_urls") or []))
    title = re.sub(r"[^a-z0-9]+", " ", str(record.get("title", "")).lower()).strip()
    return "|".join([title, str(record.get("category", "")).lower(), urls])


def prior_detail_package(root: Path, edition_date: str) -> Path | None:
    detail_root = root / "output" / "detail" / DISPATCH_SLUG
    if not detail_root.exists():
        return None
    candidates = []
    for child in detail_root.iterdir():
        if not child.is_dir() or child.name >= edition_date:
            continue
        path = child / "cascadia_signal_records.json"
        if path.exists():
            candidates.append(path)
    return sorted(candidates, key=lambda path: path.parent.name)[-1] if candidates else None


def load_prior_records(root: Path, edition_date: str) -> dict[str, dict[str, Any]]:
    path = prior_detail_package(root, edition_date)
    if not path:
        return {}
    records = json.loads(path.read_text(encoding="utf-8"))
    by_key: dict[str, dict[str, Any]] = {}
    for record in records:
        by_key[str(record.get("signal_id"))] = record
        by_key[normalized_match_key(record)] = record
    return by_key


def movement(current_score: int | float | None, prior: dict[str, Any] | None) -> tuple[str, str, int | float | None, int | float | None]:
    if not prior:
        return "New", "new", None, None
    previous_score = prior.get("severity_score")
    if previous_score is None:
        previous_score = prior.get("score")
    if previous_score is None or current_score is None:
        return "Stable", "flat", previous_score, None
    delta = current_score - previous_score
    if delta > 0:
        return "Rising", "up", previous_score, delta
    if delta < 0:
        return "Falling", "down", previous_score, delta
    return "Stable", "flat", previous_score, 0


def source_titles(story: dict[str, Any]) -> list[str]:
    return [record.get("title", "") for record in story.get("source_records", []) if record.get("title")]


def publisher_names(story: dict[str, Any]) -> list[str]:
    names = []
    for record in story.get("source_records", []):
        publisher = record.get("publisher") or record.get("source_name")
        if publisher and publisher not in names:
            names.append(publisher)
    return names


def sources_manifest_from_curated(curated: list[dict[str, Any]], edition_date: str) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for story in curated:
        for record in story.get("source_records", []):
            by_id[record["source_record_id"]] = {
                "source_record_id": record["source_record_id"],
                "source_id": record.get("source_id"),
                "title": record.get("title"),
                "url": record.get("canonical_url"),
                "publisher": record.get("publisher"),
                "published_at": record.get("published_at"),
                "retrieved_at": record.get("retrieved_at"),
                "archive_path": None,
                "used_in_story_ids": [story["story_id"]],
                "claim_ids": [story["story_id"]],
                "dispatch_slug": DISPATCH_SLUG,
                "edition_date": edition_date,
                "region_scope": record.get("region_scope"),
                "category_hint": record.get("category_hint"),
            }
    return sorted(by_id.values(), key=lambda item: item["source_record_id"])


def signal_records(root: Path, edition_date: str, curated: list[dict[str, Any]], generated_at: str | None = None) -> list[dict[str, Any]]:
    generated_at = generated_at or utc_now()
    prior = load_prior_records(root, edition_date)
    records = []
    for story in curated:
        signal_id = story.get("signal_id") or story.get("story_id")
        previous = prior.get(str(signal_id)) or prior.get(normalized_match_key(story))
        label, direction, previous_score, delta = movement(story.get("score"), previous)
        records.append(
            {
                "signal_id": signal_id,
                "edition_date": edition_date,
                "dispatch_id": "dispatch-cascadia",
                "dispatch_slug": DISPATCH_SLUG,
                "first_seen": previous.get("first_seen") if previous else edition_date,
                "last_seen": edition_date,
                "state": None,
                "county": None,
                "city": None,
                "category": story.get("category"),
                "subcategory": story.get("subcategory"),
                "title": story.get("title"),
                "public_summary": story.get("summary"),
                "detail_summary": story.get("detail_summary") or story.get("summary"),
                "severity_score": story.get("score"),
                "regional_relevance_score": story.get("regional_relevance_score"),
                "systems_impact_score": story.get("systems_impact_score"),
                "public_consequence_score": story.get("public_consequence_score"),
                "recency_score": story.get("recency_score"),
                "source_reliability_score": story.get("source_reliability_score"),
                "source_count": len(story.get("source_record_ids") or []),
                "source_record_ids": story.get("source_record_ids") or [],
                "source_urls": story.get("source_urls") or [],
                "source_titles": source_titles(story),
                "publisher_names": publisher_names(story),
                "included_public": bool(story.get("included_in_public_summary")),
                "included_detail": bool(story.get("included_in_detail_dataset")),
                "exclusion_reason": story.get("excluded_reason"),
                "movement_label": label,
                "previous_score": previous_score,
                "score_delta": delta,
                "trend_direction": direction,
                "generated_at": generated_at,
            }
        )
    return records


def category_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("category") or "Uncategorized")].append(record)
    summary = []
    for category, items in sorted(grouped.items()):
        scores = [item.get("severity_score") or 0 for item in items]
        summary.append(
            {
                "edition_date": items[0]["edition_date"] if items else None,
                "dispatch_id": "dispatch-cascadia",
                "dispatch_slug": DISPATCH_SLUG,
                "category": category,
                "record_count": len(items),
                "public_count": sum(1 for item in items if item.get("included_public")),
                "detail_count": sum(1 for item in items if item.get("included_detail")),
                "max_severity_score": max(scores) if scores else 0,
                "average_severity_score": round(sum(scores) / len(scores), 2) if scores else 0,
            }
        )
    return summary


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str], dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row.get(field)) if isinstance(row.get(field), list) else row.get(field) for field in fieldnames})


def write_json(path: Path, payload: Any, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_cascadia_signal_package(root: Path, edition_date: str, dry_run: bool = False) -> dict[str, Any]:
    root = root.resolve()
    curated_path = root / CASCADE_DATA_ROOT / "curated" / edition_date / "curation_manifest.json"
    detail_dir = root / "output" / "detail" / DISPATCH_SLUG / edition_date
    warnings: list[str] = []
    errors: list[str] = []
    written: list[str] = []
    if not curated_path.exists():
        errors.append(f"curation manifest not found: {curated_path}")
        return {"ok": False, "detail_count": 0, "output_paths": {}, "written": written, "warnings": warnings, "errors": errors}
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    generated_at = utc_now()
    records = signal_records(root, edition_date, curated, generated_at)
    sources = sources_manifest_from_curated(curated, edition_date)
    categories = category_summary(records)
    run_manifest = {
        "ok": not errors,
        "dispatch_id": "dispatch-cascadia",
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "record_count": len(records),
        "source_count": len(sources),
        "category_count": len(categories),
        "public_exposed": False,
        "warnings": warnings,
        "errors": errors,
    }
    paths = {
        "records_json": detail_dir / "cascadia_signal_records.json",
        "records_csv": detail_dir / "cascadia_signal_records.csv",
        "source_manifest": detail_dir / "cascadia_source_manifest.json",
        "category_summary_json": detail_dir / "cascadia_category_summary.json",
        "category_summary_csv": detail_dir / "cascadia_category_summary.csv",
        "run_manifest": detail_dir / "cascadia_run_manifest.json",
        "legacy_records_json": detail_dir / "cascadian_detail_records.json",
        "legacy_records_csv": detail_dir / "cascadian_detail_records.csv",
    }
    write_json(paths["records_json"], records, dry_run, written)
    write_csv(paths["records_csv"], records, SIGNAL_FIELDS, dry_run, written)
    write_json(paths["source_manifest"], sources, dry_run, written)
    write_json(paths["category_summary_json"], categories, dry_run, written)
    write_csv(paths["category_summary_csv"], categories, list(categories[0].keys()) if categories else ["edition_date", "dispatch_id", "dispatch_slug", "category", "record_count", "public_count", "detail_count", "max_severity_score", "average_severity_score"], dry_run, written)
    write_json(paths["run_manifest"], run_manifest, dry_run, written)
    write_json(paths["legacy_records_json"], curated, dry_run, written)
    legacy_fields = ["story_id", "title", "category", "score", "source_record_ids", "source_urls", "included_in_public_summary", "included_in_detail_dataset", "excluded_reason"]
    write_csv(paths["legacy_records_csv"], curated, legacy_fields, dry_run, written)
    return {
        "ok": not errors,
        "detail_count": len(records),
        "output_paths": {key: str(value) for key, value in paths.items()},
        "written": written,
        "warnings": warnings,
        "errors": errors,
    }
