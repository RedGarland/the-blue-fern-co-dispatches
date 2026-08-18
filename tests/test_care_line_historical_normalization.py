from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bluefern_dispatches.care_line_normalize import (
    DECISIONS_SCHEMA_VERSION,
    apply_decision,
    import_review_decisions,
    load_source_records,
    normalize_historical_records,
    proposal_fingerprint,
    sample_decisions_from_review,
    source_payload_fingerprint,
    write_review_package,
)
from bluefern_dispatches.care_line_record import CareLineReviewedRecord
from bluefern_dispatches.universal_events import SQLiteUniversalEventRepository, UniversalEventService
from bluefern_dispatches.universal_events.adapters.care_line import ingest_care_line_shadow
from bluefern_dispatches.universal_events.adapters.care_line_phase5 import detect_input_type, load_canonical_records
from bluefern_dispatches.universal_events.operators.care_line_shadow import build_input_manifest, load_manifest_records
from bluefern_dispatches.universal_events.orm import CandidateEventRow, EventRow


REAL_SAMPLE = Path("data/dispatches/care-line/sources/2026-05-23/manual_sources.json")


@pytest.fixture()
def source_rows() -> list[dict]:
    return load_source_records(REAL_SAMPLE)


@pytest.fixture()
def normalized(source_rows: list[dict]):
    return normalize_historical_records(source_rows, input_path=REAL_SAMPLE, sample_id="phase6-test")


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def canonical_output(tmp_path: Path, source_rows: list[dict], normalized: dict) -> Path:
    review_dir = tmp_path / "review"
    write_review_package(review_dir, "phase6-test", normalized)
    decisions = sample_decisions_from_review("phase6-test", normalized["review_items"])
    decisions_path = write_json(review_dir / "phase6-test.normalization-decisions.json", decisions)
    output = tmp_path / "reviewed_records.json"
    result = import_review_decisions(source_rows, input_path=REAL_SAMPLE, decisions_path=decisions_path, output_path=output, sample_id="phase6-test")
    assert result["errors"] == []
    return output


def test_01_title_extraction_produces_proposal_not_confirmed(normalized: dict):
    item = next(row for row in normalized["review_items"] if "kcrg-centerville" in row["producer_record_id"])
    facility = next(row for row in item["proposals"] if row["field"] == "facility_name")
    assert facility["value"] == "River Hills Community Health Center"
    record = next(row for row in normalized["records"] if "kcrg-centerville" in row.producer_record_id and "stale" not in row.producer_record_id)
    assert record.field_provenance["facility_name"].review_status == "proposed"


def test_02_unsupported_title_pattern_remains_unresolved(source_rows: list[dict]):
    row = dict(source_rows[1], source_record_id="unmatched", title="Residents discuss local healthcare access")
    result = normalize_historical_records([row], input_path=REAL_SAMPLE, sample_id="phase6-test")
    assert result["review_items"] == []
    assert result["records"][0].universal_event_status == "needs_normalization_review"


def test_03_source_url_fingerprint_is_deterministic(source_rows: list[dict]):
    assert source_payload_fingerprint(source_rows[1]) == source_payload_fingerprint(dict(source_rows[1]))


def test_04_ambiguous_cross_artifact_joins_are_rejected_by_default(normalized: dict):
    assert all(item["conflicts"] == [] for item in normalized["review_items"])
    assert "cross_artifact_join" not in normalized["metrics"]["provenance"]


def test_05_stale_review_decisions_are_rejected(tmp_path: Path, source_rows: list[dict], normalized: dict):
    write_review_package(tmp_path, "phase6-test", normalized)
    decisions = sample_decisions_from_review("phase6-test", normalized["review_items"])
    decisions["decisions"][0]["source_payload_fingerprint"] = "stale"
    decisions_path = write_json(tmp_path / "decisions.json", decisions)
    result = import_review_decisions(source_rows, input_path=REAL_SAMPLE, decisions_path=decisions_path, output_path=tmp_path / "reviewed_records.json", sample_id="phase6-test")
    assert result["errors"]
    assert not (tmp_path / "reviewed_records.json").exists()


def test_06_unsupported_event_types_are_rejected_by_contract():
    payload = {
        "producer_record_id": "bad-event",
        "source_url": "https://example.org",
        "source_title": "Bad event",
        "source_publisher": "Example",
        "raw_payload_hash": "hash",
        "event_type": "bad_event",
    }
    with pytest.raises(ValueError):
        CareLineReviewedRecord.model_validate(payload)


def test_07_financial_context_only_is_not_converted_to_event(normalized: dict):
    record = next(row for row in normalized["records"] if "heraldstandard-hospital-funding" in row.producer_record_id)
    assert record.universal_event_status == "care_line_only"
    assert record.universal_event_eligible is False


def test_08_workforce_only_record_is_not_converted_to_event(source_rows: list[dict]):
    row = dict(source_rows[1], source_record_id="workforce-only", pressure_type="staffing_shortage_access", title="Clinic workers warn of staffing pressure")
    result = normalize_historical_records([row], input_path=REAL_SAMPLE, sample_id="phase6-test")
    assert result["records"][0].universal_event_status == "care_line_only"


def test_09_clinic_closure_title_yields_reviewable_facility_proposal(normalized: dict):
    item = next(row for row in normalized["review_items"] if "kcrg-centerville" in row["producer_record_id"])
    assert {"facility_name", "event_type", "city"} <= {row["field"] for row in item["proposals"]}


def test_10_labor_delivery_halt_yields_facility_and_service_line(normalized: dict):
    item = next(row for row in normalized["review_items"] if "searchlightnm-labor-delivery" in row["producer_record_id"])
    proposals = {row["field"]: row["value"] for row in item["proposals"]}
    assert proposals["facility_name"] == "Los Alamos Medical Center"
    assert proposals["service_line"] == "labor_and_delivery"


def test_11_stale_record_remains_excluded(normalized: dict):
    stale = next(row for row in normalized["records"] if "closure-stale" in row.producer_record_id)
    assert stale.universal_event_status == "excluded"


def test_12_resource_context_record_remains_care_line_only(normalized: dict):
    resource = next(row for row in normalized["records"] if "medicaidgov-enrollment-map" in row.producer_record_id)
    assert resource.universal_event_status == "care_line_only"


def test_13_review_import_creates_canonical_reviewed_record(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"].endswith(".v1")
    assert len(payload["records"]) == 5


def test_14_review_import_preserves_reviewer_and_reason(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    records = [CareLineReviewedRecord.model_validate(row) for row in json.loads(output.read_text(encoding="utf-8"))["records"]]
    ready = [row for row in records if row.universal_event_status == "universal_event_ready"]
    assert ready
    assert all(row.metadata["normalization_review"]["reviewer"] == "phase6-reviewer" for row in ready)


def test_15_review_correction_preserves_history(normalized: dict):
    item = normalized["review_items"][0]
    record = next(row for row in normalized["records"] if row.producer_record_id == item["producer_record_id"])
    decision = sample_decisions_from_review("phase6-test", [item])["decisions"][0]
    decision["decision"] = "replace_proposed_value"
    decision["field_decisions"][0]["action"] = "replace"
    decision["field_decisions"][0]["value"] = "Corrected Facility"
    updated = apply_decision(record, item, decision)
    assert updated.field_provenance[decision["field_decisions"][0]["field"]].provenance_type == "reviewer_corrected"


def test_16_canonical_reviewed_adapter_input_creates_candidate(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    records = [row.as_ingestion_record() for row in load_canonical_records(output, "canonical-reviewed-records")]
    ready = [row for row in records if row.get("_care_line_reviewed_record_contract", {}).get("universal_event_status") == "universal_event_ready"]
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    report = ingest_care_line_shadow(ready, UniversalEventService(repo))
    assert report["run_summary"]["created_candidate_count"] == 2


def test_17_canonical_adapter_preserves_field_provenance(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    records = [row.as_ingestion_record() for row in load_canonical_records(output, "canonical-reviewed-records")]
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    ingest_care_line_shadow([row for row in records if row.get("_care_line_reviewed_record_contract", {}).get("universal_event_status") == "universal_event_ready"], service)
    with service.repository.session_scope() as session:
        candidate = session.execute(select(CandidateEventRow)).scalars().first()
        assert "care_line_reviewed_record_contract" in candidate.metadata_json
        assert "field_provenance" in candidate.metadata_json["care_line_reviewed_record_contract"]


def test_18_candidate_identity_stable_across_normalized_corrections(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    first = [row.as_ingestion_record() for row in load_canonical_records(output, "canonical-reviewed-records")]
    second_payload = json.loads(output.read_text(encoding="utf-8"))
    second_payload["records"][0]["claim_summary"] = "Corrected context summary"
    second = write_json(tmp_path / "reviewed_records_corrected.json", second_payload)
    second_rows = [row.as_ingestion_record() for row in load_canonical_records(second, "canonical-reviewed-records")]
    assert {row["source_record_id"] for row in first} == {row["source_record_id"] for row in second_rows}


def test_19_shadow_rerun_creates_no_verified_events(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    records = [row.as_ingestion_record() for row in load_canonical_records(output, "canonical-reviewed-records")]
    ready = [row for row in records if row.get("_care_line_reviewed_record_contract", {}).get("universal_event_status") == "universal_event_ready"]
    repo = SQLiteUniversalEventRepository(tmp_path / "shadow.sqlite")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    ingest_care_line_shadow(ready, service)
    with service.repository.session_scope() as session:
        assert session.execute(select(EventRow)).scalars().all() == []


def test_20_detect_input_type_supports_canonical_reviewed_records(tmp_path: Path):
    path = write_json(tmp_path / "reviewed_records.json", {"records": []})
    assert detect_input_type(path) == "canonical-reviewed-records"


def test_21_manifest_loads_canonical_reviewed_records(tmp_path: Path, source_rows: list[dict], normalized: dict):
    output = canonical_output(tmp_path, source_rows, normalized)
    manifest = build_input_manifest(Path.cwd(), input_paths=[output], input_types=["canonical-reviewed-records"])
    rows = load_manifest_records(manifest, Path.cwd())
    assert len(rows) == 5
    assert any(row.get("_care_line_reviewed_record_contract") for row in rows)


def test_22_proposal_fingerprint_changes_when_proposal_changes(normalized: dict):
    proposals = normalized["review_items"][0]["proposals"]
    assert proposal_fingerprint(proposals) != proposal_fingerprint([dict(proposals[0], value="changed")])


def test_23_review_package_files_are_created(tmp_path: Path, normalized: dict):
    paths = write_review_package(tmp_path, "phase6-test", normalized)
    assert Path(paths["review_json"]).exists()
    assert Path(paths["review_md"]).exists()
    assert Path(paths["decisions_template"]).exists()


def test_24_decisions_schema_version_is_versioned(normalized: dict):
    decisions = sample_decisions_from_review("phase6-test", normalized["review_items"])
    assert decisions["schema_version"] == DECISIONS_SCHEMA_VERSION


def test_25_pages_repository_remains_clean():
    pages = Path("bluefern-dispatches-pages")
    if not pages.exists():
        pytest.skip("nested Pages repo not present")
    status = subprocess.run(["git", "-C", str(pages), "status", "--short", "--branch"], check=True, text=True, capture_output=True)
    assert not any(line and not line.startswith("##") for line in status.stdout.splitlines())
