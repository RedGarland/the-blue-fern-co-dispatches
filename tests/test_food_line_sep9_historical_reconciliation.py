from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_DATE = "2026-09-09"
RECONCILIATION = (
    ROOT
    / "data/dispatches/food-line/historical-intake/2026-09-09/reconciliation-receipt.json"
)
SUMMARY = (
    ROOT
    / "data/dispatches/food-line/historical-intake/2026-09-09/reconciliation-summary.md"
)
HISTORICAL_ROOT = ROOT / "data/dispatches/food-line/historical-intake/2026-09-09"


def _receipt() -> dict:
    return json.loads(RECONCILIATION.read_text(encoding="utf-8"))


def test_sep9_reconciliation_preserves_original_failure_lineage() -> None:
    receipt = _receipt()

    assert receipt["historical_date"] == HISTORICAL_DATE
    assert receipt["replay_reason"] == "scheduler_overlap_missing_run_state"
    assert receipt["replay_feasibility"] == "LIMITED_REPLAY"
    assert (
        receipt["final_classification"]
        == "FOOD LINE SEP 9 — HISTORICAL REPLAY BLOCKED / INSUFFICIENT EVIDENCE"
    )

    categories = {
        ref["category"]
        for ref in receipt["historical_evidence_boundary"]["operator_attention"]
    }
    assert categories == {
        "overlapping_run",
        "status_resume_failed",
        "source_watch_failed",
        "current_intake_failed",
    }
    for ref in receipt["historical_evidence_boundary"]["operator_attention"]:
        path = ROOT / ref["path"]
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        assert payload["edition_date"] == HISTORICAL_DATE
        assert payload["schema_version"] == "food_line_operator_attention_v1"

    daily_refs = receipt["historical_evidence_boundary"]["daily_publish_receipts"]
    assert len(daily_refs) == 1
    daily = json.loads((ROOT / daily_refs[0]["path"]).read_text(encoding="utf-8-sig"))
    assert daily["status"] == "skipped_not_release_ready"
    assert daily["publication_attempted"] is False


def test_sep9_reconciliation_does_not_fabricate_historical_success() -> None:
    receipt = _receipt()

    assert receipt["historical_evidence_boundary"]["network_access"] is False
    assert receipt["historical_evidence_boundary"]["current_state_fallback"] is False
    assert receipt["historical_evidence_boundary"]["fresh_source_watch_run"] is False
    assert receipt["historical_evidence_boundary"]["retained_source_discovery_evidence"] is False
    assert receipt["historical_evidence_boundary"]["retained_source_watch_handoff"] is False
    assert receipt["replay_mechanism"]["source_watch_reconstruction"] == (
        "not_performed_no_retained_discovery_evidence"
    )
    assert receipt["replay_mechanism"]["current_intake_replay"] == (
        "not_performed_no_supportable_source_watch_input_set"
    )
    assert receipt["operational_health_handling"]["fabricated_success_receipt"] is False
    assert receipt["operational_health_handling"]["historical_reconciliation_status"] == "BLOCKED"


def test_sep9_reconciliation_counts_are_zero_and_not_auto_approved() -> None:
    receipt = _receipt()

    assert receipt["replay_result"] == {
        "approved": 0,
        "considered": 0,
        "duplicate": 0,
        "imported": 0,
        "pending": 0,
        "rejected": 0,
        "selected": 0,
        "unresolved": 0,
    }
    assert receipt["editorial_safety"]["auto_approval"] is False
    assert receipt["editorial_safety"]["item_approvals"] == 0
    assert receipt["editorial_safety"]["publication_approval"] is False


def test_sep9_reconciliation_creates_no_queue_proposal_or_publication_artifacts() -> None:
    receipt = _receipt()

    assert not (HISTORICAL_ROOT / "current-signal-review.json").exists()
    assert not (ROOT / "data/dispatches/food-line/review/reports/2026-09-09/current-intake.json").exists()
    assert not (ROOT / "data/dispatches/food-line/review/proposed-editions/2026-09-09.json").exists()
    assert not (ROOT / "data/dispatches/food-line/review/proposed-editions/2026-09-09.md").exists()
    assert not (ROOT / "output/site/food-line/editions/2026-09-09").exists()
    assert not any(receipt["publication_side_effects"].values())


def test_sep9_reconciliation_records_absent_and_unsafe_inputs() -> None:
    receipt = _receipt()

    absent = set(receipt["historical_evidence_boundary"]["absent"])
    assert "data/dispatches/food-line/discovery-runs/2026-09-09" in absent
    assert "data/dispatches/food-line/discovery/2026-09-09/discovery_candidates.json" in absent
    assert "data/dispatches/food-line/agent-intake/2026-09-09" in absent
    assert "data/dispatches/food-line/agent-inbox/food-line-source-watch-2026-09-09*.json" in absent

    unsafe = set(receipt["historical_evidence_boundary"]["unsafe_for_replay"])
    assert "September 10+ source/web state" in unsafe
    assert "fresh Source Watch output not retained by the original September 9 run" in unsafe


def test_sep9_reconciliation_summary_matches_blocked_limited_classification() -> None:
    text = SUMMARY.read_text(encoding="utf-8")

    assert "historical_date: 2026-09-09" in text
    assert "replay_feasibility: LIMITED_REPLAY" in text
    assert "HISTORICAL REPLAY BLOCKED / INSUFFICIENT EVIDENCE" in text
    assert "No historical Source Watch reconstruction or Current Intake replay was performed" in text
    assert "No current web/source state was fetched" in text
