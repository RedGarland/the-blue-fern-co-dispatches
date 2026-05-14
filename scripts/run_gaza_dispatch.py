from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.generator import (
    BASE_URL,
    DispatchConfig,
    footer,
    header,
    page,
    render_archive_for_dates,
    render_dispatch_index_for_dates,
    render_rss_for_dates,
)
from bluefern_dispatches.gaza_sources import filter_recent_duplicate_sources
from bluefern_dispatches.gaza_sources import canonicalize_url, extract_canonical_from_google_wrapper
from bluefern_dispatches.gaza_sources import rank_gaza_candidates
from bluefern_dispatches.story_dedupe import dedupe_public_stories


DISPATCH_SLUG = "gaza"
DISPATCH_ID = "dispatch-gaza"
DISPATCH_NAME = "Dispatches From Gaza"
DISPATCH_TAGLINE = "Daily briefing"
BACKUP_ROOT = Path(
    os.getenv("BLUEFERN_BACKUP_ROOT", str(ROOT / "output" / "tmp-backups-pages"))
) / DISPATCH_SLUG
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_SOURCE_FIELDS = {
    "source_record_id",
    "title",
    "url",
    "publisher",
    "published_at",
    "retrieved_at",
    "summary_or_snippet",
    "source_type",
    "region_scope",
    "category_hint",
    "reliability_tier",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_text(path: Path, content: str, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_file(source: Path, target: Path, dry_run: bool, wrote: list[str], warnings: list[str]) -> None:
    if not source.exists():
        warnings.append(f"Missing file: {source}")
        return
    wrote.append(str(target))
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def ensure_gaza_source_tree(root: Path, dry_run: bool, wrote: list[str]) -> None:
    for relative in (
        "data/dispatches/gaza",
        "data/dispatches/gaza/sources",
        "data/dispatches/gaza/raw",
        "data/dispatches/gaza/normalized",
        "data/dispatches/gaza/curated",
        "data/dispatches/gaza/editions",
    ):
        path = root / relative
        wrote.append(str(path))
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)


def load_manual_sources(root: Path, edition_date: str) -> tuple[Path, list[dict[str, Any]]]:
    path = root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / "manual_sources.json"
    if not path.exists():
        raise FileNotFoundError(f"manual source file is required: {path}")
    payload = read_json(path)
    records = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("manual_sources.json must be a list or an object with a sources list")
    return path, records


def normalize_sources(records: list[dict[str, Any]], edition_date: str, now: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append(f"source record {index} is not an object")
            continue
        missing = sorted(field for field in REQUIRED_SOURCE_FIELDS if not str(record.get(field) or "").strip())
        if missing:
            errors.append(f"source record {index} missing required fields: {', '.join(missing)}")
            continue
        url = str(record["url"]).strip()
        if not url.startswith(("http://", "https://")):
            errors.append(f"source record {index} has invalid URL: {url}")
            continue
        source_id = str(record["source_record_id"]).strip()
        key = (url.lower(), str(record["title"]).strip().lower())
        if key in seen:
            warnings.append(f"deduped duplicate source record: {source_id}")
            continue
        seen.add(key)
        canonical_url = str(record.get("canonical_url") or "").strip()
        wrapper_url = str(record.get("wrapper_url") or "").strip()
        canonicalization_status = str(record.get("canonicalization_status") or "").strip()
        if not canonical_url:
            extracted, status = extract_canonical_from_google_wrapper(url)
            canonical_url = extracted or canonicalize_url(url)
            canonicalization_status = canonicalization_status or status
            if status != "not_wrapper":
                wrapper_url = wrapper_url or url
        normalized.append(
            {
                "source_record_id": source_id,
                "source_id": source_id,
                "title": str(record["title"]).strip(),
                "url": url,
                "canonical_url": canonical_url,
                "canonical_url_attempted": bool(record.get("canonical_url_attempted") or wrapper_url),
                "canonicalization_status": canonicalization_status or ("direct_url" if not wrapper_url else "wrapper_unresolved"),
                "wrapper_url": wrapper_url or None,
                "publisher": str(record["publisher"]).strip(),
                "published_at": str(record["published_at"]).strip(),
                "retrieved_at": str(record.get("retrieved_at") or now).strip(),
                "summary_or_snippet": str(record["summary_or_snippet"]).strip(),
                "source_type": str(record["source_type"]).strip(),
                "region_scope": str(record["region_scope"]).strip(),
                "category_hint": str(record["category_hint"]).strip(),
                "reliability_tier": str(record["reliability_tier"]).strip(),
                "dispatch_slug": DISPATCH_SLUG,
                "edition_date": edition_date,
                "used_in_story_ids": [f"gaza-story-{edition_date}-{len(normalized) + 1:03d}"],
                "claim_ids": [],
            }
        )
    ranked = rank_gaza_candidates(normalized, edition_date)
    return ranked, warnings, errors


def curate_stories(sources: list[dict[str, Any]], edition_date: str, now: str) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        source_score = int(source.get("candidate_score") or 0)
        score = max(1, source_score)
        ranking_reasons = list(source.get("ranking_reasons") or [])
        breakdown = dict(source.get("candidate_score_breakdown") or {})
        stories.append(
            {
                "story_id": f"gaza-story-{edition_date}-{index:03d}",
                "title": source["title"],
                "summary": source["summary_or_snippet"],
                "category": source["category_hint"],
                "score": score,
                "scoring_reasons": ranking_reasons or ["Included because a complete project-local source record was provided."],
                "candidate_score_breakdown": breakdown,
                "included_in_public_summary": True,
                "included_in_detail_dataset": False,
                "source_record_ids": [source["source_record_id"]],
                "source_ids": [source["source_record_id"]],
                "source_urls": [source["url"]],
                "publisher_names": [source["publisher"]],
                "generated_at": now,
            }
        )
    return stories


def render_gaza_edition(edition_date: str, stories: list[dict[str, Any]], sources: list[dict[str, Any]]) -> str:
    source_by_id = {source["source_record_id"]: source for source in sources}

    def render_story(story: dict[str, Any]) -> None:
        chunks.append(f"<article><h3>{html.escape(story['title'])}</h3>")
        chunks.append(f"<p>{html.escape(story['summary'])}</p>")
        chunks.append("<p><strong>Sources</strong></p><ul>")
        for source_id in story["source_record_ids"]:
            source = source_by_id[source_id]
            chunks.append(
                f'<li><a href="{html.escape(source["url"])}" target="_blank" rel="noopener noreferrer">'
                f'{html.escape(source["title"])}</a> - {html.escape(source["publisher"])}</li>'
            )
        chunks.append("</ul></article>")

    chunks: list[str] = []
    chunks.append("<h1>Dispatches From Gaza</h1>")
    if stories:
        chunks.append("<h2>At A Glance</h2>")
        chunks.append("<ul>")
        for story in stories:
            chunks.append(f"<li>{html.escape(story['title'])}</li>")
        chunks.append("</ul>")
        chunks.append("<h2>Top Story</h2>")
        render_story(stories[0])
        chunks.append("<h2>Other Developments</h2>")
        if len(stories) > 1:
            for story in stories[1:]:
                render_story(story)
        else:
            chunks.append("<p>No additional source-backed developments cleared the public threshold for this edition.</p>")
    else:
        chunks.append("<p>No source-backed Gaza stories were generated for this date. Add project-local source records before publishing factual coverage.</p>")
        chunks.append("<h2>Sources</h2><p>No source records were available.</p>")
    chunks.append("<h2>Source Note</h2>")
    chunks.append("<p>This free public edition is generated only from project-local source records listed in the manifests.</p>")
    body_chunks = "\n    ".join(chunks)
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/gaza/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/gaza-logo.png" alt="Dispatches From Gaza">
    </section>
    <p class="eyebrow">Daily briefing / {edition_date}</p>
    {body_chunks}
  </main>
{footer("../../")}"""
    return page(f"{DISPATCH_NAME} - {edition_date}", f"{BASE_URL}/gaza/editions/{edition_date}/", "../../assets/site.css", body, DISPATCH_NAME)


def discover_edition_dates(site_root: Path) -> list[str]:
    editions_root = site_root / DISPATCH_SLUG / "editions"
    if not editions_root.exists():
        return []
    def _is_listable(path: Path) -> bool:
        manifest_path = path / "edition_manifest.json"
        sources_path = path / "sources_manifest.json"
        curation_path = path / "curation_manifest.json"
        if not manifest_path.exists() or not sources_path.exists() or not curation_path.exists():
            return False
        try:
            manifest = read_json(manifest_path)
            sources = read_json(sources_path)
            stories = read_json(curation_path)
        except Exception:
            return False
        if not isinstance(manifest, dict) or not isinstance(sources, list) or not isinstance(stories, list):
            return False
        if len(sources) <= 0 or len(stories) <= 0:
            return False
        return not any(
            "No new source-backed Gaza developments after cross-edition dedupe" in str(item)
            for item in (manifest.get("errors") or [])
        )

    return sorted(
        (
            path.name
            for path in editions_root.iterdir()
            if path.is_dir() and DATE_RE.match(path.name) and (path / "index.html").exists() and _is_listable(path)
        ),
        reverse=True,
    )


def render_archive_index_rss(root: Path, edition_date: str, dry_run: bool, wrote: list[str], include_current: bool = True) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(
        slug=DISPATCH_SLUG,
        name=DISPATCH_NAME,
        edition_date=edition_date,
        tagline=DISPATCH_TAGLINE,
        logo="gaza-logo.png",
        sources=[],
        stories=[],
        detail_artifacts=[],
    )
    dates = discover_edition_dates(site_root)
    if include_current and edition_date not in dates:
        dates = sorted([*dates, edition_date], reverse=True)
    gaza_root = site_root / DISPATCH_SLUG
    write_text(gaza_root / "index.html", render_dispatch_index_for_dates(dispatch, dates), dry_run, wrote)
    write_text(gaza_root / "archive.html", render_archive_for_dates(dispatch, dates), dry_run, wrote)
    write_text(gaza_root / "rss.xml", render_rss_for_dates(dispatch, dates), dry_run, wrote)


def build_manifests(
    root: Path,
    edition_date: str,
    sources: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    generated_at: str,
    warnings: list[str],
    errors: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    dispatch_dir = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date
    backup_dir = BACKUP_ROOT / edition_date
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/gaza/editions/{edition_date}/",
        "local_output_path": str(site_dir),
        "local_dispatch_output_path": str(dispatch_dir),
        "local_backup_path": str(backup_dir),
        "template_version": "gaza-source-record-v1",
        "source_count": len(sources),
        "story_count": len(stories),
        "source_manifest_path": str(site_dir / "sources_manifest.json"),
        "curation_manifest_path": str(site_dir / "curation_manifest.json"),
        "free_public_artifacts": [
            str(site_dir / "index.html"),
            str(site_dir / "edition_manifest.json"),
            str(site_dir / "sources_manifest.json"),
            str(site_dir / "curation_manifest.json"),
        ],
        "paid_or_detail_artifacts": [],
        "detail_artifacts_publicly_exposed": False,
        "is_free_public": True,
        "has_detail_tier": False,
        "public_exposed": True,
        "warnings": warnings,
        "errors": errors,
    }
    run_manifest = {
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": generated_at,
        "source_workflow": "project-local-manual-source-records",
        "did_not_invent_sources": True,
        "old_project_dependency": False,
        "warnings": warnings,
        "errors": errors,
    }
    return edition_manifest, sources, stories, run_manifest


def upsert(rows: list[dict[str, Any]], key: str, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {str(row[key]): row for row in rows if key in row}
    for row in incoming:
        by_key[str(row[key])] = row
    return sorted(by_key.values(), key=lambda row: str(row.get(key, "")))


def read_record_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    return payload if isinstance(payload, list) else []


def update_shared_records(
    root: Path,
    edition_date: str,
    sources: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    generated_at: str,
    dry_run: bool,
    wrote: list[str],
) -> None:
    records_root = root / "data" / "records"
    records_root.mkdir(parents=True, exist_ok=True)
    files = {
        "dispatches": records_root / "dispatches.json",
        "editions": records_root / "editions.json",
        "sources": records_root / "sources.json",
        "records": records_root / "records.json",
        "curation_decisions": records_root / "curation_decisions.json",
        "detail_packages": records_root / "detail_packages.json",
    }
    edition_id = f"gaza-{edition_date}"
    dispatches = upsert(
        read_record_file(files["dispatches"]),
        "dispatch_id",
        [
            {
                "dispatch_id": DISPATCH_ID,
                "slug": DISPATCH_SLUG,
                "dispatch_slug": DISPATCH_SLUG,
                "public_name": DISPATCH_NAME,
                "internal_name": "Gaza Dispatch",
                "description": "Free public Gaza briefing compiled from traceable project-local source records.",
                "is_free_public": True,
                "has_detail_tier": False,
                "public_exposed": True,
                "created_at": "2026-05-03T00:00:00Z",
                "updated_at": generated_at,
            }
        ],
    )
    site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
    backup_dir = BACKUP_ROOT / edition_date
    editions = upsert(
        read_record_file(files["editions"]),
        "edition_id",
        [
            {
                "edition_id": edition_id,
                "dispatch_id": DISPATCH_ID,
                "dispatch_slug": DISPATCH_SLUG,
                "slug": DISPATCH_SLUG,
                "edition_date": edition_date,
                "public_url": f"{BASE_URL}/gaza/editions/{edition_date}/",
                "output_path": str(site_dir),
                "backup_path": str(backup_dir),
                "generated_at": generated_at,
                "status": "public",
                "is_free_public": True,
                "public_exposed": True,
                "has_detail_tier": False,
            }
        ],
    )
    source_rows = [
        {
            "source_id": source["source_record_id"],
            "source_record_id": source["source_record_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_id": edition_id,
            "publisher": source["publisher"],
            "title": source["title"],
            "url": source["url"],
            "published_at": source["published_at"],
            "retrieved_at": source["retrieved_at"],
            "archive_path": None,
            "reliability_tier": source["reliability_tier"],
        }
        for source in sources
    ]
    record_rows = [
        {
            "record_id": story["story_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_id": edition_id,
            "category": story["category"],
            "title": story["title"],
            "public_summary": story["summary"],
            "detail_summary": None,
            "score": story["score"],
            "included_public": True,
            "included_detail": False,
            "source_ids": story["source_record_ids"],
            "generated_at": generated_at,
            "is_free_public": True,
            "public_exposed": True,
        }
        for story in stories
    ]
    decisions = [
        {
            "decision_id": f"decision-{story['story_id']}",
            "record_id": story["story_id"],
            "dispatch_id": DISPATCH_ID,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_id": edition_id,
            "included_public": True,
            "included_detail": False,
            "exclusion_reason": None,
            "scoring_reasons": story["scoring_reasons"],
        }
        for story in stories
    ]
    existing_sources = [row for row in read_record_file(files["sources"]) if row.get("edition_id") != edition_id]
    existing_records = [row for row in read_record_file(files["records"]) if row.get("edition_id") != edition_id]
    existing_decisions = [row for row in read_record_file(files["curation_decisions"]) if row.get("edition_id") != edition_id]
    write_json(files["dispatches"], dispatches, dry_run, wrote)
    write_json(files["editions"], upsert(read_record_file(files["editions"]), "edition_id", editions), dry_run, wrote)
    write_json(files["sources"], upsert(existing_sources, "source_id", source_rows), dry_run, wrote)
    write_json(files["records"], upsert(existing_records, "record_id", record_rows), dry_run, wrote)
    write_json(files["curation_decisions"], upsert(existing_decisions, "decision_id", decisions), dry_run, wrote)
    write_json(files["detail_packages"], read_record_file(files["detail_packages"]), dry_run, wrote)


def run_gaza_dispatch(root: Path, edition_date: str, from_manual_sources: bool, dry_run: bool, render: bool, all_steps: bool) -> dict[str, Any]:
    edition_date = validate_date(edition_date)
    generated_at = utc_now()
    warnings: list[str] = []
    errors: list[str] = []
    wrote: list[str] = []
    ensure_gaza_source_tree(root, dry_run, wrote)
    if not from_manual_sources:
        raise ValueError("Gaza generation currently requires --from-manual-sources")
    manual_path, manual_records = load_manual_sources(root, edition_date)
    raw_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "raw" / edition_date
    normalized_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "normalized" / edition_date
    curated_dir = root / "data" / "dispatches" / DISPATCH_SLUG / "curated" / edition_date
    write_json(raw_dir / "raw_sources.json", manual_records, dry_run, wrote)
    normalized, norm_warnings, norm_errors = normalize_sources(manual_records, edition_date, generated_at)
    warnings.extend(norm_warnings)
    errors.extend(norm_errors)
    normalized, cross_edition_report = filter_recent_duplicate_sources(root, edition_date, normalized, lookback_days=7)
    write_json(normalized_dir / "normalized_sources.json", normalized, dry_run, wrote)
    write_json(root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "dedupe_report.json", cross_edition_report, dry_run, wrote)
    if cross_edition_report.get("suppressed_candidate_count", 0):
        warnings.append(
            f"suppressed {cross_edition_report['suppressed_candidate_count']} repeated/stale candidate sources via cross-edition dedupe"
        )
    if cross_edition_report.get("input_candidate_count", 0) > 0 and not normalized:
        errors.append("No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition.")
    provider_diagnostics = [
        {
            "provider": "manual_sources_json",
            "status": "ok" if manual_records else "no_candidates",
            "raw_items": len(manual_records),
            "accepted": int(cross_edition_report.get("kept_candidate_count", 0)),
        }
    ]
    rejected_by_reason = {
        "normalization_errors": len(norm_errors),
        "cross_edition_duplicates": int(cross_edition_report.get("suppressed_candidate_count", 0)),
    }
    stage_counts = {
        "registry_sources": 1,
        "providers_attempted": 1,
        "providers_successful": 1 if manual_records else 0,
        "raw_candidates": len(manual_records),
        "normalized_candidates": len(normalized) + int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "accepted_before_dedupe": int(cross_edition_report.get("input_candidate_count", 0)),
    }
    collection_report = {
        "edition_date": edition_date,
        "lookback_window_days": 7,
        "source_providers_attempted": provider_diagnostics,
        "provider_failures": [],
        "raw_candidate_count": len(manual_records),
        "normalized_candidate_count": len(normalized) + int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "accepted_candidate_count_before_dedupe": int(cross_edition_report.get("input_candidate_count", 0)),
        "kept_after_dedupe": int(cross_edition_report.get("kept_candidate_count", 0)),
        "suppressed_after_dedupe": int(cross_edition_report.get("suppressed_candidate_count", 0)),
        "rejection_counts_by_reason": rejected_by_reason,
        "top_rejected_examples": norm_errors[:5],
        "no_story_credibility_decision": "",
        "providers_attempted_count": 1,
        "providers_successful_count": 1 if manual_records else 0,
        "provider_diagnostics": provider_diagnostics,
        "stage_counts": stage_counts,
        "google_wrapper_count": int(cross_edition_report.get("google_wrapper_count", 0)),
        "canonical_publisher_url_count": int(cross_edition_report.get("canonical_publisher_url_count", 0)),
    }
    if collection_report["raw_candidate_count"] == 0:
        collection_report["no_story_credibility_decision"] = "no_candidates_found"
    elif collection_report["accepted_candidate_count_before_dedupe"] > 0 and collection_report["kept_after_dedupe"] == 0:
        collection_report["no_story_credibility_decision"] = "all_candidates_deduped"
    elif norm_errors:
        collection_report["no_story_credibility_decision"] = "candidates_rejected"
    else:
        collection_report["no_story_credibility_decision"] = "stories_available"
    write_json(root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "collection_report.json", collection_report, dry_run, wrote)
    stories = curate_stories(normalized, edition_date, generated_at)
    dedupe_result = dedupe_public_stories(root, DISPATCH_SLUG, edition_date, stories, dry_run=dry_run, written=wrote)
    stories = dedupe_result.stories
    if len(normalized) == 0:
        errors.append("No valid traceable Gaza sources survived normalization and dedupe; refusing public edition generation.")
    if len(stories) == 0:
        errors.append("No source-backed Gaza stories survived curation/dedupe; refusing public edition generation.")
    write_json(curated_dir / "curation_manifest.json", stories, dry_run, wrote)
    should_render = render or all_steps
    if should_render and not errors:
        html_content = render_gaza_edition(edition_date, stories, normalized)
        edition_manifest, sources_manifest, curation_manifest, run_manifest = build_manifests(
            root, edition_date, normalized, stories, generated_at, warnings, errors
        )
        for base in (
            root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date,
            root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date,
        ):
            write_text(base / "index.html", html_content, dry_run, wrote)
            write_json(base / "edition_manifest.json", edition_manifest, dry_run, wrote)
            write_json(base / "sources_manifest.json", sources_manifest, dry_run, wrote)
            write_json(base / "curation_manifest.json", curation_manifest, dry_run, wrote)
        for asset in ("site.css", "gaza-logo.png", "bluefern.png"):
            copy_file(root / "assets" / asset, root / "output" / "site" / DISPATCH_SLUG / "assets" / asset, dry_run, wrote, warnings)
        render_archive_index_rss(root, edition_date, dry_run, wrote, include_current=True)
        backup_dir = BACKUP_ROOT / edition_date
        write_text(backup_dir / "index.html", html_content, dry_run, wrote)
        write_json(backup_dir / "edition_manifest.json", edition_manifest, dry_run, wrote)
        write_json(backup_dir / "sources_manifest.json", sources_manifest, dry_run, wrote)
        write_json(backup_dir / "curation_manifest.json", curation_manifest, dry_run, wrote)
        write_json(backup_dir / "run_manifest.json", run_manifest, dry_run, wrote)
        update_shared_records(root, edition_date, normalized, stories, generated_at, dry_run, wrote)
    elif should_render:
        failed_manifest = {
            "dispatch_name": DISPATCH_NAME,
            "dispatch_slug": DISPATCH_SLUG,
            "edition_date": edition_date,
            "generated_at": generated_at,
            "public_url": None,
            "local_output_path": None,
            "local_dispatch_output_path": str(root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date),
            "local_backup_path": None,
            "template_version": "gaza-source-record-v1",
            "source_count": len(normalized),
            "story_count": len(stories),
            "free_public_artifacts": [],
            "paid_or_detail_artifacts": [],
            "detail_artifacts_publicly_exposed": False,
            "is_free_public": True,
            "has_detail_tier": False,
            "public_exposed": False,
            "warnings": warnings,
            "errors": errors,
        }
        dispatch_dir = root / "output" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date
        write_json(dispatch_dir / "edition_manifest.json", failed_manifest, dry_run, wrote)
        write_json(dispatch_dir / "sources_manifest.json", normalized, dry_run, wrote)
        write_json(dispatch_dir / "curation_manifest.json", stories, dry_run, wrote)
        site_dir = root / "output" / "site" / DISPATCH_SLUG / "editions" / edition_date
        if site_dir.exists():
            wrote.append(str(site_dir))
            if not dry_run:
                shutil.rmtree(site_dir)
        render_archive_index_rss(root, edition_date, dry_run, wrote, include_current=False)
    return {
        "ok": not errors,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "manual_source_path": str(manual_path),
        "source_count": len(normalized),
        "story_count": len(stories),
        "dry_run": dry_run,
        "wrote": wrote,
        "warnings": warnings,
        "errors": errors,
        "is_free_public": True,
        "has_detail_tier": False,
        "public_exposed": not errors,
        "backup_root": str(BACKUP_ROOT),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate self-contained source-backed Gaza editions.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--historical", action="store_true", help="Generate a historical Gaza edition.")
    parser.add_argument("--from-manual-sources", action="store_true", help="Use project-local manual source records.")
    parser.add_argument("--dry-run", action="store_true", help="Report writes without changing files.")
    parser.add_argument("--render", action="store_true", help="Render the public edition and manifests.")
    parser.add_argument("--all", action="store_true", help="Run all generation stages.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_gaza_dispatch(
            ROOT,
            args.date,
            from_manual_sources=args.from_manual_sources,
            dry_run=args.dry_run,
            render=args.render,
            all_steps=args.all,
        )
    except Exception as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
