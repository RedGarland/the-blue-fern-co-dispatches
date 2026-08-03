"""Private Phase 1 editorial site preview built only from public rendered output."""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

PALETTE = {"ink": "#1E3F4F", "paper": "#EFE7DA", "muted": "#4E6B79"}
PUBLIC_STATUSES = ("Active", "Pilot", "In development", "Paused", "Archived")
STATUS_BY_SLUG = {"gaza": "Active", "food-line": "Active", "care-line": "Pilot", "cascadia": "Paused", "american-pressure": "In development", "ice-activity-and-consequences": "In development"}
LABELS = {"gaza": "Dispatches From Gaza", "food-line": "Food Line Dispatch", "care-line": "The Care Line Dispatch", "cascadia": "The Cascadia Briefing", "american-pressure": "The American Pressure Dispatch", "ice-activity-and-consequences": "ICE Activity and Consequences"}
CADENCE = {"gaza": "Daily", "food-line": "Daily", "care-line": "Pilot publication", "cascadia": "Weekly · currently paused", "american-pressure": "Weekly workflow", "ice-activity-and-consequences": "Not yet recurring"}
DESCRIPTIONS = {"gaza": "Daily source-backed reporting from Gaza.", "food-line": "Source-backed reporting on food-access pressure and household strain.", "care-line": "A pilot Signal Wire for source-backed healthcare-access pressure.", "cascadia": "Cascadia is paused. Its latest public edition is May 5, 2026; its latest substantive development was published May 3, 2026, and its public archive remains available through May 31, 2026.", "american-pressure": "A developing weekly product about pressures reshaping household life.", "ice-activity-and-consequences": "A proposed reporting area; no recurring public edition is currently established."}


@dataclass(frozen=True)
class Edition:
    slug: str
    date: str
    url: str
    headline: str
    status: str
    source_count: int | None
    publisher_count: int | None
    signal_count: int | None
    summary: str = ""
    location: str = ""
    item_url: str | None = None
    no_update: bool = False
    substantive: bool = False
    published_at: str | None = None

    @property
    def display_date(self) -> str:
        try:
            return date.fromisoformat(self.date).strftime("%B %d, %Y").replace(" 0", " ")
        except (AttributeError, TypeError, ValueError):
            return self.date


@dataclass(frozen=True)
class Dispatch:
    slug: str
    name: str
    status: str
    description: str
    cadence: str
    url: str | None
    archive_url: str | None
    latest: Edition | None
    public_links: tuple[tuple[str, str], ...] = ()


class _HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.level: int | None = None
        self.headings: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3"}:
            self.level = int(tag[1])

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3"}:
            self.level = None

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if clean and self.level is not None:
            self.headings.append((self.level, clean))


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _json(path: Path) -> dict:
    try:
        value = json.loads(_text(path))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _clean(value: object) -> str:
    text = str(value or "")
    replacements = {"â†’": "→", "â€”": "—", "â€“": "–", "â€œ": "“", "â€": "”", "â€™": "’", "â€¦": "…", "âœ¦": "✦", "ï¿½": ""}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
def _count(payload: dict, *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, list):
            return len(value)
    return None


def _fallback_headline(path: Path, fallback: str) -> str:
    parser = _HeadingParser()
    parser.feed(_text(path))
    ignored = {"archive", "recent editions", "source table", "claim ledger", "today’s read", "today's read", "at a glance", "source mix", "source note"}
    for level, value in parser.headings:
        if value.lower() not in ignored and level == 3:
            return _clean(value)
    return _clean(fallback)


def _edition(site_root: Path, slug: str, edition_date: str) -> Edition:
    directory = site_root / slug / "editions" / edition_date
    manifest = _json(directory / "edition_manifest.json")
    sources = _json(directory / "sources_manifest.json")
    curation = _json(directory / "curation_manifest.json")
    stories = manifest.get("stories")
    story = next((row for row in stories if isinstance(row, dict) and (row.get("title") or row.get("headline"))), {}) if isinstance(stories, list) else {}
    fallback = _fallback_headline(directory / "index.html", f"Edition for {edition_date}")
    headline = _clean(story.get("title") or story.get("headline") or manifest.get("lead_headline") or manifest.get("lead_title") or manifest.get("primary_signal_title") or fallback)
    mode = str(manifest.get("edition_mode") or "").lower()
    no_update = "no_update" in mode or "no current update" in headline.lower() or int(manifest.get("public_signal_count", manifest.get("story_count", 0)) or 0) == 0
    structured = bool(story or manifest.get("lead_headline") or manifest.get("lead_title") or manifest.get("primary_signal_title") or fallback != f"Edition for {edition_date}")
    homepage_url = {"care-line": "/care-line/", "cascadia": "/cascadia/archive.html"}.get(slug, f"/{slug}/editions/{edition_date}/")
    return Edition(slug, edition_date, homepage_url, headline, _clean(manifest.get("public_status") or manifest.get("edition_mode") or "Published").replace("_", " ").title(), _count(manifest, "source_count", "included_source_count") or _count(sources, "sources", "records"), _count(manifest, "publisher_count", "included_publisher_count"), _count(manifest, "signal_count", "story_count", "included_story_count") or _count(curation, "stories", "records"), _clean(story.get("summary") or story.get("description") or manifest.get("lead_summary") or ""), _clean(story.get("location") or story.get("geography") or ""), str(story.get("url") or story.get("source_url") or manifest.get("lead_source_canonical_url") or "") or None, no_update, structured and not no_update)


def public_editions(site_root: Path, slug: str) -> list[Edition]:
    root = site_root / slug / "editions"
    if not root.exists():
        return []
    values = sorted((item.name for item in root.iterdir() if item.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.name) and (item / "index.html").exists()), reverse=True)
    result = []
    for value in values:
        manifest = _json(root / value / "edition_manifest.json")
        if manifest.get("public_rendered") is False or manifest.get("public_visible") is False:
            continue
        result.append(_edition(site_root, slug, value))
    return result


def public_model(site_root: Path) -> tuple[list[Dispatch], list[Edition]]:
    dispatches = []
    recent = []
    for slug in STATUS_BY_SLUG:
        found = public_editions(site_root, slug)
        recent.extend(found[:3])
        links = [(label, f"/{slug}/{relative}") for label, relative in (("Audio", "audio/index.html"), ("Feed", "rss.xml"), ("Podcast", "podcast.xml"), ("Map", "map/index.html")) if (site_root / slug / relative).exists()]
        dispatches.append(Dispatch(slug, LABELS[slug], STATUS_BY_SLUG[slug], DESCRIPTIONS[slug], CADENCE[slug], f"/{slug}/" if (site_root / slug / "index.html").exists() else None, f"/{slug}/archive.html" if (site_root / slug / "archive.html").exists() else None, found[0] if found else None, tuple(links)))
    return dispatches, sorted(recent, key=lambda item: item.date, reverse=True)


def _nav(active: str = "home") -> str:
    links = [("/", "Home", "home"), ("/dispatches/", "Dispatches", "dispatches"), ("/methodology/", "Methodology", "methodology"), ("/about/", "About", "about")]
    rendered = []
    for href, label, key in links:
        current = ' class="is-active" aria-current="page"' if active == key else ""
        rendered.append(f'<a href="{href}"{current}>{label}</a>')
    return '<header class="site-header"><a class="brand" href="/"><span class="brand-kicker">The Blue Fern Co.</span><span class="brand-title">Dispatches From The Blue Fern Co.</span></a><nav aria-label="Primary">' + "".join(rendered) + '</nav></header>'
def _page(title: str, body: str, active: str = "home") -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title><link rel="stylesheet" href="/assets/site-phase1.css"></head><body>{_nav(active)}<main>{body}</main><footer class="site-footer"><div><strong>The Blue Fern Co.</strong><p>Source-backed public briefings for reading, research, and accountability.</p></div><p><a href="/methodology/">How we work</a> · <a href="/about/">About this project</a></p></footer></body></html>'


def _edition_card(item: Edition) -> str:
    meta = " · ".join(value for value in (item.location, f"{item.source_count} public sources" if item.source_count is not None else "") if value) or "Public edition"
    return f'<article class="edition-card"><p class="eyebrow">Public source headline · {html.escape(LABELS[item.slug])} · {html.escape(item.display_date)}</p><h3><a href="{item.url}">{html.escape(item.headline)}</a></h3><p class="edition-summary">{html.escape(item.summary or "Published public development")}</p><p class="edition-meta">{html.escape(meta)}</p></article>'
def _dispatch_card(item: Dispatch, compact: bool = False) -> str:
    latest = item.latest
    latest_html = f'<p class="latest-label">Latest public development</p><h3 class="latest-headline"><a href="{latest.url}">{html.escape(latest.headline)}</a></h3><p class="date-line">{html.escape(latest.display_date)}</p>' if latest else '<p class="latest-label">Current public status</p><h3 class="latest-headline">No public edition indexed</h3>'
    actions = f'<a class="button" href="{latest.url}">Read latest</a>' if latest else ""
    if item.archive_url:
        actions += f'<a class="text-link" href="{item.archive_url}">Archive</a>'
    actions += "".join(f'<a class="support-link" href="{href}">{label}</a>' for label, href in item.public_links)
    return f'<article class="dispatch-card {"dispatch-card--compact" if compact else "dispatch-card--featured"}"><div class="card-rule" aria-hidden="true"></div><p class="status">{html.escape(item.status)}</p>{latest_html}<h2>{html.escape(item.name)}</h2><p class="card-description">{html.escape(item.description)}</p><p class="cadence">{html.escape(item.cadence)}</p><div class="card-actions">{actions}</div></article>'


def _home(dispatches: list[Dispatch], recent: list[Edition]) -> str:
    active = "".join(_dispatch_card(item) for item in dispatches if item.status == "Active")
    quieter = "".join(_dispatch_card(item, True) for item in dispatches if item.status in {"Pilot", "In development"})
    paused = "".join(f'<li><span><strong>{html.escape(item.name)}</strong><small>{html.escape(item.description)}</small></span><span class="status">{item.status}</span></li>' for item in dispatches if item.status in {"Paused", "Archived"})
    developments = "".join(_edition_card(item) for item in recent if item.substantive and not item.no_update) or '<p class="empty-state">No substantive public development is indexed.</p>'
    return f'<section class="hero"><div class="hero-mark" aria-hidden="true">✦</div><p class="eyebrow">The Blue Fern Co.</p><h1>Source-backed briefings about public systems, access, pressure, and accountability.</h1><p class="lede">Read the latest published developments, then follow the public record back to the dispatches that report them.</p><p class="actions"><a class="button" href="/dispatches/">View latest dispatches</a><a class="button button--quiet" href="/about/">Explore the public record</a></p></section><section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p><h2>Latest published developments</h2></div><div class="edition-grid">{developments}</div></section><section class="section-block"><div class="section-heading"><p class="eyebrow">Reporting now</p><h2>Active dispatches</h2><p>Two ongoing products are publishing source-backed briefings for public reading.</p></div><div class="active-grid">{active}</div></section><section class="section-block section-block--quiet"><div class="section-heading"><p class="eyebrow">The wider project</p><h2>Pilot and in development</h2></div><div class="quiet-grid">{quieter}</div></section><section class="section-block section-block--quiet"><div class="section-heading"><p class="eyebrow">A pause in publication</p><h2>Paused / archived work</h2></div><ul class="paused-list">{paused}</ul></section>'


MOBILE_CSS = "@media(max-width:560px){.site-header,.site-footer,main{width:calc(100% - 1.5rem);min-width:0}.hero h1{font-size:clamp(1.8rem,9vw,3rem);max-width:100%;overflow-wrap:anywhere}.active-grid,.quiet-grid,.edition-grid,.directory-list,.closing-grid{grid-template-columns:1fr}.actions,.card-actions{align-items:stretch}.actions>* ,.card-actions>*{max-width:100%}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"
BASE_CSS = ":root{--ink:#1E3F4F;--paper:#EFE7DA;--muted:#4E6B79;--white:#fffdf8;--line:#b9aa96}*{box-sizing:border-box}html{background:var(--paper);color:var(--ink);font:16px/1.65 Georgia,'Times New Roman',serif}body{margin:0;background:linear-gradient(180deg,#f7f1e8 0,var(--paper) 34rem)}a{color:var(--ink);text-underline-offset:.18em}.site-header,.site-footer,main{width:min(1160px,calc(100% - 3rem));margin:auto;min-width:0}.site-header{display:flex;align-items:flex-end;justify-content:space-between;gap:2rem;padding:1.2rem 0 1rem;border-bottom:1px solid var(--line)}.brand{display:flex;flex-direction:column;text-decoration:none;line-height:1.1;min-width:0}.brand:before{content:'✦';color:var(--muted);font-size:1.15rem}.brand-kicker,.eyebrow,.site-header nav,.button,.status,.cadence,.edition-meta{font-family:system-ui,sans-serif}.brand-kicker,.eyebrow{font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--muted)}.brand-kicker{font-size:.68rem}.brand-title{font-size:1.12rem;font-weight:700}.site-header nav{display:flex;flex-wrap:wrap;gap:1.15rem;font-weight:700;font-size:.82rem;min-width:0}.site-header a:focus-visible{outline:3px solid var(--muted);outline-offset:4px}main{padding:clamp(2.5rem,7vw,6rem) 0}.hero{max-width:900px;padding-bottom:clamp(3rem,7vw,6rem);min-width:0}.hero-mark{font-size:1.4rem;color:var(--muted)}.eyebrow{font-size:.72rem;line-height:1.2;margin:0 0 .65rem}.hero h1{font-size:clamp(2.2rem,5.8vw,5.2rem);line-height:1.02;max-width:20ch;margin:.3rem 0 1.2rem;letter-spacing:-.035em;overflow-wrap:anywhere}.lede{font-size:clamp(1.2rem,2.5vw,1.65rem);max-width:42rem;margin:0 0 1.7rem}.actions,.card-actions{display:flex;flex-wrap:wrap;align-items:center;gap:.85rem}.button{display:inline-block;background:var(--ink);color:var(--white);padding:.72rem 1.1rem;border-radius:3px;text-decoration:none;font-size:.82rem;font-weight:700}.button--quiet{background:transparent;color:var(--ink);border:1px solid var(--ink)}.section-block{margin:0 0 clamp(3.2rem,7vw,6.5rem);min-width:0}.section-heading{max-width:660px;border-top:1px solid var(--ink);padding-top:1rem;margin-bottom:1.5rem}.section-heading h2{font-size:clamp(1.7rem,3.2vw,2.8rem);line-height:1.08;margin:.15rem 0 .6rem}.active-grid,.quiet-grid,.edition-grid,.directory-list,.closing-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1.25rem}.edition-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.dispatch-card,.edition-card{min-width:0;overflow-wrap:anywhere}.dispatch-card{background:var(--white);border:1px solid var(--line);padding:clamp(1.3rem,3vw,2.1rem);position:relative;box-shadow:0 18px 45px #1e3f4f14}.dispatch-card--featured{min-height:430px;display:flex;flex-direction:column}.card-rule{position:absolute;top:0;left:0;width:34%;height:5px;background:var(--muted)}.status{display:inline-block;width:max-content;padding:.2rem .55rem;background:var(--paper);border-radius:99px;font-size:.68rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase}.latest-label{font:700 .72rem/1.2 system-ui,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:2rem 0 .45rem}.latest-headline{font-size:clamp(1.45rem,2.6vw,2.15rem);line-height:1.1;margin:0 0 .55rem}.latest-headline a,.edition-card h3 a{text-decoration:none}.date-line{font:700 .85rem/1.3 system-ui,sans-serif;color:var(--muted);margin:0}.dispatch-card h2{font-size:1.35rem;margin:auto 0 .6rem;padding-top:2rem}.card-description{margin:0 0 1rem}.cadence{font-size:.75rem;font-weight:700;color:var(--muted);margin:0 0 1.2rem}.text-link,.support-link{font:700 .82rem/1.2 system-ui,sans-serif}.support-links,.support-link{color:var(--muted)}.support-links{display:flex;gap:.65rem;flex-wrap:wrap;margin-left:.25rem}.dispatch-card--compact{box-shadow:none;background:#f8f2e9;padding:1.15rem}.dispatch-card--compact .latest-label{margin-top:1.2rem}.dispatch-card--compact .latest-headline{font-size:1.25rem}.dispatch-card--compact h2{font-size:1.1rem;padding-top:1.1rem}.paused-list{list-style:none;margin:0;padding:0;border-top:1px solid var(--line);max-width:760px}.paused-list li{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:1rem 0;border-bottom:1px solid var(--line)}.paused-list small{display:block;color:var(--muted);margin-top:.2rem}.edition-card{background:#f8f2e9;border-top:3px solid var(--muted);padding:1.15rem}.edition-card h3{font-size:1.2rem;line-height:1.15;margin:.25rem 0 .65rem}.edition-summary{margin:.2rem 0 .7rem}.edition-meta{font-size:.72rem;font-weight:700;color:var(--muted);margin:0}.page-intro{max-width:750px;margin-bottom:3rem}.page-intro h1{font-size:clamp(2.5rem,6vw,4.5rem);line-height:1.02;margin:.2rem 0 1rem}.prose{max-width:720px}.site-footer{display:flex;justify-content:space-between;gap:2rem;border-top:1px solid var(--line);padding:2rem 0 3.5rem;font-size:.9rem;color:var(--muted)}.site-footer p{margin:.35rem 0}.site-footer a{color:var(--ink)}img{max-width:100%;height:auto}.active-grid>*,.quiet-grid>*,.edition-grid>*,.closing-grid>*{min-width:0;overflow-wrap:anywhere}"


MOBILE_FIX = "@media(max-width:800px){.site-header{flex-direction:column;align-items:stretch}.site-header nav{width:100%;gap:.65rem}}@media(max-width:560px){body{overflow-x:visible}.site-header,.site-footer,main{width:calc(100vw - 1.5rem);max-width:calc(100vw - 1.5rem)}.hero,.section-block,.section-heading,.hero h1,.lede{width:100%;max-width:100%;overflow-wrap:anywhere}.hero h1{font-size:clamp(1.55rem,8vw,2.5rem);letter-spacing:-.02em;word-break:break-word}.lede{font-size:1.05rem;line-height:1.45;word-break:break-word}.section-heading h2{font-size:clamp(1.45rem,7vw,2.2rem);overflow-wrap:anywhere}.latest-headline{font-size:1.18rem;overflow-wrap:anywhere}}"


CORRECTION_CSS = ".site-header{flex-wrap:wrap}.site-header nav{min-width:0;max-width:100%;flex-wrap:wrap}.brand,.brand-title,.lede,.latest-headline{min-width:0;max-width:100%;overflow-wrap:anywhere;word-break:break-word}.actions,.card-actions{display:flex;flex-wrap:wrap;min-width:0;max-width:100%}.button{background:#1E3F4F;color:#fffdf8;border:1px solid #1E3F4F}.button--quiet{background:#EFE7DA;color:#1E3F4F;border:1px solid #1E3F4F}img{display:block;max-width:100%;height:auto}@media(max-width:800px){.site-header{flex-direction:column;align-items:stretch}.site-header nav{width:100%;gap:.65rem 1rem}.actions,.card-actions{flex-direction:column;align-items:stretch}.actions>* ,.card-actions>*{width:100%;max-width:100%}}@media(max-width:560px){html,body{max-width:100%;overflow-x:visible}.site-header,.site-footer,main{width:calc(100% - 1.5rem);max-width:100%}.active-grid,.quiet-grid,.edition-grid,.directory-list,.closing-grid{grid-template-columns:1fr}.hero h1,.section-heading h2,.latest-headline{overflow-wrap:anywhere;word-break:break-word}}@media(min-width:1600px){main{padding-top:4rem}.hero{padding-bottom:4rem}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"

def render_site(site_root: Path, output_root: Path) -> dict[str, int]:
    dispatches, recent = public_model(site_root)
    output_root.mkdir(parents=True, exist_ok=True)
    assets = output_root / "assets"
    assets.mkdir(exist_ok=True)
    shutil.copy2(site_root / "assets" / "site.css", assets / "site-legacy.css")
    for name in ("bluefern.png", "dispatches-from-blue-fern-co.png"):
        source = site_root / "assets" / name
        if source.exists():
            shutil.copy2(source, assets / name)
    (assets / "site-phase1.css").write_text(BASE_CSS + MOBILE_CSS + MOBILE_FIX + CORRECTION_CSS, encoding="utf-8")
    for slug in ("gaza", "food-line", "care-line", "cascadia", "american-pressure"):
        source = site_root / slug
        if source.exists():
            shutil.copytree(source, output_root / slug, dirs_exist_ok=True)
    from .public_site_shell import render_dispatch_landing
    for slug in ("gaza", "food-line", "care-line"):
        if (site_root / slug / "index.html").exists():
            (output_root / slug / "index.html").write_text(render_dispatch_landing(site_root, slug), encoding="utf-8")
    for page in output_root.rglob("*.html"):
        page.write_text(_clean(_text(page)), encoding="utf-8")
    (output_root / "index.html").write_text(_page("Dispatches From The Blue Fern Co.", _home(dispatches, recent)), encoding="utf-8")
    directory = "".join(_dispatch_card(item, True) for item in dispatches if item.url)
    (output_root / "dispatches").mkdir(exist_ok=True)
    (output_root / "dispatches" / "index.html").write_text(_page("Dispatches", f'<section class="page-intro"><p class="eyebrow">The Blue Fern Co. public directory</p><h1>Dispatches</h1><p>Current public products, their status, and the latest edition available to readers.</p></section><div class="directory-list">{directory}</div>', "dispatches"), encoding="utf-8")
    (output_root / "methodology").mkdir(exist_ok=True)
    (output_root / "methodology" / "index.html").write_text(_page("Methodology", '<section class="page-intro"><p class="eyebrow">Public record</p><h1>Methodology</h1><p>Only released records and rendered public manifests feed this preview. Review queues, proposed editions, raw payloads, internal IDs, and unpublished findings remain private.</p></section>', "methodology"), encoding="utf-8")
    (output_root / "about").mkdir(exist_ok=True)
    (output_root / "about" / "index.html").write_text(_page("About", '<section class="page-intro"><p class="eyebrow">The project</p><h1>About Blue Fern Dispatches</h1><p>Dispatches From The Blue Fern Co. is a source-based public dispatch system for reporting on systems, access, pressure, and accountability.</p><p>Gaza and Food Line are Active. Care Line is a Pilot. Cascadia is Paused: its latest visible public edition is May 5, 2026, and no currently operating weekly publication task was found.</p></section>', "about"), encoding="utf-8")
    routes = ("index.html", "dispatches/index.html", "methodology/index.html", "about/index.html", "gaza/index.html", "food-line/index.html", "care-line/index.html")
    return {"dispatches": len(dispatches), "recent_editions": len(recent), "copied_dispatch_roots": 5, "valid_routes": sum((output_root / route).exists() for route in routes)}


PHASE1A_ROUTES = ("index.html", "dispatches/index.html", "methodology/index.html", "about/index.html")


def render_phase1a_site(
    site_root: Path,
    output_root: Path,
    *,
    shell_asset_root: Path | None = None,
) -> dict[str, object]:
    """Render only the root-site foundation; dispatch-owned paths are untouched."""
    from .public_site_shell import (
        render_about,
        render_dispatch_directory,
        render_homepage,
        render_methodology,
        render_site_shell_stylesheet,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    assets = output_root / "assets"
    assets.mkdir(exist_ok=True)
    (output_root / "index.html").write_text(_clean(render_homepage(site_root)), encoding="utf-8")
    (output_root / "dispatches").mkdir(exist_ok=True)
    (output_root / "dispatches" / "index.html").write_text(_clean(render_dispatch_directory(site_root)), encoding="utf-8")
    (output_root / "methodology").mkdir(exist_ok=True)
    (output_root / "methodology" / "index.html").write_text(_clean(render_methodology()), encoding="utf-8")
    (output_root / "about").mkdir(exist_ok=True)
    (output_root / "about" / "index.html").write_text(_clean(render_about()), encoding="utf-8")
    (assets / "site.css").write_text(render_site_shell_stylesheet(shell_asset_root or site_root), encoding="utf-8")
    return {
        "scope": "phase1a-root-foundation",
        "routes": list(PHASE1A_ROUTES),
        "dispatch_owned_paths": [],
        "private_paths": [],
    }


# Phase 1B root-shell refinements.  These definitions intentionally override
# the earlier Phase 1A helpers while leaving dispatch-owned renderers alone.
TOPIC_LABELS = {
    "gaza": "Gaza",
    "food-line": "Food access",
    "care-line": "Healthcare access",
    "cascadia": "Cascadia",
    "american-pressure": "American pressure",
    "ice-activity-and-consequences": "ICE activity",
}


def _nav(active: str = "home") -> str:
    links = [
        ("/", "Home", "home"),
        ("/dispatches/", "Dispatches", "dispatches"),
        ("/methodology/", "Methodology", "methodology"),
        ("/about/", "About", "about"),
    ]
    rendered = []
    for href, label, key in links:
        current = ' class="is-active" aria-current="page"' if active == key else ""
        rendered.append(f'<a href="{href}"{current}>{label}</a>')
    return '<header class="site-header"><a class="brand" href="/"><img class="brand-mark" src="/assets/bluefern-mark.png" alt="The Blue Fern Co. logo"><span class="brand-text"><span class="brand-kicker">The Blue Fern Co.</span><span class="brand-title">Dispatches From The Blue Fern Co.</span></span></a><nav aria-label="Primary">' + "".join(rendered) + "</nav></header>"


def _page(title: str, body: str, active: str = "home") -> str:
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="/assets/bluefern.ico"><title>{html.escape(title)}</title><link rel="stylesheet" href="/assets/site-phase1.css"></head><body>{_nav(active)}<main>{body}</main><footer class="site-footer"><div class="footer-brand"><img class="footer-mark" src="/assets/bluefern-mark.png" alt="The Blue Fern Co. logo"><div><strong>The Blue Fern Co.</strong><p>Source-backed public briefings for reading, research, and accountability.</p></div></div><p><a href="/methodology/">How we work</a> · <a href="/about/">About this project</a></p></footer></body></html>'


def _edition_card(item: Edition) -> str:
    meta = " · ".join(value for value in (item.location, f"{item.source_count} public sources" if item.source_count is not None else "") if value) or "Public edition"
    topic = TOPIC_LABELS.get(item.slug, item.slug.replace("-", " ").title())
    return f'<article class="edition-card edition-card--{html.escape(item.slug)}"><p class="topic-badge topic-badge--{html.escape(item.slug)}">{html.escape(topic)}</p><h3><a href="{item.url}">{html.escape(item.headline)}</a></h3><p class="edition-source">Public source headline · {html.escape(LABELS[item.slug])} · {html.escape(item.display_date)}</p><p class="edition-summary">{html.escape(item.summary or "Published public development")}</p><p class="edition-meta">{html.escape(meta)}</p></article>'


def _dispatch_card(item: Dispatch, compact: bool = False) -> str:
    latest = item.latest
    latest_html = f'<p class="latest-label">Latest public development</p><h3 class="latest-headline"><a href="{latest.url}">{html.escape(latest.headline)}</a></h3><p class="date-line">{html.escape(latest.display_date)}</p>' if latest else '<p class="latest-label">Current public status</p><h3 class="latest-headline">No public edition indexed</h3>'
    actions = f'<a class="button" href="{latest.url}">Read latest</a>' if latest else ""
    if item.archive_url:
        actions += f'<a class="text-link" href="{item.archive_url}">Archive</a>'
    actions += "".join(f'<a class="support-link" href="{href}">{label}</a>' for label, href in item.public_links)
    card_class = "dispatch-card--compact" if compact else "dispatch-card--featured"
    return f'<article class="dispatch-card {card_class}"><p class="status">{html.escape(item.status)}</p>{latest_html}<h2>{html.escape(item.name)}</h2><p class="card-description">{html.escape(item.description)}</p><p class="cadence">{html.escape(item.cadence)}</p><div class="card-actions">{actions}</div></article>'


_LEGACY_RENDER_PHASE1A_SITE = render_phase1a_site


def render_phase1a_site(
    site_root: Path,
    output_root: Path,
    *,
    shell_asset_root: Path | None = None,
) -> dict[str, object]:
    result = _LEGACY_RENDER_PHASE1A_SITE(site_root, output_root, shell_asset_root=shell_asset_root)
    assets = output_root / "assets"
    asset_root = shell_asset_root or site_root
    for name in ("bluefern.png", "bluefern-mark.png", "dispatches-from-blue-fern-co.png", "bluefern.ico"):
        source = asset_root / "assets" / name
        if not source.exists() and name in {"bluefern-mark.png", "bluefern.ico"}:
            source = Path(__file__).resolve().parents[2] / "assets" / name
        if source.exists():
            shutil.copy2(source, assets / name)
    return result


# Phase 1C editorial presentation refinements.
_LEGACY_EDITION = _edition
_LEGACY_HOME = _home


def _topic_safe_title(slug: str, title: str) -> bool:
    value = title.lower().strip()
    if slug != "gaza":
        return True
    return not any(marker in value for marker in ("middle east crisis live", "iran war live", "live:", "iran denies", "trump", "us president", "washington politics"))


def _curation_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("stories") or payload.get("records") or []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return []


def _public_development_title(slug: str, manifest: dict, curation: object, fallback: str) -> str:
    for key in ("public_development_title", "lead_headline", "lead_title", "primary_signal_title"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip() and _topic_safe_title(slug, value):
            return _clean(value)
    rows = _curation_rows(curation)
    ranked = sorted(enumerate(rows), key=lambda pair: (bool(pair[1].get("substantive_ground")), bool(pair[1].get("core_ground_development")), pair[1].get("score", 0), -pair[0]), reverse=True)
    for _, row in ranked:
        value = row.get("title") or row.get("headline")
        if isinstance(value, str) and value.strip() and _topic_safe_title(slug, value):
            return _clean(value)
    return "Gaza public developments" if slug == "gaza" else _clean(fallback)


def _public_timestamp(manifest: dict) -> str | None:
    for key in ("published_at", "publication_datetime", "actual_run_local_time"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _display_timestamp(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo("America/Los_Angeles"))
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year} &middot; {parsed.strftime('%I:%M %p').lstrip('0')} PT"
    except (TypeError, ValueError):
        return fallback


def _edition(site_root: Path, slug: str, edition_date: str) -> Edition:
    base = _LEGACY_EDITION(site_root, slug, edition_date)
    directory = site_root / slug / "editions" / edition_date
    manifest = _json(directory / "edition_manifest.json")
    try:
        curation = json.loads(_text(directory / "curation_manifest.json"))
    except json.JSONDecodeError:
        curation = {}
    headline = _public_development_title(slug, manifest, curation, base.headline)
    return Edition(base.slug, base.date, base.url, headline, base.status, base.source_count, base.publisher_count, base.signal_count, base.summary, base.location, base.item_url, base.no_update, base.substantive, _public_timestamp(manifest))


def _home(dispatches: list[Dispatch], recent: list[Edition]) -> str:
    body = _LEGACY_HOME(dispatches, recent)
    return re.sub(r'<div class="hero-mark"[^>]*>.*?</div>', "", body, flags=re.DOTALL)


def _edition_card(item: Edition) -> str:
    source_count = f"{item.source_count} public sources" if item.source_count is not None else "Public edition"
    topic = TOPIC_LABELS.get(item.slug, item.slug.replace("-", " ").title())
    timestamp = _display_timestamp(item.published_at, item.display_date)
    return f'<article class="edition-card edition-card--{html.escape(item.slug)}"><p class="topic-badge topic-badge--{html.escape(item.slug)}">{html.escape(topic)}</p><h3><a href="{item.url}">{html.escape(item.headline)}</a></h3><p class="edition-source">{html.escape(LABELS[item.slug])} · {html.escape(timestamp).replace("&amp;middot;", "&middot;")}</p><p class="edition-provenance">Based on public source reporting</p><p class="edition-meta">{html.escape(source_count)}</p></article>'



def _dispatch_card(item: Dispatch, compact: bool = False) -> str:
    latest = item.latest
    if latest:
        timestamp = _display_timestamp(latest.published_at, latest.display_date)
        latest_html = f'<p class="latest-label">Latest public development</p><h3 class="latest-headline"><a href="{latest.url}">{html.escape(latest.headline)}</a></h3><p class="date-line">{html.escape(item.name)} &middot; {html.escape(timestamp).replace("&amp;middot;", "&middot;")}</p><p class="edition-provenance">Based on public source reporting</p>'
        actions = f'<a class="button" href="{latest.url}">Read latest</a>'
    else:
        latest_html = '<p class="latest-label">Current public status</p><h3 class="latest-headline">No public edition indexed</h3>'
        actions = ""
    if item.archive_url:
        actions += f'<a class="text-link" href="{item.archive_url}">Archive</a>'
    actions += "".join(f'<a class="support-link" href="{href}">{label}</a>' for label, href in item.public_links)
    card_class = "dispatch-card--compact" if compact else "dispatch-card--featured"
    return f'<article class="dispatch-card {card_class}"><p class="status">{html.escape(item.status)}</p>{latest_html}<h2>{html.escape(item.name)}</h2><p class="card-description">{html.escape(item.description)}</p><p class="cadence">{html.escape(item.cadence)}</p><div class="card-actions">{actions}</div></article>'



def _source_count_label(count: int | None) -> str:
    if count is None:
        return "Public edition"
    return f"{count} public source" if count == 1 else f"{count} public sources"


def _curated_public_source_count(curation: object, headline: str, fallback: int | None) -> int | None:
    for row in _curation_rows(curation):
        title = row.get("title") or row.get("headline")
        ids = row.get("public_source_record_ids")
        if isinstance(title, str) and _clean(title) == headline and isinstance(ids, list):
            return len(ids)
    return fallback


def _edition(site_root: Path, slug: str, edition_date: str) -> Edition:
    base = _LEGACY_EDITION(site_root, slug, edition_date)
    directory = site_root / slug / "editions" / edition_date
    manifest = _json(directory / "edition_manifest.json")
    try:
        curation = json.loads(_text(directory / "curation_manifest.json"))
    except json.JSONDecodeError:
        curation = {}
    headline = _public_development_title(slug, manifest, curation, base.headline)
    count = _curated_public_source_count(curation, headline, base.source_count)
    return Edition(base.slug, base.date, base.url, headline, base.status, count, base.publisher_count, base.signal_count, base.summary, base.location, base.item_url, base.no_update, base.substantive, _public_timestamp(manifest))


def _publication_same_day(item: Edition) -> bool:
    if not item.published_at:
        return False
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        published = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
        if published.tzinfo is not None:
            published = published.astimezone(ZoneInfo("America/Los_Angeles"))
        return published.date().isoformat() == item.date
    except (TypeError, ValueError):
        return False


def _edition_card(item: Edition) -> str:
    topic = TOPIC_LABELS.get(item.slug, item.slug.replace("-", " ").title())
    count = _source_count_label(item.source_count)
    edition_date = item.display_date
    timestamp = _display_timestamp(item.published_at, "")
    source = f"{LABELS[item.slug]} &middot; {edition_date}"
    published = ""
    if timestamp:
        if _publication_same_day(item):
            source = f"{LABELS[item.slug]} &middot; {timestamp}"
        else:
            source = f"{LABELS[item.slug]} &middot; {edition_date} edition"
            published = f'<p class="edition-published">Published {html.escape(timestamp).replace("&amp;middot;", "&middot;")}</p>'
    source_html = html.escape(source).replace("&amp;middot;", "&middot;")
    return f'<article class="edition-card edition-card--{html.escape(item.slug)}"><p class="topic-badge topic-badge--{html.escape(item.slug)}">{html.escape(topic)}</p><h3><a href="{item.url}">{html.escape(item.headline)}</a></h3><p class="edition-source">{source_html}</p>{published}<p class="edition-provenance">Based on public source reporting</p><p class="edition-meta">{html.escape(count)}</p></article>'


def _dispatch_card(item: Dispatch, compact: bool = False) -> str:
    latest = item.latest
    if latest and latest.no_update:
        latest_html = f'<p class="latest-label">Latest public edition</p><h3 class="latest-headline"><a href="{latest.url}">No current update</a></h3><p class="date-line">{html.escape(latest.display_date)}</p>'
        actions = f'<a class="button" href="{latest.url}">Read latest edition</a>'
    elif latest:
        timestamp = _display_timestamp(latest.published_at, latest.display_date)
        latest_html = f'<p class="latest-label">Latest public development</p><h3 class="latest-headline"><a href="{latest.url}">{html.escape(latest.headline)}</a></h3><p class="date-line">{html.escape(item.name)} &middot; {html.escape(timestamp).replace("&amp;middot;", "&middot;")}</p><p class="edition-provenance">Based on public source reporting</p>'
        actions = f'<a class="button" href="{latest.url}">Read latest</a>'
    else:
        latest_html = '<p class="latest-label">Latest public status</p><h3 class="latest-headline">No public edition indexed</h3>'
        actions = ""
    if item.archive_url:
        actions += f'<a class="text-link" href="{item.archive_url}">Archive</a>'
    actions += "".join(f'<a class="support-link" href="{href}">{label}</a>' for label, href in item.public_links)
    card_class = "dispatch-card--compact" if compact else "dispatch-card--featured"
    return f'<article class="dispatch-card {card_class}"><p class="status">{html.escape(item.status)}</p>{latest_html}<h2>{html.escape(item.name)}</h2><p class="card-description">{html.escape(item.description)}</p><p class="cadence">{html.escape(item.cadence)}</p><div class="card-actions">{actions}</div></article>'
