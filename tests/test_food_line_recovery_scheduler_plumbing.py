from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "windows" / "run_food_line_recovery_executor.ps1"
REGISTER = ROOT / "scripts" / "register_food_line_recovery_task.ps1"
INSPECT = ROOT / "scripts" / "inspect_food_line_recovery_task.ps1"


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if not executable:
        pytest.skip("PowerShell is not available")
    return executable


def _write_fake_runner(path: Path, *, payload: dict[str, object], exit_code: int = 0) -> Path:
    fake = path / "fake-python.ps1"
    fake.write_text(
        "\n".join(
            [
                "$encoding = New-Object System.Text.UTF8Encoding($false)",
                "[System.IO.File]::WriteAllText($env:ARGV_PATH, ($args | ConvertTo-Json -Compress), $encoding)",
                f"Write-Output @'",
                json.dumps(payload, sort_keys=True),
                "'@",
                f"exit {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return fake


def _run_wrapper(tmp_path: Path, *, payload: dict[str, object], exit_code: int = 0) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "runner"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "run_scheduled_recovery_executor.py").write_text("# fake executor\n", encoding="utf-8")
    argv_path = tmp_path / "argv.json"
    fake_python = _write_fake_runner(tmp_path, payload=payload, exit_code=exit_code)
    env = os.environ.copy()
    env["ARGV_PATH"] = str(argv_path)

    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-RepositoryRoot",
            str(repo),
            "-PythonExecutable",
            str(fake_python),
            "-EditionDate",
            "2026-09-17",
            "-EvaluatedAt",
            "2026-09-18T02:16:52Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _run_wrapper_with_executor_output(tmp_path: Path, *, output: str, exit_code: int = 0) -> subprocess.CompletedProcess[str]:
    repo = tmp_path / "runner"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "run_scheduled_recovery_executor.py").write_text("# fake executor\n", encoding="utf-8")
    fake = tmp_path / "fake-python.ps1"
    fake.write_text(
        "\n".join(
            [
                "Write-Output @'",
                output,
                "'@",
                f"exit {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-RepositoryRoot",
            str(repo),
            "-PythonExecutable",
            str(fake),
            "-EditionDate",
            "2026-09-17",
            "-EvaluatedAt",
            "2026-09-18T02:16:52Z",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _latest_terminal(repo: Path) -> dict[str, object]:
    files = sorted((repo / "logs" / "food-line" / "recovery-executor" / "2026-09-17").glob("*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_wrapper_invokes_fixed_food_executor_arguments_and_writes_terminal_record(tmp_path: Path) -> None:
    payload = {
        "decision": "RECOVERY_CONFIRMED",
        "plan": {
            "task_key": "food_line_current_intake",
            "recovery_adapter": "food_current_intake_dependency_recovered",
            "scheduled_instance": "2026-09-17:food_line_current_intake",
        },
        "execution_receipt": {
            "recovery_attempt_id": "attempt-1",
            "post_execution_receipt_observation": "RECOVERY_CONFIRMED",
        },
    }

    result = _run_wrapper(tmp_path, payload=payload)

    assert result.returncode == 0, result.stderr
    repo = tmp_path / "runner"
    argv = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert argv == [
        str(repo / "scripts" / "run_scheduled_recovery_executor.py"),
        "--dispatch",
        "food-line",
        "--source-root",
        str(repo),
        "--date",
        "2026-09-17",
        "--evaluated-at",
        "2026-09-18T02:16:52Z",
        "--execute",
        "--expected-branch",
        "add/pages-repo-default",
    ]
    assert "publish" not in " ".join(argv).lower()
    assert "care-line" not in argv
    assert "ice" not in argv

    terminal = _latest_terminal(repo)
    assert terminal["schema_version"] == "bluefern_food_recovery_executor_terminal_v1"
    assert terminal["dispatch"] == "food-line"
    assert terminal["execute_requested"] is True
    assert terminal["executor_decision"] == "RECOVERY_CONFIRMED"
    assert terminal["selected_task_key"] == "food_line_current_intake"
    assert terminal["recovery_attempt_id"] == "attempt-1"
    assert terminal["execution_receipt_id"] == "attempt-1"
    assert terminal["parse_status"] == "parsed"
    assert terminal["exit_classification"] == "scheduler_success"


def test_inspection_reports_recovery_attempt_id_when_action_executed(tmp_path: Path) -> None:
    payload = {
        "decision": "RECOVERY_CONFIRMED",
        "plan": {
            "task_key": "food_line_current_intake",
            "recovery_adapter": "food_current_intake_dependency_recovered",
            "scheduled_instance": "2026-09-17:food_line_current_intake",
        },
        "execution_receipt": {
            "recovery_attempt_id": "attempt-42",
            "post_execution_receipt_observation": "RECOVERY_CONFIRMED",
        },
    }
    wrapper_result = _run_wrapper(tmp_path, payload=payload)
    assert wrapper_result.returncode == 0, wrapper_result.stderr

    inspection = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSPECT),
            "-RepositoryRoot",
            str(tmp_path / "runner"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(inspection.stdout)
    assert report["host_timezone_id"]
    assert report["schedule_timezone_contract"] == "Pacific Standard Time"
    assert report["recovery_action_executed"] is True
    assert report["latest_terminal_result"]["recovery_action_executed"] is True
    assert report["latest_terminal_result"]["recovery_attempt_id"] == "attempt-42"
    assert report["latest_terminal_result"]["execution_receipt_id"] == "attempt-42"


def test_inspection_reports_no_action_when_no_execution_receipt(tmp_path: Path) -> None:
    wrapper_result = _run_wrapper(tmp_path, payload={"decision": "NO_CANDIDATE", "plan": None, "execution_receipt": None})
    assert wrapper_result.returncode == 0, wrapper_result.stderr

    inspection = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSPECT),
            "-RepositoryRoot",
            str(tmp_path / "runner"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    report = json.loads(inspection.stdout)
    assert report["recovery_action_executed"] is False
    assert report["latest_terminal_result"]["recovery_action_executed"] is False
    assert report["latest_terminal_result"]["recovery_attempt_id"] is None


@pytest.mark.parametrize(
    "decision",
    [
        "NO_CANDIDATE",
        "RECOVERY_CONFIRMED",
        "RECOVERY_DEGRADED_TERMINAL",
        "STALE_PLAN_SUPPRESSED",
        "EXECUTION_DEFERRED_BACKOFF",
        "EXECUTION_DENIED_ATTEMPT_LIMIT",
        "EXECUTION_DENIED_RECOVERY_WINDOW_EXPIRED",
        "EXECUTION_DENIED_ACTIVE_OR_AMBIGUOUS_LOCK",
    ],
)
def test_wrapper_success_exit_semantics_for_expected_no_action_and_bounded_outcomes(tmp_path: Path, decision: str) -> None:
    result = _run_wrapper(tmp_path, payload={"decision": decision, "plan": None, "execution_receipt": None})

    assert result.returncode == 0, result.stderr
    assert _latest_terminal(tmp_path / "runner")["exit_classification"] == "scheduler_success"


@pytest.mark.parametrize(
    ("payload", "exit_code"),
    [
        ({"decision": "EXECUTION_DENIED_UNSAFE_RUNNER_STATE"}, 0),
        ({"decision": "RECOVERY_UNPROVEN"}, 0),
        ({"decision": "EXECUTION_DENIED_PUBLICATION"}, 0),
        ({"decision": "RECOVERY_FAILED"}, 0),
        ({"decision": "NO_CANDIDATE"}, 7),
    ],
)
def test_wrapper_failure_exit_semantics_for_unsafe_unproven_or_child_crash(
    tmp_path: Path,
    payload: dict[str, object],
    exit_code: int,
) -> None:
    result = _run_wrapper(tmp_path, payload=payload, exit_code=exit_code)

    assert result.returncode == 1
    assert _latest_terminal(tmp_path / "runner")["exit_classification"] == "scheduler_failure"


def test_wrapper_fails_nonzero_on_malformed_executor_json(tmp_path: Path) -> None:
    repo = tmp_path / "runner"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "run_scheduled_recovery_executor.py").write_text("# fake executor\n", encoding="utf-8")
    fake = tmp_path / "fake-python.ps1"
    fake.write_text("Write-Output 'not json'\nexit 0\n", encoding="utf-8")

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WRAPPER),
            "-RepositoryRoot",
            str(repo),
            "-PythonExecutable",
            str(fake),
            "-EditionDate",
            "2026-09-17",
            "-EvaluatedAt",
            "2026-09-18T02:16:52Z",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    terminal = _latest_terminal(repo)
    assert terminal["parse_status"] == "malformed_executor_json"
    assert terminal["error_classification"] == "malformed_executor_json"


@pytest.mark.parametrize(
    "output",
    [
        'noise\n{"decision":"NO_CANDIDATE","plan":null,"execution_receipt":null}',
        '{"child":"terminal"}\n{"decision":"RECOVERY_CONFIRMED","plan":null,"execution_receipt":{"recovery_attempt_id":"attempt-1"}}',
        '{"decision":"NO_CANDIDATE","plan":null,"execution_receipt":null}\ntrailing noise',
    ],
)
def test_wrapper_strict_parser_rejects_noise_or_adjacent_json_documents(tmp_path: Path, output: str) -> None:
    result = _run_wrapper_with_executor_output(tmp_path, output=output)

    assert result.returncode == 1
    terminal = _latest_terminal(tmp_path / "runner")
    assert terminal["parse_status"] == "malformed_executor_json"
    assert terminal["error_classification"] == "malformed_executor_json"
    assert terminal["executor_decision"] is None
    assert terminal["recovery_attempt_id"] is None
    assert terminal["exit_classification"] == "scheduler_failure"


def test_wrapper_uses_windows_pacific_time_zone_for_edition_dates() -> None:
    script = (
        '[System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId('
        '[DateTimeOffset]"2026-07-01T06:30:00Z", "Pacific Standard Time").ToString("yyyy-MM-dd");'
        '[System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId('
        '[DateTimeOffset]"2026-12-01T07:30:00Z", "Pacific Standard Time").ToString("yyyy-MM-dd")'
    )

    result = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["2026-06-30", "2026-11-30"]
    assert '"Pacific Standard Time"' in WRAPPER.read_text(encoding="utf-8")


def test_registration_script_defines_only_the_bounded_non_elevated_food_task() -> None:
    text = REGISTER.read_text(encoding="utf-8")

    assert "[CmdletBinding(SupportsShouldProcess)]" in text
    assert "Blue Fern Food Line Recovery" in text
    assert '"\\Blue Fern Co\\"' in text
    assert "C:\\BlueFernRunner\\FoodLineCurrent6" in text
    assert "run_food_line_recovery_executor.ps1" in text
    assert 'New-ScheduledTaskAction -Execute "PowerShell.exe"' in text
    assert "-WorkingDirectory $repoRoot" in text
    assert "New-ScheduledTaskPrincipal -UserId $effectiveUserId -LogonType Interactive -RunLevel Limited" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "-StartWhenAvailable" in text
    assert "New-TimeSpan -Minutes 20" in text
    assert '$requiredTimezone = "Pacific Standard Time"' in text
    assert "[System.TimeZoneInfo]::Local.Id" in text
    assert "Food recovery schedule requires production host timezone" in text
    assert "Start-ScheduledTask" not in text
    for slot in ("05:45", "06:15", "06:45", "07:15", "07:45", "08:15", "08:45", "09:15", "09:45", "10:15", "10:45", "11:15", "11:45"):
        assert slot in text


def test_inspection_script_is_read_only_and_reports_latest_terminal_result() -> None:
    text = INSPECT.read_text(encoding="utf-8")

    assert "Get-ScheduledTask" in text
    assert "Get-ScheduledTaskInfo" in text
    assert "latest_terminal_result" in text
    assert "recovery_action_executed" in text
    assert "host_timezone_id" in text
    assert 'schedule_timezone_contract = "Pacific Standard Time"' in text
    assert "recovery_attempt_id" in text
    for forbidden in ("Register-ScheduledTask", "Set-ScheduledTask", "Start-ScheduledTask", "Enable-ScheduledTask", "Disable-ScheduledTask"):
        assert forbidden not in text
