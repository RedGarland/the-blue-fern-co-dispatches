from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus, RecoveryPlan


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def approved_engineering_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(operator, "APPROVED_ENGINEERING_WORKTREE_ROOT", tmp_path / "OperatorWorktrees")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _policy(repo: Path, *, rebuild_mode: str = "recommend", engineer_mode: str = "automatic_prepare_pr") -> None:
    (repo / "ops/operator").mkdir(parents=True, exist_ok=True)
    (repo / "ops/operator/remediation-policy.yaml").write_text(
        "\n".join(
            [
                f"REBUILD_STATUS:\n  mode: {rebuild_mode}",
                "REPLAY_COLLECTION:\n  mode: forbidden",
                "PUBLISH_NO_UPDATE:\n  mode: forbidden",
                f"ENGINEER_PREPARE_FIX:\n  mode: {engineer_mode}",
                "MERGE_PR:\n  mode: approval_required",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _config(runner: Path, dispatch: str = "care-line") -> operator.OperatorConfig:
    return operator.OperatorConfig(
        status_freshness_threshold_minutes=120,
        dispatches={
            name: operator.DispatchConfig(name, runner / name, enabled=name == dispatch)
            for name in operator.DISPATCH_ORDER
        },
    )


def _export(repo: Path, dispatch: str, date: str, exported_at: str) -> None:
    _write_json(
        repo / "ops/status" / dispatch / "latest.json",
        {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": dispatch,
            "observed_date": date,
            "last_exported_at": exported_at,
            "aggregate_status": "SUCCESS",
        },
    )


def _status(
    *,
    dispatch: str = "care-line",
    date: str = "2026-09-20",
    state: str = "FAILED",
    next_action: str = "INVESTIGATE_COLLECTION",
    evidence: list[str] | None = None,
) -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date=date,
        state=state,
        collection="FAILED" if state == "FAILED" else "COMPLETE",
        editorial="COMPLETE",
        publication="COMPLETE",
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery="NEEDS_OPERATOR" if state not in {"COMPLETE", "SAFE_NO_OP"} else "HEALTHY",
        next_action=next_action,
        evidence=evidence or [f"status/operational-health/{dispatch}/{date}/proof.json"],
    )


def _plan(status: DispatchStatus) -> RecoveryPlan:
    actions = {
        "FAILED": ("PLAN_AVAILABLE", "INVESTIGATE_COLLECTION"),
        "DEGRADED": ("OPERATOR_REVIEW_REQUIRED", "INVESTIGATE_FAILED_SOURCES"),
        "MISSED": ("PLAN_AVAILABLE", "REPLAY_COLLECTION"),
        "NEEDS_REVIEW": ("OPERATOR_REVIEW_REQUIRED", "REVIEW_CANDIDATES"),
        "UNKNOWN": ("UNKNOWN", "INVESTIGATE_STATUS_EXPORT"),
    }
    disposition, action = actions.get(status.state, ("NO_ACTION", "NONE"))
    if status.next_action == "VERIFY_PUBLIC_STATE":
        disposition, action = "OPERATOR_REVIEW_REQUIRED", "VERIFY_PUBLIC_STATE"
    return RecoveryPlan(
        dispatch=status.dispatch,
        date=status.date,
        status_state=status.state,
        disposition=disposition,
        action=action,
        safe_to_apply=False,
        requires_operator_confirmation=action != "NONE",
        public_side_effects=False,
        scheduler_changes=False,
        collection_rerun=False,
        reason="v05 test plan",
        evidence=[f"plan/{status.dispatch}/{status.state}.json"],
    )


def _patch_status(monkeypatch: pytest.MonkeyPatch, status: DispatchStatus) -> None:
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(operator, "build_recovery_plan_from_status", lambda _status: _plan(_status))


def _run(repo: Path, runner: Path, monkeypatch: pytest.MonkeyPatch, status: DispatchStatus, *, fresh: bool = False) -> operator.OperatorResult:
    _policy(repo)
    exported_at = "2026-09-20T11:30:00Z" if fresh else "2026-09-11T18:07:41Z"
    _export(repo, status.dispatch, "2026-09-20" if fresh else "2026-09-11", exported_at)
    _patch_status(monkeypatch, status)
    return operator.check_operator(
        repo_root=repo,
        operator_root=repo / "ops/operator",
        config=_config(runner, status.dispatch),
        now=NOW,
    )


def _by_class(result: operator.OperatorResult) -> dict[str, operator.Incident]:
    return {incident.classification: incident for incident in result.incidents}


@pytest.mark.parametrize(
    ("state", "next_action", "classification", "recovery_action", "recommended"),
    [
        ("FAILED", "INVESTIGATE_COLLECTION", "FAILED_RUN", "INVESTIGATE_COLLECTION", "INVESTIGATE_COLLECTION"),
        ("DEGRADED", "INVESTIGATE_FAILED_SOURCES", "DEGRADED_RUN", "INVESTIGATE_FAILED_SOURCES", "INVESTIGATE_FAILED_SOURCES"),
        ("MISSED", "RECOVER_MISSING_RUN", "MISSED_RUN", "REPLAY_COLLECTION", "INVESTIGATE_STATUS_EXPORT"),
        ("NEEDS_REVIEW", "REVIEW_CANDIDATES", "NEEDS_REVIEW", "REVIEW_CANDIDATES", "REVIEW_CANDIDATES"),
        ("UNKNOWN", "INVESTIGATE_STATUS_EXPORT", "UNKNOWN_OPERATIONAL_STATE", "INVESTIGATE_STATUS_EXPORT", "INVESTIGATE_STATUS_EXPORT"),
    ],
)
def test_stale_export_creates_observability_and_underlying_incidents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: str,
    next_action: str,
    classification: str,
    recovery_action: str,
    recommended: str,
) -> None:
    status = _status(state=state, next_action=next_action)

    result = _run(tmp_path / "repo", tmp_path / "runner", monkeypatch, status)
    incidents = _by_class(result)

    assert set(incidents) == {"STALE_OBSERVABILITY", classification}
    assert incidents["STALE_OBSERVABILITY"].recovery_action == "INVESTIGATE_STATUS_EXPORT"
    assert incidents["STALE_OBSERVABILITY"].recommended_action == "INVESTIGATE_STATUS_EXPORT"
    assert incidents[classification].recovery_action == recovery_action
    assert incidents[classification].recommended_action == recommended
    assert incidents["STALE_OBSERVABILITY"].incident_id != incidents[classification].incident_id


def test_stale_export_with_local_complete_creates_stale_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    status = _status(state="COMPLETE", next_action="NONE")

    result = _run(tmp_path / "repo", tmp_path / "runner", monkeypatch, status)

    assert [incident.classification for incident in result.incidents] == ["STALE_OBSERVABILITY"]


def test_fresh_export_with_failed_local_status_creates_failed_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    status = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")

    result = _run(tmp_path / "repo", tmp_path / "runner", monkeypatch, status, fresh=True)

    assert [incident.classification for incident in result.incidents] == ["FAILED_RUN"]


def test_stale_resolves_while_failure_remains_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    failed = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    first = _run(repo, runner, monkeypatch, failed)

    second = _run(repo, runner, monkeypatch, failed, fresh=True)
    incidents = _by_class(second)

    assert _by_class(first)["FAILED_RUN"].incident_id == incidents["FAILED_RUN"].incident_id
    assert incidents["FAILED_RUN"].state == "OPEN"
    assert incidents["STALE_OBSERVABILITY"].state == "RECOVERED"


def test_failure_resolves_while_stale_remains_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    first = _run(repo, runner, monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    second = _run(repo, runner, monkeypatch, _status(state="COMPLETE", next_action="NONE"))
    incidents = _by_class(second)

    assert incidents["STALE_OBSERVABILITY"].incident_id == _by_class(first)["STALE_OBSERVABILITY"].incident_id
    assert incidents["STALE_OBSERVABILITY"].state == "OPEN"
    assert incidents["FAILED_RUN"].state == "RECOVERED"


def test_both_observability_and_failure_resolve_independently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _run(repo, runner, monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    result = _run(repo, runner, monkeypatch, _status(state="COMPLETE", next_action="NONE"), fresh=True)

    assert {incident.classification: incident.state for incident in result.incidents} == {
        "FAILED_RUN": "RECOVERED",
        "STALE_OBSERVABILITY": "RECOVERED",
    }


def test_repeated_supervisor_checks_reuse_stable_incident_ids(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    status = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")

    first = _run(repo, runner, monkeypatch, status)
    second = _run(repo, runner, monkeypatch, status)

    assert {i.classification: i.incident_id for i in first.incidents} == {i.classification: i.incident_id for i in second.incidents}
    assert len([i for i in second.incidents if i.state == "OPEN"]) == 2


def test_notification_suppresses_stale_when_underlying_higher_severity_is_new(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _run(tmp_path / "repo", tmp_path / "runner", monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    assert result.notification.notification_required is True
    assert result.notification.incident_ids == [_by_class(result)["FAILED_RUN"].incident_id]
    assert result.notification.summary[0]["classification"] == "FAILED_RUN"
    assert result.notification.summary[0]["observability_context"]["classification"] == "STALE_OBSERVABILITY"


def test_underlying_failed_run_becomes_engineer_mode_eligible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    result = _run(repo, tmp_path / "runner", monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    items = operator.prepare_engineering_work(
        result,
        operator_root=repo / "ops/operator",
        worktree_root=operator.APPROVED_ENGINEERING_WORKTREE_ROOT,
        policy=operator.RemediationPolicy({"ENGINEER_PREPARE_FIX": "automatic_prepare_pr", "MERGE_PR": "approval_required"}),
        base_sha="base",
    )

    assert [item.classification for item in items] == ["FAILED_RUN"]


def test_degraded_run_remains_engineer_mode_ineligible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    result = _run(repo, tmp_path / "runner", monkeypatch, _status(state="DEGRADED", next_action="INVESTIGATE_FAILED_SOURCES"))

    items = operator.prepare_engineering_work(
        result,
        operator_root=repo / "ops/operator",
        worktree_root=operator.APPROVED_ENGINEERING_WORKTREE_ROOT,
        policy=operator.RemediationPolicy({"ENGINEER_PREPARE_FIX": "automatic_prepare_pr", "MERGE_PR": "approval_required"}),
        base_sha="base",
    )

    assert items == []


def test_engineer_work_item_dedupe_remains_intact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    result = _run(repo, tmp_path / "runner", monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))
    policy = operator.RemediationPolicy({"ENGINEER_PREPARE_FIX": "automatic_prepare_pr", "MERGE_PR": "approval_required"})

    first = operator.prepare_engineering_work(result, operator_root=repo / "ops/operator", worktree_root=operator.APPROVED_ENGINEERING_WORKTREE_ROOT, policy=policy)
    second = operator.prepare_engineering_work(result, operator_root=repo / "ops/operator", worktree_root=operator.APPROVED_ENGINEERING_WORKTREE_ROOT, policy=policy)

    assert first[0].work_id == second[0].work_id
    assert len(list((repo / "ops/operator/engineering/active").glob("*/work-item.json"))) == 1


def test_rebuild_status_automatic_policy_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, rebuild_mode="automatic")
    _export(repo, "care-line", "2026-09-11", "2026-09-11T18:07:41Z")
    status = _status(state="COMPLETE", next_action="REBUILD_STATUS")
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(
        operator,
        "build_recovery_plan_from_status",
        lambda _status: RecoveryPlan(
            dispatch=_status.dispatch,
            date=_status.date,
            status_state=_status.state,
            disposition="PLAN_AVAILABLE",
            action="REBUILD_STATUS",
            safe_to_apply=True,
            requires_operator_confirmation=False,
            public_side_effects=False,
            scheduler_changes=False,
            collection_rerun=False,
            reason="status export rebuild",
            evidence=["status export stale"],
        ),
    )
    monkeypatch.setattr(operator, "apply_recovery_plan", lambda *_args, **_kwargs: pytest.fail("no apply during read-only test"))

    result = operator.check_operator(
        repo_root=repo,
        operator_root=repo / "ops/operator",
        config=_config(runner, "care-line"),
        now=NOW,
        write_ledger=False,
        allow_automatic_remediation=False,
    )

    incident = _by_class(result)["STALE_OBSERVABILITY"]
    assert incident.recovery_action == "REBUILD_STATUS"
    assert incident.remediation["reason"] == "automatic remediation disabled for this run"


def test_production_roots_remain_read_only_during_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    marker = runner / "care-line" / "existing.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("stable", encoding="utf-8")
    before = {
        path.relative_to(runner).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in runner.rglob("*")
        if path.is_file()
    }

    _run(repo, runner, monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    after = {
        path.relative_to(runner).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in runner.rglob("*")
        if path.is_file()
    }
    assert after == before
