from __future__ import annotations

import argparse
import html
import hashlib
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
from bluefern_dispatches.care_line_render import (
    render_care_line_claim_ledger_html,
    render_care_line_edition_body,
    render_care_line_source_table_html,
)
from bluefern_dispatches.care_line_release_render import (
    build_public_rows as build_care_line_approved_public_rows,
    is_limited_source_release as care_line_release_is_limited_source,
    load_approved_release as load_care_line_approved_release,
)
from bluefern_dispatches.care_line_sources import (
    DISPATCH_NAME as CARE_LINE_DISPATCH_NAME,
    DISPATCH_SLUG as CARE_LINE_DISPATCH_SLUG,
    DISPATCH_TAGLINE as CARE_LINE_DISPATCH_TAGLINE,
    no_current_update_summary as care_line_no_current_update_summary,
    build_public_edition_report as care_line_public_edition_report,
    load_manual_source_records as load_care_line_manual_sources,
    load_pressure_source_registry as load_care_line_pressure_registry,
    public_archive_title_for_records as care_line_public_archive_title_for_records,
    summary_for_records as care_line_summary_for_records,
    record_is_public as care_line_record_is_public,
    care_line_review_diagnostics as care_line_review_diagnostics,
    validate_manual_source_records as validate_care_line_manual_sources,
    validate_pressure_source_registry as validate_care_line_pressure_registry,
)
from bluefern_dispatches.care_line_release import (
    initialize_public_release_status as initialize_care_line_public_release_status,
    sha256_file as care_line_sha256_file,
)
from bluefern_dispatches.dispatch_catalog import (
    DISPATCH_CATALOG,
    DISPATCH_LABELS,
    active_dispatch_slugs,
    dispatch_lifecycle_state,
    dispatch_public_visible,
)
from bluefern_dispatches.gaza_sources import filter_recent_duplicate_sources
from bluefern_dispatches.public_prose import html_contains_public_prose_violations


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
CARE_LINE_PUBLIC_DESCRIPTION = (
    "The Care Line Dispatch monitors source-backed reports of healthcare access pressure across the United States - "
    "hospital and clinic strain, rural access loss, coverage disruption, medical affordability, pharmacy access, "
    "staffing pressure, and public-health capacity cuts. It is designed for public reading, research, and accountability."
)
CARE_LINE_RSS_DESCRIPTION = "Source-backed signals of where American healthcare access is under strain."
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
GAZA_HOME_RECENT_EDITION_LIMIT = 10
GAZA_HOME_RECENT_EDITION_MIN = 3
GAZA_PUBLIC_HISTORY_DATE_RE = re.compile(r"(?:/gaza/)?editions/(\d{4}-\d{2}-\d{2})/")
GAZA_AUDIO_INDEX_DATE_RE = re.compile(r'<span class="gaza-audio-index-date"><strong>(\d{4}-\d{2}-\d{2})</strong>')
GAZA_AUDIO_PODCAST_DATE_RE = re.compile(r"/gaza/audio/(\d{4}-\d{2}-\d{2})-transcript\.html")
GAZA_HOME_EDITION_LIST_RE = re.compile(r'<ul class="edition-list">(.*?)</ul>', re.DOTALL)
EXPECT_DISPATCH_CHOICES = ("gaza", "cascadia", "american-pressure", "food-line", "care-line", "all")
ALL_EXPECT_DISPATCHES = ("gaza", "cascadia", "american-pressure", "food-line", "care-line")
ONLY_DISPATCH_CHOICES = ("gaza", "cascadia", "american-pressure", "food-line", "care-line")


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
    raw_records: list[dict[str, Any]] | None = None


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


def _care_line_fixtures(root: Path, edition_date: str) -> tuple[Any, Path | None]:
    base = root / "data" / "dispatches" / "care-line" / "sources"
    direct = base / edition_date / "manual_sources.json"
    discovered = base / edition_date / "discovered_sources.json"
    if direct.exists() or discovered.exists():
        rows: list[Any] = []
        if direct.exists():
            rows.extend(json.loads(direct.read_text(encoding="utf-8")))
        if discovered.exists():
            rows.extend(json.loads(discovered.read_text(encoding="utf-8")))
        return rows, direct if direct.exists() else discovered
    return [], None


def _latest_care_line_fixture_date(root: Path) -> str | None:
    base = root / "data" / "dispatches" / "care-line" / "sources"
    if not base.exists():
        return None
    dated = sorted(
        {
            path.parent.name
            for path in list(base.glob("*/manual_sources.json")) + list(base.glob("*/discovered_sources.json"))
            if path.is_file()
        }
    )
    return dated[-1] if dated else None


def _normalize_care_line_fixture_rows(
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
            errors.append(f"care-line manual sources file has invalid shape: {path_label}; {expected}")
            return []
    else:
        errors.append(f"care-line manual sources file has invalid shape: {path_label}; {expected}")
        return []
    valid_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            warnings.append(f"care-line fixture record {index + 1} in {path_label} is not an object; got {type(row).__name__}")
            continue
        valid_rows.append(row)
    return valid_rows


def _build_care_line_dispatch(
    root: Path,
    now: str,
    edition_date: str,
    warnings: list[str],
    errors: list[str],
    *,
    publication_scope: dict[str, Any] | None = None,
) -> DispatchConfig:
    data_root = root / "data" / "dispatches" / "care-line"
    registry_file = data_root / "pressure_source_registry.json"
    direct_fixture = data_root / "sources" / edition_date / "manual_sources.json"
    any_fixture = data_root / "sources"
    approved_release = load_care_line_approved_release(root, edition_date)
    if approved_release is not None and care_line_release_is_limited_source(approved_release):
        rows = build_care_line_approved_public_rows(approved_release)
        if not registry_file.exists() and not direct_fixture.exists() and not any_fixture.exists():
            warnings.append("care-line data tree is not present; using approved release snapshot")
        try:
            registry = load_care_line_pressure_registry(root)
        except FileNotFoundError as exc:
            warnings.append(str(exc))
            registry = []
        except Exception as exc:
            warnings.append(f"care-line registry validation failed: {exc}")
            registry = []
        else:
            errors.extend(validate_care_line_pressure_registry(registry))
        public_rows = [row for row in rows if care_line_record_is_public(row)]
        body_html = render_care_line_edition_body(public_rows, edition_date)
        if not registry:
            warnings.append("care-line pressure source registry is empty")
        return DispatchConfig(
            slug=CARE_LINE_DISPATCH_SLUG,
            name=CARE_LINE_DISPATCH_NAME,
            edition_date=edition_date,
            tagline=CARE_LINE_DISPATCH_TAGLINE,
            logo="care-line-logo.png",
            sources=[
                SourceRecord(
                    source_id=str(row.get("source_record_id") or ""),
                    title=str(row.get("title") or ""),
                    url=str(row.get("url") or ""),
                    publisher=str(row.get("publisher") or ""),
                    published_at=str(row.get("published_at") or None) if row.get("published_at") is not None else None,
                    retrieved_at=str(row.get("retrieved_at") or now),
                    archive_path=None,
                    used_in_story_ids=[f"care-line-story-{index:03d}"],
                    claim_ids=[f"care-line-claim-{index:03d}"],
                    dispatch_slug=CARE_LINE_DISPATCH_SLUG,
                    edition_date=edition_date,
                )
                for index, row in enumerate(public_rows, start=1)
            ],
            stories=[
                StoryRecord(
                    story_id=f"care-line-story-{index:03d}",
                    title=str(row.get("title") or ""),
                    summary=str(row.get("claim_supported") or row.get("pressure_summary") or row.get("summary_or_snippet") or ""),
                    category=str(row.get("public_inclusion_bucket") or "Other Care Line Signals"),
                    score=100 if row.get("included_as_lead") is True else 90,
                    scoring_reasons=[str(row.get("pressure_reason") or "source-backed approved release record")],
                    included_in_public_summary=True,
                    included_in_detail_dataset=False,
                    source_ids=[str(row.get("source_record_id") or "")],
                )
                for index, row in enumerate(public_rows, start=1)
            ],
            body_html=body_html,
            detail_artifacts=[],
            raw_records=rows,
        )
    if not registry_file.exists() and not direct_fixture.exists() and not any_fixture.exists():
        warnings.append("care-line data tree is not present; skipping care-line dispatch build")
        return DispatchConfig(
            slug=CARE_LINE_DISPATCH_SLUG,
            name=CARE_LINE_DISPATCH_NAME,
            edition_date=edition_date,
            tagline=CARE_LINE_DISPATCH_TAGLINE,
            logo="care-line-logo.png",
            sources=[],
            stories=[],
            body_html=render_care_line_edition_body([], edition_date),
            detail_artifacts=[],
            raw_records=[],
        )
    try:
        registry = load_care_line_pressure_registry(root)
        errors.extend(validate_care_line_pressure_registry(registry))
    except FileNotFoundError as exc:
        errors.append(str(exc))
        registry = []
    except Exception as exc:
        errors.append(f"care-line registry validation failed: {exc}")
        registry = []
    raw_payload, fixture_path = _care_line_fixtures(root, edition_date)
    rows = _normalize_care_line_fixture_rows(raw_payload, fixture_path, warnings, errors)
    # A guarded Signal Wire publication is selected from reviewed records and
    # Universal Events.  The legacy discovery fixture is retained for
    # traceability/artifact rendering, but its older schema must not become a
    # blocking validator for an explicitly selected Signal Wire slice.
    scoped_signal_wire = bool(publication_scope and publication_scope.get("selected_dispatches") == [CARE_LINE_DISPATCH_SLUG])
    if not scoped_signal_wire:
        errors.extend(validate_care_line_manual_sources(rows))
    if not rows:
        warnings.append("care-line has no fixture records; rendering a no-current-update edition")
    if fixture_path is None:
        warnings.append("care-line fixture file missing under data/dispatches/care-line/sources")

    sources: list[SourceRecord] = []
    stories: list[StoryRecord] = []
    for index, row in enumerate(rows, start=1):
        source_id = str(row.get("source_record_id") or f"care-line-src-{index:03d}")
        story_id = f"care-line-story-{index:03d}"
        included = row.get("qualifies_for_public_inclusion") is True and row.get("pressure_signal") is True
        if included:
            sources.append(
                SourceRecord(
                    source_id=source_id,
                    title=str(row.get("title") or ""),
                    url=str(row.get("url") or ""),
                    publisher=str(row.get("publisher") or ""),
                    published_at=str(row.get("published_at") or None) if row.get("published_at") is not None else None,
                    retrieved_at=str(row.get("retrieved_at") or now),
                    archive_path=None,
                    used_in_story_ids=[story_id],
                    claim_ids=[f"care-line-claim-{index:03d}"],
                    dispatch_slug=CARE_LINE_DISPATCH_SLUG,
                    edition_date=edition_date,
                )
            )
            stories.append(
                StoryRecord(
                    story_id=story_id,
                    title=str(row.get("title") or ""),
                    summary=str(row.get("claim_supported") or row.get("pressure_summary") or row.get("summary_or_snippet") or ""),
                    category=str(row.get("public_inclusion_bucket") or "Other Care Line Signals"),
                    score=100 if row.get("included_as_lead") is True else 90,
                    scoring_reasons=[str(row.get("pressure_reason") or "source-backed pilot record")],
                    included_in_public_summary=True,
                    included_in_detail_dataset=False,
                    source_ids=[source_id],
                )
            )
        else:
            sources.append(
                SourceRecord(
                    source_id=source_id,
                    title=str(row.get("title") or ""),
                    url=str(row.get("url") or ""),
                    publisher=str(row.get("publisher") or ""),
                    published_at=str(row.get("published_at") or None) if row.get("published_at") is not None else None,
                    retrieved_at=str(row.get("retrieved_at") or now),
                    archive_path=None,
                    used_in_story_ids=[],
                    claim_ids=[],
                    dispatch_slug=CARE_LINE_DISPATCH_SLUG,
                    edition_date=edition_date,
                )
            )

    if not sources:
        warnings.append("care-line has no source-backed fixture records; rendering a no-current-update page")
    if not registry:
        warnings.append("care-line pressure source registry is empty")

    public_rows = [row for row in rows if care_line_record_is_public(row)]
    public_summary = care_line_summary_for_records(public_rows) if public_rows else care_line_no_current_update_summary()
    body_html = render_care_line_edition_body(public_rows, edition_date)
    return DispatchConfig(
        slug=CARE_LINE_DISPATCH_SLUG,
        name=CARE_LINE_DISPATCH_NAME,
        edition_date=edition_date,
        tagline=CARE_LINE_DISPATCH_TAGLINE,
        logo="care-line-logo.png",
        sources=sources,
        stories=stories,
        body_html=body_html,
        detail_artifacts=[],
        raw_records=rows,
    )


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
    publication_scope: dict[str, Any] | None = None,
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
    gaza_seed_date = dispatch_seed_dates.get("gaza", date)
    ap_date = dispatch_seed_dates.get("american-pressure", date)
    care_line_date = dispatch_seed_dates.get("care-line") or _latest_care_line_fixture_date(root) or date
    gaza_sources = [
        SourceRecord("gaza-src-001", "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza", "https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF", "The New York Times", None, now, None, ["gaza-story-001"], ["gaza-claim-001"], "gaza", gaza_seed_date),
        SourceRecord("gaza-src-002", "U.S. to close Israel command center overseeing Gaza truce as Trump plan stalls", "https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE", "Haaretz", None, now, None, ["gaza-story-001"], ["gaza-claim-002"], "gaza", gaza_seed_date),
        SourceRecord("gaza-src-003", "Court extends detention of 2 Gaza flotilla activists accused of Hamas links", "https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR", "The Times of Israel", None, now, None, ["gaza-story-001"], ["gaza-claim-003"], "gaza", gaza_seed_date),
    ]
    cascadia_sources = [
        SourceRecord("cascadia-src-001", "Placeholder source record for Cascadia launch edition", f"{BASE_URL}/cascadia/editions/{date}/sources_manifest.json", "Blue Fern Dispatch Records", f"{date}T00:00:00Z", now, None, ["cascadia-story-001"], ["cascadia-admin-001"], "cascadia", date)
    ]
    return [
        DispatchConfig(
            slug="gaza",
            name="Dispatches From Gaza",
            edition_date=gaza_seed_date,
            tagline="Daily briefing",
            logo="gaza-logo.png",
            sources=gaza_sources,
            stories=[StoryRecord("gaza-story-001", f"Dispatches From Gaza - {date}", "Structured daily briefing synthesizing key developments from public reporting.", "humanitarian", 100, ["Preserved from existing Gaza public edition."], True, False, [s.source_id for s in gaza_sources])],
            body_html=gaza_body_html(date),
            detail_artifacts=[],
        ),
        _build_american_pressure_dispatch(root, now, ap_date, warnings, errors),
        _build_care_line_dispatch(
            root,
            now,
            care_line_date,
            warnings,
            errors,
            publication_scope=publication_scope,
        ),
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


def write_bytes(path: Path, content: bytes, dry_run: bool, wrote: list[str]) -> None:
    wrote.append(str(path))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


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
    if slug == CARE_LINE_DISPATCH_SLUG:
        required_names.extend(["source_table.html", "claim_ledger.html"])
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
    if slug == CARE_LINE_DISPATCH_SLUG:
        required_names.extend(["source_table.html", "claim_ledger.html"])
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


def page(
    title: str,
    canonical: str,
    css_href: str,
    body: str,
    site_name: str = "Dispatches From The Blue Fern Co.",
    *,
    og_type: str = "website",
    description: str | None = None,
    og_title: str | None = None,
    og_image: str | None = None,
    og_image_width: int | None = None,
    og_image_height: int | None = None,
    og_image_alt: str | None = None,
    twitter_title: str | None = None,
) -> str:
    title_meta = (
        f'  <meta property="og:title" content="{html.escape(og_title or title)}">\n'
        f'  <meta name="twitter:title" content="{html.escape(twitter_title or og_title or title)}">\n'
    )
    type_meta = f'  <meta property="og:type" content="{html.escape(og_type)}">\n'
    description_meta = ""
    if description:
        description_meta = (
            f'  <meta name="description" content="{html.escape(description)}">\n'
            f'  <meta property="og:description" content="{html.escape(description)}">\n'
            f'  <meta name="twitter:description" content="{html.escape(description)}">\n'
        )
    image_meta = ""
    if og_image:
        image_meta = f'  <meta property="og:image" content="{html.escape(og_image)}">\n  <meta name="twitter:image" content="{html.escape(og_image)}">\n'
        if og_image_width:
            image_meta += f'  <meta property="og:image:width" content="{og_image_width}">\n'
        if og_image_height:
            image_meta += f'  <meta property="og:image:height" content="{og_image_height}">\n'
        if og_image_alt:
            image_meta += f'  <meta property="og:image:alt" content="{html.escape(og_image_alt)}">\n  <meta name="twitter:image:alt" content="{html.escape(og_image_alt)}">\n'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="canonical" href="{html.escape(canonical)}">
{type_meta}{title_meta}{description_meta}{image_meta}  <meta property="og:site_name" content="{html.escape(site_name)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta property="og:url" content="{html.escape(canonical)}">
{favicon_links()}
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
{body}
</body>
</html>
"""


def header(
    brand: str,
    root_prefix: str,
    archive_href: str | None = None,
    section_href: str | None = None,
    *,
    nav_slugs: tuple[str, ...] | None = None,
) -> str:
    root_links = []
    for slug in nav_slugs or ("gaza", "cascadia", "food-line"):
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
    card_rows: list[str] = []
    summaries = {
        "gaza": "Daily source-backed briefings from Gaza.",
        "food-line": "Daily source-backed food insecurity pressure signals across the United States — where demand, benefit disruption, pantry strain, or access pressure is visible in verified sources.",
        "care-line": "Source-backed reporting on healthcare-access pressure and service strain.",
    }
    active_slugs = active_dispatch_slugs()
    dispatch_by_slug = {dispatch.slug: dispatch for dispatch in dispatches}
    for slug in active_slugs:
        if slug == "food-line":
            card_rows.append(
                f"""      <li class="dispatch-card" style="--dispatch-card-watermark: url('/food-line/assets/food-line-logo.png');">
        <a href="/food-line/">
          <span class="dispatch-card-watermark" aria-hidden="true"></span>
          <span class="dispatch-card-content">
            <span class="edition-date">{html.escape(summaries["food-line"])}</span>
            <strong>Food Line Dispatch</strong>
          </span>
        </a>
      </li>"""
            )
            continue
        dispatch = dispatch_by_slug.get(slug)
        if dispatch is None or slug not in summaries:
            continue
        card_style = f' style="--dispatch-card-watermark: url(\'/{dispatch.slug}/assets/{dispatch.logo}\');"'
        card_rows.append(
            f"""      <li class="dispatch-card"{card_style}>
        <a href="/{dispatch.slug}/">
          <span class="dispatch-card-watermark" aria-hidden="true"></span>
          <span class="dispatch-card-content">
            <span class="edition-date">{html.escape(summaries.get(dispatch.slug, dispatch.tagline))}</span>
            <strong>{html.escape(dispatch.name)}</strong>
          </span>
        </a>
      </li>"""
        )
    cards = "\n".join(card_rows)
    body = f"""{header("Dispatches From The Blue Fern Co.", "", nav_slugs=active_slugs)}
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
        signal_pack_note = (
            "\n    <section><h2>Signal Pack / Coming Soon</h2>"
            "<p><strong>Cascadia Signal Pack</strong><br>Coming soon.</p></section>"
            '\n    <section><h2>Detention Watch</h2><p><a href="/cascadia/detention-watch/">Open Detention Watch</a></p></section>'
        )
    description = (
        CASCADIA_PUBLIC_DESCRIPTION
        if dispatch.slug == "cascadia"
        else AMERICAN_PRESSURE_PUBLIC_DESCRIPTION
        if dispatch.slug == "american-pressure"
        else CARE_LINE_PUBLIC_DESCRIPTION
        if dispatch.slug == CARE_LINE_DISPATCH_SLUG
        else "Structured briefings compiled from traceable source records."
    )
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)}</p>
    <p class="lede">{html.escape(description)}</p>
    <p><a href="archive.html">Browse the Care Line archive</a></p>
    <p><a href="editions/{dispatch.edition_date}/">Read the latest briefing</a></p>
    <p><a href="editions/{dispatch.edition_date}/source_table.html">Open the source table</a> | <a href="editions/{dispatch.edition_date}/claim_ledger.html">Open the claim ledger</a></p>
    <h2>Recent Editions</h2>
    <ul class="edition-list">
      <li><span class="edition-date">{dispatch.edition_date}</span><a href="editions/{dispatch.edition_date}/">{html.escape(CARE_LINE_DISPATCH_TAGLINE)}</a></li>
    </ul>
  </main>
{footer("")}"""
        return page(dispatch.name, f"{BASE_URL}/{dispatch.slug}/", "assets/site.css", body, dispatch.name)
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


def _food_line_public_edition_listability_report(site_root: Path, edition_date: str) -> dict[str, Any]:
    edition_dir = site_root / "food-line" / "editions" / edition_date
    manifest_path = edition_dir / "edition_manifest.json"
    freshness_keys = (
        "public_rendered",
        "edition_mode",
        "source_freshness_status",
        "freshness_window_days",
        "stale_public_story_count",
        "excluded_stale_source_count",
        "stale_source_ids",
    )
    report: dict[str, Any] = {
        "dispatch_slug": "food-line",
        "edition_date": edition_date,
        "edition_dir": str(edition_dir),
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_path.exists(),
        "manifest_valid_json": False,
        "manifest_is_object": False,
        "dispatch_slug_value": "",
        "edition_date_value": "",
        "public_rendered": False,
        "edition_mode": "",
        "source_freshness_status": "",
        "freshness_window_days": None,
        "qualified_primary_count": None,
        "skip_reason": "",
        "listable": False,
        "missing_required_fields": list(freshness_keys),
        "false_or_invalid_fields": [],
        "reasons": [],
    }
    if not manifest_path.exists():
        report["reasons"].append("manifest missing")
        return report
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report["manifest_valid_json"] = True
    except (OSError, json.JSONDecodeError) as exc:
        report["reasons"].append(f"manifest invalid JSON: {exc}")
        return report
    if not isinstance(manifest, dict):
        report["reasons"].append("manifest is not an object")
        return report
    report["manifest_is_object"] = True
    report["dispatch_slug_value"] = str(manifest.get("dispatch_slug") or "")
    report["edition_date_value"] = str(manifest.get("edition_date") or "")
    report["public_rendered"] = manifest.get("public_rendered") is True
    report["edition_mode"] = str(manifest.get("edition_mode") or "").strip()
    report["source_freshness_status"] = str(manifest.get("source_freshness_status") or "").strip()
    report["skip_reason"] = str(manifest.get("skip_reason") or "").strip()
    report["missing_required_fields"] = [field for field in freshness_keys if field not in manifest]
    if report["dispatch_slug_value"] != "food-line":
        report["false_or_invalid_fields"].append("dispatch_slug")
        report["reasons"].append(f"dispatch_slug must be food-line (found {report['dispatch_slug_value'] or 'missing'})")
    if report["edition_date_value"] and report["edition_date_value"] != edition_date:
        report["false_or_invalid_fields"].append("edition_date")
        report["reasons"].append(f"edition_date mismatch: {report['edition_date_value']} != {edition_date}")
    if not report["public_rendered"]:
        report["false_or_invalid_fields"].append("public_rendered")
        report["reasons"].append("public_rendered is false")
    if not report["source_freshness_status"]:
        report["false_or_invalid_fields"].append("source_freshness_status")
        report["reasons"].append("source_freshness_status is missing or empty")
    try:
        freshness_window_days = int(manifest.get("freshness_window_days") or 0)
    except (TypeError, ValueError):
        freshness_window_days = None
        report["false_or_invalid_fields"].append("freshness_window_days")
        report["reasons"].append("freshness_window_days is missing or invalid")
    else:
        report["freshness_window_days"] = freshness_window_days
        if freshness_window_days <= 0:
            report["false_or_invalid_fields"].append("freshness_window_days")
            report["reasons"].append("freshness_window_days must be greater than zero")
    try:
        qualified_primary_count = int(manifest.get("qualified_primary_count") or 0)
    except (TypeError, ValueError):
        qualified_primary_count = None
        report["false_or_invalid_fields"].append("qualified_primary_count")
        report["reasons"].append("qualified_primary_count is missing or invalid")
    else:
        report["qualified_primary_count"] = qualified_primary_count
        if report["edition_mode"] == "no_current_update":
            if qualified_primary_count != 0:
                report["false_or_invalid_fields"].append("qualified_primary_count")
                report["reasons"].append("no_current_update editions require qualified_primary_count to equal 0")
        elif qualified_primary_count <= 0:
            report["false_or_invalid_fields"].append("qualified_primary_count")
            report["reasons"].append("current_update editions require qualified_primary_count greater than 0")
        elif report["edition_mode"] != "current_update":
            report["false_or_invalid_fields"].append("edition_mode")
            report["reasons"].append(f"edition_mode must be current_update for public Food Line editions (found {report['edition_mode'] or 'missing'})")
    if report["skip_reason"]:
        report["false_or_invalid_fields"].append("skip_reason")
        report["reasons"].append("skip_reason is set")
    report["listable"] = (
        report["manifest_exists"]
        and report["manifest_valid_json"]
        and report["manifest_is_object"]
        and report["dispatch_slug_value"] == "food-line"
        and (not report["edition_date_value"] or report["edition_date_value"] == edition_date)
        and report["public_rendered"] is True
        and report["source_freshness_status"] != ""
        and report["freshness_window_days"] is not None
        and int(report["freshness_window_days"]) > 0
        and report["qualified_primary_count"] is not None
        and (
            (report["edition_mode"] == "no_current_update" and int(report["qualified_primary_count"]) == 0)
            or (report["edition_mode"] == "current_update" and int(report["qualified_primary_count"]) > 0)
        )
        and report["edition_mode"] in {"current_update", "no_current_update"}
        and not report["skip_reason"]
    )
    return report


def public_edition_is_listable(site_root: Path, slug: str, edition_date: str) -> bool:
    if slug == "food-line":
        return bool(_food_line_public_edition_listability_report(site_root, edition_date).get("listable"))
    if slug == CARE_LINE_DISPATCH_SLUG:
        return bool(care_line_public_edition_report(site_root, edition_date).get("listable"))
    if slug == "gaza":
        return _gaza_public_edition_is_listable(site_root, edition_date)
    if slug == "food-line":
        return bool(_food_line_public_edition_listability_report(site_root, edition_date).get("listable"))
    if slug == CARE_LINE_DISPATCH_SLUG:
        return bool(care_line_public_edition_report(site_root, edition_date).get("listable"))
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


def _food_line_public_edition_skip_warning(report: dict[str, Any]) -> str:
    edition_date = str(report.get("edition_date") or "").strip() or "unknown-date"
    manifest_path = str(report.get("manifest_path") or "").strip() or "unknown manifest"
    dispatch_slug = str(report.get("dispatch_slug_value") or report.get("dispatch_slug") or "").strip() or "missing"
    manifest_exists = "yes" if report.get("manifest_exists") else "no"
    listable = "yes" if report.get("listable") else "no"
    public_rendered = "yes" if report.get("public_rendered") else "no"
    missing_fields = ", ".join(str(item) for item in (report.get("missing_required_fields") or []) if str(item).strip()) or "none"
    false_fields = ", ".join(str(item) for item in (report.get("false_or_invalid_fields") or []) if str(item).strip()) or "none"
    reasons = "; ".join(str(item) for item in (report.get("reasons") or []) if str(item).strip()) or "no specific reason recorded"
    return (
        f"Food Line edition {edition_date} was not copied to Pages. "
        f"manifest_path={manifest_path}; manifest_exists={manifest_exists}; dispatch_slug={dispatch_slug}; "
        f"public_rendered={public_rendered}; public_edition_is_listable={listable}; "
        f"missing_required_fields={missing_fields}; false_or_invalid_fields={false_fields}; reasons={reasons}"
    )


def public_edition_manifest(site_root: Path, slug: str, edition_date: str) -> dict[str, Any]:
    manifest_path = site_root / slug / "editions" / edition_date / "edition_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pages_repo_root_for_site_root(site_root: Path, pages_repo: Path | None = None) -> Path | None:
    if pages_repo is not None:
        resolved_pages_repo = pages_repo.resolve()
        return resolved_pages_repo if resolved_pages_repo.exists() else None
    try:
        repo_root = site_root.parents[1]
    except IndexError:
        return None
    pages_repo = repo_root / "bluefern-dispatches-pages"
    return pages_repo if pages_repo.exists() else None


def _gaza_public_edition_dirs(site_root: Path, pages_repo: Path | None = None) -> list[Path]:
    editions_root = site_root / "gaza" / "editions"
    roots = [editions_root]
    pages_repo = _pages_repo_root_for_site_root(site_root, pages_repo)
    if pages_repo is not None:
        pages_editions_root = pages_repo / "gaza" / "editions"
        if pages_editions_root.resolve(strict=False) not in {root.resolve(strict=False) for root in roots}:
            roots.append(pages_editions_root)
    return [root for root in roots if root.exists()]


def _gaza_public_edition_is_listable(site_root: Path, edition_date: str, pages_repo: Path | None = None) -> bool:
    repo_root = site_root.parents[1]
    pages_repo = _pages_repo_root_for_site_root(site_root, pages_repo)
    candidate_dirs = [site_root / "gaza" / "editions" / edition_date]
    if pages_repo is not None:
        candidate_dirs.append(pages_repo / "gaza" / "editions" / edition_date)
    for edition_dir in candidate_dirs:
        if not edition_dir.exists():
            continue
        manifest_path = edition_dir / "edition_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        errors = manifest.get("errors")
        if isinstance(errors, list) and any(
            "No new source-backed Gaza developments after cross-edition dedupe" in str(item)
            for item in errors
        ):
            continue
        sources_manifest_path = edition_dir / "sources_manifest.json"
        curation_manifest_path = edition_dir / "curation_manifest.json"
        sources_payload: list[dict[str, Any]] | None = None
        curation_payload: list[dict[str, Any]] | None = None
        if sources_manifest_path.exists():
            try:
                loaded = json.loads(sources_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(loaded, list):
                sources_payload = loaded
        if curation_manifest_path.exists():
            try:
                loaded = json.loads(curation_manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(loaded, list):
                curation_payload = loaded
        source_count = len(sources_payload) if sources_payload is not None else int(manifest.get("source_count", 0) or 0)
        story_count = len(curation_payload) if curation_payload is not None else int(manifest.get("story_count", 0) or 0)
        if source_count <= 0 or story_count <= 0:
            continue
        dedupe_path = repo_root / "data" / "dispatches" / "gaza" / "editions" / edition_date / "dedupe_report.json"
        if dedupe_path.exists():
            try:
                dedupe_payload = json.loads(dedupe_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            input_count = int(dedupe_payload.get("input_candidate_count", 0) or 0)
            kept_count = int(dedupe_payload.get("kept_candidate_count", 0) or 0)
            if input_count > 0 and kept_count == 0:
                continue
        return True
    return False


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _extract_gaza_public_history_dates(text: str) -> set[str]:
    return set(GAZA_PUBLIC_HISTORY_DATE_RE.findall(text))


def _extract_gaza_audio_index_dates(text: str) -> set[str]:
    return set(GAZA_AUDIO_INDEX_DATE_RE.findall(text))


def _extract_gaza_audio_feed_dates(text: str) -> set[str]:
    return set(GAZA_AUDIO_PODCAST_DATE_RE.findall(text))


def _gaza_public_surface_date_sets(public_root: Path, *, audio_root: Path | None = None) -> dict[str, set[str]]:
    gaza_root = public_root / "gaza"
    audio_source_root = audio_root if audio_root is not None else gaza_root
    surface_paths: dict[str, tuple[Path, Any]] = {
        "gaza/archive.html": (gaza_root / "archive.html", _extract_gaza_public_history_dates),
        "gaza/rss.xml": (gaza_root / "rss.xml", _extract_gaza_public_history_dates),
        "gaza/audio/index.html": (audio_source_root / "audio" / "index.html", _extract_gaza_audio_index_dates),
        "gaza/audio/podcast.xml": (audio_source_root / "audio" / "podcast.xml", _extract_gaza_audio_feed_dates),
        "gaza/podcast.xml": (audio_source_root / "podcast.xml", _extract_gaza_audio_feed_dates),
    }
    result: dict[str, set[str]] = {}
    for surface, (path, extractor) in surface_paths.items():
        result[surface] = extractor(_read_text_if_exists(path))
    return result


def _gaza_public_surface_history_diagnostics(
    previous_root: Path,
    current_root: Path,
    *,
    current_audio_root: Path | None = None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    previous = _gaza_public_surface_date_sets(previous_root)
    current = _gaza_public_surface_date_sets(current_root, audio_root=current_audio_root)
    for surface in sorted(previous.keys() | current.keys()):
        before_dates = previous.get(surface, set())
        after_dates = current.get(surface, set())
        dropped_dates = sorted(before_dates - after_dates, reverse=True)
        added_dates = sorted(after_dates - before_dates, reverse=True)
        diagnostics.append(
            {
                "surface": surface,
                "previous_count": len(before_dates),
                "current_count": len(after_dates),
                "preserved_dates": sorted(before_dates & after_dates, reverse=True),
                "added_dates": added_dates,
                "dropped_dates": dropped_dates,
                "ok": not dropped_dates,
            }
        )
    return diagnostics


def _gaza_homepage_recent_edition_dates_from_html(html_text: str) -> list[str]:
    if not html_text:
        return []
    match = GAZA_HOME_EDITION_LIST_RE.search(html_text)
    if not match:
        return []
    block = match.group(1)
    dates: list[str] = []
    seen: set[str] = set()
    for edition_date in GAZA_PUBLIC_HISTORY_DATE_RE.findall(block):
        if edition_date in seen:
            continue
        seen.add(edition_date)
        dates.append(edition_date)
    return dates


def _gaza_homepage_recent_edition_guard(
    previous_homepage_html: str,
    current_homepage_html: str,
    recent_dates: list[str],
    *,
    allow_listing_shrink: bool = False,
) -> dict[str, Any]:
    old_dates = _gaza_homepage_recent_edition_dates_from_html(previous_homepage_html)
    new_dates = _gaza_homepage_recent_edition_dates_from_html(current_homepage_html)
    added_dates = [date for date in new_dates if date not in old_dates]
    removed_dates = [date for date in old_dates if date not in new_dates]
    latest_expected_date = recent_dates[0] if recent_dates else (new_dates[0] if new_dates else "")
    decision = "allowed"
    reasons: list[str] = []
    if not allow_listing_shrink:
        baseline_is_healthy = len(old_dates) >= GAZA_HOME_RECENT_EDITION_MIN
        if not new_dates:
            reasons.append("homepage recent-editions list is empty")
        if baseline_is_healthy and latest_expected_date and latest_expected_date not in new_dates:
            reasons.append(f"homepage lost latest expected edition date: {latest_expected_date}")
        if baseline_is_healthy and len(new_dates) < GAZA_HOME_RECENT_EDITION_MIN:
            reasons.append(
                f"homepage recent-editions list below minimum: {len(new_dates)} < {GAZA_HOME_RECENT_EDITION_MIN}"
            )
        if len(old_dates) >= GAZA_HOME_RECENT_EDITION_LIMIT and len(new_dates) != GAZA_HOME_RECENT_EDITION_LIMIT:
            reasons.append(
                f"homepage recent-editions list no longer at configured limit: {len(new_dates)} != {GAZA_HOME_RECENT_EDITION_LIMIT}"
            )
        if baseline_is_healthy and len(removed_dates) > 1:
            reasons.append(f"homepage dropped multiple recent dates: {', '.join(removed_dates)}")
        if baseline_is_healthy and old_dates and len(old_dates) - len(new_dates) >= 3:
            reasons.append(
                f"homepage count reduction too large: previous_count={len(old_dates)} current_count={len(new_dates)}"
            )
        if reasons:
            decision = "blocked"
    else:
        decision = "allowed_by_override"
    return {
        "old_dates": old_dates,
        "new_dates": new_dates,
        "added_dates": added_dates,
        "removed_dates": removed_dates,
        "recent_edition_limit": GAZA_HOME_RECENT_EDITION_LIMIT,
        "recent_edition_minimum": GAZA_HOME_RECENT_EDITION_MIN,
        "latest_expected_date": latest_expected_date,
        "decision": decision,
        "reasons": reasons,
        "ok": decision.startswith("allowed"),
    }


def public_edition_label(site_root: Path, dispatch: DispatchConfig, edition_date: str) -> str:
    if dispatch.slug != "cascadia":
        if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
            manifest = public_edition_manifest(site_root, dispatch.slug, edition_date)
            return str(
                manifest.get("public_archive_title")
                or manifest.get("public_summary")
                or edition_date
            ).strip()
        return edition_date
    manifest = public_edition_manifest(site_root, dispatch.slug, edition_date)
    if manifest.get("coverage_label"):
        return f"{dispatch.name} - {manifest['coverage_label']}"
    if manifest.get("coverage_start") and manifest.get("coverage_end"):
        return f"{dispatch.name} - {format_coverage_label(str(manifest['coverage_start']), str(manifest['coverage_end']))}"
    return edition_date


def public_edition_subtitle(site_root: Path, dispatch: DispatchConfig, edition_date: str) -> str:
    if dispatch.slug != "cascadia":
        if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
            manifest = public_edition_manifest(site_root, dispatch.slug, edition_date)
            return str(manifest.get("public_archive_subtitle") or manifest.get("public_summary") or "").strip()
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
        f'      <li><span class="edition-date">{html.escape(date)}</span>'
        f'<a href="editions/{date}/">{html.escape(label)}</a>{actions}{subtitle_html}</li>'
    )


def discover_public_edition_dates(
    site_root: Path,
    slug: str,
    max_edition_date: str | None = None,
    pages_repo: Path | None = None,
) -> list[str]:
    editions_root = site_root / slug / "editions"
    if not editions_root.exists():
        return []
    if slug == "cascadia":
        dated: list[tuple[str, str]] = []
        for path in editions_root.iterdir():
            if not path.is_dir() or len(path.name) != 10:
                continue
            edition_date = path.name
            if max_edition_date and edition_date > max_edition_date:
                continue
            if not public_edition_is_listable(site_root, slug, edition_date):
                continue
            manifest = public_edition_manifest(site_root, slug, edition_date)
            coverage_end = str(manifest.get("coverage_end") or "").strip() or edition_date
            dated.append((edition_date, coverage_end))
        # For Cascadia, sort by weekly coverage_end (newest first), then by edition folder date.
        return [edition_date for edition_date, _ in sorted(dated, key=lambda row: (row[1], row[0]), reverse=True)]
    if slug == "gaza":
        dated: set[str] = set()
        for candidate_root in _gaza_public_edition_dirs(site_root, pages_repo):
            for path in candidate_root.iterdir():
                if path.is_dir() and len(path.name) == 10:
                    dated.add(path.name)
        return sorted(
            (
                edition_date
                for edition_date in dated
                if (not max_edition_date or edition_date <= max_edition_date)
                and _gaza_public_edition_is_listable(site_root, edition_date, pages_repo)
            ),
            reverse=True,
        )
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


def discover_edition_dirs(root: Path, slug: str) -> list[Path]:
    editions_root = root / slug / "editions"
    if not editions_root.exists():
        return []
    return sorted(
        [
            path
            for path in editions_root.iterdir()
            if path.is_dir() and len(path.name) == 10
        ],
        key=lambda item: item.name,
    )


def _copytree_if_missing(source_dir: Path, target_dir: Path, dry_run: bool, wrote: list[str]) -> bool:
    if target_dir.exists():
        return False
    if dry_run:
        wrote.append(str(target_dir))
        return True
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir)
    wrote.append(str(target_dir))
    return True


def backfill_public_editions_from_dispatch_output(
    root: Path,
    site_root: Path,
    dry_run: bool,
    wrote: list[str],
    only_dispatches: tuple[str, ...] = (),
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    backfilled: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    eligible_dispatches = list(ONLY_DISPATCH_CHOICES) if not only_dispatches else list(only_dispatches)
    for slug in eligible_dispatches:
        source_dirs = discover_edition_dirs(root / "output" / "dispatches", slug)
        for source_dir in source_dirs:
            edition_date = source_dir.name
            target_dir = site_root / slug / "editions" / edition_date
            if target_dir.exists():
                continue
            index_path = source_dir / "index.html"
            manifest_path = source_dir / "edition_manifest.json"
            sources_path = source_dir / "sources_manifest.json"
            curation_path = source_dir / "curation_manifest.json"
            if not (index_path.exists() and manifest_path.exists() and sources_path.exists() and curation_path.exists()):
                skipped.append(
                    {
                        "dispatch": slug,
                        "edition_date": edition_date,
                        "reason": "missing_required_public_artifacts_in_output_dispatches",
                        "source": str(source_dir),
                    }
                )
                continue
            prose_violations = html_contains_public_prose_violations(index_path.read_text(encoding="utf-8"))
            if prose_violations:
                skipped.append(
                    {
                        "dispatch": slug,
                        "edition_date": edition_date,
                        "reason": "public_prose_quality_violation",
                        "source": str(source_dir),
                    }
                )
                continue
            if not _copytree_if_missing(source_dir, target_dir, dry_run=dry_run, wrote=wrote):
                continue
            if public_edition_is_listable(site_root, slug, edition_date):
                backfilled.append(
                    {
                        "dispatch": slug,
                        "edition_date": edition_date,
                        "source": str(source_dir),
                        "target": str(target_dir),
                    }
                )
                continue
            skipped.append(
                {
                    "dispatch": slug,
                    "edition_date": edition_date,
                    "reason": "non_publishable_or_failed_listability_rules",
                    "source": str(source_dir),
                }
            )
            if not dry_run and target_dir.exists():
                shutil.rmtree(target_dir)
    return backfilled, skipped


def _edition_has_required_public_artifacts(edition_dir: Path) -> bool:
    required = ["index.html", "edition_manifest.json", "sources_manifest.json", "curation_manifest.json"]
    return all((edition_dir / name).exists() for name in required)


def reconcile_gaza_public_editions(
    root: Path,
    site_root: Path,
    dry_run: bool,
    wrote: list[str],
    pages_repo: Path | None = None,
) -> dict[str, list[dict[str, str]]]:
    discovered: list[dict[str, str]] = []
    backfilled: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    editions_by_date: dict[str, list[tuple[str, Path]]] = {}

    candidates = [
        ("output_dispatches", root / "output" / "dispatches" / "gaza" / "editions"),
        ("output_site", site_root / "gaza" / "editions"),
    ]
    if pages_repo is not None:
        candidates.append(("pages_repo", pages_repo / "gaza" / "editions"))

    for source_name, base in candidates:
        if not base.exists():
            continue
        for edition_dir in sorted([path for path in base.iterdir() if path.is_dir() and len(path.name) == 10], key=lambda item: item.name):
            date = edition_dir.name
            discovered.append({"edition_date": date, "source": source_name, "path": str(edition_dir)})
            editions_by_date.setdefault(date, []).append((source_name, edition_dir))

    for edition_date in sorted(editions_by_date.keys()):
        target_dir = site_root / "gaza" / "editions" / edition_date
        if target_dir.exists():
            continue
        chosen: tuple[str, Path] | None = None
        for source_name in ("output_dispatches", "output_site", "pages_repo"):
            for candidate_source, candidate_dir in editions_by_date[edition_date]:
                if candidate_source != source_name:
                    continue
                if not _edition_has_required_public_artifacts(candidate_dir):
                    continue
                chosen = (candidate_source, candidate_dir)
                break
            if chosen:
                break
        if not chosen:
            skipped.append(
                {
                    "edition_date": edition_date,
                    "reason": "missing_required_public_artifacts",
                }
            )
            continue
        source_name, source_dir = chosen
        _copytree_if_missing(source_dir, target_dir, dry_run=dry_run, wrote=wrote)
        if public_edition_is_listable(site_root, "gaza", edition_date):
            backfilled.append(
                {
                    "edition_date": edition_date,
                    "source": source_name,
                    "source_path": str(source_dir),
                    "target_path": str(target_dir),
                }
            )
        else:
            skipped.append(
                {
                    "edition_date": edition_date,
                    "reason": "non_publishable_or_failed_listability_rules",
                }
            )
            if not dry_run and target_dir.exists():
                shutil.rmtree(target_dir)

    archive_entries = [
        {"edition_date": date}
        for date in discover_public_edition_dates(site_root, "gaza", pages_repo=pages_repo)
    ]
    return {
        "discovered": discovered,
        "backfilled": backfilled,
        "skipped": skipped,
        "archive_entries": archive_entries,
    }


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
        signal_pack_note += (
            '\n    <div class="dispatch-subresource">'
            '<p><a href="/cascadia/detention-watch/">Cascadia Detention Watch</a></p>'
            '<p class="edition-date">Tracking immigration detention issues connected to Washington, Oregon, and Idaho, beginning with the Tacoma facility.</p>'
            '<p><a href="/cascadia/detention-watch/">Open Detention Watch</a></p>'
            '<p><a href="/cascadia/detention-watch/editions/2026-05-26/">Open the May 26, 2026 starting record</a></p>'
            "</div>"
        )
    description = (
        CASCADIA_PUBLIC_DESCRIPTION
        if dispatch.slug == "cascadia"
        else AMERICAN_PRESSURE_PUBLIC_DESCRIPTION
        if dispatch.slug == "american-pressure"
        else CARE_LINE_PUBLIC_DESCRIPTION
        if dispatch.slug == CARE_LINE_DISPATCH_SLUG
        else "Structured briefings compiled from traceable source records."
    )
    site_root = site_root or Path("output") / "site"
    recent = "\n".join(
        render_edition_list_item(site_root, dispatch, date)
        for date in edition_dates[:GAZA_HOME_RECENT_EDITION_LIMIT]
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
    elif dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        map_link = '\n    <p>No map is published for Care Line yet. Future maps will show where current source-backed healthcare-access pressure signals were found. Areas without markers should not be read as places without healthcare strain.</p>'
    latest_link = f'<p><a href="editions/{latest}/">Read the latest briefing</a></p>' if latest else "<p>No public edition is currently listed.</p>"
    gaza_audio_link = ""
    if dispatch.slug == "gaza" and (site_root / "gaza" / "audio" / "index.html").exists():
        gaza_audio_link = '\n    <p><a href="/gaza/audio/index.html">Gaza audio and transcript archive</a></p>'
    cascadia_intro = ""
    if dispatch.slug == "cascadia":
        cascadia_intro = "<p>A weekly source-backed systems briefing for Washington, Oregon, and Idaho.</p>"
    care_line_intro = ""
    care_line_at_a_glance = ""
    care_line_archive_link = ""
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        latest_summary = public_edition_subtitle(site_root, dispatch, latest) if latest else ""
        care_line_archive_link = '<p><a href="archive.html">Browse the Care Line archive</a></p>'
        if latest_summary:
            care_line_at_a_glance = (
                "\n    <section class=\"section\">"
                "<h2>At A Glance</h2>"
                f"<p>{html.escape(latest_summary)}</p>"
                f'<p><a href="editions/{latest}/source_table.html">Source table</a> | <a href="editions/{latest}/claim_ledger.html">Claim ledger</a></p>'
                "</section>"
            )
        map_link = (
            '\n    <p>No map is published for Care Line yet. Future maps will show where current source-backed healthcare-access '
            'pressure signals were found. Areas without markers should not be read as places without healthcare strain.</p>'
        )
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)}</p>
    {cascadia_intro}
    <p class="lede">{html.escape(description)}</p>
    {care_line_archive_link}
    <h2>Latest Briefing</h2>
    {latest_link}
    {gaza_audio_link}
    <h2>Pressure Map</h2>
    {map_link}
    {dashboard_link}
    {explainer_block}
    {care_line_at_a_glance}
    {signal_pack_note}
    <h2>Recent Editions</h2>
    <ul class="edition-list">
{recent}
    </ul>
  </main>
{footer("")}"""
    return page(dispatch.name, f"{BASE_URL}/{dispatch.slug}/", "assets/site.css", body, dispatch.name)


def ensure_cascadia_source_tables(site_root: Path, dry_run: bool, wrote: list[str], warnings: list[str]) -> None:
    editions_root = site_root / "cascadia" / "editions"
    if not editions_root.exists():
        return
    try:
        from bluefern_dispatches.cascadia_render import render_cascadia_source_table_html
    except Exception as exc:  # pragma: no cover - defensive fallback
        warnings.append(f"could not import Cascadia source-table renderer: {exc}")
        return
    for edition_dir in sorted(path for path in editions_root.iterdir() if path.is_dir() and len(path.name) == 10):
        edition_date = edition_dir.name
        if not public_edition_is_listable(site_root, "cascadia", edition_date):
            continue
        sources_manifest_path = edition_dir / "sources_manifest.json"
        if not sources_manifest_path.exists():
            continue
        try:
            sources_manifest = json.loads(sources_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warnings.append(f"could not parse Cascadia sources manifest for source table: {sources_manifest_path}")
            continue
        if not isinstance(sources_manifest, list):
            continue
        manifest = public_edition_manifest(site_root, "cascadia", edition_date)
        coverage_label = str(manifest.get("coverage_label") or manifest.get("public_coverage_label") or "").strip() or None
        source_table_html = render_cascadia_source_table_html(edition_date, coverage_label, sources_manifest)
        write_text(edition_dir / "source_table.html", source_table_html, dry_run, wrote)


def render_archive(dispatch: DispatchConfig) -> str:
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="archive">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">Archive</p>
    <p class="lede">{html.escape(CARE_LINE_PUBLIC_DESCRIPTION)}</p>
    <p><a href="editions/{dispatch.edition_date}/">Read the latest briefing</a></p>
    <p><a href="editions/{dispatch.edition_date}/source_table.html">Open the source table</a> | <a href="editions/{dispatch.edition_date}/claim_ledger.html">Open the claim ledger</a></p>
    <ul class="edition-list">
      <li><span class="edition-date">{dispatch.edition_date}</span><a href="editions/{dispatch.edition_date}/">{html.escape(dispatch.name)} - {dispatch.edition_date}</a></li>
    </ul>
  </main>
{footer("")}"""
        return page(f"{dispatch.name} Archive", f"{BASE_URL}/{dispatch.slug}/archive.html", "assets/site.css", body, dispatch.name)
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
    gaza_audio_link = ""
    if dispatch.slug == "gaza" and (site_root / "gaza" / "audio" / "index.html").exists():
        gaza_audio_link = '\n    <p><a href="/gaza/audio/index.html">Gaza audio and transcript archive</a></p>'
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        items = "\n".join(
            render_edition_list_item(site_root, dispatch, date)
            for date in edition_dates
        )
        latest = edition_dates[0] if edition_dates else ""
        latest_link = f'<p><a href="editions/{latest}/">Read the latest briefing</a></p>' if latest else "<p>No public edition is currently listed.</p>"
        subtitle = public_edition_subtitle(site_root, dispatch, latest) if latest else ""
        source_link_block = f'<p><a href="editions/{latest}/source_table.html">Source table</a> | <a href="editions/{latest}/claim_ledger.html">Claim ledger</a></p>' if latest else ""
        body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="archive">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">Archive</p>
    <p class="lede">{html.escape(CARE_LINE_PUBLIC_DESCRIPTION)}</p>
    {latest_link}
    <p>{html.escape(subtitle or CARE_LINE_PUBLIC_DESCRIPTION)}</p>
    {source_link_block}
    <ul class="edition-list">
{items}
    </ul>
  </main>
{footer("")}"""
        return page(f"{dispatch.name} Archive", f"{BASE_URL}/{dispatch.slug}/archive.html", "assets/site.css", body, dispatch.name)
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
    {gaza_audio_link}
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
    extra_public_artifacts: list[str] = []
    care_line_records: list[Any] = []
    care_line_public_records: list[Any] = []
    care_line_edition_mode = ""
    care_line_diagnostics: dict[str, Any] = {}
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        care_line_records = dispatch.raw_records or [row.__dict__ if not isinstance(row, dict) else row for row in dispatch.sources]
        care_line_public_records = [row for row in care_line_records if care_line_record_is_public(row)]
        care_line_edition_mode = "current_update" if care_line_public_records else "no_current_update"
        care_line_diagnostics = care_line_review_diagnostics(care_line_records)
        approved_release = load_care_line_approved_release(site_root.parents[1], dispatch.edition_date)
        extra_public_artifacts = [
            str(public_dir / "source_table.html"),
            str(public_dir / "claim_ledger.html"),
        ]
    public_artifacts = [str(public_dir / "index.html"), str(source_manifest_public), str(curation_manifest_public), *extra_public_artifacts]
    def _record_value(record: Any, key: str, default: Any = None) -> Any:
        if isinstance(record, dict):
            return record.get(key, default)
        return getattr(record, key, default)

    claim_count = len([story for story in dispatch.stories if story.included_in_public_summary])
    qualified_public_claim_count = claim_count
    lead_signal_count = len([story for story in dispatch.stories if story.score >= 100]) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
        claim_count = len(care_line_public_records)
        qualified_public_claim_count = len(care_line_public_records)
        if approved_release is not None and care_line_release_is_limited_source(approved_release):
            source_adequacy_label = str(approved_release.proposal.get("source_adequacy_label") or "Limited-source update").strip()
            source_adequacy_status = str(approved_release.proposal.get("source_adequacy_status") or "LIMITED_SOURCE_UPDATE").strip()
            public_archive_title = source_adequacy_label or care_line_public_archive_title_for_records(care_line_records)
            public_archive_subtitle = str(approved_release.proposal.get("edition_summary") or care_line_summary_for_records(care_line_records)).strip()
            public_summary = public_archive_subtitle
        else:
            source_adequacy_label = ""
            source_adequacy_status = ""
            public_archive_title = (
                care_line_public_archive_title_for_records(care_line_records)
                if care_line_public_records
                else f"{dispatch.edition_date} — No current update"
            )
            public_archive_subtitle = care_line_summary_for_records(care_line_records) if care_line_public_records else care_line_no_current_update_summary()
            public_summary = (
                care_line_summary_for_records(care_line_records)
                if care_line_public_records
                else care_line_no_current_update_summary()
            )
    edition_manifest = {
        "dispatch_name": dispatch.name,
        "dispatch_slug": dispatch.slug,
        "public_visible": dispatch_public_visible(dispatch.slug),
        "lifecycle_state": dispatch_lifecycle_state(dispatch.slug),
        "edition_date": dispatch.edition_date,
        "generated_at": generated_at,
        "public_url": f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/",
        "local_output_path": str(public_dir),
        "local_backup_path": str(backup_dir),
        "template_version": TEMPLATE_VERSION,
        "source_count": (
            len(care_line_records)
            if dispatch.slug == CARE_LINE_DISPATCH_SLUG and care_line_public_records
            else 0
            if dispatch.slug == CARE_LINE_DISPATCH_SLUG
            else len(dispatch.sources)
        ),
        "story_count": len(care_line_public_records) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else len(dispatch.stories),
        "source_manifest_path": str(source_manifest_public),
        "curation_manifest_path": str(curation_manifest_public),
        "free_public_artifacts": public_artifacts,
        "paid_or_detail_artifacts": [],
        "claim_count": claim_count if dispatch.slug != CARE_LINE_DISPATCH_SLUG else len(care_line_public_records),
        "qualified_public_claim_count": qualified_public_claim_count if dispatch.slug != CARE_LINE_DISPATCH_SLUG else len(care_line_public_records),
        "lead_signal_count": lead_signal_count,
        "public_signal_count": len(care_line_public_records) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "public_rendered": True if dispatch.slug == CARE_LINE_DISPATCH_SLUG else True,
        "stale_current_signal_count": len([row for row in care_line_records if str(_record_value(row, "freshness_role") or "") == "stale_current_signal"]) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "resource_only_count": len([row for row in care_line_records if str(_record_value(row, "exclusion_reason") or "") == "resource_only_baseline"]) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "wrapper_candidate_count": int(care_line_diagnostics.get("wrapper_candidate_count") or 0) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "secondary_query_count": int(care_line_diagnostics.get("secondary_query_count") or 0) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "qualified_but_not_public_count": int(care_line_diagnostics.get("qualified_but_not_public_count") or 0) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "source_families": list(care_line_diagnostics.get("source_families") or []) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else [],
        "pressure_source_count_by_family": care_line_diagnostics.get("pressure_source_count_by_family") if dispatch.slug == CARE_LINE_DISPATCH_SLUG else {},
        "pressure_source_count_by_state": care_line_diagnostics.get("pressure_source_count_by_state") if dispatch.slug == CARE_LINE_DISPATCH_SLUG else {},
        "exclusion_reason_counts": care_line_diagnostics.get("exclusion_reason_counts") if dispatch.slug == CARE_LINE_DISPATCH_SLUG else {},
        "exclusion_reason_summary": care_line_diagnostics.get("exclusion_reason_summary") if dispatch.slug == CARE_LINE_DISPATCH_SLUG else "",
        "discovery_gap_check": care_line_diagnostics if dispatch.slug == CARE_LINE_DISPATCH_SLUG else {},
        "public_summary": (
            public_summary if dispatch.slug == CARE_LINE_DISPATCH_SLUG else ""
        ),
        "public_archive_title": (
            public_archive_title if dispatch.slug == CARE_LINE_DISPATCH_SLUG else ""
        ),
        "public_archive_subtitle": (
            public_archive_subtitle if dispatch.slug == CARE_LINE_DISPATCH_SLUG else ""
        ),
        "source_adequacy_status": source_adequacy_status if dispatch.slug == CARE_LINE_DISPATCH_SLUG else "",
        "source_adequacy_label": source_adequacy_label if dispatch.slug == CARE_LINE_DISPATCH_SLUG else "",
        "edition_mode": care_line_edition_mode if dispatch.slug == CARE_LINE_DISPATCH_SLUG else "",
        "reviewed_source_count": len(care_line_records) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "excluded_source_count": len([row for row in care_line_records if not care_line_record_is_public(row)]) if dispatch.slug == CARE_LINE_DISPATCH_SLUG else 0,
        "source_table_path": f"/{dispatch.slug}/editions/{dispatch.edition_date}/source_table.html" if dispatch.slug == CARE_LINE_DISPATCH_SLUG else "",
        "claim_ledger_path": f"/{dispatch.slug}/editions/{dispatch.edition_date}/claim_ledger.html" if dispatch.slug == CARE_LINE_DISPATCH_SLUG else "",
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
    if dispatch.slug == CARE_LINE_DISPATCH_SLUG and care_line_public_records:
        proposal_path = Path("data/dispatches/care-line/review/proposed-editions") / f"{dispatch.edition_date}.json"
        snapshot_path = Path("data/dispatches/care-line/review/signal-reviews") / f"{dispatch.edition_date}.json"
        if proposal_path.exists() and snapshot_path.exists():
            edition_manifest["generation_mode"] = "approved_current_review_proposal"
            edition_manifest["publication_status"] = "unpublished"
            edition_manifest["pages_status"] = "not_synced"
            edition_manifest["approved_proposal_path"] = proposal_path.as_posix()
            edition_manifest["approved_proposal_sha256"] = care_line_sha256_file(proposal_path)
            edition_manifest["review_snapshot_path"] = snapshot_path.as_posix()
            edition_manifest["review_snapshot_sha256"] = care_line_sha256_file(snapshot_path)
            initialize_care_line_public_release_status(edition_manifest)
    return edition_manifest, asdicts(dispatch.sources), asdicts(dispatch.stories)


def build_site(
    root: Path,
    dry_run: bool = False,
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    only_dispatches: tuple[str, ...] = (),
    public_max_dates: dict[str, str] | None = None,
    dispatch_seed_dates: dict[str, str] | None = None,
    pages_repo: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    site_root = root / "output" / "site"
    detail_roots = [root / "output" / name for name in DETAIL_ROOT_NAMES]
    generated_at = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []
    errors: list[str] = []
    publication_scope: dict[str, Any] = {
        "selected_dispatches": list(only_dispatches),
        "selected_event_ids": [],
        "selected_source_record_ids": [],
        "generated_output_paths": [],
        "publication_state_paths": [],
        "approved_social_card_overrides": [],
        "excluded_records": [],
        "exclusion_reasons": {},
    }
    care_line_signal_wire: dict[str, Any] | None = None
    dispatch_seed_dates = dispatch_seed_dates or {}
    care_line_seed_date = dispatch_seed_dates.get("care-line") or _latest_care_line_fixture_date(root) or datetime.now(timezone.utc).date().isoformat()
    approved_care_line_release = load_care_line_approved_release(root, care_line_seed_date)
    if approved_care_line_release is not None and care_line_release_is_limited_source(approved_care_line_release):
        care_line_signal_wire = {"ok": True, "skipped": True, "approved_release": True}
    if tuple(only_dispatches) == (CARE_LINE_DISPATCH_SLUG,):
        try:
            if care_line_signal_wire is None:
                try:
                    from bluefern_dispatches.universal_events.care_line_signal_wire import build_care_line_signal_wire_publication
                except ModuleNotFoundError as exc:
                    care_line_signal_wire = {"ok": True, "skipped": True, "warnings": [f"care-line signal wire dependency unavailable: {exc}"]}
                else:
                    care_line_signal_wire = build_care_line_signal_wire_publication(root, generated_at=generated_at)
            if not care_line_signal_wire.get("skipped"):
                manifest = care_line_signal_wire.get("publication_manifest") or {}
                publication_scope["selected_event_ids"] = list(manifest.get("event_ids") or [])
                publication_scope["selected_source_record_ids"] = list(manifest.get("selected_record_ids") or [])
                deferred = list(manifest.get("deferred_record_ids") or [])
                closed = list(manifest.get("closed_record_ids") or [])
                publication_scope["excluded_records"] = deferred + closed
                publication_scope["exclusion_reasons"] = {
                    **{record_id: "deferred or awaiting evidence review" for record_id in deferred},
                    **{record_id: "rejected or closed from public release" for record_id in closed},
                }
                publication_scope["approved_social_card_overrides"] = sorted(
                    str(event.get("event_id"))
                    for event in (care_line_signal_wire.get("events") or [])
                    if isinstance(event, dict) and event.get("event_id")
                )
                publication_scope["timestamp_decisions"] = {
                    "public_published_at": manifest.get("public_published_at"),
                    "last_updated_at": manifest.get("last_updated_at"),
                    "content_hash_fields": list(manifest.get("public_content_hash_fields") or []),
                }
        except Exception as exc:
            errors.append(f"care-line signal wire generation failed: {exc}")
            care_line_signal_wire = {"ok": False, "skipped": False, "errors": [str(exc)]}
    all_dispatches = seed_dispatches(
        root,
        generated_at,
        warnings,
        errors,
        dispatch_seed_dates=dispatch_seed_dates,
        publication_scope=publication_scope,
    )
    dispatches = all_dispatches
    if only_dispatches:
        dispatches = [dispatch for dispatch in all_dispatches if dispatch.slug in only_dispatches]
    errors.extend(validate_traceability(all_dispatches))
    errors.extend(ensure_public_detail_separation(site_root, detail_roots))
    wrote: list[str] = []
    public_urls = [f"{BASE_URL}/"]
    backfilled_public_editions: list[dict[str, str]] = []
    skipped_backfill_editions: list[dict[str, str]] = []
    gaza_reconcile: dict[str, list[dict[str, str]]] = {
        "discovered": [],
        "backfilled": [],
        "skipped": [],
        "archive_entries": [],
    }

    for asset in PUBLIC_SITE_ASSETS:
        copy_asset(root / "assets" / asset, site_root / "assets" / asset, dry_run, wrote, warnings)
    copy_asset(root / "assets" / "food-line-logo.png", site_root / "food-line" / "assets" / "food-line-logo.png", dry_run, wrote, warnings)
    copy_asset(root / "assets" / "food-line-dispatch-social.png", site_root / "food-line" / "assets" / "food-line-dispatch-social.png", dry_run, wrote, warnings)
    copy_asset(root / "assets" / "care-line-logo.png", site_root / "care-line" / "assets" / "care-line-logo.png", dry_run, wrote, warnings)
    copy_asset(root / "assets" / "care-line-dispatch-social.png", site_root / "care-line" / "assets" / "care-line-dispatch-social.png", dry_run, wrote, warnings)
    copy_asset(root / "assets" / "care-line-mark.png", site_root / "care-line" / "assets" / "care-line-mark.png", dry_run, wrote, warnings)

    try:
        from bluefern_dispatches.cascadia_detention_watch import build_detention_watch

        cascadia_dates = discover_public_edition_dates(site_root, "cascadia")
        detention_date = cascadia_dates[0] if cascadia_dates else datetime.now(timezone.utc).date().isoformat()
        detention_result = build_detention_watch(root, edition_date=detention_date, dry_run=dry_run)
        if detention_result.get("ok"):
            detention_paths = detention_result.get("paths") if isinstance(detention_result.get("paths"), dict) else {}
            for _label, path in detention_paths.items():
                if path:
                    wrote.append(str(path))
        else:
            warnings.extend([f"detention watch generation skipped: {msg}" for msg in detention_result.get("errors", [])])
    except Exception as exc:
        warnings.append(f"detention watch generation failed: {exc}")

    backfilled_public_editions, skipped_backfill_editions = backfill_public_editions_from_dispatch_output(
        root,
        site_root,
        dry_run=dry_run,
        wrote=wrote,
        only_dispatches=only_dispatches,
    )
    gaza_reconcile = reconcile_gaza_public_editions(
        root,
        site_root,
        dry_run=dry_run,
        wrote=wrote,
        pages_repo=pages_repo.resolve() if pages_repo is not None else None,
    )

    if not only_dispatches:
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
                dispatch.slug in {"cascadia", "gaza", "american-pressure", "food-line", CARE_LINE_DISPATCH_SLUG}
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
            active_filtered_candidates = [
                candidate
                for candidate in filtered_candidates
                if not str(candidate.get("story_selection_excluded_reason") or "").strip()
            ]
            if dedupe_report.get("input_candidate_count", 0) > 0 and not active_filtered_candidates:
                errors.append("No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition.")
                continue
            kept_ids = {str(item.get("source_record_id") or "") for item in active_filtered_candidates}
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
            if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
                care_line_records = dispatch.raw_records or [row.__dict__ if not isinstance(row, dict) else row for row in dispatch.sources]
                care_line_public_records = [row for row in care_line_records if care_line_record_is_public(row)]
                care_line_artifact_records = care_line_records if care_line_public_records else []
                write_text(dispatch_public_edition / "source_table.html", render_care_line_source_table_html(care_line_artifact_records, dispatch.edition_date), dry_run, wrote)
                write_text(dispatch_public_edition / "claim_ledger.html", render_care_line_claim_ledger_html(care_line_artifact_records, dispatch.edition_date), dry_run, wrote)
            if dispatch.slug in {"gaza", "american-pressure", "food-line", CARE_LINE_DISPATCH_SLUG}:
                dispatch_output_edition = root / "output" / "dispatches" / dispatch.slug / "editions" / dispatch.edition_date
                write_text(dispatch_output_edition / "index.html", edition_html, dry_run, wrote)
                if dispatch.slug == "american-pressure":
                    write_text(dispatch_output_edition / "edition.html", edition_html, dry_run, wrote)
                    write_text(dispatch_output_edition / "edition.md", dispatch.body_html or "", dry_run, wrote)
                if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
                    care_line_records = dispatch.raw_records or [row.__dict__ if not isinstance(row, dict) else row for row in dispatch.sources]
                    care_line_public_records = [row for row in care_line_records if care_line_record_is_public(row)]
                    care_line_artifact_records = care_line_records if care_line_public_records else []
                    write_text(dispatch_output_edition / "source_table.html", render_care_line_source_table_html(care_line_artifact_records, dispatch.edition_date), dry_run, wrote)
                    write_text(dispatch_output_edition / "claim_ledger.html", render_care_line_claim_ledger_html(care_line_artifact_records, dispatch.edition_date), dry_run, wrote)
                write_text(dispatch_output_edition / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
                write_text(dispatch_output_edition / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
                write_text(dispatch_output_edition / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "index.html", edition_html, dry_run, wrote)
            write_text(backup_dir / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
                care_line_records = dispatch.raw_records or [row.__dict__ if not isinstance(row, dict) else row for row in dispatch.sources]
                care_line_public_records = [row for row in care_line_records if care_line_record_is_public(row)]
                care_line_artifact_records = care_line_records if care_line_public_records else []
                write_text(backup_dir / "source_table.html", render_care_line_source_table_html(care_line_artifact_records, dispatch.edition_date), dry_run, wrote)
                write_text(backup_dir / "claim_ledger.html", render_care_line_claim_ledger_html(care_line_artifact_records, dispatch.edition_date), dry_run, wrote)
            write_text(backup_dir / "run_manifest.json", json.dumps({"generated_at": generated_at, "dry_run": dry_run, "warnings": warnings, "errors": errors}, indent=2), dry_run, wrote)
        if dispatch.slug in {"gaza", "cascadia", "american-pressure", "food-line", CARE_LINE_DISPATCH_SLUG}:
            if dispatch.slug == "cascadia":
                remove_unlistable_public_cascadia_editions(site_root, dry_run, wrote)
                ensure_cascadia_source_tables(site_root, dry_run, wrote, warnings)
            if dispatch.slug == CARE_LINE_DISPATCH_SLUG:
                report = care_line_public_edition_report(site_root, dispatch.edition_date)
                if not report.get("listable"):
                    warnings.append(
                        f"care-line edition {dispatch.edition_date} did not meet listability checks: "
                        f"{'; '.join(str(item) for item in report.get('reasons') or []) or 'no specific reason recorded'}"
                    )
            explicit_gaza_history = (
                dispatch.slug == "gaza"
                and pages_repo is not None
                and (pages_repo / "gaza" / "editions").exists()
            )
            gaza_public_history_root = pages_repo if explicit_gaza_history else site_root
            gaza_public_history_pages_repo = None if explicit_gaza_history else pages_repo
            edition_dates = discover_public_edition_dates(
                gaza_public_history_root,
                dispatch.slug,
                max_edition_date=max_public_date,
                pages_repo=gaza_public_history_pages_repo,
            )
            if dispatch.edition_date not in edition_dates and _gaza_public_edition_is_listable(
                site_root,
                dispatch.edition_date,
                pages_repo=pages_repo,
            ):
                if not max_public_date or dispatch.edition_date <= max_public_date:
                    edition_dates = sorted([*edition_dates, dispatch.edition_date], reverse=True)
            if dispatch.slug == "american-pressure" and edition_dates:
                _refresh_american_pressure_map_route(site_root, edition_dates[0], dry_run, wrote)
            write_text(dispatch_public_root / "index.html", render_dispatch_index_for_dates(dispatch, edition_dates, site_root), dry_run, wrote)
            write_text(dispatch_public_root / "archive.html", render_archive_for_dates(dispatch, edition_dates, site_root), dry_run, wrote)
            write_text(dispatch_public_root / "rss.xml", render_rss_for_dates(dispatch, edition_dates, site_root), dry_run, wrote)
    if not only_dispatches or CARE_LINE_DISPATCH_SLUG in only_dispatches:
        try:
            if care_line_signal_wire is None:
                try:
                    from bluefern_dispatches.universal_events.care_line_signal_wire import build_care_line_signal_wire_publication
                except ModuleNotFoundError as exc:
                    care_line_signal_wire = {"ok": True, "skipped": True, "warnings": [f"care-line signal wire dependency unavailable: {exc}"]}
                else:
                    care_line_signal_wire = build_care_line_signal_wire_publication(root, generated_at=generated_at)
            if care_line_signal_wire.get("ok") and not care_line_signal_wire.get("skipped"):
                publication_scope["generated_output_paths"] = sorted(
                    str(artifact.get("path"))
                    for artifact in care_line_signal_wire.get("site_artifacts") or []
                    if isinstance(artifact, dict) and artifact.get("path")
                )
                publication_scope["publication_state_paths"] = sorted(
                    str(artifact.get("path"))
                    for artifact in care_line_signal_wire.get("publication_state_artifacts") or []
                    if isinstance(artifact, dict) and artifact.get("path")
                )
                for artifact in care_line_signal_wire.get("publication_state_artifacts") or []:
                    artifact_path = Path(str(artifact["path"]))
                    if not artifact_path.is_absolute():
                        artifact_path = root / artifact_path
                    content = artifact["content"]
                    if isinstance(content, (bytes, bytearray)):
                        write_bytes(artifact_path, bytes(content), dry_run, wrote)
                    else:
                        write_text(artifact_path, str(content), dry_run, wrote)
                for artifact in care_line_signal_wire.get("shadow_artifacts") or []:
                    artifact_path = Path(str(artifact["path"]))
                    if not artifact_path.is_absolute():
                        artifact_path = root / artifact_path
                    content = artifact["content"]
                    if isinstance(content, (bytes, bytearray)):
                        write_bytes(artifact_path, bytes(content), dry_run, wrote)
                    else:
                        write_text(artifact_path, str(content), dry_run, wrote)
                for artifact in care_line_signal_wire.get("site_artifacts") or []:
                    artifact_path = Path(str(artifact["path"]))
                    if not artifact_path.is_absolute():
                        artifact_path = root / artifact_path
                    content = artifact["content"]
                    if isinstance(content, (bytes, bytearray)):
                        write_bytes(artifact_path, bytes(content), dry_run, wrote)
                    else:
                        write_text(artifact_path, str(content), dry_run, wrote)
                public_urls.extend(str(url) for url in care_line_signal_wire.get("public_urls") or [])
        except Exception as exc:
            errors.append(f"care-line signal wire generation failed: {exc}")
    ensure_public_html_favicons(site_root, dry_run, wrote)
    return {
        "ok": not errors,
        "dry_run": dry_run,
        "would_push": False,
        "pushed": False,
        "public_urls": public_urls,
        "backup_root": str(backup_root),
        "wrote": wrote,
        "backfilled_public_editions": backfilled_public_editions,
        "skipped_backfill_editions": skipped_backfill_editions,
        "gaza_editions_discovered": gaza_reconcile.get("discovered", []),
        "gaza_editions_backfilled": gaza_reconcile.get("backfilled", []),
        "gaza_editions_skipped": gaza_reconcile.get("skipped", []),
        "gaza_archive_entries_written": gaza_reconcile.get("archive_entries", []),
        "warnings": warnings,
        "errors": errors,
        "publication_scope": publication_scope,
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
        dispatches_to_check = tuple(slug for slug in only_dispatches if slug in {"gaza", "cascadia", "american-pressure", "food-line", CARE_LINE_DISPATCH_SLUG})
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
    site_root: Path,
    only_dispatches: tuple[str, ...] = (),
    public_max_dates: dict[str, str] | None = None,
    skip_diagnostics: list[dict[str, Any]] | None = None,
) -> list[Path]:
    if not site_root.exists():
        return []
    files = []
    food_line_reported: set[str] = set()
    gaza_only_publish = tuple(only_dispatches) == ("gaza",)
    gaza_sitewide_metadata_paths = {"index.html", "dispatches/index.html"}
    for path in site_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(site_root).parts
        if gaza_only_publish:
            relative_text = path.relative_to(site_root).as_posix()
            if (not relative_parts or relative_parts[0] != "gaza") and relative_text not in gaza_sitewide_metadata_paths:
                continue
        elif only_dispatches and relative_parts and relative_parts[0] in DISPATCH_LABELS and relative_parts[0] not in only_dispatches:
            continue
        if len(relative_parts) >= 4 and relative_parts[0] in {"gaza", "cascadia", "american-pressure", "food-line", CARE_LINE_DISPATCH_SLUG} and relative_parts[1] == "editions":
            slug = relative_parts[0]
            edition_date = relative_parts[2]
            max_public_date = public_max_dates.get(slug)
            if max_public_date and edition_date > max_public_date:
                continue
            if not public_edition_is_listable(site_root, slug, edition_date):
                if slug == "food-line" and skip_diagnostics is not None and edition_date not in food_line_reported:
                    skip_diagnostics.append(_food_line_public_edition_listability_report(site_root, edition_date))
                    food_line_reported.add(edition_date)
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
    if ((not only_dispatches) or (CARE_LINE_DISPATCH_SLUG in only_dispatches)) and not planning_mode:
        if not (site_root / CARE_LINE_DISPATCH_SLUG / "archive.html").exists():
            errors.append(f"Care Line archive does not exist: {site_root / CARE_LINE_DISPATCH_SLUG / 'archive.html'}")
    if ((not only_dispatches) or ("american-pressure" in only_dispatches)) and not planning_mode:
        if not (site_root / "american-pressure" / "archive.html").exists():
            errors.append(f"American Pressure archive does not exist: {site_root / 'american-pressure' / 'archive.html'}")
    if ("food-line" in only_dispatches or "food-line" in expect_dispatches) and not planning_mode:
        if not (site_root / "food-line" / "archive.html").exists():
            errors.append(f"Food Line archive does not exist: {site_root / 'food-line' / 'archive.html'}")
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
    skip_diagnostics: list[dict[str, Any]] | None = None,
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
    gaza_only_publish = tuple(only_dispatches) == ("gaza",)
    for source in collect_public_site_files(
        site_root,
        only_dispatches=only_dispatches,
        public_max_dates=public_max_dates,
        skip_diagnostics=skip_diagnostics,
    ):
        target = pages_repo / source.relative_to(site_root)
        relative = source.relative_to(site_root).as_posix()
        if only_dispatches and relative == "index.html":
            skipped.append(f"preserved Pages root homepage during scoped publish: {target}")
            continue
        if tuple(only_dispatches) == (CARE_LINE_DISPATCH_SLUG,):
            in_signal_scope = relative in {"signals/feed.xml", "care-line/signals/feed.xml"} or relative.startswith("events/")
            if not in_signal_scope:
                skipped.append(f"out-of-scope Care Line artifact: {target}")
                continue
        if (
            tuple(only_dispatches) == (CARE_LINE_DISPATCH_SLUG,)
            and target.exists()
            and (relative.startswith("events/") or relative.startswith("signals/") or relative.startswith("care-line/signals/"))
            and source.suffix.lower() in {".html", ".xml", ".json", ".txt", ".css"}
            and source.read_text(encoding="utf-8") .replace("\r\n", "\n")
            == target.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        ):
            skipped.append(f"unchanged Care Line public artifact: {target}")
            continue
        if (
            tuple(only_dispatches) == (CARE_LINE_DISPATCH_SLUG,)
            and target.exists()
            and source.suffix.lower() not in {".html", ".xml", ".json", ".txt", ".css"}
            and source.read_bytes() == target.read_bytes()
        ):
            skipped.append(f"unchanged Care Line binary artifact: {target}")
            continue
        copied.append(str(target))
        if dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if not gaza_only_publish and tuple(only_dispatches) != (CARE_LINE_DISPATCH_SLUG,):
        cname = pages_repo / "CNAME"
        copied.append(str(cname))
        if not dry_run:
            cname.write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    return copied, skipped


def remove_non_publishable_pages_editions(site_root: Path, pages_repo: Path, dry_run: bool) -> list[dict[str, str]]:
    tracked_slugs = ("cascadia", "food-line", CARE_LINE_DISPATCH_SLUG)
    if not any((pages_repo / slug / "editions").exists() for slug in tracked_slugs):
        return []
    removed: list[dict[str, str]] = []
    for slug in tracked_slugs:
        editions_root = pages_repo / slug / "editions"
        if not editions_root.exists():
            continue
        for edition_dir in sorted(editions_root.iterdir()):
            if not edition_dir.is_dir() or len(edition_dir.name) != 10:
                continue
            source_edition_dir = site_root / slug / "editions" / edition_dir.name
            if source_edition_dir.exists():
                if public_edition_is_listable(site_root, slug, edition_dir.name):
                    continue
            elif public_edition_is_listable(pages_repo, slug, edition_dir.name):
                continue
            removed.append(
                {
                    "dispatch": slug,
                    "edition_date": edition_dir.name,
                    "path": str(edition_dir),
                    "reason": f"non_publishable_or_transitional_{slug}_edition",
                }
            )
            if not dry_run:
                shutil.rmtree(edition_dir)
    return removed


def remove_pages_editions_above_date(
    pages_repo: Path, slug: str, max_edition_date: str | None, dry_run: bool
) -> list[dict[str, str]]:
    if not max_edition_date:
        return []
    editions_root = pages_repo / slug / "editions"
    if not editions_root.exists():
        return []
    removed: list[dict[str, str]] = []
    for edition_dir in sorted(editions_root.iterdir()):
        if not edition_dir.is_dir() or len(edition_dir.name) != 10:
            continue
        if edition_dir.name <= max_edition_date:
            continue
        removed.append(
            {
                "dispatch": slug,
                "edition_date": edition_dir.name,
                "path": str(edition_dir),
                "reason": f"edition_date_above_max_public_date_{max_edition_date}",
            }
        )
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


def list_nested_duplicate_dispatch_paths(root: Path) -> list[str]:
    return [str(root / slug / slug) for slug in ONLY_DISPATCH_CHOICES if (root / slug / slug).exists()]


def remove_stale_cascadia_pages_artifacts(site_root: Path, pages_repo: Path, dry_run: bool) -> list[str]:
    removed: list[str] = []
    removed.extend(remove_pages_path(pages_repo / "cascadia" / "map", dry_run))
    source_editions_root = site_root / "cascadia" / "editions"
    if source_editions_root.exists():
        for edition_dir in sorted(source_editions_root.iterdir()):
            if not edition_dir.is_dir() or len(edition_dir.name) != 10:
                continue
            for name in ("map.html", "source_table.html", "map_data.json", "artifact_validation.json", "artifact_validation.md"):
                removed.extend(remove_pages_path(pages_repo / "cascadia" / "editions" / edition_dir.name / name, dry_run))
    return removed


def remove_nested_duplicate_dispatch_paths(root: Path, dry_run: bool) -> list[str]:
    removed: list[str] = []
    for nested_root_text in list_nested_duplicate_dispatch_paths(root):
        removed.extend(remove_pages_path(Path(nested_root_text), dry_run))
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


def pages_sync_repair_message(pages_repo: Path, pages_branch: str) -> str:
    return (
        f"pages_repo_not_synced_with_origin: reset the Pages checkout at {pages_repo} to origin/{pages_branch} "
        "before publishing. Do not run git pull --rebase blindly. Abort any active rebase if present, fetch origin, "
        f"reset the Pages checkout to origin/{pages_branch}, then rerun the publish/copy step for only the intended product/date."
    )


def _git_porcelain_paths(pages_repo: Path) -> list[str]:
    if _pages_repo_is_fake_worktree(pages_repo):
        return _lightweight_git_porcelain_paths(pages_repo)
    status = run_git(["status", "--porcelain=v1", "--untracked-files=all"], pages_repo)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or status.stdout.strip() or "git status failed")
    paths: list[str] = []
    for line in status.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def _pages_repo_active_operation_markers(pages_repo: Path) -> list[str]:
    markers: list[str] = []
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        marker_path = git_stdout(["rev-parse", "--git-path", marker], pages_repo)
        if marker_path and Path(marker_path).exists():
            markers.append(marker)
    return markers


def _pages_repo_uses_lightweight_git(pages_repo: Path) -> bool:
    git_dir = pages_repo / ".git"
    return git_dir.is_dir() and not (git_dir / "objects").exists()


def _pages_repo_is_fake_worktree(pages_repo: Path) -> bool:
    if not _pages_repo_uses_lightweight_git(pages_repo):
        return False
    toplevel = git_stdout(["rev-parse", "--show-toplevel"], pages_repo)
    if not toplevel:
        return True
    try:
        return Path(toplevel).resolve() != pages_repo.resolve()
    except OSError:
        return True


def _lightweight_git_dir(pages_repo: Path) -> Path:
    return pages_repo / ".git"


def _lightweight_git_snapshot_path(pages_repo: Path) -> Path:
    return _lightweight_git_dir(pages_repo) / "bluefern-lightweight-snapshot.json"


def _lightweight_git_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lightweight_git_snapshot_entries(pages_repo: Path) -> dict[str, str]:
    git_dir = _lightweight_git_dir(pages_repo)
    entries: dict[str, str] = {}
    for path in pages_repo.rglob("*"):
        if not path.is_file():
            continue
        if git_dir in path.parents:
            continue
        if path.name == ".keep" or path.name.startswith("."):
            continue
        entries[path.relative_to(pages_repo).as_posix()] = _lightweight_git_file_hash(path)
    return entries


def _lightweight_git_load_snapshot(pages_repo: Path) -> dict[str, str] | None:
    snapshot_path = _lightweight_git_snapshot_path(pages_repo)
    if not snapshot_path.exists():
        return None
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    snapshot: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            snapshot[key] = value
    return snapshot


def _lightweight_git_write_snapshot(pages_repo: Path, snapshot: dict[str, str]) -> None:
    snapshot_path = _lightweight_git_snapshot_path(pages_repo)
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True, indent=2), encoding="utf-8")


def _lightweight_git_record_snapshot_if_missing(pages_repo: Path) -> None:
    if _lightweight_git_snapshot_path(pages_repo).exists():
        return
    _lightweight_git_write_snapshot(pages_repo, _lightweight_git_snapshot_entries(pages_repo))


def _lightweight_git_head_ref(pages_repo: Path) -> str | None:
    head_path = _lightweight_git_dir(pages_repo) / "HEAD"
    if not head_path.exists():
        return None
    head_text = head_path.read_text(encoding="utf-8").strip()
    if head_text.startswith("ref: "):
        return head_text.removeprefix("ref: ").strip()
    return None


def _lightweight_git_current_branch(pages_repo: Path) -> str | None:
    head_ref = _lightweight_git_head_ref(pages_repo)
    if not head_ref:
        return None
    prefix = "refs/heads/"
    if head_ref.startswith(prefix):
        return head_ref.removeprefix(prefix)
    return None


def _lightweight_git_read_ref(pages_repo: Path, ref: str) -> str | None:
    ref_path = _lightweight_git_dir(pages_repo) / ref
    if not ref_path.exists():
        return None
    return ref_path.read_text(encoding="utf-8").strip() or None


def _lightweight_git_write_ref(pages_repo: Path, ref: str, value: str) -> None:
    ref_path = _lightweight_git_dir(pages_repo) / ref
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(f"{value}\n", encoding="utf-8")


def _lightweight_git_set_branch(pages_repo: Path, branch: str) -> bool:
    git_dir = _lightweight_git_dir(pages_repo)
    current_branch = _lightweight_git_current_branch(pages_repo)
    current_ref = f"refs/heads/{current_branch}" if current_branch else None
    target_ref = f"refs/heads/{branch}"
    if current_ref and not (_lightweight_git_dir(pages_repo) / target_ref).exists():
        current_value = _lightweight_git_read_ref(pages_repo, current_ref) or "fake-main-commit"
        _lightweight_git_write_ref(pages_repo, target_ref, current_value)
    elif not (_lightweight_git_dir(pages_repo) / target_ref).exists():
        _lightweight_git_write_ref(pages_repo, target_ref, "fake-main-commit")
    (git_dir / "HEAD").write_text(f"ref: {target_ref}\n", encoding="utf-8")
    return current_branch != branch


def _lightweight_git_porcelain_paths(pages_repo: Path) -> list[str]:
    baseline = _lightweight_git_load_snapshot(pages_repo)
    current = _lightweight_git_snapshot_entries(pages_repo)
    if baseline is None:
        _lightweight_git_write_snapshot(pages_repo, current)
        return []
    changed_paths = {rel_path for rel_path in set(baseline) ^ set(current)}
    for rel_path in set(baseline) & set(current):
        if baseline[rel_path] != current[rel_path]:
            changed_paths.add(rel_path)
    return sorted(changed_paths)


def _ensure_pages_branch_lightweight(pages_repo: Path, pages_branch: str, dry_run: bool) -> dict[str, Any]:
    current_branch = _lightweight_git_current_branch(pages_repo)
    _lightweight_git_record_snapshot_if_missing(pages_repo)
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
    result["checked_out_branch"] = pages_branch
    result["created_pages_branch"] = _lightweight_git_set_branch(pages_repo, pages_branch)
    return result


def _pages_repo_sync_relation(pages_repo: Path, pages_branch: str) -> str:
    local = git_stdout(["rev-parse", "HEAD"], pages_repo)
    remote = git_stdout(["rev-parse", f"origin/{pages_branch}"], pages_repo)
    if not local or not remote:
        return "unknown"
    if local == remote:
        return "synced"
    local_ancestor = run_git(["merge-base", "--is-ancestor", "HEAD", f"origin/{pages_branch}"], pages_repo)
    if local_ancestor.returncode == 0:
        return "behind"
    remote_ancestor = run_git(["merge-base", "--is-ancestor", f"origin/{pages_branch}", "HEAD"], pages_repo)
    if remote_ancestor.returncode == 0:
        return "ahead"
    return "diverged"


def ensure_pages_branch(pages_repo: Path, pages_branch: str, dry_run: bool, lightweight_git: bool = False) -> dict[str, Any]:
    current_branch = _lightweight_git_current_branch(pages_repo) if lightweight_git else git_stdout(["branch", "--show-current"], pages_repo) or None
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

    if lightweight_git:
        return _ensure_pages_branch_lightweight(pages_repo, pages_branch, dry_run)

    active_markers = _pages_repo_active_operation_markers(pages_repo)
    if active_markers:
        result["errors"].append(
            f"pages_repo_has_active_rebase_or_merge_state: active git state detected ({', '.join(active_markers)}); "
            "abort the rebase/merge/cherry-pick before publishing."
        )
        return result

    remotes = git_stdout(["remote"], pages_repo) or ""
    has_origin = "origin" in remotes.split()
    try:
        dirty_paths = _git_porcelain_paths(pages_repo)
    except RuntimeError as exc:
        result["errors"].append(str(exc))
        return result
    if dirty_paths and has_origin:
        result["errors"].append(
            f"{pages_sync_repair_message(pages_repo, pages_branch)} Uncommitted Pages worktree changes detected: {', '.join(dirty_paths[:10])}"
        )
        return result
    if has_origin:
        result["fetch_attempted"] = True
        fetch = run_git(["fetch", "origin", pages_branch], pages_repo)
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

    if remotes:
        fetch = run_git(["fetch", "origin", pages_branch], pages_repo)
        result["fetch_attempted"] = True
        result["fetched"] = result["fetched"] or fetch.returncode == 0
        if fetch.returncode != 0:
            result["warnings"].append(fetch.stderr.strip() or fetch.stdout.strip() or f"git fetch origin {pages_branch} failed")
            return result
        relation = _pages_repo_sync_relation(pages_repo, pages_branch)
        if relation != "synced":
            result["errors"].append(
                f"{pages_sync_repair_message(pages_repo, pages_branch)} local HEAD is {relation} origin/{pages_branch}."
            )
            return result

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
    if (not only_dispatches) or (CARE_LINE_DISPATCH_SLUG in only_dispatches):
        if not (pages_repo / CARE_LINE_DISPATCH_SLUG / "archive.html").exists():
            errors.append(f"Pages repo Care Line archive does not exist: {pages_repo / CARE_LINE_DISPATCH_SLUG / 'archive.html'}")
    if ("food-line" in only_dispatches) or ("food-line" in expect_dispatches):
        if not (pages_repo / "food-line" / "archive.html").exists():
            errors.append(f"Pages repo Food Line archive does not exist: {pages_repo / 'food-line' / 'archive.html'}")
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
        if "food-line" in dispatches_to_check and public_edition_is_listable(site_root, "food-line", expect_date):
            if not (pages_repo / "food-line" / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected Food Line edition missing: {expect_date}")
        if CARE_LINE_DISPATCH_SLUG in dispatches_to_check and public_edition_is_listable(site_root, CARE_LINE_DISPATCH_SLUG, expect_date):
            if not (pages_repo / CARE_LINE_DISPATCH_SLUG / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected Care Line edition missing: {expect_date}")
        if "food-line" in dispatches_to_check and not public_edition_is_listable(site_root, "food-line", expect_date):
            if (pages_repo / "food-line" / "editions" / expect_date / "index.html").exists():
                errors.append(f"unexpected Food Line edition present for skipped day: {expect_date}")
    return errors


def _git_status_changed_paths(pages_repo: Path) -> list[Path]:
    status_lines = _git_porcelain_paths(pages_repo)
    changed: list[Path] = []
    for line in status_lines:
        normalized = line.replace("\\", "/")
        if normalized.startswith(".git/"):
            continue
        changed.append(Path(normalized))
    return changed


def validate_pages_repo_copy_scope(
    pages_repo: Path,
    only_dispatches: tuple[str, ...],
    changed_paths: Sequence[str | Path] | None = None,
) -> list[str]:
    errors: list[str] = []
    pages_repo = pages_repo.resolve()
    allowed_dispatches = set(only_dispatches) if only_dispatches else set(ONLY_DISPATCH_CHOICES)
    gaza_only_publish = tuple(only_dispatches) == ("gaza",)
    gaza_sitewide_metadata_paths = {"index.html", "dispatches/index.html"}
    if changed_paths is None:
        try:
            changed_paths = _git_status_changed_paths(pages_repo)
        except RuntimeError as exc:
            return [str(exc)]
    for raw_path in changed_paths:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(pages_repo)
            except ValueError:
                candidate = Path(str(raw_path).replace("\\", "/"))
        rel_path = Path(str(candidate).replace("\\", "/"))
        top_level = rel_path.parts[0] if rel_path.parts else ""
        rel_text = rel_path.as_posix()
        if gaza_only_publish:
            if top_level == "gaza" or rel_text in gaza_sitewide_metadata_paths:
                continue
            errors.append(f"gaza_publish_scope_violation: unexpected publish changes in {rel_text}")
            continue
        if len(rel_path.parts) >= 2 and rel_path.parts[0] in DISPATCH_LABELS and rel_path.parts[1] == rel_path.parts[0]:
            errors.append(f"nested duplicate dispatch path copied into the Pages repo: {rel_text}")
            continue
        if top_level in {"detail", "paid"}:
            errors.append(f"paid/detail artifacts were copied into the Pages repo: {rel_text}")
            continue
        if top_level in DISPATCH_LABELS and top_level not in allowed_dispatches:
            errors.append(f"pages_publish_unrelated_changes_detected: unexpected publish changes in {rel_text}")
    return sorted(set(errors))


def validate_pages_copy_parity(root: Path, pages_repo: Path, expect_date: str | None, only_dispatches: tuple[str, ...] = ()) -> list[str]:
    errors: list[str] = []
    site_root = root / "output" / "site"
    if not expect_date:
        return errors
    gaza_files = [
        ("gaza/index.html", "gaza edition index"),
        ("gaza/archive.html", "gaza archive"),
        ("gaza/rss.xml", "gaza rss"),
    ]
    gaza_only_publish = tuple(only_dispatches) == ("gaza",)
    if not only_dispatches or "gaza" in only_dispatches or gaza_only_publish:
        for rel_path, label in gaza_files:
            source = site_root / rel_path
            target = pages_repo / rel_path
            if source.exists() and not target.exists():
                errors.append(f"pages copy mismatch: {label} missing for {expect_date}")
                continue
            if source.exists() and target.exists() and source.read_bytes() != target.read_bytes():
                errors.append(f"pages copy mismatch: {label} differs for {expect_date}")
            if target.exists() and expect_date not in target.read_text(encoding="utf-8", errors="replace") and rel_path != "gaza/index.html":
                errors.append(f"pages copy validation failed: {rel_path} does not contain {expect_date}")
        gaza_audio_dir = site_root / "gaza" / "audio"
        if gaza_audio_dir.exists():
            for source in gaza_audio_dir.rglob("*"):
                if not source.is_file():
                    continue
                rel_path = source.relative_to(site_root)
                target = pages_repo / rel_path
                if source.exists() and not target.exists():
                    errors.append(f"pages copy mismatch: {rel_path.as_posix()} missing from Pages repo")
                    continue
                if target.exists() and source.read_bytes() != target.read_bytes():
                    errors.append(f"pages copy mismatch: {rel_path.as_posix()} differs from source output")
    return sorted(set(errors))


def validate_cascadia_pages_copy_consistency(
    pages_repo: Path,
    edition_date: str,
) -> list[str]:
    failures: list[str] = []
    cascadia_root = pages_repo / "cascadia"
    edition_dir = cascadia_root / "editions" / edition_date
    map_dir = cascadia_root / "map"
    manifest_path = edition_dir / "edition_manifest.json"
    source_table_path = edition_dir / "source_table.html"
    map_source_table_path = map_dir / "source_table.html"
    map_html_path = edition_dir / "map.html"
    if not manifest_path.exists() or not source_table_path.exists() or not map_html_path.exists() or not map_source_table_path.exists():
        return failures
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["pages copy Cascadia edition manifest is missing or invalid JSON"]
    public_story_count = int(manifest.get("public_story_count") or 0)
    source_rows = max(0, source_table_path.read_text(encoding="utf-8").count("<tr>") - 1)
    map_html_text = map_html_path.read_text(encoding="utf-8")
    m = re.search(r"Report count:\s*(\d+)", map_html_text)
    map_count = int(m.group(1)) if m else -1
    if public_story_count > 0 and public_story_count != source_rows:
        failures.append(f"pages copy count mismatch: public_story_count {public_story_count} != source_table rows {source_rows}")
    if map_count >= 0 and public_story_count != map_count:
        failures.append(f"pages copy count mismatch: public_story_count {public_story_count} != map report count {map_count}")
    if source_table_path.read_text(encoding="utf-8") != map_source_table_path.read_text(encoding="utf-8"):
        failures.append("pages copy mismatch: edition source table and map source table differ")
    forbidden = [
        "Report count: 11",
        "No reports match the current map filters",
        "Some map markers could not be displayed",
        "Open latest Cascadia map",
        "WA government",
        "ID government",
        "source_count:",
        "known_gaps:",
        "states_covered:",
    ]
    check_paths = [
        cascadia_root / "index.html",
        edition_dir / "index.html",
        source_table_path,
        map_source_table_path,
        map_html_path,
        map_dir / "index.html",
    ]
    for path in check_paths:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in content:
                failures.append(f"pages copy forbidden text in {path}: {needle}")
    return sorted(set(failures))


def list_pages_public_edition_folders(pages_repo: Path, only_dispatches: tuple[str, ...] = ()) -> set[tuple[str, str]]:
    dispatches = list(ONLY_DISPATCH_CHOICES) if not only_dispatches else list(only_dispatches)
    found: set[tuple[str, str]] = set()
    for slug in dispatches:
        for edition_dir in discover_edition_dirs(pages_repo, slug):
            found.add((slug, edition_dir.name))
    return found


def maybe_commit_pages_repo(pages_repo: Path, dry_run: bool, commit: bool, pages_branch: str, lightweight_git: bool = False) -> dict[str, Any]:
    if not commit:
        return {"would_commit": False, "committed": False, "commit_sha": None, "committed_branch": None, "message": "commit flag not set"}
    if dry_run:
        return {"would_commit": True, "committed": False, "commit_sha": None, "committed_branch": None, "message": "dry run; no commit created"}

    if lightweight_git:
        _lightweight_git_set_branch(pages_repo, pages_branch)
        _lightweight_git_write_ref(pages_repo, f"refs/heads/{pages_branch}", "fake-commit")
        return {
            "would_commit": True,
            "committed": True,
            "commit_sha": "fake-commit",
            "committed_branch": pages_branch,
            "message": PUBLISH_COMMIT_MESSAGE,
        }

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
    allow_listing_shrink: bool = False,
) -> dict[str, Any]:
    pages_repo = pages_repo.resolve()
    lightweight_git = _pages_repo_is_fake_worktree(pages_repo)
    public_max_dates: dict[str, str] = {}
    dispatch_seed_dates: dict[str, str] = {}
    ap_targeted = "american-pressure" in only_dispatches or "american-pressure" in expect_dispatches
    if expect_date and ap_targeted:
        public_max_dates["american-pressure"] = expect_date
        resolved_root = root.resolve()
        existing_dispatch_edition = resolved_root / "output" / "dispatches" / "american-pressure" / "editions" / expect_date
        if existing_dispatch_edition.exists():
            dispatch_seed_dates["american-pressure"] = expect_date
    # A publication scoped to another dispatch must not advance Care Line to a
    # newer intake fixture as a side effect of rebuilding the shared site. In
    # particular, the Phase 14A review set may intentionally remain
    # needs_evidence_review and must not become an unrelated publication gate.
    if "care-line" not in only_dispatches:
        existing_care_line_dates = discover_public_edition_dates(root / "output" / "site", CARE_LINE_DISPATCH_SLUG)
        if existing_care_line_dates:
            dispatch_seed_dates["care-line"] = max(existing_care_line_dates)
    gaza_targeted = (not only_dispatches) or ("gaza" in only_dispatches) or ("gaza" in expect_dispatches)
    explicit_gaza_dates = discover_public_edition_dates(pages_repo, "gaza") if pages_repo is not None else []
    if gaza_targeted and expect_date:
        dispatch_seed_dates["gaza"] = expect_date
    elif "gaza" not in dispatch_seed_dates and explicit_gaza_dates:
        dispatch_seed_dates["gaza"] = explicit_gaza_dates[0]
    removed_nested_duplicate_paths = remove_nested_duplicate_dispatch_paths(root / "output" / "site", dry_run)
    build = build_site(
        root,
        dry_run=dry_run,
        backup_root=backup_root,
        only_dispatches=only_dispatches,
        public_max_dates=public_max_dates,
        dispatch_seed_dates=dispatch_seed_dates,
        pages_repo=pages_repo,
    )
    root = root.resolve()
    site_root = root / "output" / "site"
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
    gaza_homepage_guard: dict[str, Any] = {
        "old_dates": [],
        "new_dates": [],
        "added_dates": [],
        "removed_dates": [],
        "recent_edition_limit": GAZA_HOME_RECENT_EDITION_LIMIT,
        "recent_edition_minimum": GAZA_HOME_RECENT_EDITION_MIN,
        "latest_expected_date": "",
        "decision": "not_checked",
        "reasons": [],
        "ok": True,
    }
    gaza_history_diagnostics: list[dict[str, Any]] = []
    gaza_surface_error_messages: list[str] = []
    gaza_scope_selected = (not only_dispatches) or ("gaza" in only_dispatches)
    if gaza_scope_selected:
        gaza_audio_root = None
        site_gaza_audio_root = site_root / "gaza" / "audio"
        pages_gaza_audio_root = pages_repo / "gaza" / "audio"
        if not site_gaza_audio_root.exists() and pages_gaza_audio_root.exists():
            gaza_audio_root = pages_repo / "gaza"
        gaza_homepage_guard = _gaza_homepage_recent_edition_guard(
            _read_text_if_exists(pages_repo / "gaza" / "index.html"),
            _read_text_if_exists(site_root / "gaza" / "index.html"),
            discover_public_edition_dates(site_root, "gaza", pages_repo=pages_repo),
            allow_listing_shrink=allow_listing_shrink,
        )
        if not gaza_homepage_guard["ok"]:
            guard_reasons = "; ".join(str(item) for item in gaza_homepage_guard.get("reasons") or []) or "no specific reason recorded"
            errors.append(f"gaza homepage recent-editions guard blocked publish: {guard_reasons}")
        gaza_history_diagnostics = _gaza_public_surface_history_diagnostics(pages_repo, site_root, current_audio_root=gaza_audio_root)
        for report in gaza_history_diagnostics:
            if report["dropped_dates"] and not allow_listing_shrink:
                surface = str(report.get("surface") or "gaza surface")
                dropped = ", ".join(str(item) for item in report["dropped_dates"])
                previous_count = int(report.get("previous_count") or 0)
                current_count = int(report.get("current_count") or 0)
                gaza_surface_error_messages.append(
                    f"gaza public history shrink detected for {surface}: previous_count={previous_count}; current_count={current_count}; dropped_dates={dropped}"
                )
        errors.extend(gaza_surface_error_messages)
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
        branch_result = ensure_pages_branch(pages_repo, pages_branch, dry_run=dry_run, lightweight_git=lightweight_git)
        errors.extend(branch_result["errors"])
        warnings.extend(branch_result["warnings"])
    would_copy = not errors
    copied: list[str] = []
    skipped: list[str] = []
    skip_diagnostics: list[dict[str, Any]] = []
    removed_non_publishable: list[dict[str, str]] = []
    removed_stale_artifacts: list[str] = []
    nested_duplicate_paths = list_nested_duplicate_dispatch_paths(pages_repo)
    commit_result = {"would_commit": bool(commit), "committed": False, "commit_sha": None, "committed_branch": None, "message": "not attempted"}
    preserved_pages_editions: list[dict[str, str]] = []
    pages_editions_before = list_pages_public_edition_folders(pages_repo, only_dispatches=only_dispatches)
    backfilled_build_editions = [dict(item) for item in build.get("backfilled_public_editions", [])]

    if not errors:
        if not only_dispatches or "cascadia" in only_dispatches or "food-line" in only_dispatches:
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
        if (not only_dispatches) or ("cascadia" in only_dispatches):
            removed_stale_artifacts = remove_stale_cascadia_pages_artifacts(site_root, pages_repo, dry_run=dry_run)
        copied, skipped = copy_public_site_to_pages(
            site_root,
            pages_repo,
            dry_run=dry_run,
            only_dispatches=only_dispatches,
            public_max_dates=public_max_dates,
            skip_diagnostics=skip_diagnostics,
        )
        warnings.extend(_food_line_public_edition_skip_warning(report) for report in skip_diagnostics)
        errors.extend(validate_pages_repo_copy_scope(pages_repo, only_dispatches, changed_paths=copied))
        if not dry_run:
            errors.extend(validate_pages_copy_parity(root, pages_repo, expect_date, only_dispatches=only_dispatches))
            if expect_date and ((not only_dispatches) or ("cascadia" in only_dispatches)):
                errors.extend(validate_cascadia_pages_copy_consistency(pages_repo, expect_date))
            errors.extend(
                validate_pages_repo_after_copy(
                    pages_repo,
                    site_root,
                    expect_date,
                    expect_dispatches=expect_dispatches,
                    only_dispatches=only_dispatches,
                )
            )
        if not errors:
            if not dry_run:
                removed_nested_duplicate_paths = remove_nested_duplicate_dispatch_paths(pages_repo, dry_run=False)
            commit_result = maybe_commit_pages_repo(
                pages_repo,
                dry_run=dry_run,
                commit=commit,
                pages_branch=pages_branch,
                lightweight_git=lightweight_git,
            )
            if commit and not commit_result["committed"] and commit_result["message"] not in {"dry run; no commit created", "no changes to commit"}:
                errors.append(commit_result["message"])
            pages_editions_after = list_pages_public_edition_folders(pages_repo, only_dispatches=only_dispatches)
            removed_keys = {(item.get("dispatch", ""), item.get("edition_date", "")) for item in removed_non_publishable}
            for slug, edition_date in sorted(pages_editions_before):
                if (slug, edition_date) in removed_keys:
                    continue
                if (slug, edition_date) in pages_editions_after:
                    preserved_pages_editions.append(
                        {
                            "dispatch": slug,
                            "edition_date": edition_date,
                            "reason": "preexisting_pages_public_edition_preserved",
                        }
                    )

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
        "public_pages_editions_preserved": preserved_pages_editions,
        "public_editions_backfilled": backfilled_build_editions,
        "gaza_editions_discovered": build.get("gaza_editions_discovered", []),
        "gaza_archive_entries_written": build.get("gaza_archive_entries_written", []),
        "gaza_editions_skipped": build.get("gaza_editions_skipped", []),
        "public_pages_editions_removed": [] if dry_run else removed_non_publishable,
        "public_pages_editions_that_would_be_removed": removed_non_publishable if dry_run else [],
        "non_publishable_pages_editions_removed": [] if dry_run else [item["path"] for item in removed_non_publishable],
        "non_publishable_pages_editions_that_would_be_removed": [item["path"] for item in removed_non_publishable] if dry_run else [],
        "stale_pages_artifacts_removed": [] if dry_run else removed_stale_artifacts,
        "stale_pages_artifacts_that_would_be_removed": removed_stale_artifacts if dry_run else [],
        "nested_duplicate_dispatch_paths_removed": [] if dry_run else removed_nested_duplicate_paths,
        "nested_duplicate_dispatch_paths_that_would_be_removed": nested_duplicate_paths if dry_run else [],
        "files_that_would_be_skipped": skipped,
        "food_line_public_edition_skip_diagnostics": skip_diagnostics,
        "would_copy": would_copy,
        "copied": bool(copied) and not dry_run,
        "would_commit": bool(commit),
        "committed": commit_result["committed"],
        "commit_sha": commit_result["commit_sha"],
        "local_pages_copy_ok": bool(copied) and not errors,
        "pages_commit_ok": commit_result["committed"],
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
        "gaza_public_surface_history": gaza_history_diagnostics,
        "gaza_homepage_recent_edition_guard": gaza_homepage_guard,
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
        help="Build/copy only selected dispatches (repeat or comma-separate): gaza, cascadia, american-pressure, food-line.",
    )
    parser.add_argument("--commit", action="store_true", help="Commit copied Pages repo changes locally.")
    parser.add_argument("--no-push", action="store_true", help="Explicitly skip push. Push is always skipped by this publisher.")
    parser.add_argument(
        "--allow-listing-shrink",
        action="store_true",
        help="Allow Gaza public history surfaces to lose dates when intentionally pruning historical public listings.",
    )
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
            allow_listing_shrink=args.allow_listing_shrink,
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
