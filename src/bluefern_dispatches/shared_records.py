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
            "description": "Regional Cascadia systems briefing with a private Signal detail package.",
            "is_free_public": True,
            "has_detail_tier": True,
            "created_at": "2026-05-03T00:00:00Z",
            "updated_at": now,
        },
    ]


def seed_gaza_records(root: Path, now: str) -> dict[str, list[dict[str, Any]]]:
    edition_date = "2026-05-03"
    edition_dir = root / "output" / "site" / "gaza" / "editions" / edition_date
    public_url = f"{BASE_URL}/gaza/editions/{edition_date}/"
    sources_path = edition_dir / "sources_manifest.json"
    curation_path = edition_dir / "curation_manifest.json"
    sources = read_json(sources_path)
    stories = read_json(curation_path)
    if not sources:
        sources = [
            {
                "source_id": "gaza-src-001",
                "title": "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza",
                "url": "https://news.google.com/",
                "publisher": "The New York Times",
                "published_at": None,
                "retrieved_at": now,
                "archive_path": None,
            }
        ]
    if not stories:
        stories = [
            {
                "story_id": "gaza-story-001",
                "title": f"Dispatches From Gaza - {edition_date}",
                "summary": "Structured daily briefing synthesizing key developments from public reporting.",
                "category": "humanitarian",
                "score": 100,
                "included_in_public_summary": True,
                "included_in_detail_dataset": False,
                "source_ids": [sources[0]["source_id"]],
            }
        ]
    source_rows = [
        {
            "source_id": source.get("source_id"),
            "dispatch_id": "dispatch-gaza",
            "edition_id": f"gaza-{edition_date}",
            "publisher": source.get("publisher"),
            "title": source.get("title"),
            "url": source.get("url"),
            "published_at": source.get("published_at"),
            "retrieved_at": source.get("retrieved_at") or now,
            "archive_path": source.get("archive_path"),
            "reliability_tier": source.get("reliability_tier") or "source-backed-public",
        }
        for source in sources
    ]
    record_rows = [
        {
            "record_id": story.get("story_id"),
            "dispatch_id": "dispatch-gaza",
            "edition_id": f"gaza-{edition_date}",
            "category": story.get("category"),
            "title": story.get("title"),
            "public_summary": story.get("summary"),
            "detail_summary": None,
            "score": story.get("score"),
            "included_public": bool(story.get("included_in_public_summary")),
            "included_detail": bool(story.get("included_in_detail_dataset")),
            "source_ids": story.get("source_ids") or [],
            "generated_at": now,
        }
        for story in stories
    ]
    decision_rows = [
        {
            "decision_id": f"decision-{row['record_id']}",
            "record_id": row["record_id"],
            "dispatch_id": row["dispatch_id"],
            "edition_id": row["edition_id"],
            "included_public": row["included_public"],
            "included_detail": row["included_detail"],
            "exclusion_reason": None,
            "scoring_reasons": [],
        }
        for row in record_rows
    ]
    return {
        "editions": [
            {
                "edition_id": f"gaza-{edition_date}",
                "dispatch_id": "dispatch-gaza",
                "slug": "gaza",
                "edition_date": edition_date,
                "public_url": public_url,
                "output_path": str(edition_dir),
                "backup_path": None,
                "generated_at": now,
                "status": "public",
            }
        ],
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
    source_rows = [
        {
            "source_id": record.get("source_record_id"),
            "dispatch_id": "dispatch-cascadia",
            "edition_id": f"cascadia-{edition_date}",
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
                "edition_date": edition_date,
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
