import json
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import scripts.run_american_pressure_dispatch as ap_runner


@pytest.fixture()
def work_root():
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "american-pressure-runner"
    shutil.copytree(repo / "assets", root / "assets")
    shutil.copytree(repo / "data" / "dispatches" / "american-pressure", root / "data" / "dispatches" / "american-pressure")
    candidates_root = root / "data" / "dispatches" / "american-pressure" / "candidates"
    if candidates_root.exists():
        shutil.rmtree(candidates_root)
        candidates_root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def _write_manual_sources(root: Path, edition_date: str, records: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "american-pressure" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def _write_daily_candidates(root: Path, day: str, records: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": records}, indent=2), encoding="utf-8")
    return path


def _record(source_id: str, pillar: str, title: str, summary: str, url: str, *, source_type: str = "official_dataset_page", source_role: str | None = None, linked_data_anchor_ids: list[str] | None = None) -> dict:
    row = {
        "source_record_id": f"ap-2026-05-12-{source_id}",
        "source_id": source_id,
        "title": title,
        "url": url,
        "publisher": "Publisher",
        "published_at": "2026-05-10T00:00:00Z",
        "retrieved_at": "2026-05-12T12:00:00Z",
        "summary_or_snippet": summary,
        "source_type": source_type,
        "geography": "US",
        "pillar": pillar,
        "category_hint": pillar,
        "reliability_tier": "official_primary",
    }
    if source_role:
        row["source_role"] = source_role
        if source_role == "human_story":
            row["public_pressure_angle"] = "Household pressure signal from current-week local impacts."
    if linked_data_anchor_ids:
        row["linked_data_anchor_ids"] = linked_data_anchor_ids
    return row


def test_future_date_refused_without_allow_future(work_root):
    future_date = (datetime.now().date() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        ap_runner.run_american_pressure_dispatch(work_root, future_date, publish=False, dry_run=False, from_manual_sources=False, source_mode="auto")


def test_human_story_and_data_anchor_combine_into_one_mini_brief(work_root):
    manual = [
        _record("food-story", "food_pressure", "Local food bank demand surges", "Food bank reported longer pantry lines this week.", "https://example.com/food-story", source_type="news_report", source_role="human_story"),
        _record("snap-anchor", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    assert result["source_count"] == 2
    assert result["story_count"] == 1
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    story = curation["stories"][0]
    assert story["brief_quality"] == "story_plus_data"
    assert len(story["human_story_source_ids"]) == 1
    assert len(story["data_anchor_source_ids"]) == 1


def test_food_bank_story_links_to_snap_anchor(work_root):
    manual = [
        _record("food-story", "food_pressure", "Neighborhood pantry warning", "Pantries report demand spikes.", "https://example.com/food-story", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["ap-2026-05-12-snap-anchor"]),
        _record("snap-anchor", "food_pressure", "USDA SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "<strong>Current Development:</strong>" in html
    assert "<strong>Data Context:</strong>" in html
    assert "<strong>Sources:</strong>" in html
    assert "USDA SNAP Household Characteristics" in html


def test_bankruptcy_story_links_to_us_courts_data_anchor(work_root):
    manual = [
        _record("bk-story", "financial_distress_pressure", "Regional employer files Chapter 11", "The filing raises concern for payroll continuity.", "https://example.com/employer-bankruptcy", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["ap-2026-05-12-uscourts"]),
        _record("uscourts", "financial_distress_pressure", "U.S. Courts Bankruptcy Filings Statistics", "Official baseline indicator source.", "https://www.uscourts.gov/statistics-reports/analysis-reports/bankruptcy-filings-statistics", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    story = curation["stories"][0]
    assert "ap-2026-05-12-uscourts" in story["data_anchor_source_ids"]


def test_baseline_only_item_gets_baseline_label(work_root):
    manual = [
        _record("cpi", "housing_household_cost_pressure", "BLS CPI Shelter Index", "Official baseline indicator source.", "https://example.com/cpi", source_role="data_anchor"),
        _record("medicaid", "health_access_pressure", "Medicaid and CHIP Enrollment Data", "Official baseline indicator source.", "https://example.com/medicaid", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "<strong>Data Context:</strong>" in html
    assert "No current-development source was captured for this pillar." in html
    assert "Type: baseline_gauge" not in html


def test_data_anchor_not_mislabeled_current_week_development(work_root):
    manual = [
        _record("uscourts", "financial_distress_pressure", "U.S. Courts Bankruptcy Filings Statistics", "Official baseline indicator source.", "https://www.uscourts.gov/statistics-reports/analysis-reports/bankruptcy-filings-statistics", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    assert curation["stories"][0]["item_type"] == "baseline_gauge"


def test_story_count_counts_mini_briefs_not_sources_and_counts_reconcile(work_root):
    manual = [
        _record("food-story", "food_pressure", "Food pantry pressure report", "Pantry demand is rising.", "https://example.com/food-story", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
        _record("jobs", "labor_income_pressure", "BLS Employment Situation", "Official baseline indicator source.", "https://example.com/jobs", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="both")
    assert result["ok"] is True
    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-12" / "edition_manifest.json").read_text(encoding="utf-8"))
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_count"] == len(sources)
    assert manifest["story_count"] == len(curation["stories"])
    assert manifest["story_count"] < manifest["source_count"]
    assert curation["stories"][0]["item_type"] in {"current_week_development", "baseline_gauge"}
    assert curation["stories"][0]["brief_quality"] in {"story_plus_data", "baseline_only", "watchlist_only", "official_release_only"}
    assert any(source.get("manual_source_role") for source in sources)


def test_no_duplicate_reader_headlines(work_root):
    manual = [
        _record("snap-a", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap-a", source_role="data_anchor"),
        _record("snap-b", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap-b", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    titles = [story["title"] for story in curation["stories"]]
    assert len(titles) == len(set(titles))


def test_linked_data_anchor_ids_must_resolve(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand story", "Pantry demand rose this week.", "https://example.com/story", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["missing-anchor-id"]),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    manual[0]["public_pressure_angle"] = "Food assistance demand pressure."
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is False
    assert any("unknown linked_data_anchor_id" in err for err in result["errors"])
    assert any("available anchors:" in err for err in result["errors"])
    assert any("food-story" in err for err in result["errors"])


def test_human_story_without_public_pressure_angle_rejected(work_root):
    manual = [
        _record("labor-story", "labor_income_pressure", "Local layoffs announced", "Employer announced layoffs this week.", "https://example.com/labor-story", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["bls-unemployment-situation"]),
        _record("bls", "labor_income_pressure", "BLS Employment Situation", "Official baseline indicator source.", "https://example.com/bls", source_role="data_anchor"),
    ]
    manual[0].pop("public_pressure_angle", None)
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is False
    assert any("public_pressure_angle" in err for err in result["errors"])


def test_both_mode_with_manual_human_stories_produces_story_plus_data(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand story", "Pantry demand rose this week.", "https://example.com/story", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["usda-fns-snap-data-tables"]),
    ]
    manual[0]["public_pressure_angle"] = "Food assistance demand pressure."
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="both")
    assert result["ok"] is True
    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-12" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert manifest["brief_quality_counts"]["story_plus_data"] > 0


def test_both_mode_auto_baselines_do_not_fail_human_story_validation(work_root):
    manual = [
        _record(
            "food-story",
            "food_pressure",
            "Pantry demand story",
            "Pantry demand rose this week.",
            "https://example.com/story",
            source_type="news_report",
            source_role="human_story",
            linked_data_anchor_ids=["usda-fns-snap-data-tables"],
        ),
    ]
    manual[0]["public_pressure_angle"] = "Food assistance demand pressure."
    _write_manual_sources(work_root, "2026-05-23", manual)
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-23",
        publish=False,
        dry_run=False,
        from_manual_sources=False,
        source_mode="both",
    )
    assert result["ok"] is True
    assert not any("missing required fields: public_pressure_angle" in err for err in result["errors"])


def test_weekly_cadence_label_remains_public(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Weekly briefing / May 6–May 12, 2026" in html


def test_weekly_manifest_includes_date_range_metadata(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-09", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-09", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-09" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert manifest["week_start_date"] == "2026-05-03"
    assert manifest["week_end_date"] == "2026-05-09"
    assert manifest["display_date_range"] == "May 3–May 9, 2026"


def test_publish_filters_future_american_pressure_editions_from_archive(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-09", manual)
    future = work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-19"
    future.mkdir(parents=True, exist_ok=True)
    (future / "index.html").write_text("<html>future</html>", encoding="utf-8")
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-09", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    index_html = (work_root / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    archive_html = (work_root / "output" / "site" / "american-pressure" / "archive.html").read_text(encoding="utf-8")
    rss_xml = (work_root / "output" / "site" / "american-pressure" / "rss.xml").read_text(encoding="utf-8")
    assert "2026-05-19" not in index_html
    assert "2026-05-19" not in archive_html
    assert "2026-05-19" not in rss_xml


def test_publish_lists_valid_american_pressure_editions_with_latest_first(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-09", manual)
    _write_manual_sources(work_root, "2026-05-16", manual)
    first = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-09", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    second = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert first["ok"] is True
    assert second["ok"] is True
    index_html = (work_root / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    archive_html = (work_root / "output" / "site" / "american-pressure" / "archive.html").read_text(encoding="utf-8")
    rss_xml = (work_root / "output" / "site" / "american-pressure" / "rss.xml").read_text(encoding="utf-8")
    assert "2026-05-16" in index_html
    assert "2026-05-09" in index_html
    assert index_html.index("2026-05-16") < index_html.index("2026-05-09")
    assert "2026-05-16" in archive_html and "2026-05-09" in archive_html
    assert "2026-05-16" in rss_xml and "2026-05-09" in rss_xml


def test_publish_excludes_stale_thin_unlistable_edition(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-09", manual)
    _write_manual_sources(work_root, "2026-05-16", manual)
    stale_dir = work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-17"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "index.html").write_text("<html><a href='https://example.com'>stale</a></html>", encoding="utf-8")
    (stale_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "american-pressure",
                "edition_date": "2026-05-17",
                "week_start_date": "2026-05-11",
                "week_end_date": "2026-05-17",
                "display_date_range": "May 11–May 17, 2026",
                "source_count": 1,
                "story_count": 1,
                "story_plus_data_count": 0,
                "baseline_only_edition": True,
                "public_exposed": True,
                "is_free_public": True,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    first = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-09", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert first["ok"] is True
    assert result["ok"] is True
    index_html = (work_root / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    archive_html = (work_root / "output" / "site" / "american-pressure" / "archive.html").read_text(encoding="utf-8")
    rss_xml = (work_root / "output" / "site" / "american-pressure" / "rss.xml").read_text(encoding="utf-8")
    assert "2026-05-16" in index_html and "2026-05-09" in index_html
    assert "2026-05-17" not in index_html
    assert "2026-05-17" not in archive_html
    assert "2026-05-17" not in rss_xml


def test_daily_candidates_feed_weekly_edition(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    candidate = _record(
        "labor-story-daily",
        "labor_income_pressure",
        "Employer announces layoffs",
        "Local employer announced layoffs this week.",
        "https://example.com/daily-layoff",
        source_type="news_report",
        source_role="human_story",
    )
    _write_manual_sources(work_root, "2026-05-12", manual)
    _write_daily_candidates(work_root, "2026-05-10", [candidate])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="both")
    assert result["ok"] is True
    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-12" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert manifest["human_story_count_by_pillar"]["labor_income_pressure"] >= 1


def test_required_coverage_diagnostics_are_produced(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-12" / "edition_manifest.json").read_text(encoding="utf-8"))
    for key in (
        "searched_pillars",
        "current_development_count_by_pillar",
        "human_story_count_by_pillar",
        "missing_required_current_development_pillars",
        "story_plus_data_count",
        "baseline_only_count",
    ):
        assert key in manifest


def test_missing_housing_and_debt_current_developments_create_warning(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    joined = " | ".join(result["warnings"])
    assert "missing important current-development pillars" in joined
    assert "housing_household_cost_pressure" in joined
    assert "financial_distress_pressure" in joined
    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-12" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert "collection_gap_pillars" in manifest
    assert "housing_household_cost_pressure" in manifest["collection_gap_pillars"]
    assert "financial_distress_pressure" in manifest["collection_gap_pillars"]


def test_key_stat_renders_only_when_sourced(work_root):
    stat_story = _record(
        "labor-story",
        "labor_income_pressure",
        "District announces layoffs",
        "District announced layoffs this week.",
        "https://example.com/labor",
        source_type="news_report",
        source_role="human_story",
    )
    stat_story["key_stat_label"] = "Reported layoffs"
    stat_story["key_stat_value"] = "503"
    stat_story["key_stat_unit"] = "workers"
    stat_story["key_stat_context"] = "district budget crisis"
    stat_story["key_stat_source_id"] = "ap-labor-story"
    stat_story["source_id"] = "ap-labor-story"
    _write_manual_sources(work_root, "2026-05-12", [stat_story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Key number:" in html
    assert "503 workers" in html


def test_unsourced_key_stat_is_suppressed(work_root):
    stat_story = _record(
        "labor-story",
        "labor_income_pressure",
        "District announces layoffs",
        "District announced layoffs this week.",
        "https://example.com/labor",
        source_type="news_report",
        source_role="human_story",
    )
    stat_story["key_stat_label"] = "Reported layoffs"
    stat_story["key_stat_value"] = "9999"
    stat_story["key_stat_source_id"] = ""
    _write_manual_sources(work_root, "2026-05-12", [stat_story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Key number:" not in html


def test_one_stat_per_brief_behavior(work_root):
    stat_story = _record(
        "labor-story",
        "labor_income_pressure",
        "District announces layoffs",
        "District announced layoffs this week.",
        "https://example.com/labor",
        source_type="news_report",
        source_role="human_story",
    )
    stat_story["key_stat_label"] = "Reported layoffs"
    stat_story["key_stat_value"] = "503"
    stat_story["key_stat_source_id"] = "ap-labor-story"
    stat_story["source_id"] = "ap-labor-story"
    data_anchor = _record("bls", "labor_income_pressure", "BLS Employment Situation", "Official baseline indicator source.", "https://example.com/bls", source_role="data_anchor")
    data_anchor["key_stat_label"] = "Unemployment rate"
    data_anchor["key_stat_value"] = "4.1"
    data_anchor["key_stat_unit"] = "%"
    data_anchor["key_stat_source_id"] = "bls"
    _write_manual_sources(work_root, "2026-05-12", [stat_story, data_anchor])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert html.count("Key number:") == 1


def test_capradio_503_layoff_stat_renders_when_present_in_source_text(work_root):
    cap = _record(
        "labor-story-capradio",
        "labor_income_pressure",
        "Amidst district budget crisis, 503 employees laid off and receivership looms",
        "District approved layoffs amid cash flow concerns.",
        "https://example.com/capradio",
        source_type="news_report",
        source_role="human_story",
    )
    _write_manual_sources(work_root, "2026-05-12", [cap])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Key number:" in html
    assert "503 workers" in html


def test_location_specific_prose_uses_centerville_iowa(work_root):
    clinic = _record(
        "clinic",
        "health_access_pressure",
        "River Hills Community Health Center announces clinic closure",
        "A community health center announced closure of a rural clinic.",
        "https://example.com/clinic",
        source_type="news_report",
        source_role="human_story",
    )
    clinic["human_story_summary"] = "A community health center announced closure of a rural clinic."
    clinic["location"] = "Centerville, Iowa"
    _write_manual_sources(work_root, "2026-05-12", [clinic])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "In Centerville, Iowa," in html


def test_location_specific_prose_uses_sacramento_california(work_root):
    layoffs = _record(
        "layoff",
        "labor_income_pressure",
        "District approves layoffs affecting 503 employees",
        "District approved layoffs affecting 503 employees.",
        "https://example.com/layoff",
        source_type="news_report",
        source_role="human_story",
    )
    layoffs["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-12", [layoffs])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "In Sacramento, California," in html


def test_location_specific_prose_uses_san_luis_obispo_county(work_root):
    food = _record(
        "food",
        "food_pressure",
        "SLO Food Bank reports rising demand",
        "The SLO Food Bank reported rising demand.",
        "https://example.com/food",
        source_type="news_report",
        source_role="human_story",
    )
    food["location"] = "San Luis Obispo County, California"
    _write_manual_sources(work_root, "2026-05-12", [food])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "In San Luis Obispo County" in html
    assert "California, a California food bank" not in html


def test_location_fallback_uses_region_scope_when_location_missing(work_root):
    story = _record(
        "wi-storm",
        "local_system_strain",
        "Damage assessments begin after storms",
        "Officials began damage assessments after storms and flooding.",
        "https://example.com/wi",
        source_type="news_report",
        source_role="human_story",
    )
    story.pop("location", None)
    story.pop("location_scope", None)
    story["region_scope"] = "US-WI"
    _write_manual_sources(work_root, "2026-05-12", [story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "In US-WI," in html


def test_location_fallback_avoids_in_us_intro(work_root):
    story = _record(
        "us-story",
        "food_pressure",
        "Food bank demand rises",
        "A local food bank reported rising demand this week.",
        "https://example.com/us-story",
        source_type="news_report",
        source_role="human_story",
    )
    story["region_scope"] = "US"
    story.pop("location", None)
    _write_manual_sources(work_root, "2026-05-12", [story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "In US," not in html
    assert "Nationally," in html


def test_wisconsin_multiple_counties_phrase_is_normalized(work_root):
    story = _record(
        "wi-storm",
        "local_system_strain",
        "State and federal teams begin assessments after storms",
        "Across multiple Wisconsin counties, state and federal teams began damage assessments after storms and flooding.",
        "https://example.com/wi",
        source_type="news_report",
        source_role="human_story",
    )
    story["location"] = "Multiple counties in Wisconsin"
    _write_manual_sources(work_root, "2026-05-12", [story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Across multiple Wisconsin counties" in html
    assert "In Multiple counties in Wisconsin, Wisconsin" not in html
    assert "State and federal teams wisconsin officials" not in html
    assert "wisconsin officials" not in html
    assert "state officials Across multiple Wisconsin counties" not in html
    assert "This may affect Residents" not in html
    assert "communities.." not in html
    assert "Across multiple Wisconsin counties, state and federal teams began damage assessments" in html


def test_wisconsin_preferred_phrase_from_fixture_is_clean(work_root):
    repo = Path(__file__).resolve().parents[1]
    fixture_path = repo / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-12" / "manual_sources.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    records = payload.get("sources") if isinstance(payload, dict) else payload
    _write_manual_sources(work_root, "2026-05-12", records)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="both")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Across multiple Wisconsin counties, state and federal teams" in html
    assert "State and federal teams wisconsin officials" not in html
    assert html.count("state and federal teams") == 1
    assert "state officials Across multiple Wisconsin counties" not in html
    assert "This may affect Residents" not in html
    assert "communities.." not in html


def test_collection_gap_wording_does_not_claim_no_relevant_news(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "No current-development source was captured for this pillar." in html
    assert "no relevant news" not in html.lower()


def test_public_output_hides_internal_labels_and_uses_plain_summary(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand story", "Pantry demand rose this week.", "https://example.com/story", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["ap-2026-05-12-snap"]),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "This week’s sources point to pressure around groceries, debt, housing costs, health coverage, jobs, and local disruptions." in html
    for disallowed in ("current_week_development", "baseline_gauge", "story_plus_data", "source_role", "item_type", "Type:", "Brief quality:"):
        assert disallowed not in html


def test_story_plus_data_renders_current_development_before_data_context(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand story", "Pantry demand rose this week.", "https://example.com/story", source_type="news_report", source_role="human_story", linked_data_anchor_ids=["ap-2026-05-12-snap"]),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    current_idx = html.index("<strong>Current Development:</strong>")
    data_idx = html.index("<strong>Data Context:</strong>")
    assert current_idx < data_idx


def test_public_prose_rejects_raw_html_tokens(work_root):
    manual = [
        _record(
            "food-story",
            "food_pressure",
            "Food story",
            'In US, <a href="https://news.google.com/rss/articles/abc">messy</a> <font color="#6f6f6f">markup</font>.',
            "https://example.com/story",
            source_type="news_report",
            source_role="human_story",
        ),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "<font" not in html
    assert "news.google.com/rss/articles" not in html


def test_public_prose_sanitizes_internal_rationale_and_incomplete_modal_clause(work_root):
    story = _record(
        "idaho-rural-hospitals",
        "health_access_pressure",
        "Idaho rural hospitals seek relief",
        "Idaho’s rural hospital leaders are facing mounting financial pressures.",
        "https://example.com/idaho-rural-hospitals",
        source_type="news_report",
        source_role="human_story",
    )
    story["summary_or_snippet"] = (
        "Idaho’s rural hospital leaders are facing mounting financial pressures. "
        "In three states, Democratic lawmakers introduced bills this session that would allow. "
        "It is included because the source metadata ties it to housing in Idaho."
    )
    _write_manual_sources(work_root, "2026-05-12", [story])
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-12",
        publish=False,
        dry_run=False,
        from_manual_sources=False,
        source_mode="manual",
    )
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "It is included because the source metadata ties it to housing in Idaho." not in html
    assert "In three states, Democratic lawmakers introduced bills this session that would allow." not in html


def test_reader_headline_fallback_not_raw_rss_title(work_root):
    story = _record(
        "food-story",
        "food_pressure",
        "St. Johns Food Share Sees Community Demand Surge As Donations To Portland Food Bank Drop (IjaOQJ3uR) - Fathom Journal",
        "Food support demand is rising in the Portland area.",
        "https://example.com/story",
        source_type="news_report",
        source_role="human_story",
    )
    story["reader_headline"] = story["title"]
    _write_manual_sources(work_root, "2026-05-12", [story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    headline = curation["stories"][0]["title"]
    assert "IjaOQJ3uR" not in headline
    assert " - Fathom Journal" not in headline
    assert headline != story["title"]


def test_source_links_remain_visible_with_valid_urls(work_root):
    story = _record(
        "food-story",
        "food_pressure",
        "Pantry demand rises",
        "Pantry demand rose this week.",
        "https://example.com/story",
        source_type="news_report",
        source_role="human_story",
    )
    _write_manual_sources(work_root, "2026-05-12", [story])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert 'href="https://example.com/story"' in html


def test_real_life_story_sources_capped_at_three(work_root):
    records = []
    for i in range(5):
        row = _record(
            f"food-story-{i}",
            "food_pressure",
            f"Food story {i}",
            "Food demand increased for households in California.",
            f"https://example.com/food-{i}",
            source_type="news_report",
            source_role="human_story",
        )
        row["location"] = "Sacramento, California"
        records.append(row)
    _write_manual_sources(work_root, "2026-05-12", records)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    story = curation["stories"][0]
    assert len(story["human_story_source_ids"]) <= 3


def test_baseline_only_note_appears_once_when_present(work_root):
    manual = [
        _record("cpi", "housing_household_cost_pressure", "BLS CPI Shelter Index", "Official baseline indicator source.", "https://example.com/cpi", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    note = "Some items are baseline data points. They help track pressure, but they do not by themselves prove what changed this week."
    assert html.count(note) == 1


def test_map_latest_page_renders_and_links(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True

    map_html = (work_root / "output" / "site" / "american-pressure" / "map" / "index.html").read_text(encoding="utf-8")
    assert "American Pressure Map" in map_html
    assert "May 10" in map_html and "May 16, 2026" in map_html
    assert 'href="/american-pressure/"' in map_html
    assert "map_data.json" in map_html
    assert "Source:" in map_html
    assert "ap-map-popup-title" in map_html
    assert "ap-map-popup-label" in map_html
    assert "bindTooltip" in map_html
    assert "Circle = Local report" in map_html


def test_publish_index_links_to_map_and_dashboard_when_present(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    index = (work_root / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    assert 'href="map/"' in index
    assert 'href="dashboard/"' in index


def test_dashboard_page_contains_reference_sections_and_links(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    coverage_summary_path = work_root / "output" / "site" / "american-pressure" / "source_coverage_summary.json"
    feed_health_path = work_root / "output" / "site" / "american-pressure" / "source_feed_health.json"
    coverage_summary_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_summary_path.write_text(
        json.dumps(
            {
                "total_sources": 306,
                "states_covered": ["AL", "AK", "AZ"],
                "states_lacking_rural_coverage": ["DC"],
                "states_lacking_urban_coverage": [],
                "weakly_covered_states": ["DC"],
                "weakly_covered_pillars": ["transportation_daily_access_pressure"],
            }
        ),
        encoding="utf-8",
    )
    feed_health_path.write_text(
        json.dumps(
            {
                "total_ingest_ready_sources": 0,
                "known_feed_url_count": 22,
                "live_validated_feed_count": 0,
                "pending_validation_count": 22,
                "failed_validation_count": 0,
                "identified_but_failed_or_pending_count": 22,
                "rss_capable_count": 9,
                "atom_count": 2,
                "json_feed_count": 1,
                "manual_only_count": 280,
                "ingest_ready_by_state": {},
            }
        ),
        encoding="utf-8",
    )
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True

    dashboard = (work_root / "output" / "site" / "american-pressure" / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "American Pressure Dashboard" in dashboard
    assert 'class="apd-support-metrics"' in dashboard
    assert 'class="apd-grid"' in dashboard
    assert 'class="apd-detail"' in dashboard
    assert 'class="apd-map-frame"' in dashboard
    assert '<iframe title="American Pressure national map" src="/american-pressure/map/"' in dashboard
    assert 'href="/american-pressure/map/"' in dashboard
    assert 'href="/american-pressure/editions/2026-05-16/"' in dashboard
    assert 'href="/american-pressure/editions/2026-05-16/sources_manifest.json"' in dashboard
    assert "What changed this week" in dashboard
    assert "Map" in dashboard
    assert "Top visible pressure areas" in dashboard
    assert "What this means for daily life" in dashboard
    assert "Where our view is limited" in dashboard
    assert "Sources and methods" in dashboard
    assert "States with collected reporting" in dashboard
    assert "Areas with weaker visibility" in dashboard
    assert "Validated automated feeds:" in dashboard
    assert "Manual-only sources:" in dashboard
    assert "Places shown on the map" in dashboard
    assert "States with collected reports" in dashboard
    assert "Source-backed reports" in dashboard
    assert "Areas we may be missing" in dashboard
    assert "Start here: the map shows where we found source-backed signs of household or community strain." in dashboard
    assert "Map layer coming after location quality improves." not in dashboard
    assert dashboard.index("What changed this week") < dashboard.index('class="apd-map-frame"')
    assert dashboard.index('class="apd-map-frame"') < dashboard.index("Sources and methods")
    assert dashboard.index('class="apd-map-frame"') < dashboard.index('class="apd-support-metrics"')
    for banned in (
        "ingest-ready",
        "semantic dedupe",
        "signal layer",
        "validation topology",
        "state_context",
        "raw ingestion",
        "source topology",
    ):
        assert banned not in dashboard.lower()
    assert "/source_coverage_summary.json" in dashboard
    assert "/source_feed_health.json" in dashboard
    assert "/sources_manifest.json" in dashboard
    assert "/output/paid" not in dashboard.lower()
    assert "/output/detail" not in dashboard.lower()


def test_map_page_is_generated_and_links_back(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Pantry demand rose this week.", "https://example.com/story", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["latitude"] = 38.5816
    manual[0]["longitude"] = -121.4944
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    map_html = (work_root / "output" / "site" / "american-pressure" / "map" / "index.html").read_text(encoding="utf-8")
    assert "American Pressure Map" in map_html
    assert 'href="/american-pressure/"' in map_html
    assert 'href="/">' in map_html
    assert "map_data.json" in map_html
    assert "Source:" in map_html
    assert "No written section yet" in map_html


def test_map_data_city_state_lookup_maps_without_explicit_coordinates(work_root):
    manual = [
        _record("city-mapped", "food_pressure", "City mapped record", "City summary.", "https://example.com/city", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    manual[0]["location_scope"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    pin = next((p for p in payload["pins"] if "https://example.com/city" in (p.get("source_urls") or [])), None)
    assert pin is not None
    assert pin["location_precision"] == "city_state"


def test_map_data_state_level_fallback_is_labeled(work_root):
    manual = [
        _record("state-mapped", "housing_household_cost_pressure", "State mapped record", "State summary.", "https://example.com/state", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Colorado"
    manual[0]["location_scope"] = "Colorado"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    pin = next((p for p in payload["pins"] if "https://example.com/state" in (p.get("source_urls") or [])), None)
    assert pin is not None
    assert pin["location_precision"] == "state_level"
    assert pin["location_precision_warning"] == "state-level location, not exact address."


def test_map_data_county_state_fallback_is_labeled(work_root):
    manual = [
        _record("county-mapped", "environmental_pressure", "County mapped record", "County summary.", "https://example.com/county", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["county"] = "Osceola County"
    manual[0]["state"] = "FL"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    pin = next((p for p in payload["pins"] if "https://example.com/county" in (p.get("source_urls") or [])), None)
    assert pin is not None
    assert pin["location_precision"] == "county_state"
    assert pin["location_precision_warning"] == "county-level location, not exact address."


def test_map_data_pins_require_traceable_source_url_and_valid_coordinates(work_root):
    manual = [
        _record("mapped", "food_pressure", "Mapped record", "Mapped summary.", "https://example.com/mapped", source_type="news_report", source_role="human_story"),
        _record("unlocated", "health_access_pressure", "No coords record", "No coords summary.", "https://example.com/unlocated", source_type="news_report", source_role="human_story"),
        _record("bad-coords", "labor_income_pressure", "Bad coords record", "Bad coords summary.", "https://example.com/bad", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["latitude"] = 38.5816
    manual[0]["longitude"] = -121.4944
    manual[0]["location"] = "Sacramento, California"
    manual[2]["latitude"] = 200.0
    manual[2]["longitude"] = 400.0
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    mapped_pin = next((p for p in payload["pins"] if "https://example.com/mapped" in (p.get("source_urls") or [])), None)
    assert mapped_pin is not None
    assert mapped_pin["title"] == "Mapped record"
    assert mapped_pin["source_record_ids"][0]
    assert any(row["title"] == "Bad coords record" for row in payload["unmapped_records"])
    assert any(row["title"] == "No coords record" for row in payload["unmapped_records"] + payload["national_records"])
    assert any(row.get("unmapped_reason") for row in payload["unmapped_records"])


def test_map_data_has_national_and_unmapped_sections(work_root):
    manual = [
        _record("national", "food_pressure", "National baseline", "United States food data update.", "https://example.com/national", source_type="official_source", source_role="data_anchor"),
        _record("ambiguous", "labor_income_pressure", "Washington workers face strain", "Workers affected in Washington.", "https://example.com/ambiguous", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["region_scope"] = "US"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert isinstance(payload.get("national_records"), list)
    assert isinstance(payload.get("unmapped_records"), list)
    assert payload["national_records_count"] >= 1
    assert all(pin.get("location_precision") in {"exact_or_source_provided", "city_state", "county_state", "service_area", "state_level"} for pin in payload["pins"])


def test_map_data_geography_us_alone_does_not_force_national_scope(work_root):
    manual = [
        _record("city-mapped", "food_pressure", "City mapped record", "City summary.", "https://example.com/city", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["geography"] = "US"
    manual[0]["location"] = "Sacramento, California"
    manual[0]["location_scope"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    pin = next((p for p in payload["pins"] if "https://example.com/city" in (p.get("source_urls") or [])), None)
    assert pin is not None
    assert not any(row for row in payload["national_records"] if "https://example.com/city" in (row.get("source_urls") or []))


def test_map_data_ambiguous_washington_is_not_pinned(work_root):
    manual = [
        _record("ambiguous", "labor_income_pressure", "Washington workers face strain", "Workers affected in Washington.", "https://example.com/ambiguous", source_type="news_report", source_role="human_story"),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert not any(pin for pin in payload["pins"] if "https://example.com/ambiguous" in (pin.get("source_urls") or []))
    assert any(row for row in (payload["national_records"] + payload["unmapped_records"]) if "https://example.com/ambiguous" in (row.get("source_urls") or []))


def test_map_data_extracts_source_backed_place_mentions_and_writes_diagnostics(work_root):
    manual = [
        _record("food", "food_pressure", "Food pressure", "Impacts San Luis Obispo County, California households.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("financial", "financial_distress_pressure", "Hospital strain", "Fitzgibbon Hospital in Marshall, Missouri filed Chapter 11.", "https://example.com/fin", source_type="news_report", source_role="human_story"),
        _record("health", "health_access_pressure", "Clinic closure", "River Hills announced closure in Centerville, Iowa.", "https://example.com/health", source_type="news_report", source_role="human_story"),
        _record("systems", "local_system_strain", "Storm assessments", "Assessments across multiple counties in Wisconsin.", "https://example.com/systems", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "San Luis Obispo County, California"
    manual[1]["location"] = "Marshall, Missouri"
    manual[2]["location"] = "Centerville, Iowa"
    manual[3]["location"] = "Multiple counties in Wisconsin"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True

    map_path = work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json"
    diag_path = work_root / "output" / "site" / "american-pressure" / "map" / "location_extraction_diagnostics.json"
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diag_path.read_text(encoding="utf-8"))

    assert payload["pin_count"] >= 3
    assert diagnostics["accepted_candidates"]
    assert isinstance(diagnostics["rejected_candidates"], list)


def test_map_pin_records_include_traceability_fields(work_root):
    manual = [
        _record("city-mapped", "food_pressure", "City mapped", "In Sacramento, California demand is up.", "https://example.com/city", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    pin = next((p for p in payload["pins"] if "https://example.com/city" in (p.get("source_urls") or [])), None)
    assert pin is not None
    assert pin["source_url"].startswith("https://")
    assert pin["source_record_id"]
    assert "story_id" in pin
    assert pin["edition_url"].startswith("/american-pressure/editions/")
    assert pin["location_label"]
    assert pin["location_role"] in {"primary_location", "affected_location", "service_area", "state_context"}
    assert pin["location_extraction_method"]
    assert pin["extraction_method"]
    assert pin["extraction_evidence_text"]
    assert pin["evidence_text"]
    assert pin["evidence_field"]
    assert pin["confidence"]
    assert isinstance(pin["is_exact_location"], bool)
    assert pin["location_precision"] in {"exact_or_source_provided", "city_state", "county_state", "service_area", "state_level"}


def test_map_repeated_locations_are_aggregated(work_root):
    manual = [
        _record("layoff-1", "labor_income_pressure", "District layoffs", "In Sacramento, California layoffs were announced.", "https://example.com/layoff-1", source_type="news_report", source_role="human_story"),
        _record("layoff-2", "labor_income_pressure", "District layoffs continue", "Sacramento, California faces additional cuts.", "https://example.com/layoff-2", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    manual[1]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    grouped = [row for row in payload.get("aggregated_pins", []) if row.get("location_label") == "Sacramento, CA"]
    assert grouped
    assert grouped[0]["record_count"] >= 2
    assert grouped[0]["raw_record_count"] >= grouped[0]["record_count"]


def test_map_can_include_records_outside_written_weekly_selection(work_root):
    prior_week = [
        _record("older-map-only", "food_pressure", "Older local signal", "In Sacramento, California pantry demand rose.", "https://example.com/older", source_type="news_report", source_role="human_story"),
    ]
    prior_week[0]["published_at"] = "2026-05-01T00:00:00Z"
    prior_week[0]["location"] = "Sacramento, California"
    current_week = [
        _record("current-story", "food_pressure", "Current weekly signal", "In Marshall, Missouri pressure rose.", "https://example.com/current", source_type="news_report", source_role="human_story"),
    ]
    current_week[0]["published_at"] = "2026-05-16T00:00:00Z"
    current_week[0]["location"] = "Marshall, Missouri"
    _write_manual_sources(work_root, "2026-05-09", prior_week)
    _write_manual_sources(work_root, "2026-05-16", current_week)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    older_pin = next((p for p in payload["pins"] if "https://example.com/older" in (p.get("source_urls") or [])), None)
    current_pin = next((p for p in payload["pins"] if "https://example.com/current" in (p.get("source_urls") or [])), None)
    assert older_pin is not None
    assert current_pin is not None
    assert older_pin["time_window"] in {"last_30_days", "last_60_days"}
    assert current_pin["time_window"] == "current_week"


def test_deduped_written_signal_still_contributes_to_map_counts(work_root):
    manual = [
        _record("food-1", "food_pressure", "Same headline duplicate", "In Sacramento, California pantry strain increased.", "https://example.com/a", source_type="news_report", source_role="human_story"),
        _record("food-2", "food_pressure", "Same headline duplicate", "In Sacramento, California pantry strain increased again.", "https://example.com/b", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    manual[1]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True

    edition_root = work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-16"
    sources_manifest = json.loads((edition_root / "sources_manifest.json").read_text(encoding="utf-8"))
    map_source_records = json.loads((edition_root / "map_source_records.json").read_text(encoding="utf-8"))
    map_payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))

    assert len(sources_manifest) == 1
    assert len(map_source_records) == 2
    grouped = [row for row in map_payload.get("aggregated_pins", []) if row.get("location_label") == "Sacramento, CA"]
    assert grouped
    assert grouped[0]["record_count"] == 2
    assert map_payload["raw_record_count"] >= map_payload["mapped_records_count"]


def test_map_page_has_aggregated_and_individual_view_toggle(work_root):
    manual = [
        _record("mapped", "food_pressure", "Mapped", "In Sacramento, California households face strain.", "https://example.com/m", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    map_html = (work_root / "output" / "site" / "american-pressure" / "map" / "index.html").read_text(encoding="utf-8")
    assert "Show grouped places" in map_html
    assert "Show individual reports" in map_html
    assert "Current week/current edition" in map_html
    assert '<option value="last_30_days" selected>Last 30 days</option>' in map_html
    assert "Source-backed reports:" in map_html
    assert "Reset map" in map_html
    assert "About this map" in map_html


def test_map_semantic_dedupe_collapses_same_source_url_location_category(work_root):
    manual = [
        _record("dup-1", "labor_income_pressure", "Layoffs in Sacramento", "In Sacramento, California layoffs were announced.", "https://example.com/same", source_type="news_report", source_role="human_story"),
        _record("dup-2", "labor_income_pressure", "Layoffs in Sacramento updated", "In Sacramento, California layoffs were announced.", "https://example.com/same", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    manual[1]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    diag = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "location_extraction_diagnostics.json").read_text(encoding="utf-8"))
    same = [
        p
        for p in payload["pins"]
        if "https://example.com/same" in (p.get("source_urls") or []) and p.get("location_role") == "primary_location"
    ]
    assert len(same) == 1
    assert same[0]["raw_record_count"] == 2
    assert same[0]["duplicate_count"] == 1
    assert len(same[0].get("duplicate_record_ids") or []) == 1
    assert payload["raw_record_count"] >= payload["mapped_records_count"]
    assert diag.get("duplicates_collapsed_count", 0) >= 1
    assert diag.get("duplicate_groups")


def test_local_city_suppresses_state_fallback_for_same_source(work_root):
    manual = [
        _record(
            "city-over-state",
            "labor_income_pressure",
            "Sacramento layoffs",
            "In Sacramento, California layoffs were announced, with additional mentions of California in state filings.",
            "https://example.com/sac-over-state",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    diag = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "location_extraction_diagnostics.json").read_text(encoding="utf-8"))
    assert any(pin.get("location_label") == "Sacramento, CA" for pin in payload["pins"])
    assert not any(pin.get("location_role") == "state_context" and pin.get("source_url") == "https://example.com/sac-over-state" for pin in payload["pins"])
    assert diag.get("state_fallback_suppressed_records")


def test_malformed_facility_city_candidate_is_cleaned_or_rejected(work_root):
    manual = [
        _record(
            "facility-city",
            "financial_distress_pressure",
            "Hospital filing",
            "Fitzgibbon Hospital in Marshall, Missouri filed Chapter 11 bankruptcy.",
            "https://example.com/marshall",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert any(pin.get("location_label") == "Marshall, MO" for pin in payload["pins"])
    assert not any("fitzgibbon hospital in marshall" in str(pin.get("location_label") or "").lower() for pin in payload["pins"])


def test_map_one_story_can_produce_multiple_signals(work_root):
    manual = [
        _record(
            "systems",
            "local_system_strain",
            "Wisconsin recovery update",
            "Flood impacts in Vernon County, Wisconsin and Crawford County, Wisconsin with support from the Ho-Chunk Nation.",
            "https://example.com/systems",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    manual[0]["location"] = "Vernon County, Wisconsin"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert payload["pin_count"] >= 2
    assert any(pin.get("location_role") == "primary_location" for pin in payload["pins"])
    assert any(pin.get("location_role") in {"affected_location", "service_area"} for pin in payload["pins"])


def test_affected_locations_require_explicit_evidence_text(work_root):
    manual = [
        _record(
            "ambiguous-affected",
            "labor_income_pressure",
            "Payroll concerns",
            "Sacramento, California.",
            "https://example.com/ambiguous-affected",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    diag = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "location_extraction_diagnostics.json").read_text(encoding="utf-8"))
    assert any(row.get("rejection_reason") == "no_explicit_pressure_link" for row in diag.get("rejected_candidates", []))


def test_service_area_locations_require_explicit_evidence_and_lookup(work_root):
    manual = [
        _record(
            "district-lookup",
            "labor_income_pressure",
            "District pressure",
            "Sacramento City Unified School District faced budget and layoff pressure.",
            "https://example.com/district",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert any(pin.get("location_role") == "service_area" for pin in payload.get("pins", []))
    assert any(pin.get("location_precision") == "service_area" for pin in payload.get("pins", []))


def test_diagnostics_include_summary_counts(work_root):
    manual = [
        _record("city-mapped", "food_pressure", "City mapped", "In Sacramento, California demand is up.", "https://example.com/city", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    diag = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "location_extraction_diagnostics.json").read_text(encoding="utf-8"))
    for key in (
        "candidates_seen",
        "raw_extracted_candidate_count",
        "accepted_count",
        "accepted_before_dedupe_count",
        "accepted_after_dedupe_count",
        "duplicates_collapsed_count",
        "rejected_count",
        "primary_count",
        "affected_count",
        "service_area_count",
        "state_context_count",
    ):
        assert key in diag
    assert isinstance(diag.get("accepted_records"), list)
    assert isinstance(diag.get("rejected_records"), list)


def test_aggregated_map_records_include_written_story_links_when_available(work_root):
    manual = [
        _record("mapped", "labor_income_pressure", "Layoffs reported", "In Sacramento, California layoffs were reported.", "https://example.com/layoff", source_type="news_report", source_role="human_story"),
    ]
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    grouped = [row for row in payload.get("aggregated_pins", []) if row.get("location_label") == "Sacramento, CA"]
    assert grouped
    assert grouped[0]["written_story_links"]


def test_map_uses_latest_listable_public_edition(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-09", manual)
    _write_manual_sources(work_root, "2026-05-16", manual)
    first = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-09", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    second = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert first["ok"] is True
    assert second["ok"] is True
    map_payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert map_payload["edition_date"] == "2026-05-16"


def test_publish_excludes_invalid_later_edition_without_weekly_manifest_fields(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    invalid_dir = work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-18"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    (invalid_dir / "index.html").write_text("<html>invalid future</html>", encoding="utf-8")
    (invalid_dir / "sources_manifest.json").write_text(json.dumps([{"url": "https://example.com/future"}]), encoding="utf-8")
    (invalid_dir / "curation_manifest.json").write_text(json.dumps({"stories": [{"story_id": "s1"}]}), encoding="utf-8")
    (invalid_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "american-pressure",
                "edition_date": "2026-05-18",
                "source_count": 1,
                "story_count": 1,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    index = (work_root / "output" / "site" / "american-pressure" / "index.html").read_text(encoding="utf-8")
    archive = (work_root / "output" / "site" / "american-pressure" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-18" not in index
    assert "2026-05-18" not in archive


def test_public_source_list_is_concise_and_excludes_generic_registry_watchlist(work_root):
    manual = [
        _record(
            "local-system-story",
            "local_system_strain",
            "Iowa disaster proclamation issued",
            "Iowa activated disaster programs after severe weather impacts.",
            "https://example.com/iowa-disaster",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    manual[0]["public_pressure_angle"] = "Local systems and households are under recovery strain."
    _write_manual_sources(work_root, "2026-05-23", manual)
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-23",
        publish=False,
        dry_run=False,
        from_manual_sources=False,
        source_mode="both",
    )
    assert result["ok"] is True
    md = (work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23" / "edition.md").read_text(encoding="utf-8")
    assert "NPR Network (Alabama)" not in md
    assert "Nonprofit Policy Reporting (Alabama)" not in md
    parts = md.split("\n## ")
    first_block = parts[1] if len(parts) > 1 else md
    if len(parts) > 2:
        first_block = first_block.split("\n## ", 1)[0]
    source_lines = [line for line in first_block.splitlines() if line.startswith("Source: ")]
    assert len(source_lines) <= 5


def test_baseline_only_items_are_labeled_in_public_outputs(work_root):
    manual = [
        _record(
            "uscourts-anchor",
            "financial_distress_pressure",
            "U.S. Courts Bankruptcy Filings Statistics",
            "Official baseline indicator source.",
            "https://www.uscourts.gov/statistics-reports/analysis-reports/bankruptcy-filings-statistics",
            source_role="data_anchor",
        ),
    ]
    _write_manual_sources(work_root, "2026-05-23", manual)
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-23",
        publish=False,
        dry_run=False,
        from_manual_sources=False,
        source_mode="manual",
    )
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-23" / "index.html").read_text(encoding="utf-8")
    md = (work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-23" / "edition.md").read_text(encoding="utf-8")
    assert "Baseline/context item:" in html
    assert "Baseline/context item:" in md


def test_local_system_story_uses_local_system_headline_not_housing_headline(work_root):
    manual = [
        _record(
            "iowa-disaster",
            "local_system_strain",
            "Iowa severe weather response",
            "Iowa activated housing and recovery support after severe weather disruptions.",
            "https://example.com/iowa-response",
            source_type="news_report",
            source_role="human_story",
        ),
    ]
    manual[0]["public_pressure_angle"] = "Local service response and household recovery pressure."
    _write_manual_sources(work_root, "2026-05-23", manual)
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-23",
        publish=False,
        dry_run=False,
        from_manual_sources=False,
        source_mode="manual",
    )
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-23" / "curation_manifest.json").read_text(encoding="utf-8"))
    story = next(item for item in curation["stories"] if item.get("pillar") == "local_system_strain")
    assert "Housing costs are still the budget pressure that can crowd out everything else" not in story["title"]
    assert "Local systems" in story["title"]


def test_public_source_display_is_capped_while_full_ledger_is_preserved(work_root):
    manual = [
        _record(
            "food-story",
            "food_pressure",
            "Food pressure story",
            "Food bank demand rose this week.",
            "https://example.com/food-story",
            source_type="news_report",
            source_role="human_story",
            linked_data_anchor_ids=["usda-fns-snap-data-tables"],
        ),
    ]
    manual[0]["public_pressure_angle"] = "Food affordability stress is rising for households."
    _write_manual_sources(work_root, "2026-05-23", manual)
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-23",
        publish=False,
        dry_run=False,
        from_manual_sources=False,
        source_mode="both",
    )
    assert result["ok"] is True
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-23" / "curation_manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-23" / "sources_manifest.json").read_text(encoding="utf-8"))
    story = next(item for item in curation["stories"] if item.get("pillar") == "food_pressure")
    assert len(story.get("public_source_record_ids", [])) <= 5
    assert len(sources) > len(story.get("public_source_record_ids", []))


def test_map_includes_feed_backfill_records_but_written_dispatch_remains_curated(work_root):
    manual = [
        _record("manual-story", "food_pressure", "Manual pantry story", "Pantry demand rose this week.", "https://example.com/manual-story", source_type="news_report", source_role="human_story"),
    ]
    _write_manual_sources(work_root, "2026-05-16", manual)
    backfill_dir = work_root / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-16"
    backfill_dir.mkdir(parents=True, exist_ok=True)
    backfill_rows = [
        {
            "source_record_id": "feed-backfill-2026-05-16-ca-001",
            "source_id": "ca-nonprofit-policy-news",
            "title": "Food bank demand rises in Sacramento, CA",
            "url": "https://example.com/backfill-1",
            "publisher": "CA Nonprofit",
            "published_at": "2026-05-16T08:00:00Z",
            "retrieved_at": "2026-05-16T09:00:00Z",
            "summary_or_snippet": "SNAP delays and pantry strain were reported.",
            "source_type": "news_report",
            "region_scope": "statewide",
            "category_hint": "food_pressure",
            "pillar": "food_pressure",
            "reliability_tier": "reputable_reporting",
            "source_state": "feed_backfill",
            "state": "CA",
            "location": "Sacramento, CA",
            "location_precision": "city_state",
            "manual_source_role": "human_story",
            "map_collection_source": "feed_backfill",
        }
    ]
    (backfill_dir / "feed_backfill_sources.json").write_text(json.dumps(backfill_rows, indent=2), encoding="utf-8")

    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    assert result["story_count"] == 1

    map_payload = json.loads((work_root / "output" / "site" / "american-pressure" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert any("https://example.com/backfill-1" in (pin.get("source_urls") or []) for pin in map_payload.get("pins", []))


def test_reader_first_public_pages_and_banned_terms(work_root):
    manual = [
        _record("food-story", "food_pressure", "Pantry demand rises", "Food bank demand rose this week in Sacramento, California.", "https://example.com/food", source_type="news_report", source_role="human_story"),
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    manual[0]["location"] = "Sacramento, California"
    _write_manual_sources(work_root, "2026-05-16", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-16", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True

    dispatch_html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-16" / "index.html").read_text(encoding="utf-8")
    dashboard_html = (work_root / "output" / "site" / "american-pressure" / "dashboard" / "index.html").read_text(encoding="utf-8")
    map_html = (work_root / "output" / "site" / "american-pressure" / "map" / "index.html").read_text(encoding="utf-8")

    assert "View Dashboard" in dispatch_html and "View Map" in dispatch_html and "View Source Ledger" in dispatch_html
    assert "This week’s dispatch is based on source-backed reporting collected so far. It is not a complete census of American hardship." in dispatch_html
    assert "What changed this week" in dispatch_html
    assert "Where our view is limited" in dispatch_html
    assert "Food and grocery pressure" in dispatch_html
    assert "Housing and utility pressure" in dispatch_html

    assert "What changed this week" in dashboard_html
    assert "Map" in dashboard_html
    assert "Top visible pressure areas" in dashboard_html
    assert "What this means for daily life" in dashboard_html
    assert "Where our view is limited" in dashboard_html
    assert "Sources reviewed" in dashboard_html
    assert "Sources monitored automatically" in dashboard_html

    assert "Source-backed signs of household or community strain across the U.S." in map_html
    assert "About this map" in map_html
    assert "Reset map" in map_html
    assert "Places shown on the map" in map_html
    assert "States with collected reports" in map_html
    assert "Source-backed reports" in map_html
    assert "Areas we may be missing" in map_html
    assert "How this map was built" in map_html
    assert '<option value="last_30_days" selected>Last 30 days</option>' in map_html
    assert "Show grouped places" in map_html
    assert "Show individual reports" in map_html
    assert "What we found" in map_html
    assert "Why it matters" in map_html
    assert "Source:" in map_html
    assert "Read more:" in map_html
    assert ">Source</a>" not in map_html
    assert "Mapped to city level, not an exact address." in map_html
    assert "Mapped to county level." in map_html
    assert "Statewide report." in map_html
    assert "Circle = Local report" in map_html
    assert "Square = County/service-area report" in map_html
    assert "Diamond = Statewide report" in map_html
    assert "Larger marks mean more source-backed reports are grouped at that place." in map_html
    assert "<summary>Legend</summary>" in map_html
    assert "ap-map-marker-count" in map_html
    assert "ap-map-hover-tooltip" in map_html
    assert "min-height: 720px" in map_html
    assert "height: 520px" in map_html
    assert "height: 680px" in dashboard_html
    assert "height: 480px" in dashboard_html
    assert map_html.index('class="ap-map-canvas"') < map_html.index("How this map was built")

    banned = (
        "primary location signals",
        "affected location signals",
        "service-area signals",
        "source records collected for map processing",
        "mapped records",
        "ingest-ready",
        "semantic dedupe",
        "source topology",
        "validation topology",
        "state_context",
        "signal layer",
        "raw ingestion",
        "primary_location",
        "source_record_ids",
        "evidence snippets",
        "count by location type",
        "related dispatch section",
    )
    combined = (dispatch_html + dashboard_html + map_html).lower()
    for token in banned:
        assert token not in combined

