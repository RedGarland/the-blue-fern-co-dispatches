from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import scripts.gaza_operator_status as status


def make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-operator-status"
    root.mkdir(parents=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "bluefern-dispatches-pages").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = make_root(repo)
    monkeypatch.setattr(status, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_summarize_git_repo_classifies_dirty_allowed_and_risky_paths(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    lines = [
        "## add/pages-repo-default...origin/add/pages-repo-default",
        " M logs/gaza-daily-2026-07-05.log",
        "?? output/site/gaza/editions/2026-07-05/index.html",
        " M notes/todo.txt",
    ]

    def fake_run_git(repo: Path, *args: str):
        text = ""
        if args == ("status", "--short", "--branch", "--untracked-files=all"):
            text = "\n".join(lines)
        elif args == ("branch", "--show-current"):
            text = "add/pages-repo-default"
        elif args == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"):
            text = "origin/add/pages-repo-default"
        elif args == ("rev-list", "--left-right", "--count", "origin/add/pages-repo-default...HEAD"):
            text = "1 2"
        elif args == ("rev-parse", "--short", "HEAD"):
            text = "abc1234"
        elif args == ("rev-parse", "origin/add/pages-repo-default"):
            text = "def5678"
        return type("Completed", (), {"returncode": 0, "stdout": text, "stderr": ""})()

    monkeypatch.setattr(status, "_run_git", fake_run_git)
    repo = isolated / "source"
    repo.mkdir()
    report = status.summarize_git_repo(repo)

    assert report["state"] == "risky"
    assert report["branch"] == "add/pages-repo-default"
    assert report["upstream"] == "origin/add/pages-repo-default"
    assert report["ahead"] == 2
    assert report["behind"] == 1
    assert any(entry["category"] == "logs" for entry in report["summary"]["allowed_entries"])
    assert any(entry["category"] == "unknown" for entry in report["summary"]["risky_entries"])


def test_summarize_manual_sources_reports_field_counts_and_missing_fields(isolated: Path) -> None:
    path = status.manual_sources_path(isolated, "2026-07-05")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-1",
                    "title": "Gaza aid convoy reaches shelters",
                    "url": "https://www.reuters.com/world/middle-east/gaza-aid-convoy-reaches-shelters/",
                    "publisher": "Reuters",
                    "published_at": "2026-07-05T00:00:00Z",
                    "retrieved_at": "2026-07-05T00:01:00Z",
                    "summary_or_snippet": "Source-backed update.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                    "traceability_note": "Traceable note.",
                    "attribution_mode": "reported_public_source",
                    "claim_status": "reported_public_source",
                },
                {
                    "source_record_id": "gaza-src-2",
                    "title": "Second Gaza aid update",
                    "url": "https://www.reuters.com/world/middle-east/gaza-aid-update/",
                    "publisher": "Reuters",
                    "published_at": "2026-07-05T01:00:00Z",
                    "retrieved_at": "2026-07-05T01:01:00Z",
                    "summary_or_snippet": "Source-backed update.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                    "traceability_note": "",
                    "attribution_mode": "",
                    "claim_status": "",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    report = status.summarize_manual_sources(isolated, "2026-07-05")
    assert report["status"] == "invalid"
    assert report["record_count"] == 2
    assert report["field_counts"]["traceability_note"] == {"present": 1, "missing": 1}
    assert report["field_counts"]["attribution_mode"] == {"present": 1, "missing": 1}
    assert report["field_counts"]["claim_status"] == {"present": 1, "missing": 1}
    assert report["missing_fields"]["traceability_note"] == [2]


def test_summarize_manual_sources_rejects_placeholder_example_records(isolated: Path) -> None:
    path = status.manual_sources_path(isolated, "2026-07-05")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-1",
                    "title": "Example: Gaza story for 2026-07-05",
                    "url": "https://example.com/gaza-story-2026-07-05",
                    "publisher": "Example News",
                    "published_at": "2026-07-05T00:00:00Z",
                    "retrieved_at": "2026-07-05T00:01:00Z",
                    "summary_or_snippet": "Short summary for the example Gaza story.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                    "traceability_note": "Manually added for generator run.",
                    "attribution_mode": "reported_public_source",
                    "claim_status": "reported_public_source",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    report = status.summarize_manual_sources(isolated, "2026-07-05")
    assert report["status"] == "invalid"
    assert any("record 1 appears to be a placeholder/example source" in error for error in report["errors"])
    assert "Remove or replace the placeholder/example source record and rerun." in report["next_action"]


def test_summarize_pages_artifacts_detects_audio_links_and_feed_mentions(isolated: Path) -> None:
    pages_root = isolated / "bluefern-dispatches-pages"
    (pages_root / "gaza" / "editions" / "2026-07-05").mkdir(parents=True, exist_ok=True)
    (pages_root / "gaza" / "audio").mkdir(parents=True, exist_ok=True)
    (pages_root / "gaza" / "index.html").write_text(
        '<a href="/gaza/editions/2026-07-05/">Read the latest Gaza edition</a>',
        encoding="utf-8",
    )
    (pages_root / "gaza" / "audio" / "index.html").write_text(
        '<a href="2026-07-05-transcript.html">Transcript</a><a href="2026-07-05.mp3">MP3</a>',
        encoding="utf-8",
    )
    (pages_root / "gaza" / "audio" / "podcast.xml").write_text("<enclosure url=\"https://example.test/2026-07-05.mp3\" />", encoding="utf-8")
    (pages_root / "gaza" / "podcast.xml").write_text("<enclosure url=\"https://example.test/2026-07-05.mp3\" />", encoding="utf-8")
    (pages_root / "gaza" / "archive.html").write_text("2026-07-05", encoding="utf-8")
    (pages_root / "gaza" / "rss.xml").write_text("2026-07-05", encoding="utf-8")
    (pages_root / "gaza" / "audio" / "2026-07-05-transcript.html").write_text("transcript", encoding="utf-8")
    (pages_root / "gaza" / "audio" / "2026-07-05.mp3").write_bytes(b"audio")

    report = status.summarize_pages_artifacts(isolated, "2026-07-05")
    assert report["audio_index"]["links_date"] is True
    assert report["audio_index"]["links_mp3"] is True
    assert report["podcast_feed"]["audio_podcast"]["includes_mp3"] is True
    assert report["podcast_feed"]["site_podcast"]["includes_mp3"] is True
    assert report["archive"]["includes_date"] is True
    assert report["rss"]["includes_date"] is True
    assert report["index"]["latest_link_includes_date"] is True


def test_summarize_overall_healthy_and_actionable_next_action(isolated: Path) -> None:
    healthy = status.summarize_overall(
        source_repo={"risky": False, "dirty": False},
        manual_sources={"exists": True, "status": "valid", "path": str(status.manual_sources_path(isolated, "2026-07-05"))},
        source_artifacts={"run_manifest": {"exists": True}, "dedupe_report": {"exists": True}, "source_diversity_report": {"exists": True}},
        pages_repo={"risky": False, "dirty": False},
        pages_artifacts={
            "edition_page": {"exists": True},
            "audio_transcript": {"exists": True},
            "audio_mp3": {"exists": True},
        },
        live={"enabled": False, "ok": True, "unknown_only": False},
        recent_logs={"merged_fields": {}},
        edition_date="2026-07-05",
    )
    assert healthy["overall_status"] == "healthy"
    assert "No action needed" in healthy["next_action"]

    actionable = status.summarize_overall(
        source_repo={"risky": False, "dirty": False},
        manual_sources={"exists": True, "status": "invalid", "path": str(status.manual_sources_path(isolated, "2026-07-05"))},
        source_artifacts={"run_manifest": {"exists": False}, "dedupe_report": {"exists": True}, "source_diversity_report": {"exists": True}},
        pages_repo={"risky": False, "dirty": False},
        pages_artifacts={
            "edition_page": {"exists": False},
            "audio_transcript": {"exists": True},
            "audio_mp3": {"exists": True},
        },
        live={"enabled": True, "ok": False, "unknown_only": False},
        recent_logs={"merged_fields": {"ok": False, "operator_status": "FAILED"}},
        edition_date="2026-07-05",
    )
    assert actionable["overall_status"] == "action_needed"
    assert actionable["issues"]


def test_summarize_overall_flags_pages_repo_ahead_operator_logs(isolated: Path) -> None:
    report = status.summarize_overall(
        source_repo={"risky": False, "dirty": False},
        manual_sources={"exists": True, "status": "valid", "path": str(status.manual_sources_path(isolated, "2026-07-05"))},
        source_artifacts={"run_manifest": {"exists": True}, "dedupe_report": {"exists": True}, "source_diversity_report": {"exists": True}},
        pages_repo={"risky": False, "dirty": False},
        pages_artifacts={
            "edition_page": {"exists": True},
            "audio_transcript": {"exists": True},
            "audio_mp3": {"exists": True},
        },
        live={"enabled": False, "ok": True, "unknown_only": False},
        recent_logs={"merged_fields": {"operator_status": "PAGES_REPO_AHEAD_BLOCKED"}},
        edition_date="2026-07-05",
    )

    assert report["overall_status"] == "action_needed"
    assert "PAGES_REPO_AHEAD_BLOCKED" in report["issues"][0]


def test_main_json_output_contains_requested_sections(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], isolated: Path) -> None:
    payload = {
        "date": "2026-07-05",
        "source_repo": {},
        "pages_repo": {},
        "manual_sources": {},
        "source_artifacts": {},
        "pages_artifacts": {},
        "live": {},
        "recent_logs": {},
        "overall_status": "healthy",
        "next_action": "No action needed.",
        "issues": [],
    }
    monkeypatch.setattr(status, "build_report", lambda *args, **kwargs: payload)

    code = status.main(["--date", "2026-07-05", "--json"])
    out = json.loads(capsys.readouterr().out)

    assert code == 0
    assert out["date"] == "2026-07-05"
    assert set(("date", "source_repo", "pages_repo", "manual_sources", "source_artifacts", "pages_artifacts", "live", "recent_logs", "overall_status", "next_action")).issubset(out.keys())
