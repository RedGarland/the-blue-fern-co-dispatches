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
    assert "Real-life story sources:" in html
    assert "Data/context sources:" in html
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
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
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


def test_weekly_cadence_label_remains_public(work_root):
    manual = [
        _record("snap", "food_pressure", "SNAP Household Characteristics", "Official baseline indicator source.", "https://example.com/snap", source_role="data_anchor"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Weekly briefing / 2026-05-12" in html


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
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
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
    assert "In San Luis Obispo County, California," in html


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
