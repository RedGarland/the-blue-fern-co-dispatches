from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import scripts.review_american_pressure_candidates as review_script
import scripts.scout_american_pressure_candidates as scout
import scripts.run_american_pressure_dispatch as ap_runner
import scripts.approve_american_pressure_candidates as approve_script
import scripts.american_pressure_review_workflow as apwf


def _write_min_registry(root: Path) -> None:
    path = root / "data" / "dispatches" / "american-pressure" / "source_registry.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: snap-anchor
    name: SNAP Data Tables
    url: https://example.com/snap-anchor
    publisher: USDA
    pillar: food_pressure
    geography: US
    source_type: official_dataset_page
    reliability_tier: official_primary
    update_frequency: monthly
    enabled: true
    source_state: enabled
    notes: baseline
""",
        encoding="utf-8",
    )


def test_candidate_files_created_for_date_range(tmp_path, monkeypatch):
    monkeypatch.setattr(scout, "ROOT", tmp_path)
    monkeypatch.setattr(scout, "CANDIDATES_ROOT", tmp_path / "data" / "dispatches" / "american-pressure" / "candidates")
    monkeypatch.setattr(scout, "TARGETS_PATH", tmp_path / "data" / "dispatches" / "american-pressure" / "search_targets.yml")
    scout.TARGETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    scout.TARGETS_PATH.write_text(json.dumps({"target_groups": {pillar: {"search_phrases": ["x"], "data_anchor_hints": []} for pillar in scout.PILLARS}}), encoding="utf-8")
    monkeypatch.setattr(scout, "_load_registry_anchors", lambda: {pillar: [] for pillar in scout.PILLARS})
    monkeypatch.setattr(scout, "_fetch_rss_items", lambda _query: [{"title": "Local layoffs hit county workers", "url": "https://example.com/a", "publisher": "Local News", "published_at": "2026-05-10T00:00:00Z", "summary_or_snippet": "County residents face layoffs and service strain."}])
    rc = scout.main(["--start-date", "2026-05-10", "--end-date", "2026-05-11", "--write", "--max-per-pillar", "1"])
    assert rc == 0
    for day in ("2026-05-10", "2026-05-11"):
        assert (scout.CANDIDATES_ROOT / day / "candidate_sources.json").exists()


def test_candidate_schema_and_scoring(monkeypatch):
    monkeypatch.setattr(scout, "_load_registry_anchors", lambda: {pillar: ["anchor-1"] for pillar in scout.PILLARS})
    monkeypatch.setattr(scout, "_read_existing_candidate_urls", lambda: set())
    monkeypatch.setattr(scout, "_load_targets", lambda: {"target_groups": {pillar: {"search_phrases": ["x"], "data_anchor_hints": ["anchor-1"]} for pillar in scout.PILLARS}})
    fake = [{"title": "County layoffs affect 503 workers", "url": "https://example.com/story", "publisher": "Local Gazette", "published_at": "2026-05-10T00:00:00Z", "summary_or_snippet": "Residents face job loss and rent pressure in Sacramento, California."}]
    payload = scout.scout_day("2026-05-10", max_per_pillar=1, fetcher=lambda _query: fake)
    row = payload["sources"][0]
    for key in (
        "source_record_id", "source_id", "title", "url", "publisher", "published_at", "retrieved_at", "summary_or_snippet",
        "source_type", "region_scope", "category_hint", "pillar", "reliability_tier", "source_role", "item_type",
        "reader_headline", "human_story_summary", "what_happened", "potential_relevance", "who_may_feel_it",
        "what_to_watch_next", "location", "affected_people", "pressure_direction", "public_pressure_angle",
        "linked_data_anchor_ids", "candidate_score", "candidate_score_reasons", "review_status",
    ):
        assert key in row
    assert row["review_status"] == "needs_review"
    assert int(row["candidate_score"]) > 0


def test_google_rss_html_is_stripped_from_candidate_text(monkeypatch):
    monkeypatch.setattr(scout, "_load_registry_anchors", lambda: {pillar: ["anchor-1"] for pillar in scout.PILLARS})
    monkeypatch.setattr(scout, "_read_existing_candidate_urls", lambda: set())
    monkeypatch.setattr(scout, "_load_targets", lambda: {"target_groups": {pillar: {"search_phrases": ["x"], "data_anchor_hints": ["anchor-1"]} for pillar in scout.PILLARS}})
    fake = [
        {
            "title": '<a href="https://news.google.com/rss/articles/abc" target="_blank">St. Johns Food Share demand rises</a> - Fathom Journal',
            "url": "https://example.com/story",
            "publisher": "Fathom Journal",
            "published_at": "2026-05-10T00:00:00Z",
            "summary_or_snippet": '<font color="#6f6f6f">Households face pressure</font> &amp; pantry demand',
        }
    ]
    payload = scout.scout_day("2026-05-10", max_per_pillar=1, fetcher=lambda _query: fake)
    row = payload["sources"][0]
    combined = f"{row['title']} {row['summary_or_snippet']} {row['reader_headline']}".lower()
    assert "<a " not in combined
    assert "</a>" not in combined
    assert "<font" not in combined
    assert "href=" not in combined
    assert "target=" not in combined
    assert "news.google.com/rss/articles" not in combined


def test_non_us_candidate_is_quarantined(monkeypatch):
    monkeypatch.setattr(scout, "_load_registry_anchors", lambda: {pillar: ["anchor-1"] for pillar in scout.PILLARS})
    monkeypatch.setattr(scout, "_read_existing_candidate_urls", lambda: set())
    monkeypatch.setattr(scout, "_load_targets", lambda: {"target_groups": {pillar: {"search_phrases": ["x"], "data_anchor_hints": ["anchor-1"]} for pillar in scout.PILLARS}})
    fake = [{"title": "BC SPCA warns of rising costs", "url": "https://example.com/nonus", "publisher": "BC SPCA", "published_at": "2026-05-10T00:00:00Z", "summary_or_snippet": "Vancouver households face pressure."}]
    payload = scout.scout_day("2026-05-10", max_per_pillar=1, fetcher=lambda _query: fake)
    row = payload["sources"][0]
    assert row["review_status"] == "quarantine"
    assert row["us_relevance_ok"] is False


def test_malformed_title_fragment_quarantined(monkeypatch):
    monkeypatch.setattr(scout, "_load_registry_anchors", lambda: {pillar: ["anchor-1"] for pillar in scout.PILLARS})
    monkeypatch.setattr(scout, "_read_existing_candidate_urls", lambda: set())
    monkeypatch.setattr(scout, "_load_targets", lambda: {"target_groups": {pillar: {"search_phrases": ["x"], "data_anchor_hints": ["anchor-1"]} for pillar in scout.PILLARS}})
    fake = [{"title": "Housing and bill pressure in Structure, Rejecting is squeezing household budgets", "url": "https://example.com/bad", "publisher": "Some News", "published_at": "2026-05-10T00:00:00Z", "summary_or_snippet": "Local households affected."}]
    payload = scout.scout_day("2026-05-10", max_per_pillar=1, fetcher=lambda _query: fake)
    row = payload["sources"][0]
    assert row["review_status"] == "quarantine"
    assert row["editorial_rejection_reason"] == "prose_quality_failed"


def test_investor_only_downrank_and_no_url_rejected():
    score, _, rejected = scout.score_candidate(
        {"title": "Investor call raises EPS guidance", "url": "https://example.com/investor", "publisher": "Bizwire", "summary_or_snippet": "Shareholder value focus"},
        pillar="financial_distress_pressure",
        anchor_ids=[],
        seen_urls=set(),
    )
    assert score < 0
    assert "investor_only" in rejected
    score2, _, rejected2 = scout.score_candidate(
        {"title": "Layoffs", "url": "", "publisher": "News", "summary_or_snippet": "Local layoffs hit workers"},
        pillar="labor_income_pressure",
        anchor_ids=[],
        seen_urls=set(),
    )
    assert score2 <= -100
    assert "no_url" in rejected2


def test_duplicate_candidate_suppressed():
    score, _, rejected = scout.score_candidate(
        {"title": "Local layoffs hit workers", "url": "https://example.com/dup", "publisher": "News", "summary_or_snippet": "Workers impacted."},
        pillar="labor_income_pressure",
        anchor_ids=[],
        seen_urls={"https://example.com/dup"},
    )
    assert score < 0
    assert "duplicate_or_stale" in rejected


def test_review_report_includes_missing_pillars(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-10" / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps({"sources": [{"title": "Food story", "url": "https://example.com/food", "pillar": "food_pressure", "candidate_score": 50, "candidate_bucket": "recommended"}]}), encoding="utf-8")
    rc = review_script.main(["--date", "2026-05-10", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-10_candidate_review.md").read_text(encoding="utf-8")
    assert "Missing required pillars" in report
    assert "labor_income_pressure" in report
    assert "candidate_file_exists: True" in report
    assert "candidate_count_raw: 1" in report


def test_review_report_sanitizes_candidate_title(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-10" / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "title": '<a href="https://news.google.com/rss/articles/abc">Messy headline</a> - Publisher',
                        "url": "https://example.com/food",
                        "publisher": "Publisher",
                        "pillar": "food_pressure",
                        "public_pressure_angle": "x",
                        "candidate_score": 50,
                        "candidate_bucket": "recommended",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rc = review_script.main(["--date", "2026-05-10", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-10_candidate_review.md").read_text(encoding="utf-8")
    assert "Messy headline" in report
    assert "<a " not in report


def test_review_report_empty_candidate_file_has_clear_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-11" / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps({"sources": []}), encoding="utf-8")
    rc = review_script.main(["--date", "2026-05-11", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-11_candidate_review.md").read_text(encoding="utf-8")
    assert "candidate_file_exists: True" in report
    assert "candidate_count_raw: 0" in report
    assert "review_state: Candidate file exists but is empty." in report
    assert "Missing required pillars" in report


def test_review_report_invalid_candidates_counted_and_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-12" / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        json.dumps(
            {
                "sources": [
                    {"title": "Missing URL", "pillar": "food_pressure", "candidate_bucket": "recommended", "public_pressure_angle": "x"},
                    {"title": "Missing angle", "pillar": "food_pressure", "candidate_bucket": "recommended", "url": "https://example.com/a"},
                ]
            }
        ),
        encoding="utf-8",
    )
    rc = review_script.main(["--date", "2026-05-12", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-12_candidate_review.md").read_text(encoding="utf-8")
    assert "candidate_count_raw: 2" in report
    assert "candidate_count_valid: 0" in report
    assert "rejected_validation_count: 2" in report
    assert "skipped_no_url_count: 1" in report
    assert "skipped_no_public_pressure_angle_count: 1" in report


def test_review_report_valid_candidates_show_recommended_and_maybe(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-13" / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "title": "Recommended item",
                        "url": "https://example.com/r",
                        "pillar": "food_pressure",
                        "public_pressure_angle": "x",
                        "candidate_bucket": "recommended",
                        "candidate_score": 50,
                    },
                    {
                        "title": "Maybe item",
                        "url": "https://example.com/m",
                        "pillar": "labor_income_pressure",
                        "public_pressure_angle": "x",
                        "candidate_bucket": "maybe",
                        "candidate_score": 10,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    rc = review_script.main(["--date", "2026-05-13", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-13_candidate_review.md").read_text(encoding="utf-8")
    assert "Recommended item" in report
    assert "Maybe item" in report
    assert "candidate_count_valid: 2" in report


def test_review_report_missing_path_reports_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    rc = review_script.main(["--date", "2026-05-14", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-14_candidate_review.md").read_text(encoding="utf-8")
    assert "candidate_file_exists: False" in report
    assert "candidate_file_path:" in report
    assert "No candidate file was found for this date." in report


def test_review_report_shows_no_live_backend_notice_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(review_script, "ROOT", tmp_path)
    monkeypatch.setattr(review_script, "REVIEW_ROOT", tmp_path / "output" / "dispatches" / "american-pressure" / "review")
    monkeypatch.setattr(review_script, "_load_targets", lambda: {"target_groups": {pillar: {"data_anchor_hints": ["x"]} for pillar in scout.PILLARS}})
    candidate_path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-15" / "candidate_sources.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(
        json.dumps(
            {
                "sources": [],
                "diagnostics": {
                    "no_live_collection_backend_message": "No live candidate collection backend is configured; add manual candidate records or configure source collectors."
                },
            }
        ),
        encoding="utf-8",
    )
    rc = review_script.main(["--date", "2026-05-15", "--write"])
    assert rc == 0
    report = (review_script.REVIEW_ROOT / "2026-05-15_candidate_review.md").read_text(encoding="utf-8")
    assert "collector_notice: No live candidate collection backend is configured; add manual candidate records or configure source collectors." in report


def test_approved_candidates_feed_weekly_run(tmp_path):
    _write_min_registry(tmp_path)
    manual = [
        {
            "source_record_id": "ap-2026-05-10-snap",
            "source_id": "snap",
            "title": "SNAP baseline",
            "url": "https://example.com/snap",
            "publisher": "USDA",
            "published_at": "2026-05-10T00:00:00Z",
            "retrieved_at": "2026-05-10T01:00:00Z",
            "summary_or_snippet": "Official baseline indicator source.",
            "source_type": "official_dataset_page",
            "region_scope": "US",
            "category_hint": "food_pressure",
            "pillar": "food_pressure",
            "reliability_tier": "official_primary",
            "source_role": "data_anchor",
        }
    ]
    manual_path = tmp_path / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-10" / "manual_sources.json"
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(json.dumps({"sources": manual}, indent=2), encoding="utf-8")
    candidate = {
        "source_record_id": "ap-2026-05-10-labor-story",
        "source_id": "labor-story",
        "title": "County layoffs affect workers",
        "url": "https://example.com/labor",
        "publisher": "Local News",
        "published_at": "2026-05-10T00:00:00Z",
        "retrieved_at": "2026-05-10T01:00:00Z",
        "summary_or_snippet": "County layoffs affect workers this week.",
        "source_type": "news_report",
        "region_scope": "US",
        "category_hint": "labor_income_pressure",
        "pillar": "labor_income_pressure",
        "reliability_tier": "reputable_reporting",
        "source_role": "human_story",
        "public_pressure_angle": "Household pressure signal from current-week local impacts.",
        "review_status": "approved",
        "linked_data_anchor_ids": ["ap-2026-05-10-snap"],
    }
    cpath = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-10" / "candidate_sources.json"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps({"sources": [candidate]}, indent=2), encoding="utf-8")
    result = ap_runner.run_american_pressure_dispatch(
        tmp_path, "2026-05-10", publish=False, dry_run=False, from_manual_sources=False, source_mode="both", include_approved_candidates=True
    )
    assert result["ok"] is True
    manifest = json.loads((tmp_path / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-10" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert manifest["human_story_count_by_pillar"]["labor_income_pressure"] >= 1


def test_unapproved_candidates_do_not_feed_weekly_run(tmp_path):
    _write_min_registry(tmp_path)
    manual = [
        {
            "source_record_id": "ap-2026-05-10-snap",
            "source_id": "snap",
            "title": "SNAP baseline",
            "url": "https://example.com/snap",
            "publisher": "USDA",
            "published_at": "2026-05-10T00:00:00Z",
            "retrieved_at": "2026-05-10T01:00:00Z",
            "summary_or_snippet": "Official baseline indicator source.",
            "source_type": "official_dataset_page",
            "region_scope": "US",
            "category_hint": "food_pressure",
            "pillar": "food_pressure",
            "reliability_tier": "official_primary",
            "source_role": "data_anchor",
        }
    ]
    manual_path = tmp_path / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-10" / "manual_sources.json"
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(json.dumps({"sources": manual}, indent=2), encoding="utf-8")
    candidate = {
        "source_record_id": "ap-2026-05-10-labor-story",
        "source_id": "labor-story",
        "title": "County layoffs affect workers",
        "url": "https://example.com/labor",
        "publisher": "Local News",
        "published_at": "2026-05-10T00:00:00Z",
        "retrieved_at": "2026-05-10T01:00:00Z",
        "summary_or_snippet": "County layoffs affect workers this week.",
        "source_type": "news_report",
        "region_scope": "US",
        "category_hint": "labor_income_pressure",
        "pillar": "labor_income_pressure",
        "reliability_tier": "reputable_reporting",
        "source_role": "human_story",
        "public_pressure_angle": "Household pressure signal from current-week local impacts.",
        "review_status": "needs_review",
        "linked_data_anchor_ids": ["ap-2026-05-10-snap"],
    }
    cpath = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / "2026-05-10" / "candidate_sources.json"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps({"sources": [candidate]}, indent=2), encoding="utf-8")
    result = ap_runner.run_american_pressure_dispatch(
        tmp_path, "2026-05-10", publish=False, dry_run=False, from_manual_sources=False, source_mode="both", include_approved_candidates=True
    )
    assert result["ok"] is True
    manifest = json.loads((tmp_path / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-10" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert manifest["human_story_count_by_pillar"]["labor_income_pressure"] == 0
    assert manifest["source_count"] == len(json.loads((tmp_path / "output" / "site" / "american-pressure" / "editions" / "2026-05-10" / "sources_manifest.json").read_text(encoding="utf-8")))


def test_review_script_help_direct_and_module():
    root = Path(__file__).resolve().parents[1]
    direct = subprocess.run(
        [sys.executable, "scripts\\review_american_pressure_candidates.py", "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    module = subprocess.run(
        [sys.executable, "-m", "scripts.review_american_pressure_candidates", "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0
    assert module.returncode == 0
    assert "usage:" in direct.stdout.lower()
    assert "usage:" in module.stdout.lower()


def test_scout_script_help_direct_and_module():
    root = Path(__file__).resolve().parents[1]
    direct = subprocess.run(
        [sys.executable, "scripts\\scout_american_pressure_candidates.py", "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    module = subprocess.run(
        [sys.executable, "-m", "scripts.scout_american_pressure_candidates", "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 0
    assert module.returncode == 0
    assert "usage:" in direct.stdout.lower()
    assert "usage:" in module.stdout.lower()


def test_invalid_suggested_anchors_excluded_and_diagnosed(monkeypatch):
    monkeypatch.setattr(scout, "_read_existing_candidate_urls", lambda: set())
    monkeypatch.setattr(scout, "_load_targets", lambda: {"target_groups": {pillar: {"search_phrases": ["x"], "data_anchor_hints": ["valid-anchor", "invalid-anchor"]} for pillar in scout.PILLARS}})
    monkeypatch.setattr(scout, "_load_registry_anchors", lambda: {pillar: ["valid-anchor"] for pillar in scout.PILLARS})
    fake = [{"title": "County layoffs affect workers", "url": "https://example.com/story", "publisher": "Local Gazette", "published_at": "2026-05-10T00:00:00Z", "summary_or_snippet": "Residents face job loss."}]
    payload = scout.scout_day("2026-05-10", max_per_pillar=1, fetcher=lambda _query: fake)
    assert payload["sources"]
    for row in payload["sources"]:
        assert "invalid-anchor" not in row.get("linked_data_anchor_ids", [])
    diagnostics = payload.get("diagnostics", {})
    assert "invalid-anchor" in diagnostics.get("suggested_unavailable_anchor_ids", [])


def test_approve_helper_list_output_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(approve_script, "CANDIDATES_ROOT", tmp_path / "data" / "dispatches" / "american-pressure" / "candidates")
    cpath = approve_script.CANDIDATES_ROOT / "2026-05-10" / "candidate_sources.json"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(
        json.dumps(
            {
                "sources": [
                    {"source_record_id": "ap-1", "pillar": "food_pressure", "review_status": "approved", "title": "Food"},
                    {"source_record_id": "ap-2", "pillar": "labor_income_pressure", "review_status": "needs_review", "title": "Labor"},
                ]
            }
        ),
        encoding="utf-8",
    )
    rc = approve_script.main(["--date", "2026-05-10", "--list"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["status_counts"]["approved"] == 1
    assert payload["summary"]["status_counts"]["needs_review"] == 1
    assert payload["summary"]["approved_by_pillar"]["food_pressure"] == 1
    assert len(payload["candidates"]) == 2


def test_approve_helper_status_changes_and_write(tmp_path, monkeypatch):
    monkeypatch.setattr(approve_script, "CANDIDATES_ROOT", tmp_path / "data" / "dispatches" / "american-pressure" / "candidates")
    cpath = approve_script.CANDIDATES_ROOT / "2026-05-11" / "candidate_sources.json"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "sources": [
            {"source_record_id": "ap-1", "title": "Title1", "url": "https://example.com/1", "source_id": "one", "review_status": "needs_review"},
            {"source_record_id": "ap-2", "title": "Title2", "url": "https://example.com/2", "source_id": "two", "review_status": "needs_review"},
            {"source_record_id": "ap-3", "title": "Title3", "url": "https://example.com/3", "source_id": "three", "review_status": "needs_review"},
            {"source_record_id": "ap-4", "title": "Title4", "url": "https://example.com/4", "source_id": "four", "review_status": "needs_review"},
        ]
    }
    cpath.write_text(json.dumps(original, indent=2), encoding="utf-8")
    assert approve_script.main(["--date", "2026-05-11", "--approve", "ap-1", "--write"]) == 0
    assert approve_script.main(["--date", "2026-05-11", "--reject", "ap-2", "--write"]) == 0
    assert approve_script.main(["--date", "2026-05-11", "--maybe", "ap-3", "--write"]) == 0
    assert approve_script.main(["--date", "2026-05-11", "--needs-review", "ap-4", "--write"]) == 0
    rows = json.loads(cpath.read_text(encoding="utf-8"))["sources"]
    by_id = {row["source_record_id"]: row for row in rows}
    assert by_id["ap-1"]["review_status"] == "approved"
    assert by_id["ap-2"]["review_status"] == "rejected"
    assert by_id["ap-3"]["review_status"] == "maybe"
    assert by_id["ap-4"]["review_status"] == "needs_review"
    assert by_id["ap-1"]["title"] == "Title1"
    assert by_id["ap-1"]["url"] == "https://example.com/1"
    assert by_id["ap-1"]["source_id"] == "one"
    original_by_id = {row["source_record_id"]: row for row in original["sources"]}
    for key in ("ap-1", "ap-2", "ap-3", "ap-4"):
        for field, value in original_by_id[key].items():
            if field == "review_status":
                continue
            assert by_id[key][field] == value


def test_approve_helper_missing_source_record_id_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(approve_script, "CANDIDATES_ROOT", tmp_path / "data" / "dispatches" / "american-pressure" / "candidates")
    cpath = approve_script.CANDIDATES_ROOT / "2026-05-12" / "candidate_sources.json"
    cpath.parent.mkdir(parents=True, exist_ok=True)
    cpath.write_text(json.dumps({"sources": [{"source_record_id": "ap-1", "review_status": "needs_review"}]}), encoding="utf-8")
    rc = approve_script.main(["--date", "2026-05-12", "--approve", "missing", "--write"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "source_record_id not found" in payload["errors"][0]


def test_weekly_candidate_loads_across_seven_days(tmp_path):
    for i in range(7):
        day = f"2026-05-{10 + i:02d}"
        path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sources": [{"source_record_id": f"id-{i}", "url": f"https://example.com/{i}", "review_status": "needs_review"}]}), encoding="utf-8")
    rows = apwf.load_weekly_candidates(tmp_path, "2026-05-16")
    assert len(rows) == 7


def test_save_review_decisions_mutates_only_review_metadata_fields(tmp_path):
    day = "2026-05-16"
    path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "sources": [
            {
                "source_record_id": "ap-1",
                "title": "Keep title",
                "url": "https://example.com/1",
                "publisher": "Keep publisher",
                "review_status": "needs_review",
                "public_pressure_angle": "angle",
                "us_relevance_ok": True,
            }
        ]
    }
    path.write_text(json.dumps(original, indent=2), encoding="utf-8")
    row = apwf.load_weekly_candidates(tmp_path, day)[0]
    apwf.save_review_decisions(tmp_path, day, {row["candidate_key"]: "approved"})
    updated = json.loads(path.read_text(encoding="utf-8"))["sources"][0]
    assert updated["review_status"] == "approved"
    assert updated["title"] == "Keep title"
    assert updated["url"] == "https://example.com/1"
    assert updated["publisher"] == "Keep publisher"
    assert "user_reviewed_at" in updated


def test_weekly_candidate_duplicate_source_record_ids_have_unique_keys(tmp_path):
    day = "2026-05-16"
    path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"source_record_id": "dup-id", "url": "https://example.com/a", "review_status": "needs_review"},
                    {"source_record_id": "dup-id", "url": "https://example.com/b", "review_status": "needs_review"},
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    rows = apwf.load_weekly_candidates(tmp_path, day)
    assert len(rows) == 2
    assert len({row["candidate_key"] for row in rows}) == 2
    assert rows[0]["source_record_id"] == "dup-id"
    assert rows[1]["source_record_id"] == "dup-id"
    assert rows[0]["source_record_ordinal"] == 0
    assert rows[1]["source_record_ordinal"] == 1


def test_save_review_decisions_updates_only_targeted_duplicate_row(tmp_path):
    day = "2026-05-16"
    path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sources": [
            {"source_record_id": "dup-id", "title": "First", "url": "https://example.com/a", "review_status": "needs_review"},
            {"source_record_id": "dup-id", "title": "Second", "url": "https://example.com/b", "review_status": "needs_review"},
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    rows = apwf.load_weekly_candidates(tmp_path, day)
    second = rows[1]
    apwf.save_review_decisions(tmp_path, day, {second["candidate_key"]: "approved"})
    updated = json.loads(path.read_text(encoding="utf-8"))["sources"]
    assert updated[0]["review_status"] == "needs_review"
    assert updated[1]["review_status"] == "approved"
    assert updated[0]["source_record_id"] == "dup-id"
    assert updated[1]["source_record_id"] == "dup-id"


def test_weekly_candidate_load_handles_155_rows(tmp_path):
    day = "2026-05-16"
    path = tmp_path / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(155):
        rows.append(
            {
                "source_record_id": f"ap-{i % 20}",
                "url": f"https://example.com/{i}",
                "review_status": "needs_review",
                "public_pressure_angle": "x",
                "us_relevance_ok": True,
            }
        )
    path.write_text(json.dumps({"sources": rows}, indent=2), encoding="utf-8")
    loaded = apwf.load_weekly_candidates(tmp_path, day)
    assert len(loaded) == 155
    assert len({row["candidate_key"] for row in loaded}) == 155
