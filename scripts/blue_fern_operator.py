from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
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
            if mode in {"automatic", "recommend", "forbidden"}:
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
    for incident in result.incidents:
        incident_reasons = _material_change_reasons(incident, previous_incidents.get(incident.incident_id), policy)
        incident_reasons = {reason for reason in incident_reasons if reason in NOTIFICATION_REASONS}
        if not incident_reasons:
            continue
        reasons.update(incident_reasons)
        dispatches.add(incident.dispatch)
        incident_ids.add(incident.incident_id)
        rows.append(
            {
                "dispatch": incident.dispatch,
                "incident_id": incident.incident_id,
                "classification": incident.classification,
                "state": incident.state,
                "status_state": incident.status_state,
                "recovery_action": incident.recovery_action,
                "reasons": sorted(incident_reasons),
            }
        )
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
            recommended = _recommended_action(classification, status, plan)
            evidence = [*exported.evidence, *status.evidence, *plan.evidence]
            remediation = _remediation_payload(action=plan.action or "NONE", reason="recommendation only")
            notification_state = "RECOMMENDATION_ONLY"
            auto_allowed, auto_reason = _can_auto_apply_rebuild(
                classification=classification,
                status=status,
                plan=plan,
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
                    plan=plan,
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
                remediation = _remediation_payload(action=plan.action, reason="automatic remediation disabled for this run")
            elif plan.action == "REBUILD_STATUS":
                remediation = _remediation_payload(action=plan.action, reason=auto_reason)
            incident = _build_incident(
                dispatch=dispatch,
                classification=classification,
                status=status,
                plan=plan,
                detected_at=checked_at,
                evidence=evidence,
                recommended_action=recommended,
                existing=open_incidents.get(_incident_key(dispatch, classification, runner_date, status)),
                affected_date=runner_date,
                remediation=remediation,
            )
            current_keys.add(incident.incident_key)
            incidents.append(incident)
            dispatch_results.append(
                DispatchResult(
                    dispatch=dispatch,
                    state="STALE_OBSERVABILITY",
                    classification=classification.value,
                    recommended_action=recommended,
                    status_state=status.state,
                    observed_date=runner_date,
                    recovery_disposition=plan.disposition,
                    recovery_action=plan.action,
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    now = _parse_time(args.now) if args.now else None
    config = load_config(args.config)
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
