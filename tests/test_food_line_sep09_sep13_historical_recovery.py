from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.coverage_gap_controller import BackfillStatus, ObservationStatus, evaluate_dispatch_date


RECOVERY_ROOT = Path("data/private-agent-handoff/discovery-recovery/food-line/2026-09-15")
EVENT_ROOT = Path("data/dispatches/food-line/historical-events")
GAP_ROOT = Path("data/dispatches/food-line/coverage-gaps")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_food_sep09_sep13_recovery_artifacts_are_bounded_and_non_public() -> None:
    audit = _load(RECOVERY_ROOT / "observation-window-audit.json")
    intake = _load(RECOVERY_ROOT / "recovery-review-intake.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")
    per_date_audits = [
        _load(RECOVERY_ROOT / "audits" / f"{date}.json")
        for date in ("2026-09-09", "2026-09-11", "2026-09-12", "2026-09-13")
    ]

    assert audit["manual_production_task_triggered"] is False
    assert {row["observation_date"] for row in audit["excluded_observation_dates"]} == {"2026-09-10", "2026-09-14"}
    assert {row["observation_date"] for row in per_date_audits} == {
        "2026-09-09",
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
    }
    assert intake["provenance_class"] == "operator_recovered_public_source_recall_evidence"
    assert intake["original_production_discovery_lineage_fabricated"] is False
    assert len(intake["items"]) == 3
    assert audit["duplicate_only_reconciliation"]["item_id"] == "food-line-2026-09-11-arizona-snap-interview-call-failures"
    assert all(item["production_artifact_present"] is False for item in intake["items"])
    assert all(item["publication_eligible"] is False for item in intake["items"])
    assert all(item["publication_approval"] is False for item in intake["items"])
    assert all(item["publication_performed"] is False for item in intake["items"])
    assert all(row["manual_production_task_triggered"] is False for row in per_date_audits)
    assert all(row["publication_performed"] is False for row in per_date_audits)
    assert review["publication_approval"] is False
    assert review["public_generation_authorized"] is False
    assert review["pages_authorized"] is False


def test_food_sep09_sep13_coverage_gap_records_resolve_controller_state() -> None:
    expected = {
        "2026-09-09": (ObservationStatus.OBSERVED_ZERO_QUALIFYING, BackfillStatus.BACKFILL_NOT_REQUIRED, ()),
        "2026-09-11": (ObservationStatus.OBSERVED_ZERO_QUALIFYING, BackfillStatus.BACKFILL_NOT_REQUIRED, ()),
        "2026-09-12": (
            ObservationStatus.OBSERVED_WITH_FINDINGS,
            BackfillStatus.RECOVERED,
            (
                "food-line-2026-09-11-onondaga-pantry-infrastructure-snap-loss",
                "food-line-2026-09-12-northern-illinois-food-bank-demand",
            ),
        ),
        "2026-09-13": (
            ObservationStatus.OBSERVED_WITH_FINDINGS,
            BackfillStatus.RECOVERED,
            ("food-line-2026-09-12-texas-snap-application-backlog",),
        ),
    }
    for observation_date, (observation, backfill, recovered_ids) in expected.items():
        result = evaluate_dispatch_date(Path("."), "food-line", observation_date, evaluated_at="2026-09-15T03:00:00Z")
        assert result.observation_status == observation
        assert result.backfill_status == backfill
        assert result.recovered_event_ids == recovered_ids
        assert result.operator_attention_required is False


def test_food_sep09_sep13_historical_records_preserve_failed_lineage_and_authority_flags() -> None:
    paths = [
        EVENT_ROOT
        / "2026-09-11"
        / "food-line-2026-09-11-onondaga-pantry-infrastructure-snap-loss.json",
        EVENT_ROOT / "2026-09-12" / "food-line-2026-09-12-northern-illinois-food-bank-demand.json",
        EVENT_ROOT / "2026-09-12" / "food-line-2026-09-12-texas-snap-application-backlog.json",
    ]
    payloads = [_load(path) for path in paths]

    assert {payload["observation_date"] for payload in payloads} == {"2026-09-12", "2026-09-13"}
    assert all(payload["recovery_provenance"] == "operator_recovered_public_source_recall_evidence" for payload in payloads)
    assert all(payload["original_production_discovery_lineage_present"] is False for payload in payloads)
    assert all(payload["production_artifact_present"] is False for payload in payloads)
    assert all(payload["publication_eligible"] is False for payload in payloads)
    assert all(payload["publication_approval"] is False for payload in payloads)
    assert all(payload["publication_performed"] is False for payload in payloads)
    assert all(payload["public_generation_authorized"] is False for payload in payloads)
    assert all(payload["pages_authorized"] is False for payload in payloads)


def test_food_sep10_and_sep14_recovery_boundaries_are_not_rewritten() -> None:
    spencer = _load(EVENT_ROOT / "2026-09-11" / "food-line-2026-09-11-spencer-shurfine-loss.json")
    review = _load(RECOVERY_ROOT / "editorial-review.json")

    assert spencer["source_published_date"] == "2026-09-13"
    assert spencer["observation_date"] == "2026-09-14"
    assert spencer["recovered_at"] == "2026-09-14"
    assert "food-line-2026-09-11-spencer-shurfine-loss" not in _load(GAP_ROOT / "2026-09-11.json")[
        "recovered_event_ids"
    ]
    assert not list(EVENT_ROOT.glob("2026-09-10/*sep09-sep13*.json"))
    assert all("annapolis" not in json.dumps(item).lower() for item in review["items"])
