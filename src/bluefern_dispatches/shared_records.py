from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.generator import BASE_URL


RECORD_ROOT = Path("data") / "records"
SHARED_FILES = {
    "dispatches": "dispatches.json",
    "editions": "editions.json",
    "sources": "sources.json",
    "records": "records.json",
    "curation_decisions": "curation_decisions.json",
    "detail_packages": "detail_packages.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def upsert(rows: list[dict[str, Any]], key: str, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {str(row[key]): row for row in rows if key in row}
    for row in incoming:
        by_key[str(row[key])] = row
    return sorted(by_key.values(), key=lambda row: str(row.get(key, "")))


def seed_dispatch_rows(now: str) -> list[dict[str, Any]]:
    return [
        {
            "dispatch_id": "dispatch-gaza",
            "slug": "gaza",
            "public_name": "Dispatches From Gaza",
            "internal_name": "Gaza Dispatch",
            "description": "Free public Gaza briefing compiled from traceable source records.",
            "is_free_public": True,
            "has_detail_tier": False,
            "created_at": "2026-05-03T00:00:00Z",
            "updated_at": now,
        },
        {
            "dispatch_id": "dispatch-cascadia",
            "slug": "cascadia",
            "public_name": "The Cascadia Briefing",
            "internal_name": "Cascadia Signal",
            "description": "The Cascadia Briefing is a weekly, source-backed regional briefing for Washington, Oregon, and Idaho, tracking public systems, infrastructure, health, safety, environment, economy, and resilience.",
            "is_free_public": True,
            "has_detail_tier": True,
            "created_at": "2026-05-03T00:00:00Z",
            "updated_at": now,
        },
        {
            "dispatch_id": "dispatch-american-pressure",
            "slug": "american-pressure",
            "public_name": "The American Pressure Dispatch",
            "internal_name": "American Pressure",
            "description": "Source-based reporting on the pressures reshaping household life across the United States.",
            "is_free_public": True,
            "has_detail_tier": False,
            "created_at": "2026-05-03T00:00:00Z",
            "updated_at": now,
        },
    ]


def seed_gaza_records(root: Path, now: str) -> dict[str, list[dict[str, Any]]]:
    editions_root = root / "output" / "site" / "gaza" / "editions"
    edition_dates = sorted(
        path.name
        for path in editions_root.iterdir()
        if path.is_dir() and (path / "sources_manifest.json").exists() and (path / "curation_manifest.json").exists()
    ) if editions_root.exists() else []
    if not edition_dates:
        edition_dates = ["2026-05-03"]

    edition_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    record_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    for edition_date in edition_dates:
        edition_dir = root / "output" / "site" / "gaza" / "editions" / edition_date
        public_url = f"{BASE_URL}/gaza/editions/{edition_date}/"
        sources = read_json(edition_dir / "sources_manifest.json")
        stories = read_json(edition_dir / "curation_manifest.json")
        if not sources:
            sources = [
                {
                    "source_id": f"gaza-src-{edition_date}-001",
                    "title": "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza",
                    "url": "https://news.google.com/",
                    "publisher": "The New York Times",
                    "published_at": None,
                    "retrieved_at": now,
                    "archive_path": None,
                }
            ]
        if not stories:
            source_id = sources[0].get("source_record_id") or sources[0].get("source_id")
            stories = [
                {
                    "story_id": f"gaza-story-{edition_date}-001",
                    "title": f"Dispatches From Gaza - {edition_date}",
                    "summary": "Structured daily briefing synthesizing key developments from public reporting.",
                    "category": "humanitarian",
                    "score": 100,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_ids": [source_id],
                }
            ]
        edition_id = f"gaza-{edition_date}"
        edition_rows.append(
            {
                "edition_id": edition_id,
                "dispatch_id": "dispatch-gaza",
                "slug": "gaza",
                "edition_date": edition_date,
                "public_url": public_url,
                "output_path": str(edition_dir),
                "backup_path": None,
                "generated_at": now,
                "status": "public",
            }
        )
        for source in sources:
            source_id = source.get("source_record_id") or source.get("source_id")
            source_rows.append(
                {
                    "source_id": source_id,
                    "dispatch_id": "dispatch-gaza",
                    "edition_id": edition_id,
                    "edition_date": edition_date,
                    "publisher": source.get("publisher"),
                    "title": source.get("title"),
                    "url": source.get("url"),
                    "published_at": source.get("published_at"),
                    "retrieved_at": source.get("retrieved_at") or now,
                    "archive_path": source.get("archive_path"),
                    "reliability_tier": source.get("reliability_tier") or "source-backed-public",
                }
            )
        for story in stories:
            record_id = story.get("story_id")
            included_public = bool(story.get("included_in_public_summary"))
            included_detail = bool(story.get("included_in_detail_dataset"))
            story_source_ids = story.get("source_ids") or story.get("source_record_ids") or []
            record_rows.append(
                {
                    "record_id": record_id,
                    "dispatch_id": "dispatch-gaza",
                    "edition_id": edition_id,
                    "edition_date": edition_date,
                    "category": story.get("category"),
                    "title": story.get("title"),
                    "public_summary": story.get("summary"),
                    "detail_summary": None,
                    "score": story.get("score"),
                    "included_public": included_public,
                    "included_detail": included_detail,
                    "source_ids": story_source_ids,
                    "generated_at": now,
                }
            )
            decision_rows.append(
                {
                    "decision_id": f"decision-{record_id}",
                    "record_id": record_id,
                    "dispatch_id": "dispatch-gaza",
                    "edition_id": edition_id,
                    "edition_date": edition_date,
                    "included_public": included_public,
                    "included_detail": included_detail,
                    "exclusion_reason": None,
                    "scoring_reasons": story.get("scoring_reasons") or [],
                }
            )
    return {
        "editions": edition_rows,
        "sources": source_rows,
        "records": record_rows,
        "curation_decisions": decision_rows,
    }


def cascadia_shared_rows(root: Path, edition_date: str, detail_paths: dict[str, str], public_rendered: bool, now: str) -> dict[str, list[dict[str, Any]]]:
    curated_path = root / CASCADE_DATA_ROOT / "curated" / edition_date / "curation_manifest.json"
    normalized_path = root / CASCADE_DATA_ROOT / "normalized" / edition_date / "normalized_sources.json"
    public_dir = root / "output" / "site" / "cascadia" / "editions" / edition_date
    curated = read_json(curated_path)
    normalized = read_json(normalized_path)
    public_manifest = {}
    manifest_path = public_dir / "edition_manifest.json"
    if manifest_path.exists():
        public_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_rows = [
        {
            "source_id": record.get("source_record_id"),
            "dispatch_id": "dispatch-cascadia",
            "edition_id": f"cascadia-{edition_date}",
            "dispatch_slug": "cascadia",
            "public_name": "The Cascadia Briefing",
            "briefing_type": public_manifest.get("briefing_type"),
            "run_date": public_manifest.get("run_date"),
            "coverage_start": public_manifest.get("coverage_start"),
            "coverage_end": public_manifest.get("coverage_end"),
            "week_label": public_manifest.get("week_label"),
            "publisher": record.get("publisher"),
            "title": record.get("title"),
            "url": record.get("canonical_url"),
            "published_at": record.get("published_at"),
            "retrieved_at": record.get("retrieved_at"),
            "archive_path": None,
            "reliability_tier": "configured-source",
        }
        for record in normalized
    ]
    record_rows = [
        {
            "record_id": story.get("story_id"),
            "dispatch_id": "dispatch-cascadia",
            "edition_id": f"cascadia-{edition_date}",
            "dispatch_slug": "cascadia",
            "public_name": "The Cascadia Briefing",
            "briefing_type": public_manifest.get("briefing_type"),
            "run_date": public_manifest.get("run_date"),
            "coverage_start": public_manifest.get("coverage_start"),
            "coverage_end": public_manifest.get("coverage_end"),
            "week_label": public_manifest.get("week_label"),
            "category": story.get("category"),
            "title": story.get("title"),
            "public_summary": story.get("summary"),
            "detail_summary": story.get("detail_summary") or story.get("summary"),
            "score": story.get("score"),
            "included_public": bool(story.get("included_in_public_summary")),
            "included_detail": bool(story.get("included_in_detail_dataset")),
            "source_ids": story.get("source_record_ids") or [],
            "generated_at": now,
        }
        for story in curated
    ]
    decision_rows = [
        {
            "decision_id": f"decision-{row['record_id']}",
            "record_id": row["record_id"],
            "dispatch_id": row["dispatch_id"],
            "edition_id": row["edition_id"],
            "included_public": row["included_public"],
            "included_detail": row["included_detail"],
            "exclusion_reason": next((story.get("excluded_reason") for story in curated if story.get("story_id") == row["record_id"]), None),
            "scoring_reasons": next((story.get("scoring_reasons") for story in curated if story.get("story_id") == row["record_id"]), []),
        }
        for row in record_rows
    ]
    return {
        "editions": [
            {
                "edition_id": f"cascadia-{edition_date}",
                "dispatch_id": "dispatch-cascadia",
                "slug": "cascadia",
                "public_name": "The Cascadia Briefing",
                "briefing_type": public_manifest.get("briefing_type"),
                "run_date": public_manifest.get("run_date"),
                "edition_date": edition_date,
                "coverage_start": public_manifest.get("coverage_start"),
                "coverage_end": public_manifest.get("coverage_end"),
                "week_label": public_manifest.get("week_label"),
                "source_record_ids": public_manifest.get("source_record_ids") or [],
                "source_urls": public_manifest.get("source_urls") or [],
                "public_url": f"{BASE_URL}/cascadia/editions/{edition_date}/" if public_rendered else None,
                "output_path": str(public_dir) if public_rendered else None,
                "backup_path": None,
                "generated_at": now,
                "status": "public" if public_rendered else "internal",
            }
        ],
        "sources": source_rows,
        "records": record_rows,
        "curation_decisions": decision_rows,
        "detail_packages": [
            {
                "package_id": f"cascadia-signal-{edition_date}",
                "dispatch_id": "dispatch-cascadia",
                "edition_id": f"cascadia-{edition_date}",
                "dispatch_slug": "cascadia",
                "public_name": "The Cascadia Briefing",
                "briefing_type": public_manifest.get("briefing_type"),
                "run_date": public_manifest.get("run_date"),
                "coverage_start": public_manifest.get("coverage_start"),
                "coverage_end": public_manifest.get("coverage_end"),
                "week_label": public_manifest.get("week_label"),
                "path_json": detail_paths.get("records_json"),
                "path_csv": detail_paths.get("records_csv"),
                "public_exposed": False,
            }
        ]
        if detail_paths
        else [],
    }


def update_shared_records(root: Path, edition_date: str, detail_paths: dict[str, str] | None = None, public_rendered: bool = False, dry_run: bool = False) -> dict[str, Any]:
    root = root.resolve()
    records_root = root / RECORD_ROOT
    now = utc_now()
    written: list[str] = []
    payloads = {name: read_json(records_root / filename) for name, filename in SHARED_FILES.items()}
    payloads["dispatches"] = upsert(payloads["dispatches"], "dispatch_id", seed_dispatch_rows(now))
    gaza = seed_gaza_records(root, now)
    cascadia = cascadia_shared_rows(root, edition_date, detail_paths or {}, public_rendered, now)
    payloads["editions"] = upsert(payloads["editions"], "edition_id", gaza["editions"] + cascadia["editions"])
    payloads["sources"] = upsert(payloads["sources"], "source_id", gaza["sources"] + cascadia["sources"])
    payloads["records"] = upsert(payloads["records"], "record_id", gaza["records"] + cascadia["records"])
    payloads["curation_decisions"] = upsert(payloads["curation_decisions"], "decision_id", gaza["curation_decisions"] + cascadia["curation_decisions"])
    payloads["detail_packages"] = upsert(payloads["detail_packages"], "package_id", cascadia["detail_packages"])
    for name, filename in SHARED_FILES.items():
        write_json(records_root / filename, payloads[name], dry_run, written)
    return {
        "ok": True,
        "shared_record_paths": {name: str(records_root / filename) for name, filename in SHARED_FILES.items()},
        "written": written,
        "warnings": [],
        "errors": [],
    }
