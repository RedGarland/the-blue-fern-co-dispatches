from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

from .operational_health import (
    DISPATCH_AGGREGATE_SCHEMA_VERSION,
    CARE_LINE_TASK_EXPECTATIONS,
    FOOD_LINE_TASK_EXPECTATIONS,
    OperationalStatus,
    RecoveryContext,
    RecoveryState,
    RECEIPT_SCHEMA_VERSION,
    atomic_write_json,
    evaluate_dispatch_health,
    parse_timestamp,
    validate_operational_receipt,
)


EXTERNAL_STATUS_SCHEMA_VERSION = "bluefern_external_operational_status_v1"
SYSTEM_STATUS_SCHEMA_VERSION = "bluefern_external_system_status_v1"
SUPPORTED_TASK_STATUSES = {status.value for status in OperationalStatus}
SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
HEX_HEAD_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
PRIVATE_KEY_RE = re.compile(r"(?:path|body|excerpt|source|raw|secret|token|credential|password|environment|env)", re.IGNORECASE)

NON_MIGRATED_DISPATCHES = ("gaza", "care-line", "ice", "cascadia", "american-pressure")
HANDOFF_STATES = {
    "NO_EXTERNAL_HANDOFF_EXPECTED",
    "HANDOFF_RECEIVED_SUCCESS",
    "HANDOFF_FAILED",
    "HANDOFF_STALE_UNPROCESSED",
}
HANDOFF_TERMINAL_STATUSES = {"SUCCESS", "SAFE_NO_OP", "FAILED"}
RETIREMENT_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class ExportError(RuntimeError):
    """Raised when an external status export cannot be safely completed."""


def _parse_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read receipt {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"receipt must be an object: {path.name}")
    return value


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
    receipts = []
    for path in _receipt_paths(source_root, "care-line", date):
        receipt = _parse_json(path)
        try:
            validate_operational_receipt(receipt)
        except ValueError as exc:
            raise ExportError(f"invalid operational receipt {path.name}: {exc}") from exc
        if receipt.get("dispatch") != "care-line":
            raise ExportError(f"receipt dispatch mismatch: {path.name}")
        receipts.append(receipt)
    return receipts


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
        artifact = receipt.get("artifact_refs", {}).get("task_receipt")
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
    if expectations == FOOD_LINE_TASK_EXPECTATIONS and any(len(grouped.get(task_key, [])) != 1 for task_key in expected):
        inconsistent = inconsistent or any(len(grouped.get(task_key, [])) > 1 for task_key in expected)
    missing = expected - set(grouped)
    if inconsistent:
        return "INCONSISTENT", linkage
    if missing:
        return ("MISSING" if not grouped else "PARTIAL"), linkage
    return "COMPLETE", linkage


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


def _task_summary(receipt: dict[str, Any], linkage: dict[str, str]) -> dict[str, Any]:
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
        "artifact_id": linkage.get(str(receipt.get("task_key"))),
    }


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
    completeness, linkage = receipt_completeness(receipts, source_root=source_root)
    stale, last_receipt_at, stale_after = _staleness(receipts, date=date, evaluated_at=evaluated_at)
    aggregate = evaluate_dispatch_health(
        dispatch="food-line",
        receipts=receipts,
        expectations=FOOD_LINE_TASK_EXPECTATIONS,
        evaluated_at=evaluated_at,
        recovery=recovery,
    )
    if stale:
        aggregate_status = OperationalStatus.STALE_OBSERVABILITY.value
    elif aggregate["failed_tasks"]:
        aggregate_status = OperationalStatus.FAILED.value
    elif aggregate["missed_tasks"]:
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
    return {
        "schema_version": EXTERNAL_STATUS_SCHEMA_VERSION,
        "dispatch": "food-line",
        "observed_date": date,
        "aggregate_status": aggregate_status,
        "recovery_lifecycle": _recovery_state(aggregate_status, recovery),
        "incident_reference": {"date": date} if aggregate_status in {"FAILED", "STALE_OBSERVABILITY"} else None,
        "latest_runtime_proof_date": aggregate.get("latest_success_at"),
        "receipt_completeness": completeness,
        "task_summaries": [_task_summary(receipt, linkage) for receipt in receipts],
        "runner_source_head": sorted(source_heads)[0] if len(source_heads) == 1 else None,
        "publication_attempted": publication_attempted,
        "publication_status": publication_statuses[-1] if publication_statuses else None,
        "public_side_effects": {
            "publication_attempted": publication_attempted,
            "publication_status": publication_statuses[-1] if publication_statuses else None,
        },
        "stale_observability": stale,
        "last_receipt_at": last_receipt_at,
        "last_exported_at": exported_at,
        "next_expected_run": next((item.get("next_expected_run") for item in receipts if item.get("next_expected_run")), None),
        "stale_after": stale_after,
        "agent_handoff": agent_handoff,
    }


def build_care_line_status(
    *,
    source_root: Path,
    date: str,
    evaluated_at: str,
    exported_at: str,
    recovery: RecoveryContext | None = None,
    expected_instances: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    receipts = load_care_line_receipts(source_root, date)
    completeness, linkage = receipt_completeness(
        receipts, source_root=source_root, expectations=CARE_LINE_TASK_EXPECTATIONS
    )
    aggregate = evaluate_dispatch_health(
        dispatch="care-line",
        receipts=receipts,
        expectations=CARE_LINE_TASK_EXPECTATIONS,
        evaluated_at=evaluated_at,
        expected_instances=list(expected_instances) if expected_instances is not None else [
            {"task_key": receipt.get("task_key"), "scheduled_for": receipt.get("scheduled_for")}
            for receipt in receipts
        ],
        recovery=recovery,
    ) if receipts else {
        "overall_health": OperationalStatus.UNKNOWN.value,
        "recovery_state": RecoveryState.HEALTHY.value,
        "expected_tasks": [item.task_key for item in CARE_LINE_TASK_EXPECTATIONS],
        "completed_tasks": [], "missed_tasks": [], "failed_tasks": [],
        "degraded_tasks": [], "upstream_blocked_tasks": [],
        "stale_observability": [], "latest_success_at": None,
    }
    aggregate_status = aggregate["overall_health"] if receipts else OperationalStatus.UNKNOWN.value
    source_heads = {_safe_head(receipt.get("source_head")) for receipt in receipts}
    source_heads.discard(None)
    publication_attempted = any(receipt.get("publication_attempted") is True for receipt in receipts)
    publication_statuses = [receipt.get("publication_status") for receipt in receipts if receipt.get("publication_status")]
    return {
        "schema_version": EXTERNAL_STATUS_SCHEMA_VERSION,
        "dispatch": "care-line",
        "migration_status": "NOT_MIGRATED",
        "observed_date": date,
        "aggregate_status": aggregate_status,
        "scheduled_health_authoritative": bool(receipts),
        "recovery_lifecycle": _recovery_state(aggregate_status, recovery),
        "receipt_completeness": completeness if receipts else "NO_PROOF",
        "task_summaries": [_task_summary(receipt, linkage) for receipt in receipts],
        "runner_source_head": sorted(source_heads)[0] if len(source_heads) == 1 else None,
        "publication_attempted": publication_attempted,
        "publication_status": publication_statuses[-1] if publication_statuses else None,
        "public_side_effects": {"publication_attempted": publication_attempted, "publication_status": publication_statuses[-1] if publication_statuses else None},
        "stale_observability": bool(aggregate.get("stale_observability")),
        "last_receipt_at": max((_handoff_time(item) for item in receipts), default=None).isoformat().replace("+00:00", "Z") if receipts else None,
        "last_exported_at": exported_at,
        "agent_handoff": load_agent_handoff_status(source_root, "care-line"),
        "scheduled_task_keys": [item.task_key for item in CARE_LINE_TASK_EXPECTATIONS],
    }


def build_system_status(
    food_line_status: dict[str, Any],
    *,
    source_root: Path,
    exported_at: str,
    care_line_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    states = {
        "food-line": {
            "migration_status": "MIGRATED",
            "aggregate_status": food_line_status["aggregate_status"],
            "recovery_lifecycle": food_line_status["recovery_lifecycle"],
            "agent_handoff": food_line_status["agent_handoff"],
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
    if care_line_status is not None:
        states["care-line"]["scheduled_health_available"] = bool(care_line_status.get("scheduled_health_authoritative"))
        # Care remains NOT_MIGRATED until production deployment and real proof.
        states["care-line"]["agent_handoff"] = care_line_status["agent_handoff"]
    return {
        "schema_version": SYSTEM_STATUS_SCHEMA_VERSION,
        "exported_at": exported_at,
        "system_status": food_line_status["aggregate_status"],
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
) -> dict[str, Any]:
    source_root = source_root.resolve()
    status_checkout = status_checkout.resolve()
    if source_root == status_checkout:
        raise ExportError("status checkout must be separate from the production source root")
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
        reused = _reuse_exported_at(food_path, food_payload)
        if reused:
            food_payload["last_exported_at"] = reused
        care_payload = build_care_line_status(
            source_root=(care_source_root or source_root).resolve(),
            date=date,
            evaluated_at=evaluated_at,
            exported_at=food_payload["last_exported_at"],
        ) if care_source_root is not None else None
        system_payload = build_system_status(
            food_payload,
            source_root=source_root,
            exported_at=food_payload["last_exported_at"],
            care_line_status=care_payload,
        )
        history_path = status_checkout / "ops" / "status" / "food-line" / "history" / f"{date}.json"
        care_path = status_checkout / "ops" / "status" / "care-line" / "latest.json"
        care_history_path = status_checkout / "ops" / "status" / "care-line" / "history" / f"{date}.json"
        system_path = status_checkout / "ops" / "status" / "system" / "latest.json"
        atomic_write_json(food_path, food_payload)
        atomic_write_json(history_path, food_payload)
        if care_payload is not None:
            care_reused = _reuse_exported_at(care_path, care_payload)
            if care_reused:
                care_payload["last_exported_at"] = care_reused
            atomic_write_json(care_path, care_payload)
            atomic_write_json(care_history_path, care_payload)
        atomic_write_json(system_path, system_payload)
    paths = [
        "ops/status/food-line/latest.json",
        f"ops/status/food-line/history/{date}.json",
        "ops/status/system/latest.json",
    ]
    if care_payload is not None:
        paths[2:2] = ["ops/status/care-line/latest.json", f"ops/status/care-line/history/{date}.json"]
    result = {"food_line": food_payload, "system": system_payload, "paths": paths}
    if care_payload is not None:
        result["care_line"] = care_payload
    return result


def validate_status_paths(paths: list[str]) -> None:
    if any(not (path == "ops/status" or path.startswith("ops/status/")) for path in paths):
        raise ExportError("operational status export touched a path outside ops/status/")


def git_status_paths(status_checkout: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
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


def _git(status_checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=status_checkout,
        capture_output=True,
        text=True,
        check=False,
    )


def prepare_status_checkout(status_checkout: Path, *, branch: str, remote: str = "origin") -> None:
    """Synchronize a dedicated status checkout without touching production repos."""
    status_checkout = status_checkout.resolve()
    current = _git(status_checkout, "branch", "--show-current")
    if current.returncode or current.stdout.strip() != branch:
        raise ExportError(f"status checkout branch mismatch: expected {branch}")
    existing = git_status_paths(status_checkout)
    validate_status_paths(existing)
    if existing:
        raise ExportError("status checkout must be clean before fast-forward")
    fetched = _git(status_checkout, "fetch", remote, branch)
    if fetched.returncode:
        raise ExportError(fetched.stderr.strip() or "status checkout fetch failed")
    ancestor = _git(status_checkout, "merge-base", "--is-ancestor", "HEAD", f"{remote}/{branch}")
    if ancestor.returncode:
        raise ExportError("status checkout cannot fast-forward to its protected branch")
    merged = _git(status_checkout, "merge", "--ff-only", f"{remote}/{branch}")
    if merged.returncode:
        raise ExportError(merged.stderr.strip() or "status checkout fast-forward failed")


def commit_and_push_status(
    status_checkout: Path,
    *,
    paths: list[str],
    message: str,
    remote: str = "origin",
    branch: str | None = None,
) -> str | None:
    """Commit and push only sanitized status artifacts from the status checkout."""
    validate_status_paths(paths)
    actual = git_status_paths(status_checkout)
    validate_status_paths(actual)
    if set(actual) - set(paths):
        raise ExportError("status checkout contains an unapproved ops/status path")
    if not actual:
        return None
    staged = _git(status_checkout, "add", "--", *paths)
    if staged.returncode:
        raise ExportError(staged.stderr.strip() or "status artifact staging failed")
    staged_names = _git(status_checkout, "diff", "--cached", "--name-only")
    if staged_names.returncode:
        raise ExportError(staged_names.stderr.strip() or "cannot inspect staged status artifacts")
    staged_paths = [line.strip().replace("\\", "/") for line in staged_names.stdout.splitlines() if line.strip()]
    validate_status_paths(staged_paths)
    commit = _git(status_checkout, "commit", "-m", message)
    if commit.returncode:
        raise ExportError(commit.stderr.strip() or "status artifact commit failed")
    target_branch = branch or _git(status_checkout, "branch", "--show-current").stdout.strip()
    if not target_branch:
        raise ExportError("status checkout is detached")
    pushed = _git(status_checkout, "push", remote, target_branch)
    if pushed.returncode:
        raise ExportError(pushed.stderr.strip() or "status artifact push failed")
    return _git(status_checkout, "rev-parse", "HEAD").stdout.strip()


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
