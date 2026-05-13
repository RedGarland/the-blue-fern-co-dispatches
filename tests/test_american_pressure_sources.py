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
    notes: valid
"""


def test_source_registry_file_parses_from_project_root():
    root = Path(__file__).resolve().parents[1]
    sources = aps.load_source_registry(root)
    assert sources
    assert any(source.get("source_id") == "usda-fns-snap-household-characteristics" for source in sources)


def test_all_enabled_sources_have_required_fields():
    root = Path(__file__).resolve().parents[1]
    errors = aps.validate_registry_sources(aps.load_source_registry(root))
    assert not errors


def test_duplicate_source_id_is_rejected(work_root):
    _write_registry(
        work_root,
        """
sources:
  - source_id: duplicate-id
    name: One
    url: https://example.com/one
    publisher: Org
    pillar: food_pressure
    geography: US
    source_type: feed
    reliability_tier: official_primary
    update_frequency: monthly
    enabled: true
    notes: one
  - source_id: duplicate-id
    name: Two
    url: https://example.com/two
    publisher: Org
    pillar: food_pressure
    geography: US
    source_type: feed
    reliability_tier: official_primary
    update_frequency: monthly
    enabled: true
    notes: two
""",
    )
    errors = aps.validate_registry_sources(aps.load_source_registry(work_root))
    assert any("duplicate source_id" in error for error in errors)


def test_invalid_pillar_is_rejected(work_root):
    _write_registry(work_root, _minimal_valid_registry().replace("food_pressure", "bad_pillar"))
    errors = aps.validate_registry_sources(aps.load_source_registry(work_root))
    assert any("invalid pillar" in error for error in errors)


def test_invalid_reliability_tier_is_rejected(work_root):
    _write_registry(work_root, _minimal_valid_registry().replace("official_primary", "bad_tier"))
    errors = aps.validate_registry_sources(aps.load_source_registry(work_root))
    assert any("invalid reliability_tier" in error for error in errors)


def test_malformed_url_is_rejected(work_root):
    _write_registry(work_root, _minimal_valid_registry().replace("https://www.fns.usda.gov/research/snap/household-characteristics", "notaurl"))
    errors = aps.validate_registry_sources(aps.load_source_registry(work_root))
    assert any("malformed URL" in error for error in errors)


def test_source_health_report_can_be_generated_without_live_fetch(work_root):
    _write_registry(work_root, _minimal_valid_registry())
    sources = aps.load_source_registry(work_root)
    report = aps.build_source_health_report(sources, fetch_check=False, checked_at="2026-05-13T00:00:00Z")
    assert len(report) == 1
    assert report[0]["fetch_attempted"] is False
    assert report[0]["fetch_success"] is None
    assert report[0]["status_code"] is None
    assert report[0]["failure_reason"] is None


def test_source_health_report_write_is_not_under_output_site(work_root):
    _write_registry(work_root, _minimal_valid_registry())
    report = aps.build_source_health_report(aps.load_source_registry(work_root), fetch_check=False)
    out = aps.write_source_health_report(work_root, report, "2026-05-13")
    assert out.exists()
    assert "output\\site\\" not in str(out).lower()
    assert not (work_root / "output" / "site" / "american-pressure" / "source_health" / "2026-05-13.json").exists()


def test_fetch_check_mode_can_be_mocked_deterministically(work_root, monkeypatch):
    _write_registry(work_root, _minimal_valid_registry())
    monkeypatch.setattr(aps, "_fetch_status", lambda url, timeout_seconds=8, user_agent="x": (True, 200, None))
    report = aps.build_source_health_report(aps.load_source_registry(work_root), fetch_check=True)
    assert report[0]["fetch_attempted"] is True
    assert report[0]["fetch_success"] is True
    assert report[0]["status_code"] == 200


def test_checker_script_validate_only(work_root):
    _write_registry(work_root, _minimal_valid_registry())
    code = run_check_script(["--root", str(work_root)])
    assert code == 0


def test_checker_script_write_report(work_root):
    _write_registry(work_root, _minimal_valid_registry())
    code = run_check_script(["--root", str(work_root), "--write-report", "--date", "2026-05-13"])
    assert code == 0
    report_path = work_root / "output" / "dispatches" / "american-pressure" / "source_health" / "2026-05-13.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload and payload[0]["source_id"] == "usda-snap"
