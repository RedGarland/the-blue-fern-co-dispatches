from __future__ import annotations

import argparse
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
    DispatchStatus,
    RecoveryPlan,
    build_recovery_plan_from_status,
    build_status,
)


SCHEMA_VERSION = "blue_fern_operator_result_v1"
INCIDENT_SCHEMA_VERSION = "blue_fern_operator_incident_v1"
DEFAULT_CONFIG_PATH = ROOT / "ops" / "operator" / "config.json"
DEFAULT_OPERATOR_ROOT = ROOT / "ops" / "operator"
DISPATCH_ORDER = ("food-line", "care-line", "gaza", "ice")
TERMINAL_NO_ACTION_STATES = {"COMPLETE", "PUBLISHED", "NO_UPDATE", "SAFE_NO_OP"}
FORBIDDEN_REMEDIATION_ACTIONS = {
    "PUBLISH_APPROVED_RELEASE",
    "PUBLISH_NO_UPDATE",
    "REPLAY_COLLECTION",
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
class ExportedStatusObservation:
    path: str
    observed_date: str | None
    exported_at: str | None
    age_minutes: float | None
    is_fresh: bool
    evidence: list[str] = field(default_factory=list)


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
    schema_version: str = INCIDENT_SCHEMA_VERSION
    affected_date: str | None = None
    updated_at: str | None = None

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

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: payload[key] for key in sorted(payload)}


@dataclass(frozen=True)
class OperatorResult:
    checked_at: str
    dispatches: list[DispatchResult]
    incidents: list[Incident]
    production_state_mutated: bool = False
    schema_version: str = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "dispatches": [result.to_payload() for result in self.dispatches],
            "incidents": [incident.to_payload() for incident in self.incidents],
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


def _latest_exported_status(repo_root: Path, dispatch: str, now: datetime, threshold_minutes: int) -> ExportedStatusObservation:
    path = repo_root / "ops" / "status" / dispatch / "latest.json"
    payload = _read_json(path)
    if not payload:
        return ExportedStatusObservation(
            path=_rel(repo_root, path),
            observed_date=None,
            exported_at=None,
            age_minutes=None,
            is_fresh=False,
            evidence=[f"{_rel(repo_root, path)} missing"],
        )
    exported_at = str(payload.get("last_exported_at") or payload.get("exported_at") or "")
    exported_time = _parse_time(exported_at)
    age_minutes = ((now - exported_time).total_seconds() / 60) if exported_time else None
    evidence = [_rel(repo_root, path)]
    if exported_at:
        evidence.append(f"exported_at={exported_at}")
    observed_date = str(payload.get("observed_date") or "") or None
    if observed_date:
        evidence.append(f"observed_date={observed_date}")
    is_fresh = age_minutes is not None and age_minutes <= threshold_minutes
    return ExportedStatusObservation(
        path=_rel(repo_root, path),
        observed_date=observed_date,
        exported_at=exported_at or None,
        age_minutes=age_minutes,
        is_fresh=is_fresh,
        evidence=evidence,
    )


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
) -> Incident:
    key = _incident_key(dispatch, classification, affected_date, status)
    created_at = str((existing or {}).get("detected_at") or detected_at)
    return Incident(
        incident_id=str((existing or {}).get("incident_id") or _incident_id(key)),
        incident_key=key,
        dispatch=dispatch,
        detected_at=created_at,
        updated_at=detected_at,
        state=IncidentState.OPEN.value,
        classification=classification.value,
        status_state=status.state,
        recovery_disposition=plan.disposition,
        recovery_action=plan.action,
        evidence=sorted(dict.fromkeys(evidence)),
        recommended_action=recommended_action,
        affected_date=affected_date or status.date,
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
    )


def check_operator(
    *,
    repo_root: Path = ROOT,
    operator_root: Path = DEFAULT_OPERATOR_ROOT,
    config: OperatorConfig | None = None,
    now: datetime | None = None,
    write_ledger: bool = True,
) -> OperatorResult:
    config = config or load_config(operator_root / "config.json")
    now = now or _utc_now()
    checked_at = _format_time(now)
    open_incidents = _load_open_incidents(operator_root)
    current_keys: set[str] = set()
    dispatch_results: list[DispatchResult] = []
    incidents: list[Incident] = []

    for dispatch in DISPATCH_ORDER:
        dispatch_config = config.dispatches[dispatch]
        if not dispatch_config.enabled:
            continue
        exported = _latest_exported_status(
            repo_root,
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
            recommended = _recommended_action(classification, status, plan)
            evidence = [*exported.evidence, *status.evidence, *plan.evidence]
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
    if write_ledger:
        _write_ledger(operator_root, result)
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_ledger(operator_root: Path, result: OperatorResult) -> None:
    operator_root.mkdir(parents=True, exist_ok=True)
    _write_json(operator_root / "latest.json", result.to_payload())
    history = operator_root / "history.jsonl"
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.to_payload(), sort_keys=True) + "\n")
    for incident in result.incidents:
        _write_json(operator_root / "incidents" / f"{incident.incident_id}.json", incident.to_payload())


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
