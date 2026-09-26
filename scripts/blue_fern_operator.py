from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
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
from bluefern_dispatches.operational_status_exporter import (  # noqa: E402
    classify_status_checkout_state,
    export_status,
    prepare_status_checkout,
)
from scripts.run_operational_status_export import (  # noqa: E402
    DEFAULT_BRANCH as DEFAULT_STATUS_BRANCH,
    care_expected_instances_from_task_scheduler,
)


SCHEMA_VERSION = "blue_fern_operator_result_v1"
INCIDENT_SCHEMA_VERSION = "blue_fern_operator_incident_v1"
NOTIFICATION_SCHEMA_VERSION = "blue_fern_operator_notification_v1"
RUN_RECEIPT_SCHEMA_VERSION = "blue_fern_operator_run_receipt_v1"
ENGINEERING_SCHEMA_VERSION = "blue_fern_operator_engineering_v1"
CODEX_ATTEMPT_SCHEMA_VERSION = "blue_fern_operator_codex_attempt_v1"
REMEDIATION_PLAN_SCHEMA_VERSION = "blue_fern_operator_remediation_plan_v1"
REMEDIATION_RECEIPT_SCHEMA_VERSION = "blue_fern_operator_remediation_receipt_v1"
AUTONOMY_STATE_SCHEMA_VERSION = "blue_fern_operator_autonomy_state_v1"
DIAGNOSIS_EVIDENCE_EXCERPT_BYTES = 4096
DIAGNOSIS_EVIDENCE_FILE_LIMIT = 12
ENGINEERING_CLOSE_SCHEMA_VERSION = "blue_fern_operator_engineering_close_v1"
DEFAULT_CONFIG_PATH = ROOT / "ops" / "operator" / "config.json"
DEFAULT_OPERATOR_ROOT = ROOT / "ops" / "operator"
DISPATCH_ORDER = ("food-line", "care-line", "gaza", "ice")
TERMINAL_NO_ACTION_STATES = {"COMPLETE", "PUBLISHED", "NO_UPDATE", "SAFE_NO_OP", "DEGRADED"}
FORBIDDEN_REMEDIATION_ACTIONS = {
    "PUBLISH_APPROVED_RELEASE",
    "PUBLISH_NO_UPDATE",
    "REPLAY_COLLECTION",
}
AUTOMATIC_REMEDIATION_ACTIONS = {"REBUILD_STATUS"}
TRANSIENT_NETWORK_RETRY_ACTION = "TRANSIENT_NETWORK_TASK_RETRY"
SOURCE_TRANSIENT_RETRY_ACTION = "SOURCE_TRANSIENT_FETCH_RETRY"
SOURCE_TRANSIENT_BACKOFF_MINUTES = 30
SOURCE_TRANSIENT_MAX_RETRY_AFTER_MINUTES = 360
SOURCE_TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}
SOURCE_NON_TRANSIENT_HTTP_STATUS_CODES = {400, 401, 403, 404, 410, 451}
REMEDIATION_OUTCOMES = {
    "NOT_ATTEMPTED",
    "REBUILT",
    "REFRESHED",
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
ENGINEERING_CLOSE_CONFIRMATION = "CLOSE_ENGINEERING_WORK"
ENGINEERING_CLOSE_DISPOSITIONS = {
    "NON_CODE_INCIDENT",
    "INCIDENT_RECOVERED",
    "SUPERSEDED",
}
ENGINEERING_CLOSEABLE_STATES = {
    "BLOCKED",
    "DETECTED",
    "WORKTREE_CREATED",
    "DIAGNOSING",
    "PATCHED",
    "VALIDATING",
    "FAILED",
}
REMEDIATION_ACTIONS = {
    "RUNNER_ROLL_FORWARD",
    "REBUILD_STATUS",
    "REFRESH_STATUS_EXPORT",
    TRANSIENT_NETWORK_RETRY_ACTION,
    SOURCE_TRANSIENT_RETRY_ACTION,
    "VERIFY_PUBLIC_STATE",
    "INVESTIGATE_SOURCE_FAILURES",
    "WAIT_FOR_NEXT_SCHEDULED_RUN",
    "ENGINEER_PREPARE_FIX",
    "CLOSE_NON_CODE_INCIDENT",
    "NO_ACTION",
}
EXECUTABLE_REMEDIATION_ACTIONS = {
    "RUNNER_ROLL_FORWARD",
    "REBUILD_STATUS",
    "REFRESH_STATUS_EXPORT",
    TRANSIENT_NETWORK_RETRY_ACTION,
    SOURCE_TRANSIENT_RETRY_ACTION,
    "VERIFY_PUBLIC_STATE",
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
AUTONOMOUS_RETRY_LIMITS = {
    "REBUILD_STATUS": 2,
    "REFRESH_STATUS_EXPORT": 2,
    TRANSIENT_NETWORK_RETRY_ACTION: 2,
    SOURCE_TRANSIENT_RETRY_ACTION: 2,
    "RUNNER_ROLL_FORWARD": 1,
    "VERIFY_PUBLIC_STATE": 1,
    "ENGINEER_PREPARE_FIX": 1,
}
TRANSIENT_NETWORK_BACKOFF_MINUTES = 30
TRANSIENT_NETWORK_DEPENDENCIES = {
    "github": {
        "host": "github.com",
        "read_only_probe": ["git", "ls-remote", "--heads", "https://github.com/RedGarland/the-blue-fern-co-dispatches.git", "add/pages-repo-default"],
    }
}
TRANSIENT_RETRY_TASKS = {
    "food-line": {
        "task_keys": {"food_line_source_watch", "food_line_source_watch_resume", "food_line_current_intake"},
        "wrapper": "scripts/windows/run_food_line_current_intake.ps1",
        "date_argument": "-EditionDate",
        "public_side_effects": False,
    },
    "care-line": {
        "task_keys": {"care_line_collection"},
        "wrapper": "scripts/windows/run_care_line_national_collection.ps1",
        "date_argument": "-RunDate",
        "public_side_effects": False,
    },
    "ice": {
        "task_keys": {"ice_monitor"},
        "wrapper": "scripts/windows/run_ice_monitor.ps1",
        "date_argument": None,
        "public_side_effects": False,
    },
}
SOURCE_TRANSIENT_RETRY_HANDLERS = {
    "food-line": {
        "enabled": False,
        "reason": "Food Line has durable source-watch recovery receipts but no canonical one-source replay handler.",
        "public_side_effects": False,
    },
    "care-line": {
        "enabled": False,
        "reason": "Care Line has per-source collection internals, but the production scheduler wrapper lacks a source-id replay contract.",
        "public_side_effects": False,
    },
    "ice": {
        "enabled": False,
        "reason": "ICE monitor does not expose a source-isolated replay handler.",
        "public_side_effects": False,
    },
    "gaza": {
        "enabled": False,
        "excluded": True,
        "reason": "Gaza source collection remains excluded from automatic replay because of publication/editorial coupling.",
        "public_side_effects": True,
    },
}
APPROVAL_REQUIRED_ACTIONS = {
    "PUBLISH_APPROVED_RELEASE",
    "PUBLISH_NO_UPDATE",
    "REPLAY_COLLECTION",
    "MERGE_PR",
    "CLOSE_NON_CODE_INCIDENT",
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


class AutonomyLifecycle(StrEnum):
    DETECTED = "DETECTED"
    CLASSIFIED = "CLASSIFIED"
    DIAGNOSING = "DIAGNOSING"
    REMEDIATION_PLANNED = "REMEDIATION_PLANNED"
    REMEDIATING = "REMEDIATING"
    VALIDATING = "VALIDATING"
    PROMOTING = "PROMOTING"
    SYNCING = "SYNCING"
    PROVING = "PROVING"
    STATUS_REFRESH = "STATUS_REFRESH"
    RECOVERED = "RECOVERED"
    HEALTHY = "HEALTHY"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKED = "BLOCKED"
    FAILED_SAFE = "FAILED_SAFE"


class RootCauseClassification(StrEnum):
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    SOURCE_EXTERNAL_RESTRICTION = "SOURCE_EXTERNAL_RESTRICTION"
    SOURCE_TRANSIENT = "SOURCE_TRANSIENT"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"
    RUNNER_SYNC_FAILURE = "RUNNER_SYNC_FAILURE"
    REPOSITORY_DIRTY_STATE_FAILURE = "REPOSITORY_DIRTY_STATE_FAILURE"
    DNS_NETWORK_FAILURE = "DNS_NETWORK_FAILURE"
    SCHEDULER_FAILURE = "SCHEDULER_FAILURE"
    COLLECTION_FAILURE = "COLLECTION_FAILURE"
    SOURCE_SPECIFIC_HTTP_FAILURE = "SOURCE_SPECIFIC_HTTP_FAILURE"
    PARSER_CONFIGURATION_DEFECT = "PARSER_CONFIGURATION_DEFECT"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    PUBLICATION_FAILURE = "PUBLICATION_FAILURE"
    OPERATIONAL_STATUS_EXPORTER_FAILURE = "OPERATIONAL_STATUS_EXPORTER_FAILURE"
    STALE_OBSERVABILITY = "STALE_OBSERVABILITY"
    MISSING_RECEIPT_PROOF = "MISSING_RECEIPT_PROOF"
    PERSISTENT_EXTERNAL_ACCESS_RESTRICTION = "PERSISTENT_EXTERNAL_ACCESS_RESTRICTION"
    EXTERNAL_TRANSIENT_DEPENDENCY_FAILURE = "EXTERNAL_TRANSIENT_DEPENDENCY_FAILURE"
    EDITORIAL_POLICY_BOUNDARY = "EDITORIAL_POLICY_BOUNDARY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DispatchConfig:
    dispatch: str
    runner_root: Path
    enabled: bool = True


@dataclass(frozen=True)
class OperatorConfig:
    status_freshness_threshold_minutes: int
    dispatches: dict[str, DispatchConfig]
    status_root: Path | None = None
    status_branch: str = DEFAULT_STATUS_BRANCH


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
    lifecycle_state: str | None = None
    root_cause_classification: str | None = None
    approval_required: bool = False
    next_action: str | None = None
    retry_state: dict[str, Any] = field(default_factory=dict)

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
    closure: dict[str, Any] | None = None
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
    autonomy_state: dict[str, Any] = field(default_factory=dict)
    production_state_mutated: bool = False
    schema_version: str = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "autonomy_state": self.autonomy_state,
            "checked_at": self.checked_at,
            "dispatches": [result.to_payload() for result in self.dispatches],
            "incidents": [incident.to_payload() for incident in self.incidents],
            "notification": self.notification.to_payload(),
            "production_state_mutated": self.production_state_mutated,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class RemediationActionPlan:
    dispatch: str
    incident_id: str
    classification: str
    affected_date: str | None
    current_condition: str
    proposed_action: str
    reason: str
    evidence: list[str]
    safety_checks: dict[str, Any]
    approval_required: bool
    executable: bool
    expected_mutation_scope: list[str]
    schema_version: str = REMEDIATION_PLAN_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class RemediationReceipt:
    dispatch: str
    incident_id: str
    action: str
    accepted: bool
    outcome: str
    reason: str
    started_at: str
    completed_at: str
    before_head: str | None = None
    after_head: str | None = None
    expected_mutation_scope: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    receipt_path: str | None = None
    schema_version: str = REMEDIATION_RECEIPT_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class RemediationEvidenceContext:
    dispatch: str
    affected_date: str | None
    status_state: str
    next_action: str
    recovery_action: str
    task_failures: list[dict[str, Any]] = field(default_factory=list)
    failure_stages: list[str] = field(default_factory=list)
    exit_codes: list[int] = field(default_factory=list)
    checkout_failures: list[str] = field(default_factory=list)
    source_failures: list[str] = field(default_factory=list)
    source_failure_records: list[dict[str, Any]] = field(default_factory=list)
    code_exception_markers: list[str] = field(default_factory=list)
    evidence_entries: list[dict[str, Any]] = field(default_factory=list)
    evidence_complete: bool = True

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


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
        status_root=Path(str(operator["status_root"])) if operator.get("status_root") else None,
        status_branch=str(operator.get("status_branch") or DEFAULT_STATUS_BRANCH),
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


def _configured_status_root(config: OperatorConfig, repo_root: Path) -> Path:
    return (config.status_root or repo_root).resolve()


def _operator_code_root(operator_root: Path) -> Path:
    operator_root = operator_root.resolve()
    if operator_root.name == "operator" and operator_root.parent.name == "ops":
        return operator_root.parent.parent
    return operator_root


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
    attempt_count: int = 0,
) -> dict[str, Any]:
    if outcome not in REMEDIATION_OUTCOMES:
        outcome = "FAILED"
    return {
        "action": action,
        "attempted": attempted,
        "attempt_count": attempt_count or (1 if attempted else 0),
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
    source_retry_state = remediation.get("source_retry_state") if isinstance(remediation.get("source_retry_state"), dict) else {}
    isolated_source_retry_tracking = (
        remediation.get("action") == SOURCE_TRANSIENT_RETRY_ACTION
        and remediation.get("attempted") is not True
        and not source_retry_state.get("terminal_classification")
    )

    if previous is None:
        if isolated_source_retry_tracking:
            return reasons
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
    if isolated_source_retry_tracking:
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


def _string_blob(*values: Any) -> str:
    return "\n".join(_lower_values(value) for value in values if value is not None)


def _dispatch_result_for(result: OperatorResult, dispatch: str) -> DispatchResult | None:
    return next((row for row in result.dispatches if row.dispatch == dispatch), None)


def _summary_int(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = _int_value(row.get(key))
        if value is not None:
            return value
    return 0


def _transient_network_markers(blob: str) -> bool:
    return any(
        token in blob
        for token in (
            "dns",
            "name resolution",
            "temporary failure in name resolution",
            "getaddrinfo",
            "could not resolve host",
            "failed to resolve",
            "network is unreachable",
            "temporary network",
            "github.com",
            "connection timed out",
            "connect timeout",
        )
    )


def _source_external_restriction_markers(blob: str) -> bool:
    return any(
        token in blob
        for token in (
            "403",
            "401",
            "forbidden",
            "unauthorized",
            "cloudflare",
            "captcha",
            "access denied",
            "publisher restriction",
            "external access restriction",
        )
    )


def _source_transient_markers(blob: str) -> bool:
    return any(token in blob for token in ("timeout", "timed out", "tls", "connection reset", "503", "502", "504", "429"))


def _source_parser_or_content_markers(blob: str) -> bool:
    return any(
        token in blob
        for token in (
            "parseerror",
            "parser",
            "schema mismatch",
            "invalid content",
            "non-xml",
            "jsondecodeerror",
            "valueerror",
            "typeerror",
        )
    )


def _source_http_status(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or "")
    match = re.search(r"\b(?:http(?:error)?[:\s]*)?([45][0-9]{2})\b", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _source_retry_after_minutes(value: Any, *, now: datetime | None = None) -> int | None:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        seconds = max(0, int(value))
        return min(SOURCE_TRANSIENT_MAX_RETRY_AFTER_MINUTES, (seconds + 59) // 60)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        seconds = max(0, int(text))
        return min(SOURCE_TRANSIENT_MAX_RETRY_AFTER_MINUTES, (seconds + 59) // 60)
    parsed = _parse_time(text)
    if parsed is None:
        for fmt in ("%a, %d %b %Y %H:%M:%S GMT", "%A, %d-%b-%y %H:%M:%S GMT"):
            try:
                parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    base = now or _utc_now()
    seconds = max(0, int((parsed - base).total_seconds()))
    return min(SOURCE_TRANSIENT_MAX_RETRY_AFTER_MINUTES, (seconds + 59) // 60)


def classify_source_failure_record(record: dict[str, Any]) -> str:
    blob = _lower_values(record)
    status = _source_http_status(record.get("status_code") or record.get("http_status") or record.get("failure_reason") or record.get("error") or blob)
    if _source_external_restriction_markers(blob) or status in {401, 403, 451}:
        return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    if _source_parser_or_content_markers(blob):
        return RootCauseClassification.INTERNAL_FAILURE.value
    if status in SOURCE_TRANSIENT_HTTP_STATUS_CODES:
        if status == 429 and any(token in blob for token in ("no retry", "do not retry", "quota exceeded", "rate limit policy denied")):
            return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
        return RootCauseClassification.SOURCE_TRANSIENT.value
    if status in SOURCE_NON_TRANSIENT_HTTP_STATUS_CODES:
        return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    if any(token in blob for token in ("timeout", "timed out", "connection reset", "temporary failure in name resolution", "getaddrinfo", "temporarily unavailable")):
        return RootCauseClassification.SOURCE_TRANSIENT.value
    if any(token in blob for token in ("redirect loop", "source retired", "robots", "auth", "unauthorized")):
        return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    return RootCauseClassification.UNKNOWN.value


def _bounded_source_failure_record(row: Any, *, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    payload = payload or {}
    if isinstance(row, str):
        record: dict[str, Any] = {"failure_reason": row}
    elif isinstance(row, dict):
        record = {
            key: row.get(key)
            for key in (
                "source_id",
                "source_name",
                "domain",
                "failure_class",
                "failure_reason",
                "error",
                "status_code",
                "http_status",
                "transient",
                "retry_after",
                "last_successful_fetch",
                "run_id",
                "pipeline_run_id",
                "logical_run_id",
            )
            if row.get(key) not in {None, ""}
        }
    else:
        return None
    if not str(record.get("source_id") or "").strip():
        source_id = payload.get("source_id") or payload.get("failed_source_id")
        if source_id:
            record["source_id"] = str(source_id)
    if not str(record.get("source_id") or "").strip():
        match = re.search(r"\bsource[_ -]?id[:=]\s*([A-Za-z0-9_.:-]+)", str(record.get("failure_reason") or record.get("error") or ""))
        if match:
            record["source_id"] = match.group(1)
    if not str(record.get("source_id") or "").strip():
        return None
    for key in ("run_id", "pipeline_run_id", "logical_run_id"):
        if not record.get(key) and payload.get(key):
            record[key] = payload.get(key)
    if payload.get("edition_date") and not record.get("logical_date"):
        record["logical_date"] = payload.get("edition_date")
    if payload.get("scheduled_for") and not record.get("logical_date"):
        record["logical_date"] = payload.get("scheduled_for")
    record["source_id"] = str(record["source_id"])[:160]
    record["failure_classification"] = classify_source_failure_record(record)
    return record


def _source_failure_records_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("source_failure_diagnostics", "failed_source_diagnostics", "failed_source_ids", "source_failures"):
        value = payload.get(key)
        if isinstance(value, list):
            for row in value[:25]:
                record = _bounded_source_failure_record(row, payload=payload)
                if record:
                    records.append(record)
        elif isinstance(value, dict):
            for row_key, row_value in list(value.items())[:25]:
                record = _bounded_source_failure_record(row_value, payload={**payload, "source_id": row_key})
                if record:
                    records.append(record)
    details = payload.get("details")
    if isinstance(details, dict):
        records.extend(_source_failure_records_from_payload(details))
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        deduped[_source_retry_key_from_record(record, fallback_date=str(payload.get("edition_date") or payload.get("scheduled_for") or ""))] = record
    return list(deduped.values())[:25]


def _source_retry_key_from_record(record: dict[str, Any], *, fallback_date: str = "") -> str:
    source_id = str(record.get("source_id") or "unknown-source")
    logical_date = str(record.get("logical_date") or fallback_date or "")
    logical_run_id = str(record.get("logical_run_id") or record.get("run_id") or record.get("pipeline_run_id") or "")
    failure_class = str(record.get("failure_class") or record.get("failure_classification") or "UNKNOWN")
    return "|".join((logical_date, source_id, logical_run_id, failure_class))


def classify_incident_root_cause(
    incident: Incident,
    dispatch_result: DispatchResult | None = None,
) -> str:
    exported = dispatch_result.exported_status if dispatch_result else {}
    exported = exported if isinstance(exported, dict) else {}
    source_summary = exported.get("source_failure_summary") if isinstance(exported.get("source_failure_summary"), dict) else {}
    blob = _string_blob(incident.to_payload(), dispatch_result.to_payload() if dispatch_result else None)
    if (
        source_summary.get("all_current_failures_external") is True
        or (
            int(source_summary.get("external_access_restriction_count") or 0) > 0
            and int(source_summary.get("unclassified_source_failure_count") or 0) == 0
        )
        or _source_external_restriction_markers(blob)
    ):
        return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    failed_source_count = _summary_int(
        source_summary,
        "current_source_failure_count",
        "failed_source_count",
        "source_failure_count",
        "total_source_failure_count",
    )
    if _transient_network_markers(blob):
        if failed_source_count == 1 and _source_transient_markers(blob):
            return RootCauseClassification.SOURCE_TRANSIENT.value
        return RootCauseClassification.TRANSIENT_NETWORK.value
    if incident.classification == Classification.STALE_OBSERVABILITY.value:
        return RootCauseClassification.STALE_OBSERVABILITY.value
    if incident.classification == Classification.STATUS_EXPORT_PROBLEM.value or incident.recovery_action == "INVESTIGATE_STATUS_EXPORT":
        return RootCauseClassification.OPERATIONAL_STATUS_EXPORTER_FAILURE.value
    if incident.classification == Classification.MISSED_RUN.value or incident.status_state == "MISSED":
        return RootCauseClassification.MISSING_RECEIPT_PROOF.value
    if incident.recovery_action in {"PUBLISH_APPROVED_RELEASE", "PUBLISH_NO_UPDATE"}:
        return RootCauseClassification.EDITORIAL_POLICY_BOUNDARY.value
    if "runner_roll_forward" in blob or "fast-forward" in blob or "runner is behind" in blob:
        return RootCauseClassification.RUNNER_SYNC_FAILURE.value
    if "verify_checkout" in blob or ("dirty" in blob and "checkout" in blob):
        return RootCauseClassification.REPOSITORY_DIRTY_STATE_FAILURE.value
    if "task scheduler" in blob or "scheduled task" in blob:
        return RootCauseClassification.SCHEDULER_FAILURE.value
    if _source_transient_markers(blob):
        return RootCauseClassification.SOURCE_TRANSIENT.value
    if any(token in blob for token in ("traceback", "typeerror", "valueerror", "assertionerror", "nameerror", "modulenotfounderror")):
        return RootCauseClassification.INTERNAL_FAILURE.value
    if "validation failed" in blob or "doctor" in blob or "preflight" in blob:
        return RootCauseClassification.VALIDATION_FAILURE.value
    if "publication" in blob and ("failed" in blob or "incident_open" in blob):
        return RootCauseClassification.PUBLICATION_FAILURE.value
    if incident.classification == Classification.FAILED_RUN.value:
        return RootCauseClassification.COLLECTION_FAILURE.value
    return RootCauseClassification.UNKNOWN.value


def _retry_limit(action: str) -> int:
    return AUTONOMOUS_RETRY_LIMITS.get(action, 0)


def _attempt_count(remediation: dict[str, Any], action: str) -> int:
    if not remediation:
        return 0
    try:
        explicit = int(remediation.get("attempt_count") or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit:
        return explicit
    if remediation.get("attempted") is True and remediation.get("action") == action:
        return 1
    return 0


def _retry_state_for(incident: Incident, action: str) -> dict[str, Any]:
    remediation = incident.remediation if isinstance(incident.remediation, dict) else {}
    if action == SOURCE_TRANSIENT_RETRY_ACTION and isinstance(remediation.get("source_retry_state"), dict):
        return dict(remediation["source_retry_state"])
    attempts = _attempt_count(remediation, action)
    limit = _retry_limit(action)
    eligible = bool(action and action != "NONE" and attempts < limit)
    attempted_at = remediation.get("attempted_at")
    parsed_attempted_at = _parse_time(str(attempted_at)) if attempted_at else None
    next_eligible_at = None
    if action == TRANSIENT_NETWORK_RETRY_ACTION and eligible and parsed_attempted_at is not None:
        next_eligible_at = _format_time(parsed_attempted_at + timedelta(minutes=TRANSIENT_NETWORK_BACKOFF_MINUTES))
    if action == SOURCE_TRANSIENT_RETRY_ACTION and eligible and parsed_attempted_at is not None:
        next_eligible_at = _format_time(parsed_attempted_at + timedelta(minutes=SOURCE_TRANSIENT_BACKOFF_MINUTES))
    return {
        "attempt_count": attempts,
        "last_attempted_at": attempted_at,
        "last_result": remediation.get("outcome"),
        "max_attempts": limit,
        "minimum_backoff_minutes": TRANSIENT_NETWORK_BACKOFF_MINUTES
        if action == TRANSIENT_NETWORK_RETRY_ACTION
        else SOURCE_TRANSIENT_BACKOFF_MINUTES
        if action == SOURCE_TRANSIENT_RETRY_ACTION
        else None,
        "next_eligible_at": next_eligible_at,
        "next_eligible_action": action if eligible else None,
        "terminal_reason": None if eligible else ("retry_limit_reached" if limit else "no_autonomous_retry_policy"),
    }


def _incident_approval_required(incident: Incident, policy: RemediationPolicy) -> bool:
    action = incident.recovery_action or incident.recommended_action
    if action in APPROVAL_REQUIRED_ACTIONS:
        return True
    mode = policy.mode_for(action)
    if action == "ENGINEER_PREPARE_FIX" and mode == "automatic_prepare_pr":
        return False
    return bool(action and action != "NONE" and mode not in {"automatic", "automatic_prepare_pr"})


def lifecycle_for_incident(incident: Incident, policy: RemediationPolicy) -> str:
    if incident.state == IncidentState.RECOVERED.value:
        return AutonomyLifecycle.RECOVERED.value
    if incident.state != IncidentState.OPEN.value:
        return AutonomyLifecycle.HEALTHY.value
    root_cause = incident.root_cause_classification or classify_incident_root_cause(incident)
    if root_cause in {
        RootCauseClassification.PERSISTENT_EXTERNAL_ACCESS_RESTRICTION.value,
        RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value,
    }:
        return AutonomyLifecycle.WAITING_EXTERNAL.value
    if _incident_approval_required(incident, policy):
        return AutonomyLifecycle.APPROVAL_REQUIRED.value
    remediation = incident.remediation if isinstance(incident.remediation, dict) else {}
    action = str(remediation.get("action") or incident.recovery_action or incident.recommended_action or "")
    if remediation.get("attempted") is True:
        if remediation.get("outcome") in {"FAILED", "ROLLOUT_APPLIED_VALIDATION_FAILED"} or remediation.get("unexpected_changes"):
            return AutonomyLifecycle.FAILED_SAFE.value
        if action == "REFRESH_STATUS_EXPORT":
            return AutonomyLifecycle.STATUS_REFRESH.value
        if action == "RUNNER_ROLL_FORWARD":
            return AutonomyLifecycle.SYNCING.value
        return AutonomyLifecycle.VALIDATING.value
    if action == "RUNNER_ROLL_FORWARD":
        return AutonomyLifecycle.SYNCING.value
    if action == "REFRESH_STATUS_EXPORT":
        return AutonomyLifecycle.STATUS_REFRESH.value
    if action == "ENGINEER_PREPARE_FIX":
        return AutonomyLifecycle.REMEDIATION_PLANNED.value
    if action.startswith("INVESTIGATE"):
        return AutonomyLifecycle.DIAGNOSING.value
    if action == "WAIT_FOR_NEXT_SCHEDULED_RUN":
        return AutonomyLifecycle.WAITING_EXTERNAL.value
    return AutonomyLifecycle.CLASSIFIED.value


def enrich_incident_for_autonomy(
    incident: Incident,
    *,
    dispatch_result: DispatchResult | None,
    policy: RemediationPolicy,
) -> Incident:
    root_cause = classify_incident_root_cause(incident, dispatch_result)
    action = incident.recovery_action or incident.recommended_action
    approval_required = _incident_approval_required(incident, policy)
    if root_cause in {
        RootCauseClassification.PERSISTENT_EXTERNAL_ACCESS_RESTRICTION.value,
        RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value,
    }:
        approval_required = False
    enriched = replace(
        incident,
        root_cause_classification=root_cause,
        approval_required=approval_required,
        next_action=action,
    )
    return replace(
        enriched,
        lifecycle_state=lifecycle_for_incident(enriched, policy),
        retry_state=_retry_state_for(enriched, action),
    )


def build_autonomy_state(
    result: OperatorResult,
    *,
    config: OperatorConfig,
    policy: RemediationPolicy,
    protected_source_head: str | None,
) -> dict[str, Any]:
    active = [incident for incident in result.incidents if incident.state == IncidentState.OPEN.value]
    approval_required = [incident for incident in active if incident.approval_required]
    recovered = [incident for incident in result.incidents if incident.state == IncidentState.RECOVERED.value]
    runner_deployment = {
        dispatch: {
            "runner_root": str(row.runner_root),
            "enabled": row.enabled,
        }
        for dispatch, row in sorted(config.dispatches.items())
    }
    return {
        "schema_version": AUTONOMY_STATE_SCHEMA_VERSION,
        "current_system_health": "HEALTHY" if not active else "DEGRADED",
        "active_incident_count": len(active),
        "active_incidents": [
            {
                "incident_id": incident.incident_id,
                "dispatch": incident.dispatch,
                "lifecycle_state": incident.lifecycle_state,
                "root_cause_classification": incident.root_cause_classification,
                "remediation_state": incident.remediation,
                "current_blocker": "APPROVAL_REQUIRED" if incident.approval_required else None,
                "next_action": incident.next_action,
                "approval_required": incident.approval_required,
                "retry_state": incident.retry_state,
            }
            for incident in active
        ],
        "recovered_incidents": [incident.incident_id for incident in recovered],
        "protected_source_head": protected_source_head,
        "runner_deployment_state": runner_deployment,
        "last_proof_timestamp": result.checked_at,
        "public_side_effects": False,
    }


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
        remediation=remediation if remediation is not None else dict((existing or {}).get("remediation") or {}),
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


def _remediation_payload_from_receipt(
    receipt: RemediationReceipt,
    *,
    attempted_at: str,
    attempt_count: int,
) -> dict[str, Any]:
    return _remediation_payload(
        attempted=True,
        action=receipt.action,
        outcome=receipt.outcome if receipt.accepted else "FAILED",
        changed=bool(receipt.changed_paths),
        attempted_at=attempted_at,
        post_status=receipt.validation.get("underlying_status_after", {}).get("state")
        if isinstance(receipt.validation.get("underlying_status_after"), dict)
        else None,
        unexpected_changes=receipt.warnings if not receipt.accepted else [],
        status_artifacts_changed=receipt.changed_paths,
        reason=receipt.reason,
        attempt_count=attempt_count,
    )


def _auto_refresh_status_export_if_allowed(
    *,
    incident: Incident,
    status: DispatchStatus,
    dispatch_root: Path,
    repo_root: Path,
    operator_root: Path,
    config: OperatorConfig,
    policy: RemediationPolicy,
    checked_at: str,
    open_incidents: dict[str, dict[str, Any]],
    allow_automatic_remediation: bool,
) -> tuple[Incident, DispatchStatus, RecoveryPlan, dict[str, Any], bool]:
    plan = build_remediation_action_plan(
        incident,
        runner_root=dispatch_root,
        operator_root=operator_root,
        current_status=status,
        status_root=_configured_status_root(config, repo_root),
        policy=policy,
    )
    if plan.proposed_action != "REFRESH_STATUS_EXPORT":
        return incident, status, build_recovery_plan_from_status(status), incident.remediation, False
    attempts = _attempt_count(incident.remediation if isinstance(incident.remediation, dict) else {}, plan.proposed_action)
    if not allow_automatic_remediation:
        remediation = _remediation_payload(action=plan.proposed_action, reason="automatic remediation disabled for this run", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if policy.mode_for("REFRESH_STATUS_EXPORT") != "automatic":
        remediation = _remediation_payload(action=plan.proposed_action, reason=f"policy mode is {policy.mode_for('REFRESH_STATUS_EXPORT')}", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if not plan.executable:
        remediation = _remediation_payload(action=plan.proposed_action, reason=plan.reason, attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if attempts >= _retry_limit(plan.proposed_action):
        remediation = _remediation_payload(action=plan.proposed_action, reason="bounded retry limit reached", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if any(
        row.get("dispatch") == status.dispatch
        and row.get("classification") not in {Classification.STALE_OBSERVABILITY.value, Classification.STATUS_EXPORT_PROBLEM.value}
        for row in open_incidents.values()
    ):
        remediation = _remediation_payload(action=plan.proposed_action, reason="conflicting open underlying incident exists", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    receipt = _apply_refresh_status_export(
        plan,
        repo_root=repo_root,
        runner_root=dispatch_root,
        operator_root=operator_root,
        config=config,
        current_status=status,
        now=_parse_time(checked_at),
    )
    remediation = _remediation_payload_from_receipt(receipt, attempted_at=checked_at, attempt_count=attempts + 1)
    post_status = build_status(status.dispatch, status.date, root=dispatch_root)
    post_plan = build_recovery_plan_from_status(post_status)
    stale_recovered = receipt.accepted and not receipt.validation.get("stale_observability_after")
    incident_state = IncidentState.RECOVERED if stale_recovered else IncidentState.OPEN
    return (
        replace(
            incident,
            state=incident_state.value,
            remediation=remediation,
            recommended_action="NO_ACTION" if stale_recovered else incident.recommended_action,
        ),
        post_status,
        post_plan,
        remediation,
        stale_recovered,
    )


def _remediation_payload_from_transient_receipt(
    receipt: RemediationReceipt,
    *,
    attempted_at: str,
    attempt_count: int,
) -> dict[str, Any]:
    status_after = receipt.validation.get("underlying_status_after") if isinstance(receipt.validation, dict) else None
    return _remediation_payload(
        attempted=True,
        action=receipt.action,
        outcome=receipt.outcome if receipt.accepted else "FAILED",
        changed=receipt.accepted and receipt.outcome != "NO_ACTION",
        attempted_at=attempted_at,
        post_status=status_after.get("state") if isinstance(status_after, dict) else None,
        unexpected_changes=receipt.warnings if not receipt.accepted else [],
        status_artifacts_changed=receipt.changed_paths,
        reason=receipt.reason,
        attempt_count=attempt_count,
    )


def _auto_transient_network_retry_if_allowed(
    *,
    incident: Incident,
    status: DispatchStatus,
    dispatch_root: Path,
    repo_root: Path,
    operator_root: Path,
    config: OperatorConfig,
    policy: RemediationPolicy,
    checked_at: str,
    allow_automatic_remediation: bool,
) -> tuple[Incident, DispatchStatus, RecoveryPlan, dict[str, Any], bool]:
    plan = build_remediation_action_plan(
        incident,
        runner_root=dispatch_root,
        operator_root=operator_root,
        current_status=status,
        status_root=_configured_status_root(config, repo_root),
        policy=policy,
        runner=_run_command,
    )
    if plan.proposed_action != TRANSIENT_NETWORK_RETRY_ACTION:
        return incident, status, build_recovery_plan_from_status(status), incident.remediation, False
    attempts = _attempt_count(incident.remediation if isinstance(incident.remediation, dict) else {}, plan.proposed_action)
    if not allow_automatic_remediation:
        remediation = _remediation_payload(action=plan.proposed_action, reason="automatic remediation disabled for this run", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if policy.mode_for(TRANSIENT_NETWORK_RETRY_ACTION) != "automatic":
        remediation = _remediation_payload(action=plan.proposed_action, reason=f"policy mode is {policy.mode_for(TRANSIENT_NETWORK_RETRY_ACTION)}", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    retry_state = _transient_retry_backoff_state(incident, now=_parse_time(checked_at) or _utc_now())
    if attempts >= _retry_limit(plan.proposed_action) or not retry_state["retry_budget_remaining"]:
        remediation = _remediation_payload(action=plan.proposed_action, reason="bounded retry limit reached", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if not retry_state["backoff_elapsed"]:
        remediation = _remediation_payload(action=plan.proposed_action, reason=f"minimum backoff active until {retry_state['next_eligible_at']}", attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if not plan.executable:
        remediation = _remediation_payload(action=plan.proposed_action, reason=plan.reason, attempt_count=attempts)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    receipt = _apply_transient_network_task_retry(
        plan,
        repo_root=repo_root,
        runner_root=dispatch_root,
        operator_root=operator_root,
        config=config,
        current_status=status,
        runner=_run_command,
        now=_parse_time(checked_at),
    )
    remediation = _remediation_payload_from_transient_receipt(receipt, attempted_at=checked_at, attempt_count=attempts + 1)
    post_status = build_status(status.dispatch, status.date, root=dispatch_root)
    post_plan = build_recovery_plan_from_status(post_status)
    recovered = receipt.accepted and _terminal_retry_proof(post_status)
    return (
        replace(
            incident,
            state=IncidentState.RECOVERED.value if recovered else IncidentState.OPEN.value,
            remediation=remediation,
            recommended_action="NO_ACTION" if recovered else incident.recommended_action,
        ),
        post_status,
        post_plan,
        remediation,
        recovered,
    )


def _auto_source_transient_retry_if_allowed(
    *,
    incident: Incident,
    status: DispatchStatus,
    dispatch_root: Path,
    repo_root: Path,
    operator_root: Path,
    config: OperatorConfig,
    policy: RemediationPolicy,
    checked_at: str,
    allow_automatic_remediation: bool,
) -> tuple[Incident, DispatchStatus, RecoveryPlan, dict[str, Any], bool]:
    plan = build_remediation_action_plan(
        incident,
        runner_root=dispatch_root,
        operator_root=operator_root,
        current_status=status,
        status_root=_configured_status_root(config, repo_root),
        policy=policy,
        runner=_run_command,
    )
    if plan.proposed_action != SOURCE_TRANSIENT_RETRY_ACTION:
        return incident, status, build_recovery_plan_from_status(status), incident.remediation, False
    source_state = dict(plan.safety_checks.get("source_retry_state") or {})
    attempts = int(source_state.get("attempt_count") or 0)
    if not allow_automatic_remediation:
        remediation = _source_retry_remediation_payload(state=source_state, reason="automatic remediation disabled for this run")
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if policy.mode_for(SOURCE_TRANSIENT_RETRY_ACTION) != "automatic":
        remediation = _source_retry_remediation_payload(state=source_state, reason=f"policy mode is {policy.mode_for(SOURCE_TRANSIENT_RETRY_ACTION)}")
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if attempts >= _retry_limit(SOURCE_TRANSIENT_RETRY_ACTION) or not source_state.get("retry_budget_remaining", False):
        source_state["terminal_classification"] = source_state.get("terminal_classification") or "retry_limit_reached"
        remediation = _source_retry_remediation_payload(state=source_state, reason="bounded source retry limit reached")
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if not source_state.get("backoff_elapsed", False):
        remediation = _source_retry_remediation_payload(state=source_state, reason=f"minimum source backoff active until {source_state.get('next_eligible_at')}")
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    if not plan.executable:
        remediation = _source_retry_remediation_payload(state=source_state, reason=plan.reason)
        return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False
    remediation = _source_retry_remediation_payload(
        state={**source_state, "attempt_count": attempts + 1, "last_attempted_at": checked_at},
        reason="source-specific retry handler executed",
        attempted=True,
        outcome="NO_ACTION",
        attempted_at=checked_at,
    )
    return replace(incident, remediation=remediation), status, build_recovery_plan_from_status(status), remediation, False


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
    status_export_root = _configured_status_root(config, repo_root)

    for dispatch in DISPATCH_ORDER:
        dispatch_config = config.dispatches[dispatch]
        if not dispatch_config.enabled:
            continue
        exported = _latest_exported_status(
            status_export_root,
            dispatch,
            now,
            config.status_freshness_threshold_minutes,
        )
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
            if observability_plan.action != "REBUILD_STATUS":
                incident, refreshed_status, refreshed_plan, remediation, stale_recovered = _auto_refresh_status_export_if_allowed(
                    incident=incident,
                    status=status,
                    dispatch_root=dispatch_config.runner_root,
                    repo_root=repo_root,
                    operator_root=operator_root,
                    config=config,
                    policy=policy,
                    checked_at=checked_at,
                    open_incidents=open_incidents,
                    allow_automatic_remediation=allow_automatic_remediation,
                )
                if remediation.get("attempted") is True:
                    status = refreshed_status
                    plan = refreshed_plan
                    underlying_classification = _classify_status(status)
                    if stale_recovered:
                        recommended = "NO_ACTION"
                    else:
                        recommended = _recommended_action(underlying_classification, status, plan) if underlying_classification else "NO_ACTION"
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
                    recovery_disposition=plan.disposition if remediation.get("attempted") is True else observability_plan.disposition,
                    recovery_action=plan.action if remediation.get("attempted") is True else observability_plan.action,
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
        incident, retried_status, retried_plan, remediation, retry_recovered = _auto_transient_network_retry_if_allowed(
            incident=incident,
            status=status,
            dispatch_root=dispatch_config.runner_root,
            repo_root=repo_root,
            operator_root=operator_root,
            config=config,
            policy=policy,
            checked_at=checked_at,
            allow_automatic_remediation=allow_automatic_remediation,
        )
        if remediation.get("attempted") is not True and not retry_recovered:
            incident, source_status, source_plan, source_remediation, source_recovered = _auto_source_transient_retry_if_allowed(
                incident=incident,
                status=status,
                dispatch_root=dispatch_config.runner_root,
                repo_root=repo_root,
                operator_root=operator_root,
                config=config,
                policy=policy,
                checked_at=checked_at,
                allow_automatic_remediation=allow_automatic_remediation,
            )
            if source_remediation:
                remediation = source_remediation
            if source_recovered:
                status = source_status
                plan = source_plan
                retry_recovered = True
        if remediation.get("attempted") is True:
            status = retried_status
            plan = retried_plan
            if retry_recovered:
                recommended = "NO_ACTION"
            else:
                classification = _classify_status(status) or classification
                recommended = _recommended_action(classification, status, plan)
        current_keys.add(key)
        incidents.append(incident)
        dispatch_results.append(
            DispatchResult(
                dispatch=dispatch,
                state="AUTO_RECOVERED" if retry_recovered else status.state,
                classification=classification.value,
                recommended_action=recommended,
                status_state=status.state,
                observed_date=status.date,
                recovery_disposition=plan.disposition,
                recovery_action=plan.action,
                incident_id=incident.incident_id,
                evidence=incident.evidence,
                exported_status=asdict(exported),
                notification_state="AUTO_RECOVERED" if retry_recovered else "RECOMMENDATION_ONLY",
                remediation=remediation,
            )
        )

    for key, existing in sorted(open_incidents.items()):
        if key not in current_keys:
            recovered = _recovered_incident(existing, checked_at, ["condition no longer present"])
            incidents.append(recovered)

    result = OperatorResult(checked_at=checked_at, dispatches=dispatch_results, incidents=incidents)
    enriched_incidents = [
        enrich_incident_for_autonomy(
            incident,
            dispatch_result=_dispatch_result_for(result, incident.dispatch),
            policy=policy,
        )
        for incident in result.incidents
    ]
    result = OperatorResult(
        checked_at=result.checked_at,
        dispatches=result.dispatches,
        incidents=enriched_incidents,
        autonomy_state=build_autonomy_state(
            OperatorResult(checked_at=result.checked_at, dispatches=result.dispatches, incidents=enriched_incidents),
            config=config,
            policy=policy,
            protected_source_head=_git_head(repo_root),
        ),
    )
    result = OperatorResult(
        checked_at=result.checked_at,
        dispatches=result.dispatches,
        incidents=result.incidents,
        notification=_build_notification_event(result, previous_incidents, policy),
        autonomy_state=result.autonomy_state,
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


def _engineering_active_root(operator_root: Path) -> Path:
    return _engineering_root(operator_root) / "active"


def _engineering_history_root(operator_root: Path) -> Path:
    return _engineering_root(operator_root) / "history"


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


def _safe_relative_path(path: str) -> bool:
    candidate = Path(path.replace("\\", "/"))
    return not candidate.is_absolute() and ".." not in candidate.parts


def _bounded_text_excerpt(path: Path, limit: int = DIAGNOSIS_EVIDENCE_EXCERPT_BYTES) -> str:
    data = path.read_bytes()[:limit]
    return _redact_diagnostic_text(data.decode("utf-8", errors="replace"))


def _evidence_type(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "json"
    if path.suffix.lower() in {".log", ".txt"}:
        return "text"
    return "artifact"


def _summarize_evidence_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = {
        key: payload.get(key)
        for key in (
            "task_key",
            "task_name",
            "status",
            "classification",
            "exit_code",
            "failure_stage",
            "scheduled_for",
            "started_at",
            "completed_at",
            "collection_health",
            "failed_source_count",
            "source_failure_summary",
            "selected_source_ids",
        )
        if key in payload
    }
    source_records = _source_failure_records_from_payload(payload)
    if source_records:
        summary["source_failure_records"] = source_records
    details = payload.get("details")
    if isinstance(details, dict):
        summary["details"] = {
            key: details.get(key)
            for key in ("failure_stage", "pipeline_run_id", "no_op_reason", "selected_event_count", "failed_source_count")
            if key in details
        }
    return summary


def _evidence_failure(summary: dict[str, Any]) -> bool:
    return summary.get("status") not in {None, "COMPLETE", "SUCCESS", "SAFE_NO_OP"} or summary.get("exit_code") not in {None, 0}


def _resolve_evidence_reference(reference: str, *, evidence_root: Path | None) -> Path | None:
    if not reference:
        return None
    candidate = Path(reference)
    if candidate.is_absolute():
        if evidence_root and _inside_path(candidate, evidence_root):
            return candidate
        return None
    if not _safe_relative_path(reference) or evidence_root is None:
        return None
    return evidence_root / candidate


def _append_evidence_entry(
    entries: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    *,
    reference: str,
    full_path: Path | None,
    entry_type: str | None = None,
    evidence_root: Path | None = None,
) -> None:
    entry: dict[str, Any] = {"path": reference}
    if entry_type:
        entry["type"] = entry_type
    if full_path and full_path.is_file():
        raw = full_path.read_bytes()
        entry.update(
            {
                "type": entry_type or _evidence_type(full_path),
                "sha256": hashlib.sha256(raw).hexdigest().upper(),
                "excerpt": _bounded_text_excerpt(full_path),
            }
        )
        if full_path.suffix.lower() == ".json":
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                artifact_refs = payload.get("artifact_refs")
                if isinstance(artifact_refs, dict):
                    for value in artifact_refs.values():
                        if isinstance(value, str) and _resolve_evidence_reference(value, evidence_root=evidence_root) is None:
                            entry["excerpt"] = entry["excerpt"].replace(value, "[REDACTED_UNSAFE_PATH]")
                            entry["excerpt"] = entry["excerpt"].replace(json.dumps(value)[1:-1], "[REDACTED_UNSAFE_PATH]")
                summary = _summarize_evidence_payload(payload)
                entry["summary"] = summary
                if _evidence_failure(summary):
                    failures.append({"path": reference, **summary})
    else:
        entry["error"] = "evidence file not available to Operator"
    entries.append(entry)


def _build_diagnosis_packet(
    incident: Incident,
    item: EngineeringWorkItem,
    *,
    eligibility_reason: str,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    evidence_entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for evidence_path in item.evidence[:DIAGNOSIS_EVIDENCE_FILE_LIMIT]:
        if not _safe_relative_path(evidence_path):
            evidence_entries.append({"path": evidence_path, "error": "unsafe evidence path"})
            continue
        if len(evidence_entries) >= DIAGNOSIS_EVIDENCE_FILE_LIMIT:
            break
        full_path = _resolve_evidence_reference(evidence_path, evidence_root=evidence_root)
        _append_evidence_entry(evidence_entries, failures, reference=evidence_path, full_path=full_path, evidence_root=evidence_root)
        if not full_path or not full_path.is_file() or full_path.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(full_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        artifact_refs = payload.get("artifact_refs")
        if not isinstance(artifact_refs, dict):
            continue
        for key, value in sorted(artifact_refs.items()):
            if len(evidence_entries) >= DIAGNOSIS_EVIDENCE_FILE_LIMIT:
                break
            if not isinstance(value, str):
                continue
            referenced_path = _resolve_evidence_reference(value, evidence_root=evidence_root)
            if referenced_path is None:
                continue
            _append_evidence_entry(
                evidence_entries,
                failures,
                reference=value,
                full_path=referenced_path,
                entry_type=f"artifact_ref:{key}",
                evidence_root=evidence_root,
            )
    return {
        "schema_version": "blue_fern_operator_diagnosis_v2",
        "work_id": item.work_id,
        "incident_id": incident.incident_id,
        "dispatch": incident.dispatch,
        "classification": incident.classification,
        "affected_date": incident.affected_date,
        "status": {
            "state": incident.status_state,
            "classification": incident.classification,
            "recommended_action": incident.recommended_action,
        },
        "recovery_plan": {
            "disposition": incident.recovery_disposition,
            "action": incident.recovery_action,
        },
        "eligibility_reason": eligibility_reason,
        "failures": failures,
        "evidence": evidence_entries,
        "source_artifact_paths": item.evidence,
        "allowed_paths": item.allowed_paths,
        "validation_targets": item.tests_required,
        "forbidden_actions": [
            "publish",
            "sync_pages",
            "collection_replay",
            "scheduler_mutation",
            "production_runner_patch",
            "merge_pr",
        ],
    }


def _has_existing_engineering_work(operator_root: Path, incident_id: str) -> dict[str, Any] | None:
    root = _engineering_root(operator_root)
    for path in [*sorted((root / "active").glob("*/work-item.json")), *sorted((root / "history").glob("*/work-item.json"))]:
        payload = _read_json(path)
        if payload and payload.get("incident_id") == incident_id and payload.get("state") != "FAILED":
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
    active_root = _engineering_work_root(operator_root, item.work_id)
    _write_json(active_root / "work-item.json", item.to_payload())
    _write_json(active_root / "diagnosis.json", diagnosis)


def _engineering_work_root(operator_root: Path, work_id: str) -> Path:
    return _engineering_active_root(operator_root) / work_id


def _engineering_history_work_root(operator_root: Path, work_id: str) -> Path:
    return _engineering_history_root(operator_root) / work_id


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


def _load_engineering_work_item_anywhere(operator_root: Path, work_id: str) -> EngineeringWorkItem:
    for root in (_engineering_active_root(operator_root), _engineering_history_root(operator_root)):
        path = root / work_id / "work-item.json"
        payload = _read_json(path)
        if payload:
            return EngineeringWorkItem(**{key: payload[key] for key in EngineeringWorkItem.__dataclass_fields__ if key in payload})
    raise FileNotFoundError(_engineering_work_item_path(operator_root, work_id))


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
    dispatch_results = {row.dispatch: row for row in result.dispatches}
    for incident in result.incidents:
        eligible, reason = _is_engineering_eligible(incident, policy)
        if not eligible:
            continue
        existing = _has_existing_engineering_work(operator_root, incident.incident_id)
        if existing:
            if existing.get("state") == EngineeringState.CLOSED.value:
                continue
            items.append(EngineeringWorkItem(**{key: existing[key] for key in EngineeringWorkItem.__dataclass_fields__ if key in existing}))
            continue
        item = _build_engineering_work_item(
            incident,
            operator_root=operator_root,
            worktree_root=worktree_root,
            base_sha=base_sha or _git_head(ROOT),
        )
        dispatch_result = dispatch_results.get(incident.dispatch)
        exported_status = dispatch_result.exported_status if dispatch_result else {}
        exported_status = exported_status or {}
        evidence_root = Path(exported_status.get("source_root", "")) if exported_status.get("source_root") else None
        diagnosis = _build_diagnosis_packet(
            incident,
            item,
            eligibility_reason=reason,
            evidence_root=evidence_root,
        )
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


def _run_command(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> EngineeringCommandResult:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    completed = subprocess.run(args, cwd=cwd, env=run_env, text=True, capture_output=True, check=False)
    return EngineeringCommandResult(completed.returncode, completed.stdout, completed.stderr)


def _git_stdout(runner: Any, args: list[str], *, cwd: Path) -> str | None:
    result = runner(["git", *args], cwd=cwd)
    if not result.ok:
        return None
    return result.stdout.strip()


def _git_ok(runner: Any, args: list[str], *, cwd: Path) -> bool:
    return runner(["git", *args], cwd=cwd).ok


def _normalize_git_path(path: str) -> str:
    return path.strip().replace("\\", "/").rstrip("/")


def _runner_status_paths(status_text: str) -> tuple[list[str], list[str]]:
    tracked: list[str] = []
    untracked: list[str] = []
    for raw_line in status_text.splitlines():
        if not raw_line.strip():
            continue
        code = raw_line[:2]
        path = _normalize_git_path(raw_line[3:] if len(raw_line) > 3 else raw_line)
        if code == "??":
            untracked.append(path)
        else:
            tracked.append(path)
    return sorted(tracked), sorted(untracked)


def _sanctioned_untracked_patterns(dispatch: str) -> list[str]:
    return [
        ".venv",
        ".pytest_cache",
        "__pycache__",
        f"data/dispatches/{dispatch}/collection-runs",
        f"data/dispatches/{dispatch}/queue-runs",
        f"data/dispatches/{dispatch}/review/candidate-registry.json",
        f"data/dispatches/{dispatch}/review/current-duplicates.json",
        f"data/dispatches/{dispatch}/review/current-exclusions.json",
        f"data/dispatches/{dispatch}/review/current-failed-extractions.json",
        f"data/dispatches/{dispatch}/review/current-manual-review.json",
        f"data/dispatches/{dispatch}/review/current-review-backlog.json",
        f"data/dispatches/{dispatch}/review/current-review-queue.json",
        f"data/dispatches/{dispatch}/review/effective-date-follow-up-state.json",
        f"logs/{dispatch}",
    ]


def _path_matches_prefix(path: str, prefix: str) -> bool:
    normalized = _normalize_git_path(path)
    normalized_prefix = _normalize_git_path(prefix)
    return normalized == normalized_prefix or normalized.startswith(normalized_prefix + "/")


def _unsanctioned_untracked(dispatch: str, untracked: Iterable[str]) -> list[str]:
    patterns = _sanctioned_untracked_patterns(dispatch)
    return sorted(
        path
        for path in untracked
        if not any(_path_matches_prefix(path, pattern) for pattern in patterns)
    )


def _untracked_incoming_overlap(untracked: Iterable[str], incoming: Iterable[str]) -> list[str]:
    incoming_paths = [_normalize_git_path(path) for path in incoming if path]
    overlaps: set[str] = set()
    for raw_untracked in untracked:
        untracked_path = _normalize_git_path(raw_untracked)
        for incoming_path in incoming_paths:
            if incoming_path == untracked_path or incoming_path.startswith(untracked_path + "/"):
                overlaps.add(incoming_path)
    return sorted(overlaps)


def _runner_roll_forward_safety(
    dispatch: str,
    runner_root: Path,
    *,
    target_ref: str = "origin/add/pages-repo-default",
    runner: Any = _run_command,
) -> dict[str, Any]:
    status = runner(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=runner_root)
    tracked_dirty, untracked = _runner_status_paths(status.stdout if status.ok else "")
    unsanctioned = _unsanctioned_untracked(dispatch, untracked)
    branch = _git_stdout(runner, ["branch", "--show-current"], cwd=runner_root)
    before_head = _git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root)
    target_head = _git_stdout(runner, ["rev-parse", target_ref], cwd=runner_root)
    ancestor = bool(target_head and _git_ok(runner, ["merge-base", "--is-ancestor", "HEAD", target_ref], cwd=runner_root))
    up_to_date = bool(before_head and target_head and before_head == target_head)
    incoming_text = _git_stdout(runner, ["diff", "--name-only", f"HEAD..{target_ref}"], cwd=runner_root) or ""
    incoming_paths = sorted(_normalize_git_path(line) for line in incoming_text.splitlines() if line.strip())
    overlaps = _untracked_incoming_overlap(untracked, incoming_paths)
    validation_capability = _runner_validation_capability(runner_root)
    safe = (
        status.ok
        and branch == "add/pages-repo-default"
        and bool(before_head)
        and bool(target_head)
        and not up_to_date
        and not tracked_dirty
        and not unsanctioned
        and ancestor
        and not overlaps
        and validation_capability["ok"]
    )
    return {
        "safe": safe,
        "runner_root": str(runner_root),
        "branch": branch,
        "before_head": before_head,
        "target_ref": target_ref,
        "target_head": target_head,
        "runner_is_behind": bool(before_head and target_head and before_head != target_head),
        "current_head_is_ancestor": ancestor,
        "tracked_dirty_paths": tracked_dirty,
        "untracked_paths": sorted(untracked),
        "unsanctioned_untracked_paths": unsanctioned,
        "incoming_tracked_paths": incoming_paths,
        "untracked_incoming_overlaps": overlaps,
        "sanctioned_runtime_untracked_preserved": not unsanctioned,
        "validation_capability": validation_capability,
    }


def _runner_python(runner_root: Path) -> Path:
    return runner_root / ".venv" / "Scripts" / "python.exe"


def _runner_validation_capability(runner_root: Path) -> dict[str, Any]:
    python_executable = _runner_python(runner_root)
    preflight = runner_root / "scripts" / "preflight_repo_state.py"
    doctor = runner_root / "scripts" / "doctor.py"
    preflight_argv = [str(python_executable), "scripts/preflight_repo_state.py", "--source-repo", "."]
    doctor_argv = [str(python_executable), "scripts/doctor.py"]
    ok = python_executable.is_file() and preflight.is_file() and doctor.is_file()
    return {
        "ok": ok,
        "python_executable": str(python_executable),
        "python_exists": python_executable.is_file(),
        "preflight_exists": preflight.is_file(),
        "doctor_exists": doctor.is_file(),
        "preflight_argv": preflight_argv,
        "doctor_argv": doctor_argv,
        "doctor_environment_overrides": {"PYTHONPATH": "src"},
    }


def _lower_values(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"{key}={_lower_values(row)}" for key, row in sorted(value.items()))
    if isinstance(value, list):
        return "\n".join(_lower_values(row) for row in value)
    return str(value).lower()


def _int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_context_from_mapping(
    payload: dict[str, Any],
    *,
    task_failures: list[dict[str, Any]],
    failure_stages: set[str],
    exit_codes: set[int],
    checkout_failures: set[str],
    source_failures: set[str],
    code_exception_markers: set[str],
    source_failure_records: list[dict[str, Any]] | None = None,
) -> None:
    text = _lower_values(payload)
    task_key = str(payload.get("task_key") or payload.get("task_name") or "")
    status = str(payload.get("status") or payload.get("aggregate_status") or "")
    classification = str(payload.get("classification") or "")
    if status in {"FAILED", "DEGRADED"} or classification in {"failure", "partial_success"}:
        task_failures.append({key: payload.get(key) for key in ("task_key", "task_name", "status", "classification") if payload.get(key) is not None})
    for container in (payload, payload.get("details") if isinstance(payload.get("details"), dict) else {}):
        stage = container.get("failure_stage") if isinstance(container, dict) else None
        if stage:
            failure_stages.add(str(stage))
        exit_code = _int_value(container.get("exit_code")) if isinstance(container, dict) else None
        if exit_code is not None:
            exit_codes.add(exit_code)
    failed_source_count = _int_value(payload.get("failed_source_count"))
    if failed_source_count and failed_source_count > 0:
        source_failures.add(f"failed_source_count={failed_source_count}")
    for record in _source_failure_records_from_payload(payload):
        if source_failure_records is not None:
            source_failure_records.append(record)
        source_failures.add(str(record.get("source_id") or "source failure"))
    task_statuses = payload.get("task_statuses")
    if isinstance(task_statuses, dict):
        for key, value in task_statuses.items():
            if str(value).upper() in {"FAILED", "DEGRADED", "PARTIAL_SUCCESS"}:
                task_failures.append({"task_key": str(key), "status": str(value)})
    if "verify_checkout" in text or ("dirty" in text and "checkout" in text):
        checkout_failures.add(task_key or "checkout failure")
    source_markers = ("failed source", "failed_source", "failed-extractions", "source failure", "feed", "rss", "network", "timeout", "tls")
    if any(marker in text for marker in source_markers):
        source_failures.add(task_key or "source failure")
    code_markers = ("traceback", "typeerror", "valueerror", "assertionerror", "modulenotfounderror", "nameerror")
    for marker in code_markers:
        if marker in text:
            code_exception_markers.add(marker)


def build_remediation_evidence_context(
    incident: Incident,
    *,
    runner_root: Path,
    current_status: DispatchStatus | None = None,
    evidence_limit: int = 8,
) -> RemediationEvidenceContext:
    task_failures: list[dict[str, Any]] = []
    failure_stages: set[str] = set()
    exit_codes: set[int] = set()
    checkout_failures: set[str] = set()
    source_failures: set[str] = set()
    source_failure_records: list[dict[str, Any]] = []
    code_exception_markers: set[str] = set()
    evidence_entries: list[dict[str, Any]] = []
    evidence_complete = True
    if current_status is not None:
        _append_context_from_mapping(
            current_status.to_json_payload(),
            task_failures=task_failures,
            failure_stages=failure_stages,
            exit_codes=exit_codes,
            checkout_failures=checkout_failures,
            source_failures=source_failures,
            code_exception_markers=code_exception_markers,
            source_failure_records=source_failure_records,
        )
        if isinstance(current_status.details, dict):
            _append_context_from_mapping(
                current_status.details,
                task_failures=task_failures,
                failure_stages=failure_stages,
                exit_codes=exit_codes,
                checkout_failures=checkout_failures,
                source_failures=source_failures,
                code_exception_markers=code_exception_markers,
                source_failure_records=source_failure_records,
            )
    if len(incident.evidence) > evidence_limit:
        evidence_complete = False
    for reference in incident.evidence[:evidence_limit]:
        full_path = _resolve_evidence_reference(reference, evidence_root=runner_root)
        entry: dict[str, Any] = {"path": reference}
        if full_path and full_path.is_file():
            raw = full_path.read_bytes()
            entry["sha256"] = hashlib.sha256(raw).hexdigest().upper()
            entry["excerpt"] = _bounded_text_excerpt(full_path)
            if full_path.suffix.lower() == ".json":
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    payload = {}
                    evidence_complete = False
                if isinstance(payload, dict):
                    summary = _summarize_evidence_payload(payload)
                    entry["summary"] = summary
                    _append_context_from_mapping(
                        payload,
                        task_failures=task_failures,
                        failure_stages=failure_stages,
                        exit_codes=exit_codes,
                        checkout_failures=checkout_failures,
                        source_failures=source_failures,
                        code_exception_markers=code_exception_markers,
                        source_failure_records=source_failure_records,
                    )
                    _append_context_from_mapping(
                        summary,
                        task_failures=task_failures,
                        failure_stages=failure_stages,
                        exit_codes=exit_codes,
                        checkout_failures=checkout_failures,
                        source_failures=source_failures,
                        code_exception_markers=code_exception_markers,
                        source_failure_records=source_failure_records,
                    )
            else:
                _append_context_from_mapping(
                    {"excerpt": entry["excerpt"]},
                    task_failures=task_failures,
                    failure_stages=failure_stages,
                    exit_codes=exit_codes,
                    checkout_failures=checkout_failures,
                    source_failures=source_failures,
                    code_exception_markers=code_exception_markers,
                    source_failure_records=source_failure_records,
                )
        elif full_path is None:
            entry["error"] = "evidence reference outside runner root or unsafe"
            evidence_complete = False
        else:
            entry["error"] = "evidence file not available"
            evidence_complete = False
        evidence_entries.append(entry)
    return RemediationEvidenceContext(
        dispatch=incident.dispatch,
        affected_date=incident.affected_date,
        status_state=current_status.state if current_status else incident.status_state,
        next_action=current_status.next_action if current_status else incident.recommended_action,
        recovery_action=incident.recovery_action,
        task_failures=task_failures,
        failure_stages=sorted(failure_stages),
        exit_codes=sorted(exit_codes),
        checkout_failures=sorted(checkout_failures),
        source_failures=sorted(source_failures),
        source_failure_records=list({json.dumps(record, sort_keys=True): record for record in source_failure_records}.values())[:25],
        code_exception_markers=sorted(code_exception_markers),
        evidence_entries=evidence_entries,
        evidence_complete=evidence_complete,
    )


def _context_supports_checkout_hygiene(context: RemediationEvidenceContext) -> bool:
    return bool(context.checkout_failures or "verify_checkout" in context.failure_stages)


def _context_supports_source_failure(incident: Incident, context: RemediationEvidenceContext) -> bool:
    return incident.recovery_action == "INVESTIGATE_FAILED_SOURCES" or bool(context.source_failures)


def _context_supports_code_defect(context: RemediationEvidenceContext) -> bool:
    return bool(context.code_exception_markers) and not _context_supports_checkout_hygiene(context) and not context.source_failures


def _context_blob(context: RemediationEvidenceContext) -> str:
    evidence_diagnostics = [
        {
            key: entry.get(key)
            for key in ("summary", "excerpt", "error")
            if entry.get(key) is not None
        }
        for entry in context.evidence_entries
    ]
    return _lower_values(
        {
            "task_failures": context.task_failures,
            "failure_stages": context.failure_stages,
            "exit_codes": context.exit_codes,
            "checkout_failures": context.checkout_failures,
            "source_failures": context.source_failures,
            "code_exception_markers": context.code_exception_markers,
            "evidence_diagnostics": evidence_diagnostics,
            "evidence_complete": context.evidence_complete,
        }
    )


def _root_cause_from_context(incident: Incident, context: RemediationEvidenceContext) -> str:
    blob = _context_blob(context)
    record_classes = {str(record.get("failure_classification") or "") for record in context.source_failure_records}
    if record_classes and record_classes <= {RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value}:
        return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    if RootCauseClassification.INTERNAL_FAILURE.value in record_classes and RootCauseClassification.SOURCE_TRANSIENT.value not in record_classes:
        return RootCauseClassification.INTERNAL_FAILURE.value
    if RootCauseClassification.SOURCE_TRANSIENT.value in record_classes:
        return RootCauseClassification.SOURCE_TRANSIENT.value
    if _source_external_restriction_markers(blob):
        return RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value
    if context.code_exception_markers:
        return RootCauseClassification.INTERNAL_FAILURE.value
    if _transient_network_markers(blob):
        if len(context.source_failures) <= 1 and _source_transient_markers(blob) and "github" not in blob:
            return RootCauseClassification.SOURCE_TRANSIENT.value
        return RootCauseClassification.TRANSIENT_NETWORK.value
    if _source_transient_markers(blob):
        return RootCauseClassification.SOURCE_TRANSIENT.value
    return RootCauseClassification.UNKNOWN.value


def _task_keys_from_context(context: RemediationEvidenceContext) -> set[str]:
    keys: set[str] = set()
    for row in context.task_failures:
        for key in ("task_key", "task_name"):
            value = str(row.get(key) or "").strip()
            if value:
                keys.add(value)
    return keys


def _transient_retry_handler(incident: Incident, context: RemediationEvidenceContext) -> dict[str, Any] | None:
    row = TRANSIENT_RETRY_TASKS.get(incident.dispatch)
    if not row:
        return None
    task_keys = _task_keys_from_context(context)
    allowed = set(row["task_keys"])
    if task_keys and not task_keys.intersection(allowed):
        return None
    if incident.dispatch == "gaza":
        return None
    return {
        "dispatch": incident.dispatch,
        "task_keys": sorted(task_keys.intersection(allowed) or allowed),
        "wrapper": str(row["wrapper"]),
        "date_argument": row.get("date_argument"),
        "public_side_effects": bool(row.get("public_side_effects")),
    }


def _transient_retry_command(handler: dict[str, Any], *, runner_root: Path, date: str, incident_id: str, attempt_count: int) -> list[str]:
    wrapper = runner_root / str(handler["wrapper"])
    python_executable = _runner_python(runner_root)
    run_id = f"operator-retry-{incident_id}-{attempt_count + 1}"
    command = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(wrapper),
        "-RepositoryRoot",
        str(runner_root),
        "-PythonExecutable",
        str(python_executable),
        "-SourceBranch",
        "add/pages-repo-default",
    ]
    date_argument = handler.get("date_argument")
    if date_argument:
        command.extend([str(date_argument), date])
    if handler["dispatch"] in {"care-line", "ice"}:
        command.extend(["-RunId", run_id])
    return command


def _runner_transient_retry_safety(
    dispatch: str,
    runner_root: Path,
    *,
    protected_head: str | None,
    runner: Any = _run_command,
) -> dict[str, Any]:
    status = runner(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=runner_root)
    tracked_dirty, untracked = _runner_status_paths(status.stdout if status.ok else "")
    unsanctioned = _unsanctioned_untracked(dispatch, untracked)
    branch = _git_stdout(runner, ["branch", "--show-current"], cwd=runner_root)
    head = _git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root)
    validation_capability = _runner_validation_capability(runner_root)
    preflight: dict[str, Any] = {"attempted": False, "ok": False}
    if validation_capability["ok"]:
        preflight_result = runner(validation_capability["preflight_argv"], cwd=runner_root)
        preflight = {
            "attempted": True,
            "ok": preflight_result.ok,
            "exit_code": preflight_result.exit_code,
            "stdout_tail": preflight_result.stdout[-1000:],
            "stderr_tail": preflight_result.stderr[-1000:],
        }
    safe = (
        status.ok
        and branch == "add/pages-repo-default"
        and bool(head)
        and bool(protected_head)
        and head == protected_head
        and not tracked_dirty
        and not unsanctioned
        and validation_capability["ok"]
        and preflight.get("ok") is True
    )
    return {
        "safe": safe,
        "runner_root": str(runner_root),
        "branch": branch,
        "head": head,
        "protected_head": protected_head,
        "tracked_dirty_paths": tracked_dirty,
        "untracked_paths": sorted(untracked),
        "unsanctioned_untracked_paths": unsanctioned,
        "validation_capability": validation_capability,
        "preflight": preflight,
    }


def _connectivity_recovered_proof(runner_root: Path, *, runner: Any = _run_command) -> dict[str, Any]:
    dns = runner(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "[System.Net.Dns]::GetHostEntry('github.com') | Out-Null"],
        cwd=runner_root,
    )
    git_probe = runner(TRANSIENT_NETWORK_DEPENDENCIES["github"]["read_only_probe"], cwd=runner_root)
    return {
        "attempted": True,
        "dependency": "github",
        "host": TRANSIENT_NETWORK_DEPENDENCIES["github"]["host"],
        "dns_resolution": {
            "ok": dns.ok,
            "exit_code": dns.exit_code,
            "stdout_tail": dns.stdout[-500:],
            "stderr_tail": dns.stderr[-500:],
        },
        "read_only_git_probe": {
            "ok": git_probe.ok,
            "exit_code": git_probe.exit_code,
            "stdout_tail": git_probe.stdout[-500:],
            "stderr_tail": git_probe.stderr[-500:],
        },
        "ok": dns.ok and git_probe.ok,
    }


def _transient_retry_backoff_state(incident: Incident, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utc_now()
    remediation = incident.remediation if isinstance(incident.remediation, dict) else {}
    attempts = _attempt_count(remediation, TRANSIENT_NETWORK_RETRY_ACTION)
    attempted_at = remediation.get("attempted_at")
    parsed_attempted_at = _parse_time(str(attempted_at)) if attempted_at else None
    next_eligible_at = parsed_attempted_at + timedelta(minutes=TRANSIENT_NETWORK_BACKOFF_MINUTES) if parsed_attempted_at else None
    eligible = next_eligible_at is None or now >= next_eligible_at
    return {
        "attempt_count": attempts,
        "max_attempts": _retry_limit(TRANSIENT_NETWORK_RETRY_ACTION),
        "last_attempted_at": attempted_at,
        "minimum_backoff_minutes": TRANSIENT_NETWORK_BACKOFF_MINUTES,
        "next_eligible_at": _format_time(next_eligible_at) if next_eligible_at else None,
        "backoff_elapsed": eligible,
        "retry_budget_remaining": attempts < _retry_limit(TRANSIENT_NETWORK_RETRY_ACTION),
    }


def _source_retry_candidates(context: RemediationEvidenceContext) -> list[dict[str, Any]]:
    candidates = [
        record
        for record in context.source_failure_records
        if record.get("failure_classification") == RootCauseClassification.SOURCE_TRANSIENT.value
    ]
    return sorted(candidates, key=lambda row: str(row.get("source_id") or ""))[:10]


def _source_retry_handler(dispatch: str) -> dict[str, Any]:
    row = dict(SOURCE_TRANSIENT_RETRY_HANDLERS.get(dispatch) or {})
    row.setdefault("enabled", False)
    row.setdefault("reason", "dispatch has no registered source-isolated retry handler")
    row.setdefault("public_side_effects", False)
    row["dispatch"] = dispatch
    return row


def _source_retry_state(
    incident: Incident,
    context: RemediationEvidenceContext,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    candidates = _source_retry_candidates(context)
    existing = {}
    remediation = incident.remediation if isinstance(incident.remediation, dict) else {}
    if isinstance(remediation.get("source_retry_state"), dict):
        existing = dict(remediation["source_retry_state"])
    if not candidates:
        return {
            "source_retry_key": existing.get("source_retry_key"),
            "attempt_count": _attempt_count(remediation, SOURCE_TRANSIENT_RETRY_ACTION),
            "max_attempts": _retry_limit(SOURCE_TRANSIENT_RETRY_ACTION),
            "eligible": False,
            "terminal_classification": "no_source_transient_candidate",
        }
    record = candidates[0]
    logical_date = str(record.get("logical_date") or incident.affected_date or "")
    source_retry_key = _source_retry_key_from_record(record, fallback_date=logical_date)
    attempt_count = int(existing.get("attempt_count") or _attempt_count(remediation, SOURCE_TRANSIENT_RETRY_ACTION) or 0)
    retry_after_minutes = _source_retry_after_minutes(record.get("retry_after"), now=now)
    minimum_backoff = retry_after_minutes or SOURCE_TRANSIENT_BACKOFF_MINUTES
    last_attempted_at = existing.get("last_attempted_at") or remediation.get("attempted_at")
    parsed_attempted_at = _parse_time(str(last_attempted_at)) if last_attempted_at else None
    next_eligible = parsed_attempted_at + timedelta(minutes=minimum_backoff) if parsed_attempted_at else now
    terminal = None
    if attempt_count >= _retry_limit(SOURCE_TRANSIENT_RETRY_ACTION):
        terminal = "retry_limit_reached"
    elif record.get("failure_classification") != RootCauseClassification.SOURCE_TRANSIENT.value:
        terminal = str(record.get("failure_classification") or "not_source_transient")
    return {
        "source_retry_key": source_retry_key,
        "dispatch": incident.dispatch,
        "source_id": str(record.get("source_id") or ""),
        "logical_date": logical_date,
        "logical_run_id": str(record.get("logical_run_id") or record.get("run_id") or record.get("pipeline_run_id") or ""),
        "failure_class": str(record.get("failure_class") or record.get("failure_classification") or ""),
        "failure_classification": str(record.get("failure_classification") or ""),
        "first_failure_at": existing.get("first_failure_at") or incident.detected_at,
        "latest_failure_at": incident.updated_at or incident.detected_at,
        "attempt_count": attempt_count,
        "max_attempts": _retry_limit(SOURCE_TRANSIENT_RETRY_ACTION),
        "last_attempted_at": last_attempted_at,
        "next_eligible_at": _format_time(next_eligible) if next_eligible else None,
        "minimum_backoff_minutes": minimum_backoff,
        "retry_after_minutes": retry_after_minutes,
        "backoff_elapsed": next_eligible is None or now >= next_eligible,
        "retry_budget_remaining": attempt_count < _retry_limit(SOURCE_TRANSIENT_RETRY_ACTION),
        "terminal_classification": terminal,
        "last_successful_fetch": existing.get("last_successful_fetch") or record.get("last_successful_fetch"),
        "logical_run_identity": {
            "dispatch": incident.dispatch,
            "date": logical_date,
            "source_id": str(record.get("source_id") or ""),
            "run_id": str(record.get("logical_run_id") or record.get("run_id") or record.get("pipeline_run_id") or ""),
            "failure_class": str(record.get("failure_class") or record.get("failure_classification") or ""),
        },
    }


def _source_retry_remediation_payload(
    *,
    state: dict[str, Any],
    reason: str,
    attempted: bool = False,
    outcome: str = "NOT_ATTEMPTED",
    attempted_at: str | None = None,
) -> dict[str, Any]:
    payload = _remediation_payload(
        attempted=attempted,
        action=SOURCE_TRANSIENT_RETRY_ACTION,
        outcome=outcome,
        attempted_at=attempted_at,
        reason=reason,
        attempt_count=int(state.get("attempt_count") or 0),
    )
    payload["source_retry_state"] = state
    payload["notification_suppressed"] = outcome == "NOT_ATTEMPTED" and not state.get("terminal_classification")
    return payload


def _terminal_retry_proof(status: DispatchStatus) -> bool:
    if status.next_action in {"PUBLISH_APPROVED_RELEASE", "PUBLISH_NO_UPDATE", "REPLAY_COLLECTION"}:
        return False
    if status.public_state not in {"VERIFIED", "NOT_VERIFIED", "UNKNOWN"}:
        return False
    return status.state in TERMINAL_NO_ACTION_STATES and status.next_action not in {"INVESTIGATE_COLLECTION", "UNKNOWN_REQUIRES_OPERATOR"}


def _current_condition_for(incident: Incident, current_status: DispatchStatus | None) -> str:
    if current_status is not None:
        return current_status.state
    return incident.status_state


def _refresh_status_supported(dispatch: str, current_status: DispatchStatus | None) -> tuple[bool, str]:
    if dispatch in {"gaza", "food-line", "ice"}:
        return True, "existing external-status builder is available"
    if dispatch == "care-line":
        if current_status is not None and current_status.evidence:
            return True, "Care external-status builder can use current operational-health receipts"
        return False, "Care refresh requires authoritative scheduler receipt evidence"
    return False, "dispatch does not support status export refresh"


def _current_dispatch_ops_rebuild_available(current_status: DispatchStatus | None) -> tuple[bool, RecoveryPlan | None]:
    if current_status is None:
        return False, None
    plan = build_recovery_plan_from_status(current_status)
    return plan.disposition == "PLAN_AVAILABLE" and plan.action == "REBUILD_STATUS", plan


def _status_export_scope(dispatch: str, date: str | None) -> list[str]:
    return [
        f"ops/status/{dispatch}/latest.json",
        f"ops/status/{dispatch}/history/{date or '<date>'}.json",
    ]


def _refresh_status_export_scope(dispatch: str, date: str | None) -> list[str]:
    day = date or "<date>"
    paths = [
        "ops/status/food-line/latest.json",
        f"ops/status/food-line/history/{day}.json",
    ]
    if dispatch == "care-line":
        paths.extend(["ops/status/care-line/latest.json", f"ops/status/care-line/history/{day}.json"])
    if dispatch == "gaza":
        paths.extend(["ops/status/gaza/latest.json", f"ops/status/gaza/history/{day}.json"])
    if dispatch == "ice":
        paths.extend(["ops/status/ice/latest.json", f"ops/status/ice/history/{day}.json"])
    paths.append("ops/status/system/latest.json")
    return sorted(dict.fromkeys(paths))


def build_remediation_action_plan(
    incident: Incident,
    *,
    runner_root: Path,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    current_status: DispatchStatus | None = None,
    status_root: Path | None = None,
    policy: RemediationPolicy | None = None,
    runner: Any = _run_command,
) -> RemediationActionPlan:
    condition = _current_condition_for(incident, current_status)
    evidence = sorted(dict.fromkeys(incident.evidence))
    context = build_remediation_evidence_context(incident, runner_root=runner_root, current_status=current_status)
    safety_checks: dict[str, Any] = {}
    action = "NO_ACTION"
    reason = "no remediation route matched"
    executable = False
    mutation_scope: list[str] = []

    if incident.classification in {Classification.STALE_OBSERVABILITY.value, Classification.STATUS_EXPORT_PROBLEM.value} or incident.recovery_action == "REBUILD_STATUS":
        rebuild_available, dispatch_ops_plan = _current_dispatch_ops_rebuild_available(current_status)
        if rebuild_available:
            action = "REBUILD_STATUS"
            reason = "dispatch_ops currently proves REBUILD_STATUS is available"
            executable = True
            mutation_scope = _status_export_scope(incident.dispatch, incident.affected_date)
        elif incident.classification == Classification.STALE_OBSERVABILITY.value:
            supported, support_reason = _refresh_status_supported(incident.dispatch, current_status)
            if supported:
                destination_root = (status_root or _operator_code_root(operator_root)).resolve()
                roots_distinct = destination_root not in {_operator_code_root(operator_root), runner_root.resolve()}
                action = "REFRESH_STATUS_EXPORT"
                reason = (
                    "status export surface is stale and can be refreshed from current durable runner evidence"
                    if roots_distinct
                    else "status destination root must be distinct from Operator source and production runner roots"
                )
                executable = roots_distinct
                mutation_scope = _refresh_status_export_scope(incident.dispatch, incident.affected_date) if roots_distinct else []
                safety_checks = {
                    "operator_root": str(operator_root.resolve()),
                    "source_runner_root": str(runner_root.resolve()),
                    "status_destination_root": str(destination_root),
                    "roots_distinct": roots_distinct,
                    "refresh_supported": True,
                    "refresh_support_reason": support_reason,
                    "dispatch_ops_action": dispatch_ops_plan.action if dispatch_ops_plan else None,
                    "structured_evidence": context.to_payload(),
                    "public_side_effects": False,
                    "scheduler_changes": False,
                    "collection_rerun": False,
                    "editorial_mutation": False,
                    "merge_pr": False,
                }
            else:
                action = "INVESTIGATE_STATUS_EXPORT"
                reason = support_reason
                executable = False
                destination_root = (status_root or _operator_code_root(operator_root)).resolve()
                safety_checks = {
                    "operator_root": str(operator_root.resolve()),
                    "source_runner_root": str(runner_root.resolve()),
                    "status_destination_root": str(destination_root),
                    "roots_distinct": destination_root not in {_operator_code_root(operator_root), runner_root.resolve()},
                    "refresh_supported": False,
                    "refresh_support_reason": support_reason,
                    "dispatch_ops_action": dispatch_ops_plan.action if dispatch_ops_plan else None,
                    "structured_evidence": context.to_payload(),
                    "public_side_effects": False,
                    "scheduler_changes": False,
                    "collection_rerun": False,
                    "editorial_mutation": False,
                    "merge_pr": False,
                }
        else:
            action = "INVESTIGATE_STATUS_EXPORT"
            reason = "dispatch_ops does not currently prove REBUILD_STATUS and this is not stale observability"
            executable = False
    elif incident.recovery_action == "VERIFY_PUBLIC_STATE" or incident.classification == Classification.PUBLIC_STATE_UNVERIFIED.value:
        action = "VERIFY_PUBLIC_STATE"
        reason = "public state requires durable verification"
        executable = True
        mutation_scope = []
    elif _has_existing_engineering_work(operator_root, incident.incident_id) and _context_supports_checkout_hygiene(context):
        action = "CLOSE_NON_CODE_INCIDENT"
        reason = "existing engineering item is tied to operational checkout hygiene rather than code repair"
        mutation_scope = ["ops/operator/engineering/active/<work-id>", "ops/operator/engineering/history/<work-id>", "ops/operator/engineering/audit/<date>/<work-id>-closed.json"]
    elif _context_supports_checkout_hygiene(context):
        safety_checks = _runner_roll_forward_safety(incident.dispatch, runner_root, runner=runner)
        if safety_checks.get("safe"):
            action = "RUNNER_ROLL_FORWARD"
            reason = "runner is behind approved base and checkout hygiene can be remediated by fast-forward rollout"
            executable = True
            mutation_scope = ["production runner Git HEAD only", "Python __pycache__ from validation if needed"]
        else:
            action = "RUNNER_ROLL_FORWARD"
            reason = "runner rollout is the bounded operational remedy, but safety checks must pass before execution"
            executable = False
            mutation_scope = ["production runner Git HEAD only"]
    elif _context_supports_source_failure(incident, context) or _root_cause_from_context(incident, context) in {
        RootCauseClassification.TRANSIENT_NETWORK.value,
        RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value,
        RootCauseClassification.SOURCE_TRANSIENT.value,
        RootCauseClassification.INTERNAL_FAILURE.value,
    }:
        root_cause = _root_cause_from_context(incident, context)
        handler = _transient_retry_handler(incident, context)
        if root_cause == RootCauseClassification.TRANSIENT_NETWORK.value and handler:
            protected_head = _git_head(_operator_code_root(operator_root))
            retry_state = _transient_retry_backoff_state(incident)
            runner_safety = _runner_transient_retry_safety(incident.dispatch, runner_root, protected_head=protected_head, runner=runner)
            connectivity = _connectivity_recovered_proof(runner_root, runner=runner)
            action = TRANSIENT_NETWORK_RETRY_ACTION
            reason = "transient network failure is eligible for canonical non-public task retry"
            executable = (
                policy is not None
                and policy.mode_for(TRANSIENT_NETWORK_RETRY_ACTION) == "automatic"
                and runner_safety["safe"]
                and connectivity["ok"]
                and retry_state["retry_budget_remaining"]
                and retry_state["backoff_elapsed"]
            )
            mutation_scope = [
                "non-public collection or monitor runtime receipts",
                "ops/status/<dispatch>/latest.json after status refresh",
                "ops/operator/remediation/receipts/<date>/<receipt>.json",
            ]
            safety_checks = {
                "structured_evidence": context.to_payload(),
                "root_cause_classification": root_cause,
                "handler": handler,
                "runner_safety": runner_safety,
                "connectivity_proof": connectivity,
                "retry_state": retry_state,
                "policy_mode": policy.mode_for(TRANSIENT_NETWORK_RETRY_ACTION) if policy else "recommend",
                "public_side_effects": False,
                "scheduler_changes": False,
                "collection_rerun": True,
                "editorial_mutation": False,
                "publication_attempted": False,
                "merge_pr": False,
            }
        elif root_cause == RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value:
            action = "WAIT_FOR_NEXT_SCHEDULED_RUN"
            reason = "source evidence is an external access restriction and is not eligible for transient-network retry"
        elif root_cause == RootCauseClassification.SOURCE_TRANSIENT.value:
            source_state = _source_retry_state(incident, context)
            handler = _source_retry_handler(incident.dispatch)
            if incident.dispatch == "gaza" or handler.get("excluded") is True:
                action = "WAIT_FOR_NEXT_SCHEDULED_RUN"
                reason = "Gaza source replay is excluded from automatic remediation"
                executable = False
                mutation_scope = []
            else:
                action = SOURCE_TRANSIENT_RETRY_ACTION
                reason = (
                    "single-source transient failure is tracked for bounded source retry; "
                    + (handler.get("reason") or "source-safe handler unavailable")
                )
                executable = (
                    policy is not None
                    and policy.mode_for(SOURCE_TRANSIENT_RETRY_ACTION) == "automatic"
                    and bool(handler.get("enabled"))
                    and source_state.get("retry_budget_remaining") is True
                    and source_state.get("backoff_elapsed") is True
                    and not source_state.get("terminal_classification")
                )
                mutation_scope = [
                    "source-specific collection artifacts only when a registered source-safe handler exists",
                    "ops/operator/remediation/receipts/<date>/<receipt>.json",
                ]
            safety_checks = {
                "structured_evidence": context.to_payload(),
                "root_cause_classification": root_cause,
                "source_retry_state": source_state,
                "source_retry_candidates": _source_retry_candidates(context),
                "handler": handler,
                "policy_mode": policy.mode_for(SOURCE_TRANSIENT_RETRY_ACTION) if policy else "recommend",
                "public_side_effects": False,
                "scheduler_changes": False,
                "collection_rerun": False,
                "source_only_retry": bool(handler.get("enabled")),
                "editorial_mutation": False,
                "publication_attempted": False,
                "merge_pr": False,
            }
        elif root_cause == RootCauseClassification.INTERNAL_FAILURE.value:
            action = "ENGINEER_PREPARE_FIX"
            reason = "evidence supports an internal parser/configuration failure, not network retry"
            mutation_scope = ["C:\\BlueFernRunner\\OperatorWorktrees\\<work-id>", "operator repair branch", "approval-ready repair PR"]
        else:
            action = "INVESTIGATE_SOURCE_FAILURES"
            reason = "evidence points to source/feed failures rather than a code defect"
    elif (
        current_status is not None
        and incident.affected_date
        and current_status.date > incident.affected_date
        and current_status.state in TERMINAL_NO_ACTION_STATES
        and current_status.next_action == "NONE"
    ):
        action = "WAIT_FOR_NEXT_SCHEDULED_RUN"
        reason = "incident appears historical or transient and current runner state is healthy"
    elif _context_supports_code_defect(context):
        action = "ENGINEER_PREPARE_FIX"
        reason = "evidence supports a plausible code defect within isolated Engineer Mode scope"
        mutation_scope = ["C:\\BlueFernRunner\\OperatorWorktrees\\<work-id>", "operator repair branch", "approval-ready repair PR"]
    else:
        action = "INVESTIGATE_SOURCE_FAILURES" if incident.recovery_action == "INVESTIGATE_FAILED_SOURCES" else "WAIT_FOR_NEXT_SCHEDULED_RUN"
        reason = "no safe automatic production mutation is indicated"

    approval_required = action in EXECUTABLE_REMEDIATION_ACTIONS or action in {"ENGINEER_PREPARE_FIX", "CLOSE_NON_CODE_INCIDENT"}
    if not safety_checks:
        safety_checks = {
            "structured_evidence": context.to_payload(),
            "public_side_effects": False,
            "scheduler_changes": False,
            "collection_rerun": False,
            "editorial_mutation": False,
            "merge_pr": False,
        }
    elif "structured_evidence" not in safety_checks:
        safety_checks = {**safety_checks, "structured_evidence": context.to_payload()}
    return RemediationActionPlan(
        dispatch=incident.dispatch,
        incident_id=incident.incident_id,
        classification=incident.classification,
        affected_date=incident.affected_date,
        current_condition=condition,
        proposed_action=action,
        reason=reason,
        evidence=evidence,
        safety_checks=safety_checks,
        approval_required=approval_required,
        executable=executable,
        expected_mutation_scope=mutation_scope,
    )


def _find_incidents_for_plan(
    dispatch: str,
    date: str,
    *,
    repo_root: Path,
    operator_root: Path,
    config: OperatorConfig,
    now: datetime | None,
) -> tuple[OperatorResult, list[Incident], DispatchStatus | None]:
    result = check_operator(
        repo_root=repo_root,
        operator_root=operator_root,
        config=config,
        now=now,
        write_ledger=False,
        allow_automatic_remediation=False,
    )
    incidents = [
        incident
        for incident in result.incidents
        if incident.dispatch == dispatch
        and incident.state == IncidentState.OPEN.value
        and (incident.affected_date == date or not incident.affected_date)
    ]
    runner_root = config.dispatches[dispatch].runner_root
    try:
        current_status = build_status(dispatch, date, root=runner_root)
    except Exception:  # noqa: BLE001
        current_status = None
    return result, incidents, current_status


def build_remediation_plan(
    dispatch: str,
    date: str,
    *,
    repo_root: Path = ROOT,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    config: OperatorConfig | None = None,
    now: datetime | None = None,
    runner: Any = _run_command,
) -> dict[str, Any]:
    config = config or load_config(operator_root / "config.json")
    if dispatch not in config.dispatches:
        raise ValueError(f"unknown dispatch: {dispatch}")
    result, incidents, current_status = _find_incidents_for_plan(
        dispatch,
        date,
        repo_root=repo_root,
        operator_root=operator_root,
        config=config,
        now=now,
    )
    runner_root = config.dispatches[dispatch].runner_root
    policy = load_remediation_policy(operator_root / "remediation-policy.yaml")
    plans = [
        build_remediation_action_plan(
            incident,
            runner_root=runner_root,
            current_status=current_status,
            status_root=_configured_status_root(config, repo_root),
            policy=policy,
            operator_root=operator_root,
            runner=runner,
        )
        for incident in incidents
    ]
    return {
        "schema_version": REMEDIATION_PLAN_SCHEMA_VERSION,
        "dispatch": dispatch,
        "date": date,
        "checked_at": result.checked_at,
        "plan_count": len(plans),
        "plans": [plan.to_payload() for plan in plans],
        "production_state_mutated": False,
    }


def _write_remediation_receipt(operator_root: Path, receipt: RemediationReceipt) -> RemediationReceipt:
    receipt_id = hashlib.sha256(json.dumps(receipt.to_payload(), sort_keys=True).encode("utf-8")).hexdigest()[:12]
    date = receipt.started_at[:10]
    path = operator_root / "remediation" / "receipts" / date / f"{receipt.dispatch}-{receipt.incident_id}-{receipt.action}-{receipt_id}.json"
    payload = receipt.to_payload()
    payload["receipt_path"] = str(path)
    _write_json(path, payload)
    return replace(receipt, receipt_path=str(path))


def _refused_remediation_receipt(
    *,
    dispatch: str,
    incident_id: str,
    action: str,
    reason: str,
    now: datetime | None = None,
) -> RemediationReceipt:
    timestamp = _format_time(now or _utc_now())
    return RemediationReceipt(
        dispatch=dispatch,
        incident_id=incident_id,
        action=action,
        accepted=False,
        outcome="REFUSED",
        reason=reason,
        started_at=timestamp,
        completed_at=timestamp,
    )


def _run_validation_command(command: list[str], *, cwd: Path, runner: Any, env: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        result = runner(command, cwd=cwd, env=env)
    except TypeError:
        result = runner(command, cwd=cwd)
    return {
        "command_argv": command,
        "environment_overrides": env or {},
        "exit_code": result.exit_code,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
    }


def _apply_runner_roll_forward(
    plan: RemediationActionPlan,
    *,
    runner_root: Path,
    operator_root: Path,
    runner: Any = _run_command,
    now: datetime | None = None,
) -> RemediationReceipt:
    started = _format_time(now or _utc_now())
    before_head = _git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root)
    validation_capability = _runner_validation_capability(runner_root)
    if not validation_capability["ok"]:
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "REFUSED",
            "runner-local validation capability is unavailable",
            started,
            _format_time(_utc_now()),
            before_head=before_head,
            expected_mutation_scope=plan.expected_mutation_scope,
            validation={"validation_capability": validation_capability, "merge_applied": False, "validation_passed": False},
        )
        return _write_remediation_receipt(operator_root, receipt)
    fetch = runner(["git", "fetch", "origin"], cwd=runner_root)
    if not fetch.ok:
        receipt = RemediationReceipt(plan.dispatch, plan.incident_id, plan.proposed_action, False, "FAILED", "git fetch failed", started, _format_time(_utc_now()), before_head=before_head, expected_mutation_scope=plan.expected_mutation_scope, warnings=[fetch.stderr.strip() or fetch.stdout.strip()])
        return _write_remediation_receipt(operator_root, receipt)
    safety = _runner_roll_forward_safety(plan.dispatch, runner_root, runner=runner)
    if not safety.get("safe"):
        receipt = RemediationReceipt(plan.dispatch, plan.incident_id, plan.proposed_action, False, "REFUSED", "runner roll-forward safety checks failed", started, _format_time(_utc_now()), before_head=before_head, expected_mutation_scope=plan.expected_mutation_scope, validation={"safety_checks": safety})
        return _write_remediation_receipt(operator_root, receipt)
    merge = runner(["git", "merge", "--ff-only", "origin/add/pages-repo-default"], cwd=runner_root)
    after_head = _git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root)
    if not merge.ok:
        receipt = RemediationReceipt(plan.dispatch, plan.incident_id, plan.proposed_action, False, "FAILED", "git merge --ff-only failed", started, _format_time(_utc_now()), before_head=before_head, after_head=after_head, expected_mutation_scope=plan.expected_mutation_scope, warnings=[merge.stderr.strip() or merge.stdout.strip()])
        return _write_remediation_receipt(operator_root, receipt)
    validation_capability = _runner_validation_capability(runner_root)
    validation = {
        "python_executable": validation_capability["python_executable"],
        "preflight": _run_validation_command(validation_capability["preflight_argv"], cwd=runner_root, runner=runner),
        "doctor": _run_validation_command(validation_capability["doctor_argv"], cwd=runner_root, runner=runner, env={"PYTHONPATH": "src"}),
        "tracked_dirty_after": _runner_roll_forward_safety(plan.dispatch, runner_root, runner=runner).get("tracked_dirty_paths", []),
        "merge_applied": True,
    }
    status_after = _runner_roll_forward_safety(plan.dispatch, runner_root, runner=runner)
    accepted = not validation["tracked_dirty_after"] and validation["preflight"]["exit_code"] == 0 and validation["doctor"]["exit_code"] == 0
    validation["validation_passed"] = accepted
    receipt = RemediationReceipt(
        dispatch=plan.dispatch,
        incident_id=plan.incident_id,
        action=plan.proposed_action,
        accepted=accepted,
        outcome="APPLIED" if accepted else "ROLLOUT_APPLIED_VALIDATION_FAILED",
        reason="runner fast-forwarded with preserved runtime state" if accepted else "post-rollout validation failed",
        started_at=started,
        completed_at=_format_time(_utc_now()),
        before_head=before_head,
        after_head=after_head,
        expected_mutation_scope=plan.expected_mutation_scope,
        changed_paths=[] if before_head == after_head else ["HEAD"],
        validation={**validation, "post_safety_checks": status_after},
    )
    return _write_remediation_receipt(operator_root, receipt)


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_status_destination(root: Path) -> dict[str, str | None]:
    status_root = root / "ops" / "status"
    if not status_root.exists():
        return {}
    snapshot: dict[str, str | None] = {}
    for path in sorted(status_root.rglob("*")):
        if path.is_file():
            snapshot[_rel(root, path)] = _file_hash(path)
    return snapshot


def _status_path_hashes(root: Path, paths: Iterable[str]) -> dict[str, str | None]:
    return {path: _file_hash(root / path) for path in sorted(paths)}


def _status_latest_path(status_root: Path, dispatch: str) -> Path:
    return status_root / "ops" / "status" / dispatch / "latest.json"


def _status_last_exported_at(status_root: Path, dispatch: str) -> str | None:
    payload = _read_json(_status_latest_path(status_root, dispatch))
    if not payload:
        return None
    value = payload.get("last_exported_at", payload.get("exported_at"))
    return str(value) if value else None


def _apply_refresh_status_export(
    plan: RemediationActionPlan,
    *,
    repo_root: Path,
    runner_root: Path,
    operator_root: Path,
    config: OperatorConfig,
    current_status: DispatchStatus,
    now: datetime | None = None,
) -> RemediationReceipt:
    started_time = now or _utc_now()
    started = _format_time(started_time)
    date = plan.affected_date or current_status.date
    destination_root = _configured_status_root(config, repo_root)
    operator_code_root = _operator_code_root(operator_root)
    allowed_paths = _refresh_status_export_scope(plan.dispatch, date)
    supported, support_reason = _refresh_status_supported(plan.dispatch, current_status)
    status_head_before = _git_stdout(_run_command, ["rev-parse", "HEAD"], cwd=destination_root) if destination_root.is_dir() else None
    checkout_state_before = classify_status_checkout_state(destination_root).to_payload() if destination_root.is_dir() else None
    roots_distinct = destination_root.resolve() not in {operator_code_root.resolve(), runner_root.resolve()}
    if not roots_distinct:
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "REFUSED",
            "status destination root must be distinct from Operator source and production runner roots",
            started,
            _format_time(_utc_now()),
            expected_mutation_scope=[],
            validation={
                "operator_root": str(operator_root.resolve()),
                "operator_code_root": str(operator_code_root.resolve()),
                "source_runner_root": str(runner_root.resolve()),
                "status_destination_root": str(destination_root.resolve()),
                "roots_distinct": False,
                "underlying_status_before": current_status.to_json_payload(),
                "underlying_status_after": current_status.to_json_payload(),
                "status_checkout_head_before": status_head_before,
                "status_checkout_head_after": status_head_before,
                "status_checkout_state_before": checkout_state_before,
                "status_checkout_state_after": checkout_state_before,
                "sanctioned_dirty_paths_before": sorted(
                    (checkout_state_before or {}).get("tracked_status_paths", [])
                    + (checkout_state_before or {}).get("untracked_status_paths", [])
                ),
                "sanctioned_dirty_paths_after": sorted(
                    (checkout_state_before or {}).get("tracked_status_paths", [])
                    + (checkout_state_before or {}).get("untracked_status_paths", [])
                ),
                "unexpected_dirty_paths": (checkout_state_before or {}).get("unexpected_paths", []),
                "remote_overlap_paths": [],
                "commit_created": False,
                "push_attempted": False,
            },
        )
        return _write_remediation_receipt(operator_root, receipt)
    if not destination_root.is_dir():
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "REFUSED",
            "status checkout does not exist",
            started,
            _format_time(_utc_now()),
            expected_mutation_scope=[],
            validation={
                "operator_root": str(operator_root.resolve()),
                "source_runner_root": str(runner_root.resolve()),
                "status_destination_root": str(destination_root.resolve()),
                "status_checkout_head_before": status_head_before,
                "status_checkout_head_after": status_head_before,
                "status_checkout_state_before": checkout_state_before,
                "status_checkout_state_after": checkout_state_before,
                "sanctioned_dirty_paths_before": [],
                "sanctioned_dirty_paths_after": [],
                "unexpected_dirty_paths": [],
                "remote_overlap_paths": [],
                "commit_created": False,
                "push_attempted": False,
            },
        )
        return _write_remediation_receipt(operator_root, receipt)
    if not supported:
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "REFUSED",
            support_reason,
            started,
            _format_time(_utc_now()),
            expected_mutation_scope=allowed_paths,
            validation={
                "operator_root": str(operator_root.resolve()),
                "source_runner_root": str(runner_root.resolve()),
                "status_destination_root": str(destination_root.resolve()),
                "refresh_supported": False,
                "underlying_status_before": current_status.to_json_payload(),
                "underlying_status_after": current_status.to_json_payload(),
                "status_checkout_head_before": status_head_before,
                "status_checkout_head_after": status_head_before,
                "status_checkout_state_before": checkout_state_before,
                "status_checkout_state_after": checkout_state_before,
                "sanctioned_dirty_paths_before": sorted(
                    (checkout_state_before or {}).get("tracked_status_paths", [])
                    + (checkout_state_before or {}).get("untracked_status_paths", [])
                ),
                "sanctioned_dirty_paths_after": sorted(
                    (checkout_state_before or {}).get("tracked_status_paths", [])
                    + (checkout_state_before or {}).get("untracked_status_paths", [])
                ),
                "unexpected_dirty_paths": (checkout_state_before or {}).get("unexpected_paths", []),
                "remote_overlap_paths": [],
                "commit_created": False,
                "push_attempted": False,
            },
        )
        return _write_remediation_receipt(operator_root, receipt)

    before_tree = _snapshot_status_destination(destination_root)
    before_hashes = _status_path_hashes(destination_root, allowed_paths)
    target_exported_at_before = _status_last_exported_at(destination_root, plan.dispatch)
    remote_overlap_paths: list[str] = []
    try:
        prepare_status_checkout(destination_root, branch=config.status_branch, allow_local_status_changes=True)
        food_root = config.dispatches["food-line"].runner_root.resolve()
        export_kwargs: dict[str, Any] = {
            "source_root": runner_root.resolve() if plan.dispatch == "food-line" else food_root,
            "status_checkout": destination_root.resolve(),
            "date": date,
            "evaluated_at": started,
            "exported_at": started,
            "force_refresh_dispatches": {plan.dispatch},
        }
        if plan.dispatch == "care-line":
            export_kwargs["care_source_root"] = runner_root.resolve()
            export_kwargs["care_expected_instances"] = care_expected_instances_from_task_scheduler(date)
        elif plan.dispatch == "gaza":
            export_kwargs["gaza_source_root"] = runner_root.resolve()
        elif plan.dispatch == "ice":
            export_kwargs["ice_source_root"] = runner_root.resolve()
        result = export_status(**export_kwargs)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "overlap local ops/status changes:" in message:
            remote_overlap_paths = [part.strip() for part in message.rsplit(":", 1)[-1].split(",") if part.strip()]
        checkout_state_after = classify_status_checkout_state(destination_root).to_payload() if destination_root.is_dir() else None
        status_head_after = _git_stdout(_run_command, ["rev-parse", "HEAD"], cwd=destination_root) if destination_root.is_dir() else None
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "FAILED",
            f"status export refresh failed: {exc}",
            started,
            _format_time(_utc_now()),
            expected_mutation_scope=allowed_paths,
            validation={
                "operator_root": str(operator_root.resolve()),
                "source_runner_root": str(runner_root.resolve()),
                "status_destination_root": str(destination_root.resolve()),
                "before_status_export_hashes": before_hashes,
                "underlying_status_before": current_status.to_json_payload(),
                "status_checkout_head_before": status_head_before,
                "status_checkout_head_after": status_head_after,
                "status_checkout_state_before": checkout_state_before,
                "status_checkout_state_after": checkout_state_after,
                "force_refresh_dispatches": [plan.dispatch],
                "target_exported_at_before": target_exported_at_before,
                "target_exported_at_after": _status_last_exported_at(destination_root, plan.dispatch),
                "target_timestamp_advanced": False,
                "sanctioned_dirty_paths_before": sorted(
                    (checkout_state_before or {}).get("tracked_status_paths", [])
                    + (checkout_state_before or {}).get("untracked_status_paths", [])
                ),
                "sanctioned_dirty_paths_after": sorted(
                    (checkout_state_after or {}).get("tracked_status_paths", [])
                    + (checkout_state_after or {}).get("untracked_status_paths", [])
                ),
                "unexpected_dirty_paths": (checkout_state_after or {}).get("unexpected_paths", []),
                "remote_overlap_paths": remote_overlap_paths,
                "commit_created": False,
                "push_attempted": False,
            },
        )
        return _write_remediation_receipt(operator_root, receipt)

    allowed_paths = sorted(str(path).replace("\\", "/") for path in result.get("paths", []))
    after_tree = _snapshot_status_destination(destination_root)
    checkout_state_after = classify_status_checkout_state(destination_root).to_payload()
    status_head_after = _git_stdout(_run_command, ["rev-parse", "HEAD"], cwd=destination_root)
    before_hashes = {path: before_tree.get(path) for path in allowed_paths}
    after_hashes = _status_path_hashes(destination_root, allowed_paths)
    changed_paths = sorted(path for path in set(before_tree) | set(after_tree) if before_tree.get(path) != after_tree.get(path))
    unexpected = sorted(path for path in changed_paths if path not in allowed_paths)
    status_after = build_status(plan.dispatch, date, root=runner_root)
    stale_after = not _latest_exported_status(destination_root, plan.dispatch, started_time, config.status_freshness_threshold_minutes).is_fresh
    target_exported_at_after = _status_last_exported_at(destination_root, plan.dispatch)
    target_timestamp_advanced = (
        target_exported_at_after is not None
        and target_exported_at_after != target_exported_at_before
    )
    validation = {
        "operator_root": str(operator_root.resolve()),
        "operator_code_root": str(operator_code_root.resolve()),
        "source_runner_root": str(runner_root.resolve()),
        "status_destination_root": str(destination_root.resolve()),
        "observed_date": date,
        "before_status_export_hashes": before_hashes,
        "after_status_export_hashes": after_hashes,
        "exporter_paths": allowed_paths,
        "status_branch": config.status_branch,
        "underlying_status_before": current_status.to_json_payload(),
        "underlying_status_after": status_after.to_json_payload(),
        "underlying_incident_preserved": status_after.state == current_status.state and status_after.next_action == current_status.next_action,
        "stale_observability_after": stale_after,
        "force_refresh_dispatches": [plan.dispatch],
        "target_exported_at_before": target_exported_at_before,
        "target_exported_at_after": target_exported_at_after,
        "target_timestamp_advanced": target_timestamp_advanced,
        "refresh_support_reason": support_reason,
        "status_checkout_head_before": status_head_before,
        "status_checkout_head_after": status_head_after,
        "status_checkout_state_before": checkout_state_before,
        "status_checkout_state_after": checkout_state_after,
        "sanctioned_dirty_paths_before": sorted(
            (checkout_state_before or {}).get("tracked_status_paths", [])
            + (checkout_state_before or {}).get("untracked_status_paths", [])
        ),
        "sanctioned_dirty_paths_after": sorted(
            (checkout_state_after or {}).get("tracked_status_paths", [])
            + (checkout_state_after or {}).get("untracked_status_paths", [])
        ),
        "unexpected_dirty_paths": (checkout_state_after or {}).get("unexpected_paths", []),
        "remote_overlap_paths": remote_overlap_paths,
        "commit_created": False,
        "push_attempted": False,
        "public_side_effects": False,
        "scheduler_changes": False,
        "collection_rerun": False,
        "editorial_mutation": False,
        "publication_attempted": False,
    }
    if unexpected:
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "FAILED",
            "unexpected status-path mutation detected; no automatic cleanup was attempted",
            started,
            _format_time(_utc_now()),
            expected_mutation_scope=allowed_paths,
            changed_paths=changed_paths,
            validation=validation,
            warnings=unexpected,
        )
        return _write_remediation_receipt(operator_root, receipt)
    if plan.classification == "STALE_OBSERVABILITY" and (stale_after or not target_timestamp_advanced):
        reason = "status export completed but target stale observability was not repaired"
        if not target_timestamp_advanced:
            reason = "status export completed but target last_exported_at did not advance"
        receipt = RemediationReceipt(
            plan.dispatch,
            plan.incident_id,
            plan.proposed_action,
            False,
            "FAILED",
            reason,
            started,
            _format_time(_utc_now()),
            expected_mutation_scope=allowed_paths,
            changed_paths=changed_paths,
            validation=validation,
        )
        return _write_remediation_receipt(operator_root, receipt)
    outcome = "REFRESHED" if changed_paths else "ALREADY_CURRENT"
    receipt = RemediationReceipt(
        plan.dispatch,
        plan.incident_id,
        plan.proposed_action,
        True,
        outcome,
        "status export refreshed from durable runner evidence",
        started,
        _format_time(_utc_now()),
        expected_mutation_scope=allowed_paths,
        changed_paths=changed_paths,
        validation=validation,
    )
    return _write_remediation_receipt(operator_root, receipt)


def _apply_transient_network_task_retry(
    plan: RemediationActionPlan,
    *,
    repo_root: Path,
    runner_root: Path,
    operator_root: Path,
    config: OperatorConfig,
    current_status: DispatchStatus,
    runner: Any = _run_command,
    now: datetime | None = None,
) -> RemediationReceipt:
    started_time = now or _utc_now()
    started = _format_time(started_time)
    safety = plan.safety_checks if isinstance(plan.safety_checks, dict) else {}
    handler = safety.get("handler") if isinstance(safety.get("handler"), dict) else None
    if not handler:
        return _write_remediation_receipt(
            operator_root,
            RemediationReceipt(
                plan.dispatch,
                plan.incident_id,
                plan.proposed_action,
                False,
                "REFUSED",
                "no registered transient retry handler",
                started,
                _format_time(_utc_now()),
                validation={"plan_safety_checks": safety},
            ),
        )
    protected_head = _git_head(repo_root)
    runner_safety = _runner_transient_retry_safety(plan.dispatch, runner_root, protected_head=protected_head, runner=runner)
    connectivity = _connectivity_recovered_proof(runner_root, runner=runner)
    retry_state = {
        key: safety.get("retry_state", {}).get(key)
        for key in ("attempt_count", "max_attempts", "last_attempted_at", "minimum_backoff_minutes", "next_eligible_at")
        if isinstance(safety.get("retry_state"), dict)
    }
    validation: dict[str, Any] = {
        "handler": handler,
        "runner_safety": runner_safety,
        "connectivity_proof": connectivity,
        "retry_state": retry_state,
        "underlying_status_before": current_status.to_json_payload(),
        "public_side_effects": False,
        "scheduler_changes": False,
        "publication_attempted": False,
    }
    if not runner_safety["safe"]:
        receipt = RemediationReceipt(plan.dispatch, plan.incident_id, plan.proposed_action, False, "REFUSED", "runner preconditions failed", started, _format_time(_utc_now()), expected_mutation_scope=plan.expected_mutation_scope, validation=validation)
        return _write_remediation_receipt(operator_root, receipt)
    if not connectivity["ok"]:
        receipt = RemediationReceipt(plan.dispatch, plan.incident_id, plan.proposed_action, False, "BLOCKED", "connectivity proof did not recover", started, _format_time(_utc_now()), expected_mutation_scope=plan.expected_mutation_scope, validation=validation)
        return _write_remediation_receipt(operator_root, receipt)
    if _terminal_retry_proof(current_status):
        validation["underlying_status_after"] = current_status.to_json_payload()
        receipt = RemediationReceipt(plan.dispatch, plan.incident_id, plan.proposed_action, True, "NO_ACTION", "terminal receipt already exists; retry not duplicated", started, _format_time(_utc_now()), before_head=runner_safety.get("head"), after_head=runner_safety.get("head"), expected_mutation_scope=plan.expected_mutation_scope, validation=validation)
        return _write_remediation_receipt(operator_root, receipt)

    command = _transient_retry_command(handler, runner_root=runner_root, date=plan.affected_date or current_status.date, incident_id=plan.incident_id, attempt_count=int((retry_state or {}).get("attempt_count") or 0))
    retry_result = runner(command, cwd=runner_root)
    status_after = build_status(plan.dispatch, plan.affected_date or current_status.date, root=runner_root)
    validation.update(
        {
            "command_shape": command[:6] + ["<wrapper>", *command[7:]],
            "retry_exit_code": retry_result.exit_code,
            "retry_stdout_tail": retry_result.stdout[-2000:],
            "retry_stderr_tail": retry_result.stderr[-2000:],
            "underlying_status_after": status_after.to_json_payload(),
            "terminal_proof": _terminal_retry_proof(status_after),
        }
    )
    status_refresh_receipt: RemediationReceipt | None = None
    if retry_result.ok and _terminal_retry_proof(status_after):
        refresh_plan = RemediationActionPlan(
            dispatch=plan.dispatch,
            incident_id=plan.incident_id,
            classification=Classification.STALE_OBSERVABILITY.value,
            affected_date=plan.affected_date or status_after.date,
            current_condition=status_after.state,
            proposed_action="REFRESH_STATUS_EXPORT",
            reason="refresh operational status after successful transient network retry",
            evidence=plan.evidence,
            safety_checks={
                "handler": "post_transient_retry_status_refresh",
                "public_side_effects": False,
                "scheduler_changes": False,
                "collection_rerun": False,
                "editorial_mutation": False,
                "merge_pr": False,
            },
            approval_required=False,
            executable=True,
            expected_mutation_scope=_refresh_status_export_scope(plan.dispatch, plan.affected_date or status_after.date),
        )
        status_refresh_receipt = _apply_refresh_status_export(
            refresh_plan,
            repo_root=repo_root,
            runner_root=runner_root,
            operator_root=operator_root,
            config=config,
            current_status=status_after,
            now=started_time,
        )
        validation["status_refresh_receipt"] = status_refresh_receipt.to_payload()
    accepted = retry_result.ok and _terminal_retry_proof(status_after) and (status_refresh_receipt is None or status_refresh_receipt.accepted)
    outcome = "REFRESHED" if accepted else "FAILED"
    reason = "canonical non-public task retry produced terminal evidence and refreshed status" if accepted else "canonical retry did not produce accepted terminal proof"
    receipt = RemediationReceipt(
        plan.dispatch,
        plan.incident_id,
        plan.proposed_action,
        accepted,
        outcome,
        reason,
        started,
        _format_time(_utc_now()),
        before_head=runner_safety.get("head"),
        after_head=_git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root),
        expected_mutation_scope=plan.expected_mutation_scope,
        changed_paths=[],
        validation=validation,
        warnings=[] if accepted else ["transient network retry failed-safe; no publication was attempted"],
    )
    return _write_remediation_receipt(operator_root, receipt)


def _apply_source_transient_fetch_retry(
    plan: RemediationActionPlan,
    *,
    operator_root: Path,
    now: datetime | None = None,
) -> RemediationReceipt:
    started = _format_time(now or _utc_now())
    safety = plan.safety_checks if isinstance(plan.safety_checks, dict) else {}
    handler = safety.get("handler") if isinstance(safety.get("handler"), dict) else {}
    state = safety.get("source_retry_state") if isinstance(safety.get("source_retry_state"), dict) else {}
    receipt = RemediationReceipt(
        plan.dispatch,
        plan.incident_id,
        plan.proposed_action,
        False,
        "REFUSED",
        str(handler.get("reason") or "no registered source-safe retry handler"),
        started,
        _format_time(_utc_now()),
        expected_mutation_scope=plan.expected_mutation_scope,
        validation={
            "source_retry_state": state,
            "handler": handler,
            "public_side_effects": False,
            "scheduler_changes": False,
            "collection_rerun": False,
            "publication_attempted": False,
        },
    )
    return _write_remediation_receipt(operator_root, receipt)


def apply_remediation(
    *,
    dispatch: str,
    incident_id: str,
    action: str,
    confirm: str,
    repo_root: Path = ROOT,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    config: OperatorConfig | None = None,
    runner: Any = _run_command,
    now: datetime | None = None,
) -> RemediationReceipt:
    if action not in REMEDIATION_ACTIONS:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason="unsupported remediation action", now=now)
    if confirm != action:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason=f"missing required confirmation token: {action}", now=now)
    if action not in EXECUTABLE_REMEDIATION_ACTIONS:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason="action is not executable by remediate-apply", now=now)

    config = config or load_config(operator_root / "config.json")
    result = check_operator(
        repo_root=repo_root,
        operator_root=operator_root,
        config=config,
        now=now,
        write_ledger=False,
        allow_automatic_remediation=False,
    )
    incident = next((row for row in result.incidents if row.incident_id == incident_id and row.dispatch == dispatch), None)
    if incident is None:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason="incident is not present in current read-only check", now=now)
    if incident.state != IncidentState.OPEN.value:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason="incident is no longer open", now=now)
    runner_root = config.dispatches[dispatch].runner_root
    current_status = build_status(dispatch, incident.affected_date or _latest_runner_date(runner_root, dispatch) or _utc_now().date().isoformat(), root=runner_root)
    plan = build_remediation_action_plan(
        incident,
        runner_root=runner_root,
        operator_root=operator_root,
        current_status=current_status,
        status_root=_configured_status_root(config, repo_root),
        runner=runner,
    )
    if plan.proposed_action != action:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason=f"current plan proposes {plan.proposed_action}", now=now)
    if not plan.executable:
        return _refused_remediation_receipt(dispatch=dispatch, incident_id=incident_id, action=action, reason="current plan is not executable", now=now)
    if action == "RUNNER_ROLL_FORWARD":
        return _apply_runner_roll_forward(plan, runner_root=runner_root, operator_root=operator_root, runner=runner, now=now)
    if action == "REFRESH_STATUS_EXPORT":
        return _apply_refresh_status_export(
            plan,
            repo_root=repo_root,
            runner_root=runner_root,
            operator_root=operator_root,
            config=config,
            current_status=current_status,
            now=now,
        )
    if action == TRANSIENT_NETWORK_RETRY_ACTION:
        return _apply_transient_network_task_retry(
            plan,
            repo_root=repo_root,
            runner_root=runner_root,
            operator_root=operator_root,
            config=config,
            current_status=current_status,
            runner=runner,
            now=now,
        )
    if action == SOURCE_TRANSIENT_RETRY_ACTION:
        return _apply_source_transient_fetch_retry(plan, operator_root=operator_root, now=now)

    started = _format_time(now or _utc_now())
    apply_result = apply_recovery_plan(dispatch, incident.affected_date or current_status.date, root=runner_root, confirm=action)
    receipt = RemediationReceipt(
        dispatch=dispatch,
        incident_id=incident_id,
        action=action,
        accepted=apply_result.outcome not in {"REFUSED", "FAILED", "BLOCKED"},
        outcome=apply_result.outcome,
        reason=f"dispatch_ops.apply_recovery_plan executed {action}",
        started_at=started,
        completed_at=_format_time(_utc_now()),
        before_head=_git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root),
        after_head=_git_stdout(runner, ["rev-parse", "HEAD"], cwd=runner_root),
        expected_mutation_scope=plan.expected_mutation_scope,
        changed_paths=apply_result.status_artifacts_changed,
        validation={"apply_result": asdict(apply_result)},
        warnings=apply_result.warnings,
    )
    return _write_remediation_receipt(operator_root, receipt)


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


def _inside_path(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _resolve_pytest_target(pattern: str, *, worktree: Path) -> tuple[list[str], str | None]:
    normalized = pattern.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [], "VALIDATION_TARGET_UNSAFE"
    if not normalized.startswith("tests/"):
        return [], "VALIDATION_TARGET_UNSAFE"
    matches = sorted(worktree.glob(normalized)) if any(char in normalized for char in "*?[") else [worktree / normalized]
    resolved: list[str] = []
    for match in matches:
        if not match.is_file():
            continue
        if not _inside_path(match, worktree / "tests"):
            return [], "VALIDATION_TARGET_UNSAFE"
        rel = match.relative_to(worktree).as_posix()
        resolved.append(rel)
    if not resolved:
        return [], "VALIDATION_TARGET_NOT_FOUND"
    return sorted(set(resolved)), None


def _validation_invocation(command: str, *, worktree: Path) -> dict[str, Any]:
    if command.startswith("$env:PYTHONPATH='src'; python "):
        rest = command.removeprefix("$env:PYTHONPATH='src'; ")
        argv = shlex.split(rest, posix=False)
        return {"command_argv": argv, "env": {"PYTHONPATH": "src"}, "requested_pattern": None, "resolved_paths": [], "outcome": "READY"}
    argv = shlex.split(command, posix=False)
    if len(argv) >= 4 and argv[:3] == ["python", "-m", "pytest"]:
        resolved_argv = argv[:3]
        resolved_paths: list[str] = []
        requested_patterns: list[str] = []
        options: list[str] = []
        for arg in argv[3:]:
            if arg.startswith("-"):
                options.append(arg)
                continue
            if arg.startswith("tests/"):
                requested_patterns.append(arg)
                paths, error = _resolve_pytest_target(arg, worktree=worktree)
                if error:
                    return {
                        "command_argv": [],
                        "requested_pattern": arg,
                        "resolved_paths": [],
                        "outcome": error,
                    }
                resolved_paths.extend(paths)
            else:
                candidate = Path(arg.replace("\\", "/"))
                if candidate.is_absolute() or ".." in candidate.parts:
                    return {
                        "command_argv": [],
                        "requested_pattern": arg,
                        "resolved_paths": [],
                        "outcome": "VALIDATION_TARGET_UNSAFE",
                    }
                resolved_argv.append(arg)
        resolved_argv.extend(sorted(set(resolved_paths)))
        resolved_argv.extend(options)
        return {
            "command_argv": resolved_argv,
            "requested_pattern": " ".join(requested_patterns) if requested_patterns else None,
            "resolved_paths": sorted(set(resolved_paths)),
            "outcome": "READY",
        }
    return {"command_argv": argv, "requested_pattern": None, "resolved_paths": [], "outcome": "READY"}


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
        invocation = _validation_invocation(command, worktree=worktree)
        if invocation["outcome"] != "READY":
            row = {
                "command": command,
                "requested_pattern": invocation.get("requested_pattern"),
                "resolved_paths": invocation.get("resolved_paths", []),
                "command_argv": invocation.get("command_argv", []),
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "outcome": invocation["outcome"],
            }
            results.append(row)
            item = replace(item, validation_results=results, merge_allowed=False)
            item = _save_engineering_work_item(operator_root, item)
            return item, False
        env = invocation.get("env")
        try:
            result = runner(invocation["command_argv"], cwd=worktree, env=env) if env else runner(invocation["command_argv"], cwd=worktree)
        except TypeError:
            result = runner(invocation["command_argv"], cwd=worktree)
        row = {
            "command": command,
            "requested_pattern": invocation.get("requested_pattern"),
            "resolved_paths": invocation.get("resolved_paths", []),
            "command_argv": invocation["command_argv"],
            "env": env or {},
            "exit_code": result.exit_code,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "outcome": "PASSED" if result.ok else "FAILED",
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


def _refused_close_payload(work_id: str, reason: str, item: EngineeringWorkItem | None = None) -> dict[str, Any]:
    return {
        "schema_version": ENGINEERING_CLOSE_SCHEMA_VERSION,
        "event": "ENGINEERING_WORK_CLOSE_REFUSED",
        "work_id": work_id,
        "accepted": False,
        "reason": reason,
        "work_item": item.to_payload() if item else None,
    }


def _bounded_close_text(value: str, *, limit: int, label: str) -> tuple[str | None, str | None]:
    text = value.strip()
    if not text:
        return None, f"{label} is required"
    if len(text) > limit:
        return None, f"{label} exceeds {limit} characters"
    if _redact_diagnostic_text(text) != text:
        return None, f"{label} contains secret-like text"
    return text, None


def _tree_file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    return hashes


def _write_engineering_close_audit(operator_root: Path, payload: dict[str, Any]) -> Path:
    date = str(payload.get("closed_at") or _format_time(_utc_now()))[:10]
    path = operator_root / "engineering" / "audit" / date / f"{payload['work_id']}-closed.json"
    _write_json(path, payload)
    return path


def close_engineering_work_item(
    work_id: str,
    *,
    disposition: str,
    confirm: str,
    reason: str,
    facts: list[str] | None = None,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    runner: Any = _run_command,
) -> dict[str, Any]:
    if confirm != ENGINEERING_CLOSE_CONFIRMATION:
        return _refused_close_payload(work_id, "confirmation token required")
    if disposition not in ENGINEERING_CLOSE_DISPOSITIONS:
        return _refused_close_payload(work_id, "unsupported disposition")
    bounded_reason, reason_error = _bounded_close_text(reason, limit=1000, label="reason")
    if reason_error:
        return _refused_close_payload(work_id, reason_error)
    facts = facts or []
    if len(facts) > 10:
        return _refused_close_payload(work_id, "too many supporting facts")
    bounded_facts: list[str] = []
    for fact in facts:
        bounded_fact, fact_error = _bounded_close_text(fact, limit=500, label="fact")
        if fact_error:
            return _refused_close_payload(work_id, fact_error)
        bounded_facts.append(bounded_fact or "")

    try:
        item = _load_engineering_work_item(operator_root, work_id)
    except FileNotFoundError:
        return _refused_close_payload(work_id, "work item not found")
    if item.state == EngineeringState.PR_OPEN.value:
        return _refused_close_payload(work_id, "work item has an open repair PR", item)
    if item.state not in ENGINEERING_CLOSEABLE_STATES:
        return _refused_close_payload(work_id, f"work item state is not closeable: {item.state}", item)
    if item.merge_allowed:
        return _refused_close_payload(work_id, "merge_allowed must be false", item)
    if operator_root.joinpath(".lock").exists():
        return _refused_close_payload(work_id, "Operator lock is active", item)

    worktree = Path(item.worktree)
    if not worktree.exists() or not (worktree / ".git").exists():
        return _refused_close_payload(work_id, "worktree is missing", item)
    clean, clean_reason = _worktree_status_clean(item, runner=runner)
    if not clean:
        return _refused_close_payload(work_id, clean_reason, item)
    if _remote_engineering_branch_exists(item, runner=runner):
        return _refused_close_payload(work_id, "remote repair branch already exists", item)
    if _open_engineering_pr_exists(item, runner=runner):
        return _refused_close_payload(work_id, "repair PR already exists", item)

    active_root = _engineering_work_root(operator_root, work_id)
    history_root = _engineering_history_work_root(operator_root, work_id)
    if not active_root.is_dir():
        return _refused_close_payload(work_id, "active work item metadata is missing", item)
    if history_root.exists():
        return _refused_close_payload(work_id, "history work item already exists", item)

    prior_state = item.state
    closed_at = _format_time(_utc_now())
    artifact_names = sorted(path.name for path in active_root.iterdir() if path.is_file())
    closure = {
        "schema_version": ENGINEERING_CLOSE_SCHEMA_VERSION,
        "disposition": disposition,
        "closed_at": closed_at,
        "prior_state": prior_state,
        "reason": bounded_reason,
        "supporting_facts": bounded_facts,
        "related_incident_id": item.incident_id,
        "artifacts_preserved": artifact_names,
        "merge_allowed": False,
    }
    closed_item = replace(item, state=EngineeringState.CLOSED.value, closure=closure, merge_allowed=False)
    _save_engineering_work_item(operator_root, closed_item)
    audit_payload = {
        "schema_version": ENGINEERING_CLOSE_SCHEMA_VERSION,
        "event": "ENGINEERING_WORK_CLOSED",
        "work_id": work_id,
        "incident_id": item.incident_id,
        "disposition": disposition,
        "prior_state": prior_state,
        "reason": bounded_reason,
        "supporting_facts": bounded_facts,
        "closed_at": closed_at,
        "merge_allowed": False,
    }
    _write_json(active_root / "closure.json", audit_payload)

    before_hashes = _tree_file_hashes(active_root)
    shutil.copytree(active_root, history_root)
    after_hashes = _tree_file_hashes(history_root)
    if before_hashes != after_hashes:
        return _refused_close_payload(work_id, "history archive verification failed", closed_item)
    audit_path = _write_engineering_close_audit(operator_root, audit_payload)
    shutil.rmtree(active_root)

    history_item = _load_engineering_work_item_anywhere(operator_root, work_id)
    return {
        "schema_version": ENGINEERING_CLOSE_SCHEMA_VERSION,
        "event": "ENGINEERING_WORK_CLOSED",
        "accepted": True,
        "work_id": work_id,
        "incident_id": item.incident_id,
        "disposition": disposition,
        "prior_state": prior_state,
        "reason": bounded_reason,
        "closed_at": closed_at,
        "audit_path": str(audit_path),
        "active_path_removed": not active_root.exists(),
        "history_path": str(history_root),
        "work_item": history_item.to_payload(),
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
        if not changed:
            reason = "insufficient engineering evidence" if item.root_cause == "INSUFFICIENT_EVIDENCE" else "codex produced no patch"
            return _block_engineering_work(operator_root, item, reason)
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
        "autonomy_state": result.autonomy_state,
        "approval_required_incidents": [
            incident.incident_id
            for incident in result.incidents
            if incident.approval_required
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
    close = sub.add_parser("engineer-close", help="Close an engineering work item without repair execution.")
    close.add_argument("--work-id", required=True, help=argparse.SUPPRESS)
    close.add_argument("--disposition", required=True, choices=sorted(ENGINEERING_CLOSE_DISPOSITIONS), help=argparse.SUPPRESS)
    close.add_argument("--confirm", required=True, help=argparse.SUPPRESS)
    close.add_argument("--reason", required=True, help=argparse.SUPPRESS)
    close.add_argument("--fact", action="append", default=[], help=argparse.SUPPRESS)
    close.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    close.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    plan = sub.add_parser("remediate-plan", help="Build read-only approval-gated remediation plans.")
    plan.add_argument("dispatch", choices=DISPATCH_ORDER)
    plan.add_argument("--date", required=True, help=argparse.SUPPRESS)
    plan.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    plan.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help=argparse.SUPPRESS)
    plan.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    plan.add_argument("--repo-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    plan.add_argument("--now", help=argparse.SUPPRESS)
    apply = sub.add_parser("remediate-apply", help="Apply one approved bounded remediation after replanning.")
    apply.add_argument("--dispatch", required=True, choices=DISPATCH_ORDER, help=argparse.SUPPRESS)
    apply.add_argument("--incident-id", required=True, help=argparse.SUPPRESS)
    apply.add_argument("--action", required=True, choices=sorted(REMEDIATION_ACTIONS), help=argparse.SUPPRESS)
    apply.add_argument("--confirm", required=True, help=argparse.SUPPRESS)
    apply.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    apply.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help=argparse.SUPPRESS)
    apply.add_argument("--operator-root", type=Path, default=DEFAULT_OPERATOR_ROOT, help=argparse.SUPPRESS)
    apply.add_argument("--repo-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
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
    if args.command == "engineer-close":
        payload = close_engineering_work_item(
            args.work_id,
            disposition=args.disposition,
            confirm=args.confirm,
            reason=args.reason,
            facts=args.fact,
            operator_root=args.operator_root,
        )
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            if payload["accepted"]:
                print(f"{payload['work_id']} closed {payload['disposition']}")
            else:
                print(f"{payload['work_id']} accepted=false {payload['reason']}")
        return 0 if payload["accepted"] else 2
    if args.command == "remediate-plan":
        now = _parse_time(args.now) if args.now else None
        payload = build_remediation_plan(
            args.dispatch,
            args.date,
            repo_root=args.repo_root,
            operator_root=args.operator_root,
            config=load_config(args.config),
            now=now,
        )
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for row in payload["plans"]:
                print(f"{row['incident_id']} {row['proposed_action']} executable={row['executable']}")
        return 0
    if args.command == "remediate-apply":
        receipt = apply_remediation(
            dispatch=args.dispatch,
            incident_id=args.incident_id,
            action=args.action,
            confirm=args.confirm,
            repo_root=args.repo_root,
            operator_root=args.operator_root,
            config=load_config(args.config),
        )
        payload = receipt.to_payload()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"{receipt.incident_id} {receipt.action} accepted={receipt.accepted} {receipt.outcome}")
        return 0 if receipt.accepted else 2
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
