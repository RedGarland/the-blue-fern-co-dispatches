from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gaza_sync_core
from gaza_sync_core import SyncError, SyncSpec, apply, preflight, rollback  # noqa: E402


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "gaza"
    repo.mkdir()
    git(repo, "init", "-b", "add/pages-repo-default")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Gaza test")
    git(repo, "remote", "add", "origin", "https://github.com/RedGarland/the-blue-fern-co-dispatches.git")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "run_daily_gaza.py").write_text("old\n", encoding="utf-8")
    (repo / "scripts" / "run_gaza_daily_operator.py").write_text("old operator\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / ".gitignore").write_text(
        "ignored-runtime.json\nrollback-runtime.json\n", encoding="utf-8"
    )
    (repo / "data" / "records.json").write_text("runtime\n", encoding="utf-8")
    (repo / "data" / "rollback-runtime.json").write_text("old rollback source\n", encoding="utf-8")
    git(repo, "add", "-f", "data/rollback-runtime.json")
    old = commit(repo, "old release")
    git(repo, "switch", "-c", "candidate")
    (repo / "scripts" / "run_daily_gaza.py").write_text("new\n", encoding="utf-8")
    (repo / "scripts" / "run_gaza_daily_operator.py").write_text("new operator\n", encoding="utf-8")
    (repo / "data" / "ignored-runtime.json").write_text("candidate tracked bytes\n", encoding="utf-8")
    git(repo, "add", "-f", "data/ignored-runtime.json")
    (repo / "data" / "rollback-runtime.json").unlink()
    desired = commit(repo, "candidate release")
    git(repo, "switch", "add/pages-repo-default")
    return repo, old, desired


def spec(repo: Path, old: str, desired: str, backup: Path) -> SyncSpec:
    return SyncSpec(
        target=repo,
        backup_root=backup,
        expected_branch="add/pages-repo-default",
        expected_current_head=old,
        desired_head=desired,
        expected_remote="https://github.com/RedGarland/the-blue-fern-co-dispatches.git",
        reserve_bytes=1,
        target_allowlist=(repo,),
    )


def test_plan_is_read_only_and_preserves_runtime_file(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    runtime = repo / "data" / "runtime-untracked.json"
    runtime.write_text("keep", encoding="utf-8")
    result = preflight(spec(repo, old, desired, tmp_path / "backup"))
    assert result["current_head"] == old
    assert result["desired_head"] == desired
    assert runtime.read_text(encoding="utf-8") == "keep"
    assert git(repo, "rev-parse", "HEAD") == old
    assert not (tmp_path / "backup").exists()


def test_dirty_incoming_path_is_rejected(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    (repo / "scripts" / "run_daily_gaza.py").write_text("local edit\n", encoding="utf-8")
    with pytest.raises(SyncError, match="overlap"):
        preflight(spec(repo, old, desired, tmp_path / "backup"))


def test_ignored_incoming_path_is_rejected_without_overwriting_bytes(
    fixture_repo: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, old, desired = fixture_repo
    ignored = repo / "data" / "ignored-runtime.json"
    ignored.write_text("local runtime bytes\n", encoding="utf-8")
    with pytest.raises(SyncError, match="ignored files collide"):
        preflight(spec(repo, old, desired, tmp_path / "backup"))
    assert ignored.read_text(encoding="utf-8") == "local runtime bytes\n"


def test_apply_advances_only_fixture_and_records_backup(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    runtime = repo / "data" / "runtime-untracked.json"
    runtime.write_text("keep", encoding="utf-8")
    result = apply(spec(repo, old, desired, tmp_path / "backup"), allow_mutation=True)
    assert result["status"] == "success"
    assert git(repo, "rev-parse", "HEAD") == desired
    assert runtime.read_text(encoding="utf-8") == "keep"
    receipts = list((tmp_path / "backup").glob("*/apply-result.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["actual_head"] == desired


def test_rollback_requires_exact_state_and_restores_old_head(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    backup = tmp_path / "backup"
    result = apply(spec(repo, old, desired, backup), allow_mutation=True)
    receipt = next(backup.glob("*/apply-result.json"))
    rollback_result = rollback(receipt, allow_mutation=True, target_allowlist=(repo,))
    assert rollback_result["rollback_status"] == "success"
    assert git(repo, "rev-parse", "HEAD") == old
    assert result["actual_head"] == desired


def test_rollback_rejects_preexisting_dirty_bytes_without_reset(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    tracked = repo / "data" / "records.json"
    tracked.write_text("dirty before apply\n", encoding="utf-8")
    result = apply(spec(repo, old, desired, tmp_path / "backup"), allow_mutation=True)
    receipt = next((tmp_path / "backup").glob("*/apply-result.json"))
    with pytest.raises(SyncError, match="pre-existing dirty"):
        rollback(receipt, allow_mutation=True, target_allowlist=(repo,))
    assert tracked.read_text(encoding="utf-8") == "dirty before apply\n"
    assert git(repo, "rev-parse", "HEAD") == desired
    assert result["dirty_snapshot"][0]["sha256"] == gaza_sync_core._hash_file(tracked)


def test_rollback_rejects_same_status_with_changed_dirty_bytes(
    fixture_repo: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, old, desired = fixture_repo
    tracked = repo / "data" / "records.json"
    tracked.write_text("dirty before apply\n", encoding="utf-8")
    backup = tmp_path / "backup"
    apply(spec(repo, old, desired, backup), allow_mutation=True)
    receipt = next(backup.glob("*/apply-result.json"))
    tracked.write_text("different dirty bytes\n", encoding="utf-8")
    with pytest.raises(SyncError, match="dirty/untracked state changed"):
        rollback(receipt, allow_mutation=True, target_allowlist=(repo,))
    assert git(repo, "rev-parse", "HEAD") == desired
    assert tracked.read_text(encoding="utf-8") == "different dirty bytes\n"


def test_rollback_rejects_recreated_ignored_path_deleted_by_installed_revision(
    fixture_repo: tuple[Path, str, str], tmp_path: Path
) -> None:
    repo, old, desired = fixture_repo
    backup = tmp_path / "backup"
    apply(spec(repo, old, desired, backup), allow_mutation=True)
    receipt = next(backup.glob("*/apply-result.json"))
    recreated = repo / "data" / "rollback-runtime.json"
    recreated.write_text("runtime recreated after install\n", encoding="utf-8")

    with pytest.raises(SyncError, match="reverse incoming source paths"):
        rollback(receipt, allow_mutation=True, target_allowlist=(repo,))

    assert git(repo, "rev-parse", "HEAD") == desired
    assert recreated.read_text(encoding="utf-8") == "runtime recreated after install\n"


def test_dirty_snapshot_binds_staged_index_oid_when_status_and_worktree_match(
    fixture_repo: tuple[Path, str, str],
) -> None:
    repo, _old, _desired = fixture_repo
    tracked = repo / "data" / "records.json"
    tracked.write_text("staged-one\n", encoding="utf-8")
    git(repo, "add", "data/records.json")
    tracked.write_text("worktree\n", encoding="utf-8")
    before_records = gaza_sync_core._git_status(repo)
    before_snapshot = gaza_sync_core._dirty_snapshot(repo, before_records)
    staged_blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"staged-two\n",
        capture_output=True,
        check=True,
    ).stdout.decode().strip()
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--cacheinfo", f"100644,{staged_blob},data/records.json"],
        check=True,
        capture_output=True,
    )
    after_records = gaza_sync_core._git_status(repo)
    after_snapshot = gaza_sync_core._dirty_snapshot(repo, after_records)
    assert gaza_sync_core._status_key(before_records) == gaza_sync_core._status_key(after_records)
    assert before_snapshot[0]["sha256"] == after_snapshot[0]["sha256"]
    assert before_snapshot[0]["index_oid"] != after_snapshot[0]["index_oid"]
    assert not gaza_sync_core._dirty_state_matches(repo, before_records, before_snapshot)


def test_rollback_post_reset_head_verification_failure_is_unknown(
    fixture_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, old, desired = fixture_repo
    backup = tmp_path / "backup"
    apply(spec(repo, old, desired, backup), allow_mutation=True)
    receipt = next(backup.glob("*/apply-result.json"))
    original_git = gaza_sync_core._git
    head_reads = 0

    def fail_post_reset_head(target: Path, args: list[str], *, check: bool = True) -> str:
        nonlocal head_reads
        if args == ["rev-parse", "HEAD"]:
            head_reads += 1
            if head_reads == 2:
                raise SyncError("post-reset HEAD verification failed")
        return original_git(target, args, check=check)

    monkeypatch.setattr(gaza_sync_core, "_git", fail_post_reset_head)
    result = rollback(receipt, allow_mutation=True, target_allowlist=(repo,))
    assert result["rollback_status"] == "unknown"
    assert result["rollback_mutation"] == "unknown_after_rollback_verification"
    assert git(repo, "rev-parse", "HEAD") == old
    assert (receipt.parent / "rollback-result.json").exists()


def test_rollback_post_reset_dirty_state_mismatch_is_unknown(
    fixture_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, old, desired = fixture_repo
    backup = tmp_path / "backup"
    apply(spec(repo, old, desired, backup), allow_mutation=True)
    receipt = next(backup.glob("*/apply-result.json"))
    original_status = gaza_sync_core._git_status
    status_reads = 0

    def report_post_reset_dirty(target: Path) -> list[dict[str, str]]:
        nonlocal status_reads
        status_reads += 1
        if status_reads == 2:
            return [{"status": "??", "path": "unexpected-after-reset.json"}]
        return original_status(target)

    monkeypatch.setattr(gaza_sync_core, "_git_status", report_post_reset_dirty)
    result = rollback(receipt, allow_mutation=True, target_allowlist=(repo,))
    assert result["rollback_status"] == "unknown"
    assert result["rollback_mutation"] == "unknown_after_rollback_verification"
    assert result["rollback_head"] == old


def test_merge_error_with_changed_dirty_bytes_is_unknown(
    fixture_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, old, desired = fixture_repo
    original_run = gaza_sync_core.subprocess.run

    def fail_merge(command: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:4] == ["git", "-C", str(repo), "merge"]:
            (repo / "data" / "records.json").write_text("concurrent edit\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="merge failed")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(gaza_sync_core.subprocess, "run", fail_merge)
    result = apply(spec(repo, old, desired, tmp_path / "backup"), allow_mutation=True)
    assert result["status"] == "unknown"
    assert result["mutation"] == "unknown_after_merge_error"
    assert result["after_dirty_snapshot"][0]["sha256"] == gaza_sync_core._hash_file(
        repo / "data" / "records.json"
    )


def test_projected_preimages_must_fit_reserve(
    fixture_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, old, desired = fixture_repo
    real_usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        gaza_sync_core.shutil,
        "disk_usage",
        lambda _path: real_usage._replace(free=1),
    )
    with pytest.raises(SyncError, match="projected preimages"):
        preflight(spec(repo, old, desired, tmp_path / "backup"))


def test_post_apply_verification_failure_writes_unknown_receipt(
    fixture_repo: tuple[Path, str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, old, desired = fixture_repo
    original_status = gaza_sync_core._git_status
    calls = 0

    def fail_after_preflight(target: Path) -> list[dict[str, str]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SyncError("verification status unavailable")
        return original_status(target)

    monkeypatch.setattr(gaza_sync_core, "_git_status", fail_after_preflight)
    result = apply(spec(repo, old, desired, tmp_path / "backup"), allow_mutation=True)
    assert result["status"] == "unknown"
    assert result["mutation"] == "unknown_after_apply_verification"
    receipt = next((tmp_path / "backup").glob("*/apply-result.json"))
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "unknown"


def test_rollback_rejects_forged_target_even_when_receipt_allowlist_is_forged(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    apply(spec(repo, old, desired, tmp_path / "backup"), allow_mutation=True)
    receipt = next((tmp_path / "backup").glob("*/apply-result.json"))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    forged = tmp_path / "forged-repo"
    payload["target"] = str(forged)
    payload["target_allowlist"] = [str(forged)]
    forged_receipt = tmp_path / "forged.json"
    forged_receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SyncError, match="outside the Gaza allowlist"):
        rollback(forged_receipt, allow_mutation=True, target_allowlist=(repo,))


def test_parser_accepts_wrapper_rollback_without_head_arguments() -> None:
    args = gaza_sync_core._build_parser().parse_args(["--rollback-receipt", "receipt.json", "--allow-mutation"])
    assert args.rollback_receipt == Path("receipt.json")
    assert args.expected_current_head is None
    assert args.desired_head is None


def test_main_returns_failure_when_rollback_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        gaza_sync_core,
        "rollback",
        lambda *_args, **_kwargs: {"status": "success", "rollback_status": "failed"},
    )
    assert gaza_sync_core.main(["--rollback-receipt", str(receipt), "--allow-mutation"]) == 1
    assert json.loads(capsys.readouterr().out)["rollback_status"] == "failed"


def test_wrapper_makes_rollback_heads_optional_but_apply_heads_required(
    tmp_path: Path,
) -> None:
    candidates = [
        Path(__file__).resolve().parent / "sync_gaza_production_runner.ps1",
        Path(__file__).resolve().parent.parent / "scripts" / "sync_gaza_production_runner.ps1",
    ]
    wrapper_path = next(path for path in candidates if path.exists())
    wrapper = wrapper_path.read_text(encoding="utf-8")
    assert "[string]$ExpectedCurrentHead = ''" in wrapper
    assert "[string]$DesiredHead = ''" in wrapper
    assert "unless RollbackReceipt is supplied" in wrapper
    assert "--rollback-receipt" in wrapper

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not available for wrapper CLI execution")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    captured = tmp_path / "captured-args.txt"
    if os.name == "nt":
        fake_python = fake_bin / "python.cmd"
        fake_python.write_text(
            '@echo off\r\necho %* > "%GAZA_CAPTURE%"\r\nexit /b 0\r\n',
            encoding="utf-8",
        )
    else:
        fake_python = fake_bin / "python"
        fake_python.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" > "$GAZA_CAPTURE"\nexit 0\n',
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    env["GAZA_CAPTURE"] = str(captured)
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    rollback_run = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper_path),
            "-RollbackReceipt",
            str(receipt),
            "-AllowMutation",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert rollback_run.returncode == 0, rollback_run.stderr
    args = captured.read_text(encoding="utf-8")
    assert "--rollback-receipt" in args
    assert "--expected-current-head" not in args
    assert "--desired-head" not in args
    missing_heads_run = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper_path)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert missing_heads_run.returncode != 0
    assert "ExpectedCurrentHead" in (missing_heads_run.stdout + missing_heads_run.stderr)


def test_wrong_branch_and_missing_desired_object_fail_closed(fixture_repo: tuple[Path, str, str], tmp_path: Path) -> None:
    repo, old, desired = fixture_repo
    bad = spec(repo, old, "0" * 40, tmp_path / "backup")
    with pytest.raises(SyncError):
        preflight(bad)
    git(repo, "checkout", "-b", "wrong-branch")
    with pytest.raises(SyncError, match="branch drift"):
        preflight(spec(repo, old, desired, tmp_path / "backup2"))
