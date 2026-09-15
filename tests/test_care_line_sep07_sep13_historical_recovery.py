from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.coverage_gap_controller import BackfillStatus, ObservationStatus, evaluate_dispatch_date


RECOVERY_ROOT = Path("data/private-agent-handoff/discovery-recovery/care-line/2026-09-15")
EVENT_ROOT = Path("data/dispatches/care-line/historical-events")
GAP_ROOT = Path("data/dispatches/care-line/coverage-gaps")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_care_sep07_sep13_private_recovery_artifacts_are_bounded_and_non_public() -> None:
    audit = _load(RECOVERY_ROOT / "observation-window-audit.json")
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")
    daily = [_load(RECOVERY_ROOT / "audits" / f"2026-09-{day:02d}.json") for day in range(7, 14)]

    assert audit["manual_production_task_triggered"] is False
    assert intake["manual_production_task_triggered"] is False
    assert {row["observation_date"] for row in daily} == {
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
    }
    assert intake["provenance_class"] == "operator_recovered_public_source_evidence"
    assert intake["original_production_discovery_lineage_fabricated"] is False
    assert len(intake["items"]) == 5
    assert all(item["production_artifact_present"] is False for item in intake["items"])
    assert all(flag is False for flag in (
        audit["publication_eligible"],
        audit["publication_approval"],
        audit["publication_performed"],
        audit["public_generation_authorized"],
        audit["pages_authorized"],
        intake["publication_eligible"],
        intake["publication_approval"],
        intake["publication_performed"],
        intake["public_generation_authorized"],
        intake["pages_authorized"],
        review["publication_eligible"],
        review["publication_approval"],
        review["publication_performed"],
        review["public_generation_authorized"],
        review["pages_authorized"],
    ))
    assert all(row["manual_production_task_triggered"] is False for row in daily)
    assert all(row["publication_performed"] is False for row in daily)


def test_care_partial_success_does_not_certify_complete_observation_windows() -> None:
    audit = _load(RECOVERY_ROOT / "observation-window-audit.json")
    by_date = {row["observation_date"]: row for row in audit["observation_dates"]}

    for date in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"):
        assert by_date[date]["receipt_statuses"] == ["partial_success", "partial_success", "partial_success"]
        assert by_date[date]["surviving_collection_artifacts_complete"] is False

    assert by_date["2026-09-07"]["final_observation_status"] == "OBSERVATION_INCOMPLETE"
    assert by_date["2026-09-08"]["final_observation_status"] == "OBSERVED_WITH_FINDINGS"
    assert by_date["2026-09-09"]["duplicate_only"] is True


def test_care_sep07_sep13_coverage_gap_records_resolve_controller_state() -> None:
    expected = {
        "2026-09-07": (ObservationStatus.OBSERVATION_INCOMPLETE, BackfillStatus.BACKFILL_REQUIRED, ()),
        "2026-09-08": (
            ObservationStatus.OBSERVED_WITH_FINDINGS,
            BackfillStatus.RECOVERED,
            (
                "care-line-2026-09-08-wcchc-clinic-closure-lowell",
                "care-line-2026-09-08-cecil-county-birthing-center-gap",
                "care-line-2026-09-08-medstar-postpartum-bed-cuts",
            ),
        ),
        "2026-09-09": (ObservationStatus.OBSERVED_ZERO_QUALIFYING, BackfillStatus.BACKFILL_NOT_REQUIRED, ()),
        "2026-09-10": (ObservationStatus.OBSERVATION_INCOMPLETE, BackfillStatus.BACKFILL_REQUIRED, ()),
        "2026-09-11": (
            ObservationStatus.OBSERVED_WITH_FINDINGS,
            BackfillStatus.RECOVERED,
            ("care-line-2026-09-11-cms-michigan-home-health-hospice-terminations",),
        ),
        "2026-09-12": (
            ObservationStatus.OBSERVED_WITH_FINDINGS,
            BackfillStatus.RECOVERED,
            ("care-line-2026-09-12-cms-clia-lab-limitations",),
        ),
        "2026-09-13": (ObservationStatus.OBSERVATION_INCOMPLETE, BackfillStatus.BACKFILL_REQUIRED, ()),
    }

    for observation_date, (observation, backfill, recovered_ids) in expected.items():
        result = evaluate_dispatch_date(Path("."), "care-line", observation_date, evaluated_at="2026-09-15T12:00:00Z")
        assert result.observation_status == observation
        assert result.backfill_status == backfill
        assert tuple(sorted(result.recovered_event_ids)) == tuple(sorted(recovered_ids))
        assert result.operator_attention_required is (backfill == BackfillStatus.BACKFILL_REQUIRED)


def test_care_terminal_accounting_can_close_zero_without_creating_duplicates() -> None:
    gap = _load(GAP_ROOT / "2026-09-09.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")

    assert gap["observation_status"] == "OBSERVED_ZERO_QUALIFYING"
    assert gap["backfill_status"] == "BACKFILL_NOT_REQUIRED"
    assert gap["recovered_event_ids"] == []
    duplicate = next(item for item in review["items"] if item["item_id"] == "care-line-2026-09-09-fox5-medstar-followup")
    assert duplicate["disposition"] == "rejected_duplicate"
    assert duplicate["duplicate_target"] == "care-line-2026-09-08-medstar-postpartum-bed-cuts"
    assert not list(EVENT_ROOT.glob("2026-09-09/*.json"))


def test_care_failed_collection_does_not_prove_no_event_and_incomplete_dates_stay_open() -> None:
    sep11_audit = _load(RECOVERY_ROOT / "audits" / "2026-09-11.json")
    sep13_audit = _load(RECOVERY_ROOT / "audits" / "2026-09-13.json")
    sep13_gap = _load(GAP_ROOT / "2026-09-13.json")

    assert {row["status"] for row in sep11_audit["scheduler_receipts"]} == {"failure"}
    assert sep11_audit["approved_recovered_event_ids"] == [
        "care-line-2026-09-11-cms-michigan-home-health-hospice-terminations"
    ]
    assert sep13_audit["complete_observation_proven_from_runtime"] is False
    assert sep13_gap["backfill_status"] == "BACKFILL_REQUIRED"
    assert sep13_gap["recovered_event_ids"] == []


def test_care_historical_records_preserve_dates_lineage_and_authority_flags() -> None:
    paths = [
        EVENT_ROOT / "2026-09-08" / "care-line-2026-09-08-wcchc-clinic-closure-lowell.json",
        EVENT_ROOT / "2026-09-08" / "care-line-2026-09-08-cecil-county-birthing-center-gap.json",
        EVENT_ROOT / "2026-09-08" / "care-line-2026-09-08-medstar-postpartum-bed-cuts.json",
        EVENT_ROOT / "2026-09-11" / "care-line-2026-09-11-cms-michigan-home-health-hospice-terminations.json",
        EVENT_ROOT / "2026-09-12" / "care-line-2026-09-12-cms-clia-lab-limitations.json",
    ]
    payloads = [_load(path) for path in paths]

    assert {payload["review_disposition"] for payload in payloads} == {"approved"}
    assert all(payload["recovery_provenance"] == "operator_recovered_public_source_evidence" for payload in payloads)
    assert all(payload["original_production_discovery_lineage_present"] is False for payload in payloads)
    assert all(payload["production_artifact_present"] is False for payload in payloads)
    assert all(payload["publication_eligible"] is False for payload in payloads)
    assert all(payload["publication_approval"] is False for payload in payloads)
    assert all(payload["publication_performed"] is False for payload in payloads)
    assert all(payload["public_generation_authorized"] is False for payload in payloads)
    assert all(payload["pages_authorized"] is False for payload in payloads)
    medstar = _load(EVENT_ROOT / "2026-09-08" / "care-line-2026-09-08-medstar-postpartum-bed-cuts.json")
    assert medstar["event_date"] == "2026-07-26"
    assert medstar["source_published_date"] == "2026-09-08"
    assert medstar["observation_date"] == "2026-09-08"
    assert medstar["effective_date"] == "2026-07-26"


def test_care_sep10_special_accounting_requirement_keeps_gap_open_without_proof() -> None:
    audit = _load(RECOVERY_ROOT / "audits" / "2026-09-10.json")
    gap = _load(GAP_ROOT / "2026-09-10.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")

    assert audit["sep10_terminal_accounting_claim"] == {
        "raw_rows": 909,
        "deferred": 673,
        "rejected": 236,
        "qualified": 0,
        "unaccounted": 0,
        "authoritative_machine_readable_proof_found": False,
    }
    assert audit["deferred_candidates"][0]["item_id"] == "care-line-2026-09-10-cms-public-notices"
    assert gap["observation_status"] == "OBSERVATION_INCOMPLETE"
    assert gap["backfill_status"] == "BACKFILL_REQUIRED"
    assert gap["recovered_event_ids"] == []
    deferred = next(item for item in review["items"] if item["item_id"] == "care-line-2026-09-10-cms-public-notices")
    assert deferred["disposition"] == "deferred_for_corroboration"
    assert not list((EVENT_ROOT / "2026-09-10").glob("care-line-2026-09-10-cms*.json"))


def test_care_fenway_and_atlanta_existing_recovery_boundaries_are_not_rewritten() -> None:
    fenway = _load(EVENT_ROOT / "2026-09-11" / "care-line-2026-09-11-fenway-it-disruption.json")
    atlanta = _load(EVENT_ROOT / "2026-09-14" / "care-line-2026-09-14-atlanta-delivery-pause.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")

    assert fenway["event_date"] == "2026-09-11"
    assert fenway["source_published_date"] == "2026-09-13"
    assert fenway["observation_date"] == "2026-09-14"
    assert fenway["recovered_at"] == "2026-09-14"
    assert atlanta["observation_date"] == "2026-09-14"
    assert atlanta["effective_date"] == "2026-09-30"
    assert "care-line-2026-09-11-fenway-it-disruption" not in _load(GAP_ROOT / "2026-09-11.json")[
        "recovered_event_ids"
    ]
    assert "care-line-2026-09-11-fenway-it-disruption" not in _load(GAP_ROOT / "2026-09-13.json")[
        "recovered_event_ids"
    ]
    excluded = next(item for item in review["items"] if item["item_id"] == "care-line-2026-09-13-fenway-health-it-incident")
    assert excluded["disposition"] == "excluded_existing_recovery"


def test_care_transition_history_preserves_original_gap_reasons() -> None:
    expected_reason = {
        "2026-09-07": "historical_evidence_incomplete",
        "2026-09-08": "historical_evidence_incomplete",
        "2026-09-09": "historical_evidence_incomplete",
        "2026-09-10": "historical_evidence_incomplete",
        "2026-09-11": "scheduled_run_failed",
        "2026-09-12": "scheduled_run_failed",
        "2026-09-13": "scheduled_run_failed",
    }

    for observation_date, reason in expected_reason.items():
        gap = _load(GAP_ROOT / f"{observation_date}.json")
        assert gap["transition_history"][0]["reason_code"] == reason
        if gap["backfill_status"] == "RECOVERED":
            assert gap["transition_history"][-1]["reason_code"] == "historical_recovery_completed"
