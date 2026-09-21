from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator
from tests.test_blue_fern_operator_v04 import _incident, _policy, _result


@pytest.fixture(autouse=True)
def approved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(operator, "APPROVED_ENGINEERING_WORKTREE_ROOT", tmp_path / "OperatorWorktrees")


class CloseRunner:
    def __init__(self, *, dirty: bool = False, remote_branch: bool = False, open_pr: bool = False) -> None:
        self.dirty = dirty
        self.remote_branch = remote_branch
        self.open_pr = open_pr
        self.commands: list[tuple[list[str], Path]] = []

    def __call__(self, args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        self.commands.append((args, cwd))
        if args == ["git", "status", "--short"]:
            return operator.EngineeringCommandResult(0, " M src/bluefern_dispatches/care_line_bug.py\n" if self.dirty else "")
        if args[:4] == ["git", "ls-remote", "--heads", "origin"]:
            return operator.EngineeringCommandResult(0, "abc\trefs/heads/operator/care-line/bfo-test\n" if self.remote_branch else "")
        if args[:3] == ["gh", "pr", "list"]:
            return operator.EngineeringCommandResult(0, '[{"number": 12, "state": "OPEN"}]\n' if self.open_pr else "[]\n")
        return operator.EngineeringCommandResult(0)


def _care_close_fixture(tmp_path: Path, **overrides: object) -> tuple[Path, operator.EngineeringWorkItem]:
    root = tmp_path / "ops/operator"
    worktree = operator.APPROVED_ENGINEERING_WORKTREE_ROOT / "bfoe-test"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: ../repo/.git/worktrees/bfoe-test\n", encoding="utf-8")
    item = operator.EngineeringWorkItem(
        work_id="bfoe-test",
        incident_id="bfo-test",
        dispatch="care-line",
        classification="FAILED_RUN",
        state="BLOCKED",
        branch="operator/care-line/bfo-test",
        worktree=str(worktree),
        evidence=["status/operational-health/care-line/2026-09-20/runs/care_line_collection.json"],
        allowed_paths=["src/bluefern_dispatches/care_line_*.py", "tests/test_care_line*.py"],
        tests_required=["python -m pytest tests/test_care_line*.py -q"],
        base_sha="base-sha",
        blocked_reason="validation failed after bounded repair attempts",
        root_cause="INSUFFICIENT_EVIDENCE",
        retry_authorization={"reason": "CODEX_RETRY", "prior_state": "BLOCKED"},
        validation_results=[{"command": "python -m pytest tests/test_care_line*.py -q", "exit_code": 1}],
        merge_allowed=False,
    )
    item = operator.replace(item, **overrides)
    active = root / "engineering" / "active" / item.work_id
    operator._save_engineering_work_item(root, item)
    diagnosis = {
        "schema_version": "blue_fern_operator_diagnosis_v2",
        "work_id": item.work_id,
        "incident_id": item.incident_id,
        "evidence": [
            {
                "summary": {
                    "task_key": "care_line_collection",
                    "status": "FAILED",
                    "failure_stage": "verify_checkout",
                    "exit_code": 1,
                }
            }
        ],
    }
    operator._write_json(active / "diagnosis.json", diagnosis)
    (active / "codex-prompt.txt").write_text("repair prompt\n", encoding="utf-8")
    for attempt in (1, 2, 3):
        operator._write_json(active / f"codex-attempt-{attempt}.json", {"attempt": attempt, "work_id": item.work_id})
    return root, item


def _close(root: Path, item: operator.EngineeringWorkItem, **kwargs: object) -> dict[str, object]:
    return operator.close_engineering_work_item(
        item.work_id,
        disposition=str(kwargs.pop("disposition", "NON_CODE_INCIDENT")),
        confirm=str(kwargs.pop("confirm", operator.ENGINEERING_CLOSE_CONFIRMATION)),
        reason=str(kwargs.pop("reason", "Care failure was traced to production checkout/rollout hygiene.")),
        facts=list(kwargs.pop("facts", ["affected task care_line_collection", "failure_stage verify_checkout"])),
        operator_root=root,
        runner=kwargs.pop("runner", CloseRunner()),
    )


def test_missing_confirmation_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item, confirm="")

    assert payload["accepted"] is False
    assert payload["reason"] == "confirmation token required"


def test_wrong_confirmation_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item, confirm="YES")

    assert payload["accepted"] is False


def test_unsupported_disposition_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item, disposition="LOCAL_NOTE")

    assert payload["accepted"] is False
    assert payload["reason"] == "unsupported disposition"


def test_pr_open_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path, state="PR_OPEN", pr_number=99)

    payload = _close(root, item)

    assert payload["accepted"] is False
    assert payload["reason"] == "work item has an open repair PR"


def test_pr_ready_refused_as_pending_patch(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path, state="PR_READY", head_sha="head-sha")

    payload = _close(root, item)

    assert payload["accepted"] is False
    assert payload["reason"] == "work item state is not closeable: PR_READY"


def test_merge_allowed_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path, merge_allowed=True)

    payload = _close(root, item)

    assert payload["accepted"] is False
    assert payload["reason"] == "merge_allowed must be false"


def test_dirty_worktree_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item, runner=CloseRunner(dirty=True))

    assert payload["accepted"] is False
    assert payload["reason"] == "worktree is not clean"


def test_remote_branch_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item, runner=CloseRunner(remote_branch=True))

    assert payload["accepted"] is False
    assert payload["reason"] == "remote repair branch already exists"


def test_open_pr_refused(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item, runner=CloseRunner(open_pr=True))

    assert payload["accepted"] is False
    assert payload["reason"] == "repair PR already exists"


def test_valid_blocked_non_code_item_closes_and_archives(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    payload = _close(root, item)

    assert payload["accepted"] is True
    assert payload["event"] == "ENGINEERING_WORK_CLOSED"
    assert payload["disposition"] == "NON_CODE_INCIDENT"
    closed = payload["work_item"]
    assert closed["state"] == "CLOSED"
    assert closed["merge_allowed"] is False
    assert closed["closure"]["disposition"] == "NON_CODE_INCIDENT"
    assert closed["closure"]["prior_state"] == "BLOCKED"
    assert closed["blocked_reason"] == "validation failed after bounded repair attempts"
    assert closed["retry_authorization"] == {"reason": "CODEX_RETRY", "prior_state": "BLOCKED"}
    assert closed["validation_results"] == [{"command": "python -m pytest tests/test_care_line*.py -q", "exit_code": 1}]
    assert not (root / "engineering/active" / item.work_id).exists()
    assert (root / "engineering/history" / item.work_id / "work-item.json").is_file()
    assert (root / "engineering/history" / item.work_id / "diagnosis.json").is_file()
    assert (root / "engineering/history" / item.work_id / "codex-prompt.txt").is_file()
    for attempt in (1, 2, 3):
        assert (root / "engineering/history" / item.work_id / f"codex-attempt-{attempt}.json").is_file()
    assert (root / "engineering/history" / item.work_id / "closure.json").is_file()
    assert list((root / "engineering/audit").glob("*/*-closed.json"))


def test_closure_metadata_records_care_supporting_facts(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)
    facts = [
        "affected task care_line_collection",
        "failure_stage verify_checkout",
        "original dirty tracked files operational_status_exporter.py and test_operational_status_exporter.py",
        "current files clean and identical to HEAD/upstream",
        "live Engineer discovery no longer returns original FAILED_RUN",
        "remediation path is operational rollout, not code repair",
    ]

    payload = _close(root, item, facts=facts)

    closure = payload["work_item"]["closure"]
    assert closure["supporting_facts"] == facts
    assert closure["related_incident_id"] == "bfo-test"
    assert closure["artifacts_preserved"]


def test_reason_and_fact_bounds_are_enforced(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)

    long_reason = _close(root, item, reason="x" * 1001)
    too_many = _close(root, item, facts=[f"fact {index}" for index in range(11)])
    secret = _close(root, item, facts=["token=SECRET"])

    assert long_reason["accepted"] is False
    assert too_many["accepted"] is False
    assert secret["accepted"] is False


def test_operator_lock_refuses_close(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)
    (root / ".lock").mkdir(parents=True)

    payload = _close(root, item)

    assert payload["accepted"] is False
    assert payload["reason"] == "Operator lock is active"


def test_duplicate_engineer_discovery_does_not_recreate_closed_work(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)
    _close(root, item)
    incident = _incident(incident_id=item.incident_id)

    items = operator.prepare_engineering_work(
        _result(incident),
        operator_root=root,
        worktree_root=operator.APPROVED_ENGINEERING_WORKTREE_ROOT,
        policy=_policy(tmp_path),
    )

    assert items == []
    assert not (root / "engineering/active" / item.work_id).exists()
    assert len(list((root / "engineering/history").glob("*/work-item.json"))) == 1


def test_genuinely_new_incident_can_create_new_work(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)
    _close(root, item)

    items = operator.prepare_engineering_work(
        _result(_incident(incident_id="bfo-new")),
        operator_root=root,
        worktree_root=operator.APPROVED_ENGINEERING_WORKTREE_ROOT,
        policy=_policy(tmp_path),
    )

    assert len(items) == 1
    assert items[0].incident_id == "bfo-new"
    assert items[0].state == "DETECTED"


def test_cli_engineer_close_outputs_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, item = _care_close_fixture(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_close(work_id: str, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schema_version": operator.ENGINEERING_CLOSE_SCHEMA_VERSION,
            "event": "ENGINEERING_WORK_CLOSED",
            "accepted": True,
            "work_id": work_id,
            "disposition": kwargs["disposition"],
            "work_item": operator.replace(item, state="CLOSED", closure={"disposition": kwargs["disposition"]}).to_payload(),
        }

    monkeypatch.setattr(operator, "close_engineering_work_item", fake_close)

    rc = operator.main(
        [
            "engineer-close",
            "--work-id",
            item.work_id,
            "--disposition",
            "NON_CODE_INCIDENT",
            "--confirm",
            "CLOSE_ENGINEERING_WORK",
            "--reason",
            "non-code runner hygiene",
            "--fact",
            "verify_checkout",
            "--operator-root",
            str(root),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["accepted"] is True
    assert payload["work_item"]["state"] == "CLOSED"
    assert calls[0]["confirm"] == "CLOSE_ENGINEERING_WORK"
    assert calls[0]["facts"] == ["verify_checkout"]


def test_no_production_path_mutation(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)
    production_marker = tmp_path / "CareLineNationalCurrent8" / "marker.txt"
    production_marker.parent.mkdir()
    production_marker.write_text("stable", encoding="utf-8")

    _close(root, item)

    assert production_marker.read_text(encoding="utf-8") == "stable"


def test_closure_creates_no_approval_notification(tmp_path: Path) -> None:
    root, item = _care_close_fixture(tmp_path)
    payload = _close(root, item)
    closed = operator.EngineeringWorkItem(**{key: payload["work_item"][key] for key in operator.EngineeringWorkItem.__dataclass_fields__ if key in payload["work_item"]})

    event = operator.build_engineering_approval_notification(closed)

    assert event.notification_required is False
