from pathlib import Path

import json

from bluefern_dispatches.phase1_site import PUBLIC_STATUSES, public_editions, public_model, render_site
from bluefern_dispatches.public_site_shell import stylesheet, render_dispatch_landing


ROOT = Path(__file__).resolve().parents[1]


def test_phase1_model_uses_public_editions_only():
    dispatches, editions = public_model(ROOT / "output" / "site")
    assert {item.status for item in dispatches} <= set(PUBLIC_STATUSES)
    assert next(item for item in dispatches if item.slug == "cascadia").status == "Paused"
    assert editions
    assert all("review" not in str(item.url) for item in editions)


def test_phase1_preview_contains_real_navigation_and_no_private_roots(tmp_path):
    output = tmp_path / "preview"
    result = render_site(ROOT / "output" / "site", output)
    assert result["dispatches"] == 6
    homepage = (output / "index.html").read_text(encoding="utf-8")
    assert "/methodology/" in homepage
    assert "/about/" in homepage
    assert "Active dispatches" in homepage
    assert "Paused / archived work" in homepage
    assert "Cascadia" in homepage
    assert "June 20, 2026" in homepage
    assert "· 2026-06-20</p>" not in homepage
    assert "output/detail" not in homepage
    assert (output / "methodology" / "index.html").exists()


def test_newer_public_edition_wins_over_older_fixture(tmp_path):
    root = tmp_path / "site"
    for edition_date, title in (("2026-06-20", "Older public item"), ("2026-07-31", "Newer public item")):
        directory = root / "food-line" / "editions" / edition_date
        directory.mkdir(parents=True)
        (directory / "index.html").write_text(f"<h1>Food Line</h1><h3>{title}</h3>", encoding="utf-8")
        (directory / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "story_count": 1}), encoding="utf-8")
    assert public_editions(root, "food-line")[0].date == "2026-07-31"
    assert public_editions(root, "food-line")[0].headline == "Newer public item"


def test_no_update_and_mojibake_are_excluded_from_production_preview(tmp_path):
    output = tmp_path / "preview"
    render_site(ROOT / "output" / "site", output)
    html_files = list(output.rglob("*.html"))
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in html_files)
    assert "PRIVATE PHASE 1 PROTOTYPE" not in rendered
    assert not any(marker in rendered for marker in ("Ã‚", "Ãƒ", "ï¿½", "�"))
    assert all((output / route).exists() for route in ("index.html", "dispatches/index.html", "methodology/index.html", "about/index.html"))


def test_recovered_responsive_shell_rules_and_button_contrast(tmp_path):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    css = stylesheet(root)
    assert "overflow-x:hidden" in css
    assert "flex-wrap:wrap" in css
    assert "flex-direction:column" in css
    assert "grid-template-columns:1fr" in css
    assert "img{display:block;max-width:100%" in css
    assert "background:#1E3F4F;color:#fffdf8" in css


def test_food_line_latest_date_url_and_no_rss_link(tmp_path):
    root = tmp_path / "site"
    food = root / "food-line"
    (food / "editions" / "2026-07-31").mkdir(parents=True)
    (food / "index.html").write_text("<h1>Food Line</h1>", encoding="utf-8")
    (food / "archive.html").write_text("<h1>Archive</h1>", encoding="utf-8")
    (food / "editions" / "2026-07-31" / "index.html").write_text("<h3>Superior food pantry closes after more than 30 years</h3>", encoding="utf-8")
    (food / "editions" / "2026-07-31" / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "story_count": 1}), encoding="utf-8")
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    landing = render_dispatch_landing(root, "food-line")
    assert "July 31, 2026" in landing
    assert "/food-line/editions/2026-07-31/" in landing
    assert "/food-line/rss.xml" not in landing


def test_public_source_headline_and_no_update_exclusion(tmp_path):
    root = tmp_path / "site"
    for slug in ("gaza", "food-line"):
        (root / slug / "editions" / "2026-07-31").mkdir(parents=True)
        (root / slug / "index.html").write_text("<h1>Dispatch</h1>", encoding="utf-8")
        (root / slug / "editions" / "2026-07-31" / "index.html").write_text("<h3>Visible public headline</h3>", encoding="utf-8")
        (root / slug / "editions" / "2026-07-31" / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "story_count": 1}), encoding="utf-8")
    (root / "gaza" / "editions" / "2026-07-30").mkdir(parents=True)
    (root / "gaza" / "editions" / "2026-07-30" / "index.html").write_text("<h3>No current update</h3>", encoding="utf-8")
    (root / "gaza" / "editions" / "2026-07-30" / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "edition_mode": "no_update", "story_count": 0}), encoding="utf-8")
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    rendered = render_site(root, tmp_path / "preview")
    homepage = (tmp_path / "preview" / "index.html").read_text(encoding="utf-8")
    assert "Public source headline" in homepage
    assert "No current update" not in homepage


def test_care_line_logo_path_is_canonical(tmp_path):
    root = tmp_path / "site"
    (root / "care-line" / "editions" / "2026-07-22").mkdir(parents=True)
    (root / "care-line" / "index.html").write_text("<h1>Care Line</h1>", encoding="utf-8")
    (root / "care-line" / "archive.html").write_text("<h1>Archive</h1>", encoding="utf-8")
    (root / "care-line" / "editions" / "2026-07-22" / "index.html").write_text("<h3>Care access signal</h3>", encoding="utf-8")
    (root / "care-line" / "editions" / "2026-07-22" / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "story_count": 1}), encoding="utf-8")
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    landing = render_dispatch_landing(root, "care-line")
    assert 'src="assets/care-line-logo.png"' in landing