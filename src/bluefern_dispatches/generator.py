from __future__ import annotations

import argparse
import html
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://dispatches.thebluefernco.com"
BLUE_FERN_URL = "https://thebluefernco.com"
TEMPLATE_VERSION = "dispatches-static-v1"
DEFAULT_BACKUP_ROOT = Path(r"C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups")
PUBLIC_ROOT_NAMES = {"site"}
DETAIL_ROOT_NAMES = {"detail", "paid"}


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


def seed_dispatches(now: str) -> list[DispatchConfig]:
    gaza_sources = [
        SourceRecord("gaza-src-001", "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza", "https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF", "The New York Times", None, now, None, ["gaza-story-001"], ["gaza-claim-001"], "gaza", "2026-05-03"),
        SourceRecord("gaza-src-002", "U.S. to close Israel command center overseeing Gaza truce as Trump plan stalls", "https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE", "Haaretz", None, now, None, ["gaza-story-001"], ["gaza-claim-002"], "gaza", "2026-05-03"),
        SourceRecord("gaza-src-003", "Court extends detention of 2 Gaza flotilla activists accused of Hamas links", "https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR", "The Times of Israel", None, now, None, ["gaza-story-001"], ["gaza-claim-003"], "gaza", "2026-05-03"),
    ]
    cascadia_sources = [
        SourceRecord("cascadia-src-001", "Placeholder source record for Cascadia launch edition", f"{BASE_URL}/cascadia/editions/2026-05-03/sources_manifest.json", "Blue Fern Dispatch Records", "2026-05-03T00:00:00Z", now, None, ["cascadia-story-001"], ["cascadia-admin-001"], "cascadia", "2026-05-03")
    ]
    return [
        DispatchConfig(
            slug="gaza",
            name="Dispatches From Gaza",
            edition_date="2026-05-03",
            tagline="Daily briefing",
            logo="gaza-logo.png",
            sources=gaza_sources,
            stories=[StoryRecord("gaza-story-001", "Dispatches From Gaza - 2026-05-03", "Structured daily briefing synthesizing key developments from public reporting.", "humanitarian", 100, ["Preserved from existing Gaza public edition."], True, False, [s.source_id for s in gaza_sources])],
            body_html=GAZA_BODY_HTML,
            detail_artifacts=[],
        ),
        DispatchConfig(
            slug="cascadia",
            name="Cascadia Systems Dispatch",
            edition_date="2026-05-03",
            tagline="Regional systems briefing",
            logo="cascadia-logo-placeholder.png",
            sources=cascadia_sources,
            stories=[StoryRecord("cascadia-story-001", "Launch placeholder", "The Cascadia dispatch area is prepared for dated, source-backed system briefings.", "editorial-admin", 0, ["Administrative launch placeholder; not a factual regional signal."], True, False, ["cascadia-src-001"], editorial_admin_copy=True)],
            detail_artifacts=["output/detail/cascadia/2026-05-03/detail-placeholder.json"],
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
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
{body}
</body>
</html>
"""


def header(brand: str, root_prefix: str, archive_href: str | None = None) -> str:
    nav = '<a href="/gaza/">Gaza</a><a href="/cascadia/">Cascadia</a>'
    if archive_href:
        nav = f'<a href="{archive_href}">Archive</a><a href="{root_prefix}rss.xml">RSS</a>'
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
    <section class="hero">
      <img class="publisher-mark" src="assets/bluefern.png" alt="The Blue Fern Co.">
      <h1>Dispatches From The Blue Fern Co.</h1>
      <p class="tagline">Source-backed public briefings</p>
    </section>
    <p class="lede">A unified home for Blue Fern dispatches, built around traceable source records and clear public archives.</p>
    <ul class="dispatch-grid">
{cards}
    </ul>
  </main>
{footer("")}"""
    return page("Dispatches From The Blue Fern Co.", f"{BASE_URL}/", "assets/site.css", body)


def render_dispatch_index(dispatch: DispatchConfig) -> str:
    body = f"""{header(dispatch.name, "", "archive.html")}
  <main class="home">
    <section class="hero">
      <img class="hero-logo" src="assets/{dispatch.logo}" alt="{html.escape(dispatch.name)}">
    </section>
    <p class="eyebrow">{html.escape(dispatch.tagline)} archive</p>
    <p class="lede">Structured briefings compiled from traceable source records.</p>
    <p><a href="editions/{dispatch.edition_date}/">Read the latest briefing</a></p>
    <h2>Recent Editions</h2>
    <ul class="edition-list">
      <li><span class="edition-date">{dispatch.edition_date}</span><a href="editions/{dispatch.edition_date}/">{html.escape(dispatch.name)} - {dispatch.edition_date}</a></li>
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
    body = f"""{header(dispatch.name, "../../", "../../archive.html")}
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
  <description>{html.escape(dispatch.tagline)}</description>
  <item>
    <title>{html.escape(dispatch.name)} - {dispatch.edition_date}</title>
    <link>{edition_url}</link>
    <guid>{edition_url}</guid>
  </item>
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
        "paid_or_detail_artifacts": dispatch.detail_artifacts or [],
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

    for asset in ["site.css", "gaza-logo.png", "bluefern.png", "cascadia-logo-placeholder.png"]:
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
        edition_html = render_edition(dispatch)
        write_text(dispatch_public_edition / "index.html", edition_html, dry_run, wrote)
        edition_manifest, sources_manifest, curation_manifest = build_manifests(dispatch, site_root, backup_root, generated_at, warnings, errors)
        write_text(dispatch_public_edition / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
        write_text(dispatch_public_edition / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
        write_text(dispatch_public_edition / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
        write_text(backup_dir / "index.html", edition_html, dry_run, wrote)
        write_text(backup_dir / "edition_manifest.json", json.dumps(edition_manifest, indent=2), dry_run, wrote)
        write_text(backup_dir / "sources_manifest.json", json.dumps(sources_manifest, indent=2), dry_run, wrote)
        write_text(backup_dir / "curation_manifest.json", json.dumps(curation_manifest, indent=2), dry_run, wrote)
        write_text(backup_dir / "run_manifest.json", json.dumps({"generated_at": generated_at, "dry_run": dry_run, "warnings": warnings, "errors": errors}, indent=2), dry_run, wrote)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Dispatches From The Blue Fern Co. static site.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned writes without touching output files.")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT), help="Outside-repo backup root.")
    args = parser.parse_args(argv)
    result = build_site(Path.cwd(), dry_run=args.dry_run, backup_root=Path(args.backup_root))
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
