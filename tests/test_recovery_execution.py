from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bluefern_dispatches.operational_health import OperationalStatus, build_operational_receipt
from bluefern_dispatches.recovery_execution import (
    CommandResult,
    ExecutionOptions,
    instance_ledger_dir,
    run_executor,
    _subprocess_runner,
)


DATE = "2026-09-10"
EVALUATED = "2026-09-10T15:00:00Z"
ROOT = Path(__file__).resolve().parents[1]


def _ok_preflight(_root: Path, _branch: str) -> tuple[bool, str]:
    return True, "ok"


def _bad_preflight(_root: Path, _branch: str) -> tuple[bool, str]:
    return False, "unsafe"


def _write_receipt(
    root: Path,
    *,
    task_key: str = "food_line_source_watch",
    status: OperationalStatus = OperationalStatus.FAILED,
    classification: str = "provider_timeout",
    started: str = "2026-09-10T12:30:00Z",
    completed: str | None = None,
    dispatch: str = "food-line",
    name: str | None = None,
    exit_code: int | None = None,
    run_id: str | None = None,
    details: dict[str, object] | None = None,
    task_receipt: dict[str, object] | None = None,
) -> None:
    artifact = root / "logs" / "food-line" / "test-operational-artifacts" / f"{name or task_key}-artifact.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(task_receipt or {}) + "\n", encoding="utf-8")
    receipt = build_operational_receipt(
        dispatch=dispatch,
        task_key=task_key,
        task_name=task_key,
        scheduled_for=DATE,
        started_at=started,
        completed_at=completed or started,
        exit_code=exit_code if exit_code is not None else 0 if status in {OperationalStatus.SUCCESS, OperationalStatus.SAFE_NO_OP} else 1,
        status=status,
        classification=classification,
        run_id=run_id or name or f"{task_key}-run",
        public_side_effects={},
        artifact_refs={"task_receipt": str(artifact)},
        details=details or {},
    )
    path = root / "status" / "operational-health" / dispatch / DATE / "runs" / f"{name or task_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")


def _write_qualifying_source_state(root: Path, *, date: str = DATE, run_id: str = "source-watch-run") -> None:
    export_path = root / "data" / "dispatches" / "food-line" / "agent-inbox" / f"food-line-source-watch-{date}-{run_id}.json"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_source_watch_agent_export_v1",
                "agent_name": "Food Line Source Watch",
                "agent_run_id": run_id,
                "edition_date": date,
                "findings": [
                    {
                        "title": "Pantry closes after supply loss",
                        "publisher": "Example News",
                        "source_url": "https://example.org/current-food-pressure",
                        "canonical_source_url": "https://example.org/current-food-pressure",
                        "exact_supporting_passage": "The pantry closed Friday after losing its remaining food supply.",
                        "summary": "Example News reports that a local pantry closed after losing its food supply.",
                        "location_name": "Example City",
                        "state": "CA",
                        "location_scope": "city",
                        "pressure_type": "service_closure",
                        "source_role": "local_signal",
                        "evidence_level": "direct_reporting",
                        "source_published_date": date,
                        "source_published_at": f"{date}T08:00:00-07:00",
                        "affected_groups": ["pantry clients"],
                        "why_it_matters": "A food-access point is no longer operating.",
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    record = {
        "schema_version": "food_line_scheduled_run_record_v1",
        "edition_date": date,
        "run_id": run_id,
        "source_commit": "test-source-commit",
        "source_branch": "add/pages-repo-default",
        "source_watch_status": "completed_with_exclusions",
        "last_status": "completed_with_exclusions",
        "run_state_path": str(root / "data" / "dispatches" / "food-line" / "discovery-runs" / date / run_id / "run-state.json"),
    }
    state = {
        "schema_version": "food_line_bounded_run_state_v1",
        "edition_date": date,
        "run_id": run_id,
        "status": "completed_with_exclusions",
        "partitions_total": 1,
        "partitions_completed": 1,
        "coverage": {"required_success_ratio": 1.0, "direct_success_ratio": 1.0},
        "options": {"required_coverage_threshold": 0.9, "direct_source_coverage_threshold": 0.75},
        "agent_export": {"status": "success", "path": str(export_path), "sha256": "export-sha"},
        "final_error": "",
    }
    record_path = root / "status" / "food-line" / "runs" / f"{date}.json"
    state_path = root / "data" / "dispatches" / "food-line" / "discovery-runs" / date / run_id / "run-state.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _write_dependency_recovered_intake_state(root: Path, *, date: str = DATE, run_id: str = "source-watch-run") -> None:
    _write_receipt(
        root,
        task_key="food_line_current_intake",
        status=OperationalStatus.UPSTREAM_BLOCKED,
        classification="upstream_blocked",
        started="2026-09-10T13:10:00Z",
        name="01-intake-blocked",
        run_id=run_id,
        details={
            "edition_date": date,
            "qualifying_discovery_run_id": run_id,
            "source_status": "blocked_overlapping_run",
            "intake_status": "upstream_blocked",
        },
        task_receipt={
            "schema_version": "food_line_current_intake_receipt_v1",
            "edition_date": date,
            "qualifying_discovery_run_id": run_id,
            "status": "upstream_blocked",
        },
    )
    _write_qualifying_source_state(root, date=date, run_id=run_id)
    _write_receipt(
        root,
        task_key="food_line_source_watch_resume",
        status=OperationalStatus.SUCCESS,
        classification="resume_qualified",
        started="2026-09-10T13:20:00Z",
        name="02-resume-qualified",
        run_id=run_id,
        details={"edition_date": date, "source_status": "completed_with_exclusions", "source_export_status": "success"},
        task_receipt={
            "schema_version": "food_line_source_watch_receipt_v1",
            "action": "status_resume",
            "edition_date": date,
            "run_id": run_id,
            "final_status": "completed_with_exclusions",
            "resume_status": "resume_qualified",
            "export_status": "success",
            "exit_code": 0,
        },
    )


def _run(root: Path, *, execute: bool = False, dispatch: str = "food-line", evaluated_at: str = EVALUATED, runner=None, preflight=_ok_preflight, proof_root: Path | None = None):
    return run_executor(
        ExecutionOptions(
            dispatch=dispatch,
            source_root=root,
            date=DATE,
            evaluated_at=evaluated_at,
            execute=execute,
            proof_root=proof_root,
        ),
        command_runner=runner,
        preflight_checker=preflight,
    )


@pytest.mark.parametrize(
    ("status", "classification", "decision"),
    [
        (OperationalStatus.SUCCESS, "completed", "NO_CANDIDATE"),
        (OperationalStatus.DEGRADED, "completed_with_exclusions", "NO_CANDIDATE"),
        (OperationalStatus.UPSTREAM_BLOCKED, "source_watch_not_initialized", "NO_CANDIDATE"),
        (OperationalStatus.FAILED, "invalid_configuration", "NO_CANDIDATE"),
    ],
)
def test_non_retryable_evaluator_recommendations_do_not_execute(tmp_path: Path, status: OperationalStatus, classification: str, decision: str) -> None:
    _write_receipt(tmp_path, status=status, classification=classification)

    result = _run(tmp_path)

    assert result["decision"] == decision
    assert result["execution_receipt"] is None


def test_waiting_and_missed_states_do_not_execute(tmp_path: Path) -> None:
    waiting = _run(tmp_path, evaluated_at="2026-09-10T12:00:00Z")
    missed = _run(tmp_path, dispatch="ice", evaluated_at="2026-09-11T12:00:00Z")

    assert waiting["decision"] == "NO_CANDIDATE"
    assert missed["decision"] == "NO_CANDIDATE"


def test_retry_eligible_source_watch_plans_existing_status_resume_not_source_watch(tmp_path: Path) -> None:
    _write_receipt(tmp_path)

    result = _run(tmp_path)
    plan = result["plan"]

    assert result["decision"] == "DRY_RUN"
    assert plan["task_key"] == "food_line_source_watch"
    assert plan["recovery_adapter"] == "food_source_watch_status_resume"
    assert "resume" in plan["command_argv"]
    assert "source-watch" not in plan["command_argv"]
    assert plan["public_side_effect_expected"] is False
    assert plan["grace_end"] == "2026-09-10T14:00:00Z"
    assert plan["recovery_deadline"] == "2026-09-10T18:00:00Z"
    assert plan["verification_task_keys"] == ["food_line_source_watch_resume"]


def test_retryable_resume_stage_uses_status_resume_adapter(tmp_path: Path) -> None:
    _write_receipt(tmp_path, task_key="food_line_source_watch_resume", started="2026-09-10T13:00:00Z")

    result = _run(tmp_path)

    assert result["decision"] == "DRY_RUN"
    assert result["plan"]["task_key"] == "food_line_source_watch_resume"
    assert "resume" in result["plan"]["command_argv"]


def test_dependency_recovered_current_intake_selects_intake_adapter(tmp_path: Path) -> None:
    _write_dependency_recovered_intake_state(tmp_path)

    result = _run(tmp_path)
    plan = result["plan"]

    assert result["decision"] == "DRY_RUN"
    assert plan["task_key"] == "food_line_current_intake"
    assert plan["recovery_adapter"] == "food_current_intake_dependency_recovered"
    assert "intake" in plan["command_argv"]
    assert "publish" not in plan["command_argv"]
    assert plan["verification_task_keys"] == ["food_line_current_intake"]
    assert plan["supported_for_automatic_execution"] is True


def test_subprocess_runner_uses_argv_without_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(argv, *, cwd, shell, check, capture_output, text):
        calls.append({"argv": argv, "cwd": cwd, "shell": shell, "check": check, "capture_output": capture_output, "text": text})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = _subprocess_runner(["python", "script.py"], tmp_path)

    assert result == CommandResult(category="child_process_completed", exit_code=0)
    assert calls == [
        {"argv": ["python", "script.py"], "cwd": tmp_path, "shell": False, "check": False, "capture_output": True, "text": True}
    ]


def test_publication_retry_eligible_is_hard_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from bluefern_dispatches import recovery_execution

    monkeypatch.setattr(
        recovery_execution,
        "build_execution_plan",
        lambda options: (
            {
                "schema_version": "bluefern_recovery_execution_plan_v1",
                "plan_id": "malformed-publication",
                "dispatch": "food-line",
                "task_key": "food_line_daily_publish",
                "scheduled_instance": "2026-09-10:food_line_daily_publish",
                "scheduled_for": "2026-09-10T15:30:00Z",
                "evaluated_at": EVALUATED,
                "recommendation": "RETRY_ELIGIBLE",
                "original_status": "FAILED",
                "original_classification": "provider_timeout",
                "recovery_adapter": "malformed",
                "max_attempts": 2,
                "prior_attempt_count": 0,
                "earliest_allowed_retry": None,
                "recovery_deadline": "2026-09-10T19:30:00Z",
                "execute_requested": True,
                "executable": False,
                "denial_reason": "",
                "source_head": "HEAD",
                "public_side_effect_expected": False,
                "supported_for_planning": True,
                "supported_for_automatic_execution": True,
                "deferred_candidates": [],
                "verification_task_keys": [],
                "command_argv": ["malformed"],
            },
            {"instances": []},
        ),
    )

    result = _run(tmp_path, execute=True)

    assert result["decision"] == "EXECUTION_DENIED_PUBLICATION"


def test_missing_evaluator_deadline_fails_closed_for_retry_eligible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from bluefern_dispatches import recovery_execution

    monkeypatch.setattr(
        recovery_execution,
        "build_execution_plan",
        lambda options: (
            {
                "schema_version": "bluefern_recovery_execution_plan_v1",
                "plan_id": "missing-deadline",
                "dispatch": "food-line",
                "task_key": "food_line_source_watch",
                "scheduled_instance": "2026-09-10:food_line_source_watch",
                "scheduled_for": "2026-09-10T12:30:00Z",
                "evaluated_at": EVALUATED,
                "recommendation": "RETRY_ELIGIBLE",
                "original_status": "FAILED",
                "original_classification": "provider_timeout",
                "recovery_adapter": "food_source_watch_status_resume",
                "max_attempts": 2,
                "prior_attempt_count": 0,
                "earliest_allowed_retry": None,
                "grace_end": "2026-09-10T14:00:00Z",
                "recovery_deadline": None,
                "execute_requested": True,
                "executable": False,
                "denial_reason": "",
                "source_head": "HEAD",
                "public_side_effect_expected": False,
                "supported_for_planning": True,
                "supported_for_automatic_execution": True,
                "deferred_candidates": [],
                "verification_task_keys": ["food_line_source_watch_resume"],
                "command_argv": ["fixed"],
            },
            {"instances": []},
        ),
    )

    result = _run(tmp_path, execute=True)

    assert result["decision"] == "EXECUTION_DENIED_INVALID_RECOVERY_CONTRACT"


def test_executor_uses_evaluator_deadline_without_shortening_by_grace(tmp_path: Path) -> None:
    _write_receipt(tmp_path)

    still_open = _run(tmp_path, evaluated_at="2026-09-10T16:31:00Z")
    expired = _run(tmp_path, evaluated_at="2026-09-10T18:01:00Z")

    assert still_open["decision"] == "DRY_RUN"
    assert still_open["plan"]["recovery_deadline"] == "2026-09-10T18:00:00Z"
    assert expired["decision"] == "NO_CANDIDATE"


def test_executor_denies_retry_eligible_plan_after_evaluator_deadline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from bluefern_dispatches import recovery_execution

    monkeypatch.setattr(
        recovery_execution,
        "build_execution_plan",
        lambda options: (
            {
                "schema_version": "bluefern_recovery_execution_plan_v1",
                "plan_id": "expired-deadline",
                "dispatch": "food-line",
                "task_key": "food_line_source_watch",
                "scheduled_instance": "2026-09-10:food_line_source_watch",
                "scheduled_for": "2026-09-10T12:30:00Z",
                "evaluated_at": "2026-09-10T18:01:00Z",
                "recommendation": "RETRY_ELIGIBLE",
                "original_status": "FAILED",
                "original_classification": "provider_timeout",
                "recovery_adapter": "food_source_watch_status_resume",
                "max_attempts": 2,
                "prior_attempt_count": 0,
                "earliest_allowed_retry": None,
                "grace_end": "2026-09-10T14:00:00Z",
                "recovery_deadline": "2026-09-10T18:00:00Z",
                "execute_requested": True,
                "executable": False,
                "denial_reason": "",
                "source_head": "HEAD",
                "public_side_effect_expected": False,
                "supported_for_planning": True,
                "supported_for_automatic_execution": True,
                "deferred_candidates": [],
                "verification_task_keys": ["food_line_source_watch_resume"],
                "command_argv": ["fixed"],
            },
            {"instances": []},
        ),
    )

    result = _run(tmp_path, execute=True, evaluated_at="2026-09-10T18:01:00Z")

    assert result["decision"] == "EXECUTION_DENIED_RECOVERY_WINDOW_EXPIRED"


def test_attempt_count_persists_and_limits_after_two_attempts(tmp_path: Path) -> None:
    _write_receipt(tmp_path)
    first = _run(tmp_path, evaluated_at="2026-09-10T15:00:00Z")
    ledger = instance_ledger_dir(tmp_path, "food-line", DATE, first["plan"]["scheduled_instance"])
    (ledger / "attempts").mkdir(parents=True)
    for index in (1, 2):
        (ledger / "attempts" / f"0{index}-attempt.json").write_text(
            json.dumps({"schema_version": "bluefern_recovery_execution_receipt_v1", "completed_at": "2026-09-10T14:00:00Z"}),
            encoding="utf-8",
        )

    result = _run(tmp_path, evaluated_at="2026-09-10T15:00:00Z")

    assert result["decision"] == "EXECUTION_DENIED_ATTEMPT_LIMIT"


def test_backoff_before_and_after_30_minutes(tmp_path: Path) -> None:
    _write_receipt(tmp_path)
    initial = _run(tmp_path, evaluated_at="2026-09-10T15:00:00Z")
    ledger = instance_ledger_dir(tmp_path, "food-line", DATE, initial["plan"]["scheduled_instance"])
    (ledger / "attempts").mkdir(parents=True)
    (ledger / "attempts" / "01-attempt.json").write_text(
        json.dumps({"schema_version": "bluefern_recovery_execution_receipt_v1", "completed_at": "2026-09-10T14:45:00Z"}),
        encoding="utf-8",
    )

    early = _run(tmp_path, evaluated_at="2026-09-10T15:00:00Z")
    later = _run(tmp_path, evaluated_at="2026-09-10T15:16:00Z")

    assert early["decision"] == "EXECUTION_DEFERRED_BACKOFF"
    assert later["decision"] == "DRY_RUN"


@pytest.mark.parametrize("status", [OperationalStatus.SUCCESS, OperationalStatus.SAFE_NO_OP, OperationalStatus.DEGRADED])
def test_stale_plan_suppresses_if_new_terminal_receipt_arrives(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: OperationalStatus) -> None:
    _write_receipt(tmp_path, status=OperationalStatus.FAILED, classification="provider_timeout", name="01-failed")
    from bluefern_dispatches import recovery_execution

    original = recovery_execution._stale_report

    def fake_stale(options):
        _write_receipt(tmp_path, status=status, classification="completed", name="02-terminal", started="2026-09-10T12:45:00Z")
        return original(options)

    monkeypatch.setattr(recovery_execution, "_stale_report", fake_stale)

    result = _run(tmp_path, execute=True, runner=lambda _argv, _cwd: CommandResult("should_not_run", 0))

    assert result["decision"] == "STALE_PLAN_SUPPRESSED"


def test_active_lock_denies_execution(tmp_path: Path) -> None:
    _write_receipt(tmp_path)
    initial = _run(tmp_path)
    ledger = instance_ledger_dir(tmp_path, "food-line", DATE, initial["plan"]["scheduled_instance"])
    ledger.mkdir(parents=True)
    (ledger / "recovery.lock").write_text("{}\n", encoding="utf-8")

    result = _run(tmp_path, execute=True, runner=lambda _argv, _cwd: CommandResult("should_not_run", 0))

    assert result["decision"] == "EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK"


def test_unsafe_runner_state_denies_real_execution(tmp_path: Path) -> None:
    _write_receipt(tmp_path)

    result = _run(tmp_path, execute=True, preflight=_bad_preflight, runner=lambda _argv, _cwd: CommandResult("should_not_run", 0))

    assert result["decision"] == "EXECUTION_DENIED_UNSAFE_RUNNER_STATE"


def test_one_action_per_invocation_defers_other_candidates(tmp_path: Path) -> None:
    _write_receipt(tmp_path, task_key="food_line_source_watch")
    _write_receipt(tmp_path, task_key="food_line_source_watch_resume", started="2026-09-10T13:00:00Z")
    _write_dependency_recovered_intake_state(tmp_path)

    result = _run(tmp_path)

    assert result["plan"]["task_key"] == "food_line_source_watch"
    assert result["plan"]["deferred_candidates"] == [
        {"task_key": "food_line_current_intake", "scheduled_instance": "2026-09-10:food_line_current_intake"},
    ]


def test_dry_run_writes_only_explicit_proof_root(tmp_path: Path) -> None:
    _write_receipt(tmp_path)
    proof = tmp_path / "proof"

    result = _run(tmp_path, proof_root=proof)

    assert result["decision"] == "DRY_RUN"
    assert (proof / "recovery-executor-report.json").is_file()
    assert not (tmp_path / "status" / "operational-recovery").exists()


def test_execute_exit_zero_without_new_receipt_is_unproven_and_writes_ledger(tmp_path: Path) -> None:
    _write_receipt(tmp_path)

    result = _run(tmp_path, execute=True, runner=lambda _argv, _cwd: CommandResult("child_process_completed", 0))

    assert result["decision"] == "RECOVERY_UNPROVEN"
    assert result["execution_receipt"]["post_execution_receipt_observation"] == "RECOVERY_UNPROVEN"
    assert result["execution_receipt"]["public_side_effects"] is False


@pytest.mark.parametrize(
    ("status", "classification", "expected"),
    [
        (OperationalStatus.SUCCESS, "resume_qualified", "RECOVERY_CONFIRMED"),
        (OperationalStatus.SAFE_NO_OP, "resume_not_required", "RECOVERY_CONFIRMED"),
        (OperationalStatus.DEGRADED, "completed_with_exclusions", "RECOVERY_DEGRADED_TERMINAL"),
        (OperationalStatus.UPSTREAM_BLOCKED, "source_watch_not_initialized", "RECOVERY_UPSTREAM_BLOCKED"),
        (OperationalStatus.FAILED, "provider_timeout", "RECOVERY_FAILED"),
    ],
)
def test_source_watch_recovery_classifies_from_new_resume_receipt_even_when_exit_nonzero(
    tmp_path: Path,
    status: OperationalStatus,
    classification: str,
    expected: str,
) -> None:
    _write_receipt(tmp_path, status=OperationalStatus.FAILED, classification="provider_timeout", name="01-failed")

    def runner(_argv, _cwd):
        _write_receipt(
            tmp_path,
            task_key="food_line_source_watch_resume",
            status=status,
            classification=classification,
            name="02-resume-after",
            started="2026-09-10T13:00:00Z",
            exit_code=0 if status != OperationalStatus.FAILED else 1,
        )
        return CommandResult("child_process_completed", 1)

    result = _run(tmp_path, execute=True, runner=runner)

    assert result["decision"] == expected
    assert result["execution_receipt"]["post_execution_receipt_observation"] == expected


def test_old_resume_receipt_alone_cannot_confirm_source_watch_recovery(tmp_path: Path) -> None:
    _write_receipt(tmp_path, status=OperationalStatus.FAILED, classification="provider_timeout", name="01-failed")
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch_resume",
        status=OperationalStatus.SUCCESS,
        classification="resume_qualified",
        name="old-resume",
        started="2026-09-10T13:00:00Z",
        exit_code=0,
    )

    result = _run(tmp_path, execute=True, runner=lambda _argv, _cwd: CommandResult("child_process_completed", 0))

    assert result["decision"] == "RECOVERY_UNPROVEN"


def test_unrelated_new_task_receipt_cannot_confirm_source_watch_recovery(tmp_path: Path) -> None:
    _write_receipt(tmp_path, status=OperationalStatus.FAILED, classification="provider_timeout", name="01-failed")

    def runner(_argv, _cwd):
        _write_receipt(
            tmp_path,
            task_key="food_line_current_intake",
            status=OperationalStatus.SUCCESS,
            classification="success",
            name="unrelated-intake",
            started="2026-09-10T13:10:00Z",
            exit_code=0,
        )
        return CommandResult("child_process_completed", 0)

    result = _run(tmp_path, execute=True, runner=runner)

    assert result["decision"] == "RECOVERY_UNPROVEN"


def test_resume_retry_itself_verifies_against_new_resume_receipt(tmp_path: Path) -> None:
    _write_receipt(
        tmp_path,
        task_key="food_line_source_watch_resume",
        status=OperationalStatus.FAILED,
        classification="provider_timeout",
        name="01-resume-failed",
        started="2026-09-10T13:00:00Z",
    )

    def runner(_argv, _cwd):
        _write_receipt(
            tmp_path,
            task_key="food_line_source_watch_resume",
            status=OperationalStatus.SUCCESS,
            classification="resume_qualified",
            name="02-resume-success",
            started="2026-09-10T13:05:00Z",
            exit_code=0,
        )
        return CommandResult("child_process_completed", 0)

    result = _run(tmp_path, execute=True, runner=runner)

    assert result["decision"] == "RECOVERY_CONFIRMED"


def _copy_isolated_runner(destination: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".pytest_cache")
    shutil.copytree(ROOT / "scripts", destination / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "src", destination / "src", ignore=ignore)
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    subprocess.run(["git", "init", "-b", "add/pages-repo-default"], cwd=destination, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=destination, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=destination, check=True)
    subprocess.run(["git", "add", "scripts", "src", "pyproject.toml"], cwd=destination, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=destination, check=True, capture_output=True)


def _install_noisy_current_intake_child(runner: Path) -> str:
    scheduler = runner / "scripts" / "food_line_daily_scheduler.py"
    scheduler.write_text(
        r'''
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--python")
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--branch")
    args = parser.parse_args()
    repo = Path(args.repo_root)
    sys.path.insert(0, str(repo / "src"))
    from bluefern_dispatches.operational_health import OperationalStatus, build_operational_receipt

    print(json.dumps({"child": "terminal"}))
    print("child diagnostic text", file=sys.stderr)
    run_id = "source-watch-run"
    artifact = repo / "logs" / "food-line" / "current-intake" / args.edition_date / "noisy-child-current-intake.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"status": "success", "publication_side_effects": {"pages": False, "public_output": False}}) + "\n", encoding="utf-8")
    receipt = build_operational_receipt(
        dispatch="food-line",
        task_key="food_line_current_intake",
        task_name="food_line_current_intake",
        scheduled_for=args.edition_date,
        started_at=f"{args.edition_date}T15:00:01Z",
        completed_at=f"{args.edition_date}T15:00:02Z",
        exit_code=0,
        status=OperationalStatus.SUCCESS,
        classification="success",
        run_id=run_id,
        public_side_effects={"pages": False, "public_output": False, "audio": False, "bluesky": False, "maps": False, "schedule": False},
        artifact_refs={"task_receipt": str(artifact)},
        details={"edition_date": args.edition_date},
    )
    out = repo / "status" / "operational-health" / "food-line" / args.edition_date / "runs" / f"food_line_current_intake-{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''.lstrip(),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "scripts/food_line_daily_scheduler.py"], cwd=runner, check=True)
    subprocess.run(["git", "commit", "-m", "noisy child fixture"], cwd=runner, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=runner, check=True, capture_output=True, text=True).stdout.strip()


def _assert_single_json_object(text: str) -> dict[str, object]:
    stripped = text.strip()
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(stripped)
    assert end == len(stripped)
    assert isinstance(value, dict)
    return value


def test_executor_cli_captures_child_stdout_and_emits_single_recovery_report(tmp_path: Path) -> None:
    runner = tmp_path / "isolated-runner"
    _copy_isolated_runner(runner)
    head = _install_noisy_current_intake_child(runner)
    _write_dependency_recovered_intake_state(runner)
    record_path = runner / "status" / "food-line" / "runs" / f"{DATE}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["source_commit"] = head
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(runner / "scripts" / "run_scheduled_recovery_executor.py"),
            "--dispatch",
            "food-line",
            "--source-root",
            str(runner),
            "--date",
            DATE,
            "--evaluated-at",
            EVALUATED,
            "--execute",
            "--expected-branch",
            "add/pages-repo-default",
        ],
        cwd=runner,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = _assert_single_json_object(completed.stdout)
    assert report["decision"] == "RECOVERY_CONFIRMED"
    assert report["execution_receipt"]["recovery_attempt_id"].startswith("recovery-")
    assert report["execution_receipt"]["post_execution_receipt_observation"] == "RECOVERY_CONFIRMED"
    assert report["execution_receipt"]["public_side_effects"] is False
    assert '{"child": "terminal"}' not in completed.stdout
    assert "child diagnostic text" not in completed.stdout
    assert "child diagnostic text" not in completed.stderr


def test_isolated_real_current_intake_execution_rehearsal_is_idempotent(tmp_path: Path) -> None:
    runner = tmp_path / "isolated-runner"
    _copy_isolated_runner(runner)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=runner, check=True, capture_output=True, text=True).stdout.strip()
    _write_dependency_recovered_intake_state(runner)
    record_path = runner / "status" / "food-line" / "runs" / f"{DATE}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["source_commit"] = head
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    first = run_executor(
        ExecutionOptions(
            dispatch="food-line",
            source_root=runner,
            date=DATE,
            evaluated_at=EVALUATED,
            execute=True,
            python=Path(sys.executable),
        )
    )
    assert first["decision"] == "RECOVERY_CONFIRMED", first
    queue_path = runner / "status" / "food-line" / "runtime" / "current-signal-review.json"
    proposal_path = runner / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{DATE}.json"
    queue_first = json.loads(queue_path.read_text(encoding="utf-8"))
    proposal_first = json.loads(proposal_path.read_text(encoding="utf-8"))

    second = run_executor(
        ExecutionOptions(
            dispatch="food-line",
            source_root=runner,
            date=DATE,
            evaluated_at=EVALUATED,
            execute=True,
            python=Path(sys.executable),
        )
    )
    queue_second = json.loads(queue_path.read_text(encoding="utf-8"))
    proposal_second = json.loads(proposal_path.read_text(encoding="utf-8"))

    assert first["decision"] == "RECOVERY_CONFIRMED"
    assert first["plan"]["task_key"] == "food_line_current_intake"
    assert first["execution_receipt"]["post_execution_receipt_observation"] == "RECOVERY_CONFIRMED"
    assert second["decision"] == "NO_CANDIDATE"
    assert queue_first["items"] == queue_second["items"]
    assert proposal_first["items"] == proposal_second["items"]
    assert len(queue_first["items"]) == 1
    assert proposal_first["publication_eligible"] is False
    assert not (runner / "output" / "site").exists()
    assert not (runner / "bluefern-dispatches-pages").exists()
    assert (instance_ledger_dir(runner, "food-line", DATE, f"{DATE}:food_line_current_intake") / "latest.json").is_file()


def test_care_and_ice_are_planning_only_unsupported(tmp_path: Path) -> None:
    _write_receipt(tmp_path / "care", dispatch="care-line", task_key="care_line_collection")
    _write_receipt(tmp_path / "ice", dispatch="ice", task_key="ice_monitor", started="2026-09-11T04:15:00Z")

    care = _run(tmp_path / "care", dispatch="care-line", evaluated_at="2026-09-10T20:00:00Z")
    ice = _run(tmp_path / "ice", dispatch="ice", evaluated_at="2026-09-11T05:00:00Z")

    assert care["decision"] == "EXECUTION_UNSUPPORTED"
    assert care["plan"]["supported_for_planning"] is True
    assert care["plan"]["supported_for_automatic_execution"] is False
    assert ice["decision"] == "EXECUTION_UNSUPPORTED"
    assert ice["plan"]["supported_for_automatic_execution"] is False


def test_path_traversal_instance_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        instance_ledger_dir(tmp_path, "food-line", DATE, "../escape")
