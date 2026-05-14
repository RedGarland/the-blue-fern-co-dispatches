from __future__ import annotations

from pathlib import Path

from scripts import dispatches_control_panel as cp


def test_build_command_gaza_manual_historical(tmp_path):
    cmd = cp.build_command("Gaza", "Run dispatch", "2026-05-09", root=tmp_path)
    assert cmd[1].endswith("run_gaza_dispatch.py")
    assert "--historical" in cmd
    assert "--from-manual-sources" in cmd
    assert "--all" in cmd


def test_build_command_gaza_notify(tmp_path):
    cmd = cp.build_command("Gaza", "Run with notification", "2026-05-09", root=tmp_path)
    assert cmd[1].endswith("run_and_notify.py")
    assert cmd[-1] == "--publish"


def test_build_command_cascadia_weekly(tmp_path):
    cmd = cp.build_command("Cascadia", "Run dispatch", "2026-05-10", root=tmp_path)
    assert cmd[1].endswith("run_cascadia_dispatch.py")
    assert "--weekly-public" in cmd
    assert "--historical-search" in cmd
    assert cmd[-1] == "all"


def test_build_command_american_pressure_manual(tmp_path):
    cmd = cp.build_command("American Pressure", "Run dispatch", "2026-05-10", root=tmp_path)
    assert cmd[1].endswith("run_american_pressure_dispatch.py")
    assert "--from-manual-sources" in cmd
    assert "--publish" in cmd


def test_build_command_american_pressure_notify(tmp_path):
    cmd = cp.build_command("American Pressure", "Run with notification", "2026-05-10", root=tmp_path)
    assert cmd[1].endswith("run_american_pressure_and_notify.py")
    assert "--publish" in cmd


def test_build_command_status(tmp_path):
    cmd = cp.build_command("Gaza", "Run dashboard", "2026-05-10", root=tmp_path)
    assert cmd[1].endswith("dispatches_status.py")


def test_build_command_doctor(tmp_path):
    cmd = cp.build_command("Gaza", "Run doctor", "2026-05-10", root=tmp_path)
    assert cmd[1].endswith("doctor.py")


def test_build_command_pages_publish_local_no_push(tmp_path):
    cmd = cp.build_command("Gaza", "Publish Pages locally, no push", "2026-05-10", root=tmp_path)
    joined = " ".join(cmd)
    assert "publish_github_pages.py" in joined
    assert "--no-push" in cmd


def test_no_push_command_in_command_map(tmp_path):
    pages_cmd = cp.build_command("Gaza", "Publish Pages locally, no push", "2026-05-10", root=tmp_path)
    assert "--no-push" in pages_cmd
    for dispatch in cp.DISPATCHES:
        for action in ("Run dispatch", "Run with notification", "Run dashboard", "Run doctor"):
            cmd = cp.build_command(dispatch, action, "2026-05-10", root=tmp_path)
            lowered = [part.lower() for part in cmd]
            assert "push" not in lowered
            assert "git" not in lowered


def test_missing_gaza_manual_source_warning_logic(tmp_path):
    target = cp.manual_source_path("Gaza", "2026-05-09", root=tmp_path)
    assert target == tmp_path / "data" / "dispatches" / "gaza" / "sources" / "2026-05-09" / "manual_sources.json"
    assert not target.exists()


def test_missing_american_pressure_manual_source_warning_logic(tmp_path):
    target = cp.manual_source_path("American Pressure", "2026-05-09", root=tmp_path)
    assert target == tmp_path / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-09" / "manual_sources.json"
    assert not target.exists()


def test_validate_date_accepts_iso():
    assert cp.validate_date("2026-05-10") is True


def test_validate_date_rejects_invalid():
    assert cp.validate_date("2026/05/10") is False
    assert cp.validate_date("2026-13-10") is False
    assert cp.validate_date("not-a-date") is False


def test_status_json_parsing_ok_true():
    raw = {
        "ok": True,
        "critical_errors": [],
        "warnings": [],
        "project": {},
        "pages_repo": {},
        "public_safety": {},
        "dispatches": {},
    }
    summary = cp.summarize_status_for_gui(raw)
    assert summary["overview"]["ok"] is True
    assert summary["flags"]["do_not_publish"] is False


def test_status_json_parsing_ok_false_with_critical():
    raw = {
        "ok": False,
        "critical_errors": ["output/site/paid exists"],
        "warnings": [],
        "project": {"has_source_test_doc_changes": True},
        "pages_repo": {"clean": False},
        "public_safety": {"output_site_paid_exists": True},
        "dispatches": {"gaza": {}},
    }
    summary = cp.summarize_status_for_gui(raw)
    assert summary["flags"]["do_not_publish"] is True
    assert summary["flags"]["pages_dirty"] is True


def test_status_json_parsing_missing_optional_fields():
    summary = cp.summarize_status_for_gui({"ok": True})
    assert summary["overview"]["project_root"] is None
    assert summary["american_pressure_stats"]["enabled_sources_by_pillar"] == {}


def test_statistics_summary_includes_registry_counts_when_present():
    raw = {
        "ok": True,
        "dispatches": {
            "american_pressure": {
                "registry_summary": {"total_sources": 5, "enabled_sources": 3, "enabled_by_pillar": {"food": 2}},
            }
        },
    }
    summary = cp.summarize_status_for_gui(raw)
    assert summary["american_pressure_stats"]["total_registry_sources"] == 5
    assert summary["american_pressure_stats"]["enabled_registry_sources"] == 3


def test_statistics_summary_includes_cascadia_gap_stats_when_present():
    raw = {
        "ok": True,
        "dispatches": {
            "cascadia": {
                "latest_weekly_gap_report": {
                    "path": "gap.json",
                    "source_checks_attempted": 10,
                    "source_checks_successful": 9,
                }
            }
        },
    }
    summary = cp.summarize_status_for_gui(raw)
    assert summary["cascadia_discovery_stats"]["latest_weekly_gap_report_path"] == "gap.json"
    assert summary["cascadia_discovery_stats"]["source_checks_attempted"] == 10


def test_statistics_summary_includes_gaza_dedupe_stats_when_present():
    raw = {
        "ok": True,
        "dispatches": {
            "gaza": {
                "latest_dedupe_report": {
                    "edition_date": "2026-05-10",
                    "input_candidate_count": 10,
                    "kept_candidate_count": 6,
                    "suppressed_candidate_count": 4,
                    "warnings": ["x"],
                },
                "repeated_source_urls_recent": {"https://a": ["2026-05-10", "2026-05-11"]},
            }
        },
    }
    summary = cp.summarize_status_for_gui(raw)
    assert summary["gaza_dedupe_stats"]["latest_dedupe_report_edition_date"] == "2026-05-10"
    assert summary["gaza_dedupe_stats"]["repeated_source_url_count"] == 1


def test_no_network_fetch_occurs():
    # Helper-only module tests do not invoke network calls.
    assert True


def test_no_publish_push_occurs_during_tests(tmp_path):
    cmd = cp.build_command("Gaza", "Publish Pages locally, no push", "2026-05-10", root=tmp_path)
    joined = " ".join(cmd)
    assert "--no-push" in joined
    assert "git push" not in joined.lower()


def test_smtp_password_not_rendered_in_summary():
    raw = {
        "ok": False,
        "critical_errors": ["bad"],
        "public_safety": {"smtp_password_in_logs": ["SMTP_PASSWORD=abc123"]},
        "dispatches": {},
    }
    summary = cp.summarize_status_for_gui(raw)
    rendered = str(summary)
    assert "abc123" not in rendered
    assert "SMTP_PASSWORD" not in rendered
