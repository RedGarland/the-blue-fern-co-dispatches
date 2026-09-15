from __future__ import annotations

import json
from pathlib import Path


EVENT_ROOT = Path("data/dispatches/ice/historical-events")
GAP_ROOT = Path("data/dispatches/ice/coverage-gaps")
RECOVERY_ROOT = Path("data/private-agent-handoff/discovery-recovery/ice/2026-09-14")


EXPECTED_EVENT_IDS = {
    "ice-2026-09-07-camp-east-montana-detention-disturbance",
    "ice-2026-09-08-irs-taxpayer-data-sharing-injunction",
    "ice-2026-09-08-state-college-transfer-flight-surge",
    "ice-2026-09-08-danville-workplace-arrests",
    "ice-2026-09-09-houston-hpd-policy-fatal-ice-shooting",
    "ice-2026-09-09-springfield-ice-officers-tro",
    "ice-2026-09-09-leavenworth-capacity-expansion",
    "ice-2026-09-10-fourth-circuit-mandatory-detention-ruling",
    "ice-2026-09-10-tacoma-mass-detainee-transfers",
    "ice-2026-09-13-otay-mesa-delayed-care-kidney-infection",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _event_files() -> list[Path]:
    return sorted(EVENT_ROOT.glob("2026-09-*/*.json"))


def test_approved_recovery_enters_historical_ledger_with_separate_recovery_date() -> None:
    events = [_load(path) for path in _event_files()]

    assert {event["event_id"] for event in events} == EXPECTED_EVENT_IDS
    assert {event["recovered_at"] for event in events} == {"2026-09-14"}
    assert {event["recovery_provenance"] for event in events} == {
        "operator_recovered_public_source_recall_evidence"
    }
    assert {event["original_production_discovery_lineage_present"] for event in events} == {False}
    assert {event["publication_eligible"] for event in events} == {False}
    assert {event["publication_approval"] for event in events} == {False}
    assert {event["publication_performed"] for event in events} == {False}
    assert {event["public_generation_authorized"] for event in events} == {False}
    assert {event["pages_authorized"] for event in events} == {False}


def test_historical_chronology_uses_event_date_not_recovery_date() -> None:
    events = {event["event_id"]: event for event in (_load(path) for path in _event_files())}

    assert events["ice-2026-09-07-camp-east-montana-detention-disturbance"]["event_date"] == "2026-09-07"
    assert events["ice-2026-09-08-irs-taxpayer-data-sharing-injunction"]["event_date"] == "2026-09-08"
    assert events["ice-2026-09-08-state-college-transfer-flight-surge"]["event_date"] == "2026-09-08"
    assert events["ice-2026-09-08-danville-workplace-arrests"]["event_date"] == "2026-09-08"
    assert events["ice-2026-09-09-houston-hpd-policy-fatal-ice-shooting"]["event_date"] == "2026-09-09"
    assert events["ice-2026-09-09-springfield-ice-officers-tro"]["event_date"] == "2026-09-09"
    assert events["ice-2026-09-09-leavenworth-capacity-expansion"]["event_date"] == "2026-09-09"
    assert events["ice-2026-09-10-fourth-circuit-mandatory-detention-ruling"]["event_date"] == "2026-09-10"
    assert events["ice-2026-09-10-tacoma-mass-detainee-transfers"]["event_date"] == "2026-09-10"
    assert events["ice-2026-09-13-otay-mesa-delayed-care-kidney-infection"]["event_date"] == "2026-09-13"
    assert all(event["event_date"] != event["recovered_at"] for event in events.values())


def test_observation_date_can_differ_from_event_date_without_backdating_source_availability() -> None:
    leavenworth = _load(
        EVENT_ROOT
        / "2026-09-09"
        / "ice-2026-09-09-leavenworth-capacity-expansion.json"
    )

    assert leavenworth["event_date"] == "2026-09-09"
    assert leavenworth["source_published_date"] == "2026-09-10"
    assert leavenworth["observation_date"] == "2026-09-10"
    assert leavenworth["recovered_at"] == "2026-09-14"


def test_duplicate_and_follow_up_relationships_are_explicit() -> None:
    events = {event["event_id"]: event for event in (_load(path) for path in _event_files())}

    canonical_ids = [event["canonical_event_id"] for event in events.values()]
    assert len(canonical_ids) == len(set(canonical_ids))
    assert events["ice-2026-09-09-houston-hpd-policy-fatal-ice-shooting"]["relationship_to_existing_event"] == (
        "follow_up_with_new_material_facts"
    )
    assert events["ice-2026-09-09-houston-hpd-policy-fatal-ice-shooting"]["related_underlying_event"] == (
        "fatal_ice_shooting_lorenzo_salgado_araujo"
    )
    assert {
        event["relationship_to_existing_event"]
        for event in events.values()
        if event["event_id"] != "ice-2026-09-09-houston-hpd-policy-fatal-ice-shooting"
    } == {"new_event"}


def test_observation_gap_records_transition_to_recovered() -> None:
    gaps = [_load(path) for path in sorted(GAP_ROOT.glob("2026-09-*.json"))]
    by_date = {gap["observation_date"]: gap for gap in gaps}

    assert set(by_date) == {"2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-13"}
    recovered = [by_date[day] for day in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-13")]
    assert {gap["observation_status"] for gap in recovered} == {"OBSERVED_WITH_FINDINGS"}
    assert {gap["backfill_status"] for gap in recovered} == {"RECOVERED"}
    assert {gap["recovered_at"] for gap in recovered} == {"2026-09-14"}
    assert all(gap["recovered_event_ids"] for gap in recovered)
    assert by_date["2026-09-08"]["observed_finding_count"] == 3
    assert by_date["2026-09-10"]["observed_finding_count"] == 3
    assert by_date["2026-09-08"]["transition_history"][-1]["new_backfill_status"] == "RECOVERED"
    assert by_date["2026-09-10"]["transition_history"][-1]["new_backfill_status"] == "RECOVERED"


def test_zero_qualifying_state_remains_distinct_from_incomplete_and_recovered() -> None:
    allowed_observation_states = {
        "OBSERVED_WITH_FINDINGS",
        "OBSERVED_ZERO_QUALIFYING",
        "OBSERVATION_INCOMPLETE",
    }
    allowed_backfill_states = {
        "BACKFILL_REQUIRED",
        "RECOVERY_IN_REVIEW",
        "RECOVERED",
        "BACKFILL_NOT_REQUIRED",
    }
    gaps = [_load(path) for path in sorted(GAP_ROOT.glob("2026-09-*.json"))]

    assert all(gap["observation_status"] in allowed_observation_states for gap in gaps)
    assert all(gap["backfill_status"] in allowed_backfill_states for gap in gaps)
    assert "OBSERVED_ZERO_QUALIFYING" in allowed_observation_states
    assert "OBSERVATION_INCOMPLETE" in allowed_observation_states


def test_sep08_sep10_recovery_audit_has_no_unresolved_qualifying_items() -> None:
    review = _load(RECOVERY_ROOT / "sep08-sep10-recovery-review.json")
    sep08 = _load(RECOVERY_ROOT / "2026-09-08-recovery-audit.json")
    sep10 = _load(RECOVERY_ROOT / "2026-09-10-recovery-audit.json")

    assert review["summary"]["approved"] == 6
    assert review["summary"]["deferred_for_corroboration"] == 0
    assert {item["review_disposition"] for item in review["items"]} == {"approved"}
    assert sep08["coverage_certification"] == "sufficient_for_bounded_recovery_with_findings"
    assert sep10["coverage_certification"] == "sufficient_for_bounded_recovery_with_findings"
    assert sep08["recommended_backfill_status"] == "RECOVERED"
    assert sep10["recommended_backfill_status"] == "RECOVERED"


def test_missing_monitor_artifact_did_not_fabricate_original_lineage() -> None:
    recovered = [
        _load(path)
        for path in _event_files()
        if _load(path).get("observation_date") in {"2026-09-08", "2026-09-10"}
    ]

    assert len(recovered) == 6
    assert all(event["original_production_discovery_lineage_present"] is False for event in recovered)
    assert all(event["recovery_provenance"] == "operator_recovered_public_source_recall_evidence" for event in recovered)


def test_rejected_or_private_recovery_does_not_auto_publish() -> None:
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")
    events = [_load(path) for path in _event_files()]
    original_intake_events = [
        event
        for event in events
        if event.get("source_recovery_artifact")
        == "data/private-agent-handoff/discovery-recovery/ice/2026-09-14/recovery-review-intake.json"
    ]

    assert intake["publication_eligible"] is False
    assert intake["publication_approval"] is False
    assert intake["publication_performed"] is False
    assert len(original_intake_events) == intake["summary"]["retained_for_review"]
    assert all(event["review_disposition"] == "approved" for event in events)
    assert all(not event["pages_authorized"] for event in events)
