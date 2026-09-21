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
    diagnosis = {
        "work_id": item.work_id,
        "incident_id": item.incident_id,
        "classification": item.classification,
        "evidence": item.evidence,
        "allowed_paths": item.allowed_paths,
    }
    operator._write_json(Path(item.diagnosis_path), diagnosis)
    operator._save_engineering_work_item(root, item)
    return root, item


class CodexProbeRunner:
    def __init__(self, *, login: operator.EngineeringCommandResult | None = None, exec_result: operator.EngineeringCommandResult | None = None, help_result: operator.EngineeringCommandResult | None = None) -> None:
        self.commands: list[list[str]] = []
        self.login = login or operator.EngineeringCommandResult(0, "Logged in\n")
        self.exec_result = exec_result or operator.EngineeringCommandResult(0, "Root cause: fixed\n")
        self.help_result = help_result or operator.EngineeringCommandResult(0, "codex exec --sandbox workspace-write --cd <DIR>\n")

    def __call__(self, args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        self.commands.append(args)
        if args[1:] == ["--version"]:
            return operator.EngineeringCommandResult(0, "codex 1.2.3\n")
        if args[1:] == ["exec", "--help"]:
            return self.help_result
        if args[1:] == ["login", "status"]:
            return self.login
        if args[1] == "exec" and "--sandbox" in args:
            return self.exec_result
        if args[:2] == ["git", "status"]:
            return operator.EngineeringCommandResult(0, "## operator/care-line/bfo-test\n")
        if args[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return operator.EngineeringCommandResult(0, "operator/care-line/bfo-test\n")
        return operator.EngineeringCommandResult(0)


def _attempt(root: Path) -> dict:
    path = root / "engineering/active/bfoe-test/codex-attempt-1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_codex_stderr_is_preserved_in_bounded_attempt_artifact(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    stderr = "x" * 9000

    blocked = operator._invoke_codex_for_engineering(
        root,
        item,
        codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(1, "out", stderr),
    )

    artifact = _attempt(root)
    assert blocked.state == "BLOCKED"
    assert blocked.diagnostic_artifact.endswith("codex-attempt-1.json")
    assert artifact["stderr_tail"] == stderr[-8192:]
    assert artifact["stdout_tail"] == "out"
    assert artifact["failure_classification"] == "CODEX_PROCESS_FAILURE"


def test_codex_stdout_is_bounded_and_prompt_is_not_duplicated(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    stdout = "y" * 9000

    operator._invoke_codex_for_engineering(
        root,
        item,
        codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(1, stdout, "failed"),
    )

    artifact = _attempt(root)
    assert artifact["stdout_tail"] == stdout[-8192:]
    assert "prompt" not in artifact
    assert "environment" not in artifact


def test_codex_artifact_does_not_persist_env_or_secret_material(tmp_path: Path) -> None:
    root, item = _item(tmp_path)

    operator._invoke_codex_for_engineering(
        root,
        item,
        codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(1, "", "token=SECRET api_key=SECRET"),
    )

    artifact = _attempt(root)
    artifact_text = json.dumps(artifact)
    assert "env" not in artifact
    assert "SECRET" not in artifact_text
    assert "api_key=[REDACTED]" in artifact["stderr_tail"]
    assert "token=[REDACTED]" in artifact["stderr_tail"]


def test_auth_failure_classification_records_attempt_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    blocked = operator._invoke_codex_for_engineering(
        root,
        item,
        runner=CodexProbeRunner(login=operator.EngineeringCommandResult(1, "", "not logged in")),
    )

    assert blocked.failure_classification == "CODEX_AUTH_FAILURE"
    assert blocked.blocked_reason == "codex readiness probe failed: CODEX_AUTH_FAILURE"
    assert blocked.diagnostic_artifact.endswith("codex-attempt-1.json")
    assert _attempt(root)["failure_classification"] == "CODEX_AUTH_FAILURE"


def test_unsupported_cli_argument_classification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    blocked = operator._invoke_codex_for_engineering(
        root,
        item,
        runner=CodexProbeRunner(help_result=operator.EngineeringCommandResult(2, "", "unknown option --sandbox")),
    )

    assert blocked.failure_classification == "CODEX_CLI_ARGUMENT_ERROR"
    assert "CODEX_CLI_ARGUMENT_ERROR" in (blocked.blocked_reason or "")


def test_sandbox_failure_classification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    blocked = operator._invoke_codex_for_engineering(
        root,
        item,
        runner=CodexProbeRunner(exec_result=operator.EngineeringCommandResult(1, "", "sandbox denied workspace-write")),
    )

    assert blocked.failure_classification == "CODEX_SANDBOX_FAILURE"
    assert _attempt(root)["failure_classification"] == "CODEX_SANDBOX_FAILURE"


def test_generic_process_failure_classification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    blocked = operator._invoke_codex_for_engineering(
        root,
        item,
        runner=CodexProbeRunner(exec_result=operator.EngineeringCommandResult(1, "", "process exited")),
    )

    assert blocked.failure_classification == "CODEX_PROCESS_FAILURE"


def test_blocked_work_item_remains_blocked_without_retry(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    blocked = operator.replace(item, state="BLOCKED", blocked_reason="codex execution failed: CODEX_AUTH_FAILURE")
    runner = CodexProbeRunner()

    result = operator.execute_engineering_work_item(blocked, operator_root=root, repo_root=tmp_path / "repo", runner=runner)

    assert result.state == "BLOCKED"
    assert runner.commands == []


def test_diagnose_command_is_read_only_and_reports_codex_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")
    runner = CodexProbeRunner()

    payload = operator.diagnose_engineering_work_item(item.work_id, operator_root=root, runner=runner)

    assert payload["work_item"]["work_id"] == item.work_id
    assert payload["codex_readiness"]["version"]["stdout_tail"] == "codex 1.2.3\n"
    assert payload["codex_readiness"]["exec_help"]["supports_workspace_write"] is True
    assert payload["codex_readiness"]["login_status"]["exit_code"] == 0
    assert not any(command[1:2] == ["exec"] and "--sandbox" in command for command in runner.commands)


def test_diagnose_reports_latest_codex_attempt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")
    operator._invoke_codex_for_engineering(root, item, codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(1, "", "process exited"))

    payload = operator.diagnose_engineering_work_item(item.work_id, operator_root=root, runner=CodexProbeRunner())

    assert payload["latest_codex_attempt"]["path"].endswith("codex-attempt-1.json")
    assert payload["latest_codex_attempt"]["failure_classification"] == "CODEX_PROCESS_FAILURE"


def test_production_runner_paths_are_not_touched_by_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    production_marker = tmp_path / "production-runner/marker.txt"
    production_marker.parent.mkdir()
    production_marker.write_text("stable", encoding="utf-8")
    before = production_marker.read_text(encoding="utf-8")
    monkeypatch.setattr(operator.shutil, "which", lambda _name: "codex.cmd")

    operator.diagnose_engineering_work_item(item.work_id, operator_root=root, runner=CodexProbeRunner())

    assert production_marker.read_text(encoding="utf-8") == before
