from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import scripts.gaza_command_center as command_center


def _make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-command-center"
    (root / "bluefern-dispatches-pages").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = _make_root(repo)
    monkeypatch.setattr(command_center, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def _parsed(argv: list[str]) -> object:
    return command_center.parse_args(argv)


def _dashboard_report(date_text: str) -> dict[str, object]:
    return {
        "mode": "test",
        "date_scope": {"date": date_text, "start_date": None, "end_date": None, "dates": [date_text]},
        "selected_actions": [],
        "dates": [
            {
                "date": date_text,
                "mode": "test",
                "selected_actions": [],
                "repos": {
                    "source": {
                        "path": str(Path("source")),
                        "branch": "add/pages-repo-default",
                        "state": "clean",
                        "clean": True,
                        "allowed_only": False,
                        "risky": False,
                        "entry_count": 2,
                        "risky_entries": [],
                        "allowed_entries": [],
                    },
                    "pages": {
                        "path": str(Path("pages")),
                        "branch": "gh-pages",
                        "state": "clean",
                        "clean": True,
                        "allowed_only": False,
                        "risky": False,
                        "entry_count": 1,
                        "risky_entries": [],
                        "allowed_entries": [],
                    },
                },
                "manual_sources": {
                    "path": str(Path("data/dispatches/gaza/sources") / date_text / "manual_sources.json"),
                    "status": "valid",
                    "record_count": 2,
                    "errors": [],
                    "next_action": "No action needed.",
                },
                "readiness": {
                    "date": date_text,
                    "overall_status": "healthy",
                    "next_action": "No action needed.",
                    "issues": [],
                    "source_repo": {
                        "path": str(Path("source")),
                        "branch": "add/pages-repo-default",
                        "state": "clean",
                        "upstream": "origin/add/pages-repo-default",
                        "ahead": 0,
                        "behind": 0,
                        "head_sha": "abc1234",
                        "origin_head_sha": "abc1234",
                        "summary": {"entry_count": 2, "risky_entries": [], "allowed_entries": []},
                    },
                    "pages_repo": {
                        "path": str(Path("pages")),
                        "branch": "gh-pages",
                        "state": "clean",
                        "upstream": "origin/gh-pages",
                        "ahead": 0,
                        "behind": 0,
                        "head_sha": "def5678",
                        "origin_head_sha": "def5678",
                        "summary": {"entry_count": 1, "risky_entries": [], "allowed_entries": []},
                    },
                    "live": {"enabled": False, "ok": True, "status": "skipped"},
                    "recent_logs": {
                        "merged_fields": {
                            "operator_status": "healthy",
                            "next_action": "No action needed.",
                            "audio_status": "audio_ready",
                            "bluesky_status": "skipped",
                            "bluesky_post_uri": None,
                            "live_http_ok": True,
                            "live_archive_ok": True,
                        },
                        "latest_status": "healthy",
                        "latest_next_action": "No action needed.",
                    },
                    "pages_artifacts": {
                        "audio_transcript": {"exists": True},
                        "audio_mp3": {"exists": True},
                        "audio_index": {"exists": True},
                    },
                },
                "next_safe_action": "No action needed.",
            }
        ],
        "ok": True,
        "readiness_ok": True,
        "failed_dates": [],
        "processed_dates": [date_text],
        "aggregate": {
            "date_count": 1,
            "failed_date_count": 0,
            "succeeded_date_count": 1,
            "selected_action_count": 0,
            "readiness_status": "healthy",
        },
    }


def _dashboard_report_variant(
    date_text: str,
    *,
    overall_status: str,
    manual_status: str,
    manual_record_count: int,
    manual_next_action: str,
    next_safe_action: str,
) -> dict[str, object]:
    report = _dashboard_report(date_text)
    date_result = report["dates"][0]
    date_result["readiness"]["overall_status"] = overall_status
    date_result["readiness"]["next_action"] = next_safe_action
    date_result["readiness"]["manual_status"] = manual_status
    date_result["manual_sources"]["status"] = manual_status
    date_result["manual_sources"]["record_count"] = manual_record_count
    date_result["manual_sources"]["next_action"] = manual_next_action
    date_result["next_safe_action"] = next_safe_action
    return report


def test_date_parsing_accepts_single_date_and_range() -> None:
    single = _parsed(["--date", "2026-07-05"])
    ranged = _parsed(["--start-date", "2026-07-01", "--end-date", "2026-07-05"])

    assert command_center.build_date_list(single) == ["2026-07-05"]
    assert command_center.build_date_list(ranged) == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
        "2026-07-05",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["--date", "2026-07-05", "--start-date", "2026-07-01"],
        ["--date", "2026-07-05", "--end-date", "2026-07-05"],
        ["--start-date", "2026-07-05"],
        ["--end-date", "2026-07-05"],
        ["--start-date", "2026-07-05", "--end-date", "2026-07-01"],
    ],
)
def test_date_parsing_rejects_invalid_combinations(argv: list[str]) -> None:
    args = _parsed(argv)
    with pytest.raises(ValueError):
        command_center.build_date_list(args)


def test_production_mode_without_actions_does_not_trigger_public_side_effects(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(command_center, "_manual_source_status", lambda date_text: {"status": "valid", "record_count": 2, "errors": []})

    called = {"email": 0, "publish": 0, "bluesky": 0}
    monkeypatch.setattr(command_center, "_run_publish", lambda *args, **kwargs: called.__setitem__("publish", called["publish"] + 1) or pytest.fail("publish should not run"))
    monkeypatch.setattr(command_center, "_run_post_bluesky", lambda *args, **kwargs: called.__setitem__("bluesky", called["bluesky"] + 1) or pytest.fail("bluesky should not run"))
    monkeypatch.setattr(command_center, "_run_email", lambda *args, **kwargs: called.__setitem__("email", called["email"] + 1) or pytest.fail("email should not run"))

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--production"]))

    assert report["ok"] is True
    assert report["dates"][0]["actions"] == []
    assert called == {"email": 0, "publish": 0, "bluesky": 0}


def test_test_mode_plans_publish_post_and_email_without_public_side_effects(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_TO", "ops@example.com")
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(command_center, "_manual_source_status", lambda date_text: {"status": "valid", "record_count": 2, "errors": []})
    monkeypatch.setattr(command_center.daily_operator, "run_operator", lambda *args, **kwargs: pytest.fail("publish must be planned in test mode"))
    monkeypatch.setattr(command_center, "send_email", lambda *args, **kwargs: pytest.fail("email must not be sent in test mode"))

    bluesky_calls: list[dict[str, object]] = []

    def fake_bluesky(**kwargs: object) -> dict[str, object]:
        bluesky_calls.append(kwargs)
        return {"status": "planned", "card_title": "Preview card", "post_text": "Preview", "source_artifact_paths": []}

    monkeypatch.setattr(command_center, "maybe_post_gaza_dispatch_to_bluesky", fake_bluesky)

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--publish", "--post-bluesky", "--email"]))
    actions = report["dates"][0]["actions"]

    assert [action["action"] for action in actions] == ["publish", "post_bluesky", "email"]
    assert all(action["execution"] == "plan" for action in actions)
    assert actions[0]["status"] == "planned"
    assert actions[1]["status"] == "planned"
    assert actions[2]["status"] == "planned"
    assert bluesky_calls and bluesky_calls[0]["allow_publish"] is False


def test_range_processing_is_ascending(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_run_date(date_text: str, args: object, selected_actions: list[str]) -> dict[str, object]:
        seen.append(date_text)
        return {"date": date_text, "mode": "test", "selected_actions": selected_actions, "repos": {"source": {}, "pages": {}}, "manual_sources": {"status": "valid", "record_count": 0, "errors": []}, "preflight": {"ok": True}, "actions": [], "ok": True, "stopped_early": False, "next_safe_action": "No action needed."}

    monkeypatch.setattr(command_center, "_run_date", fake_run_date)

    report = command_center.build_report(_parsed(["--start-date", "2026-07-01", "--end-date", "2026-07-05"]))

    assert seen == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]
    assert report["processed_dates"] == seen


def test_production_range_stops_on_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_run_date(date_text: str, args: object, selected_actions: list[str]) -> dict[str, object]:
        seen.append(date_text)
        ok = date_text != "2026-07-02"
        return {"date": date_text, "mode": "production", "selected_actions": selected_actions, "repos": {"source": {}, "pages": {}}, "manual_sources": {"status": "valid", "record_count": 0, "errors": []}, "preflight": {"ok": True}, "actions": [], "ok": ok, "stopped_early": not ok, "next_safe_action": "No action needed." if ok else "Fix the failed publish action and rerun."}

    monkeypatch.setattr(command_center, "_run_date", fake_run_date)

    report = command_center.build_report(_parsed(["--start-date", "2026-07-01", "--end-date", "2026-07-03", "--production", "--publish"]))

    assert seen == ["2026-07-01", "2026-07-02"]
    assert report["failed_dates"] == ["2026-07-02"]
    assert report["ok"] is False


def test_dry_run_full_invokes_wrapper_with_expected_flags(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(command_center, "_manual_source_status", lambda date_text: {"status": "not_present", "record_count": 0, "errors": [], "next_action": "Run manual source intake or create manual_sources.json."})
    monkeypatch.setattr(
        command_center,
        "_readiness_report",
        lambda date_text: {
            "overall_status": "action_needed",
            "next_action": "Run the Gaza pipeline to create the run manifest.",
            "issues": ["Run the Gaza pipeline to create the run manifest."],
        },
    )

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return type("Completed", (), {"returncode": 0, "stdout": "Isolated Gaza dry-run workspace: C:\\tmp\\gaza\n", "stderr": ""})()

    monkeypatch.setattr(command_center.subprocess, "run", fake_run)

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--dry-run-full"]))
    action = report["dates"][0]["actions"][0]

    assert action["status"] == "passed"
    assert action["details"]["workspace"] == "C:\\tmp\\gaza"
    assert report["ok"] is True
    assert report["readiness_ok"] is False
    assert report["aggregate"]["readiness_status"] == "needs_attention"
    assert report["dates"][0]["next_safe_action"].startswith("Dry-run mechanism passed.")
    assert "-DryRunFull" in captured["cmd"]
    assert "-Push" not in captured["cmd"]
    assert "-PostBluesky" not in captured["cmd"]


def test_check_reports_placeholder_manual_source_as_needs_attention(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_center, "_preflight_report", lambda: {"ok": True, "source_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}, "pages_repo": {"summary": {"entry_count": 0, "risky_entries": [], "allowed_entries": []}}})
    monkeypatch.setattr(
        command_center.operator_status,
        "build_report",
        lambda *args, **kwargs: {
            "overall_status": "action_needed",
            "next_action": "Remove or replace the placeholder/example manual source record and rerun.",
            "issues": ["Remove or replace the placeholder/example manual source record and rerun."],
            "manual_sources": {
                "status": "invalid",
                "errors": ["record 1 appears to be a placeholder/example source"],
                "next_action": "Remove or replace the placeholder/example manual source record and rerun.",
            },
            "source_repo": {"state": "clean"},
            "pages_repo": {"state": "clean"},
            "source_artifacts": {},
            "pages_artifacts": {},
            "live": {"enabled": False, "ok": True, "unknown_only": False},
            "recent_logs": {"merged_fields": {}},
        },
    )
    manual_path = isolated / "data" / "dispatches" / "gaza" / "sources" / "2026-07-05" / "manual_sources.json"
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-2026-07-05-001",
                    "title": "Example: Gaza story for 2026-07-05",
                    "url": "https://example.com/gaza-story-2026-07-05",
                    "publisher": "Example News",
                    "published_at": "2026-07-05T00:00:00Z",
                    "retrieved_at": "2026-07-05T00:01:00Z",
                    "summary_or_snippet": "Short summary for the example Gaza story.",
                    "source_type": "news",
                    "provider_id": "manual-supplement",
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

    report = command_center.build_report(_parsed(["--date", "2026-07-05", "--check"]))
    date_result = report["dates"][0]

    assert report["ok"] is False
    assert date_result["manual_sources"]["status"] == "invalid"
    assert any("placeholder/example source" in error for error in date_result["manual_sources"]["errors"])
    assert "remove or replace" in date_result["next_safe_action"].lower()


def test_json_output_includes_dates_and_aggregate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        command_center,
        "_run_date",
        lambda date_text, args, selected_actions: {
            "date": date_text,
            "mode": "test",
            "selected_actions": selected_actions,
            "repos": {"source": {"state": "clean"}, "pages": {"state": "clean"}},
            "manual_sources": {"status": "valid", "record_count": 2, "errors": []},
            "preflight": {"ok": True},
            "actions": [],
            "ok": True,
            "stopped_early": False,
            "next_safe_action": "No action needed.",
        },
    )

    code = command_center.main(["--date", "2026-07-05", "--check", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["dates"][0]["date"] == "2026-07-05"
    assert payload["aggregate"]["date_count"] == 1
    assert payload["ok"] is True


def test_dashboard_state_and_html_include_required_sections(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    date_text = "2026-07-05"
    monkeypatch.setattr(command_center, "build_report", lambda args: _dashboard_report(date_text))
    monkeypatch.setattr(
        command_center.daily_operator,
        "_pages_repo_snapshot",
        lambda pages_repo: {"head_subject": "Publish Gaza edition", "head_sha": "def5678", "ahead": 0, "behind": 0, "branch": "gh-pages"},
    )

    state = command_center.build_dashboard_state(_parsed(["--date", date_text, "--dashboard"]))
    html = command_center.render_dashboard_html(state)

    assert state["mode"] == "dashboard"
    assert state["overall_status"] == "healthy"
    assert state["publishable_update_available"] is True
    assert state["manual_source_update_available"] is True
    assert state["source_repo_blocks_publish"] is False
    assert state["pages_repo_blocks_publish"] is False
    assert state["pages_commit_subject"] == "Publish Gaza edition"
    assert state["command_snippets"]["website_publishing"][0]["command"].endswith("--dry-run-full")
    assert state["command_snippets"]["audio"][1]["command"].endswith("--audio-generate --production")
    assert state["command_snippets"]["bluesky"][0]["command"].endswith("--post-bluesky-only --post-bluesky --dry-run")
    assert state["command_snippets"]["bluesky"][1]["command"].endswith("--post-bluesky-only --post-bluesky")
    assert state["command_snippets"]["bluesky"][2]["command"].endswith("--post-bluesky-only --post-bluesky --force-bluesky-post")
    assert state["command_snippets"]["verification"][0]["command"].endswith("--verify-live")

    for heading in [
        "Overall status",
        "Repo safety",
        "Manual sources",
        "Gaza readiness",
        "Website publishing",
        "Audio",
        "Bluesky",
        "Live verification",
        "Safe command checklist",
    ]:
        assert heading in html
    assert "Requires explicit operator action" in html
    assert "--dry-run-full" in html
    assert "--audio-generate --production" in html
    assert "--audio-publish --production" in html
    assert "--post-bluesky-only --post-bluesky --dry-run" in html
    assert "--post-bluesky-only --post-bluesky" in html
    assert "--post-bluesky-only --post-bluesky --force-bluesky-post" in html
    assert "--verify-live" in html
    assert "output/site" not in html


def test_dashboard_main_writes_review_files_and_stays_read_only(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    date_text = "2026-07-05"
    monkeypatch.setattr(command_center, "build_report", lambda args: _dashboard_report(date_text))
    monkeypatch.setattr(
        command_center.daily_operator,
        "_pages_repo_snapshot",
        lambda pages_repo: {"head_subject": "Publish Gaza edition", "head_sha": "def5678", "ahead": 0, "behind": 0, "branch": "gh-pages"},
    )
    monkeypatch.setattr(command_center, "_run_publish", lambda *args, **kwargs: pytest.fail("publish must not run for dashboard"))
    monkeypatch.setattr(command_center, "_run_post_bluesky", lambda *args, **kwargs: pytest.fail("bluesky must not run for dashboard"))
    monkeypatch.setattr(command_center, "_run_email", lambda *args, **kwargs: pytest.fail("email must not run for dashboard"))

    code = command_center.main(["--date", date_text, "--dashboard"])

    html_path = isolated / "output" / "review" / "gaza" / "operator-dashboard.html"
    json_path = isolated / "output" / "review" / "gaza" / "operator-dashboard.json"

    assert code == 0
    assert html_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")
    assert payload["mode"] == "dashboard"
    assert payload["date"] == date_text
    assert "Gaza Operator Dashboard" in html
    assert "Read-only dashboard output written to" in html
    assert "output/review/gaza/operator-dashboard.html" in html.replace("\\", "/")
    assert "output/site" not in html


def test_dashboard_uses_nonpublishing_next_action_when_no_publishable_update(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    date_text = "2026-07-09"
    report = _dashboard_report_variant(
        date_text,
        overall_status="action_needed",
        manual_status="valid",
        manual_record_count=2,
        manual_next_action="No action needed.",
        next_safe_action="Publish the Gaza edition page into the Pages repo.",
    )
    monkeypatch.setattr(command_center, "build_report", lambda args: report)
    monkeypatch.setattr(
        command_center.daily_operator,
        "_pages_repo_snapshot",
        lambda pages_repo: {"head_subject": "Publish Gaza edition", "head_sha": "def5678", "ahead": 0, "behind": 0, "branch": "gh-pages"},
    )

    state = command_center.build_dashboard_state(_parsed(["--date", date_text, "--dashboard"]))
    html = command_center.render_dashboard_html(state)

    assert state["publishable_update_available"] is False
    assert state["next_safe_action"] == (
        "No publishable source-backed Gaza update is currently available. "
        "Add valid manual sources or wait for the next source collection."
    )
    assert "Publish the Gaza edition page into the Pages repo." not in state["next_safe_action"]
    assert "Publish the Gaza edition page into the Pages repo." not in html
    assert "No publishable source-backed Gaza update is currently available" in html


def test_dashboard_prefers_manual_source_next_action_when_manual_sources_need_attention(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    date_text = "2026-07-10"
    report = _dashboard_report_variant(
        date_text,
        overall_status="action_needed",
        manual_status="invalid",
        manual_record_count=0,
        manual_next_action="Fill the missing manual source traceability fields and rerun.",
        next_safe_action="Publish the Gaza edition page into the Pages repo.",
    )
    monkeypatch.setattr(command_center, "build_report", lambda args: report)
    monkeypatch.setattr(
        command_center.daily_operator,
        "_pages_repo_snapshot",
        lambda pages_repo: {"head_subject": "Publish Gaza edition", "head_sha": "def5678", "ahead": 0, "behind": 0, "branch": "gh-pages"},
    )

    state = command_center.build_dashboard_state(_parsed(["--date", date_text, "--dashboard"]))
    html = command_center.render_dashboard_html(state)

    assert state["publishable_update_available"] is False
    assert state["next_safe_action"] == "Fill the missing manual source traceability fields and rerun."
    assert "Fill the missing manual source traceability fields and rerun." in html
    assert "Publish the Gaza edition page into the Pages repo." not in html


def test_dashboard_rejects_action_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_center, "build_report", lambda args: _dashboard_report("2026-07-05"))
    args = _parsed(["--date", "2026-07-05", "--dashboard", "--publish"])
    with pytest.raises(ValueError, match="read-only"):
        command_center.build_dashboard_state(args)
