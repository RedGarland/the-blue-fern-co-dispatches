from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = REPO_ROOT / "scripts" / "run_runner_dispatch.ps1"


def _write_runner_script(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _read_log(path: Path) -> str:
    for encoding in ("utf-8", "utf-16", "utf-16-le"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError:
            continue
    raise AssertionError(f"could not decode log file: {path}")


def _make_fake_runner_repo(tmp_path: Path, *, sync_ok: bool = True) -> Path:
    repo = tmp_path / "runner-repo"
    scripts_dir = repo / "scripts"
    pages_repo = repo / "bluefern-dispatches-pages"
    logs_dir = repo / "logs"
    scripts_dir.mkdir(parents=True)
    pages_repo.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    shutil.copy2(WRAPPER_PATH, scripts_dir / "run_runner_dispatch.ps1")
    _write_runner_script(
        scripts_dir / "runner_repo_maintenance.py",
        f"""
import json
import sys

command = sys.argv[1]
if command == "sync":
    print("sync step starting")
    print(json.dumps({{"ok": {str(sync_ok)}, "errors": {[] if sync_ok else ["sync gate failed"]}}}, indent=2))
    raise SystemExit(0)
if command == "postflight":
    print(json.dumps({{"ok": True, "errors": []}}, indent=2))
    raise SystemExit(0)
print(json.dumps({{"ok": False, "errors": ["unexpected command"]}}, indent=2))
raise SystemExit(1)
""".strip()
        + "\n",
    )
    _write_runner_script(
        scripts_dir / "smoke_gaza_operator.py",
        """
import json

print("manual source smoke gate")
print(json.dumps({"ok": True, "operator_status": "MANUAL_SOURCE_VALID"}, indent=2))
""".strip()
        + "\n",
    )
    return repo


def _run_wrapper(repo: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("powershell.exe not available in test environment")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repo / "scripts" / "run_runner_dispatch.ps1"),
            "-Dispatch",
            "gaza",
            "-Date",
            "2026-07-02",
            "-CheckOnly",
        ],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_wrapper_check_only_succeeds_when_sync_outputs_json_and_exit_zero(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)

    result = _run_wrapper(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _read_log(max((repo / "logs").glob("runner-gaza-*.log"), key=lambda p: p.stat().st_mtime))
    assert "Runner sync/preflight failed" not in log_text
    assert "Runner check-only validation finished with exit code 0." in log_text


def test_wrapper_check_only_fails_closed_when_sync_json_reports_not_ok(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=False)

    result = _run_wrapper(repo)

    assert result.returncode == 10
    log_text = _read_log(max((repo / "logs").glob("runner-gaza-*.log"), key=lambda p: p.stat().st_mtime))
    assert "Runner sync/preflight reported ok=false" in log_text
