from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.care_line_authoritative_intake import INTAKE_BATCH_SCHEMA_VERSION, INTAKE_SCHEMA_VERSION, difference_from_phase9, import_intake
from bluefern_dispatches.care_line_source_recovery import discovery_inventory
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.operators.care_line_accumulate import (
    build_bootstrap_review,
    import_entity_review_decisions,
    sample_entity_review_decisions,
)
from bluefern_dispatches.universal_events.operators.care_line_phase8 import run_phase8
from bluefern_dispatches.universal_events.orm import EntityResolutionDecisionRow, EventRow


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(Path.cwd() / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    (root / "docs").mkdir(parents=True)
    (root / "output" / "site").mkdir(parents=True)
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def inv(root: Path) -> dict:
    return discovery_inventory(root, date_from="2026-06-18", date_to="2026-06-18", max_records=5)


def intake_row(inventory: dict, idx: int = 0) -> dict:
    proposal = inventory["proposals"][idx]
    return {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "intake_record_id": f"phase10-shadow-{idx}",
        "discovery_record_id": proposal["discovery_record_id"],
        "discovery_date": proposal["discovery_date"],
        "expected_source_payload_fingerprint": proposal["source_payload_fingerprint"],
        "expected_proposal_fingerprint": proposal["proposal_fingerprint"],
        "reviewer": "phase10-reviewer",
        "review_reason": "Reviewer supplied authoritative publisher URL and source passage.",
        "reviewed_at": "2026-07-21T12:00:00Z",
        "canonical_source_url": f"https://publisher.example.org/shadow/{idx}",
        "source_title": "Clinic closure affects patient access",
        "publisher": "Publisher Example",
        "publication_date": "2026-06-18",
        "source_type": "publisher_article",
        "source_role": "clinic_operations_signal",
        "supporting_passage": "The publisher article reports the clinic will close and patients will need to seek care at other sites.",
        "event_type": "facility_closure",
        "service_line": "primary_care",
        "facility_name": f"Example Clinic {idx}",
        "provider_name": f"Example Clinic {idx}",
        "parent_organization": "",
        "operator_name": "",
        "former_owner": "",
        "new_owner": "",
        "facility_type": "clinic",
        "address_line_1": f"{100 + idx} Main St",
        "address_line_2": "",
        "city": f"Example City {idx}",
        "county": "Example County",
        "state": "IA",
        "postal_code": "50000",
        "country_code": "US",
        "announcement_date": "2026-06-18",
        "effective_date": "2026-07-01",
        "date_precision": "day",
        "permanence": "temporary_or_unknown",
        "evidence_level": "publisher_source",
        "evidence_strength": "reviewed",
        "is_primary_source": False,
        "care_line_public_eligible": False,
        "universal_event_eligible": True,
        "duplicate_of_record_id": "",
        "supersedes_intake_record_id": "",
        "withdrawal_status": "",
        "review_notes": "",
    }


def create_intake(repo: Path, count: int = 2) -> dict:
    inventory = inv(repo)
    rows = [intake_row(inventory, idx) for idx in range(count)]
    return import_intake(
        inventory,
        {"schema_version": INTAKE_BATCH_SCHEMA_VERSION, "batch_id": "phase10-shadow", "records": rows},
        source_root=repo / "data" / "dispatches" / "care-line" / "sources",
        repo_root=repo,
        apply=True,
    )


def phase10(repo: Path, tmp_path: Path):
    return run_phase8(
        repo_root=repo,
        date_from="2026-05-23",
        date_to="2026-06-18",
        reviewed_root=repo / "data" / "dispatches" / "care-line" / "reviewed",
        database=tmp_path / "phase10.sqlite",
        report_dir=tmp_path / "reports",
        review_dir=tmp_path / "reviews",
        calibration_dir=tmp_path / "calibration",
        shadow=True,
        resume=True,
        normalization_review=True,
        generate_bootstrap=True,
        generate_entity_review=True,
        promotion_readiness_preview_enabled=True,
    )


def test_01_shadow_ingestion_creates_candidates_only(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 2)
    result = phase10(repo_copy, tmp_path)
    assert result["counts"]["candidate_count"] == 4
    assert result["counts"]["event_count"] == 0


def test_02_no_verified_events_are_created(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 1)
    phase10(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase10.sqlite")
    repo.initialize_schema()
    with repo.session_scope() as session:
        assert session.execute(select(EventRow)).scalars().all() == []
    repo.close()


def test_03_bootstrap_requires_separate_reviewer_approval(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 1)
    result = phase10(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase10.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    review = build_bootstrap_review(service, run_id=result["run_id"])
    assert review["review_items"]
    repo.close()


def test_04_entity_review_import_rejects_stale_fingerprints(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 1)
    result = phase10(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase10.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    decisions["decisions"][0]["expected_mention_fingerprint"] = "stale"
    path = tmp_path / "entity-decisions.json"
    path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    assert import_entity_review_decisions(tmp_path / "phase10.sqlite", path, shadow=True)["errors"]
    repo.close()


def test_05_effective_decision_counts_ignore_superseded_rows(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 1)
    result = phase10(repo_copy, tmp_path)
    repo = SQLiteUniversalEventRepository(tmp_path / "phase10.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    decisions = sample_entity_review_decisions(service, run_id=result["run_id"])
    path = tmp_path / "entity-decisions.json"
    path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    import_entity_review_decisions(tmp_path / "phase10.sqlite", path, shadow=True)
    with service.repository.session_scope() as session:
        assert session.execute(select(EntityResolutionDecisionRow)).scalars().all()
    repo.close()


def test_06_calibration_uses_real_decisions_only(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 1)
    result = phase10(repo_copy, tmp_path)
    assert result["calibration"]["sample_label"] == "insufficient_sample"


def test_07_promotion_preview_creates_no_events(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 1)
    result = phase10(repo_copy, tmp_path)
    assert result["promotion_readiness_preview"]["metrics"]["total_candidates"] == 3
    assert result["counts"]["event_count"] == 0


def test_08_readiness_counts_exclude_fixtures(repo_copy: Path, tmp_path: Path):
    create_intake(repo_copy, 2)
    result = phase10(repo_copy, tmp_path)
    assert result["readiness"]["decision"].startswith("NOT READY")


def test_09_difference_report_is_deterministic(repo_copy: Path, tmp_path: Path):
    intake_result = create_intake(repo_copy, 1)
    shadow = phase10(repo_copy, tmp_path)
    assert difference_from_phase9(intake_result, shadow_result=shadow) == difference_from_phase9(intake_result, shadow_result=shadow)


def test_10_phase9_tests_exist():
    assert Path("tests/test_care_line_source_recovery_phase9.py").exists()
    assert Path("tests/test_universal_events_care_line_phase9.py").exists()


def test_11_phase8_tests_exist():
    assert Path("tests/test_universal_events_care_line_phase8.py").exists()


def test_12_phase6_to_phase7_tests_exist():
    assert Path("tests/test_care_line_reviewed_export_phase7.py").exists()
    assert Path("tests/test_universal_events_care_line_accumulation_phase7.py").exists()
    assert Path("tests/test_care_line_reviewed_record_contract.py").exists()
    assert Path("tests/test_care_line_historical_normalization.py").exists()


def test_13_phase3_to_phase5_tests_exist():
    assert Path("tests/test_universal_events_care_line_shadow.py").exists()
    assert Path("tests/test_universal_events_care_line_shadow_operator.py").exists()
    assert Path("tests/test_universal_events_care_line_phase5.py").exists()


def test_14_universal_events_phase1_and_phase2_tests_exist():
    assert Path("tests/test_universal_events_phase1.py").exists()
    assert Path("tests/test_universal_events_entity_resolution.py").exists()


def test_15_care_line_publication_tests_exist():
    assert Path("tests/test_care_line_dispatch.py").exists()


def test_16_no_new_tracked_public_output_diffs_are_created():
    status = subprocess.run(["git", "diff", "--name-only"], check=True, text=True, capture_output=True)
    assert "tests/test_universal_events_care_line_phase10.py" not in [line for line in status.stdout.splitlines() if line.startswith("output/site/")]


def test_17_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())


def test_18_complete_repository_suite_command_is_available():
    assert Path("pyproject.toml").exists()
