import json
import shutil
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches import american_pressure_sources as aps
from scripts.check_american_pressure_sources import main as run_check_script
from scripts.validate_american_pressure_feeds import main as run_feed_validator


@pytest.fixture()
def work_root():
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "american-pressure-sources"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def _write_registry(root: Path, body: str) -> Path:
    path = root / "data" / "dispatches" / "american-pressure" / "source_registry.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return path


def _minimal_valid_registry() -> str:
    return """
sources:
  - source_id: usda-snap
    name: SNAP
    url: https://www.fns.usda.gov/research/snap/household-characteristics
    publisher: USDA
    pillar: food_pressure
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: true
    source_state: enabled
    notes: valid
  - source_id: bk
    name: BK
    url: https://www.uscourts.gov/statistics-reports/analysis-reports/bankruptcy-filings-statistics
    publisher: AOUSC
    pillar: financial_distress_pressure
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: true
    source_state: enabled
    notes: valid
  - source_id: housing
    name: Housing
    url: https://www.bls.gov/cpi/
    publisher: BLS
    pillar: housing_household_cost_pressure
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: false
    source_state: diagnostics_only
    notes: valid
  - source_id: health
    name: Health
    url: https://www.medicaid.gov/medicaid/national-medicaid-chip-program-information/medicaid-chip-enrollment-data
    publisher: CMS
    pillar: health_access_pressure
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: true
    source_state: enabled
    notes: valid
  - source_id: labor
    name: Labor
    url: https://www.bls.gov/news.release/empsit.nr0.htm
    publisher: BLS
    pillar: labor_income_pressure
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: false
    source_state: manual_only
    notes: valid
  - source_id: env
    name: Env
    url: https://droughtmonitor.unl.edu/
    publisher: NDMC
    pillar: environmental_pressure
    geography: US
    source_type: official_report_page
    reliability_tier: institutional
    update_frequency: annual
    enabled: true
    source_state: enabled
    notes: valid
  - source_id: local
    name: Local
    url: https://www.fema.gov/openfema-data-page/disaster-declarations-summaries-v2
    publisher: FEMA
    pillar: local_system_strain
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: true
    source_state: enabled
    notes: valid
  - source_id: policy
    name: Policy
    url: https://www.acf.hhs.gov/ocs/programs/liheap
    publisher: HHS
    pillar: policy_implementation
    geography: US
    source_type: official_report_page
    reliability_tier: official_primary
    update_frequency: annual
    enabled: false
    source_state: manual_only
    notes: valid
"""


def test_source_registry_file_parses_from_project_root():
    root = Path(__file__).resolve().parents[1]
    sources = aps.load_source_registry(root)
    assert sources


def test_all_enabled_sources_have_required_fields():
    root = Path(__file__).resolve().parents[1]
    errors = aps.validate_registry_sources(aps.load_source_registry(root))
    assert not errors


def test_registry_requires_all_pillars(work_root):
    _write_registry(work_root, _minimal_valid_registry().split("- source_id: policy")[0])
    errors = aps.validate_registry_sources(aps.load_source_registry(work_root))
    assert any("registry missing required pillars" in e for e in errors)


def test_source_health_summary_counts(work_root):
    _write_registry(work_root, _minimal_valid_registry())
    report = aps.build_source_health_report(aps.load_source_registry(work_root), fetch_check=False)
    summary = aps.summarize_source_health(report)
    assert summary["sources_configured"] == 8
    assert summary["enabled_sources"] >= 4
    assert summary["manual_only_sources"] >= 1


def test_checker_script_write_report(work_root):
    _write_registry(work_root, _minimal_valid_registry())
    code = run_check_script(["--root", str(work_root), "--write-report", "--date", "2026-05-13"])
    assert code == 0
    payload = json.loads((work_root / "output" / "dispatches" / "american-pressure" / "source_health" / "2026-05-13.json").read_text(encoding="utf-8"))
    assert payload
    assert "recommendation" in payload[0]


def test_national_registry_parses_and_has_substantial_foundation():
    root = Path(__file__).resolve().parents[1]
    rows = aps.load_national_source_registry(root)
    assert len(rows) >= 250


def test_national_registry_validation_rules():
    root = Path(__file__).resolve().parents[1]
    rows = aps.load_national_source_registry(root)
    errors = aps.validate_national_source_registry(rows)
    assert not errors


def test_national_coverage_summary_generation():
    root = Path(__file__).resolve().parents[1]
    rows = aps.load_national_source_registry(root)
    summary = aps.build_national_coverage_summary(rows)
    assert summary["total_sources"] >= 250
    assert len(summary["states_covered"]) == 51
    assert not summary["states_missing"]


def test_feed_health_summary_generation():
    root = Path(__file__).resolve().parents[1]
    rows = aps.load_national_source_registry(root)
    summary = aps.build_feed_health_summary(rows)
    assert "total_ingest_ready_sources" in summary
    assert "known_feed_url_count" in summary
    assert "live_validated_feed_count" in summary
    assert "pending_validation_count" in summary
    assert "rss_capable_count" in summary
    assert "manual_only_count" in summary


def test_known_feed_urls_can_exist_without_live_validation_or_ingest_ready():
    root = Path(__file__).resolve().parents[1]
    rows = aps.load_national_source_registry(root)
    summary = aps.build_feed_health_summary(rows)
    assert summary["known_feed_url_count"] > 0
    assert summary["live_validated_feed_count"] >= 0
    assert summary["total_ingest_ready_sources"] == summary["live_validated_feed_count"]


def test_national_registry_rejects_invalid_feed_urls():
    rows = [
        {
            "source_id": "bad-feed",
            "source_name": "Bad Feed",
            "homepage_url": "https://example.com",
            "rss_url": "notaurl",
            "atom_url": "",
            "json_feed_url": "",
            "sitemap_url": "",
            "feed_discovery_status": "manual_only",
            "feed_type": "none",
            "polling_priority": "low",
            "collection_method": "manual_review",
            "robots_allowed": True,
            "paywall_status": "unknown",
            "last_verified_utc": "",
            "feed_health": "unknown",
            "ingest_ready": False,
            "feed_url_known": True,
            "feed_validated_live": False,
            "validation_status": "pending_live_validation",
            "source_type": "public_media",
            "coverage_scope": "statewide",
            "state": "AL",
            "metro_or_region": "Alabama",
            "urban_rural_focus": "mixed",
            "pressure_pillars": ["food_grocery_pressure"],
            "reliability_tier": "public_media",
            "ownership_type": "public_media_member_network",
            "language": "en",
            "active": True,
            "notes": "x",
        }
    ]
    errors = aps.validate_national_source_registry(rows)
    assert any("malformed rss_url" in e for e in errors)


def test_feed_validator_writes_result_rows_for_attempted_checks(work_root, monkeypatch):
    registry_path = work_root / "data" / "source_registry" / "american_pressure_sources.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            [
                {
                    "source_id": "s1",
                    "source_name": "Source One",
                    "homepage_url": "https://example.com",
                    "rss_url": "https://example.com/feed.xml",
                    "atom_url": "",
                    "json_feed_url": "",
                    "sitemap_url": "",
                    "feed_discovery_status": "discovery_attempted",
                    "feed_type": "rss",
                    "polling_priority": "medium",
                    "collection_method": "feed_polling",
                    "robots_allowed": True,
                    "paywall_status": "unknown",
                    "last_verified_utc": "",
                    "feed_health": "unknown",
                    "ingest_ready": False,
                    "feed_url_known": True,
                    "feed_validated_live": False,
                    "validation_status": "pending_live_validation",
                    "source_type": "public_media",
                    "coverage_scope": "statewide",
                    "state": "AL",
                    "metro_or_region": "Alabama",
                    "urban_rural_focus": "mixed",
                    "pressure_pillars": ["food_grocery_pressure"],
                    "reliability_tier": "public_media",
                    "ownership_type": "public_media_member_network",
                    "language": "en",
                    "active": True,
                    "notes": "x",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "scripts.validate_american_pressure_feeds.fetch_status",
        lambda url, timeout_seconds=8: (False, 503, "HTTPError: 503"),
    )
    monkeypatch.setattr(
        "scripts.validate_american_pressure_feeds.validate_national_source_registry",
        lambda sources: [],
    )
    code = run_feed_validator(["--root", str(work_root), "--max-checks", "5", "--timeout-seconds", "1"])
    assert code == 0
    payload = json.loads((work_root / "output" / "site" / "american-pressure" / "source_feed_validation_report.json").read_text(encoding="utf-8"))
    assert payload["checks_attempted"] == 1
    assert payload["results_count"] == 1
    assert len(payload["results"]) == 1
    assert payload["results"][0]["source_id"] == "s1"
    assert payload["results"][0]["validation_status"] == "live_failed"
    assert payload["live_failed_count"] == 1


def test_apply_feed_validation_report_updates_only_matched_sources():
    sources = [
        {
            "source_id": "a",
            "source_name": "A",
            "homepage_url": "https://a.example.com",
            "rss_url": "https://a.example.com/feed.xml",
            "atom_url": "",
            "json_feed_url": "",
            "sitemap_url": "",
            "feed_discovery_status": "discovery_attempted",
            "feed_type": "rss",
            "polling_priority": "high",
            "collection_method": "feed_polling",
            "robots_allowed": True,
            "paywall_status": "unknown",
            "last_verified_utc": "",
            "feed_health": "unknown",
            "ingest_ready": False,
            "feed_url_known": True,
            "feed_validated_live": False,
            "validation_status": "pending_live_validation",
            "source_type": "public_media",
            "coverage_scope": "statewide",
            "state": "AL",
            "metro_or_region": "AL",
            "urban_rural_focus": "mixed",
            "pressure_pillars": ["food_grocery_pressure"],
            "reliability_tier": "public_media",
            "ownership_type": "public_media_member_network",
            "language": "en",
            "active": True,
            "notes": "",
        },
        {
            "source_id": "b",
            "source_name": "B",
            "homepage_url": "https://b.example.com",
            "rss_url": "https://b.example.com/feed.xml",
            "atom_url": "",
            "json_feed_url": "",
            "sitemap_url": "",
            "feed_discovery_status": "discovery_attempted",
            "feed_type": "rss",
            "polling_priority": "high",
            "collection_method": "feed_polling",
            "robots_allowed": True,
            "paywall_status": "unknown",
            "last_verified_utc": "",
            "feed_health": "unknown",
            "ingest_ready": False,
            "feed_url_known": True,
            "feed_validated_live": False,
            "validation_status": "pending_live_validation",
            "source_type": "local_news",
            "coverage_scope": "statewide",
            "state": "AL",
            "metro_or_region": "AL",
            "urban_rural_focus": "rural",
            "pressure_pillars": ["food_grocery_pressure"],
            "reliability_tier": "established_local_news",
            "ownership_type": "local_or_regional_newsroom",
            "language": "en",
            "active": True,
            "notes": "",
        },
    ]
    report = {
        "results": [
            {
                "source_id": "a",
                "feed_url": "https://a.example.com/feed.xml",
                "validation_status": "live_validated",
                "checked_at_utc": "2026-05-18T23:26:29.253407Z",
            },
            {
                "source_id": "b",
                "feed_url": "https://b.example.com/feed.xml",
                "validation_status": "live_failed",
                "error": "HTTPError: 404",
                "checked_at_utc": "2026-05-18T23:26:30.468762Z",
            },
        ]
    }
    updated, summary = aps.apply_feed_validation_report(sources, report)
    a = next(row for row in updated if row["source_id"] == "a")
    b = next(row for row in updated if row["source_id"] == "b")
    assert a["feed_validated_live"] is True
    assert a["ingest_ready"] is True
    assert a["validation_status"] == "live_validated"
    assert a["feed_health"] == "ok"
    assert a["last_verified_utc"] == "2026-05-18T23:26:29.253407Z"
    assert b["feed_validated_live"] is False
    assert b["ingest_ready"] is False
    assert b["validation_status"] == "live_failed"
    assert b["feed_url_known"] is True
    assert b["feed_health_detail"] == "HTTPError: 404"
    assert summary["applied_validated"] == 1
    assert summary["applied_failed"] == 1


def test_apply_feed_validation_report_does_not_change_unmatched_sources():
    source = {
        "source_id": "x",
        "source_name": "X",
        "homepage_url": "https://x.example.com",
        "rss_url": "https://x.example.com/feed.xml",
        "atom_url": "",
        "json_feed_url": "",
        "sitemap_url": "",
        "feed_discovery_status": "discovery_attempted",
        "feed_type": "rss",
        "polling_priority": "medium",
        "collection_method": "feed_polling",
        "robots_allowed": True,
        "paywall_status": "unknown",
        "last_verified_utc": "",
        "feed_health": "unknown",
        "ingest_ready": False,
        "feed_url_known": True,
        "feed_validated_live": False,
        "validation_status": "pending_live_validation",
        "source_type": "public_media",
        "coverage_scope": "statewide",
        "state": "AL",
        "metro_or_region": "AL",
        "urban_rural_focus": "mixed",
        "pressure_pillars": ["food_grocery_pressure"],
        "reliability_tier": "public_media",
        "ownership_type": "public_media_member_network",
        "language": "en",
        "active": True,
        "notes": "",
    }
    updated, summary = aps.apply_feed_validation_report([source], {"results": [{"source_id": "other", "feed_url": "https://other/feed.xml", "validation_status": "live_validated"}]})
    assert updated[0]["validation_status"] == "pending_live_validation"
    assert updated[0]["ingest_ready"] is False
    assert summary["applied_validated"] == 0
    assert summary["applied_failed"] == 0
