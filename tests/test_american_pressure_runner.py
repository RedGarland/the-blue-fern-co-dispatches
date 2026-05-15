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
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def _write_manual_sources(root: Path, edition_date: str, records: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "american-pressure" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def _valid_record() -> dict:
    return {
        "source_record_id": "ap-2026-05-12-001",
        "source_id": "cms-medicaid-enrollment",
        "title": "Medicaid and CHIP Enrollment Data",
        "url": "https://www.medicaid.gov/medicaid/national-medicaid-chip-program-information/medicaid-chip-enrollment-data",
        "publisher": "Centers for Medicare and Medicaid Services",
        "published_at": "2026-05-10T00:00:00Z",
        "retrieved_at": "2026-05-12T12:00:00Z",
        "summary_or_snippet": "Enrollment figures indicate sustained household health access pressure.",
        "source_type": "official_dataset_page",
        "geography": "US",
        "pillar": "health_access_pressure",
        "reliability_tier": "official_primary",
    }


def _bankruptcy_record(title: str, summary: str, url: str, *, source_id: str = "bk-001", category_hint: str = "bankruptcy", pillar: str = "financial_distress_pressure") -> dict:
    return {
        "source_record_id": f"ap-2026-05-12-{source_id}",
        "source_id": source_id,
        "title": title,
        "url": url,
        "publisher": "Reuters",
        "published_at": "2026-05-10T00:00:00Z",
        "retrieved_at": "2026-05-12T12:00:00Z",
        "summary_or_snippet": summary,
        "source_type": "reputable_reporting",
        "geography": "US",
        "pillar": pillar,
        "category_hint": category_hint,
        "reliability_tier": "reputable_reporting",
    }


def test_runner_generates_from_valid_manual_source_file(work_root):
    _write_manual_sources(work_root, "2026-05-12", [_valid_record()])

    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True
    )

    assert result["ok"] is True
    assert result["source_count"] == 1
    assert result["generated"] is True
    edition = work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html"
    assert edition.exists()
    assert "https://www.medicaid.gov/medicaid/national-medicaid-chip-program-information/medicaid-chip-enrollment-data" in edition.read_text(encoding="utf-8")


def test_runner_refuses_missing_manual_source_file(work_root):
    with pytest.raises(FileNotFoundError) as excinfo:
        ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True)
    message = str(excinfo.value)
    expected_path = work_root / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-12" / "manual_sources.json"
    assert str(expected_path) in message
    assert "--init-manual-sources" in message


def test_init_manual_sources_creates_expected_file(work_root):
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-12",
        publish=True,
        dry_run=False,
        from_manual_sources=False,
        init_manual_sources=True,
    )
    assert result["ok"] is True
    path = work_root / "data" / "dispatches" / "american-pressure" / "sources" / "2026-05-12" / "manual_sources.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload.get("sources") == []


def test_init_manual_sources_does_not_publish(work_root):
    result = ap_runner.run_american_pressure_dispatch(
        work_root,
        "2026-05-12",
        publish=True,
        dry_run=False,
        from_manual_sources=False,
        init_manual_sources=True,
    )
    assert result["ok"] is True
    assert result["generated"] is False
    assert result["archive_updated"] is False
    assert result["rss_updated"] is False
    assert not (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").exists()


def test_future_date_refused_without_allow_future(work_root):
    future_date = (datetime.now().date() + timedelta(days=1)).isoformat()
    _write_manual_sources(work_root, future_date, [_valid_record()])
    with pytest.raises(ValueError) as excinfo:
        ap_runner.run_american_pressure_dispatch(work_root, future_date, publish=False, dry_run=False, from_manual_sources=True)
    assert "--allow-future" in str(excinfo.value)


def test_runner_refuses_zero_valid_records(work_root):
    _write_manual_sources(work_root, "2026-05-12", [])
    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True
    )
    assert result["ok"] is False
    assert "No valid source-backed American Pressure records found for 2026-05-12" in " ".join(result["errors"])


def test_past_date_runs_with_manual_source(work_root):
    _write_manual_sources(work_root, "2026-05-11", [_valid_record()])
    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-11", publish=False, dry_run=False, from_manual_sources=True
    )
    assert result["ok"] is True


def test_runner_refuses_missing_required_fields(work_root):
    bad = _valid_record()
    bad.pop("url")
    _write_manual_sources(work_root, "2026-05-12", [bad])
    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True
    )
    assert result["ok"] is False
    assert any("missing required fields" in error for error in result["errors"])


def test_runner_uses_only_manual_claims_not_registry_claims(work_root):
    _write_manual_sources(work_root, "2026-05-12", [_valid_record()])
    registry = work_root / "data" / "dispatches" / "american-pressure" / "source_registry.yml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        "sources:\n"
        "  - source_id: registry-only\n"
        "    name: Registry Title Should Not Appear\n"
        "    url: https://example.com\n"
        "    publisher: Registry Publisher\n"
        "    pillar: food_pressure\n"
        "    geography: US\n"
        "    source_type: official_report_page\n"
        "    reliability_tier: official_primary\n"
        "    update_frequency: monthly\n"
        "    enabled: true\n"
        "    notes: test\n",
        encoding="utf-8",
    )
    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True
    )
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Registry Title Should Not Appear" not in html


def test_runner_does_not_fetch_live_sources(work_root, monkeypatch):
    _write_manual_sources(work_root, "2026-05-12", [_valid_record()])
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network fetch not allowed")))
    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=True
    )
    assert result["ok"] is True


def test_us_courts_official_bankruptcy_data_is_accepted(work_root):
    record = _bankruptcy_record(
        "Bankruptcy Filings Statistics",
        "U.S. Courts quarterly bankruptcy filings show chapter and business-vs-nonbusiness trends by district.",
        "https://www.uscourts.gov/statistics-reports/analysis-reports/bankruptcy-filings-statistics",
        source_id="uscourts-001",
        category_hint="official filings data",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True)
    assert result["ok"] is True
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert sources[0]["pillar"] == "financial_distress_pressure"
    assert sources[0]["is_official_filings_data"] is True


def test_hospital_bankruptcy_story_is_accepted(work_root):
    record = _bankruptcy_record(
        "Regional hospital system files Chapter 11",
        "Hospital bankruptcy threatens healthcare access and local service continuity for households.",
        "https://example.com/hospital-bankruptcy",
        source_id="hospital-001",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=True)
    assert result["ok"] is True
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert sources[0]["bankruptcy_subtype"] == "healthcare"


def test_employer_bankruptcy_job_risk_is_accepted(work_root):
    record = _bankruptcy_record(
        "Major regional employer files Chapter 11 amid layoffs",
        "Employer bankruptcy puts hundreds of workers at job-loss risk in a rural county.",
        "https://example.com/employer-bankruptcy",
        source_id="jobs-001",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=True)
    assert result["ok"] is True
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert sources[0]["signal_family"] == "employer_bankruptcy_job_risk"


def test_generic_corporate_restructuring_without_public_impact_is_rejected(work_root):
    record = _bankruptcy_record(
        "Corporate Chapter 11 restructuring update",
        "Company announced Chapter 11 restructuring terms for debt holders and bondholder recoveries.",
        "https://example.com/corp-restructuring",
        source_id="corp-001",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=True)
    assert result["ok"] is False


def test_investor_only_bankruptcy_story_is_rejected(work_root):
    record = _bankruptcy_record(
        "Chapter 11 plan update for bondholders",
        "Investor presentation focuses on equity holders and capital structure optimization.",
        "https://example.com/investor-bankruptcy",
        source_id="investor-001",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=False, dry_run=False, from_manual_sources=True)
    assert result["ok"] is False


def test_household_consumer_bankruptcy_trend_is_accepted(work_root):
    record = _bankruptcy_record(
        "Consumer bankruptcy filings rise in county-level trend report",
        "Household debt burden and Chapter 13 repayment filings rose across multiple counties.",
        "https://example.com/consumer-bankruptcy",
        source_id="consumer-001",
        category_hint="consumer bankruptcy",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True)
    assert result["ok"] is True
    sources = json.loads((work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "sources_manifest.json").read_text(encoding="utf-8"))
    assert sources[0]["bankruptcy_subtype"] == "consumer"


def test_public_html_includes_financial_distress_heading_and_source_links(work_root):
    record = _bankruptcy_record(
        "Small business bankruptcy disrupts local food supplier",
        "Small business distress and bankruptcy disrupted regional food access and local jobs.",
        "https://example.com/food-bankruptcy",
        source_id="food-001",
    )
    _write_manual_sources(work_root, "2026-05-12", [record])
    result = ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True)
    assert result["ok"] is True
    html = (work_root / "output" / "site" / "american-pressure" / "editions" / "2026-05-12" / "index.html").read_text(encoding="utf-8")
    assert "Financial Distress" in html
    assert "Source: <a href=\"https://example.com/food-bankruptcy\"" in html
