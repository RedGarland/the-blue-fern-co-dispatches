import json
from pathlib import Path

import pytest

import bluefern_dispatches.generator as generator


AUTH_FILES = {
    "curation_manifest.json": "[{}]",
    "dedupe_report.json": "{}",
    "index.html": "authoritative",
    "source_quality_report.md": "quality",
    "sources_manifest.json": "[{}]",
}


def write_authority(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name, body in AUTH_FILES.items():
        (path / name).write_text(body, encoding="utf-8")


def test_real_authority_render_and_links_stay_consistent(tmp_path: Path) -> None:
    root = tmp_path / "runner"
    pages = tmp_path / "pages"
    backup = tmp_path / "backup"
    target = root / "output" / "site" / "gaza" / "editions" / "2026-10-14"
    authority = pages / "gaza" / "editions" / "2026-10-14"
    target.mkdir(parents=True)
    write_authority(authority)
    source = generator.SourceRecord(
        "source-1", "Example source", "https://example.test/gaza-source", "Example Publisher",
        "2026-10-14T12:00:00Z", "2026-10-14T13:00:00Z", None,
        ["story-1"], ["claim-1"], "gaza", "2026-10-14",
    )
    dispatch = generator.DispatchConfig(
        "gaza", "Dispatches From Gaza", "2026-10-14",
        "Daily source-backed Gaza briefings.", "gaza-logo.png", [source], [],
        body_html=f'<p>Correction source: <a href="{source.url}">{source.title}</a></p>',
    )
    (authority / "index.html").write_text(generator.render_edition(dispatch), encoding="utf-8")
    (authority / "edition_manifest.json").write_text(json.dumps({
        "dispatch_slug": "gaza",
        "edition_date": "2026-10-14",
        "publication_state": "private_correction_preview",
        "public_exposed": False,
        "publication_authorized": False,
        "source_count": 0,
        "story_count": 0,
        "saved_source_count": 1,
        "corrections": [{"correction_id": "fixture-correction"}],
    }), encoding="utf-8")
    pages_gaza = pages / "gaza"
    pages_gaza.mkdir(parents=True, exist_ok=True)
    (pages_gaza / "index.html").write_text(
        generator.render_dispatch_index_for_dates(dispatch, ["2026-10-14"], site_root=pages, pages_repo=pages),
        encoding="utf-8",
    )
    (pages_gaza / "archive.html").write_text(
        generator.render_archive_for_dates(dispatch, ["2026-10-14"], site_root=pages, pages_repo=pages),
        encoding="utf-8",
    )
    (pages_gaza / "rss.xml").write_text(
        generator.render_rss_for_dates(dispatch, ["2026-10-14"], site_root=pages, pages_repo=pages),
        encoding="utf-8",
    )
    (target / "index.html").write_text("generated-conflict", encoding="utf-8")

    assert generator._gaza_pages_correction_only_edition_dir(pages, "2026-10-14") == authority
    generator.reconcile_gaza_public_editions(
        root, root / "output" / "site", False, [], pages_repo=pages, backup_root=backup
    )
    restored = (target / "index.html").read_text(encoding="utf-8")
    assert restored == (authority / "index.html").read_text(encoding="utf-8")
    assert source.url in restored

    site_gaza = root / "output" / "site" / "gaza"
    site_gaza.mkdir(parents=True, exist_ok=True)
    site_root = root / "output" / "site"
    (site_gaza / "index.html").write_text(
        generator.render_dispatch_index_for_dates(dispatch, ["2026-10-14"], site_root=site_root, pages_repo=pages),
        encoding="utf-8",
    )
    (site_gaza / "archive.html").write_text(
        generator.render_archive_for_dates(dispatch, ["2026-10-14"], site_root=site_root, pages_repo=pages),
        encoding="utf-8",
    )
    (site_gaza / "rss.xml").write_text(
        generator.render_rss_for_dates(dispatch, ["2026-10-14"], site_root=site_root, pages_repo=pages),
        encoding="utf-8",
    )
    assert generator.validate_gaza_public_link_consistency(site_root, pages_repo=pages) == []


def test_publish_pages_checks_lexical_pages_before_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "runner"
    pages = tmp_path / "pages"
    pages_alias = tmp_path / "pages-alias"
    root.mkdir()
    pages.mkdir()
    observed: list[Path] = []
    original_guard = generator._reconciliation_assert_ordinary_path

    def record_guard(path: Path) -> None:
        observed.append(Path(path))
        if Path(path) == pages_alias:
            raise ValueError("lexical alias guard fixture")
        original_guard(path)

    monkeypatch.setattr(generator, "_reconciliation_assert_ordinary_path", record_guard)
    with pytest.raises(ValueError, match="lexical alias guard fixture"):
        generator.publish_pages(root, pages_alias, None, True, False, True)
    assert observed == [root, pages_alias]
