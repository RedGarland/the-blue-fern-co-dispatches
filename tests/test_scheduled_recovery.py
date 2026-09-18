from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bluefern_dispatches.operational_health import OperationalStatus, build_operational_receipt
from bluefern_dispatches.scheduled_recovery import evaluate_recovery


DATE = "2026-09-10"
RUN_ID = "source-watch-run"


def _write_receipt(
    root: Path,
    *,
    task_key: str,
    status: OperationalStatus,
    classification: str,
    started: str = "2026-09-10T12:30:00Z",
    completed: str | None = None,
    dispatch: str = "food-line",
    exit_code: int | None = None,
    name: str | None = None,
    run_id: str | None = None,
    details: dict[str, object] | None = None,
    task_receipt: dict[str, object] | None = None,
) -> None:
    artifact = root / "logs" / "food-line" / "test-operational-artifacts" / f"{name or task_key}-artifact.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(task_receipt or {}) + "\n", encoding="utf-8")
    receipt = build_operational_receipt(
        dispatch=dispatch,
        task_key=task_key,
        task_name=task_key,
        scheduled_for=DATE,
        started_at=started,
        completed_at=completed or started,
        exit_code=exit_code if exit_code is not None else 0 if status in {OperationalStatus.SUCCESS, OperationalStatus.SAFE_NO_OP} else 1,
        status=status,
        classification=classification,
        run_id=run_id or name or f"{task_key}-run",
        public_side_effects={},
        artifact_refs={"task_receipt": str(artifact)},
        details=details or {},
    )
    path = root / "status" / "operational-health" / dispatch / DATE / "runs" / f"{name or task_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")


def _write_qualifying_source_state(
    root: Path,
    *,
    date: str = DATE,
    run_id: str = RUN_ID,
    edition_date: str | None = None,
    state_run_id: str | None = None,
    status: str = "completed_with_exclusions",
    export_status: str = "success",
    export_present: bool = True,
) -> None:
    effective_date = edition_date or date
    effective_run_id = state_run_id or run_id
    export_path = root / "data" / "dispatches" / "food-line" / "agent-inbox" / f"food-line-source-watch-{date}-{run_id}.json"
    if export_present:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text('{"schema_version":"food_line_source_watch_agent_export_v1","findings":[]}\n', encoding="utf-8")
    record = {
        "schema_version": "food_line_scheduled_run_record_v1",
        "edition_date": date,
        "run_id": run_id,
        "source_watch_status": status,
        "last_status": status,
        "run_state_path": str(root / "data" / "dispatches" / "food-line" / "discovery-runs" / date / run_id / "run-state.json"),
    }
    state = {
        "schema_version": "food_line_bounded_run_state_v1",
        "edition_date": effective_date,
        "run_id": effective_run_id,
        "status": status,
        "partitions_total": 1,
        "partitions_completed": 1,
        "coverage": {"required_success_ratio": 1.0, "direct_success_ratio": 1.0},
        "options": {"required_coverage_threshold": 0.9, "direct_source_coverage_threshold": 0.75},
        "agent_export": {
            "status": export_status,
            "path": str(export_path),
            "sha256": "export-sha",
        },
        "final_error": "",
    }
    record_path = root / "status" / "food-line" / "runs" / f"{date}.json"
    state_path = root / "data" / "dispatches" / "food-line" / "discovery-runs" / date / run_id / "run-state.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _write_blocked_intake(root: Path, *, started: str = "2026-09-10T13:10:00Z", classification: str = "upstream_blocked") -> None:
    _write_receipt(
        root,
        task_key="food_line_current_intake",
        status=OperationalStatus.UPSTREAM_BLOCKED,
        classification=classification,
        started=started,
        name="01-intake-blocked",
        run_id=RUN_ID,
        details={
            "edition_date": DATE,
            "qualifying_discovery_run_id": RUN_ID,
            "source_status": "blocked_overlapping_run",
            "intake_status": classification,
        },
        task_receipt={
            "schema_version": "food_line_current_intake_receipt_v1",
            "edition_date": DATE,
            "qualifying_discovery_run_id": RUN_ID,
            "status": classification,
        },
    )


def _write_recovered_resume(root: Path, *, started: str = "2026-09-10T13:20:00Z", run_id: str = RUN_ID) -> None:
    _write_receipt(
        root,
        task_key="food_line_source_watch_resume",
        status=OperationalStatus.SUCCESS,
        classification="resume_qualified",
        started=started,
        name="02-resume-qualified",
        run_id=run_id,
        details={
            "edition_date": DATE,
            "source_status": "completed_with_exclusions",
            "source_export_status": "success",
            "inbox_export_path": "data/dispatches/food-line/agent-inbox/export.json",
        },
        task_receipt={
            "schema_version": "food_line_source_watch_receipt_v1",
            "action": "status_resume",
            "edition_date": DATE,
            "run_id": run_id,
            "final_status": "completed_with_exclusions",
            "resume_status": "resume_qualified",
            "export_status": "success",
            "exit_code": 0,
        },
    )


def test_not_yet_due_and_grace_active_do_not_raise_false_alarm(tmp_path: Path) -> None:
    not_due = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T12:00:00Z")
    grace = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T13:00:00Z")

    assert not_due["overall_recommendation"] == "WAITING_FOR_SCHEDULE"
    assert any(row["state"] == "NOT_YET_DUE" for row in not_due["instances"])
    assert grace["overall_recommendation"] == "WAITING_FOR_GRACE"
    assert any(row["state"] == "GRACE_ACTIVE" for row in grace["instances"])


def test_retryable_transient_failure_and_max_attempts(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
    )
    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    capped = evaluate_recovery(
        dispatch="food-line",
        source_root=tmp_path,
        date=DATE,
        evaluated_at="2026-09-10T15:00:00Z",
        attempts_by_instance={f"{DATE}:food_line_source_watch": 2},
    )

    assert report["overall_recommendation"] == "RETRY_ELIGIBLE"
    assert capped["overall_recommendation"] == "MANUAL_ATTENTION"


def test_terminal_degraded_food_source_watch_does_not_raise_recovery_attention(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.DEGRADED,
        classification="completed_with_exclusions",
        exit_code=0,
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_source_watch")

    assert row["state"] == "DEGRADED_TERMINAL"
    assert row["recommendation"] == "NO_ACTION"
    assert row["receipt_status"] == "DEGRADED"
    assert row["classification"] == "completed_with_exclusions"
    assert row["retry_eligible"] is False
    assert report["overall_recommendation"] != "MANUAL_ATTENTION"


def test_terminal_degraded_care_collection_does_not_raise_recovery_attention(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        dispatch="care-line",
        task_key="care_line_collection",
        status=OperationalStatus.DEGRADED,
        classification="partial_success",
        started="2026-09-10T13:00:00Z",
        exit_code=0,
    )

    report = evaluate_recovery(dispatch="care-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T20:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "care_line_collection")

    assert row["state"] == "DEGRADED_TERMINAL"
    assert row["recommendation"] == "NO_ACTION"
    assert row["classification"] == "partial_success"
    assert row["retry_eligible"] is False
    assert report["overall_recommendation"] != "MANUAL_ATTENTION"


def test_degraded_transient_classification_is_retry_eligible(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.DEGRADED,
        classification="provider_timeout",
        exit_code=0,
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_source_watch")

    assert row["state"] == "FAILED_RETRYABLE"
    assert row["recommendation"] == "RETRY_ELIGIBLE"
    assert row["retry_eligible"] is True


def test_food_source_watch_exposes_exact_grace_and_recovery_deadline(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_source_watch")
    scheduled = datetime.fromisoformat(f"{DATE}T05:30:00").replace(tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(ZoneInfo("UTC"))
    grace_end = scheduled + timedelta(minutes=90)
    recovery_deadline = grace_end + timedelta(minutes=240)

    assert row["scheduled_for"] == scheduled.isoformat().replace("+00:00", "Z")
    assert row["grace_end"] == grace_end.isoformat().replace("+00:00", "Z")
    assert row["recovery_deadline"] == recovery_deadline.isoformat().replace("+00:00", "Z")


def test_food_source_watch_recovery_window_preserves_grace_interval(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
    )
    scheduled = datetime.fromisoformat(f"{DATE}T05:30:00").replace(tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(ZoneInfo("UTC"))
    after_scheduled_plus_window = (scheduled + timedelta(minutes=241)).isoformat().replace("+00:00", "Z")
    after_grace_plus_window = (scheduled + timedelta(minutes=90 + 241)).isoformat().replace("+00:00", "Z")

    still_open = evaluate_recovery(
        dispatch="food-line",
        source_root=tmp_path,
        date=DATE,
        evaluated_at=after_scheduled_plus_window,
    )
    expired = evaluate_recovery(
        dispatch="food-line",
        source_root=tmp_path,
        date=DATE,
        evaluated_at=after_grace_plus_window,
    )

    assert next(item for item in still_open["instances"] if item["task_key"] == "food_line_source_watch")["recommendation"] == "RETRY_ELIGIBLE"
    assert next(item for item in expired["instances"] if item["task_key"] == "food_line_source_watch")["recommendation"] == "RECOVERY_WINDOW_EXPIRED"


def test_failed_transient_and_nonretryable_classifications_are_distinct(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path / "transient",
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
    )
    _write_receipt(
        tmp_path / "nonretryable",
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="invalid_configuration",
    )

    transient = evaluate_recovery(
        dispatch="food-line", source_root=tmp_path / "transient", date=DATE, evaluated_at="2026-09-10T15:00:00Z"
    )
    nonretryable = evaluate_recovery(
        dispatch="food-line", source_root=tmp_path / "nonretryable", date=DATE, evaluated_at="2026-09-10T15:00:00Z"
    )

    assert transient["overall_recommendation"] == "RETRY_ELIGIBLE"
    assert nonretryable["overall_recommendation"] == "MANUAL_ATTENTION"


def test_publication_and_nonretryable_failures_require_manual_attention(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_daily_publish",
        status=OperationalStatus.FAILED,
        classification="approval_failure",
        started="2026-09-10T16:00:00Z",
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T18:00:00Z")

    assert report["overall_recommendation"] == "MANUAL_ATTENTION"
    publish = next(row for row in report["instances"] if row["task_key"] == "food_line_daily_publish")
    assert publish["publication_task"] is True


def test_successful_terminal_result_suppresses_retry(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.SUCCESS,
        classification="completed",
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")

    first = next(row for row in report["instances"] if row["task_key"] == "food_line_source_watch")
    assert first["state"] == "RECOVERED"
    assert first["recommendation"] == "NO_ACTION"


def test_safe_no_op_terminal_result_suppresses_retry(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_daily_publish",
        status=OperationalStatus.SAFE_NO_OP,
        classification="nothing_to_publish",
        started="2026-09-10T15:30:00Z",
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T18:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_daily_publish")

    assert row["state"] == "RECOVERED"
    assert row["recommendation"] == "NO_ACTION"


def test_current_intake_blocked_with_upstream_still_blocked_remains_upstream_blocked(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "UPSTREAM_BLOCKED"
    assert row["retry_eligible"] is False


def test_current_intake_blocked_with_newer_qualifying_resume_is_retry_eligible(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path)
    _write_recovered_resume(tmp_path)

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "RETRY_ELIGIBLE"
    assert row["classification"] == "dependency_recovered_current_intake"
    assert row["retry_eligible"] is True


def test_current_intake_blocked_with_older_qualifying_resume_stays_blocked(tmp_path: Path) -> None:
    _write_recovered_resume(tmp_path, started="2026-09-10T13:00:00Z")
    _write_blocked_intake(tmp_path, started="2026-09-10T13:10:00Z")
    _write_qualifying_source_state(tmp_path)

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "UPSTREAM_BLOCKED"
    assert row["retry_eligible"] is False


def test_current_intake_dependency_recovery_fails_closed_on_run_id_mismatch(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path, state_run_id="different-run")
    _write_recovered_resume(tmp_path)

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "UPSTREAM_BLOCKED"


def test_current_intake_dependency_recovery_fails_closed_on_edition_date_mismatch(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path, edition_date="2026-09-09")
    _write_recovered_resume(tmp_path)

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "UPSTREAM_BLOCKED"


def test_current_intake_dependency_recovery_fails_closed_on_missing_export_evidence(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path, export_present=False)
    _write_recovered_resume(tmp_path)

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "UPSTREAM_BLOCKED"


def test_current_intake_dependency_recovery_fails_closed_on_disagreeing_upstream_evidence(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path)
    _write_recovered_resume(tmp_path)
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
        started="2026-09-10T13:25:00Z",
        name="03-source-watch-disagrees",
        run_id=RUN_ID,
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "UPSTREAM_BLOCKED"


def test_current_intake_dependency_recovery_suppressed_by_newer_intake_success(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path)
    _write_recovered_resume(tmp_path)
    _write_receipt(
        tmp_path,
        task_key="food_line_current_intake",
        status=OperationalStatus.SUCCESS,
        classification="success",
        started="2026-09-10T13:30:00Z",
        name="03-intake-success",
        run_id=RUN_ID,
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "NO_ACTION"
    assert row["state"] == "RECOVERED"


def test_current_intake_newer_independent_failure_does_not_dependency_retry(tmp_path: Path) -> None:
    _write_blocked_intake(tmp_path)
    _write_qualifying_source_state(tmp_path)
    _write_recovered_resume(tmp_path)
    _write_receipt(
        tmp_path,
        task_key="food_line_current_intake",
        status=OperationalStatus.FAILED,
        classification="invalid_configuration",
        started="2026-09-10T13:30:00Z",
        name="03-intake-independent-failed",
        run_id=RUN_ID,
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_current_intake")

    assert row["recommendation"] == "MANUAL_ATTENTION"
    assert row["retry_eligible"] is False


def test_later_success_after_earlier_failure_suppresses_retry(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
        started="2026-09-10T12:30:00Z",
        name="01-failed",
    )
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch",
        status=OperationalStatus.SUCCESS,
        classification="completed",
        started="2026-09-10T12:45:00Z",
        name="02-success",
    )

    report = evaluate_recovery(dispatch="food-line", source_root=tmp_path, date=DATE, evaluated_at="2026-09-10T15:00:00Z")
    row = next(item for item in report["instances"] if item["task_key"] == "food_line_source_watch")

    assert row["state"] == "RECOVERED"
    assert row["recommendation"] == "NO_ACTION"
    assert row["receipt_status"] == "SUCCESS"


def test_missing_after_recovery_window_is_expired(tmp_path: Path) -> None:
    report = evaluate_recovery(dispatch="ice", source_root=tmp_path, date=DATE, evaluated_at="2026-09-11T12:00:00Z")

    assert report["overall_recommendation"] == "RECOVERY_WINDOW_EXPIRED"
    assert report["instances"][0]["state"] == "MISSED"
