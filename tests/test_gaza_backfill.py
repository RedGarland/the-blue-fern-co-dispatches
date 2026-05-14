from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from scripts import backfill_gaza_dispatch, dispatches_status


def _make_root() -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-backfill"
    shutil.copytree(repo / "assets", root / "assets")
    (root / "data" / "records").mkdir(parents=True, exist_ok=True)
    for name in ("dispatches", "editions", "sources", "records", "curation_decisions", "detail_packages", "story_memory"):
        (root / "data" / "records" / f"{name}.json").write_text("[]", encoding="utf-8")
    return root


def _write_manual(root: Path, date_str: str, records: list[dict]) -> None:
    path = root / "data" / "dispatches" / "gaza" / "sources" / date_str / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _record(date_str: str, suffix: str, *, url: str | None = None) -> dict:
    resolved_url = url or f"https://example.org/{date_str}/{suffix}"
    return {
        "source_record_id": f"gaza-{date_str}-{suffix}",
        "title": f"Gaza aid update {suffix}",
        "url": resolved_url,
        "publisher": "Reuters",
        "published_at": f"{date_str}T12:00:00Z",
        "retrieved_at": f"{date_str}T13:00:00Z",
        "summary_or_snippet": "Aid convoy and civilian protection update.",
        "source_type": "rss",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "reported-public-source",
    }


def _stub_git(monkeypatch, *, root: Path, pages: Path, pages_branch: str = "gh-pages"):
    def fake(repo: Path, *args: str):
        repo = repo.resolve()
        if repo == root.resolve():
            if args == ("branch", "--show-current"):
                return True, "main"
            if args == ("rev-parse", "--short", "HEAD"):
                return True, "abc1234"
            if args == ("status", "--short"):
                return True, ""
            if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                return False, ""
        if repo == pages.resolve():
            if args == ("branch", "--show-current"):
                return True, pages_branch
            if args == ("rev-parse", "--short", "HEAD"):
                return True, "def5678"
            if args == ("status", "--porcelain"):
                return True, ""
            if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
                return False, ""
        return False, ""

    monkeypatch.setattr(dispatches_status, "run_git", fake)


def test_backfill_one_date_with_fresh_fixture_candidates_succeeds():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-10", [_record("2026-05-10", "a")])
        report = backfill_gaza_dispatch.run_backfill(
            root,
            ["2026-05-10"],
            source_mode="manual",
            from_manual_sources=True,
            publish_local=True,
            allow_partial=False,
            max_sources=12,
        )
        assert report["ok"] is True
        assert report["completed_dates"] == ["2026-05-10"]
        assert report["failed_dates"] == []
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_zero_candidates_fails_safely_and_unlinked():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-11", [])
        report = backfill_gaza_dispatch.run_backfill(
            root,
            ["2026-05-11"],
            source_mode="manual",
            from_manual_sources=True,
            publish_local=True,
            allow_partial=False,
            max_sources=12,
        )
        assert report["ok"] is False
        row = report["per_date"][0]
        assert row["generated"] is False
        assert row["public_exposed"] is False
        assert row["archive_linked"] is False
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_backfill_all_duplicate_candidates_fails_safely_and_unlinked():
    root = _make_root()
    try:
        repeated = _record("2026-05-10", "dup", url="https://news.google.com/rss/articles/abc?url=https%3A%2F%2Freuters.com%2Fstory")
        _write_manual(root, "2026-05-10", [repeated])
        first = backfill_gaza_dispatch.run_backfill(
            root, ["2026-05-10"], source_mode="manual", from_manual_sources=True, publish_local=True, allow_partial=False, max_sources=12
        )
        assert first["ok"] is True

        _write_manual(root, "2026-05-11", [repeated])
        second = backfill_gaza_dispatch.run_backfill(
            root, ["2026-05-11"], source_mode="manual", from_manual_sources=True, publish_local=True, allow_partial=True, max_sources=12
        )
        row = second["per_date"][0]
        assert row["generated"] is False
        assert row["archive_linked"] is False
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_multi_date_backfill_continues_only_with_allow_partial():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-10", [_record("2026-05-10", "a")])
        # 2026-05-11 missing on purpose
        _write_manual(
            root,
            "2026-05-12",
            [
                {
                    **_record("2026-05-12", "b"),
                    "title": "Gaza ceasefire talks update reports 42 aid trucks",
                    "summary_or_snippet": "New numbers reported for aid access and ceasefire corridor operations.",
                    "category_hint": "diplomatic",
                }
            ],
        )
        blocked = backfill_gaza_dispatch.run_backfill(
            root,
            ["2026-05-10", "2026-05-11", "2026-05-12"],
            source_mode="manual",
            from_manual_sources=True,
            publish_local=False,
            allow_partial=False,
            max_sources=12,
        )
        assert blocked["ok"] is False
        assert blocked["completed_dates"] == ["2026-05-10"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)

    root = _make_root()
    try:
        _write_manual(root, "2026-05-10", [_record("2026-05-10", "a")])
        _write_manual(
            root,
            "2026-05-12",
            [
                {
                    **_record("2026-05-12", "b"),
                    "title": "Gaza ceasefire talks update reports 42 aid trucks",
                    "summary_or_snippet": "New numbers reported for aid access and ceasefire corridor operations.",
                    "category_hint": "diplomatic",
                }
            ],
        )
        partial = backfill_gaza_dispatch.run_backfill(
            root,
            ["2026-05-10", "2026-05-11", "2026-05-12"],
            source_mode="manual",
            from_manual_sources=True,
            publish_local=False,
            allow_partial=True,
            max_sources=12,
        )
        assert partial["ok"] is True
        assert partial["completed_dates"] == ["2026-05-10", "2026-05-12"]
        assert "2026-05-11" in partial["failed_dates"]
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_date_range_runs_chronologically():
    dates = backfill_gaza_dispatch._expand_dates(None, [], "2026-05-10", "2026-05-14")
    assert dates == ["2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14"]


def test_backfill_report_written_outside_output_site():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-10", [_record("2026-05-10", "a")])
        report = backfill_gaza_dispatch.run_backfill(
            root, ["2026-05-10"], source_mode="manual", from_manual_sources=True, publish_local=False, allow_partial=False, max_sources=12
        )
        report_path = Path(report["report_path"])
        assert report_path.exists()
        assert "output\\site\\" not in str(report_path).lower()
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_archive_rss_only_include_valid_source_backed_dates():
    root = _make_root()
    try:
        _write_manual(root, "2026-05-10", [_record("2026-05-10", "a")])
        _write_manual(root, "2026-05-11", [])
        report = backfill_gaza_dispatch.run_backfill(
            root,
            ["2026-05-10", "2026-05-11"],
            source_mode="manual",
            from_manual_sources=True,
            publish_local=True,
            allow_partial=True,
            max_sources=12,
        )
        assert report["ok"] is True
        archive = (root / "output" / "site" / "gaza" / "archive.html").read_text(encoding="utf-8")
        rss = (root / "output" / "site" / "gaza" / "rss.xml").read_text(encoding="utf-8")
        assert "2026-05-10" in archive and "2026-05-10" in rss
        assert "2026-05-11" not in archive and "2026-05-11" not in rss
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_dispatches_status_ok_when_failed_folders_unlinked(monkeypatch):
    root = _make_root()
    try:
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "run_american_pressure_dispatch.py").write_text('result = {"live_fetch_enabled": False}\n', encoding="utf-8")
        pages = root / "bluefern-dispatches-pages"
        (pages / ".git").mkdir(parents=True, exist_ok=True)
        (pages / "CNAME").write_text(f"{dispatches_status.EXPECTED_CNAME}\n", encoding="utf-8")
        _write_manual(root, "2026-05-10", [_record("2026-05-10", "a")])
        _write_manual(root, "2026-05-11", [])
        backfill_gaza_dispatch.run_backfill(
            root, ["2026-05-10", "2026-05-11"], source_mode="manual", from_manual_sources=True, publish_local=True, allow_partial=True, max_sources=12
        )
        _stub_git(monkeypatch, root=root, pages=pages)
        status = dispatches_status.build_status(root, pages)
        assert status["ok"] is True
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_no_push_command_exists_in_backfill_script():
    text = Path(backfill_gaza_dispatch.__file__).read_text(encoding="utf-8").lower()
    assert "git push" not in text
