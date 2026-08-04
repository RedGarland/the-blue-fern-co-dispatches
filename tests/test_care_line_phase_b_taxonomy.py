from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_evidence_review import load_reviewed_records
from bluefern_dispatches.care_line_record import (
    JURISDICTIONS_BY_CODE,
    CareLineReviewedRecord,
    deterministic_public_location_label,
    normalize_event_type,
    normalize_jurisdiction,
    normalize_service_line,
)


REPO = Path(__file__).resolve().parents[1]


def sample_record(**overrides) -> CareLineReviewedRecord:
    payload = {
        "producer_record_id": "care-line-phase-b-001",
        "record_status": "universal_event_ready",
        "review_status": "approved",
        "public_status": "public_approved",
        "universal_event_status": "universal_event_ready",
        "care_line_public_eligible": True,
        "source_url": "https://example.org/facility-change",
        "source_title": "Example facility changes care access",
        "source_publisher": "Example Publisher",
        "source_publication_date": "2026-07-01",
        "source_publication_date_precision": "day",
        "source_type": "publisher_article",
        "source_role": "clinic_operations_signal",
        "supporting_passage": "The source says the facility will change services.",
        "effective_evidence_text": "The source says the facility will change services.",
        "evidence_provenance_type": "reviewer_transcribed",
        "evidence_valid_for_universal_event": True,
        "raw_payload_hash": "phase-b-hash",
        "event_type": "facility_closure",
        "change_direction": "reduced",
        "permanence": "permanent",
        "announcement_date": "2026-07-01",
        "announcement_date_precision": "day",
        "effective_date": "2026-07-15",
        "effective_date_precision": "day",
        "date_precision": "day",
        "service_line": "unknown",
        "facility_name": "Example Facility",
        "provider_name": "Example Facility",
        "city": "Des Moines",
        "locality_name": "Des Moines",
        "state": "IA",
        "country_code": "US",
        "location_text": "Des Moines, IA",
        "geographic_scope": "city",
        "claim_summary": "The change reduces local healthcare access.",
        "evidence_level": "reported_story",
        "evidence_strength": "reviewed",
        "authority_level": "official",
        "is_primary_source": True,
        "verification_notes": "Directly supported by the source.",
        "access_consequences": ["LOSS_OF_LOCAL_ACCESS"],
        "field_provenance": {
            "facility_name": {
                "value": "Example Facility",
                "provenance_type": "source_explicit",
                "source_field": "title",
                "supporting_text": "Example facility changes care access",
                "confidence": 1.0,
                "review_status": "confirmed",
            }
        },
    }
    payload.update(overrides)
    return CareLineReviewedRecord.model_validate(payload)


def test_supports_exactly_56_us_jurisdictions() -> None:
    assert len(JURISDICTIONS_BY_CODE) == 56
    for code, row in JURISDICTIONS_BY_CODE.items():
        normalized = normalize_jurisdiction(code)
        assert normalized["code"] == code
        assert normalized["name"] == row["name"]
        assert normalized["type"] == row["type"]


@pytest.mark.parametrize(
    ("alias", "expected_code"),
    [
        ("District of Columbia", "DC"),
        ("Washington, DC", "DC"),
        ("Washington D.C.", "DC"),
        ("Puerto Rico", "PR"),
        ("PR", "PR"),
        ("U.S. Virgin Islands", "VI"),
        ("USVI", "VI"),
        ("Virgin Islands", "VI"),
        ("Northern Mariana Islands", "MP"),
        ("CNMI", "MP"),
        ("Commonwealth of the Northern Mariana Islands", "MP"),
        ("American Samoa", "AS"),
        ("AS", "AS"),
        ("Guam", "GU"),
    ],
)
def test_dc_and_territory_aliases_normalize(alias: str, expected_code: str) -> None:
    assert normalize_jurisdiction(alias)["code"] == expected_code


def test_washington_state_and_dc_do_not_collapse() -> None:
    assert normalize_jurisdiction("Washington")["code"] == "WA"
    assert normalize_jurisdiction("Washington, DC")["code"] == "DC"


def test_territory_locality_can_validate_without_county() -> None:
    record = sample_record(
        state="PR",
        city="San Juan",
        locality_name="San Juan",
        county="",
        county_equivalent_name="",
        location_text="San Juan, Puerto Rico",
    )
    assert record.jurisdiction_type == "TERRITORY"
    assert record.validation_issues() == []


def test_tribal_service_area_scope_is_supported() -> None:
    record = sample_record(
        state="NM",
        geographic_scope="tribal_service_area",
        tribal_service_area="Navajo Nation service area",
        city="",
        locality_name="",
        location_text="Navajo Nation service area, New Mexico",
    )
    assert record.geographic_scope_canonical == "TRIBAL_SERVICE_AREA"
    assert record.validation_issues() == []


def test_legacy_service_expansion_is_readable_but_not_publishable_without_prior_loss_link() -> None:
    record = sample_record(
        event_type="service_expansion",
        event_type_raw="service_expansion",
        service_line="urgent_care",
        permanence="temporary_or_unknown",
        care_line_public_eligible=True,
        claim_summary="The provider is adding urgent care appointments.",
        supporting_passage="The clinic is expanding urgent care hours and appointments.",
    )
    assert record.canonical_event_type == "SERVICE_RESTORATION"
    assert record.service_expansion_requires_prior_loss_link is True
    assert "service_expansion_requires_prior_loss_link" in {issue.code for issue in record.validation_issues()}
    assert record.universal_event_eligible is False


def test_legacy_service_expansion_can_qualify_when_linked_to_prior_loss() -> None:
    record = sample_record(
        event_type="service_expansion",
        event_type_raw="service_expansion",
        service_line="urgent_care",
        permanence="temporary_or_unknown",
        prior_access_loss_event_id="event_prior_loss_urgent_care",
        claim_summary="The provider is restoring urgent care access after a previous cut.",
        supporting_passage="The clinic said the new urgent care schedule restores access after last month's service reduction.",
    )
    assert record.canonical_event_type == "SERVICE_RESTORATION"
    assert record.service_expansion_requires_prior_loss_link is False
    assert "service_expansion_requires_prior_loss_link" not in {issue.code for issue in record.validation_issues()}


def test_event_taxonomy_distinguishes_closure_relocation_and_temporary_suspension() -> None:
    assert normalize_event_type("facility_closure")[1] == "FACILITY_CLOSURE"
    assert normalize_event_type("facility_relocation")[1] == "RELOCATION"
    assert normalize_event_type("temporary_facility_suspension")[1] == "TEMPORARY_FACILITY_CLOSURE"


def test_event_taxonomy_distinguishes_reduced_hours_and_capacity() -> None:
    assert normalize_event_type("hours_reduction")[1] == "REDUCED_HOURS"
    assert normalize_event_type("capacity_reduction")[1] == "REDUCED_CAPACITY"


def test_service_line_taxonomy_normalizes_common_aliases() -> None:
    assert normalize_service_line("ER")[1] == "EMERGENCY"
    assert normalize_service_line("labor & delivery")[1] == "LABOR_AND_DELIVERY"
    assert normalize_service_line("mental health")[1] == "BEHAVIORAL_HEALTH"
    assert normalize_service_line("EMS")[1] == "AMBULANCE_EMS"


def test_future_effective_dates_remain_distinguishable() -> None:
    record = sample_record(effective_date="2026-12-01", publication_date="2026-08-04", publication_date_precision="day")
    assert record.effective_date == "2026-12-01"
    assert record.publication_date == "2026-08-04"


def test_reopening_can_link_to_prior_loss_event() -> None:
    record = sample_record(
        event_type="facility_reopening",
        prior_access_loss_event_id="event_prior_loss_123",
        permanence="temporary_or_unknown",
    )
    assert record.canonical_event_type == "REOPENING"
    assert record.prior_access_loss_event_id == "event_prior_loss_123"


def test_authoritative_single_source_approval_is_allowed() -> None:
    record = sample_record()
    assert record.workflow_state == "APPROVED"
    assert record.verification_state in {"SOURCE_VERIFIED", "AUTHORITY_CONFIRMED"}
    assert record.validation_issues() == []


def test_corroborated_event_state_is_supported() -> None:
    record = sample_record(review_status="reviewed", is_primary_source=False)
    assert record.verification_state == "CORROBORATED"


def test_insufficient_evidence_fails_closed() -> None:
    record = sample_record(
        supporting_passage="",
        evidence_valid_for_universal_event=False,
        verification_state="INSUFFICIENT_EVIDENCE",
        workflow_state="APPROVED",
    )
    assert "approved_with_insufficient_evidence" in {issue.code for issue in record.validation_issues()}


def test_unknown_taxonomy_value_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported event_type"):
        sample_record(event_type="made_up_event")
    with pytest.raises(ValueError, match="unsupported service_line"):
        sample_record(service_line="made_up_service", event_type="service_suspension")


def test_longer_travel_distance_claim_requires_source_support() -> None:
    record = sample_record(access_consequences=["LONGER_TRAVEL_DISTANCE"], claim_summary="The closure reduces local access.")
    assert "travel_claim_not_sourced" in {issue.code for issue in record.validation_issues()}


def test_private_provenance_paths_fail_closed() -> None:
    with pytest.raises(ValueError, match="private reviewed_record_path"):
        sample_record(reviewed_record_path=r"C:\private\reviewed_records.json")


def test_june_reviewed_records_remain_backward_compatible() -> None:
    records = load_reviewed_records(REPO / "data" / "dispatches" / "care-line" / "reviewed" / "2026-05-23" / "reviewed_records.json")
    assert records
    assert any(record.event_type == "facility_closure" for record in records)
    assert all(record.country_code == "US" for record in records)


def test_public_location_labels_are_deterministic() -> None:
    label = deterministic_public_location_label(
        facility_name="Example Clinic",
        locality="Phoenix",
        jurisdiction_display="Arizona",
    )
    assert label == "Example Clinic, Phoenix, Arizona"


def test_territory_public_location_priority_prefers_locality_over_county_equivalent() -> None:
    label = deterministic_public_location_label(
        facility_name="San Juan Community Clinic",
        locality="San Juan",
        county_equivalent="San Juan Municipio",
        jurisdiction_display="Puerto Rico",
    )
    assert label == "San Juan Community Clinic, San Juan, Puerto Rico"


def test_territory_public_location_priority_uses_island_without_fabricated_county() -> None:
    label = deterministic_public_location_label(
        facility_name="Charlotte Amalie Clinic",
        jurisdiction_display="U.S. Virgin Islands",
        island="St. Thomas",
    )
    assert label == "Charlotte Amalie Clinic, St. Thomas, U.S. Virgin Islands"


@pytest.mark.parametrize(
    ("facility_name", "locality", "island", "jurisdiction_display", "expected"),
    [
        ("Dededo Health Center", "Dededo", "", "Guam", "Dededo Health Center, Dededo, Guam"),
        ("Saipan Family Clinic", "", "Saipan", "Northern Mariana Islands", "Saipan Family Clinic, Saipan, Northern Mariana Islands"),
        ("Pago Pago Outreach Clinic", "Pago Pago", "Tutuila", "American Samoa", "Pago Pago Outreach Clinic, Pago Pago, American Samoa"),
    ],
)
def test_territory_location_rules_cover_multiple_territories(
    facility_name: str,
    locality: str,
    island: str,
    jurisdiction_display: str,
    expected: str,
) -> None:
    label = deterministic_public_location_label(
        facility_name=facility_name,
        locality=locality,
        island=island,
        jurisdiction_display=jurisdiction_display,
    )
    assert label == expected


def test_tribal_nation_label_is_used_for_nation_specific_facility_event() -> None:
    label = deterministic_public_location_label(
        facility_name="Cherokee Nation Outpatient Clinic",
        tribal_nation="Cherokee Nation",
        jurisdiction_display="Oklahoma",
    )
    assert label == "Cherokee Nation Outpatient Clinic, Cherokee Nation, Oklahoma"


def test_tribal_service_area_label_is_used_for_multi_community_operational_region() -> None:
    label = deterministic_public_location_label(
        tribal_nation="Cherokee Nation",
        tribal_service_area="Northern Plains tribal health service area",
        jurisdiction_display="South Dakota",
    )
    assert label == "Northern Plains tribal health service area, South Dakota"


def test_locality_still_outranks_tribal_labels_when_supported() -> None:
    label = deterministic_public_location_label(
        facility_name="River Valley Tribal Clinic",
        locality="Browning",
        tribal_nation="Blackfeet Nation",
        tribal_service_area="Blackfeet service area",
        jurisdiction_display="Montana",
    )
    assert label == "River Valley Tribal Clinic, Browning, Montana"

