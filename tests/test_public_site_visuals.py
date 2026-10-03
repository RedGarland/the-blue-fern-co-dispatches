from __future__ import annotations

import base64
import shutil
from pathlib import Path

import bluefern_dispatches.generator as generator
from bluefern_dispatches.public_site_visuals import validate_public_site_visuals


GOOD_CSS = """
:root {
  --bf-dark-blue: #1E3F4F;
  --bf-background-cream: #EFE7DA;
  --bf-accent-blue-grey: #4E6B79;
  --bf-soft-steel-grey: #9BAEB5;
  --bf-pale-beige: #D9CEC0;
  --bf-white: #FFFFFF;
  --bf-text: #1E3F4F;
  --ink: var(--bf-text);
  --paper: var(--bf-background-cream);
  --muted: var(--bf-accent-blue-grey);
  --white: var(--bf-white);
  --line: var(--bf-pale-beige);
}
html, body { margin: 0; font-family: Georgia, serif; background: var(--bf-background-cream); color: var(--ink); }
.hero { min-height: 260px; padding: 48px; box-sizing: border-box; }
.section-block { padding: 32px 48px; }
.edition-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.edition-card, .dispatch-card { border: 1px solid var(--line); border-radius: 8px; padding: 20px; background: var(--white); box-shadow: 0 18px 45px rgba(30, 63, 79, 0.08); }
.active-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.actions, .card-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 0.85rem; }
.button, .button:visited { display: inline-block; background: var(--bf-dark-blue); color: var(--bf-white); border: 1px solid var(--bf-dark-blue); border-radius: 3px; padding: 0.72rem 1.1rem; font: 700 0.82rem/1.2 system-ui, sans-serif; text-decoration: none; }
.button:hover { background: var(--bf-accent-blue-grey); color: var(--bf-white); }
.button:focus, .button:focus-visible { outline: 3px solid var(--bf-soft-steel-grey); outline-offset: 3px; }
.text-link, .support-link { font: 700 0.82rem/1.2 system-ui, sans-serif; }
.support-link { color: var(--muted); }
.hero-logo { display: block; width: 260px; height: 160px; object-fit: contain; }
.archive--gaza .hero { min-height: 0; padding: 8px 48px 6px; }
.archive--gaza .hero-logo { width: 180px; height: 100px; object-fit: contain; }
.archive-latest { margin: 14px 0; padding: 14px; border: 1px solid var(--line); }
.archive-month { margin: 16px 0; }
.archive-list .archive-row { display: grid; grid-template-columns: 8rem 1fr auto; padding: 8px 0; border-top: 1px solid var(--line); }
.food-line-hero { display: grid; place-items: center; padding: 40px; min-height: 220px; }
.food-line-logo--edition, .food-line-logo--home { width: 260px; height: 120px; object-fit: contain; }
.food-line-source-card { border: 1px solid #c5d2d0; border-radius: 8px; padding: 18px; margin: 20px 0; }
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
            "DUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
    )


def _root_html(*, hero_class: str = "hero") -> str:
    return f"""<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"><title>Dispatches</title></head>
<body><main>
<section class="{hero_class}"><h1>Dispatches From The Blue Fern Co.</h1><p>Source-backed public briefings.</p><p class="actions"><a class="button" href="/dispatches/">View latest dispatches</a><a class="button button--quiet" href="/about/">Explore the public record</a></p></section>
<section class="section-block"><div class="section-heading"><p>The current edition desk</p><h2>Latest published developments</h2></div>
<div class="edition-grid">
<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-10-01/">Gaza latest public edition</a></h3></article>
<article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-10-01/">Bradford County Food Pantry running low on food, leaders say</a></h3></article>
<article class="edition-card edition-card--care-line"><h3><a href="/care-line/editions/2026-08-20/">Care latest public edition</a></h3></article>
</div></section>
<section class="section-block"><h2>Active dispatches</h2><div class="active-grid">
<article class="dispatch-card dispatch-card--featured"><h2>Dispatches From Gaza</h2><div class="card-actions"><a class="button" href="/gaza/editions/2026-10-01/">Read latest</a><a class="text-link" href="/gaza/archive.html">Archive</a><a class="support-link" href="/gaza/rss.xml">Feed</a></div></article>
<article class="dispatch-card dispatch-card--featured"><h2>Food Line Dispatch</h2><div class="card-actions"><a class="button" href="/food-line/editions/2026-10-01/">Read latest</a><a class="text-link" href="/food-line/archive.html">Archive</a><a class="support-link" href="/food-line/podcast.xml">Podcast</a></div></article>
<article class="dispatch-card dispatch-card--featured"><h2>The Care Line Dispatch</h2><div class="card-actions"><a class="button" href="/care-line/editions/2026-08-20/">Read latest</a><a class="text-link" href="/care-line/archive.html">Archive</a><a class="support-link" href="/care-line/rss.xml">Feed</a></div></article>
</div></section>
</main></body></html>"""


def _food_home_html() -> str:
    return """<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"><title>Food Line</title></head>
<body><main><section class="food-line-hero"><img class="food-line-logo food-line-logo--home" src="/assets/food-line-logo.png" alt="Food Line"><h1>Food Line Dispatch</h1></section>
<h2>Latest Briefing</h2><p><a href="editions/2026-10-01/">Read the latest briefing</a></p>
<article class="food-line-source-card"><h3>Bradford County Food Pantry running low on food, leaders say</h3></article>
<article class="food-line-source-card"><h3>Seniors in West LA facing long waitlist for Meals on Wheels</h3></article>
</main></body></html>"""


def _food_edition_html(*, story_count: int = 2, oversized_logo: bool = False) -> str:
    extra_class = " oversized-logo" if oversized_logo else ""
    stories = """
<article class='food-line-source-card'><h3>Bradford County Food Pantry running low on food, leaders say</h3><p>WCJB | TV20</p></article>
"""
    if story_count >= 2:
        stories += """
<article class='food-line-source-card'><h3>Seniors in West LA facing long waitlist for Meals on Wheels</h3><p>Spectrum News</p></article>
"""
    return f"""<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"><title>Food Line 2026-10-01</title></head>
<body><main><section class="hero food-line-hero"><img class="hero-logo food-line-logo food-line-logo--edition{extra_class}" src="/assets/food-line-logo.png" alt="Food Line">
<h1>Food Line Dispatch</h1><p>October 1, 2026</p></section>{stories}</main></body></html>"""


def _source_table_html(*, yes_count: int = 2) -> str:
    rows = "\n".join(
        f"<tr><td>item-{index}</td><td>Source {index}</td><td>Yes</td></tr>"
        for index in range(yes_count)
    )
    return f"<html><body><table><tr><th>ID</th><th>Title</th><th>Used on public page</th></tr>{rows}</table></body></html>"


def _simple_dispatch_html(title: str) -> str:
    return f'<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"></head><body><main><section class="hero"><h1>{title}</h1></section><p><a href="editions/2026-10-01/">Read latest</a></p></main></body></html>'


def _dispatch_logo_html(title: str) -> str:
    return f'<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"></head><body><main class="home"><section class="hero"><img class="hero-logo" src="/assets/food-line-logo.png" alt="{title}"></section><h2>Latest Briefing</h2><p><a href="editions/2026-10-01/">Read latest</a></p></main></body></html>'


def _gaza_archive_html() -> str:
    return """<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"><title>Gaza Archive</title></head>
<body><main class="archive archive--gaza"><section class="hero"><img class="hero-logo" src="/assets/food-line-logo.png" alt="Dispatches From Gaza"></section>
<p class="eyebrow">Archive</p><h1>Edition Archive</h1>
<section class="archive-latest" aria-label="Latest archive entry"><p class="eyebrow">Latest entry</p><p class="archive-latest-date">2026-10-01</p><h2>Daily briefing</h2><p>Daily Gaza briefing</p><a class="button" href="editions/2026-10-01/">Read latest</a></section>
<section class="archive-month"><h2>October 2026</h2><ul class="edition-list archive-list">
<li class="archive-row archive-row--daily"><span class="edition-date">2026-10-01</span><a href="editions/2026-10-01/">Daily briefing</a></li>
</ul></section>
<section class="archive-month"><h2>September 2026</h2><ul class="edition-list archive-list">
<li class="archive-row archive-row--no-update"><span class="edition-date">2026-09-30</span><span class="no-update-label">No qualifying update</span><span class="archive-row-note">22 sources checked</span></li>
</ul></section>
</main></body></html>"""


def _make_pages_root(tmp_path: Path, *, css: str = GOOD_CSS, story_count: int = 2, source_yes_count: int = 2, oversized_logo: bool = False) -> Path:
    root = tmp_path / "pages"
    if css:
        _write(root / "assets" / "site.css", css)
    _write_png(root / "assets" / "food-line-logo.png")
    _write(root / "index.html", _root_html())
    _write(root / "dispatches" / "index.html", _root_html())
    _write(root / "food-line" / "index.html", _food_home_html())
    food_edition = root / "food-line" / "editions" / "2026-10-01"
    _write(food_edition / "index.html", _food_edition_html(story_count=story_count, oversized_logo=oversized_logo))
    _write(food_edition / "source_table.html", _source_table_html(yes_count=source_yes_count))
    _write(food_edition / "edition_manifest.json", '{"story_count": 2, "source_count": 2}')
    _write(root / "gaza" / "index.html", _simple_dispatch_html("Dispatches From Gaza"))
    _write(root / "gaza" / "archive.html", _gaza_archive_html())
    _write(root / "care-line" / "index.html", _simple_dispatch_html("The Care Line Dispatch"))
    return root


def _issue_checks(result: dict) -> set[str]:
    return {str(issue["check"]) for issue in result.get("issues") or []}


def test_current_repaired_public_site_fixture_passes(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)

    result = validate_public_site_visuals(root, screenshot_dir=tmp_path / "screenshots")

    assert result["ok"] is True
    assert result["html_metrics"]["expected_food_story_count"] == 2
    assert result["html_metrics"]["rendered_food_story_count"] == 2
    assert result["screenshots"]


def test_missing_css_plain_fallback_homepage_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path, css="")

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "stylesheet" in _issue_checks(result)


def test_excessive_homepage_hero_height_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path, css=GOOD_CSS + "\n.hero { height: 920px; }\n")

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "hero_height" in _issue_checks(result)


def test_homepage_latest_desk_below_first_viewport_fails(tmp_path: Path) -> None:
    css = GOOD_CSS + "\n.edition-grid { margin-top: 860px; }\n"
    root = _make_pages_root(tmp_path, css=css)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "latest_desk" in _issue_checks(result)


def test_cold_body_background_token_fails(tmp_path: Path) -> None:
    css = GOOD_CSS.replace("--bf-background-cream: #EFE7DA", "--bf-background-cream: #F7F8F4")
    root = _make_pages_root(tmp_path, css=css)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "body_background" in _issue_checks(result)


def test_missing_canonical_palette_token_fails(tmp_path: Path) -> None:
    css = GOOD_CSS.replace("  --bf-pale-beige: #D9CEC0;\n", "")
    root = _make_pages_root(tmp_path, css=css)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "canonical_tokens" in _issue_checks(result)


def test_low_contrast_primary_button_fails(tmp_path: Path) -> None:
    css = GOOD_CSS + "\n.button, .button:visited { background: #1E3F4F; color: #172126; }\n"
    root = _make_pages_root(tmp_path, css=css)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "primary_button_contrast" in _issue_checks(result)


def test_broken_required_route_image_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)
    (root / "assets" / "food-line-logo.png").unlink()

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "broken_image" in _issue_checks(result)


def test_latest_card_active_desk_order_fails_when_gaza_drops_from_first_row(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)
    wrong_order = _root_html().replace(
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-10-01/">Gaza latest public edition</a></h3></article>\n'
        '<article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-10-01/">Bradford County Food Pantry running low on food, leaders say</a></h3></article>\n'
        '<article class="edition-card edition-card--care-line"><h3><a href="/care-line/editions/2026-08-20/">Care latest public edition</a></h3></article>',
        '<article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-10-01/">Bradford County Food Pantry running low on food, leaders say</a></h3></article>\n'
        '<article class="edition-card edition-card--care-line"><h3><a href="/care-line/editions/2026-08-20/">Care latest public edition</a></h3></article>\n'
        '<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-10-01/">Gaza latest public edition</a></h3></article>',
    )
    _write(root / "index.html", wrong_order)
    _write(root / "dispatches" / "index.html", wrong_order)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "latest_card_active_desk_order" in _issue_checks(result)


def test_cramped_active_dispatch_action_links_fail(tmp_path: Path) -> None:
    css = GOOD_CSS + "\n.card-actions { gap: 2px; }\n"
    root = _make_pages_root(tmp_path, css=css)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "active_dispatch_action_links" in _issue_checks(result)


def test_food_landing_duplicate_latest_title_link_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)
    duplicate = """<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"></head>
<body><main><section class="food-line-hero"><img class="food-line-logo food-line-logo--home" src="/assets/food-line-logo.png" alt="Food Line"><h1>Food Line Dispatch</h1></section>
<section class="food-line-panel"><h2>Latest Briefing</h2>
<h3><a href="editions/2026-10-01/">Bradford County Food Pantry running low on food, leaders say</a></h3>
<div class="food-line-actions"><a href="editions/2026-10-01/">Read briefing</a></div>
</section>
<section class="food-line-panel"><h2>Recent Editions</h2>
<ul class="food-line-recent-list"><li><a class="food-line-recent-title" href="editions/2026-10-01/">Bradford County Food Pantry running low on food, leaders say</a></li></ul>
</section></main></body></html>"""
    _write(root / "food-line" / "index.html", duplicate)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "food_latest_duplicate" in _issue_checks(result)


def test_gaza_recent_editions_duplicate_adjacent_date_link_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)
    duplicate = """<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"></head>
<body><main class="home"><section class="hero"><img class="hero-logo" src="/assets/food-line-logo.png" alt="Dispatches From Gaza"></section>
<h2>Latest Briefing</h2><p><a href="editions/2026-10-01/">Read the latest briefing</a></p>
<h2>Recent Editions</h2>
<ul class="edition-list"><li><span class="edition-date">2026-10-01</span><a href="editions/2026-10-01/">2026-10-01</a></li></ul>
</main></body></html>"""
    _write(root / "gaza" / "index.html", duplicate)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "gaza_recent_duplicate_date_link" in _issue_checks(result)


def test_gaza_recent_editions_jammed_no_update_date_text_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)
    duplicate = """<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"></head>
<body><main class="home"><section class="hero"><img class="hero-logo" src="/assets/food-line-logo.png" alt="Dispatches From Gaza"></section>
<h2>Recent Editions</h2>
<ul class="edition-list"><li class="no-update"><span class="edition-date">2026-09-28</span><span class="no-update-label">No update</span></li></ul>
</main></body></html>"""
    _write(root / "gaza" / "index.html", duplicate)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "gaza_recent_no_update_mislabeled" in _issue_checks(result)


def test_gaza_no_update_checks_without_recent_checks_section_fail(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path)
    duplicate = """<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"></head>
<body><main class="home"><section class="hero"><img class="hero-logo" src="/assets/food-line-logo.png" alt="Dispatches From Gaza"></section>
<h2>Latest Briefing</h2><p><a href="editions/2026-09-30/">Read latest</a></p>
<ul class="edition-list"><li class="no-update-check"><span class="edition-date">2026-10-02</span><span class="no-update-label">No qualifying update</span><span class="archive-row-note">22 sources checked</span></li></ul>
</main></body></html>"""
    _write(root / "gaza" / "index.html", duplicate)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "gaza_recent_checks_section" in _issue_checks(result)


def test_gaza_archive_logo_splash_or_rows_below_viewport_fails(tmp_path: Path) -> None:
    css = GOOD_CSS + "\n.archive--gaza .hero-logo { width: 540px; height: 360px; }\n.archive-latest { margin-top: 520px; }\n.archive-month { margin-top: 640px; }\n"
    root = _make_pages_root(tmp_path, css=css)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    checks = _issue_checks(result)
    assert "gaza_archive_logo_size" in checks
    assert "gaza_archive_first_viewport" in checks


def test_product_landing_oversized_logo_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path, css=GOOD_CSS + "\n.home .hero-logo { width: 760px; height: 420px; }\n")
    _write(root / "gaza" / "index.html", _dispatch_logo_html("Dispatches From Gaza"))

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "product_logo_size" in _issue_checks(result)


def test_food_edition_oversized_logo_proof_layout_fails(tmp_path: Path) -> None:
    css = GOOD_CSS + "\n.food-line-logo--edition.oversized-logo { width: 760px; height: 360px; }\n"
    root = _make_pages_root(tmp_path, css=css, oversized_logo=True)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "food_edition_logo" in _issue_checks(result)


def test_food_rendered_story_count_mismatch_fails(tmp_path: Path) -> None:
    root = _make_pages_root(tmp_path, story_count=1, source_yes_count=2)

    result = validate_public_site_visuals(root)

    assert result["ok"] is False
    assert "food_story_count" in _issue_checks(result)


def test_pages_publish_blocks_commit_when_visual_validation_fails(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "repo"
    work.mkdir()
    pages_repo = _make_pages_root(tmp_path / "pages-fixture")
    site_root = work / "output" / "site"
    shutil.copytree(pages_repo, site_root)

    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: {"ok": True, "warnings": [], "errors": [], "backfilled_public_editions": []})
    monkeypatch.setattr(generator, "validate_pages_publish", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(generator, "validate_pages_repo_copy_scope", lambda *args, **kwargs: [])
    monkeypatch.setattr(generator, "validate_pages_copy_parity", lambda *args, **kwargs: [])
    monkeypatch.setattr(generator, "validate_pages_repo_after_copy", lambda *args, **kwargs: [])
    monkeypatch.setattr(generator, "_gaza_homepage_recent_edition_guard", lambda *args, **kwargs: {"ok": True, "decision": "allowed", "reasons": []})
    monkeypatch.setattr(generator, "_gaza_public_surface_history_diagnostics", lambda *args, **kwargs: [])
    monkeypatch.setattr(generator, "refresh_shared_release_surfaces_from_pages_inventory", lambda *args, **kwargs: {"ok": True, "changed_surfaces": []})
    monkeypatch.setattr(
        generator,
        "validate_public_site_visuals",
        lambda *args, **kwargs: {
            "ok": False,
            "issues": [{"page": "/", "check": "stylesheet", "message": "shared stylesheet missing"}],
        },
    )

    def fail_commit(*args, **kwargs):
        raise AssertionError("Pages commit must not run after visual validation failure")

    monkeypatch.setattr(generator, "maybe_commit_pages_repo", fail_commit)

    result = generator.publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=True,
        no_push=True,
        backup_root=work / "backup",
    )

    assert result["ok"] is False
    assert result["committed"] is False
    assert "public site visual validation failed for / [stylesheet]" in "\n".join(result["errors"])
