from __future__ import annotations

import json
import re
from pathlib import Path

from bluefern_dispatches.coverage_gap_controller import BackfillStatus, GapReasonCode, evaluate_dispatch_date


RECOVERY_ROOT = Path("data/private-agent-handoff/discovery-recovery/care-line/2026-09-24")
ACTIVE_REVIEW_PATHS = (
    Path("data/dispatches/care-line/review/candidate-registry.json"),
    Path("data/dispatches/care-line/review/current-review-queue.json"),
    Path("data/dispatches/care-line/review/current-review-backlog.json"),
    Path("data/dispatches/care-line/review/current-failed-extractions.json"),
)
APPROVED_EVENT_IDS = {
    "care-line-2026-06-30-southern-oregon-surgery-center-closure",
    "care-line-2026-09-14-phi-red-bluff-air-medical-base-suspension",
    "care-line-2026-09-15-minnesota-hotel-crisis-respite-placement-end",
    "care-line-2026-09-17-choate-psychiatric-bed-elimination-plan-challenged",
    "care-line-2026-09-23-pearl-youth-residence-scheduled-closure",
    "care-line-2026-09-30-fitzgibbon-inpatient-labor-delivery-closure",
}
REJECTED_ITEM_IDS = {
    "care-line-2026-09-17-west-suburban-weiss-reopening-path",
    "care-line-2026-09-15-burdett-birth-center-closure-challenge",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_care_sep15_sep24_alt_recovery_package_is_private_pending_review_only() -> None:
    audit = _load(RECOVERY_ROOT / "observation-window-audit.json")
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")

    assert audit["accounting"] == {
        "accounting_delta": 0,
        "candidates": 8,
        "conflicts": 0,
        "duplicates": 2,
        "evidence_exhausted": 39,
        "exclusions": 4,
        "frozen_identities": 53,
        "resolution_occurrences": {
            "ALT_CANDIDATE_CONFIRMED": 23,
            "ALT_DUPLICATE_CONFIRMED": 4,
            "ALT_EXCLUSION_CONFIRMED": 23,
            "EVIDENCE_EXHAUSTED": 108,
        },
        "total": 53,
    }
    assert intake["candidate_identity_count"] == 8
    assert len(intake["items"]) == 8
    assert len(audit["candidate_identities"]) == 8
    assert len(audit["terminal_noncandidate_dispositions"]["exclusions"]) == 4
    assert len(audit["terminal_noncandidate_dispositions"]["duplicates"]) == 2
    assert len(audit["evidence_exhausted_identities"]) == 39
    assert audit["conflicting_evidence_identities"] == []

    assert {item["review_status"] for item in intake["items"]} == {"pending_review"}
    assert {item["review_retention_disposition"] for item in intake["items"]} == {"retained_for_review"}
    assert {item["recovery_provenance"] for item in intake["items"]} == {
        "operator_recovered_external_source_evidence"
    }
    assert all(item["production_artifact_present"] is False for item in intake["items"])
    assert all(
        item["publication_eligible"] is False
        and item["publication_approval"] is False
        and item["publication_performed"] is False
        and item["public_generation_authorized"] is False
        and item["pages_authorized"] is False
        for item in intake["items"]
    )
    assert all(intake[flag] is False for flag in (
        "publication_eligible",
        "publication_approval",
        "publication_performed",
        "public_generation_authorized",
        "pages_authorized",
    ))
    assert all(audit["authority_flags"][flag] is False for flag in (
        "manual_production_task_triggered",
        "original_production_discovery_lineage_fabricated",
        "publication_eligible",
        "publication_approval",
        "publication_performed",
        "public_generation_authorized",
        "pages_authorized",
        "historical_event_records_created",
        "editorial_review_created",
    ))


def test_care_sep15_sep24_alt_recovery_keeps_noncandidates_out_of_review_intake() -> None:
    audit = _load(RECOVERY_ROOT / "observation-window-audit.json")
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")

    intake_fingerprints = {item["source_evidence_fingerprint"] for item in intake["items"]}
    exclusion_fingerprints = {
        item["source_evidence_fingerprint"]
        for item in audit["terminal_noncandidate_dispositions"]["exclusions"]
    }
    duplicate_fingerprints = {
        item["source_evidence_fingerprint"]
        for item in audit["terminal_noncandidate_dispositions"]["duplicates"]
    }
    exhausted_fingerprints = {
        item["source_evidence_fingerprint"]
        for item in audit["evidence_exhausted_identities"]
    }

    assert intake_fingerprints.isdisjoint(exclusion_fingerprints)
    assert intake_fingerprints.isdisjoint(duplicate_fingerprints)
    assert intake_fingerprints.isdisjoint(exhausted_fingerprints)
    assert len(intake_fingerprints | exclusion_fingerprints | duplicate_fingerprints | exhausted_fingerprints) == 53


def test_care_sep15_sep24_exhaustion_keeps_dates_backfill_required() -> None:
    candidate_dates = {
        "2026-09-15": 2,
        "2026-09-16": 2,
        "2026-09-17": 4,
        "2026-09-18": 1,
        "2026-09-20": 0,
        "2026-09-21": 1,
        "2026-09-22": 2,
        "2026-09-23": 1,
        "2026-09-24": 1,
    }

    audit = _load(RECOVERY_ROOT / "observation-window-audit.json")
    exhausted_by_date = {row["date"]: row["exhausted_or_conflicting"] for row in audit["per_date"]}
    for observation_date, candidate_count in candidate_dates.items():
        result = evaluate_dispatch_date(Path("."), "care-line", observation_date, evaluated_at="2026-09-25T09:00:00Z")
        assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
        assert GapReasonCode.HISTORICAL_EVIDENCE_INCOMPLETE in result.reason_codes
        assert result.recovery_candidate_refs == ()
        if candidate_count:
            assert "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24/recovery-review-intake.json" in result.evidence_refs
        assert "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24/observation-window-audit.json" in result.evidence_refs
        assert exhausted_by_date[observation_date] > 0


def test_care_sep15_sep24_package_does_not_create_active_review_state() -> None:
    assert all(not path.exists() for path in ACTIVE_REVIEW_PATHS)


def test_care_sep15_sep24_candidate_dates_are_precise_or_explicitly_unknown() -> None:
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")
    exact_date = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    southern_oregon = next(
        item
        for item in intake["items"]
        if item["item_id"] == "care-line-2026-09-16-southern-oregon-surgery-center-closure"
    )
    assert southern_oregon["event_date"] == "2026-06-30"
    assert southern_oregon["source_publication_date"] is None
    assert southern_oregon["source_published_date"] is None
    assert southern_oregon["source_publication_date_uncertainty"]

    for item in intake["items"]:
        assert exact_date.match(item["event_date"])
        for key in ("source_publication_date", "source_published_date"):
            value = item.get(key)
            assert value is None or exact_date.match(value)
            assert value != "2026"


def test_care_sep15_sep24_editorial_review_disposes_exactly_eight_candidates() -> None:
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")

    assert len(review["items"]) == 8
    assert {item["item_id"] for item in review["items"]} == {item["item_id"] for item in intake["items"]}
    assert review["approved_count"] == 6
    assert review["rejected_count"] == 2
    assert review["rejected_duplicate_count"] == 0
    assert review["excluded_existing_recovery_count"] == 0
    assert review["deferred_for_corroboration_count"] == 0
    assert review["total_count"] == 8
    assert {item["item_id"] for item in review["items"] if item["disposition"] == "rejected"} == REJECTED_ITEM_IDS
    assert all(review[flag] is False for flag in (
        "publication_eligible",
        "publication_approval",
        "publication_performed",
        "public_generation_authorized",
        "pages_authorized",
    ))


def test_care_sep15_sep24_approved_nonduplicates_have_private_historical_events_only() -> None:
    event_root = Path("data/dispatches/care-line/historical-events")
    event_paths = sorted(event_root.glob("*/care-line-2026-*-*.json"))
    package_records = [
        _load(path)
        for path in event_paths
        if _load(path).get("source_recovery_artifact")
        == "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24/recovery-review-intake.json"
    ]

    assert {record["event_id"] for record in package_records} == APPROVED_EVENT_IDS
    assert {record["source_recovery_item_id"] for record in package_records}.isdisjoint(REJECTED_ITEM_IDS)
    assert all(record["review_disposition"] == "approved" for record in package_records)
    assert all(record["original_production_discovery_lineage_present"] is False for record in package_records)
    assert all(record["production_artifact_present"] is False for record in package_records)
    assert all(record["uncertainty_preserved"] is True for record in package_records)
    assert all(
        record["publication_eligible"] is False
        and record["publication_approval"] is False
        and record["publication_performed"] is False
        and record["public_generation_authorized"] is False
        and record["pages_authorized"] is False
        for record in package_records
    )


def test_care_sep15_sep24_editorial_records_preserve_key_date_distinctions() -> None:
    southern = _load(
        Path(
            "data/dispatches/care-line/historical-events/2026-06-30/"
            "care-line-2026-06-30-southern-oregon-surgery-center-closure.json"
        )
    )
    choate = _load(
        Path(
            "data/dispatches/care-line/historical-events/2026-09-17/"
            "care-line-2026-09-17-choate-psychiatric-bed-elimination-plan-challenged.json"
        )
    )
    pearl = _load(
        Path(
            "data/dispatches/care-line/historical-events/2026-09-23/"
            "care-line-2026-09-23-pearl-youth-residence-scheduled-closure.json"
        )
    )
    fitzgibbon = _load(
        Path(
            "data/dispatches/care-line/historical-events/2026-09-18/"
            "care-line-2026-09-30-fitzgibbon-inpatient-labor-delivery-closure.json"
        )
    )

    assert southern["source_published_date"] is None
    assert southern["source_publication_date_uncertainty"]
    assert "Periop Leader Network" in southern["event_date_basis"]
    assert any(source.get("supports_event_date") is True for source in southern["sources"])
    assert any(source.get("supports_closure_status") is True for source in southern["sources"])
    assert not all(source.get("source_url") == "https://www.sosurgi.com/" for source in southern["sources"])
    assert choate["effective_date"] is None
    assert "not proven completed" in choate["materiality"]
    assert pearl["event_date"] == "2026-09-23"
    assert pearl["event_id"] == "care-line-2026-09-23-pearl-youth-residence-scheduled-closure"
    assert pearl["effective_date"] is None
    assert "WARN notice lists Nov. 21 for layoffs" in pearl["effective_date_uncertainty"]
    assert "exact facility closure date is not established" in pearl["materiality"]
    assert not Path(
        "data/dispatches/care-line/historical-events/2026-09-23/"
        "care-line-2026-11-21-pearl-youth-residence-closure.json"
    ).exists()
    assert fitzgibbon["event_date"] == "2026-09-18"
    assert fitzgibbon["effective_date"] == "2026-09-30"
    assert "prenatal/postpartum" in fitzgibbon["materiality"]


def test_care_sep15_sep24_sep19_remains_untouched() -> None:
    assert not Path("data/dispatches/care-line/historical-events/2026-09-19").exists()
    assert not Path("data/dispatches/care-line/coverage-gaps/2026-09-19.json").exists()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_intake_package(root: Path, *, exhausted: int) -> None:
    recovery_root = root / "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24"
    _write_json(
        recovery_root / "recovery-review-intake.json",
        {
            "provenance_class": "operator_recovered_external_source_evidence",
            "items": [
                {
                    "dispatch": "care-line",
                    "item_id": "care-line-reviewable",
                    "observed_dates": ["2026-09-15"],
                    "review_status": "pending_review",
                    "production_artifact_present": False,
                }
            ],
        },
    )
    _write_json(
        recovery_root / "observation-window-audit.json",
        {
            "per_date": [
                {
                    "date": "2026-09-15",
                    "coverage_result": "EVIDENCE_EXHAUSTED_REMAINS" if exhausted else "ALL_RECOVERY_IDENTITIES_ACCOUNTED",
                    "exhausted_or_conflicting": exhausted,
                }
            ]
        },
    )


def _write_recovered_event(root: Path) -> None:
    _write_json(
        root / "data/dispatches/care-line/historical-events/2026-09-15/care-line-reviewable.json",
        {
            "event_id": "care-line-reviewable",
            "observation_date": "2026-09-15",
            "recovery_provenance": "operator_recovered_external_source_evidence",
            "original_production_discovery_lineage_present": False,
        },
    )


def test_pending_candidate_with_exhaustion_stays_backfill_required(tmp_path: Path) -> None:
    _write_intake_package(tmp_path, exhausted=1)

    result = evaluate_dispatch_date(tmp_path, "care-line", "2026-09-15", evaluated_at="2026-09-25T09:00:00Z")

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert GapReasonCode.HISTORICAL_EVIDENCE_INCOMPLETE in result.reason_codes
    assert result.recovery_candidate_refs == ()
    assert "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24/recovery-review-intake.json" in result.evidence_refs


def test_pending_candidate_without_exhaustion_remains_recovery_in_review(tmp_path: Path) -> None:
    _write_intake_package(tmp_path, exhausted=0)

    result = evaluate_dispatch_date(tmp_path, "care-line", "2026-09-15", evaluated_at="2026-09-25T09:00:00Z")

    assert result.backfill_status == BackfillStatus.RECOVERY_IN_REVIEW
    assert result.recovery_candidate_refs == (
        "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24/recovery-review-intake.json",
    )


def test_recovered_event_with_exhaustion_stays_backfill_required(tmp_path: Path) -> None:
    _write_intake_package(tmp_path, exhausted=1)
    _write_recovered_event(tmp_path)

    result = evaluate_dispatch_date(tmp_path, "care-line", "2026-09-15", evaluated_at="2026-09-25T09:00:00Z")

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert GapReasonCode.HISTORICAL_EVIDENCE_INCOMPLETE in result.reason_codes


def test_fully_accounted_recovered_event_still_recovers(tmp_path: Path) -> None:
    _write_intake_package(tmp_path, exhausted=0)
    _write_recovered_event(tmp_path)

    result = evaluate_dispatch_date(tmp_path, "care-line", "2026-09-15", evaluated_at="2026-09-25T09:00:00Z")

    assert result.backfill_status == BackfillStatus.RECOVERED
    assert result.recovered_event_ids == ("care-line-reviewable",)
