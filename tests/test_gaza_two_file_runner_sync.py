import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_gaza_two_file_runner.ps1"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
DESIGN_DOC = (Path(__file__).resolve().parents[1] / "docs" / "gaza-two-file-runner-sync.md").read_text(encoding="utf-8")
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
    assert 'Target dirty-state paths, statuses, or contents changed before apply' in SCRIPT
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


def test_overlay_design_binds_runtime_root_and_exact_tree_manifest():
    assert 'bluefern.gaza_immutable_release.v1' in DESIGN_DOC
    assert 'BLUEFERN_RUNTIME_ROOT' in DESIGN_DOC
    assert 'unchanged_entries_verified' in DESIGN_DOC
    assert 'atomic' in DESIGN_DOC.lower()
    assert '"activation": "unactivated"' in DESIGN_DOC


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
elif args[:3] == ["ls-remote", "--heads", "origin"]:
    print(remote + "\trefs/heads/add/pages-repo-default")
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


def _run_controller(tmp_path: Path, mode: str, expected_head: str, *, operator_blob=OPERATOR_BLOB, bluesky_blob=BLUESKY_BLOB, report_path: Path | None = None):
    powershell = _powershell()
    if not powershell:
        import pytest

        pytest.skip("PowerShell is required for behavior-level controller tests")
    target = tmp_path / "gaza-runner"
    (target / ".git").mkdir(parents=True)
    for relative in ("scripts/run_gaza_daily_operator.py", "src/bluefern_dispatches/bluesky_post.py"):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture: {relative}\n", encoding="utf-8")
    report = report_path or (tmp_path / "report.json")
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
    if report.exists():
        return result, json.loads(report.read_text(encoding="utf-8-sig"))
    # Unsafe report paths are rejected and the controller must fall back to a
    # safe temporary receipt rather than writing inside the production root.
    matches = re.findall(r"Report:\s*(.+)", result.stdout)
    assert matches, result.stdout + result.stderr
    safe_report = Path(matches[-1].strip())
    assert safe_report.exists()
    return result, json.loads(safe_report.read_text(encoding="utf-8-sig"))


def test_actual_old_baseline_fails_closed_on_full_source_divergence(tmp_path):
    result, report = _run_controller(tmp_path, "scope", CURRENT_PROTECTED_HEAD)
    assert result.returncode == 2
    assert report["result"] == "FULL_SOURCE_ADVANCEMENT_REQUIRED"
    assert len(report["incoming_paths"]) == 23
    assert "scope" in report["error"].lower()
    assert report["production_state_mutated"] is False


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and result.returncode != 0:
        if "NtCreateDirectoryObject" in result.stderr:
            import pytest

            pytest.skip("Windows Git local transport is unavailable in this restricted test session")
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}\n{result.stdout}")
    return result


def _real_git_fixture(tmp_path: Path):
    """Build a real bare remote and target checkout for apply/rollback tests."""
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    target = tmp_path / "target"
    backup = tmp_path / "backups"
    _git(tmp_path, "init", str(source))
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "Gaza sync tests")
    _git(source, "checkout", "-b", "add/pages-repo-default")
    paths = {
        "scripts/run_gaza_daily_operator.py": "baseline operator\n",
        "src/bluefern_dispatches/bluesky_post.py": "baseline bluesky\n",
        "README.md": "unchanged tree entry\n",
    }
    for relative, content in paths.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "baseline")
    baseline = _git(source, "rev-parse", "HEAD").stdout.strip()

    (source / "scripts/run_gaza_daily_operator.py").write_text("forward operator\n", encoding="utf-8")
    (source / "src/bluefern_dispatches/bluesky_post.py").write_text("forward bluesky\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "reviewed forward pair")
    forward = _git(source, "rev-parse", "HEAD").stdout.strip()
    # A local bare clone avoids transport helpers and keeps this test fully
    # offline while still exercising a real fetch/merge remote.
    source_uri = source.as_uri()
    _git(tmp_path, "clone", "--bare", source_uri, str(bare))
    _git(tmp_path, "clone", "--branch", "add/pages-repo-default", str(bare), str(target))
    _git(target, "config", "user.email", "tests@example.invalid")
    _git(target, "config", "user.name", "Gaza sync tests")
    _git(target, "reset", "--hard", baseline)
    operator_blob = _git(source, "rev-parse", f"{forward}:scripts/run_gaza_daily_operator.py").stdout.strip()
    bluesky_blob = _git(source, "rev-parse", f"{forward}:src/bluefern_dispatches/bluesky_post.py").stdout.strip()
    return {
        "bare": bare,
        "source": source,
        "target": target,
        "backup": backup,
        "baseline": baseline,
        "forward": forward,
        "operator_blob": operator_blob,
        "bluesky_blob": bluesky_blob,
    }


def _run_real_controller(fixture, *, expected_head: str, operator_blob: str, bluesky_blob: str, apply: bool):
    powershell = _powershell()
    if not powershell:
        import pytest

        pytest.skip("PowerShell is required for behavior-level controller tests")
    report = fixture["target"].parent / ("apply-report.json" if apply else "plan-report.json")
    args = [
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
        str(fixture["target"]),
        "-BackupRoot",
        str(fixture["backup"]),
        "-ReportPath",
        str(report),
    ]
    if apply:
        args.append("-Apply")
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert report.exists(), result.stdout + result.stderr
    return result, json.loads(report.read_text(encoding="utf-8-sig"))


def test_real_git_apply_preserves_backup_byte_integrity(tmp_path):
    fixture = _real_git_fixture(tmp_path)
    result, report = _run_real_controller(
        fixture,
        expected_head=fixture["forward"],
        operator_blob=fixture["operator_blob"],
        bluesky_blob=fixture["bluesky_blob"],
        apply=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["result"] == "APPLY_COMPLETE"
    assert report["production_state_mutated"] is True
    assert report["production_state_mutation_status"] == "mutated_verified"
    assert _git(fixture["target"], "rev-parse", "HEAD").stdout.strip() == fixture["forward"]
    manifest = json.loads(Path(report["backup_manifest"]).read_text(encoding="utf-8-sig"))
    assert len(manifest["entries"]) == 2
    for entry in manifest["entries"]:
        assert entry["source_file"] == entry["backup_file"]
        backup_path = Path(entry["backup_path"])
        assert backup_path.is_file()
        assert backup_path.read_bytes() in {b"baseline operator\n", b"baseline bluesky\n"}


def test_real_git_apply_failure_reports_no_mutation_when_backup_source_missing(tmp_path):
    fixture = _real_git_fixture(tmp_path)
    (fixture["target"] / "src/bluefern_dispatches/bluesky_post.py").unlink()
    result, report = _run_real_controller(
        fixture,
        expected_head=fixture["forward"],
        operator_blob=fixture["operator_blob"],
        bluesky_blob=fixture["bluesky_blob"],
        apply=True,
    )
    assert result.returncode == 2
    assert report["result"] == "BLOCKED"
    assert report["production_state_mutated"] is False
    assert report["production_state_mutation_status"] == "not_attempted"
    assert _git(fixture["target"], "rev-parse", "HEAD").stdout.strip() == fixture["baseline"]


def test_real_git_descendant_rollback_is_guarded_and_restores_reviewed_pair(tmp_path):
    fixture = _real_git_fixture(tmp_path)
    source = fixture["source"]
    (source / "scripts/run_gaza_daily_operator.py").write_text("rollback operator\n", encoding="utf-8")
    (source / "src/bluefern_dispatches/bluesky_post.py").write_text("rollback bluesky\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "reviewed descendant rollback")
    rollback = _git(source, "rev-parse", "HEAD").stdout.strip()
    rollback_operator = _git(source, "rev-parse", f"{rollback}:scripts/run_gaza_daily_operator.py").stdout.strip()
    rollback_bluesky = _git(source, "rev-parse", f"{rollback}:src/bluefern_dispatches/bluesky_post.py").stdout.strip()
    _git(fixture["bare"], "fetch", source.as_uri(), "add/pages-repo-default")
    _git(fixture["bare"], "update-ref", "refs/heads/add/pages-repo-default", rollback)
    _git(fixture["target"], "merge", "--ff-only", fixture["forward"])
    result, report = _run_real_controller(
        fixture,
        expected_head=rollback,
        operator_blob=rollback_operator,
        bluesky_blob=rollback_bluesky,
        apply=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["result"] == "APPLY_COMPLETE"
    assert _git(fixture["target"], "rev-parse", "HEAD").stdout.strip() == rollback
    assert (fixture["target"] / "scripts/run_gaza_daily_operator.py").read_text() == "rollback operator\n"


def test_real_git_non_descendant_rollback_is_blocked_without_mutation(tmp_path):
    fixture = _real_git_fixture(tmp_path)
    target = fixture["target"]
    _git(target, "merge", "--ff-only", fixture["forward"])
    # Move the remote branch back only in this disposable repository. The
    # target remains at the reviewed forward commit, so the requested old head
    # is not a fast-forward destination.
    _git(fixture["bare"], "update-ref", "refs/heads/add/pages-repo-default", fixture["baseline"])
    result, report = _run_real_controller(
        fixture,
        expected_head=fixture["baseline"],
        operator_blob=_git(fixture["source"], "rev-parse", f"{fixture['baseline']}:scripts/run_gaza_daily_operator.py").stdout.strip(),
        bluesky_blob=_git(fixture["source"], "rev-parse", f"{fixture['baseline']}:src/bluefern_dispatches/bluesky_post.py").stdout.strip(),
        apply=True,
    )
    assert result.returncode == 2
    assert report["result"] == "BLOCKED"
    assert report["production_state_mutated"] is False
    assert _git(target, "rev-parse", "HEAD").stdout.strip() == fixture["forward"]


def test_first_porcelain_dirty_entry_is_not_truncated(tmp_path):
    result, report = _run_controller(tmp_path, "dirty", "d" * 40)
    assert result.returncode == 2
    assert report["result"] == "BLOCKED"
    assert "Approved source paths are already dirty: scripts/run_gaza_daily_operator.py" in report["error"]
    assert "Approved source paths are already dirty: cripts/run_gaza_daily_operator.py" not in report["error"]
    assert report["production_state_mutated"] is False


def test_report_path_inside_target_is_rejected_and_receipt_falls_back_safely(tmp_path):
    target = tmp_path / "gaza-runner"
    unsafe_report = target / "unsafe-report.json"
    result, report = _run_controller(
        tmp_path,
        "valid",
        CURRENT_PROTECTED_HEAD,
        report_path=unsafe_report,
    )
    assert result.returncode == 2
    assert not unsafe_report.exists()
    assert report["report_path_rejected"] == str(unsafe_report)
    assert "ReportPath" in report["error"]
    assert report["production_state_mutated"] is False


def test_report_path_with_missing_nested_ancestors_uses_platform_safe_walk(tmp_path):
    report_path = tmp_path / "artifact-parent" / "missing-child" / "report.json"
    result, report = _run_controller(
        tmp_path,
        "valid",
        CURRENT_PROTECTED_HEAD,
        report_path=report_path,
    )
    assert result.returncode == 0
    assert report["result"] == "PLAN_READY_TO_FAST_FORWARD"
    assert report_path.is_file()
    assert "DirectorySeparatorChar" in SCRIPT
    assert "AltDirectorySeparatorChar" in SCRIPT
    assert "while ($probe -and -not (Test-Path -LiteralPath $probe))" in SCRIPT


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

