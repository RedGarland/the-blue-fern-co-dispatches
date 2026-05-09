from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.cascadia_weekly import format_coverage_label


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
EXPECT_DISPATCH_CHOICES = ("gaza", "cascadia", "all")
ALL_EXPECT_DISPATCHES = ("gaza", "cascadia")
DISPATCH_LABELS = {"gaza": "Gaza", "cascadia": "Cascadia"}


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


def seed_dispatches(now: str) -> list[DispatchConfig]:
    # Use explicit seed edition date if provided via env, otherwise default
    # to the current run date (the 'now' param is an ISO timestamp).
    env_date = os.getenv("BLUEFERN_SEED_EDITION_DATE")
    if env_date and env_date.strip():
        date = env_date.strip()
    else:
        # 'now' is an ISO timestamp from build_site; extract YYYY-MM-DD
        date = (now or "").split("T")[0] or "2026-05-03"
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
        return files
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
    nav = '<a href="/gaza/">Gaza</a><a href="/cascadia/">Cascadia</a>'
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
    description = CASCADIA_PUBLIC_DESCRIPTION if dispatch.slug == "cascadia" else "Structured briefings compiled from traceable source records."
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
    if manifest.get("briefing_type") == "weekly":
        return True
    return all(manifest.get(field) for field in ("coverage_start", "coverage_end", "week_label"))


def public_edition_is_listable(site_root: Path, slug: str, edition_date: str) -> bool:
    if slug != "cascadia":
        return True
    manifest_path = site_root / slug / "editions" / edition_date / "edition_manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return is_weekly_cascadia_manifest(manifest, edition_date)


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
    subtitle = str(manifest.get("public_archive_subtitle") or "").strip()
    if subtitle:
        return subtitle
    if manifest.get("public_story_count") == 0:
        return "0 stories | No qualifying public signals identified"
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
    return (
        f'      <li><span class="edition-date">{html.escape(label)}</span>'
        f'<a href="editions/{date}/">{html.escape(dispatch.name)} - {html.escape(label)}</a>{subtitle_html}</li>'
    )


def discover_public_edition_dates(site_root: Path, slug: str) -> list[str]:
    editions_root = site_root / slug / "editions"
    if not editions_root.exists():
        return []
    return sorted(
        (
            path.name
            for path in editions_root.iterdir()
            if path.is_dir() and len(path.name) == 10 and public_edition_is_listable(site_root, slug, path.name)
        ),
        reverse=True,
    )


def render_dispatch_index_for_dates(dispatch: DispatchConfig, edition_dates: list[str], site_root: Path | None = None) -> str:
    latest = edition_dates[0] if edition_dates else dispatch.edition_date
    signal_pack_note = ""
    if dispatch.slug == "cascadia":
        signal_pack_note = "\n    <p><strong>Cascadia Signal Pack</strong><br>Detailed downloadable records are being prepared for future release.</p>"
    description = CASCADIA_PUBLIC_DESCRIPTION if dispatch.slug == "cascadia" else "Structured briefings compiled from traceable source records."
    site_root = site_root or Path("output") / "site"
    recent = "\n".join(
        render_edition_list_item(site_root, dispatch, date)
        for date in edition_dates[:10]
    )
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)} archive</p>
    <p class="lede">{html.escape(description)}</p>
    <p><a href="editions/{latest}/">Read the latest briefing</a></p>
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
        "detail_artifacts_publicly_exposed": False,
        "warnings": warnings,
        "errors": errors,
    }
    return edition_manifest, asdicts(dispatch.sources), asdicts(dispatch.stories)


def build_site(root: Path, dry_run: bool = False, backup_root: Path = DEFAULT_BACKUP_ROOT) -> dict[str, Any]:
    root = root.resolve()
    site_root = root / "output" / "site"
    detail_roots = [root / "output" / name for name in DETAIL_ROOT_NAMES]
    generated_at = datetime.now(timezone.utc).isoformat()
    dispatches = seed_dispatches(generated_at)
    warnings: list[str] = []
    errors = validate_traceability(dispatches)
    errors.extend(ensure_public_detail_separation(site_root, detail_roots))
    wrote: list[str] = []
    public_urls = [f"{BASE_URL}/"]

    for asset in PUBLIC_SITE_ASSETS:
        copy_asset(root / "assets" / asset, site_root / "assets" / asset, dry_run, wrote, warnings)

    write_text(site_root / "index.html", render_root(dispatches), dry_run, wrote)
    for dispatch in dispatches:
        public_urls.append(f"{BASE_URL}/{dispatch.slug}/")
        public_urls.append(f"{BASE_URL}/{dispatch.slug}/editions/{dispatch.edition_date}/")
        dispatch_public_root = site_root / dispatch.slug
        dispatch_public_edition = dispatch_public_root / "editions" / dispatch.edition_date
        backup_dir = backup_root / dispatch.slug / dispatch.edition_date
        for asset in ["site.css", dispatch.logo, "bluefern.png"]:
            copy_asset(root / "assets" / asset, dispatch_public_root / "assets" / asset, dry_run, wrote, warnings)
        write_text(dispatch_public_root / "index.html", render_dispatch_index(dispatch), dry_run, wrote)
        write_text(dispatch_public_root / "archive.html", render_archive(dispatch), dry_run, wrote)
        write_text(dispatch_public_root / "rss.xml", render_rss(dispatch), dry_run, wrote)
        copied_real_edition = dispatch.slug in {"cascadia", "gaza"} and copy_real_dispatch_edition(root, dispatch.slug, dispatch.edition_date, site_root, dry_run, wrote)
        if not copied_real_edition:
            edition_html = render_edition(dispatch)
            write_text(dispatch_public_edition / "index.html", edition_html, dry_run, wrote)
            edition_manifest, sources_manifest, curation_manifest = build_manifests(dispatch, site_root, backup_root, generated_at, warnings, errors)
            write_text(dispatch_public_edition / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
            write_text(dispatch_public_edition / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
            write_text(dispatch_public_edition / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            if dispatch.slug == "gaza":
                dispatch_output_edition = root / "output" / "dispatches" / dispatch.slug / "editions" / dispatch.edition_date
                write_text(dispatch_output_edition / "index.html", edition_html, dry_run, wrote)
                write_text(dispatch_output_edition / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
                write_text(dispatch_output_edition / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
                write_text(dispatch_output_edition / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "index.html", edition_html, dry_run, wrote)
            write_text(backup_dir / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
            write_text(backup_dir / "run_manifest.json", json.dumps({"generated_at": generated_at, "dry_run": dry_run, "warnings": warnings, "errors": errors}, indent=2), dry_run, wrote)
        if dispatch.slug in {"gaza", "cascadia"}:
            edition_dates = discover_public_edition_dates(site_root, dispatch.slug)
            if dispatch.edition_date not in edition_dates and public_edition_is_listable(site_root, dispatch.slug, dispatch.edition_date):
                edition_dates = sorted([*edition_dates, dispatch.edition_date], reverse=True)
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


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def collect_public_site_files(site_root: Path) -> list[Path]:
    if not site_root.exists():
        return []
    files = []
    for path in site_root.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(site_root).parts
        if len(relative_parts) >= 4 and relative_parts[0] == "cascadia" and relative_parts[1] == "editions":
            edition_date = relative_parts[2]
            if not public_edition_is_listable(site_root, "cascadia", edition_date):
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
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
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
    if not (site_root / "index.html").exists():
        errors.append(f"public site index does not exist: {site_root / 'index.html'}")
    dispatches_to_check = expect_dispatches or ALL_EXPECT_DISPATCHES
    if not (site_root / "gaza" / "archive.html").exists():
        errors.append(f"Gaza archive does not exist: {site_root / 'gaza' / 'archive.html'}")
    elif expect_date and "gaza" in dispatches_to_check and (site_root / "gaza" / "editions" / expect_date).exists():
        archive_text = (site_root / "gaza" / "archive.html").read_text(encoding="utf-8")
        if expect_date not in archive_text:
            errors.append(f"output/site/gaza/archive.html does not contain expected date {expect_date}")
    detail_files = public_site_contains_detail_artifacts(site_root)
    if detail_files:
        errors.append(f"paid/detail artifacts are present in public output: {', '.join(detail_files)}")
    blocked_public_text = public_site_contains_blocked_public_text(site_root)
    if blocked_public_text:
        errors.append(f"blocked private artifact names are present in public output: {', '.join(blocked_public_text)}")
    if expect_date:
        for dispatch_slug in dispatches_to_check:
            source_edition = site_root / dispatch_slug / "editions" / expect_date
            if expect_dispatches and not source_edition.exists():
                label = DISPATCH_LABELS.get(dispatch_slug, dispatch_slug)
                errors.append(f"expected {label} edition missing: {expect_date}")
            elif source_edition.exists() and not (source_edition / "index.html").exists():
                label = DISPATCH_LABELS.get(dispatch_slug, dispatch_slug)
                errors.append(f"expected {label} edition exists but index is missing: {source_edition / 'index.html'}")
    cname = pages_repo / "CNAME"
    if cname.exists() and cname.read_text(encoding="utf-8").strip() != CNAME_VALUE:
        errors.append(f"CNAME value is not correct in {cname}")
    return errors, warnings


def copy_public_site_to_pages(site_root: Path, pages_repo: Path, dry_run: bool) -> tuple[list[str], list[str]]:
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
    for source in collect_public_site_files(site_root):
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


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
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
) -> list[str]:
    errors: list[str] = []
    if not (pages_repo / ".git").exists():
        errors.append(f".git was not preserved in Pages repo: {pages_repo / '.git'}")
    cname = pages_repo / "CNAME"
    if not cname.exists() or cname.read_text(encoding="utf-8").strip() != CNAME_VALUE:
        errors.append(f"CNAME does not contain {CNAME_VALUE}")
    if not (pages_repo / "index.html").exists():
        errors.append(f"Pages repo index does not exist: {pages_repo / 'index.html'}")
    if not (pages_repo / "gaza" / "archive.html").exists():
        errors.append(f"Pages repo Gaza archive does not exist: {pages_repo / 'gaza' / 'archive.html'}")
    if (pages_repo / "detail").exists() or (pages_repo / "paid").exists():
        errors.append("paid/detail artifacts were copied into the Pages repo")
    blocked_text = public_site_contains_blocked_public_text(pages_repo)
    if blocked_text:
        errors.append(f"blocked private artifact names are present in Pages repo: {', '.join(blocked_text)}")
    dispatches_to_check = expect_dispatches or ALL_EXPECT_DISPATCHES
    if expect_date:
        archive = pages_repo / "gaza" / "archive.html"
        if "gaza" in dispatches_to_check and (site_root / "gaza" / "editions" / expect_date).exists():
            if not (pages_repo / "gaza" / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected Gaza edition missing: {expect_date}")
            elif archive.exists() and expect_date not in archive.read_text(encoding="utf-8"):
                errors.append(f"Pages repo Gaza archive does not contain expected date {expect_date}")
        if "cascadia" in dispatches_to_check and (site_root / "cascadia" / "editions" / expect_date).exists():
            if not (pages_repo / "cascadia" / "editions" / expect_date / "index.html").exists():
                errors.append(f"expected Cascadia edition missing: {expect_date}")
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
) -> dict[str, Any]:
    build = build_site(root, dry_run=dry_run, backup_root=backup_root)
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
    )
    errors.extend(validation_errors)
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
        removed_non_publishable = remove_non_publishable_pages_editions(site_root, pages_repo, dry_run=dry_run)
        copied, skipped = copy_public_site_to_pages(site_root, pages_repo, dry_run=dry_run)
        if not dry_run:
            errors.extend(validate_pages_repo_after_copy(pages_repo, site_root, expect_date, expect_dispatches=expect_dispatches))
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
    parser.add_argument("--commit", action="store_true", help="Commit copied Pages repo changes locally.")
    parser.add_argument("--no-push", action="store_true", help="Explicitly skip push. Push is always skipped by this publisher.")
    args = parser.parse_args(argv)
    try:
        expect_dispatches = normalize_expect_dispatches(tuple(args.expect_dispatch))
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
        )
    else:
        result = build_site(Path.cwd(), dry_run=args.dry_run, backup_root=Path(args.backup_root))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
