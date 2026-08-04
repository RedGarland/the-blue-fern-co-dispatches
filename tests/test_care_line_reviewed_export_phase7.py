from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_record import SCHEMA_VERSION, CareLineReviewedRecord
from bluefern_dispatches.care_line_reviewed_export import export_range, export_records_for_date, refuse_public_or_pages_path, reviewed_record_from_source
from bluefern_dispatches.care_line_sources import load_manual_source_records, public_claim_rows, record_is_public


REAL_DATE = "2026-05-23"


@pytest.fixture()
def repo_copy(tmp_path: Path) -> Path:
    repo = Path.cwd()
    root = tmp_path / "repo"
    shutil.copytree(repo / "data" / "dispatches" / "care-line", root / "data" / "dispatches" / "care-line")
    copied_reviewed = root / "data" / "dispatches" / "care-line" / "reviewed"
    if copied_reviewed.exists():
        shutil.rmtree(copied_reviewed)
    (root / "output" / "site").mkdir(parents=True)
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def records(repo_copy: Path) -> list[dict]:
    return load_manual_source_records(repo_copy, REAL_DATE)


def test_01_final_reviewed_record_exports_canonical_record(repo_copy: Path):
    row = records(repo_copy)[1] | {"facility_name": "River Hills Community Health Center", "event_type": "facility_closure"}
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"), reviewer="r", review_reason="approved")
    assert record.schema_version == SCHEMA_VERSION
    assert record.source_url.startswith("https://")


def test_02_pending_record_does_not_export_as_ready(repo_copy: Path):
    row = records(repo_copy)[1] | {"review_status": "needs_review", "facility_name": "River Hills Community Health Center"}
    assert reviewed_record_from_source(row, input_path=Path("manual_sources.json")).universal_event_status == "needs_normalization_review"


def test_03_care_line_only_record_remains_publicly_usable(repo_copy: Path):
    row = records(repo_copy)[0]
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    assert record.care_line_public_eligible is True
    assert record.universal_event_status == "care_line_only"


def test_04_universal_event_ineligible_does_not_block_care_line_publication(repo_copy: Path):
    rows = records(repo_copy)
    assert len([row for row in rows if record_is_public(row)]) == 3
    assert reviewed_record_from_source(rows[0], input_path=Path("manual_sources.json")).universal_event_eligible is False
    assert len(public_claim_rows(rows)) == 3


def test_05_missing_facility_fails_universal_event_eligibility(repo_copy: Path):
    row = records(repo_copy)[1]
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    assert record.universal_event_status == "needs_normalization_review"
    assert record.metadata["canonical_export_reason"] == "missing_facility_or_provider"


def test_06_missing_service_line_fails_service_event_profile(repo_copy: Path):
    row = records(repo_copy)[1] | {"facility_name": "Clinic", "event_type": "service_closure", "service_line": ""}
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    assert record.metadata["canonical_export_reason"] == "missing_service_line"


def test_07_complete_service_closure_exports_ready(repo_copy: Path):
    row = records(repo_copy)[2] | {"facility_name": "Los Alamos Medical Center", "event_type": "service_suspension", "service_line": "labor_and_delivery"}
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    assert record.universal_event_status == "universal_event_ready"


def test_08_correction_creates_new_record_version(repo_copy: Path):
    row = records(repo_copy)[1] | {"facility_name": "River Hills Community Health Center", "event_type": "facility_closure", "correction_reason": "name"}
    first = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    second = CareLineReviewedRecord.model_validate(first.model_dump(mode="json") | {"version": 2, "supersedes_record_id": first.version_id, "version_id": ""})
    assert second.version_id != first.version_id
    assert second.supersedes_record_id == first.version_id


def test_09_correction_preserves_producer_id(repo_copy: Path):
    row = records(repo_copy)[1] | {"facility_name": "River Hills Community Health Center", "event_type": "facility_closure"}
    first = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    second = CareLineReviewedRecord.model_validate(first.model_dump(mode="json") | {"version": 2, "version_id": ""})
    assert second.producer_record_id == first.producer_record_id


def test_10_withdrawal_preserves_prior_history(repo_copy: Path):
    row = records(repo_copy)[1] | {"facility_name": "River Hills Community Health Center", "event_type": "facility_closure", "withdrawn": True}
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    assert record.universal_event_status == "withdrawn"
    assert record.is_withdrawn is True


def test_11_duplicate_preserves_source_provenance(repo_copy: Path):
    row = records(repo_copy)[1] | {"duplicate_of_record_id": "care-line-retained"}
    record = reviewed_record_from_source(row, input_path=Path("manual_sources.json"))
    assert record.universal_event_status == "duplicate"
    assert record.source_url


def test_12_export_ordering_is_deterministic(repo_copy: Path):
    output = repo_copy / "data" / "dispatches" / "care-line" / "reviewed"
    export_records_for_date(repo_copy, REAL_DATE, output_root=output)
    first = (output / REAL_DATE / "reviewed_records.json").read_text(encoding="utf-8")
    export_records_for_date(repo_copy, REAL_DATE, output_root=output)
    assert first == (output / REAL_DATE / "reviewed_records.json").read_text(encoding="utf-8")


def test_13_manifest_is_deterministic(repo_copy: Path):
    output = repo_copy / "data" / "dispatches" / "care-line" / "reviewed"
    first = export_range(repo_copy, date_from=REAL_DATE, date_to=REAL_DATE, output_root=output)
    second = export_range(repo_copy, date_from=REAL_DATE, date_to=REAL_DATE, output_root=output)
    assert first == second


def test_14_repeated_export_is_idempotent(repo_copy: Path):
    output = repo_copy / "data" / "dispatches" / "care-line" / "reviewed"
    export_records_for_date(repo_copy, REAL_DATE, output_root=output)
    payload = json.loads((output / REAL_DATE / "reviewed_records.json").read_text(encoding="utf-8"))
    export_records_for_date(repo_copy, REAL_DATE, output_root=output)
    assert len(json.loads((output / REAL_DATE / "reviewed_records.json").read_text(encoding="utf-8"))["records"]) == len(payload["records"])


def test_15_check_only_writes_nothing(repo_copy: Path):
    output = repo_copy / "data" / "dispatches" / "care-line" / "reviewed"
    export_records_for_date(repo_copy, REAL_DATE, output_root=output, check_only=True)
    assert not (output / REAL_DATE / "reviewed_records.json").exists()


def test_16_export_refuses_public_output_paths(repo_copy: Path):
    with pytest.raises(ValueError):
        refuse_public_or_pages_path(repo_copy / "output" / "site" / "reviewed", repo_copy)


def test_17_export_refuses_pages_paths(repo_copy: Path):
    with pytest.raises(ValueError):
        refuse_public_or_pages_path(repo_copy / "bluefern-dispatches-pages" / "reviewed", repo_copy)


def test_18_canonical_records_contain_field_provenance(repo_copy: Path):
    record = reviewed_record_from_source(records(repo_copy)[1], input_path=Path("manual_sources.json"))
    assert "source_url" in record.field_provenance


def test_19_reviewer_identity_and_reason_are_preserved(repo_copy: Path):
    record = reviewed_record_from_source(records(repo_copy)[1], input_path=Path("manual_sources.json"), reviewer="reviewer", review_reason="approved")
    assert record.metadata["reviewer"] == "reviewer"
    assert record.field_provenance["source_url"].reviewer == "reviewer"


def test_20_unsupported_schema_version_is_rejected(repo_copy: Path):
    record = reviewed_record_from_source(records(repo_copy)[1], input_path=Path("manual_sources.json"))
    payload = record.model_dump(mode="json")
    payload["schema_version"] = "bad"
    with pytest.raises(ValueError):
        CareLineReviewedRecord.model_validate(payload)


def test_21_export_manifest_counts_statuses(repo_copy: Path):
    output = repo_copy / "data" / "dispatches" / "care-line" / "reviewed"
    manifest = export_records_for_date(repo_copy, REAL_DATE, output_root=output)
    assert manifest["input_record_count"] == 5
    assert manifest["care_line_only_count"] >= 1


def test_22_export_report_can_be_check_only(repo_copy: Path):
    report = repo_copy / "data" / "universal_events" / "phase7-export-report.json"
    result = export_range(repo_copy, date_from=REAL_DATE, date_to=REAL_DATE, output_root=repo_copy / "data" / "dispatches" / "care-line" / "reviewed", report_path=report, check_only=True)
    assert result["check_only"] is True
    assert report.exists()
