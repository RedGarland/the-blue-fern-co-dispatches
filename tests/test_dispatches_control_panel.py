from __future__ import annotations

from scripts import dispatches_control_panel as cp


def _base_status() -> dict:
    return {
        "ok": True,
        "critical_errors": [],
        "warnings": [],
        "project": {
            "root": "C:/repo",
            "branch": "main",
            "head_short_sha": "abc123",
            "tracking": "up-to-date",
            "has_source_test_doc_changes": False,
            "has_generated_runtime_dirt": False,
        },
        "pages_repo": {
            "exists": True,
            "branch": "gh-pages",
            "head_short_sha": "def456",
            "clean": True,
            "tracking": "up-to-date",
            "cname_ok": True,
            "cname_value": "dispatches.thebluefernco.com",
        },
        "public_safety": {
            "output_site_detail_exists": False,
            "output_site_paid_exists": False,
            "smtp_password_in_logs": [],
        },
        "dispatches": {
            "gaza": {
                "latest_public_edition_date": "2026-05-10",
                "latest_pages_edition_date": "2026-05-10",
                "latest_source_count": 5,
                "latest_story_count": 4,
                "archive_exists": True,
                "rss_exists": True,
                "latest_has_visible_source_links": True,
                "latest_public_url": "https://dispatches.thebluefernco.com/gaza/editions/2026-05-10/",
                "public_archive_dates": ["2026-05-10"],
                "stale_or_unlinked_edition_dates": [],
                "public_linked_zero_source_dates": [],
                "public_linked_zero_story_dates": [],
                "public_linked_dedupe_refusal_dates": [],
                "repeated_source_urls_recent": {},
            },
            "cascadia": {
                "latest_public_edition_date": "2026-05-10",
                "latest_pages_edition_date": "2026-05-10",
                "latest_source_count": 7,
                "latest_story_count": 6,
                "archive_exists": True,
                "rss_exists": True,
                "latest_has_visible_source_links": True,
                "latest_public_url": "https://dispatches.thebluefernco.com/cascadia/editions/2026-05-10/",
                "latest_weekly_edition_date": "2026-05-10",
                "latest_weekly_gap_report": {
                    "source_checks_attempted": 53,
                    "source_checks_successful": 34,
                    "successful_fetch_rate": 0.64,
                    "final_public_story_count": 6,
                },
                "latest_manifest_warnings": [
                    "Weak date basis for item a",
                    "Weak date basis for item b",
                    "Registry fetch failed",
                    "GDELT timeout",
                ],
            },
            "american_pressure": {
                "latest_public_edition_date": "2026-05-19",
                "latest_pages_edition_date": "2026-05-19",
                "latest_source_count": 2,
                "latest_story_count": 2,
                "latest_manual_source_date": "2026-05-19",
                "latest_manual_source_exists_for_latest_public_edition": True,
                "registry_summary": {
                    "total_sources": 8,
                    "enabled_sources": 5,
                    "enabled_by_pillar": {
                        "food_pressure": 2,
                        "health_access_pressure": 3,
                    },
                },
                "bad_fns_hits_in_active_output": [],
            },
        },
    }


def test_command_map_no_push(tmp_path):
    cmd = cp.build_command("Gaza", "Publish Pages locally, no push", "2026-05-10", root=tmp_path)
    assert "--no-push" in cmd


def test_health_classification_ok():
    health = cp.classify_health(_base_status())
    assert health["overall"] == "Needs Review" or health["overall"] == "Review"


def test_health_classification_blocked_by_critical_errors():
    raw = _base_status()
    raw["ok"] = False
    raw["critical_errors"] = ["bad"]
    health = cp.classify_health(raw)
    assert health["overall"] == "Blocked"


def test_do_not_publish_produces_blocked():
    raw = _base_status()
    raw["ok"] = False
    health = cp.classify_health(raw)
    assert health["flags"]["do_not_publish"] is True
    assert health["overall"] == "Blocked"


def test_gaza_repeated_urls_review_not_blocked():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {"https://x": ["2026-05-09", "2026-05-10"]}
    health = cp.classify_health(raw)
    assert health["flags"]["gaza_repeated_urls"] is True
    assert health["overall"] != "Blocked"


def test_cascadia_fetch_rate_below_target_review():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    health = cp.classify_health(raw)
    assert health["flags"]["cascadia_fetch_rate_low"] is True


def test_generated_runtime_dirt_no_source_changes_informational():
    raw = _base_status()
    raw["dispatches"]["cascadia"]["latest_weekly_gap_report"]["successful_fetch_rate"] = 0.9
    raw["dispatches"]["cascadia"]["latest_manifest_warnings"] = []
    raw["project"]["has_generated_runtime_dirt"] = True
    health = cp.classify_health(raw)
    assert "Generated/runtime dirt exists" in " ".join(health["info_reasons"])


def test_ap_missing_manual_source_review():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["cascadia"]["latest_weekly_gap_report"]["successful_fetch_rate"] = 0.9
    raw["dispatches"]["cascadia"]["latest_manifest_warnings"] = []
    raw["dispatches"]["american_pressure"]["latest_manual_source_exists_for_latest_public_edition"] = False
    health = cp.classify_health(raw)
    assert health["flags"]["manual_source_missing_ap"] is True


def test_publish_decision_allowed_wording_when_no_blockers():
    raw = _base_status()
    text = cp.build_publish_decision(raw)
    assert "Publishing is allowed" in text


def test_publish_decision_blocked_wording_when_blocker():
    raw = _base_status()
    raw["ok"] = False
    text = cp.build_publish_decision(raw)
    assert "blocked" in text.lower()


def test_long_cascadia_warnings_summarized_counts():
    counts = cp.summarize_warning_counts(_base_status())
    assert counts["weak_date_warning_count"] == 2
    assert counts["registry_fetch_error_count"] >= 1
    assert counts["gdelt_timeout_rate_limit_count"] >= 1


def test_warning_counts_use_status_fields_when_present():
    raw = _base_status()
    raw["dispatches"]["cascadia"]["weak_date_warning_count"] = 137
    raw["dispatches"]["cascadia"]["registry_fetch_error_count"] = 18
    raw["dispatches"]["cascadia"]["gdelt_timeout_rate_limit_count"] = 2
    counts = cp.summarize_warning_counts(raw)
    assert counts == {
        "weak_date_warning_count": 137,
        "registry_fetch_error_count": 18,
        "gdelt_timeout_rate_limit_count": 2,
    }


def test_raw_warning_list_not_in_main_summary_by_default():
    summary = cp.summarize_status_for_gui(_base_status())
    main = cp.format_main_summary_text(summary)
    assert '"warnings": [' not in main


def test_recommendation_ordering_blocked_before_review_before_info():
    raw = _base_status()
    raw["ok"] = False
    raw["project"]["has_generated_runtime_dirt"] = True
    recs = cp.build_recommendations(raw)
    assert recs[0]["severity"] == "Blocked"


def test_generated_prompt_prefers_gaza_cleanup():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {"https://x": ["2026-05-09", "2026-05-10"]}
    prompt = cp.generate_codex_prompt(raw)
    assert "Gaza" in prompt
    assert "repeated source URLs" in prompt


def test_generated_prompt_chooses_cascadia_when_no_gaza_issue():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    prompt = cp.generate_codex_prompt(raw)
    assert "Cascadia" in prompt


def test_generated_prompt_includes_required_language():
    prompt = cp.generate_codex_prompt(_base_status())
    assert "Read docs/project-contract.md first." in prompt
    assert "Do not push." in prompt
    assert "Do not use git add ." in prompt
    assert "Run focused tests, full pytest, doctor, and dispatches_status.py." in prompt


def test_copy_prompt_button_logic_without_clipboard():
    summary = cp.summarize_status_for_gui(_base_status())
    assert "suggested_codex_prompt" in summary
