from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from .operational_health import OperationalStatus, parse_timestamp
from .scheduled_recovery import PUBLICATION_TASK_KEYS, RecoveryRecommendation, RetryPolicy, evaluate_recovery


PLAN_SCHEMA_VERSION = "bluefern_recovery_execution_plan_v1"
RECEIPT_SCHEMA_VERSION = "bluefern_recovery_execution_receipt_v1"
LEDGER_ROOT = Path("status") / "operational-recovery"
PRODUCTION_BRANCH = "add/pages-repo-default"
TERMINAL_SUPPRESSING_RECOMMENDATIONS = {
    RecoveryRecommendation.NO_ACTION.value,
    RecoveryRecommendation.UPSTREAM_BLOCKED.value,
    RecoveryRecommendation.MANUAL_ATTENTION.value,
}


class ExecutionDecision(StrEnum):
    DRY_RUN = "DRY_RUN"
    EXECUTION_READY = "EXECUTION_READY"
    EXECUTION_UNSUPPORTED = "EXECUTION_UNSUPPORTED"
    EXECUTION_DENIED_PUBLICATION = "EXECUTION_DENIED_PUBLICATION"
    EXECUTION_DENIED_NOT_RETRY_ELIGIBLE = "EXECUTION_DENIED_NOT_RETRY_ELIGIBLE"
    EXECUTION_DENIED_ATTEMPT_LIMIT = "EXECUTION_DENIED_ATTEMPT_LIMIT"
    EXECUTION_DEFERRED_BACKOFF = "EXECUTION_DEFERRED_BACKOFF"
    EXECUTION_DENIED_RECOVERY_WINDOW_EXPIRED = "EXECUTION_DENIED_RECOVERY_WINDOW_EXPIRED"
    EXECUTION_DENIED_INVALID_RECOVERY_CONTRACT = "EXECUTION_DENIED_INVALID_RECOVERY_CONTRACT"
    EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK = "EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK"
    EXECUTION_DENIED_UNSAFE_RUNNER_STATE = "EXECUTION_DENIED_UNSAFE_RUNNER_STATE"
    STALE_PLAN_SUPPRESSED = "STALE_PLAN_SUPPRESSED"
    NO_CANDIDATE = "NO_CANDIDATE"


class PostExecutionStatus(StrEnum):
    NOT_EXECUTED = "NOT_EXECUTED"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    RECOVERY_DEGRADED_TERMINAL = "RECOVERY_DEGRADED_TERMINAL"
    RECOVERY_UPSTREAM_BLOCKED = "RECOVERY_UPSTREAM_BLOCKED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RECOVERY_UNPROVEN = "RECOVERY_UNPROVEN"


@dataclass(frozen=True)
class CommandResult:
    category: str
    exit_code: int | None


@dataclass(frozen=True)
class ExecutionOptions:
    dispatch: str
    source_root: Path
    date: str
    evaluated_at: str
    execute: bool = False
    proof_root: Path | None = None
    expected_branch: str = PRODUCTION_BRANCH
    python: Path = Path(sys.executable)
    policy: RetryPolicy = RetryPolicy()


@dataclass(frozen=True)
class Adapter:
    name: str
    supported_for_planning: bool
    supported_for_automatic_execution: bool
    unsupported_reason: str = ""
    verification_task_keys: tuple[str, ...] = ()

    def argv(self, options: ExecutionOptions, plan: dict[str, Any]) -> list[str]:
        raise NotImplementedError


@dataclass(frozen=True)
class FoodResumeAdapter(Adapter):
    name: str = "food_source_watch_status_resume"
    supported_for_planning: bool = True
    supported_for_automatic_execution: bool = True
    unsupported_reason: str = ""
    verification_task_keys: tuple[str, ...] = ("food_line_source_watch_resume",)

    def argv(self, options: ExecutionOptions, plan: dict[str, Any]) -> list[str]:
        script = options.source_root / "scripts" / "food_line_daily_scheduler.py"
        return [
            str(options.python),
            str(script),
            "resume",
            "--repo-root",
            str(options.source_root),
            "--python",
            str(options.python),
            "--edition-date",
            options.date,
            "--branch",
            options.expected_branch,
        ]


@dataclass(frozen=True)
class UnsupportedAdapter(Adapter):
    pass


ADAPTERS: dict[tuple[str, str], Adapter] = {
    ("food-line", "food_line_source_watch"): FoodResumeAdapter(),
    ("food-line", "food_line_source_watch_resume"): FoodResumeAdapter(),
    (
        "care-line",
        "care_line_collection",
    ): UnsupportedAdapter(
        name="care_collection_planning_only",
        supported_for_planning=True,
        supported_for_automatic_execution=False,
        unsupported_reason=(
            "Care collection has no audited same-instance automatic retry contract in source: "
            "instance-addressability/idempotency and duplicate suppression require a separate proof."
        ),
    ),
    (
        "ice",
        "ice_monitor",
    ): UnsupportedAdapter(
        name="ice_monitor_planning_only",
        supported_for_planning=True,
        supported_for_automatic_execution=False,
        unsupported_reason=(
            "ICE monitor has no audited automatic same-instance retry contract yet: Pacific scheduled "
            "instance identity is preserved for planning, but production retry activation needs a separate proof."
        ),
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    parsed = parse_timestamp(str(value or ""))
    return _utc(parsed) if parsed else None


def _safe_component(value: str) -> str:
    if not value:
        return "unknown"
    if "/" in value or "\\" in value or value in {".", ".."} or value.startswith(".."):
        raise ValueError(f"unsafe recovery path component: {value!r}")
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)
    cleaned = cleaned.strip(".-")
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"unsafe recovery path component: {value!r}")
    return cleaned[:180]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def instance_ledger_dir(source_root: Path, dispatch: str, date: str, instance_id: str) -> Path:
    return source_root / LEDGER_ROOT / _safe_component(dispatch) / _safe_component(date) / _safe_component(instance_id)


def _attempt_files(ledger_dir: Path) -> list[Path]:
    return sorted((ledger_dir / "attempts").glob("*.json"))


def load_attempts(source_root: Path, dispatch: str, date: str, instance_id: str) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for path in _attempt_files(instance_ledger_dir(source_root, dispatch, date, instance_id)):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") == RECEIPT_SCHEMA_VERSION:
            attempts.append(value)
    return attempts


def attempt_count(source_root: Path, dispatch: str, date: str, instance_id: str) -> int:
    return len(load_attempts(source_root, dispatch, date, instance_id))


def _latest_completed_attempt(attempts: list[dict[str, Any]]) -> datetime | None:
    times = [_parse_utc(str(item.get("completed_at") or "")) for item in attempts]
    present = [item for item in times if item is not None]
    return max(present) if present else None


def _source_head(source_root: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return "UNKNOWN"
    return result.stdout.strip()


def _build_attempts_map(source_root: Path, dispatch: str, date: str, report: dict[str, Any]) -> dict[str, int]:
    return {
        str(row["instance_id"]): attempt_count(source_root, dispatch, date, str(row["instance_id"]))
        for row in report.get("instances", [])
    }


def _report_with_ledger_attempts(options: ExecutionOptions) -> dict[str, Any]:
    return evaluate_recovery(
        dispatch=options.dispatch,
        source_root=options.source_root,
        date=options.date,
        evaluated_at=options.evaluated_at,
        policy=options.policy,
    )


def _candidate_sort_key(row: dict[str, Any]) -> tuple[str, int, str]:
    scheduled = str(row.get("scheduled_for") or row.get("instance_id") or "")
    dependency_order = {
        "food_line_source_watch": 0,
        "food_line_source_watch_resume": 1,
        "care_line_collection": 2,
        "ice_monitor": 3,
    }.get(str(row.get("task_key") or ""), 99)
    return scheduled, dependency_order, str(row.get("task_key") or "")


def select_candidate(report: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates = [
        row for row in report.get("instances", [])
        if row.get("recommendation") == RecoveryRecommendation.RETRY_ELIGIBLE.value
    ]
    ordered = sorted(candidates, key=_candidate_sort_key)
    selected = ordered[0] if ordered else None
    deferred = ordered[1:] if len(ordered) > 1 else []
    return selected, deferred


def _evaluator_deadline(value: Any) -> str | None:
    parsed = _parse_utc(str(value or ""))
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _plan_for_row(options: ExecutionOptions, report: dict[str, Any], row: dict[str, Any], deferred: list[dict[str, Any]]) -> dict[str, Any]:
    adapter = ADAPTERS.get((options.dispatch, str(row.get("task_key") or "")))
    attempts = load_attempts(options.source_root, options.dispatch, options.date, str(row["instance_id"]))
    latest_completed = _latest_completed_attempt(attempts)
    earliest = (
        latest_completed + timedelta(minutes=options.policy.min_backoff_minutes)
        if latest_completed is not None else None
    )
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": f"recovery-plan-{uuid.uuid4().hex[:12]}",
        "dispatch": options.dispatch,
        "task_key": row["task_key"],
        "scheduled_instance": row["instance_id"],
        "scheduled_for": row.get("scheduled_for"),
        "evaluated_at": options.evaluated_at,
        "recommendation": row.get("recommendation"),
        "original_status": row.get("receipt_status"),
        "original_classification": row.get("classification"),
        "recovery_adapter": adapter.name if adapter else "",
        "max_attempts": options.policy.max_attempts,
        "prior_attempt_count": len(attempts),
        "earliest_allowed_retry": earliest.isoformat().replace("+00:00", "Z") if earliest else None,
        "grace_end": _evaluator_deadline(row.get("grace_end")),
        "recovery_deadline": _evaluator_deadline(row.get("recovery_deadline")),
        "execute_requested": options.execute,
        "executable": False,
        "denial_reason": "",
        "source_head": _source_head(options.source_root),
        "public_side_effect_expected": False,
        "supported_for_planning": bool(adapter and adapter.supported_for_planning),
        "supported_for_automatic_execution": bool(adapter and adapter.supported_for_automatic_execution),
        "deferred_candidates": [
            {"task_key": item.get("task_key"), "scheduled_instance": item.get("instance_id")}
            for item in deferred
        ],
        "verification_task_keys": list(adapter.verification_task_keys) if adapter else [],
        "command_argv": [],
    }
    if adapter and adapter.supported_for_automatic_execution:
        plan["command_argv"] = adapter.argv(options, plan)
    return plan


def _denied_plan(options: ExecutionOptions, report: dict[str, Any], reason: ExecutionDecision) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": f"recovery-plan-{uuid.uuid4().hex[:12]}",
        "dispatch": options.dispatch,
        "task_key": "",
        "scheduled_instance": "",
        "scheduled_for": None,
        "evaluated_at": options.evaluated_at,
        "recommendation": report.get("overall_recommendation"),
        "original_status": None,
        "original_classification": None,
        "recovery_adapter": "",
        "max_attempts": options.policy.max_attempts,
        "prior_attempt_count": 0,
        "earliest_allowed_retry": None,
        "recovery_deadline": None,
        "execute_requested": options.execute,
        "executable": False,
        "denial_reason": reason.value,
        "source_head": _source_head(options.source_root),
        "public_side_effect_expected": False,
        "supported_for_planning": False,
        "supported_for_automatic_execution": False,
        "deferred_candidates": [],
        "verification_task_keys": [],
        "command_argv": [],
    }


def build_execution_plan(options: ExecutionOptions) -> tuple[dict[str, Any], dict[str, Any]]:
    report = _report_with_ledger_attempts(options)
    selected, deferred = select_candidate(report)
    if selected is None:
        return _denied_plan(options, report, ExecutionDecision.NO_CANDIDATE), report
    return _plan_for_row(options, report, selected, deferred), report


def _decision(options: ExecutionOptions, plan: dict[str, Any], *, preflight_ok: bool, stale_report: dict[str, Any] | None = None) -> ExecutionDecision:
    if not plan.get("task_key"):
        return ExecutionDecision.NO_CANDIDATE
    if plan.get("task_key") in PUBLICATION_TASK_KEYS:
        return ExecutionDecision.EXECUTION_DENIED_PUBLICATION
    if plan.get("recommendation") != RecoveryRecommendation.RETRY_ELIGIBLE.value:
        return ExecutionDecision.EXECUTION_DENIED_NOT_RETRY_ELIGIBLE
    if not plan.get("supported_for_planning") or not plan.get("supported_for_automatic_execution"):
        return ExecutionDecision.EXECUTION_UNSUPPORTED
    if _parse_utc(plan.get("recovery_deadline")) is None:
        return ExecutionDecision.EXECUTION_DENIED_INVALID_RECOVERY_CONTRACT
    if int(plan.get("prior_attempt_count") or 0) >= int(plan.get("max_attempts") or 0):
        return ExecutionDecision.EXECUTION_DENIED_ATTEMPT_LIMIT
    now = _parse_utc(options.evaluated_at)
    earliest = _parse_utc(plan.get("earliest_allowed_retry"))
    if now and earliest and now < earliest:
        return ExecutionDecision.EXECUTION_DEFERRED_BACKOFF
    deadline = _parse_utc(plan.get("recovery_deadline"))
    if now and deadline and now > deadline:
        return ExecutionDecision.EXECUTION_DENIED_RECOVERY_WINDOW_EXPIRED
    ledger_dir = instance_ledger_dir(options.source_root, options.dispatch, options.date, str(plan["scheduled_instance"]))
    if (ledger_dir / "recovery.lock").exists():
        return ExecutionDecision.EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK
    if stale_report is not None:
        row = next((item for item in stale_report.get("instances", []) if item.get("instance_id") == plan["scheduled_instance"]), None)
        if row is None or row.get("recommendation") != RecoveryRecommendation.RETRY_ELIGIBLE.value:
            return ExecutionDecision.STALE_PLAN_SUPPRESSED
    if not preflight_ok:
        return ExecutionDecision.EXECUTION_DENIED_UNSAFE_RUNNER_STATE
    if not options.execute:
        return ExecutionDecision.DRY_RUN
    return ExecutionDecision.EXECUTION_READY


def default_preflight_checker(source_root: Path, expected_branch: str) -> tuple[bool, str]:
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=source_root, capture_output=True, text=True, check=False)
    if branch.returncode != 0 or branch.stdout.strip() != expected_branch:
        return False, f"branch mismatch: expected {expected_branch}, found {branch.stdout.strip() or '<unknown>'}"
    script = source_root / "scripts" / "preflight_repo_state.py"
    result = subprocess.run([sys.executable, str(script), "--source-repo", str(source_root)], cwd=source_root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, "repository preflight failed"
    return True, "repository preflight passed"


def _stale_report(options: ExecutionOptions) -> dict[str, Any]:
    return evaluate_recovery(
        dispatch=options.dispatch,
        source_root=options.source_root,
        date=options.date,
        evaluated_at=options.evaluated_at,
        policy=options.policy,
    )


def _receipt_fingerprint(path: Path, receipt: dict[str, Any]) -> str:
    return "|".join(
        [
            path.name,
            str(receipt.get("task_key") or ""),
            str(receipt.get("scheduled_for") or ""),
            str(receipt.get("run_id") or ""),
            str(receipt.get("started_at") or ""),
            str(receipt.get("completed_at") or ""),
            str(receipt.get("observed_at") or ""),
            str(receipt.get("status") or ""),
        ]
    )


def _verification_receipts(options: ExecutionOptions, plan: dict[str, Any]) -> list[tuple[Path, dict[str, Any], str]]:
    task_keys = set(str(item) for item in plan.get("verification_task_keys") or ())
    if not task_keys:
        task_keys = {str(plan.get("task_key") or "")}
    root = options.source_root / "status" / "operational-health" / options.dispatch / options.date / "runs"
    records: list[tuple[Path, dict[str, Any], str]] = []
    for path in sorted(root.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if receipt.get("task_key") not in task_keys:
            continue
        scheduled_for = str(receipt.get("scheduled_for") or "")
        if scheduled_for and not scheduled_for.startswith(options.date):
            continue
        records.append((path, receipt, _receipt_fingerprint(path, receipt)))
    return records


def _classify_post_execution(options: ExecutionOptions, plan: dict[str, Any], before_fingerprints: set[str]) -> tuple[PostExecutionStatus, dict[str, Any] | None]:
    relevant = [
        (path, receipt, fingerprint)
        for path, receipt, fingerprint in _verification_receipts(options, plan)
        if fingerprint not in before_fingerprints
    ]
    if not relevant:
        return PostExecutionStatus.RECOVERY_UNPROVEN, None
    _path, latest, _fingerprint = max(
        relevant,
        key=lambda item: _parse_utc(str(item[1].get("completed_at") or item[1].get("observed_at") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    status = latest.get("status")
    if status in {OperationalStatus.SUCCESS.value, OperationalStatus.SAFE_NO_OP.value}:
        return PostExecutionStatus.RECOVERY_CONFIRMED, latest
    if status == OperationalStatus.DEGRADED.value:
        return PostExecutionStatus.RECOVERY_DEGRADED_TERMINAL, latest
    if status == OperationalStatus.UPSTREAM_BLOCKED.value:
        return PostExecutionStatus.RECOVERY_UPSTREAM_BLOCKED, latest
    if status == OperationalStatus.FAILED.value:
        return PostExecutionStatus.RECOVERY_FAILED, latest
    return PostExecutionStatus.RECOVERY_UNPROVEN, latest


def _write_attempt_receipt(
    options: ExecutionOptions,
    plan: dict[str, Any],
    *,
    started_at: str,
    completed_at: str,
    command_result: CommandResult,
    post_status: PostExecutionStatus,
    observed_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    ledger_dir = instance_ledger_dir(options.source_root, options.dispatch, options.date, str(plan["scheduled_instance"]))
    attempt_number = attempt_count(options.source_root, options.dispatch, options.date, str(plan["scheduled_instance"])) + 1
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "recovery_attempt_id": f"recovery-{uuid.uuid4().hex[:12]}",
        "dispatch": options.dispatch,
        "original_task_key": plan["task_key"],
        "scheduled_instance": plan["scheduled_instance"],
        "adapter": plan.get("recovery_adapter"),
        "attempt_number": attempt_number,
        "planned_at": plan["evaluated_at"],
        "started_at": started_at,
        "completed_at": completed_at,
        "source_head": plan.get("source_head"),
        "evaluator_recommendation": plan.get("recommendation"),
        "original_status": plan.get("original_status"),
        "original_classification": plan.get("original_classification"),
        "command_result_category": command_result.category,
        "child_exit_code": command_result.exit_code,
        "post_execution_receipt_observation": post_status.value,
        "post_execution_task_status": observed_receipt.get("status") if observed_receipt else None,
        "public_side_effects": False,
        "execution_mode": "execute" if options.execute else "dry-run",
    }
    path = ledger_dir / "attempts" / f"{attempt_number:02d}-{receipt['recovery_attempt_id']}.json"
    _atomic_write_json(path, receipt)
    _atomic_write_json(ledger_dir / "latest.json", receipt)
    return receipt


def _write_proof(options: ExecutionOptions, payload: dict[str, Any]) -> None:
    if options.proof_root is None:
        return
    _atomic_write_json(options.proof_root / "recovery-executor-report.json", payload)


def run_executor(
    options: ExecutionOptions,
    *,
    command_runner: Callable[[list[str], Path], CommandResult] | None = None,
    preflight_checker: Callable[[Path, str], tuple[bool, str]] = default_preflight_checker,
) -> dict[str, Any]:
    plan, report = build_execution_plan(options)
    preflight_ok, preflight_message = preflight_checker(options.source_root, options.expected_branch)
    stale = _stale_report(options) if plan.get("task_key") else None
    decision = _decision(options, plan, preflight_ok=preflight_ok, stale_report=stale)
    plan["executable"] = decision in {ExecutionDecision.DRY_RUN, ExecutionDecision.EXECUTION_READY}
    plan["denial_reason"] = "" if plan["executable"] else decision.value
    result: dict[str, Any] = {
        "schema_version": "bluefern_recovery_execution_report_v1",
        "decision": decision.value,
        "plan": plan,
        "evaluator_report": report,
        "preflight": {"ok": preflight_ok, "message": preflight_message},
        "execution_receipt": None,
    }
    if decision != ExecutionDecision.EXECUTION_READY:
        _write_proof(options, result)
        return result

    ledger_dir = instance_ledger_dir(options.source_root, options.dispatch, options.date, str(plan["scheduled_instance"]))
    lock_path = ledger_dir / "recovery.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps({"plan_id": plan["plan_id"], "created_at": utc_now()}) + "\n")
    except FileExistsError:
        result["decision"] = ExecutionDecision.EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK.value
        _write_proof(options, result)
        return result

    before_fingerprints = {fingerprint for _path, _receipt, fingerprint in _verification_receipts(options, plan)}
    started_at = utc_now()
    runner = command_runner or _subprocess_runner
    try:
        command_result = runner(list(plan["command_argv"]), options.source_root)
        post_status, observed = _classify_post_execution(options, plan, before_fingerprints)
        completed_at = utc_now()
        result["execution_receipt"] = _write_attempt_receipt(
            options,
            plan,
            started_at=started_at,
            completed_at=completed_at,
            command_result=command_result,
            post_status=post_status,
            observed_receipt=observed,
        )
        result["decision"] = post_status.value
    finally:
        lock_path.unlink(missing_ok=True)
    _write_proof(options, result)
    return result


def _subprocess_runner(argv: list[str], cwd: Path) -> CommandResult:
    result = subprocess.run(argv, cwd=cwd, shell=False, check=False)
    return CommandResult(category="child_process_completed", exit_code=result.returncode)
