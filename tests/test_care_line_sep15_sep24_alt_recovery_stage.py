from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.coverage_gap_controller import BackfillStatus, evaluate_dispatch_date


RECOVERY_ROOT = Path("data/private-agent-handoff/discovery-recovery/care-line/2026-09-24")
ACTIVE_REVIEW_PATHS = (
    Path("data/dispatches/care-line/review/candidate-registry.json"),
    Path("data/dispatches/care-line/review/current-review-queue.json"),
    Path("data/dispatches/care-line/review/current-review-backlog.json"),
    Path("data/dispatches/care-line/review/current-failed-extractions.json"),
)


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


def test_care_sep15_sep24_candidate_intake_marks_applicable_dates_in_review() -> None:
    candidate_dates = {
        "2026-09-15": 2,
        "2026-09-16": 2,
        "2026-09-17": 4,
        "2026-09-18": 1,
        "2026-09-21": 1,
        "2026-09-22": 2,
        "2026-09-23": 1,
        "2026-09-24": 1,
    }

    for observation_date in candidate_dates:
        result = evaluate_dispatch_date(Path("."), "care-line", observation_date, evaluated_at="2026-09-25T09:00:00Z")
        assert result.backfill_status == BackfillStatus.RECOVERY_IN_REVIEW
        assert result.recovery_candidate_refs == (
            "data/private-agent-handoff/discovery-recovery/care-line/2026-09-24/recovery-review-intake.json",
        )

    sep20 = evaluate_dispatch_date(Path("."), "care-line", "2026-09-20", evaluated_at="2026-09-25T09:00:00Z")
    assert sep20.backfill_status != BackfillStatus.RECOVERY_IN_REVIEW


def test_care_sep15_sep24_package_does_not_create_active_review_or_event_state() -> None:
    assert all(not path.exists() for path in ACTIVE_REVIEW_PATHS)
    assert not (RECOVERY_ROOT / "editorial-review.json").exists()
    assert not Path("data/dispatches/care-line/historical-events/2026-09-15").exists()
    assert not Path("data/dispatches/care-line/historical-events/2026-09-24").exists()
