from __future__ import annotations

import json
import inspect
import subprocess
import sys
import time
from pathlib import Path

from scripts import dispatches_control_panel as cp
from scripts import american_pressure_review_workflow as apwf


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


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
                "archive_exists": True,
                "rss_exists": True,
                "latest_has_visible_source_links": True,
                "latest_public_url": "https://dispatches.thebluefernco.com/american-pressure/editions/2026-05-19/",
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


def test_cascadia_wording_when_only_fetch_rate_is_issue():
    raw = _base_status()
    raw["dispatches"]["cascadia"]["weak_date_warning_count"] = 0
    raw["dispatches"]["cascadia"]["registry_fetch_error_count"] = 1
    raw["dispatches"]["cascadia"]["gdelt_timeout_rate_limit_count"] = 0
    raw["dispatches"]["cascadia"]["latest_manifest_warnings"] = []
    raw["dispatches"]["cascadia"]["repeated_registry_failures"] = []
    raw["dispatches"]["cascadia"]["persistent_failure_type_counts"] = {}
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["cascadia_fetch_rate_low"] is True
    assert health["flags"]["cascadia_weak_date_warnings"] is False
    assert health["flags"]["cascadia_registry_errors_need_review"] is False
    assert cards["cascadia"]["main_issue"] == "Fetch success rate is below target."
    assert "do not disable sources unless failures are persistent" in cards["cascadia"]["next_action"]


def test_cascadia_registry_persistent_failures_trigger_source_action_wording():
    raw = _base_status()
    raw["dispatches"]["cascadia"]["weak_date_warning_count"] = 0
    raw["dispatches"]["cascadia"]["registry_fetch_error_count"] = 1
    raw["dispatches"]["cascadia"]["latest_manifest_warnings"] = []
    raw["dispatches"]["cascadia"]["repeated_registry_failures"] = [
        {"source_id": "or-odot-news", "status_code": 404, "reason": "http_404", "count": 2}
    ]
    raw["dispatches"]["cascadia"]["persistent_failure_type_counts"] = {"http_404": 2}
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["cascadia_registry_errors_need_review"] is True
    assert any("persistent registry fetch failures" in reason for reason in health["review_reasons"])
    assert cards["cascadia"]["next_action"] == "Disable/deprioritize dead registry sources and reduce reliability warning noise."


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
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = ["2026-05-09"]
    prompt = cp.generate_codex_prompt(raw)
    assert "Gaza duplicate/public-listing cleanup." in prompt
    assert "repeated source URL count" in prompt


def test_generated_prompt_chooses_cascadia_when_no_gaza_issue():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    prompt = cp.generate_codex_prompt(raw)
    assert "Cascadia source reliability cleanup." in prompt


def test_generated_prompt_includes_required_language():
    prompt = cp.generate_codex_prompt(_base_status())
    assert "Read docs/project-contract.md first." in prompt
    assert "Do not violate it." in prompt
    assert "Do not push." in prompt
    assert "Do not use git add ." in prompt
    assert "Report files changed." in prompt
    assert "Do not expose secrets." in prompt
    assert "Do not commit generated output/logs/runtime artifacts." in prompt
    assert "Run focused tests, full pytest, doctor, and dispatches_status.py." in prompt


def test_copy_prompt_button_logic_without_clipboard():
    summary = cp.summarize_status_for_gui(_base_status())
    assert "suggested_codex_prompt" in summary


def test_pages_repo_ok_when_clean_even_with_stale_gaza_folders():
    raw = _base_status()
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = ["2026-05-09"]
    summary = cp.summarize_status_for_gui(raw)
    assert summary["health_summary"]["pages_repo"] == "OK"


def test_gaza_stale_unlinked_is_info_not_review():
    raw = _base_status()
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = ["2026-05-09"]
    health = cp.classify_health(raw)
    assert all("stale/unlinked" not in reason.lower() for reason in health["review_reasons"])
    assert any("stale/unlinked" in reason.lower() for reason in health["info_reasons"])


def test_gaza_card_labels_public_vs_newest_generated():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_public_edition_date"] = "2026-05-09"
    raw["dispatches"]["gaza"]["latest_pages_edition_date"] = "2026-05-10"
    summary = cp.summarize_status_for_gui(raw)
    text = cp.format_main_summary_text(summary)
    assert "Latest public edition: 2026-05-09" in text
    assert "Newest generated folder: 2026-05-10" in text


def test_cascadia_fetch_rate_display_includes_target():
    summary = cp.summarize_status_for_gui(_base_status())
    text = cp.format_main_summary_text(summary)
    assert "Fetch success rate: 64% / target 75%" in text


def test_attention_prioritizes_cascadia_then_ap_coverage():
    raw = _base_status()
    summary = cp.summarize_status_for_gui(raw)
    attention = summary["what_needs_attention"]
    review_lines = [item["text"] for item in attention if item["severity"] == "Review"]
    assert review_lines
    assert "Cascadia fetch success rate" in review_lines[0]


def test_generated_prompt_prefers_cascadia_when_gaza_has_no_active_public_issue():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {"https://x": ["2026-05-09", "2026-05-10"]}
    # No zero-source/zero-story/dedupe-refusal linked issue => Cascadia should still win.
    prompt = cp.generate_codex_prompt(raw)
    assert "Cascadia source reliability cleanup." in prompt


def test_publish_status_label_allowed_when_no_blockers():
    summary = cp.summarize_status_for_gui(_base_status())
    assert summary["health_summary"]["publish_status_label"] == "Allowed"


def test_ap_card_uses_archive_rss_visible_links_and_public_url():
    summary = cp.summarize_status_for_gui(_base_status())
    card = summary["dispatch_cards"]["american_pressure"]
    assert card["archive_exists"] is True
    assert card["rss_exists"] is True
    assert card["visible_source_links"] is True
    assert "american-pressure/editions/2026-05-19/" in card["public_url"]


def test_ap_missing_pillars_are_growth_not_review():
    raw = _base_status()
    summary = cp.summarize_status_for_gui(raw)
    assert summary["health_summary"]["growth_items_count"] >= 1
    assert any("source coverage expansion" in item for item in summary["attention_sections"]["growth"])
    assert not any("source coverage expansion" in item for item in summary["attention_sections"]["review"])


def test_attention_sections_split_review_growth_housekeeping():
    raw = _base_status()
    raw["project"]["has_generated_runtime_dirt"] = True
    summary = cp.summarize_status_for_gui(raw)
    sections = summary["attention_sections"]
    assert "review" in sections
    assert "growth" in sections
    assert "housekeeping" in sections
    assert any("Generated/runtime dirt exists" in text for text in sections["housekeeping"])


def test_gaza_stale_unlinked_wording_marks_not_public_archive():
    raw = _base_status()
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = ["2026-05-09"]
    summary = cp.summarize_status_for_gui(raw)
    text = cp.format_main_summary_text(summary)
    assert "not public archive entries" in text


def test_gaza_undercollection_sets_review():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_source_count"] = 0
    raw["dispatches"]["gaza"]["latest_story_count"] = 0
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 0,
        "kept_after_dedupe": 0,
        "accepted_candidate_count_before_dedupe": 0,
        "final_story_count": 0,
        "provider_failures": [],
    }
    health = cp.classify_health(raw)
    assert health["flags"]["gaza_undercollection_review"] is True
    assert any("under-collection" in reason.lower() for reason in health["review_reasons"])


def test_gaza_tls_environment_review_wording():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 0,
        "kept_after_dedupe": 0,
        "accepted_candidate_count_before_dedupe": 0,
        "final_story_count": 0,
        "provider_failures": [{"source_id": "who-news", "reason": "tls_certificate_verification_failed"}],
        "enabled_auto_all_failed_tls": True,
    }
    health = cp.classify_health(raw)
    assert health["flags"]["gaza_undercollection_review"] is True
    assert any("failed due TLS/certificate verification" in reason for reason in health["review_reasons"])


def test_gaza_high_raw_low_accepted_review_wording():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 135,
        "accepted_candidate_count_before_dedupe": 2,
        "kept_after_dedupe": 1,
        "review_candidates": [{"title": "x", "rejection_reason": "rejected_off_topic"}],
        "provider_failures": [],
    }
    health = cp.classify_health(raw)
    assert health["flags"]["gaza_undercollection_review"] is True
    assert any("relevance filtering accepted few" in reason for reason in health["review_reasons"])


def test_gaza_source_health_most_enabled_fail_sets_review():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_source_health_report"] = {
        "providers_enabled": 6,
        "providers_attempted": 6,
        "providers_successful": 2,
        "providers_failed": 4,
    }
    health = cp.classify_health(raw)
    assert health["flags"]["gaza_undercollection_review"] is True


def test_gaza_source_backed_healthy_state_is_ok():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_public_edition_date"] = "2026-05-15"
    raw["dispatches"]["gaza"]["latest_pages_edition_date"] = "2026-05-15"
    raw["dispatches"]["gaza"]["latest_source_count"] = 4
    raw["dispatches"]["gaza"]["latest_story_count"] = 4
    raw["dispatches"]["gaza"]["public_archive_dates"] = [f"2026-05-{d:02d}" for d in range(7, 16)]
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = []
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_zero_story_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_dedupe_refusal_dates"] = []
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-15",
        "raw_candidate_count": 8,
        "normalized_candidate_count": 6,
        "accepted_candidate_count_before_dedupe": 6,
        "kept_after_dedupe": 4,
        "final_story_count": 4,
        "provider_failures": [],
        "providers_attempted_count": 5,
        "review_candidates": [],
    }
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["gaza_public_safe"] is True
    assert health["flags"]["gaza_undercollection_review"] is False
    assert cards["gaza"]["status"] == "OK"
    assert cards["gaza"]["main_issue"] == "No current blocking Gaza issue."
    assert cards["gaza"]["next_action"] == "No immediate Gaza action needed."
    assert not any("under-collection" in reason.lower() for reason in health["review_reasons"])


def test_gaza_exact_current_metrics_2026_05_15_is_ok_even_with_provider_no_matches():
    raw = _base_status()
    raw["dispatches"]["gaza"].update(
        {
            "latest_public_edition_date": "2026-05-15",
            "latest_pages_edition_date": "2026-05-15",
            "latest_source_count": 4,
            "latest_story_count": 4,
            "archive_exists": True,
            "rss_exists": True,
            "latest_has_visible_source_links": True,
            "stale_or_unlinked_edition_dates": [],
            "repeated_source_urls_recent": {},
            "public_linked_zero_source_dates": [],
            "public_linked_zero_story_dates": [],
            "public_linked_dedupe_refusal_dates": [],
            "public_archive_dates": [f"2026-05-{d:02d}" for d in range(7, 16)],
            "latest_collection_report": {
                "edition_date": "2026-05-15",
                "raw_candidate_count": 141,
                "accepted_candidate_count_before_dedupe": 6,
                "kept_after_dedupe": 4,
                "final_story_count": 4,
                "provider_failures": [{"source_id": "who-news", "reason": "no matching Gaza items for 2026-05-15"}],
                "enabled_auto_provider_count": 4,
                "enabled_auto_providers_attempted": ["who-news", "bbc-middle-east", "guardian-world", "aljazeera-middle-east"],
                "enabled_auto_tls_failures": 0,
                "providers_successful_count": 4,
                "review_candidates": [{"title": "x"}],
            },
        }
    )
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["gaza_undercollection_review"] is False
    assert cards["gaza"]["status"] == "OK"
    assert cards["gaza"]["main_issue"] == "No current blocking Gaza issue."
    assert cards["gaza"]["next_action"] == "No immediate Gaza action needed."


def test_gaza_ok_even_when_generic_warnings_present_if_no_current_gaza_blockers():
    raw = _base_status()
    raw["warnings"] = ["General provider warning outside Gaza."]
    raw["dispatches"]["gaza"]["latest_source_count"] = 4
    raw["dispatches"]["gaza"]["latest_story_count"] = 4
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = []
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_zero_story_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_dedupe_refusal_dates"] = []
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 4,
        "accepted_candidate_count_before_dedupe": 4,
        "kept_after_dedupe": 4,
        "final_story_count": 4,
        "provider_failures": [],
    }
    cards = cp.build_health_cards(raw)
    assert cards["gaza"]["status"] == "OK"


def test_gaza_public_safe_current_viable_collection_overrides_source_health_failure_summary():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_public_edition_date"] = "2026-05-15"
    raw["dispatches"]["gaza"]["latest_pages_edition_date"] = "2026-05-15"
    raw["dispatches"]["gaza"]["latest_source_count"] = 4
    raw["dispatches"]["gaza"]["latest_story_count"] = 4
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = []
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_zero_story_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_dedupe_refusal_dates"] = []
    raw["dispatches"]["gaza"]["public_archive_dates"] = [f"2026-05-{d:02d}" for d in range(7, 16)]
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-15",
        "raw_candidate_count": 141,
        "accepted_candidate_count_before_dedupe": 6,
        "kept_after_dedupe": 4,
        "final_story_count": 4,
        "provider_failures": [{"source_id": "who-news", "reason": "no matching Gaza items for 2026-05-15"}],
        "enabled_auto_provider_count": 4,
        "enabled_auto_providers_attempted": ["who-news", "bbc-middle-east", "guardian-world", "aljazeera-middle-east"],
        "enabled_auto_tls_failures": 0,
        "providers_successful_count": 4,
    }
    raw["dispatches"]["gaza"]["latest_source_health_report"] = {
        "providers_enabled": 4,
        "providers_attempted": 4,
        "providers_successful": 0,
        "providers_failed": 4,
    }
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["gaza_undercollection_review"] is False
    assert cards["gaza"]["status"] == "OK"


def test_gaza_undercollection_not_triggered_by_stale_historical_warning_fields():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_public_edition_date"] = "2026-05-15"
    raw["dispatches"]["gaza"]["latest_pages_edition_date"] = "2026-05-15"
    raw["dispatches"]["gaza"]["latest_source_count"] = 4
    raw["dispatches"]["gaza"]["latest_story_count"] = 4
    raw["dispatches"]["gaza"]["public_archive_dates"] = [f"2026-05-{d:02d}" for d in range(7, 16)]
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 0,
        "kept_after_dedupe": 0,
        "accepted_candidate_count_before_dedupe": 0,
        "final_story_count": 0,
        "provider_failures": [],
    }
    health = cp.classify_health(raw)
    assert health["flags"]["gaza_public_safe"] is True
    assert health["flags"]["gaza_undercollection_review"] is False


def test_gaza_ok_state_prefers_cascadia_prompt_when_fetch_rate_low():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_source_count"] = 4
    raw["dispatches"]["gaza"]["latest_story_count"] = 4
    raw["dispatches"]["gaza"]["stale_or_unlinked_edition_dates"] = []
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_zero_story_dates"] = []
    raw["dispatches"]["gaza"]["public_linked_dedupe_refusal_dates"] = []
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 4,
        "kept_after_dedupe": 4,
        "accepted_candidate_count_before_dedupe": 4,
        "final_story_count": 4,
        "provider_failures": [],
    }
    raw["dispatches"]["cascadia"]["latest_weekly_gap_report"]["successful_fetch_rate"] = 0.64
    prompt = cp.generate_codex_prompt(raw)
    assert "Cascadia source reliability cleanup." in prompt
    assert "Gaza source collection / under-collection fix." not in prompt


def test_gaza_review_triggers_when_enabled_providers_not_attempted():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "enabled_auto_provider_count": 4,
        "providers_attempted_count": 0,
        "raw_candidate_count": 4,
        "kept_after_dedupe": 4,
        "accepted_candidate_count_before_dedupe": 4,
        "final_story_count": 4,
        "provider_failures": [],
    }
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["gaza_undercollection_review"] is True
    assert cards["gaza"]["status"] == "Review"


def test_gaza_review_triggers_for_zero_source_or_zero_story_linked_dates():
    raw = _base_status()
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = ["2026-05-09"]
    raw["dispatches"]["gaza"]["public_linked_zero_story_dates"] = ["2026-05-08"]
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["gaza_zero_source_linked"] is True
    assert health["flags"]["gaza_zero_story_linked"] is True
    assert cards["gaza"]["status"] == "Blocked"


def test_real_gaza_zero_source_linked_date_triggers_blocked_and_cleanup_prompt():
    raw = _base_status()
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = ["2026-05-14"]
    cards = cp.build_health_cards(raw)
    prompt = cp.generate_codex_prompt(raw)
    assert cards["gaza"]["status"] == "Blocked"
    assert "Gaza duplicate/public-listing cleanup." in prompt


def test_gaza_review_high_raw_low_accepted_with_review_candidates_and_no_source_backed_latest():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_source_count"] = 0
    raw["dispatches"]["gaza"]["latest_story_count"] = 0
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 120,
        "accepted_candidate_count_before_dedupe": 1,
        "kept_after_dedupe": 0,
        "final_story_count": 0,
        "review_candidates": [{"title": "x", "rejection_reason": "rejected_off_topic"}],
        "provider_failures": [],
    }
    health = cp.classify_health(raw)
    cards = cp.build_health_cards(raw)
    assert health["flags"]["gaza_undercollection_review"] is True
    assert health["flags"]["gaza_high_raw_low_accept_review"] is True
    assert cards["gaza"]["status"] == "Review"


def test_cascadia_shows_public_story_count_and_candidate_pool():
    summary = cp.summarize_status_for_gui(_base_status())
    text = cp.format_main_summary_text(summary)
    assert "Public story count: 6" in text
    assert "Candidate/accepted pool: 6" in text


def test_suggested_prompt_preview_shows_selected_title():
    summary = cp.summarize_status_for_gui(_base_status())
    text = cp.format_main_summary_text(summary)
    assert "Suggested Codex Prompt (preview)" in text
    assert "Cascadia source reliability cleanup." in text


def test_cascadia_prompt_includes_dashboard_facts_and_commands():
    prompt = cp.generate_codex_prompt(_base_status())
    assert "Current status" in prompt
    assert "Latest public edition: 2026-05-10" in prompt
    assert "Source/story count: 7 / 6" in prompt
    assert "Source checks attempted/successful: 53 / 34" in prompt
    assert "Fetch success rate: 64% (target 75%)" in prompt
    assert "Weak-date warning count: 2" in prompt
    assert "Registry fetch error count: 1" in prompt
    assert "GDELT timeout/rate-limit count: 1" in prompt
    assert "tests\\test_cascadia_pipeline.py" in prompt


def test_gaza_undercollection_prompt_contains_required_rules():
    raw = _base_status()
    raw["dispatches"]["gaza"]["latest_source_count"] = 0
    raw["dispatches"]["gaza"]["latest_story_count"] = 0
    raw["dispatches"]["gaza"]["latest_collection_report"] = {
        "edition_date": "2026-05-10",
        "raw_candidate_count": 0,
        "kept_after_dedupe": 0,
        "accepted_candidate_count_before_dedupe": 0,
        "final_story_count": 0,
        "provider_failures": [],
    }
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["cascadia"]["latest_weekly_gap_report"]["successful_fetch_rate"] = 0.95
    raw["dispatches"]["cascadia"]["latest_manifest_warnings"] = []
    prompt = cp.generate_codex_prompt(raw)
    assert "Gaza source collection / under-collection fix." in prompt
    assert "Do not relax dedupe." in prompt
    assert "No zero-source public editions." in prompt


def test_gaza_public_linked_issue_outranks_cascadia():
    raw = _base_status()
    raw["dispatches"]["gaza"]["public_linked_zero_source_dates"] = ["2026-05-09"]
    prompt = cp.generate_codex_prompt(raw)
    assert "Gaza duplicate/public-listing cleanup." in prompt


def test_american_pressure_prompt_selected_when_only_growth_gap():
    raw = _base_status()
    raw["dispatches"]["gaza"]["repeated_source_urls_recent"] = {}
    raw["dispatches"]["cascadia"]["latest_weekly_gap_report"]["successful_fetch_rate"] = 0.95
    raw["dispatches"]["cascadia"]["latest_manifest_warnings"] = []
    prompt = cp.generate_codex_prompt(raw)
    assert "American Pressure source coverage expansion." in prompt
    assert "Prioritize household_cost_pressure first." in prompt


def test_prompt_includes_likely_files_and_final_response_required():
    prompt = cp.generate_codex_prompt(_base_status())
    assert "Files likely involved" in prompt
    assert "Final response required" in prompt


def test_prompt_has_required_sections():
    prompt = cp.generate_codex_prompt(_base_status())
    for heading in (
        "Goal",
        "Current status",
        "Core rules",
        "Files likely involved",
        "Requirements",
        "Tests",
        "Validation commands",
        "Git rules",
        "Final response required",
    ):
        assert heading in prompt


def test_generated_prompt_does_not_include_secrets():
    raw = _base_status()
    raw["warnings"] = ["SMTP_PASSWORD=secret123"]
    prompt = cp.generate_codex_prompt(raw)
    assert "secret123" not in prompt


def test_ap_run_dispatch_uses_source_mode_both_and_include_approved(tmp_path):
    cmd = cp.build_command("American Pressure", "Run dispatch", "2026-05-09", root=tmp_path)
    assert "scripts\\run_american_pressure_dispatch.py" in cmd
    assert "--source-mode" in cmd
    assert "both" in cmd
    assert "--include-approved-candidates" in cmd
    assert "--from-manual-sources" not in cmd


def test_ap_explicit_approved_candidate_run_includes_flag(tmp_path):
    cmd = cp.build_command("American Pressure", "Run American Pressure with approved candidates", "2026-05-09", root=tmp_path)
    assert "scripts\\run_american_pressure_dispatch.py" in cmd
    assert "--include-approved-candidates" in cmd


def test_ap_scout_review_weekly_commands_build(tmp_path):
    scout = cp.build_command("American Pressure", "Scout American Pressure candidates", "2026-05-09", root=tmp_path)
    review = cp.build_command("American Pressure", "Review American Pressure candidates", "2026-05-09", root=tmp_path)
    weekly = cp.build_command("American Pressure", "Run weekly American Pressure", "2026-05-09", root=tmp_path)
    assert scout[1] == "scripts\\scout_american_pressure_candidates.py"
    assert "--write" in scout
    assert "--max-per-pillar" in scout
    assert "5" in scout
    assert review[1] == "scripts\\review_american_pressure_candidates.py"
    assert "--write" in review
    assert weekly[1] == "scripts\\run_weekly_american_pressure.py"
    assert "--include-approved-candidates" in weekly


def test_ap_preflight_no_manual_warning_when_approved_candidates_exist(tmp_path):
    day = "2026-05-09"
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        json.dumps({"sources": [{"source_id": "x", "url": "https://example.com/x", "review_status": "approved"}]}),
        encoding="utf-8",
    )
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    notes = panel._preflight_warnings("American Pressure", "Run dispatch", day)
    assert "No manual sources found, but approved daily candidates may still be used." in notes
    assert not any("Missing manual sources:" in note for note in notes)


def test_open_latest_edition_uses_status_json_latest_public_date(monkeypatch, tmp_path):
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    opened: list[Path] = []
    monkeypatch.setattr(cp, "load_status_json", lambda _root: {"dispatches": {"american_pressure": {"latest_public_edition_date": "2026-05-09"}}})
    monkeypatch.setattr(panel, "_open", lambda path: opened.append(path))
    panel.open_dispatch_latest("american-pressure")
    assert opened
    assert str(opened[0]).endswith("output\\site\\american-pressure\\editions\\2026-05-09\\index.html")


def test_pages_publish_scoped_no_expect_dispatch_for_american_pressure(tmp_path):
    cmd = cp.build_command("American Pressure", "Publish Pages locally, no push", "2026-05-09", root=tmp_path)
    joined = " ".join(cmd)
    assert "--only-dispatch american-pressure" in joined
    assert "--expect-date 2026-05-09" in joined
    assert "--expect-dispatch american-pressure" not in joined


def test_ap_card_story_none_displays_not_reported():
    summary = cp.summarize_status_for_gui(_base_status())
    summary["dispatch_cards"]["american_pressure"]["stories"] = None
    text = cp.format_main_summary_text(summary)
    assert "Sources/Stories: 2 / not reported" in text


def test_ap_card_uses_story_count_fallback_when_latest_story_count_missing():
    raw = _base_status()
    raw["dispatches"]["american_pressure"].pop("latest_story_count", None)
    raw["dispatches"]["american_pressure"]["story_count"] = 7
    summary = cp.summarize_status_for_gui(raw)
    card = summary["dispatch_cards"]["american_pressure"]
    assert card["stories"] == 7
    text = cp.format_main_summary_text(summary)
    assert "Sources/Stories: 2 / 7" in text


def test_ap_review_tab_command_builders(tmp_path):
    scout = cp.build_ap_review_command("Scout Candidates", "2026-05-09", root=tmp_path)
    review = cp.build_ap_review_command("Generate Review Report", "2026-05-09", root=tmp_path)
    readiness = cp.build_ap_review_command("Check Weekly Readiness", "2026-05-09", root=tmp_path)
    weekly = cp.build_ap_review_command("Run Weekly With Approved Candidates", "2026-05-09", root=tmp_path)
    assert scout[1] == "scripts\\scout_american_pressure_candidates.py"
    assert scout[-2:] == ["--max-per-pillar", "5"]
    assert review[1] == "scripts\\review_american_pressure_candidates.py"
    assert readiness[1] == "scripts\\check_american_pressure_weekly_readiness.py"
    assert weekly[1] == "scripts\\run_weekly_american_pressure.py"
    assert "--include-approved-candidates" in weekly
    assert "--publish" in weekly


def test_tooltip_instantiation_does_not_break_import():
    class DummyWidget:
        def bind(self, *_args, **_kwargs):
            return None

    tooltip = cp.Tooltip(DummyWidget(), "x")
    assert tooltip.text == "x"


def test_ap_review_tab_contains_publish_and_checklist_controls():
    src = inspect.getsource(cp.DispatchesControlPanel._build_ap_review_tab)
    for label in (
        "Scout Week",
        "Generate Review Report",
        "Load Candidates",
        "Save Review Decisions",
        "Show Recommended Review Queue",
        "Clear Queue/Filters",
        "Check Weekly Readiness",
        "Generate HTML",
        "Open Generated HTML",
        "Publish to Pages Locally",
        "Run Publish Checklist",
        "Copy Checklist Report",
        "Push Pages Live",
    ):
        assert label in src


def test_week_selector_uses_saturday_as_edition_date():
    start, end = apwf.week_dates_for_year_week(2026, 20)
    assert start.isoformat() == "2026-05-10"
    assert end.isoformat() == "2026-05-16"
    assert apwf.week_label(2026, 20) == "Week 20: May 10–May 16, 2026"


def test_publish_local_readiness_block_and_override(monkeypatch, tmp_path):
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    panel.ap_review_date_var = type("S", (), {"get": lambda self: "2026-05-16"})()
    calls: list[list[str]] = []
    monkeypatch.setattr(cp.subprocess, "run", lambda *a, **k: type("C", (), {"stdout": '{"weekly_publish_recommended": false, "reasons_if_not_recommended": ["thin"]}', "stderr": "", "returncode": 0})())
    monkeypatch.setattr(cp.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    panel._run_async_command = lambda cmd, _label: calls.append(cmd)
    panel.publish_ap_pages_locally()
    assert calls
    assert "--publish" in calls[0]
    assert "--allow-thin-edition" in calls[0]


def test_push_pages_live_requires_confirmation(monkeypatch, tmp_path):
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    panel.ap_review_date_var = type("S", (), {"get": lambda self: "2026-05-16"})()
    panel.run_ap_publish_checklist = lambda: {"items": [], "fail_count": 0, "warn_count": 0}
    panel._run_async_command = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run"))
    monkeypatch.setattr(cp, "load_status_json", lambda _root: {"dispatches": {"american_pressure": {"latest_public_edition_date": "2026-05-09", "latest_pages_edition_date": "2026-05-09"}}})
    monkeypatch.setattr(cp.messagebox, "askyesno", lambda *_args, **_kwargs: False)
    panel.push_ap_pages_live()


def _write_ap_publish_fixture(root: Path, day: str, *, map_dashboard_route: bool = False, landing_missing_tracks: bool = False, edition_no_sources: bool = False, include_edition: bool = True):
    pages = root / "bluefern-dispatches-pages"
    (pages / "american-pressure" / "map").mkdir(parents=True, exist_ok=True)
    (pages / "american-pressure" / "editions").mkdir(parents=True, exist_ok=True)
    (pages / "index.html").write_text('<a href="/american-pressure/">AP</a>', encoding="utf-8")
    landing = (
        "<h2>What American Pressure Tracks</h2>"
        "<p><strong>What it tracks:</strong></p>"
        "<p><strong>What it does not claim:</strong></p>"
        "<p><strong>How to read it:</strong></p>"
    )
    if landing_missing_tracks:
        landing = landing.replace("What American Pressure Tracks", "American Pressure")
    (pages / "american-pressure" / "index.html").write_text(landing, encoding="utf-8")
    map_html = '<a href="/american-pressure/">Dispatch</a> | <a href="/american-pressure/archive.html">Archive</a> | <a href="/">Home</a>'
    if map_dashboard_route:
        map_html += ' <a href="/american-pressure/dashboard/">Dashboard</a>'
    map_html += " May 17–May 23, 2026"
    (pages / "american-pressure" / "map" / "index.html").write_text(map_html, encoding="utf-8")
    (pages / "american-pressure" / "map" / "map_data.json").write_text(
        json.dumps({"edition_date": day, "display_date_range": "May 17–May 23, 2026"}),
        encoding="utf-8",
    )
    if include_edition:
        edition = pages / "american-pressure" / "editions" / day
        edition.mkdir(parents=True, exist_ok=True)
        body = (
            '<p><a href="dashboard.html">View Dashboard</a> | <a href="/american-pressure/editions/2026-05-23/sources_manifest.json">View Source Ledger</a></p>'
            "<p><strong>Sources:</strong></p><a href=\"https://example.com/source\">source</a>"
        )
        if edition_no_sources:
            body += " No source links were available"
        (edition / "index.html").write_text(body, encoding="utf-8")
        (edition / "sources_manifest.json").write_text("{}", encoding="utf-8")
        (edition / "curation_manifest.json").write_text("{}", encoding="utf-8")
        (edition / "edition_manifest.json").write_text("{}", encoding="utf-8")


def test_ap_publish_checklist_passes_on_valid_fixture(tmp_path):
    day = "2026-05-23"
    _write_ap_publish_fixture(tmp_path, day)
    report = cp.run_weekly_publish_checklist(tmp_path, "american-pressure", day)
    assert report["fail_count"] == 0
    assert report["warn_count"] >= 0
    assert report["ok_to_push"] is True


def test_ap_publish_checklist_fails_if_map_contains_global_dashboard_route(tmp_path):
    day = "2026-05-23"
    _write_ap_publish_fixture(tmp_path, day, map_dashboard_route=True)
    report = cp.run_weekly_publish_checklist(tmp_path, "american-pressure", day)
    assert any(item["id"] == "ap-map-no-global-dashboard-route" and item["status"] == cp.CHECKLIST_FAIL for item in report["items"])


def test_ap_publish_checklist_fails_if_landing_missing_tracks_heading(tmp_path):
    day = "2026-05-23"
    _write_ap_publish_fixture(tmp_path, day, landing_missing_tracks=True)
    report = cp.run_weekly_publish_checklist(tmp_path, "american-pressure", day)
    assert any(item["id"] == "ap-landing-what-american-pressure-tracks" and item["status"] == cp.CHECKLIST_FAIL for item in report["items"])


def test_ap_publish_checklist_fails_if_edition_has_no_source_links_placeholder(tmp_path):
    day = "2026-05-23"
    _write_ap_publish_fixture(tmp_path, day, edition_no_sources=True)
    report = cp.run_weekly_publish_checklist(tmp_path, "american-pressure", day)
    assert any(item["id"] == "ap-edition-no-sourceless-placeholder" and item["status"] == cp.CHECKLIST_FAIL for item in report["items"])


def test_ap_publish_checklist_fails_if_selected_edition_missing(tmp_path):
    day = "2026-05-23"
    _write_ap_publish_fixture(tmp_path, day, include_edition=False)
    report = cp.run_weekly_publish_checklist(tmp_path, "american-pressure", day)
    assert any(item["id"] == "ap-edition-exists" and item["status"] == cp.CHECKLIST_FAIL for item in report["items"])


def test_ap_publish_checklist_report_is_deterministic_for_copy_use(tmp_path):
    day = "2026-05-23"
    _write_ap_publish_fixture(tmp_path, day)
    report = cp.run_weekly_publish_checklist(tmp_path, "american-pressure", day)
    text = cp.format_weekly_publish_checklist_report(report)
    assert "Weekly Publish Checklist" in text
    assert "Dispatch: american-pressure" in text
    assert "Date: 2026-05-23" in text
    assert "[PASS]" in text or "[WARN]" in text or "[FAIL]" in text


def test_ap_candidate_summary_includes_quarantine_and_failure_counts(tmp_path):
    path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-16" / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"source_record_id": "a", "pillar": "food_pressure", "title": "A", "review_status": "approved"},
                    {"source_record_id": "b", "pillar": "food_pressure", "title": "B", "review_status": "quarantine", "us_relevance_ok": False},
                    {"source_record_id": "c", "pillar": "food_pressure", "title": "C", "review_status": "rejected", "editorial_rejection_reason": "prose_quality_failed"},
                ]
            }
        ),
        encoding="utf-8",
    )
    summary = cp._candidate_summary("2026-05-16", tmp_path)
    assert summary["quarantine_count"] == 1
    assert summary["us_relevance_failures"] == 1
    assert summary["prose_quality_failures"] == 1


def test_ap_refresh_summary_shows_duplicate_candidate_note(tmp_path):
    day = "2026-05-16"
    path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"source_record_id": "dup-id", "review_status": "needs_review"},
                    {"source_record_id": "dup-id", "review_status": "approved"},
                ]
            }
        ),
        encoding="utf-8",
    )
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    panel.ap_review_date_var = type("S", (), {"get": lambda self: day})()
    panel.ap_candidate_path_var = type("S", (), {"set": lambda self, _v: None})()
    panel.ap_review_report_path_var = type("S", (), {"set": lambda self, _v: None})()
    panel.ap_summary_var = type("S", (), {"set": lambda self, _v: None})()
    note_value = {"text": ""}
    panel.ap_duplicate_note_var = type("S", (), {"set": lambda self, v: note_value.__setitem__("text", v)})()
    panel.ap_candidate_rows = cp.load_weekly_candidates(tmp_path, day)
    panel.ap_candidate_status_updates = {row["candidate_key"]: row["review_status"] for row in panel.ap_candidate_rows}
    cp.DispatchesControlPanel.refresh_ap_review_summary(panel)
    assert "Duplicate candidate IDs detected" in note_value["text"]


def test_dispatches_control_panel_self_check_direct_and_module():
    root = Path(__file__).resolve().parents[1]
    direct = subprocess.run(
        [sys.executable, "scripts\\dispatches_control_panel.py", "--self-check"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    module = subprocess.run(
        [sys.executable, "-m", "scripts.dispatches_control_panel", "--self-check"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0
    assert module.returncode == 0
    assert "ok" in direct.stdout.lower()
    assert "ok" in module.stdout.lower()


def test_ap_triage_helpers_handle_155_candidates_and_recommended_caps():
    rows = []
    for i in range(155):
        pillar = f"pillar_{i % 12}"
        row = {
            "candidate_key": f"k{i}",
            "review_status": "needs_review",
            "pillar": pillar,
            "publisher_quality": "local_reporting" if i % 2 == 0 else "reputable_reporting",
            "location": "Seattle, WA" if i % 3 else "",
            "score": 80 - (i % 20),
            "url": f"https://example.com/{i}",
            "reader_headline": f"Row {i}",
            "raw": {
                "candidate_bucket": "recommended",
                "us_relevance_ok": True,
                "public_pressure_angle": "household pressure",
                "linked_data_anchor_ids": ["a1"] if i % 2 == 0 else [],
            },
        }
        rows.append(row)
    status_updates = {row["candidate_key"]: row["review_status"] for row in rows}
    queue = cp.build_recommended_review_queue(rows, status_updates, score_threshold=45, max_per_pillar=3, max_total=25)
    assert len(queue) == 25
    by_key = {row["candidate_key"]: row for row in rows}
    pillar_counts: dict[str, int] = {}
    for key in queue:
        pillar = by_key[key]["pillar"]
        pillar_counts[pillar] = pillar_counts.get(pillar, 0) + 1
    assert all(count <= 3 for count in pillar_counts.values())


def test_ap_filter_and_sort_helpers_reduce_rows_and_prioritize_strongest():
    rows = [
        {
            "candidate_key": "a",
            "review_status": "needs_review",
            "pillar": "food_pressure",
            "publisher_quality": "local_reporting",
            "location": "Portland, OR",
            "score": 70,
            "url": "https://example.com/a",
            "reader_headline": "A",
            "raw": {"candidate_bucket": "recommended", "us_relevance_ok": True, "public_pressure_angle": "x", "linked_data_anchor_ids": ["z"]},
        },
        {
            "candidate_key": "b",
            "review_status": "rejected",
            "pillar": "food_pressure",
            "publisher_quality": "low_confidence_aggregator",
            "location": "",
            "score": 90,
            "url": "https://example.com/b",
            "reader_headline": "B",
            "raw": {"candidate_bucket": "recommended", "us_relevance_ok": False, "public_pressure_angle": "", "editorial_rejection_reason": "prose_quality_failed"},
        },
    ]
    status_updates = {"a": "needs_review", "b": "rejected"}
    filters = {
        "status": "all",
        "pillar": "all",
        "publisher_quality": "all",
        "min_score": 45,
        "us_relevance": "pass",
        "prose_quality": "pass",
        "has_location": True,
        "has_anchor": True,
        "recommended_only": True,
        "show_quarantined": False,
        "show_rejected": False,
    }
    visible = [row for row in rows if cp.row_matches_ap_filters(row, status_updates, filters)]
    assert [row["candidate_key"] for row in visible] == ["a"]
    sorted_rows = sorted(rows, key=lambda row: cp.ap_default_sort_key(row, status_updates))
    assert sorted_rows[0]["candidate_key"] == "b"


def test_ap_bulk_visible_and_selected_mutate_intended_rows_only():
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.ap_visible_candidate_keys = ["a", "b"]
    panel.ap_candidate_status_updates = {"a": "needs_review", "b": "needs_review", "c": "needs_review"}
    panel._confirm_bulk_change = lambda *_args, **_kwargs: True
    panel.apply_ap_filters_and_render = lambda: None
    panel.bulk_update_visible_status("rejected")
    assert panel.ap_candidate_status_updates["a"] == "rejected"
    assert panel.ap_candidate_status_updates["b"] == "rejected"
    assert panel.ap_candidate_status_updates["c"] == "needs_review"

    panel.ap_candidates_tree = type("T", (), {"selection": lambda self: ("c",)})()
    panel.bulk_update_selected_status("approved")
    assert panel.ap_candidate_status_updates["c"] == "approved"


def test_ap_summary_counts_and_readiness_progress_update():
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = Path(".")
    panel.ap_review_date_var = _Var("2026-05-16")
    panel.ap_candidate_path_var = _Var("")
    panel.ap_review_report_path_var = _Var("")
    panel.ap_summary_var = _Var("")
    panel.ap_duplicate_note_var = _Var("")
    panel.ap_readiness_progress_var = _Var("")
    panel.ap_filter_min_score_var = _Var(45)
    panel.ap_recommended_queue_active_var = _Var(True)
    panel.ap_visible_candidate_rows = []
    panel.ap_candidate_rows = [
        {
            "candidate_key": "a",
            "source_record_id": "dup",
            "review_status": "approved",
            "pillar": "food_pressure",
            "publisher_quality": "local_reporting",
            "location": "Seattle, WA",
            "score": 65,
            "url": "https://example.com/a",
            "reader_headline": "A",
            "raw": {"us_relevance_ok": True, "public_pressure_angle": "x", "linked_data_anchor_ids": ["x"]},
        },
        {
            "candidate_key": "b",
            "source_record_id": "dup",
            "review_status": "needs_review",
            "pillar": "health_access_pressure",
            "publisher_quality": "reputable_reporting",
            "location": "",
            "score": 50,
            "url": "https://example.com/b",
            "reader_headline": "B",
            "raw": {"us_relevance_ok": True, "public_pressure_angle": "x", "linked_data_anchor_ids": []},
        },
    ]
    panel.ap_candidate_status_updates = {"a": "approved", "b": "needs_review"}
    cp.DispatchesControlPanel.refresh_ap_review_summary(panel)
    assert "total=2" in panel.ap_summary_var.get()
    assert "recommended_queue=" in panel.ap_summary_var.get()
    assert "Duplicate candidate IDs detected" in panel.ap_duplicate_note_var.get()
    assert "Readiness progress: 1 approved / 4 minimum" in panel.ap_readiness_progress_var.get()


def test_generate_ap_html_rewrites_and_reports_success(monkeypatch, tmp_path):
    day = "2026-05-16"
    site_dir = tmp_path / "output" / "site" / "american-pressure" / "editions" / day
    dispatch_dir = tmp_path / "output" / "dispatches" / "american-pressure" / "editions" / day
    site_dir.mkdir(parents=True, exist_ok=True)
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    for path in (
        site_dir / "index.html",
        site_dir / "edition_manifest.json",
        site_dir / "sources_manifest.json",
        site_dir / "curation_manifest.json",
        dispatch_dir / "index.html",
        dispatch_dir / "edition_manifest.json",
        dispatch_dir / "sources_manifest.json",
        dispatch_dir / "curation_manifest.json",
    ):
        path.write_text("old", encoding="utf-8")
    old_mtime = (site_dir / "index.html").stat().st_mtime
    time.sleep(0.02)

    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    panel.ap_review_date_var = _Var(day)
    panel.command_var = _Var("")
    panel.execution_var = _Var("")
    panel.ap_generate_status_var = _Var("")
    panel.ap_last_generated_var = _Var("")
    panel.refresh_ap_review_summary = lambda: None
    panel._append_output = lambda _line: None

    def fake_run(_cmd, cwd, capture_output, text, check):
        for target in panel._ap_generation_targets(day):
            target.write_text("new", encoding="utf-8")
            target.touch()
        payload = {"ok": True}
        return type("C", (), {"stdout": json.dumps(payload), "stderr": "", "returncode": 0})()

    monkeypatch.setattr(cp.subprocess, "run", fake_run)
    panel.generate_ap_html()
    new_mtime = (site_dir / "index.html").stat().st_mtime
    assert new_mtime != old_mtime
    assert "Generated:" in panel.ap_generate_status_var.get()
    assert "Files rewritten: yes" in panel.ap_generate_status_var.get()
    assert "Last generated locally:" in panel.ap_last_generated_var.get()


def test_generate_ap_html_failure_shows_explicit_reason(monkeypatch, tmp_path):
    day = "2026-05-16"
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    panel.ap_review_date_var = _Var(day)
    panel.command_var = _Var("")
    panel.execution_var = _Var("")
    panel.ap_generate_status_var = _Var("")
    panel.ap_last_generated_var = _Var("")
    panel.refresh_ap_review_summary = lambda: None
    panel._append_output = lambda _line: None

    def fake_run(_cmd, cwd, capture_output, text, check):
        payload = {"ok": False, "errors": ["weekly publish blocked by readiness/quality gate"]}
        return type("C", (), {"stdout": json.dumps(payload), "stderr": "", "returncode": 1})()

    monkeypatch.setattr(cp.subprocess, "run", fake_run)
    panel.generate_ap_html()
    assert "failed/skipped" in panel.ap_generate_status_var.get().lower()
    assert "readiness/quality gate" in panel.ap_generate_status_var.get().lower()


def test_generate_ap_html_mode_has_no_publish_or_push(monkeypatch, tmp_path):
    day = "2026-05-16"
    panel = cp.DispatchesControlPanel.__new__(cp.DispatchesControlPanel)
    panel.root_dir = tmp_path
    panel.ap_review_date_var = _Var(day)
    panel.command_var = _Var("")
    panel.execution_var = _Var("")
    panel.ap_generate_status_var = _Var("")
    panel.ap_last_generated_var = _Var("")
    panel.refresh_ap_review_summary = lambda: None
    panel._append_output = lambda _line: None
    captured = {"cmd": []}

    def fake_run(cmd, cwd, capture_output, text, check):
        captured["cmd"] = cmd
        return type("C", (), {"stdout": json.dumps({"ok": True}), "stderr": "", "returncode": 0})()

    monkeypatch.setattr(cp.subprocess, "run", fake_run)
    panel.generate_ap_html()
    assert "--publish" not in captured["cmd"]
    assert "--push" not in captured["cmd"]
    assert "--force-regenerate" in captured["cmd"]
