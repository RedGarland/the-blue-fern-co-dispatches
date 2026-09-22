from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import bluefern_dispatches.generator as generator
import bluefern_dispatches.pages_release_safety as pages_release_safety
from bluefern_dispatches.food_line_approved_proposal import build_release_manifest, write_json_deterministic


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _init_repo(root: Path, branch: str, *, empty_commit: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "tests@example.test")
    _run_git(root, "config", "user.name", "Tests")
    if empty_commit:
        _run_git(root, "commit", "--allow-empty", "-m", "initial")
    else:
        (root / "README.md").write_text("repo", encoding="utf-8")
        _run_git(root, "add", "README.md")
        _run_git(root, "commit", "-m", "initial")
    _run_git(root, "checkout", "-b", branch)
    return root


def _write_food_line_site(source_root: Path, dates: list[str]) -> None:
    site_root = source_root / "output" / "site" / "food-line"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Food Line index</html>", encoding="utf-8")
    (site_root / "archive.html").write_text("<html>Archive</html>", encoding="utf-8")
    (site_root / "rss.xml").write_text("<?xml version=\"1.0\" encoding=\"utf-8\"?><rss><channel></channel></rss>", encoding="utf-8")
    for date_text in dates:
        edition = site_root / "editions" / date_text
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<html>{date_text}</html>", encoding="utf-8")
        (edition / "edition_manifest.json").write_text(json.dumps({"edition_date": date_text}), encoding="utf-8")
        (edition / "sources_manifest.json").write_text(json.dumps([{"source_record_id": "src-1"}]), encoding="utf-8")
        (edition / "curation_manifest.json").write_text(json.dumps([{"story_id": "story-1"}]), encoding="utf-8")
        (edition / "source_table.html").write_text("<table><tr><td>1</td></tr></table>", encoding="utf-8")
        (edition / "claim_ledger.html").write_text("<html>Claims</html>", encoding="utf-8")


def _commit_repo(repo: Path, message: str = "update") -> None:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", message)


def _release_manifest(source: Path, pages: Path, date_text: str) -> Path:
    site = source / "output/site/food-line"
    edition = site / "editions" / date_text
    source_paths = [site / "index.html", site / "archive.html", *sorted(path for path in edition.iterdir() if path.is_file())]
    payload = build_release_manifest(
        root=source,
        pages_root=pages,
        edition_date=date_text,
        source_commit=_git_output(source, "rev-parse", "HEAD"),
        source_paths=source_paths,
    )
    path = source / "data/dispatches/food-line/review/releases" / f"{date_text}.json"
    write_json_deterministic(path, payload)
    return path


def _mark_retrospective(manifest_path: Path) -> None:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        entry["provenance_role"] = "approved_retrospective_generated_output"
    write_json_deterministic(manifest_path, payload)


def test_retrospective_pages_dry_run_rejects_history_shrink(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-08-30"])
    (source / "output/site/food-line/archive.html").write_text(
        '<a href="editions/2026-08-30/">new</a>', encoding="utf-8"
    )
    (source / "output/site/food-line/rss.xml").write_text(
        '<rss><channel><item><link>https://dispatches.thebluefernco.com/food-line/editions/2026-08-30/</link></item></channel></rss>',
        encoding="utf-8",
    )
    (pages / "food-line").mkdir(parents=True)
    (pages / "food-line/archive.html").write_text(
        '<a href="editions/2026-08-24/">old</a>', encoding="utf-8"
    )
    (pages / "food-line/rss.xml").write_text(
        '<rss><channel><item><link>https://dispatches.thebluefernco.com/food-line/editions/2026-08-24/</link></item></channel></rss>',
        encoding="utf-8",
    )
    _commit_repo(source, "retrospective candidate")
    _commit_repo(pages, "published history")
    manifest = _release_manifest(source, pages, "2026-08-30")
    _mark_retrospective(manifest)
    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-08-30"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
        include_rss=True,
    )
    assert report["ok"] is False
    assert any("archive dropped: ['2026-08-24']" in error for error in report["errors"])
    assert any("rss dropped: ['2026-08-24']" in error for error in report["errors"])


def _release_manifest_with_runtime_inputs(source: Path, pages: Path, date_text: str) -> Path:
    manifest_path = _release_manifest(source, pages, date_text)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    proposal = source / "data/dispatches/food-line/review/proposed-editions" / f"{date_text}.json"
    snapshot = source / "data/dispatches/food-line/review/signal-reviews" / f"{date_text}.json"
    payload["approved_proposal_path"] = proposal.relative_to(source).as_posix()
    payload["approved_proposal_sha256"] = hashlib.sha256(proposal.read_bytes()).hexdigest()
    payload["review_snapshot_path"] = snapshot.relative_to(source).as_posix()
    payload["review_snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    write_json_deterministic(manifest_path, payload)
    return manifest_path


@pytest.fixture()
def release_repos(tmp_path: Path) -> tuple[Path, Path]:
    source = _init_repo(tmp_path / "source", "add/pages-repo-default")
    pages = _init_repo(tmp_path / "bluefern-dispatches-pages", "gh-pages", empty_commit=True)
    return source, pages


def _write_gaza_no_update_site(source: Path, date_text: str = "2026-09-19") -> None:
    site = source / "output" / "site"
    gaza = site / "gaza"
    status = gaza / "status" / "no-updates"
    audio = gaza / "audio"
    edition = gaza / "editions" / date_text
    status.mkdir(parents=True, exist_ok=True)
    audio.mkdir(parents=True, exist_ok=True)
    edition.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<html>Site</html>", encoding="utf-8")
    (gaza / "index.html").write_text(
        '<html><ul class="edition-list"><li><a href="editions/2026-09-18/">Sep. 18</a></li><li><a href="editions/2026-09-17/">Sep. 17</a></li><li><a href="editions/2026-09-16/">Sep. 16</a></li></ul><p>September 19, 2026 - No new source-backed Gaza update met publication threshold today.</p></html>',
        encoding="utf-8",
    )
    (gaza / "archive.html").write_text(
        '<html><a href="editions/2026-09-18/">Sep. 18 edition</a><a href="editions/2026-09-17/">Sep. 17 edition</a><a href="editions/2026-09-16/">Sep. 16 edition</a><a href="status/no-updates/2026-09-19.json">Sep. 19 no update</a></html>',
        encoding="utf-8",
    )
    (gaza / "rss.xml").write_text("<rss><channel></channel></rss>", encoding="utf-8")
    (audio / "index.html").write_text('<span class="gaza-audio-index-date"><strong>2026-06-20</strong></span>', encoding="utf-8")
    (audio / "podcast.xml").write_text('<rss><link>/gaza/audio/2026-06-20-transcript.html</link></rss>', encoding="utf-8")
    (gaza / "podcast.xml").write_text('<rss><link>/gaza/audio/2026-06-20-transcript.html</link></rss>', encoding="utf-8")
    (edition / "index.html").write_text("<html>must not copy</html>", encoding="utf-8")
    (status / f"{date_text}.json").write_text(
        json.dumps(
            {
                "date": date_text,
                "classification": "no_publication_needed",
                "message": "No new source-backed Gaza update met publication threshold today.",
                "source_count": 4,
                "public_story_count": 0,
                "run_completed_successfully": True,
                "run_manifest_path": f"data/dispatches/gaza/editions/{date_text}/run_manifest.json",
                "collection_report_path": f"data/dispatches/gaza/editions/{date_text}/collection_report.json",
            }
        ),
        encoding="utf-8",
    )


def _write_gaza_pages_history(pages: Path) -> None:
    gaza = pages / "gaza"
    audio = gaza / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    for date_text in ("2026-09-18", "2026-09-17", "2026-09-16"):
        edition = gaza / "editions" / date_text
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<html>{date_text}</html>", encoding="utf-8")
    (gaza / "index.html").write_text('<html><ul class="edition-list"><li><a href="editions/2026-09-18/">Sep. 18</a></li><li><a href="editions/2026-09-17/">Sep. 17</a></li><li><a href="editions/2026-09-16/">Sep. 16</a></li></ul></html>', encoding="utf-8")
    (gaza / "archive.html").write_text('<html><a href="editions/2026-09-18/">Sep. 18</a><a href="editions/2026-09-17/">Sep. 17</a><a href="editions/2026-09-16/">Sep. 16</a></html>', encoding="utf-8")
    (gaza / "rss.xml").write_text("<rss><channel></channel></rss>", encoding="utf-8")
    (audio / "index.html").write_text('<span class="gaza-audio-index-date"><strong>2026-09-18</strong></span><span class="gaza-audio-index-date"><strong>2026-06-20</strong></span>', encoding="utf-8")
    (audio / "podcast.xml").write_text('<rss><link>/gaza/audio/2026-09-18-transcript.html</link><link>/gaza/audio/2026-06-20-transcript.html</link></rss>', encoding="utf-8")
    (gaza / "podcast.xml").write_text('<rss><link>/gaza/audio/2026-09-18-transcript.html</link><link>/gaza/audio/2026-06-20-transcript.html</link></rss>', encoding="utf-8")
    _commit_repo(pages, "published gaza history")


def _stub_gaza_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        generator,
        "build_site",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "public_urls": [],
            "gaza_editions_discovered": [],
            "gaza_archive_entries_written": [],
            "gaza_editions_skipped": [],
            "backfilled_public_editions": [],
        },
    )


def _rel_pages_paths(paths: list[str], pages: Path) -> list[str]:
    return sorted(Path(path).relative_to(pages).as_posix() for path in paths)


def test_gaza_no_update_scope_dry_run_copies_exact_status_files(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_gaza_no_update_site(source)
    _write_gaza_pages_history(pages)
    _stub_gaza_build(monkeypatch)

    report = generator.publish_pages(
        source,
        pages,
        remote_url=None,
        dry_run=True,
        commit=True,
        no_push=True,
        only_dispatches=("gaza",),
        artifact_family="no-update",
        expect_date="2026-09-19",
    )

    assert report["ok"] is True
    assert _rel_pages_paths(report["files_that_would_be_copied"], pages) == [
        "gaza/archive.html",
        "gaza/index.html",
        "gaza/status/no-updates/2026-09-19.json",
    ]
    assert not (pages / "gaza/status/no-updates/2026-09-19.json").exists()
    assert "2026-09-18" in (pages / "gaza/audio/index.html").read_text(encoding="utf-8")
    assert all("gaza/rss.xml" not in path for path in report["files_that_would_be_copied"])
    assert all("/audio/" not in path.replace("\\", "/") for path in report["files_that_would_be_copied"])
    assert all("/editions/" not in path.replace("\\", "/") for path in report["files_that_would_be_copied"])


def test_gaza_no_update_scope_local_commit_changes_only_intended_pages_files(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_gaza_no_update_site(source)
    _write_gaza_pages_history(pages)
    _stub_gaza_build(monkeypatch)

    report = generator.publish_pages(
        source,
        pages,
        remote_url=None,
        dry_run=False,
        commit=True,
        no_push=True,
        only_dispatches=("gaza",),
        artifact_family="no-update",
        expect_date="2026-09-19",
    )

    assert report["ok"] is True
    assert report["committed"] is True
    assert report["would_push"] is False
    assert _rel_pages_paths(report["files_copied"], pages) == [
        "gaza/archive.html",
        "gaza/index.html",
        "gaza/status/no-updates/2026-09-19.json",
    ]
    assert not (pages / "gaza/editions/2026-09-19/index.html").exists()
    assert "2026-09-18" in (pages / "gaza/audio/index.html").read_text(encoding="utf-8")
    assert "2026-09-18" in (pages / "gaza/podcast.xml").read_text(encoding="utf-8")


def test_gaza_no_update_scope_does_not_run_unrelated_pages_cleanup(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_gaza_no_update_site(source)
    _write_gaza_pages_history(pages)
    nested_duplicate = pages / "gaza" / "gaza" / "index.html"
    nested_duplicate.parent.mkdir(parents=True, exist_ok=True)
    nested_duplicate.write_text("preexisting unrelated artifact", encoding="utf-8")
    _commit_repo(pages, "preexisting nested duplicate")
    _stub_gaza_build(monkeypatch)

    report = generator.publish_pages(
        source,
        pages,
        remote_url=None,
        dry_run=False,
        commit=True,
        no_push=True,
        only_dispatches=("gaza",),
        artifact_family="no-update",
        expect_date="2026-09-19",
    )

    assert report["ok"] is True
    assert nested_duplicate.exists()
    assert report["nested_duplicate_dispatch_paths_removed"] == []


def test_gaza_no_update_scope_rejects_wrong_or_missing_status_json(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_gaza_no_update_site(source)
    _write_gaza_pages_history(pages)
    _stub_gaza_build(monkeypatch)
    status = source / "output/site/gaza/status/no-updates/2026-09-19.json"
    payload = json.loads(status.read_text(encoding="utf-8"))
    payload["date"] = "2026-09-18"
    status.write_text(json.dumps(payload), encoding="utf-8")

    wrong_date = generator.publish_pages(
        source,
        pages,
        remote_url=None,
        dry_run=True,
        commit=False,
        no_push=True,
        only_dispatches=("gaza",),
        artifact_family="no-update",
        expect_date="2026-09-19",
    )
    assert wrong_date["ok"] is False
    assert any("date mismatch" in error for error in wrong_date["errors"])

    status.unlink()
    missing = generator.publish_pages(
        source,
        pages,
        remote_url=None,
        dry_run=True,
        commit=False,
        no_push=True,
        only_dispatches=("gaza",),
        artifact_family="no-update",
        expect_date="2026-09-19",
    )
    assert missing["ok"] is False
    assert any("required Gaza no-update publish artifact is missing" in error for error in missing["errors"])


def test_gaza_no_update_copy_scope_rejects_unrelated_files(release_repos: tuple[Path, Path]) -> None:
    _source, pages = release_repos

    errors = generator.validate_pages_repo_copy_scope(
        pages,
        ("gaza",),
        changed_paths=[
            "gaza/index.html",
            "gaza/archive.html",
            "gaza/status/no-updates/2026-09-19.json",
            "gaza/rss.xml",
            "food-line/index.html",
        ],
        artifact_family="no-update",
        expect_date="2026-09-19",
    )

    assert any("gaza/rss.xml" in error for error in errors)
    assert any("food-line/index.html" in error for error in errors)


def test_normal_gaza_publish_still_enforces_audio_history_shrink(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_gaza_no_update_site(source)
    _write_gaza_pages_history(pages)
    _stub_gaza_build(monkeypatch)

    report = generator.publish_pages(
        source,
        pages,
        remote_url=None,
        dry_run=True,
        commit=False,
        no_push=True,
        only_dispatches=("gaza",),
    )

    assert report["ok"] is False
    assert any("gaza public history shrink detected for gaza/audio/index.html" in error for error in report["errors"])


def _write_gaza_history_guard_fixture(
    root: Path,
    *,
    archive_edition_dates: tuple[str, ...] = (),
    rss_edition_dates: tuple[str, ...] = (),
    audio_dates: tuple[str, ...] = (),
    no_update_dates: tuple[str, ...] = (),
) -> None:
    gaza = root / "gaza"
    audio = gaza / "audio"
    status = gaza / "status" / "no-updates"
    audio.mkdir(parents=True, exist_ok=True)
    archive_links = "".join(
        f'<a href="editions/{date_text}/">{date_text}</a>' for date_text in archive_edition_dates
    )
    no_update_rows = "".join(
        f'<li class="no-update"><span class="edition-date">{date_text}</span><span>No update</span></li>'
        for date_text in no_update_dates
    )
    (gaza / "archive.html").write_text(f"<html><body>{archive_links}{no_update_rows}</body></html>", encoding="utf-8")
    rss_items = "".join(
        f"<item><link>https://dispatches.thebluefernco.com/gaza/editions/{date_text}/</link></item>"
        for date_text in rss_edition_dates
    )
    (gaza / "rss.xml").write_text(f"<rss><channel>{rss_items}</channel></rss>", encoding="utf-8")
    audio_rows = "".join(
        f'<span class="gaza-audio-index-date"><strong>{date_text}</strong></span>' for date_text in audio_dates
    )
    (audio / "index.html").write_text(f"<html>{audio_rows}</html>", encoding="utf-8")
    audio_items = "".join(
        f"<item><link>https://dispatches.thebluefernco.com/gaza/audio/{date_text}-transcript.html</link></item>"
        for date_text in audio_dates
    )
    podcast = f"<rss><channel>{audio_items}</channel></rss>"
    (audio / "podcast.xml").write_text(podcast, encoding="utf-8")
    (gaza / "podcast.xml").write_text(podcast, encoding="utf-8")
    for date_text in archive_edition_dates:
        edition = gaza / "editions" / date_text
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<html>{date_text}</html>", encoding="utf-8")
    if no_update_dates:
        status.mkdir(parents=True, exist_ok=True)
    for date_text in no_update_dates:
        (status / f"{date_text}.json").write_text(
            json.dumps(
                {
                    "date": date_text,
                    "classification": "no_publication_needed",
                    "message": "No new source-backed Gaza update met publication threshold today.",
                    "source_count": 2,
                    "public_story_count": 0,
                    "run_completed_successfully": True,
                    "run_manifest_path": f"data/dispatches/gaza/editions/{date_text}/run_manifest.json",
                    "collection_report_path": f"data/dispatches/gaza/editions/{date_text}/collection_report.json",
                }
            ),
            encoding="utf-8",
        )


def _gaza_history_report(reports: list[dict[str, object]], surface: str) -> dict[str, object]:
    for report in reports:
        if report["surface"] == surface:
            return report
    raise AssertionError(f"missing Gaza history report for {surface}")


def test_gaza_archive_history_guard_treats_authoritative_no_update_as_preserved_history(tmp_path: Path) -> None:
    previous = tmp_path / "pages"
    current = tmp_path / "site"
    _write_gaza_history_guard_fixture(
        previous,
        archive_edition_dates=("2026-09-21",),
        no_update_dates=("2026-09-21",),
    )
    _write_gaza_history_guard_fixture(current, no_update_dates=("2026-09-21",))

    reports = generator._gaza_public_surface_history_diagnostics(previous, current)
    archive = _gaza_history_report(reports, "gaza/archive.html")

    assert "2026-09-21" in archive["preserved_dates"]
    assert archive["dropped_dates"] == []
    assert archive["ok"] is True


def test_gaza_archive_history_guard_still_blocks_actual_edition_deletion(tmp_path: Path) -> None:
    previous = tmp_path / "pages"
    current = tmp_path / "site"
    _write_gaza_history_guard_fixture(previous, archive_edition_dates=("2026-09-21",))
    _write_gaza_history_guard_fixture(current)

    archive = _gaza_history_report(
        generator._gaza_public_surface_history_diagnostics(previous, current),
        "gaza/archive.html",
    )

    assert archive["dropped_dates"] == ["2026-09-21"]
    assert archive["ok"] is False


def test_gaza_archive_history_guard_preserves_no_update_to_no_update(tmp_path: Path) -> None:
    previous = tmp_path / "pages"
    current = tmp_path / "site"
    _write_gaza_history_guard_fixture(previous, no_update_dates=("2026-09-21",))
    _write_gaza_history_guard_fixture(current, no_update_dates=("2026-09-21",))

    archive = _gaza_history_report(
        generator._gaza_public_surface_history_diagnostics(previous, current),
        "gaza/archive.html",
    )

    assert archive["preserved_dates"] == ["2026-09-21"]
    assert archive["dropped_dates"] == []
    assert archive["ok"] is True


def test_gaza_archive_history_guard_allows_no_update_to_normal_edition(tmp_path: Path) -> None:
    previous = tmp_path / "pages"
    current = tmp_path / "site"
    _write_gaza_history_guard_fixture(previous, no_update_dates=("2026-09-21",))
    _write_gaza_history_guard_fixture(current, archive_edition_dates=("2026-09-21",))

    archive = _gaza_history_report(
        generator._gaza_public_surface_history_diagnostics(previous, current),
        "gaza/archive.html",
    )

    assert archive["preserved_dates"] == ["2026-09-21"]
    assert archive["dropped_dates"] == []
    assert archive["ok"] is True


def test_gaza_archive_history_guard_blocks_no_update_deletion(tmp_path: Path) -> None:
    previous = tmp_path / "pages"
    current = tmp_path / "site"
    _write_gaza_history_guard_fixture(previous, no_update_dates=("2026-09-21",))
    _write_gaza_history_guard_fixture(current)

    archive = _gaza_history_report(
        generator._gaza_public_surface_history_diagnostics(previous, current),
        "gaza/archive.html",
    )

    assert archive["dropped_dates"] == ["2026-09-21"]
    assert archive["ok"] is False


def test_gaza_no_update_history_does_not_change_rss_or_audio_guards(tmp_path: Path) -> None:
    previous = tmp_path / "pages"
    current = tmp_path / "site"
    _write_gaza_history_guard_fixture(
        previous,
        rss_edition_dates=("2026-09-20",),
        audio_dates=("2026-09-18",),
        no_update_dates=("2026-09-21",),
    )
    _write_gaza_history_guard_fixture(current, no_update_dates=("2026-09-21",))

    reports = generator._gaza_public_surface_history_diagnostics(previous, current)

    assert _gaza_history_report(reports, "gaza/archive.html")["dropped_dates"] == []
    assert _gaza_history_report(reports, "gaza/rss.xml")["dropped_dates"] == ["2026-09-20"]
    assert _gaza_history_report(reports, "gaza/audio/index.html")["dropped_dates"] == ["2026-09-18"]
    assert _gaza_history_report(reports, "gaza/audio/podcast.xml")["dropped_dates"] == ["2026-09-18"]
    assert _gaza_history_report(reports, "gaza/podcast.xml")["dropped_dates"] == ["2026-09-18"]


def test_dry_run_reports_planned_paths_for_food_line_dates(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19", "2026-06-20"])
    _commit_repo(source, "food line site")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19", "2026-06-20"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is True
    assert report["commit_status"] == "dry-run"
    assert report["push_status"] == "dry-run"
    assert report["copied_paths"] == [
        "food-line/index.html",
        "food-line/archive.html",
        "food-line/editions/2026-06-19/claim_ledger.html",
        "food-line/editions/2026-06-19/curation_manifest.json",
        "food-line/editions/2026-06-19/edition_manifest.json",
        "food-line/editions/2026-06-19/index.html",
        "food-line/editions/2026-06-19/source_table.html",
        "food-line/editions/2026-06-19/sources_manifest.json",
        "food-line/editions/2026-06-20/claim_ledger.html",
        "food-line/editions/2026-06-20/curation_manifest.json",
        "food-line/editions/2026-06-20/edition_manifest.json",
        "food-line/editions/2026-06-20/index.html",
        "food-line/editions/2026-06-20/source_table.html",
        "food-line/editions/2026-06-20/sources_manifest.json",
    ]
    assert report["planned_pages_paths"] == report["copied_paths"]
    assert not (pages / "food-line").exists()


def test_allowed_path_validation_rejects_unexpected_pages_diff(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")

    monkeypatch.setattr(pages_release_safety, "_pages_changed_paths", lambda _repo: ["food-line/index.html", "notes.txt"])

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )

    assert report["ok"] is False
    assert any("unexpected Pages repo changes outside the allowed Food Line scope" in error for error in report["errors"])
    assert "notes.txt" in report["errors"][0]


def test_allowed_path_validation_can_permit_exact_shared_release_surfaces(release_repos: tuple[Path, Path]) -> None:
    _source, pages = release_repos

    errors = generator.validate_pages_repo_copy_scope(
        pages,
        ("food-line",),
        changed_paths=["food-line/index.html", "index.html", "dispatches/index.html"],
        allowed_shared_surface_changes=["index.html", "dispatches/index.html"],
    )

    assert errors == []


def test_allowed_path_validation_rejects_unsanctioned_dispatch_directory_change(release_repos: tuple[Path, Path]) -> None:
    _source, pages = release_repos

    errors = generator.validate_pages_repo_copy_scope(
        pages,
        ("food-line",),
        changed_paths=["food-line/index.html", "dispatches/index.html"],
        allowed_shared_surface_changes=[],
    )

    assert any("dispatches/index.html" in error for error in errors)


def test_allowed_path_validation_rejects_other_dispatch_and_root_paths(release_repos: tuple[Path, Path]) -> None:
    _source, pages = release_repos

    errors = generator.validate_pages_repo_copy_scope(
        pages,
        ("care-line",),
        changed_paths=["care-line/index.html", "dispatches/anything-else.html", "notes.html"],
        allowed_shared_surface_changes=["dispatches/index.html"],
    )

    assert any("dispatches/anything-else.html" in error for error in errors)
    assert any("notes.html" in error for error in errors)


def test_allowed_path_validation_rejects_invalid_shared_surface_authorization(release_repos: tuple[Path, Path]) -> None:
    _source, pages = release_repos

    errors = generator.validate_pages_repo_copy_scope(
        pages,
        ("gaza",),
        changed_paths=["gaza/index.html"],
        allowed_shared_surface_changes=["dispatches/anything-else.html"],
    )

    assert any("invalid shared release-surface authorization" in error for error in errors)


def test_missing_source_artifact_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    (source / "output" / "site" / "food-line" / "archive.html").unlink()
    _commit_repo(source, "food line site without archive")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is False
    assert any("missing required source artifact" in error for error in report["errors"])


def test_wrong_source_branch_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    _run_git(source, "checkout", "-b", "unexpected-branch")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is False
    assert any("source branch mismatch" in error for error in report["errors"])


def test_wrong_pages_branch_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    _run_git(pages, "checkout", "-b", "not-gh-pages")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is False
    assert any("pages repo branch mismatch" in error for error in report["errors"])


def test_pages_dirty_state_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    (pages / "dirty.txt").write_text("dirty", encoding="utf-8")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=False,
    )

    assert report["ok"] is False
    assert any("pages repo must be clean before sync" in error for error in report["errors"])


def test_food_line_source_repo_clean_accepts_allowed_untracked_runtime_paths(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, _pages = release_repos
    monkeypatch.setattr(
        pages_release_safety,
        "_git_status_entries",
        lambda _repo: [
            ("??", "data/dispatches/food-line/agent-inbox/file.json"),
            ("??", "data/dispatches/food-line/agent-intake/2026-08-14/file.json"),
            ("??", "data/dispatches/food-line/agent-intake/reports/2026-08-14/file.json"),
            ("??", "data/dispatches/food-line/review/proposed-editions/file.json"),
            ("??", "data/dispatches/food-line/review/signal-reviews/file.json"),
            ("??", "data/dispatches/food-line/discovery-runs/2026-08-14/file.json"),
            ("??", "data/agent-history-staging/food-line/file.txt"),
            ("??", "logs/food-line/file.log"),
            ("??", "status/food-line/file.json"),
        ],
    )

    assert pages_release_safety._food_line_source_repo_clean(source) is True


def test_food_line_source_repo_clean_rejects_tracked_and_unrelated_untracked_paths(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, _pages = release_repos
    monkeypatch.setattr(
        pages_release_safety,
        "_git_status_entries",
        lambda _repo: [
            (" M", "data/dispatches/food-line/agent-intake/2026-08-14/file.json"),
            ("??", "data/dispatches/food-line/random/file.json"),
            ("??", "data/dispatches/food-line/agent-intake-notes/file.json"),
        ],
    )

    assert pages_release_safety._food_line_source_repo_clean(source) is False


def test_commit_requires_clean_allowed_scope_and_cleans_repo(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        commit=True,
    )

    assert report["ok"] is True
    assert report["commit_status"] == "committed"
    assert report["push_status"] == "skipped"
    assert report["commit_hash"]
    assert pages_release_safety._repo_clean(pages)
    assert (pages / "food-line" / "editions" / "2026-06-19" / "index.html").exists()
    assert _git_output(pages, "branch", "--show-current") == "gh-pages"


def test_push_requires_commit(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        push=True,
    )

    assert report["ok"] is False
    assert "--push requires --commit." in report["errors"]


def test_live_check_url_construction_and_only_mode(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    seen_urls: list[str] = []

    def fake_status(url: str, timeout: int) -> int:
        seen_urls.append(url)
        return 200

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        live_check=True,
        live_check_only=True,
        cache_bust="abc 123",
        fetch_status=fake_status,
    )

    assert report["ok"] is True
    assert len(seen_urls) == 3
    assert seen_urls[0] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/?cache_bust=abc+123"
    assert seen_urls[1] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/sources_manifest.json?cache_bust=abc+123"
    assert seen_urls[2] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/curation_manifest.json?cache_bust=abc+123"


def test_date_validation_and_multiple_date_handling() -> None:
    assert pages_release_safety._parse_dates(["2026-06-19", "2026-06-20", "2026-06-19"]) == ("2026-06-19", "2026-06-20")
    assert pages_release_safety._parse_dates(["2026-06-19,2026-06-20"]) == ("2026-06-19", "2026-06-20")
    with pytest.raises(ValueError):
        pages_release_safety._parse_dates(["2026-06-31"])


def test_script_does_not_require_audio_map_podcast_assets_support(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    extra_root = source / "output" / "site" / "food-line"
    (extra_root / "audio").mkdir(parents=True, exist_ok=True)
    (extra_root / "audio" / "clip.mp3").write_bytes(b"audio")
    (extra_root / "map").mkdir(parents=True, exist_ok=True)
    (extra_root / "map" / "index.html").write_text("map", encoding="utf-8")
    (extra_root / "podcast.xml").write_text("<rss />", encoding="utf-8")
    (source / "output" / "site" / "assets").mkdir(parents=True, exist_ok=True)
    (source / "output" / "site" / "assets" / "site.css").write_text("body{}", encoding="utf-8")

    plan = pages_release_safety._build_copy_plan(source, pages, "food-line", ["2026-06-19"])
    planned = [path.relative_to(source).as_posix() for path in plan.source_paths]

    assert "output/site/food-line/audio/clip.mp3" not in planned
    assert "output/site/food-line/map/index.html" not in planned
    assert "output/site/food-line/podcast.xml" not in planned
    assert all("output/site/assets" not in path for path in planned)


def test_release_manifest_dry_run_ignores_unrelated_source_dirt_and_writes_nothing(
    release_repos: tuple[Path, Path],
) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    manifest = _release_manifest(source, pages, "2026-06-19")
    (source / "unrelated-dirty.txt").write_text("not in release", encoding="utf-8")
    before_status = _git_output(pages, "status", "--porcelain=v1", "--untracked-files=all")
    before_files = sorted(path.relative_to(pages).as_posix() for path in pages.rglob("*") if path.is_file() and ".git" not in path.parts)

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is True
    assert len(report["additions"]) == 8
    assert report["modifications"] == []
    assert report["deletions"] == []
    assert _git_output(pages, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert sorted(path.relative_to(pages).as_posix() for path in pages.rglob("*") if path.is_file() and ".git" not in path.parts) == before_files


def test_release_manifest_requires_clean_pages_even_for_dry_run(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    manifest = _release_manifest(source, pages, "2026-06-19")
    (pages / "dirty.txt").write_text("dirty", encoding="utf-8")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is False
    assert any("pages repo must be clean before sync" in error for error in report["errors"])


def test_runtime_editorial_inputs_are_verified_in_working_tree_not_git_history(
    release_repos: tuple[Path, Path],
) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-08-09"])
    _commit_repo(source, "food line site")
    proposal = source / "data/dispatches/food-line/review/proposed-editions" / "2026-08-09.json"
    snapshot = source / "data/dispatches/food-line/review/signal-reviews" / "2026-08-09.json"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps({"approved": True}), encoding="utf-8")
    snapshot.write_text(json.dumps({"review": True}), encoding="utf-8")
    manifest = _release_manifest_with_runtime_inputs(source, pages, "2026-08-09")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-08-09"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is True
    assert "source-input file is missing at source_commit" not in "\n".join(report["errors"])


def test_runtime_editorial_input_tamper_fails_closed(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-08-09"])
    _commit_repo(source, "food line site")
    proposal = source / "data/dispatches/food-line/review/proposed-editions" / "2026-08-09.json"
    snapshot = source / "data/dispatches/food-line/review/signal-reviews" / "2026-08-09.json"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps({"approved": True}), encoding="utf-8")
    snapshot.write_text(json.dumps({"review": True}), encoding="utf-8")
    manifest = _release_manifest_with_runtime_inputs(source, pages, "2026-08-09")
    proposal.write_text(json.dumps({"approved": False}), encoding="utf-8")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-08-09"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is False
    assert any("runtime editorial hash mismatch" in error for error in report["errors"])


def test_git_tracked_source_input_missing_at_source_commit_still_fails(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-08-09"])
    _commit_repo(source, "food line site")
    manifest = _release_manifest(source, pages, "2026-08-09")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["entries"][0]["source_path"] = "scripts/missing-git-tracked-input.py"
    payload["entries"][0]["source_sha256"] = "0" * 64
    payload["entries"][0]["provenance_role"] = "generated_output"
    write_json_deterministic(manifest, payload)

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-08-09"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is False
    assert any("source file is missing" in error or "source SHA-256 mismatch" in error for error in report["errors"])
