"""Canonical Blue Fern public shell used by the production generator.

The shell reads only rendered public output. Dispatch generators continue to
own their evidence, prose, manifests, archives, and feeds.
"""

from __future__ import annotations

import html
from pathlib import Path

from . import phase1_site


def stylesheet(source_root: Path) -> str:
    legacy = (source_root / "assets" / "site.css").read_text(encoding="utf-8") if (source_root / "assets" / "site.css").exists() else ""
    responsive_guard = "@media(max-width:560px){html,body{width:100%;max-width:100%;overflow-x:hidden}.site-header,.site-footer,main,.hero,.section-block{width:calc(100vw - 1.5rem)!important;max-width:calc(100vw - 1.5rem)!important;min-width:0!important}.brand-title,.hero h1,.lede,.section-heading h2,.latest-headline{overflow-wrap:anywhere;word-break:break-all!important}.hero h1{font-size:clamp(1.5rem,8vw,2.5rem)!important}}"
    return legacy.replace("âœ¦", "✦") + "\n" + phase1_site.BASE_CSS.replace("âœ¦", "✦") + phase1_site.MOBILE_CSS + phase1_site.MOBILE_FIX + responsive_guard


def _page(title: str, body: str, active: str = "home") -> str:
    return phase1_site._page(title, body, active).replace('/assets/site-phase1.css', '/assets/site.css')


def _directory(dispatches: list[phase1_site.Dispatch]) -> str:
    groups = []
    for status in ("Active", "Pilot", "In development", "Paused", "Archived"):
        cards = "".join(phase1_site._dispatch_card(item, compact=status != "Active") for item in dispatches if item.status == status and item.url)
        if cards:
            groups.append(f'<section class="section-block section-block--quiet"><div class="section-heading"><p class="eyebrow">Project status</p><h2>{status}</h2></div><div class="directory-list">{cards}</div></section>')
    return "".join(groups)


def render_homepage(site_root: Path) -> str:
    dispatches, recent = phase1_site.public_model(site_root)
    body = phase1_site._home(dispatches, recent)
    body += '<section class="closing-grid"><div><p class="eyebrow">Explore the archive</p><h2>Follow the public record.</h2><p>Browse dated editions, source links, and corrections.</p><a class="text-link" href="/dispatches/">Open the dispatch directory →</a></div><div><p class="eyebrow">Reporting principles</p><h2>Traceable by design.</h2><p>Claims are bounded by source quality, freshness, deduplication, uncertainty, and editorial review.</p><a class="text-link" href="/methodology/">Read the methodology →</a></div></section>'
    return _page("Dispatches From The Blue Fern Co.", body)


def stylesheet(source_root: Path) -> str:
    legacy = (source_root / "assets" / "site.css").read_text(encoding="utf-8") if (source_root / "assets" / "site.css").exists() else ""
    responsive_guard = "@media(max-width:560px){html,body{width:100%;max-width:100%;overflow-x:hidden}.site-header,.site-footer,main,.hero,.section-block{width:calc(100vw - 1.5rem)!important;max-width:calc(100vw - 1.5rem)!important;min-width:0!important}.brand-title,.hero h1,.lede,.section-heading h2,.latest-headline{overflow-wrap:anywhere;word-break:break-all!important}.hero h1{font-size:clamp(1.5rem,8vw,2.5rem)!important}.brand:before{content:'*'!important}}"
    combined = legacy + "\n" + phase1_site.BASE_CSS + phase1_site.MOBILE_CSS + phase1_site.MOBILE_FIX + responsive_guard
    return combined.replace("\u00e2\u0153\u00a6", "*")


def render_homepage(site_root: Path) -> str:
    dispatches, recent = phase1_site.public_model(site_root)
    body = phase1_site._home(dispatches, recent)
    body += '<section class="closing-grid"><div><p class="eyebrow">Explore the archive</p><h2>Follow the public record.</h2><p>Browse dated editions, source links, and corrections.</p><a class="text-link" href="/dispatches/">Open the dispatch directory -&gt;</a></div><div><p class="eyebrow">Reporting principles</p><h2>Traceable by design.</h2><p>Claims are bounded by source quality, freshness, deduplication, uncertainty, and editorial review.</p><a class="text-link" href="/methodology/">Read the methodology -&gt;</a></div></section>'
    return _page("Dispatches From The Blue Fern Co.", body)


def render_dispatch_directory(site_root: Path) -> str:
    dispatches, _recent = phase1_site.public_model(site_root)
    body = '<section class="page-intro"><p class="eyebrow">The Blue Fern Co. public directory</p><h1>Dispatches</h1><p>Current public products, their status, and the latest released edition available to readers.</p></section>' + _directory(dispatches)
    return _page("Dispatches", body, "dispatches")


def render_methodology() -> str:
    body = '<section class="page-intro"><p class="eyebrow">Public record</p><h1>Methodology</h1><p>Blue Fern dispatches use source-traceable records, bounded claims, editorial review, freshness checks, deduplication, uncertainty, and corrections.</p></section><section class="prose"><h2>Public and private separation</h2><p>Only released public artifacts appear on this site. Review queues, proposed editions, operational logs, raw payloads, paid/detail roots, and private records remain outside public rendering.</p><h2>No-update editions</h2><p>A no-update edition records that no qualifying public signal met the threshold for that window. It remains in the archive but is not presented as a current development.</p></section>'
    return _page("Methodology", body, "methodology")


def render_about() -> str:
    body = '<section class="page-intro"><p class="eyebrow">The project</p><h1>About Blue Fern Dispatches</h1><p>Dispatches From The Blue Fern Co. is a source-based public dispatch system for reporting on systems, access, pressure, and accountability.</p></section><section class="prose"><p>Gaza and Food Line are Active. Care Line is a Pilot. Cascadia is Paused, with its latest visible public edition dated May 5, 2026. American Pressure and ICE Activity and Consequences are In development.</p></section>'
    return _page("About", body, "about")
