from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _load_scheduler_module(repo: Path):
    path = repo / "scripts" / "care_line_collection_scheduler.py"
    spec = importlib.util.spec_from_file_location("care_line_collection_scheduler", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_powershell_executable() -> str:
    for candidate in ("powershell.exe", "pwsh"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError("PowerShell is not available for wrapper execution tests")


def test_care_line_windows_wrapper_and_helper_are_present_and_bound_to_collection_contract(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    wrapper = repo / "scripts" / "windows" / "run_care_line_national_collection.ps1"
    helper = repo / "scripts" / "care_line_collection_scheduler.py"

    assert wrapper.exists()
    assert helper.exists()

    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "scripts\\care_line_collection_scheduler.py" in wrapper_text
    assert "$RepositoryRoot" in wrapper_text
    assert "$PythonExecutable" in wrapper_text
    assert "$SourceBranch" in wrapper_text
    assert "$RunDate" in wrapper_text
    assert "$SmokeTest" in wrapper_text
    assert "$MaxSources" in wrapper_text
    assert "$MaxItemsPerSource" in wrapper_text
    assert "$IncludeManualReview" in wrapper_text
    assert "$ExcludePartial" in wrapper_text
    assert "$AllowInsecureTls" in wrapper_text
    assert "add/pages-repo-default" in wrapper_text
    assert "agent/refine-care-line-signal-wire-public-rendering" not in wrapper_text

    scheduler = _load_scheduler_module(repo)
    assert scheduler.PRODUCTION_BRANCH == "add/pages-repo-default"
    parser = scheduler.build_parser()
    parsed = parser.parse_args(["--repo-root", str(tmp_path), "--run-date", "2026-08-16"])
    assert parsed.branch == "add/pages-repo-default"
    captured: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], *, cwd: Path):
        captured.append((command, cwd))
        payload = {
            "run_manifest": {
                "status": "success",
                "run_id": "run-1",
                "successful_attempt_count": 100,
                "failed_source_count": 4,
                "skipped_source_count": 2,
                "active_review_queue_count": 0,
                "manual_review_count": 0,
                "production_review_queue_mutation_disabled": True,
            }
        }
        return scheduler.ChildExecution(pid=4321, returncode=0, stdout=json.dumps(payload), stderr="")

    class DummyLock:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.stale_recovered = False
            self.acquired = False

        def acquire(self, *, now=None):  # noqa: ANN001
            self.acquired = True
            return "acquired"

        def release(self) -> None:
            self.acquired = False

    scheduler.verify_checkout = lambda root, branch: "abc123"  # type: ignore[assignment]
    scheduler.run_preflight = lambda root: None  # type: ignore[assignment]
    scheduler._run_child = fake_run  # type: ignore[assignment]
    scheduler.SchedulerLock = DummyLock  # type: ignore[assignment]

    exit_code, receipt = scheduler.run_collection_once(
        tmp_path,
        run_date="2026-08-16",
        branch=scheduler.PRODUCTION_BRANCH,
        run_id="run-1",
        smoke_test=True,
        include_partial=False,
        include_manual_review=True,
        allow_insecure_tls=False,
        max_sources=2,
        fetch_timeout=17,
        max_items_per_source=3,
        active_queue_limit=111,
        low_priority_cap=9,
    )

    assert exit_code == 0
    assert receipt["ok"] is True
    assert receipt["collection_only"] is True
    assert receipt["smoke_test"] is True
    assert receipt["production_review_queue_mutation_disabled"] is True
    assert receipt["publication_side_effects"]["pages_sync"] is False
    assert receipt["publication_side_effects"]["bluesky_publication"] is False
    assert receipt["pipeline_status"] == "success"
    assert len(captured) == 1

    command, cwd = captured[0]
    assert cwd == tmp_path.resolve()
    assert Path(command[1]).name == "run_care_line_national_pipeline.py"
    assert "--collection-only" in command
    assert "--smoke-test" in command
    assert "--max-sources" in command
    assert "2" in command
    assert "--max-items-per-source" in command
    assert "3" in command
    assert "--include-manual-review" in command
    assert "--exclude-partial" in command
    assert "--allow-insecure-tls" not in command


def test_care_line_scheduler_helpers_force_utf8_subprocess_decoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    scheduler = _load_scheduler_module(repo)

    run_calls: list[dict[str, object]] = []

    def fake_run(command: list[str], *, cwd: Path, text: bool, encoding: str, errors: str, capture_output: bool, check: bool):  # noqa: ANN001
        run_calls.append(
            {
                "command": command,
                "cwd": cwd,
                "text": text,
                "encoding": encoding,
                "errors": errors,
                "capture_output": capture_output,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout="Care–Line ✓", stderr="diagnostic: café")

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    completed = scheduler._run(["git", "status"], cwd=tmp_path)

    assert completed.stdout == "Care–Line ✓"
    assert completed.stderr == "diagnostic: café"
    assert run_calls == [
        {
            "command": ["git", "status"],
            "cwd": tmp_path,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "capture_output": True,
            "check": False,
        }
    ]

    popen_calls: list[dict[str, object]] = []

    class FakePopen:
        def __init__(
            self,
            command: list[str],
            *,
            cwd: Path,
            text: bool,
            encoding: str,
            errors: str,
            stdout: object,
            stderr: object,
            check: bool,
        ) -> None:
            popen_calls.append(
                {
                    "command": command,
                    "cwd": cwd,
                    "text": text,
                    "encoding": encoding,
                    "errors": errors,
                    "stdout": stdout,
                    "stderr": stderr,
                    "check": check,
                }
            )
            self.pid = 2468
            self.returncode = 0

        def communicate(self) -> tuple[str, str]:
            payload = json.dumps(
                {
                    "run_manifest": {
                        "status": "success",
                        "run_id": "utf8-run",
                        "successful_attempt_count": 1,
                        "failed_source_count": 0,
                        "skipped_source_count": 0,
                        "active_review_queue_count": 0,
                        "manual_review_count": 0,
                        "production_review_queue_mutation_disabled": True,
                    },
                    "message": "Care–Line ✓",
                },
                ensure_ascii=False,
            )
            return payload, "diagnostic: café"

    monkeypatch.setattr(scheduler.subprocess, "Popen", FakePopen)

    child = scheduler._run_child(["python", "-c", "print('utf8')"], cwd=tmp_path)

    assert child.pid == 2468
    assert child.returncode == 0
    payload = json.loads(child.stdout)
    assert payload["message"] == "Care–Line ✓"
    assert child.stderr == "diagnostic: café"
    assert popen_calls == [
        {
            "command": ["python", "-c", "print('utf8')"],
            "cwd": tmp_path,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "check": False,
        }
    ]


def test_care_line_windows_wrapper_writes_diagnostic_receipt_on_python_launch_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts" / "windows").mkdir(parents=True, exist_ok=True)
    (repo / "status").mkdir(parents=True, exist_ok=True)
    (repo / "logs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts" / "windows" / "run_care_line_national_collection.ps1", repo / "scripts" / "windows" / "run_care_line_national_collection.ps1")
    shutil.copy2(Path(__file__).resolve().parents[1] / "scripts" / "care_line_collection_scheduler.py", repo / "scripts" / "care_line_collection_scheduler.py")

    powershell = _resolve_powershell_executable()
    run_date = "2026-08-22"
    run_id = "diagnostic-failure-1"
    fake_python = repo / ".venv" / "Scripts" / "missing-python.exe"
    command = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(repo / "scripts" / "windows" / "run_care_line_national_collection.ps1"),
        "-RepositoryRoot",
        str(repo),
        "-PythonExecutable",
        str(fake_python),
        "-SourceBranch",
        "add/pages-repo-default",
        "-RunDate",
        run_date,
        "-RunId",
        run_id,
    ]

    completed = subprocess.run(
        command,
        cwd=repo,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    receipt = repo / "status" / "care-line" / "scheduler-runs" / run_date / f"{run_id}.json"
    log_path = repo / "logs" / "care-line" / "collection-scheduler" / run_date / f"{run_id}.log"

    assert completed.returncode != 0
    assert receipt.exists(), completed.stdout + completed.stderr
    assert log_path.exists(), completed.stdout + completed.stderr

    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_data["run_id"] == run_id
    assert receipt_data["status"] == "failure"
    assert receipt_data["ok"] is False
    assert receipt_data["failure_stage"] == "launch_python"
    assert receipt_data["wrapper_exception_type"] == "ItemNotFoundException"
    assert "missing-python.exe" in receipt_data["wrapper_exception_message"]
    assert receipt_data["wrapper_path"].endswith("run_care_line_national_collection.ps1")
    assert receipt_data["python_executable"] == str(fake_python)
    assert receipt_data["source_branch"] == "add/pages-repo-default"
    assert receipt_data["child_process_id"] is None
    assert receipt_data["child_exit_code"] is None

    log_text = log_path.read_text(encoding="utf-8")
    assert "status=failure" in log_text
    assert "failure_stage=launch_python" in log_text
    assert "missing-python.exe" in log_text
