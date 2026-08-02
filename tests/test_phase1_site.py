from pathlib import Path

import json

from bluefern_dispatches.phase1_site import PUBLIC_STATUSES, public_editions, public_model, render_site


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
