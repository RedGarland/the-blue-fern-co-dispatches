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
                "source_failure_diagnostics": [
                    {
                        "source_id": "cms-public-notices",
                        "source_name": "CMS Public Notices",
                        "adapter_type": "structured_index",
                        "failure_class": "HTTPError",
                        "http_status": 503,
                        "failure_status_class": "transient_http_failure",
                        "failure_reason": "HTTPError: 503 temporary",
                        "source_url": "https://example.invalid/private-proof",
                        "transient": True,
                    }
                ],
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
    assert receipt["failed_source_diagnostics"] == [
        {
            "source_id": "cms-public-notices",
            "source_name": "CMS Public Notices",
            "adapter_type": "structured_index",
            "failure_class": "HTTPError",
            "http_status": 503,
            "failure_status_class": "transient_http_failure",
            "transient": True,
        }
    ]
    health_receipt = next(
        (tmp_path / "status" / "operational-health" / "care-line" / "2026-08-16" / "runs").glob("*.json")
    )
    health = json.loads(health_receipt.read_text(encoding="utf-8"))
    assert health["details"]["failed_source_count"] == 4
    assert health["details"]["failed_source_diagnostics"][0]["source_id"] == "cms-public-notices"
    assert "failure_reason" not in health["details"]["failed_source_diagnostics"][0]
    assert "source_url" not in health["details"]["failed_source_diagnostics"][0]
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
        }
    ]


def test_care_line_scheduler_real_child_uses_utf8_decoding(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    scheduler = _load_scheduler_module(repo)

    child = scheduler._run_child(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stdout.buffer.write('Care–Line ✓\\n'.encode('utf-8')); "
                "sys.stderr.buffer.write('diagnostic: café\\n'.encode('utf-8'))"
            ),
        ],
        cwd=tmp_path,
    )

    assert child.pid is not None
    assert child.returncode == 0
    assert "Care–Line ✓" in child.stdout
    assert "diagnostic: café" in child.stderr


def test_care_line_scheduler_operational_health_includes_verify_checkout_exception(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    scheduler = _load_scheduler_module(repo)

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

    scheduler.verify_checkout = lambda root, branch: (_ for _ in ()).throw(  # type: ignore[assignment]
        scheduler.SchedulerError(
            "collection runner checkout is dirty; run failed closed: "
            "ops/operator/runs/2026-09-26/runner-sync-apply.json"
        )
    )
    scheduler.SchedulerLock = DummyLock  # type: ignore[assignment]

    exit_code, receipt = scheduler.run_collection_once(
        tmp_path,
        run_date="2026-08-16",
        branch=scheduler.PRODUCTION_BRANCH,
        run_id="verify-checkout-failure-1",
        smoke_test=False,
        include_partial=True,
        include_manual_review=False,
        allow_insecure_tls=False,
        max_sources=None,
        fetch_timeout=17,
        max_items_per_source=None,
        active_queue_limit=111,
        low_priority_cap=9,
    )

    assert exit_code == 1
    assert receipt["failure_stage"] == "verify_checkout"
    assert receipt["wrapper_exception_type"] == "SchedulerError"
    assert "ops/operator/runs/2026-09-26/runner-sync-apply.json" in receipt["wrapper_exception_message"]

    health_receipt = next(
        (tmp_path / "status" / "operational-health" / "care-line" / "2026-08-16" / "runs").glob("*.json")
    )
    health = json.loads(health_receipt.read_text(encoding="utf-8"))
    assert health["status"] == "FAILED"
    assert health["details"]["failure_stage"] == "verify_checkout"
    assert health["details"]["wrapper_exception_type"] == "SchedulerError"
    assert "ops/operator/runs/2026-09-26/runner-sync-apply.json" in health["details"]["wrapper_exception_message"]
    assert health["details"]["failed_source_diagnostics"] == []


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


def _health_export_case(tmp_path, monkeypatch, outcome):
    scheduler = _load_scheduler_module(Path(__file__).resolve().parents[1])
    state = {"released": False, "child_calls": 0}

    class DummyLock:
        def __init__(self, path):
            self.stale_recovered = False

        def acquire(self):
            return "already_running" if outcome == "already_running" else "acquired"

        def release(self):
            state["released"] = True

    def verify_checkout(root, branch):
        if outcome == "verify_failure":
            raise scheduler.SchedulerError("synthetic checkout failure")
        return "synthetic-source-head"

    def fake_child(command, *, cwd):
        state["child_calls"] += 1
        assert "--collection-only" in command
        assert "--run-id" not in command
        manifest = {
            "run_id": "synthetic-pipeline-run", "status": outcome,
            "successful_attempt_count": 2, "failed_source_count": 1,
            "manual_review_count": 3,
            "run_manifest_path": "synthetic/run-manifest.json",
        }
        return scheduler.ChildExecution(
            pid=1234, returncode=7 if outcome == "failure" else 0,
            stdout=json.dumps({"run_manifest": manifest}), stderr="synthetic child stderr",
        )

    monkeypatch.setattr(scheduler, "SchedulerLock", DummyLock)
    monkeypatch.setattr(scheduler, "verify_checkout", verify_checkout)
    monkeypatch.setattr(scheduler, "run_preflight", lambda root: None)
    monkeypatch.setattr(scheduler, "_run_child", fake_child)
    kwargs = dict(
        run_date="2026-10-29", branch=scheduler.PRODUCTION_BRANCH,
        run_id="synthetic-export-test", smoke_test=False, include_partial=True,
        include_manual_review=False, allow_insecure_tls=False,
        max_sources=None, fetch_timeout=20, max_items_per_source=None,
        active_queue_limit=150, low_priority_cap=25,
    )
    health_base = tmp_path / "status/operational-health/care-line/2026-10-29"
    paths = {
        "receipt": tmp_path / scheduler.RECEIPT_ROOT / kwargs["run_date"] / (kwargs["run_id"] + ".json"),
        "log": tmp_path / scheduler.LOG_ROOT / kwargs["run_date"] / (kwargs["run_id"] + ".log"),
        "per_run": health_base / "runs" / ("care_line_collection-" + kwargs["run_id"] + ".json"),
        "latest": health_base / "latest.json",
    }
    return scheduler, state, kwargs, paths


@pytest.mark.parametrize("outcome", ["success", "partial_success", "failure", "already_running", "verify_failure"])
@pytest.mark.parametrize("failed_write", ["per_run", "latest"])
def test_health_export_failure_preserves_collection_outcome(tmp_path, monkeypatch, outcome, failed_write):
    from bluefern_dispatches import operational_health as health

    scheduler, state, kwargs, paths = _health_export_case(tmp_path, monkeypatch, outcome)
    writes = []
    original_write = health.atomic_write_json
    before_export = {}

    def fail_selected_write(path, value):
        if not writes:
            before_export.update(json.loads(paths["receipt"].read_text()))
        writes.append(path)
        if path == paths[failed_write]:
            raise OSError("synthetic export failure")
        original_write(path, value)

    monkeypatch.setattr(health, "atomic_write_json", fail_selected_write)
    code, record = scheduler.run_collection_once(tmp_path, **kwargs)
    assert code == (7 if outcome == "failure" else 1)
    assert record["wrapper_exit_code"] == code
    assert record["status"] == ("failure" if outcome == "verify_failure" else outcome)
    assert record["ok"] is (outcome in {"success", "partial_success", "already_running"})
    assert {key: value for key, value in record.items() if key not in {"operational_health_export", "wrapper_exit_code"}} == before_export
    assert record["operational_health_export"] == {
        "status": "failure", "completed_at": record["operational_health_export"]["completed_at"],
        "failure_stage": "operational_health_export", "exception_type": "OSError",
        "exception_message": "synthetic export failure", "latest_pointer_status": "unknown",
    }
    assert not paths["latest"].exists()
    assert writes == ([paths["per_run"]] if failed_write == "per_run" else [paths["per_run"], paths["latest"]])
    if failed_write == "latest":
        exported = json.loads(paths["per_run"].read_text())
        expected_health = {"success": "SUCCESS", "partial_success": "DEGRADED", "failure": "FAILED", "already_running": "SAFE_NO_OP", "verify_failure": "FAILED"}
        assert exported["status"] == expected_health[outcome]
        assert exported["classification"] == record["status"]
    else:
        assert not paths["per_run"].exists()
    assert json.loads(paths["receipt"].read_text()) == record
    log = paths["log"].read_text()
    assert '"latest_pointer_status": "unknown"' in log
    assert f"wrapper_exit_code={code}" in log
    assert state["released"] and state["child_calls"] == (0 if outcome in {"already_running", "verify_failure"} else 1)
    assert not any(record["publication_side_effects"].values())
    if outcome == "verify_failure":
        assert record["failure_stage"] == "verify_checkout"
        assert record["wrapper_exception_type"] == "SchedulerError"
        assert "synthetic checkout failure" in log
    else:
        assert record["failure_stage"] is None
        assert record["wrapper_exception_type"] is None
    if state["child_calls"]:
        assert "[stdout]" in log and "[stderr]" in log and "synthetic child stderr" in log


@pytest.mark.parametrize("outcome", ["success", "partial_success", "failure", "already_running", "verify_failure"])
def test_health_export_success_records_both_returned_paths(tmp_path, monkeypatch, outcome):
    scheduler, state, kwargs, paths = _health_export_case(tmp_path, monkeypatch, outcome)
    code, record = scheduler.run_collection_once(tmp_path, **kwargs)
    assert code == (7 if outcome == "failure" else 1 if outcome == "verify_failure" else 0)
    assert record["wrapper_exit_code"] == code
    assert record["operational_health_export"]["status"] == "success"
    assert record["operational_health_export"]["latest_pointer_status"] == "written"
    assert record["operational_health_export"]["receipt_path"] == str(paths["per_run"])
    assert record["operational_health_export"]["latest_path"] == str(paths["latest"])
    assert json.loads(paths["receipt"].read_text()) == record
    assert paths["latest"].read_bytes() == paths["per_run"].read_bytes()
    assert state["released"]


@pytest.mark.parametrize("diagnostic_write", ["receipt", "log"])
def test_health_export_diagnostic_write_failure_propagates_without_collection_reclassification(tmp_path, monkeypatch, diagnostic_write):
    from bluefern_dispatches import operational_health as health

    scheduler, state, kwargs, paths = _health_export_case(tmp_path, monkeypatch, "partial_success")
    attempts = []

    def fail_export(path, value):
        attempts.append(path)
        raise OSError("synthetic export failure")

    monkeypatch.setattr(health, "atomic_write_json", fail_export)
    if diagnostic_write == "receipt":
        original = scheduler.atomic_write_json

        def fail_diagnostic(path, value):
            if "operational_health_export" in value:
                raise OSError("synthetic diagnostic write failure")
            return original(path, value)
        monkeypatch.setattr(scheduler, "atomic_write_json", fail_diagnostic)
    else:
        original = scheduler._write_scheduler_log

        def fail_diagnostic(path, *, record, **extra):
            if "operational_health_export" in record:
                raise OSError("synthetic diagnostic write failure")
            return original(path, record=record, **extra)
        monkeypatch.setattr(scheduler, "_write_scheduler_log", fail_diagnostic)
    with pytest.raises(OSError, match="synthetic diagnostic write failure"):
        scheduler.run_collection_once(tmp_path, **kwargs)
    persisted = json.loads(paths["receipt"].read_text())
    assert persisted["status"] == "partial_success" and persisted["pipeline_exit_code"] == 0
    assert persisted["wrapper_exception_type"] is None and persisted["failure_stage"] is None
    assert attempts == [paths["per_run"]]
    assert state["released"]


def test_health_export_does_not_swallow_interruption_or_claim_pointer_success(tmp_path, monkeypatch):
    from bluefern_dispatches import operational_health as health

    scheduler, state, kwargs, paths = _health_export_case(tmp_path, monkeypatch, "partial_success")
    original = health.atomic_write_json

    class SimulatedInterruption(BaseException):
        pass

    def interrupt_latest(path, value):
        if path == paths["latest"]:
            raise SimulatedInterruption()
        return original(path, value)

    monkeypatch.setattr(health, "atomic_write_json", interrupt_latest)
    with pytest.raises(SimulatedInterruption):
        scheduler.run_collection_once(tmp_path, **kwargs)
    persisted = json.loads(paths["receipt"].read_text())
    assert persisted["status"] == "partial_success"
    assert "operational_health_export" not in persisted
    assert json.loads(paths["per_run"].read_text())["status"] == "DEGRADED"
    assert not paths["latest"].exists()
    assert state["released"]


@pytest.mark.parametrize("outcome", ["partial_success", "already_running"])
def test_terminal_log_failure_preserves_already_durable_collection_receipt(tmp_path, monkeypatch, outcome):
    scheduler, state, kwargs, paths = _health_export_case(tmp_path, monkeypatch, outcome)
    original = scheduler._write_scheduler_log
    before_failure = {}
    export_calls = []

    def unexpected_export(root, record):
        export_calls.append(record)
        raise AssertionError("Health export must not run after terminal log failure")

    def fail_terminal_log(path, *, record, **extra):
        if record["completed_at"] is not None:
            before_failure.update(json.loads(paths["receipt"].read_text()))
            raise OSError("synthetic first terminal log failure")
        return original(path, record=record, **extra)

    monkeypatch.setattr(scheduler, "_write_scheduler_log", fail_terminal_log)
    monkeypatch.setattr(scheduler, "_write_operational_health_receipt", unexpected_export)
    with pytest.raises(OSError, match="synthetic first terminal log failure"):
        scheduler.run_collection_once(tmp_path, **kwargs)
    persisted = json.loads(paths["receipt"].read_text())
    assert persisted == before_failure
    assert persisted["status"] == outcome and persisted["ok"] is True
    assert persisted["failure_stage"] is None and persisted["wrapper_exception_type"] is None
    assert "operational_health_export" not in persisted
    assert not paths["per_run"].exists() and not paths["latest"].exists()
    assert state["released"]
    assert export_calls == []
    assert state["child_calls"] == (0 if outcome == "already_running" else 1)
