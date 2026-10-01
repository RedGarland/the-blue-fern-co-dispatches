from __future__ import annotations

import shutil
from pathlib import Path

import bluefern_dispatches.generator as generator
from bluefern_dispatches.public_site_visuals import validate_public_site_visuals


GOOD_CSS = """
body { margin: 0; font-family: Georgia, serif; }
.hero { min-height: 260px; padding: 48px; box-sizing: border-box; }
.section-block { padding: 32px 48px; }
.edition-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.edition-card, .dispatch-card { border: 1px solid #c5d2d0; border-radius: 8px; padding: 20px; background: #fff; }
.active-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }
.food-line-hero { display: grid; place-items: center; padding: 40px; min-height: 220px; }
.food-line-logo--edition, .food-line-logo--home { width: 320px; height: 120px; object-fit: contain; }
.food-line-source-card { border: 1px solid #c5d2d0; border-radius: 8px; padding: 18px; margin: 20px 0; }
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _root_html(*, hero_class: str = "hero") -> str:
    return f"""<!doctype html><html><head><link rel="stylesheet" href="/assets/site.css"><title>Dispatches</title></head>
<body><main>
<section class="{hero_class}"><h1>Dispatches From The Blue Fern Co.</h1><p>Source-backed public briefings.</p></section>
<section class="section-block"><div class="section-heading"><p>The current edition desk</p><h2>Latest published developments</h2></div>
<div class="edition-grid">
<article class="edition-card edition-card--food-line"><h3><a href="/food-line/editions/2026-10-01/">Bradford County Food Pantry running low on food, leaders say</a></h3></article>
<article class="edition-card edition-card--gaza"><h3><a href="/gaza/editions/2026-10-01/">Gaza latest public edition</a></h3></article>
<article class="edition-card edition-card--care-line"><h3><a href="/care-line/editions/2026-08-20/">Care latest public edition</a></h3></article>
</div></section>
<section class="section-block"><h2>Active dispatches</h2><div class="active-grid">
<article class="dispatch-card dispatch-card--featured"><h2>Dispatches From Gaza</h2><a href="/gaza/editions/2026-10-01/">Read latest</a></article>
<article class="dispatch-card dispatch-card--featured"><h2>Food Line Dispatch</h2><a href="/food-line/editions/2026-10-01/">Read latest</a></article>
<article class="dispatch-card dispatch-card--featured"><h2>The Care Line Dispatch</h2><a href="/care-line/editions/2026-08-20/">Read latest</a></article>
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


def _make_pages_root(tmp_path: Path, *, css: str = GOOD_CSS, story_count: int = 2, source_yes_count: int = 2, oversized_logo: bool = False) -> Path:
    root = tmp_path / "pages"
    if css:
        _write(root / "assets" / "site.css", css)
    _write(root / "assets" / "food-line-logo.png", b"fake".decode("ascii"))
    _write(root / "index.html", _root_html())
    _write(root / "dispatches" / "index.html", _root_html())
    _write(root / "food-line" / "index.html", _food_home_html())
    food_edition = root / "food-line" / "editions" / "2026-10-01"
    _write(food_edition / "index.html", _food_edition_html(story_count=story_count, oversized_logo=oversized_logo))
    _write(food_edition / "source_table.html", _source_table_html(yes_count=source_yes_count))
    _write(food_edition / "edition_manifest.json", '{"story_count": 2, "source_count": 2}')
    _write(root / "gaza" / "index.html", _simple_dispatch_html("Dispatches From Gaza"))
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
