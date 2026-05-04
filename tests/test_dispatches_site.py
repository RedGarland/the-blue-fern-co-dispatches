import json
import shutil
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.generator import (
    BASE_URL,
    DEFAULT_BACKUP_ROOT,
    DispatchConfig,
    SourceRecord,
    StoryRecord,
    build_site,
    ensure_public_detail_separation,
    validate_traceability,
)


@pytest.fixture()
def built_site():
    repo = Path(__file__).resolve().parents[1]
    test_root = repo / "output" / "test-runs" / uuid.uuid4().hex
    work = test_root / "repo"
    shutil.copytree(repo / "assets", work / "assets")
    backup_root = test_root / "dispatches-bluefern-backups"
    result = build_site(work, dry_run=False, backup_root=backup_root)
    return work, backup_root, result


def read(path):
    return path.read_text(encoding="utf-8")


def test_landing_page_links_and_blue_fern_scheme(built_site):
    work, _, result = built_site
    index = work / "output" / "site" / "index.html"
    css = work / "output" / "site" / "assets" / "site.css"

    assert result["ok"] is True
    assert index.exists()
    html = read(index)
    assert 'href="/gaza/"' in html
    assert 'href="/cascadia/"' in html
    assert "Dispatches From The Blue Fern Co." in html
    assert "--blue-fern: #2F6F88" in read(css)
    assert "assets/bluefern.png" in html


def test_gaza_content_and_requested_logo_placement(built_site):
    work, _, _ = built_site
    gaza_index = read(work / "output" / "site" / "gaza" / "index.html")
    gaza_edition = read(work / "output" / "site" / "gaza" / "editions" / "2026-05-03" / "index.html")

    assert 'src="assets/gaza-logo.png"' in gaza_index
    assert 'src="assets/bluefern.png"' in gaza_index
    assert 'href="https://thebluefernco.com/"' in gaza_index
    assert "Israel has issued threats to resume war in\nGaza" in gaza_edition
    assert "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza" in gaza_edition
    assert 'src="../../assets/gaza-logo.png"' in gaza_edition
    assert 'src="../../assets/bluefern.png"' in gaza_edition


def test_cascadia_page_and_dated_edition_url(built_site):
    work, _, _ = built_site
    cascadia_index = read(work / "output" / "site" / "cascadia" / "index.html")
    cascadia_edition = work / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html"

    assert "Cascadia Systems Dispatch" in cascadia_index
    assert "cascadia-logo-placeholder.png" in cascadia_index
    assert cascadia_edition.exists()
    assert f"{BASE_URL}/cascadia/editions/2026-05-03/" in read(cascadia_edition)
    assert "class=\"briefing\"" in read(cascadia_edition)


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


def test_detail_roots_inside_public_site_are_rejected():
    site_root = Path("output") / "site"
    assert ensure_public_detail_separation(site_root, [site_root / "paid"])
