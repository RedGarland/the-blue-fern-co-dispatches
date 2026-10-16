import json
from pathlib import Path

import pytest

from bluefern_dispatches.generator import (
    DispatchConfig,
    _gaza_aggregate_render_from_pages_history,
    briefing_presentation_for_edition,
)
import scripts.run_gaza_dispatch as gaza_runner


DATE = "2026-10-16"
OLD_CORRECTION = "OCT14-CORRECTION-RECORD-MUST-REMAIN-BYTE-IDENTIFIABLE"
OLD_NO_UPDATE = "October 15, 2026 — No new source-backed Gaza update met publication threshold today."
OLD_DAILY_SUMMARY = "Historical daily summary that must not be regenerated."


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fixture_roots(tmp_path: Path) -> tuple[Path, Path, DispatchConfig]:
    site_root = tmp_path / "site"
    pages_repo = tmp_path / "pages"
    edition = site_root / "gaza" / "editions" / DATE
    edition.mkdir(parents=True)
    (pages_repo / "gaza").mkdir(parents=True)
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "edition_date": DATE,
                "public_archive_title": "UNICEF documents the struggle for survival in Gaza",
                "public_archive_subtitle": "A limited-source update from UNICEF.",
                "public_story_count": 1,
                "public_source_count": 1,
                "source_adequacy_status": "limited_source_update",
                "source_adequacy_label": "Limited-source update",
            }
        ),
        encoding="utf-8",
    )
    dispatch = DispatchConfig(
        slug="gaza",
        name="Dispatches From Gaza",
        edition_date=DATE,
        tagline="Daily briefing",
        logo="gaza-logo.png",
        sources=[],
        stories=[],
    )

    old_archive_row = (
        '      <li class="archive-row archive-row--daily"><span class="edition-date">2026-10-14</span>'
        '<span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-14/">'
        'Correction history</a><span class="archive-row-note">'
        f'{OLD_CORRECTION}</span></span><a class="briefing-row-cta" href="editions/2026-10-14/">Read</a></li>'
    )
    old_readable_row = (
        '      <li class="briefing-row briefing-row--readable"><span class="edition-date">2026-10-14</span>'
        '<span class="briefing-row-main"><a class="briefing-row-title" href="editions/2026-10-14/">'
        "Correction history</a>"
        f'<span class="briefing-row-summary">{OLD_CORRECTION}</span>'
        '<span class="briefing-row-meta">Correction | 1 source</span></span>'
        '<a class="briefing-row-cta" href="editions/2026-10-14/">Read briefing</a></li>'
    )
    archive = (
        '<!doctype html><html><body>\n'
        '    <section class="archive-latest" aria-label="Latest archive entry">\n'
        '      <h2><a href="editions/2026-10-15/">2026-10-15</a></h2>\n'
        '      <p>Old latest entry.</p>\n'
        "    </section>\n"
        '    <section class="archive-month"><h2>October 2026</h2>\n'
        '      <ul class="edition-list archive-list">\n'
        f"{old_archive_row}\n"
        '      <li class="archive-row archive-row--no-update"><span class="edition-date">2026-10-15</span>'
        '<span class="no-update-label">No qualifying update</span></li>\n'
        f'      <li><a href="editions/2026-10-13/">{OLD_DAILY_SUMMARY}</a></li>\n'
        "      </ul>\n    </section>\n"
        '</body></html>\n'
    )
    index = (
        '<!doctype html><html><body>\n'
        '    <article class="gaza-latest-card">\n'
        '      <p class="edition-date">2026-10-15</p>\n'
        '      <h3>Daily briefing</h3>\n'
        '      <p class="topic-badge topic-badge--gaza">Daily briefing</p>\n'
        "    </article>\n"
        f"    <p>{OLD_NO_UPDATE}</p>\n"
        '    <ul class="edition-list gaza-readable-list">\n'
        f"{old_readable_row}\n"
        "    </ul>\n"
        '</body></html>\n'
    )
    correction_item = (
        "  <item>\n"
        "    <title>Oct. 14 correction record</title>\n"
        "    <link>https://dispatches.thebluefernco.com/gaza/editions/2026-10-14/</link>\n"
        f"    <guid>{OLD_CORRECTION}</guid>\n"
        "    <description>Correction RSS record.</description>\n"
        "  </item>"
    )
    rss = (
        '<rss><channel>\n'
        "  <title>Dispatches From Gaza</title>\n"
        "  <description>Saved Gaza updates.</description>\n"
        f"{correction_item}\n"
        "  <item>\n"
        "    <title>Old daily item</title>\n"
        "    <link>https://dispatches.thebluefernco.com/gaza/editions/2026-10-13/</link>\n"
        "    <guid>old-daily-guid</guid>\n"
        f"    <description>{OLD_DAILY_SUMMARY}</description>\n"
        "  </item>\n"
        "</channel></rss>\n"
    )
    write(pages_repo / "gaza/archive.html", archive)
    write(pages_repo / "gaza/index.html", index)
    write(pages_repo / "gaza/rss.xml", rss)
    return site_root, pages_repo, dispatch


def test_pages_history_preserves_corrections_and_no_update_notice(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    wrote: list[str] = []

    assert _gaza_aggregate_render_from_pages_history(
        site_root,
        dispatch,
        DATE,
        pages_repo,
        dry_run=False,
        wrote=wrote,
    ) is True
    assert {Path(path).name for path in wrote} == {"archive.html", "index.html", "rss.xml"}

    archive = (site_root / "gaza" / "archive.html").read_text(encoding="utf-8")
    index = (site_root / "gaza" / "index.html").read_text(encoding="utf-8")
    rss = (site_root / "gaza" / "rss.xml").read_text(encoding="utf-8")
    assert OLD_CORRECTION in archive
    assert OLD_CORRECTION in rss
    assert OLD_NO_UPDATE in index
    assert OLD_DAILY_SUMMARY in archive
    assert archive.count("editions/2026-10-16/") >= 2
    assert index.count("editions/2026-10-16/") >= 2
    assert rss.count("editions/2026-10-16/") == 2
    assert "Limited-source update" in archive
    assert "Limited-source update" in index
    assert "Limited-source update" in rss


def test_partial_current_date_identity_is_rejected(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    archive = (pages_repo / "gaza" / "archive.html").read_text(encoding="utf-8")
    write(pages_repo / "gaza" / "archive.html", archive.replace("2026-10-15", DATE, 1))
    with pytest.raises(ValueError, match="partial current-date identity"):
        _gaza_aggregate_render_from_pages_history(
            site_root,
            dispatch,
            DATE,
            pages_repo,
            dry_run=True,
            wrote=[],
        )


def test_new_month_is_created_when_pages_archive_has_no_month_section(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    archive_path = pages_repo / "gaza" / "archive.html"
    archive = archive_path.read_text(encoding="utf-8")
    start = archive.index('    <section class="archive-month">')
    end = archive.index('    </section>', start) + len('    </section>')
    archive_path.write_text(archive[:start] + archive[end + 1 :], encoding="utf-8")

    assert _gaza_aggregate_render_from_pages_history(
        site_root,
        dispatch,
        DATE,
        pages_repo,
        dry_run=False,
        wrote=[],
    ) is True
    rendered_archive = (site_root / "gaza" / "archive.html").read_text(encoding="utf-8")
    assert '<section class="archive-month"><h2>October 2026</h2>' in rendered_archive
    assert 'editions/2026-10-16/' in rendered_archive


def test_backdated_recovery_does_not_replace_actual_latest_entry(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    assert _gaza_aggregate_render_from_pages_history(
        site_root,
        dispatch,
        "2026-10-12",
        pages_repo,
        dry_run=False,
        wrote=[],
    ) is True
    rendered_archive = (site_root / "gaza" / "archive.html").read_text(encoding="utf-8")
    rendered_index = (site_root / "gaza" / "index.html").read_text(encoding="utf-8")
    rendered_rss = (site_root / "gaza" / "rss.xml").read_text(encoding="utf-8")
    assert '<h2><a href="editions/2026-10-15/">2026-10-15</a></h2>' in rendered_archive
    assert '<p class="edition-date">2026-10-15</p>' in rendered_index
    assert rendered_archive.index("editions/2026-10-12/") > rendered_archive.index("editions/2026-10-14/")
    assert rendered_index.index("editions/2026-10-12/") > rendered_index.index("editions/2026-10-14/")
    assert rendered_rss.index("editions/2026-10-12/") > rendered_rss.index("editions/2026-10-14/")


@pytest.mark.parametrize("run_kind", ["no-update", "failed-render"])
def test_non_current_render_preserves_pages_history(tmp_path: Path, monkeypatch, run_kind: str):
    _site_root, pages_repo, _dispatch = fixture_roots(tmp_path)
    root = tmp_path
    (root / "output" / "site" / "gaza").mkdir(parents=True)

    monkeypatch.setattr(gaza_runner, "reconcile_gaza_public_editions", lambda *args, **kwargs: None)
    monkeypatch.setattr(gaza_runner, "discover_edition_dates", lambda *args, **kwargs: [])
    monkeypatch.setattr(gaza_runner, "_default_pages_repo", lambda *args, **kwargs: pages_repo)
    wrote: list[str] = []
    gaza_runner.render_archive_index_rss(
        root,
        DATE,
        dry_run=False,
        wrote=wrote,
        include_current=False,
        pages_repo=pages_repo,
    )
    for filename in ("index.html", "archive.html", "rss.xml"):
        assert (root / "output" / "site" / "gaza" / filename).read_bytes() == (
            pages_repo / "gaza" / filename
        ).read_bytes(), run_kind
    assert {Path(path).name for path in wrote} == {"index.html", "archive.html", "rss.xml"}


def test_established_pages_history_missing_surface_fails_closed(tmp_path: Path, monkeypatch):
    _site_root, pages_repo, _dispatch = fixture_roots(tmp_path)
    (pages_repo / "gaza" / "rss.xml").unlink()
    root = tmp_path
    (root / "output" / "site" / "gaza").mkdir(parents=True)
    monkeypatch.setattr(gaza_runner, "reconcile_gaza_public_editions", lambda *args, **kwargs: None)
    monkeypatch.setattr(gaza_runner, "discover_edition_dates", lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match="authoritative Gaza aggregate baseline incomplete/unreadable"):
        gaza_runner.render_archive_index_rss(
            root,
            DATE,
            dry_run=False,
            wrote=[],
            include_current=False,
            pages_repo=pages_repo,
        )
    assert not (root / "output" / "site" / "gaza" / "archive.html").exists()


def test_empty_pages_bootstrap_allows_generic_aggregate_render(tmp_path: Path, monkeypatch):
    root = tmp_path
    pages_repo = root / "pages"
    pages_repo.mkdir()
    (root / "output" / "site" / "gaza").mkdir(parents=True)
    monkeypatch.setattr(gaza_runner, "reconcile_gaza_public_editions", lambda *args, **kwargs: None)
    monkeypatch.setattr(gaza_runner, "discover_edition_dates", lambda *args, **kwargs: [])
    wrote: list[str] = []
    gaza_runner.render_archive_index_rss(
        root,
        DATE,
        dry_run=False,
        wrote=wrote,
        include_current=False,
        pages_repo=pages_repo,
    )
    assert {Path(path).name for path in wrote} == {"index.html", "archive.html", "rss.xml"}


def test_pages_history_without_aggregates_fails_closed(tmp_path: Path):
    site_root = tmp_path / "site"
    pages_repo = tmp_path / "pages"
    (site_root / "gaza" / "editions" / DATE).mkdir(parents=True)
    write(pages_repo / "gaza" / "editions" / "2026-10-15" / "index.html", "historical edition")
    dispatch = DispatchConfig(
        slug="gaza",
        name="Dispatches From Gaza",
        edition_date=DATE,
        tagline="Daily briefing",
        logo="gaza-logo.png",
        sources=[],
        stories=[],
    )
    with pytest.raises(ValueError, match="Gaza history exists but archive.html"):
        _gaza_aggregate_render_from_pages_history(
            site_root,
            dispatch,
            DATE,
            pages_repo,
            dry_run=True,
            wrote=[],
        )


def test_current_date_exact_repeat_is_idempotent_but_mismatch_requires_correction_workflow(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    _gaza_aggregate_render_from_pages_history(
        site_root, dispatch, DATE, pages_repo, dry_run=False, wrote=[]
    )
    for filename in ("index.html", "archive.html", "rss.xml"):
        write(
            pages_repo / "gaza" / filename,
            (site_root / "gaza" / filename).read_text(encoding="utf-8"),
        )
    original = {filename: (pages_repo / "gaza" / filename).read_bytes() for filename in ("index.html", "archive.html", "rss.xml")}
    assert _gaza_aggregate_render_from_pages_history(
        site_root, dispatch, DATE, pages_repo, dry_run=False, wrote=[]
    ) is True
    assert {filename: (site_root / "gaza" / filename).read_bytes() for filename in original} == original

    manifest_path = site_root / "gaza" / "editions" / DATE / "edition_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["public_archive_title"] = "Changed input must not overwrite established history"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="separate correction workflow"):
        _gaza_aggregate_render_from_pages_history(
            site_root, dispatch, DATE, pages_repo, dry_run=True, wrote=[]
        )


def test_aggregate_insertion_handles_no_update_rows_and_irregular_rss_items(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    archive_path = pages_repo / "gaza" / "archive.html"
    archive = archive_path.read_text(encoding="utf-8")
    correction = archive[archive.index('      <li class="archive-row archive-row--daily"'):archive.index("\n      <li class=\"archive-row archive-row--no-update\"")]
    no_update = archive[archive.index('      <li class="archive-row archive-row--no-update"'):archive.index("\n      <li><a href=")]
    archive = archive.replace(correction + "\n" + no_update, no_update + "\n" + correction)
    archive_path.write_text(archive, encoding="utf-8")
    rss_path = pages_repo / "gaza" / "rss.xml"
    rss_path.write_text(rss_path.read_text(encoding="utf-8").replace("  <item>", "<item>"), encoding="utf-8")

    assert _gaza_aggregate_render_from_pages_history(
        site_root, dispatch, DATE, pages_repo, dry_run=False, wrote=[]
    ) is True
    rendered_archive = (site_root / "gaza" / "archive.html").read_text(encoding="utf-8")
    rendered_rss = (site_root / "gaza" / "rss.xml").read_text(encoding="utf-8")
    assert rendered_archive.index("editions/2026-10-16/") < rendered_archive.index("2026-10-15")
    assert rendered_rss.index("editions/2026-10-16/") < rendered_rss.index("editions/2026-10-14/")


def test_backdated_missing_month_is_inserted_in_date_order(tmp_path: Path):
    site_root, pages_repo, dispatch = fixture_roots(tmp_path)
    assert _gaza_aggregate_render_from_pages_history(
        site_root, dispatch, "2026-03-10", pages_repo, dry_run=False, wrote=[]
    ) is True
    rendered_archive = (site_root / "gaza" / "archive.html").read_text(encoding="utf-8")
    assert rendered_archive.index("October 2026") < rendered_archive.index("March 2026")


def test_limited_source_manifest_gets_explicit_aggregate_label(tmp_path: Path):
    site_root, _pages_repo, dispatch = fixture_roots(tmp_path)
    presentation = briefing_presentation_for_edition(site_root, dispatch, DATE)
    assert presentation.type_label == "Limited-source update"
