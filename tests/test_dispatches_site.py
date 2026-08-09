import json
import shutil
import re
import subprocess
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import bluefern_dispatches.generator as generator
from bluefern_dispatches.generator import (
    BASE_URL,
    CASCADIA_LOGO_ASSET,
    CASCADIA_PUBLIC_DESCRIPTION,
    CNAME_VALUE,
    DEFAULT_BACKUP_ROOT,
    DispatchConfig,
    FAVICON_ASSETS,
    ROOT_DESCRIPTION,
    ROOT_MASTHEAD_ASSET,
    SourceRecord,
    StoryRecord,
    build_site,
    ensure_public_detail_separation,
    publish_pages,
    normalize_expect_dispatches,
    normalize_only_dispatches,
    public_edition_subtitle,
    seed_dispatches,
    validate_pages_repo_copy_scope,
    validate_pages_publish,
    validate_pages_repo_after_copy,
    validate_traceability,
)


def add_cascadia_dispatch_edition(work: Path, edition_date: str) -> None:
    end = date.fromisoformat(edition_date)
    start = end - timedelta(days=6)
    coverage_label = f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}, {end.year}"
    edition = work / "output" / "dispatches" / "cascadia" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text(
        f'''<!doctype html>
<html>
<head>
  <link rel="stylesheet" href="../../assets/site.css">
</head>
<body>
  <nav><a href="/">Dispatches Home</a><a href="/cascadia/">The Cascadia Briefing</a></nav>
  <main class="briefing"><a href="{BASE_URL}/cascadia/editions/{edition_date}/">The Cascadia Briefing</a><img src="../../assets/{CASCADIA_LOGO_ASSET}"></main>
</body>
</html>''',
        encoding="utf-8",
    )
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "cascadia",
                "edition_date": edition_date,
                "briefing_type": "weekly",
                "cadence": "weekly",
                "edition_type": "weekly",
                "coverage_start": start.isoformat(),
                "coverage_end": edition_date,
                "coverage_label": coverage_label,
                "public_coverage_label": coverage_label,
                "public_coverage_range": {
                    "coverage_start": start.isoformat(),
                    "coverage_end": edition_date,
                },
                "week_label": f"{end.isocalendar().year}-W{end.isocalendar().week:02d}",
                "source_count": 1,
                "story_count": 1,
                "source_manifest_path": str(edition / "sources_manifest.json"),
                "curation_manifest_path": str(edition / "curation_manifest.json"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_id": "src-001", "title": "Source", "url": "https://example.com/source"}]),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "story-001", "source_ids": ["src-001"]}]),
        encoding="utf-8",
    )


def copy_repo_assets(repo: Path, work: Path) -> None:
    assets_root = repo / "assets"
    work_assets = work / "assets"
    work_assets.mkdir(parents=True, exist_ok=True)
    for asset in assets_root.iterdir():
        if asset.is_file():
            (work_assets / asset.name).write_bytes(asset.read_bytes())


def copy_care_line_data(repo: Path, work: Path) -> None:
    source_root = repo / "data" / "dispatches" / "care-line"
    target_root = work / "data" / "dispatches" / "care-line"
    shutil.copytree(source_root, target_root)


def copy_tree_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)


@pytest.fixture(scope="session")
def built_site_template(tmp_path_factory):
    import os

    repo = Path(__file__).parent.parent
    test_root = tmp_path_factory.mktemp("dispatches-site-template")
    work = test_root / "repo"
    copy_repo_assets(repo, work)
    copy_care_line_data(repo, work)
    backup_root = test_root / "dispatches-bluefern-backups"
    previous_seed = os.environ.get("BLUEFERN_SEED_EDITION_DATE")
    os.environ["BLUEFERN_SEED_EDITION_DATE"] = "2026-05-03"
    try:
        add_cascadia_dispatch_edition(work, "2026-05-03")
        result = build_site(
            work,
            dry_run=False,
            backup_root=backup_root,
            dispatch_seed_dates={"care-line": "2026-05-23"},
        )
        yield work, backup_root, result
    finally:
        if previous_seed is None:
            os.environ.pop("BLUEFERN_SEED_EDITION_DATE", None)
        else:
            os.environ["BLUEFERN_SEED_EDITION_DATE"] = previous_seed


@pytest.fixture()
def built_site(tmp_path_factory, built_site_template):
    template_work, template_backup_root, result = built_site_template
    test_root = tmp_path_factory.mktemp("dispatches-site-copy")
    work = test_root / "repo"
    backup_root = test_root / "dispatches-bluefern-backups"
    shutil.copytree(template_work, work)
    shutil.copytree(template_backup_root, backup_root)
    return work, backup_root, result


def read(path):
    return path.read_text(encoding="utf-8")


def assert_favicon_links(html):
    expected = [
        '<link rel="icon" href="/assets/favicon.ico" sizes="any">',
        '<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png">',
        '<link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png">',
        '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">',
    ]
    for tag in expected:
        assert tag in html
    assert "href=\"assets/favicon" not in html
    assert "href=\"../../assets/favicon" not in html


def test_landing_page_links_and_blue_fern_scheme(built_site):
    work, _, result = built_site
    index = work / "output" / "site" / "index.html"
    css = work / "output" / "site" / "assets" / "site.css"

    assert result["ok"] is True
    assert index.exists()
    html = read(index)
    nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert nav == (
        '<a href="/gaza/">Gaza</a>'
        '<a href="/food-line/">Food Line Dispatch</a>'
        '<a href="/care-line/">The Care Line Dispatch</a>'
    )
    assert 'href="/gaza/"' in nav
    assert 'href="/food-line/"' in nav
    assert 'href="/care-line/"' in nav
    assert 'href="/cascadia/"' not in nav
    assert html.count('class="dispatch-card"') == 3
    assert 'href="/american-pressure/"' not in html
    assert "Dispatches From Gaza" in html
    assert "The Care Line Dispatch" in html
    assert "Food Line Dispatch" in html
    card_grid = html.split('<ul class="dispatch-grid">', 1)[1].split("</ul>", 1)[0]
    assert "The Care Line Dispatch" in card_grid
    assert "The Cascadia Briefing" not in card_grid
    assert "Daily source-backed food insecurity pressure signals across the United States" in html
    assert '<img class="dispatch-card-logo" src="/food-line/assets/food-line-logo.png"' not in html
    assert 'class="dispatch-card-watermark"' in html
    assert 'class="dispatch-card-content"' in html
    assert '--dispatch-card-watermark: url(\'/food-line/assets/food-line-logo.png\')' in html
    assert '--dispatch-card-watermark: url(\'/gaza/assets/gaza-logo.png\')' in html
    assert 'href="/cascadia/detention-watch/"' not in html
    assert "Related: Cascadia Detention Watch" not in html
    assert "Immigration detention monitoring for WA, OR, and ID." not in html
    assert "dispatch-card-links" not in html
    assert "Cascadia Systems Dispatch" not in html
    css_text = read(css)
    assert "--blue-fern: #2F6F88" in css_text
    assert "opacity: 0.05;" in css_text
    assert "pointer-events: none;" in css_text
    assert "position: absolute;" in css_text
    assert "z-index: 2;" in css_text
    assert "background-position: center" in css_text
    assert "background-size: min(76%, 19rem) auto" in css_text
    assert f'src="assets/{ROOT_MASTHEAD_ASSET}"' in html
    assert ROOT_DESCRIPTION in html
    assert "<h1>Dispatches From The Blue Fern Co.</h1>" not in html
    root_hero = html.split('<section class="hero root-hero">', 1)[1].split("</section>", 1)[0]
    assert '<img class="publisher-mark" src="assets/bluefern.png"' not in root_hero


def test_favicon_assets_are_copied_and_linked_from_public_html(built_site):
    work, _, _ = built_site
    site = work / "output" / "site"

    for asset in FAVICON_ASSETS:
        assert (work / "assets" / asset).exists()
        assert (site / "assets" / asset).read_bytes() == (work / "assets" / asset).read_bytes()

    pages = [
        site / "index.html",
        site / "gaza" / "index.html",
        site / "gaza" / "archive.html",
        site / "gaza" / "editions" / "2026-05-03" / "index.html",
        site / "cascadia" / "index.html",
        site / "cascadia" / "archive.html",
        site / "cascadia" / "editions" / "2026-05-03" / "index.html",
        site / "care-line" / "index.html",
        site / "care-line" / "archive.html",
        site / "care-line" / "editions" / "2026-05-23" / "index.html",
        site / "care-line" / "editions" / "2026-05-23" / "source_table.html",
        site / "care-line" / "editions" / "2026-05-23" / "claim_ledger.html",
    ]
    for page in pages:
        assert_favicon_links(read(page))


def test_build_adds_favicons_to_existing_public_edition_html(monkeypatch):
    repo = Path(__file__).parent.parent
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    copy_repo_assets(repo, work)
    old_edition = work / "output" / "site" / "cascadia" / "editions" / "2026-04-26"
    old_edition.mkdir(parents=True)
    (old_edition / "index.html").write_text(
        '<!doctype html>\n<html><head>\n  <link rel="stylesheet" href="../../assets/site.css">\n</head><body>Old weekly page</body></html>\n',
        encoding="utf-8",
    )
    (old_edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "cascadia",
                "edition_date": "2026-04-26",
                "briefing_type": "weekly",
                "coverage_start": "2026-04-20",
                "coverage_end": "2026-04-26",
                "coverage_label": "Apr 20-26, 2026",
                "week_label": "2026-W17",
                "public_story_count": 1,
                "source_count": 1,
                "story_count": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (old_edition / "sources_manifest.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "old-src-1",
                    "url": "https://example.com/source",
                    "title": "Source-backed report",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (old_edition / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "old-story-1",
                    "title": "Old weekly page",
                    "included_in_public_summary": True,
                    "source_urls": ["https://example.com/source"],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-03")

    result = build_site(work, dry_run=False, backup_root=work / "backup")

    assert result["ok"] is True
    assert_favicon_links(read(old_edition / "index.html"))


def test_landing_page_uses_scalable_card_grid_and_copies_masthead(built_site):
    work, _, _ = built_site
    index = read(work / "output" / "site" / "index.html")
    css_path = work / "output" / "site" / "assets" / "site.css"

    assert '<ul class="dispatch-grid">' in index
    assert 'class="dispatch-card"' in index
    assert 'class="dispatch-card-logo"' not in index
    css_text = read(css_path)
    assert 'class="dispatch-card-watermark"' in index
    assert 'class="dispatch-card-content"' in index
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in css_text
    assert '@media (max-width: 768px)' in css_text
    assert 'grid-template-columns: 1fr;' in css_text
    assert (work / "output" / "site" / "assets" / ROOT_MASTHEAD_ASSET).exists()
    assert (work / "output" / "site" / "food-line" / "assets" / "food-line-logo.png").exists()


def test_homepage_lists_public_dispatch_cards(built_site):
    work, _, _ = built_site
    index = read(work / "output" / "site" / "index.html")
    nav = index.split("<nav>", 1)[1].split("</nav>", 1)[0]
    card_grid = index.split('<ul class="dispatch-grid">', 1)[1].split("</ul>", 1)[0]
    cards = re.findall(r'<li class="dispatch-card".*?<a href="([^"]+)">.*?<strong>([^<]+)</strong>', card_grid, re.DOTALL)

    assert nav.count("<a ") == 3
    assert 'href="/gaza/">Gaza</a>' in nav
    assert 'href="/food-line/">Food Line Dispatch</a>' in nav
    assert 'href="/care-line/">The Care Line Dispatch</a>' in nav
    assert 'href="/cascadia/"' not in nav
    assert cards == [
        ("/gaza/", "Dispatches From Gaza"),
        ("/food-line/", "Food Line Dispatch"),
        ("/care-line/", "The Care Line Dispatch"),
    ]
    assert 'href="/care-line/"' in card_grid
    assert 'href="/american-pressure/"' not in card_grid
    assert 'href="/cascadia/"' not in card_grid


def test_gaza_content_and_requested_logo_placement(built_site):
    work, _, _ = built_site
    gaza_index = read(work / "output" / "site" / "gaza" / "index.html")
    gaza_edition = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-03" / "index.html")

    assert 'src="assets/gaza-logo.png"' in gaza_index
    assert 'src="assets/bluefern.png"' in gaza_index
    assert 'href="https://thebluefernco.com/"' in gaza_index
    assert "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza" in gaza_edition
    assert 'src="../../assets/gaza-logo.png"' in gaza_edition
    assert 'src="../../assets/bluefern.png"' in gaza_edition


def test_dispatch_pages_link_back_to_dispatches_home(built_site):
    work, _, _ = built_site
    site = work / "output" / "site"
    pages = [
        site / "gaza" / "index.html",
        site / "gaza" / "archive.html",
        site / "gaza" / "editions" / "2026-05-03" / "index.html",
        site / "cascadia" / "index.html",
        site / "cascadia" / "archive.html",
        site / "cascadia" / "editions" / "2026-05-03" / "index.html",
        site / "care-line" / "index.html",
        site / "care-line" / "archive.html",
        site / "care-line" / "editions" / "2026-05-23" / "index.html",
    ]

    for page in pages:
        html = read(page)
        assert 'href="/">Dispatches Home</a>' in html

    gaza_edition = read(site / "gaza" / "editions" / "2026-05-03" / "index.html")
    cascadia_edition = read(site / "cascadia" / "editions" / "2026-05-03" / "index.html")
    care_line_edition = read(site / "care-line" / "editions" / "2026-05-23" / "index.html")
    assert 'href="/gaza/">Dispatches From Gaza</a>' in gaza_edition
    assert 'href="/cascadia/">The Cascadia Briefing</a>' in cascadia_edition
    assert 'href="/care-line/">The Care Line Dispatch</a>' in care_line_edition


def test_cascadia_page_and_dated_edition_url(built_site):
    work, _, _ = built_site
    cascadia_index = read(work / "output" / "site" / "cascadia" / "index.html")
    cascadia_edition = work / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html"

    assert "The Cascadia Briefing" in cascadia_index
    assert CASCADIA_PUBLIC_DESCRIPTION in cascadia_index
    assert "Signal Pack" in cascadia_index
    assert "Latest Briefing" in cascadia_index
    assert "Pressure Map" in cascadia_index
    assert "Detention Watch" in cascadia_index
    assert "Recent Editions" in cascadia_index
    assert "A weekly source-backed systems briefing for Washington, Oregon, and Idaho." in cascadia_index
    assert "Open latest Cascadia pressure map" in cascadia_index
    assert "Latest Detention Watch" not in cascadia_index
    assert 1 <= cascadia_index.count('href="/cascadia/detention-watch/"') <= 2
    assert 'href="/cascadia/detention-watch/"' in cascadia_index
    assert "Open Detention Watch" in cascadia_index
    assert "Read briefing" in cascadia_index
    map_path = work / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html"
    if map_path.exists():
        assert "View map" in cascadia_index
    assert 'href="/cascadia/"' not in cascadia_index
    assert f"assets/{CASCADIA_LOGO_ASSET}" in cascadia_index
    assert cascadia_edition.exists()
    assert f"{BASE_URL}/cascadia/editions/2026-05-03/" in read(cascadia_edition)
    assert "class=\"briefing\"" in read(cascadia_edition)


def test_detention_watch_links_have_no_malformed_paths(built_site):
    work, _, _ = built_site
    root_index = read(work / "output" / "site" / "index.html")
    cascadia_index = read(work / "output" / "site" / "cascadia" / "index.html")
    assert root_index.count('href="/cascadia/detention-watch/"') <= 1
    for html in (root_index, cascadia_index):
        assert "//cascadia/detention-watch" not in html
        assert "/cascadia/cascadia/detention-watch" not in html


def test_build_does_not_publish_synthetic_current_cascadia_edition(monkeypatch):
    repo = Path(__file__).parent.parent
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    copy_repo_assets(repo, work)
    synthetic_current = work / "output" / "site" / "cascadia" / "editions" / "2026-05-11"
    if synthetic_current.exists():
        shutil.rmtree(synthetic_current)
    synthetic_current_dispatch = work / "output" / "dispatches" / "cascadia" / "editions" / "2026-05-11"
    if synthetic_current_dispatch.exists():
        shutil.rmtree(synthetic_current_dispatch)
    stale_daily = work / "output" / "site" / "cascadia" / "editions" / "2026-05-04"
    stale_daily.mkdir(parents=True)
    (stale_daily / "index.html").write_text("<html>daily</html>", encoding="utf-8")
    (stale_daily / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": "cascadia", "edition_date": "2026-05-04", "briefing_type": "daily"}),
        encoding="utf-8",
    )
    add_cascadia_site_edition(work / "output" / "site", "2026-05-03")
    add_cascadia_dispatch_edition(work, "2026-05-03")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-12")

    result = build_site(work, dry_run=False, backup_root=work / "backup")

    assert result["ok"] is True
    assert not (work / "output" / "site" / "cascadia" / "editions" / "2026-05-11").exists()
    assert not stale_daily.exists()
    index = read(work / "output" / "site" / "cascadia" / "index.html")
    assert 'href="editions/2026-05-03/"' in index
    assert "2026-05-11" not in index


def test_zero_story_cascadia_subtitle_reflects_review_threshold(built_site):
    work, _, _ = built_site
    site_root = work / "output" / "site"
    dispatch = DispatchConfig("cascadia", "The Cascadia Briefing", "2026-05-10", "Weekly", CASCADIA_LOGO_ASSET, [], [])
    add_cascadia_site_edition(site_root, "2026-05-10")
    manifest_path = site_root / "cascadia" / "editions" / "2026-05-10" / "edition_manifest.json"
    manifest = json.loads(read(manifest_path))
    manifest["public_story_count"] = 0
    manifest["public_archive_subtitle"] = "0 stories | No qualifying public signals identified"
    manifest["minimum_review_threshold_met"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert public_edition_subtitle(site_root, dispatch, "2026-05-10") == "Reviewed week | No qualifying source-backed regional signals surfaced"

    manifest["minimum_review_threshold_met"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert public_edition_subtitle(site_root, dispatch, "2026-05-10") == "Reviewed week | No qualifying source-backed regional signals identified"


def test_cascadia_logo_asset_is_copied_to_public_locations(built_site):
    work, _, _ = built_site
    source_logo = work / "assets" / CASCADIA_LOGO_ASSET
    global_logo = work / "output" / "site" / "assets" / CASCADIA_LOGO_ASSET
    cascadia_logo = work / "output" / "site" / "cascadia" / "assets" / CASCADIA_LOGO_ASSET
    cascadia_index = read(work / "output" / "site" / "cascadia" / "index.html")
    cascadia_archive = read(work / "output" / "site" / "cascadia" / "archive.html")
    cascadia_edition = read(work / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html")

    assert global_logo.read_bytes() == source_logo.read_bytes()
    assert cascadia_logo.read_bytes() == source_logo.read_bytes()
    assert f'src="assets/{CASCADIA_LOGO_ASSET}"' in cascadia_index
    assert f'src="assets/{CASCADIA_LOGO_ASSET}"' in cascadia_archive
    assert f'src="../../assets/{CASCADIA_LOGO_ASSET}"' in cascadia_edition


def test_public_cascadia_pages_use_current_public_name(built_site):
    work, _, _ = built_site
    cascadia_pages = [
        work / "output" / "site" / "cascadia" / "index.html",
        work / "output" / "site" / "cascadia" / "archive.html",
        work / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html",
        work / "output" / "site" / "cascadia" / "rss.xml",
    ]

    for page in cascadia_pages:
        html = read(page)
        assert "The Cascadia Briefing" in html
        assert "Cascadia Systems Dispatch" not in html


def test_american_pressure_index_links_to_map_only(built_site):
    work, _, _ = built_site
    ap_index = read(work / "output" / "site" / "american-pressure" / "index.html")
    assert 'href="map/"' in ap_index
    assert 'href="dashboard/"' not in ap_index
    assert "What American Pressure Tracks" in ap_index
    assert "What it tracks:" in ap_index
    assert "What it does not claim:" in ap_index
    assert "How to read it:" in ap_index


def test_manifests_and_source_traceability(built_site):
    work, _, _ = built_site
    edition_dir = work / "output" / "site" / "cascadia" / "editions" / "2026-05-03"
    edition_manifest = json.loads(read(edition_dir / "edition_manifest.json"))
    sources_manifest = json.loads(read(edition_dir / "sources_manifest.json"))
    curation_manifest = json.loads(read(edition_dir / "curation_manifest.json"))

    assert edition_manifest["dispatch_slug"] == "cascadia"
    assert edition_manifest["source_count"] >= 1
    assert edition_manifest["story_count"] >= 1
    assert edition_manifest["source_manifest_path"].endswith("sources_manifest.json")
    assert sources_manifest[0]["source_id"]
    assert curation_manifest[0]["source_ids"]


def test_public_stories_require_sources_unless_editorial():
    dispatch = DispatchConfig(
        slug="test",
        name="Test Dispatch",
        edition_date="2026-05-03",
        tagline="Test",
        logo="logo.png",
        sources=[],
        stories=[
            StoryRecord(
                story_id="story-001",
                title="Unsupported claim",
                summary="A factual public story with no sources.",
                category="public-safety",
                score=10,
                scoring_reasons=[],
                included_in_public_summary=True,
                included_in_detail_dataset=False,
                source_ids=[],
            )
        ],
    )
    assert validate_traceability([dispatch])


def test_editorial_admin_copy_may_render_with_explicit_marker():
    dispatch = DispatchConfig(
        slug="test",
        name="Test Dispatch",
        edition_date="2026-05-03",
        tagline="Test",
        logo="logo.png",
        sources=[],
        stories=[
            StoryRecord(
                story_id="story-001",
                title="Administrative copy",
                summary="This page is being prepared.",
                category="editorial-admin",
                score=0,
                scoring_reasons=[],
                included_in_public_summary=True,
                included_in_detail_dataset=False,
                source_ids=[],
                editorial_admin_copy=True,
            )
        ],
    )
    assert validate_traceability([dispatch]) == []


def test_backup_path_is_outside_repo_and_contains_manifests(built_site):
    work, backup_root, _ = built_site
    assert work.resolve() not in backup_root.resolve().parents
    backup_dir = backup_root / "gaza" / "2026-05-03"
    assert (backup_dir / "index.html").exists()
    assert (backup_dir / "sources_manifest.json").exists()
    assert (backup_dir / "curation_manifest.json").exists()
    assert (backup_dir / "edition_manifest.json").exists()


def test_paid_detail_files_are_not_public(built_site):
    work, _, result = built_site
    public_paths = [path.relative_to(work / "output" / "site").as_posix() for path in (work / "output" / "site").rglob("*") if path.is_file()]
    assert result["paid_detail_excluded_from_public"] is True
    assert not any(path.startswith("detail/") or path.startswith("paid/") for path in public_paths)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in (work / "output" / "site").rglob("*") if path.suffix in {".html", ".json", ".xml", ".css"})
    assert "output/detail" not in public_text
    assert "output/paid" not in public_text
    assert "cascadia_signal_records" not in public_text


def test_detail_roots_inside_public_site_are_rejected():
    site_root = Path("output") / "site"
    assert ensure_public_detail_separation(site_root, [site_root / "paid"])


def make_pages_repo(path):
    path.mkdir(parents=True)
    git_dir = path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("fake-main-commit\n", encoding="utf-8")
    (path / ".keep").write_text("keep\n", encoding="utf-8")
    return path


def write_min_food_line_public_edition(
    root: Path,
    edition_date: str,
    *,
    body_html: str = "<html><body>Food Line edition</body></html>",
    manifest_overrides: dict[str, object] | None = None,
) -> Path:
    edition_dir = root / "food-line" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dispatch_slug": "food-line",
        "edition_date": edition_date,
        "public_rendered": True,
        "edition_mode": "current_update",
        "source_freshness_status": "passed",
        "freshness_window_days": 3,
        "stale_public_story_count": 0,
        "excluded_stale_source_count": 0,
        "stale_source_ids": [],
        "qualified_primary_count": 1,
        "skip_reason": "",
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (edition_dir / "index.html").write_text(body_html, encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (edition_dir / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": f"food-src-{edition_date}", "title": "Source", "url": "https://example.com"}], indent=2),
        encoding="utf-8",
    )
    (edition_dir / "curation_manifest.json").write_text(
        json.dumps([{"story_id": f"food-story-{edition_date}", "source_ids": [f"food-src-{edition_date}"]}], indent=2),
        encoding="utf-8",
    )
    return edition_dir


def write_min_food_line_dispatch_edition(
    root: Path,
    edition_date: str,
) -> Path:
    edition_dir = root / "output" / "dispatches" / "food-line" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text("<html><body>Food Line dispatch edition</body></html>", encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": edition_date,
                "public_rendered": True,
                "edition_mode": "current_update",
                "source_freshness_status": "passed",
                "freshness_window_days": 3,
                "stale_public_story_count": 0,
                "excluded_stale_source_count": 0,
                "stale_source_ids": [],
                "qualified_primary_count": 1,
                "skip_reason": "",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition_dir / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "food-src-2026-06-01", "title": "Source", "url": "https://example.com"}], indent=2),
        encoding="utf-8",
    )
    (edition_dir / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "food-story-2026-06-01", "source_ids": ["food-src-2026-06-01"]}], indent=2),
        encoding="utf-8",
    )
    return edition_dir


def write_min_care_line_public_edition(
    root: Path,
    edition_date: str,
    *,
    body_html: str = "<html><body>Care Line edition</body></html>",
) -> Path:
    edition_dir = root / "care-line" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text(body_html, encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "care-line",
                "edition_date": edition_date,
                "public_rendered": True,
                "edition_mode": "current_update",
                "source_freshness_status": "passed",
                "freshness_window_days": 14,
                "stale_public_story_count": 0,
                "excluded_stale_source_count": 0,
                "stale_source_ids": [],
                "qualified_primary_count": 1,
                "skip_reason": "",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition_dir / "source_table.html").write_text("<html><body>source table</body></html>", encoding="utf-8")
    (edition_dir / "claim_ledger.html").write_text("<html><body>claim ledger</body></html>", encoding="utf-8")
    return edition_dir


def write_min_care_line_dispatch_edition(
    root: Path,
    edition_date: str,
) -> Path:
    edition_dir = root / "output" / "dispatches" / "care-line" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "index.html").write_text("<html><body>Care Line dispatch edition</body></html>", encoding="utf-8")
    (edition_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "care-line",
                "edition_date": edition_date,
                "public_rendered": True,
                "edition_mode": "current_update",
                "source_freshness_status": "passed",
                "freshness_window_days": 14,
                "stale_public_story_count": 0,
                "excluded_stale_source_count": 0,
                "stale_source_ids": [],
                "qualified_primary_count": 1,
                "skip_reason": "",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition_dir / "source_table.html").write_text("<html><body>source table</body></html>", encoding="utf-8")
    (edition_dir / "claim_ledger.html").write_text("<html><body>claim ledger</body></html>", encoding="utf-8")
    return edition_dir


def test_pages_sync_repair_message_is_explicit_about_resetting_to_origin(tmp_path):
    message = generator.pages_sync_repair_message(tmp_path / "pages", "gh-pages")
    assert "pages_repo_not_synced_with_origin" in message
    assert "Do not run git pull --rebase blindly" in message
    assert "reset the Pages checkout" in message
    assert "origin/gh-pages" in message


def test_ensure_pages_branch_rejects_active_rebase_state(monkeypatch, tmp_path):
    pages_repo = make_pages_repo(tmp_path / "pages")
    monkeypatch.setattr(generator, "git_stdout", lambda args, cwd: "main" if args == ["branch", "--show-current"] else None)
    monkeypatch.setattr(generator, "_pages_repo_active_operation_markers", lambda cwd: ["rebase-merge"])
    monkeypatch.setattr(generator, "_git_porcelain_paths", lambda cwd: [])

    result = generator.ensure_pages_branch(pages_repo, "gh-pages", dry_run=False)

    assert result["errors"]
    assert "pages_repo_has_active_rebase_or_merge_state" in result["errors"][0]


def test_ensure_pages_branch_rejects_pages_repo_that_is_behind_origin(monkeypatch, tmp_path):
    pages_repo = make_pages_repo(tmp_path / "pages")

    def fake_git_stdout(args, cwd):
        if args == ["branch", "--show-current"]:
            return "main"
        if args == ["remote"]:
            return "origin"
        if args == ["rev-parse", "HEAD"]:
            return "local-sha"
        if args == ["rev-parse", "origin/gh-pages"]:
            return "remote-sha"
        return None

    def fake_run_git(args, cwd):
        if args[:1] == ["fetch"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["checkout"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["merge-base", "--is-ancestor", "HEAD", "origin/gh-pages"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["merge-base", "--is-ancestor", "origin/gh-pages", "HEAD"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(generator, "git_stdout", fake_git_stdout)
    monkeypatch.setattr(generator, "git_ref_exists", lambda ref, cwd: True)
    monkeypatch.setattr(generator, "_pages_repo_active_operation_markers", lambda cwd: [])
    monkeypatch.setattr(generator, "_git_porcelain_paths", lambda cwd: [])
    monkeypatch.setattr(generator, "run_git", fake_run_git)

    result = generator.ensure_pages_branch(pages_repo, "gh-pages", dry_run=False)

    assert result["errors"]
    assert "pages_repo_not_synced_with_origin" in result["errors"][0]
    assert "local HEAD is behind origin/gh-pages" in result["errors"][0]


def test_ensure_pages_branch_allows_local_publish_when_fetch_fails(monkeypatch, tmp_path):
    pages_repo = make_pages_repo(tmp_path / "pages")

    def fake_git_stdout(args, cwd):
        if args == ["branch", "--show-current"]:
            return "gh-pages"
        if args == ["remote"]:
            return "origin"
        return None

    def fake_run_git(args, cwd):
        if args[:1] == ["fetch"]:
            return subprocess.CompletedProcess(args, 1, "", "schannel fetch failed")
        if args[:1] == ["checkout"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(generator, "git_stdout", fake_git_stdout)
    monkeypatch.setattr(generator, "git_ref_exists", lambda ref, cwd: ref == "refs/heads/gh-pages")
    monkeypatch.setattr(generator, "_pages_repo_active_operation_markers", lambda cwd: [])
    monkeypatch.setattr(generator, "_git_porcelain_paths", lambda cwd: [])
    monkeypatch.setattr(generator, "run_git", fake_run_git)

    result = generator.ensure_pages_branch(pages_repo, "gh-pages", dry_run=False)

    assert result["errors"] == []
    assert result["checked_out_branch"] == "gh-pages"
    assert result["fetch_attempted"] is True
    assert result["fetched"] is False
    assert any("schannel fetch failed" in warning for warning in result["warnings"])


def test_validate_pages_repo_copy_scope_flags_detail_and_unrelated_changes(monkeypatch, tmp_path):
    pages_repo = make_pages_repo(tmp_path / "pages")
    monkeypatch.setattr(
        generator,
        "_git_status_changed_paths",
        lambda cwd: [
            Path("detail/private.html"),
            Path("gaza/index.html"),
            Path("food-line/index.html"),
            Path("index.html"),
            Path("assets/site.css"),
            Path("CNAME"),
        ],
    )

    errors = generator.validate_pages_repo_copy_scope(pages_repo, ("gaza",))

    assert any("gaza_publish_scope_violation" in error and "detail/private.html" in error for error in errors)
    assert any("unexpected publish changes" in error and "food-line/index.html" in error for error in errors)
    assert any("gaza_publish_scope_violation" in error and "index.html" in error for error in errors)
    assert any("gaza_publish_scope_violation" in error and "assets/site.css" in error for error in errors)
    assert any("gaza_publish_scope_violation" in error and "CNAME" in error for error in errors)


def test_validate_pages_repo_copy_scope_uses_explicit_changed_paths_for_gaza_only(tmp_path, monkeypatch):
    pages_repo = make_pages_repo(tmp_path / "pages")
    monkeypatch.setattr(
        generator,
        "_git_status_changed_paths",
        lambda cwd: [Path("care-line/index.html"), Path("cascadia/editions/2026-05-17/index.html"), Path("assets/site.css")],
    )
    changed_paths = [
        Path("gaza/index.html"),
        Path("gaza/archive.html"),
        Path("gaza/rss.xml"),
        Path("gaza/editions/2026-06-22/index.html"),
    ]

    errors = generator.validate_pages_repo_copy_scope(pages_repo, ("gaza",), changed_paths=changed_paths)

    assert errors == []


def test_gaza_only_dry_run_ignores_preexisting_pages_repo_dirtiness_when_copy_plan_is_scoped(
    built_site, monkeypatch
):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    monkeypatch.setattr(
        generator,
        "_git_status_changed_paths",
        lambda cwd: [
            Path("care-line/index.html"),
            Path("cascadia/editions/2026-05-17/index.html"),
            Path("food-line/editions/2026-06-21/index.html"),
            Path("assets/site.css"),
        ],
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-03",
        expect_dispatches=("gaza",),
        only_dispatches=("gaza",),
    )

    copied = result["files_that_would_be_copied"]
    assert result["ok"] is True
    assert result["paid_detail_excluded_from_public"] is True
    assert copied
    assert any("gaza/editions/2026-05-03/index.html" in path.replace("\\", "/") for path in copied)
    assert any("gaza/archive.html" in path.replace("\\", "/") for path in copied)
    assert any("gaza/rss.xml" in path.replace("\\", "/") for path in copied)
    assert any("gaza/index.html" in path.replace("\\", "/") for path in copied)
    assert not any("/care-line/" in path.replace("\\", "/") for path in copied)
    assert not any("/cascadia/" in path.replace("\\", "/") for path in copied)
    assert not any("/food-line/" in path.replace("\\", "/") for path in copied)
    assert not any(path.endswith("/CNAME") or path.endswith("\\CNAME") for path in copied)


def test_gaza_only_dry_run_copy_scope_includes_only_reconciled_root_metadata_and_gaza(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    directory = work / "output" / "site" / "dispatches" / "index.html"
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("<html><body>Current dispatch directory</body></html>", encoding="utf-8")
    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-03",
        expect_dispatches=("gaza",),
        only_dispatches=("gaza",),
    )

    copied = result["files_that_would_be_copied"]
    assert copied
    assert any("/gaza/" in path.replace("\\", "/") for path in copied)
    assert any("gaza/editions/2026-05-03/index.html" in path.replace("\\", "/") for path in copied)
    assert any("gaza/archive.html" in path.replace("\\", "/") for path in copied)
    assert any("gaza/rss.xml" in path.replace("\\", "/") for path in copied)
    assert any("gaza/index.html" in path.replace("\\", "/") for path in copied)
    assert not any("/american-pressure/" in path.replace("\\", "/") for path in copied)
    assert not any("/care-line/" in path.replace("\\", "/") for path in copied)
    assert not any("/cascadia/" in path.replace("\\", "/") for path in copied)
    assert not any("/food-line/" in path.replace("\\", "/") for path in copied)
    normalized = {path.replace("\\", "/").split("/bluefern-dispatches-pages/", 1)[-1] for path in copied}
    assert "index.html" in normalized
    assert "dispatches/index.html" in normalized
    assert all(path.startswith("gaza/") or path in {"index.html", "dispatches/index.html"} for path in normalized)
    assert not any(path.endswith("/CNAME") or path.endswith("\\CNAME") for path in copied)
    assert not any("/assets/" in path.replace("\\", "/") and "/gaza/" not in path.replace("\\", "/") for path in copied)


def test_gaza_only_copy_scope_allows_only_exact_sitewide_metadata_paths(tmp_path):
    pages = tmp_path / "pages"
    pages.mkdir()

    assert validate_pages_repo_copy_scope(
        pages,
        ("gaza",),
        changed_paths=["gaza/index.html", "index.html", "dispatches/index.html"],
    ) == []
    errors = validate_pages_repo_copy_scope(
        pages,
        ("gaza",),
        changed_paths=["assets/site.css", "dispatches/archive.html", "food-line/index.html"],
    )
    assert len(errors) == 3
    assert all("gaza_publish_scope_violation" in error for error in errors)


def test_validate_pages_copy_parity_detects_public_copy_drift(tmp_path):
    root = tmp_path / "root"
    pages_repo = tmp_path / "pages"
    source_index = root / "output" / "site" / "gaza" / "index.html"
    target_index = pages_repo / "gaza" / "index.html"
    source = root / "output" / "site" / "gaza" / "editions" / "2026-05-07"
    target = pages_repo / "gaza" / "editions" / "2026-05-07"
    source_index.parent.mkdir(parents=True, exist_ok=True)
    source.mkdir(parents=True, exist_ok=True)
    target_index.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    source_index.write_text("source landing", encoding="utf-8")
    target_index.write_text("target landing", encoding="utf-8")
    (source / "index.html").write_text("source edition", encoding="utf-8")
    (target / "index.html").write_text("target edition", encoding="utf-8")
    (root / "output" / "site" / "gaza" / "archive.html").write_text("2026-05-07 archive", encoding="utf-8")
    (root / "output" / "site" / "gaza" / "rss.xml").write_text("2026-05-07 rss", encoding="utf-8")
    (pages_repo / "gaza").mkdir(parents=True, exist_ok=True)
    (pages_repo / "gaza" / "archive.html").write_text("2026-05-07 archive", encoding="utf-8")
    (pages_repo / "gaza" / "rss.xml").write_text("2026-05-07 rss", encoding="utf-8")

    errors = generator.validate_pages_copy_parity(root, pages_repo, "2026-05-07")

    assert any("gaza edition index differs" in error for error in errors)


def test_pages_dry_run_does_not_write_to_pages_repo(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(
        work,
        pages_repo,
        "https://github.com/RedGarland/the-blue-fern-co-dispatches/",
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
    )

    assert result["ok"] is True
    assert result["files_that_would_be_copied"]
    assert not (pages_repo / "index.html").exists()
    assert not (pages_repo / "CNAME").exists()
    assert (pages_repo / ".git").exists()


def test_pages_dry_run_with_new_edition_reports_copies_without_parity_failure(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-03",
        expect_dispatches=("gaza",),
        only_dispatches=("gaza",),
    )

    copied = result["files_that_would_be_copied"]
    assert result["ok"] is True
    assert result["local_pages_copy_ok"] is True
    assert result["errors"] == []
    assert copied
    assert any("gaza/index.html" in path.replace("\\", "/") for path in copied)
    assert any("gaza/archive.html" in path.replace("\\", "/") for path in copied)
    assert any("gaza/rss.xml" in path.replace("\\", "/") for path in copied)
    assert any("gaza/editions/2026-05-03/index.html" in path.replace("\\", "/") for path in copied)
    assert not (pages_repo / "gaza" / "index.html").exists()
    assert not (pages_repo / "gaza" / "archive.html").exists()
    assert not (pages_repo / "gaza" / "rss.xml").exists()


def test_pages_copy_creates_cname_and_preserves_git(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
    )

    assert result["ok"] is True
    assert (pages_repo / ".git").exists()
    assert (pages_repo / "CNAME").read_text(encoding="utf-8").strip() == CNAME_VALUE
    assert (pages_repo / "index.html").exists()
    assert (pages_repo / "assets" / ROOT_MASTHEAD_ASSET).exists()
    for asset in FAVICON_ASSETS:
        assert (pages_repo / "assets" / asset).exists()
    assert (pages_repo / "gaza" / "editions" / "2026-05-03" / "index.html").exists()
    assert (pages_repo / "cascadia" / "index.html").exists()
    assert (pages_repo / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()


def test_pages_publish_excludes_paid_detail_folders(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=backup_root)

    assert result["paid_detail_excluded_from_public"] is True
    assert not (pages_repo / "paid").exists()
    assert not (pages_repo / "detail").exists()
    assert "output/paid/" in result["files_that_would_be_skipped"]
    assert "output/detail/" in result["files_that_would_be_skipped"]


def add_gaza_site_edition(site_root: Path, edition_date: str) -> None:
    edition = site_root / "gaza" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text("<html><body>Gaza daily</body></html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": "gaza", "edition_date": edition_date, "source_count": 1, "story_count": 1}),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_id": "gaza-src-001", "url": "https://example.com/gaza"}]),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "gaza-story-001", "source_ids": ["gaza-src-001"]}]),
        encoding="utf-8",
    )
    archive = site_root / "gaza" / "archive.html"
    archive.write_text(archive.read_text(encoding="utf-8") + f"\n{edition_date}\n", encoding="utf-8")


def add_gaza_public_history_surface(
    site_root: Path,
    dates: list[str],
    *,
    archive_dates: list[str] | None = None,
    audio_dates: list[str] | None = None,
) -> None:
    gaza_root = site_root / "gaza"
    gaza_root.mkdir(parents=True, exist_ok=True)
    homepage_items = "".join(
        f'<li class="edition-item"><span class="edition-date">{date_text}</span><a href="editions/{date_text}/">Edition</a></li>'
        for date_text in dates
    )
    (gaza_root / "index.html").write_text(f'<html><body><ul class="edition-list">{homepage_items}</ul></body></html>', encoding="utf-8")
    archive_source_dates = archive_dates if archive_dates is not None else dates
    archive_links = "".join(f'<a href="editions/{date_text}/">{date_text}</a>' for date_text in archive_source_dates)
    (gaza_root / "archive.html").write_text(f"<html><body>{archive_links}</body></html>", encoding="utf-8")
    rss_items = "".join(f"<item><link>https://dispatches.thebluefernco.com/gaza/editions/{date_text}/</link></item>" for date_text in archive_source_dates)
    (gaza_root / "rss.xml").write_text(f"<rss><channel>{rss_items}</channel></rss>", encoding="utf-8")
    edition_dates = audio_dates or archive_source_dates
    audio_root = gaza_root / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    audio_index_items = "".join(
        f'<li class="gaza-audio-index-row"><span class="gaza-audio-index-date"><strong>{date_text}</strong></span>'
        f'<span class="gaza-audio-index-transcript"><a href="/gaza/audio/{date_text}-transcript.html">Transcript</a></span></li>'
        for date_text in edition_dates
    )
    (audio_root / "index.html").write_text(f"<html><body><ul>{audio_index_items}</ul></body></html>", encoding="utf-8")
    podcast_items = "".join(
        f"<item><link>https://dispatches.thebluefernco.com/gaza/audio/{date_text}-transcript.html</link>"
        f"<guid>https://dispatches.thebluefernco.com/gaza/audio/{date_text}-transcript.html</guid></item>"
        for date_text in edition_dates
    )
    podcast_xml = f"<rss><channel>{podcast_items}</channel></rss>"
    (audio_root / "podcast.xml").write_text(podcast_xml, encoding="utf-8")
    (gaza_root / "podcast.xml").write_text(podcast_xml, encoding="utf-8")
    for date_text in edition_dates:
        (audio_root / f"{date_text}-transcript.html").write_text(f"<html>{date_text}</html>", encoding="utf-8")


def add_cascadia_site_edition(site_root: Path, edition_date: str) -> None:
    end = date.fromisoformat(edition_date)
    start = end - timedelta(days=6)
    coverage_label = f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}, {end.year}"
    edition = site_root / "cascadia" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text("<html><body>Cascadia weekly</body></html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "cascadia",
                "edition_date": edition_date,
                "briefing_type": "weekly",
                "cadence": "weekly",
                "edition_type": "weekly",
                "coverage_start": start.isoformat(),
                "coverage_end": edition_date,
                "coverage_label": coverage_label,
                "public_coverage_label": coverage_label,
                "public_coverage_range": {
                    "coverage_start": start.isoformat(),
                    "coverage_end": edition_date,
                },
                "week_label": f"{end.isocalendar().year}-W{end.isocalendar().week:02d}",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (edition / "curation_manifest.json").write_text("[]", encoding="utf-8")


def add_american_pressure_site_edition(site_root: Path, edition_date: str) -> None:
    end = date.fromisoformat(edition_date)
    start = end - timedelta(days=6)
    display = f"{start.strftime('%B')} {start.day}–{end.strftime('%B')} {end.day}, {end.year}"
    edition = site_root / "american-pressure" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text("<html><body>American Pressure weekly</body></html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "american-pressure",
                "edition_date": edition_date,
                "source_count": 3,
                "story_count": 2,
                "week_start_date": start.isoformat(),
                "week_end_date": edition_date,
                "display_date_range": display,
            }
        ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_id": "ap-src-001", "url": "https://example.com/source-1"}]),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(json.dumps([{"story_id": "ap-story-001"}]), encoding="utf-8")
    archive = site_root / "american-pressure" / "archive.html"
    archive.write_text(archive.read_text(encoding="utf-8") + f"\n{edition_date}\n", encoding="utf-8")


def add_invalid_american_pressure_site_edition(site_root: Path, edition_date: str) -> None:
    edition = site_root / "american-pressure" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text("<html><body>Invalid American Pressure weekly</body></html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "american-pressure",
                "edition_date": edition_date,
                "source_count": 2,
                "story_count": 2,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_id": "ap-src-invalid", "url": "https://example.com/source-invalid"}]),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(json.dumps({"stories": [{"story_id": "ap-story-invalid"}]}), encoding="utf-8")


def test_gaza_expect_date_does_not_require_same_date_cascadia(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    add_gaza_site_edition(site_root, "2026-05-12")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-12",
        expect_dispatches=("gaza",),
    )

    assert result["ok"] is True
    assert (pages_repo / "gaza" / "editions" / "2026-05-12" / "index.html").exists()
    assert not (pages_repo / "cascadia" / "editions" / "2026-05-12" / "index.html").exists()
    assert not any("expected Cascadia" in error for error in result["errors"])


def test_gaza_expect_date_reports_gaza_missing_only(built_site):
    work, _, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = work / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    (pages_repo / ".git").mkdir()
    (pages_repo / "CNAME").write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html></html>", encoding="utf-8")
    (pages_repo / "gaza").mkdir()
    (pages_repo / "gaza" / "archive.html").write_text("2026-05-09", encoding="utf-8")
    add_gaza_site_edition(site_root, "2026-05-09")

    errors = validate_pages_repo_after_copy(
        pages_repo,
        site_root,
        "2026-05-09",
        expect_dispatches=("gaza",),
    )

    assert "expected Gaza edition missing: 2026-05-09" in errors
    assert not any("Cascadia" in error for error in errors)


def test_legacy_full_site_expectation_still_checks_same_date_dispatches(built_site):
    work, _, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = work / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    (pages_repo / ".git").mkdir()
    (pages_repo / "CNAME").write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html></html>", encoding="utf-8")
    (pages_repo / "gaza").mkdir()
    (pages_repo / "gaza" / "archive.html").write_text("2026-05-10", encoding="utf-8")
    add_gaza_site_edition(site_root, "2026-05-10")
    add_cascadia_site_edition(site_root, "2026-05-10")

    errors = validate_pages_repo_after_copy(pages_repo, site_root, "2026-05-10")

    assert "expected Cascadia edition missing: 2026-05-10" in errors


def test_cascadia_expect_date_does_not_require_same_date_gaza(built_site):
    work, _, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = work / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    (pages_repo / ".git").mkdir()
    (pages_repo / "CNAME").write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html></html>", encoding="utf-8")
    (pages_repo / "gaza").mkdir()
    (pages_repo / "gaza" / "archive.html").write_text("", encoding="utf-8")
    add_gaza_site_edition(site_root, "2026-05-10")
    add_cascadia_site_edition(site_root, "2026-05-10")

    pre_errors, _ = validate_pages_publish(
        work,
        site_root,
        pages_repo,
        require_git=False,
        expect_date="2026-05-10",
        expect_dispatches=("cascadia",),
    )
    (pages_repo / "cascadia" / "editions" / "2026-05-10").mkdir(parents=True)
    (pages_repo / "cascadia" / "editions" / "2026-05-10" / "index.html").write_text("weekly", encoding="utf-8")
    post_errors = validate_pages_repo_after_copy(
        pages_repo,
        site_root,
        "2026-05-10",
        expect_dispatches=("cascadia",),
        only_dispatches=("cascadia",),
    )

    assert pre_errors == []
    assert post_errors == []


def test_cascadia_expect_date_reports_cascadia_missing_only(built_site):
    work, _, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = work / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    (pages_repo / ".git").mkdir()
    (pages_repo / "CNAME").write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html></html>", encoding="utf-8")
    (pages_repo / "gaza").mkdir()
    (pages_repo / "gaza" / "archive.html").write_text("", encoding="utf-8")
    add_cascadia_site_edition(site_root, "2026-05-10")

    errors = validate_pages_repo_after_copy(
        pages_repo,
        site_root,
        "2026-05-10",
        expect_dispatches=("cascadia",),
    )

    assert "expected Cascadia edition missing: 2026-05-10" in errors
    assert not any("Gaza edition missing" in error for error in errors)


def test_expect_dispatch_all_expands_to_full_site_expectation():
    assert normalize_expect_dispatches(("all",)) == ("gaza", "cascadia", "american-pressure", "food-line", "care-line")


def test_american_pressure_expect_date_does_not_require_same_date_cascadia_or_gaza(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    add_american_pressure_site_edition(site_root, "2026-05-09")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-09",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    assert (pages_repo / "american-pressure" / "editions" / "2026-05-09" / "index.html").exists()
    assert not any("expected Gaza" in error or "expected Cascadia" in error for error in result["errors"])


def test_american_pressure_only_dispatch_expect_date_uses_public_cutoff(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    # Seed stale future folders that should not become latest public metadata.
    add_american_pressure_site_edition(site_root, "2026-05-09")
    add_american_pressure_site_edition(site_root, "2026-05-16")
    add_american_pressure_site_edition(site_root, "2026-05-19")
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-09",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    assert not (pages_repo / "american-pressure" / "editions" / "2026-05-16" / "index.html").exists()
    assert not (pages_repo / "american-pressure" / "editions" / "2026-05-19" / "index.html").exists()


def test_expect_date_rejects_mismatched_selected_public_url_date(built_site, monkeypatch):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    real_build_site = build_site

    def fake_build_site(*args, **kwargs):
        result = real_build_site(*args, **kwargs)
        result["public_urls"] = [
            "https://dispatches.thebluefernco.com/american-pressure/editions/2026-05-24/"
        ]
        return result

    monkeypatch.setattr("bluefern_dispatches.generator.build_site", fake_build_site)
    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-23",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is False
    assert any("expected american-pressure edition date 2026-05-23" in err for err in result["errors"])


def test_targeted_american_pressure_publish_copies_expected_date_to_pages_repo(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    repo = Path(__file__).parent.parent
    src_dispatch_edition = repo / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    dst_dispatch_edition = work / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    if src_dispatch_edition.exists():
        shutil.copytree(src_dispatch_edition, dst_dispatch_edition, dirs_exist_ok=True)

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-23",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    assert (pages_repo / "american-pressure" / "editions" / "2026-05-23" / "index.html").exists()


def test_american_pressure_only_dispatch_does_not_modify_gaza_or_cascadia_pages(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    repo = Path(__file__).parent.parent
    src_dispatch_edition = repo / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    dst_dispatch_edition = work / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    if src_dispatch_edition.exists():
        shutil.copytree(src_dispatch_edition, dst_dispatch_edition, dirs_exist_ok=True)

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-23",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    copied = result["files_copied"]
    assert copied
    assert not any("\\gaza\\" in path.lower() or "/gaza/" in path.lower() for path in copied)
    assert not any("\\cascadia\\" in path.lower() or "/cascadia/" in path.lower() for path in copied)


def test_dry_run_reports_same_selected_ap_date_as_real_publish(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    repo = Path(__file__).parent.parent
    src_dispatch_edition = repo / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    dst_dispatch_edition = work / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    if src_dispatch_edition.exists():
        shutil.copytree(src_dispatch_edition, dst_dispatch_edition, dirs_exist_ok=True)

    dry = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-23",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )
    real = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-23",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert dry["ok"] is True
    assert real["ok"] is True
    dry_dates = sorted(
        {
            url.rstrip("/").split("/")[-1]
            for url in dry["build"]["public_urls"]
            if "/american-pressure/editions/" in url
        }
    )
    real_dates = sorted(
        {
            url.rstrip("/").split("/")[-1]
            for url in real["build"]["public_urls"]
            if "/american-pressure/editions/" in url
        }
    )
    assert dry_dates == ["2026-05-23"]
    assert real_dates == ["2026-05-23"]


def test_american_pressure_index_excludes_invalid_later_edition(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    add_american_pressure_site_edition(site_root, "2026-05-16")
    add_invalid_american_pressure_site_edition(site_root, "2026-05-18")
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    index = read(site_root / "american-pressure" / "index.html")
    archive = read(site_root / "american-pressure" / "archive.html")
    assert "2026-05-16" in index and "2026-05-18" not in index
    assert "2026-05-16" in archive and "2026-05-18" not in archive


def test_publish_pages_copies_american_pressure_dashboard_file(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    dashboard = site_root / "american-pressure" / "dashboard" / "index.html"
    dashboard.parent.mkdir(parents=True, exist_ok=True)
    dashboard.write_text("American Pressure Dashboard / 2026-05-09", encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    assert (pages_repo / "american-pressure" / "dashboard" / "index.html").exists()


def test_publish_pages_copies_american_pressure_map_files(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    map_dir = site_root / "american-pressure" / "map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "index.html").write_text("American Pressure Map", encoding="utf-8")
    (map_dir / "map_data.json").write_text(json.dumps({"pins": [], "needs_location": []}), encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    assert (pages_repo / "american-pressure" / "map" / "index.html").exists()
    assert (pages_repo / "american-pressure" / "map" / "map_data.json").exists()


def test_pages_publish_copies_gaza_audio_and_feed_artifacts(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "index.html").write_text("<html>Root home</html>", encoding="utf-8")
    (pages_repo / "CNAME").write_text(CNAME_VALUE + "\n", encoding="utf-8")
    gaza_audio_dir = site_root / "gaza" / "audio"
    gaza_audio_dir.mkdir(parents=True, exist_ok=True)
    (gaza_audio_dir / "index.html").write_text("<html>Audio archive</html>", encoding="utf-8")
    (gaza_audio_dir / "2026-05-31-transcript.html").write_text("<html>Transcript</html>", encoding="utf-8")
    (site_root / "gaza" / "podcast.xml").write_text("<rss/>", encoding="utf-8")
    (site_root / "gaza" / "flash-briefing.json").write_text("[]", encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("gaza",),
    )

    assert result["ok"] is True
    assert (pages_repo / "gaza" / "audio" / "index.html").exists()
    assert (pages_repo / "gaza" / "audio" / "2026-05-31-transcript.html").exists()
    assert (pages_repo / "gaza" / "podcast.xml").exists()
    assert (pages_repo / "gaza" / "flash-briefing.json").exists()
    assert (pages_repo / "index.html").read_text(encoding="utf-8") == (site_root / "index.html").read_text(encoding="utf-8")
    assert (pages_repo / "CNAME").read_text(encoding="utf-8").strip() == CNAME_VALUE
    assert not (pages_repo / "detail").exists()
    assert not (pages_repo / "paid").exists()
    assert "output/paid/" in result["files_that_would_be_skipped"]
    assert "output/detail/" in result["files_that_would_be_skipped"]


def test_pages_publish_rejects_gaza_history_shrink_on_archive_and_audio_surfaces(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    add_gaza_public_history_surface(site_root, ["2026-07-04"])
    add_gaza_site_edition(site_root, "2026-07-04")
    add_gaza_public_history_surface(pages_repo, ["2026-07-03", "2026-07-04"])
    add_gaza_site_edition(pages_repo, "2026-07-03")
    add_gaza_site_edition(pages_repo, "2026-07-04")
    backup_root = work / "backups"

    def fake_build_site(*args, **kwargs):
        return {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-07-04"}],
        }

    monkeypatch.setattr(generator, "build_site", fake_build_site)

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("gaza",),
    )

    assert result["ok"] is False
    assert any("gaza public history shrink detected" in error for error in result["errors"])
    assert any(item["surface"] == "gaza/archive.html" and item["dropped_dates"] == ["2026-07-03"] for item in result["gaza_public_surface_history"])
    assert any(item["surface"] == "gaza/audio/index.html" and item["dropped_dates"] == ["2026-07-03"] for item in result["gaza_public_surface_history"])


def test_pages_publish_preserves_gaza_audio_history_from_explicit_pages_repo_when_source_has_no_audio(
    tmp_path,
    monkeypatch,
):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html>Root</html>", encoding="utf-8")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    site_dates = [
        "2026-08-07",
        "2026-08-05",
        "2026-08-04",
        "2026-08-03",
        "2026-08-01",
        "2026-07-31",
        "2026-07-30",
        "2026-07-29",
        "2026-07-28",
        "2026-07-27",
    ]
    homepage_items = "".join(
        f'<li class="edition-item"><span class="edition-date">{date_text}</span><a href="editions/{date_text}/">Edition</a></li>'
        for date_text in site_dates
    )
    gaza_root = site_root / "gaza"
    gaza_root.mkdir(parents=True, exist_ok=True)
    (gaza_root / "index.html").write_text(f'<html><body><ul class="edition-list">{homepage_items}</ul></body></html>', encoding="utf-8")
    archive_links = "".join(f'<a href="editions/{date_text}/">{date_text}</a>' for date_text in site_dates)
    (gaza_root / "archive.html").write_text(f"<html><body>{archive_links}</body></html>", encoding="utf-8")
    rss_items = "".join(f"<item><link>https://dispatches.thebluefernco.com/gaza/editions/{date_text}/</link></item>" for date_text in site_dates)
    (gaza_root / "rss.xml").write_text(f"<rss><channel>{rss_items}</channel></rss>", encoding="utf-8")
    add_gaza_site_edition(site_root, "2026-08-07")
    add_gaza_public_history_surface(
        pages_repo,
        [
            "2026-08-05",
            "2026-08-04",
            "2026-08-03",
            "2026-08-01",
            "2026-07-31",
            "2026-07-30",
            "2026-07-29",
            "2026-07-28",
            "2026-07-27",
            "2026-07-26",
        ],
        archive_dates=["2026-08-07"],
        audio_dates=["2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
    )
    add_gaza_site_edition(pages_repo, "2026-08-07")
    audio_root = pages_repo / "gaza" / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    backup_root = work / "backups"

    def fake_build_site(*args, **kwargs):
        return {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-08-07"}],
        }

    monkeypatch.setattr(generator, "build_site", fake_build_site)

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("gaza",),
    )

    assert result["ok"] is True
    assert result["gaza_homepage_recent_edition_guard"]["decision"] == "allowed"
    assert result["gaza_homepage_recent_edition_guard"]["added_dates"] == ["2026-08-07"]
    assert result["gaza_homepage_recent_edition_guard"]["removed_dates"] == ["2026-07-26"]
    assert any(item["surface"] == "gaza/audio/index.html" and not item["dropped_dates"] for item in result["gaza_public_surface_history"])


def test_scoped_build_preserves_modern_root_homepage_for_gaza(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    pages_root = pages_repo / "index.html"
    pages_root_original = b"<!doctype html><html><body><main>Latest published developments</main></body></html>\n"
    pages_root.write_bytes(pages_root_original)
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    root_index = site_root / "index.html"
    stale_root = b"<!doctype html><html><body><main>obsolete root homepage</main></body></html>\n"
    root_index.write_bytes(stale_root)
    gaza_root = site_root / "gaza"
    gaza_root.mkdir(parents=True, exist_ok=True)
    gaza_root.joinpath("index.html").write_text("<html><body>stale gaza homepage</body></html>", encoding="utf-8")
    history_dates = [
        "2026-07-04",
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
    ]
    add_gaza_public_history_surface(site_root, history_dates, archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(site_root, "2026-07-04")
    add_gaza_public_history_surface(pages_repo, history_dates, archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(pages_repo, "2026-07-04")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=work / "backup",
        only_dispatches=("gaza",),
        allow_listing_shrink=True,
    )

    assert result["ok"] is True
    assert root_index.read_bytes() == stale_root
    assert pages_root.read_bytes() == pages_root_original
    assert "stale gaza homepage" not in gaza_root.joinpath("index.html").read_text(encoding="utf-8")
    assert "Dispatches From Gaza" in gaza_root.joinpath("index.html").read_text(encoding="utf-8")
    assert (site_root / "gaza" / "editions" / "2026-07-04" / "index.html").exists()
    assert (pages_repo / "gaza" / "editions" / "2026-07-04" / "index.html").exists()
    assert result["files_copied"]
    assert str(pages_repo / "index.html") not in result["files_copied"]
    assert str(pages_repo / "gaza" / "index.html") in result["files_copied"]


def test_pages_publish_allows_normal_gaza_homepage_rotation(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html>Root</html>", encoding="utf-8")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    add_gaza_public_history_surface(site_root, [
        "2026-07-04",
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
    ], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(site_root, "2026-07-04")
    add_gaza_public_history_surface(pages_repo, [
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
        "2026-06-24",
    ], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(pages_repo, "2026-07-04")

    monkeypatch.setattr(
        generator,
        "build_site",
        lambda *args, **kwargs: {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-07-04"}],
        },
    )

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=work / "backups", only_dispatches=("gaza",))

    assert result["ok"] is True
    assert result["gaza_homepage_recent_edition_guard"]["decision"] == "allowed"
    assert result["gaza_homepage_recent_edition_guard"]["added_dates"] == ["2026-07-04"]
    assert result["gaza_homepage_recent_edition_guard"]["removed_dates"] == ["2026-06-24"]


def test_pages_publish_rejects_sparse_gaza_homepage_collapse(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html>Root</html>", encoding="utf-8")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    add_gaza_public_history_surface(site_root, ["2026-07-04", "2026-07-03"], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(site_root, "2026-07-04")
    add_gaza_public_history_surface(pages_repo, [
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
        "2026-06-24",
    ], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(pages_repo, "2026-07-04")

    monkeypatch.setattr(
        generator,
        "build_site",
        lambda *args, **kwargs: {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-07-04"}],
        },
    )

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=work / "backups", only_dispatches=("gaza",))

    assert result["ok"] is False
    assert result["gaza_homepage_recent_edition_guard"]["decision"] == "blocked"
    assert any("recent-editions list below minimum" in reason for reason in result["gaza_homepage_recent_edition_guard"]["reasons"])
    assert any("gaza homepage recent-editions guard blocked publish" in error for error in result["errors"])


def test_pages_publish_rejects_gaza_homepage_missing_latest_expected_date(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html>Root</html>", encoding="utf-8")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    add_gaza_public_history_surface(site_root, [
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
        "2026-06-24",
    ], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(site_root, "2026-07-04")
    add_gaza_public_history_surface(pages_repo, [
        "2026-07-04",
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
    ], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(pages_repo, "2026-07-04")

    monkeypatch.setattr(
        generator,
        "build_site",
        lambda *args, **kwargs: {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-07-04"}],
        },
    )

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=work / "backups", only_dispatches=("gaza",))

    assert result["ok"] is False
    assert result["gaza_homepage_recent_edition_guard"]["decision"] == "blocked"
    assert any("latest expected edition date" in reason for reason in result["gaza_homepage_recent_edition_guard"]["reasons"])


def test_pages_publish_uses_explicit_pages_repo_for_gaza_homepage_history(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html>Root</html>", encoding="utf-8")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    prior_dates = [
        "2026-08-05",
        "2026-08-04",
        "2026-08-03",
        "2026-08-01",
        "2026-07-31",
        "2026-07-30",
        "2026-07-29",
        "2026-07-28",
        "2026-07-27",
        "2026-07-26",
    ]
    add_gaza_public_history_surface(site_root, [
        "2026-08-07",
        "2026-08-05",
        "2026-08-04",
        "2026-08-03",
        "2026-08-01",
        "2026-07-31",
        "2026-07-30",
        "2026-07-29",
        "2026-07-28",
        "2026-07-27",
    ], archive_dates=prior_dates, audio_dates=prior_dates)
    add_gaza_site_edition(site_root, "2026-08-07")
    add_gaza_public_history_surface(pages_repo, prior_dates, archive_dates=prior_dates, audio_dates=prior_dates)
    add_gaza_site_edition(pages_repo, "2026-08-05")

    monkeypatch.setattr(
        generator,
        "build_site",
        lambda *args, **kwargs: {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-08-07"}],
        },
    )

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=work / "backups", only_dispatches=("gaza",))

    assert result["ok"] is True
    assert result["gaza_homepage_recent_edition_guard"]["decision"] == "allowed"
    assert result["gaza_homepage_recent_edition_guard"]["new_dates"][0] == "2026-08-07"
    assert result["gaza_homepage_recent_edition_guard"]["removed_dates"] == ["2026-07-26"]


def test_pages_publish_allows_gaza_homepage_shrink_with_explicit_override(tmp_path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "index.html").write_text("<html>Root</html>", encoding="utf-8")
    site_root = work / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Home</html>", encoding="utf-8")
    add_gaza_public_history_surface(site_root, ["2026-07-04", "2026-07-03"], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(site_root, "2026-07-04")
    add_gaza_public_history_surface(pages_repo, [
        "2026-07-03",
        "2026-07-02",
        "2026-07-01",
        "2026-06-30",
        "2026-06-29",
        "2026-06-28",
        "2026-06-27",
        "2026-06-26",
        "2026-06-25",
        "2026-06-24",
    ], archive_dates=["2026-07-04"], audio_dates=["2026-07-04"])
    add_gaza_site_edition(pages_repo, "2026-07-04")

    monkeypatch.setattr(
        generator,
        "build_site",
        lambda *args, **kwargs: {
            "ok": True,
            "warnings": [],
            "errors": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_editions_backfilled": [],
            "gaza_editions_skipped": [],
            "gaza_archive_entries_written": [{"edition_date": "2026-07-04"}],
        },
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=work / "backups",
        only_dispatches=("gaza",),
        allow_listing_shrink=True,
    )

    assert result["ok"] is True
    assert result["gaza_homepage_recent_edition_guard"]["decision"] == "allowed_by_override"


def test_pages_publish_copies_food_line_audio_map_and_feed_artifacts(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    site_root = work / "output" / "site"
    food_root = site_root / "food-line"
    assets = food_root / "assets"
    edition = food_root / "editions" / "2026-06-01"
    audio = food_root / "audio"
    fmap = food_root / "map"
    assets.mkdir(parents=True, exist_ok=True)
    edition.mkdir(parents=True, exist_ok=True)
    audio.mkdir(parents=True, exist_ok=True)
    fmap.mkdir(parents=True, exist_ok=True)
    repo_assets = work / "assets"
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>2026-06-01</html>", encoding="utf-8")
    (food_root / "rss.xml").write_text("<rss/>", encoding="utf-8")
    (food_root / "podcast.xml").write_text("<rss/>", encoding="utf-8")
    (audio / "podcast.xml").write_text("<rss/>", encoding="utf-8")
    (assets / "food-line-logo.png").write_bytes((repo_assets / "food-line-logo.png").read_bytes())
    (assets / "site.css").write_text("body{}", encoding="utf-8")
    (assets / "bluefern.png").write_bytes((repo_assets / "bluefern.png").read_bytes())
    (edition / "index.html").write_text("<html>Food Line edition</html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": "2026-06-01",
                    "public_story_count": 1,
                    "public_rendered": True,
                    "edition_mode": "current_update",
                    "source_freshness_status": "passed",
                    "freshness_window_days": 14,
                    "stale_public_story_count": 0,
                    "excluded_stale_source_count": 0,
                    "stale_source_ids": [],
                    "qualified_primary_count": 1,
                    "skip_reason": "",
                },
                indent=2,
            ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "food-src-1", "title": "Source", "url": "https://example.com"}], indent=2),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "food-story-1", "source_ids": ["food-src-1"]}], indent=2),
        encoding="utf-8",
    )
    (fmap / "index.html").write_text("<html>Food Line map</html>", encoding="utf-8")
    (fmap / "map_data.json").write_text(json.dumps({"edition_date": "2026-06-01", "markers": []}), encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert (pages_repo / "food-line" / "index.html").exists()
    assert (pages_repo / "food-line" / "editions" / "2026-06-01" / "index.html").exists()
    assert (pages_repo / "food-line" / "map" / "index.html").exists()
    assert (pages_repo / "food-line" / "audio" / "podcast.xml").exists()
    assert (pages_repo / "food-line" / "podcast.xml").exists()
    assert (pages_repo / "food-line" / "assets" / "food-line-logo.png").exists()
    assert (pages_repo / "food-line" / "assets" / "food-line-dispatch-social.png").exists()


def test_pages_publish_removes_nested_duplicate_dispatch_trees(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    site_root = work / "output" / "site"
    food_root = site_root / "food-line"
    food_root.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Food Line archive</html>", encoding="utf-8")

    for root in (food_root / "food-line", pages_repo / "food-line" / "food-line"):
        (root / "editions" / "2026-06-01").mkdir(parents=True, exist_ok=True)
        (root / "index.html").write_text("<html>Nested duplicate</html>", encoding="utf-8")
        (root / "archive.html").write_text("<html>Nested archive</html>", encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert not (food_root / "food-line").exists()
    assert not (pages_repo / "food-line" / "food-line").exists()
    assert result["nested_duplicate_dispatch_paths_removed"]
    assert any("food-line/food-line" in path.replace("\\", "/") for path in result["nested_duplicate_dispatch_paths_removed"])


def test_pages_publish_removes_skipped_food_line_editions(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    site_root = work / "output" / "site"
    food_root = site_root / "food-line"
    stale_edition = pages_repo / "food-line" / "editions" / "2026-06-01"
    stale_edition.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Archive</html>", encoding="utf-8")
    (food_root / "rss.xml").write_text("<rss/>", encoding="utf-8")
    (food_root / "podcast.xml").write_text("<rss/>", encoding="utf-8")
    (stale_edition / "index.html").write_text("<html>Stale skipped edition</html>", encoding="utf-8")
    (stale_edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": "2026-06-01",
                "public_rendered": False,
                "qualified_primary_count": 0,
                "skip_reason": "No new primary food-access signal qualified for public Food Line publication.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert not (pages_repo / "food-line" / "editions" / "2026-06-01" / "index.html").exists()


def test_pages_publish_preserves_existing_food_line_history_when_source_site_is_scoped_to_new_backfill(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    site_root = work / "output" / "site"
    food_root = site_root / "food-line"

    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Archive</html>", encoding="utf-8")
    (food_root / "rss.xml").write_text("<rss/>", encoding="utf-8")
    (food_root / "podcast.xml").write_text("<rss/>", encoding="utf-8")

    write_min_food_line_public_edition(
        pages_repo,
        "2026-06-25",
        body_html="<html><body>Pages 2026-06-25</body></html>",
    )
    write_min_food_line_public_edition(
        pages_repo,
        "2026-06-26",
        body_html="<html><body>Pages 2026-06-26</body></html>",
    )
    write_min_food_line_public_edition(
        site_root,
        "2026-06-27",
        body_html="<html><body>Site 2026-06-27</body></html>",
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert (pages_repo / "food-line" / "editions" / "2026-06-25" / "index.html").exists()
    assert (pages_repo / "food-line" / "editions" / "2026-06-26" / "index.html").exists()
    assert (pages_repo / "food-line" / "editions" / "2026-06-27" / "index.html").read_text(encoding="utf-8") == "<html><body>Site 2026-06-27</body></html>"
    preserved = {(item["dispatch"], item["edition_date"]) for item in result["public_pages_editions_preserved"]}
    assert ("food-line", "2026-06-25") in preserved
    assert ("food-line", "2026-06-26") in preserved


def test_pages_publish_preserves_prior_food_line_backfills_across_sequential_dates(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    site_root = work / "output" / "site"
    food_root = site_root / "food-line"

    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Archive</html>", encoding="utf-8")
    (food_root / "rss.xml").write_text("<rss/>", encoding="utf-8")
    (food_root / "podcast.xml").write_text("<rss/>", encoding="utf-8")

    write_min_food_line_public_edition(
        pages_repo,
        "2026-06-25",
        body_html="<html><body>Pages 2026-06-25</body></html>",
    )
    write_min_food_line_public_edition(
        site_root,
        "2026-06-27",
        body_html="<html><body>Site 2026-06-27</body></html>",
    )

    first = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert first["ok"] is True
    assert (pages_repo / "food-line" / "editions" / "2026-06-25" / "index.html").exists()
    assert (pages_repo / "food-line" / "editions" / "2026-06-27" / "index.html").exists()

    shutil.rmtree(site_root / "food-line" / "editions" / "2026-06-27")
    write_min_food_line_public_edition(
        site_root,
        "2026-06-28",
        body_html="<html><body>Site 2026-06-28</body></html>",
    )

    second = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert second["ok"] is True
    for edition_date in ("2026-06-25", "2026-06-27", "2026-06-28"):
        assert (pages_repo / "food-line" / "editions" / edition_date / "index.html").exists()


def test_food_line_only_dispatch_publish_does_not_copy_other_dispatch_files(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    food_root = site_root / "food-line"
    edition = food_root / "editions" / "2026-06-01"
    edition.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>2026-06-01</html>", encoding="utf-8")
    (food_root / "rss.xml").write_text("<rss/>", encoding="utf-8")
    (edition / "index.html").write_text("<html>Food Line edition</html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": "2026-06-01",
                "public_story_count": 1,
                "public_rendered": True,
                "qualified_primary_count": 1,
                "skip_reason": "",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "food-src-1", "title": "Source", "url": "https://example.com"}], indent=2),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "food-story-1", "source_ids": ["food-src-1"]}], indent=2),
        encoding="utf-8",
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-06-01",
        expect_dispatches=("food-line",),
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    copied = result["files_copied"]
    assert copied
    assert not any("\\gaza\\" in path.lower() or "/gaza/" in path.lower() for path in copied)
    assert not any("\\cascadia\\" in path.lower() or "/cascadia/" in path.lower() for path in copied)
    assert not any("\\american-pressure\\" in path.lower() or "/american-pressure/" in path.lower() for path in copied)


@pytest.mark.parametrize("nested_path", [
    "food-line/food-line/index.html",
    "gaza/gaza/index.html",
    "cascadia/cascadia/index.html",
])
def test_validate_pages_repo_copy_scope_rejects_nested_duplicate_dispatch_paths(tmp_path: Path, nested_path: str):
    pages_repo = tmp_path / "pages"
    pages_repo.mkdir()
    errors = validate_pages_repo_copy_scope(
        pages_repo,
        ("food-line",),
        changed_paths=[pages_repo / nested_path],
    )

    assert any("nested duplicate dispatch path" in error for error in errors)


def test_dry_run_reports_nested_duplicate_pages_paths_without_removing_them(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    food_root = work / "output" / "site" / "food-line"
    food_root.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Food Line archive</html>", encoding="utf-8")
    nested = pages_repo / "food-line" / "food-line"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "index.html").write_text("nested", encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=True,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert nested.exists()
    assert any("food-line/food-line" in path.replace("\\", "/") for path in result["nested_duplicate_dispatch_paths_that_would_be_removed"])


def test_pages_repo_validation_failure_leaves_nested_duplicate_pages_paths_intact(built_site, monkeypatch):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    food_root = work / "output" / "site" / "food-line"
    food_root.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Food Line archive</html>", encoding="utf-8")
    nested = pages_repo / "food-line" / "food-line"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "index.html").write_text("nested", encoding="utf-8")

    def fail_pages_branch(*args, **kwargs):
        return {
            "current_branch": "main",
            "target_pages_branch": "gh-pages",
            "checked_out_branch": None,
            "fetch_attempted": False,
            "fetched": False,
            "created_pages_branch": False,
            "warnings": [],
            "errors": ["pages_repo_not_synced_with_origin: local HEAD is behind origin/gh-pages."],
        }

    monkeypatch.setattr(generator, "ensure_pages_branch", fail_pages_branch)

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert result["ok"] is False
    assert any("pages_repo_not_synced_with_origin" in error for error in result["errors"])
    assert nested.exists()
    assert result["nested_duplicate_dispatch_paths_removed"] == []


def test_real_publish_removes_nested_duplicate_pages_paths_after_validation_passes(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    food_root = work / "output" / "site" / "food-line"
    food_root.mkdir(parents=True, exist_ok=True)
    (food_root / "index.html").write_text("<html>Food Line home</html>", encoding="utf-8")
    (food_root / "archive.html").write_text("<html>Food Line archive</html>", encoding="utf-8")
    nested = pages_repo / "food-line" / "food-line"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "index.html").write_text("nested", encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert not nested.exists()
    assert any("food-line/food-line" in path.replace("\\", "/") for path in result["nested_duplicate_dispatch_paths_removed"])


def test_attached_landing_index_contains_american_pressure_map_button():
    repo = Path(__file__).parent.parent
    attached = repo / "bluefern-dispatches-pages" / "assets" / "index_updated_logo.html"
    fallback = repo / "output" / "site_bluefern_root" / "index.html"
    path = attached if attached.exists() else fallback
    if not path.exists():
        pytest.skip("attached landing index fixture not present in this checkout")
    html = path.read_text(encoding="utf-8")
    assert "The American Pressure Map" in html
    assert "https://dispatches.thebluefernco.com/american-pressure/map/" in html


def test_bluehost_root_upload_index_contains_american_pressure_map_button():
    repo = Path(__file__).parent.parent
    path = repo / "output" / "site_bluefern_root" / "index.html"
    if not path.exists():
        pytest.skip("site_bluefern_root fixture not present in this checkout")
    html = path.read_text(encoding="utf-8")
    assert "The American Pressure Map" in html
    assert "https://dispatches.thebluefernco.com/american-pressure/map/" in html


def test_commit_flag_does_not_imply_push(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(work, pages_repo, None, dry_run=True, commit=True, no_push=True, backup_root=backup_root)

    assert result["would_commit"] is True
    assert result["committed"] is False
    assert result["would_push"] is False
    assert result["pushed"] is False
    assert result["no_push"] is True
    assert result["target_pages_branch"] == "gh-pages"
    assert "git push origin gh-pages" in result["manual_push_command"]


def test_pages_publish_commits_on_gh_pages_branch(built_site):
    work, backup_root, _ = built_site
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=True, no_push=True, backup_root=backup_root, pages_branch="gh-pages")

    assert result["ok"] is True
    assert result["current_branch"] == "main"
    assert result["checked_out_branch"] == "gh-pages"
    assert result["committed_branch"] == "gh-pages"
    assert result["committed"] is True
    assert result["would_push"] is False
    assert result["pushed"] is False
    assert (pages_repo / ".git").exists()
    assert (pages_repo / "CNAME").read_text(encoding="utf-8").strip() == CNAME_VALUE


def test_gaza_public_lists_merge_pages_repo_history_when_local_site_is_sparser(tmp_path: Path):
    site_root = tmp_path / "output" / "site"
    local_edition = site_root / "gaza" / "editions" / "2026-07-04"
    local_edition.mkdir(parents=True, exist_ok=True)
    (local_edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "gaza",
                "edition_date": "2026-07-04",
                "source_count": 1,
                "story_count": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (local_edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "gaza-local-1"}], indent=2),
        encoding="utf-8",
    )
    (local_edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "gaza-local-story-1"}], indent=2),
        encoding="utf-8",
    )

    pages_edition = tmp_path / "bluefern-dispatches-pages" / "gaza" / "editions" / "2026-07-03"
    pages_edition.mkdir(parents=True, exist_ok=True)
    (pages_edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "gaza",
                "edition_date": "2026-07-03",
                "source_count": 1,
                "story_count": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (pages_edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "gaza-pages-1"}], indent=2),
        encoding="utf-8",
    )
    (pages_edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "gaza-pages-story-1"}], indent=2),
        encoding="utf-8",
    )

    dates = generator.discover_public_edition_dates(site_root, "gaza")
    dispatch = DispatchConfig(slug="gaza", name="Dispatches From Gaza", edition_date="2026-07-04", tagline="Daily briefing", logo="gaza-logo.png", sources=[], stories=[], detail_artifacts=[])
    index_html = generator.render_dispatch_index_for_dates(dispatch, dates, site_root)
    archive_html = generator.render_archive_for_dates(dispatch, dates, site_root)
    rss_xml = generator.render_rss_for_dates(dispatch, dates, site_root)

    assert dates == ["2026-07-04", "2026-07-03"]
    for body in (index_html, archive_html, rss_xml):
        assert "2026-07-04" in body
        assert "2026-07-03" in body


def test_gaza_public_edition_discovery_uses_explicit_pages_repo_for_sibling_clones(tmp_path: Path):
    site_root = tmp_path / "source" / "output" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    local_dates = [
        "2026-08-05",
        "2026-06-20",
        "2026-06-18",
        "2026-06-16",
        "2026-05-04",
        "2026-05-03",
    ]
    add_gaza_public_history_surface(site_root, local_dates)
    for edition_date in local_dates:
        add_gaza_site_edition(site_root, edition_date)

    pages_repo = tmp_path / "pages"
    pages_dates = [
        "2026-08-04",
        "2026-08-03",
        "2026-08-01",
        "2026-07-31",
    ]
    add_gaza_public_history_surface(pages_repo, pages_dates)
    for edition_date in pages_dates:
        add_gaza_site_edition(pages_repo, edition_date)

    inferred_dates = generator.discover_public_edition_dates(site_root, "gaza")
    explicit_dates = generator.discover_public_edition_dates(site_root, "gaza", pages_repo=pages_repo)

    assert inferred_dates == local_dates
    assert explicit_dates == [
        "2026-08-05",
        "2026-08-04",
        "2026-08-03",
        "2026-08-01",
        "2026-07-31",
        "2026-06-20",
        "2026-06-18",
        "2026-06-16",
        "2026-05-04",
        "2026-05-03",
    ]


def test_gaza_public_seeding_prefers_explicit_pages_repo_history_over_stale_source_site(tmp_path: Path):
    repo = Path(__file__).parent.parent
    work = tmp_path / "repo"
    copy_repo_assets(repo, work)
    stale_source_site = work / "output" / "site"
    add_gaza_public_history_surface(
        stale_source_site,
        ["2026-08-08", "2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
        archive_dates=["2026-08-08", "2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
        audio_dates=["2026-08-08", "2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
    )
    add_gaza_site_edition(stale_source_site, "2026-08-08")
    add_gaza_site_edition(stale_source_site, "2026-08-07")

    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    add_gaza_public_history_surface(
        pages_repo,
        ["2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27"],
        archive_dates=["2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27"],
        audio_dates=["2026-08-07", "2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27"],
    )
    add_gaza_site_edition(pages_repo, "2026-08-07")

    result = build_site(
        work,
        dry_run=False,
        backup_root=work / "backups",
        only_dispatches=("gaza",),
        pages_repo=pages_repo,
    )

    html = (work / "output" / "site" / "gaza" / "index.html").read_text(encoding="utf-8")
    assert result["ok"] is True
    assert "2026-08-07" in html
    assert "2026-08-08" not in html
    assert generator.discover_public_edition_dates(pages_repo, "gaza") == ["2026-08-07"]


def test_gaza_current_generated_edition_merges_explicit_pages_history_without_stale_future_date(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    add_gaza_public_history_surface(
        pages_repo,
        ["2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
        archive_dates=["2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
        audio_dates=["2026-08-05", "2026-08-04", "2026-08-03", "2026-08-01", "2026-07-31", "2026-07-30", "2026-07-29", "2026-07-28", "2026-07-27", "2026-07-26"],
    )
    for edition_date in [
        "2026-08-05",
        "2026-08-04",
        "2026-08-03",
        "2026-08-01",
        "2026-07-31",
        "2026-07-30",
        "2026-07-29",
        "2026-07-28",
        "2026-07-27",
        "2026-07-26",
    ]:
        add_gaza_site_edition(pages_repo, edition_date)
    add_gaza_site_edition(site_root, "2026-08-07")
    add_gaza_site_edition(site_root, "2026-08-08")
    stale_future_manifest = site_root / "gaza" / "editions" / "2026-08-08" / "edition_manifest.json"
    stale_future_payload = json.loads(stale_future_manifest.read_text(encoding="utf-8"))
    stale_future_payload["errors"] = [
        "No new source-backed Gaza developments after cross-edition dedupe; refusing to publish repeated edition."
    ]
    stale_future_manifest.write_text(json.dumps(stale_future_payload, indent=2), encoding="utf-8")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-08-07",
        expect_dispatches=("gaza",),
        only_dispatches=("gaza",),
    )

    html = (site_root / "gaza" / "index.html").read_text(encoding="utf-8")
    guard = result["gaza_homepage_recent_edition_guard"]
    assert result["ok"] is True
    assert guard["added_dates"] == ["2026-08-07"]
    assert guard["removed_dates"] == ["2026-07-26"]
    assert guard["latest_expected_date"] == "2026-08-07"
    assert "2026-08-07" in html
    assert "2026-08-08" not in html


def test_seed_dispatches_uses_gaza_historical_seed_without_changing_other_dispatches(tmp_path: Path, monkeypatch):
    work = tmp_path / "repo"
    work.mkdir()
    copy_repo_assets(Path(__file__).parent.parent, work)
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-08-08")

    dispatches = seed_dispatches(
        work,
        "2026-08-08T12:00:00Z",
        [],
        [],
        dispatch_seed_dates={"gaza": "2026-08-07"},
    )

    by_slug = {dispatch.slug: dispatch for dispatch in dispatches}
    assert by_slug["gaza"].edition_date == "2026-08-07"
    assert by_slug["american-pressure"].edition_date == "2026-08-08"


def test_only_dispatch_cascadia_bypasses_gaza_fallback_failure(monkeypatch):
    repo = Path(__file__).parent.parent
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    copy_repo_assets(repo, work)
    add_cascadia_dispatch_edition(work, "2026-05-10")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-10")

    def fail_all_gaza_candidates(*args, **kwargs):
        return [], {"input_candidate_count": 1, "suppressed_candidate_count": 1}

    monkeypatch.setattr("bluefern_dispatches.generator.filter_recent_duplicate_sources", fail_all_gaza_candidates)

    full = build_site(work, dry_run=False, backup_root=work / "backup")
    cascadia_only = build_site(work, dry_run=False, backup_root=work / "backup", only_dispatches=("cascadia",))

    assert full["ok"] is False
    assert any("No new source-backed Gaza developments after cross-edition dedupe" in error for error in full["errors"])
    assert cascadia_only["ok"] is True
    assert (work / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "index.html").exists()
    root_index = (work / "output" / "site" / "index.html").read_text(encoding="utf-8")
    assert "Dispatches From Gaza" in root_index
    assert "Food Line Dispatch" in root_index
    assert "The Care Line Dispatch" in root_index
    assert "The American Pressure Dispatch" not in root_index


def test_targeted_ap_publish_refreshes_map_date_label_and_payload(built_site):
    work, backup_root, _ = built_site
    site_root = work / "output" / "site"
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    repo = Path(__file__).parent.parent
    src_dispatch_edition = repo / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    dst_dispatch_edition = work / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23"
    if src_dispatch_edition.exists():
        shutil.copytree(src_dispatch_edition, dst_dispatch_edition, dirs_exist_ok=True)
    map_dir = site_root / "american-pressure" / "map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "index.html").write_text(
        '<p class="ap-map-subtitle">Source-backed signs of household or community strain across the U.S. (May 10–May 16, 2026).</p>',
        encoding="utf-8",
    )
    (map_dir / "map_data.json").write_text(
        json.dumps({"edition_date": "2026-05-16", "display_date_range": "May 10–May 16, 2026"}, indent=2),
        encoding="utf-8",
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date="2026-05-23",
        expect_dispatches=("american-pressure",),
        only_dispatches=("american-pressure",),
    )

    assert result["ok"] is True
    pages_map_html = read(pages_repo / "american-pressure" / "map" / "index.html")
    pages_map_payload = json.loads(read(pages_repo / "american-pressure" / "map" / "map_data.json"))
    assert "May 17–May 23, 2026" in pages_map_html
    assert "/american-pressure/dashboard/" not in pages_map_html
    assert pages_map_payload.get("edition_date") == "2026-05-23"


def test_cascadia_only_publish_copies_map_files(built_site):
    work, backup_root, _ = built_site
    edition_dir = work / "output" / "dispatches" / "cascadia" / "editions" / "2026-05-03"
    edition_dir.mkdir(parents=True, exist_ok=True)
    (edition_dir / "map.html").write_text("<html><body>map</body></html>", encoding="utf-8")
    (edition_dir / "map_data.json").write_text(json.dumps({"markers": []}, indent=2), encoding="utf-8")
    (edition_dir / "source_table.html").write_text("<html><body>source table</body></html>", encoding="utf-8")
    site_map_dir = work / "output" / "site" / "cascadia" / "map"
    site_map_dir.mkdir(parents=True, exist_ok=True)
    (site_map_dir / "source_table.html").write_text("<html><body>latest source table</body></html>", encoding="utf-8")
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        only_dispatches=("cascadia",),
    )

    assert result["ok"] is True
    assert (pages_repo / "cascadia" / "editions" / "2026-05-03" / "map.html").exists()
    assert (pages_repo / "cascadia" / "editions" / "2026-05-03" / "map_data.json").exists()
    assert (pages_repo / "cascadia" / "editions" / "2026-05-03" / "source_table.html").exists()
    assert (pages_repo / "cascadia" / "map" / "source_table.html").exists()
    assert result["only_dispatches"] == ["cascadia"]


def test_cascadia_publish_overwrites_stale_map_and_source_table_artifacts(built_site):
    work, backup_root, _ = built_site
    edition_date = "2026-05-03"
    site_root = work / "output" / "site"
    map_dir = site_root / "cascadia" / "map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "index.html").write_text("<html><body>Report count: 5</body></html>", encoding="utf-8")
    (map_dir / "source_table.html").write_text(
        "<html><body><table><tr><th>Pressure Area</th></tr><tr><td>Environment and climate</td></tr></table></body></html>",
        encoding="utf-8",
    )
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    stale_pages_map = pages_repo / "cascadia" / "map"
    stale_pages_map.mkdir(parents=True, exist_ok=True)
    (stale_pages_map / "index.html").write_text("<html><body>Report count: 11</body></html>", encoding="utf-8")
    (stale_pages_map / "source_table.html").write_text(
        "<html><body>Open latest Cascadia map WA government ID government</body></html>",
        encoding="utf-8",
    )

    result = publish_pages(
        work,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=backup_root,
        expect_date=edition_date,
        only_dispatches=("cascadia",),
    )

    assert result["ok"] is True
    paths = [
        site_root / "cascadia" / "map" / "index.html",
        site_root / "cascadia" / "map" / "source_table.html",
        pages_repo / "cascadia" / "map" / "index.html",
        pages_repo / "cascadia" / "map" / "source_table.html",
        pages_repo / "cascadia" / "editions" / edition_date / "map.html",
        pages_repo / "cascadia" / "editions" / edition_date / "source_table.html",
    ]
    final_strings = "\n".join(read(path) for path in paths if path.exists())
    forbidden = [
        "Report count: 11",
        "No reports match the current map filters",
        "Some map markers could not be displayed",
        "Open latest Cascadia map",
        "WA government",
        "ID government",
        "source_count:",
        "known_gaps:",
        "states_covered:",
    ]
    for needle in forbidden:
        assert needle not in final_strings


def test_normalize_only_dispatches_accepts_comma_and_repeat():
    assert normalize_only_dispatches(("cascadia", "gaza,cascadia")) == ("cascadia", "gaza")
