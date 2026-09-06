import shutil
import uuid
from pathlib import Path

from bluefern_dispatches.generator import (
    CNAME_VALUE,
    build_site,
    publish_pages,
)
from test_dispatches_site import (
    add_american_pressure_site_edition,
    add_cascadia_site_edition,
    add_gaza_site_edition,
    add_cascadia_dispatch_edition,
    make_pages_repo,
)


def _fresh_workdir() -> tuple[Path, Path]:
    repo = Path(__file__).resolve().parents[1]
    test_root = repo / "output" / "test-runs" / uuid.uuid4().hex
    work = test_root / "repo"
    backup = test_root / "backups"
    shutil.copytree(repo / "assets", work / "assets")
    return work, backup


def test_pages_publish_preserves_older_gaza_editions_when_newer_exists(monkeypatch):
    work, backup = _fresh_workdir()
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-05-24")

    build_site(work, dry_run=False, backup_root=backup)
    add_gaza_site_edition(work / "output" / "site", "2026-05-03")
    add_gaza_site_edition(work / "output" / "site", "2026-05-24")
    (pages_repo / "CNAME").write_text(f"{CNAME_VALUE}\n", encoding="utf-8")
    (pages_repo / "gaza" / "editions" / "2026-05-03").mkdir(parents=True, exist_ok=True)
    (pages_repo / "gaza" / "editions" / "2026-05-03" / "index.html").write_text("old", encoding="utf-8")

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=backup)

    assert result["ok"] is True
    assert (pages_repo / "gaza" / "editions" / "2026-05-03" / "index.html").exists()
    assert (pages_repo / "gaza" / "editions" / "2026-05-24" / "index.html").exists()


def test_archive_lists_publishable_may_2026_editions():
    work, backup = _fresh_workdir()
    build_site(work, dry_run=False, backup_root=backup)
    add_gaza_site_edition(work / "output" / "site", "2026-05-03")
    add_gaza_site_edition(work / "output" / "site", "2026-05-24")
    add_cascadia_site_edition(work / "output" / "site", "2026-05-03")
    add_american_pressure_site_edition(work / "output" / "site", "2026-05-09")
    add_american_pressure_site_edition(work / "output" / "site", "2026-05-16")
    add_american_pressure_site_edition(work / "output" / "site", "2026-05-23")

    result = build_site(work, dry_run=False, backup_root=backup)
    assert result["ok"] is True

    gaza_archive = (work / "output" / "site" / "gaza" / "archive.html").read_text(encoding="utf-8")
    ap_archive = (work / "output" / "site" / "american-pressure" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-03" in gaza_archive
    assert "2026-05-24" in gaza_archive
    assert "2026-05-09" in ap_archive
    assert "2026-05-16" in ap_archive
    assert "2026-05-23" in ap_archive


def test_pages_publish_only_removes_explicitly_non_publishable_cascadia():
    work, backup = _fresh_workdir()
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    add_cascadia_dispatch_edition(work, "2026-05-03")
    add_cascadia_site_edition(work / "output" / "site", "2026-05-04")
    build_site(work, dry_run=False, backup_root=backup)

    (pages_repo / "cascadia" / "editions" / "2026-05-06").mkdir(parents=True, exist_ok=True)
    (pages_repo / "cascadia" / "editions" / "2026-05-06" / "index.html").write_text("daily", encoding="utf-8")

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=backup)
    removed = result["public_pages_editions_removed"]
    assert any(item["dispatch"] == "cascadia" and item["edition_date"] == "2026-05-06" for item in removed)


def test_cascadia_transitional_daily_editions_remain_excluded():
    work, backup = _fresh_workdir()
    stale_daily = work / "output" / "site" / "cascadia" / "editions" / "2026-05-04"
    stale_daily.mkdir(parents=True, exist_ok=True)
    (stale_daily / "index.html").write_text("<html>daily</html>", encoding="utf-8")
    (stale_daily / "edition_manifest.json").write_text(
        '{"dispatch_slug":"cascadia","edition_date":"2026-05-04","briefing_type":"daily"}',
        encoding="utf-8",
    )
    add_cascadia_dispatch_edition(work, "2026-05-03")
    result = build_site(work, dry_run=False, backup_root=backup)
    assert result["ok"] is True
    archive = (work / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-04" not in archive


def test_publish_excludes_paid_detail_private_artifacts():
    work, backup = _fresh_workdir()
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    (work / "output" / "site" / "detail").mkdir(parents=True, exist_ok=True)
    (work / "output" / "site" / "paid").mkdir(parents=True, exist_ok=True)
    (work / "output" / "site" / "detail" / "x.json").write_text("{}", encoding="utf-8")
    (work / "output" / "site" / "paid" / "x.json").write_text("{}", encoding="utf-8")

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=backup)
    assert result["ok"] is False
    assert any("paid/detail artifacts are present in public output" in err for err in result["errors"])


def test_gaza_archive_uses_durable_publishable_union_including_pages_repo_editions():
    work, backup = _fresh_workdir()
    pages_repo = make_pages_repo(work / "bluefern-dispatches-pages")
    build_site(work, dry_run=False, backup_root=backup)

    for edition_date in ("2026-05-22", "2026-05-23"):
        edition_dir = pages_repo / "gaza" / "editions" / edition_date
        edition_dir.mkdir(parents=True, exist_ok=True)
        (edition_dir / "index.html").write_text("<html><body>Gaza</body></html>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            '{"dispatch_slug":"gaza","edition_date":"' + edition_date + '","source_count":1,"story_count":1}',
            encoding="utf-8",
        )
        (edition_dir / "sources_manifest.json").write_text(
            '[{"source_record_id":"gaza-' + edition_date + '-1","url":"https://example.com","title":"Source"}]',
            encoding="utf-8",
        )
        (edition_dir / "curation_manifest.json").write_text(
            '[{"story_id":"story-1","source_ids":["gaza-' + edition_date + '-1"]}]',
            encoding="utf-8",
        )

    result = publish_pages(work, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=backup)
    assert result["ok"] is True
    archive = (work / "output" / "site" / "gaza" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-22" in archive
    assert "2026-05-23" in archive
    written = {row["edition_date"] for row in result["gaza_archive_entries_written"]}
    assert "2026-05-22" in written
    assert "2026-05-23" in written
