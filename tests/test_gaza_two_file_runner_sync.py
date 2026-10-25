import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_gaza_two_file_runner.ps1"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
OPERATOR_BLOB = "9ab8b65401805aec00ded19ed1939bfe949f9ac3"
BLUESKY_BLOB = "aa01f332e2c017a8d003a6bd1a985a1273ef5626"
OLD_HEAD = "f" * 40
CURRENT_PROTECTED_HEAD = "a" * 40


def test_requires_pinned_protected_head_and_gaza_default():
    assert '[Parameter(Mandatory = $true)][string]$ExpectedProtectedHead' in SCRIPT
    assert '[Parameter(Mandatory = $true)][string]$ExpectedOperatorBlob' in SCRIPT
    assert '[Parameter(Mandatory = $true)][string]$ExpectedBlueskyBlob' in SCRIPT
    assert 'C:\\BlueFernRunner\\GazaDispatchesCurrent6' in SCRIPT
    assert 'TargetBranch = "add/pages-repo-default"' in SCRIPT


def test_allowlist_is_exactly_two_runtime_paths():
    assert '"scripts/run_gaza_daily_operator.py"' in SCRIPT
    assert '"src/bluefern_dispatches/bluesky_post.py"' in SCRIPT
    assert 'FoodLineCurrent6' not in SCRIPT
    assert 'CareLineNationalCurrent8' not in SCRIPT
    assert 'ICEMonitorCurrent' not in SCRIPT


def test_expected_blobs_are_bound_per_reviewed_release():
    assert '"scripts/run_gaza_daily_operator.py" = $ExpectedOperatorBlob' in SCRIPT
    assert '"src/bluefern_dispatches/bluesky_post.py" = $ExpectedBlueskyBlob' in SCRIPT
    assert 'Expected runtime blobs must be full 40-character Git blob SHAs.' in SCRIPT
    assert 'Expected blob mismatch' in SCRIPT


def test_incoming_revision_must_stay_within_allowlist():
    assert 'Protected revision is broader than the approved two-file scope' in SCRIPT
    assert 'FULL_SOURCE_ADVANCEMENT_REQUIRED' in SCRIPT
    assert '$_ -notin $AllowedPaths' in SCRIPT
    assert '"diff", "--name-only", "HEAD..$ExpectedProtectedHead"' in SCRIPT


def test_dirty_and_branch_guards_are_fail_closed():
    assert 'Target branch mismatch' in SCRIPT
    assert 'Approved source paths are already dirty' in SCRIPT
    assert 'Target HEAD is not an ancestor' in SCRIPT
    assert 'Target dirty-state paths changed before apply' in SCRIPT
    assert 'Preserve leading porcelain status whitespace' in SCRIPT


def test_backup_manifest_and_reviewed_rollback_are_required():
    assert 'BackupRoot is required with -Apply' in SCRIPT
    assert 'backup-manifest.json' in SCRIPT
    assert 'Use a reviewed protected rollback head' in SCRIPT
    assert '"merge", "--ff-only", $ExpectedProtectedHead' in SCRIPT


def test_no_destructive_or_public_side_effect_commands():
    for forbidden in ('git", "reset', 'git", "clean', 'git", "checkout', 'git", "push'):
        assert forbidden not in SCRIPT
    assert 'production_state_mutated = $true' in SCRIPT
    assert 'public_side_effects = $false' in SCRIPT
    assert 'publication_triggered = $false' in SCRIPT
    assert 'collection_triggered = $false' in SCRIPT


def _powershell():
    return shutil.which("pwsh") or shutil.which("powershell")


def _fake_git_bin(tmp_path: Path):
    """Create a deterministic git shim so the controller runs real guards."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_code = r'''
import os
import sys

args = sys.argv[1:]
if args[:1] == ["-C"]:
    args = args[2:]
mode = os.environ.get("FAKE_GIT_MODE", "valid")
operator_blob = os.environ.get("FAKE_OPERATOR_BLOB", "9ab8b65401805aec00ded19ed1939bfe949f9ac3")
bluesky_blob = os.environ.get("FAKE_BLUESKY_BLOB", "aa01f332e2c017a8d003a6bd1a985a1273ef5626")
remote = os.environ.get("FAKE_REMOTE", "a" * 40)
head = os.environ.get("FAKE_HEAD", "f" * 40)
allowed = ["scripts/run_gaza_daily_operator.py", "src/bluefern_dispatches/bluesky_post.py"]

if args[:2] == ["branch", "--show-current"]:
    print("add/pages-repo-default")
elif args[:2] == ["rev-parse", "HEAD"]:
    print(head)
elif args[:1] == ["fetch"]:
    pass
elif args[:2] == ["rev-parse", "origin/add/pages-repo-default"]:
    print(remote)
elif args[:2] == ["cat-file", "-e"]:
    pass
elif args[:1] == ["rev-parse"] and len(args) == 2 and ":" in args[1]:
    path = args[1].split(":", 1)[1]
    print(operator_blob if path == allowed[0] else bluesky_blob)
elif args[:1] == ["status"]:
    if mode == "dirty":
        print(" M scripts/run_gaza_daily_operator.py")
elif args[:2] == ["diff", "--name-only"]:
    if mode == "scope":
        print("\n".join(allowed + [f"extra/path-{i}.txt" for i in range(21)]))
    else:
        print("\n".join(allowed))
elif args[:2] == ["merge-base", "--is-ancestor"]:
    pass
'''
    fake_py = fake_bin / "fake_git.py"
    fake_py.write_text(fake_code, encoding="utf-8")
    if os.name == "nt":
        launcher = fake_bin / "git.cmd"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
        )
    else:
        launcher = fake_bin / "git"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{fake_py}" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return fake_bin


def _run_controller(tmp_path: Path, mode: str, expected_head: str, *, operator_blob=OPERATOR_BLOB, bluesky_blob=BLUESKY_BLOB):
    powershell = _powershell()
    if not powershell:
        import pytest

        pytest.skip("PowerShell is required for behavior-level controller tests")
    target = tmp_path / "gaza-runner"
    (target / ".git").mkdir(parents=True)
    report = tmp_path / "report.json"
    fake_bin = _fake_git_bin(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            "FAKE_GIT_MODE": mode,
            "FAKE_REMOTE": expected_head,
            "FAKE_HEAD": OLD_HEAD,
            "FAKE_OPERATOR_BLOB": operator_blob,
            "FAKE_BLUESKY_BLOB": bluesky_blob,
        }
    )
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(SCRIPT_PATH),
            "-ExpectedProtectedHead",
            expected_head,
            "-ExpectedOperatorBlob",
            operator_blob,
            "-ExpectedBlueskyBlob",
            bluesky_blob,
            "-TargetRoot",
            str(target),
            "-ReportPath",
            str(report),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert report.exists(), result.stdout + result.stderr
    return result, json.loads(report.read_text(encoding="utf-8-sig"))


def test_actual_old_baseline_fails_closed_on_full_source_divergence(tmp_path):
    result, report = _run_controller(tmp_path, "scope", CURRENT_PROTECTED_HEAD)
    assert result.returncode == 2
    assert report["result"] == "FULL_SOURCE_ADVANCEMENT_REQUIRED"
    assert len(report["incoming_paths"]) == 23
    assert "scope" in report["error"].lower()
    assert report["production_state_mutated"] is False


def test_first_porcelain_dirty_entry_is_not_truncated(tmp_path):
    result, report = _run_controller(tmp_path, "dirty", "d" * 40)
    assert result.returncode == 2
    assert report["result"] == "BLOCKED"
    assert "Approved source paths are already dirty: scripts/run_gaza_daily_operator.py" in report["error"]
    assert "Approved source paths are already dirty: cripts/run_gaza_daily_operator.py" not in report["error"]
    assert report["production_state_mutated"] is False


def test_valid_reviewed_forward_or_rollback_plan_binds_its_exact_blobs(tmp_path):
    rollback_head = "b" * 40
    rollback_operator = "c" * 40
    rollback_bluesky = "e" * 40
    result, report = _run_controller(
        tmp_path,
        "valid",
        rollback_head,
        operator_blob=rollback_operator,
        bluesky_blob=rollback_bluesky,
    )
    assert result.returncode == 0
    assert report["result"] == "PLAN_READY_TO_FAST_FORWARD"
    assert report["expected_blobs"]["scripts/run_gaza_daily_operator.py"] == rollback_operator
    assert report["expected_blobs"]["src/bluefern_dispatches/bluesky_post.py"] == rollback_bluesky
    assert report["incoming_paths"] == [
        "scripts/run_gaza_daily_operator.py",
        "src/bluefern_dispatches/bluesky_post.py",
    ]
    assert report["production_state_mutated"] is False

