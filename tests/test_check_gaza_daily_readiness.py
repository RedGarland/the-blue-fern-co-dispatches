from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.check_gaza_daily_readiness as readiness


def _write_manual_sources(root: Path, edition_date: str, records: list[dict] | None = None) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = records or [
        {
            "source_record_id": f"gaza-src-{edition_date}-001",
            "title": "UN warns shelters remain blocked from Gaza",
            "url": "https://www.aa.com.tr/en/middle-east/un-says-israel-blocks-durable-shelter-materials-from-entering-gaza/3923572",
            "publisher": "Anadolu Agency",
            "published_at": f"{edition_date}T12:00:00Z",
            "retrieved_at": "2026-07-06T00:00:00Z",
            "summary_or_snippet": "The report says UN comments noted durable shelter materials remain blocked.",
            "source_type": "news",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "reported-public-source",
        }
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _configure_repo_mocks(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    pages_repo: Path,
    *,
    dry_run_payload: dict | None = None,
) -> list[list[str]]:
    calls: list[list[str]] = []
    safe_pages_payload = dry_run_payload or {
        "ok": True,
        "errors": [],
        "pages_dry_run_ok": True,
        "paid_detail_excluded_from_public": True,
        "gaza_public_surface_history": [
            {"surface": "gaza/archive.html", "previous_count": 2, "current_count": 2, "preserved_dates": ["2026-07-04", "2026-07-05"], "added_dates": [], "dropped_dates": [], "ok": True},
            {"surface": "gaza/rss.xml", "previous_count": 2, "current_count": 2, "preserved_dates": ["2026-07-04", "2026-07-05"], "added_dates": [], "dropped_dates": [], "ok": True},
            {"surface": "gaza/audio/index.html", "previous_count": 2, "current_count": 2, "preserved_dates": ["2026-07-04", "2026-07-05"], "added_dates": [], "dropped_dates": [], "ok": True},
            {"surface": "gaza/audio/podcast.xml", "previous_count": 2, "current_count": 2, "preserved_dates": ["2026-07-04", "2026-07-05"], "added_dates": [], "dropped_dates": [], "ok": True},
            {"surface": "gaza/podcast.xml", "previous_count": 2, "current_count": 2, "preserved_dates": ["2026-07-04", "2026-07-05"], "added_dates": [], "dropped_dates": [], "ok": True},
        ],
    }

    monkeypatch.setattr(readiness, "ROOT", root)
    monkeypatch.setattr(
        readiness,
        "REQUIRED_GAZA_ASSETS",
        (root / "assets" / "site.css", root / "assets" / "gaza-logo.png"),
    )
    monkeypatch.setattr(readiness, "_git_status_lines", lambda repo: [])

    def fake_git_output(repo: Path, *args: str) -> str:
        if repo == pages_repo and args == ("branch", "--show-current"):
            return "gh-pages"
        if repo == pages_repo and args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
            return "origin/gh-pages"
        if repo == pages_repo and args == ("rev-list", "--left-right", "--count", "origin/gh-pages...HEAD"):
            return "0 0"
        if repo == pages_repo and args == ("rev-parse", "--short", "HEAD"):
            return "abc1234"
        if repo == pages_repo and args == ("rev-parse", "--short", "origin/gh-pages"):
            return "abc1234"
        if repo == root and args == ("branch", "--show-current"):
            return "add/pages-repo-default"
        raise AssertionError(f"unexpected git query: repo={repo} args={args}")

    monkeypatch.setattr(readiness, "_git_output", fake_git_output)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_TO", "ops@example.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    def fake_run_command(args: list[str], *, cwd: Path = readiness.ROOT):
        calls.append(args)
        command = " ".join(args)
        if "run_daily_gaza.py" in command:
            payload = safe_pages_payload
            text = "DRY RUN\n" + json.dumps(payload)
            return subprocess.CompletedProcess(args, 0, stdout=text, stderr="")
        if args[:3] == ["git", "restore", "--source=HEAD"] or args[:3] == ["git", "clean", "-fd"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(readiness, "_run_command", fake_run_command)
    return calls


def test_build_readiness_report_green_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "assets" / "gaza-logo.png").write_bytes(b"png")
    _write_manual_sources(tmp_path, "2026-07-06")
    calls = _configure_repo_mocks(monkeypatch, tmp_path, pages_repo)

    report = readiness.build_readiness_report(edition_date="2026-07-06", pages_repo=pages_repo)

    assert report["ok"] is True
    assert report["source_repo_clean"] is True
    assert report["pages_repo_clean"] is True
    assert report["pages_repo_synced"] is True
    assert report["manual_sources_status"]["status"] == "valid"
    assert report["credentials_status"]["ok"] is True
    assert report["assets_status"]["ok"] is True
    assert report["dry_run_status"]["ok"] is True
    assert report["dry_run_status"]["cleanup_ok"] is True
    assert report["history_guard_status"]["ok"] is True
    assert report["blockers"] == []
    assert report["next_action"] == "Schedule the Gaza daily run."
    assert any("run_daily_gaza.py" in " ".join(call) and "--dry-run" in call for call in calls)
    assert not any("publish_github_pages.py" in " ".join(call) and "--dry-run" in call for call in calls)


def test_build_readiness_report_blocks_on_invalid_manual_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "assets" / "gaza-logo.png").write_bytes(b"png")
    _write_manual_sources(
        tmp_path,
        "2026-07-06",
        [
            {
                "source_record_id": "",
                "title": "Missing required fields",
                "url": "https://example.com/article",
                "publisher": "Example Publisher",
                "published_at": "2026-07-06T12:00:00Z",
                "retrieved_at": "2026-07-06T12:00:00Z",
                "summary_or_snippet": "Invalid because the source record ID is missing.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    monkeypatch.setattr(readiness, "ROOT", tmp_path)
    monkeypatch.setattr(
        readiness,
        "REQUIRED_GAZA_ASSETS",
        (tmp_path / "assets" / "site.css", tmp_path / "assets" / "gaza-logo.png"),
    )
    monkeypatch.setattr(readiness, "_git_status_lines", lambda repo: [])
    monkeypatch.setattr(readiness, "_git_output", lambda repo, *args: "gh-pages" if args == ("branch", "--show-current") and repo == pages_repo else "origin/gh-pages" if args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}") else "0 0" if args == ("rev-list", "--left-right", "--count", "origin/gh-pages...HEAD") else "abc1234")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_TO", "ops@example.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(readiness, "_run_command", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dry-run should not run when manual sources are invalid")))

    report = readiness.build_readiness_report(edition_date="2026-07-06", pages_repo=pages_repo)

    assert report["ok"] is False
    assert report["manual_sources_status"]["ok"] is False
    assert report["dry_run_status"]["skipped"] is True
    assert report["history_guard_status"]["skipped"] is True
    assert report["blockers"]
    assert report["next_action"].startswith("Fix the first blocker")
    assert "source_record_id" in report["blockers"][0]


def test_build_readiness_report_wires_explicit_audio_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "assets" / "gaza-logo.png").write_bytes(b"png")
    _write_manual_sources(tmp_path, "2026-07-06")
    calls = _configure_repo_mocks(monkeypatch, tmp_path, pages_repo)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EMAIL_TO", "ops@example.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    report = readiness.build_readiness_report(
        edition_date="2026-07-06",
        pages_repo=pages_repo,
        generate_audio=True,
        tts_provider="openai",
        audio_model="gpt-4o-mini-tts",
        audio_voice="alloy",
        audio_format="mp3",
    )

    assert report["ok"] is True
    assert report["credentials_status"]["ok"] is True
    assert "OPENAI_API_KEY" in report["credentials_status"]["required"]
    assert "OPENAI_API_KEY" in report["credentials_status"]["present"]
    daily_calls = [call for call in calls if "run_daily_gaza.py" in " ".join(call)]
    assert daily_calls
    assert "--generate-audio" in daily_calls[0]
    assert "--tts-provider" in daily_calls[0]
    assert "openai" in daily_calls[0]


def test_build_readiness_report_blocks_on_daily_pages_history_shrink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "assets" / "gaza-logo.png").write_bytes(b"png")
    _write_manual_sources(tmp_path, "2026-07-06")
    shrink_payload = {
        "ok": True,
        "errors": [],
        "pages_dry_run_ok": False,
        "paid_detail_excluded_from_public": True,
        "gaza_public_surface_history": [
            {
                "surface": "gaza/archive.html",
                "previous_count": 5,
                "current_count": 3,
                "preserved_dates": ["2026-07-05", "2026-07-06"],
                "added_dates": [],
                "dropped_dates": ["2026-07-04", "2026-07-03"],
                "ok": False,
            }
        ],
    }
    _configure_repo_mocks(monkeypatch, tmp_path, pages_repo, dry_run_payload=shrink_payload)

    report = readiness.build_readiness_report(edition_date="2026-07-06", pages_repo=pages_repo)

    assert report["ok"] is False
    assert report["dry_run_status"]["ok"] is True
    assert report["history_guard_status"]["ok"] is False
    assert report["blockers"]
    assert "pages check failed" in report["blockers"][0]


def test_build_readiness_report_ignores_cleanup_failure_after_safe_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True)
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "assets" / "site.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "assets" / "gaza-logo.png").write_bytes(b"png")
    _write_manual_sources(tmp_path, "2026-07-06")
    _configure_repo_mocks(monkeypatch, tmp_path, pages_repo)
    monkeypatch.setattr(readiness, "_cleanup_generated_artifacts", lambda edition_date: (_ for _ in ()).throw(RuntimeError("fatal: Unable to create .git/index.lock: Permission denied")))

    report = readiness.build_readiness_report(edition_date="2026-07-06", pages_repo=pages_repo)

    assert report["ok"] is True
    assert report["dry_run_status"]["ok"] is True
    assert report["dry_run_status"]["cleanup_ok"] is False
    assert "index.lock" in report["dry_run_status"]["cleanup_error"]
    assert report["history_guard_status"]["ok"] is True
    assert report["blockers"] == []
