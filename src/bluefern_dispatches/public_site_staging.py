from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from bluefern_dispatches.generator import (
    DispatchConfig,
    briefing_presentation_for_edition,
    render_archive_for_dates,
    render_briefing_archive_row,
    render_briefing_list_item,
    render_dispatch_index_for_dates,
)
from bluefern_dispatches.public_site_visuals import validate_public_site_visuals
from bluefern_dispatches.root_homepage import (
    discover_public_releases,
    render_dispatch_directory_from_releases,
    render_homepage_from_template,
    select_effective_latest,
    select_homepage_cards,
)


DEFAULT_FOOD_ROOT = Path(r"C:\BlueFernRunner\FoodLineCurrent6")
DEFAULT_GAZA_ROOT = Path(r"C:\BlueFernRunner\GazaDispatchesCurrent6")
DEFAULT_CARE_ROOT = Path(r"C:\BlueFernRunner\CareLineNationalCurrent8")
FALLBACK_TEXT_SENTINELS = ("Old Gaza", "Old Food", "Old Care")
FALLBACK_SHARED_SURFACE_SENTINELS = ("2026-08-05",)
REQUIRED_SHARED_ASSETS = ("site.css", "bluefern.png")
REQUIRED_DISPATCH_LOGOS = {
    "gaza": "gaza-logo.png",
    "food-line": "food-line-logo.png",
    "care-line": "care-line-logo.png",
}


DISPATCH_DIRECTORY_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dispatches</title>
  <link rel="stylesheet" href="/assets/site.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="/index.html">Dispatches From The Blue Fern Co.</a>
    <nav><a href="/">Dispatches Home</a><a href="/gaza/">Gaza</a><a href="/food-line/">Food Line</a><a href="/care-line/">Care Line</a></nav>
  </header>
  <main>
    <section class="section-block">
      <div class="section-heading"><p class="eyebrow">The current edition desk</p><h2>Latest published developments</h2></div>
      <div class="edition-grid">
        <article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-08-05/">Old Gaza</a></h3><p class="edition-source">Dispatches From Gaza &middot; August 5, 2026</p><p class="edition-meta">1 public source</p></article>
        <article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-08-05/">Old Food</a></h3><p class="edition-source">Food Line Dispatch &middot; August 5, 2026</p><p class="edition-meta">1 public source</p></article>
        <article class="edition-card edition-card--care-line"><h3><a href="/care-line/editions/2026-08-05/">Old Care</a></h3><p class="edition-source">Care Line &middot; August 5, 2026</p><p class="edition-meta">1 public source</p></article>
      </div>
    </section>
    <section class="section-block">
      <div class="section-heading"><p class="eyebrow">Active dispatches</p><h2>Public desks</h2></div>
      <div class="active-grid">
        <article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/gaza/editions/2026-08-05/">Old Gaza</a></h3><p class="date-line">Dispatches From Gaza &middot; August 5, 2026</p><h2>Dispatches From Gaza</h2><div class="card-actions"><a class="button" href="/gaza/editions/2026-08-05/">Read latest</a><a class="text-link" href="/gaza/archive.html">Archive</a><a class="support-link" href="/gaza/rss.xml">Feed</a></div></article>
        <article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/food-line/editions/2026-08-05/">Old Food</a></h3><p class="date-line">Food Line Dispatch &middot; August 5, 2026</p><h2>Food Line Dispatch</h2><div class="card-actions"><a class="button" href="/food-line/editions/2026-08-05/">Read latest</a><a class="text-link" href="/food-line/archive.html">Archive</a><a class="support-link" href="/food-line/rss.xml">Feed</a></div></article>
        <article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/care-line/editions/2026-08-05/">Old Care</a></h3><p class="date-line">Care Line &middot; August 5, 2026</p><h2>The Care Line Dispatch</h2><div class="card-actions"><a class="button" href="/care-line/editions/2026-08-05/">Read latest</a><a class="text-link" href="/care-line/archive.html">Archive</a><a class="support-link" href="/care-line/rss.xml">Feed</a></div></article>
      </div>
    </section>
  </main>
  <footer class="site-footer"><a href="/methodology/">How we work</a> &middot; <a href="/about/">About this project</a></footer>
</body>
</html>
"""


SHARED_HOMEPAGE_TEMPLATE = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<title>Dispatches From The Blue Fern Co.</title><link rel="stylesheet" href="/assets/site.css"></head>'
    '<body><header class="site-header"><a class="brand" href="/index.html">Dispatches From The Blue Fern Co.</a>'
    '<nav><a href="/">Dispatches Home</a><a href="/dispatches/">All Dispatches</a><a href="/gaza/">Gaza</a>'
    '<a href="/food-line/">Food Line</a><a href="/care-line/">Care Line</a></nav></header><main>'
    '<section class="hero"><p class="eyebrow">Source-backed public briefings</p><h1>Dispatches From The Blue Fern Co.</h1>'
    '<p class="lede">Public desks tracking source-backed developments across Gaza, food access, and health-care access.</p>'
    '<p class="actions"><a class="button" href="/dispatches/">View latest dispatches</a>'
    '<a class="button button--quiet" href="/about/">How we work</a></p></section>'
    '<section class="section-block"><div class="section-heading"><p class="eyebrow">The current edition desk</p>'
    '<h2>Latest published developments</h2></div><div class="edition-grid"><article>stale</article></div></section>'
    '<section class="section-block"><div class="section-heading"><p class="eyebrow">Active dispatches</p><h2>Public desks</h2></div>'
    '<div class="active-grid">'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/gaza/editions/2026-08-05/">Old Gaza</a></h3>'
    '<p class="date-line">Dispatches From Gaza &middot; August 5, 2026</p><h2>Dispatches From Gaza</h2>'
    '<div class="card-actions"><a class="button" href="/gaza/editions/2026-08-05/">Read latest</a>'
    '<a class="text-link" href="/gaza/archive.html">Archive</a><a class="support-link" href="/gaza/rss.xml">Feed</a></div></article>'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/food-line/editions/2026-08-05/">Old Food</a></h3>'
    '<p class="date-line">Food Line Dispatch &middot; August 5, 2026</p><h2>Food Line Dispatch</h2>'
    '<div class="card-actions"><a class="button" href="/food-line/editions/2026-08-05/">Read latest</a>'
    '<a class="text-link" href="/food-line/archive.html">Archive</a><a class="support-link" href="/food-line/rss.xml">Feed</a></div></article>'
    '<article class="dispatch-card dispatch-card--featured"><h3 class="latest-headline"><a href="/care-line/editions/2026-08-05/">Old Care</a></h3>'
    '<p class="date-line">Care Line &middot; August 5, 2026</p><h2>The Care Line Dispatch</h2>'
    '<div class="card-actions"><a class="button" href="/care-line/editions/2026-08-05/">Read latest</a>'
    '<a class="text-link" href="/care-line/archive.html">Archive</a><a class="support-link" href="/care-line/rss.xml">Feed</a></div></article>'
    '</div></section></main><footer class="site-footer"><a href="/methodology/">How we work</a> &middot; '
    '<a href="/about/">About this project</a></footer></body></html>'
)


def _copy_tree_contents(source: Path, destination: Path, *, exclude_names: set[str] | None = None) -> None:
    if not source.exists():
        raise FileNotFoundError(f"staging source does not exist: {source}")
    excluded = exclude_names or set()
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in excluded:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _copy_required_asset(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"required staging asset is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_current_assets(source_root: Path, stage_root: Path) -> None:
    for asset in REQUIRED_SHARED_ASSETS:
        _copy_required_asset(source_root / "assets" / asset, stage_root / "assets" / asset)
    for dispatch, logo in REQUIRED_DISPATCH_LOGOS.items():
        _copy_required_asset(source_root / "assets" / "site.css", stage_root / dispatch / "assets" / "site.css")
        _copy_required_asset(source_root / "assets" / logo, stage_root / dispatch / "assets" / logo)
        _copy_required_asset(source_root / "assets" / logo, stage_root / "assets" / logo)
    for asset in (
        "bluefern.png",
        "care-line-mark.png",
        "food-line-dispatch-social.jpg",
        "food-line-dispatch-social.png",
        "care-line-dispatch-social.png",
    ):
        source = source_root / "assets" / asset
        if source.exists():
            shutil.copy2(source, stage_root / "assets" / asset)
            for dispatch in REQUIRED_DISPATCH_LOGOS:
                target_dir = stage_root / dispatch / "assets"
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target_dir / asset)


def _dated_dirs(root: Path, *, max_date: str | None = None) -> list[str]:
    if not root.exists():
        return []
    dates = [
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and len(path.name) == 10
        and (max_date is None or path.name <= max_date)
        and (path / "edition_manifest.json").exists()
    ]
    return sorted(dates, reverse=True)


def _prune_gaza_no_update_markers_after(stage_root: Path, max_gaza_date: str | None) -> None:
    if not max_gaza_date:
        return
    status_root = stage_root / "gaza" / "status" / "no-updates"
    if not status_root.exists():
        return
    overflow_root = stage_root / "gaza" / "status" / "no-updates-after-staging-horizon"
    for path in status_root.glob("*.json"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem) and path.stem > max_gaza_date:
            overflow_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(overflow_root / path.name))


def _relative_asset_url(from_dir: Path, target: Path) -> str:
    return os.path.relpath(target, from_dir).replace(os.sep, "/")


def _normalize_asset_references(stage_root: Path) -> None:
    for html_path in stage_root.rglob("*.html"):
        text = html_path.read_text(encoding="utf-8", errors="replace")
        updated = re.sub(
            r'\bsrc=(["\'])(?:\.\./){0,6}assets/bluefern\.png\1',
            'src="/assets/bluefern.png"',
            text,
        )
        for dispatch, logo in REQUIRED_DISPATCH_LOGOS.items():
            dispatch_root = stage_root / dispatch
            if html_path.parent != dispatch_root and dispatch_root not in html_path.parents:
                continue
            replacement = f'src="{_relative_asset_url(html_path.parent, dispatch_root / "assets" / logo)}"'
            updated = re.sub(
                rf'\bsrc=(["\'])(?:(?:\./)|(?:(?:\.\./)+))?{re.escape(dispatch)}/assets/{re.escape(logo)}\1',
                replacement,
                updated,
            )
        if updated != text:
            html_path.write_text(updated, encoding="utf-8")


def _route_path(stage_root: Path, route: str) -> Path:
    if route.endswith("/"):
        return stage_root / route.strip("/") / "index.html" if route != "/" else stage_root / "index.html"
    return stage_root / route.lstrip("/")


def _local_asset_path(stage_root: Path, html_path: Path, src: str) -> Path | None:
    parsed = urlparse(src.strip())
    if parsed.scheme in {"http", "https", "data", "mailto", "tel"}:
        return None
    if parsed.netloc:
        return None
    clean_path = unquote(parsed.path)
    if not clean_path:
        return None
    if clean_path.startswith("/"):
        return stage_root / clean_path.lstrip("/")
    return html_path.parent / clean_path


def _assert_staging_integrity(stage_root: Path, routes: list[str]) -> None:
    errors: list[str] = []
    route_paths: list[Path] = []
    for route in routes:
        path = _route_path(stage_root, route)
        if not path.is_file():
            errors.append(f"required staged route is missing: {route} ({path})")
        else:
            route_paths.append(path)
    shared_surface_paths = {
        _route_path(stage_root, "/"),
        _route_path(stage_root, "/dispatches/"),
    }
    for html_path in stage_root.rglob("*.html"):
        try:
            text = html_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"could not read staged HTML for integrity check: {html_path}: {exc}")
            continue
        for sentinel in FALLBACK_TEXT_SENTINELS:
            if sentinel in text:
                errors.append(f"fallback sentinel {sentinel!r} found in staged HTML: {html_path}")
        if html_path in shared_surface_paths:
            for sentinel in FALLBACK_SHARED_SURFACE_SENTINELS:
                if sentinel in text:
                    errors.append(f"fallback sentinel {sentinel!r} found in staged shared-surface HTML: {html_path}")
    for html_path in route_paths:
        try:
            text = html_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"could not read staged route HTML for image check: {html_path}: {exc}")
            continue
        for match in re.finditer(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', text, flags=re.IGNORECASE):
            src = html.unescape(match.group(1))
            asset_path = _local_asset_path(stage_root, html_path, src)
            if asset_path is None:
                continue
            if not asset_path.is_file():
                relative_html = html_path.relative_to(stage_root)
                errors.append(f"staged route image is missing: {relative_html} -> {src} ({asset_path})")
    if errors:
        raise RuntimeError("staging integrity check failed:\n" + "\n".join(errors))


def _food_manifest(stage_root: Path, edition_date: str) -> dict[str, Any]:
    manifest = stage_root / "food-line" / "editions" / edition_date / "edition_manifest.json"
    if not manifest.exists():
        return {}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _food_title(stage_root: Path, edition_date: str) -> str:
    manifest = _food_manifest(stage_root, edition_date)
    for key in ("public_archive_title", "headline", "lead_headline", "title"):
        value = str(manifest.get(key) or "").strip()
        if value:
            return value
    index = stage_root / "food-line" / "editions" / edition_date / "index.html"
    if index.exists():
        text = index.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"<article\b[^>]*class=['\"][^'\"]*food-line-source-card[^'\"]*['\"][^>]*>.*?<h3\b[^>]*>(.*?)</h3>",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if match is None:
            match = re.search(r"<h1(?:\s[^>]*)?>(.*?)</h1>", text, re.DOTALL | re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()
    return f"Food Line Dispatch - {edition_date}"


def _food_story_count(stage_root: Path, edition_date: str) -> int:
    manifest = _food_manifest(stage_root, edition_date)
    for key in ("public_story_count", "story_count", "source_count"):
        value = manifest.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _refresh_food_line_surfaces(stage_root: Path) -> None:
    dates = _dated_dirs(stage_root / "food-line" / "editions")
    food_root = stage_root / "food-line"
    if not dates:
        return
    latest = dates[0]
    dispatch = DispatchConfig(
        slug="food-line",
        name="Food Line Dispatch",
        edition_date=latest,
        tagline="Source-backed food access pressure briefing.",
        logo="food-line-logo.png",
        sources=[],
        stories=[],
        detail_artifacts=[],
    )
    latest_row = briefing_presentation_for_edition(stage_root, dispatch, latest)
    latest_title = latest_row.title
    recent_items = []
    archive_items = []
    for edition_date in dates:
        row = briefing_presentation_for_edition(stage_root, dispatch, edition_date)
        recent_items.append(render_briefing_list_item(row))
        archive_items.append(render_briefing_archive_row(row))
    recent_html = "".join(recent_items[1:9])
    archive_html = "".join(archive_items)
    mission = (
        "The Food Line Dispatch tracks source-backed signs of food access pressure across the United States, "
        "including pantry strain, meal-service disruption, benefit disruption, price pressure, and local access failures."
    )
    food_root.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Food Line Dispatch</title><link rel=\"stylesheet\" href=\"/assets/site.css\"></head><body>"
        "<header class=\"site-header\"><a class=\"brand\" href=\"/index.html\">Food Line Dispatch</a>"
        "<nav><a href=\"/\">Dispatches Home</a><a href=\"/food-line/archive.html\">Archive</a><a href=\"/food-line/rss.xml\">RSS</a></nav></header>"
        "<main class=\"home food-line-shell\"><section class=\"food-line-hero\">"
        "<img class=\"food-line-logo food-line-logo--home\" src=\"assets/food-line-logo.png\" alt=\"Food Line Dispatch\">"
        "<p class=\"eyebrow\">The Blue Fern Co.</p><h1>Food Line Dispatch</h1>"
        f"<p>{html.escape(mission)}</p></section><section class=\"food-line-panel\"><h2>Latest Briefing</h2>"
        f"<div class=\"food-line-briefing-meta\">{html.escape(latest)}</div>"
        f"<h3><a href=\"editions/{html.escape(latest)}/\">{html.escape(latest_title)}</a></h3>"
        f"<div class=\"food-line-actions\"><a href=\"editions/{html.escape(latest)}/\">Read briefing</a><a href=\"archive.html\">Archive</a></div>"
        "</section><section class=\"food-line-panel\"><h2>About Food Line</h2>"
        "<p>This dispatch is source-backed and uses verified pressure signals only.</p></section>"
        "<section class=\"food-line-panel\"><h2>Recent Editions</h2>"
        f"<ul class=\"food-line-recent-list\">{recent_html}</ul>"
        "<p class=\"food-line-open-archive\"><a href=\"archive.html\">Open the full archive</a></p></section></main>"
        "<footer class=\"site-footer\">Published by The Blue Fern Company</footer></body></html>",
        encoding="utf-8",
    )
    (food_root / "archive.html").write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Food Line Archive</title><link rel=\"stylesheet\" href=\"/assets/site.css\"></head><body>"
        "<header class=\"site-header\"><a class=\"brand\" href=\"/food-line/\">Food Line Dispatch</a>"
        "<nav><a href=\"/\">Dispatches Home</a><a href=\"/food-line/\">Food Line</a><a href=\"/food-line/rss.xml\">RSS</a></nav></header>"
        "<main class=\"archive food-line-shell\"><section class=\"food-line-hero\">"
        "<img class=\"food-line-logo food-line-logo--home\" src=\"assets/food-line-logo.png\" alt=\"Food Line Dispatch\">"
        "<p class=\"eyebrow\">Archive</p><h1>Food Line Archive</h1><p>Chronological archive of source-backed Food Line editions.</p></section>"
        "<section class=\"food-line-panel\"><h2>Latest edition</h2>"
        f"<p><a href=\"editions/{html.escape(latest)}/\">Read the latest briefing</a></p>"
        f"<p>{html.escape(latest_title)}</p><h2>Archive</h2><ul class=\"edition-list\">{archive_html}</ul>"
        "<p><a href=\"index.html\">Back to the Food Line home page</a></p></section></main>"
        "<footer class=\"site-footer\">Published by The Blue Fern Company</footer></body></html>",
        encoding="utf-8",
    )
    rss_items = "".join(
        f"<item><title>{html.escape(_food_title(stage_root, edition_date))}</title>"
        f"<link>https://dispatches.thebluefernco.com/food-line/editions/{html.escape(edition_date)}/</link>"
        f"<guid isPermaLink=\"true\">https://dispatches.thebluefernco.com/food-line/editions/{html.escape(edition_date)}/</guid></item>"
        for edition_date in dates
    )
    (food_root / "rss.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Food Line Dispatch</title>{rss_items}</channel></rss>',
        encoding="utf-8",
    )


def _render_gaza_surfaces(stage_root: Path, *, max_gaza_date: str | None) -> None:
    dates = _dated_dirs(stage_root / "gaza" / "editions", max_date=max_gaza_date)
    dispatch = DispatchConfig(
        slug="gaza",
        name="Dispatches From Gaza",
        edition_date=dates[0] if dates else (max_gaza_date or date.today().isoformat()),
        tagline="Daily source-backed briefings from Gaza.",
        logo="gaza-logo.png",
        sources=[],
        stories=[],
        detail_artifacts=[],
    )
    (stage_root / "gaza" / "index.html").write_text(
        render_dispatch_index_for_dates(dispatch, dates, stage_root),
        encoding="utf-8",
    )
    archive_html = render_archive_for_dates(dispatch, dates, stage_root)
    (stage_root / "gaza" / "archive.html").write_text(archive_html, encoding="utf-8")


def _refresh_shared_surfaces(stage_root: Path) -> None:
    homepage = stage_root / "index.html"
    if not homepage.exists():
        raise FileNotFoundError(f"staged homepage is missing: {homepage}")
    homepage_html = homepage.read_text(encoding="utf-8")
    releases = discover_public_releases(stage_root, verify_root=stage_root, homepage_html=homepage_html)
    latest = select_effective_latest(releases)
    missing = [slug for slug in ("gaza", "food-line", "care-line") if slug not in latest]
    if missing:
        raise RuntimeError(f"cannot refresh shared staging surfaces; missing releases: {', '.join(missing)}")
    homepage_cards = select_homepage_cards(releases)
    try:
        rendered_homepage = render_homepage_from_template(homepage_html, homepage_cards)
    except ValueError:
        rendered_homepage = render_homepage_from_template(SHARED_HOMEPAGE_TEMPLATE, homepage_cards)
    rendered_homepage = render_dispatch_directory_from_releases(rendered_homepage, latest)
    homepage.write_text(rendered_homepage, encoding="utf-8")
    dispatches = stage_root / "dispatches" / "index.html"
    dispatches.parent.mkdir(parents=True, exist_ok=True)
    template = dispatches.read_text(encoding="utf-8") if dispatches.exists() else DISPATCH_DIRECTORY_TEMPLATE
    dispatches.write_text(render_dispatch_directory_from_releases(template, latest), encoding="utf-8")


def stage_public_site_preview(
    *,
    source_root: Path,
    output_root: Path,
    food_runner: Path = DEFAULT_FOOD_ROOT,
    gaza_runner: Path = DEFAULT_GAZA_ROOT,
    care_runner: Path = DEFAULT_CARE_ROOT,
    max_gaza_date: str | None = None,
    screenshot_dir: Path | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    stage_root = output_root.resolve()
    if stage_root.exists() and any(stage_root.iterdir()):
        raise FileExistsError(f"staging output root is not empty: {stage_root}")
    stage_root.mkdir(parents=True, exist_ok=True)

    _copy_tree_contents(
        food_runner / "output" / "site",
        stage_root,
        exclude_names={"gaza", "care-line"},
    )
    _copy_tree_contents(gaza_runner / "output" / "site" / "gaza", stage_root / "gaza")
    _copy_tree_contents(care_runner / "output" / "site" / "care-line", stage_root / "care-line")
    _copy_current_assets(source_root, stage_root)
    _prune_gaza_no_update_markers_after(stage_root, max_gaza_date)
    _refresh_food_line_surfaces(stage_root)
    _render_gaza_surfaces(stage_root, max_gaza_date=max_gaza_date)
    _refresh_shared_surfaces(stage_root)
    _normalize_asset_references(stage_root)
    latest_food = next(iter(_dated_dirs(stage_root / "food-line" / "editions")), None)

    routes = [
        "/",
        "/dispatches/",
        "/gaza/",
        "/gaza/archive.html",
        "/food-line/",
        "/care-line/",
    ]
    if latest_food:
        routes.append(f"/food-line/editions/{latest_food}/")
    _assert_staging_integrity(stage_root, routes)

    result: dict[str, Any] = {
        "ok": True,
        "stage_root": str(stage_root),
        "max_gaza_date": max_gaza_date,
        "routes": routes,
    }
    if validate:
        visual = validate_public_site_visuals(stage_root, screenshot_dir=screenshot_dir)
        result["visual_validation"] = visual
        result["ok"] = bool(visual.get("ok"))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a private production-like Dispatches public site preview from runner outputs.")
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--food-runner", type=Path, default=DEFAULT_FOOD_ROOT)
    parser.add_argument("--gaza-runner", type=Path, default=DEFAULT_GAZA_ROOT)
    parser.add_argument("--care-runner", type=Path, default=DEFAULT_CARE_ROOT)
    parser.add_argument("--max-gaza-date")
    parser.add_argument("--screenshot-dir", type=Path)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = stage_public_site_preview(
        source_root=args.source_root,
        output_root=args.output_root,
        food_runner=args.food_runner,
        gaza_runner=args.gaza_runner,
        care_runner=args.care_runner,
        max_gaza_date=args.max_gaza_date,
        screenshot_dir=args.screenshot_dir,
        validate=not args.no_validate,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"stage_root: {result['stage_root']}")
        visual = result.get("visual_validation") or {}
        if visual:
            print(f"visual_ok: {visual.get('ok')}")
            for screenshot in visual.get("screenshots") or []:
                print(f"screenshot: {screenshot}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
