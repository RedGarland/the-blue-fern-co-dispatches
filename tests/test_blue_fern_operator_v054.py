from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator


def _item(tmp_path: Path, *, root_cause: str | None = None) -> tuple[Path, operator.EngineeringWorkItem]:
    root = tmp_path / "ops/operator"
    worktree = tmp_path / "worktree"
    (worktree / "tests").mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../repo/.git/worktrees/test\n", encoding="utf-8")
    item = operator.EngineeringWorkItem(
        work_id="bfoe-test",
        incident_id="bfo-test",
        dispatch="care-line",
        classification="FAILED_RUN",
        state="PATCHED",
        branch="operator/care-line/bfo-test",
        worktree=str(worktree),
        evidence=["status/operational-health/care-line/proof.json"],
        allowed_paths=["src/bluefern_dispatches/care_line_*.py", "tests/test_care_line*.py"],
        tests_required=[
            "python -m py_compile scripts/blue_fern_operator.py scripts/dispatch_ops.py",
            "python -m pytest tests/test_care_line*.py -q",
        ],
        root_cause=root_cause,
    )
    operator._save_engineering_work_item(root, item)
    return root, item


def _clean_existing_worktree_runner(item: operator.EngineeringWorkItem, repo_root: Path) -> object:
    worktree = Path(item.worktree)

    def runner(args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        if args == ["git", "fetch", "origin"]:
            return operator.EngineeringCommandResult(0)
        if args == ["git", "rev-parse", "origin/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0, "base-sha\n")
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return operator.EngineeringCommandResult(0, f"{worktree}\n")
        if args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return operator.EngineeringCommandResult(0, f"{item.branch}\n")
        if args == ["git", "rev-parse", "--git-common-dir"] and cwd == repo_root:
            return operator.EngineeringCommandResult(0, ".git\n")
        if args == ["git", "rev-parse", "--git-common-dir"] and cwd == worktree:
            return operator.EngineeringCommandResult(0, "..\\repo\\.git\n")
        if args == ["git", "merge-base", "--is-ancestor", "base-sha", "HEAD"]:
            return operator.EngineeringCommandResult(0)
        if args == ["git", "diff", "--name-only"]:
            return operator.EngineeringCommandResult(0, "")
        return operator.EngineeringCommandResult(0, "")

    return runner


def test_test_wildcard_resolves_to_explicit_sorted_files(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    (worktree / "tests").mkdir(parents=True)
    for name in ["test_care_line_z.py", "test_care_line_a.py"]:
        (worktree / "tests" / name).write_text("# test\n", encoding="utf-8")

    invocation = operator._validation_invocation("python -m pytest tests/test_care_line*.py -q", worktree=worktree)

    assert invocation["resolved_paths"] == ["tests/test_care_line_a.py", "tests/test_care_line_z.py"]
    assert invocation["command_argv"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_care_line_a.py",
        "tests/test_care_line_z.py",
        "-q",
    ]


def test_no_shell_wildcard_expansion_is_used(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    (Path(item.worktree) / "tests/test_care_line_collection.py").write_text("# test\n", encoding="utf-8")
    commands: list[list[str]] = []

    def runner(args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        commands.append(args)
        return operator.EngineeringCommandResult(0)

    operator._run_engineering_validation(root, item, runner=runner)

    assert not any(command[:3] == ["powershell.exe", "-NoProfile", "-Command"] for command in commands)
    assert any(command[:3] == ["python", "-m", "pytest"] for command in commands)
    assert not any("*" in part for command in commands for part in command)


def test_zero_matches_returns_validation_target_not_found(tmp_path: Path) -> None:
    root, item = _item(tmp_path)

    updated, passed = operator._run_engineering_validation(root, item, runner=lambda *_args, **_kwargs: operator.EngineeringCommandResult(0))

    assert passed is False
    assert updated.validation_results[-1]["outcome"] == "VALIDATION_TARGET_NOT_FOUND"
    assert updated.validation_results[-1]["requested_pattern"] == "tests/test_care_line*.py"
    assert updated.repair_attempts == 0


@pytest.mark.parametrize("target", ["tests/../secrets.py", "../tests/test_care_line.py"])
def test_traversal_validation_target_refused(tmp_path: Path, target: str) -> None:
    invocation = operator._validation_invocation(f"python -m pytest {target} -q", worktree=tmp_path)

    assert invocation["outcome"] == "VALIDATION_TARGET_UNSAFE"


def test_resolved_validation_paths_recorded(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    (Path(item.worktree) / "tests/test_care_line_collection.py").write_text("# test\n", encoding="utf-8")

    updated, passed = operator._run_engineering_validation(root, item, runner=lambda *_args, **_kwargs: operator.EngineeringCommandResult(0))

    assert passed is True
    pytest_row = updated.validation_results[1]
    assert pytest_row["resolved_paths"] == ["tests/test_care_line_collection.py"]
    assert pytest_row["command_argv"] == ["python", "-m", "pytest", "tests/test_care_line_collection.py", "-q"]
    assert pytest_row["outcome"] == "PASSED"


def test_validation_applies_recorded_pythonpath_env(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    item = operator.replace(item, tests_required=["$env:PYTHONPATH='src'; python scripts/doctor.py"])
    seen_env: list[dict[str, str] | None] = []

    def runner(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> operator.EngineeringCommandResult:
        seen_env.append(env)
        return operator.EngineeringCommandResult(0)

    updated, passed = operator._run_engineering_validation(root, item, runner=runner)

    assert passed is True
    assert seen_env == [{"PYTHONPATH": "src"}]
    assert updated.validation_results[0]["env"] == {"PYTHONPATH": "src"}


def test_diagnosis_packet_includes_bounded_redacted_evidence(tmp_path: Path) -> None:
    evidence_root = tmp_path / "runner"
    evidence_path = evidence_root / "status/operational-health/care-line/proof.json"
    receipt_path = evidence_root / "ops/status/care-line/history/task-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "task_key": "care_line_collection",
                "status": "FAILED",
                "exit_code": 1,
                "failure_stage": "verify_checkout",
                "details": {"failure_stage": "verify_checkout"},
                "error": "checkout mismatch",
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "task_key": "care_line_collection",
        "status": "FAILED",
        "exit_code": 1,
        "failure_stage": "verify_checkout",
        "details": {"failure_stage": "verify_checkout"},
        "artifact_refs": {
            "task_receipt": str(receipt_path),
            "outside": str(tmp_path / "outside.json"),
        },
        "secret": "token=SECRET",
        "long": "x" * 9000,
    }
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    incident = operator.Incident(
        incident_id="bfo-test",
        incident_key="care-line|FAILED_RUN|2026-09-20|care_line_collection",
        dispatch="care-line",
        detected_at="2026-09-20T00:00:00Z",
        state="OPEN",
        classification="FAILED_RUN",
        status_state="FAILED",
        recovery_disposition="PLAN_AVAILABLE",
        recovery_action="INVESTIGATE_COLLECTION",
        evidence=["status/operational-health/care-line/proof.json"],
        recommended_action="INVESTIGATE_COLLECTION",
        affected_date="2026-09-20",
    )
    item = operator.EngineeringWorkItem(
        work_id="bfoe-test",
        incident_id="bfo-test",
        dispatch="care-line",
        classification="FAILED_RUN",
        state="DETECTED",
        branch="operator/care-line/bfo-test",
        worktree=str(tmp_path / "worktree"),
        evidence=incident.evidence,
        allowed_paths=["src/bluefern_dispatches/care_line_*.py"],
        tests_required=["python -m pytest tests/test_care_line*.py -q"],
    )

    packet = operator._build_diagnosis_packet(incident, item, eligibility_reason="eligible", evidence_root=evidence_root)

    evidence = packet["evidence"][0]
    assert evidence["summary"]["task_key"] == "care_line_collection"
    assert evidence["summary"]["details"]["failure_stage"] == "verify_checkout"
    assert evidence["sha256"]
    assert len(evidence["excerpt"].encode("utf-8")) <= operator.DIAGNOSIS_EVIDENCE_EXCERPT_BYTES * 2
    assert "SECRET" not in evidence["excerpt"]
    assert [entry["type"] for entry in packet["evidence"]] == ["json", "artifact_ref:task_receipt"]
    assert packet["evidence"][1]["summary"]["failure_stage"] == "verify_checkout"
    assert "outside.json" not in json.dumps(packet)
    assert packet["failures"][0]["status"] == "FAILED"


def test_codex_insufficient_evidence_no_patch_blocks_without_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator, "APPROVED_ENGINEERING_WORKTREE_ROOT", tmp_path)
    root, item = _item(tmp_path)
    item = operator.replace(item, state="WORKTREE_CREATED")
    operator._save_engineering_work_item(root, item)

    blocked = operator.execute_engineering_work_item(
        item,
        operator_root=root,
        repo_root=tmp_path / "repo",
        runner=_clean_existing_worktree_runner(item, tmp_path / "repo"),
        codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(0, "Root cause: INSUFFICIENT_EVIDENCE\n"),
    )

    assert blocked.state == "BLOCKED"
    assert blocked.blocked_reason == "insufficient engineering evidence"
    assert blocked.validation_results == []
    assert blocked.repair_attempts == 0


def test_codex_root_cause_no_patch_blocks_deterministically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator, "APPROVED_ENGINEERING_WORKTREE_ROOT", tmp_path)
    root, item = _item(tmp_path)
    item = operator.replace(item, state="WORKTREE_CREATED")
    operator._save_engineering_work_item(root, item)

    blocked = operator.execute_engineering_work_item(
        item,
        operator_root=root,
        repo_root=tmp_path / "repo",
        runner=_clean_existing_worktree_runner(item, tmp_path / "repo"),
        codex_runner=lambda **_kwargs: operator.EngineeringCommandResult(0, "Root cause: definite bug\n"),
    )

    assert blocked.blocked_reason == "codex produced no patch"
    assert blocked.validation_results == []


def test_validation_runs_after_actual_in_scope_patch(tmp_path: Path) -> None:
    root, item = _item(tmp_path)
    (Path(item.worktree) / "tests/test_care_line_collection.py").write_text("# test\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        calls.append(args)
        return operator.EngineeringCommandResult(0, "src/bluefern_dispatches/care_line_fix.py\n" if args[:3] == ["git", "diff", "--name-only"] else "")

    updated, passed = operator._run_engineering_validation(root, item, runner=runner)

    assert passed is True
    assert any(command[:3] == ["python", "-m", "pytest"] for command in calls)
