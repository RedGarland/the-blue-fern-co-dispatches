from __future__ import annotations

from pathlib import Path

from bluefern_dispatches.care_line_effective_date_follow_up import (
    build_follow_up_query,
    care_line_effective_follow_up_status,
    care_line_event_identity,
    care_line_event_instance_id,
    care_line_follow_up_window,
    care_line_lifecycle_status,
)
from bluefern_dispatches.care_line_record import CareLineReviewedRecord


def timed_payload(**overrides) -> dict:
    payload = {
        "producer_record_id": "care-line-effective-date-001",
        "record_status": "universal_event_ready",
        "review_status": "approved",
        "public_status": "public_approved",
        "universal_event_status": "universal_event_ready",
        "care_line_public_eligible": True,
        "source_url": "https://newsroom.ohiohealth.com/ohiohealth-announces-updates-to-maternity-and-womens-health-services-at-ohiohealth-grady-memorial-hospital/",
        "source_title": "OhioHealth Announces Updates to Maternity and Women's Health Services at OhioHealth Grady Memorial Hospital",
        "source_publisher": "OhioHealth",
        "source_publication_date": "2026-06-10",
        "source_type": "official_notice",
        "source_role": "hospital_service_update",
        "supporting_passage": "OhioHealth will discontinue inpatient maternity services at OhioHealth Grady Memorial Hospital effective Friday, July 31, 2026.",
        "effective_evidence_text": "OhioHealth will discontinue inpatient maternity services at OhioHealth Grady Memorial Hospital effective Friday, July 31, 2026.",
        "evidence_provenance_type": "source_explicit",
        "evidence_valid_for_universal_event": True,
        "recommended_status": "universal_event_ready",
        "raw_payload_hash": "care-line-effective-date-001",
        "event_type": "service_suspension",
        "event_type_raw": "service_suspension",
        "change_direction": "reduced",
        "permanence": "temporary_or_unknown",
        "announcement_date": "2026-06-10",
        "effective_date": "2026-07-31",
        "date_precision": "day",
        "service_line": "maternity",
        "service_line_raw": "maternity",
        "facility_name": "OhioHealth Grady Memorial Hospital",
        "provider_name": "OhioHealth Grady Memorial Hospital",
        "facility_type": "hospital",
        "city": "Delaware",
        "county": "Delaware County",
        "state": "OH",
        "country_code": "US",
        "location_text": "Delaware, OH",
        "geographic_scope": "county_equivalent",
        "claim_summary": "Inpatient maternity services will discontinue effective July 31, 2026.",
        "evidence_level": "direct_reporting",
        "evidence_strength": "high",
        "is_primary_source": True,
    }
    payload.update(overrides)
    return payload


def context_only_payload(**overrides) -> dict:
    payload = {
        "producer_record_id": "care-line-effective-date-context-001",
        "record_status": "care_line_only",
        "review_status": "reviewed",
        "public_status": "care_line_only",
        "universal_event_status": "care_line_only",
        "care_line_public_eligible": True,
        "source_url": "https://example.org/context-only",
        "source_title": "Background on care access",
        "source_publisher": "Example News",
        "source_publication_date": "2026-06-10",
        "source_type": "local_news",
        "source_role": "context_only",
        "supporting_passage": "This item is background context and does not describe an operational access change.",
        "effective_evidence_text": "This item is background context and does not describe an operational access change.",
        "evidence_provenance_type": "source_explicit",
        "evidence_valid_for_universal_event": False,
        "recommended_status": "care_line_only",
        "raw_payload_hash": "care-line-effective-date-context-001",
        "event_type": "resource_context",
        "event_type_raw": "resource_context",
        "change_direction": "",
        "permanence": "",
        "announcement_date": "",
        "effective_date": "",
        "date_precision": "day",
        "service_line": "",
        "service_line_raw": "",
        "facility_name": "",
        "provider_name": "",
        "city": "Delaware",
        "state": "OH",
        "country_code": "US",
        "location_text": "Delaware, OH",
        "geographic_scope": "county_equivalent",
        "claim_summary": "Background context only.",
        "evidence_level": "direct_reporting",
        "evidence_strength": "low",
        "is_primary_source": False,
    }
    payload.update(overrides)
    return payload


def make_record(**overrides) -> CareLineReviewedRecord:
    return CareLineReviewedRecord.model_validate(timed_payload(**overrides))


def test_grady_effective_date_announcement_retains_pending_state_and_window():
    record = make_record()
    assert care_line_event_identity(record).startswith("care_line_event_")
    assert care_line_lifecycle_status(record) == "PENDING_EFFECTIVE_DATE"
    assert care_line_event_instance_id(record).startswith(care_line_event_identity(record) + "_")
    assert care_line_follow_up_window(record) == ("2026-07-17", "2026-08-07")


def test_follow_up_after_effective_date_matches_same_event_and_advances_status():
    announcement = make_record()
    follow_up = make_record(
        producer_record_id="care-line-effective-date-002",
        source_title="OhioHealth Grady Memorial Hospital maternity services discontinue today as scheduled",
        source_publication_date="2026-07-31",
        supporting_passage="OhioHealth says inpatient maternity services discontinued today as scheduled.",
        effective_evidence_text="OhioHealth says inpatient maternity services discontinued today as scheduled.",
    )
    assert care_line_event_identity(announcement) == care_line_event_identity(follow_up)
    assert care_line_event_instance_id(announcement) != care_line_event_instance_id(follow_up)
    assert care_line_lifecycle_status(follow_up) == "EFFECTIVE"
    assert care_line_effective_follow_up_status(follow_up, reference_date="2026-07-31") == "effective_date_reached"
    query = build_follow_up_query(announcement)
    assert query["event_identity"] == care_line_event_identity(announcement)
    assert query["event_instance_id"].startswith(query["event_identity"] + "_")


def test_follow_up_delay_cancel_and_restore_have_distinct_lifecycle_outcomes():
    delayed = make_record(
        producer_record_id="care-line-effective-date-003",
        source_title="OhioHealth delays Grady Memorial maternity discontinuation until September 1",
        source_publication_date="2026-06-24",
        supporting_passage="OhioHealth delayed the maternity discontinuation until September 1 after staffing concerns.",
        effective_evidence_text="OhioHealth delayed the maternity discontinuation until September 1 after staffing concerns.",
        effective_date="2026-09-01",
    )
    cancelled = make_record(
        producer_record_id="care-line-effective-date-004",
        source_title="OhioHealth cancels Grady Memorial maternity discontinuation plan",
        source_publication_date="2026-06-28",
        supporting_passage="OhioHealth withdrew the planned maternity discontinuation after community feedback.",
        effective_evidence_text="OhioHealth withdrew the planned maternity discontinuation after community feedback.",
        effective_date="2026-07-31",
    )
    restored = make_record(
        producer_record_id="care-line-effective-date-005",
        event_type="facility_reopening",
        event_type_raw="facility_reopening",
        source_title="OhioHealth restores Grady Memorial maternity service access",
        source_publication_date="2026-08-14",
        supporting_passage="OhioHealth restored maternity service access at Grady Memorial Hospital.",
        effective_evidence_text="OhioHealth restored maternity service access at Grady Memorial Hospital.",
        effective_date="2026-08-14",
    )
    assert care_line_event_identity(delayed) == care_line_event_identity(announcement := make_record())
    assert care_line_lifecycle_status(delayed) == "DELAYED"
    assert care_line_lifecycle_status(cancelled) == "CANCELLED"
    assert care_line_lifecycle_status(restored) == "RESTORED"


def test_same_facility_different_service_and_similarly_named_facilities_do_not_merge():
    maternity = make_record()
    ed = make_record(
        producer_record_id="care-line-effective-date-006",
        source_title="OhioHealth Grady Memorial Hospital emergency department update",
        service_line="emergency_care",
        service_line_raw="emergency_care",
        event_type="service_suspension",
        event_type_raw="service_suspension",
        supporting_passage="Emergency care at the hospital changed separately from maternity care.",
        effective_evidence_text="Emergency care at the hospital changed separately from maternity care.",
        effective_date="2026-07-31",
    )
    similarly_named = make_record(
        producer_record_id="care-line-effective-date-007",
        facility_name="Grady Memorial Hospital",
        provider_name="Grady Memorial Hospital",
        city="Atlanta",
        county="Fulton County",
        state="GA",
        location_text="Atlanta, GA",
        supporting_passage="A different Grady Memorial Hospital in another location announced a closure.",
        effective_evidence_text="A different Grady Memorial Hospital in another location announced a closure.",
    )
    assert care_line_event_identity(maternity) != care_line_event_identity(ed)
    assert care_line_event_identity(maternity) != care_line_event_identity(similarly_named)


def test_no_effective_date_leaves_existing_behavior_unchanged():
    record = CareLineReviewedRecord.model_validate(context_only_payload())
    assert care_line_event_identity(record) == ""
    assert care_line_event_instance_id(record) == ""
    assert care_line_lifecycle_status(record) == ""
    assert care_line_follow_up_window(record) == ("", "")
    assert record.universal_event_eligible is False
