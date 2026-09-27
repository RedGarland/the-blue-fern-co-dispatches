from __future__ import annotations

import json
from pathlib import Path

from scripts import blue_fern_operator as operator


class FakeRunnerSyncRunner:
    def __init__(self, *, sync_exit_code: int = 10) -> None:
        self.sync_exit_code = sync_exit_code
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> operator.EngineeringCommandResult:
        self.commands.append(args)
        if args[:4] == ["git", "ls-remote", "origin", "refs/heads/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0, "abc123\trefs/heads/add/pages-repo-default\n")
        if args and args[0] == "powershell":
            report_path = Path(args[args.index("-ReportPath") + 1])
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "Status": "READY_TO_FAST_FORWARD" if self.sync_exit_code == 10 else "SYNCED",
                        "PublicSideEffects": False,
                        "PagesMutation": False,
                        "PublicationTriggered": False,
                        "CollectionTriggered": False,
                        "SchedulerMutation": False,
                    }
                ),
                encoding="utf-8",
            )
            return operator.EngineeringCommandResult(self.sync_exit_code, "sync stdout\n", "sync stderr\n")
        return operator.EngineeringCommandResult(0)


def _make_repo_root(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    scripts = repo_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "sync_active_production_runners.ps1").write_text("# sync\n", encoding="utf-8")
    (repo_root / ".git").mkdir()
    (repo_root / ".git" / "HEAD").write_text("abc123\n", encoding="utf-8")
    return repo_root


def test_runner_sync_plan_ready_is_successful_checkpoint(tmp_path: Path) -> None:
    repo_root = _make_repo_root(tmp_path)
    operator_root = tmp_path / "ops" / "operator"
    runner = FakeRunnerSyncRunner(sync_exit_code=10)

    receipt = operator.run_guarded_runner_sync(repo_root=repo_root, operator_root=operator_root, runner=runner)

    assert receipt["accepted"] is True
    assert receipt["outcome"] == "PLAN_READY_TO_FAST_FORWARD"
    assert receipt["apply_requested"] is False
    assert receipt["expected_protected_head"] == "abc123"
    assert receipt["public_side_effects"] is False
    assert receipt["pages_mutation"] is False
    assert receipt["publication_triggered"] is False
    assert receipt["collection_triggered"] is False
    assert receipt["scheduler_mutation"] is False
    assert Path(receipt["receipt_path"]).is_file()
    sync_command = [command for command in runner.commands if command and command[0] == "powershell"][0]
    assert "-Apply" not in sync_command
    assert "-ProveStatusExport" not in sync_command


def test_runner_sync_apply_and_status_proof_are_explicit(tmp_path: Path) -> None:
    repo_root = _make_repo_root(tmp_path)
    operator_root = tmp_path / "ops" / "operator"
    runner = FakeRunnerSyncRunner(sync_exit_code=0)

    receipt = operator.run_guarded_runner_sync(
        repo_root=repo_root,
        operator_root=operator_root,
        expected_protected_head="def456",
        apply=True,
        prove_status_export=True,
        runner=runner,
    )

    assert receipt["accepted"] is True
    assert receipt["outcome"] == "APPLY_COMPLETE"
    assert receipt["apply_requested"] is True
    assert receipt["prove_status_export_requested"] is True
    assert receipt["expected_protected_head"] == "def456"
    assert receipt["production_state_mutated"] is True
    sync_command = [command for command in runner.commands if command and command[0] == "powershell"][0]
    assert "-Apply" in sync_command
    assert "-ProveStatusExport" in sync_command
    assert ["git", "ls-remote", "origin", "refs/heads/add/pages-repo-default"] not in runner.commands


def test_runner_sync_refuses_status_proof_without_apply(tmp_path: Path) -> None:
    repo_root = _make_repo_root(tmp_path)
    operator_root = tmp_path / "ops" / "operator"
    runner = FakeRunnerSyncRunner(sync_exit_code=0)

    receipt = operator.run_guarded_runner_sync(
        repo_root=repo_root,
        operator_root=operator_root,
        expected_protected_head="def456",
        prove_status_export=True,
        runner=runner,
    )

    assert receipt["accepted"] is False
    assert receipt["outcome"] == "REFUSED"
    assert receipt["refusal_reason"] == "--prove-status-export requires --apply"
    assert not [command for command in runner.commands if command and command[0] == "powershell"]


def test_runner_sync_refuses_missing_sync_script(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    operator_root = tmp_path / "ops" / "operator"
    runner = FakeRunnerSyncRunner(sync_exit_code=0)

    receipt = operator.run_guarded_runner_sync(
        repo_root=repo_root,
        operator_root=operator_root,
        expected_protected_head="def456",
        apply=True,
        runner=runner,
    )

    assert receipt["accepted"] is False
    assert receipt["outcome"] == "REFUSED"
    assert "guarded sync script is missing" in receipt["refusal_reason"]
    assert not [command for command in runner.commands if command and command[0] == "powershell"]
