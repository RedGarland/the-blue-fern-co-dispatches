from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

from .operational_health import (
    DISPATCH_AGGREGATE_SCHEMA_VERSION,
    CARE_LINE_TASK_EXPECTATIONS,
    FOOD_LINE_TASK_EXPECTATIONS,
    GAZA_TASK_EXPECTATIONS,
    MIGRATION_TASK_EXPECTATIONS,
    OperationalStatus,
    RecoveryContext,
    RecoveryState,
    RECEIPT_SCHEMA_VERSION,
    atomic_write_json,
    evaluate_dispatch_health,
    parse_timestamp,
    validate_operational_receipt,
)
from .operational_debug import build_debug_summary
from .scheduled_recovery import evaluate_recovery, food_source_receipt_is_durably_ready
from .source_replay import load_care_line_source_replay_receipts


EXTERNAL_STATUS_SCHEMA_VERSION = "bluefern_external_operational_status_v1"
SYSTEM_STATUS_SCHEMA_VERSION = "bluefern_external_system_status_v1"
SUPPORTED_TASK_STATUSES = {status.value for status in OperationalStatus}
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
HEX_HEAD_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GAZA_RUNNER_LOG_RE = re.compile(r"^runner-gaza-(?P<date>\d{8})-\d{6}\.log$")
GAZA_DAILY_LOG_RE = re.compile(r"^gaza-daily-(?P<date>\d{8})-\d{6}\.log$")
GAZA_LOG_FIELD_RE = re.compile(r"^(?:summary\.)?(?P<key>[a-z_]+):\s*(?P<value>.*)$", re.IGNORECASE)
PRIVATE_KEY_RE = re.compile(r"(?:path|body|excerpt|source|raw|secret|token|credential|password|environment|env)", re.IGNORECASE)
FAILURE_DIAGNOSTIC_KEYS = {
    "child_error_message",
    "child_error_type",
    "child_exit_code",
    "child_stderr_tail",
    "child_stdout_tail",
    "child_terminal_status",
    "child_validation_error",
    "collection_health",
    "error",
    "error_message",
    "error_type",
    "export_status",
    "failed_source_count",
    "failure_stage",
    "fatal_error",
    "final_error",
    "final_status",
    "manual_review_count",
    "pipeline_exit_code",
    "pipeline_run_id",
    "pipeline_status",
    "resume_status",
    "selected_event_count",
    "skipped_source_count",
    "status",
    "successful_attempt_count",
    "wrapper_exception_message",
    "wrapper_exception_type",
}
FAILURE_DIAGNOSTIC_TEXT_LIMIT = 1000

NON_MIGRATED_DISPATCHES = ("gaza", "ice", "american-pressure")
INTENTIONALLY_INACTIVE_DISPATCHES = ("cascadia",)
ICE_TASK_EXPECTATIONS = MIGRATION_TASK_EXPECTATIONS["ice"]
HANDOFF_STATES = {
    "NO_EXTERNAL_HANDOFF_EXPECTED",
    "HANDOFF_RECEIVED_SUCCESS",
    "HANDOFF_FAILED",
    "HANDOFF_STALE_UNPROCESSED",
}
HANDOFF_TERMINAL_STATUSES = {"SUCCESS", "SAFE_NO_OP", "FAILED"}
RETIREMENT_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
CARE_LINE_SOURCE_REGISTRY_RELATIVE = Path("data") / "dispatches" / "care-line" / "source_registry.json"
CARE_LINE_EXTERNAL_RESTRICTION_CLASSIFICATION = "PERSISTENT_EXTERNAL_ACCESS_RESTRICTION"
FOOD_LINE_PROPOSED_EDITIONS_RELATIVE = (
    Path("data") / "dispatches" / "food-line" / "review" / "proposed-editions"
)
FOOD_LINE_RELEASE_READINESS_RELATIVE = (
    Path("data") / "dispatches" / "food-line" / "review" / "release-readiness"
)
FOOD_LINE_PUBLICATION_APPROVAL_RELATIVE = (
    Path("data") / "dispatches" / "food-line" / "review" / "publication-approval"
)
FOOD_LINE_REVIEW_RELEASES_RELATIVE = (
    Path("data") / "dispatches" / "food-line" / "review" / "releases"
)
FOOD_LINE_PRIVATE_REVIEW_DISPOSITIONS_RELATIVE = (
    Path("data") / "dispatches" / "food-line" / "review" / "private-review-dispositions"
)
FOOD_LINE_RELEASE_READY_STATUSES = {
    "approved_current_review_ready_for_source_generation",
    "approved",
    "release_ready",
    "ready_for_publication",
}
FOOD_LINE_PUBLISHED_STATUSES = {
    "published",
    "publication_success",
    "released",
}
FOOD_LINE_APPROVED_PROPOSAL_STATUSES = {
    "approved",
    "approved_pending_publication",
    "draft_approved_pending_publication",
    "ready_for_publication",
}
FOOD_LINE_PENDING_ITEM_STATUSES = {
    "",
    "pending",
    "pending_review",
    "pending_editorial_review",
    "review_required",
    "needs_review",
    "retained_for_review",
    "draft_pending_editorial_review",
}
FOOD_LINE_DISPOSITIONED_ITEM_STATUSES = {
    "approved",
    "approved_for_publication",
    "publish_candidate",
    "published",
    "publication_success",
    "rejected",
    "reject",
    "duplicate",
    "duplicate_or_stale",
    "stale",
    "weak_food_pressure",
    "needs_source_check",
    "held",
    "hold",
}
FOOD_LINE_PRIVATE_REVIEW_DISPOSITIONS = {
    "published_in_recovery_brief",
    "rejected_or_weak",
    "duplicate_or_stale",
    "needs_source_check",
    "hold",
    "unresolved",
}
FOOD_LINE_PRIVATE_REVIEW_SUBTRACTING_DISPOSITIONS = {
    "published_in_recovery_brief",
    "rejected_or_weak",
    "duplicate_or_stale",
}


class ExportError(RuntimeError):
    """Raised when an external status export cannot be safely completed."""


@dataclass(frozen=True)
class StatusCheckoutGitState:
    state: str
    tracked_status_paths: list[str]
    untracked_status_paths: list[str]
    unexpected_paths: list[str]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def _parse_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read receipt {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"receipt must be an object: {path.name}")
    return value


def _load_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_log_scalar(value: str) -> Any:
    text = value.strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null"}:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _merge_log_json_fields(fields: dict[str, Any], value: Any) -> None:
    if not isinstance(value, dict):
        return
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").lower()
        if isinstance(raw_value, dict):
            _merge_log_json_fields(fields, raw_value)
            continue
        if key:
            fields[key] = raw_value


def _merge_embedded_json_log_fields(fields: dict[str, Any], text: str) -> None:
    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            return
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        _merge_log_json_fields(fields, value)
        index = start + max(end, 1)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_identifier(value: Any) -> str | None:
    text = str(value or "")
    return text if SAFE_KEY_RE.fullmatch(text) else None


def _symbolic_artifact(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    return name if SAFE_KEY_RE.fullmatch(name) else None


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    if PRIVATE_KEY_RE.search(key):
        return None
    if isinstance(value, dict):
        return {
            safe_key: safe_value
            for raw_key, raw_value in value.items()
            if (safe_key := _safe_identifier(raw_key))
            and (safe_value := _sanitize_value(raw_value, key=safe_key)) is not None
        }
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _sanitized_public_side_effects(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _sanitize_value(value)
    return result if isinstance(result, dict) else {}


def _bounded_diagnostic_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        if len(text) > FAILURE_DIAGNOSTIC_TEXT_LIMIT:
            return text[-FAILURE_DIAGNOSTIC_TEXT_LIMIT:]
        return text
    if isinstance(value, list):
        return [_bounded_diagnostic_value(item) for item in value[:10]]
    return None


def _failure_diagnostics(receipt: dict[str, Any]) -> dict[str, Any] | None:
    status = str(receipt.get("status") or "")
    if status not in {
        OperationalStatus.FAILED.value,
        OperationalStatus.DEGRADED.value,
        OperationalStatus.UPSTREAM_BLOCKED.value,
    }:
        return None
    diagnostics: dict[str, Any] = {}
    fallback_failure_stage = receipt.get("task_key") if status in {
        OperationalStatus.FAILED.value,
        OperationalStatus.DEGRADED.value,
    } else None
    if fallback_failure_stage:
        diagnostics["failure_stage"] = str(fallback_failure_stage)
    for key in ("failure_stage", "collection_health", "upstream_dependency_status"):
        value = receipt.get(key)
        if value not in (None, ""):
            bounded = _bounded_diagnostic_value(value)
            if bounded is not None:
                diagnostics[key] = bounded
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    for key in sorted(FAILURE_DIAGNOSTIC_KEYS):
        if key not in details:
            continue
        value = _bounded_diagnostic_value(details.get(key))
        if value not in (None, "", [], {}):
            diagnostics[key] = value
    return diagnostics or None


def _receipt_paths(source_root: Path, dispatch: str, date: str) -> list[Path]:
    root = source_root / "status" / "operational-health" / dispatch / date / "runs"
    return sorted(root.glob("*.json"))


def load_food_line_receipts(source_root: Path, date: str) -> list[dict[str, Any]]:
    receipts = []
    for path in _receipt_paths(source_root, "food-line", date):
        receipt = _parse_json(path)
        try:
            validate_operational_receipt(receipt)
        except ValueError as exc:
            raise ExportError(f"invalid operational receipt {path.name}: {exc}") from exc
        receipts.append(receipt)
    return receipts


def load_care_line_receipts(source_root: Path, date: str) -> list[dict[str, Any]]:
    return load_care_line_receipts_for_dates(source_root, [date])


def _care_receipt_dates_for_expected_instances(
    date: str,
    expected_instances: Iterable[dict[str, Any]] | None,
) -> list[str]:
    dates = {date}
    grace_by_task = {item.task_key: item.grace_minutes for item in CARE_LINE_TASK_EXPECTATIONS}
    for instance in expected_instances or []:
        scheduled = parse_timestamp(str(instance.get("scheduled_for") or ""))
        if scheduled is None:
            continue
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        task_key = str(instance.get("task_key") or "")
        grace_minutes = grace_by_task.get(task_key, 0)
        # Keep this coupled to the matching window used by
        # _receipt_matches_expected_instance in operational_health.py.
        lower = scheduled - timedelta(minutes=5)
        upper = scheduled + timedelta(minutes=grace_minutes)
        dates.add(lower.astimezone(timezone.utc).date().isoformat())
        dates.add(scheduled.astimezone(timezone.utc).date().isoformat())
        dates.add(upper.astimezone(timezone.utc).date().isoformat())
    return sorted(dates)


def load_care_line_receipts_for_dates(source_root: Path, dates: Iterable[str]) -> list[dict[str, Any]]:
    receipts = []
    seen: set[Path] = set()
    for date in sorted(set(dates)):
        for path in _receipt_paths(source_root, "care-line", date):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            receipt = _parse_json(path)
            try:
                validate_operational_receipt(receipt)
            except ValueError as exc:
                raise ExportError(f"invalid operational receipt {path.name}: {exc}") from exc
            if receipt.get("dispatch") != "care-line":
                raise ExportError(f"receipt dispatch mismatch: {path.name}")
            receipts.append(receipt)
    receipts.sort(key=lambda item: _handoff_time(item) or datetime.min.replace(tzinfo=timezone.utc))
    return receipts


def load_gaza_receipts(source_root: Path, date: str) -> list[dict[str, Any]]:
    receipts = []
    for path in _receipt_paths(source_root, "gaza", date):
        receipt = _parse_json(path)
        try:
            validate_operational_receipt(receipt)
        except ValueError as exc:
            raise ExportError(f"invalid operational receipt {path.name}: {exc}") from exc
        if receipt.get("dispatch") != "gaza" or receipt.get("task_key") != "gaza_daily_dispatch":
            raise ExportError(f"Gaza receipt dispatch/task mismatch: {path.name}")
        receipts.append(receipt)
    receipts.sort(key=lambda item: _handoff_time(item) or datetime.min.replace(tzinfo=timezone.utc))
    return receipts


def _gaza_log_date(path: Path) -> str | None:
    match = GAZA_RUNNER_LOG_RE.match(path.name) or GAZA_DAILY_LOG_RE.match(path.name)
    if not match:
        return None
    raw = match.group("date")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _gaza_log_fields(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    fields: dict[str, Any] = {}
    _merge_embedded_json_log_fields(fields, text)
    for line in text.splitlines():
        match = GAZA_LOG_FIELD_RE.match(line.strip())
        if not match:
            continue
        fields[match.group("key").lower()] = _parse_log_scalar(match.group("value"))
    return fields


def _latest_gaza_dry_run_recovery_proof(source_root: Path, date: str) -> dict[str, Any] | None:
    logs = source_root / "logs"
    if not logs.exists():
        return None
    candidates: list[dict[str, Any]] = []
    for path in sorted([*logs.glob("runner-gaza-*.log"), *logs.glob("gaza-daily-*.log")]):
        if _gaza_log_date(path) != date:
            continue
        fields = _gaza_log_fields(path)
        operator_status = str(fields.get("operator_status") or "").upper()
        if operator_status not in {"DRY_RUN_READY", "NO_UPDATE_DRY_RUN_READY"}:
            continue
        if fields.get("ok") is not True:
            continue
        if fields.get("pages_dry_run_ok") is not True:
            continue
        if str(fields.get("audio_status") or "").lower() != "audio_skipped":
            continue
        if fields.get("pages_push_ok") is True or fields.get("pushed") is True:
            continue
        if fields.get("bluesky_status") not in (None, "skipped"):
            continue
        completed_at = _iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))
        candidates.append(
            {
                "path": str(path),
                "completed_at": completed_at,
                "operator_status": operator_status,
                "audio_status": fields.get("audio_status"),
                "pages_dry_run_ok": fields.get("pages_dry_run_ok"),
                "pages_push_ok": fields.get("pages_push_ok"),
                "pushed": fields.get("pushed"),
                "bluesky_status": fields.get("bluesky_status"),
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("completed_at") or ""))


def load_ice_receipts(source_root: Path, date: str) -> list[dict[str, Any]]:
    return load_ice_receipts_for_dates(source_root, [date])


def load_ice_receipts_for_dates(source_root: Path, dates: Iterable[str]) -> list[dict[str, Any]]:
    receipts = []
    seen: set[Path] = set()
    for date in sorted(set(dates)):
        for path in _receipt_paths(source_root, "ice", date):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            receipt = _parse_json(path)
            try:
                validate_operational_receipt(receipt)
            except ValueError as exc:
                raise ExportError(f"invalid operational receipt {path.name}: {exc}") from exc
            if receipt.get("dispatch") != "ice" or receipt.get("task_key") != "ice_monitor":
                raise ExportError(f"ICE receipt dispatch/task mismatch: {path.name}")
            receipts.append(receipt)
    receipts.sort(key=lambda item: _handoff_time(item) or datetime.min.replace(tzinfo=timezone.utc))
    return receipts


def _ice_receipt_dates_for_export_date(date: str, evaluated_at: str) -> list[str]:
    dates = {date}
    evaluated = parse_timestamp(evaluated_at)
    try:
        export_day = datetime.fromisoformat(date).date()
    except ValueError:
        return sorted(dates)
    current_expected = _expected_run(date, "21:15", "America/Los_Angeles")
    expectation = ICE_TASK_EXPECTATIONS[0]
    if evaluated is not None and current_expected is not None:
        if current_expected.tzinfo is None:
            current_expected = current_expected.replace(tzinfo=timezone.utc)
        if evaluated.tzinfo is None:
            evaluated = evaluated.replace(tzinfo=timezone.utc)
        if evaluated < current_expected + timedelta(minutes=expectation.grace_minutes):
            dates.add((export_day - timedelta(days=1)).isoformat())
    return sorted(dates)


def _handoff_timestamp(value: Any) -> datetime | None:
    return parse_timestamp(str(value or ""))


def _handoff_time(receipt: dict[str, Any] | None) -> datetime | None:
    if receipt is None:
        return None
    for key in ("receipt_created_at", "completed_at", "started_at"):
        parsed = _handoff_timestamp(receipt.get(key))
        if parsed is not None:
            return parsed
    return None


def _receipt_sort_time(receipt: dict[str, Any]) -> datetime:
    return _handoff_time(receipt) or datetime.min.replace(tzinfo=timezone.utc)


def _handoff_count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _handoff_summary_row(value: dict[str, Any]) -> dict[str, Any]:
    status = str(value.get("status") or "")
    classification = str(value.get("classification") or "")
    return {
        "agent_run_id": _safe_identifier(value.get("agent_run_id")),
        "status": status if status in HANDOFF_TERMINAL_STATUSES | {"SAFE_NO_OP"} else "UNKNOWN",
        "classification": classification if SAFE_KEY_RE.fullmatch(classification) else None,
        "receipt_created_at": value.get("receipt_created_at") if _handoff_timestamp(value.get("receipt_created_at")) else None,
        "unaccounted_count": _handoff_count(value.get("unaccounted_count", value.get("unaccounted"))),
    }


def _retired_proof_receipts(source_root: Path, dispatch: str) -> set[tuple[str, str]]:
    """Load exact proof-retirement identities without treating malformed data as a wildcard."""
    root = source_root / "data" / "private-agent-handoff" / "cleanup" / dispatch
    retired: set[tuple[str, str]] = set()
    for path in sorted(root.glob("proof-receipt-retirement-*.json")):
        value = _parse_json(path)
        receipt_ref = value.get("receipt_ref")
        receipt_sha256 = str(value.get("receipt_sha256") or "").lower()
        attempt_id = value.get("receipt_attempt_id")
        if (
            value.get("dispatch") != dispatch
            or value.get("synthetic_proof") is not True
            or not isinstance(receipt_ref, str)
            or Path(receipt_ref).is_absolute()
            or ".." in Path(receipt_ref).parts
            or not receipt_ref.startswith(f"data/private-agent-handoff/receipts/{dispatch}/")
            or not isinstance(attempt_id, str)
            or attempt_id != Path(receipt_ref).stem
            or not RETIREMENT_SHA_RE.fullmatch(receipt_sha256)
            or value.get("audit_preserved") is not True
        ):
            raise ExportError(f"invalid proof retirement identity: {path.name}")
        retired.add((receipt_ref.replace("\\", "/"), receipt_sha256))
    return retired


def load_agent_handoff_status(source_root: Path, dispatch: str) -> dict[str, Any]:
    """Summarize active sanitized handoff evidence without exporting payload content."""
    receipt_root = source_root / "data" / "private-agent-handoff" / "receipts" / dispatch
    retired = _retired_proof_receipts(source_root, dispatch)
    receipts: list[dict[str, Any]] = []
    for path in sorted(receipt_root.glob("*/*.json")):
        value = _parse_json(path)
        receipt_ref = path.relative_to(source_root).as_posix()
        receipt_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if value.get("dispatch") == dispatch and (receipt_ref, receipt_sha256) not in retired:
            receipts.append(value)
    receipts.sort(key=lambda item: _handoff_time(item) or datetime.min.replace(tzinfo=timezone.utc))
    inbox_root = source_root / "data" / "private-agent-handoff" / "inbox" / dispatch
    delivered: list[dict[str, Any]] = []
    for path in sorted(inbox_root.glob("*.json")):
        try:
            value = _parse_json(path)
        except ExportError:
            value = {}
        agent_run_id = _safe_identifier(value.get("agent_run_id"))
        if agent_run_id:
            delivered.append({"agent_run_id": agent_run_id})

    terminal = [item for item in receipts if str(item.get("status")) in HANDOFF_TERMINAL_STATUSES]
    latest = receipts[-1] if receipts else None
    latest_terminal = terminal[-1] if terminal else None
    latest_id = _safe_identifier((latest or {}).get("agent_run_id"))
    latest_time = _handoff_time(latest)
    last_success = max(
        (_handoff_time(item) for item in terminal if item.get("status") in {"SUCCESS", "SAFE_NO_OP"}),
        default=None,
    )
    last_failure = max(
        (_handoff_time(item) for item in terminal if item.get("status") == "FAILED"),
        default=None,
    )
    latest_unaccounted = _handoff_count((latest_terminal or {}).get("unaccounted_count", (latest_terminal or {}).get("unaccounted")))
    if latest_terminal is None:
        state = "HANDOFF_STALE_UNPROCESSED" if delivered else "NO_EXTERNAL_HANDOFF_EXPECTED"
    elif latest_terminal.get("status") == "FAILED":
        state = "HANDOFF_FAILED"
    elif latest_terminal.get("status") in {"SUCCESS", "SAFE_NO_OP"}:
        state = "HANDOFF_RECEIVED_SUCCESS" if latest_unaccounted == 0 else "HANDOFF_FAILED"
    else:
        state = "HANDOFF_STALE_UNPROCESSED"
    return {
        "state": state,
        "last_attempt_at": _iso(latest_time) if latest_time else None,
        "last_success_at": _iso(last_success) if last_success else None,
        "last_failure_at": _iso(last_failure) if last_failure else None,
        "latest_agent_run_id": latest_id or (delivered[-1]["agent_run_id"] if delivered else None),
        "latest_status": (latest_terminal or latest or {}).get("status") if (latest_terminal or latest) else None,
        "latest_classification": (latest_terminal or latest or {}).get("classification") if (latest_terminal or latest) else None,
        "unaccounted_count": latest_unaccounted,
        "stale": state == "HANDOFF_STALE_UNPROCESSED",
    }


def receipt_completeness(
    receipts: list[dict[str, Any]],
    *,
    source_root: Path,
    expectations: tuple[Any, ...] = FOOD_LINE_TASK_EXPECTATIONS,
) -> tuple[str, dict[str, str]]:
    expected = {item.task_key for item in expectations}
    grouped: dict[str, list[dict[str, Any]]] = {}
    linkage: dict[str, str] = {}
    inconsistent = False
    for receipt in receipts:
        task_key = str(receipt.get("task_key") or "")
        grouped.setdefault(task_key, []).append(receipt)
        if task_key not in expected:
            inconsistent = True
        refs = receipt.get("artifact_refs", {}) if isinstance(receipt.get("artifact_refs"), dict) else {}
        artifact = refs.get("task_receipt")
        if (
            not artifact
            and expectations == ICE_TASK_EXPECTATIONS
            and receipt.get("dispatch") == "ice"
            and task_key == "ice_monitor"
        ):
            artifact = refs.get("monitor_receipt")
        if not isinstance(artifact, str) or not artifact:
            inconsistent = True
            continue
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = source_root / artifact_path
        if not artifact_path.exists():
            inconsistent = True
        symbolic = _symbolic_artifact(artifact)
        if symbolic is None:
            inconsistent = True
        else:
            linkage[task_key] = symbolic
    missing = expected - set(grouped)
    if inconsistent:
        return "INCONSISTENT", linkage
    if missing:
        return ("MISSING" if not grouped else "PARTIAL"), linkage
    return "COMPLETE", linkage


def _latest_receipts_by_task(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for receipt in sorted(receipts, key=_receipt_sort_time):
        latest[str(receipt.get("task_key") or "")] = receipt
    return [latest[key] for key in sorted(latest)]


def _gaza_receipt_has_public_success_proof(receipt: dict[str, Any]) -> bool:
    if str(receipt.get("status") or "") != OperationalStatus.SUCCESS.value:
        return False
    if str(receipt.get("publication_status") or receipt.get("classification") or "") != "published_and_posted":
        return False
    if receipt.get("publication_attempted") is not True:
        return False
    effects = receipt.get("public_side_effects")
    if not isinstance(effects, dict) or effects.get("pages") is not True:
        return False
    details = receipt.get("details")
    if not isinstance(details, dict):
        return False
    return details.get("live_verify_status") == "LIVE_OK" or details.get("validation_ok") is True


def _gaza_receipt_failed_before_public_side_effect(receipt: dict[str, Any]) -> bool:
    if str(receipt.get("status") or "") != OperationalStatus.FAILED.value:
        return False
    if receipt.get("publication_attempted") is True:
        return False
    effects = receipt.get("public_side_effects")
    if isinstance(effects, dict) and any(value is True for value in effects.values()):
        return False
    return True


def _gaza_effective_receipts(receipts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    public_successes = [
        receipt for receipt in receipts
        if str(receipt.get("task_key") or "") == "gaza_daily_dispatch"
        and _gaza_receipt_has_public_success_proof(receipt)
    ]
    if not public_successes:
        return receipts, []
    authoritative = max(public_successes, key=_receipt_sort_time)
    authoritative_time = _receipt_sort_time(authoritative)
    effective: list[dict[str, Any]] = []
    non_authoritative: list[dict[str, Any]] = []
    for receipt in receipts:
        if (
            receipt is not authoritative
            and str(receipt.get("task_key") or "") == "gaza_daily_dispatch"
            and _gaza_receipt_failed_before_public_side_effect(receipt)
            and _receipt_sort_time(receipt) >= authoritative_time
        ):
            non_authoritative.append(receipt)
            continue
        effective.append(receipt)
    return effective, non_authoritative


def _receipt_has_public_side_effect(receipt: dict[str, Any]) -> bool:
    if receipt.get("publication_attempted") is True:
        return True
    effects = receipt.get("public_side_effects")
    return isinstance(effects, dict) and any(value is True for value in effects.values())


def _care_checkout_or_source_state_failure(receipt: dict[str, Any]) -> bool:
    if str(receipt.get("status") or "") != OperationalStatus.FAILED.value:
        return False
    if _receipt_has_public_side_effect(receipt):
        return False
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    markers = {
        str(receipt.get("failure_stage") or "").strip().lower(),
        str(details.get("failure_stage") or "").strip().lower(),
        str(receipt.get("collection_health") or "").strip().lower(),
        str(receipt.get("classification") or "").strip().lower(),
    }
    if markers.intersection({"source_state", "verify_checkout", "source_state_blocked", "checkout_dirty"}):
        return True
    wrapper_message = str(details.get("wrapper_exception_message") or "").lower()
    if "checkout is dirty" in wrapper_message or "runner checkout is dirty" in wrapper_message:
        return True
    return False


def _care_non_public_clean_proof(receipt: dict[str, Any]) -> bool:
    if _receipt_has_public_side_effect(receipt):
        return False
    if receipt.get("exit_code") not in (None, 0):
        return False
    return str(receipt.get("status") or "") in {
        OperationalStatus.SUCCESS.value,
        OperationalStatus.SAFE_NO_OP.value,
        OperationalStatus.DEGRADED.value,
    }


def _care_superseded_checkout_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    effective = dict(receipt)
    details = dict(effective.get("details") or {}) if isinstance(effective.get("details"), dict) else {}
    details["superseded_status_export_only"] = True
    details["original_status"] = receipt.get("status")
    details["original_classification"] = receipt.get("classification")
    effective["status"] = OperationalStatus.SAFE_NO_OP.value
    effective["classification"] = "superseded_checkout_failure"
    effective["failure_stage"] = None
    effective["exit_code"] = 0
    effective["details"] = details
    return effective


def _care_effective_receipts(receipts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clean_proofs = [receipt for receipt in receipts if _care_non_public_clean_proof(receipt)]
    if not clean_proofs:
        return receipts, []
    clean_proof_times = [_receipt_sort_time(receipt) for receipt in clean_proofs]
    latest_clean_proof_time = max(clean_proof_times)
    clean_proofs_by_task: dict[str, list[datetime]] = {}
    for receipt in clean_proofs:
        clean_proofs_by_task.setdefault(str(receipt.get("task_key") or ""), []).append(_receipt_sort_time(receipt))

    effective: list[dict[str, Any]] = []
    non_authoritative: list[dict[str, Any]] = []
    for receipt in receipts:
        if not _care_checkout_or_source_state_failure(receipt):
            effective.append(receipt)
            continue
        receipt_time = _receipt_sort_time(receipt)
        task_key = str(receipt.get("task_key") or "")
        same_task_proofs = clean_proofs_by_task.get(task_key, [])
        same_task_later = any(proof_time > receipt_time for proof_time in same_task_proofs)
        same_task_proven = bool(same_task_proofs)
        later_checkout_proof = latest_clean_proof_time > receipt_time
        if same_task_later or (same_task_proven and later_checkout_proof):
            non_authoritative.append(receipt)
            effective.append(_care_superseded_checkout_receipt(receipt))
            continue
        effective.append(receipt)
    return effective, non_authoritative


def _food_source_watch_durably_ready(
    latest_by_task: dict[str, dict[str, Any]],
    *,
    source_root: Path,
    date: str,
) -> bool:
    return food_source_receipt_is_durably_ready(
        latest_by_task.get("food_line_source_watch", {}),
        source_root=source_root,
        date=date,
    )


def _food_effective_receipts(
    *,
    source_root: Path,
    date: str,
    receipts: list[dict[str, Any]],
    latest_receipts: list[dict[str, Any]],
    source_watch_durably_ready: bool,
    evaluated_at: str,
) -> list[dict[str, Any]]:
    effective = latest_receipts
    try:
        recovery = evaluate_recovery(
            dispatch="food-line",
            source_root=source_root,
            date=date,
            evaluated_at=evaluated_at,
        )
    except Exception:
        return effective
    instances = recovery.get("instances") if isinstance(recovery.get("instances"), list) else []
    filtered: list[dict[str, Any]] = []
    for receipt in effective:
        task_key = str(receipt.get("task_key") or "")
        row = next((item for item in instances if isinstance(item, dict) and item.get("task_key") == task_key), None)
        if (
            task_key == "food_line_source_watch_resume"
            and row
            and row.get("recommendation") == "UPSTREAM_BLOCKED"
            and source_watch_durably_ready
        ):
            continue
        filtered.append(receipt)
    return filtered


def _expected_run(date: str, expected_time: str, timezone_name: str) -> datetime:
    local = datetime.fromisoformat(f"{date}T{expected_time}:00").replace(tzinfo=ZoneInfo(timezone_name))
    return local.astimezone(timezone.utc)


def _staleness(
    receipts: list[dict[str, Any]],
    *,
    date: str,
    evaluated_at: str,
    expectations: tuple[Any, ...] = FOOD_LINE_TASK_EXPECTATIONS,
) -> tuple[bool, str | None, str | None]:
    evaluated = parse_timestamp(evaluated_at)
    if evaluated is None:
        raise ExportError(f"invalid evaluated_at: {evaluated_at}")
    observed = [parse_timestamp(str(item.get("observed_at") or "")) for item in receipts]
    observed = [value for value in observed if value is not None]
    last_receipt = max(observed) if observed else None
    known_deadlines = [
        _expected_run(date, item.expected_time, item.timezone) + timedelta(minutes=item.grace_minutes)
        for item in expectations if item.expected_time != "configured scheduler time"
    ]
    stale_after = min(known_deadlines) if known_deadlines else None
    missing_expected = {item.task_key for item in expectations if item.expected_time != "configured scheduler time"} - {
        str(receipt.get("task_key")) for receipt in receipts
    }
    stale = bool(stale_after) and evaluated > stale_after and (not observed or bool(missing_expected))
    return stale, _iso(last_receipt) if last_receipt else None, _iso(stale_after) if stale_after else None


def _safe_head(value: Any) -> str | None:
    text = str(value or "")
    return text if HEX_HEAD_RE.fullmatch(text) else None


def _runner_git_identity(source_root: Path) -> dict[str, str | None]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=False,
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError):
        return {
            "current_runner_head": None,
            "current_runner_branch": None,
        }
    head_value = _safe_head(head.stdout.strip()) if head.returncode == 0 else None
    branch_value = branch.stdout.strip() if branch.returncode == 0 else ""
    return {
        "current_runner_head": head_value,
        "current_runner_branch": branch_value if SAFE_BRANCH_RE.fullmatch(branch_value) else None,
    }


def _same_utc_date(value: str | None, date: str) -> bool:
    parsed = parse_timestamp(str(value or ""))
    return bool(parsed and parsed.astimezone(timezone.utc).date().isoformat() == date)


def _receipt_artifact_id(receipt: dict[str, Any], linkage: dict[str, str]) -> str | None:
    refs = receipt.get("artifact_refs", {}) if isinstance(receipt.get("artifact_refs"), dict) else {}
    artifact = refs.get("task_receipt")
    if (
        not artifact
        and receipt.get("dispatch") == "ice"
        and receipt.get("task_key") == "ice_monitor"
    ):
        artifact = refs.get("monitor_receipt")
    return _symbolic_artifact(artifact) or linkage.get(str(receipt.get("task_key")))


def _load_care_line_source_failure_policies(source_root: Path) -> dict[str, dict[str, Any]]:
    path = source_root / CARE_LINE_SOURCE_REGISTRY_RELATIVE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(sources, list):
        return {}
    policies: dict[str, dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict) or source.get("enabled") is not True:
            continue
        source_id = _safe_identifier(source.get("source_id"))
        classification = _safe_identifier(source.get("operational_failure_classification"))
        if not source_id or classification != CARE_LINE_EXTERNAL_RESTRICTION_CLASSIFICATION:
            continue
        policies[source_id] = {
            "classification": classification,
            "coverage_reduced": bool(source.get("coverage_reduced")),
            "remediation_available": bool(source.get("remediation_available")),
        }
    return policies


def _care_failed_source_rows(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    rows = details.get("failed_source_diagnostics") if isinstance(details.get("failed_source_diagnostics"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _care_detail_count(receipt: dict[str, Any], key: str) -> int | None:
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    value = details.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _care_source_failure_summary(
    receipt: dict[str, Any] | None,
    source_failure_policies: dict[str, dict[str, Any]],
    replay_receipts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not receipt or receipt.get("dispatch") != "care-line" or receipt.get("task_key") != "care_line_collection":
        return {
            "failed_source_count": 0,
            "original_failed_source_count": 0,
            "recovered_source_count": 0,
            "external_access_restriction_count": 0,
            "transient_source_failure_count": 0,
            "unclassified_source_failure_count": 0,
            "external_restriction_sources": [],
            "transient_sources": [],
            "recovered_sources": [],
            "all_current_failures_external": False,
            "all_current_failures_non_actionable": False,
            "successful_attempt_count": None,
        }
    failed_rows = _care_failed_source_rows(receipt)
    original_failed_count = _care_detail_count(receipt, "failed_source_count")
    if original_failed_count is None:
        original_failed_count = len(failed_rows)
    parent_run_id = str(receipt.get("run_id") or "")
    recovered_by_source: dict[str, dict[str, Any]] = {}
    for replay in replay_receipts or []:
        if replay.get("dispatch") != "care-line" or replay.get("parent_run_id") != parent_run_id:
            continue
        if replay.get("effective_terminal_source_state") not in {"ok", "partial"}:
            continue
        if replay.get("publication_attempted") is not False or replay.get("public_side_effects") is not False:
            continue
        source_id = _safe_identifier(replay.get("source_id"))
        if source_id:
            recovered_by_source[source_id] = replay
    recovered_failed_rows: list[dict[str, Any]] = []
    unresolved_failed_rows: list[dict[str, Any]] = []
    for row in failed_rows:
        source_id = _safe_identifier(row.get("source_id"))
        if source_id and source_id in recovered_by_source:
            recovered_failed_rows.append(row)
        else:
            unresolved_failed_rows.append(row)
    failed_count = max(0, original_failed_count - len(recovered_failed_rows))
    external_sources: list[dict[str, Any]] = []
    transient_sources: list[dict[str, Any]] = []
    unclassified_count = 0
    for row in unresolved_failed_rows:
        source_id = _safe_identifier(row.get("source_id"))
        policy = source_failure_policies.get(source_id or "")
        if policy:
            external_sources.append(
                {
                    "source_id": source_id,
                    "classification": policy["classification"],
                    "coverage_reduced": policy["coverage_reduced"],
                    "remediation_available": policy["remediation_available"],
                }
            )
        elif row.get("transient") is True:
            transient_sources.append(
                {
                    "source_id": source_id,
                    "failure_class": _safe_identifier(row.get("failure_class")),
                    "adapter_type": _safe_identifier(row.get("adapter_type")),
                }
            )
        else:
            unclassified_count += 1
    unreported_count = max(0, failed_count - len(unresolved_failed_rows))
    unclassified_count += unreported_count
    all_external = failed_count > 0 and len(external_sources) == failed_count and unclassified_count == 0
    all_non_actionable = (
        failed_count > 0
        and len(external_sources) + len(transient_sources) == failed_count
        and unclassified_count == 0
    )
    return {
        "failed_source_count": failed_count,
        "original_failed_source_count": original_failed_count,
        "recovered_source_count": len(recovered_failed_rows),
        "external_access_restriction_count": len(external_sources),
        "transient_source_failure_count": len(transient_sources),
        "unclassified_source_failure_count": unclassified_count,
        "external_restriction_sources": external_sources,
        "transient_sources": transient_sources,
        "recovered_sources": [
            {
                "source_id": _safe_identifier(row.get("source_id")),
                "receipt": _symbolic_artifact(recovered_by_source.get(str(row.get("source_id") or ""), {}).get("receipt_path")),
            }
            for row in recovered_failed_rows
        ],
        "all_current_failures_external": all_external,
        "all_current_failures_non_actionable": all_non_actionable,
        "successful_attempt_count": _care_detail_count(receipt, "successful_attempt_count"),
    }


def _care_collection_diagnostics(
    receipt: dict[str, Any],
    source_failure_policies: dict[str, dict[str, Any]] | None = None,
    replay_receipts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if receipt.get("dispatch") != "care-line" or receipt.get("task_key") != "care_line_collection":
        return None
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    rows = _care_failed_source_rows(receipt)
    failures: list[dict[str, Any]] = []
    policies = source_failure_policies or {}
    parent_run_id = str(receipt.get("run_id") or "")
    recovered = {
        str(replay.get("source_id") or "")
        for replay in replay_receipts or []
        if replay.get("parent_run_id") == parent_run_id
        and replay.get("effective_terminal_source_state") in {"ok", "partial"}
        and replay.get("publication_attempted") is False
        and replay.get("public_side_effects") is False
    }
    for row in rows[:20]:
        source_id = _safe_identifier(row.get("source_id"))
        failure = {
            "source_id": source_id,
            "source_name": str(row.get("source_name") or "")[:200] or None,
            "adapter_type": _safe_identifier(row.get("adapter_type")),
            "failure_class": _safe_identifier(row.get("failure_class")),
            "transient": bool(row.get("transient")),
            "effective_source_state": "recovered" if source_id in recovered else "failed",
        }
        policy = policies.get(source_id or "")
        if policy:
            failure["external_classification"] = policy["classification"]
            failure["coverage_reduced"] = policy["coverage_reduced"]
            failure["remediation_available"] = policy["remediation_available"]
        failures.append(failure)
    def _count(key: str) -> int | None:
        value = details.get(key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    return {
        "successful_attempt_count": _count("successful_attempt_count"),
        "failed_source_count": _count("failed_source_count"),
        "skipped_source_count": _count("skipped_source_count"),
        "active_review_queue_count": _count("active_review_queue_count"),
        "manual_review_count": _count("manual_review_count"),
        "failed_sources": failures,
    }


def _task_summary(
    receipt: dict[str, Any],
    linkage: dict[str, str],
    source_failure_policies: dict[str, dict[str, Any]] | None = None,
    replay_receipts: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    diagnostics = _care_collection_diagnostics(receipt, source_failure_policies, replay_receipts)
    failure_diagnostics = _failure_diagnostics(receipt)
    if failure_diagnostics:
        diagnostics = {**(diagnostics or {}), "failure": failure_diagnostics}
    return {
        "task_key": receipt.get("task_key"),
        "status": receipt.get("status") if receipt.get("status") in SUPPORTED_TASK_STATUSES else "UNKNOWN",
        "classification": str(receipt.get("classification") or "unknown"),
        "scheduled_for": receipt.get("scheduled_for"),
        "started_at": receipt.get("started_at"),
        "completed_at": receipt.get("completed_at"),
        "exit_code": receipt.get("exit_code"),
        "run_id": receipt.get("run_id"),
        "next_expected_run": receipt.get("next_expected_run"),
        "publication_attempted": receipt.get("publication_attempted"),
        "publication_status": receipt.get("publication_status"),
        "public_side_effects": _sanitized_public_side_effects(receipt.get("public_side_effects")),
        "artifact_id": _receipt_artifact_id(receipt, linkage),
        "diagnostics": diagnostics,
    }


def _safe_nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _food_line_proposal_date(path: Path, payload: dict[str, Any]) -> str | None:
    for key in ("proposal_date", "edition_date", "observed_date", "date"):
        value = payload.get(key)
        if isinstance(value, str) and DATE_RE.fullmatch(value.strip()):
            return value.strip()
    return path.stem if DATE_RE.fullmatch(path.stem) else None


def _food_line_release_readiness_approved(source_root: Path, proposal_date: str) -> bool:
    path = source_root / FOOD_LINE_RELEASE_READINESS_RELATIVE / f"{proposal_date}.json"
    payload = _load_json_object(path)
    if not payload:
        return False
    status = str(
        payload.get("status")
        or payload.get("release_readiness_status")
        or payload.get("approval_status")
        or payload.get("publication_status")
        or ""
    ).strip().lower()
    if status in FOOD_LINE_RELEASE_READY_STATUSES:
        return True
    for key in ("release_ready", "approved", "publication_approved", "publication_approval"):
        if payload.get(key) is True:
            return True
    return False


def _food_line_proposal_published_or_dispositioned(payload: dict[str, Any]) -> bool:
    if (
        payload.get("published") is True
        or payload.get("publication_completed") is True
        or payload.get("approved") is True
        or payload.get("publication_approval") is True
    ):
        return True
    status = str(
        payload.get("publication_status")
        or payload.get("public_status")
        or payload.get("draft_status")
        or payload.get("approval_status")
        or payload.get("status")
        or ""
    ).strip().lower()
    return status in FOOD_LINE_PUBLISHED_STATUSES or status in FOOD_LINE_APPROVED_PROPOSAL_STATUSES


def _food_line_item_pending(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    status_values = [
        item.get("status"),
        item.get("review_status"),
        item.get("editorial_status"),
        item.get("disposition"),
        item.get("classification"),
    ]
    normalized = [
        str(value or "").strip().lower()
        for value in status_values
        if value is not None and str(value).strip()
    ]
    if not normalized:
        return True
    if any(value in FOOD_LINE_DISPOSITIONED_ITEM_STATUSES for value in normalized):
        return False
    return any(value in FOOD_LINE_PENDING_ITEM_STATUSES for value in normalized)


def _food_line_equivalent_pending_item_count(payload: dict[str, Any]) -> int:
    pending = _safe_nonnegative_int(payload.get("pending_item_count"))
    if pending is not None:
        return pending
    for key in ("historical_editorial_disposition", "editorial_disposition", "review_disposition"):
        disposition = payload.get(key)
        if isinstance(disposition, dict):
            pending = _safe_nonnegative_int(disposition.get("pending"))
            if pending is not None:
                return pending
    items = payload.get("items")
    if isinstance(items, list):
        return sum(1 for item in items if _food_line_item_pending(item))
    return 0


def _food_line_pending_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and _food_line_item_pending(item)]


def _food_line_item_id(value: dict[str, Any]) -> str | None:
    for key in ("item_id", "id", "source_record_id", "candidate_id", "record_id"):
        item_id = str(value.get(key) or "").strip()
        if item_id:
            return item_id
    return None


def _food_line_item_source_url(value: dict[str, Any]) -> str | None:
    for key in ("source_url", "url", "canonical_url", "publisher_url", "original_url", "link"):
        source_url = str(value.get(key) or "").strip()
        if source_url:
            return source_url
    source = value.get("source")
    if isinstance(source, dict):
        for key in ("source_url", "url", "canonical_url", "publisher_url"):
            source_url = str(source.get(key) or "").strip()
            if source_url:
                return source_url
    return None


def _food_line_source_urls_from_items(payload: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for key in ("items", "sources", "source_records", "records"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            source_url = _food_line_item_source_url(item)
            if source_url:
                urls.add(source_url)
    return urls


def _food_line_publication_approval_urls(source_root: Path, proposal_date: str) -> set[str]:
    approval_root = source_root / FOOD_LINE_PUBLICATION_APPROVAL_RELATIVE / proposal_date
    urls: set[str] = set()
    if not approval_root.is_dir():
        return urls
    readiness_files = sorted(approval_root.glob("*release-readiness.json"))
    readiness_approved = False
    for path in readiness_files:
        payload = _load_json_object(path)
        if not payload:
            continue
        if (
            payload.get("pages_push_authorized") is True
            or payload.get("publication_approval_supplied_externally") is True
            or str(payload.get("status") or "").strip().lower() in FOOD_LINE_RELEASE_READY_STATUSES
        ):
            readiness_approved = True
    if not readiness_approved:
        return urls
    for path in sorted(approval_root.glob("*approved-proposal.json")):
        payload = _load_json_object(path)
        if payload:
            urls.update(_food_line_source_urls_from_items(payload))
    return urls


def _food_line_release_manifest_urls(source_root: Path, proposal_date: str) -> set[str]:
    release_manifest = source_root / FOOD_LINE_REVIEW_RELEASES_RELATIVE / f"{proposal_date}.json"
    sources_manifest = (
        source_root
        / "output"
        / "site"
        / "food-line"
        / "editions"
        / proposal_date
        / "sources_manifest.json"
    )
    if not release_manifest.is_file() or not sources_manifest.is_file():
        return set()
    try:
        payload = json.loads(sources_manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return set()
    urls: set[str] = set()
    if not isinstance(payload, list):
        return urls
    for item in payload:
        if not isinstance(item, dict):
            continue
        source_url = _food_line_item_source_url(item)
        if source_url:
            urls.add(source_url)
    return urls


def _food_line_published_private_review_urls(source_root: Path, proposal_date: str) -> set[str]:
    return _food_line_publication_approval_urls(source_root, proposal_date) | _food_line_release_manifest_urls(
        source_root,
        proposal_date,
    )


def _food_line_disposition_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "dispositions", "entries", "item_dispositions"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            rows: list[dict[str, Any]] = []
            for item_key, item_value in value.items():
                if not isinstance(item_value, dict):
                    continue
                row = dict(item_value)
                row.setdefault("item_id", item_key)
                rows.append(row)
            return rows
    if isinstance(payload.get("disposition"), str):
        return [payload]
    return []


def _food_line_sidecar_default_date(path: Path, payload: dict[str, Any]) -> str | None:
    for key in ("date", "proposal_date", "edition_date", "observed_date"):
        value = str(payload.get(key) or "").strip()
        if DATE_RE.fullmatch(value):
            return value
    return path.stem if DATE_RE.fullmatch(path.stem) else None


def _food_line_disposition_id(entry: dict[str, Any], payload: dict[str, Any], artifact: str) -> str:
    for value in (entry.get("disposition_id"), payload.get("disposition_id")):
        disposition_id = str(value or "").strip()
        if disposition_id:
            return disposition_id
    return artifact


def _food_line_superseded_disposition_ids(entry: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for key in ("supersedes_disposition_ids", "supersedes_dispositions"):
        value = entry.get(key)
        if isinstance(value, list):
            values.extend(value)
    for key in ("supersedes_disposition_id", "supersedes_disposition"):
        value = entry.get(key)
        if isinstance(value, str):
            values.append(value)
    return {str(value).strip() for value in values if str(value or "").strip()}


def _food_line_disposition_bucket_superseded(
    bucket: list[dict[str, Any]],
    normalized: dict[str, Any],
) -> bool:
    new_id = str(normalized.get("disposition_id") or "")
    new_supersedes = set(normalized.get("supersedes_disposition_ids") or [])
    existing_ids = {str(item.get("disposition_id") or "") for item in bucket}
    existing_supersedes: set[str] = set()
    for item in bucket:
        existing_supersedes.update(str(value) for value in item.get("supersedes_disposition_ids") or [])
    return bool(new_supersedes & existing_ids) or bool(new_id and new_id in existing_supersedes)


def _food_line_load_private_review_dispositions(source_root: Path) -> dict[str, Any]:
    disposition_root = source_root / FOOD_LINE_PRIVATE_REVIEW_DISPOSITIONS_RELATIVE
    result: dict[str, Any] = {
        "by_item_id": {},
        "by_source_url": {},
        "sources": {},
        "diagnostics": {
            "malformed_entry_count": 0,
            "unknown_disposition_count": 0,
            "duplicate_entry_count": 0,
        },
    }
    if not disposition_root.is_dir():
        return result
    for path in sorted(disposition_root.glob("*.json")):
        relative = (FOOD_LINE_PRIVATE_REVIEW_DISPOSITIONS_RELATIVE / path.name).as_posix()
        payload = _load_json_object(path)
        source_summary = {
            "disposition_artifact": relative,
            "entry_count": 0,
            "subtracting_entry_count": 0,
            "applied_item_count": 0,
            "ignored_entry_count": 0,
            "malformed_entry_count": 0,
            "unknown_disposition_count": 0,
        }
        result["sources"][relative] = source_summary
        if payload is None:
            source_summary["malformed_entry_count"] += 1
            result["diagnostics"]["malformed_entry_count"] += 1
            continue
        default_date = _food_line_sidecar_default_date(path, payload)
        for entry in _food_line_disposition_entries(payload):
            source_summary["entry_count"] += 1
            disposition = str(entry.get("disposition") or "").strip().lower()
            if disposition not in FOOD_LINE_PRIVATE_REVIEW_DISPOSITIONS:
                source_summary["unknown_disposition_count"] += 1
                result["diagnostics"]["unknown_disposition_count"] += 1
                continue
            entry_date = str(entry.get("date") or entry.get("proposal_date") or default_date or "").strip()
            if not DATE_RE.fullmatch(entry_date):
                source_summary["malformed_entry_count"] += 1
                result["diagnostics"]["malformed_entry_count"] += 1
                continue
            item_id = _food_line_item_id(entry)
            source_url = _food_line_item_source_url(entry)
            if not item_id and not source_url:
                source_summary["malformed_entry_count"] += 1
                result["diagnostics"]["malformed_entry_count"] += 1
                continue
            normalized = {
                "disposition_id": _food_line_disposition_id(entry, payload, relative),
                "disposition": disposition,
                "source_artifact": relative,
                "subtracts": disposition in FOOD_LINE_PRIVATE_REVIEW_SUBTRACTING_DISPOSITIONS,
                "supersedes_disposition_ids": sorted(_food_line_superseded_disposition_ids(entry)),
            }
            if normalized["subtracts"]:
                source_summary["subtracting_entry_count"] += 1
            if item_id:
                key = (entry_date, item_id)
                bucket = result["by_item_id"].setdefault(key, [])
                if bucket and not _food_line_disposition_bucket_superseded(bucket, normalized):
                    source_summary["ignored_entry_count"] += 1
                    result["diagnostics"]["duplicate_entry_count"] += 1
                bucket.append(normalized)
            if source_url:
                key = (entry_date, source_url)
                bucket = result["by_source_url"].setdefault(key, [])
                if bucket and not _food_line_disposition_bucket_superseded(bucket, normalized):
                    source_summary["ignored_entry_count"] += 1
                    result["diagnostics"]["duplicate_entry_count"] += 1
                bucket.append(normalized)
    return result


def _food_line_matching_disposition(
    item: dict[str, Any],
    *,
    proposal_date: str,
    disposition_index: dict[str, Any],
) -> dict[str, Any] | None:
    item_id = _food_line_item_id(item)
    if item_id:
        matches = disposition_index["by_item_id"].get((proposal_date, item_id), [])
        if matches:
            return _food_line_resolve_disposition_match(matches, disposition_index)
    source_url = _food_line_item_source_url(item)
    if source_url:
        matches = disposition_index["by_source_url"].get((proposal_date, source_url), [])
        if matches:
            return _food_line_resolve_disposition_match(matches, disposition_index)
    return None


def _food_line_resolve_disposition_match(
    matches: list[dict[str, Any]],
    disposition_index: dict[str, Any],
) -> dict[str, Any]:
    superseded_ids: set[str] = set()
    for item in matches:
        superseded_ids.update(str(value) for value in item.get("supersedes_disposition_ids") or [])
    active_matches = [
        item
        for item in matches
        if not str(item.get("disposition_id") or "") or str(item.get("disposition_id") or "") not in superseded_ids
    ]
    if not active_matches:
        active_matches = matches
    non_subtracting = [item for item in active_matches if not item.get("subtracts")]
    selected = non_subtracting[0] if non_subtracting else active_matches[0]
    if selected.get("subtracts"):
        source = disposition_index["sources"].get(selected["source_artifact"])
        if isinstance(source, dict):
            source["applied_item_count"] += 1
    return selected


def _food_line_private_review_backlog_age_hours(proposal_date: str, evaluated_at: str) -> int | None:
    evaluated = parse_timestamp(evaluated_at)
    if evaluated is None:
        return None
    try:
        start = datetime.fromisoformat(proposal_date).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = evaluated.astimezone(timezone.utc) - start
    return max(0, int(delta.total_seconds() // 3600))


def food_line_private_review_backlog(*, source_root: Path, evaluated_at: str) -> dict[str, Any]:
    proposal_root = source_root / FOOD_LINE_PROPOSED_EDITIONS_RELATIVE
    disposition_index = _food_line_load_private_review_dispositions(source_root)
    rows: list[dict[str, Any]] = []
    total_dispositioned = 0
    total_count_only_gap = 0
    if proposal_root.is_dir():
        for path in sorted(proposal_root.glob("*.json")):
            payload = _load_json_object(path)
            if payload is None:
                continue
            proposal_date = _food_line_proposal_date(path, payload)
            if not proposal_date:
                continue
            pending_count = _food_line_equivalent_pending_item_count(payload)
            if pending_count <= 0:
                continue
            if _food_line_proposal_published_or_dispositioned(payload):
                continue
            if _food_line_release_readiness_approved(source_root, proposal_date):
                continue
            published_urls = _food_line_published_private_review_urls(source_root, proposal_date)
            pending_items = _food_line_pending_items(payload)
            pending_count = max(pending_count, len(pending_items))
            dispositioned_count = 0
            unresolved_visible_count = 0
            for item in pending_items:
                source_url = _food_line_item_source_url(item)
                if source_url and source_url in published_urls:
                    dispositioned_count += 1
                    continue
                disposition = _food_line_matching_disposition(
                    item,
                    proposal_date=proposal_date,
                    disposition_index=disposition_index,
                )
                if disposition and disposition.get("subtracts"):
                    dispositioned_count += 1
                    continue
                unresolved_visible_count += 1
            count_only_gap = max(0, pending_count - len(pending_items))
            # Count-only gaps usually mean a proposal-level pending count is
            # larger than the item rows available for item-level review. Keep
            # the diagnostic visible, but do not treat undispositionable counts
            # as actionable private-review items.
            unresolved_count = unresolved_visible_count
            total_count_only_gap += count_only_gap
            if unresolved_count <= 0:
                total_dispositioned += dispositioned_count
                continue
            total_dispositioned += dispositioned_count
            row = {
                "date": proposal_date,
                "pending_item_count": unresolved_count,
                "unresolved_item_count": unresolved_count,
                "dispositioned_item_count": dispositioned_count,
                "count_only_gap_count": count_only_gap,
                "proposal_artifact": (FOOD_LINE_PROPOSED_EDITIONS_RELATIVE / path.name).as_posix(),
            }
            age_hours = _food_line_private_review_backlog_age_hours(proposal_date, evaluated_at)
            if age_hours is not None:
                row["age_hours"] = age_hours
            rows.append(row)
    rows.sort(key=lambda item: str(item["date"]))
    max_age_values = [item["age_hours"] for item in rows if isinstance(item.get("age_hours"), int)]
    return {
        "pending_date_count": len(rows),
        "pending_item_count": sum(int(item["pending_item_count"]) for item in rows),
        "dispositioned_item_count": total_dispositioned,
        "unresolved_item_count": sum(int(item["unresolved_item_count"]) for item in rows),
        "count_only_gap_count": total_count_only_gap,
        "oldest_pending_date": rows[0]["date"] if rows else None,
        "max_age_hours": max(max_age_values) if max_age_values else 0,
        "dates": rows,
        "disposition_sources": list(disposition_index["sources"].values()),
        "disposition_diagnostics": disposition_index["diagnostics"],
    }


def _with_debug_summary(payload: dict[str, Any]) -> dict[str, Any]:
    payload["debug_summary"] = build_debug_summary(payload)
    return payload


def _care_downstream_failure_still_current(task_name: str, receipts: Iterable[dict[str, Any]]) -> bool:
    task_key = task_name.split(":", 1)[0]
    if not task_key or task_key == "care_line_collection":
        return False
    relevant = [receipt for receipt in receipts if str(receipt.get("task_key") or "") == task_key]
    if not relevant:
        return True
    latest = max(relevant, key=_receipt_sort_time)
    latest_status = str(latest.get("status") or "")
    if latest_status not in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value}:
        return True
    if latest.get("publication_attempted") is True:
        return True
    return False


def _care_adjusted_aggregate_status(
    *,
    aggregate_status: str,
    aggregate: dict[str, Any],
    completeness: str,
    latest_collection: dict[str, Any] | None,
    source_failure_summary: dict[str, Any],
    receipts: Iterable[dict[str, Any]] = (),
) -> str:
    if aggregate.get("stale_observability") or aggregate_status == OperationalStatus.STALE_OBSERVABILITY.value:
        return aggregate_status
    if aggregate.get("missed_tasks") or completeness != "COMPLETE":
        return aggregate_status
    if (
        latest_collection
        and source_failure_summary["failed_source_count"] <= 0
        and source_failure_summary.get("recovered_source_count", 0) > 0
    ):
        failed_tasks = [str(task) for task in aggregate.get("failed_tasks", [])]
        degraded_tasks = [str(task) for task in aggregate.get("degraded_tasks", [])]
        non_collection_problems = [
            task
            for task in failed_tasks + degraded_tasks
            if not task.startswith("care_line_collection")
        ]
        if not non_collection_problems:
            return OperationalStatus.SUCCESS.value
    if not latest_collection or source_failure_summary["failed_source_count"] <= 0:
        return aggregate_status
    current_mixed_failure = (
        source_failure_summary["external_access_restriction_count"] > 0
        and source_failure_summary["unclassified_source_failure_count"] > 0
    )
    if current_mixed_failure:
        return OperationalStatus.FAILED.value
    latest_status = str(latest_collection.get("status") or "")
    successful_attempts = source_failure_summary.get("successful_attempt_count")
    non_actionable_current_failure = (
        latest_status == OperationalStatus.DEGRADED.value
        and isinstance(successful_attempts, int)
        and successful_attempts > 0
        and (
            source_failure_summary["all_current_failures_external"]
            or source_failure_summary.get("all_current_failures_non_actionable") is True
        )
    )
    if not non_actionable_current_failure:
        return aggregate_status
    failed_tasks = [str(task) for task in aggregate.get("failed_tasks", [])]
    has_non_collection_failure = any(
        not task.startswith("care_line_collection")
        and _care_downstream_failure_still_current(task, receipts)
        for task in failed_tasks
    )
    if has_non_collection_failure:
        return aggregate_status
    return OperationalStatus.DEGRADED.value


def _recovery_state(
    aggregate_status: str,
    recovery: RecoveryContext | None,
) -> str:
    if recovery is None:
        return RecoveryState.INCIDENT_OPEN.value if aggregate_status in {"FAILED", "STALE_OBSERVABILITY"} else RecoveryState.HEALTHY.value
    context = recovery
    if context.incident_opened_at is None:
        return RecoveryState.HEALTHY.value
    if context.fix_deployed_at is None:
        return RecoveryState.INCIDENT_OPEN.value
    proof_after = parse_timestamp(context.runtime_proof_after) or parse_timestamp(context.fix_deployed_at)
    proof = parse_timestamp(context.recovered_at)
    if proof_after and proof and proof > proof_after and aggregate_status in {"SUCCESS", "DEGRADED"}:
        return RecoveryState.RECOVERED.value
    return RecoveryState.RECOVERY_PENDING_RUNTIME_PROOF.value


def build_food_line_status(
    *,
    source_root: Path,
    date: str,
    evaluated_at: str,
    exported_at: str,
    recovery: RecoveryContext | None = None,
) -> dict[str, Any]:
    receipts = load_food_line_receipts(source_root, date)
    runner_identity = _runner_git_identity(source_root)
    completeness, linkage = receipt_completeness(receipts, source_root=source_root)
    latest_receipts = _latest_receipts_by_task(receipts)
    latest_by_task = {str(receipt.get("task_key") or ""): receipt for receipt in latest_receipts}
    source_watch_durably_ready = _food_source_watch_durably_ready(
        latest_by_task,
        source_root=source_root,
        date=date,
    )
    effective_receipts = _food_effective_receipts(
        source_root=source_root,
        date=date,
        receipts=receipts,
        latest_receipts=latest_receipts,
        source_watch_durably_ready=source_watch_durably_ready,
        evaluated_at=evaluated_at,
    )
    stale, last_receipt_at, stale_after = _staleness(receipts, date=date, evaluated_at=evaluated_at)
    same_day_evidence = _same_utc_date(last_receipt_at, date) and _same_utc_date(evaluated_at, date)
    stale_observability = stale and not same_day_evidence
    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=effective_receipts,
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at=evaluated_at,
        recovery=recovery,
    )
    actionable_missed = [
        task for task in aggregate["missed_tasks"]
        if not (source_watch_durably_ready and task == "food_line_source_watch_resume")
    ]
    if stale_observability:
        aggregate_status = OperationalStatus.STALE_OBSERVABILITY.value
    elif aggregate["failed_tasks"]:
        aggregate_status = OperationalStatus.FAILED.value
    elif actionable_missed:
        aggregate_status = OperationalStatus.MISSED.value
    elif aggregate["degraded_tasks"] or aggregate["upstream_blocked_tasks"]:
        aggregate_status = OperationalStatus.DEGRADED.value
    else:
        aggregate_status = OperationalStatus.SUCCESS.value
    source_heads = {_safe_head(receipt.get("source_head")) for receipt in receipts}
    source_heads.discard(None)
    publication_attempted = any(receipt.get("publication_attempted") is True for receipt in receipts)
    publication_statuses = [receipt.get("publication_status") for receipt in receipts if receipt.get("publication_status")]
    agent_handoff = load_agent_handoff_status(source_root, "food-line")
    private_review_backlog = food_line_private_review_backlog(source_root=source_root, evaluated_at=evaluated_at)
    return _with_debug_summary({
        "schema_version": EXTERNAL_STATUS_SCHEMA_VERSION,
        "dispatch": "food-line",
        "observed_date": date,
        "aggregate_status": aggregate_status,
        "recovery_lifecycle": _recovery_state(aggregate_status, recovery),
        "incident_reference": {"date": date} if aggregate_status in {"FAILED", "STALE_OBSERVABILITY"} else None,
        "latest_runtime_proof_date": aggregate.get("latest_success_at"),
        "receipt_completeness": completeness,
        "task_summaries": [_task_summary(receipt, linkage) for receipt in receipts],
        "effective_task_summaries": [_task_summary(receipt, linkage) for receipt in effective_receipts],
        "runner_source_head": sorted(source_heads)[0] if len(source_heads) == 1 else None,
        **runner_identity,
        "publication_attempted": publication_attempted,
        "publication_status": publication_statuses[-1] if publication_statuses else None,
        "public_side_effects": {
            "publication_attempted": publication_attempted,
            "publication_status": publication_statuses[-1] if publication_statuses else None,
        },
        "stale_observability": stale_observability,
        "last_receipt_at": last_receipt_at,
        "last_exported_at": exported_at,
        "next_expected_run": next((item.get("next_expected_run") for item in receipts if item.get("next_expected_run")), None),
        "stale_after": stale_after,
        "agent_handoff": agent_handoff,
        "private_review_backlog": private_review_backlog,
    })


def build_care_line_status(
    *,
    source_root: Path,
    date: str,
    evaluated_at: str,
    exported_at: str,
    recovery: RecoveryContext | None = None,
    expected_instances: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    instance_rows = list(expected_instances) if expected_instances is not None else None
    runner_identity = _runner_git_identity(source_root)
    receipt_dates = _care_receipt_dates_for_expected_instances(date, instance_rows)
    receipts = load_care_line_receipts_for_dates(source_root, receipt_dates)
    completeness, linkage = receipt_completeness(
        receipts, source_root=source_root, expectations=CARE_LINE_TASK_EXPECTATIONS
    )
    source_failure_policies = _load_care_line_source_failure_policies(source_root)
    effective_receipts, non_authoritative_receipts = _care_effective_receipts(receipts)
    collection_receipts = [
        receipt for receipt in effective_receipts
        if receipt.get("task_key") == "care_line_collection"
        and str(receipt.get("classification") or "") != "superseded_checkout_failure"
    ]
    latest_collection = max(collection_receipts, key=_receipt_sort_time, default=None)
    replay_receipts = load_care_line_source_replay_receipts(source_root, receipt_dates)
    source_failure_summary = _care_source_failure_summary(latest_collection, source_failure_policies, replay_receipts)
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=effective_receipts,
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at=evaluated_at,
        expected_instances=instance_rows,
        recovery=recovery,
    ) if effective_receipts or instance_rows is not None else {
        "overall_health": OperationalStatus.UNKNOWN.value,
        "recovery_state": RecoveryState.HEALTHY.value,
        "expected_tasks": [item.task_key for item in CARE_LINE_TASK_EXPECTATIONS],
        "completed_tasks": [], "missed_tasks": [], "failed_tasks": [],
        "degraded_tasks": [], "upstream_blocked_tasks": [],
        "stale_observability": [], "latest_success_at": None,
    }
    aggregate_status = (
        aggregate["overall_health"]
        if receipts or aggregate.get("missed_tasks")
        else OperationalStatus.UNKNOWN.value
    )
    aggregate_status = _care_adjusted_aggregate_status(
        aggregate_status=aggregate_status,
        aggregate=aggregate,
        completeness=completeness if receipts else "NO_PROOF",
        latest_collection=latest_collection,
        source_failure_summary=source_failure_summary,
        receipts=receipts,
    )
    source_heads = {_safe_head(receipt.get("source_head")) for receipt in receipts}
    source_heads.discard(None)
    publication_attempted = any(receipt.get("publication_attempted") is True for receipt in receipts)
    publication_statuses = [receipt.get("publication_status") for receipt in receipts if receipt.get("publication_status")]
    return _with_debug_summary({
        "schema_version": EXTERNAL_STATUS_SCHEMA_VERSION,
        "dispatch": "care-line",
        "migration_status": "MIGRATED",
        "observed_date": date,
        "aggregate_status": aggregate_status,
        "scheduled_health_authoritative": bool(receipts),
        "recovery_lifecycle": _recovery_state(aggregate_status, recovery),
        "latest_runtime_proof_date": aggregate.get("latest_success_at"),
        "receipt_completeness": completeness if receipts else "NO_PROOF",
        "task_summaries": [_task_summary(receipt, linkage, source_failure_policies, replay_receipts) for receipt in receipts],
        "effective_task_summaries": [
            _task_summary(receipt, linkage, source_failure_policies, replay_receipts)
            for receipt in effective_receipts
        ],
        "non_authoritative_receipts": [
            {
                "task_key": receipt.get("task_key"),
                "run_id": receipt.get("run_id"),
                "status": receipt.get("status"),
                "classification": receipt.get("classification"),
                "completed_at": receipt.get("completed_at"),
                "reason": "superseded_checkout_or_source_state_failure",
            }
            for receipt in non_authoritative_receipts
        ],
        "source_failure_summary": source_failure_summary,
        "runner_source_head": sorted(source_heads)[0] if len(source_heads) == 1 else None,
        **runner_identity,
        "publication_attempted": publication_attempted,
        "publication_status": publication_statuses[-1] if publication_statuses else None,
        "public_side_effects": {"publication_attempted": publication_attempted, "publication_status": publication_statuses[-1] if publication_statuses else None},
        "stale_observability": bool(aggregate.get("stale_observability")),
        "last_receipt_at": max((_handoff_time(item) for item in receipts), default=None).isoformat().replace("+00:00", "Z") if receipts else None,
        "last_exported_at": exported_at,
        "next_expected_run": next((item.get("next_expected_run") for item in receipts if item.get("next_expected_run")), None),
        "agent_handoff": load_agent_handoff_status(source_root, "care-line"),
        "scheduled_task_keys": [item.task_key for item in CARE_LINE_TASK_EXPECTATIONS],
        "expected_instances": instance_rows or [],
    })


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0



def _monitor_success_overrides_stale(
    aggregate: dict[str, Any],
    receipts: list[dict[str, Any]],
    *,
    task_key: str,
) -> bool:
    if aggregate.get("overall_health") != OperationalStatus.STALE_OBSERVABILITY.value:
        return False
    stale_tasks = aggregate.get("stale_observability")
    if stale_tasks not in ([task_key], (task_key,)):
        return False
    return any(
        str(receipt.get("task_key") or "") == task_key
        and str(receipt.get("status") or "") in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value}
        for receipt in receipts
    )


def build_gaza_status(
    *,
    source_root: Path,
    date: str,
    evaluated_at: str,
    exported_at: str,
    recovery: RecoveryContext | None = None,
) -> dict[str, Any]:
    receipts = load_gaza_receipts(source_root, date)
    effective_receipts, non_authoritative_receipts = _gaza_effective_receipts(receipts)
    runner_identity = _runner_git_identity(source_root)
    completeness, linkage = receipt_completeness(
        effective_receipts, source_root=source_root, expectations=GAZA_TASK_EXPECTATIONS
    )
    expected_instances = None if effective_receipts else [
        {
            "task_key": "gaza_daily_dispatch",
            "scheduled_for": _iso(_expected_run(date, "06:00", "America/Los_Angeles")),
        }
    ]
    aggregate = evaluate_dispatch_health(
        dispatch="gaza",
        receipts=effective_receipts,
        expectations=GAZA_TASK_EXPECTATIONS,
        evaluated_at=evaluated_at,
        recovery=recovery,
        expected_instances=expected_instances,
    )
    aggregate_status = aggregate["overall_health"]
    stale_observability = bool(aggregate.get("stale_observability"))
    dry_run_recovery_proof = _latest_gaza_dry_run_recovery_proof(source_root, date)
    dry_run_recovery_applied = False
    if _monitor_success_overrides_stale(aggregate, effective_receipts, task_key="gaza_daily_dispatch"):
        aggregate_status = OperationalStatus.SUCCESS.value
        stale_observability = False
    if not effective_receipts and aggregate_status == OperationalStatus.SUCCESS.value:
        aggregate_status = OperationalStatus.UNKNOWN.value
    if (
        dry_run_recovery_proof
        and aggregate_status
        in {
            OperationalStatus.FAILED.value,
            OperationalStatus.MISSED.value,
            OperationalStatus.STALE_OBSERVABILITY.value,
            OperationalStatus.UNKNOWN.value,
        }
    ):
        aggregate_status = OperationalStatus.DEGRADED.value
        stale_observability = False
        dry_run_recovery_applied = True
    source_heads = {_safe_head(receipt.get("source_head")) for receipt in effective_receipts}
    source_heads.discard(None)
    publication_attempted = any(receipt.get("publication_attempted") is True for receipt in effective_receipts)
    publication_statuses = [receipt.get("publication_status") for receipt in effective_receipts if receipt.get("publication_status")]
    return _with_debug_summary({
        "schema_version": EXTERNAL_STATUS_SCHEMA_VERSION,
        "dispatch": "gaza",
        "migration_status": "MIGRATED",
        "observed_date": date,
        "aggregate_status": aggregate_status,
        "scheduled_health_authoritative": bool(receipts),
        "recovery_lifecycle": RecoveryState.RECOVERY_PENDING_RUNTIME_PROOF.value
        if dry_run_recovery_applied
        else _recovery_state(aggregate_status, recovery),
        "latest_runtime_proof_date": aggregate.get("latest_success_at"),
        "latest_recovery_proof_at": dry_run_recovery_proof.get("completed_at") if dry_run_recovery_proof else None,
        "dry_run_recovery_proof": dry_run_recovery_proof,
        "dry_run_recovery_applied": dry_run_recovery_applied,
        "receipt_completeness": completeness if effective_receipts else "NO_PROOF",
        "task_summaries": [_task_summary(receipt, linkage) for receipt in effective_receipts],
        "observed_receipt_count": len(receipts),
        "effective_receipt_count": len(effective_receipts),
        "non_authoritative_receipt_count": len(non_authoritative_receipts),
        "non_authoritative_receipts": [
            {
                "task_key": receipt.get("task_key"),
                "status": receipt.get("status") if receipt.get("status") in SUPPORTED_TASK_STATUSES else "UNKNOWN",
                "classification": str(receipt.get("classification") or "unknown"),
                "run_id": receipt.get("run_id"),
                "completed_at": receipt.get("completed_at"),
                "publication_attempted": receipt.get("publication_attempted"),
                "publication_status": receipt.get("publication_status"),
                "reason": "superseded_by_same_day_public_success",
            }
            for receipt in non_authoritative_receipts
        ],
        "runner_source_head": sorted(source_heads)[0] if len(source_heads) == 1 else None,
        **runner_identity,
        "publication_attempted": publication_attempted,
        "publication_status": publication_statuses[-1] if publication_statuses else None,
        "public_side_effects": {
            "publication_attempted": publication_attempted,
            "publication_status": publication_statuses[-1] if publication_statuses else None,
        },
        "stale_observability": stale_observability,
        "last_receipt_at": max((_handoff_time(item) for item in effective_receipts), default=None).isoformat().replace("+00:00", "Z") if effective_receipts else None,
        "last_exported_at": exported_at,
        "next_expected_run": next((item.get("next_expected_run") for item in effective_receipts if item.get("next_expected_run")), None),
        "agent_handoff": {
            "state": "NO_EXTERNAL_HANDOFF_EXPECTED",
            "last_attempt_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "latest_agent_run_id": None,
            "latest_status": None,
            "latest_classification": None,
            "unaccounted_count": 0,
            "stale": False,
        },
        "scheduled_task_keys": [item.task_key for item in GAZA_TASK_EXPECTATIONS],
    })


def build_ice_status(
    *,
    source_root: Path,
    date: str,
    evaluated_at: str,
    exported_at: str,
    recovery: RecoveryContext | None = None,
) -> dict[str, Any]:
    receipt_dates = _ice_receipt_dates_for_export_date(date, evaluated_at)
    receipts = load_ice_receipts_for_dates(source_root, receipt_dates)
    runner_identity = _runner_git_identity(source_root)
    completeness, linkage = receipt_completeness(
        receipts, source_root=source_root, expectations=ICE_TASK_EXPECTATIONS
    )
    expected_instances = None if receipts else [
        {
            "task_key": "ice_monitor",
            "scheduled_for": _iso(_expected_run(date, "21:15", "America/Los_Angeles")),
        }
    ]
    aggregate = evaluate_dispatch_health(
        dispatch="ice",
        receipts=receipts,
        expectations=ICE_TASK_EXPECTATIONS,
        evaluated_at=evaluated_at,
        recovery=recovery,
        expected_instances=expected_instances,
    )
    aggregate_status = aggregate["overall_health"]
    stale_observability = bool(aggregate.get("stale_observability"))
    if _monitor_success_overrides_stale(aggregate, receipts, task_key="ice_monitor"):
        aggregate_status = OperationalStatus.SUCCESS.value
        stale_observability = False
    if not receipts and aggregate_status == OperationalStatus.SUCCESS.value:
        aggregate_status = OperationalStatus.UNKNOWN.value
    unaccounted_event_count = sum(
        _positive_int((receipt.get("details") or {}).get("unaccounted"))
        for receipt in receipts
    )
    if unaccounted_event_count > 0:
        aggregate_status = OperationalStatus.FAILED.value
    source_heads = {_safe_head(receipt.get("source_head")) for receipt in receipts}
    source_heads.discard(None)
    publication_attempted = any(receipt.get("publication_attempted") is True for receipt in receipts)
    publication_statuses = [receipt.get("publication_status") for receipt in receipts if receipt.get("publication_status")]
    return _with_debug_summary({
        "schema_version": EXTERNAL_STATUS_SCHEMA_VERSION,
        "dispatch": "ice",
        "migration_status": "MIGRATED",
        "observed_date": date,
        "aggregate_status": aggregate_status,
        "scheduled_health_authoritative": bool(receipts),
        "recovery_lifecycle": _recovery_state(aggregate_status, recovery),
        "latest_runtime_proof_date": aggregate.get("latest_success_at"),
        "receipt_completeness": completeness if receipts else "NO_PROOF",
        "task_summaries": [_task_summary(receipt, linkage) for receipt in receipts],
        "runner_source_head": sorted(source_heads)[0] if len(source_heads) == 1 else None,
        **runner_identity,
        "publication_attempted": publication_attempted,
        "publication_status": publication_statuses[-1] if publication_statuses else None,
        "unaccounted_event_count": unaccounted_event_count,
        "public_side_effects": {
            "publication_attempted": publication_attempted,
            "publication_status": publication_statuses[-1] if publication_statuses else None,
        },
        "stale_observability": stale_observability,
        "last_receipt_at": max((_handoff_time(item) for item in receipts), default=None).isoformat().replace("+00:00", "Z") if receipts else None,
        "last_exported_at": exported_at,
        "next_expected_run": next((item.get("next_expected_run") for item in receipts if item.get("next_expected_run")), None),
        "agent_handoff": {
            "state": "NO_EXTERNAL_HANDOFF_EXPECTED",
            "last_attempt_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "latest_agent_run_id": None,
            "latest_status": None,
            "latest_classification": None,
            "unaccounted_count": 0,
            "stale": False,
        },
        "scheduled_task_keys": [item.task_key for item in ICE_TASK_EXPECTATIONS],
    })


def _system_status(states: dict[str, dict[str, Any]]) -> str:
    active = [
        str(state.get("aggregate_status") or OperationalStatus.UNKNOWN.value)
        for state in states.values()
        if state.get("migration_status") == "MIGRATED"
    ]
    for status in (
        OperationalStatus.FAILED.value,
        OperationalStatus.MISSED.value,
        OperationalStatus.STALE_OBSERVABILITY.value,
        OperationalStatus.DEGRADED.value,
        OperationalStatus.UNKNOWN.value,
    ):
        if status in active:
            return status
    return OperationalStatus.SUCCESS.value


def build_system_status(
    food_line_status: dict[str, Any],
    *,
    source_root: Path,
    exported_at: str,
    care_line_status: dict[str, Any] | None = None,
    gaza_status: dict[str, Any] | None = None,
    ice_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    states = {
        "food-line": {
            "migration_status": "MIGRATED",
            "aggregate_status": food_line_status["aggregate_status"],
            "recovery_lifecycle": food_line_status["recovery_lifecycle"],
            "current_runner_head": food_line_status.get("current_runner_head"),
            "current_runner_branch": food_line_status.get("current_runner_branch"),
            "debug_summary": food_line_status.get("debug_summary"),
            "agent_handoff": food_line_status["agent_handoff"],
            "private_review_backlog": food_line_status.get("private_review_backlog"),
        }
    }
    for dispatch in NON_MIGRATED_DISPATCHES:
        states[dispatch] = {
            "migration_status": "NOT_MIGRATED",
            "aggregate_status": "UNKNOWN",
            "recovery_lifecycle": RecoveryState.HEALTHY.value,
            "agent_handoff": load_agent_handoff_status(source_root, dispatch)
            if dispatch in {"care-line", "food-line"}
            else {
                "state": "NO_EXTERNAL_HANDOFF_EXPECTED",
                "last_attempt_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "latest_agent_run_id": None,
                "latest_status": None,
                "latest_classification": None,
                "unaccounted_count": 0,
                "stale": False,
            },
        }
    for dispatch in INTENTIONALLY_INACTIVE_DISPATCHES:
        states[dispatch] = {
            "migration_status": "INTENTIONALLY_INACTIVE",
            "aggregate_status": OperationalStatus.INTENTIONALLY_INACTIVE.value,
            "recovery_lifecycle": RecoveryState.HEALTHY.value,
            "scheduled_health_available": False,
            "expected_active_schedule": False,
            "agent_handoff": {
                "state": "NO_EXTERNAL_HANDOFF_EXPECTED",
                "last_attempt_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "latest_agent_run_id": None,
                "latest_status": None,
                "latest_classification": None,
                "unaccounted_count": 0,
                "stale": False,
            },
        }
    if care_line_status is not None and care_line_status.get("migration_status") == "MIGRATED":
        states["care-line"] = {
            "migration_status": "MIGRATED",
            "aggregate_status": care_line_status["aggregate_status"],
            "recovery_lifecycle": care_line_status["recovery_lifecycle"],
            "scheduled_health_available": bool(care_line_status.get("scheduled_health_authoritative")),
            "observed_date": care_line_status.get("observed_date"),
            "latest_runtime_proof_date": care_line_status.get("latest_runtime_proof_date"),
            "receipt_completeness": care_line_status.get("receipt_completeness"),
            "stale_observability": care_line_status.get("stale_observability"),
            "source_failure_summary": care_line_status.get("source_failure_summary"),
            "current_runner_head": care_line_status.get("current_runner_head"),
            "current_runner_branch": care_line_status.get("current_runner_branch"),
            "debug_summary": care_line_status.get("debug_summary"),
            "agent_handoff": care_line_status["agent_handoff"],
        }
    elif care_line_status is None:
        states["care-line"] = {
            "migration_status": "NOT_MIGRATED",
            "aggregate_status": "UNKNOWN",
            "recovery_lifecycle": RecoveryState.HEALTHY.value,
            "scheduled_health_available": False,
            "agent_handoff": load_agent_handoff_status(source_root, "care-line"),
        }
    if gaza_status is not None and gaza_status.get("migration_status") == "MIGRATED":
        states["gaza"] = {
            "migration_status": "MIGRATED",
            "aggregate_status": gaza_status["aggregate_status"],
            "recovery_lifecycle": gaza_status["recovery_lifecycle"],
            "scheduled_health_available": bool(gaza_status.get("scheduled_health_authoritative")),
            "observed_date": gaza_status.get("observed_date"),
            "latest_runtime_proof_date": gaza_status.get("latest_runtime_proof_date"),
            "latest_recovery_proof_at": gaza_status.get("latest_recovery_proof_at"),
            "receipt_completeness": gaza_status.get("receipt_completeness"),
            "stale_observability": gaza_status.get("stale_observability"),
            "dry_run_recovery_applied": gaza_status.get("dry_run_recovery_applied"),
            "current_runner_head": gaza_status.get("current_runner_head"),
            "current_runner_branch": gaza_status.get("current_runner_branch"),
            "debug_summary": gaza_status.get("debug_summary"),
            "agent_handoff": gaza_status["agent_handoff"],
        }
    if ice_status is not None and ice_status.get("migration_status") == "MIGRATED":
        states["ice"] = {
            "migration_status": "MIGRATED",
            "aggregate_status": ice_status["aggregate_status"],
            "recovery_lifecycle": ice_status["recovery_lifecycle"],
            "scheduled_health_available": bool(ice_status.get("scheduled_health_authoritative")),
            "observed_date": ice_status.get("observed_date"),
            "latest_runtime_proof_date": ice_status.get("latest_runtime_proof_date"),
            "receipt_completeness": ice_status.get("receipt_completeness"),
            "stale_observability": ice_status.get("stale_observability"),
            "current_runner_head": ice_status.get("current_runner_head"),
            "current_runner_branch": ice_status.get("current_runner_branch"),
            "debug_summary": ice_status.get("debug_summary"),
            "agent_handoff": ice_status["agent_handoff"],
        }
    system_status = _system_status(states)
    return {
        "schema_version": SYSTEM_STATUS_SCHEMA_VERSION,
        "exported_at": exported_at,
        "system_status": system_status,
        "dispatches": states,
        "stale_observability": [
            dispatch for dispatch, state in states.items() if state["aggregate_status"] == OperationalStatus.STALE_OBSERVABILITY.value
        ],
    }


@contextmanager
def exporter_lock(status_checkout: Path) -> Iterator[None]:
    lock_path = status_checkout / "ops" / "status" / ".operational-status-exporter.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    acquired = False
    try:
        try:
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        except OSError as exc:
            raise ExportError("another operational status export is running") from exc
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ExportError("another operational status export is running") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ExportError("another operational status export is running") from exc
        acquired = True
        yield
    finally:
        if acquired and os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        elif acquired:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            handle.close()
        except OSError:
            pass
        try:
            lock_path.unlink()
        except OSError:
            pass


def _reuse_exported_at(path: Path, payload: dict[str, Any]) -> str | None:
    if not path.exists():
        return None
    existing = _parse_json(path)
    old = dict(existing)
    old_exported = old.pop("last_exported_at", old.pop("exported_at", None))
    new = dict(payload)
    new_exported = new.pop("last_exported_at", new.pop("exported_at", None))
    return str(old_exported) if old == new and old_exported else None


def export_status(
    *,
    source_root: Path,
    status_checkout: Path,
    date: str,
    evaluated_at: str,
    recovery: RecoveryContext | None = None,
    exported_at: str | None = None,
    care_source_root: Path | None = None,
    care_expected_instances: Iterable[dict[str, Any]] | None = None,
    gaza_source_root: Path | None = None,
    ice_source_root: Path | None = None,
    force_refresh_dispatches: Iterable[str] | None = None,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    status_checkout = status_checkout.resolve()
    if source_root == status_checkout:
        raise ExportError("status checkout must be separate from the production source root")
    forced_dispatches = set(force_refresh_dispatches or ())
    unsupported_forced = sorted(forced_dispatches - {"food-line", "care-line", "gaza", "ice"})
    if unsupported_forced:
        raise ExportError(f"unsupported force_refresh_dispatches: {', '.join(unsupported_forced)}")
    exported_at = exported_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with exporter_lock(status_checkout):
        food_path = status_checkout / "ops" / "status" / "food-line" / "latest.json"
        food_payload = build_food_line_status(
            source_root=source_root,
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
            recovery=recovery,
        )
        if "food-line" not in forced_dispatches:
            reused = _reuse_exported_at(food_path, food_payload)
            if reused:
                food_payload["last_exported_at"] = reused
        if care_source_root is not None and care_expected_instances is None:
            raise ExportError("Care scheduler expected_instances are required when care_source_root is configured")
        care_payload = build_care_line_status(
            source_root=(care_source_root or source_root).resolve(),
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
            expected_instances=care_expected_instances,
        ) if care_source_root is not None else None
        care_path = status_checkout / "ops" / "status" / "care-line" / "latest.json"
        if care_payload is not None:
            if "care-line" not in forced_dispatches:
                care_reused = _reuse_exported_at(care_path, care_payload)
                if care_reused:
                    care_payload["last_exported_at"] = care_reused
        gaza_payload = build_gaza_status(
            source_root=gaza_source_root.resolve(),
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
            recovery=recovery,
        ) if gaza_source_root is not None else None
        gaza_path = status_checkout / "ops" / "status" / "gaza" / "latest.json"
        if gaza_payload is not None:
            if "gaza" not in forced_dispatches:
                gaza_reused = _reuse_exported_at(gaza_path, gaza_payload)
                if gaza_reused:
                    gaza_payload["last_exported_at"] = gaza_reused
        ice_payload = build_ice_status(
            source_root=ice_source_root.resolve(),
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
            recovery=recovery,
        ) if ice_source_root is not None else None
        ice_path = status_checkout / "ops" / "status" / "ice" / "latest.json"
        if ice_payload is not None:
            if "ice" not in forced_dispatches:
                ice_reused = _reuse_exported_at(ice_path, ice_payload)
                if ice_reused:
                    ice_payload["last_exported_at"] = ice_reused
        system_payload = build_system_status(
            food_payload,
            source_root=source_root,
            exported_at=exported_at,
            care_line_status=care_payload,
            gaza_status=gaza_payload,
            ice_status=ice_payload,
        )
        system_path = status_checkout / "ops" / "status" / "system" / "latest.json"
        system_reused = _reuse_exported_at(system_path, system_payload)
        if system_reused:
            system_payload["exported_at"] = system_reused
        history_path = status_checkout / "ops" / "status" / "food-line" / "history" / f"{date}.json"
        care_history_path = status_checkout / "ops" / "status" / "care-line" / "history" / f"{date}.json"
        gaza_history_path = status_checkout / "ops" / "status" / "gaza" / "history" / f"{date}.json"
        ice_history_path = status_checkout / "ops" / "status" / "ice" / "history" / f"{date}.json"
        atomic_write_json(food_path, food_payload)
        atomic_write_json(history_path, food_payload)
        if care_payload is not None:
            atomic_write_json(care_path, care_payload)
            atomic_write_json(care_history_path, care_payload)
        if gaza_payload is not None:
            atomic_write_json(gaza_path, gaza_payload)
            atomic_write_json(gaza_history_path, gaza_payload)
        if ice_payload is not None:
            atomic_write_json(ice_path, ice_payload)
            atomic_write_json(ice_history_path, ice_payload)
        atomic_write_json(system_path, system_payload)
    paths = [
        "ops/status/food-line/latest.json",
        f"ops/status/food-line/history/{date}.json",
        "ops/status/system/latest.json",
    ]
    if care_payload is not None:
        paths[2:2] = ["ops/status/care-line/latest.json", f"ops/status/care-line/history/{date}.json"]
    if gaza_payload is not None:
        insert_at = 2 + (2 if care_payload is not None else 0)
        paths[insert_at:insert_at] = ["ops/status/gaza/latest.json", f"ops/status/gaza/history/{date}.json"]
    if ice_payload is not None:
        insert_at = 2 + (2 if care_payload is not None else 0) + (2 if gaza_payload is not None else 0)
        paths[insert_at:insert_at] = ["ops/status/ice/latest.json", f"ops/status/ice/history/{date}.json"]
    result = {"food_line": food_payload, "system": system_payload, "paths": paths}
    if care_payload is not None:
        result["care_line"] = care_payload
    if gaza_payload is not None:
        result["gaza"] = gaza_payload
    if ice_payload is not None:
        result["ice"] = ice_payload
    return result


def rebuild_dispatch_status_artifacts(
    *,
    source_root: Path,
    dispatch: str,
    date: str,
    evaluated_at: str,
    exported_at: str,
) -> dict[str, Any]:
    """Rebuild one dispatch's local operational-status artifacts without git or remote effects."""
    source_root = source_root.resolve()
    if dispatch == "food-line":
        payload = build_food_line_status(
            source_root=source_root,
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
        )
    elif dispatch == "gaza":
        payload = build_gaza_status(
            source_root=source_root,
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
        )
    elif dispatch == "ice":
        payload = build_ice_status(
            source_root=source_root,
            date=date,
            evaluated_at=evaluated_at,
            exported_at=exported_at,
        )
    else:
        raise ExportError(f"local status rebuild is not supported for dispatch: {dispatch}")

    latest_path = source_root / "ops" / "status" / dispatch / "latest.json"
    history_path = source_root / "ops" / "status" / dispatch / "history" / f"{date}.json"
    reused = _reuse_exported_at(history_path, payload) or _reuse_exported_at(latest_path, payload)
    if reused:
        payload["last_exported_at"] = reused
    atomic_write_json(latest_path, payload)
    atomic_write_json(history_path, payload)
    return {
        "status": payload,
        "paths": [
            f"ops/status/{dispatch}/latest.json",
            f"ops/status/{dispatch}/history/{date}.json",
        ],
    }


def validate_status_paths(paths: list[str]) -> None:
    if any(not (path == "ops/status" or path.startswith("ops/status/")) for path in paths):
        raise ExportError("operational status export touched a path outside ops/status/")


def _is_status_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip()
    return normalized == "ops/status" or normalized.startswith("ops/status/")


def _git_args(status_checkout: Path, *args: str) -> list[str]:
    safe_directory = status_checkout.resolve().as_posix()
    return ["git", "-c", f"safe.directory={safe_directory}", *args]


def git_status_paths(status_checkout: Path) -> list[str]:
    result = subprocess.run(
        _git_args(status_checkout, "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=status_checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ExportError(result.stderr.strip() or "cannot inspect status checkout")
    paths = []
    for line in result.stdout.splitlines():
        if len(line) >= 4:
            paths.append(line[3:].replace("\\", "/").strip())
    return paths


def classify_status_checkout_state(status_checkout: Path) -> StatusCheckoutGitState:
    result = subprocess.run(
        _git_args(status_checkout, "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=status_checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ExportError(result.stderr.strip() or "cannot inspect status checkout")
    ignored_status = subprocess.run(
        _git_args(status_checkout, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching", "--", "ops/status"),
        cwd=status_checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if ignored_status.returncode:
        raise ExportError(ignored_status.stderr.strip() or "cannot inspect ignored status checkout paths")
    tracked_status_paths: list[str] = []
    untracked_status_paths: list[str] = []
    unexpected_paths: list[str] = []
    for line in [*result.stdout.splitlines(), *ignored_status.stdout.splitlines()]:
        if len(line) < 4:
            continue
        status = line[:2]
        path = line[3:].replace("\\", "/").strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        if status == "!!" and path.endswith("/"):
            directory = status_checkout / path
            if directory.is_dir():
                for child in sorted(item for item in directory.rglob("*") if item.is_file()):
                    child_path = child.relative_to(status_checkout).as_posix()
                    if _is_status_path(child_path):
                        untracked_status_paths.append(child_path)
                    else:
                        unexpected_paths.append(child_path)
                continue
        if _is_status_path(path):
            if status in {"??", "!!"}:
                untracked_status_paths.append(path)
            else:
                tracked_status_paths.append(path)
        else:
            unexpected_paths.append(path)
    if unexpected_paths:
        state = "UNSAFE_DIRTY"
    elif tracked_status_paths or untracked_status_paths:
        state = "SANCTIONED_STATUS_ONLY"
    else:
        state = "CLEAN"
    return StatusCheckoutGitState(
        state=state,
        tracked_status_paths=sorted(dict.fromkeys(tracked_status_paths)),
        untracked_status_paths=sorted(dict.fromkeys(untracked_status_paths)),
        unexpected_paths=sorted(dict.fromkeys(unexpected_paths)),
    )


def _git(status_checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _git_args(status_checkout, *args),
        cwd=status_checkout,
        capture_output=True,
        text=True,
        check=False,
    )


def _incoming_paths(status_checkout: Path) -> list[str]:
    incoming = _git(status_checkout, "diff", "--name-only", "HEAD..FETCH_HEAD")
    if incoming.returncode:
        raise ExportError(incoming.stderr.strip() or "cannot inspect incoming status checkout paths")
    return sorted(line.strip().replace("\\", "/") for line in incoming.stdout.splitlines() if line.strip())


def _status_file_hashes(status_checkout: Path, paths: Iterable[str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for path in sorted(dict.fromkeys(paths)):
        target = status_checkout / path
        hashes[path] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    return hashes


def _dirty_status_paths(state: StatusCheckoutGitState) -> list[str]:
    return sorted(dict.fromkeys([*state.tracked_status_paths, *state.untracked_status_paths]))


def prepare_status_checkout(
    status_checkout: Path,
    *,
    branch: str,
    remote: str = "origin",
    allow_local_status_changes: bool = False,
) -> None:
    """Synchronize a dedicated status checkout without touching production repos."""
    status_checkout = status_checkout.resolve()
    current = _git(status_checkout, "branch", "--show-current")
    if current.returncode or current.stdout.strip() != branch:
        raise ExportError(f"status checkout branch mismatch: expected {branch}")
    local_state = classify_status_checkout_state(status_checkout)
    if local_state.state == "UNSAFE_DIRTY":
        raise ExportError("status checkout contains dirty paths outside ops/status/")
    if local_state.state != "CLEAN" and not allow_local_status_changes:
        raise ExportError("status checkout must be clean before fast-forward")
    fetched = _git(status_checkout, "fetch", "--no-tags", remote, f"refs/heads/{branch}")
    if fetched.returncode:
        raise ExportError(fetched.stderr.strip() or "status checkout fetch failed")
    if local_state.state == "SANCTIONED_STATUS_ONLY":
        dirty_status_paths = set(local_state.tracked_status_paths) | set(local_state.untracked_status_paths)
        overlap = sorted(dirty_status_paths & set(_incoming_paths(status_checkout)))
        if overlap:
            raise ExportError(f"incoming status checkout changes overlap local ops/status changes: {', '.join(overlap)}")
    ancestor = _git(status_checkout, "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")
    if ancestor.returncode:
        raise ExportError("status checkout cannot fast-forward to its protected branch")
    merged = _git(status_checkout, "merge", "--ff-only", "FETCH_HEAD")
    if merged.returncode:
        raise ExportError(merged.stderr.strip() or "status checkout fast-forward failed")


def commit_and_push_status(
    status_checkout: Path,
    *,
    paths: list[str],
    message: str,
    remote: str = "origin",
    branch: str | None = None,
    allowed_unstaged_status_paths: list[str] | None = None,
    return_details: bool = False,
) -> str | dict[str, Any] | None:
    """Commit and push only sanitized status artifacts from the status checkout."""
    approved_paths = sorted(dict.fromkeys(paths))
    carryover_paths = sorted(dict.fromkeys(allowed_unstaged_status_paths or []))
    validate_status_paths(approved_paths)
    validate_status_paths(carryover_paths)
    state_before = classify_status_checkout_state(status_checkout)
    if state_before.unexpected_paths:
        raise ExportError("status checkout contains dirty paths outside ops/status/")
    actual = _dirty_status_paths(state_before)
    allowed_dirty = set(approved_paths) | set(carryover_paths)
    if set(actual) - allowed_dirty:
        raise ExportError("status checkout contains an unapproved ops/status path")
    preserved_carryover = sorted(set(carryover_paths) - set(approved_paths))
    carryover_hashes_before = _status_file_hashes(status_checkout, preserved_carryover)
    staged = _git(status_checkout, "add", "--force", "--", *approved_paths)
    if staged.returncode:
        raise ExportError(staged.stderr.strip() or "status artifact staging failed")
    staged_names = _git(status_checkout, "diff", "--cached", "--name-only")
    if staged_names.returncode:
        raise ExportError(staged_names.stderr.strip() or "cannot inspect staged status artifacts")
    staged_paths = [line.strip().replace("\\", "/") for line in staged_names.stdout.splitlines() if line.strip()]
    validate_status_paths(staged_paths)
    if set(staged_paths) - set(approved_paths):
        raise ExportError("status artifact staging included an unapproved path")
    if not staged_paths:
        if return_details:
            return {
                "commit": None,
                "staged_paths": [],
                "committed_paths": [],
                "preserved_carryover_paths": preserved_carryover,
                "carryover_hashes_before": carryover_hashes_before,
                "carryover_hashes_after": _status_file_hashes(status_checkout, preserved_carryover),
            }
        return None
    commit = _git(status_checkout, "commit", "-m", message)
    if commit.returncode:
        raise ExportError(commit.stderr.strip() or "status artifact commit failed")
    carryover_hashes_after = _status_file_hashes(status_checkout, preserved_carryover)
    if carryover_hashes_after != carryover_hashes_before:
        raise ExportError("preserved status carryover changed during commit")
    state_after_commit = classify_status_checkout_state(status_checkout)
    dirty_after_commit = set(_dirty_status_paths(state_after_commit))
    missing_carryover = sorted(path for path in preserved_carryover if path not in dirty_after_commit)
    if missing_carryover:
        raise ExportError(f"preserved status carryover was unexpectedly staged or cleaned: {', '.join(missing_carryover)}")
    target_branch = branch or _git(status_checkout, "branch", "--show-current").stdout.strip()
    if not target_branch:
        raise ExportError("status checkout is detached")
    pushed = _git(status_checkout, "push", remote, target_branch)
    if pushed.returncode:
        raise ExportError(pushed.stderr.strip() or "status artifact push failed")
    commit_sha = _git(status_checkout, "rev-parse", "HEAD").stdout.strip()
    if return_details:
        return {
            "commit": commit_sha,
            "staged_paths": staged_paths,
            "committed_paths": staged_paths,
            "preserved_carryover_paths": preserved_carryover,
            "carryover_hashes_before": carryover_hashes_before,
            "carryover_hashes_after": carryover_hashes_after,
        }
    return commit_sha


def load_recovery_context(path: Path | None) -> RecoveryContext | None:
    if path is None:
        return None
    value = _parse_json(path)
    return RecoveryContext(
        incident_opened_at=value.get("incident_opened_at"),
        fix_deployed_at=value.get("fix_deployed_at"),
        runtime_proof_after=value.get("runtime_proof_after"),
        recovered_at=value.get("recovered_at"),
    )
