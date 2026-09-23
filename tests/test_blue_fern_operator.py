from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.dispatch_ops import ApplyResult, DispatchStatus, RecoveryPlan
from scripts import blue_fern_operator as operator


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _config(runner: Path, dispatch: str = "food-line") -> operator.OperatorConfig:
    dispatches = {
        name: operator.DispatchConfig(name, runner / name, enabled=name == dispatch)
        for name in operator.DISPATCH_ORDER
    }
    return operator.OperatorConfig(status_freshness_threshold_minutes=120, dispatches=dispatches)


def _policy(repo: Path, mode: str = "automatic") -> None:
    _write_json(repo / "ops/operator/placeholder.json", {"ok": True})
    (repo / "ops/operator/remediation-policy.yaml").write_text(
        f"REBUILD_STATUS:\n  mode: {mode}\nREPLAY_COLLECTION:\n  mode: forbidden\nPUBLISH_NO_UPDATE:\n  mode: forbidden\n",
        encoding="utf-8",
    )


def _export(repo: Path, dispatch: str, date: str, exported_at: str = "2026-09-20T11:30:00Z") -> None:
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
    dispatch: str = "food-line",
    date: str = "2026-09-20",
    state: str = "SAFE_NO_OP",
    next_action: str = "NONE",
    evidence: list[str] | None = None,
) -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date=date,
        state=state,
        collection="COMPLETE" if state != "MISSED" else "MISSING",
        editorial="COMPLETE",
        publication="COMPLETE",
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery="HEALTHY",
        next_action=next_action,
        evidence=evidence or ["status/operational-health/proof.json"],
    )


def _plan(status: DispatchStatus, action: str = "NONE", disposition: str = "NO_ACTION") -> RecoveryPlan:
    return RecoveryPlan(
        dispatch=status.dispatch,
        date=status.date,
        status_state=status.state,
        disposition=disposition,
        action=action,
        safe_to_apply=False,
        requires_operator_confirmation=False,
        public_side_effects=False,
        scheduler_changes=False,
        collection_rerun=False,
        reason="test plan",
        evidence=status.evidence,
    )


def _apply_result(
    status: DispatchStatus,
    *,
    outcome: str = "REBUILT",
    changed: bool = True,
    unexpected: list[str] | None = None,
) -> ApplyResult:
    return ApplyResult(
        dispatch=status.dispatch,
        date=status.date,
        planned_action="REBUILD_STATUS",
        outcome=outcome,
        changed=changed,
        public_side_effects=False,
        scheduler_changes=False,
        collection_rerun=False,
        editorial_mutation=False,
        publication_attempted=False,
        status_artifacts_changed=[
            f"ops/status/{status.dispatch}/history/{status.date}.json",
            f"ops/status/{status.dispatch}/latest.json",
        ]
        if changed
        else [],
        unexpected_changes=unexpected or [],
        evidence=status.evidence,
        status_before=status.to_json_payload(),
        status_after=status.to_json_payload(),
    )


def _patch_status(monkeypatch, status: DispatchStatus, plan: RecoveryPlan | None = None) -> None:
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(operator, "build_recovery_plan_from_status", lambda _status: plan or _plan(status))


def _run(repo: Path, runner: Path, dispatch: str = "food-line") -> operator.OperatorResult:
    return operator.check_operator(
        repo_root=repo,
        operator_root=repo / "ops/operator",
        config=_config(runner, dispatch),
        now=NOW,
    )


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), os.stat(path).st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_fresh_healthy_status_has_no_incident(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    _patch_status(monkeypatch, _status())

    result = _run(repo, runner)

    assert result.incidents == []
    assert result.dispatches[0].state == "NO_ACTION"


def test_terminal_food_degraded_status_has_no_failed_sources_incident(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="DEGRADED", next_action="NONE")
    _patch_status(monkeypatch, status, _plan(status, action="NONE", disposition="NO_ACTION"))

    result = _run(repo, runner)

    assert result.incidents == []
    assert result.dispatches[0].state == "NO_ACTION"


def test_food_missing_resume_without_durable_source_watch_is_not_no_action(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="MISSED", next_action="RECOVER_MISSING_RUN")
    _patch_status(
        monkeypatch,
        status,
        _plan(status, action="RECOVER_MISSING_RUN", disposition="PLAN_AVAILABLE"),
    )

    result = _run(repo, runner)

    assert result.dispatches[0].state != "NO_ACTION"
    assert result.incidents
    assert result.incidents[0].recommended_action == "RECOVER_MISSING_RUN"


def test_stale_exported_status_is_stale_observability(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    _patch_status(monkeypatch, _status(date="2026-09-19"))

    result = _run(repo, runner)

    assert result.incidents[0].classification == "STALE_OBSERVABILITY"
    assert result.dispatches[0].state == "STALE_OBSERVABILITY"


def test_stale_export_with_local_rebuild_plan_recommends_rebuild_status(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    status = _status(date="2026-09-20")
    _patch_status(monkeypatch, status, _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE"))

    result = _run(repo, runner)

    assert result.incidents[0].recommended_action == "REBUILD_STATUS"
    assert result.incidents[0].recovery_action == "REBUILD_STATUS"


def test_stale_status_with_automatic_policy_auto_applies_rebuild(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "automatic")
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    before = _status(date="2026-09-20", state="SAFE_NO_OP", next_action="INVESTIGATE_STATUS_EXPORT")
    after = _status(date="2026-09-20", state="SAFE_NO_OP", next_action="NONE")
    statuses = iter([before, after])
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr(
        operator,
        "build_recovery_plan_from_status",
        lambda status: _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE")
        if status.next_action == "INVESTIGATE_STATUS_EXPORT"
        else _plan(status),
    )
    calls: list[Path] = []

    def apply(dispatch, date, *, root, confirm):
        calls.append(root)
        return _apply_result(before)

    monkeypatch.setattr(operator, "apply_recovery_plan", apply)

    result = _run(repo, runner)

    assert calls == [runner / "food-line"]
    assert result.dispatches[0].notification_state == "AUTO_RECOVERED_FULLY"
    assert result.incidents[0].state == "RECOVERED"
    assert result.incidents[0].remediation["attempted"] is True
    assert result.incidents[0].remediation["outcome"] == "REBUILT"


def test_successful_rebuild_with_underlying_degraded_opens_underlying_incident(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "automatic")
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    before = _status(date="2026-09-20", state="SAFE_NO_OP", next_action="INVESTIGATE_STATUS_EXPORT")
    degraded = _status(date="2026-09-20", state="DEGRADED", next_action="INVESTIGATE_FAILED_SOURCES")
    statuses = iter([before, degraded])
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr(
        operator,
        "build_recovery_plan_from_status",
        lambda status: _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE")
        if status.next_action == "INVESTIGATE_STATUS_EXPORT"
        else _plan(status, action="INVESTIGATE_FAILED_SOURCES", disposition="OPERATOR_REVIEW_REQUIRED"),
    )
    monkeypatch.setattr(operator, "apply_recovery_plan", lambda *_args, **_kwargs: _apply_result(before))

    result = _run(repo, runner)

    by_class = {incident.classification: incident for incident in result.incidents}
    assert by_class["STALE_OBSERVABILITY"].state == "RECOVERED"
    assert by_class["DEGRADED_RUN"].state == "OPEN"
    assert by_class["DEGRADED_RUN"].recommended_action == "INVESTIGATE_FAILED_SOURCES"
    assert result.dispatches[0].notification_state == "AUTO_RECOVERED"


def test_second_run_does_not_rebuild_after_fresh_configured_status_export(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "automatic")
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    before = _status(date="2026-09-20", state="SAFE_NO_OP", next_action="INVESTIGATE_STATUS_EXPORT")
    after = _status(date="2026-09-20", state="SAFE_NO_OP", next_action="NONE")
    statuses = iter([before, after, after])
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr(
        operator,
        "build_recovery_plan_from_status",
        lambda status: _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE")
        if status.next_action == "INVESTIGATE_STATUS_EXPORT"
        else _plan(status),
    )
    calls = 0

    def apply(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        _export(repo, "food-line", "2026-09-20", "2026-09-20T11:30:00Z")
        return _apply_result(before)

    monkeypatch.setattr(operator, "apply_recovery_plan", apply)

    first = _run(repo, runner)
    second = _run(repo, runner)

    assert calls == 1
    assert first.dispatches[0].notification_state == "AUTO_RECOVERED_FULLY"
    assert second.dispatches[0].state == "NO_ACTION"


def test_recommend_policy_does_not_auto_apply(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "recommend")
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    status = _status(next_action="INVESTIGATE_STATUS_EXPORT")
    _patch_status(monkeypatch, status, _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE"))
    monkeypatch.setattr(operator, "apply_recovery_plan", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not apply")))

    result = _run(repo, runner)

    assert result.incidents[0].state == "OPEN"
    assert result.incidents[0].remediation["attempted"] is False
    assert result.incidents[0].recommended_action == "REBUILD_STATUS"


def test_forbidden_policy_does_not_auto_apply(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "forbidden")
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    status = _status(next_action="INVESTIGATE_STATUS_EXPORT")
    _patch_status(monkeypatch, status, _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE"))
    monkeypatch.setattr(operator, "apply_recovery_plan", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not apply")))

    result = _run(repo, runner)

    assert result.incidents[0].state == "OPEN"
    assert result.incidents[0].remediation["reason"] == "policy mode is forbidden"


def test_unexpected_rebuild_mutation_keeps_incident_open(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "automatic")
    _export(repo, "ice", "2026-09-11", "2026-09-11T18:07:41Z")
    before = _status(dispatch="ice", date="2026-09-19", state="COMPLETE", next_action="INVESTIGATE_STATUS_EXPORT")
    after = _status(dispatch="ice", date="2026-09-19", state="COMPLETE", next_action="INVESTIGATE_STATUS_EXPORT")
    statuses = iter([before, after])
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr(operator, "build_recovery_plan_from_status", lambda status: _plan(status, action="REBUILD_STATUS", disposition="PLAN_AVAILABLE"))
    monkeypatch.setattr(operator, "apply_recovery_plan", lambda *_args, **_kwargs: _apply_result(before, outcome="FAILED", unexpected=["data/unexpected.txt"]))

    result = _run(repo, runner, "ice")

    assert result.dispatches[0].notification_state == "REMEDIATION_FAILED"
    assert result.incidents[0].state == "OPEN"
    assert result.incidents[0].remediation["unexpected_changes"] == ["data/unexpected.txt"]


def test_care_degraded_classifies_failed_sources(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "care-line", "2026-09-20")
    status = _status(dispatch="care-line", state="DEGRADED", next_action="INVESTIGATE_FAILED_SOURCES")
    _patch_status(monkeypatch, status, _plan(status, action="INVESTIGATE_FAILED_SOURCES", disposition="OPERATOR_REVIEW_REQUIRED"))

    result = _run(repo, runner, "care-line")

    assert result.incidents[0].classification == "DEGRADED_RUN"
    assert result.incidents[0].recommended_action == "INVESTIGATE_FAILED_SOURCES"


def test_gaza_no_update_has_no_incident(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "gaza", "2026-09-20")
    _patch_status(monkeypatch, _status(dispatch="gaza", state="NO_UPDATE"))

    result = _run(repo, runner, "gaza")

    assert result.incidents == []


def test_ice_complete_has_no_incident(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "ice", "2026-09-20")
    _patch_status(monkeypatch, _status(dispatch="ice", state="COMPLETE"))

    result = _run(repo, runner, "ice")

    assert result.incidents == []


def test_failed_status_classifies_failed_run(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch_status(monkeypatch, status, _plan(status, action="INVESTIGATE_COLLECTION", disposition="PLAN_AVAILABLE"))

    result = _run(repo, runner)

    assert result.incidents[0].classification == "FAILED_RUN"


def test_missed_status_classifies_missed_run(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="MISSED", next_action="RECOVER_MISSING_RUN")
    _patch_status(monkeypatch, status, _plan(status, action="INVESTIGATE_STATUS_EXPORT", disposition="OPERATOR_REVIEW_REQUIRED"))

    result = _run(repo, runner)

    assert result.incidents[0].classification == "MISSED_RUN"


def test_forbidden_replay_collection_is_not_recommended(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="MISSED", next_action="RECOVER_MISSING_RUN")
    _patch_status(monkeypatch, status, _plan(status, action="REPLAY_COLLECTION", disposition="PLAN_AVAILABLE"))

    result = _run(repo, runner)

    assert result.incidents[0].recovery_action == "REPLAY_COLLECTION"
    assert result.incidents[0].recommended_action == "INVESTIGATE_STATUS_EXPORT"


def test_unknown_status_classifies_unknown_operational_state(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="UNKNOWN", next_action="UNKNOWN_REQUIRES_OPERATOR")
    _patch_status(monkeypatch, status, _plan(status, action="INVESTIGATE_STATUS_EXPORT", disposition="UNKNOWN"))

    result = _run(repo, runner)

    assert result.incidents[0].classification == "UNKNOWN_OPERATIONAL_STATE"


def test_duplicate_supervisor_runs_update_same_incident(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch_status(monkeypatch, status, _plan(status, action="INVESTIGATE_COLLECTION", disposition="PLAN_AVAILABLE"))

    first = _run(repo, runner)
    second = _run(repo, runner)

    assert first.incidents[0].incident_id == second.incidents[0].incident_id
    assert len(list((repo / "ops/operator/incidents").glob("*.json"))) == 1


def test_recovered_condition_moves_open_to_recovered(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    failed = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch_status(monkeypatch, failed, _plan(failed, action="INVESTIGATE_COLLECTION", disposition="PLAN_AVAILABLE"))
    first = _run(repo, runner)

    healthy = _status()
    _patch_status(monkeypatch, healthy)
    second = _run(repo, runner)

    assert first.incidents[0].state == "OPEN"
    assert any(incident.state == "RECOVERED" for incident in second.incidents)


def test_runner_files_remain_unchanged(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    _write_json(runner / "food-line/status/operational-health/food-line/2026-09-20/runs/proof.json", {"ok": True})
    before = _snapshot(runner)
    _patch_status(monkeypatch, _status())

    _run(repo, runner)

    assert _snapshot(runner) == before


def test_only_ops_operator_changes(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    _write_json(repo / "data/unrelated.json", {"unchanged": True})
    before = _snapshot(repo)
    _patch_status(monkeypatch, _status(state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    _run(repo, runner)

    changed = {
        path
        for path, fingerprint in _snapshot(repo).items()
        if before.get(path) != fingerprint
    } | (set(_snapshot(repo)) - set(before))
    assert changed
    assert all(path.startswith("ops/operator/") for path in changed)


def test_json_output_is_deterministic(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    status = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch_status(monkeypatch, status, _plan(status, action="INVESTIGATE_COLLECTION", disposition="PLAN_AVAILABLE"))

    first = operator.check_operator(repo_root=repo, operator_root=repo / "ops/operator", config=_config(runner), now=NOW, write_ledger=False).to_payload()
    second = operator.check_operator(repo_root=repo, operator_root=repo / "ops/operator", config=_config(runner), now=NOW, write_ledger=False).to_payload()

    assert json.dumps(first, indent=2, sort_keys=True) == json.dumps(second, indent=2, sort_keys=True)


def test_dispatch_ops_behavior_unchanged_for_terminal_status() -> None:
    status = _status(dispatch="gaza", state="NO_UPDATE", next_action="NONE")
    plan = operator.build_recovery_plan_from_status(status)

    assert plan.action == "NONE"
    assert plan.disposition == "NO_ACTION"
