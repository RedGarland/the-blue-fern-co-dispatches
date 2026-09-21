from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator


def _item(tmp_path: Path) -> tuple[Path, operator.EngineeringWorkItem]:
    root = tmp_path / "ops/operator"
    worktree = tmp_path / "OperatorWorktrees/bfoe-test"
    worktree.mkdir(parents=True)
    item = operator.EngineeringWorkItem(
        work_id="bfoe-test",
        incident_id="bfo-test",
        dispatch="care-line",
        classification="FAILED_RUN",
        state="WORKTREE_CREATED",
        branch="operator/care-line/bfo-test",
        worktree=str(worktree),
        evidence=["status/operational-health/care-line/proof.json"],
        allowed_paths=["src/bluefern_dispatches/care_line_*.py"],
        tests_required=["python -m pytest tests/test_care_line*.py -q"],
        diagnosis_path=str(root / "engineering/active/bfoe-test/diagnosis.json"),
    )
    operator._write_json(Path(item.diagnosis_path), {"work_id": item.work_id, "evidence": item.evidence})
    operator._save_engineering_work_item(root, item)
    return root, item


class CodexCliRunner:
    def __init__(self, help_text: str, *, exec_result: operator.EngineeringCommandResult | None = None) -> None:
        self.help_text = help_text
        self.exec_result = exec_result or operator.EngineeringCommandResult(0, "Root cause: fixed\n")
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        self.commands.append(args)
        if args[1:] == ["--version"]:
            return operator.EngineeringCommandResult(0, "codex 1.2.3\n")
        if args[1:] == ["exec", "--help"]:
            return operator.EngineeringCommandResult(0, self.help_text)
        if args[1:] == ["login", "status"]:
            return operator.EngineeringCommandResult(0, "", "Logged in using ChatGPT\n")
        if args[1] == "exec":
            return self.exec_result
        if args[:2] == ["git", "status"]:
            return operator.EngineeringCommandResult(0, "## operator/care-line/bfo-test\n")
        if args[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return operator.EngineeringCommandResult(0, "operator/care-line/bfo-test\n")
        return operator.EngineeringCommandResult(0)


HELP_WITH_APPROVAL = "codex exec --sandbox workspace-write --ask-for-approval never --cd <DIR> --ignore-user-config\n"
HELP_WITHOUT_APPROVAL = "codex exec --sandbox workspace-write --cd <DIR> --ignore-user-config\n"
HELP_WITHOUT_IGNORE = "codex exec --sandbox workspace-write --cd <DIR>\n"
HELP_WITHOUT_SANDBOX = "codex exec --cd <DIR> --ignore-user-config\n"
HELP_WITHOUT_CD = "codex exec --sandbox workspace-write --ignore-user-config\n"


def _last_exec(runner: CodexCliRunner) -> list[str]:
    return [command for command in runner.commands if len(command) > 1 and command[1] == "exec" and "--help" not in command][-1]


def test_cli_with_ask_for_approval_supported_includes_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    runner = CodexCliRunner(HELP_WITH_APPROVAL)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    patched = operator._invoke_codex_for_engineering(root, item, runner=runner)

    command = _last_exec(runner)
    assert patched.state == "PATCHED"
    assert command[command.index("--ask-for-approval") + 1] == "never"
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--cd") + 1] == item.worktree


def test_cli_without_ask_for_approval_omits_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    runner = CodexCliRunner(HELP_WITHOUT_APPROVAL)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    patched = operator._invoke_codex_for_engineering(root, item, runner=runner)

    command = _last_exec(runner)
    assert patched.state == "PATCHED"
    assert "--ask-for-approval" not in command
    assert "--ignore-user-config" in command
    assert "danger-full-access" not in command
    assert "--add-dir" not in command


@pytest.mark.parametrize("help_text", [HELP_WITHOUT_SANDBOX, HELP_WITHOUT_CD])
def test_missing_mandatory_safety_flag_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, help_text: str) -> None:
    root, item = _item(tmp_path)
    runner = CodexCliRunner(help_text)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    blocked = operator._invoke_codex_for_engineering(root, item, runner=runner)

    assert blocked.state == "BLOCKED"
    assert blocked.failure_classification == "CODEX_CLI_ARGUMENT_ERROR"
    assert not any(command for command in runner.commands if len(command) > 1 and command[1] == "exec" and "--help" not in command)


def test_ignore_user_config_missing_is_omitted_but_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    runner = CodexCliRunner(HELP_WITHOUT_IGNORE)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    patched = operator._invoke_codex_for_engineering(root, item, runner=runner)

    command = _last_exec(runner)
    assert patched.state == "PATCHED"
    assert "--ignore-user-config" not in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--cd") + 1] == item.worktree


def test_diagnose_reports_supported_flags_and_effective_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    runner = CodexCliRunner(HELP_WITHOUT_APPROVAL)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    payload = operator.diagnose_engineering_work_item(item.work_id, operator_root=root, runner=runner)

    readiness = payload["codex_readiness"]
    assert readiness["supported_exec_flags"] == {
        "sandbox": True,
        "cd": True,
        "ignore_user_config": True,
        "ask_for_approval": False,
    }
    assert readiness["effective_exec_command_shape"] == [
        "<codex>",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        "<worktree>",
        "--ignore-user-config",
        "<prompt>",
    ]
    assert all("Root cause:" not in part for part in readiness["effective_exec_command_shape"])


def test_existing_attempt_artifact_is_not_overwritten(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    operator._write_codex_attempt_artifact(
        root,
        item,
        attempt=1,
        started_at="2026-09-21T00:00:00Z",
        completed_at="2026-09-21T00:00:01Z",
        executable="codex",
        cwd=Path(item.worktree),
        result=operator.EngineeringCommandResult(1, "old", "old"),
        failure_classification="CODEX_CLI_ARGUMENT_ERROR",
    )
    runner = CodexCliRunner(HELP_WITHOUT_APPROVAL, exec_result=operator.EngineeringCommandResult(1, "", "process failed"))
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    blocked = operator._invoke_codex_for_engineering(root, item, runner=runner)

    assert blocked.diagnostic_artifact.endswith("codex-attempt-2.json")
    first = json.loads((root / "engineering/active/bfoe-test/codex-attempt-1.json").read_text(encoding="utf-8"))
    second = json.loads((root / "engineering/active/bfoe-test/codex-attempt-2.json").read_text(encoding="utf-8"))
    assert first["stdout_tail"] == "old"
    assert second["stderr_tail"] == "process failed"


def test_retry_logic_still_refuses_without_confirmation(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    item = operator.replace(item, state="BLOCKED", blocked_reason="codex execution failed")
    operator._save_engineering_work_item(root, item)

    payload = operator.retry_engineering_work_item(item.work_id, confirm="", operator_root=root)

    assert payload["accepted"] is False
    assert payload["reason"] == "confirmation token required"
