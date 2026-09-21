from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator


@pytest.fixture(autouse=True)
def approved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(operator, "APPROVED_ENGINEERING_WORKTREE_ROOT", tmp_path / "OperatorWorktrees")


def _blocked_item(tmp_path: Path, **overrides: object) -> tuple[Path, operator.EngineeringWorkItem]:
    root = tmp_path / "ops/operator"
    worktree = operator.APPROVED_ENGINEERING_WORKTREE_ROOT / "bfoe-test"
    (worktree / ".git").mkdir(parents=True)
    item = operator.EngineeringWorkItem(
        work_id="bfoe-test",
        incident_id="bfo-test",
        dispatch="care-line",
        classification="FAILED_RUN",
        state="BLOCKED",
        branch="operator/care-line/bfo-test",
        worktree=str(worktree),
        evidence=["status/operational-health/care-line/proof.json"],
        allowed_paths=["src/bluefern_dispatches/care_line_*.py"],
        tests_required=["python -m pytest tests/test_care_line*.py -q"],
        base_sha="old-base",
        blocked_reason="codex execution failed",
        merge_allowed=False,
    )
    item = operator.replace(item, **overrides)
    operator._save_engineering_work_item(root, item)
    return root, item


class RetryRunner:
    def __init__(
        self,
        *,
        dirty: bool = False,
        remote_branch: bool = False,
        open_pr: bool = False,
        readiness_ok: bool = True,
        ff_ok: bool = True,
        wrong_branch: bool = False,
    ) -> None:
        self.commands: list[tuple[list[str], Path]] = []
        self.dirty = dirty
        self.remote_branch = remote_branch
        self.open_pr = open_pr
        self.readiness_ok = readiness_ok
        self.ff_ok = ff_ok
        self.wrong_branch = wrong_branch

    def __call__(self, args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        self.commands.append((args, cwd))
        if args == ["git", "fetch", "origin"]:
            return operator.EngineeringCommandResult(0)
        if args == ["git", "rev-parse", "origin/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0, "new-base\n")
        if args == ["git", "rev-parse", "HEAD"]:
            return operator.EngineeringCommandResult(0, "old-base\n" if len([c for c, _ in self.commands if c == args]) == 1 else "new-base\n")
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return operator.EngineeringCommandResult(0, f"{cwd}\n")
        if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return operator.EngineeringCommandResult(0, "wrong\n" if self.wrong_branch else "operator/care-line/bfo-test\n")
        if args == ["git", "rev-parse", "--git-common-dir"]:
            return operator.EngineeringCommandResult(0, "C:/repo/.git\n")
        if args[:3] == ["git", "merge-base", "--is-ancestor"]:
            return operator.EngineeringCommandResult(0)
        if args == ["git", "status", "--short"]:
            return operator.EngineeringCommandResult(0, " M src/bluefern_dispatches/care_line_bug.py\n" if self.dirty else "")
        if args[:4] == ["git", "ls-remote", "--heads", "origin"]:
            return operator.EngineeringCommandResult(0, "abc\trefs/heads/operator/care-line/bfo-test\n" if self.remote_branch else "")
        if args[:3] == ["gh", "pr", "list"]:
            return operator.EngineeringCommandResult(0, '[{"number": 9}]\n' if self.open_pr else "[]\n")
        if args[1:] == ["--version"]:
            return operator.EngineeringCommandResult(0 if self.readiness_ok else 1, "codex 1.2.3\n", "" if self.readiness_ok else "not logged in\n")
        if args[1:] == ["exec", "--help"]:
            return operator.EngineeringCommandResult(0, "codex exec --sandbox workspace-write --cd <DIR>\n")
        if args[1:] == ["login", "status"]:
            return operator.EngineeringCommandResult(0 if self.readiness_ok else 1, "", "" if self.readiness_ok else "not logged in\n")
        if args == ["git", "merge", "--ff-only", "origin/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0 if self.ff_ok else 1, "ff\n", "" if self.ff_ok else "not possible\n")
        return operator.EngineeringCommandResult(0)


class Executor:
    def __init__(self, *, result_state: str = "PR_OPEN") -> None:
        self.calls: list[operator.EngineeringWorkItem] = []
        self.result_state = result_state

    def __call__(self, item: operator.EngineeringWorkItem, **_kwargs: object) -> operator.EngineeringWorkItem:
        self.calls.append(item)
        return operator.replace(item, state=self.result_state, merge_allowed=False)


def test_retry_without_confirmation_refused(tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)

    payload = operator.retry_engineering_work_item(item.work_id, confirm="", operator_root=root)

    assert payload["accepted"] is False
    assert payload["reason"] == "confirmation token required"


def test_retry_wrong_confirmation_refused(tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)

    payload = operator.retry_engineering_work_item(item.work_id, confirm="YES", operator_root=root)

    assert payload["accepted"] is False


def test_non_blocked_work_item_refused(tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path, state="PR_OPEN")

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root)

    assert payload["accepted"] is False
    assert payload["reason"] == "work item is not BLOCKED"


def test_legacy_codex_failure_with_green_readiness_retries_and_updates_base(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path, failure_classification=None, blocked_reason="codex execution failed")
    runner = RetryRunner()
    executor = Executor()
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=runner, executor=executor)

    assert payload["accepted"] is True
    assert executor.calls[0].state == "WORKTREE_CREATED"
    assert executor.calls[0].base_sha == "new-base"
    assert executor.calls[0].retry_authorization["prior_blocked_reason"] == "codex execution failed"
    assert payload["work_item"]["merge_allowed"] is False
    commands = [command for command, _cwd in runner.commands]
    assert ["git", "merge", "--ff-only", "origin/add/pages-repo-default"] in commands
    assert not any(command[:2] == ["git", "reset"] for command in commands)
    assert not any(command[:2] == ["git", "rebase"] for command in commands)
    assert not any(command[:2] == ["git", "clean"] for command in commands)


def test_classified_codex_failure_with_green_readiness_is_permitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path, failure_classification="CODEX_PROCESS_FAILURE", blocked_reason="codex execution failed: CODEX_PROCESS_FAILURE")
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=RetryRunner(), executor=Executor())

    assert payload["accepted"] is True


def test_readiness_failure_refuses_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=RetryRunner(readiness_ok=False), executor=Executor())

    assert payload["accepted"] is False
    assert "codex readiness failed" in payload["reason"]


@pytest.mark.parametrize(
    ("blocked_reason", "failure_classification"),
    [
        ("patch changed paths outside allowed scope", None),
        ("validation failed after bounded repair attempts", None),
        ("remote branch already exists without recorded PR", None),
        ("existing worktree branch mismatch: other", None),
    ],
)
def test_non_codex_blocked_reasons_are_refused(tmp_path: Path, blocked_reason: str, failure_classification: str | None) -> None:
    root, item = _blocked_item(tmp_path, blocked_reason=blocked_reason, failure_classification=failure_classification)

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root)

    assert payload["accepted"] is False
    assert payload["reason"] == "blocked reason is not Codex retry eligible"


def test_dirty_worktree_refuses_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=RetryRunner(dirty=True), executor=Executor())

    assert payload["accepted"] is False
    assert payload["reason"] == "worktree is not clean"


def test_remote_branch_or_open_pr_refuses_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    remote_payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=RetryRunner(remote_branch=True), executor=Executor())
    pr_payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=RetryRunner(open_pr=True), executor=Executor())

    assert remote_payload["reason"] == "remote repair branch already exists"
    assert pr_payload["reason"] == "repair PR already exists"


def test_fast_forward_failure_refuses_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    payload = operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root, repo_root=tmp_path / "repo", runner=RetryRunner(ff_ok=False), executor=Executor())

    assert payload["accepted"] is False
    assert payload["reason"] == "worktree fast-forward failed"


def test_retry_failure_preserves_new_attempt_without_overwrite(tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path)
    operator._write_codex_attempt_artifact(
        root,
        item,
        attempt=1,
        started_at="2026-09-21T00:00:00Z",
        completed_at="2026-09-21T00:00:01Z",
        executable="codex",
        cwd=Path(item.worktree),
        result=operator.EngineeringCommandResult(1, "old", "old"),
        failure_classification="CODEX_PROCESS_FAILURE",
    )

    blocked = operator._invoke_codex_for_engineering(
        root,
        item,
        codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(1, "new", "new"),
    )

    assert blocked.diagnostic_artifact.endswith("codex-attempt-2.json")
    assert json.loads((root / "engineering/active/bfoe-test/codex-attempt-1.json").read_text(encoding="utf-8"))["stdout_tail"] == "old"
    assert json.loads((root / "engineering/active/bfoe-test/codex-attempt-2.json").read_text(encoding="utf-8"))["stdout_tail"] == "new"


def test_production_runner_marker_untouched_by_refused_retry(tmp_path: Path) -> None:
    root, item = _blocked_item(tmp_path, blocked_reason="patch changed paths outside allowed scope")
    marker = tmp_path / "production-runner/marker.txt"
    marker.parent.mkdir()
    marker.write_text("stable", encoding="utf-8")

    operator.retry_engineering_work_item(item.work_id, confirm="RETRY_CODEX", operator_root=root)

    assert marker.read_text(encoding="utf-8") == "stable"
