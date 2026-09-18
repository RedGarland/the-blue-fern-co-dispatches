from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .operational_health import (
    CARE_LINE_TASK_EXPECTATIONS,
    FOOD_LINE_TASK_EXPECTATIONS,
    MIGRATION_TASK_EXPECTATIONS,
    OperationalStatus,
    TaskExpectation,
    load_operational_receipts,
    parse_timestamp,
)


class RecoveryRecommendation(StrEnum):
    NO_ACTION = "NO_ACTION"
    WAITING_FOR_SCHEDULE = "WAITING_FOR_SCHEDULE"
    WAITING_FOR_GRACE = "WAITING_FOR_GRACE"
    RETRY_ELIGIBLE = "RETRY_ELIGIBLE"
    UPSTREAM_BLOCKED = "UPSTREAM_BLOCKED"
    MANUAL_ATTENTION = "MANUAL_ATTENTION"
    MISSED = "MISSED"
    RECOVERY_WINDOW_EXPIRED = "RECOVERY_WINDOW_EXPIRED"


class InstanceState(StrEnum):
    NOT_YET_DUE = "NOT_YET_DUE"
    GRACE_ACTIVE = "GRACE_ACTIVE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_NONRETRYABLE = "FAILED_NONRETRYABLE"
    DEGRADED_TERMINAL = "DEGRADED_TERMINAL"
    MISSED = "MISSED"
    RECOVERED = "RECOVERED"


RETRYABLE_CLASSIFICATIONS = {
    "transient_dns_failure",
    "dns_failure",
    "provider_timeout",
    "timeout",
    "temporary_upstream_http_failure",
    "http_429",
    "http_503",
    "recoverable_transport_failure",
    "network_error",
    "stale_lock_recovered",
}

DEPENDENCY_RECOVERED_CURRENT_INTAKE = "dependency_recovered_current_intake"
CURRENT_INTAKE_DEPENDENCY_BLOCKED_CLASSIFICATIONS = {
    "source_watch_not_initialized",
    "source_watch_in_progress",
    "upstream_blocked",
}
FOOD_SOURCE_TERMINAL_TASK_KEYS = {
    "food_line_source_watch",
    "food_line_source_watch_resume",
}
FOOD_SOURCE_TERMINAL_CLASSIFICATIONS = {
    "completed",
    "completed_with_exclusions",
    "resume_qualified",
    "resume_not_required",
}
FOOD_SOURCE_QUALIFYING_STATUSES = {
    "completed",
    "completed_with_exclusions",
}
FOOD_SOURCE_QUALIFYING_EXPORT_STATUSES = {
    "success",
    "success_with_exclusions",
    "no_exportable_findings",
}

NONRETRYABLE_CLASSIFICATIONS = {
    "safe_no_op",
    "no_qualifying_material",
    "nothing_to_publish",
    "already_published",
    "not_release_ready",
    "invalid_configuration",
    "missing_credentials",
    "import_error",
    "schema_corruption",
    "repository_divergence",
    "unsafe_dirty_state",
    "approval_failure",
    "terminal_accounting_inconsistency",
}

PUBLICATION_TASK_KEYS = {
    "food_line_daily_publish",
    "care_line_approved_release_publication",
}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    min_backoff_minutes: int = 30
    recovery_window_minutes: int = 240


EXPECTATIONS_BY_DISPATCH = {
    "food-line": FOOD_LINE_TASK_EXPECTATIONS,
    "care-line": CARE_LINE_TASK_EXPECTATIONS,
    "ice": MIGRATION_TASK_EXPECTATIONS["ice"],
}


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def expected_datetime(date: str, expectation: TaskExpectation, expected_instance: dict[str, Any] | None = None) -> datetime | None:
    if expected_instance and expected_instance.get("scheduled_for"):
        parsed = parse_timestamp(str(expected_instance["scheduled_for"]))
        return _utc(parsed) if parsed else None
    if expectation.expected_time == "configured scheduler time":
        return None
    local = datetime.fromisoformat(f"{date}T{expectation.expected_time}:00").replace(
        tzinfo=ZoneInfo(expectation.timezone)
    )
    return local.astimezone(timezone.utc)


def _receipt_time(receipt: dict[str, Any]) -> datetime | None:
    for key in ("completed_at", "observed_at", "started_at"):
        parsed = parse_timestamp(str(receipt.get(key) or ""))
        if parsed is not None:
            return _utc(parsed)
    return None


def _matches(receipt: dict[str, Any], task_key: str, scheduled: datetime | None, date: str) -> bool:
    if receipt.get("task_key") != task_key:
        return False
    if scheduled is None:
        return str(receipt.get("scheduled_for") or "").startswith(date)
    receipt_time = _receipt_time(receipt)
    if receipt_time is None:
        return False
    return scheduled - timedelta(minutes=10) <= receipt_time <= scheduled + timedelta(hours=12)


def _latest_matching_receipt(
    receipts: list[dict[str, Any]],
    task_key: str,
    scheduled: datetime | None,
    date: str,
) -> dict[str, Any] | None:
    matches = [item for item in receipts if _matches(item, task_key, scheduled, date)]
    if not matches:
        return None
    return max(matches, key=lambda item: _receipt_time(item) or datetime.min.replace(tzinfo=timezone.utc))


def _is_retryable(receipt: dict[str, Any]) -> bool:
    classification = str(receipt.get("classification") or "").lower()
    if receipt.get("task_key") in PUBLICATION_TASK_KEYS:
        return False
    if classification in NONRETRYABLE_CLASSIFICATIONS:
        return False
    if classification in RETRYABLE_CLASSIFICATIONS:
        return True
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    return any(str(value).lower() in RETRYABLE_CLASSIFICATIONS for value in details.values())


def _receipt_status(receipt: dict[str, Any] | None) -> str | None:
    return str(receipt.get("status") or "") if receipt else None


def _receipt_classification(receipt: dict[str, Any] | None) -> str | None:
    return str(receipt.get("classification") or "") if receipt else None


def _details(receipt: dict[str, Any] | None) -> dict[str, Any]:
    value = receipt.get("details") if isinstance(receipt, dict) else None
    return value if isinstance(value, dict) else {}


def _task_receipt(receipt: dict[str, Any], source_root: Path) -> dict[str, Any] | None:
    refs = receipt.get("artifact_refs") if isinstance(receipt.get("artifact_refs"), dict) else {}
    raw_path = str(refs.get("task_receipt") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = source_root / path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _newer_receipts(
    receipts: list[dict[str, Any]],
    *,
    task_key: str,
    after: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for receipt in receipts:
        if receipt.get("task_key") != task_key:
            continue
        observed = _receipt_time(receipt)
        if observed is None or observed <= after:
            continue
        rows.append(receipt)
    return sorted(rows, key=lambda item: _receipt_time(item) or datetime.min.replace(tzinfo=timezone.utc))


def _receipt_text(receipt: dict[str, Any], key: str) -> str:
    return str(receipt.get(key) or "").strip()


def _detail_text(receipt: dict[str, Any], key: str) -> str:
    return str(_details(receipt).get(key) or "").strip()


def _source_state_agrees_with_record(
    *,
    source_root: Path,
    date: str,
    run_id: str,
    source_status: str,
    export_status: str,
) -> bool:
    if not run_id:
        return False
    record_path = source_root / "status" / "food-line" / "runs" / f"{date}.json"
    state_path = source_root / "data" / "dispatches" / "food-line" / "discovery-runs" / date / run_id / "run-state.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict) or not isinstance(state, dict):
        return False
    if record.get("edition_date") != date or state.get("edition_date") != date:
        return False
    if str(record.get("run_id") or "") != run_id or str(state.get("run_id") or "") != run_id:
        return False
    state_status = str(state.get("status") or "")
    if state_status != source_status or state_status not in FOOD_SOURCE_QUALIFYING_STATUSES:
        return False
    record_status = str(record.get("source_watch_status") or record.get("last_status") or "")
    if record_status != source_status:
        return False
    export = state.get("agent_export") if isinstance(state.get("agent_export"), dict) else {}
    if str(export.get("status") or "") != export_status or export_status not in FOOD_SOURCE_QUALIFYING_EXPORT_STATUSES:
        return False
    if export_status == "no_exportable_findings":
        return True
    export_path = str(export.get("path") or "").strip()
    if not export_path:
        return False
    path = Path(export_path)
    if not path.is_absolute():
        path = source_root / path
    return path.is_file()


def _upstream_receipt_is_intake_ready(
    receipt: dict[str, Any],
    *,
    source_root: Path,
    date: str,
) -> bool:
    if receipt.get("task_key") not in FOOD_SOURCE_TERMINAL_TASK_KEYS:
        return False
    if receipt.get("status") not in {OperationalStatus.SUCCESS.value, OperationalStatus.DEGRADED.value, OperationalStatus.SAFE_NO_OP.value}:
        return False
    classification = str(receipt.get("classification") or "").lower()
    if classification not in FOOD_SOURCE_TERMINAL_CLASSIFICATIONS:
        return False
    task_receipt = _task_receipt(receipt, source_root) or {}
    run_id = _receipt_text(receipt, "run_id") or str(task_receipt.get("run_id") or "")
    source_status = (
        str(task_receipt.get("final_status") or "")
        or _detail_text(receipt, "source_status")
        or classification
    )
    export_status = (
        str(task_receipt.get("export_status") or "")
        or _detail_text(receipt, "source_export_status")
    )
    receipt_date = str(task_receipt.get("edition_date") or _detail_text(receipt, "edition_date") or receipt.get("scheduled_for") or "")[:10]
    if receipt_date != date:
        return False
    return _source_state_agrees_with_record(
        source_root=source_root,
        date=date,
        run_id=run_id,
        source_status=source_status,
        export_status=export_status,
    )


def _current_intake_dependency_recovered(
    *,
    source_root: Path,
    date: str,
    receipts: list[dict[str, Any]],
    receipt: dict[str, Any],
) -> bool:
    classification = str(receipt.get("classification") or "").lower()
    detail_status = str(_details(receipt).get("intake_status") or "").lower()
    if classification not in CURRENT_INTAKE_DEPENDENCY_BLOCKED_CLASSIFICATIONS and detail_status not in CURRENT_INTAKE_DEPENDENCY_BLOCKED_CLASSIFICATIONS:
        return False
    intake_time = _receipt_time(receipt)
    if intake_time is None:
        return False
    for later in _newer_receipts(receipts, task_key="food_line_current_intake", after=intake_time):
        later_status = str(later.get("status") or "")
        later_classification = str(later.get("classification") or "").lower()
        if later_status in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value}:
            return False
        if later_status in {OperationalStatus.FAILED.value, OperationalStatus.DEGRADED.value} and later_classification not in CURRENT_INTAKE_DEPENDENCY_BLOCKED_CLASSIFICATIONS:
            return False
    upstream = [
        item for item in receipts
        if item.get("task_key") in FOOD_SOURCE_TERMINAL_TASK_KEYS
        and (_receipt_time(item) is not None and _receipt_time(item) > intake_time)
    ]
    if not upstream:
        return False
    ready = [item for item in upstream if _upstream_receipt_is_intake_ready(item, source_root=source_root, date=date)]
    return bool(ready) and len(ready) == len(upstream)


def evaluate_recovery(
    *,
    dispatch: str,
    source_root: Path,
    date: str,
    evaluated_at: str,
    expected_instances: Iterable[dict[str, Any]] | None = None,
    attempts_by_instance: dict[str, int] | None = None,
    policy: RetryPolicy = RetryPolicy(),
) -> dict[str, Any]:
    if dispatch not in EXPECTATIONS_BY_DISPATCH:
        raise ValueError(f"unsupported recovery dispatch: {dispatch}")
    evaluated = parse_timestamp(evaluated_at)
    if evaluated is None:
        raise ValueError(f"invalid evaluated_at: {evaluated_at}")
    evaluated = _utc(evaluated)
    receipts = load_operational_receipts(source_root, dispatch, date)
    instance_rows = list(expected_instances or [])
    attempts_by_instance = attempts_by_instance or {}
    evaluations: list[dict[str, Any]] = []

    for expectation in EXPECTATIONS_BY_DISPATCH[dispatch]:
        task_instances = [row for row in instance_rows if row.get("task_key") == expectation.task_key]
        if not task_instances:
            task_instances = [{"task_key": expectation.task_key, "scheduled_for": None}]
        for instance in task_instances:
            scheduled = expected_datetime(date, expectation, instance)
            instance_id = str(instance.get("scheduled_for") or f"{date}:{expectation.task_key}")
            grace_end = scheduled + timedelta(minutes=expectation.grace_minutes) if scheduled else None
            recovery_deadline = grace_end + timedelta(minutes=policy.recovery_window_minutes) if grace_end else None
            receipt = _latest_matching_receipt(receipts, expectation.task_key, scheduled, date)
            attempts = attempts_by_instance.get(instance_id, 0)
            if scheduled and evaluated < scheduled:
                state = InstanceState.NOT_YET_DUE
                recommendation = RecoveryRecommendation.WAITING_FOR_SCHEDULE
            elif grace_end and evaluated < grace_end and receipt is None:
                state = InstanceState.GRACE_ACTIVE
                recommendation = RecoveryRecommendation.WAITING_FOR_GRACE
            elif receipt is None:
                if recovery_deadline and evaluated > recovery_deadline:
                    state = InstanceState.MISSED
                    recommendation = RecoveryRecommendation.RECOVERY_WINDOW_EXPIRED
                else:
                    state = InstanceState.MISSED
                    recommendation = RecoveryRecommendation.MISSED
            else:
                status = _receipt_status(receipt) or ""
                if status in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value}:
                    state = InstanceState.RECOVERED
                    recommendation = RecoveryRecommendation.NO_ACTION
                elif status == OperationalStatus.UPSTREAM_BLOCKED.value:
                    if (
                        dispatch == "food-line"
                        and expectation.task_key == "food_line_current_intake"
                        and _current_intake_dependency_recovered(
                            source_root=source_root,
                            date=date,
                            receipts=receipts,
                            receipt=receipt,
                        )
                    ):
                        if recovery_deadline and evaluated > recovery_deadline:
                            state = InstanceState.FAILED_NONRETRYABLE
                            recommendation = RecoveryRecommendation.RECOVERY_WINDOW_EXPIRED
                        elif attempts >= policy.max_attempts:
                            state = InstanceState.FAILED_NONRETRYABLE
                            recommendation = RecoveryRecommendation.MANUAL_ATTENTION
                        else:
                            state = InstanceState.FAILED_RETRYABLE
                            recommendation = RecoveryRecommendation.RETRY_ELIGIBLE
                    else:
                        state = InstanceState.FAILED_NONRETRYABLE
                        recommendation = RecoveryRecommendation.UPSTREAM_BLOCKED
                elif status in {OperationalStatus.FAILED.value, OperationalStatus.DEGRADED.value} and _is_retryable(receipt):
                    if recovery_deadline and evaluated > recovery_deadline:
                        state = InstanceState.FAILED_NONRETRYABLE
                        recommendation = RecoveryRecommendation.RECOVERY_WINDOW_EXPIRED
                    elif attempts >= policy.max_attempts:
                        state = InstanceState.FAILED_NONRETRYABLE
                        recommendation = RecoveryRecommendation.MANUAL_ATTENTION
                    else:
                        state = InstanceState.FAILED_RETRYABLE
                        recommendation = RecoveryRecommendation.RETRY_ELIGIBLE
                elif status == OperationalStatus.DEGRADED.value:
                    state = InstanceState.DEGRADED_TERMINAL
                    recommendation = RecoveryRecommendation.NO_ACTION
                else:
                    state = InstanceState.FAILED_NONRETRYABLE
                    recommendation = RecoveryRecommendation.MANUAL_ATTENTION
            receipt_status_value = _receipt_status(receipt)
            evaluations.append({
                "task_key": expectation.task_key,
                "instance_id": instance_id,
                "scheduled_for": scheduled.isoformat().replace("+00:00", "Z") if scheduled else None,
                "grace_end": grace_end.isoformat().replace("+00:00", "Z") if grace_end else None,
                "recovery_deadline": recovery_deadline.isoformat().replace("+00:00", "Z") if recovery_deadline else None,
                "state": state.value,
                "recommendation": recommendation.value,
                "receipt_status": receipt_status_value,
                "classification": (
                    DEPENDENCY_RECOVERED_CURRENT_INTAKE
                    if (
                        receipt
                        and expectation.task_key == "food_line_current_intake"
                        and recommendation == RecoveryRecommendation.RETRY_ELIGIBLE
                        and receipt_status_value == OperationalStatus.UPSTREAM_BLOCKED.value
                    )
                    else _receipt_classification(receipt)
                ),
                "retry_eligible": recommendation == RecoveryRecommendation.RETRY_ELIGIBLE,
                "attempts": attempts,
                "max_attempts": policy.max_attempts,
                "publication_task": expectation.task_key in PUBLICATION_TASK_KEYS,
            })

    priority = [
        RecoveryRecommendation.RETRY_ELIGIBLE.value,
        RecoveryRecommendation.MANUAL_ATTENTION.value,
        RecoveryRecommendation.RECOVERY_WINDOW_EXPIRED.value,
        RecoveryRecommendation.MISSED.value,
        RecoveryRecommendation.UPSTREAM_BLOCKED.value,
        RecoveryRecommendation.WAITING_FOR_GRACE.value,
        RecoveryRecommendation.WAITING_FOR_SCHEDULE.value,
        RecoveryRecommendation.NO_ACTION.value,
    ]
    overall = next(
        item for item in priority
        if any(row["recommendation"] == item for row in evaluations)
    )
    return {
        "schema_version": "bluefern_scheduled_recovery_v1",
        "dispatch": dispatch,
        "date": date,
        "evaluated_at": evaluated_at,
        "overall_recommendation": overall,
        "automatic_execution": False,
        "retry_policy": {
            "max_attempts": policy.max_attempts,
            "min_backoff_minutes": policy.min_backoff_minutes,
            "recovery_window_minutes": policy.recovery_window_minutes,
        },
        "retryable_classifications": sorted(RETRYABLE_CLASSIFICATIONS),
        "nonretryable_classifications": sorted(NONRETRYABLE_CLASSIFICATIONS),
        "publication_retry_policy": "MANUAL_ATTENTION",
        "instances": evaluations,
    }


def dumps_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"
