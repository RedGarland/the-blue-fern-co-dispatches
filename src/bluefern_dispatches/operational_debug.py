from __future__ import annotations

from typing import Any


ATTENTION_STATUSES = {
    "FAILED",
    "MISSED",
    "STALE_OBSERVABILITY",
    "UPSTREAM_BLOCKED",
    "DEGRADED",
    "UNKNOWN",
}

ATTENTION_PRIORITY = {
    "FAILED": 0,
    "MISSED": 1,
    "STALE_OBSERVABILITY": 2,
    "UPSTREAM_BLOCKED": 3,
    "DEGRADED": 4,
    "UNKNOWN": 5,
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _task_sort_key(task: dict[str, Any]) -> tuple[int, str, str]:
    status = _text(task.get("status")) or "UNKNOWN"
    completed = _text(task.get("completed_at")) or ""
    task_key = _text(task.get("task_key")) or ""
    return (ATTENTION_PRIORITY.get(status, 99), completed, task_key)


def _attention_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = _text(task.get("status")) or "UNKNOWN"
        if status not in ATTENTION_STATUSES:
            continue
        diagnostics = task.get("diagnostics") if isinstance(task.get("diagnostics"), dict) else {}
        failure = diagnostics.get("failure") if isinstance(diagnostics.get("failure"), dict) else {}
        rows.append(
            {
                "task_key": _text(task.get("task_key")),
                "status": status,
                "classification": _text(task.get("classification")),
                "failure_stage": _text(failure.get("failure_stage") or diagnostics.get("failure_stage")),
                "exit_code": task.get("exit_code"),
                "artifact_id": _text(task.get("artifact_id")),
            }
        )
    return sorted(rows, key=_task_sort_key)


def _failure_layer(*, aggregate_status: str, primary: dict[str, Any] | None, source_summary: dict[str, Any]) -> str:
    if aggregate_status == "MISSED":
        return "SCHEDULER_OR_OBSERVABILITY"
    if aggregate_status == "STALE_OBSERVABILITY":
        return "OBSERVABILITY"
    if source_summary.get("failed_source_count"):
        if source_summary.get("all_current_failures_external") is True:
            return "EXTERNAL_DEPENDENCY"
        if _int(source_summary.get("unclassified_source_failure_count")):
            return "SOURCE_CLASSIFICATION"
        return "SOURCE"
    if not primary:
        return "NONE" if aggregate_status in {"SUCCESS", "SAFE_NO_OP"} else "UNKNOWN"
    status = _text(primary.get("status")) or "UNKNOWN"
    stage = (_text(primary.get("failure_stage")) or _text(primary.get("task_key")) or "").lower()
    classification = (_text(primary.get("classification")) or "").lower()
    if status == "UPSTREAM_BLOCKED":
        return "UPSTREAM_HANDOFF"
    if "wrapper" in stage or "wrapper" in classification:
        return "WRAPPER"
    if "publication" in stage or "publish" in stage:
        return "PUBLICATION"
    if "intake" in stage or "handoff" in classification:
        return "HANDOFF"
    if "collection" in stage or "source" in stage:
        return "SOURCE"
    return "TASK"


def _operator_assessment(
    *,
    aggregate_status: str,
    recovery_lifecycle: str | None,
    primary: dict[str, Any] | None,
    source_summary: dict[str, Any],
    stale_observability: bool,
) -> str:
    if stale_observability or aggregate_status == "STALE_OBSERVABILITY":
        return "ACTION_REQUIRED_OBSERVABILITY"
    if aggregate_status in {"FAILED", "MISSED", "UNKNOWN"}:
        return "FAILED_ACTION_REQUIRED"
    if aggregate_status in {"SUCCESS", "SAFE_NO_OP"}:
        return "HEALTHY"
    if aggregate_status != "DEGRADED":
        return "ACTION_REQUIRED"

    if recovery_lifecycle == "HEALTHY":
        if source_summary.get("all_current_failures_external") is True:
            return "HEALTHY_WITH_EXTERNAL_RESTRICTIONS"
        if primary and primary.get("task_key") == "food_line_source_watch":
            classification = (_text(primary.get("classification")) or "").lower()
            if classification in {"completed_with_exclusions", "success_with_exclusions"}:
                return "HEALTHY_WITH_SOURCE_EXCLUSIONS"
    return "DEGRADED_ACTION_RECOMMENDED"


def build_debug_summary(status: dict[str, Any]) -> dict[str, Any]:
    """Build a small, dispatch-neutral explanation of current operational state."""

    aggregate_status = _text(status.get("aggregate_status")) or "UNKNOWN"
    recovery_lifecycle = _text(status.get("recovery_lifecycle"))
    tasks_value = status.get("effective_task_summaries")
    if not isinstance(tasks_value, list):
        tasks_value = status.get("task_summaries")
    tasks = [task for task in tasks_value or [] if isinstance(task, dict)]
    attention = _attention_tasks(tasks)
    primary = attention[0] if attention else None
    source_summary = status.get("source_failure_summary")
    if not isinstance(source_summary, dict):
        source_summary = {}
    unaccounted_count = _int(status.get("unaccounted_event_count"))
    primary_layer = _failure_layer(
        aggregate_status=aggregate_status,
        primary=primary,
        source_summary=source_summary,
    )
    if aggregate_status == "FAILED" and primary is None and unaccounted_count:
        primary_layer = "HANDOFF"
    publication_attempted = status.get("publication_attempted")
    stale_observability = bool(status.get("stale_observability"))
    return {
        "aggregate_status": aggregate_status,
        "recovery_lifecycle": recovery_lifecycle,
        "operator_assessment": _operator_assessment(
            aggregate_status=aggregate_status,
            recovery_lifecycle=recovery_lifecycle,
            primary=primary,
            source_summary=source_summary,
            stale_observability=stale_observability,
        ),
        "primary_layer": primary_layer,
        "primary_task_key": primary.get("task_key") if primary else None,
        "primary_task_status": primary.get("status") if primary else None,
        "primary_classification": primary.get("classification") if primary else None,
        "primary_failure_stage": primary.get("failure_stage") if primary else None,
        "attention_task_count": len(attention),
        "attention_tasks": attention[:5],
        "failed_source_count": _int(source_summary.get("failed_source_count")),
        "external_access_restriction_count": _int(source_summary.get("external_access_restriction_count")),
        "unclassified_source_failure_count": _int(source_summary.get("unclassified_source_failure_count")),
        "unaccounted_event_count": unaccounted_count,
        "all_current_failures_external": source_summary.get("all_current_failures_external")
        if "all_current_failures_external" in source_summary
        else None,
        "publication_attempted": publication_attempted if isinstance(publication_attempted, bool) else None,
        "publication_status": _text(status.get("publication_status")),
        "stale_observability": stale_observability,
        "receipt_completeness": _text(status.get("receipt_completeness")),
    }
