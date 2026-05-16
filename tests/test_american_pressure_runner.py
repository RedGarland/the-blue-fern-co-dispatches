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
    assert "<strong>Current Development:</strong>" not in html
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
