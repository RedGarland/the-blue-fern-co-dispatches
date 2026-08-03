from pathlib import Path

import json
import re

from bluefern_dispatches.phase1_site import (
    PHASE1A_ROUTES,
    _edition_card,
    PUBLIC_STATUSES,
    public_editions,
    public_model,
    render_phase1a_site,
    render_site,
)
from bluefern_dispatches.public_site_shell import stylesheet, render_dispatch_landing


ROOT = Path(__file__).resolve().parents[1]


def test_phase1_model_uses_public_editions_only():
    dispatches, editions = public_model(ROOT / "output" / "site")
    assert {item.status for item in dispatches} <= set(PUBLIC_STATUSES)
    assert next(item for item in dispatches if item.slug == "cascadia").status == "Paused"
    assert editions
    assert all("review" not in str(item.url) for item in editions)


def test_phase1a_homepage_uses_real_dispatch_destinations_and_excludes_dispatch_scope(tmp_path):
    output = tmp_path / "phase1a"
    site_root = ROOT / "output" / "site"
    result = render_phase1a_site(site_root, output)
    homepage = (output / "index.html").read_text(encoding="utf-8")
    assert "/care-line/" in homepage
    assert "/cascadia/archive.html" in homepage
    assert not any(
        bad in homepage
        for bad in (
            "/care-line/editions/2026-05-23/",
            "/cascadia/editions/2026-05-04/",
            "/cascadia/editions/2026-05-05/",
        )
    )
    assert result["routes"] == list(PHASE1A_ROUTES)
    assert result["dispatch_owned_paths"] == []
    assert result["private_paths"] == []
    assert "output/detail" not in homepage
    assert "output/paid" not in homepage
    latest_section = homepage.split("Latest published developments", 1)[1].split("Reporting now", 1)[0]
    assert "No current update" not in latest_section
    assert all((site_root / route.lstrip("/")).exists() for route in ("/care-line/", "/cascadia/archive.html"))
    assert {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()} == {
        "index.html",
        "dispatches/index.html",
        "methodology/index.html",
        "about/index.html",
        "assets/site.css",
        "assets/bluefern.png",
            "assets/bluefern-mark.png",
        "assets/dispatches-from-blue-fern-co.png",
    }


def test_phase1a_homepage_card_actions_resolve_against_public_site(tmp_path):
    output = tmp_path / "phase1a"
    (output / "assets").mkdir(parents=True)
    (output / "assets" / "bluefern.ico").write_bytes(b"confirmed branding fixture")
    site_root = ROOT / "output" / "site"
    render_phase1a_site(site_root, output)
    homepage = (output / "index.html").read_text(encoding="utf-8")
    for href in re.findall(r'href="([^"]+)"', homepage):
        if href.startswith(("http:", "https:", "#")):
            continue
        target_root = output if href in ("/", "/dispatches/", "/methodology/", "/about/") or href.startswith("/assets/") else site_root
        target = target_root / href.lstrip("/")
        if href.startswith("/assets/") and not target.exists():
            target = site_root / href.lstrip("/")
        if href.endswith("/"):
            target = target / "index.html"
        assert target.exists(), href


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
    assert "Jun 20, 2026" in homepage or "June 20, 2026" in homepage
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
    assert "overflow-x:visible" in css
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
    assert "Based on public source reporting" in homepage
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


def test_phase1b_root_shell_removes_card_rules_and_adds_branding(tmp_path):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    (root / "assets" / "bluefern.ico").write_bytes(b"confirmed branding fixture")
    output = tmp_path / "phase1b"
    render_phase1a_site(root, output)
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.html"))
    css = (output / "assets" / "site.css").read_text(encoding="utf-8")
    assert "card-rule" not in rendered
    assert ".card-rule" not in css
    assert 'src="/assets/bluefern-mark.png"' in rendered
    assert 'href="/assets/bluefern.ico"' in rendered
    assert ".brand:before" not in css
    assert (output / "assets" / "bluefern.ico").exists()
    assert (output / "assets" / "bluefern-mark.png").exists()


def test_phase1b_topic_badges_are_textual_and_slug_derived():
    from bluefern_dispatches.phase1_site import TOPIC_LABELS, _edition_card, Edition

    for slug, label in TOPIC_LABELS.items():
        card = _edition_card(Edition(slug, "2026-07-31", f"/{slug}/", "A visible headline", "Published", 1, None, 1))
        assert f'class="edition-card edition-card--{slug}"' in card
        assert f'class="topic-badge topic-badge--{slug}"' in card
        assert f">{label}</p>" in card
        assert card.index("topic-badge") < card.index("<h3>")
    assert "topic-badge" in _edition_card(Edition("gaza", "2026-07-31", "/gaza/", "A visible headline", "Published", 1, None, 1))


def test_phase1b_currentness_dates_remain_approved(tmp_path):
    root = tmp_path / "current-public-site"
    for slug, edition_date in (("gaza", "2026-07-23"), ("food-line", "2026-07-31"), ("care-line", "2026-07-22")):
        edition = root / slug / "editions" / edition_date
        edition.mkdir(parents=True)
        (edition / "index.html").write_text("<h3>Approved public development</h3>", encoding="utf-8")
        (edition / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "story_count": 1}), encoding="utf-8")
    dispatches, recent = public_model(root)
    latest = {item.slug: item for item in dispatches if item.latest}
    assert latest["gaza"].latest.date == "2026-07-23"
    assert latest["food-line"].latest.date == "2026-07-31"
    assert latest["care-line"].latest.date == "2026-07-22"
    assert {item.slug for item in recent if item.substantive} >= {"gaza", "food-line"}



def test_phase1c_visible_branding_and_eyebrow_star_removal(tmp_path):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    (root / "assets" / "bluefern.png").write_bytes(b"png")
    (root / "assets" / "bluefern.ico").write_bytes(b"ico")
    output = tmp_path / "phase1c"
    render_phase1a_site(root, output)
    homepage = (output / "index.html").read_text(encoding="utf-8")
    css = (output / "assets" / "site.css").read_text(encoding="utf-8")
    assert homepage.count('src="/assets/bluefern-mark.png"') >= 2
    assert 'href="/assets/bluefern.ico"' in homepage
    assert 'class="hero-mark"' not in homepage
    assert ".brand:before" not in css
    assert "content:'?'" not in css


def test_phase1c_timestamp_and_metadata_hierarchy(tmp_path):
    root = tmp_path / "site"
    edition = root / "gaza" / "editions" / "2026-08-03"
    edition.mkdir(parents=True)
    (edition / "index.html").write_text("<h3>Middle East crisis live: unrelated headline</h3>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "story_count": 4, "actual_run_local_time": "2026-08-03T06:00:57-07:00"}), encoding="utf-8")
    (edition / "curation_manifest.json").write_text(json.dumps([
        {"title": "Middle East crisis live: unrelated headline", "substantive_ground": True, "core_ground_development": True, "score": 99},
        {"title": "Gaza public development title", "substantive_ground": True, "core_ground_development": True, "score": 50},
    ]), encoding="utf-8")
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    item = public_editions(root, "gaza")[0]
    card = _edition_card(item)
    assert item.headline == "Gaza public development title"
    assert "Aug 3, 2026 &middot; 6:00 AM PT" in card
    assert "Middle East crisis live" not in card
    assert card.index("topic-badge") < card.index("<h3>") < card.index("edition-source") < card.index("edition-provenance") < card.index("edition-meta")
    assert "Published public development" not in card



def test_phase1c_source_count_grammar_and_separate_publication_date():
    from bluefern_dispatches.phase1_site import Edition, _edition_card, _source_count_label

    assert _source_count_label(1) == "1 public source"
    assert _source_count_label(2) == "2 public sources"
    card = _edition_card(Edition("food-line", "2026-07-31", "/food-line/editions/2026-07-31/", "Food pressure", "Published", 1, None, 1, published_at="2026-08-01T10:21:30-07:00"))
    assert "July 31, 2026 edition" in card
    assert "Published Aug 1, 2026 &middot; 10:21 AM PT" in card
    assert "1 public source" in card
    assert "1 public sources" not in card


def test_phase1c_no_update_care_line_is_not_a_development():
    from bluefern_dispatches.phase1_site import Dispatch, Edition, _dispatch_card

    item = Dispatch("care-line", "The Care Line Dispatch", "Pilot", "Healthcare access", "Pilot publication", "/care-line/", "/care-line/archive.html", Edition("care-line", "2026-06-19", "/care-line/", "No current update", "No Update", 177, None, 0, no_update=True))
    card = _dispatch_card(item, compact=True)
    assert "Latest public edition" in card
    assert "No current update" in card
    assert "Latest public development" not in card


def test_phase1c_american_pressure_uses_public_supporting_count(tmp_path):
    root = tmp_path / "site"
    edition = root / "american-pressure" / "editions" / "2026-06-13"
    edition.mkdir(parents=True)
    (edition / "index.html").write_text("<h3>SNAP theft is rising</h3>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(json.dumps({"public_rendered": True, "source_count": 407, "story_count": 8}), encoding="utf-8")
    (edition / "curation_manifest.json").write_text(json.dumps({"stories": [{"title": "SNAP theft is rising", "public_source_record_ids": ["a", "b", "c", "d"]}]}), encoding="utf-8")
    item = public_editions(root, "american-pressure")[0]
    assert item.source_count == 4
    assert "407 public sources" not in _edition_card(item)


def test_phase1c_cascadia_public_edition_matches_archive_boundary():
    from bluefern_dispatches.public_site_shell import render_about

    about = render_about()
    assert "Cascadia is currently paused" in about
    assert "through May 31, 2026" in about
    assert "no currently operating weekly publication task" not in about



def test_phase1c_original_brand_asset_and_favicon_are_preserved():
    import hashlib
    original = ROOT / "assets" / "bluefern.png"
    favicon = ROOT / "assets" / "bluefern.ico"
    assert original.exists()
    assert hashlib.sha256(original.read_bytes()).hexdigest() == "b7b600bf5e87af4ad037703fc75201df4c52bebfc5616f32c2369d14c269ae54"
    assert favicon.exists()

def test_phase1c_root_card_actions_override_legacy_dimensions(tmp_path):
    root = tmp_path / "site"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "site.css").write_text("", encoding="utf-8")
    css = stylesheet(root)
    for selector in (".dispatch-card .card-actions a", ".dispatch-card .card-actions .button", ".dispatch-card .card-actions .text-link", ".dispatch-card .card-actions .support-link"):
        assert selector in css
    assert ".dispatch-card .card-actions{display:flex" in css
    assert "min-height:0;height:auto" in css


def test_phase1c_safe_middle_dot_and_cascadia_description():
    from bluefern_dispatches.phase1_site import Dispatch, Edition, _dispatch_card
    card = _dispatch_card(Dispatch("gaza", "Dispatches From Gaza", "Active", "", "Daily", "/gaza/", "/gaza/archive.html", Edition("gaza", "2026-08-03", "/gaza/editions/2026-08-03/", "Gaza title", "Published", 4, None, 1, published_at="2026-08-03T06:00:57-07:00")))
    assert "&middot;" in card
    assert " ? " not in card
    from bluefern_dispatches.public_site_shell import render_about
    about = render_about()
    assert "latest public edition is May 5, 2026" in about
    assert "latest substantive development was published May 3, 2026" in about
    assert "archive remains available through May 31, 2026" in about
