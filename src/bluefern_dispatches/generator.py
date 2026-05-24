from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_weekly import format_coverage_label
from bluefern_dispatches.gaza_sources import filter_recent_duplicate_sources


BASE_URL = "https://dispatches.thebluefernco.com"
BLUE_FERN_URL = "https://thebluefernco.com"
TEMPLATE_VERSION = "dispatches-static-v1"
DEFAULT_BACKUP_ROOT = Path(os.getenv("PUBLISH_BACKUP_ROOT", "output/tmp-backups-pages"))
PUBLIC_ROOT_NAMES = {"site"}
DETAIL_ROOT_NAMES = {"detail", "paid"}
CNAME_VALUE = "dispatches.thebluefernco.com"
PUBLISH_COMMIT_MESSAGE = "Publish Blue Fern dispatches site"
DEFAULT_PAGES_BRANCH = "gh-pages"
ROOT_MASTHEAD_ASSET = "dispatches-from-blue-fern-co.png"
CASCADIA_LOGO_ASSET = "cascadia-logo-placeholder.png"
FAVICON_ASSETS = ["favicon.ico", "favicon-32x32.png", "favicon-16x16.png", "apple-touch-icon.png"]
PUBLIC_SITE_ASSETS = ["site.css", "gaza-logo.png", "bluefern.png", CASCADIA_LOGO_ASSET, ROOT_MASTHEAD_ASSET, *FAVICON_ASSETS]
ROOT_DESCRIPTION = "Source-based dispatches from The Blue Fern Co., organized for public reading, research, and accountability."
CASCADIA_PUBLIC_DESCRIPTION = "The Cascadia Briefing is a weekly, source-backed regional briefing for Washington, Oregon, and Idaho, tracking public systems, infrastructure, health, safety, environment, economy, and resilience."
CASCADIA_RSS_DESCRIPTION = "Weekly source-backed regional briefings for Washington, Oregon, and Idaho."
AMERICAN_PRESSURE_PUBLIC_DESCRIPTION = "Source-based reporting on the pressures reshaping household life across the United States."
AMERICAN_PRESSURE_NO_SIGNAL = "No source-backed signal in this edition."
AMERICAN_PRESSURE_REQUIRED_SOURCE_FIELDS = {
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
CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE_IDENTIFIED = "Reviewed week | No qualifying source-backed regional signals identified"
CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE_SURFACED = "Reviewed week | No qualifying source-backed regional signals surfaced"
CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE = CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE_SURFACED
EXPECT_DISPATCH_CHOICES = ("gaza", "cascadia", "american-pressure", "all")
ALL_EXPECT_DISPATCHES = ("gaza", "cascadia", "american-pressure")
DISPATCH_CATALOG: dict[str, dict[str, Any]] = {
    "gaza": {"label": "Gaza", "public_visible": True},
    "cascadia": {"label": "Cascadia", "public_visible": True},
    "american-pressure": {"label": "American Pressure", "public_visible": True},
}
DISPATCH_LABELS = {slug: str(meta.get("label") or slug) for slug, meta in DISPATCH_CATALOG.items()}
ONLY_DISPATCH_CHOICES = ("gaza", "cascadia", "american-pressure")


def dispatch_public_visible(slug: str) -> bool:
    meta = DISPATCH_CATALOG.get(slug, {})
    return bool(meta.get("public_visible", True))


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    title: str
    url: str
    publisher: str
    published_at: str | None
    retrieved_at: str
    archive_path: str | None
    used_in_story_ids: list[str]
    claim_ids: list[str]
    dispatch_slug: str
    edition_date: str


@dataclass(frozen=True)
class StoryRecord:
    story_id: str
    title: str
    summary: str
    category: str
    score: int
    scoring_reasons: list[str]
    included_in_public_summary: bool
    included_in_detail_dataset: bool
    source_ids: list[str]
    excluded_reason: str | None = None
    editorial_admin_copy: bool = False


@dataclass(frozen=True)
class DispatchConfig:
    slug: str
    name: str
    edition_date: str
    tagline: str
    logo: str
    sources: list[SourceRecord]
    stories: list[StoryRecord]
    body_html: str | None = None
    detail_artifacts: list[str] | None = None


GAZA_BODY_HTML = """<p><strong>Dispatches From Gaza</strong></p>
<p>Daily Briefing - 2026-05-03</p>
<p>Today's Gaza briefing: Israel has issued threats to resume war in
Gaza to compel the disarmament of militant groups, signaling a breakdown
in a fragile truce.</p>
<h2 id="at-a-glance">At a Glance</h2>
<ul>
<li>How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza
- The New York Times</li>
<li>U.S. to close Israel command center overseeing Gaza truce as Trump
plan stalls - Haaretz</li>
<li>Court extends detention of 2 Gaza flotilla activists accused of
Hamas links - The Times of Israel</li>
</ul>
<h2 id="top-story">Top Story</h2>
<h3 id="how-israel-is-using-the-same-tactics-in-lebanon-that-it-did-in-gaza---the-new-york-times">How
Israel Is Using the Same Tactics in Lebanon That It Did in Gaza - The
New York Times</h3>
<p>Today, Israel has issued threats to resume war in Gaza to compel the
disarmament of militant groups, signaling a breakdown in a fragile
truce. Meanwhile, the court extended the detention of two Gaza flotilla
activists accused of links to Hamas, adding to tensions. Also notable is
reporting that Israel's military tactics in Lebanon echo those
previously used in Gaza. What remains unclear is the immediate
likelihood of renewed large-scale fighting, despite Israel's warnings
and ongoing political moves within Gaza, including Hamas preparing to
elect a new leader. It's also uncertain how the international community,
including US decisions like closing their Gaza mission and command
center, will influence the dynamics on the ground. Looking ahead,
attention turns to the Israeli security cabinet's upcoming discussions
on Gaza and Hamas's political developments. These factors will shape
whether tensions escalate or if diplomatic efforts can stabilize the
situation.</p>
<p><em>Source: <a href="https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF" target="_blank" rel="noopener noreferrer">News</a></em></p>
<h2 id="other-developments">Other Developments</h2>
<h3 id="u.s.-to-close-israel-command-center-overseeing-gaza-truce-as-trump-plan-stalls---haaretz">U.S.
to close Israel command center overseeing Gaza truce as Trump plan
stalls - Haaretz</h3>
<p><em>Source: <a href="https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE" target="_blank" rel="noopener noreferrer">News</a></em></p>
<h3 id="court-extends-detention-of-2-gaza-flotilla-activists-accused-of-hamas-links---the-times-of-israel">Court
extends detention of 2 Gaza flotilla activists accused of Hamas links -
The Times of Israel</h3>
<p><em>Source: <a href="https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR" target="_blank" rel="noopener noreferrer">News</a></em></p>
<h2 id="source-note">Source Note</h2>
<p>This dispatch is compiled from publicly available reporting and
should be read as a neutral informational summary. Source links are
included where available.</p>"""


def gaza_body_html(edition_date: str) -> str:
    return GAZA_BODY_HTML.replace("Daily Briefing - 2026-05-03", f"Daily Briefing - {edition_date}")


def _american_pressure_fixtures(root: Path, edition_date: str) -> tuple[Any, Path | None]:
    base = root / "data" / "dispatches" / "american-pressure" / "sources"
    direct = base / edition_date / "manual_sources.json"
    if direct.exists():
        return json.loads(direct.read_text(encoding="utf-8")), direct
    if not base.exists():
        return [], None
    dated = sorted(path for path in base.glob("*/manual_sources.json") if path.is_file())
    if not dated:
        return [], None
    chosen = dated[-1]
    return json.loads(chosen.read_text(encoding="utf-8")), chosen


def _normalize_american_pressure_fixture_rows(
    raw_payload: Any,
    fixture_path: Path | None,
    warnings: list[str],
    errors: list[str],
) -> list[dict[str, Any]]:
    if fixture_path is None:
        return []
    path_label = str(fixture_path)
    expected = "expected JSON root to be a list of records or an object with a 'sources' list"
    if isinstance(raw_payload, list):
        rows = raw_payload
    elif isinstance(raw_payload, dict):
        rows = raw_payload.get("sources")
        if not isinstance(rows, list):
            errors.append(f"american-pressure manual sources file has invalid shape: {path_label}; {expected}")
            return []
    else:
        errors.append(f"american-pressure manual sources file has invalid shape: {path_label}; {expected}")
        return []
    valid_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            warnings.append(
                f"american-pressure fixture record {index + 1} in {path_label} is not an object; got {type(row).__name__}"
            )
            continue
        valid_rows.append(row)
    return valid_rows


def _source_category_to_story_category(source_category_hint: str) -> str:
    value = source_category_hint.strip().lower()
    if value in {"food", "food-pressure"}:
        return "food-pressure"
    if value in {"health", "health-access"}:
        return "health-access-pressure"
    if value in {"household", "household-cost"}:
        return "household-cost-pressure"
    if value in {"environment", "environmental"}:
        return "environmental-pressure"
    return "local-systems-note"


def _render_american_pressure_section(title: str, story: StoryRecord | None, source: SourceRecord | None) -> str:
    if not story or not source:
        return f"<h2>{html.escape(title)}</h2><p>{AMERICAN_PRESSURE_NO_SIGNAL}</p>"
    published = source.published_at or "date not listed"
    return (
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(story.summary)}</p>"
        f"<p><em>Source: <a href=\"{html.escape(source.url)}\" target=\"_blank\" rel=\"noopener noreferrer\">"
        f"{html.escape(source.title)}</a> ({html.escape(source.publisher)}, {html.escape(published)})</em></p>"
    )


def _render_american_pressure_body(stories: list[StoryRecord], sources: list[SourceRecord]) -> str:
    by_category = {story.category: story for story in stories}
    by_source_id = {source.source_id: source for source in sources}

    top_story = stories[0] if stories else None
    top_source = by_source_id.get(top_story.source_ids[0]) if top_story and top_story.source_ids else None
    if top_story and top_source:
        top_html = (
            "<h2>Top Signal</h2>"
            f"<p>{html.escape(top_story.summary)}</p>"
            f"<p><em>Source: <a href=\"{html.escape(top_source.url)}\" target=\"_blank\" rel=\"noopener noreferrer\">"
            f"{html.escape(top_source.title)}</a> ({html.escape(top_source.publisher)})</em></p>"
        )
    else:
        top_html = f"<h2>Top Signal</h2><p>{AMERICAN_PRESSURE_NO_SIGNAL}</p>"

    sections = [
        ("Food Pressure", "food-pressure"),
        ("Health Access Pressure", "health-access-pressure"),
        ("Household Cost Pressure", "household-cost-pressure"),
        ("Environmental Pressure", "environmental-pressure"),
        ("Local Systems Note", "local-systems-note"),
    ]
    section_html = []
    for section_title, category in sections:
        story = by_category.get(category)
        source = by_source_id.get(story.source_ids[0]) if story and story.source_ids else None
        section_html.append(_render_american_pressure_section(section_title, story, source))

    why_it_matters = (
        "<h2>What Changed / Why It Matters</h2>"
        f"<p>{AMERICAN_PRESSURE_NO_SIGNAL}</p>"
        if len(stories) < 2
        else f"<h2>What Changed / Why It Matters</h2><p>{html.escape(stories[1].summary)}</p>"
    )

    source_lines = []
    for story in stories:
        for source_id in story.source_ids:
            source = by_source_id.get(source_id)
            if not source:
                continue
            source_lines.append(
                f"<li><a href=\"{html.escape(source.url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{html.escape(source.title)}</a> - "
                f"{html.escape(source.publisher)}</li>"
            )
    sources_html = "<h2>Sources</h2><ul>" + "".join(source_lines) + "</ul>" if source_lines else f"<h2>Sources</h2><p>{AMERICAN_PRESSURE_NO_SIGNAL}</p>"
    return f"<p><strong>The American Pressure Dispatch</strong></p>{top_html}{''.join(section_html)}{why_it_matters}{sources_html}"


def _build_american_pressure_dispatch(root: Path, now: str, date: str, warnings: list[str], errors: list[str]) -> DispatchConfig:
    fixture_payload, fixture_path = _american_pressure_fixtures(root, date)
    fixture_rows = _normalize_american_pressure_fixture_rows(fixture_payload, fixture_path, warnings, errors)
    valid_rows: list[dict[str, Any]] = []
    for index, row in enumerate(fixture_rows):
        missing = sorted(AMERICAN_PRESSURE_REQUIRED_SOURCE_FIELDS - set(row.keys()))
        if missing:
            warnings.append(f"american-pressure fixture record {index + 1} missing required fields: {', '.join(missing)}")
            continue
        valid_rows.append(row)
    sources: list[SourceRecord] = []
    stories: list[StoryRecord] = []
    for index, row in enumerate(valid_rows, start=1):
        source_id = str(row["source_record_id"])
        story_id = f"american-pressure-story-{index:03d}"
        category = _source_category_to_story_category(str(row.get("category_hint") or ""))
        sources.append(
            SourceRecord(
                source_id=source_id,
                title=str(row["title"]),
                url=str(row["url"]),
                publisher=str(row["publisher"]),
                published_at=str(row["published_at"]),
                retrieved_at=str(row.get("retrieved_at") or now),
                archive_path=None,
                used_in_story_ids=[story_id],
                claim_ids=[f"american-pressure-claim-{index:03d}"],
                dispatch_slug="american-pressure",
                edition_date=date,
            )
        )
        stories.append(
            StoryRecord(
                story_id=story_id,
                title=str(row["title"]),
                summary=str(row["summary_or_snippet"]),
                category=category,
                score=50,
                scoring_reasons=["source-backed fixture record"],
                included_in_public_summary=True,
                included_in_detail_dataset=False,
                source_ids=[source_id],
            )
        )
    if not sources:
        warnings.append("american-pressure has no source-backed fixture records; rendering no-signal page")
    if fixture_path is None:
        warnings.append("american-pressure fixture file missing under data/dispatches/american-pressure/sources")
    return DispatchConfig(
        slug="american-pressure",
        name="The American Pressure Dispatch",
        edition_date=date,
        tagline="Source-based reporting on household pressure in the United States",
        logo="american-pressure-logo.png",
        sources=sources,
        stories=stories,
        body_html=_render_american_pressure_body(stories, sources),
        detail_artifacts=[],
    )


def seed_dispatches(
    root: Path,
    now: str,
    warnings: list[str],
    errors: list[str],
    dispatch_seed_dates: dict[str, str] | None = None,
) -> list[DispatchConfig]:
    # Use explicit seed edition date if provided via env, otherwise default
    # to the current run date (the 'now' param is an ISO timestamp).
    env_date = os.getenv("BLUEFERN_SEED_EDITION_DATE")
    if env_date and env_date.strip():
        date = env_date.strip()
    else:
        # 'now' is an ISO timestamp from build_site; extract YYYY-MM-DD
        date = (now or "").split("T")[0] or "2026-05-03"
    dispatch_seed_dates = dispatch_seed_dates or {}
    ap_date = dispatch_seed_dates.get("american-pressure", date)
    gaza_sources = [
        SourceRecord("gaza-src-001", "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza", "https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF", "The New York Times", None, now, None, ["gaza-story-001"], ["gaza-claim-001"], "gaza", date),
        SourceRecord("gaza-src-002", "U.S. to close Israel command center overseeing Gaza truce as Trump plan stalls", "https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE", "Haaretz", None, now, None, ["gaza-story-001"], ["gaza-claim-002"], "gaza", date),
        SourceRecord("gaza-src-003", "Court extends detention of 2 Gaza flotilla activists accused of Hamas links", "https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR", "The Times of Israel", None, now, None, ["gaza-story-001"], ["gaza-claim-003"], "gaza", date),
    ]
    cascadia_sources = [
        SourceRecord("cascadia-src-001", "Placeholder source record for Cascadia launch edition", f"{BASE_URL}/cascadia/editions/{date}/sources_manifest.json", "Blue Fern Dispatch Records", f"{date}T00:00:00Z", now, None, ["cascadia-story-001"], ["cascadia-admin-001"], "cascadia", date)
    ]
    return [
        DispatchConfig(
            slug="gaza",
            name="Dispatches From Gaza",
            edition_date=date,
            tagline="Daily briefing",
            logo="gaza-logo.png",
            sources=gaza_sources,
            stories=[StoryRecord("gaza-story-001", f"Dispatches From Gaza - {date}", "Structured daily briefing synthesizing key developments from public reporting.", "humanitarian", 100, ["Preserved from existing Gaza public edition."], True, False, [s.source_id for s in gaza_sources])],
            body_html=gaza_body_html(date),
            detail_artifacts=[],
        ),
        _build_american_pressure_dispatch(root, now, ap_date, warnings, errors),
        DispatchConfig(
            slug="cascadia",
            name="The Cascadia Briefing",
            edition_date=date,
            tagline=CASCADIA_RSS_DESCRIPTION,
            logo=CASCADIA_LOGO_ASSET,
            sources=cascadia_sources,
            stories=[StoryRecord("cascadia-story-001", "Launch placeholder", "The Cascadia dispatch area is prepared for dated, source-backed system briefings.", "editorial-admin", 0, ["Administrative launch placeholder; not a factual regional signal."], True, False, ["cascadia-src-001"], editorial_admin_copy=True)],
            detail_artifacts=[],
        ),
    ]


def asdicts(records: list[Any]) -> list[dict[str, Any]]:
    return [record.__dict__ for record in records]


def validate_traceability(dispatches: list[DispatchConfig]) -> list[str]:
    errors: list[str] = []
    for dispatch in dispatches:
        source_ids = {source.source_id for source in dispatch.sources}
        for story in dispatch.stories:
            if story.included_in_public_summary and not story.editorial_admin_copy and not story.source_ids:
                errors.append(f"{dispatch.slug}:{story.story_id} is public but has no source records")
            missing = [source_id for source_id in story.source_ids if source_id not in source_ids]
            if missing:
                errors.append(f"{dispatch.slug}:{story.story_id} references missing source ids: {', '.join(missing)}")
    return errors


def ensure_public_detail_separation(site_root: Path, detail_roots: list[Path]) -> list[str]:
    errors: list[str] = []
    site_root_resolved = site_root.resolve()
    for detail_root in detail_roots:
        resolved = detail_root.resolve()
        if site_root_resolved == resolved or site_root_resolved in resolved.parents:
            errors.append(f"detail path {resolved} is inside public site output {site_root_resolved}")
        if resolved.name in PUBLIC_ROOT_NAMES:
            errors.append(f"detail path {resolved} uses a public root name")
    return errors


def public_site_contains_detail_artifacts(site_root: Path) -> list[str]:
    if not site_root.exists():
        return []
    blocked = []
    for path in site_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(site_root).parts
        if relative_parts and relative_parts[0] in DETAIL_ROOT_NAMES:
            blocked.append(str(path))
    return blocked


def public_site_contains_blocked_public_text(site_root: Path) -> list[str]:
    if not site_root.exists():
        return []
    blocked: list[str] = []
    needles = ("output/detail", "output/paid", "cascadia_signal_records")
    for path in site_root.rglob("*"):
        if ".git" in path.relative_to(site_root).parts:
            continue
        if not path.is_file() or path.suffix.lower() not in {".html", ".json", ".xml", ".css", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(needle in text for needle in needles):
            blocked.append(str(path))
    return blocked


def write_text(path: Path, content: str, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def copy_asset(src: Path, dst: Path, dry_run: bool, wrote: list[str], warnings: list[str]) -> None:
    if not src.exists():
        warnings.append(f"Missing asset: {src}")
        return
    wrote.append(str(dst))
    if dry_run:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def real_dispatch_edition_files(root: Path, slug: str, edition_date: str) -> list[Path]:
    edition_dir = root / "output" / "dispatches" / slug / "editions" / edition_date
    required_names = ["index.html", "edition_manifest.json", "sources_manifest.json", "curation_manifest.json"]
    files = [edition_dir / name for name in required_names]
    if all(path.exists() for path in files):
        return sorted(path for path in edition_dir.iterdir() if path.is_file())
    return []


def copy_real_dispatch_edition(root: Path, slug: str, edition_date: str, site_root: Path, dry_run: bool, wrote: list[str]) -> bool:
    files = real_dispatch_edition_files(root, slug, edition_date)
    if not files:
        return False
    source_dir = root / "output" / "dispatches" / slug / "editions" / edition_date
    target_dir = site_root / slug / "editions" / edition_date
    for source in files:
        target = target_dir / source.relative_to(source_dir)
        wrote.append(str(target))
        if dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.name == "index.html":
            target.write_text(ensure_favicon_links(source.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(source, target)
    return True


def existing_public_edition_files(site_root: Path, slug: str, edition_date: str) -> list[Path]:
    edition_dir = site_root / slug / "editions" / edition_date
    required_names = ["index.html", "edition_manifest.json", "sources_manifest.json", "curation_manifest.json"]
    files = [edition_dir / name for name in required_names]
    if all(path.exists() for path in files):
        return files
    return []


def favicon_links() -> str:
    return """  <link rel="icon" href="/assets/favicon.ico" sizes="any">
  <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png">
  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">"""


def ensure_favicon_links(html_text: str) -> str:
    if 'href="/assets/favicon.ico"' in html_text and 'rel="apple-touch-icon"' in html_text:
        return html_text
    return html_text.replace('  <link rel="stylesheet"', f"{favicon_links()}\n  <link rel=\"stylesheet\"", 1)


def ensure_public_html_favicons(site_root: Path, dry_run: bool, wrote: list[str]) -> None:
    if not site_root.exists():
        return
    for path in sorted(site_root.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        updated = ensure_favicon_links(text)
        if updated == text:
            continue
        wrote.append(str(path))
        if not dry_run:
            path.write_text(updated, encoding="utf-8")


def page(title: str, canonical: str, css_href: str, body: str, site_name: str = "Dispatches From The Blue Fern Co.") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="canonical" href="{html.escape(canonical)}">
  <meta property="og:url" content="{html.escape(canonical)}">
  <meta property="og:site_name" content="{html.escape(site_name)}">
  <meta name="twitter:card" content="summary_large_image">
{favicon_links()}
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
{body}
</body>
</html>
"""


def header(brand: str, root_prefix: str, archive_href: str | None = None, section_href: str | None = None) -> str:
    root_links = []
    for slug in ("gaza", "cascadia", "american-pressure"):
        if not dispatch_public_visible(slug):
            continue
        root_links.append(f'<a href="/{slug}/">{html.escape(DISPATCH_LABELS.get(slug, slug.title()))}</a>')
    nav = "".join(root_links)
    if archive_href:
        section_link = f'<a href="{section_href}">{html.escape(brand)}</a>' if section_href else ""
        nav = f'<a href="/">Dispatches Home</a>{section_link}<a href="{archive_href}">Archive</a><a href="{root_prefix}rss.xml">RSS</a>'
    return f"""  <header class="site-header">
    <a class="brand" href="{root_prefix}index.html">{html.escape(brand)}</a>
    <nav>{nav}</nav>
  </header>"""


def footer(asset_prefix: str) -> str:
    return f"""  <footer class="site-footer">
    <div class="publisher">
      <a href="{BLUE_FERN_URL}/" target="_blank" rel="noopener noreferrer"><img class="publisher-mark" src="{asset_prefix}assets/bluefern.png" alt="The Blue Fern Co."></a>
      <p class="publisher-label">Published by <a href="{BLUE_FERN_URL}/" target="_blank" rel="noopener noreferrer">The Blue Fern Company</a></p>
    </div>
  </footer>"""


def render_root(dispatches: list[DispatchConfig]) -> str:
    cards = "\n".join(
        f"""      <li class="dispatch-card">
        <a href="/{dispatch.slug}/">
          <span class="edition-date">{html.escape(dispatch.tagline)}</span>
          <strong>{html.escape(dispatch.name)}</strong>
        </a>
      </li>"""
        for dispatch in dispatches
        if dispatch_public_visible(dispatch.slug)
    )
    body = f"""{header("Dispatches From The Blue Fern Co.", "")}
  <main class="home">
    <section class="hero root-hero">
      <img class="root-masthead" src="assets/{ROOT_MASTHEAD_ASSET}" alt="Dispatches From The Blue Fern Co.">
    </section>
    <p class="lede">{ROOT_DESCRIPTION}</p>
    <ul class="dispatch-grid">
{cards}
    </ul>
  </main>
{footer("")}"""
    return page("Dispatches From The Blue Fern Co.", f"{BASE_URL}/", "assets/site.css", body)


def render_dispatch_index(dispatch: DispatchConfig) -> str:
    signal_pack_note = ""
    if dispatch.slug == "cascadia":
        signal_pack_note = "\n    <p><strong>Cascadia Signal Pack</strong><br>Detailed downloadable records are being prepared for future release.</p>"
    description = (
        CASCADIA_PUBLIC_DESCRIPTION
        if dispatch.slug == "cascadia"
        else AMERICAN_PRESSURE_PUBLIC_DESCRIPTION
        if dispatch.slug == "american-pressure"
        else "Structured briefings compiled from traceable source records."
    )
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)} archive</p>
    <p class="lede">{html.escape(description)}</p>
    <p><a href="editions/{dispatch.edition_date}/">Read the latest briefing</a></p>
    {signal_pack_note}
    <h2>Recent Editions</h2>
    <ul class="edition-list">
      <li><span class="edition-date">{dispatch.edition_date}</span><a href="editions/{dispatch.edition_date}/">{html.escape(dispatch.name)} - {dispatch.edition_date}</a></li>
    </ul>
  </main>
{footer("")}"""
    return page(dispatch.name, f"{BASE_URL}/{dispatch.slug}/", "assets/site.css", body, dispatch.name)


def is_weekly_cascadia_manifest(manifest: dict[str, Any], edition_date: str) -> bool:
    if manifest.get("dispatch_slug") != "cascadia":
        return False
    if manifest.get("edition_date") and manifest.get("edition_date") != edition_date:
        return False
    weekly_markers = {
        str(manifest.get("briefing_type") or "").strip().lower(),
        str(manifest.get("cadence") or "").strip().lower(),
        str(manifest.get("edition_type") or "").strip().lower(),
    }
    if "weekly" not in weekly_markers:
        return False
    coverage_start = str(manifest.get("coverage_start") or "").strip()
    coverage_end = str(manifest.get("coverage_end") or "").strip()
    if not coverage_start or not coverage_end:
        return False
    if coverage_end != edition_date:
        return False
    coverage_label = str(manifest.get("coverage_label") or manifest.get("public_coverage_label") or "").strip()
    if not coverage_label:
        return False
    return True


def public_edition_is_listable(site_root: Path, slug: str, edition_date: str) -> bool:
    if slug == "gaza":
        manifest_path = site_root / slug / "editions" / edition_date / "edition_manifest.json"
        if not manifest_path.exists():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(manifest, dict):
            return False
        errors = manifest.get("errors")
        if isinstance(errors, list) and any(
            "No new source-backed Gaza developments after cross-edition dedupe" in str(item)
            for item in errors
        ):
            return False
        sources_manifest_path = site_root / slug / "editions" / edition_date / "sources_manifest.json"
        curation_manifest_path = site_root / slug / "editions" / edition_date / "curation_manifest.json"
        sources_payload: list[dict[str, Any]] | None = None
        curation_payload: list[dict[str, Any]] | None = None
        if sources_manifest_path.exists():
            try:
                loaded = json.loads(sources_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            if isinstance(loaded, list):
                sources_payload = loaded
        if curation_manifest_path.exists():
            try:
                loaded = json.loads(curation_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            if isinstance(loaded, list):
                curation_payload = loaded
        source_count = len(sources_payload) if sources_payload is not None else int(manifest.get("source_count", 0) or 0)
        story_count = len(curation_payload) if curation_payload is not None else int(manifest.get("story_count", 0) or 0)
        if source_count <= 0 or story_count <= 0:
            return False
        # Cross-edition dedupe failures are recorded in project-local Gaza run artifacts.
        dedupe_path = site_root.parents[1] / "data" / "dispatches" / "gaza" / "editions" / edition_date / "dedupe_report.json"
        if dedupe_path.exists():
            try:
                dedupe_payload = json.loads(dedupe_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return False
            input_count = int(dedupe_payload.get("input_candidate_count", 0) or 0)
            kept_count = int(dedupe_payload.get("kept_candidate_count", 0) or 0)
            if input_count > 0 and kept_count == 0:
                return False
        return True
    if slug == "american-pressure":
        manifest_path = site_root / slug / "editions" / edition_date / "edition_manifest.json"
        index_path = site_root / slug / "editions" / edition_date / "index.html"
        sources_manifest_path = site_root / slug / "editions" / edition_date / "sources_manifest.json"
        curation_manifest_path = site_root / slug / "editions" / edition_date / "curation_manifest.json"
        if not index_path.exists() or not sources_manifest_path.exists() or not curation_manifest_path.exists():
            return False
        failed_markers = ["failed_run.json", "run_failed.json", ".failed"]
        for marker in failed_markers:
            if (site_root / slug / "editions" / edition_date / marker).exists():
                return False
        if not manifest_path.exists():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(manifest, dict):
            return False
        if manifest.get("dispatch_slug") != "american-pressure":
            return False
        if manifest.get("edition_date") and manifest.get("edition_date") != edition_date:
            return False
        if manifest.get("public_exposed") is False:
            return False
        if manifest.get("is_free_public") is False:
            return False
        if manifest.get("unpublishable") is True:
            return False
        week_start_date = str(manifest.get("week_start_date") or "").strip()
        week_end_date = str(manifest.get("week_end_date") or "").strip()
        display_date_range = str(manifest.get("display_date_range") or "").strip()
        if not week_start_date or not week_end_date or not display_date_range:
            return False
        if week_end_date != edition_date:
            return False
        try:
            if datetime.strptime(week_end_date, "%Y-%m-%d").weekday() != 5:
                return False
        except ValueError:
            return False
        source_count = int(manifest.get("source_count", 0) or 0)
        story_count = int(manifest.get("story_count", 0) or 0)
        if source_count <= 0 or story_count <= 0:
            return False
        if any(str(item).strip() for item in (manifest.get("errors") or [])):
            return False
        try:
            sources_payload = json.loads(sources_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(sources_payload, list):
            return False
        try:
            curation_payload = json.loads(curation_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        stories_payload: list[dict[str, Any]] = []
        if isinstance(curation_payload, dict):
            raw_stories = curation_payload.get("stories")
            if isinstance(raw_stories, list):
                stories_payload = [row for row in raw_stories if isinstance(row, dict)]
        elif isinstance(curation_payload, list):
            stories_payload = [row for row in curation_payload if isinstance(row, dict)]
        if len(sources_payload) <= 0 or len(stories_payload) <= 0:
            return False
        has_visible_source_links = any(
            str((row or {}).get("url") or "").strip().startswith(("http://", "https://"))
            for row in sources_payload if isinstance(row, dict)
        )
        if not has_visible_source_links:
            return False
        return True
    if slug != "cascadia":
        return True
    manifest_path = site_root / slug / "editions" / edition_date / "edition_manifest.json"
    index_path = site_root / slug / "editions" / edition_date / "index.html"
    sources_manifest_path = site_root / slug / "editions" / edition_date / "sources_manifest.json"
    curation_manifest_path = site_root / slug / "editions" / edition_date / "curation_manifest.json"
    if not index_path.exists() or not sources_manifest_path.exists() or not curation_manifest_path.exists():
        return False
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    weekly_manifest = is_weekly_cascadia_manifest(manifest, edition_date)
    if not weekly_manifest:
        weekly_markers = {
            str(manifest.get("briefing_type") or "").strip().lower(),
            str(manifest.get("cadence") or "").strip().lower(),
            str(manifest.get("edition_type") or "").strip().lower(),
        }
        source_count = int(manifest.get("source_count", 0) or 0)
        story_count = int(manifest.get("story_count", 0) or 0)
        if "weekly" not in weekly_markers or source_count <= 0 or story_count <= 0:
            return False
    if "public_story_count" not in manifest:
        # Backward-compatible weekly manifests created before this field existed.
        return True
    public_story_count = int(manifest.get("public_story_count", 0) or 0)
    if public_story_count > 0:
        return True
    source_count = int(manifest.get("source_count", 0) or 0)
    story_count = int(manifest.get("story_count", 0) or 0)
    if source_count > 0 and story_count > 0:
        return True
    # Zero-story weeks are listable only when explicitly marked as review-credible.
    if manifest.get("minimum_review_threshold_met") is True:
        return True
    if str(manifest.get("zero_story_review_status") or "").strip().lower() == "credible":
        return True
    return False


def public_edition_manifest(site_root: Path, slug: str, edition_date: str) -> dict[str, Any]:
    manifest_path = site_root / slug / "editions" / edition_date / "edition_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def public_edition_label(site_root: Path, dispatch: DispatchConfig, edition_date: str) -> str:
    if dispatch.slug != "cascadia":
        return edition_date
    manifest = public_edition_manifest(site_root, dispatch.slug, edition_date)
    if manifest.get("coverage_label"):
        return str(manifest["coverage_label"])
    if manifest.get("coverage_start") and manifest.get("coverage_end"):
        return format_coverage_label(str(manifest["coverage_start"]), str(manifest["coverage_end"]))
    return edition_date


def public_edition_subtitle(site_root: Path, dispatch: DispatchConfig, edition_date: str) -> str:
    if dispatch.slug != "cascadia":
        return ""
    manifest = public_edition_manifest(site_root, dispatch.slug, edition_date)
    if manifest.get("public_story_count") == 0:
        if manifest.get("minimum_review_threshold_met") is True or manifest.get("zero_story_review_status") == "credible":
            return CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE_IDENTIFIED
        return CASCADIA_ZERO_STORY_PUBLIC_SUBTITLE_SURFACED
    subtitle = str(manifest.get("public_archive_subtitle") or "").strip()
    if subtitle:
        return subtitle
    parts = []
    if isinstance(manifest.get("public_story_count"), int):
        count = int(manifest["public_story_count"])
        parts.append(f"{count} {'story' if count == 1 else 'stories'}")
    states = [str(item) for item in manifest.get("public_state_hints") or [] if item]
    categories = [str(item) for item in manifest.get("public_categories") or [] if item]
    if states:
        parts.append(", ".join(states))
    if categories:
        parts.append(", ".join(categories[:4]))
    return " | ".join(parts)


def render_edition_list_item(site_root: Path, dispatch: DispatchConfig, date: str) -> str:
    label = public_edition_label(site_root, dispatch, date)
    subtitle = public_edition_subtitle(site_root, dispatch, date)
    subtitle_html = f'<br><small>{html.escape(subtitle)}</small>' if subtitle else ""
    actions = ""
    if dispatch.slug == "cascadia":
        map_path = site_root / "cascadia" / "editions" / date / "map.html"
        actions = ' <span class="edition-actions"><a href="editions/{0}/">Read briefing</a>'.format(date)
        if map_path.exists():
            actions += ' | <a href="editions/{0}/map.html">View map</a>'.format(date)
        actions += "</span>"
    return (
        f'      <li><span class="edition-date">{html.escape(label)}</span>'
        f'<a href="editions/{date}/">{html.escape(dispatch.name)} - {html.escape(label)}</a>{actions}{subtitle_html}</li>'
    )


def discover_public_edition_dates(site_root: Path, slug: str, max_edition_date: str | None = None) -> list[str]:
    editions_root = site_root / slug / "editions"
    if not editions_root.exists():
        return []
    return sorted(
        (
            path.name
            for path in editions_root.iterdir()
            if (
                path.is_dir()
                and len(path.name) == 10
                and (not max_edition_date or path.name <= max_edition_date)
                and public_edition_is_listable(site_root, slug, path.name)
            )
        ),
        reverse=True,
    )


def _display_date_range_for_week(edition_date: str) -> str:
    end = datetime.strptime(edition_date, "%Y-%m-%d").date()
    start = end - timedelta(days=6)
    return f"{start.strftime('%B')} {start.day}\u2013{end.strftime('%B')} {end.day}, {end.year}"


def _refresh_american_pressure_map_route(site_root: Path, edition_date: str, dry_run: bool, wrote: list[str]) -> None:
    map_dir = site_root / "american-pressure" / "map"
    map_data_path = map_dir / "map_data.json"
    map_html_path = map_dir / "index.html"
    edition_manifest_path = site_root / "american-pressure" / "editions" / edition_date / "edition_manifest.json"
    display_date_range = _display_date_range_for_week(edition_date)
    if edition_manifest_path.exists():
        try:
            manifest = json.loads(edition_manifest_path.read_text(encoding="utf-8"))
            display_date_range = str(manifest.get("display_date_range") or display_date_range)
        except json.JSONDecodeError:
            pass
    payload: dict[str, Any] = {
        "edition_date": edition_date,
        "display_date_range": display_date_range,
    }
    if map_data_path.exists():
        try:
            loaded = json.loads(map_data_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            pass
    payload["edition_date"] = edition_date
    payload["display_date_range"] = display_date_range
    write_text(map_data_path, json.dumps(payload, indent=2), dry_run, wrote)

    fallback_map_html = (
        f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>American Pressure Map</title><link rel="stylesheet" href="../assets/site.css"></head><body>'
        f'<main class="home ap-map-home"><header class="ap-map-top"><p class="eyebrow">American Pressure Map</p>'
        f'<h1>American Pressure Map</h1><p class="ap-map-subtitle">Source-backed signs of household or community strain across the U.S. ({html.escape(display_date_range)}).</p>'
        f'<p class="ap-map-links"><a href="/american-pressure/">Dispatch</a> | <a href="/american-pressure/archive.html">Archive</a> | <a href="/">Home</a></p>'
        f'</header></main></body></html>'
    )
    html_text = map_html_path.read_text(encoding="utf-8") if map_html_path.exists() else fallback_map_html
    updated = re.sub(
        r'(<p class="ap-map-subtitle">Source-backed signs of household or community strain across the U\.S\. \().*?(\)\.</p>)',
        rf"\g<1>{display_date_range}\g<2>",
        html_text,
        count=1,
    )
    updated = re.sub(
        r'<p class="ap-map-links">.*?</p>',
        '<p class="ap-map-links"><a href="/american-pressure/">Dispatch</a> | <a href="/american-pressure/archive.html">Archive</a> | <a href="/">Home</a></p>',
        updated,
        count=1,
        flags=re.DOTALL,
    )
    write_text(map_html_path, updated, dry_run, wrote)


def remove_unlistable_public_cascadia_editions(site_root: Path, dry_run: bool, wrote: list[str]) -> list[str]:
    editions_root = site_root / "cascadia" / "editions"
    if not editions_root.exists():
        return []
    removed: list[str] = []
    for edition_dir in sorted(editions_root.iterdir()):
        if not edition_dir.is_dir() or len(edition_dir.name) != 10:
            continue
        if public_edition_is_listable(site_root, "cascadia", edition_dir.name):
            continue
        removed.append(str(edition_dir))
        wrote.append(str(edition_dir))
        if not dry_run:
            shutil.rmtree(edition_dir)
    return removed


def render_dispatch_index_for_dates(dispatch: DispatchConfig, edition_dates: list[str], site_root: Path | None = None) -> str:
    latest = edition_dates[0] if edition_dates else ""
    signal_pack_note = ""
    if dispatch.slug == "cascadia":
        signal_pack_note = "\n    <p><strong>Cascadia Signal Pack</strong><br>Detailed downloadable records are being prepared for future release.</p>"
    description = (
        CASCADIA_PUBLIC_DESCRIPTION
        if dispatch.slug == "cascadia"
        else AMERICAN_PRESSURE_PUBLIC_DESCRIPTION
        if dispatch.slug == "american-pressure"
        else "Structured briefings compiled from traceable source records."
    )
    site_root = site_root or Path("output") / "site"
    recent = "\n".join(
        render_edition_list_item(site_root, dispatch, date)
        for date in edition_dates[:10]
    )
    map_link = ""
    dashboard_link = ""
    explainer_block = ""
    if dispatch.slug == "american-pressure":
        map_link = '\n    <p><a href="map/">View American Pressure Map</a></p>'
        if (site_root / "american-pressure" / "dashboard" / "index.html").exists():
            dashboard_link = '\n    <p><a href="dashboard/">View American Pressure Dashboard</a></p>'
        explainer_block = """
    <section class="section">
      <h2>What American Pressure Tracks</h2>
      <p><strong>What it tracks:</strong> Source-backed signs of household and community strain across food, housing, health care access, jobs, debt, local services, disaster recovery, transportation, benefits delivery, and childcare/schools.</p>
      <p><strong>What it does not claim:</strong> This is not a complete national census and does not measure every hardship event in the country.</p>
      <p><strong>How to read it:</strong> Each weekly edition links claims to source records. The map shows collected source-backed locations, not every affected place.</p>
    </section>"""
    elif dispatch.slug == "cascadia":
        map_link = '\n    <p><a href="map/">Open latest Cascadia pressure map</a></p>'
    latest_link = f'<p><a href="editions/{latest}/">Read the latest briefing</a></p>' if latest else "<p>No public edition is currently listed.</p>"
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)} archive</p>
    <p class="lede">{html.escape(description)}</p>
    {latest_link}
    {map_link}
    {dashboard_link}
    {explainer_block}
    {signal_pack_note}
    <h2>Recent Editions</h2>
    <ul class="edition-list">
{recent}
    </ul>
  </main>
{footer("")}"""
    return page(dispatch.name, f"{BASE_URL}/{dispatch.slug}/", "assets/site.css", body, dispatch.name)


def render_archive(dispatch: DispatchConfig) -> str:
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="archive">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">Archive</p>
    <h1>Edition Archive</h1>
    <ul class="edition-list">
      <li><span class="edition-date">{dispatch.edition_date}</span><a href="editions/{dispatch.edition_date}/">{html.escape(dispatch.name)} - {dispatch.edition_date}</a></li>
    </ul>
  </main>
{footer("")}"""
    return page(f"{dispatch.name} Archive", f"{BASE_URL}/{dispatch.slug}/archive.html", "assets/site.css", body, dispatch.name)


def render_archive_for_dates(dispatch: DispatchConfig, edition_dates: list[str], site_root: Path | None = None) -> str:
    site_root = site_root or Path("output") / "site"
    items = "\n".join(
        render_edition_list_item(site_root, dispatch, date)
        for date in edition_dates
    )
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="archive">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">Archive</p>
    <h1>Edition Archive</h1>
    <ul class="edition-list">
{items}
    </ul>
  </main>
{footer("")}"""
    return page(f"{dispatch.name} Archive", f"{BASE_URL}/{dispatch.slug}/archive.html", "assets/site.css", body, dispatch.name)


def render_sources(stories: list[StoryRecord], sources: list[SourceRecord]) -> str:
    source_by_id = {source.source_id: source for source in sources}
    chunks = []
    for story in stories:
        if not story.included_in_public_summary:
            continue
        chunks.append(f"<h2>{html.escape(story.title)}</h2>")
        chunks.append(f"<p>{html.escape(story.summary)}</p>")
        if story.source_ids:
            chunks.append("<ul>")
            for source_id in story.source_ids:
                source = source_by_id[source_id]
                chunks.append(f'<li><a href="{html.escape(source.url)}" target="_blank" rel="noopener noreferrer">{html.escape(source.title)}</a> - {html.escape(source.publisher)}</li>')
            chunks.append("</ul>")
    return "\n".join(chunks)


def render_gaza_structured_sections(edition_date: str, stories: list[StoryRecord], sources: list[SourceRecord]) -> str:
    source_by_id = {source.source_id: source for source in sources}
    top_story = stories[0] if stories else None
    other_stories = stories[1:] if len(stories) > 1 else []
    chunks: list[str] = []
    chunks.append("<h1>Dispatches From Gaza</h1>")
    if stories:
        chunks.append("<h2>At A Glance</h2>")
        chunks.append("<ul>")
        for story in stories:
            chunks.append(f"<li>{html.escape(story.title)}</li>")
        chunks.append("</ul>")
        chunks.append("<h2>Top Story</h2>")
        if top_story:
            chunks.append(f"<article><h3>{html.escape(top_story.title)}</h3>")
            chunks.append(f"<p>{html.escape(top_story.summary)}</p>")
            chunks.append("<p><strong>Sources</strong></p><ul>")
            for source_id in top_story.source_ids:
                source = source_by_id.get(source_id)
                if source is None:
                    continue
                chunks.append(
                    f'<li><a href="{html.escape(source.url)}" target="_blank" rel="noopener noreferrer">{html.escape(source.title)}</a> - {html.escape(source.publisher)}</li>'
                )
            chunks.append("</ul></article>")
        chunks.append("<h2>Other Gaza Developments</h2>")
        if other_stories:
            for story in other_stories:
                chunks.append(f"<article><h3>{html.escape(story.title)}</h3>")
                chunks.append(f"<p>{html.escape(story.summary)}</p>")
                chunks.append("<p><strong>Sources</strong></p><ul>")
                for source_id in story.source_ids:
                    source = source_by_id.get(source_id)
                    if source is None:
                        continue
                    chunks.append(
                        f'<li><a href="{html.escape(source.url)}" target="_blank" rel="noopener noreferrer">{html.escape(source.title)}</a> - {html.escape(source.publisher)}</li>'
                    )
                chunks.append("</ul></article>")
        else:
            chunks.append("<p>No additional core Gaza developments cleared the public threshold for this edition.</p>")
        chunks.append("<h2>Palestinian Developments</h2>")
        chunks.append("<p>No additional source-backed Palestinian developments cleared the public threshold for this edition.</p>")
    else:
        chunks.append("<p>No source-backed Gaza stories were generated for this date. Add project-local source records before publishing factual coverage.</p>")
    chunks.append("<h2>Source Note</h2>")
    chunks.append("<p>This briefing is based only on saved source records. Each story includes source links so readers can verify where the information came from.</p>")
    return "\n".join(chunks)


def render_edition(dispatch: DispatchConfig) -> str:
    body_html = dispatch.body_html or render_sources(dispatch.stories, dispatch.sources)
    if dispatch.slug == "gaza":
        body_html = body_html.replace("Daily Briefing - 2026-05-03", f"Daily Briefing - {dispatch.edition_date}")
    body = f"""{header(dispatch.name, "../../", "../../archive.html", f"/{dispatch.slug}/")}
  <main class="briefing">
    <section class="hero">
      <img class="hero-logo" src="../../assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)} / {dispatch.edition_date}</p>
    {body_html}
  </main>
{footer("../../")}"""
    return page(f"{dispatch.name} - {dispatch.edition_date}", f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/", "../../assets/site.css", body, dispatch.name)


def render_rss(dispatch: DispatchConfig) -> str:
    edition_url = f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/"
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
  <title>{html.escape(dispatch.name)}</title>
  <link>{BASE_URL}/{dispatch.slug}/</link>
  <description>{html.escape(CASCADIA_RSS_DESCRIPTION if dispatch.slug == "cascadia" else dispatch.tagline)}</description>
  <item>
    <title>{html.escape(dispatch.name)} - {dispatch.edition_date}</title>
    <link>{edition_url}</link>
    <guid>{edition_url}</guid>
  </item>
</channel>
</rss>
"""


def render_rss_for_dates(dispatch: DispatchConfig, edition_dates: list[str], site_root: Path | None = None) -> str:
    site_root = site_root or Path("output") / "site"
    items = "\n".join(
        f"""  <item>
    <title>{html.escape(dispatch.name)} - {html.escape(public_edition_label(site_root, dispatch, date))}</title>
    <link>{BASE_URL}/{dispatch.slug}/editions/{date}/</link>
    <guid>{BASE_URL}/{dispatch.slug}/editions/{date}/</guid>
    <description>{html.escape(public_edition_subtitle(site_root, dispatch, date) or public_edition_label(site_root, dispatch, date))}</description>
  </item>"""
        for date in edition_dates
    )
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
  <title>{html.escape(dispatch.name)}</title>
  <link>{BASE_URL}/{dispatch.slug}/</link>
  <description>{html.escape(CASCADIA_RSS_DESCRIPTION if dispatch.slug == "cascadia" else dispatch.tagline)}</description>
{items}
</channel>
</rss>
"""


def build_manifests(dispatch: DispatchConfig, site_root: Path, backup_root: Path, generated_at: str, warnings: list[str], errors: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    public_dir = site_root / dispatch.slug / "editions" / dispatch.edition_date
    backup_dir = backup_root / dispatch.slug / dispatch.edition_date
    source_manifest_public = public_dir / "sources_manifest.json"
    curation_manifest_public = public_dir / "curation_manifest.json"
    edition_manifest = {
        "dispatch_name": dispatch.name,
        "dispatch_slug": dispatch.slug,
        "public_visible": dispatch_public_visible(dispatch.slug),
        "edition_date": dispatch.edition_date,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/",
        "local_output_path": str(public_dir),
        "local_backup_path": str(backup_dir),
        "template_version": TEMPLATE_VERSION,
        "source_count": len(dispatch.sources),
        "story_count": len(dispatch.stories),
        "source_manifest_path": str(source_manifest_public),
        "curation_manifest_path": str(curation_manifest_public),
        "free_public_artifacts": [
            str(public_dir / "index.html"),
            str(source_manifest_public),
            str(curation_manifest_public),
        ],
        "paid_or_detail_artifacts": [],
        "future_paid_fields_todo": [
            "county_fips",
            "state",
            "county",
            "food_pressure_score",
            "health_access_pressure_score",
            "household_cost_pressure_score",
            "environmental_pressure_score",
            "local_system_strain_score",
            "source_count",
            "confidence_label",
            "latest_update_date",
            "movement_since_last_period",
        ]
        if dispatch.slug == "american-pressure"
        else [],
        "detail_artifacts_publicly_exposed": False,
        "warnings": warnings,
        "errors": errors,
    }
    return edition_manifest, asdicts(dispatch.sources), asdicts(dispatch.stories)


def build_site(
    root: Path,
    dry_run: bool = False,
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    only_dispatches: tuple[str, ...] = (),
    public_max_dates: dict[str, str] | None = None,
    dispatch_seed_dates: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    site_root = root / "output" / "site"
    detail_roots = [root / "output" / name for name in DETAIL_ROOT_NAMES]
    generated_at = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []
    errors: list[str] = []
    all_dispatches = seed_dispatches(root, generated_at, warnings, errors, dispatch_seed_dates=dispatch_seed_dates)
    dispatches = all_dispatches
    if only_dispatches:
        dispatches = [dispatch for dispatch in all_dispatches if dispatch.slug in only_dispatches]
    errors.extend(validate_traceability(all_dispatches))
    errors.extend(ensure_public_detail_separation(site_root, detail_roots))
    wrote: list[str] = []
    public_urls = [f"{BASE_URL}/"]

    for asset in PUBLIC_SITE_ASSETS:
        copy_asset(root / "assets" / asset, site_root / "assets" / asset, dry_run, wrote, warnings)

    # Keep root landing cards stable across scoped publishes.
    write_text(site_root / "index.html", render_root(all_dispatches), dry_run, wrote)
    public_max_dates = public_max_dates or {}
    for dispatch in dispatches:
        max_public_date = public_max_dates.get(dispatch.slug)
        skip_current_edition = bool(max_public_date and dispatch.edition_date > max_public_date)
        public_urls.append(f"{BASE_URL}/{dispatch.slug}/")
        dispatch_public_root = site_root / dispatch.slug
        dispatch_public_edition = dispatch_public_root / "editions" / dispatch.edition_date
        backup_dir = backup_root / dispatch.slug / dispatch.edition_date
        for asset in ["site.css", dispatch.logo, "bluefern.png"]:
            copy_asset(root / "assets" / asset, dispatch_public_root / "assets" / asset, dry_run, wrote, warnings)
        write_text(dispatch_public_root / "index.html", render_dispatch_index(dispatch), dry_run, wrote)
        write_text(dispatch_public_root / "archive.html", render_archive(dispatch), dry_run, wrote)
        write_text(dispatch_public_root / "rss.xml", render_rss(dispatch), dry_run, wrote)
        copied_real_edition = (
            (
                dispatch.slug in {"cascadia", "gaza", "american-pressure"}
                and copy_real_dispatch_edition(root, dispatch.slug, dispatch.edition_date, site_root, dry_run, wrote)
            )
            if not skip_current_edition
            else False
        )
        if dispatch.slug == "gaza" and not copied_real_edition:
            seed_candidates = [
                {
                    "source_record_id": source.source_id,
                    "title": source.title,
                    "url": source.url,
                    "canonical_url": source.url,
                    "publisher": source.publisher,
                    "published_at": source.published_at,
                    "retrieved_at": source.retrieved_at,
                    "category_hint": "humanitarian",
                }
                for source in dispatch.sources
            ]
            filtered_candidates, dedupe_report = filter_recent_duplicate_sources(root, dispatch.edition_date, seed_candidates, lookback_days=7)
            dedupe_report_path = root / "data" / "dispatches" / "gaza" / "editions" / dispatch.edition_date / "dedupe_report.json"
            write_text(dedupe_report_path, json.dumps(dedupe_report, indent=2), dry_run, wrote)
            if dedupe_report.get("suppressed_candidate_count", 0):
                warnings.append(
                    f"gaza synthetic fallback suppressed {dedupe_report['suppressed_candidate_count']} repeated candidates via cross-edition dedupe"
                )
            if dedupe_report.get("input_candidate_count", 0) > 0 and not filtered_candidates:
                errors.append("No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition.")
                continue
            kept_ids = {str(item.get("source_record_id") or "") for item in filtered_candidates}
            kept_sources = [source for source in dispatch.sources if source.source_id in kept_ids]
            kept_stories = [
                story
                for story in dispatch.stories
                if any(source_id in kept_ids for source_id in story.source_ids)
            ]
            dispatch = DispatchConfig(
                slug=dispatch.slug,
                name=dispatch.name,
                edition_date=dispatch.edition_date,
                tagline=dispatch.tagline,
                logo=dispatch.logo,
                sources=kept_sources,
                stories=kept_stories,
                body_html=render_gaza_structured_sections(dispatch.edition_date, kept_stories, kept_sources),
                detail_artifacts=dispatch.detail_artifacts or [],
            )
        if skip_current_edition:
            pass
        elif copied_real_edition:
            public_urls.append(f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/")
        elif dispatch.slug != "cascadia":
            public_urls.append(f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/")
            edition_html = render_edition(dispatch)
            write_text(dispatch_public_edition / "index.html", edition_html, dry_run, wrote)
            edition_manifest, sources_manifest, curation_manifest = build_manifests(dispatch, site_root, backup_root, generated_at, warnings, errors)
            write_text(dispatch_public_edition / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
            write_text(dispatch_public_edition / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
            write_text(dispatch_public_edition / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            if dispatch.slug in {"gaza", "american-pressure"}:
                dispatch_output_edition = root / "output" / "dispatches" / dispatch.slug / "editions" / dispatch.edition_date
                write_text(dispatch_output_edition / "index.html", edition_html, dry_run, wrote)
                if dispatch.slug == "american-pressure":
                    write_text(dispatch_output_edition / "edition.html", edition_html, dry_run, wrote)
                    write_text(dispatch_output_edition / "edition.md", dispatch.body_html or "", dry_run, wrote)
                write_text(dispatch_output_edition / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
                write_text(dispatch_output_edition / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
                write_text(dispatch_output_edition / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "index.html", edition_html, dry_run, wrote)
            write_text(backup_dir / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "run_manifest.json", json.dumps({"generated_at": generated_at, "dry_run": dry_run, "warnings": warnings, "errors": errors}, indent=2), dry_run, wrote)
        if dispatch.slug in {"gaza", "cascadia", "american-pressure"}:
            if dispatch.slug == "cascadia":
                remove_unlistable_public_cascadia_editions(site_root, dry_run, wrote)
            edition_dates = discover_public_edition_dates(site_root, dispatch.slug, max_edition_date=max_public_date)
            if dispatch.edition_date not in edition_dates and public_edition_is_listable(site_root, dispatch.slug, dispatch.edition_date):
                if not max_public_date or dispatch.edition_date <= max_public_date:
                    edition_dates = sorted([*edition_dates, dispatch.edition_date], reverse=True)
            if dispatch.slug == "american-pressure" and edition_dates:
                _refresh_american_pressure_map_route(site_root, edition_dates[0], dry_run, wrote)
            write_text(dispatch_public_root / "index.html", render_dispatch_index_for_dates(dispatch, edition_dates, site_root), dry_run, wrote)
            write_text(dispatch_public_root / "archive.html", render_archive_for_dates(dispatch, edition_dates, site_root), dry_run, wrote)
            write_text(dispatch_public_root / "rss.xml", render_rss_for_dates(dispatch, edition_dates, site_root), dry_run, wrote)
    ensure_public_html_favicons(site_root, dry_run, wrote)
    return {
        "ok": not errors,
        "dry_run": dry_run,
        "would_push": False,
        "pushed": False,
        "public_urls": public_urls,
        "backup_root": str(backup_root),
        "wrote": wrote,
        "warnings": warnings,
        "errors": errors,
        "paid_detail_excluded_from_public": True,
    }


def _expected_dispatches_for_date_checks(
    expect_date: str | None,
    expect_dispatches: tuple[str, ...],
    only_dispatches: tuple[str, ...],
) -> tuple[str, ...]:
    if expect_dispatches:
        dispatches_to_check = expect_dispatches
    elif expect_date and only_dispatches:
        dispatches_to_check = tuple(slug for slug in only_dispatches if slug in {"gaza", "cascadia", "american-pressure"})
    else:
        dispatches_to_check = ALL_EXPECT_DISPATCHES
    if only_dispatches:
        scoped = tuple(slug for slug in dispatches_to_check if slug in only_dispatches)
        if scoped:
            dispatches_to_check = scoped
    return dispatches_to_check


def _validate_build_public_urls_expected_date(
    build: dict[str, Any],
    expect_date: str | None,
    expect_dispatches: tuple[str, ...],
    only_dispatches: tuple[str, ...],
    site_root: Path | None = None,
) -> list[str]:
    if not expect_date:
        return []
    # Strict selected-public-URL date matching is meaningful only for scoped
    # publish runs where --only-dispatch is set.
    if not only_dispatches:
        return []
    errors: list[str] = []
    dispatches_to_check = tuple(expect_dispatches)
    public_urls = [str(url) for url in build.get("public_urls", [])]
    for dispatch_slug in dispatches_to_check:
        marker = f"/{dispatch_slug}/editions/"
        seen_dates: set[str] = set()
        for url in public_urls:
            if marker not in url:
                continue
            tail = url.split(marker, 1)[1].strip("/")
            candidate = tail.split("/", 1)[0]
            if len(candidate) == 10:
                seen_dates.add(candidate)
        if seen_dates and seen_dates != {expect_date}:
            if site_root is not None and public_edition_is_listable(site_root, dispatch_slug, expect_date):
                continue
            errors.append(
                f"expected {dispatch_slug} edition date {expect_date}, but selected public URLs include: {', '.join(sorted(seen_dates))}"
            )
    return errors


def _filter_dispatches_for_strict_expected_url_check(
    site_root: Path,
    expect_date: str | None,
    dispatches_to_check: tuple[str, ...],
) -> tuple[str, ...]:
    if not expect_date:
        return ()
    eligible: list[str] = []
    for dispatch_slug in dispatches_to_check:
        dispatch_edition = site_root.parents[1] / "dispatches" / dispatch_slug / "editions" / expect_date
        # Strict public-URL date checks are only reliable when we have a
        # concrete dispatch build artifact for the expected date.
        if dispatch_edition.exists():
            eligible.append(dispatch_slug)
    return tuple(eligible)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def collect_public_site_files(
    site_root: Path, only_dispatches: tuple[str, ...] = (), public_max_dates: dict[str, str] | None = None
) -> list[Path]:
    if not site_root.exists():
        return []
    files = []
    for path in site_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(site_root).parts
        if only_dispatches and relative_parts and relative_parts[0] in DISPATCH_LABELS and relative_parts[0] not in only_dispatches:
            continue
        if len(relative_parts) >= 4 and relative_parts[0] in {"gaza", "cascadia", "american-pressure"} and relative_parts[1] == "editions":
            slug = relative_parts[0]
            edition_date = relative_parts[2]
            max_public_date = public_max_dates.get(slug)
            if max_public_date and edition_date > max_public_date:
                continue
            if not public_edition_is_listable(site_root, slug, edition_date):
                continue
        files.append(path)
    return sorted(files)


def validate_pages_publish(
    root: Path,
    site_root: Path,
    pages_repo: Path,
    require_git: bool = True,
    expect_date: str | None = None,
    expect_dispatches: tuple[str, ...] = (),
    only_dispatches: tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    planning_mode = not require_git
    root = root.resolve()
    site_root = site_root.resolve()
    pages_repo = pages_repo.resolve()
    if not pages_repo.exists():
        errors.append(f"pages repo path does not exist: {pages_repo}")
    elif not pages_repo.is_dir():
        errors.append(f"pages repo path is not a directory: {pages_repo}")
    elif require_git and not (pages_repo / ".git").is_dir():
        errors.append(f"pages repo path is not a git repository: {pages_repo}")
    elif not (pages_repo / ".git").is_dir():
        warnings.append(f"pages repo path is not a git repository yet: {pages_repo}")
    if pages_repo == root:
        errors.append("pages repo path must not be the project root")
    if site_root.exists() and is_relative_to(pages_repo, site_root):
        errors.append("pages repo path must not be inside output/site")
    if not (site_root / "index.html").exists() and not planning_mode:
        errors.append(f"public site index does not exist: {site_root / 'index.html'}")
    dispatches_to_check = _expected_dispatches_for_date_checks(expect_date, expect_dispatches, only_dispatches)
    if ((not only_dispatches) or ("gaza" in only_dispatches)) and not planning_mode:
        if not (site_root / "gaza" / "archive.html").exists():
            errors.append(f"Gaza archive does not exist: {site_root / 'gaza' / 'archive.html'}")
    if ((not only_dispatches) or ("american-pressure" in only_dispatches)) and not planning_mode:
        if not (site_root / "american-pressure" / "archive.html").exists():
            errors.append(f"American Pressure archive does not exist: {site_root / 'american-pressure' / 'archive.html'}")
    detail_files = public_site_contains_detail_artifacts(site_root)
    if detail_files:
        errors.append(f"paid/detail artifacts are present in public output: {', '.join(detail_files)}")
    blocked_public_text = public_site_contains_blocked_public_text(site_root)
    if blocked_public_text:
        errors.append(f"blocked private artifact names are present in public output: {', '.join(blocked_public_text)}")
    if expect_date:
        for dispatch_slug in dispatches_to_check:
            source_edition = site_root / dispatch_slug / "editions" / expect_date
            dispatch_edition = root / "output" / "dispatches" / dispatch_slug / "editions" / expect_date
            if not source_edition.exists() and dispatch_edition.exists():
                continue
            if planning_mode:
                continue
            if not source_edition.exists():
                label = DISPATCH_LABELS.get(dispatch_slug, dispatch_slug)
                errors.append(f"expected {label} edition missing: {expect_date}")
            elif source_edition.exists() and not (source_edition / "index.html").exists():
                label = DISPATCH_LABELS.get(dispatch_slug, dispatch_slug)
                errors.append(f"expected {label} edition exists but index is missing: {source_edition / 'index.html'}")
    cname = pages_repo / "CNAME"
    if cname.exists() and cname.read_text(encoding="utf-8").strip() != CNAME_VALUE:
        errors.append(f"CNAME value is not correct in {cname}")
    return errors, warnings


def copy_public_site_to_pages(
    site_root: Path,
    pages_repo: Path,
    dry_run: bool,
    only_dispatches: tuple[str, ...] = (),
    public_max_dates: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    skipped = [
        "output/paid/",
        "output/detail/",
        "data/",
        "backups/",
        "source Python files",
        "tests/",
        ".venv/",
        "__pycache__/",
        ".pytest_cache/",
        f"{pages_repo / '.git'}",
    ]
    for source in collect_public_site_files(site_root, only_dispatches=only_dispatches, public_max_dates=public_max_dates):
        target = pages_repo / source.relative_to(site_root)
        copied.append(str(target))
        if dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    cname = pages_repo / "CNAME"
    copied.append(str(cname))
    if not dry_run:
        cname.write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    return copied, skipped


def remove_non_publishable_pages_editions(site_root: Path, pages_repo: Path, dry_run: bool) -> list[str]:
    editions_root = pages_repo / "cascadia" / "editions"
    if not editions_root.exists():
        return []
    removed: list[str] = []
    for edition_dir in sorted(editions_root.iterdir()):
        if not edition_dir.is_dir() or len(edition_dir.name) != 10:
            continue
        if public_edition_is_listable(site_root, "cascadia", edition_dir.name):
            continue
        removed.append(str(edition_dir))
        if not dry_run:
            shutil.rmtree(edition_dir)
    return removed


def remove_pages_editions_above_date(
    pages_repo: Path, slug: str, max_edition_date: str | None, dry_run: bool
) -> list[str]:
    if not max_edition_date:
        return []
    editions_root = pages_repo / slug / "editions"
    if not editions_root.exists():
        return []
    removed: list[str] = []
    for edition_dir in sorted(editions_root.iterdir()):
        if not edition_dir.is_dir() or len(edition_dir.name) != 10:
            continue
        if edition_dir.name <= max_edition_date:
            continue
        removed.append(str(edition_dir))
        if not dry_run:
            shutil.rmtree(edition_dir)
    return removed


def remove_pages_path(path: Path, dry_run: bool) -> list[str]:
    if not path.exists():
        return []
    removed = [str(path)]
    if not dry_run:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return removed


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    cwd = cwd.resolve()
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd}", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def git_stdout(args: list[str], cwd: Path) -> str | None:
    result = run_git(args, cwd)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_ref_exists(ref: str, cwd: Path) -> bool:
    return run_git(["show-ref", "--verify", "--quiet", ref], cwd).returncode == 0


def manual_push_command(pages_repo: Path, pages_branch: str) -> str:
    return f'cd "{pages_repo}"\ngit status\ngit push origin {pages_branch}'


def ensure_pages_branch(pages_repo: Path, pages_branch: str, dry_run: bool) -> dict[str, Any]:
    current_branch = git_stdout(["branch", "--show-current"], pages_repo) or None
    result: dict[str, Any] = {
        "current_branch": current_branch,
        "target_pages_branch": pages_branch,
        "checked_out_branch": current_branch if dry_run else None,
        "fetch_attempted": False,
        "fetched": False,
        "created_pages_branch": False,
        "warnings": [],
        "errors": [],
    }
    if dry_run:
        return result

    remotes = git_stdout(["remote"], pages_repo) or ""
    if "origin" in remotes.split():
        result["fetch_attempted"] = True
        fetch = run_git(["fetch", "origin"], pages_repo)
        result["fetched"] = fetch.returncode == 0
        if fetch.returncode != 0:
            result["warnings"].append(fetch.stderr.strip() or fetch.stdout.strip() or "git fetch origin failed; continuing with local refs")

    local_ref = f"refs/heads/{pages_branch}"
    remote_ref = f"refs/remotes/origin/{pages_branch}"
    if git_ref_exists(local_ref, pages_repo):
        checkout = run_git(["checkout", pages_branch], pages_repo)
    elif git_ref_exists(remote_ref, pages_repo):
        checkout = run_git(["checkout", "-b", pages_branch, "--track", f"origin/{pages_branch}"], pages_repo)
        result["created_pages_branch"] = checkout.returncode == 0
    else:
        checkout = run_git(["checkout", "-b", pages_branch], pages_repo)
        result["created_pages_branch"] = checkout.returncode == 0
        if checkout.returncode == 0:
            result["warnings"].append(f"{pages_branch} did not exist locally or at origin; created it from the current Pages repo worktree.")
    if checkout.returncode != 0:
        result["errors"].append(checkout.stderr.strip() or checkout.stdout.strip() or f"could not checkout {pages_branch}")
        return result
    result["checked_out_branch"] = git_stdout(["branch", "--show-current"], pages_repo) or pages_branch
    return result


def validate_pages_repo_after_copy(
    pages_repo: Path,
    site_root: Path,
    expect_date: str | None,
    expect_dispatches: tuple[str, ...] = (),
    only_dispatches: tuple[str, ...] = (),
) -> list[str]:
    errors: list[str] = []
    if not (pages_repo / ".git").exists():
        errors.append(f".git was not preserved in Pages repo: {pages_repo / '.git'}")
    cname = pages_repo / "CNAME"
    if not cname.exists() or cname.read_text(encoding="utf-8").strip() != CNAME_VALUE:
        errors.append(f"CNAME does not contain {CNAME_VALUE}")
    if not (pages_repo / "index.html").exists():
        errors.append(f"Pages repo index does not exist: {pages_repo / 'index.html'}")
    if (not only_dispatches) or ("gaza" in only_dispatches):
        if not (pages_repo / "gaza" / "archive.html").exists():
            errors.append(f"Pages repo Gaza archive does not exist: {pages_repo / 'gaza' / 'archive.html'}")
    if (pages_repo / "detail").exists() or (pages_repo / "paid").exists():
        errors.append("paid/detail artifacts were copied into the Pages repo")
    blocked_text = public_site_contains_blocked_public_text(pages_repo)
    if blocked_text:
        errors.append(f"blocked private artifact names are present in Pages repo: {', '.join(blocked_text)}")
    dispatches_to_check = _expected_dispatches_for_date_checks(expect_date, expect_dispatches, only_dispatches)
    if expect_date:
        if "gaza" in dispatches_to_check and public_edition_is_listable(site_root, "gaza", expect_date):
            if not (pages_repo / "gaza" / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected Gaza edition missing: {expect_date}")
        if "cascadia" in dispatches_to_check and public_edition_is_listable(site_root, "cascadia", expect_date):
            if not (pages_repo / "cascadia" / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected Cascadia edition missing: {expect_date}")
        if "american-pressure" in dispatches_to_check and public_edition_is_listable(site_root, "american-pressure", expect_date):
            if not (pages_repo / "american-pressure" / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected American Pressure edition missing: {expect_date}")
    return errors


def maybe_commit_pages_repo(pages_repo: Path, dry_run: bool, commit: bool, pages_branch: str) -> dict[str, Any]:
    if not commit:
        return {"would_commit": False, "committed": False, "commit_sha": None, "committed_branch": None, "message": "commit flag not set"}
    if dry_run:
        return {"would_commit": True, "committed": False, "commit_sha": None, "committed_branch": None, "message": "dry run; no commit created"}

    add = run_git(["add", "-A"], pages_repo)
    if add.returncode != 0:
        return {"would_commit": True, "committed": False, "commit_sha": None, "committed_branch": None, "message": add.stderr.strip() or add.stdout.strip()}

    diff = run_git(["diff", "--cached", "--quiet"], pages_repo)
    if diff.returncode == 0:
        return {"would_commit": True, "committed": False, "commit_sha": None, "committed_branch": pages_branch, "message": "no changes to commit"}

    commit_result = run_git(["commit", "-m", PUBLISH_COMMIT_MESSAGE], pages_repo)
    if commit_result.returncode != 0:
        return {"would_commit": True, "committed": False, "commit_sha": None, "committed_branch": pages_branch, "message": commit_result.stderr.strip() or commit_result.stdout.strip()}

    rev = run_git(["rev-parse", "--short", "HEAD"], pages_repo)
    return {
        "would_commit": True,
        "committed": True,
        "commit_sha": rev.stdout.strip() if rev.returncode == 0 else None,
        "committed_branch": pages_branch,
        "message": PUBLISH_COMMIT_MESSAGE,
    }


def publish_pages(
    root: Path,
    pages_repo: Path,
    remote_url: str | None,
    dry_run: bool,
    commit: bool,
    no_push: bool,
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    pages_branch: str = DEFAULT_PAGES_BRANCH,
    expect_date: str | None = None,
    expect_dispatches: tuple[str, ...] = (),
    only_dispatches: tuple[str, ...] = (),
) -> dict[str, Any]:
    public_max_dates: dict[str, str] = {}
    dispatch_seed_dates: dict[str, str] = {}
    ap_targeted = "american-pressure" in only_dispatches or "american-pressure" in expect_dispatches
    if expect_date and ap_targeted:
        public_max_dates["american-pressure"] = expect_date
        resolved_root = root.resolve()
        existing_dispatch_edition = resolved_root / "output" / "dispatches" / "american-pressure" / "editions" / expect_date
        if existing_dispatch_edition.exists():
            dispatch_seed_dates["american-pressure"] = expect_date
    build = build_site(
        root,
        dry_run=dry_run,
        backup_root=backup_root,
        only_dispatches=only_dispatches,
        public_max_dates=public_max_dates,
        dispatch_seed_dates=dispatch_seed_dates,
    )
    root = root.resolve()
    site_root = root / "output" / "site"
    pages_repo = pages_repo.resolve()
    errors = list(build["errors"])
    validation_errors, validation_warnings = validate_pages_publish(
        root,
        site_root,
        pages_repo,
        require_git=not dry_run,
        expect_date=expect_date,
        expect_dispatches=expect_dispatches,
        only_dispatches=only_dispatches,
    )
    errors.extend(validation_errors)
    dispatches_to_check = _expected_dispatches_for_date_checks(expect_date, expect_dispatches, only_dispatches)
    errors.extend(
        _validate_build_public_urls_expected_date(
            build,
            expect_date,
            dispatches_to_check,
            only_dispatches,
            site_root=site_root,
        )
    )
    warnings = list(build["warnings"])
    warnings.extend(validation_warnings)
    branch_result = {
        "current_branch": None,
        "target_pages_branch": pages_branch,
        "checked_out_branch": None,
        "fetch_attempted": False,
        "fetched": False,
        "created_pages_branch": False,
        "warnings": [],
        "errors": [],
    }
    if not errors and (pages_repo / ".git").exists():
        branch_result = ensure_pages_branch(pages_repo, pages_branch, dry_run=dry_run)
        errors.extend(branch_result["errors"])
        warnings.extend(branch_result["warnings"])
    would_copy = not errors
    copied: list[str] = []
    skipped: list[str] = []
    removed_non_publishable: list[str] = []
    commit_result = {"would_commit": bool(commit), "committed": False, "commit_sha": None, "committed_branch": None, "message": "not attempted"}

    if not errors:
        if not only_dispatches or "cascadia" in only_dispatches:
            removed_non_publishable = remove_non_publishable_pages_editions(site_root, pages_repo, dry_run=dry_run)
        if "american-pressure" in public_max_dates and (not only_dispatches or "american-pressure" in only_dispatches):
            removed_non_publishable.extend(
                remove_pages_editions_above_date(
                    pages_repo,
                    "american-pressure",
                    public_max_dates.get("american-pressure"),
                    dry_run=dry_run,
                )
            )
        copied, skipped = copy_public_site_to_pages(
            site_root,
            pages_repo,
            dry_run=dry_run,
            only_dispatches=only_dispatches,
            public_max_dates=public_max_dates,
        )
        if not dry_run:
            errors.extend(
                validate_pages_repo_after_copy(
                    pages_repo,
                    site_root,
                    expect_date,
                    expect_dispatches=expect_dispatches,
                    only_dispatches=only_dispatches,
                )
            )
        commit_result = maybe_commit_pages_repo(pages_repo, dry_run=dry_run, commit=commit, pages_branch=pages_branch)
        if commit and not commit_result["committed"] and commit_result["message"] not in {"dry run; no commit created", "no changes to commit"}:
            errors.append(commit_result["message"])

    return {
        "ok": not errors,
        "source_site_path": str(site_root),
        "target_pages_repo_path": str(pages_repo),
        "remote_url": remote_url,
        "cname_value": CNAME_VALUE,
        "current_branch": branch_result["current_branch"],
        "target_pages_branch": pages_branch,
        "checked_out_branch": branch_result["checked_out_branch"],
        "committed_branch": commit_result["committed_branch"],
        "dry_run": dry_run,
        "files_that_would_be_copied": copied if dry_run else [],
        "files_copied": [] if dry_run else copied,
        "non_publishable_pages_editions_removed": [] if dry_run else removed_non_publishable,
        "non_publishable_pages_editions_that_would_be_removed": removed_non_publishable if dry_run else [],
        "files_that_would_be_skipped": skipped,
        "would_copy": would_copy,
        "copied": bool(copied) and not dry_run,
        "would_commit": bool(commit),
        "committed": commit_result["committed"],
        "commit_sha": commit_result["commit_sha"],
        "message": commit_result["message"],
        "would_push": False,
        "pushed": False,
        "no_push": no_push,
        "manual_push_command": manual_push_command(pages_repo, pages_branch),
        "expect_date": expect_date,
        "expect_dispatches": list(expect_dispatches),
        "only_dispatches": list(only_dispatches),
        "paid_detail_excluded_from_public": not public_site_contains_detail_artifacts(site_root)
        and not public_site_contains_blocked_public_text(site_root),
        "warnings": warnings,
        "errors": errors,
        "build": build,
    }


def normalize_expect_dispatches(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        return ()
    if "all" in values:
        if len(values) > 1:
            raise ValueError("--expect-dispatch all cannot be combined with another --expect-dispatch value")
        return ALL_EXPECT_DISPATCHES
    return tuple(values)


def normalize_only_dispatches(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not values:
        return ()
    normalized: list[str] = []
    for raw in values:
        for token in str(raw).split(","):
            value = token.strip().lower()
            if not value:
                continue
            if value not in ONLY_DISPATCH_CHOICES:
                raise ValueError(f"--only-dispatch must be one of: {', '.join(ONLY_DISPATCH_CHOICES)}")
            if value not in normalized:
                normalized.append(value)
    return tuple(normalized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Dispatches From The Blue Fern Co. static site.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without touching output files.")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT), help="Outside-repo backup root.")
    parser.add_argument("--pages-repo", help="Local GitHub Pages repo root to receive output/site files.")
    parser.add_argument("--remote-url", help="Expected GitHub remote URL for reporting.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Git branch GitHub Pages deploys from.")
    parser.add_argument("--expect-date", help="Optional YYYY-MM-DD date expected in generated public archives/editions.")
    parser.add_argument(
        "--expect-dispatch",
        action="append",
        choices=EXPECT_DISPATCH_CHOICES,
        default=[],
        help="Dispatch whose --expect-date edition must be present: gaza, cascadia, or all. Repeat for multiple dispatches. If omitted with --expect-date, legacy full-site checks are used.",
    )
    parser.add_argument(
        "--only-dispatch",
        action="append",
        default=[],
        help="Build/copy only selected dispatches (repeat or comma-separate): gaza, cascadia, american-pressure.",
    )
    parser.add_argument("--commit", action="store_true", help="Commit copied Pages repo changes locally.")
    parser.add_argument("--no-push", action="store_true", help="Explicitly skip push. Push is always skipped by this publisher.")
    args = parser.parse_args(argv)
    try:
        expect_dispatches = normalize_expect_dispatches(tuple(args.expect_dispatch))
        only_dispatches = normalize_only_dispatches(tuple(args.only_dispatch))
    except ValueError as exc:
        parser.error(str(exc))
    if args.pages_repo:
        result = publish_pages(
            Path.cwd(),
            Path(args.pages_repo),
            args.remote_url,
            dry_run=args.dry_run,
            commit=args.commit,
            no_push=args.no_push,
            backup_root=Path(args.backup_root),
            pages_branch=args.pages_branch,
            expect_date=args.expect_date,
            expect_dispatches=expect_dispatches,
            only_dispatches=only_dispatches,
        )
    else:
        public_max_dates: dict[str, str] = {}
        dispatch_seed_dates: dict[str, str] = {}
        ap_targeted = "american-pressure" in only_dispatches or "american-pressure" in expect_dispatches
        if args.expect_date and ap_targeted:
            public_max_dates["american-pressure"] = args.expect_date
            resolved_root = Path.cwd().resolve()
            existing_dispatch_edition = resolved_root / "output" / "dispatches" / "american-pressure" / "editions" / args.expect_date
            if existing_dispatch_edition.exists():
                dispatch_seed_dates["american-pressure"] = args.expect_date
        result = build_site(
            Path.cwd(),
            dry_run=args.dry_run,
            backup_root=Path(args.backup_root),
            only_dispatches=only_dispatches,
            public_max_dates=public_max_dates,
            dispatch_seed_dates=dispatch_seed_dates,
        )
        result_errors = list(result.get("errors", []))
        if args.expect_date:
            dispatches_to_check = _expected_dispatches_for_date_checks(args.expect_date, expect_dispatches, only_dispatches)
            site_root = Path.cwd().resolve() / "output" / "site"
            result_errors.extend(
                _validate_build_public_urls_expected_date(
                    result,
                    args.expect_date,
                    dispatches_to_check,
                    only_dispatches,
                    site_root=site_root,
                )
            )
            for dispatch_slug in dispatches_to_check:
                edition_index = site_root / dispatch_slug / "editions" / args.expect_date / "index.html"
                if not edition_index.exists():
                    result_errors.append(f"expected {dispatch_slug} edition missing from output/site: {args.expect_date}")
        if result_errors:
            result["errors"] = result_errors
            result["ok"] = False
        result_warnings = list(result.get("warnings", []))
        result_warnings.append("No --pages-repo provided: wrote only to output/site and did not modify a Pages repo.")
        result["warnings"] = result_warnings
        result["pages_repo_modified"] = False
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
