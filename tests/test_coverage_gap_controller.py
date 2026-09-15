from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bluefern_dispatches.coverage_gap_controller import (
    BackfillStatus,
    CoverageEvaluation,
    Criticality,
    EvaluationInput,
    GapReasonCode,
    MaterialDegradation,
    ObservationStatus,
    build_backfill_queue,
    evaluate_dispatch_date,
    evaluate_observation,
    evaluate_range,
    validate_gap_record,
    write_backfill_queue,
)


DATE = "2026-09-14"
EVALUATED = "2026-09-14T18:00:00Z"


def _receipt(
    task_key: str = "food_line_source_watch",
    status: str = "SUCCESS",
    *,
    classification: str = "completed",
    details: dict | None = None,
    scheduled_for: str = DATE,
    dispatch: str = "food-line",
) -> dict:
    return {
        "dispatch": dispatch,
        "task_key": task_key,
        "task_name": task_key,
        "scheduled_for": scheduled_for,
        "started_at": f"{DATE}T12:30:00Z",
        "completed_at": f"{DATE}T12:31:00Z",
        "observed_at": f"{DATE}T12:31:00Z",
        "receipt_created_at": f"{DATE}T12:31:00Z",
        "exit_code": 0 if status != "FAILED" else 10,
        "status": status,
        "classification": classification,
        "run_id": f"{task_key}-run",
        "runner_path": r"C:\BlueFernRunner\FoodLineCurrent6",
        "source_head": "abc123",
        "artifact_refs": {"task_receipt": f"status/{task_key}.json"},
        "publication_attempted": False,
        "publication_status": "safe_no_op",
        "public_side_effects": {},
        "details": details or {},
    }


def _write_receipt(root: Path, dispatch: str, observation_date: str, receipt: dict) -> Path:
    run_id = str(receipt["run_id"])
    path = root / "status" / "operational-health" / dispatch / observation_date / "runs" / f"{run_id}.json"
    if path.exists():
        suffix = str(receipt.get("scheduled_for") or "").replace(":", "").replace("-", "").replace("T", "-").replace("Z", "")
        path = path.with_name(f"{run_id}-{suffix}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_status(root: Path, dispatch: str, payload: dict) -> Path:
    path = root / "ops" / "status" / dispatch / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _durable_record(dispatch: str = "food-line", status: str = "BACKFILL_NOT_REQUIRED") -> dict:
    observation = "OBSERVED_WITH_FINDINGS" if status == "RECOVERED" else "OBSERVED_ZERO_QUALIFYING"
    if status in {"BACKFILL_REQUIRED", "RECOVERY_IN_REVIEW"}:
        observation = "OBSERVATION_INCOMPLETE"
    return {
        "schema_version": "bluefern.coverage_gap.v1",
        "dispatch": dispatch,
        "observation_date": DATE,
        "observation_status": observation,
        "backfill_status": status,
        "reason_codes": ["historical_recovery_completed"] if status == "RECOVERED" else [],
        "source_refs": ["old-source"],
        "transition_history": [
            {
                "previous_observation_status": None,
                "previous_backfill_status": None,
                "new_observation_status": observation,
                "new_backfill_status": status,
                "transition_at": "2026-09-14T00:00:00Z",
                "reason_code": "historical_recovery_completed",
                "evidence_refs": ["old-source"],
            }
        ],
    }


def test_failed_required_collection_becomes_backfill_required() -> None:
    result = evaluate_observation(EvaluationInput("food-line", DATE, EVALUATED, receipts=(_receipt(status="FAILED"),)))

    assert result.observation_status == ObservationStatus.OBSERVATION_INCOMPLETE
    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.SCHEDULED_RUN_FAILED,)
    assert result.criticality == Criticality.CRITICAL


def test_missed_scheduled_run_becomes_backfill_required() -> None:
    result = evaluate_observation(EvaluationInput("food-line", DATE, EVALUATED, receipts=(), expected_tasks=("food_line_source_watch",)))

    assert result.reason_codes == (GapReasonCode.SCHEDULED_RUN_MISSED,)
    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED


def test_stale_observability_becomes_backfill_required() -> None:
    result = evaluate_observation(EvaluationInput("food-line", DATE, EVALUATED, receipts=(_receipt(status="STALE_OBSERVABILITY"),)))

    assert result.reason_codes == (GapReasonCode.STALE_OBSERVABILITY,)
    assert result.observation_status == ObservationStatus.OBSERVATION_INCOMPLETE


def test_unaccounted_findings_require_backfill() -> None:
    result = evaluate_observation(EvaluationInput("ice", DATE, EVALUATED, receipts=(_receipt(),), unaccounted=1))

    assert result.reason_codes == (GapReasonCode.TERMINAL_ACCOUNTING_INCOMPLETE,)
    assert result.criticality == Criticality.CRITICAL


def test_recall_audit_miss_on_successful_run_reopens_date() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "food-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(),),
            recovery_candidate_count=1,
            recovery_candidate_refs=("data/private-agent-handoff/discovery-recovery/food-care/2026-09-14/recovery-review-intake.json",),
            source_refs=("https://example.invalid/source",),
        )
    )

    assert result.observation_status == ObservationStatus.OBSERVATION_INCOMPLETE
    assert result.backfill_status == BackfillStatus.RECOVERY_IN_REVIEW
    assert GapReasonCode.RECALL_AUDIT_MISS in result.reason_codes


def test_late_source_discovery_can_reopen_recovered_date() -> None:
    durable = {
        "schema_version": "bluefern.coverage_gap.v1",
        "dispatch": "ice",
        "observation_date": "2026-09-09",
        "observation_status": "OBSERVED_WITH_FINDINGS",
        "backfill_status": "RECOVERED",
        "reason_codes": ["historical_recovery_completed"],
        "source_refs": ["old-source"],
        "transition_history": [
            {
                "previous_observation_status": None,
                "previous_backfill_status": None,
                "new_observation_status": "OBSERVED_WITH_FINDINGS",
                "new_backfill_status": "RECOVERED",
                "transition_at": "2026-09-14T00:00:00Z",
                "reason_code": "historical_recovery_completed",
                "evidence_refs": ["old-source"],
            }
        ],
    }

    result = evaluate_observation(
        EvaluationInput(
            "ice",
            "2026-09-09",
            "2026-09-18T00:00:00Z",
            recovery_candidate_count=1,
            recovery_candidate_refs=("late-source",),
            source_refs=("late-source",),
            durable_gap_record=durable,
            notes="late source discovery",
        )
    )

    assert result.backfill_status == BackfillStatus.RECOVERY_IN_REVIEW
    assert result.transition_history[0].new_backfill_status == "RECOVERED"
    assert result.transition_history[-1].new_backfill_status == "RECOVERY_IN_REVIEW"


def test_durable_complete_reopens_on_failed_runtime_evidence() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "food-line",
            DATE,
            EVALUATED,
            receipts=(_receipt("food_line_source_watch", "FAILED"),),
            durable_gap_record=_durable_record("food-line", "BACKFILL_NOT_REQUIRED"),
        )
    )

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.SCHEDULED_RUN_FAILED,)
    assert result.transition_history[-1].previous_backfill_status == "BACKFILL_NOT_REQUIRED"


def test_durable_complete_reopens_on_terminal_accounting_defect() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "food-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(),),
            unaccounted=1,
            durable_gap_record=_durable_record("food-line", "BACKFILL_NOT_REQUIRED"),
        )
    )

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.TERMINAL_ACCOUNTING_INCOMPLETE,)


def test_durable_complete_with_no_new_evidence_remains_idempotent() -> None:
    durable = _durable_record("food-line", "BACKFILL_NOT_REQUIRED")
    first = evaluate_observation(EvaluationInput("food-line", DATE, EVALUATED, durable_gap_record=durable))
    second = evaluate_observation(EvaluationInput("food-line", DATE, EVALUATED, durable_gap_record=durable))

    assert first == second
    assert first.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_repeated_identical_reopen_evaluation_adds_no_duplicate_transition() -> None:
    durable = _durable_record("food-line", "BACKFILL_REQUIRED")
    result = evaluate_observation(
        EvaluationInput(
            "food-line",
            DATE,
            EVALUATED,
            receipts=(_receipt("food_line_source_watch", "FAILED"),),
            durable_gap_record=durable,
        )
    )

    assert len(result.transition_history) == len(durable["transition_history"])


def test_late_source_reason_is_preserved() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "ice",
            DATE,
            EVALUATED,
            recovery_candidate_count=1,
            recovery_reason_codes=(GapReasonCode.LATE_SOURCE_DISCOVERY,),
            recovery_candidate_refs=("candidate.json",),
        )
    )

    assert result.reason_codes == (GapReasonCode.LATE_SOURCE_DISCOVERY, GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW)


def test_missing_original_artifact_reason_is_preserved() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "food-line",
            DATE,
            EVALUATED,
            recovery_candidate_count=1,
            recovery_reason_codes=(GapReasonCode.MISSING_ORIGINAL_ARTIFACT,),
            recovery_candidate_refs=("candidate.json",),
        )
    )

    assert result.reason_codes == (GapReasonCode.MISSING_ORIGINAL_ARTIFACT, GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW)


def test_recall_miss_reason_remains_recall_audit_miss() -> None:
    result = evaluate_observation(EvaluationInput("care-line", DATE, EVALUATED, recovery_candidate_count=1, recovery_candidate_refs=("candidate.json",)))

    assert result.reason_codes == (GapReasonCode.RECALL_AUDIT_MISS, GapReasonCode.HISTORICAL_RECOVERY_PENDING_REVIEW)


def test_healthy_complete_zero_is_positive_not_absence() -> None:
    result = evaluate_observation(EvaluationInput("food-line", DATE, EVALUATED, receipts=(_receipt(),)))

    assert result.observation_status == ObservationStatus.OBSERVED_ZERO_QUALIFYING
    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_no_public_edition_does_not_imply_incomplete() -> None:
    result = evaluate_observation(
        EvaluationInput("food-line", DATE, EVALUATED, receipts=(_receipt("food_line_daily_publish", "SAFE_NO_OP", classification="skipped_not_release_ready"),))
    )

    assert result.observation_status == ObservationStatus.OBSERVED_ZERO_QUALIFYING


def test_duplicates_and_rejections_do_not_imply_incomplete() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "food-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(details={"duplicate_count": 4, "rejected_count": 2, "unaccounted": 0}),),
            unaccounted=0,
        )
    )

    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_harmless_degraded_run_with_alternate_coverage_does_not_require_backfill() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "care-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(status="DEGRADED"),),
            material_degradation=MaterialDegradation(material=False, alternate_coverage=True, explanation="equivalent coverage succeeded"),
        )
    )

    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED
    assert result.observation_status == ObservationStatus.OBSERVED_ZERO_QUALIFYING


def test_degraded_with_findings_and_alternate_coverage_is_not_zero() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "care-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(status="DEGRADED", details={"reviewable_events": 2}),),
            retained_or_reviewable_count=2,
            material_degradation=MaterialDegradation(material=False, alternate_coverage=True, explanation="alternate coverage complete"),
        )
    )

    assert result.observation_status == ObservationStatus.OBSERVED_WITH_FINDINGS
    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED
    assert result.observed_finding_count == 2


def test_degraded_with_findings_and_material_loss_requires_backfill() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "care-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(status="DEGRADED", details={"reviewable_events": 2}),),
            retained_or_reviewable_count=2,
            material_degradation=MaterialDegradation(material=True, reason_codes=(GapReasonCode.MATERIAL_COLLECTION_DEGRADATION,)),
        )
    )

    assert result.observation_status == ObservationStatus.OBSERVATION_INCOMPLETE
    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.observed_finding_count == 2


def test_degraded_zero_with_unknown_completeness_is_not_certified_zero() -> None:
    result = evaluate_observation(EvaluationInput("care-line", DATE, EVALUATED, receipts=(_receipt(status="DEGRADED"),)))

    assert result.observation_status == ObservationStatus.OBSERVATION_INCOMPLETE
    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.COLLECTION_COMPLETENESS_UNPROVEN,)


def test_material_degraded_run_requires_backfill() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "care-line",
            DATE,
            EVALUATED,
            receipts=(_receipt(status="DEGRADED"),),
            material_degradation=MaterialDegradation(material=True, reason_codes=(GapReasonCode.CRITICAL_SOURCE_FAILURE,)),
        )
    )

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.CRITICAL_SOURCE_FAILURE,)


def test_recovery_candidate_alone_is_not_recovered() -> None:
    result = evaluate_observation(EvaluationInput("ice", DATE, EVALUATED, recovery_candidate_count=1, recovery_candidate_refs=("candidate.json",)))

    assert result.backfill_status == BackfillStatus.RECOVERY_IN_REVIEW
    assert not result.recovered_event_ids


def test_historical_insertion_and_review_approval_becomes_recovered() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "ice",
            "2026-09-09",
            EVALUATED,
            recovered_event_ids=("ice-2026-09-09-springfield-ice-officers-tro",),
            source_refs=("data/dispatches/ice/historical-events/2026-09-09/ice-2026-09-09-springfield-ice-officers-tro.json",),
        )
    )

    assert result.observation_status == ObservationStatus.OBSERVED_WITH_FINDINGS
    assert result.backfill_status == BackfillStatus.RECOVERED


def test_event_date_remains_distinct_from_recovery_date() -> None:
    path = Path("data/dispatches/ice/historical-events/2026-09-07/ice-2026-09-07-camp-east-montana-detention-disturbance.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["event_date"] == "2026-09-07"
    assert payload["recovered_at"] == "2026-09-14"
    assert payload["event_date"] != payload["recovered_at"]


def test_existing_ice_gap_files_remain_schema_compatible() -> None:
    for path in Path("data/dispatches/ice/coverage-gaps").glob("*.json"):
        validate_gap_record(json.loads(path.read_text(encoding="utf-8")))

    result = evaluate_dispatch_date(Path("."), "ice", "2026-09-09", evaluated_at=EVALUATED)
    assert result.observation_status == ObservationStatus.OBSERVED_WITH_FINDINGS
    assert result.backfill_status == BackfillStatus.RECOVERED


def test_separate_runtime_roots_feed_each_dispatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    food = tmp_path / "food-runner"
    care = tmp_path / "care-runner"
    ice = tmp_path / "ice-runner"
    source.mkdir()
    _write_receipt(food, "food-line", DATE, _receipt("food_line_source_watch", dispatch="food-line"))
    _write_receipt(care, "care-line", DATE, _receipt("care_line_collection", dispatch="care-line"))
    _write_receipt(ice, "ice", DATE, _receipt("ice_monitor", dispatch="ice"))

    rows = evaluate_range(
        source,
        dispatches=("food-line", "care-line", "ice"),
        start_date=DATE,
        end_date=DATE,
        evaluated_at=EVALUATED,
        runtime_roots={"food-line": food, "care-line": care, "ice": ice},
    )

    assert {row.dispatch for row in rows} == {"food-line", "care-line", "ice"}
    assert all(row.reason_codes == (GapReasonCode.SCHEDULED_RUN_MISSED,) for row in rows[:2])
    assert rows[2].backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_absent_runtime_root_affects_only_that_dispatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    food = tmp_path / "food-runner"
    ice = tmp_path / "ice-runner"
    source.mkdir()
    _write_receipt(food, "food-line", DATE, _receipt("food_line_source_watch", dispatch="food-line"))
    _write_receipt(ice, "ice", DATE, _receipt("ice_monitor", dispatch="ice"))

    rows = evaluate_range(
        source,
        dispatches=("food-line", "care-line", "ice"),
        start_date=DATE,
        end_date=DATE,
        evaluated_at=EVALUATED,
        runtime_roots={"food-line": food, "ice": ice},
    )
    by_dispatch = {row.dispatch: row for row in rows}

    assert by_dispatch["care-line"].reason_codes == (GapReasonCode.STALE_OBSERVABILITY,)
    assert "runtime root was not supplied" in str(by_dispatch["care-line"].notes)
    assert by_dispatch["ice"].backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_care_adapter_uses_elapsed_expected_instances_from_runtime_status(tmp_path: Path) -> None:
    source = tmp_path / "source"
    care = tmp_path / "care-runner"
    source.mkdir()
    for scheduled in ("2026-09-14T13:00:00Z", "2026-09-14T19:00:00Z"):
        _write_receipt(
            care,
            "care-line",
            DATE,
            _receipt("care_line_collection", dispatch="care-line", scheduled_for=scheduled),
        )
    _write_receipt(care, "care-line", DATE, _receipt("care_line_reviewed_event_queue", "SAFE_NO_OP", dispatch="care-line"))
    _write_receipt(care, "care-line", DATE, _receipt("care_line_approved_release_publication", "SAFE_NO_OP", dispatch="care-line"))
    _write_status(
        care,
        "care-line",
        {
            "dispatch": "care-line",
            "observed_date": DATE,
            "expected_instances": [
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T13:00:00Z"},
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T19:00:00Z"},
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-15T01:00:00Z"},
            ],
        },
    )

    result = evaluate_dispatch_date(source, "care-line", DATE, evaluated_at="2026-09-14T22:00:00Z", runtime_root=care)

    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_care_adapter_missing_elapsed_instance_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    care = tmp_path / "care-runner"
    source.mkdir()
    _write_receipt(care, "care-line", DATE, _receipt("care_line_collection", dispatch="care-line", scheduled_for="2026-09-14T13:00:00Z"))
    _write_status(
        care,
        "care-line",
        {
            "dispatch": "care-line",
            "observed_date": DATE,
            "expected_instances": [
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T13:00:00Z"},
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T19:00:00Z"},
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-15T01:00:00Z"},
            ],
        },
    )

    result = evaluate_dispatch_date(source, "care-line", DATE, evaluated_at="2026-09-14T22:00:00Z", runtime_root=care)

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.SCHEDULED_RUN_MISSED,)
    assert result.notes == "single Care collection receipt does not certify all expected daily instances"


def test_care_adapter_ignores_future_instance_until_grace_expires(tmp_path: Path) -> None:
    source = tmp_path / "source"
    care = tmp_path / "care-runner"
    source.mkdir()
    _write_receipt(care, "care-line", DATE, _receipt("care_line_collection", dispatch="care-line", scheduled_for="2026-09-14T13:00:00Z"))
    _write_receipt(care, "care-line", DATE, _receipt("care_line_reviewed_event_queue", "SAFE_NO_OP", dispatch="care-line"))
    _write_receipt(care, "care-line", DATE, _receipt("care_line_approved_release_publication", "SAFE_NO_OP", dispatch="care-line"))
    _write_status(
        care,
        "care-line",
        {
            "dispatch": "care-line",
            "observed_date": DATE,
            "expected_instances": [
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T13:00:00Z"},
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T23:30:00Z"},
            ],
        },
    )

    result = evaluate_dispatch_date(source, "care-line", DATE, evaluated_at="2026-09-14T23:45:00Z", runtime_root=care)

    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_repeated_evaluation_is_idempotent_for_durable_record() -> None:
    first = evaluate_dispatch_date(Path("."), "ice", "2026-09-09", evaluated_at=EVALUATED)
    second = evaluate_dispatch_date(Path("."), "ice", "2026-09-09", evaluated_at=EVALUATED)

    assert first == second


def test_food_failed_source_watch_cannot_become_observed_zero() -> None:
    result = evaluate_observation(
        EvaluationInput("food-line", DATE, EVALUATED, receipts=(_receipt("food_line_source_watch", "FAILED"), _receipt("food_line_daily_publish", "SAFE_NO_OP")))
    )

    assert result.observation_status == ObservationStatus.OBSERVATION_INCOMPLETE


def test_care_multiple_run_day_is_not_certified_from_one_instance() -> None:
    result = evaluate_observation(
        EvaluationInput(
            "care-line",
            DATE,
            EVALUATED,
            receipts=(_receipt("care_line_collection", "SUCCESS", scheduled_for="2026-09-14T08:00:00Z"),),
            expected_instances=(
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T08:00:00Z"},
                {"task_key": "care_line_collection", "scheduled_for": "2026-09-14T15:00:00Z"},
            ),
        )
    )

    assert result.backfill_status == BackfillStatus.BACKFILL_REQUIRED
    assert result.reason_codes == (GapReasonCode.SCHEDULED_RUN_MISSED,)


def test_ice_shared_monitor_receipt_can_certify_after_runtime() -> None:
    receipt = _receipt("ice_monitor", "SUCCESS", details={"collection_health": "healthy", "canonical_events": 0, "unaccounted": 0})
    receipt["dispatch"] = "ice"
    result = evaluate_observation(EvaluationInput("ice", DATE, EVALUATED, receipts=(receipt,), unaccounted=0))

    assert result.observation_status == ObservationStatus.OBSERVED_ZERO_QUALIFYING
    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED


def test_ice_adapter_reads_monitor_receipt_runtime_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    ice = tmp_path / "ice-runner"
    source.mkdir()
    receipt = ice / "data" / "dispatches" / "ice" / "monitor" / "runs" / DATE / "ice-monitor-20260914T041501Z-test" / "monitor_receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "ok": True,
                "status": "success",
                "run_id": "ice-monitor-20260914T041501Z-test",
                "source_commit": "abc123",
                "publication_side_effects": False,
                "operator_summary": {
                    "collection_health": "healthy",
                    "canonical_events": 3,
                    "failed_providers": 0,
                    "review_queue_additions": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_dispatch_date(source, "ice", DATE, evaluated_at=EVALUATED, runtime_root=ice)

    assert result.observation_status == ObservationStatus.OBSERVED_WITH_FINDINGS
    assert result.backfill_status == BackfillStatus.BACKFILL_NOT_REQUIRED
    assert result.observed_finding_count == 3


def test_controller_queue_has_no_public_authority_and_writes_only_runtime_path(tmp_path: Path) -> None:
    unresolved = CoverageEvaluation(
        dispatch="food-line",
        observation_date=DATE,
        observation_status=ObservationStatus.OBSERVATION_INCOMPLETE,
        backfill_status=BackfillStatus.BACKFILL_REQUIRED,
        reason_codes=(GapReasonCode.SCHEDULED_RUN_FAILED,),
        criticality=Criticality.CRITICAL,
        operator_attention_required=True,
    )

    queue = build_backfill_queue([unresolved], evaluated_at=EVALUATED)
    path = write_backfill_queue(tmp_path, queue)

    assert path == tmp_path / "status/coverage-gap-controller/backfill-queue.json"
    assert queue["publication_authorized"] is False
    assert queue["public_generation_authorized"] is False
    assert queue["pages_authorized"] is False
    assert queue["summary"]["unresolved_count"] == 1


def test_cli_dry_run_reports_without_writing_runtime_queue(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_coverage_gaps.py",
            "--dispatch",
            "ice",
            "--date",
            "2026-09-09",
            "--repo-root",
            ".",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["publication_authorized"] is False
    assert payload["evaluations"][0]["backfill_status"] == "RECOVERED"
    assert not (tmp_path / "status/coverage-gap-controller/backfill-queue.json").exists()
