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


def _record(source_id: str, pillar: str, title: str, summary: str, url: str) -> dict:
    return {
        "source_record_id": f"ap-2026-05-12-{source_id}",
        "source_id": source_id,
        "title": title,
        "url": url,
        "publisher": "Publisher",
        "published_at": "2026-05-10T00:00:00Z",
        "retrieved_at": "2026-05-12T12:00:00Z",
        "summary_or_snippet": summary,
        "source_type": "official_dataset_page",
        "geography": "US",
        "pillar": pillar,
        "category_hint": pillar,
        "reliability_tier": "official_primary",
    }


def test_manual_mode_uses_manual_only(work_root):
    _write_manual_sources(work_root, "2026-05-12", [_record("snap", "food_pressure", "SNAP", "Food assistance pressure", "https://example.com/snap")])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    assert result["source_count"] == 1


def test_auto_mode_uses_enabled_baseline_sources(work_root):
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="auto")
    assert result["ok"] is True
    assert result["source_count"] >= 4


def test_both_mode_merges_auto_and_manual(work_root):
    _write_manual_sources(work_root, "2026-05-12", [_record("labor", "labor_income_pressure", "WARN layoffs", "Layoff pressure for workers", "https://example.com/warn")])
    auto = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="auto")
    both = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="both")
    assert both["source_count"] > auto["source_count"]


def test_future_date_refused_without_allow_future(work_root):
    future_date = (datetime.now().date() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        ap_runner.run_american_pressure_dispatch(work_root, future_date, publish=False, dry_run=False, from_manual_sources=False, source_mode="auto")


def test_investor_only_bankruptcy_rejected(work_root):
    _write_manual_sources(work_root, "2026-05-12", [_record("bk", "financial_distress_pressure", "Chapter 11 plan", "Investor presentation for bondholder recoveries", "https://example.com/investor")])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is False


def test_section_rendering_and_source_links(work_root):
    manual = [
        _record("foodbank", "food_pressure", "Food bank demand update", "Food bank demand spike this week", "https://example.com/foodbank"),
        _record("snap", "food_pressure", "SNAP", "Food pressure", "https://example.com/snap"),
        _record("bk", "financial_distress_pressure", "Court filings", "Household debt bankruptcy pressure", "https://www.uscourts.gov/statistics-reports/analysis-reports/bankruptcy-filings-statistics"),
        _record("health", "health_access_pressure", "Clinic closure", "Health access strain", "https://example.com/health"),
        _record("housing", "housing_household_cost_pressure", "Rents rise", "Housing cost pressure", "https://example.com/housing"),
        _record("labor", "labor_income_pressure", "BLS Employment Situation", "Labor income pressure", "https://example.com/labor"),
        _record("local", "local_system_strain", "Transit cuts", "Local service disruptions", "https://example.com/local"),
        _record("env", "environmental_pressure", "NOAA climate watch", "Heat and drought pressure", "https://example.com/noaa"),
        _record("fema", "environmental_pressure", "FEMA declaration update", "Disaster declaration update", "https://example.com/fema"),
        _record("drought", "environmental_pressure", "Drought monitor", "Weekly drought pressure", "https://droughtmonitor.unl.edu/"),
    ]
    _write_manual_sources(work_root, "2026-05-12", manual)
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=False, source_mode="manual")
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "This Week's Read" not in html
    assert "This Week’s Read" in html
    assert "What This Means" in html
    assert "What Feels Tight" in html
    assert "What Changed" in html
    assert "What We’re Watching Next" in html
    assert "What We Still Do Not Know" in html
    assert "Source: <a href=" in html
    assert "Food and Grocery Pressure" in html
    assert "Debt and Bankruptcy Pressure" in html
    assert "Jobs and Paychecks" in html
    assert "Weather, Drought, and Disaster Strain" in html
    assert "SNAP data helps show whether food assistance remains a major support for households under grocery pressure." in html
    assert "Bankruptcy filings are a delayed but concrete sign that households or businesses have run out of easier options." in html
    assert "<strong>Why it matters:</strong>" in html
    assert "<strong>Who may feel it:</strong>" in html
    assert "<strong>What to watch next:</strong>" in html
    food_section = html.split("Food and Grocery Pressure", 1)[1].split("Debt and Bankruptcy Pressure", 1)[0]
    assert food_section.index("Type:</strong> current_week_development") < food_section.index("Type:</strong> baseline_gauge")

    manifest = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "editions" / "2026-05-12" / "edition_manifest.json").read_text(encoding="utf-8"))
    curation = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "curation_manifest.json").read_text(encoding="utf-8"))
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_count"] == len(sources)
    assert manifest["story_count"] == len(curation["stories"])
    assert manifest["item_type_counts"]["current_week_development"] >= 1
    assert manifest["item_type_counts"]["baseline_gauge"] >= 1
    assert any(story.get("curation_reason") == "food assistance dependency" for story in curation["stories"])
    assert any(story.get("curation_reason") == "bankruptcy/financial distress baseline" for story in curation["stories"])
