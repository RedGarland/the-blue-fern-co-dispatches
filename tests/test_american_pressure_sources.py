import json
import shutil
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches import american_pressure_sources as aps
from scripts.check_american_pressure_sources import main as run_check_script


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
