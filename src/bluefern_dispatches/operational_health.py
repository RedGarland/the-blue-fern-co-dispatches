from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


RECEIPT_SCHEMA_VERSION = "bluefern_operational_health_receipt_v1"
DISPATCH_AGGREGATE_SCHEMA_VERSION = "bluefern_dispatch_operational_health_v1"
SYSTEM_AGGREGATE_SCHEMA_VERSION = "bluefern_system_operational_health_v1"
OPS_STATUS_PATH = "ops/status"
LOCAL_RECEIPT_ROOT = Path("status") / "operational-health"


class OperationalStatus(StrEnum):
    SUCCESS = "SUCCESS"
    SAFE_NO_OP = "SAFE_NO_OP"
    UPSTREAM_BLOCKED = "UPSTREAM_BLOCKED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    MISSED = "MISSED"
    UNKNOWN = "UNKNOWN"
    STALE_OBSERVABILITY = "STALE_OBSERVABILITY"


class RecoveryState(StrEnum):
    HEALTHY = "HEALTHY"
    INCIDENT_OPEN = "INCIDENT_OPEN"
    RECOVERY_PENDING_RUNTIME_PROOF = "RECOVERY_PENDING_RUNTIME_PROOF"
    RECOVERED = "RECOVERED"


@dataclass(frozen=True)
class TaskExpectation:
    dispatch: str
    task_key: str
    task_name: str
    timezone: str
    cadence: str
    expected_time: str
    grace_minutes: int
    required: bool = True
    success_statuses: tuple[OperationalStatus, ...] = (OperationalStatus.SUCCESS, OperationalStatus.SAFE_NO_OP)
    failure_statuses: tuple[OperationalStatus, ...] = (OperationalStatus.FAILED,)
    upstream_dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoveryContext:
    incident_opened_at: str | None = None
    fix_deployed_at: str | None = None
    runtime_proof_after: str | None = None
    recovered_at: str | None = None


@dataclass(frozen=True)
class ReceiptWriteResult:
    receipt_path: Path
    latest_path: Path


FOOD_LINE_TASK_EXPECTATIONS: tuple[TaskExpectation, ...] = (
    TaskExpectation(
        dispatch="food-line",
        task_key="food_line_source_watch",
        task_name="Blue Fern Food Line Daily Source Watch",
        timezone="America/Los_Angeles",
        cadence="daily",
        expected_time="05:30",
        grace_minutes=90,
    ),
    TaskExpectation(
        dispatch="food-line",
        task_key="food_line_source_watch_resume",
        task_name="Blue Fern Food Line Source Watch Resume",
        timezone="America/Los_Angeles",
        cadence="daily",
        expected_time="06:00",
        grace_minutes=90,
        upstream_dependencies=("food_line_source_watch",),
    ),
    TaskExpectation(
        dispatch="food-line",
        task_key="food_line_current_intake",
        task_name="Blue Fern Food Line Current Intake",
        timezone="America/Los_Angeles",
        cadence="daily",
        expected_time="06:10",
        grace_minutes=90,
        upstream_dependencies=("food_line_source_watch", "food_line_source_watch_resume"),
    ),
    TaskExpectation(
        dispatch="food-line",
        task_key="food_line_daily_publish",
        task_name="Blue Fern Food Line Daily Publish",
        timezone="America/Los_Angeles",
        cadence="daily",
        expected_time="08:30",
        grace_minutes=90,
        upstream_dependencies=("food_line_current_intake",),
    ),
)


# Installed scheduler definitions own Care cadence. These expectations describe
# the source-side execution contract without inventing a production trigger.
CARE_LINE_TASK_EXPECTATIONS: tuple[TaskExpectation, ...] = (
    TaskExpectation(
        dispatch="care-line",
        task_key="care_line_collection",
        task_name="Blue Fern Care Line National Collection",
        timezone="America/Los_Angeles",
        cadence="scheduled",
        expected_time="configured scheduler time",
        grace_minutes=120,
    ),
    TaskExpectation(
        dispatch="care-line",
        task_key="care_line_reviewed_event_queue",
        task_name="Blue Fern Care Line Reviewed Event Queue",
        timezone="America/Los_Angeles",
        cadence="daily",
        expected_time="08:00",
        grace_minutes=120,
        upstream_dependencies=("care_line_collection",),
    ),
    TaskExpectation(
        dispatch="care-line",
        task_key="care_line_approved_release_publication",
        task_name="Blue Fern Care Line Approved Release Publication",
        timezone="America/Los_Angeles",
        cadence="configured",
        expected_time="configured scheduler time",
        grace_minutes=120,
        upstream_dependencies=("care_line_reviewed_event_queue",),
    ),
)


MIGRATION_TASK_EXPECTATIONS: dict[str, tuple[TaskExpectation, ...]] = {
    "gaza": (
        TaskExpectation("gaza", "gaza_daily_dispatch", "Daily - Dispatches From Gaza", "America/Los_Angeles", "daily", "configured scheduler time", 120),
    ),
    "food-line": FOOD_LINE_TASK_EXPECTATIONS,
    "care-line": CARE_LINE_TASK_EXPECTATIONS,
    "ice": (
        TaskExpectation("ice", "ice_monitor", "Daily - ICE Monitor", "America/Los_Angeles", "daily", "21:15", 180),
    ),
    "cascadia": (
        TaskExpectation("cascadia", "cascadia_weekly_dispatch", "Weekly - Cascadia Briefing", "America/Los_Angeles", "weekly", "configured scheduler time", 24 * 60),
    ),
    "american-pressure": (
        TaskExpectation("american-pressure", "american_pressure_candidate_intake", "American Pressure Daily Candidate Intake", "America/Los_Angeles", "daily", "configured scheduler time", 180),
        TaskExpectation("american-pressure", "american_pressure_weekly_publish", "American Pressure Weekly Publish", "America/Los_Angeles", "weekly", "configured scheduler time", 24 * 60),
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "unknown"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_operational_receipt(
    *,
    dispatch: str,
    task_key: str,
    task_name: str,
    scheduled_for: str | None,
    started_at: str | None,
    completed_at: str | None,
    exit_code: int | None,
    status: OperationalStatus | str,
    classification: str,
    run_id: str | None = None,
    failure_stage: str | None = None,
    runner_id: str | None = None,
    runner_path: str | None = None,
    branch: str | None = None,
    source_head: str | None = None,
    next_expected_run: str | None = None,
    public_side_effects: dict[str, Any] | None = None,
    collection_health: str | None = None,
    upstream_dependency_status: str | None = None,
    publication_attempted: bool | None = None,
    publication_status: str | None = None,
    artifact_refs: dict[str, str] | None = None,
    operator_attention_ref: str | None = None,
    details: dict[str, Any] | None = None,
    observed_at: str | None = None,
    receipt_created_at: str | None = None,
) -> dict[str, Any]:
    normalized_status = status.value if isinstance(status, OperationalStatus) else str(status)
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "dispatch": dispatch,
        "task_key": task_key,
        "task_name": task_name,
        "scheduled_for": scheduled_for,
        "started_at": started_at,
        "completed_at": completed_at,
        "observed_at": observed_at or completed_at or started_at or utc_now(),
        "receipt_created_at": receipt_created_at or utc_now(),
        "exit_code": exit_code,
        "status": normalized_status,
        "classification": classification,
        "run_id": run_id,
        "failure_stage": failure_stage,
        "runner_id": runner_id,
        "runner_path": runner_path,
        "branch": branch,
        "source_head": source_head,
        "next_expected_run": next_expected_run,
        "public_side_effects": public_side_effects or {},
        "collection_health": collection_health,
        "upstream_dependency_status": upstream_dependency_status,
        "publication_attempted": publication_attempted,
        "publication_status": publication_status,
        "artifact_refs": artifact_refs or {},
        "operator_attention_ref": operator_attention_ref,
        "details": details or {},
    }


def validate_operational_receipt(receipt: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "dispatch",
        "task_key",
        "task_name",
        "scheduled_for",
        "started_at",
        "completed_at",
        "observed_at",
        "receipt_created_at",
        "exit_code",
        "status",
        "classification",
        "run_id",
        "failure_stage",
        "runner_id",
        "runner_path",
        "branch",
        "source_head",
        "next_expected_run",
        "public_side_effects",
        "details",
    }
    missing = sorted(required - set(receipt))
    if missing:
        raise ValueError(f"operational receipt missing required fields: {missing}")
    if receipt["schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise ValueError("unsupported operational receipt schema")
    if receipt["status"] not in {status.value for status in OperationalStatus}:
        raise ValueError(f"unsupported operational status: {receipt['status']}")
    if not isinstance(receipt.get("public_side_effects"), dict):
        raise ValueError("public_side_effects must be an object")
    if not isinstance(receipt.get("details"), dict):
        raise ValueError("details must be an object")


def write_operational_receipt(root: Path, receipt: dict[str, Any]) -> ReceiptWriteResult:
    validate_operational_receipt(receipt)
    scheduled_for = str(receipt.get("scheduled_for") or "unknown")[:10]
    run_id = _safe_component(str(receipt.get("run_id") or "no-run-id"))
    task_key = _safe_component(str(receipt["task_key"]))
    base = root / LOCAL_RECEIPT_ROOT / str(receipt["dispatch"]) / scheduled_for
    receipt_path = base / "runs" / f"{task_key}-{run_id}.json"
    latest_path = base / "latest.json"
    atomic_write_json(receipt_path, receipt)
    atomic_write_json(latest_path, receipt)
    return ReceiptWriteResult(receipt_path=receipt_path, latest_path=latest_path)


def load_operational_receipts(root: Path, dispatch: str, date: str) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for path in sorted((root / LOCAL_RECEIPT_ROOT / dispatch / date / "runs").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_operational_receipt(value)
        receipts.append(value)
    return receipts


def map_food_line_status(task_key: str, task_status: str, *, exit_code: int | None = None) -> OperationalStatus:
    if task_key == "food_line_source_watch":
        if task_status == "completed":
            return OperationalStatus.SUCCESS
        if task_status == "completed_with_exclusions":
            return OperationalStatus.DEGRADED
        if task_status in {"blocked_overlapping_run", "stale_lock_ambiguous", "failed"}:
            return OperationalStatus.FAILED
    if task_key == "food_line_source_watch_resume":
        if task_status == "resume_not_required":
            return OperationalStatus.SAFE_NO_OP
        if task_status == "resume_qualified":
            return OperationalStatus.SUCCESS
        if task_status in {"source_watch_not_initialized", "source_watch_in_progress", "upstream_blocked"}:
            return OperationalStatus.UPSTREAM_BLOCKED
        if task_status in {"resume_nonqualifying", "status_resume_failed", "failed"}:
            return OperationalStatus.FAILED
    if task_key == "food_line_current_intake":
        if task_status in {"success", "success_with_exclusions"}:
            return OperationalStatus.SUCCESS
        if task_status in {"source_watch_not_initialized", "source_watch_in_progress", "upstream_blocked"}:
            return OperationalStatus.UPSTREAM_BLOCKED
        if task_status in {"skipped_not_release_ready", "no_qualifying_release"}:
            return OperationalStatus.SAFE_NO_OP
        if task_status in {"current_intake_failed", "failed"}:
            return OperationalStatus.FAILED
    if task_key == "food_line_daily_publish":
        if task_status == "published":
            return OperationalStatus.SUCCESS
        if task_status in {"skipped_not_release_ready", "no_qualifying_edition"}:
            return OperationalStatus.SAFE_NO_OP
        if task_status == "failure":
            return OperationalStatus.FAILED
    if exit_code not in (None, 0):
        return OperationalStatus.FAILED
    return OperationalStatus.UNKNOWN


def food_line_task_key(action: str) -> str:
    if action == "source_watch":
        return "food_line_source_watch"
    if action == "status_resume":
        return "food_line_source_watch_resume"
    if action == "current_intake":
        return "food_line_current_intake"
    if action == "daily_publish":
        return "food_line_daily_publish"
    raise ValueError(f"unknown Food Line action: {action}")


def food_line_task_name(task_key: str) -> str:
    for expectation in FOOD_LINE_TASK_EXPECTATIONS:
        if expectation.task_key == task_key:
            return expectation.task_name
    raise ValueError(f"unknown Food Line task key: {task_key}")


def build_food_line_operational_receipt(
    *,
    action: str,
    scheduled_for: str,
    started_at: str | None,
    completed_at: str | None,
    exit_code: int | None,
    task_status: str,
    classification: str | None = None,
    run_id: str | None = None,
    runner_path: str | None = None,
    branch: str | None = None,
    source_head: str | None = None,
    public_side_effects: dict[str, Any] | None = None,
    collection_health: str | None = None,
    upstream_dependency_status: str | None = None,
    publication_attempted: bool | None = None,
    publication_status: str | None = None,
    artifact_refs: dict[str, str] | None = None,
    operator_attention_ref: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_key = food_line_task_key(action)
    status = map_food_line_status(task_key, task_status, exit_code=exit_code)
    return build_operational_receipt(
        dispatch="food-line",
        task_key=task_key,
        task_name=food_line_task_name(task_key),
        scheduled_for=scheduled_for,
        started_at=started_at,
        completed_at=completed_at,
        exit_code=exit_code,
        status=status,
        classification=classification or task_status,
        run_id=run_id,
        failure_stage=None if status not in {OperationalStatus.FAILED, OperationalStatus.DEGRADED} else action,
        runner_path=runner_path,
        branch=branch,
        source_head=source_head,
        public_side_effects=public_side_effects or {},
        collection_health=collection_health,
        upstream_dependency_status=upstream_dependency_status,
        publication_attempted=publication_attempted,
        publication_status=publication_status,
        artifact_refs=artifact_refs,
        operator_attention_ref=operator_attention_ref,
        details=details,
    )


def map_care_line_status(task_key: str, task_status: str, *, exit_code: int | None = None) -> OperationalStatus:
    if task_key == "care_line_collection":
        if task_status in {"success", "partial_success", "completed"}:
            return OperationalStatus.DEGRADED if task_status == "partial_success" else OperationalStatus.SUCCESS
        if task_status in {"already_running", "safe_no_op"}:
            return OperationalStatus.SAFE_NO_OP
        if task_status in {"upstream_blocked", "source_state_blocked"}:
            return OperationalStatus.UPSTREAM_BLOCKED
    elif task_key == "care_line_reviewed_event_queue":
        if task_status in {"ready_for_operator_release", "success"}:
            return OperationalStatus.SUCCESS
        if task_status in {"nothing_to_publish", "already_running", "safe_no_op"}:
            return OperationalStatus.SAFE_NO_OP
        if task_status in {"upstream_blocked", "no_collection_receipt"}:
            return OperationalStatus.UPSTREAM_BLOCKED
    elif task_key == "care_line_approved_release_publication":
        if task_status in {"publication_success", "published"}:
            return OperationalStatus.SUCCESS
        if task_status in {"safe_no_op", "no_approved_release", "already_published", "already_running"}:
            return OperationalStatus.SAFE_NO_OP
        if task_status in {"upstream_blocked", "not_release_ready"}:
            return OperationalStatus.UPSTREAM_BLOCKED
    if exit_code not in (None, 0):
        return OperationalStatus.FAILED
    return OperationalStatus.UNKNOWN


def build_care_line_operational_receipt(
    *,
    task_key: str,
    scheduled_for: str,
    started_at: str | None,
    completed_at: str | None,
    exit_code: int | None,
    task_status: str,
    run_id: str | None = None,
    runner_path: str | None = None,
    branch: str | None = None,
    source_head: str | None = None,
    public_side_effects: dict[str, Any] | None = None,
    collection_health: str | None = None,
    upstream_dependency_status: str | None = None,
    publication_attempted: bool | None = None,
    publication_status: str | None = None,
    artifact_refs: dict[str, str] | None = None,
    operator_attention_ref: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    names = {item.task_key: item.task_name for item in CARE_LINE_TASK_EXPECTATIONS}
    if task_key not in names:
        raise ValueError(f"unknown Care Line task key: {task_key}")
    status = map_care_line_status(task_key, task_status, exit_code=exit_code)
    return build_operational_receipt(
        dispatch="care-line",
        task_key=task_key,
        task_name=names[task_key],
        scheduled_for=scheduled_for,
        started_at=started_at,
        completed_at=completed_at,
        exit_code=exit_code,
        status=status,
        classification=task_status,
        run_id=run_id,
        failure_stage=task_key if status in {OperationalStatus.FAILED, OperationalStatus.DEGRADED} else None,
        runner_path=runner_path,
        branch=branch,
        source_head=source_head,
        public_side_effects=public_side_effects or {},
        collection_health=collection_health,
        upstream_dependency_status=upstream_dependency_status,
        publication_attempted=publication_attempted,
        publication_status=publication_status,
        artifact_refs=artifact_refs,
        operator_attention_ref=operator_attention_ref,
        details=details,
    )


def evaluate_dispatch_health(
    *,
    dispatch: str,
    receipts: Iterable[dict[str, Any]],
    expectations: Iterable[TaskExpectation],
    evaluated_at: str,
    recovery: RecoveryContext | None = None,
    expected_instances: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    receipt_by_task: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        receipt_by_task[str(receipt["task_key"])] = receipt

    expected = list(expectations)
    completed: list[str] = []
    missed: list[str] = []
    failed: list[str] = []
    degraded: list[str] = []
    upstream_blocked: list[str] = []
    stale: list[str] = []
    latest_success: datetime | None = None
    evaluated_dt = parse_timestamp(evaluated_at) or datetime.now(timezone.utc)

    instance_rows = list(expected_instances or [])
    instances_by_task: dict[str, list[dict[str, Any]]] = {}
    for instance in instance_rows:
        instances_by_task.setdefault(str(instance.get("task_key") or ""), []).append(instance)

    for expectation in expected:
        task_instances = instances_by_task.get(expectation.task_key)
        if instance_rows and not task_instances:
            # A supplied instance schedule is authoritative for this
            # evaluation window; absent future/configured tasks are not inferred
            # as missed without an elapsed instance.
            continue
        if task_instances:
            task_receipts = [receipt for receipt in receipts if str(receipt.get("task_key")) == expectation.task_key]
            for instance in task_instances:
                scheduled = parse_timestamp(str(instance.get("scheduled_for") or ""))
                if scheduled is not None and scheduled.tzinfo is None:
                    scheduled = scheduled.replace(tzinfo=timezone.utc)
                due = scheduled is None or evaluated_dt >= scheduled + timedelta(minutes=expectation.grace_minutes)
                if not due:
                    continue
                receipt = next(
                    (
                        candidate for candidate in task_receipts
                        if str(candidate.get("scheduled_for") or "") == str(instance.get("scheduled_for") or "")
                    ),
                    None,
                )
                if receipt is None:
                    if expectation.required:
                        missed.append(f"{expectation.task_key}:{instance.get('scheduled_for')}")
                    continue
                completed.append(f"{expectation.task_key}:{instance.get('scheduled_for')}")
                status = str(receipt["status"])
                observed = parse_timestamp(str(receipt.get("observed_at") or ""))
                if observed and evaluated_dt - observed > timedelta(minutes=max(expectation.grace_minutes * 2, 1)):
                    stale.append(f"{expectation.task_key}:{instance.get('scheduled_for')}")
                if status == OperationalStatus.FAILED.value:
                    failed.append(f"{expectation.task_key}:{instance.get('scheduled_for')}")
                elif status == OperationalStatus.DEGRADED.value:
                    degraded.append(f"{expectation.task_key}:{instance.get('scheduled_for')}")
                elif status == OperationalStatus.UPSTREAM_BLOCKED.value:
                    upstream_blocked.append(f"{expectation.task_key}:{instance.get('scheduled_for')}")
                elif status in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value} and observed:
                    latest_success = max(latest_success, observed) if latest_success else observed
            continue

        receipt = receipt_by_task.get(expectation.task_key)
        if receipt is None:
            if expectation.required:
                missed.append(expectation.task_key)
            continue
        completed.append(expectation.task_key)
        status = str(receipt["status"])
        observed = parse_timestamp(str(receipt.get("observed_at") or ""))
        if observed and evaluated_dt - observed > timedelta(minutes=max(expectation.grace_minutes * 2, 1)):
            stale.append(expectation.task_key)
        if status == OperationalStatus.FAILED.value:
            failed.append(expectation.task_key)
        elif status == OperationalStatus.DEGRADED.value:
            degraded.append(expectation.task_key)
        elif status == OperationalStatus.UPSTREAM_BLOCKED.value:
            upstream_blocked.append(expectation.task_key)
        elif status in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value} and observed:
            latest_success = max(latest_success, observed) if latest_success else observed

    if stale:
        overall = OperationalStatus.STALE_OBSERVABILITY
    elif failed:
        overall = OperationalStatus.FAILED
    elif missed:
        overall = OperationalStatus.MISSED
    elif degraded or upstream_blocked:
        overall = OperationalStatus.DEGRADED
    else:
        overall = OperationalStatus.SUCCESS

    recovery_state = RecoveryState.HEALTHY
    if recovery and recovery.incident_opened_at:
        recovery_state = RecoveryState.INCIDENT_OPEN
        if recovery.fix_deployed_at:
            recovery_state = RecoveryState.RECOVERY_PENDING_RUNTIME_PROOF
            proof_after = parse_timestamp(recovery.runtime_proof_after) or parse_timestamp(recovery.fix_deployed_at)
            if proof_after and latest_success and latest_success > proof_after:
                recovery_state = RecoveryState.RECOVERED

    return {
        "schema_version": DISPATCH_AGGREGATE_SCHEMA_VERSION,
        "dispatch": dispatch,
        "evaluated_at": evaluated_at,
        "overall_health": overall.value,
        "recovery_state": recovery_state.value,
        "expected_tasks": [task.task_key for task in expected],
        "completed_tasks": completed,
        "missed_tasks": missed,
        "failed_tasks": failed,
        "degraded_tasks": degraded,
        "upstream_blocked_tasks": upstream_blocked,
        "stale_observability": stale,
        "latest_success_at": latest_success.isoformat().replace("+00:00", "Z") if latest_success else None,
    }


def evaluate_system_health(dispatch_states: Iterable[dict[str, Any]], *, evaluated_at: str) -> dict[str, Any]:
    states = list(dispatch_states)
    health_values = {state.get("overall_health") for state in states}
    if OperationalStatus.FAILED.value in health_values:
        system_health = OperationalStatus.FAILED
    elif OperationalStatus.MISSED.value in health_values:
        system_health = OperationalStatus.MISSED
    elif OperationalStatus.STALE_OBSERVABILITY.value in health_values:
        system_health = OperationalStatus.STALE_OBSERVABILITY
    elif OperationalStatus.DEGRADED.value in health_values:
        system_health = OperationalStatus.DEGRADED
    else:
        system_health = OperationalStatus.SUCCESS

    return {
        "schema_version": SYSTEM_AGGREGATE_SCHEMA_VERSION,
        "evaluated_at": evaluated_at,
        "system_health": system_health.value,
        "dispatch_states": {str(state["dispatch"]): state for state in states},
        "incidents_open": [state["dispatch"] for state in states if state.get("recovery_state") == RecoveryState.INCIDENT_OPEN.value],
        "recovery_pending": [state["dispatch"] for state in states if state.get("recovery_state") == RecoveryState.RECOVERY_PENDING_RUNTIME_PROOF.value],
        "stale_observability": [state["dispatch"] for state in states if state.get("overall_health") == OperationalStatus.STALE_OBSERVABILITY.value],
    }


def public_site_ignored_context(*, public_edition_date: str | None = None, pages_commit: str | None = None) -> dict[str, Any]:
    return {"public_edition_date": public_edition_date, "pages_commit": pages_commit, "authoritative": False}


def proposed_external_status_paths(dispatch: str, date: str) -> dict[str, str]:
    return {
        "dispatch_latest": f"{OPS_STATUS_PATH}/{dispatch}/latest.json",
        "dispatch_history": f"{OPS_STATUS_PATH}/{dispatch}/history/{date}.json",
        "system_latest": f"{OPS_STATUS_PATH}/system/latest.json",
    }


def ops_status_is_excluded_from_pages(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return normalized == OPS_STATUS_PATH or normalized.startswith(f"{OPS_STATUS_PATH}/")
