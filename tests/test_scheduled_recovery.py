from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.operational_health import OperationalStatus, build_operational_receipt
from bluefern_dispatches.scheduled_recovery import evaluate_recovery


DATE = "2026-09-10"


def _write_receipt(root: Path, *, task_key: str, status: OperationalStatus, classification: str, started: str = "2026-09-10T12:30:00Z") -> None:
    artifact = root / "artifact.json"
    artifact.write_text("{}\n", encoding="utf-8")
    receipt = build_operational_receipt(
        dispatch="food-line",
        task_key=task_key,
        task_name=task_key,
        scheduled_for=DATE,
        started_at=started,
        completed_at=started,
        exit_code=0 if status in {OperationalStatus.SUCCESS, OperationalStatus.SAFE_NO_OP} else 1,
        status=status,
        classification=classification,
        run_id=f"{task_key}-run",
        public_side_effects={},
        artifact_refs={"task_receipt": str(artifact)},
    )
    path = root / "status" / "operational-health" / "food-line" / DATE / "runs" / f"{task_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")


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


def test_missing_after_recovery_window_is_expired(tmp_path: Path) -> None:
    report = evaluate_recovery(dispatch="ice", source_root=tmp_path, date=DATE, evaluated_at="2026-09-11T12:00:00Z")

    assert report["overall_recommendation"] == "RECOVERY_WINDOW_EXPIRED"
    assert report["instances"][0]["state"] == "MISSED"
