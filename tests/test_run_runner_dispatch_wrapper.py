from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = REPO_ROOT / "scripts" / "run_runner_dispatch.ps1"
DISPATCH_WRAPPER_PATH = REPO_ROOT / "scripts" / "run_dispatches.ps1"


def _write_runner_script(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def _init_git_repo(repo: Path, branch: str) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "codex@example.com")
    _git(repo, "config", "user.name", "Codex")
    _git(repo, "checkout", "-b", branch)


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, "--no-gpg-sign")


def _read_log(path: Path) -> str:
    for encoding in ("utf-8", "utf-16", "utf-16-le"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError:
            continue
    raise AssertionError(f"could not decode log file: {path}")


def _git_status_short(repo: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _make_fake_runner_repo(
    tmp_path: Path,
    *,
    source_branch: str = "add/pages-repo-default",
    sync_ok: bool = True,
    smoke_payload: object | None = None,
    smoke_mode: str = "json",
    capture_dispatch_argv: bool = False,
    validate_placeholder_manual_sources: bool = False,
) -> Path:
    repo = tmp_path / "runner-repo"
    scripts_dir = repo / "scripts"
    pages_repo = repo / "bluefern-dispatches-pages"
    logs_dir = repo / "logs"
    venv_scripts_dir = repo / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    pages_repo.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    venv_scripts_dir.mkdir(parents=True)
    shutil.copy2(WRAPPER_PATH, scripts_dir / "run_runner_dispatch.ps1")
    (repo / ".gitignore").write_text(
        "\n".join(
            [
                "logs/",
                ".venv/",
                "bluefern-dispatches-pages/",
                "output/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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
        scripts_dir / "run_gaza_daily_operator.py",
        """
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

print("gaza operator invoked")
args = sys.argv[1:]
date = "unknown"
if "--date" in args:
    try:
        date = args[args.index("--date") + 1]
    except (ValueError, IndexError):
        date = "unknown"
manual_source_path = ROOT / "data" / "dispatches" / "gaza" / "sources" / date / "manual_sources.json"
workspace_output = ROOT / "output" / "site" / "gaza" / "editions" / date
workspace_output.mkdir(parents=True, exist_ok=True)
(workspace_output / "index.html").write_text("dry-run", encoding="utf-8")
if __VALIDATE_PLACEHOLDER__ and manual_source_path.is_file():
    manual_text = manual_source_path.read_text(encoding="utf-8", errors="replace").lower()
    if "example.com" in manual_text or "example news" in manual_text or "manually added for generator run." in manual_text:
        result = {{
            "ok": False,
            "operator_status": "MANUAL_SOURCE_INVALID",
            "email_status": "not_requested",
            "bluesky_status": "skipped",
            "pages_push_ok": None,
            "pages_repo_updated": False,
            "publish_ok": False,
            "generation_ok": False,
            "validation_ok": False,
            "manual_source_present": True,
            "cwd": str(Path.cwd()),
            "root": str(ROOT),
            "output_root": str(ROOT / "output"),
            "argv": args,
            "errors": ["record 1 appears to be a placeholder/example source"],
        }}
        print(json.dumps(result, indent=2))
        raise SystemExit(1)
result = {
    "ok": True,
    "operator_status": "DRY_RUN_READY",
    "email_status": "not_requested",
    "bluesky_status": "skipped",
    "pages_push_ok": None,
    "pages_repo_updated": False,
    "publish_ok": False,
    "generation_ok": True,
    "validation_ok": True,
    "manual_source_present": manual_source_path.is_file(),
    "cwd": str(Path.cwd()),
    "root": str(ROOT),
    "output_root": str(ROOT / "output"),
    "argv": args,
}
print(json.dumps(result, indent=2))
raise SystemExit(0)
""".strip()
        .replace("__VALIDATE_PLACEHOLDER__", str(validate_placeholder_manual_sources))
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
args = sys.argv[1:]
payload = {
    "ok": True,
    "date": "2026-07-02",
    "check_only": "--check-only" in args,
    "release_candidate": False,
    "publication_attempted": False,
    "pages_attempted": False,
    "approved_proposal_path": None,
    "release_readiness_path": None,
    "review_snapshot_path": None,
    "errors": [],
    "status": "check_only_no_release_candidate" if "--check-only" in args else "publication_success",
    "dispatch": "food-line",
}
print(json.dumps(payload, indent=2))
raise SystemExit(0)
""".strip()
        + "\n",
    )
    _write_runner_script(
        scripts_dir / "run_food_line_publication_runner.py",
        """
import json
import sys

args = sys.argv[1:]
status = "publication_success"
if "--check-only" in args:
    status = "check_only_success"
elif "--dry-run-full" in args:
    status = "dry_run_full_success"
result = {
    "ok": True,
    "status": status,
    "mode": "check_only" if status == "check_only_success" else ("dry_run_full" if status == "dry_run_full_success" else "publication"),
    "proposed_modified_paths": [],
    "proposed_deleted_paths": [],
    "push_performed": "--push" in args,
    "argv": args,
}
print("food line publication runner invoked")
print(json.dumps(result, indent=2))
raise SystemExit(0)
""".strip()
        + "\n",
    )
    _init_git_repo(repo, source_branch)
    _commit_all(repo, "initial source commit")
    _init_git_repo(pages_repo, "gh-pages")
    (pages_repo / "index.html").write_text("pages", encoding="utf-8")
    _commit_all(pages_repo, "initial pages commit")
    shutil.copy2(Path(sys.executable), venv_scripts_dir / "python.exe")
    pyvenv_config = Path(sys.executable).parents[1] / "pyvenv.cfg"
    if pyvenv_config.is_file():
        shutil.copy2(pyvenv_config, venv_scripts_dir.parent / "pyvenv.cfg")
    return repo


def _run_wrapper(repo: Path) -> subprocess.CompletedProcess[str]:
    return _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-02", "-CheckOnly"])


def _run_wrapper_with_args(
    repo: Path,
    extra_args: list[str],
    *,
    repo_root: Path | None = None,
    env_overrides: dict[str, str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("powershell.exe not available in test environment")
    env = os.environ.copy()
    for key, value in (env_overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    invocation = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(repo / "scripts" / "run_runner_dispatch.ps1"),
    ]
    if repo_root is not None:
        invocation.extend(["-RepoRoot", str(repo_root)])
    invocation.extend(extra_args)
    return subprocess.run(
        invocation,
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _latest_log(repo: Path, dispatch: str = "gaza") -> str:
    return _read_log(max((repo / "logs").glob(f"runner-{dispatch}-*.log"), key=lambda p: p.stat().st_mtime))


@pytest.mark.parametrize("wrapper_path", [WRAPPER_PATH, DISPATCH_WRAPPER_PATH])
def test_wrapper_leaves_smtp_password_unset_when_credential_blob_is_empty(wrapper_path: Path) -> None:
    text = wrapper_path.read_text(encoding="utf-8")
    assert "had no password blob; leaving SMTP_PASSWORD unset so environment or .env fallback can load it." in text
    assert "if (-not [string]::IsNullOrEmpty($password))" in text
    assert "$env:SMTP_PASSWORD = $password" in text


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
    log_path = max((repo / "logs").glob("runner-gaza-*.log"), key=lambda p: p.stat().st_mtime)
    log_text = _read_log(log_path)
    assert "Runner check-only validation finished with exit code 0." in log_text
    assert "--protected-path" in log_text
    assert str(log_path) in log_text
    assert "root_ok_present=True" in log_text
    assert "root_smoke_mode_present=True" in log_text
    assert "operator_result_present=True" in log_text
    assert "postflight_result_present=True" in log_text
    assert "nested_operator_status_present=True" in log_text


def test_wrapper_food_line_non_check_only_omits_bluesky_without_switch(tmp_path: Path) -> None:
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

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "food-line",
            "-RepoRoot",
            str(repo),
            "-PagesRepo",
            str(repo / "bluefern-dispatches-pages"),
            "-SourceBranch",
            "add/pages-repo-default",
            "-PagesBranch",
            "gh-pages",
            "-Date",
            "2026-07-02",
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo, "food-line")
    assert "scripts\run_food_line_dispatch.py --date 2026-07-02 --collect --audit-source-collection --publish --push --generate-audio" in log_text
    assert "--post-bluesky" not in log_text
    assert "Runner dispatch finished with exit code 0." in log_text


def test_wrapper_food_line_non_check_only_includes_bluesky_only_when_requested(tmp_path: Path) -> None:
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

    result = _run_wrapper_with_args(repo, ["-Dispatch", "food-line", "-Date", "2026-07-02", "-PostBluesky"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo, "food-line")
    assert "--post-bluesky" in log_text
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

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "food-line",
            "-RepoRoot",
            str(repo),
            "-PagesRepo",
            str(repo / "bluefern-dispatches-pages"),
            "-SourceBranch",
            "add/pages-repo-default",
            "-PagesBranch",
            "gh-pages",
            "-Date",
            "2026-07-02",
            "-CheckOnly",
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo, "food-line")
    assert "scripts\\run_food_line_dispatch.py --date 2026-07-02 --check-only" in log_text
    assert "--check-only" in log_text
    assert "--dry-run-full" not in log_text
    assert "--collect" not in log_text
    assert "--audit-source-collection" not in log_text
    assert "Food Line check-only gate finished with no release candidate." in log_text


def test_wrapper_food_line_does_not_persist_global_system_or_local_git_config(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)
    pages_repo = repo / "bluefern-dispatches-pages"
    global_config = tmp_path / "global.gitconfig"
    system_config = tmp_path / "system.gitconfig"
    global_config.write_text("[test]\n\tmarker = global\n", encoding="utf-8")
    system_config.write_text("[test]\n\tmarker = system\n", encoding="utf-8")
    config_paths = [
        global_config,
        system_config,
        repo / ".git" / "config",
        pages_repo / ".git" / "config",
    ]
    before = {path: path.read_bytes() for path in config_paths}

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch", "food-line",
            "-RepoRoot", str(repo),
            "-PagesRepo", str(pages_repo),
            "-SourceBranch", "add/pages-repo-default",
            "-PagesBranch", "gh-pages",
            "-Date", "2026-07-02",
            "-CheckOnly",
        ],
        env_overrides={
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_SYSTEM": str(system_config),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert {path: path.read_bytes() for path in config_paths} == before


def test_wrapper_food_line_stdout_fallback_is_machine_readable(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)
    pages_repo = repo / "bluefern-dispatches-pages"
    shutil.rmtree(repo / "logs")
    (repo / "logs").write_text("not a directory\n", encoding="utf-8")

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch", "food-line",
            "-RepoRoot", str(repo),
            "-PagesRepo", str(pages_repo),
            "-SourceBranch", "add/pages-repo-default",
            "-PagesBranch", "gh-pages",
            "-Date", "2026-07-02",
            "-CheckOnly",
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "check_only_no_release_candidate"
    assert payload["logging"]["file_logging_available"] is False
    assert payload["logging"]["durable_log_written"] is False
    assert payload["logging"]["stdout_fallback_used"] is True
    assert str(repo / "logs" / "runner-food-line-") in payload["logging"]["intended_log_path"]
    assert payload["logging"]["fallback_messages"]


def test_wrapper_food_line_stdout_fallback_preserves_validation_failure(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)
    pages_repo = repo / "bluefern-dispatches-pages"
    shutil.rmtree(repo / "logs")
    (repo / "logs").write_text("not a directory\n", encoding="utf-8")
    _write_runner_script(
        repo / "scripts" / "run_food_line_publication_runner.py",
        "import json\nprint(json.dumps({'ok': False, 'status': 'dry_run_full_failed', 'errors': ['validation failed']}))\nraise SystemExit(1)\n",
    )

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch", "food-line",
            "-RepoRoot", str(repo),
            "-PagesRepo", str(pages_repo),
            "-SourceBranch", "add/pages-repo-default",
            "-PagesBranch", "gh-pages",
            "-Date", "2026-07-02",
            "-DryRunFull",
        ],
    )

    assert result.returncode == 10
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "wrapper_failed"
    assert payload["logging"]["stdout_fallback_used"] is True
    assert payload["logging"]["durable_log_written"] is False
    assert any("failed with exit code 1" in error for error in payload["errors"])


@pytest.mark.parametrize("flag", ["-PostBluesky", "-GenerateAudio"])
def test_wrapper_food_line_rejects_gaza_only_flags(tmp_path: Path, flag: str) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "food-line",
            "-RepoRoot",
            str(repo),
            "-PagesRepo",
            str(repo / "bluefern-dispatches-pages"),
            "-SourceBranch",
            "add/pages-repo-default",
            "-PagesBranch",
            "gh-pages",
            "-Date",
            "2026-07-02",
            flag,
        ],
    )

    assert result.returncode == 10, result.stdout + result.stderr
    assert f"Food Line dispatch does not support {flag}." in _latest_log(repo, "food-line")


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


def test_wrapper_gaza_non_check_only_defaults_to_no_push_no_post_no_audio(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-02"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert f"Resolved repo root from wrapper location: {repo}" in log_text
    assert f"Resolved Pages repo: {repo / 'bluefern-dispatches-pages'}" in log_text
    assert f"Selected Python path: {repo / '.venv' / 'Scripts' / 'python.exe'}" in log_text
    assert "Push enabled: False" in log_text
    assert "Bluesky enabled: False" in log_text
    assert "Audio enabled: False" in log_text
    assert "Check-only: False" in log_text
    assert "scripts\\run_gaza_daily_operator.py --date 2026-07-02" in log_text
    assert "--email-report" in log_text
    assert "--push" not in log_text
    assert "--post-bluesky" not in log_text
    assert "--generate-audio" not in log_text
    assert "agent/refine-care-line-signal-wire-public-rendering" in log_text


def test_wrapper_gaza_default_repo_root_comes_from_wrapper_location(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-02"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert f"Resolved repo root from wrapper location: {repo}" in log_text


def test_wrapper_gaza_blank_child_output_preserves_empty_line(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )
    _write_runner_script(
        repo / "scripts" / "run_gaza_daily_operator.py",
        """
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

print("alpha")
print()
print("omega")
print(json.dumps({
    "ok": True,
    "operator_status": "DRY_RUN_READY",
    "email_status": "not_requested",
    "bluesky_status": "skipped",
    "pages_push_ok": None,
    "pages_repo_updated": False,
    "publish_ok": False,
    "generation_ok": True,
    "validation_ok": True,
    "manual_source_present": False,
    "cwd": str(Path.cwd()),
    "root": str(ROOT),
    "output_root": str(ROOT / "output"),
    "argv": sys.argv[1:],
}, indent=2))
raise SystemExit(0)
""".strip()
        + "\n",
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-02"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "alpha" in log_text
    assert "omega" in log_text
    assert "ParameterBindingValidationException" not in log_text
    assert "\n\nomega" in log_text or "alpha\n\n" in log_text


def test_wrapper_gaza_explicit_repo_root_override_is_used(tmp_path: Path) -> None:
    wrapper_repo = _make_fake_runner_repo(
        tmp_path / "wrapper",
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )
    override_repo = _make_fake_runner_repo(
        tmp_path / "override",
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(wrapper_repo, ["-Dispatch", "gaza", "-Date", "2026-07-02"], repo_root=override_repo)

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(override_repo)
    assert f"Resolved repo root from -RepoRoot: {override_repo}" in log_text
    assert f"Resolved Pages repo: {override_repo / 'bluefern-dispatches-pages'}" in log_text


def test_wrapper_gaza_postflight_cleanup_protects_active_log(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-02"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_path = max((repo / "logs").glob("runner-gaza-*.log"), key=lambda p: p.stat().st_mtime)
    log_text = _read_log(log_path)
    assert "--protected-path" in log_text
    assert str(log_path) in log_text
    assert "scripts\\runner_repo_maintenance.py postflight" in log_text


def test_wrapper_gaza_non_check_only_appends_explicit_live_flags_only_when_requested(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-02", "-Push", "-PostBluesky", "-GenerateAudio"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "Push enabled: True" in log_text
    assert "Bluesky enabled: True" in log_text
    assert "Audio enabled: True" in log_text
    assert "scripts\\run_gaza_daily_operator.py --date 2026-07-02" in log_text
    assert "--email-report" in log_text
    assert "--push" in log_text
    assert "--post-bluesky" in log_text
    assert "--generate-audio" in log_text


def test_wrapper_gaza_generate_audio_defaults_to_openai_provider(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        capture_dispatch_argv=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-02", "-GenerateAudio"],
        env_overrides={"OPENAI_API_KEY": "secret-api-key"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "TTS provider: openai" in log_text
    assert "--generate-audio" in log_text
    assert "--tts-provider openai" in log_text
    assert "secret-api-key" not in log_text
    assert "secret-api-key" not in result.stdout
    assert "secret-api-key" not in result.stderr


def test_wrapper_gaza_generate_audio_explicit_openai_provider_is_passed_through(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        capture_dispatch_argv=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "gaza",
            "-Date",
            "2026-07-02",
            "-GenerateAudio",
            "-TtsProvider",
            "openai",
        ],
        env_overrides={"OPENAI_API_KEY": "secret-api-key"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "TTS provider: openai" in log_text
    assert "--tts-provider openai" in log_text
    assert "secret-api-key" not in log_text


def test_wrapper_gaza_generate_audio_with_none_provider_fails_before_dispatch(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-02", "-GenerateAudio", "-TtsProvider", "none"],
        env_overrides={"OPENAI_API_KEY": "secret-api-key"},
    )

    assert result.returncode == 10, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "TTS provider: none" in log_text
    assert "Gaza audio was requested with -TtsProvider none" in log_text
    assert "sync step starting" not in log_text


def test_wrapper_gaza_accepts_explicit_canonical_source_branch(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "gaza",
            "-Date",
            "2026-07-02",
            "-SourceBranch",
            "agent/refine-care-line-signal-wire-public-rendering",
            "-DryRunFull",
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "Source repo must be on" not in log_text
    assert "agent/refine-care-line-signal-wire-public-rendering" in log_text


def test_wrapper_gaza_rejects_wrong_source_branch(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "gaza",
            "-Date",
            "2026-07-02",
            "-SourceBranch",
            "add/pages-repo-default",
            "-DryRunFull",
        ],
    )

    assert result.returncode == 10, result.stdout + result.stderr
    assert "Source repo must be on add/pages-repo-default; found agent/refine-care-line-signal-wire-public-rendering." in _latest_log(repo)


def test_wrapper_gaza_rejects_missing_repo_python(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )
    (repo / ".venv" / "Scripts" / "python.exe").unlink()

    result = _run_wrapper_with_args(
        repo,
        [
            "-Dispatch",
            "gaza",
            "-Date",
            "2026-07-02",
        ],
    )

    assert result.returncode == 10, result.stdout + result.stderr
    assert not (repo / ".venv" / "Scripts" / "python.exe").exists()


def test_wrapper_gaza_generate_audio_openai_without_api_key_fails_before_dispatch(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-02", "-GenerateAudio"],
        env_overrides={"OPENAI_API_KEY": None},
    )

    assert result.returncode == 10, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "TTS provider: openai" in log_text
    assert "OPENAI_API_KEY is missing or blank" in log_text
    assert "sync step starting" not in log_text
    assert "gaza operator invoked" not in log_text
    assert "--generate-audio" not in log_text


def test_wrapper_gaza_no_audio_does_not_require_openai_api_key(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(
        tmp_path,
        sync_ok=True,
        smoke_payload={
            "ok": True,
            "edition_mode": "no_public_edition",
            "public_rendered": False,
        },
    )

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-02"],
        env_overrides={"OPENAI_API_KEY": None},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "Audio enabled: False" in log_text
    assert "TTS provider:" not in log_text
    assert "--generate-audio" not in log_text


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


def test_wrapper_gaza_dry_run_full_uses_isolated_output_root_and_skips_live_actions(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True, capture_dispatch_argv=True)

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-05", "-DryRunFull"],
        env_overrides={"OPENAI_API_KEY": "secret-api-key"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "Dry-run full: True" in log_text
    assert "Isolated Gaza dry-run workspace:" in log_text
    assert "--generate-audio" in log_text
    assert "--allow-listing-shrink" in log_text
    assert "--tts-provider none" in log_text
    assert "--push" not in log_text
    assert "--post-bluesky" not in log_text
    assert "--email-report" not in log_text
    assert "secret-api-key" not in log_text
    assert '"email_status": "not_requested"' in log_text
    assert '"bluesky_status": "skipped"' in log_text
    assert '"pages_push_ok": null' in log_text

    source_clone_match = re.search(r"Isolated source clone: (.+)", log_text)
    workspace_match = re.search(r"Isolated Gaza dry-run workspace: (.+)", log_text)
    assert source_clone_match, log_text
    assert workspace_match, log_text
    source_clone = Path(source_clone_match.group(1).strip())
    workspace = Path(workspace_match.group(1).strip())
    assert source_clone.is_dir()
    assert workspace.is_dir()
    assert (source_clone / "output" / "site" / "gaza" / "editions" / "2026-07-05" / "index.html").is_file()
    assert _git_status_short(repo) == ""
    assert _git_status_short(repo / "bluefern-dispatches-pages") == ""


def test_wrapper_gaza_dry_run_full_snapshots_untracked_manual_source_inputs(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)
    manual_source_dir = repo / "data" / "dispatches" / "gaza" / "sources" / "2026-07-05"
    manual_source_dir.mkdir(parents=True, exist_ok=True)
    (manual_source_dir / "manual_sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "url": "https://example.com/story",
                        "publisher": "Example Publisher",
                        "title": "Example title",
                        "published_at": "2026-07-05T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-05", "-DryRunFull"])

    assert result.returncode == 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert '"manual_source_present": true' in log_text


def test_wrapper_gaza_dry_run_full_rejects_placeholder_manual_source_inputs(tmp_path: Path) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True, validate_placeholder_manual_sources=True)
    manual_source_dir = repo / "data" / "dispatches" / "gaza" / "sources" / "2026-07-05"
    manual_source_dir.mkdir(parents=True, exist_ok=True)
    (manual_source_dir / "manual_sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "url": "https://example.com/story",
                        "publisher": "Example News",
                        "title": "Example title",
                        "published_at": "2026-07-05T00:00:00Z",
                        "retrieved_at": "2026-07-05T00:00:00Z",
                        "summary_or_snippet": "Manually added for generator run.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = _run_wrapper_with_args(repo, ["-Dispatch", "gaza", "-Date", "2026-07-05", "-DryRunFull"])

    assert result.returncode != 0, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "Runner wrapper failed: RuntimeException: Isolated Gaza dry-run failed with exit code 1." in log_text


@pytest.mark.parametrize("extra_flag", ["-Push", "-PostBluesky"])
def test_wrapper_gaza_dry_run_full_rejects_live_action_flags(tmp_path: Path, extra_flag: str) -> None:
    repo = _make_fake_runner_repo(tmp_path, sync_ok=True)

    result = _run_wrapper_with_args(
        repo,
        ["-Dispatch", "gaza", "-Date", "2026-07-05", "-DryRunFull", extra_flag],
    )

    assert result.returncode == 10, result.stdout + result.stderr
    log_text = _latest_log(repo)
    assert "-DryRunFull cannot be combined with -CheckOnly, -Push, or -PostBluesky." in log_text
