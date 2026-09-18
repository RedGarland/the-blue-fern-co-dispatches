from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_date_reconciliation import (
    DateReconciliationError,
    DateState,
    ReconstructionFinding,
    ReconstructionResult,
    reconcile_food_line_date,
)

DATE = "2026-09-09"
EVALUATED = "2026-09-18T16:00:00Z"


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _receipt(task_key: str, status: str = "SUCCESS", *, run_id: str = "run-1", classification: str = "completed") -> dict:
    return {
        "dispatch": "food-line",
        "task_key": task_key,
        "task_name": task_key,
        "scheduled_for": DATE,
        "started_at": "2026-09-09T12:00:00Z",
        "completed_at": "2026-09-09T12:01:00Z",
        "receipt_created_at": "2026-09-09T12:01:00Z",
        "exit_code": 0,
        "status": status,
        "classification": classification,
        "run_id": run_id,
        "publication_attempted": False,
        "publication_status": "safe_no_op",
        "public_side_effects": {},
        "artifact_refs": {},
        "details": {},
    }


def _write_receipt(root: Path, task_key: str, status: str = "SUCCESS", *, run_id: str = "run-1", classification: str = "completed") -> Path:
    return _write_json(root / "status" / "operational-health" / "food-line" / DATE / "runs" / f"{task_key}.json", _receipt(task_key, status, run_id=run_id, classification=classification))


def _source_watch_boundary(root: Path, *, run_id: str = "run-1", findings: list[dict] | None = None) -> None:
    _write_json(root / "data" / "dispatches" / "food-line" / "discovery-runs" / DATE / run_id / "run-state.json", {"run_id": run_id, "edition_date": DATE})
    _write_json(root / "data" / "dispatches" / "food-line" / "discovery-runs" / DATE / run_id / "query-plan.json", {"edition_date": DATE, "queries": []})
    _write_json(root / "data" / "dispatches" / "food-line" / "discovery" / DATE / "discovery_candidates.json", {"edition_date": DATE, "candidates": findings or []})
    _write_json(root / "data" / "dispatches" / "food-line" / "agent-inbox" / "handoff.json", {"schema_version": "food_line_source_watch_agent_export_v1", "edition_date": DATE, "findings": findings or []})


def _healthy(root: Path, *, findings: list[dict] | None = None) -> None:
    _source_watch_boundary(root, findings=findings)
    _write_receipt(root, "food_line_source_watch")
    _write_receipt(root, "food_line_source_watch_resume", "SAFE_NO_OP", classification="resume_not_required")
    if findings:
        _write_receipt(root, "food_line_current_intake")
        _write_json(root / "data" / "dispatches" / "food-line" / "review" / "reports" / DATE / "current-intake.json", {"lifecycle_reconciliation": {"unaccounted": 0}})
    _write_receipt(root, "food_line_daily_publish", "SAFE_NO_OP", classification="skipped_not_release_ready")


def test_healthy_complete_original_needs_no_mutation(tmp_path: Path) -> None:
    _healthy(tmp_path)

    result = reconcile_food_line_date(tmp_path, DATE, evaluated_at=EVALUATED)

    assert result["state"] == DateState.COMPLETE_ORIGINAL.value
    assert result["date_complete"] is True
    assert result["repair_actions_performed"] == []
    assert not (tmp_path / "data/dispatches/food-line/date-reconciliation" / f"{DATE}.json").exists()


def test_publish_decision_missing_is_a_gap_and_does_not_publish(tmp_path: Path) -> None:
    _source_watch_boundary(tmp_path)
    _write_receipt(tmp_path, "food_line_source_watch")
    _write_receipt(tmp_path, "food_line_source_watch_resume", "SAFE_NO_OP", classification="resume_not_required")

    result = reconcile_food_line_date(tmp_path, DATE, evaluated_at=EVALUATED)

    assert result["state"] == DateState.BLOCKED_ERROR.value
    assert result["stages"]["daily_publish_decision"]["status"] == "MISSING"
    assert result["publication_authorized"] is False
    assert not (tmp_path / "output/site").exists()


def test_missing_original_source_watch_dry_run_is_explicit_evidence_exhausted(tmp_path: Path) -> None:
    result = reconcile_food_line_date(tmp_path, DATE, evaluated_at=EVALUATED)

    assert result["state"] == DateState.EVIDENCE_EXHAUSTED.value
    assert result["date_complete"] is False
    assert result["evidence_exhausted"] is True
    assert result["next_action"] == "supply_original_or_historical_evidence"


def test_apply_reconstruction_with_review_candidates_stops_for_review(tmp_path: Path) -> None:
    def provider(root: Path, target_date: str) -> ReconstructionResult:
        return ReconstructionResult(
            findings=(
                ReconstructionFinding(
                    candidate_id="food-recon-1",
                    disposition="RETAINED_FOR_REVIEW",
                    canonical_url="https://example.test/story",
                    source_published_at=target_date,
                    event_date=target_date,
                    pressure_type="grocery_access_loss",
                    geography="Test County",
                    supporting_passage="A dated food-access pressure signal.",
                    title="Food access pressure",
                ),
            ),
            queries_attempted=("site:example.test 2026-09-09 food access",),
            sources_attempted=("example.test",),
            sources_succeeded=("example.test",),
            coverage_by_source_family={"local_news": "attempted"},
            coverage_by_pressure_type={"grocery_access_loss": "attempted"},
            coverage_by_geography={"Test County": "attempted"},
            coverage_sufficient=True,
        )

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED, reconstruction_provider=provider)

    assert result["state"] == DateState.REVIEW_REQUIRED.value
    assert result["network_reconstruction_performed"] is True
    assert result["candidate_count"] == 1
    assert result["publication_review_required"] is True
    assert (tmp_path / "data/dispatches/food-line/historical-reconstruction" / DATE / "review" / "candidates.json").is_file()
    assert not (tmp_path / "output/site").exists()


def test_apply_reconstructed_zero_requires_sufficient_coverage(tmp_path: Path) -> None:
    def provider(root: Path, target_date: str) -> ReconstructionResult:
        return ReconstructionResult(
            findings=(),
            queries_attempted=("bounded query",),
            sources_attempted=("source-a", "source-b"),
            sources_succeeded=("source-a", "source-b"),
            coverage_by_source_family={"local_news": "attempted", "official": "attempted"},
            coverage_by_pressure_type={"benefit_loss": "attempted"},
            coverage_by_geography={"target": "attempted"},
            coverage_sufficient=True,
        )

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED, reconstruction_provider=provider)

    assert result["state"] == DateState.COMPLETE_RECONSTRUCTED.value
    assert result["date_complete"] is True
    reconstruction = json.loads((tmp_path / "data/dispatches/food-line/historical-reconstruction" / DATE / "reconstruction.json").read_text(encoding="utf-8"))
    assert reconstruction["historical_zero_basis"]
    assert reconstruction["unaccounted"] == 0


def test_apply_reconstructed_zero_with_inadequate_coverage_stays_exhausted(tmp_path: Path) -> None:
    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED, reconstruction_provider=lambda root, date: ReconstructionResult())

    assert result["state"] == DateState.EVIDENCE_EXHAUSTED.value
    assert result["date_complete"] is False
    assert result["evidence_exhausted"] is True


def test_reconstruction_rejects_contradictory_event_date(tmp_path: Path) -> None:
    def provider(root: Path, target_date: str) -> ReconstructionResult:
        return ReconstructionResult(findings=(ReconstructionFinding(candidate_id="bad", disposition="RETAINED_FOR_REVIEW", event_date="2026-09-08"),), coverage_sufficient=True)

    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED, reconstruction_provider=provider)


def test_historical_current_intake_replay_is_orchestrated_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    finding = {"id": "f1", "title": "qualifying food pressure"}
    _source_watch_boundary(tmp_path, findings=[finding])
    _write_receipt(tmp_path, "food_line_source_watch")
    _write_receipt(tmp_path, "food_line_source_watch_resume", "SAFE_NO_OP", classification="resume_not_required")
    _write_receipt(tmp_path, "food_line_daily_publish", "SAFE_NO_OP", classification="skipped_not_release_ready")

    def fake_replay(root: Path, *, historical_date: str, inbox: Path, source_watch_run_id: str | None, replayed_at: str) -> dict:
        calls.append(historical_date)
        receipt = root / "data" / "dispatches" / "food-line" / "historical-intake" / historical_date / "replay-receipt.json"
        _write_json(receipt, {"schema_version": "food_line_historical_current_intake_replay_v1", "historical_date": historical_date, "network_access": False, "publication_side_effects": False, "approved": 0})
        _write_json(root / "data" / "dispatches" / "food-line" / "review" / "reports" / historical_date / "current-intake.json", {"lifecycle_reconciliation": {"unaccounted": 0}})
        return {}

    import bluefern_dispatches.food_line_date_reconciliation as mod

    monkeypatch.setattr(mod, "run_historical_current_intake_replay", fake_replay)
    first = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    second = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)

    assert first["state"] == DateState.COMPLETE_REPAIRED.value
    assert second["repair_actions_performed"] == []
    assert calls == [DATE]
    assert not (tmp_path / "data/dispatches/food-line/current-review-queue.json").exists()


def test_cli_emits_required_summary_fields(tmp_path: Path) -> None:
    _healthy(tmp_path)
    result = subprocess.run(
        [sys.executable, "scripts/reconcile_food_line_date.py", "--repo-root", str(tmp_path), "--date", DATE],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "bluefern_food_line_date_completeness_v1"
    assert payload["state"] == DateState.COMPLETE_ORIGINAL.value
    assert set(payload) >= {"date", "mode", "date_complete", "repair_actions_available", "coverage_gap_status", "next_action"}


def test_invalid_and_future_dates_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, "not-a-date", evaluated_at=EVALUATED)
    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, "2999-01-01", evaluated_at=EVALUATED)
