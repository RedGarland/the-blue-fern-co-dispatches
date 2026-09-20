from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus, RecoveryPlan


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _policy(repo: Path, enabled: bool = True) -> operator.RemediationPolicy:
    modes = {"ENGINEER_PREPARE_FIX": "automatic_prepare_pr" if enabled else "recommend", "MERGE_PR": "approval_required"}
    return operator.RemediationPolicy(modes=modes)


def _incident(
    *,
    incident_id: str = "bfo-test-failed",
    dispatch: str = "care-line",
    classification: str = "FAILED_RUN",
    state: str = "OPEN",
    status_state: str = "FAILED",
    recovery_action: str = "INVESTIGATE_COLLECTION",
    recovery_disposition: str = "PLAN_AVAILABLE",
    evidence: list[str] | None = None,
) -> operator.Incident:
    return operator.Incident(
        incident_id=incident_id,
        incident_key=f"{dispatch}|{classification}|2026-09-20|care_line_collection",
        dispatch=dispatch,
        detected_at="2026-09-20T12:00:00Z",
        updated_at="2026-09-20T12:00:00Z",
        state=state,
        classification=classification,
        status_state=status_state,
        recovery_disposition=recovery_disposition,
        recovery_action=recovery_action,
        evidence=evidence or ["status/operational-health/care-line/2026-09-20/runs/failure.json", "Traceback: runner defect"],
        recommended_action=recovery_action,
        affected_date="2026-09-20",
    )


def _result(*incidents: operator.Incident) -> operator.OperatorResult:
    return operator.OperatorResult(checked_at="2026-09-20T12:00:00Z", dispatches=[], incidents=list(incidents))


def test_eligible_failed_run_creates_work_item(tmp_path: Path) -> None:
    items = operator.prepare_engineering_work(
        _result(_incident()),
        operator_root=tmp_path / "ops/operator",
        worktree_root=tmp_path / "OperatorWorktrees",
        policy=_policy(tmp_path),
        base_sha="base-sha",
    )

    assert len(items) == 1
    item = items[0]
    assert item.schema_version == "blue_fern_operator_engineering_v1"
    assert item.state == "DETECTED"
    assert item.branch == "operator/care-line/bfo-test-failed"
    assert item.worktree.startswith(str(tmp_path / "OperatorWorktrees"))
    assert "src/bluefern_dispatches/care_line_*.py" in item.allowed_paths
    assert item.merge_allowed is False
    assert (tmp_path / "ops/operator/engineering/active" / item.work_id / "diagnosis.json").is_file()


def test_safe_rebuild_status_does_not_create_engineering_work(tmp_path: Path) -> None:
    incident = _incident(classification="STATUS_EXPORT_PROBLEM", recovery_action="REBUILD_STATUS")

    items = operator.prepare_engineering_work(_result(incident), operator_root=tmp_path / "ops/operator", worktree_root=tmp_path / "wt", policy=_policy(tmp_path))

    assert items == []


@pytest.mark.parametrize(
    ("classification", "status_state", "action"),
    [
        ("DEGRADED_RUN", "DEGRADED", "INVESTIGATE_FAILED_SOURCES"),
        ("NEEDS_REVIEW", "NEEDS_REVIEW", "REVIEW_CANDIDATES"),
        ("UNKNOWN_OPERATIONAL_STATE", "UNKNOWN", "INVESTIGATE_STATUS_EXPORT"),
        ("PUBLIC_STATE_UNVERIFIED", "COMPLETE", "VERIFY_PUBLIC_STATE"),
    ],
)
def test_non_engineering_incident_classes_are_not_eligible(tmp_path: Path, classification: str, status_state: str, action: str) -> None:
    incident = _incident(classification=classification, status_state=status_state, recovery_action=action)

    items = operator.prepare_engineering_work(_result(incident), operator_root=tmp_path / "ops/operator", worktree_root=tmp_path / "wt", policy=_policy(tmp_path))

    assert items == []


def test_duplicate_incident_reuses_same_work_item(tmp_path: Path) -> None:
    root = tmp_path / "ops/operator"
    worktree_root = tmp_path / "OperatorWorktrees"
    incident = _incident()

    first = operator.prepare_engineering_work(_result(incident), operator_root=root, worktree_root=worktree_root, policy=_policy(tmp_path))
    second = operator.prepare_engineering_work(_result(incident), operator_root=root, worktree_root=worktree_root, policy=_policy(tmp_path))

    assert first[0].work_id == second[0].work_id
    assert len(list((root / "engineering/active").glob("*/work-item.json"))) == 1


def test_work_id_and_branch_are_deterministic() -> None:
    assert operator._work_id("bfo-test-failed") == operator._work_id("bfo-test-failed")
    assert operator._work_id("bfo-test-failed") != operator._work_id("bfo-test-failed", repair_generation=2)
    assert operator._engineering_branch("care-line", "bfo-test-failed") == "operator/care-line/bfo-test-failed"


def test_forbidden_production_worktree_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Refusing forbidden engineering worktree"):
        operator._build_engineering_work_item(
            _incident(),
            operator_root=tmp_path / "ops/operator",
            worktree_root=Path(r"C:\BlueFernRunner\CareLineNationalCurrent8"),
            base_sha="base",
        )


def test_validation_failure_retry_then_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ops/operator"
    item = operator.prepare_engineering_work(_result(_incident()), operator_root=root, worktree_root=tmp_path / "wt", policy=_policy(tmp_path))[0]

    retry = operator.update_engineering_validation(root, item.work_id, validation_passed=False, repair_attempts=1)
    blocked = operator.update_engineering_validation(root, item.work_id, validation_passed=False, repair_attempts=2)

    assert retry.state == "VALIDATING"
    assert blocked.state == "BLOCKED"
    assert blocked.merge_allowed is False


def test_passing_validation_can_mark_pr_ready_or_open_without_merge(tmp_path: Path) -> None:
    root = tmp_path / "ops/operator"
    item = operator.prepare_engineering_work(_result(_incident()), operator_root=root, worktree_root=tmp_path / "wt", policy=_policy(tmp_path))[0]

    ready = operator.update_engineering_validation(root, item.work_id, validation_passed=True, repair_attempts=1)
    opened = operator.update_engineering_validation(root, item.work_id, validation_passed=True, repair_attempts=1, pr_number=431, head_sha="head")

    assert ready.state == "PR_READY"
    assert opened.state == "PR_OPEN"
    assert opened.pr_number == 431
    assert opened.pr_state == "OPEN"
    assert opened.merge_allowed is False


def test_engineering_policy_must_explicitly_allow_prepare_pr(tmp_path: Path) -> None:
    items = operator.prepare_engineering_work(_result(_incident()), operator_root=tmp_path / "ops/operator", worktree_root=tmp_path / "wt", policy=_policy(tmp_path, enabled=False))

    assert items == []


def test_source_runner_pages_scheduler_remain_outside_allowed_scope(tmp_path: Path) -> None:
    item = operator.prepare_engineering_work(_result(_incident()), operator_root=tmp_path / "ops/operator", worktree_root=tmp_path / "wt", policy=_policy(tmp_path))[0]
    joined = "\n".join(item.allowed_paths)

    assert "bluefern-dispatches-pages" not in joined
    assert "ops/operator" not in joined
    assert "task.xml" not in joined
    assert "review/current" not in joined


def test_engineer_command_outputs_work_item_without_llm_or_pr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    root = tmp_path / "ops/operator"
    (root / "config.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "remediation-policy.yaml").write_text("ENGINEER_PREPARE_FIX:\n  mode: automatic_prepare_pr\n", encoding="utf-8")
    incident = _incident()
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: _result(incident))

    rc = operator.main(["engineer", "--json", "--operator-root", str(root), "--worktree-root", str(tmp_path / "wt")])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["work_item_count"] == 1
    assert payload["work_items"][0]["pr_number"] is None


def test_existing_v03_notification_remains_unchanged(tmp_path: Path) -> None:
    previous = _incident()
    current = _incident()
    previous_payload = previous.to_payload()

    event = operator._build_notification_event(_result(current), {current.incident_id: previous_payload}, _policy(tmp_path))

    assert event.notification_required is False


def test_no_llm_required_for_eligibility_decision(tmp_path: Path) -> None:
    source = Path("scripts/blue_fern_operator.py").read_text(encoding="utf-8").lower()

    assert "openai" not in source
    assert "llm" not in source
