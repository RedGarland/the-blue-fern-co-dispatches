from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_ingest import CASCADE_DATA_ROOT
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_weekly import format_coverage_label, week_label
from bluefern_dispatches.generator import (
    BASE_URL,
    CASCADIA_LOGO_ASSET,
    CASCADIA_PUBLIC_DESCRIPTION,
    TEMPLATE_VERSION,
    DispatchConfig,
    discover_public_edition_dates,
    footer,
    header,
    page,
    render_archive_for_dates,
    render_dispatch_index_for_dates,
    render_rss_for_dates,
    write_text as generator_write_text,
)


DISPATCH_NAME = "The Cascadia Briefing"
INTERNAL_PRODUCT_NAME = "Cascadia Signal"
DISPATCH_SLUG = "cascadia"
SHORT_PUBLIC_DESCRIPTION = "Weekly source-backed regional briefings for Washington, Oregon, and Idaho."


def public_stories(curated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [story for story in curated if story.get("included_in_public_summary")]


def validate_public_stories(stories: list[dict[str, Any]]) -> list[str]:
    errors = []
    for story in stories:
        if not story.get("source_record_ids") or not story.get("source_urls"):
            errors.append(f"public story lacks source trace: {story.get('story_id')}")
    return errors


def sources_manifest_from_curated(
    curated: list[dict[str, Any]],
    edition_date: str,
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str | None = None,
    coverage_label: str | None = None,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for story in curated:
        for record in story.get("source_records", []):
            item = {
                "source_record_id": record["source_record_id"],
                "source_id": record.get("source_id"),
                "title": record.get("title"),
                "url": record.get("source_url") or record.get("url") or record.get("canonical_url"),
                "publisher": record.get("publisher"),
                "published_at": record.get("published_at"),
                "retrieved_at": record.get("retrieved_at"),
                "archive_path": None,
                "used_in_story_ids": [story["story_id"]],
                "claim_ids": [story["story_id"]],
                "dispatch_slug": DISPATCH_SLUG,
                "public_name": DISPATCH_NAME,
                "briefing_type": briefing_type,
                "run_date": run_date,
                "edition_date": edition_date,
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
                "coverage_label": coverage_label,
                "region_scope": record.get("region_scope"),
                "category_hint": record.get("category_hint"),
            }
            for field in [
                "source_type",
                "derived_from_edition_date",
                "derived_from_edition_path",
                "derived_from_manifest_path",
                "original_source_record_id",
                "source_url",
                "source_title",
                "weekly_date_basis",
                "traceability_note",
            ]:
                if field in record:
                    item[field] = record.get(field)
            by_id[record["source_record_id"]] = item
    return sorted(by_id.values(), key=lambda item: item["source_record_id"])


def public_curation_manifest(
    curated: list[dict[str, Any]],
    run_date: str | None = None,
    edition_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str | None = None,
    coverage_label: str | None = None,
) -> list[dict[str, Any]]:
    public = []
    for story in curated:
        item = dict(story)
        item.pop("source_records", None)
        item["dispatch_slug"] = DISPATCH_SLUG
        item["public_name"] = DISPATCH_NAME
        item["briefing_type"] = briefing_type
        item["run_date"] = run_date
        item["edition_date"] = edition_date
        item["coverage_start"] = coverage_start
        item["coverage_end"] = coverage_end
        item["coverage_label"] = coverage_label
        public.append(item)
    return public


def render_story_group(category: str, stories: list[dict[str, Any]]) -> str:
    items = []
    for story in sorted(stories, key=lambda item: item["score"], reverse=True):
        links = "\n".join(
            f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{html.escape(url)}</a></li>'
            for url in story.get("source_urls", [])
        )
        items.append(
            f"""<article class="dispatch-story">
<h3>{html.escape(story["title"])}</h3>
<p>{html.escape(story["summary"])}</p>
<p class="edition-date">Score: {int(story["score"])}</p>
<ul>{links}</ul>
</article>"""
        )
    return f"<h2>{html.escape(category)}</h2>\n" + "\n".join(items)


def render_cascadia_html(
    edition_date: str,
    stories: list[dict[str, Any]],
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str = "weekly",
) -> str:
    coverage_label = format_coverage_label(coverage_start, coverage_end) if coverage_start and coverage_end else edition_date
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for story in stories:
        grouped[story["category"]].append(story)
    groups = "\n".join(render_story_group(category, items) for category, items in sorted(grouped.items()))
    if not groups:
        if coverage_start and coverage_end and briefing_type == "weekly":
            groups = (
                "<p>No qualifying source-backed Cascadia signals were identified "
                f"for the {html.escape(coverage_start)} through {html.escape(coverage_end)} coverage window.</p>"
            )
        else:
            groups = "<p>No public Cascadia stories met the source and relevance threshold for this edition.</p>"
    coverage_line = ""
    if coverage_start and coverage_end:
        coverage_line = f"Weekly briefing / {html.escape(coverage_label)} / Coverage: {html.escape(coverage_start)} through {html.escape(coverage_end)}"
    else:
        coverage_line = f"Regional systems briefing / {html.escape(edition_date)}"
    run_line = f"\n    <p class=\"edition-date\">Run date: {html.escape(run_date)}</p>" if run_date else ""
    body = f"""{header(DISPATCH_NAME, "../../", "../../archive.html", "/cascadia/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/{CASCADIA_LOGO_ASSET}" alt="{DISPATCH_NAME}">
    </section>
    <p class="eyebrow">{coverage_line}</p>{run_line}
    <p><strong>{DISPATCH_NAME}</strong></p>
    <p>{html.escape(CASCADIA_PUBLIC_DESCRIPTION)}</p>
    <p><strong>Cascadia Signal Pack</strong><br>Detailed downloadable records are being prepared for future release.</p>
    {groups}
  </main>
{footer("../../")}"""
    return page(f"{DISPATCH_NAME} - {coverage_label}", f"{BASE_URL}/cascadia/editions/{edition_date}/", "../../assets/site.css", body, DISPATCH_NAME)


def write_json(path: Path, payload: Any, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_text(path: Path, content: str, dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_detail_csv(path: Path, records: list[dict[str, Any]], dry_run: bool, written: list[str]) -> None:
    written.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "story_id",
        "title",
        "category",
        "score",
        "source_record_ids",
        "source_urls",
        "included_in_public_summary",
        "included_in_detail_dataset",
        "excluded_reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: json.dumps(record.get(field)) if isinstance(record.get(field), list) else record.get(field) for field in fieldnames})


def refresh_cascadia_archive_pages(root: Path, dry_run: bool, written: list[str]) -> None:
    site_root = root / "output" / "site"
    dispatch = DispatchConfig(
        slug=DISPATCH_SLUG,
        name=DISPATCH_NAME,
        edition_date="",
        tagline=SHORT_PUBLIC_DESCRIPTION,
        logo=CASCADIA_LOGO_ASSET,
        sources=[],
        stories=[],
    )
    dates = discover_public_edition_dates(site_root, DISPATCH_SLUG)
    if dates:
        dispatch = DispatchConfig(
            slug=DISPATCH_SLUG,
            name=DISPATCH_NAME,
            edition_date=dates[0],
            tagline=SHORT_PUBLIC_DESCRIPTION,
            logo=CASCADIA_LOGO_ASSET,
            sources=[],
            stories=[],
        )
    public_root = site_root / DISPATCH_SLUG
    generator_write_text(public_root / "index.html", render_dispatch_index_for_dates(dispatch, dates, site_root), dry_run, written)
    generator_write_text(public_root / "archive.html", render_archive_for_dates(dispatch, dates, site_root), dry_run, written)
    generator_write_text(public_root / "rss.xml", render_rss_for_dates(dispatch, dates, site_root), dry_run, written)


def render_cascadia_edition(
    root: Path,
    edition_date: str,
    dry_run: bool = False,
    run_date: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    briefing_type: str = "weekly",
) -> dict[str, Any]:
    root = root.resolve()
    curated_path = root / CASCADE_DATA_ROOT / "curated" / edition_date / "curation_manifest.json"
    output_dispatch_dir = root / "output" / "dispatches" / "cascadia" / "editions" / edition_date
    public_dir = root / "output" / "site" / "cascadia" / "editions" / edition_date
    detail_dir = root / "output" / "detail" / "cascadia" / edition_date
    warnings: list[str] = []
    errors: list[str] = []
    written: list[str] = []
    if not curated_path.exists():
        errors.append(f"curation manifest not found: {curated_path}")
        return {"ok": False, "written": written, "warnings": warnings, "errors": errors}
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    stories = public_stories(curated)
    errors.extend(validate_public_stories(stories))
    coverage_label = format_coverage_label(coverage_start, coverage_end) if coverage_start and coverage_end else None
    sources_manifest = sources_manifest_from_curated(curated, edition_date, run_date, coverage_start, coverage_end, briefing_type, coverage_label)
    curation_manifest = public_curation_manifest(curated, run_date, edition_date, coverage_start, coverage_end, briefing_type, coverage_label)
    html_text = render_cascadia_html(edition_date, stories, run_date, coverage_start, coverage_end, briefing_type)
    generated_at = datetime.now(timezone.utc).isoformat()
    source_record_ids = sorted({source["source_record_id"] for source in sources_manifest})
    source_urls = sorted({source["url"] for source in sources_manifest if source.get("url")})
    edition_manifest = {
        "dispatch_name": DISPATCH_NAME,
        "dispatch_slug": DISPATCH_SLUG,
        "public_name": DISPATCH_NAME,
        "briefing_type": briefing_type,
        "run_date": run_date or edition_date,
        "edition_date": edition_date,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "coverage_label": coverage_label,
        "week_label": week_label(datetime.fromisoformat(coverage_start).date()) if coverage_start else None,
        "source_record_ids": source_record_ids,
        "source_urls": source_urls,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/cascadia/editions/{edition_date}/",
        "local_output_path": str(public_dir),
        "local_backup_path": None,
        "template_version": TEMPLATE_VERSION,
        "source_count": len(sources_manifest),
        "story_count": len(curated),
        "public_story_count": len(stories),
        "source_manifest_path": str(public_dir / "sources_manifest.json"),
        "curation_manifest_path": str(public_dir / "curation_manifest.json"),
        "free_public_artifacts": [
            str(public_dir / "index.html"),
            str(public_dir / "sources_manifest.json"),
            str(public_dir / "curation_manifest.json"),
        ],
        "paid_or_detail_artifacts": [],
        "detail_artifacts_publicly_exposed": False,
        "warnings": warnings,
        "errors": errors,
    }
    if errors:
        return {"ok": False, "written": written, "warnings": warnings, "errors": errors}
    for out_dir in [output_dispatch_dir, public_dir]:
        write_text(out_dir / "index.html", html_text, dry_run, written)
        write_json(out_dir / "edition_manifest.json", edition_manifest, dry_run, written)
        write_json(out_dir / "sources_manifest.json", sources_manifest, dry_run, written)
        write_json(out_dir / "curation_manifest.json", curation_manifest, dry_run, written)
    refresh_cascadia_archive_pages(root, dry_run, written)
    detail_result = write_cascadia_signal_package(
        root,
        edition_date,
        dry_run=dry_run,
        run_date=run_date,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        briefing_type=briefing_type,
    )
    written.extend(detail_result.get("written", []))
    warnings.extend(detail_result.get("warnings", []))
    errors.extend(detail_result.get("errors", []))
    return {
        "ok": not errors,
        "public_story_count": len(stories),
        "detail_count": int(detail_result.get("detail_count", 0)),
        "output_paths": {
            "dispatch_output": str(output_dispatch_dir),
            "public_site_output": str(public_dir),
            "detail_output": str(detail_dir),
        },
        "detail_output_paths": detail_result.get("output_paths", {}),
        "manifest_paths": {
            "edition_manifest": str(public_dir / "edition_manifest.json"),
            "sources_manifest": str(public_dir / "sources_manifest.json"),
            "curation_manifest": str(public_dir / "curation_manifest.json"),
        },
        "written": written,
        "warnings": warnings,
        "errors": errors,
    }
