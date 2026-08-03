"""Canonical Blue Fern public shell used by the Phase 1 preview and generator."""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from . import phase1_site


RECOVERY_CSS = """
html,body{max-width:100%;overflow-x:visible}
.site-header,.site-footer,main,.hero,.section-block,.section-heading,.dispatch-card,.edition-card{min-width:0;max-width:100%}
.site-header{flex-wrap:wrap}
.site-header nav{min-width:0;max-width:100%;flex-wrap:wrap}
.brand,.brand-title,.lede,.latest-headline{min-width:0;max-width:100%;overflow-wrap:anywhere;word-break:break-word}
.actions,.card-actions{display:flex;flex-wrap:wrap;min-width:0;max-width:100%}
.button{background:#1E3F4F;color:#fffdf8;border:1px solid #1E3F4F}
.button--quiet{background:#EFE7DA;color:#1E3F4F;border:1px solid #1E3F4F}
img{display:block;max-width:100%;height:auto}
@media(max-width:800px){.site-header{flex-direction:column;align-items:stretch}.site-header nav{width:100%;gap:.65rem 1rem}.actions,.card-actions{flex-direction:column;align-items:stretch}.actions>* ,.card-actions>*{width:100%;max-width:100%}}
@media(max-width:560px){.site-header,.site-footer,main{width:calc(100% - 1.5rem)}.active-grid,.quiet-grid,.edition-grid,.directory-list,.closing-grid{grid-template-columns:1fr}.hero h1,.section-heading h2,.latest-headline{overflow-wrap:anywhere;word-break:break-word}}
@media(min-width:1600px){main{padding-top:4rem}.hero{padding-bottom:4rem}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
""".strip()


def stylesheet(source_root: Path) -> str:
    legacy_path = source_root / "assets" / "site.css"
    legacy = legacy_path.read_text(encoding="utf-8") if legacy_path.exists() else ""
    return "\n".join((legacy, phase1_site.BASE_CSS, phase1_site.MOBILE_CSS, phase1_site.MOBILE_FIX, RECOVERY_CSS)).replace("Ã", "").replace("Â", "")


def _page(title: str, body: str, active: str = "home") -> str:
    return phase1_site._page(title, body, active).replace("/assets/site-phase1.css", "/assets/site.css")


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
    body += '<section class="closing-grid"><div><p class="eyebrow">Explore the archive</p><h2>Follow the public record.</h2><p>Browse dated editions, source links, and corrections.</p><a class="text-link" href="/dispatches/">Open the dispatch directory -&gt;</a></div><div><p class="eyebrow">Reporting principles</p><h2>Traceable by design.</h2><p>Claims are bounded by source quality, freshness, deduplication, uncertainty, and editorial review.</p><a class="text-link" href="/methodology/">Read the methodology -&gt;</a></div></section>'
    return _page("Dispatches From The Blue Fern Co.", body)


def render_dispatch_directory(site_root: Path) -> str:
    dispatches, _recent = phase1_site.public_model(site_root)
    body = '<section class="page-intro"><p class="eyebrow">The Blue Fern Co. public directory</p><h1>Dispatches</h1><p>Current public products, their status, and the latest released edition available to readers.</p></section>' + _directory(dispatches)
    return _page("Dispatches", body, "dispatches")


def render_dispatch_landing(site_root: Path, slug: str) -> str:
    dispatches, _recent = phase1_site.public_model(site_root)
    item = next((dispatch for dispatch in dispatches if dispatch.slug == slug), None)
    if item is None:
        return _page("Dispatch", '<section class="page-intro"><h1>Dispatch unavailable</h1></section>')
    latest = item.latest
    logo = f'<img class="hero-logo" src="assets/{html.escape(slug + "-logo.png" if slug == "care-line" else "")}" alt="{html.escape(item.name)}">' if slug == "care-line" else ""
    lead = f'<p class="eyebrow">Latest public coverage · Current update</p><h2>{html.escape(latest.headline)}</h2><p><a class="button" href="{latest.url}">Read latest · {html.escape(latest.display_date)}</a></p>' if latest else '<p class="eyebrow">No public edition indexed</p>'
    links = "".join(f'<a class="support-link" href="{html.escape(href)}">{html.escape(label)}</a>' for label, href in item.public_links if not (slug == "food-line" and label == "Feed"))
    body = f'<section class="hero">{logo}<p class="eyebrow">{html.escape(item.status)} · {html.escape(item.cadence)}</p><h1>{html.escape(item.name)}</h1><p class="lede">{html.escape(item.description)}</p><div class="latest-feature">{lead}</div><p class="support-links">{links}</p></section><section class="section-block"><div class="section-heading"><p class="eyebrow">Public archive</p><h2>Follow the record</h2></div><p><a class="button button--quiet" href="/{slug}/archive.html">Archive</a></p></section>'
    return _page(item.name, body)


def render_methodology() -> str:
    body = '<section class="page-intro"><p class="eyebrow">Public record</p><h1>Methodology</h1><p>Blue Fern dispatches use source-traceable records, bounded claims, editorial review, freshness checks, deduplication, uncertainty, and corrections.</p></section><section class="prose"><h2>Public and private separation</h2><p>Only released public artifacts appear on this site. Review queues, proposed editions, operational logs, raw payloads, paid/detail roots, and private records remain outside public rendering.</p><h2>No-update editions</h2><p>A no-update edition remains in the archive but is not presented as a current development.</p></section>'
    return _page("Methodology", body, "methodology")


def render_about() -> str:
    body = '<section class="page-intro"><p class="eyebrow">The project</p><h1>About Blue Fern Dispatches</h1><p>Dispatches From The Blue Fern Co. is a source-based public dispatch system for reporting on systems, access, pressure, and accountability.</p><p>Gaza and Food Line are Active. Care Line is a Pilot. Cascadia is Paused: its latest visible public edition is May 5, 2026, and no currently operating weekly publication task was found.</p></section>'
    return _page("About", body, "about")


def render_global_header(active: str = "dispatches") -> str:
    return phase1_site._nav(active)


def render_site_shell_stylesheet(source_root: Path) -> str:
    return stylesheet(source_root)


_LEGACY_STYLESHEET = stylesheet


def stylesheet(source_root: Path) -> str:
    css = _LEGACY_STYLESHEET(source_root)
    css = css.replace("overflow-x:hidden", "overflow-x:visible")
    css = re.sub(r'\.brand:before\{[^}]*\}', "", css)
    css = css.replace(".card-rule{position:absolute;top:0;left:0;width:34%;height:5px;background:var(--muted)}", "")
    css = css.replace(".hero-mark{font-size:1.4rem;color:var(--muted)}", "")
    css = css.replace(".brand:before{content:'✦';color:var(--muted);font-size:1.15rem}", "")
    css += ".brand{display:flex;align-items:center;gap:.65rem}.brand-mark{width:38px;height:38px;object-fit:contain;flex:none}.brand-text{display:flex;flex-direction:column;min-width:0;max-width:calc(100% - 2.5rem);overflow-wrap:anywhere}.brand-title{max-width:100%;font-size:clamp(.92rem,4.8vw,1.12rem);overflow-wrap:anywhere;word-break:break-word}.footer-brand{display:flex;align-items:center;gap:.65rem;min-width:0}.footer-mark{width:30px;height:30px;object-fit:contain;flex:none}.topic-badge{display:inline-block;width:max-content;margin:0 0 .8rem;padding:.28rem .55rem;border:1px solid var(--ink);border-radius:999px;background:var(--white);font:800 .68rem/1.2 system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase}.topic-badge--gaza{border-color:#1E3F4F}.topic-badge--food-line{border-color:#7c5a2d}.topic-badge--care-line{border-color:#6b4c6d}.topic-badge--cascadia{border-color:#3f6a59}.topic-badge--american-pressure{border-color:#8a4f3d}.topic-badge--ice-activity-and-consequences{border-color:#5b5b5b}.edition-card h3{margin:.25rem 0 .35rem}.edition-source{margin:0 0 .7rem;font:700 .72rem/1.2 system-ui,sans-serif;letter-spacing:.04em;color:var(--muted)}.site-footer{align-items:center}@media(max-width:560px){.brand-mark{width:32px;height:32px}.brand-text{max-width:calc(100% - 2.25rem)}.brand-title{font-size:.92rem}.footer-mark{width:28px;height:28px}}"
    css += ".dispatch-card .card-actions{display:flex;flex-wrap:wrap;align-items:center;gap:.85rem;min-height:0;height:auto;padding:0;margin:0}.dispatch-card .card-actions a{display:inline-flex;align-items:center;flex:0 0 auto;min-height:0;height:auto;width:auto;max-width:100%;padding:0;margin:0;line-height:1.2}.dispatch-card .card-actions .button{display:inline-block;width:auto;min-height:0;height:auto;padding:.72rem 1.1rem}.dispatch-card .card-actions .text-link,.dispatch-card .card-actions .support-link{display:inline-flex;width:auto;min-height:0;height:auto;padding:0}.edition-source,.date-line{white-space:normal;overflow-wrap:anywhere}@media(max-width:800px){.dispatch-card .card-actions{flex-direction:row;align-items:center}.dispatch-card .card-actions .button{width:100%;flex:1 0 100%}.dispatch-card .card-actions .text-link,.dispatch-card .card-actions .support-link{width:auto;flex:0 0 auto}}"
    css += ".dispatch-card .card-actions .button,.dispatch-card .card-actions .button:visited{background:#1E3F4F;color:#fffdf8;border-color:#1E3F4F}.dispatch-card .card-actions .button:hover{background:#2F6F88;color:#fffdf8;border-color:#2F6F88}.dispatch-card .card-actions .button:focus,.dispatch-card .card-actions .button:focus-visible{background:#1E3F4F;color:#fffdf8;border-color:#1E3F4F;outline:3px solid #2F6F88;outline-offset:3px}.dispatch-card .card-actions .button:active{background:#0F2A33;color:#fffdf8;border-color:#0F2A33}"
    css += ".site-header{align-items:center;padding:.8rem 0 .75rem}.brand{display:flex;flex-direction:row;align-items:center;gap:.7rem}.brand-mark{width:44px;height:44px;object-fit:contain;flex:none}.footer-brand{display:flex;align-items:center;gap:.7rem;min-width:0}.footer-mark{width:36px;height:36px;object-fit:contain;flex:none}.edition-source{margin:0 0 .25rem}.edition-published{margin:0 0 .55rem;font:500 .72rem/1.3 system-ui,sans-serif;color:var(--muted)}.edition-provenance{margin:0 0 .55rem;font:500 .72rem/1.3 system-ui,sans-serif;color:var(--muted)}.edition-meta{margin:0}.topic-badge{padding:.34rem .65rem;margin-bottom:.85rem}@media(max-width:560px){.site-header,.site-footer,main{width:calc(100% - 1.5rem);max-width:calc(100% - 1.5rem)}.brand{width:100%;max-width:100%;flex:0 0 auto}.brand-mark{width:42px;height:42px}.brand-text{max-width:calc(100% - 3.2rem)}.brand-title{font-size:.9rem;line-height:1.1}.site-header nav{width:100%;max-width:100%;gap:.65rem 1rem}.actions{width:100%;max-width:100%}.actions>.button{width:100%;max-width:100%}.edition-grid{grid-template-columns:1fr;width:100%;max-width:100%}.edition-card{width:100%;max-width:100%;overflow-wrap:anywhere}.footer-mark{width:32px;height:32px}}"
    return css


def render_site_shell_stylesheet(source_root: Path) -> str:
    return stylesheet(source_root)



def render_about() -> str:
    body = '<section class="page-intro"><p class="eyebrow">The project</p><h1>About Blue Fern Dispatches</h1><p>Dispatches From The Blue Fern Co. is a source-based public dispatch system for reporting on systems, access, pressure, and accountability.</p><p>Gaza and Food Line are Active. Care Line is a Pilot. Cascadia is currently paused. Its latest public edition is May 5, 2026; its latest substantive development was published May 3, 2026, and its public archive remains available through May 31, 2026.</p></section>'
    return _page("About", body, "about")
