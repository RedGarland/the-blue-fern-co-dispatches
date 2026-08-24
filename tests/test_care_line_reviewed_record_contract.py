from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from bluefern_dispatches.care_line_effective_date_follow_up import load_reviewed_records
from bluefern_dispatches.care_line_record import (
    SCHEMA_VERSION,
    CareLineReviewedRecord,
    FieldProvenance,
    corrected_record,
    deterministic_records_json,
)


def complete_record(**overrides) -> CareLineReviewedRecord:
    payload = {
        "producer_record_id": "care-line-contract-001",
        "record_status": "universal_event_ready",
        "review_status": "approved",
        "public_status": "public_approved",
        "universal_event_status": "universal_event_ready",
        "care_line_public_eligible": True,
        "source_url": "https://example.org/clinic-closes",
        "source_title": "Example Clinic announces closure",
        "source_publisher": "Example News",
        "source_publication_date": "2026-05-12T00:00:00Z",
        "source_type": "local_news",
        "source_role": "clinic_operations_signal",
        "supporting_passage": "The source reports the clinic will close.",
        "raw_payload_hash": "hash-001",
        "event_type": "facility_closure",
        "event_type_raw": "clinic_access_strain",
        "change_direction": "reduced",
        "permanence": "permanent",
        "announcement_date": "2026-05-12",
        "date_precision": "day",
        "facility_name": "Example Clinic",
        "provider_name": "Example Clinic",
        "facility_type": "clinic",
        "city": "Centerville",
        "state": "IA",
        "country_code": "US",
        "location_text": "Centerville, IA",
        "geographic_scope": "city",
        "claim_summary": "The clinic closure reduces local access.",
        "evidence_level": "reported_story",
        "evidence_strength": "high",
        "is_primary_source": True,
        "field_provenance": {
            "facility_name": {
                "value": "Example Clinic",
                "provenance_type": "source_explicit",
                "source_field": "title",
                "supporting_text": "Example Clinic announces closure",
                "confidence": 1.0,
                "review_status": "confirmed",
            }
        },
    }
    payload.update(overrides)
    return CareLineReviewedRecord.model_validate(payload)


def test_01_canonical_schema_validates_complete_facility_closure():
    record = complete_record()
    assert record.schema_version == SCHEMA_VERSION
    assert record.universal_event_eligible is True


def test_02_missing_facility_provider_fails_operational_profile():
    record = complete_record(facility_name="", provider_name="")
    assert "missing_subject" in {issue.code for issue in record.validation_issues()}
    assert record.universal_event_eligible is False


def test_03_non_operational_context_is_care_line_valid_but_universal_ineligible():
    record = complete_record(
        record_status="care_line_only",
        universal_event_status="care_line_only",
        event_type="resource_context",
        facility_name="",
        provider_name="",
        care_line_public_eligible=True,
    )
    assert record.validation_issues() == []
    assert record.universal_event_eligible is False


def test_04_service_closure_requires_service_line():
    record = complete_record(event_type="service_closure", service_line="unknown")
    assert "missing_service_line" in {issue.code for issue in record.validation_issues()}


def test_05_ownership_change_validates_owner_rules():
    record = complete_record(event_type="ownership_change", permanence="", new_owner="New Health LLC")
    assert record.validation_issues() == []


def test_06_statewide_profile_requires_statewide_scope():
    valid = complete_record(event_type="capacity_reduction", facility_name="", provider_name="", geographic_scope="statewide", location_text="Pennsylvania", city="", state="PA")
    invalid = complete_record(event_type="capacity_reduction", facility_name="", provider_name="", geographic_scope="city", location_text="Pennsylvania", city="", state="PA")
    assert valid.validation_issues() == []
    assert "missing_geography" in {issue.code for issue in invalid.validation_issues()} or "statewide_scope_required" in {issue.code for issue in invalid.validation_issues()}


def test_07_field_provenance_preserves_source_text():
    record = complete_record()
    assert record.field_provenance["facility_name"].supporting_text == "Example Clinic announces closure"


def test_08_reviewer_confirmation_differs_from_source_explicit():
    proposed = FieldProvenance(value="Example Clinic", provenance_type="deterministic_extraction", source_field="title", supporting_text="Example Clinic announces closure", confidence=0.8, review_status="proposed")
    confirmed = FieldProvenance(value="Example Clinic", provenance_type="reviewer_confirmed", source_field="title", supporting_text="Example Clinic announces closure", confidence=0.8, review_status="confirmed", reviewer="reviewer-a")
    assert proposed.provenance_type == "deterministic_extraction"
    assert confirmed.provenance_type == "reviewer_confirmed"


def test_09_corrections_supersede_without_overwrite():
    original = complete_record()
    corrected = corrected_record(original, updates={"facility_name": "Corrected Clinic"}, reviewer="reviewer-a", reason="name corrected")
    assert corrected.supersedes_record_id == original.version_id
    assert corrected.version == original.version + 1
    assert corrected.correction_history[0]["superseded_version_id"] == original.version_id


def test_10_stable_producer_record_id_survives_normalization():
    assert complete_record().producer_record_id == complete_record(source_title="Changed title").producer_record_id


def test_11_deterministic_output_is_byte_stable():
    first = deterministic_records_json([complete_record()])
    second = deterministic_records_json([complete_record(updated_at="2026-01-01T00:00:00Z")])
    assert first == second


def test_12_unknown_profile_fails_validation():
    with pytest.raises(ValueError, match="unsupported event_type"):
        complete_record(event_type="unmapped_event")


def test_13_unknown_service_line_is_blocked_when_required():
    record = complete_record(event_type="service_suspension", service_line="unknown")
    assert "missing_service_line" in {issue.code for issue in record.validation_issues()}


def test_14_withdrawn_record_cannot_be_universal_ready_without_review_status_change():
    record = complete_record(is_withdrawn=True, universal_event_status="excluded", record_status="excluded")
    assert record.universal_event_eligible is False


def test_15_adapter_record_preserves_contract_metadata():
    adapter = complete_record().to_adapter_record()
    assert adapter["_care_line_reviewed_record_contract"]["schema_version"] == SCHEMA_VERSION
    assert "facility_name" in adapter["_care_line_reviewed_record_contract"]["field_provenance"]
    assert "event_identity" not in adapter
    assert "event_instance_id" not in adapter
    assert "lifecycle_status" not in adapter
    assert "follow_up_window_start" not in adapter
    assert "follow_up_window_end" not in adapter


def test_16_schema_requires_source_url():
    with pytest.raises(ValueError):
        complete_record(source_url="")


def test_17_no_absolute_paths_required_in_deterministic_output(tmp_path: Path):
    record = complete_record(metadata={"input_path": (tmp_path / "input.json").as_posix()})
    assert str(tmp_path) not in deterministic_records_json([record])


def test_18_duplicate_candidate_record_is_not_ready():
    record = complete_record(duplicate_of_record_id="care-line-contract-000", record_status="excluded", universal_event_status="excluded")
    assert record.universal_event_eligible is False


def test_19_source_explicit_confirmed_provenance_is_allowed():
    provenance = FieldProvenance(value="Example Clinic", provenance_type="source_explicit", source_field="title", supporting_text="Example Clinic announces closure", confidence=1.0, review_status="confirmed")
    assert provenance.review_status == "confirmed"


def test_20_historical_reviewed_records_with_lifecycle_metadata_validate_and_round_trip():
    records = load_reviewed_records(Path.cwd())
    mount_carmel = next(
        record for record in records if record.producer_record_id == "care-line-historical-backfill-2026-06-24-mount-carmel-franklinton-ed"
    )
    santa_paula = next(
        record for record in records if record.producer_record_id == "care-line-historical-backfill-2026-08-19-santa-paula-hospital"
    )

    assert mount_carmel.event_identity == "care_line_event_e5ea71d96d89a09f"
    assert mount_carmel.event_instance_id == "care_line_event_e5ea71d96d89a09f_09296918c472"
    assert mount_carmel.lifecycle_status == "PENDING_EFFECTIVE_DATE"
    assert mount_carmel.effective_follow_up_status == "pending"
    assert mount_carmel.follow_up_window_start == "2026-08-08"
    assert mount_carmel.follow_up_window_end == "2026-08-29"

    assert santa_paula.event_identity == ""
    assert santa_paula.event_instance_id == ""
    assert santa_paula.lifecycle_status == ""
    assert santa_paula.effective_follow_up_status == ""
    assert santa_paula.follow_up_window_start == ""
    assert santa_paula.follow_up_window_end == ""

    for record in (mount_carmel, santa_paula):
        payload = record.model_dump(mode="json")
        round_trip = CareLineReviewedRecord.model_validate(payload)
        assert round_trip == record


def test_21_unknown_lifecycle_metadata_still_fails_strict_validation():
    payload = complete_record().model_dump(mode="json")
    payload["unexpected_lifecycle_field"] = "nope"
    with pytest.raises(ValidationError):
        CareLineReviewedRecord.model_validate(payload)


def test_22_invalid_lifecycle_metadata_values_fail_validation():
    payload = complete_record().model_dump(mode="json")
    payload["lifecycle_status"] = "SOMETHING_ELSE"
    with pytest.raises(ValidationError, match="lifecycle_status"):
        CareLineReviewedRecord.model_validate(payload)
    payload = complete_record().model_dump(mode="json")
    payload["effective_follow_up_status"] = "tomorrow"
    with pytest.raises(ValidationError, match="effective_follow_up_status"):
        CareLineReviewedRecord.model_validate(payload)
