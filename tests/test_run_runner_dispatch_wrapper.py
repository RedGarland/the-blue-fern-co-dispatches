from __future__ import annotations

import json
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


def _make_fake_runner_repo(
    tmp_path: Path,
    *,
    sync_ok: bool = True,
    smoke_payload: object | None = None,
    smoke_mode: str = "json",
    capture_dispatch_argv: bool = False,
) -> Path:
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
    smoke_python = [
        "import json",
        "import sys",
        "print('manual source smoke gate')",
    ]
    if capture_dispatch_argv:
        smoke_python.append("print(json.dumps({'argv': sys.argv[1:]}, indent=2))")
    if smoke_mode == "json":
        smoke_python.append(f"payload = json.loads({json.dumps(json.dumps(smoke_payload))})")
        smoke_python.append("print(json.dumps(payload, indent=2))")
    elif smoke_mode == "array_json":
        smoke_python.append(f"payload = json.loads({json.dumps(json.dumps(smoke_payload))})")
        smoke_python.append("print(json.dumps([payload], indent=2))")
    else:
        raise AssertionError(f"unknown smoke_mode: {smoke_mode}")
    _write_runner_script(scripts_dir / "smoke_gaza_operator.py", "\n".join(smoke_python) + "\n")
    _write_runner_script(
        scripts_dir / "run_food_line_dispatch.py",
        """
import json
import sys

print("food line dispatch invoked")
print(json.dumps({"argv": sys.argv[1:]}, indent=2))
raise SystemExit(0)
""".strip()
        + "\n",
    )
    return repo


def _run_wrapper(repo: Path) -> subprocess.CompletedProcess[str]:
    return _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-02", "-CheckOnly"])


def _run_wrapper_with_args(repo: Path, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
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
            *extra_args,
        ],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _latest_log(repo: Path, dispatch: str = "gaza") -> str:
    return _read_log(max((repo / "logs").glob(f"runner-{dispatch}-*.log"), key=lambda p: p.stat().st_mtime))


def test_wrapper_check_only_succeeds_with_nested_operator_result_json(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "smoke_mode": "gate_only",
            "operator_result": {
                "ok": True,
                "operator_status": "MANUAL_SOURCE_VALID",
            },
            "postflight_result": {"ok": True, "errors": []},
        },
    )

    result = _run_wrapper(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "Runner check-only validation finished with exit code 0." in log_text
    assert "root_ok_present=True" in log_text
    assert "root_smoke_mode_present=True" in log_text
    assert "operator_result_present=True" in log_text
    assert "postflight_result_present=True" in log_text
    assert "nested_operator_status_present=True" in log_text


def test_wrapper_food_line_non_check_only_includes_collection_flags(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        capture_dispatch_argv=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "food-line", "-Date", "2026-07-02"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo, "food-line")
    assert "scripts\\run_food_line_dispatch.py --date 2026-07-02 --collect --audit-source-collection --publish --push --post-bluesky --generate-audio" in log_text
    assert "Runner dispatch finished with exit code 0." in log_text


def test_wrapper_food_line_check_only_does_not_request_collection_flags(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        capture_dispatch_argv=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "food-line", "-Date", "2026-07-02", "-CheckOnly"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo, "food-line")
    assert "scripts\\runner_repo_maintenance.py postflight" in log_text
    assert "Runner check-only validation finished with exit code 0." in log_text
    assert "--collect" not in log_text
    assert "--audit-source-collection" not in log_text


def test_wrapper_check_only_succeeds_with_single_element_array_json(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "smoke_mode": "gate_only",
            "operator_result": {
                "operator_status": "MANUAL_SOURCE_VALID",
            },
            "postflight_result": {"ok": True},
        },
        smoke_mode="array_json",
    )

    result = _run_wrapper(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Runner check-only validation finished with exit code 0." in _latest_log(repo)


def test_wrapper_check_only_fails_closed_when_sync_json_reports_not_ok(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=False,
        smoke_payload={
            "ok": True,
            "smoke_mode": "gate_only",
            "operator_result": {"operator_status": "MANUAL_SOURCE_VALID"},
            "postflight_result": {"ok": True},
        },
    )

    result = _run_wrapper(repo)

    assert result.returncode == 10
    assert "Runner sync/preflight reported ok=false" in _latest_log(repo)


def test_wrapper_check_only_fails_clearly_when_operator_status_missing(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "smoke_mode": "gate_only",
            "postflight_result": {"ok": True},
        },
    )

    result = _run_wrapper(repo)

    assert result.returncode == 10
    log_text = _latest_log(repo)
    assert "parsed JSON missing operator_status" in log_text
    assert "operator_result_present=False" in log_text


def test_wrapper_check_only_fails_when_nested_object_contains_status_but_root_lacks_operator_result(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "smoke_mode": "gate_only",
            "postflight_result": {
                "ok": True,
                "operator_result": {
                    "operator_status": "MANUAL_SOURCE_VALID",
                },
            },
        },
    )

    result = _run_wrapper(repo)

    assert result.returncode == 10
    log_text = _latest_log(repo)
    assert "postflight_result_present=True" in log_text
    assert "operator_result_present=False" in log_text
    assert "parsed JSON missing operator_status" in log_text
