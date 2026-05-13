import json
import shutil
import uuid
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
    with pytest.raises(FileNotFoundError):
        ap_runner.run_american_pressure_dispatch(work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True)


def test_runner_refuses_zero_valid_records(work_root):
    _write_manual_sources(work_root, "2026-05-12", [])
    result = ap_runner.run_american_pressure_dispatch(
        work_root, "2026-05-12", publish=True, dry_run=False, from_manual_sources=True
    )
    assert result["ok"] is False
    assert "No valid source-backed American Pressure records found for 2026-05-12" in " ".join(result["errors"])


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
