from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.public_site_staging import (
    DISPATCH_DIRECTORY_TEMPLATE,
    _assert_staging_integrity,
    stage_public_site_preview,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(path: Path, payload: dict) -> None:
    _write(path, json.dumps(payload, indent=2, sort_keys=True))


def _make_source(root: Path) -> Path:
    _write(root / "assets" / "site.css", "body { background: current; }")
    for asset in ("gaza-logo.png", "food-line-logo.png", "care-line-logo.png", "bluefern.png"):
        _write(root / "assets" / asset, "fake image")
    return root


def _edition_manifest(slug: str, date: str, title: str) -> dict:
    return {
        "dispatch_slug": slug,
        "edition_date": date,
        "public_archive_title": title,
        "public_release_status": "published",
        "pages_release_status": "synced",
        "source_count": 2,
    }


def _write_public_edition(site_root: Path, slug: str, date: str, title: str) -> None:
    edition = site_root / slug / "editions" / date
    _write(edition / "index.html", f"<!doctype html><html><body><h1>{title}</h1></body></html>")
    _json(edition / "edition_manifest.json", _edition_manifest(slug, date, title))
    _write(site_root / slug / "archive.html", f'<a href="editions/{date}/">{date}</a>')


def _make_food_runner(root: Path) -> Path:
    site = root / "output" / "site"
    _write(site / "index.html", DISPATCH_DIRECTORY_TEMPLATE)
    _write(site / "assets" / "site.css", "body { background: stale; }")
    _write(site / "gaza" / "index.html", "stale Gaza copied from Food runner")
    _write(site / "care-line" / "index.html", "stale Care copied from Food runner")
    _write_public_edition(site, "food-line", "2026-10-02", "KLFY and Kenosha food bank access pressure")
    _write(site / "food-line" / "index.html", '<link rel="stylesheet" href="/assets/site.css"><a href="editions/2026-10-02/">Latest Food</a>')
    return root


def _make_gaza_runner(root: Path) -> Path:
    site = root / "output" / "site"
    _write(site / "gaza" / "index.html", '<link rel="stylesheet" href="/assets/site.css"><a href="editions/2026-10-01/">Latest Gaza</a>')
    _write_public_edition(site, "gaza", "2026-10-01", "Gaza daily briefing")
    _write_public_edition(site, "gaza", "2026-10-03", "Gaza residue that is newer than the approved evidence horizon")
    _json(
        site / "gaza" / "status" / "no-updates" / "2026-10-02.json",
        {
            "date": "2026-10-02",
            "classification": "no_publication_needed",
            "public_story_count": 0,
            "run_completed_successfully": True,
            "message": "No new source-backed Gaza update met publication threshold today.",
            "source_count": 22,
            "run_manifest_path": "data/dispatches/gaza/editions/2026-10-02/run_manifest.json",
            "collection_report_path": "data/dispatches/gaza/editions/2026-10-02/source_collection_context.json",
        },
    )
    _json(
        site / "gaza" / "status" / "no-updates" / "2026-10-03.json",
        {
            "date": "2026-10-03",
            "classification": "no_publication_needed",
            "public_story_count": 0,
            "run_completed_successfully": True,
            "message": "No new source-backed Gaza update met publication threshold today.",
            "source_count": 99,
            "run_manifest_path": "data/dispatches/gaza/editions/2026-10-03/run_manifest.json",
            "collection_report_path": "data/dispatches/gaza/editions/2026-10-03/source_collection_context.json",
        },
    )
    return root


def _make_care_runner(root: Path) -> Path:
    site = root / "output" / "site"
    _write(
        site / "care-line" / "index.html",
        '<link rel="stylesheet" href="/assets/site.css"><img src="assets/bluefern.png" alt="The Blue Fern Co."><a href="editions/2026-09-30/">Latest Care</a>',
    )
    _write_public_edition(site, "care-line", "2026-09-30", "Care Line public access briefing")
    return root


def test_public_site_staging_builds_release_faithful_required_routes(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    food = _make_food_runner(tmp_path / "food")
    gaza = _make_gaza_runner(tmp_path / "gaza")
    care = _make_care_runner(tmp_path / "care")
    stage = tmp_path / "stage"

    result = stage_public_site_preview(
        source_root=source,
        output_root=stage,
        food_runner=food,
        gaza_runner=gaza,
        care_runner=care,
        max_gaza_date="2026-10-02",
        validate=False,
    )

    assert result["ok"] is True
    for relative in (
        "index.html",
        "dispatches/index.html",
        "gaza/index.html",
        "gaza/archive.html",
        "food-line/index.html",
        "food-line/editions/2026-10-02/index.html",
        "care-line/index.html",
    ):
        assert (stage / relative).exists(), relative
    assert (stage / "assets" / "site.css").read_text(encoding="utf-8") == "body { background: current; }"
    assert "stale Gaza copied from Food runner" not in (stage / "gaza" / "index.html").read_text(encoding="utf-8")
    assert "stale Care copied from Food runner" not in (stage / "care-line" / "index.html").read_text(encoding="utf-8")
    assert 'src="/assets/bluefern.png"' in (stage / "care-line" / "index.html").read_text(encoding="utf-8")


def test_public_site_staging_renders_gaza_archive_through_max_runner_date(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    stage = tmp_path / "stage"

    stage_public_site_preview(
        source_root=source,
        output_root=stage,
        food_runner=_make_food_runner(tmp_path / "food"),
        gaza_runner=_make_gaza_runner(tmp_path / "gaza"),
        care_runner=_make_care_runner(tmp_path / "care"),
        max_gaza_date="2026-10-02",
        validate=False,
    )

    archive = (stage / "gaza" / "archive.html").read_text(encoding="utf-8")
    gaza_index = (stage / "gaza" / "index.html").read_text(encoding="utf-8")
    latest = archive.split('<section class="archive-latest"', 1)[1].split("</section>", 1)[0]
    assert '<p class="archive-latest-date">2026-10-01</p>' in latest
    assert "No qualifying update" not in latest
    assert '<span class="no-update-label">No qualifying update</span><span class="archive-row-note">22 sources checked</span>' in archive
    assert "2026-10-03" not in archive
    assert 'href="editions/2026-10-03/"' not in archive
    assert (stage / "gaza" / "status" / "no-updates-after-staging-horizon" / "2026-10-03.json").exists()
    assert "<h2>Latest Readable Update</h2>" in gaza_index
    assert "<h2>Readable Briefings</h2>" in gaza_index
    assert "<h2>Recent Checks</h2>" in gaza_index
    assert "<h2>Recent Editions</h2>" not in gaza_index
    latest_section = gaza_index.split("<h2>Latest Readable Update</h2>", 1)[1].split("<h2>Recent Checks</h2>", 1)[0]
    readable_list = gaza_index.split('<ul class="edition-list gaza-readable-list">', 1)[1].split("</ul>", 1)[0]
    checks_list = gaza_index.split('<ul class="edition-list gaza-check-list">', 1)[1].split("</ul>", 1)[0]
    assert 'href="editions/2026-10-01/">Read the latest readable update</a>' in latest_section
    assert "No qualifying update" not in latest_section
    assert 'href="editions/2026-10-01/">2026-10-01</a>' in readable_list
    assert "2026-10-02" not in readable_list
    assert '<span class="edition-date">2026-10-02</span><span class="no-update-label">No qualifying update</span><span class="archive-row-note">22 sources checked</span>' in checks_list
    assert 'href="editions/2026-10-02/"' not in gaza_index


def test_public_site_staging_fails_fast_when_required_css_is_missing(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    (source / "assets" / "site.css").unlink()

    with pytest.raises(FileNotFoundError, match="site.css"):
        stage_public_site_preview(
            source_root=source,
            output_root=tmp_path / "stage",
            food_runner=_make_food_runner(tmp_path / "food"),
            gaza_runner=_make_gaza_runner(tmp_path / "gaza"),
            care_runner=_make_care_runner(tmp_path / "care"),
            max_gaza_date="2026-10-02",
            validate=False,
        )


def test_public_site_staging_fails_fast_when_required_logo_is_missing(tmp_path: Path) -> None:
    source = _make_source(tmp_path / "source")
    (source / "assets" / "gaza-logo.png").unlink()

    with pytest.raises(FileNotFoundError, match="gaza-logo"):
        stage_public_site_preview(
            source_root=source,
            output_root=tmp_path / "stage",
            food_runner=_make_food_runner(tmp_path / "food"),
            gaza_runner=_make_gaza_runner(tmp_path / "gaza"),
            care_runner=_make_care_runner(tmp_path / "care"),
            max_gaza_date="2026-10-02",
            validate=False,
        )


def test_public_site_staging_integrity_rejects_fallback_sentinel_html(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    _write(root / "care-line" / "index.html", "<html><body>Old Care</body></html>")

    with pytest.raises(RuntimeError, match="fallback sentinel"):
        _assert_staging_integrity(root, ["/care-line/"])


def test_public_site_staging_integrity_rejects_placeholder_date_on_shared_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    _write(root / "index.html", "<html><body>2026-08-05</body></html>")

    with pytest.raises(RuntimeError, match="staged shared-surface HTML"):
        _assert_staging_integrity(root, ["/"])


def test_public_site_staging_integrity_rejects_missing_required_route(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()

    with pytest.raises(RuntimeError, match="required staged route is missing"):
        _assert_staging_integrity(root, ["/dispatches/"])


def test_public_site_staging_integrity_rejects_broken_required_route_image(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    _write(root / "food-line" / "index.html", '<html><body><img src="assets/food-line-logo.png" alt="Food Line"></body></html>')

    with pytest.raises(RuntimeError, match="staged route image is missing"):
        _assert_staging_integrity(root, ["/food-line/"])


def test_public_site_staging_integrity_accepts_nested_relative_logo_path(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    _write(
        root / "food-line" / "editions" / "2026-10-02" / "index.html",
        '<html><body><img src="../../assets/food-line-logo.png" alt="Food Line"></body></html>',
    )
    _write(root / "food-line" / "assets" / "food-line-logo.png", "fake image")

    _assert_staging_integrity(root, ["/food-line/editions/2026-10-02/"])
