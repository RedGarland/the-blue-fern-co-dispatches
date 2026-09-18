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
    assert result["next_action"] == "perform_bounded_historical_research"


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
def _coverage_gap(root: Path) -> dict:
    return json.loads((root / "data/dispatches/food-line/coverage-gaps" / f"{DATE}.json").read_text(encoding="utf-8"))


def _research_packet(root: Path, *, findings: list[dict] | None = None, network_access: bool = True, coverage_sufficient: bool = True, target_date: str = DATE) -> Path:
    return _write_json(
        root / "data/dispatches/food-line/historical-reconstruction-inputs" / f"{DATE}.json",
        {
            "schema_version": "food_line_historical_reconstruction_input_v1",
            "target_date": target_date,
            "researched_at": "2026-09-18T15:00:00Z",
            "research_mode": "codex_bounded_historical_web_research",
            "network_access": network_access,
            "queries_attempted": ["bounded target-date query"],
            "sources_attempted": ["example.test"],
            "sources_succeeded": ["example.test"],
            "sources_failed": [],
            "coverage_by_source_family": {"local_news": "attempted"},
            "coverage_by_pressure_type": {"grocery_access_loss": "attempted"},
            "coverage_by_geography": {"target": "attempted"},
            "coverage_sufficient": coverage_sufficient,
            "findings": findings or [],
            "limitations": [],
        },
    )


def test_apply_complete_original_promotes_backfill_not_required(tmp_path: Path) -> None:
    _healthy(tmp_path)

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    gap = _coverage_gap(tmp_path)

    assert result["state"] == DateState.COMPLETE_ORIGINAL.value
    assert result["date_complete"] is True
    assert gap["backfill_status"] == "BACKFILL_NOT_REQUIRED"
    assert gap["backfill_status"] != "BACKFILL_REQUIRED"


def test_current_intake_replay_promotes_recovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    finding = {"id": "f1", "title": "qualifying food pressure"}
    _source_watch_boundary(tmp_path, findings=[finding])
    _write_receipt(tmp_path, "food_line_source_watch")
    _write_receipt(tmp_path, "food_line_source_watch_resume", "SAFE_NO_OP", classification="resume_not_required")
    _write_receipt(tmp_path, "food_line_daily_publish", "SAFE_NO_OP", classification="skipped_not_release_ready")

    def fake_replay(root: Path, *, historical_date: str, inbox: Path, source_watch_run_id: str | None, replayed_at: str) -> dict:
        _write_json(root / "data/dispatches/food-line/historical-intake" / historical_date / "replay-receipt.json", {"schema_version": "food_line_historical_current_intake_replay_v1", "historical_date": historical_date})
        _write_json(root / "data/dispatches/food-line/review/reports" / historical_date / "current-intake.json", {"lifecycle_reconciliation": {"unaccounted": 0}})
        return {}

    import bluefern_dispatches.food_line_date_reconciliation as mod

    monkeypatch.setattr(mod, "run_historical_current_intake_replay", fake_replay)
    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)

    assert result["state"] == DateState.COMPLETE_REPAIRED.value
    assert _coverage_gap(tmp_path)["backfill_status"] == "RECOVERED"


def test_reconstructed_zero_promotes_backfill_not_required(tmp_path: Path) -> None:
    _research_packet(tmp_path, findings=[], coverage_sufficient=True)

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    gap = _coverage_gap(tmp_path)

    assert result["state"] == DateState.COMPLETE_RECONSTRUCTED.value
    assert gap["backfill_status"] == "BACKFILL_NOT_REQUIRED"
    assert gap["observation_status"] == "OBSERVED_ZERO_QUALIFYING"


def test_reconstructed_final_disposition_promotes_recovered(tmp_path: Path) -> None:
    _research_packet(
        tmp_path,
        findings=[{"candidate_id": "published-match", "disposition": "ALREADY_PUBLISHED", "event_date": DATE, "canonical_url": "https://example.test/old"}],
    )

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    gap = _coverage_gap(tmp_path)

    assert result["state"] == DateState.COMPLETE_RECONSTRUCTED.value
    assert gap["backfill_status"] == "RECOVERED"
    assert gap["observation_status"] == "OBSERVED_WITH_FINDINGS"


def test_review_required_promotes_recovery_in_review(tmp_path: Path) -> None:
    _research_packet(tmp_path, findings=[{"candidate_id": "needs-review", "disposition": "RETAINED_FOR_REVIEW", "event_date": DATE}])

    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    gap = _coverage_gap(tmp_path)

    assert result["state"] == DateState.REVIEW_REQUIRED.value
    assert gap["backfill_status"] == "RECOVERY_IN_REVIEW"


def test_evidence_exhausted_promotes_backfill_required_with_exhausted_status(tmp_path: Path) -> None:
    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    gap = _coverage_gap(tmp_path)

    assert result["state"] == DateState.EVIDENCE_EXHAUSTED.value
    assert gap["backfill_status"] == "BACKFILL_REQUIRED"
    assert gap["recovery_investigation_status"] == "EVIDENCE_EXHAUSTED"
    assert gap["reason_code"] == "historical_evidence_exhausted"
    assert not (tmp_path / "data/dispatches/food-line/historical-reconstruction" / DATE / "reconstruction.json").exists()


def test_duplicate_apply_adds_no_duplicate_coverage_transition(tmp_path: Path) -> None:
    _research_packet(tmp_path, findings=[], coverage_sufficient=True)

    first = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    first_gap = _coverage_gap(tmp_path)
    second = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    second_gap = _coverage_gap(tmp_path)

    assert first["state"] == second["state"] == DateState.COMPLETE_RECONSTRUCTED.value
    assert len(first_gap["transition_history"]) == len(second_gap["transition_history"])


def test_valid_research_packet_is_archived_with_immutable_hash_and_provenance(tmp_path: Path) -> None:
    packet = _research_packet(tmp_path, findings=[], network_access=False)

    reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    archived = tmp_path / "data/dispatches/food-line/historical-reconstruction" / DATE / "research-input.json"
    reconstruction = json.loads((archived.parent / "reconstruction.json").read_text(encoding="utf-8"))

    assert archived.read_text(encoding="utf-8") == packet.read_text(encoding="utf-8")
    assert reconstruction["network_access"] is False
    assert reconstruction["research_mode"] == "codex_bounded_historical_web_research"
    assert reconstruction["research_input"]["sha256"]
    assert reconstruction["research_input"]["original_input_path"].endswith(f"historical-reconstruction-inputs/{DATE}.json")


def test_research_packet_target_date_mismatch_rejected(tmp_path: Path) -> None:
    _research_packet(tmp_path, target_date="2026-09-08")

    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)


def test_research_packet_malformed_schema_rejected(tmp_path: Path) -> None:
    packet = _research_packet(tmp_path)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["schema_version"] = "wrong"
    _write_json(packet, payload)

    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)


def test_research_packet_duplicate_candidate_rejected(tmp_path: Path) -> None:
    _research_packet(tmp_path, findings=[{"candidate_id": "dup", "disposition": "OUTSIDE_SCOPE"}, {"candidate_id": "dup", "disposition": "OUTSIDE_SCOPE"}])

    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)


def test_changed_research_packet_after_archive_fails_closed(tmp_path: Path) -> None:
    packet = _research_packet(tmp_path, findings=[])
    reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)
    payload = json.loads(packet.read_text(encoding="utf-8"))
    payload["queries_attempted"].append("changed query")
    _write_json(packet, payload)

    with pytest.raises(DateReconciliationError):
        reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)


def test_no_research_packet_does_not_fabricate_network_reconstruction(tmp_path: Path) -> None:
    result = reconcile_food_line_date(tmp_path, DATE, apply=True, evaluated_at=EVALUATED)

    assert result["state"] == DateState.EVIDENCE_EXHAUSTED.value
    assert result["next_action"] == "perform_bounded_historical_research"
    assert result["network_reconstruction_performed"] is False
    assert not (tmp_path / "data/dispatches/food-line/historical-reconstruction" / DATE / "reconstruction.json").exists()
