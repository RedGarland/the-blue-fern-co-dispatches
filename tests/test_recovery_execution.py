from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.operational_health import OperationalStatus, build_operational_receipt
from bluefern_dispatches.recovery_execution import (
    CommandResult,
    ExecutionOptions,
    instance_ledger_dir,
    run_executor,
)


DATE = "2026-09-10"
EVALUATED = "2026-09-10T15:00:00Z"


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
) -> None:
    artifact = root / "artifact.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("{}\n", encoding="utf-8")
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
        run_id=name or f"{task_key}-run",
        public_side_effects={},
        artifact_refs={"task_receipt": str(artifact)},
    )
    path = root / "status" / "operational-health" / dispatch / DATE / "runs" / f"{name or task_key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")


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


def test_retryable_resume_stage_uses_status_resume_adapter(tmp_path: Path) -> None:
    _write_receipt(tmp_path, task_key="food_line_source_watch_resume", started="2026-09-10T13:00:00Z")

    result = _run(tmp_path)

    assert result["decision"] == "DRY_RUN"
    assert result["plan"]["task_key"] == "food_line_source_watch_resume"
    assert "resume" in result["plan"]["command_argv"]


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
                "command_argv": ["malformed"],
            },
            {"instances": []},
        ),
    )

    result = _run(tmp_path, execute=True)

    assert result["decision"] == "EXECUTION_DENIED_PUBLICATION"


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

    result = _run(tmp_path)

    assert result["plan"]["task_key"] == "food_line_source_watch"
    assert result["plan"]["deferred_candidates"] == [
        {"task_key": "food_line_source_watch_resume", "scheduled_instance": "2026-09-10:food_line_source_watch_resume"}
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
    ("status", "expected"),
    [
        (OperationalStatus.SUCCESS, "RECOVERY_CONFIRMED"),
        (OperationalStatus.SAFE_NO_OP, "RECOVERY_CONFIRMED"),
        (OperationalStatus.DEGRADED, "RECOVERY_DEGRADED_TERMINAL"),
        (OperationalStatus.UPSTREAM_BLOCKED, "RECOVERY_UPSTREAM_BLOCKED"),
        (OperationalStatus.FAILED, "RECOVERY_FAILED"),
    ],
)
def test_post_execution_classifies_from_new_receipt_even_when_exit_nonzero(tmp_path: Path, status: OperationalStatus, expected: str) -> None:
    _write_receipt(tmp_path, status=OperationalStatus.FAILED, classification="provider_timeout", name="01-failed")

    def runner(_argv, _cwd):
        _write_receipt(tmp_path, status=status, classification="completed", name="02-after", started="2026-09-10T12:45:00Z", exit_code=0 if status != OperationalStatus.FAILED else 1)
        return CommandResult("child_process_completed", 1)

    result = _run(tmp_path, execute=True, runner=runner)

    assert result["decision"] == expected
    assert result["execution_receipt"]["post_execution_receipt_observation"] == expected


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
