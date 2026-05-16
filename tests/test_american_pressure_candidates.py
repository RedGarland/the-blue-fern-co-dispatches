from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import scripts.review_american_pressure_candidates as review_script
import scripts.scout_american_pressure_candidates as scout
import scripts.run_american_pressure_dispatch as ap_runner


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
