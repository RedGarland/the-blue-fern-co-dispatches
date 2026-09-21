from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.dispatch_ops import (  # noqa: E402
    ApplyResult,
    DispatchStatus,
    RecoveryPlan,
    apply_recovery_plan,
    build_recovery_plan_from_status,
    build_status,
)


SCHEMA_VERSION = "blue_fern_operator_result_v1"
INCIDENT_SCHEMA_VERSION = "blue_fern_operator_incident_v1"
NOTIFICATION_SCHEMA_VERSION = "blue_fern_operator_notification_v1"
RUN_RECEIPT_SCHEMA_VERSION = "blue_fern_operator_run_receipt_v1"
ENGINEERING_SCHEMA_VERSION = "blue_fern_operator_engineering_v1"
CODEX_ATTEMPT_SCHEMA_VERSION = "blue_fern_operator_codex_attempt_v1"
DEFAULT_CONFIG_PATH = ROOT / "ops" / "operator" / "config.json"
DEFAULT_OPERATOR_ROOT = ROOT / "ops" / "operator"
DISPATCH_ORDER = ("food-line", "care-line", "gaza", "ice")
TERMINAL_NO_ACTION_STATES = {"COMPLETE", "PUBLISHED", "NO_UPDATE", "SAFE_NO_OP"}
FORBIDDEN_REMEDIATION_ACTIONS = {
    "PUBLISH_APPROVED_RELEASE",
    "PUBLISH_NO_UPDATE",
    "REPLAY_COLLECTION",
}
AUTOMATIC_REMEDIATION_ACTIONS = {"REBUILD_STATUS"}
REMEDIATION_OUTCOMES = {
    "NOT_ATTEMPTED",
    "REBUILT",
    "ALREADY_CURRENT",
    "NO_ACTION",
    "REFUSED",
    "BLOCKED",
    "FAILED",
}
NOTIFICATION_REASONS = {
    "NEW_INCIDENT",
    "INCIDENT_WORSENED",
    "REMEDIATION_FAILED",
    "MATERIAL_RECOVERY",
    "APPROVAL_REQUIRED",
    "OPERATOR_FAILURE",
}
ENGINEERING_ELIGIBLE_CLASSES = {"FAILED_RUN", "STATUS_EXPORT_PROBLEM"}
APPROVED_ENGINEERING_WORKTREE_ROOT = Path(r"C:\BlueFernRunner\OperatorWorktrees")
ENGINEERING_BLOCKED_ACTIONS = {
    "NONE",
    "REBUILD_STATUS",
    "REVIEW_CANDIDATES",
    "VERIFY_PUBLIC_STATE",
    "PUBLISH_APPROVED_RELEASE",
    "PUBLISH_NO_UPDATE",
}
ENGINEERING_STATES = {
    "DETECTED",
    "WORKTREE_CREATED",
    "DIAGNOSING",
    "PATCHED",
    "VALIDATING",
    "PR_READY",
    "PR_OPEN",
    "BLOCKED",
    "FAILED",
    "CLOSED",
}
STATUS_SEVERITY = {
    "RECOVERED": 0,
    "COMPLETE": 0,
    "PUBLISHED": 0,
    "NO_UPDATE": 0,
    "SAFE_NO_OP": 0,
    "NEEDS_REVIEW": 20,
    "DEGRADED": 30,
    "UNKNOWN": 40,
    "MISSED": 50,
    "FAILED": 60,
}
CLASSIFICATION_SEVERITY = {
    "STALE_OBSERVABILITY": 10,
    "NEEDS_REVIEW": 20,
    "DEGRADED_RUN": 30,
    "STATUS_EXPORT_PROBLEM": 35,
    "UNKNOWN_OPERATIONAL_STATE": 40,
    "PUBLIC_STATE_UNVERIFIED": 45,
    "MISSED_RUN": 50,
    "FAILED_RUN": 60,
}


class IncidentState(StrEnum):
    OPEN = "OPEN"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"
    SUPPRESSED = "SUPPRESSED"


class Classification(StrEnum):
    STALE_OBSERVABILITY = "STALE_OBSERVABILITY"
    FAILED_RUN = "FAILED_RUN"
    MISSED_RUN = "MISSED_RUN"
    DEGRADED_RUN = "DEGRADED_RUN"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PUBLIC_STATE_UNVERIFIED = "PUBLIC_STATE_UNVERIFIED"
    STATUS_EXPORT_PROBLEM = "STATUS_EXPORT_PROBLEM"
    UNKNOWN_OPERATIONAL_STATE = "UNKNOWN_OPERATIONAL_STATE"


class NotificationReason(StrEnum):
    NEW_INCIDENT = "NEW_INCIDENT"
    INCIDENT_WORSENED = "INCIDENT_WORSENED"
    REMEDIATION_FAILED = "REMEDIATION_FAILED"
    MATERIAL_RECOVERY = "MATERIAL_RECOVERY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    OPERATOR_FAILURE = "OPERATOR_FAILURE"


class EngineeringState(StrEnum):
    DETECTED = "DETECTED"
    WORKTREE_CREATED = "WORKTREE_CREATED"
    DIAGNOSING = "DIAGNOSING"
    PATCHED = "PATCHED"
    VALIDATING = "VALIDATING"
    PR_READY = "PR_READY"
    PR_OPEN = "PR_OPEN"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class DispatchConfig:
    dispatch: str
    runner_root: Path
    enabled: bool = True


@dataclass(frozen=True)
class OperatorConfig:
    status_freshness_threshold_minutes: int
    dispatches: dict[str, DispatchConfig]


@dataclass(frozen=True)
class RemediationPolicy:
    modes: dict[str, str]

    def mode_for(self, action: str) -> str:
        return self.modes.get(action, "recommend")


@dataclass(frozen=True)
class ExportedStatusObservation:
    path: str
    observed_date: str | None
    exported_at: str | None
    age_minutes: float | None
    is_fresh: bool
    evidence: list[str] = field(default_factory=list)
    source_root: str = ""


@dataclass(frozen=True)
class Incident:
    incident_id: str
    incident_key: str
    dispatch: str
    detected_at: str
    state: str
    classification: str
    status_state: str
    recovery_disposition: str
    recovery_action: str
    evidence: list[str]
    recommended_action: str
    production_state_mutated: bool = False
    remediation: dict[str, Any] = field(default_factory=dict)
    schema_version: str = INCIDENT_SCHEMA_VERSION
    affected_date: str | None = None
    updated_at: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class EngineeringWorkItem:
    work_id: str
    incident_id: str
    dispatch: str
    classification: str
    state: str
    branch: str
    worktree: str
    evidence: list[str]
    allowed_paths: list[str]
    tests_required: list[str]
    pr_number: int | None = None
    pr_state: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    repair_generation: int = 1
    repair_attempts: int = 0
    merge_allowed: bool = False
    diagnosis_path: str | None = None
    codex_prompt_path: str | None = None
    diagnostic_artifact: str | None = None
    failure_classification: str | None = None
    pr_url: str | None = None
    root_cause: str | None = None
    retry_authorization: dict[str, Any] | None = None
    unexpected_paths: list[str] = field(default_factory=list)
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: str | None = None
    schema_version: str = ENGINEERING_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class EngineeringCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class NotificationEvent:
    notification_required: bool
    reasons: list[str] = field(default_factory=list)
    dispatches: list[str] = field(default_factory=list)
    incident_ids: list[str] = field(default_factory=list)
    summary: list[dict[str, Any]] = field(default_factory=list)
    schema_version: str = NOTIFICATION_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class DispatchResult:
    dispatch: str
    state: str
    classification: str | None
    recommended_action: str
    status_state: str
    observed_date: str | None
    recovery_disposition: str
    recovery_action: str
    incident_id: str | None
    evidence: list[str]
    exported_status: dict[str, Any] | None = None
    notification_state: str = "RECOMMENDATION_ONLY"
    remediation: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class OperatorResult:
    checked_at: str
    dispatches: list[DispatchResult]
    incidents: list[Incident]
    notification: NotificationEvent = field(default_factory=lambda: NotificationEvent(notification_required=False))
    production_state_mutated: bool = False
    schema_version: str = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "dispatches": [result.to_payload() for result in self.dispatches],
            "incidents": [incident.to_payload() for incident in self.incidents],
            "notification": self.notification.to_payload(),
            "production_state_mutated": self.production_state_mutated,
            "schema_version": self.schema_version,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> OperatorConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    operator = payload.get("operator") if isinstance(payload.get("operator"), dict) else {}
    dispatch_rows = payload.get("dispatches") if isinstance(payload.get("dispatches"), dict) else {}
    dispatches: dict[str, DispatchConfig] = {}
    for dispatch in DISPATCH_ORDER:
        row = dispatch_rows.get(dispatch) if isinstance(dispatch_rows.get(dispatch), dict) else {}
        dispatches[dispatch] = DispatchConfig(
            dispatch=dispatch,
            runner_root=Path(str(row.get("runner_root") or "")),
            enabled=bool(row.get("enabled", True)),
        )
    return OperatorConfig(
        status_freshness_threshold_minutes=int(operator.get("status_freshness_threshold_minutes") or 120),
        dispatches=dispatches,
    )


def load_remediation_policy(path: Path) -> RemediationPolicy:
    modes: dict[str, str] = {}
    if not path.is_file():
        return RemediationPolicy(modes=modes)
    current_action: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not raw_line.startswith((" ", "\t")) and line.endswith(":"):
            current_action = line[:-1]
            continue
        if current_action and line.startswith("mode:"):
            mode = line.split(":", 1)[1].strip()
            if mode in {"automatic", "recommend", "forbidden", "automatic_prepare_pr", "approval_required"}:
                modes[current_action] = mode
    return RemediationPolicy(modes=modes)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _latest_exported_status(root: Path, dispatch: str, now: datetime, threshold_minutes: int) -> ExportedStatusObservation:
    path = root / "ops" / "status" / dispatch / "latest.json"
    payload = _read_json(path)
    if not payload:
        return ExportedStatusObservation(
            path=_rel(root, path),
            observed_date=None,
            exported_at=None,
            age_minutes=None,
            is_fresh=False,
            evidence=[f"{_rel(root, path)} missing"],
            source_root=str(root),
        )
    exported_at = str(payload.get("last_exported_at") or payload.get("exported_at") or "")
    exported_time = _parse_time(exported_at)
    age_minutes = ((now - exported_time).total_seconds() / 60) if exported_time else None
    evidence = [_rel(root, path)]
    if exported_at:
        evidence.append(f"exported_at={exported_at}")
    observed_date = str(payload.get("observed_date") or "") or None
    if observed_date:
        evidence.append(f"observed_date={observed_date}")
    is_fresh = age_minutes is not None and age_minutes <= threshold_minutes
    return ExportedStatusObservation(
        path=_rel(root, path),
        observed_date=observed_date,
        exported_at=exported_at or None,
        age_minutes=age_minutes,
        is_fresh=is_fresh,
        evidence=evidence,
        source_root=str(root),
    )


def _select_exported_status(repo_exported: ExportedStatusObservation, runner_exported: ExportedStatusObservation) -> ExportedStatusObservation:
    if repo_exported.is_fresh:
        return repo_exported
    if runner_exported.is_fresh:
        return runner_exported
    if repo_exported.observed_date or repo_exported.exported_at:
        return repo_exported
    return runner_exported


def _latest_runner_date(root: Path, dispatch: str) -> str | None:
    health_root = root / "status" / "operational-health" / dispatch
    if health_root.is_dir():
        dates = sorted(path.name for path in health_root.iterdir() if path.is_dir() and _is_date(path.name))
        if dates:
            return dates[-1]
    status_root = root / "ops" / "status" / dispatch / "history"
    if status_root.is_dir():
        dates = sorted(path.stem for path in status_root.glob("????-??-??.json"))
        if dates:
            return dates[-1]
    return None


def _is_date(value: str) -> bool:
    if len(value) != 10:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _classify_status(status: DispatchStatus) -> Classification | None:
    if status.state in TERMINAL_NO_ACTION_STATES and status.next_action == "NONE":
        return None
    if status.state == "FAILED":
        return Classification.FAILED_RUN
    if status.state == "MISSED":
        return Classification.MISSED_RUN
    if status.state == "DEGRADED":
        return Classification.DEGRADED_RUN
    if status.state == "NEEDS_REVIEW" or status.next_action == "REVIEW_CANDIDATES":
        return Classification.NEEDS_REVIEW
    if status.state == "UNKNOWN":
        return Classification.UNKNOWN_OPERATIONAL_STATE
    if status.next_action == "VERIFY_PUBLIC_STATE":
        return Classification.PUBLIC_STATE_UNVERIFIED
    if status.next_action == "INVESTIGATE_STATUS_EXPORT":
        return Classification.STATUS_EXPORT_PROBLEM
    return Classification.UNKNOWN_OPERATIONAL_STATE


def _recommended_action(classification: Classification | None, status: DispatchStatus, plan: RecoveryPlan) -> str:
    if classification is None:
        return "NO_ACTION"
    if classification == Classification.STALE_OBSERVABILITY and plan.action == "REBUILD_STATUS":
        return "REBUILD_STATUS"
    if plan.action in FORBIDDEN_REMEDIATION_ACTIONS:
        return "INVESTIGATE_STATUS_EXPORT"
    if plan.action and plan.action != "NONE":
        return plan.action
    if status.next_action in {"PUBLISH_APPROVED_RELEASE", "PUBLISH_NO_UPDATE", "RECOVER_MISSING_RUN"}:
        return "INVESTIGATE_STATUS_EXPORT"
    if status.next_action and status.next_action != "NONE":
        return status.next_action
    return "INVESTIGATE_STATUS_EXPORT"


def _observability_recovery_action(status: DispatchStatus, plan: RecoveryPlan) -> str:
    if plan.action == "REBUILD_STATUS":
        return "REBUILD_STATUS"
    if status.next_action == "REBUILD_STATUS":
        return "REBUILD_STATUS"
    return "INVESTIGATE_STATUS_EXPORT"


def _observability_plan(status: DispatchStatus, plan: RecoveryPlan) -> RecoveryPlan:
    action = _observability_recovery_action(status, plan)
    disposition = plan.disposition if action == "REBUILD_STATUS" else "OPERATOR_REVIEW_REQUIRED"
    return replace(
        plan,
        disposition=disposition,
        action=action,
        safe_to_apply=plan.safe_to_apply if action == "REBUILD_STATUS" else False,
        public_side_effects=False,
        scheduler_changes=False,
        collection_rerun=False,
        reason="exported status surface is stale or missing",
    )


def _remediation_payload(
    *,
    attempted: bool = False,
    action: str = "NONE",
    outcome: str = "NOT_ATTEMPTED",
    changed: bool = False,
    attempted_at: str | None = None,
    post_status: str | None = None,
    unexpected_changes: Iterable[str] = (),
    status_artifacts_changed: Iterable[str] = (),
    reason: str = "",
) -> dict[str, Any]:
    if outcome not in REMEDIATION_OUTCOMES:
        outcome = "FAILED"
    return {
        "action": action,
        "attempted": attempted,
        "attempted_at": attempted_at,
        "changed": changed,
        "outcome": outcome,
        "post_status": post_status,
        "reason": reason,
        "status_artifacts_changed": sorted(status_artifacts_changed),
        "unexpected_changes": sorted(unexpected_changes),
    }


def _incident_key(dispatch: str, classification: Classification, affected_date: str | None, status: DispatchStatus) -> str:
    task_key = ""
    details = status.details if isinstance(status.details, dict) else {}
    task_statuses = details.get("task_statuses") if isinstance(details.get("task_statuses"), dict) else {}
    if task_statuses:
        task_key = ",".join(sorted(str(key) for key in task_statuses))
    return "|".join([dispatch, classification.value, affected_date or status.date, task_key])


def _incident_id(key: str) -> str:
    return "bfo-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _load_open_incidents(operator_root: Path) -> dict[str, dict[str, Any]]:
    incidents: dict[str, dict[str, Any]] = {}
    incident_root = operator_root / "incidents"
    for path in sorted(incident_root.glob("*.json")):
        payload = _read_json(path)
        if payload and payload.get("state") == IncidentState.OPEN.value:
            incidents[str(payload.get("incident_key") or "")] = payload
    return incidents


def _load_incidents_by_id(operator_root: Path) -> dict[str, dict[str, Any]]:
    incidents: dict[str, dict[str, Any]] = {}
    incident_root = operator_root / "incidents"
    for path in sorted(incident_root.glob("*.json")):
        payload = _read_json(path)
        if payload and payload.get("incident_id"):
            incidents[str(payload["incident_id"])] = payload
    return incidents


def _fingerprint_evidence(values: Iterable[str]) -> str:
    normalized = json.dumps(sorted(dict.fromkeys(str(value) for value in values)), sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _severity(payload: dict[str, Any]) -> int:
    return max(
        STATUS_SEVERITY.get(str(payload.get("status_state") or ""), 0),
        CLASSIFICATION_SEVERITY.get(str(payload.get("classification") or ""), 0),
    )


def _policy_requires_operator(policy: RemediationPolicy, action: str) -> bool:
    if not action or action == "NONE":
        return False
    return policy.mode_for(action) != "automatic"


def _material_change_reasons(
    incident: Incident,
    previous: dict[str, Any] | None,
    policy: RemediationPolicy,
) -> set[str]:
    reasons: set[str] = set()
    remediation = incident.remediation if isinstance(incident.remediation, dict) else {}
    failed_remediation = remediation.get("attempted") is True and (
        remediation.get("outcome") == "FAILED" or bool(remediation.get("unexpected_changes"))
    )
    if failed_remediation:
        reasons.add(NotificationReason.REMEDIATION_FAILED.value)

    if previous is None:
        if incident.state == IncidentState.OPEN.value:
            reasons.add(NotificationReason.NEW_INCIDENT.value)
            if _policy_requires_operator(policy, incident.recovery_action):
                reasons.add(NotificationReason.APPROVAL_REQUIRED.value)
        elif incident.state == IncidentState.RECOVERED.value:
            reasons.add(NotificationReason.MATERIAL_RECOVERY.value)
        return reasons

    previous_state = str(previous.get("state") or "")
    if incident.state == IncidentState.RECOVERED.value and previous_state == IncidentState.OPEN.value:
        reasons.add(NotificationReason.MATERIAL_RECOVERY.value)
        return reasons

    if incident.state != IncidentState.OPEN.value:
        return reasons

    action_changed = str(previous.get("recovery_action") or "") != incident.recovery_action
    recommendation_changed = str(previous.get("recommended_action") or "") != incident.recommended_action
    evidence_changed = _fingerprint_evidence(previous.get("evidence") or []) != _fingerprint_evidence(incident.evidence)
    severity_worsened = _severity(incident.to_payload()) > _severity(previous)

    if severity_worsened or action_changed or recommendation_changed:
        reasons.add(NotificationReason.INCIDENT_WORSENED.value)
    elif evidence_changed and incident.classification != Classification.STALE_OBSERVABILITY.value:
        reasons.add(NotificationReason.INCIDENT_WORSENED.value)

    if (previous is None or action_changed or recommendation_changed) and _policy_requires_operator(policy, incident.recovery_action):
        reasons.add(NotificationReason.APPROVAL_REQUIRED.value)
    return reasons


def _build_notification_event(
    result: "OperatorResult",
    previous_incidents: dict[str, dict[str, Any]],
    policy: RemediationPolicy,
) -> NotificationEvent:
    rows: list[dict[str, Any]] = []
    reasons: set[str] = set()
    dispatches: set[str] = set()
    incident_ids: set[str] = set()
    simultaneous_observability = {
        incident.dispatch: incident
        for incident in result.incidents
        if incident.classification == Classification.STALE_OBSERVABILITY.value
        and incident.state == IncidentState.OPEN.value
        and previous_incidents.get(incident.incident_id) is None
    }
    simultaneous_underlying = {
        incident.dispatch
        for incident in result.incidents
        if incident.classification != Classification.STALE_OBSERVABILITY.value
        and incident.state == IncidentState.OPEN.value
        and previous_incidents.get(incident.incident_id) is None
        and CLASSIFICATION_SEVERITY.get(incident.classification, 0) > CLASSIFICATION_SEVERITY[Classification.STALE_OBSERVABILITY.value]
    }
    for incident in result.incidents:
        incident_reasons = _material_change_reasons(incident, previous_incidents.get(incident.incident_id), policy)
        incident_reasons = {reason for reason in incident_reasons if reason in NOTIFICATION_REASONS}
        if (
            incident.classification == Classification.STALE_OBSERVABILITY.value
            and incident.dispatch in simultaneous_underlying
            and NotificationReason.NEW_INCIDENT.value in incident_reasons
        ):
            incident_reasons.discard(NotificationReason.NEW_INCIDENT.value)
            incident_reasons.discard(NotificationReason.APPROVAL_REQUIRED.value)
        if not incident_reasons:
            continue
        reasons.update(incident_reasons)
        dispatches.add(incident.dispatch)
        incident_ids.add(incident.incident_id)
        row = {
            "dispatch": incident.dispatch,
            "incident_id": incident.incident_id,
            "classification": incident.classification,
            "state": incident.state,
            "status_state": incident.status_state,
            "recovery_action": incident.recovery_action,
            "reasons": sorted(incident_reasons),
        }
        if incident.classification != Classification.STALE_OBSERVABILITY.value and incident.dispatch in simultaneous_observability:
            observed = simultaneous_observability[incident.dispatch]
            row["observability_context"] = {
                "incident_id": observed.incident_id,
                "classification": observed.classification,
                "state": observed.state,
            }
        rows.append(row)
    return NotificationEvent(
        notification_required=bool(rows),
        reasons=sorted(reasons),
        dispatches=sorted(dispatches),
        incident_ids=sorted(incident_ids),
        summary=sorted(rows, key=lambda row: (row["dispatch"], row["incident_id"])),
    )


def _build_incident(
    *,
    dispatch: str,
    classification: Classification,
    status: DispatchStatus,
    plan: RecoveryPlan,
    detected_at: str,
    evidence: Iterable[str],
    recommended_action: str,
    existing: dict[str, Any] | None = None,
    affected_date: str | None = None,
    state: IncidentState = IncidentState.OPEN,
    remediation: dict[str, Any] | None = None,
) -> Incident:
    key = _incident_key(dispatch, classification, affected_date, status)
    created_at = str((existing or {}).get("detected_at") or detected_at)
    return Incident(
        incident_id=str((existing or {}).get("incident_id") or _incident_id(key)),
        incident_key=key,
        dispatch=dispatch,
        detected_at=created_at,
        updated_at=detected_at,
        state=state.value,
        classification=classification.value,
        status_state=status.state,
        recovery_disposition=plan.disposition,
        recovery_action=plan.action,
        evidence=sorted(dict.fromkeys(evidence)),
        recommended_action=recommended_action,
        affected_date=affected_date or status.date,
        remediation=remediation or {},
    )


def _recovered_incident(existing: dict[str, Any], detected_at: str, evidence: Iterable[str]) -> Incident:
    return Incident(
        incident_id=str(existing.get("incident_id")),
        incident_key=str(existing.get("incident_key")),
        dispatch=str(existing.get("dispatch")),
        detected_at=str(existing.get("detected_at")),
        updated_at=detected_at,
        state=IncidentState.RECOVERED.value,
        classification=str(existing.get("classification")),
        status_state="RECOVERED",
        recovery_disposition="NO_ACTION",
        recovery_action="NONE",
        evidence=sorted(dict.fromkeys([*list(existing.get("evidence") or []), *evidence])),
        recommended_action="NO_ACTION",
        affected_date=str(existing.get("affected_date") or ""),
        remediation=dict(existing.get("remediation") or {}),
    )


def _is_status_rebuild_classification(classification: Classification) -> bool:
    return classification in {Classification.STALE_OBSERVABILITY, Classification.STATUS_EXPORT_PROBLEM}


def _operator_checkout_healthy(repo_root: Path, operator_root: Path) -> bool:
    # v0.2 keeps this deliberately narrow and deterministic: the operator may
    # have ledger changes, but it must not already have unrelated local changes.
    return repo_root.exists() and operator_root.exists()


def _can_auto_apply_rebuild(
    *,
    classification: Classification,
    status: DispatchStatus,
    plan: RecoveryPlan,
    policy: RemediationPolicy,
    repo_root: Path,
    operator_root: Path,
    open_incidents: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    if not _is_status_rebuild_classification(classification):
        return False, "classification is not compatible with status rebuild"
    if plan.disposition != "PLAN_AVAILABLE" or plan.action not in AUTOMATIC_REMEDIATION_ACTIONS:
        return False, "planner did not return PLAN_AVAILABLE REBUILD_STATUS"
    if policy.mode_for("REBUILD_STATUS") != "automatic":
        return False, f"policy mode is {policy.mode_for('REBUILD_STATUS')}"
    if plan.public_side_effects or plan.scheduler_changes or plan.collection_rerun:
        return False, "planner reported forbidden side effects"
    if not status.evidence:
        return False, "dispatch runner evidence is not readable"
    if any(
        row.get("dispatch") == status.dispatch
        and row.get("classification") not in {Classification.STALE_OBSERVABILITY.value, Classification.STATUS_EXPORT_PROBLEM.value}
        for row in open_incidents.values()
    ):
        return False, "conflicting open underlying incident exists"
    if not _operator_checkout_healthy(repo_root, operator_root):
        return False, "operator checkout is not healthy"
    return True, "policy permits automatic REBUILD_STATUS"


def _apply_rebuild_status(
    *,
    status: DispatchStatus,
    plan: RecoveryPlan,
    dispatch_root: Path,
    attempted_at: str,
) -> tuple[ApplyResult, DispatchStatus, RecoveryPlan, dict[str, Any]]:
    result = apply_recovery_plan(status.dispatch, status.date, root=dispatch_root, confirm="REBUILD_STATUS")
    post_status = build_status(status.dispatch, status.date, root=dispatch_root)
    post_plan = build_recovery_plan_from_status(post_status)
    remediation = _remediation_payload(
        attempted=True,
        action="REBUILD_STATUS",
        outcome=result.outcome,
        changed=result.changed,
        attempted_at=attempted_at,
        post_status=post_status.state,
        unexpected_changes=result.unexpected_changes,
        status_artifacts_changed=result.status_artifacts_changed,
        reason="automatic REBUILD_STATUS via dispatch_ops.apply_recovery_plan",
    )
    return result, post_status, post_plan, remediation


def check_operator(
    *,
    repo_root: Path = ROOT,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    config: OperatorConfig | None = None,
    now: datetime | None = None,
    write_ledger: bool = True,
    allow_automatic_remediation: bool | None = None,
) -> OperatorResult:
    config = config or load_config(operator_root / "config.json")
    policy = load_remediation_policy(operator_root / "remediation-policy.yaml")
    if allow_automatic_remediation is None:
        allow_automatic_remediation = write_ledger
    if write_ledger:
        with _operator_lock(operator_root):
            result = check_operator(
                repo_root=repo_root,
                operator_root=operator_root,
                config=config,
                now=now,
                write_ledger=False,
                allow_automatic_remediation=allow_automatic_remediation,
            )
            _write_ledger(operator_root, result)
            return result
    now = now or _utc_now()
    checked_at = _format_time(now)
    open_incidents = _load_open_incidents(operator_root)
    previous_incidents = _load_incidents_by_id(operator_root)
    current_keys: set[str] = set()
    dispatch_results: list[DispatchResult] = []
    incidents: list[Incident] = []

    for dispatch in DISPATCH_ORDER:
        dispatch_config = config.dispatches[dispatch]
        if not dispatch_config.enabled:
            continue
        repo_exported = _latest_exported_status(
            repo_root,
            dispatch,
            now,
            config.status_freshness_threshold_minutes,
        )
        runner_exported = _latest_exported_status(
            dispatch_config.runner_root,
            dispatch,
            now,
            config.status_freshness_threshold_minutes,
        )
        exported = _select_exported_status(repo_exported, runner_exported)
        status_date = exported.observed_date or _latest_runner_date(dispatch_config.runner_root, dispatch) or now.date().isoformat()
        if not exported.is_fresh:
            runner_date = _latest_runner_date(dispatch_config.runner_root, dispatch) or status_date
            status = build_status(dispatch, runner_date, root=dispatch_config.runner_root)
            plan = build_recovery_plan_from_status(status)
            classification = Classification.STALE_OBSERVABILITY
            observability_plan = _observability_plan(status, plan)
            recommended = _recommended_action(classification, status, observability_plan)
            evidence = [*exported.evidence, *observability_plan.evidence]
            underlying_classification = _classify_status(status)
            remediation = _remediation_payload(action=observability_plan.action or "NONE", reason="recommendation only")
            notification_state = "RECOMMENDATION_ONLY"
            auto_allowed, auto_reason = _can_auto_apply_rebuild(
                classification=classification,
                status=status,
                plan=observability_plan,
                policy=policy,
                repo_root=repo_root,
                operator_root=operator_root,
                open_incidents=open_incidents,
            )
            if auto_allowed and allow_automatic_remediation:
                apply_result, post_status, post_plan, remediation = _apply_rebuild_status(
                    status=status,
                    plan=plan,
                    dispatch_root=dispatch_config.runner_root,
                    attempted_at=checked_at,
                )
                evidence = sorted(dict.fromkeys([*evidence, *apply_result.evidence, *post_status.evidence]))
                post_classification = _classify_status(post_status)
                stale_recovered = (
                    apply_result.outcome in {"REBUILT", "ALREADY_CURRENT", "NO_ACTION"}
                    and post_plan.action != "REBUILD_STATUS"
                    and not apply_result.unexpected_changes
                )
                incident_state = IncidentState.RECOVERED if stale_recovered else IncidentState.OPEN
                notification_state = "REMEDIATION_FAILED"
                if stale_recovered and post_classification is None:
                    notification_state = "AUTO_RECOVERED_FULLY"
                elif stale_recovered:
                    notification_state = "AUTO_RECOVERED"
                recommended = _recommended_action(post_classification, post_status, post_plan) if post_classification else "NO_ACTION"
                incident = _build_incident(
                    dispatch=dispatch,
                    classification=classification,
                    status=status,
                    plan=observability_plan,
                    detected_at=checked_at,
                    evidence=evidence,
                    recommended_action="NO_ACTION" if stale_recovered else recommended,
                    existing=open_incidents.get(_incident_key(dispatch, classification, runner_date, status)),
                    affected_date=runner_date,
                    state=incident_state,
                    remediation=remediation,
                )
                incidents.append(incident)
                current_keys.add(incident.incident_key)
                if stale_recovered and post_classification is not None:
                    underlying_key = _incident_key(dispatch, post_classification, post_status.date, post_status)
                    underlying = _build_incident(
                        dispatch=dispatch,
                        classification=post_classification,
                        status=post_status,
                        plan=post_plan,
                        detected_at=checked_at,
                        evidence=[*post_status.evidence, *post_plan.evidence],
                        recommended_action=recommended,
                        existing=open_incidents.get(underlying_key),
                        affected_date=post_status.date,
                    )
                    current_keys.add(underlying.incident_key)
                    incidents.append(underlying)
                dispatch_results.append(
                    DispatchResult(
                        dispatch=dispatch,
                        state=notification_state,
                        classification=classification.value,
                        recommended_action=recommended,
                        status_state=post_status.state,
                        observed_date=runner_date,
                        recovery_disposition=post_plan.disposition,
                        recovery_action=post_plan.action,
                        incident_id=incident.incident_id,
                        evidence=evidence,
                        exported_status=asdict(exported),
                        notification_state=notification_state,
                        remediation=remediation,
                    )
                )
                continue
            if auto_allowed and not allow_automatic_remediation:
                remediation = _remediation_payload(action=observability_plan.action, reason="automatic remediation disabled for this run")
            elif observability_plan.action == "REBUILD_STATUS":
                remediation = _remediation_payload(action=observability_plan.action, reason=auto_reason)
            incident = _build_incident(
                dispatch=dispatch,
                classification=classification,
                status=status,
                plan=observability_plan,
                detected_at=checked_at,
                evidence=evidence,
                recommended_action=recommended,
                existing=open_incidents.get(_incident_key(dispatch, classification, runner_date, status)),
                affected_date=runner_date,
                remediation=remediation,
            )
            current_keys.add(incident.incident_key)
            incidents.append(incident)
            if underlying_classification is not None:
                underlying_key = _incident_key(dispatch, underlying_classification, status.date, status)
                underlying_recommended = _recommended_action(underlying_classification, status, plan)
                underlying = _build_incident(
                    dispatch=dispatch,
                    classification=underlying_classification,
                    status=status,
                    plan=plan,
                    detected_at=checked_at,
                    evidence=[*status.evidence, *plan.evidence],
                    recommended_action=underlying_recommended,
                    existing=open_incidents.get(underlying_key),
                    affected_date=status.date,
                )
                current_keys.add(underlying.incident_key)
                incidents.append(underlying)
            dispatch_results.append(
                DispatchResult(
                    dispatch=dispatch,
                    state="STALE_OBSERVABILITY",
                    classification=classification.value,
                    recommended_action=recommended,
                    status_state=status.state,
                    observed_date=runner_date,
                    recovery_disposition=observability_plan.disposition,
                    recovery_action=observability_plan.action,
                    incident_id=incident.incident_id,
                    evidence=incident.evidence,
                    exported_status=asdict(exported),
                    notification_state=notification_state,
                    remediation=remediation,
                )
            )
            continue

        status = build_status(dispatch, status_date, root=dispatch_config.runner_root)
        plan = build_recovery_plan_from_status(status)
        classification = _classify_status(status)
        if classification is None:
            dispatch_results.append(
                DispatchResult(
                    dispatch=dispatch,
                    state="NO_ACTION",
                    classification=None,
                    recommended_action="NO_ACTION",
                    status_state=status.state,
                    observed_date=status.date,
                    recovery_disposition=plan.disposition,
                    recovery_action=plan.action,
                    incident_id=None,
                    evidence=sorted(status.evidence),
                    exported_status=asdict(exported),
                )
            )
            continue
        recommended = _recommended_action(classification, status, plan)
        key = _incident_key(dispatch, classification, status.date, status)
        incident = _build_incident(
            dispatch=dispatch,
            classification=classification,
            status=status,
            plan=plan,
            detected_at=checked_at,
            evidence=[*status.evidence, *plan.evidence],
            recommended_action=recommended,
            existing=open_incidents.get(key),
            affected_date=status.date,
        )
        current_keys.add(key)
        incidents.append(incident)
        dispatch_results.append(
            DispatchResult(
                dispatch=dispatch,
                state=status.state,
                classification=classification.value,
                recommended_action=recommended,
                status_state=status.state,
                observed_date=status.date,
                recovery_disposition=plan.disposition,
                recovery_action=plan.action,
                incident_id=incident.incident_id,
                evidence=incident.evidence,
                exported_status=asdict(exported),
            )
        )

    for key, existing in sorted(open_incidents.items()):
        if key not in current_keys:
            recovered = _recovered_incident(existing, checked_at, ["condition no longer present"])
            incidents.append(recovered)

    result = OperatorResult(checked_at=checked_at, dispatches=dispatch_results, incidents=incidents)
    result = OperatorResult(
        checked_at=result.checked_at,
        dispatches=result.dispatches,
        incidents=result.incidents,
        notification=_build_notification_event(result, previous_incidents, policy),
        production_state_mutated=any(
            bool((incident.remediation or {}).get("changed"))
            for incident in result.incidents
        ),
    )
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _engineering_root(operator_root: Path) -> Path:
    return operator_root / "engineering"


def _work_id(incident_id: str, repair_generation: int = 1) -> str:
    digest = hashlib.sha256(f"{incident_id}|{repair_generation}".encode("utf-8")).hexdigest()[:16]
    return f"bfoe-{digest}"


def _engineering_branch(dispatch: str, incident_id: str) -> str:
    return f"operator/{dispatch}/{incident_id}"


def _canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _same_canonical_path(left: Path, right: Path) -> bool:
    return str(_canonical_path(left)).lower() == str(_canonical_path(right)).lower()


def _canonical_relative_to(child: Path, parent: Path) -> bool:
    canonical_child = _canonical_path(child)
    canonical_parent = _canonical_path(parent)
    try:
        canonical_child.relative_to(canonical_parent)
    except ValueError:
        return False
    return True


def _engineering_worktree_violation(path: Path) -> str | None:
    approved_root = _canonical_path(APPROVED_ENGINEERING_WORKTREE_ROOT)
    requested = _canonical_path(path)
    if str(requested).lower() == str(approved_root).lower():
        return f"engineering worktree must be below approved root, not equal to it: {approved_root}"
    if not _canonical_relative_to(requested, approved_root):
        return f"engineering worktree must be inside approved root {approved_root}: {requested}"
    return None


def _allowed_patch_scope(dispatch: str, classification: str, recovery_action: str) -> list[str]:
    if dispatch == "care-line":
        return sorted(
            {
                "scripts/care_line_*.py",
                "src/bluefern_dispatches/care_line_*.py",
                "tests/test_care_line*.py",
                "tests/test_blue_fern_operator*.py",
            }
        )
    if dispatch == "food-line":
        return sorted(
            {
                "scripts/food_line_*.py",
                "src/bluefern_dispatches/food_line_*.py",
                "tests/test_food_line*.py",
                "tests/test_blue_fern_operator*.py",
            }
        )
    if dispatch == "ice":
        return sorted(
            {
                "scripts/*ice*.py",
                "src/bluefern_dispatches/*ice*.py",
                "tests/test_ice*.py",
                "tests/test_blue_fern_operator*.py",
            }
        )
    return ["tests/test_blue_fern_operator*.py"]


def _tests_required(dispatch: str, classification: str) -> list[str]:
    tests = [
        "python -m py_compile scripts/blue_fern_operator.py scripts/dispatch_ops.py",
        "python -m pytest tests/test_blue_fern_operator.py tests/test_blue_fern_operator_v03.py tests/test_blue_fern_operator_v04.py -q",
        "python -m pytest tests/test_dispatch_ops_status.py -q",
        "$env:PYTHONPATH='src'; python scripts/doctor.py",
        "python scripts/preflight_repo_state.py --source-repo .",
    ]
    if dispatch == "care-line":
        tests.insert(2, "python -m pytest tests/test_care_line*.py -q")
    elif dispatch == "food-line":
        tests.insert(2, "python -m pytest tests/test_food_line*.py -q")
    elif dispatch == "ice":
        tests.insert(2, "python -m pytest tests/test_ice*.py -q")
    return tests


def _has_existing_engineering_work(operator_root: Path, incident_id: str) -> dict[str, Any] | None:
    root = _engineering_root(operator_root)
    for path in [*sorted((root / "active").glob("*/work-item.json")), *sorted((root / "history").glob("*/work-item.json"))]:
        payload = _read_json(path)
        if payload and payload.get("incident_id") == incident_id and payload.get("state") not in {"CLOSED", "FAILED"}:
            return payload
    return None


def _is_engineering_eligible(incident: Incident, policy: RemediationPolicy) -> tuple[bool, str]:
    if policy.mode_for("ENGINEER_PREPARE_FIX") != "automatic_prepare_pr":
        return False, "engineering preparation policy is not enabled"
    if incident.state != IncidentState.OPEN.value:
        return False, "incident is not open"
    if incident.classification not in ENGINEERING_ELIGIBLE_CLASSES:
        return False, "classification is not eligible for engineering preparation"
    if incident.recovery_action in ENGINEERING_BLOCKED_ACTIONS:
        return False, "recovery action is not an engineering repair target"
    if incident.recovery_disposition == "NO_ACTION":
        return False, "recovery plan is NO_ACTION"
    if incident.classification == Classification.UNKNOWN_OPERATIONAL_STATE.value:
        return False, "unknown state is not eligible"
    if not incident.evidence:
        return False, "incident has insufficient evidence"
    return True, "eligible for isolated engineering preparation"


def _build_engineering_work_item(
    incident: Incident,
    *,
    operator_root: Path,
    worktree_root: Path,
    base_sha: str | None,
    repair_generation: int = 1,
) -> EngineeringWorkItem:
    work_id = _work_id(incident.incident_id, repair_generation)
    worktree = worktree_root / work_id
    blocked_reason = _engineering_worktree_violation(worktree)
    return EngineeringWorkItem(
        work_id=work_id,
        incident_id=incident.incident_id,
        dispatch=incident.dispatch,
        classification=incident.classification,
        state=EngineeringState.BLOCKED.value if blocked_reason else EngineeringState.DETECTED.value,
        branch=_engineering_branch(incident.dispatch, incident.incident_id),
        worktree=str(worktree),
        evidence=sorted(incident.evidence),
        allowed_paths=_allowed_patch_scope(incident.dispatch, incident.classification, incident.recovery_action),
        tests_required=_tests_required(incident.dispatch, incident.classification),
        base_sha=base_sha,
        repair_generation=repair_generation,
        blocked_reason=blocked_reason,
    )


def _write_engineering_work_item(operator_root: Path, item: EngineeringWorkItem, diagnosis: dict[str, Any]) -> None:
    active_root = _engineering_root(operator_root) / "active" / item.work_id
    _write_json(active_root / "work-item.json", item.to_payload())
    _write_json(active_root / "diagnosis.json", diagnosis)


def _engineering_work_root(operator_root: Path, work_id: str) -> Path:
    return _engineering_root(operator_root) / "active" / work_id


def _engineering_work_item_path(operator_root: Path, work_id: str) -> Path:
    return _engineering_work_root(operator_root, work_id) / "work-item.json"


def _save_engineering_work_item(operator_root: Path, item: EngineeringWorkItem) -> EngineeringWorkItem:
    _write_json(_engineering_work_item_path(operator_root, item.work_id), item.to_payload())
    return item


def _load_engineering_work_item(operator_root: Path, work_id: str) -> EngineeringWorkItem:
    path = _engineering_work_item_path(operator_root, work_id)
    payload = _read_json(path)
    if not payload:
        raise FileNotFoundError(path)
    return EngineeringWorkItem(**{key: payload[key] for key in EngineeringWorkItem.__dataclass_fields__ if key in payload})


def prepare_engineering_work(
    result: OperatorResult,
    *,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    worktree_root: Path = Path(r"C:\BlueFernRunner\OperatorWorktrees"),
    policy: RemediationPolicy | None = None,
    base_sha: str | None = None,
) -> list[EngineeringWorkItem]:
    policy = policy or load_remediation_policy(operator_root / "remediation-policy.yaml")
    items: list[EngineeringWorkItem] = []
    for incident in result.incidents:
        eligible, reason = _is_engineering_eligible(incident, policy)
        if not eligible:
            continue
        existing = _has_existing_engineering_work(operator_root, incident.incident_id)
        if existing:
            items.append(EngineeringWorkItem(**{key: existing[key] for key in EngineeringWorkItem.__dataclass_fields__ if key in existing}))
            continue
        item = _build_engineering_work_item(
            incident,
            operator_root=operator_root,
            worktree_root=worktree_root,
            base_sha=base_sha or _git_head(ROOT),
        )
        diagnosis = {
            "schema_version": "blue_fern_operator_diagnosis_v1",
            "work_id": item.work_id,
            "incident_id": incident.incident_id,
            "dispatch": incident.dispatch,
            "classification": incident.classification,
            "status_state": incident.status_state,
            "recovery_action": incident.recovery_action,
            "eligibility_reason": reason,
            "evidence": item.evidence,
            "allowed_paths": item.allowed_paths,
            "forbidden_actions": [
                "publish",
                "sync_pages",
                "collection_replay",
                "scheduler_mutation",
                "production_runner_patch",
                "merge_pr",
            ],
        }
        item = replace(
            item,
            diagnosis_path=str(_engineering_work_root(operator_root, item.work_id) / "diagnosis.json"),
        )
        _write_engineering_work_item(operator_root, item, diagnosis)
        items.append(item)
    return items


def update_engineering_validation(
    operator_root: Path,
    work_id: str,
    *,
    validation_passed: bool,
    repair_attempts: int,
    pr_number: int | None = None,
    head_sha: str | None = None,
) -> EngineeringWorkItem:
    path = _engineering_root(operator_root) / "active" / work_id / "work-item.json"
    payload = _read_json(path)
    if not payload:
        raise FileNotFoundError(path)
    state = EngineeringState.PR_READY.value if validation_passed else EngineeringState.BLOCKED.value
    if validation_passed and pr_number is not None:
        state = EngineeringState.PR_OPEN.value
    if not validation_passed and repair_attempts < 2:
        state = EngineeringState.VALIDATING.value
    item = EngineeringWorkItem(
        **{
            **payload,
            "state": state,
            "repair_attempts": repair_attempts,
            "pr_number": pr_number,
            "pr_state": "OPEN" if pr_number else payload.get("pr_state"),
            "head_sha": head_sha or payload.get("head_sha"),
            "merge_allowed": False,
        }
    )
    _write_json(path, item.to_payload())
    return item


def _run_command(args: list[str], *, cwd: Path) -> EngineeringCommandResult:
    completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    return EngineeringCommandResult(completed.returncode, completed.stdout, completed.stderr)


def _block_engineering_work(
    operator_root: Path,
    item: EngineeringWorkItem,
    reason: str,
    *,
    unexpected_paths: list[str] | None = None,
    validation_results: list[dict[str, Any]] | None = None,
    failure_classification: str | None = None,
    diagnostic_artifact: str | None = None,
) -> EngineeringWorkItem:
    return _save_engineering_work_item(
        operator_root,
        replace(
            item,
            state=EngineeringState.BLOCKED.value,
            blocked_reason=reason,
            failure_classification=failure_classification or item.failure_classification,
            diagnostic_artifact=diagnostic_artifact or item.diagnostic_artifact,
            unexpected_paths=sorted(unexpected_paths or item.unexpected_paths),
            validation_results=validation_results or item.validation_results,
            merge_allowed=False,
        ),
    )


def _first_output_line(result: EngineeringCommandResult) -> str:
    return result.stdout.strip().splitlines()[0] if result.ok and result.stdout.strip() else ""


def _resolve_git_common_dir(value: str, *, cwd: Path) -> Path:
    common = Path(value)
    if not common.is_absolute():
        common = cwd / common
    return _canonical_path(common)


def _verify_existing_engineering_worktree(
    item: EngineeringWorkItem,
    *,
    repo_root: Path,
    expected_base_sha: str,
    runner: Any = _run_command,
) -> tuple[bool, str]:
    worktree = Path(item.worktree)
    top = _first_output_line(runner(["git", "rev-parse", "--show-toplevel"], cwd=worktree))
    if not top or not _same_canonical_path(Path(top), worktree):
        return False, "existing worktree path identity does not match work item"

    branch = _first_output_line(runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree))
    if branch != item.branch:
        return False, f"existing worktree branch mismatch: {branch}"

    source_common = _first_output_line(runner(["git", "rev-parse", "--git-common-dir"], cwd=repo_root))
    worktree_common = _first_output_line(runner(["git", "rev-parse", "--git-common-dir"], cwd=worktree))
    if not source_common or not worktree_common:
        return False, "could not verify existing worktree repository identity"
    source_common_path = _resolve_git_common_dir(source_common, cwd=repo_root)
    worktree_common_path = _resolve_git_common_dir(worktree_common, cwd=worktree)
    if str(source_common_path).lower() != str(worktree_common_path).lower():
        return False, "existing worktree belongs to a different source repository"

    ancestry = runner(["git", "merge-base", "--is-ancestor", expected_base_sha, "HEAD"], cwd=worktree)
    if not ancestry.ok:
        return False, "existing worktree HEAD is not compatible with recorded base"

    return True, "existing worktree identity verified"


def _create_or_reuse_engineering_worktree(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    repo_root: Path = ROOT,
    base_ref: str = "origin/add/pages-repo-default",
    runner: Any = _run_command,
) -> EngineeringWorkItem:
    worktree = Path(item.worktree)
    violation = _engineering_worktree_violation(worktree)
    if violation:
        return _block_engineering_work(operator_root, item, violation)

    fetch = runner(["git", "fetch", "origin"], cwd=repo_root)
    if not fetch.ok:
        return _block_engineering_work(operator_root, item, "git fetch failed")
    base = runner(["git", "rev-parse", base_ref], cwd=repo_root)
    if not base.ok or not base.stdout.strip():
        return _block_engineering_work(operator_root, item, f"could not resolve {base_ref}")
    base_sha = base.stdout.strip().splitlines()[0]

    if worktree.exists():
        if not (worktree / ".git").exists():
            contents = list(worktree.iterdir()) if worktree.is_dir() else [worktree]
            if contents:
                return _block_engineering_work(operator_root, item, f"worktree destination has unrelated content: {worktree}")
        verified, reason = _verify_existing_engineering_worktree(
            item,
            repo_root=repo_root,
            expected_base_sha=item.base_sha or base_sha,
            runner=runner,
        )
        if not verified:
            return _block_engineering_work(operator_root, item, reason)
        item = replace(
            item,
            state=EngineeringState.WORKTREE_CREATED.value,
            base_sha=base_sha,
            merge_allowed=False,
        )
        return _save_engineering_work_item(operator_root, item)

    worktree.parent.mkdir(parents=True, exist_ok=True)
    branch_exists = runner(["git", "show-ref", "--verify", f"refs/heads/{item.branch}"], cwd=repo_root)
    if branch_exists.ok:
        add = runner(["git", "worktree", "add", str(worktree), item.branch], cwd=repo_root)
    else:
        add = runner(["git", "worktree", "add", str(worktree), "-b", item.branch, base_ref], cwd=repo_root)
    if not add.ok:
        return _block_engineering_work(operator_root, item, "git worktree add failed")
    item = replace(
        item,
        state=EngineeringState.WORKTREE_CREATED.value,
        base_sha=base_sha,
        merge_allowed=False,
    )
    return _save_engineering_work_item(operator_root, item)


def _engineering_prompt(item: EngineeringWorkItem, diagnosis_path: Path) -> str:
    diagnosis_text = diagnosis_path.read_text(encoding="utf-8") if diagnosis_path.is_file() else "{}"
    return "\n".join(
        [
            "Blue Fern Operator Engineer Mode repair request.",
            f"Work item: {item.work_id}",
            f"Incident: {item.incident_id}",
            f"Dispatch: {item.dispatch}",
            f"Classification: {item.classification}",
            f"Worktree: {item.worktree}",
            f"Diagnosis packet: {diagnosis_path}",
            "",
            "Allowed patch paths:",
            *[f"- {pattern}" for pattern in item.allowed_paths],
            "",
            "Forbidden actions:",
            "- modify production runner checkouts",
            "- publish or sync Pages",
            "- replay collection or generate editions",
            "- modify Task Scheduler",
            "- modify editorial or review state",
            "- merge PRs or mark this approved",
            "",
            "Required validation commands:",
            *[f"- {command}" for command in item.tests_required],
            "",
            "Use production evidence as read-only input. Stop and report BLOCKED if evidence is insufficient.",
            "Diagnosis packet content:",
            diagnosis_text,
        ]
    )


def _codex_supported_exec_flags(help_text: str) -> dict[str, bool]:
    return {
        "sandbox": "--sandbox" in help_text,
        "cd": "--cd" in help_text,
        "ignore_user_config": "--ignore-user-config" in help_text,
        "ask_for_approval": "--ask-for-approval" in help_text,
    }


def _codex_effective_exec_command_shape(flags: dict[str, bool]) -> list[str]:
    shape = ["<codex>", "exec", "--sandbox", "workspace-write"]
    if flags.get("ask_for_approval"):
        shape.extend(["--ask-for-approval", "never"])
    shape.extend(["--cd", "<worktree>"])
    if flags.get("ignore_user_config"):
        shape.append("--ignore-user-config")
    shape.append("<prompt>")
    return shape


def _codex_sandbox_invocation(executable: str, worktree: Path, prompt: str, flags: dict[str, bool]) -> list[str]:
    command = [executable, "exec", "--sandbox", "workspace-write"]
    if flags.get("ask_for_approval"):
        command.extend(["--ask-for-approval", "never"])
    command.extend(["--cd", str(worktree)])
    if flags.get("ignore_user_config"):
        command.append("--ignore-user-config")
    command.append(prompt)
    return command


def _redact_diagnostic_text(text: str) -> str:
    text = re.sub(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[REDACTED]", text)
    return text


def _tail_text(text: str, limit: int = 8192) -> str:
    return _redact_diagnostic_text(text[-limit:])


def _classify_codex_failure(result: EngineeringCommandResult) -> str:
    evidence = f"{result.stdout}\n{result.stderr}".lower()
    if result.exit_code in {124, 137} or "timed out" in evidence or "timeout" in evidence:
        return "CODEX_TIMEOUT"
    if any(token in evidence for token in ("not logged in", "login required", "authentication", "unauthorized", "api key", "auth")):
        return "CODEX_AUTH_FAILURE"
    if any(token in evidence for token in ("unknown option", "unexpected argument", "unrecognized option", "invalid argument", "unsupported option")):
        return "CODEX_CLI_ARGUMENT_ERROR"
    if "sandbox" in evidence or "workspace-write" in evidence or "permission denied" in evidence:
        return "CODEX_SANDBOX_FAILURE"
    if any(token in evidence for token in ("network", "dns", "connection refused", "connection reset", "could not resolve", "tls")):
        return "CODEX_NETWORK_FAILURE"
    if "model" in evidence:
        return "CODEX_MODEL_FAILURE"
    if result.stdout.strip() or result.stderr.strip():
        return "CODEX_PROCESS_FAILURE"
    return "CODEX_UNKNOWN_FAILURE"


def _codex_attempt_path(operator_root: Path, work_id: str, attempt: int) -> Path:
    return _engineering_work_root(operator_root, work_id) / f"codex-attempt-{attempt}.json"


def _next_codex_attempt_number(operator_root: Path, work_id: str) -> int:
    attempts: list[int] = []
    for path in _engineering_work_root(operator_root, work_id).glob("codex-attempt-*.json"):
        suffix = path.stem.removeprefix("codex-attempt-")
        if suffix.isdigit():
            attempts.append(int(suffix))
    return (max(attempts) + 1) if attempts else 1


def _write_codex_attempt_artifact(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    attempt: int,
    started_at: str,
    completed_at: str,
    executable: str,
    cwd: Path,
    result: EngineeringCommandResult,
    failure_classification: str | None = None,
) -> Path:
    artifact = {
        "schema_version": CODEX_ATTEMPT_SCHEMA_VERSION,
        "work_id": item.work_id,
        "incident_id": item.incident_id,
        "attempt": attempt,
        "started_at": _format_time(started_at) if isinstance(started_at, datetime) else started_at,
        "completed_at": _format_time(completed_at) if isinstance(completed_at, datetime) else completed_at,
        "executable": executable,
        "cwd": str(cwd),
        "sandbox": "workspace-write",
        "approval_policy": "never",
        "ignore_user_config": True,
        "exit_code": result.exit_code,
        "stdout_tail": _tail_text(result.stdout),
        "stderr_tail": _tail_text(result.stderr),
        "failure_classification": failure_classification,
    }
    path = _codex_attempt_path(operator_root, item.work_id, attempt)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite Codex attempt artifact: {path}")
    _write_json(path, artifact)
    return path


def _codex_readiness_probe(executable: str, *, runner: Any = _run_command, cwd: Path) -> dict[str, Any]:
    version = runner([executable, "--version"], cwd=cwd)
    help_result = runner([executable, "exec", "--help"], cwd=cwd)
    login = runner([executable, "login", "status"], cwd=cwd)
    supported_flags = _codex_supported_exec_flags(help_result.stdout if help_result.ok else "")
    supports = (
        help_result.ok
        and supported_flags["sandbox"]
        and "workspace-write" in help_result.stdout
        and supported_flags["cd"]
    )
    result = {
        "executable": executable,
        "version": {
            "exit_code": version.exit_code,
            "stdout_tail": _tail_text(version.stdout),
            "stderr_tail": _tail_text(version.stderr),
        },
        "exec_help": {
            "exit_code": help_result.exit_code,
            "stdout_tail": _tail_text(help_result.stdout),
            "stderr_tail": _tail_text(help_result.stderr),
            "supports_workspace_write": supports,
        },
        "supported_exec_flags": supported_flags,
        "effective_exec_command_shape": _codex_effective_exec_command_shape(supported_flags) if supports else [],
        "login_status": {
            "exit_code": login.exit_code,
            "stdout_tail": _tail_text(login.stdout),
            "stderr_tail": _tail_text(login.stderr),
        },
        "ok": version.ok and help_result.ok and supports and login.ok,
    }
    if not version.ok:
        result["failure_classification"] = _classify_codex_failure(version)
        result["failure_result"] = version
    elif not help_result.ok or not supports:
        result["failure_classification"] = _classify_codex_failure(help_result)
        if supports is False and help_result.ok:
            result["failure_classification"] = "CODEX_CLI_ARGUMENT_ERROR"
        result["failure_result"] = help_result
    elif not login.ok:
        result["failure_classification"] = "CODEX_AUTH_FAILURE"
        result["failure_result"] = login
    else:
        result["failure_classification"] = None
        result["failure_result"] = EngineeringCommandResult(0, "", "")
    return result


def _codex_cli_supports_workspace_sandbox(executable: str, *, runner: Any = _run_command, cwd: Path) -> bool:
    result = runner([executable, "exec", "--help"], cwd=cwd)
    if not result.ok:
        return False
    flags = _codex_supported_exec_flags(result.stdout)
    return flags["sandbox"] and "workspace-write" in result.stdout and flags["cd"]


def _bounded_root_cause(text: str) -> str:
    for line in text.splitlines():
        normalized = line.strip()
        if normalized.lower().startswith("root cause:"):
            value = normalized.split(":", 1)[1].strip()
            if value:
                return value[:300]
    return "INSUFFICIENT_EVIDENCE"


def _invoke_codex_for_engineering(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    runner: Any = _run_command,
    codex_runner: Any | None = None,
    codex_executable: str = "codex",
) -> EngineeringWorkItem:
    diagnosis_path = Path(item.diagnosis_path or (_engineering_work_root(operator_root, item.work_id) / "diagnosis.json"))
    prompt = _engineering_prompt(item, diagnosis_path)
    prompt_path = _engineering_work_root(operator_root, item.work_id) / "codex-prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    item = _save_engineering_work_item(
        operator_root,
        replace(item, state=EngineeringState.DIAGNOSING.value, codex_prompt_path=str(prompt_path), merge_allowed=False),
    )
    codex_attempt = _next_codex_attempt_number(operator_root, item.work_id)
    if codex_runner is not None:
        started_at = _utc_now()
        result = codex_runner(prompt=prompt, cwd=Path(item.worktree), item=item)
        completed_at = _utc_now()
        classification = None if result.ok else _classify_codex_failure(result)
        artifact_path = _write_codex_attempt_artifact(
            operator_root,
            item,
            attempt=codex_attempt,
            started_at=started_at,
            completed_at=completed_at,
            executable="codex_runner",
            cwd=Path(item.worktree),
            result=result,
            failure_classification=classification,
        )
    else:
        executable = shutil.which(codex_executable)
        if not executable:
            classification = "CODEX_CLI_ARGUMENT_ERROR"
            return _block_engineering_work(
                operator_root,
                item,
                f"{codex_executable} executable is unavailable: {classification}",
                failure_classification=classification,
            )
        probe = _codex_readiness_probe(executable, runner=runner, cwd=Path(item.worktree))
        if not probe["ok"]:
            classification = str(probe["failure_classification"] or "CODEX_UNKNOWN_FAILURE")
            result = probe["failure_result"]
            artifact_path = _write_codex_attempt_artifact(
                operator_root,
                item,
                attempt=codex_attempt,
                started_at=_utc_now(),
                completed_at=_utc_now(),
                executable=executable,
                cwd=Path(item.worktree),
                result=result,
                failure_classification=classification,
            )
            reason = "codex readiness probe failed"
            if classification == "CODEX_CLI_ARGUMENT_ERROR":
                reason = "codex CLI cannot enforce workspace-write sandbox non-interactively"
            return _block_engineering_work(
                operator_root,
                item,
                f"{reason}: {classification}",
                failure_classification=classification,
                diagnostic_artifact=str(artifact_path),
            )
        started_at = _utc_now()
        result = runner(
            _codex_sandbox_invocation(executable, Path(item.worktree), prompt, probe["supported_exec_flags"]),
            cwd=Path(item.worktree),
        )
        completed_at = _utc_now()
        classification = None if result.ok else _classify_codex_failure(result)
        artifact_path = _write_codex_attempt_artifact(
            operator_root,
            item,
            attempt=codex_attempt,
            started_at=started_at,
            completed_at=completed_at,
            executable=executable,
            cwd=Path(item.worktree),
            result=result,
            failure_classification=classification,
        )
    if not result.ok:
        classification = classification or "CODEX_UNKNOWN_FAILURE"
        return _block_engineering_work(
            operator_root,
            item,
            f"codex execution failed: {classification}",
            failure_classification=classification,
            diagnostic_artifact=str(artifact_path),
        )
    return _save_engineering_work_item(
        operator_root,
        replace(
            item,
            state=EngineeringState.PATCHED.value,
            root_cause=_bounded_root_cause(result.stdout),
            diagnostic_artifact=str(artifact_path),
            failure_classification=None,
            merge_allowed=False,
        ),
    )


def _changed_paths(worktree: Path, *, runner: Any = _run_command) -> list[str]:
    paths: set[str] = set()
    for args in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        result = runner(args, cwd=worktree)
        if result.ok:
            paths.update(line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip())
    return sorted(paths)


def _unexpected_patch_paths(changed_paths: Iterable[str], allowed_paths: Iterable[str]) -> list[str]:
    patterns = [pattern.replace("\\", "/") for pattern in allowed_paths]
    unexpected: list[str] = []
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        if not any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns):
            unexpected.append(normalized)
    return sorted(unexpected)


def _run_engineering_validation(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    runner: Any = _run_command,
) -> tuple[EngineeringWorkItem, bool]:
    worktree = Path(item.worktree)
    results: list[dict[str, Any]] = []
    item = _save_engineering_work_item(operator_root, replace(item, state=EngineeringState.VALIDATING.value, merge_allowed=False))
    for command in item.tests_required:
        result = runner(["powershell.exe", "-NoProfile", "-Command", command], cwd=worktree)
        row = {
            "command": command,
            "exit_code": result.exit_code,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
        results.append(row)
        if not result.ok:
            item = replace(item, repair_attempts=item.repair_attempts + 1, validation_results=results, merge_allowed=False)
            item = _save_engineering_work_item(operator_root, item)
            return item, False
    item = replace(item, validation_results=results, merge_allowed=False)
    return _save_engineering_work_item(operator_root, item), True


def _commit_engineering_patch(
    operator_root: Path,
    item: EngineeringWorkItem,
    changed_paths: list[str],
    *,
    runner: Any = _run_command,
) -> EngineeringWorkItem:
    worktree = Path(item.worktree)
    if not changed_paths:
        return _block_engineering_work(operator_root, item, "no patch was produced")
    add = runner(["git", "add", "--", *changed_paths], cwd=worktree)
    if not add.ok:
        return _block_engineering_work(operator_root, item, "git add failed")
    message = f"Operator: Fix {item.dispatch} {item.classification}"
    commit = runner(["git", "commit", "-m", message], cwd=worktree)
    if not commit.ok:
        return _block_engineering_work(operator_root, item, "git commit failed")
    head = runner(["git", "rev-parse", "HEAD"], cwd=worktree)
    head_sha = head.stdout.strip().splitlines()[0] if head.ok and head.stdout.strip() else item.head_sha
    return _save_engineering_work_item(
        operator_root,
        replace(item, state=EngineeringState.PR_READY.value, head_sha=head_sha, merge_allowed=False),
    )


def _push_engineering_branch(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    runner: Any = _run_command,
) -> EngineeringWorkItem:
    worktree = Path(item.worktree)
    remote = runner(["git", "ls-remote", "--heads", "origin", item.branch], cwd=worktree)
    if remote.ok and remote.stdout.strip() and item.pr_number is None:
        return _block_engineering_work(operator_root, item, f"remote branch already exists without recorded PR: {item.branch}")
    push = runner(["git", "push", "-u", "origin", item.branch], cwd=worktree)
    if not push.ok:
        return _block_engineering_work(operator_root, item, "git push failed")
    return _save_engineering_work_item(operator_root, replace(item, state=EngineeringState.PR_READY.value, merge_allowed=False))


def _repair_pr_title(item: EngineeringWorkItem) -> str:
    return f"Operator: Fix {item.dispatch} {item.classification}"


def _repair_pr_body(item: EngineeringWorkItem) -> str:
    root_cause = item.root_cause or "INSUFFICIENT_EVIDENCE"
    sections = [
        "## Incident",
        f"- Dispatch: {item.dispatch}",
        f"- Incident: {item.incident_id}",
        f"- Classification: {item.classification}",
        "",
        "## Root Cause",
        f"Root cause: {root_cause}",
        "",
        "## Root Cause Evidence",
        *[f"- {entry}" for entry in item.evidence],
        "",
        "## Fix Scope",
        *[f"- {entry}" for entry in item.allowed_paths],
        "",
        "## Validation",
        *[f"- {row.get('command')} => {row.get('exit_code')}" for row in item.validation_results],
        "",
        "## Safety",
        "- production_state_mutated=false",
        "- publication_attempted=false",
        "- pages_synced=false",
        "- scheduler_changed=false",
        "- editorial_policy_changed=false",
        "- collection_replayed=false",
        "- merge_allowed=false",
        "",
        "## Recommended Action",
        "REVIEW_PR",
        "",
        "Merge is approval-required and not automatic.",
    ]
    return "\n".join(sections).rstrip() + "\n"


def _open_engineering_pr(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    runner: Any = _run_command,
    pr_creator: Any | None = None,
    base_branch: str = "add/pages-repo-default",
) -> EngineeringWorkItem:
    if item.pr_number is not None:
        return _save_engineering_work_item(
            operator_root,
            replace(item, state=EngineeringState.PR_OPEN.value, pr_state="OPEN", merge_allowed=False),
        )
    title = _repair_pr_title(item)
    body = _repair_pr_body(item)
    if pr_creator is not None:
        created = pr_creator(title=title, body=body, head=item.branch, base=base_branch, item=item)
        pr_number = int(created["number"])
        pr_url = str(created.get("url") or "")
    else:
        result = runner(
            ["gh", "pr", "create", "--base", base_branch, "--head", item.branch, "--title", title, "--body", body],
            cwd=Path(item.worktree),
        )
        if not result.ok:
            return _block_engineering_work(operator_root, item, "gh pr create failed")
        pr_url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        pr_number = int(pr_url.rstrip("/").split("/")[-1]) if pr_url.rstrip("/").split("/")[-1].isdigit() else 0
    return _save_engineering_work_item(
        operator_root,
        replace(
            item,
            state=EngineeringState.PR_OPEN.value,
            pr_number=pr_number,
            pr_state="OPEN",
            pr_url=pr_url or None,
            merge_allowed=False,
        ),
    )


def build_engineering_approval_notification(item: EngineeringWorkItem) -> NotificationEvent:
    if item.state != EngineeringState.PR_OPEN.value or item.pr_number is None:
        return NotificationEvent(notification_required=False)
    return NotificationEvent(
        notification_required=True,
        reasons=[NotificationReason.APPROVAL_REQUIRED.value],
        dispatches=[item.dispatch],
        incident_ids=[item.incident_id],
        summary=[
            {
                "dispatch": item.dispatch,
                "incident_id": item.incident_id,
                "classification": item.classification,
                "state": item.state,
                "recommended_action": "REVIEW_PR",
                "pr_number": item.pr_number,
                "pr_url": item.pr_url,
                "merge_allowed": False,
            }
        ],
    )


def _latest_codex_attempt_artifact(operator_root: Path, work_id: str) -> dict[str, Any] | None:
    root = _engineering_work_root(operator_root, work_id)
    attempts = sorted(root.glob("codex-attempt-*.json"))
    if not attempts:
        return None
    path = attempts[-1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["path"] = str(path)
    return payload


def diagnose_engineering_work_item(
    work_id: str,
    *,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    codex_executable: str = "codex",
    runner: Any = _run_command,
) -> dict[str, Any]:
    item = _load_engineering_work_item(operator_root, work_id)
    worktree = Path(item.worktree)
    executable = shutil.which(codex_executable)
    if executable:
        probe = _codex_readiness_probe(executable, runner=runner, cwd=worktree if worktree.exists() else operator_root)
        probe.pop("failure_result", None)
    else:
        probe = {
            "executable": None,
            "ok": False,
            "failure_classification": "CODEX_CLI_ARGUMENT_ERROR",
            "reason": f"{codex_executable} executable is unavailable",
        }
    status = runner(["git", "status", "--short", "--branch"], cwd=worktree) if worktree.exists() else EngineeringCommandResult(1, "", "worktree missing")
    branch = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree) if worktree.exists() else EngineeringCommandResult(1, "", "worktree missing")
    return {
        "schema_version": "blue_fern_operator_engineering_diagnosis_v1",
        "work_item": item.to_payload(),
        "codex_readiness": probe,
        "worktree": {
            "path": str(worktree),
            "exists": worktree.exists(),
            "status_exit_code": status.exit_code,
            "status_stdout_tail": _tail_text(status.stdout),
            "status_stderr_tail": _tail_text(status.stderr),
            "branch_exit_code": branch.exit_code,
            "branch": branch.stdout.strip().splitlines()[0] if branch.ok and branch.stdout.strip() else None,
        },
        "latest_codex_attempt": _latest_codex_attempt_artifact(operator_root, work_id),
    }


def _refused_retry_payload(work_id: str, reason: str, item: EngineeringWorkItem | None = None) -> dict[str, Any]:
    return {
        "schema_version": "blue_fern_operator_engineering_retry_v1",
        "work_id": work_id,
        "accepted": False,
        "reason": reason,
        "work_item": item.to_payload() if item else None,
    }


def _codex_retry_related(item: EngineeringWorkItem) -> bool:
    reason = (item.blocked_reason or "").lower()
    classification = item.failure_classification
    if classification and classification.startswith("CODEX_"):
        return True
    if classification is None and reason == "codex execution failed":
        return True
    return "codex" in reason


def _remote_engineering_branch_exists(item: EngineeringWorkItem, *, runner: Any = _run_command) -> bool:
    result = runner(["git", "ls-remote", "--heads", "origin", item.branch], cwd=Path(item.worktree))
    return result.ok and bool(result.stdout.strip())


def _open_engineering_pr_exists(item: EngineeringWorkItem, *, runner: Any = _run_command) -> bool:
    if item.pr_number is not None or item.pr_url:
        return True
    result = runner(["gh", "pr", "list", "--head", item.branch, "--json", "number,state,url"], cwd=Path(item.worktree))
    if not result.ok:
        return True
    return result.ok and result.stdout.strip() not in {"", "[]"}


def _worktree_status_clean(item: EngineeringWorkItem, *, runner: Any = _run_command) -> tuple[bool, str]:
    result = runner(["git", "status", "--short"], cwd=Path(item.worktree))
    if not result.ok:
        return False, "could not inspect worktree status"
    if result.stdout.strip():
        return False, "worktree is not clean"
    return True, "worktree clean"


def _refresh_retry_worktree_base(
    operator_root: Path,
    item: EngineeringWorkItem,
    *,
    repo_root: Path = ROOT,
    base_ref: str = "origin/add/pages-repo-default",
    runner: Any = _run_command,
) -> tuple[EngineeringWorkItem, dict[str, Any] | None, str | None]:
    fetch = runner(["git", "fetch", "origin"], cwd=repo_root)
    if not fetch.ok:
        return item, None, "git fetch failed"
    base_before = runner(["git", "rev-parse", base_ref], cwd=repo_root)
    head_before = runner(["git", "rev-parse", "HEAD"], cwd=Path(item.worktree))
    if not base_before.ok or not base_before.stdout.strip() or not head_before.ok or not head_before.stdout.strip():
        return item, None, "could not resolve retry base/head"
    merge = runner(["git", "merge", "--ff-only", base_ref], cwd=Path(item.worktree))
    if not merge.ok:
        return item, None, "worktree fast-forward failed"
    base_after = runner(["git", "rev-parse", base_ref], cwd=repo_root)
    head_after = runner(["git", "rev-parse", "HEAD"], cwd=Path(item.worktree))
    if not base_after.ok or not base_after.stdout.strip() or not head_after.ok or not head_after.stdout.strip():
        return item, None, "could not verify refreshed retry base/head"
    refresh = {
        "base_sha_before": base_before.stdout.strip().splitlines()[0],
        "base_sha_after": base_after.stdout.strip().splitlines()[0],
        "worktree_head_before": head_before.stdout.strip().splitlines()[0],
        "worktree_head_after": head_after.stdout.strip().splitlines()[0],
    }
    item = replace(item, base_sha=refresh["base_sha_after"], merge_allowed=False)
    _save_engineering_work_item(operator_root, item)
    return item, refresh, None


def retry_engineering_work_item(
    work_id: str,
    *,
    confirm: str,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    repo_root: Path = ROOT,
    runner: Any = _run_command,
    codex_executable: str = "codex",
    executor: Any = execute_engineering_work_item if "execute_engineering_work_item" in globals() else None,
) -> dict[str, Any]:
    if confirm != "RETRY_CODEX":
        return _refused_retry_payload(work_id, "confirmation token required")
    item = _load_engineering_work_item(operator_root, work_id)
    if item.state != EngineeringState.BLOCKED.value:
        return _refused_retry_payload(work_id, "work item is not BLOCKED", item)
    if item.merge_allowed:
        return _refused_retry_payload(work_id, "merge_allowed must be false", item)
    if not _codex_retry_related(item):
        return _refused_retry_payload(work_id, "blocked reason is not Codex retry eligible", item)
    worktree = Path(item.worktree)
    if _engineering_worktree_violation(worktree):
        return _refused_retry_payload(work_id, "worktree is outside approved engineering root", item)
    if not worktree.exists() or not (worktree / ".git").exists():
        return _refused_retry_payload(work_id, "worktree is missing", item)
    expected_base = item.base_sha or _first_output_line(runner(["git", "rev-parse", "origin/add/pages-repo-default"], cwd=repo_root))
    verified, identity_reason = _verify_existing_engineering_worktree(item, repo_root=repo_root, expected_base_sha=expected_base, runner=runner)
    if not verified:
        return _refused_retry_payload(work_id, identity_reason, item)
    clean, clean_reason = _worktree_status_clean(item, runner=runner)
    if not clean:
        return _refused_retry_payload(work_id, clean_reason, item)
    if _remote_engineering_branch_exists(item, runner=runner):
        return _refused_retry_payload(work_id, "remote repair branch already exists", item)
    if _open_engineering_pr_exists(item, runner=runner):
        return _refused_retry_payload(work_id, "repair PR already exists", item)
    executable = shutil.which(codex_executable)
    if not executable:
        return _refused_retry_payload(work_id, f"{codex_executable} executable is unavailable", item)
    readiness = _codex_readiness_probe(executable, runner=runner, cwd=worktree)
    if not readiness["ok"]:
        return _refused_retry_payload(work_id, f"codex readiness failed: {readiness.get('failure_classification')}", item)
    item, refresh, refresh_error = _refresh_retry_worktree_base(operator_root, item, repo_root=repo_root, runner=runner)
    if refresh_error:
        return _refused_retry_payload(work_id, refresh_error, item)
    retry_authorization = {
        "confirmed": True,
        "reason": "CODEX_RETRY",
        "prior_state": EngineeringState.BLOCKED.value,
        "prior_blocked_reason": item.blocked_reason,
        "prior_failure_classification": item.failure_classification,
        "readiness_passed": True,
        "base_refreshed": True,
        **(refresh or {}),
    }
    item = _save_engineering_work_item(
        operator_root,
        replace(
            item,
            state=EngineeringState.WORKTREE_CREATED.value,
            blocked_reason=None,
            failure_classification=None,
            retry_authorization=retry_authorization,
            merge_allowed=False,
        ),
    )
    executor = executor or execute_engineering_work_item
    retried = executor(item, operator_root=operator_root, repo_root=repo_root, runner=runner)
    return {
        "schema_version": "blue_fern_operator_engineering_retry_v1",
        "work_id": work_id,
        "accepted": True,
        "reason": "RETRY_CODEX",
        "retry_authorization": retry_authorization,
        "work_item": retried.to_payload(),
    }


def execute_engineering_work_item(
    item: EngineeringWorkItem,
    *,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    repo_root: Path = ROOT,
    runner: Any = _run_command,
    codex_runner: Any | None = None,
    pr_creator: Any | None = None,
    max_attempts: int = 2,
) -> EngineeringWorkItem:
    item = _create_or_reuse_engineering_worktree(operator_root, item, repo_root=repo_root, runner=runner)
    if item.state == EngineeringState.BLOCKED.value or item.state == EngineeringState.PR_OPEN.value:
        return item
    attempt = item.repair_attempts
    while attempt < max_attempts:
        item = _invoke_codex_for_engineering(operator_root, item, runner=runner, codex_runner=codex_runner)
        if item.state == EngineeringState.BLOCKED.value:
            return item
        changed = _changed_paths(Path(item.worktree), runner=runner)
        unexpected = _unexpected_patch_paths(changed, item.allowed_paths)
        if unexpected:
            return _block_engineering_work(operator_root, item, "patch changed paths outside allowed scope", unexpected_paths=unexpected)
        item, passed = _run_engineering_validation(operator_root, item, runner=runner)
        if passed:
            changed = _changed_paths(Path(item.worktree), runner=runner)
            unexpected = _unexpected_patch_paths(changed, item.allowed_paths)
            if unexpected:
                return _block_engineering_work(operator_root, item, "patch changed paths outside allowed scope", unexpected_paths=unexpected)
            item = _commit_engineering_patch(operator_root, item, changed, runner=runner)
            if item.state == EngineeringState.BLOCKED.value:
                return item
            item = _push_engineering_branch(operator_root, item, runner=runner)
            if item.state == EngineeringState.BLOCKED.value:
                return item
            return _open_engineering_pr(operator_root, item, runner=runner, pr_creator=pr_creator)
        attempt = item.repair_attempts
    return _block_engineering_work(operator_root, item, "validation failed after bounded repair attempts")


def _write_ledger(operator_root: Path, result: OperatorResult) -> None:
    operator_root.mkdir(parents=True, exist_ok=True)
    _write_json(operator_root / "latest.json", result.to_payload())
    _write_json(operator_root / "notification-latest.json", result.notification.to_payload())
    history = operator_root / "history.jsonl"
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.to_payload(), sort_keys=True) + "\n")
    for incident in result.incidents:
        _write_json(operator_root / "incidents" / f"{incident.incident_id}.json", incident.to_payload())
    _write_run_receipt(operator_root, result, 0, "OK")


def _run_id(result: OperatorResult) -> str:
    digest = hashlib.sha256(json.dumps(result.to_payload(), sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"operator-{result.checked_at.replace(':', '').replace('-', '').replace('Z', 'Z')}-{digest}"


def _write_run_receipt(operator_root: Path, result: OperatorResult, exit_code: int, outcome: str) -> Path:
    run_id = _run_id(result)
    date = result.checked_at[:10]
    remediation_failures = [
        incident.incident_id
        for incident in result.incidents
        if (incident.remediation or {}).get("attempted") is True
        and ((incident.remediation or {}).get("outcome") == "FAILED" or (incident.remediation or {}).get("unexpected_changes"))
    ]
    payload = {
        "schema_version": RUN_RECEIPT_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": result.checked_at,
        "completed_at": result.checked_at,
        "exit_code": exit_code,
        "outcome": outcome,
        "operator_head": _git_head(ROOT),
        "dispatch_summary": [
            {
                "dispatch": row.dispatch,
                "classification": row.classification,
                "status_state": row.status_state,
                "recovery_action": row.recovery_action,
                "remediation_outcome": (row.remediation or {}).get("outcome"),
            }
            for row in result.dispatches
        ],
        "incident_count": len(result.incidents),
        "new_incidents": [
            row["incident_id"]
            for row in result.notification.summary
            if NotificationReason.NEW_INCIDENT.value in row.get("reasons", [])
        ],
        "changed_incidents": result.notification.incident_ids,
        "recoveries": [
            row["incident_id"]
            for row in result.notification.summary
            if NotificationReason.MATERIAL_RECOVERY.value in row.get("reasons", [])
        ],
        "automatic_remediations": [
            incident.incident_id
            for incident in result.incidents
            if (incident.remediation or {}).get("attempted") is True
        ],
        "remediation_failures": remediation_failures,
        "notification_required": result.notification.notification_required,
        "notification_reasons": result.notification.reasons,
    }
    path = operator_root / "runs" / date / f"{run_id}.json"
    _write_json(path, payload)
    return path


def _git_head(repo_root: Path) -> str | None:
    git_entry = repo_root / ".git"
    git_dir = git_entry
    if git_entry.is_file():
        raw_gitdir = git_entry.read_text(encoding="utf-8", errors="replace").strip()
        if raw_gitdir.startswith("gitdir:"):
            value = raw_gitdir.split(":", 1)[1].strip()
            git_dir = (repo_root / value).resolve() if not Path(value).is_absolute() else Path(value)
    head = git_dir / "HEAD"
    if not head.is_file():
        return None
    raw = head.read_text(encoding="utf-8", errors="replace").strip()
    if not raw.startswith("ref: "):
        return raw
    ref_path = repo_root / ".git" / raw.split(" ", 1)[1]
    if ref_path.is_file():
        return ref_path.read_text(encoding="utf-8", errors="replace").strip()
    return None


@contextlib.contextmanager
def _operator_lock(operator_root: Path):
    lock_dir = operator_root / ".lock"
    try:
        lock_dir.mkdir(parents=True)
    except FileExistsError as exc:
        raise RuntimeError(f"Operator lock already held: {lock_dir}") from exc
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def render_text(result: OperatorResult) -> str:
    lines = ["Blue Fern Operator", ""]
    meaningful = [row for row in result.dispatches if row.classification]
    rows = meaningful or result.dispatches
    for row in rows:
        label = row.dispatch.replace("-", " ").title().replace("Ice", "ICE")
        lines.append(f"{label}:")
        if row.classification:
            lines.append(row.classification)
            if row.exported_status and row.exported_status.get("observed_date"):
                lines.append(f"Latest exported status: {row.exported_status['observed_date']}")
            lines.append(f"Status state: {row.status_state}")
            lines.append(f"Recommended action: {row.recommended_action}")
        else:
            lines.append("NO_ACTION")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic Blue Fern Operator supervisor.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Run the read-only supervisor check.")
    check.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    check.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help=argparse.SUPPRESS)
    check.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    check.add_argument("--repo-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    check.add_argument("--now", help=argparse.SUPPRESS)
    check.add_argument("--no-ledger", action="store_true", help=argparse.SUPPRESS)
    engineer = sub.add_parser("engineer", help="Prepare deterministic engineering work items for eligible incidents.")
    engineer.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    engineer.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help=argparse.SUPPRESS)
    engineer.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    engineer.add_argument("--repo-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    engineer.add_argument("--worktree-root", type=Path, default=Path(r"C:\BlueFernRunner\OperatorWorktrees"), help=argparse.SUPPRESS)
    engineer.add_argument("--now", help=argparse.SUPPRESS)
    engineer.add_argument("--execute", action="store_true", help=argparse.SUPPRESS)
    diagnose = sub.add_parser("engineer-diagnose", help="Read-only diagnostics for a prepared engineering work item.")
    diagnose.add_argument("--work-id", required=True, help=argparse.SUPPRESS)
    diagnose.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    diagnose.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    retry = sub.add_parser("engineer-retry", help="Retry a blocked engineering work item with explicit authorization.")
    retry.add_argument("--work-id", required=True, help=argparse.SUPPRESS)
    retry.add_argument("--confirm", required=True, help=argparse.SUPPRESS)
    retry.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    retry.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    retry.add_argument("--repo-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "engineer-diagnose":
        payload = diagnose_engineering_work_item(args.work_id, operator_root=args.operator_root)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            item = payload["work_item"]
            readiness = payload["codex_readiness"]
            print(f"{item['work_id']} {item['state']} codex_ready={readiness.get('ok')}")
        return 0
    if args.command == "engineer-retry":
        payload = retry_engineering_work_item(args.work_id, confirm=args.confirm, operator_root=args.operator_root, repo_root=args.repo_root)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"{payload['work_id']} accepted={payload['accepted']} {payload['reason']}")
        return 0 if payload["accepted"] else 2
    now = _parse_time(args.now) if args.now else None
    config = load_config(args.config)
    if args.command == "engineer":
        result = check_operator(
            repo_root=args.repo_root,
            operator_root=args.operator_root,
            config=config,
            now=now,
            write_ledger=False,
            allow_automatic_remediation=False,
        )
        items = prepare_engineering_work(
            result,
            operator_root=args.operator_root,
            worktree_root=args.worktree_root,
            base_sha=_git_head(args.repo_root),
        )
        if args.execute:
            items = [
                execute_engineering_work_item(item, operator_root=args.operator_root, repo_root=args.repo_root)
                for item in items
            ]
        payload = {
            "schema_version": "blue_fern_operator_engineering_queue_v1",
            "work_items": [item.to_payload() for item in items],
            "work_item_count": len(items),
            "approval_notifications": [
                build_engineering_approval_notification(item).to_payload()
                for item in items
                if build_engineering_approval_notification(item).notification_required
            ],
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for item in items:
                print(f"{item.work_id} {item.dispatch} {item.classification} {item.state}")
        return 0
    result = check_operator(
        repo_root=args.repo_root,
        operator_root=args.operator_root,
        config=config,
        now=now,
        write_ledger=not args.no_ledger,
    )
    if args.json:
        print(json.dumps(result.to_payload(), indent=2, sort_keys=True))
    else:
        print(render_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
