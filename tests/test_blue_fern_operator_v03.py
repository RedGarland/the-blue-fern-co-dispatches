from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import ApplyResult, DispatchStatus, RecoveryPlan


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _config(runner: Path, dispatch: str = "food-line") -> operator.OperatorConfig:
    return operator.OperatorConfig(
        status_freshness_threshold_minutes=120,
        dispatches={
            name: operator.DispatchConfig(name, runner / name, enabled=name == dispatch)
            for name in operator.DISPATCH_ORDER
        },
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


def _policy(repo: Path, mode: str = "automatic") -> None:
    (repo / "ops/operator").mkdir(parents=True, exist_ok=True)
    (repo / "ops/operator/remediation-policy.yaml").write_text(
        f"REBUILD_STATUS:\n  mode: {mode}\nINVESTIGATE_COLLECTION:\n  mode: recommend\n"
        "INVESTIGATE_FAILED_SOURCES:\n  mode: recommend\nREPLAY_COLLECTION:\n  mode: forbidden\n",
        encoding="utf-8",
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
        collection="COMPLETE",
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


def _apply_result(status: DispatchStatus, *, outcome: str = "REBUILT", changed: bool = True) -> ApplyResult:
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
        status_artifacts_changed=[f"ops/status/{status.dispatch}/latest.json"] if changed else [],
        unexpected_changes=["data/unexpected.txt"] if outcome == "FAILED" else [],
        evidence=status.evidence,
        status_before=status.to_json_payload(),
        status_after=status.to_json_payload(),
    )


def _patch(monkeypatch: pytest.MonkeyPatch, statuses: list[DispatchStatus], plans: dict[str, RecoveryPlan]) -> None:
    status_iter = iter(statuses)
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: next(status_iter))
    monkeypatch.setattr(operator, "build_recovery_plan_from_status", lambda status: plans[status.state])


def _run(repo: Path, runner: Path) -> operator.OperatorResult:
    return operator.check_operator(
        repo_root=repo,
        operator_root=repo / "ops/operator",
        config=_config(runner),
        now=NOW,
    )


def test_healthy_no_action_run_has_no_notification(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    healthy = _status()
    _patch(monkeypatch, [healthy], {"SAFE_NO_OP": _plan(healthy)})

    result = _run(repo, runner)

    assert result.notification.notification_required is False
    assert result.notification.reasons == []


def test_first_new_incident_notifies_once_and_then_suppresses_spam(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    failed = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch(monkeypatch, [failed, failed], {"FAILED": _plan(failed, "INVESTIGATE_COLLECTION", "PLAN_AVAILABLE")})

    first = _run(repo, runner)
    second = _run(repo, runner)

    assert first.notification.reasons == ["APPROVAL_REQUIRED", "NEW_INCIDENT"]
    assert second.notification.notification_required is False
    assert first.incidents[0].incident_id == second.incidents[0].incident_id
    assert len(list((repo / "ops/operator/incidents").glob("*.json"))) == 1


def test_material_action_change_is_worsened_incident(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    failed = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch(
        monkeypatch,
        [failed, failed],
        {"FAILED": _plan(failed, "INVESTIGATE_COLLECTION", "PLAN_AVAILABLE")},
    )
    _run(repo, runner)
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: failed)
    monkeypatch.setattr(operator, "build_recovery_plan_from_status", lambda _status: _plan(failed, "REPLAY_COLLECTION", "PLAN_AVAILABLE"))

    second = _run(repo, runner)

    assert "INCIDENT_WORSENED" in second.notification.reasons


def test_recovered_incident_notifies_material_recovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    failed = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    healthy = _status()
    _patch(monkeypatch, [failed], {"FAILED": _plan(failed, "INVESTIGATE_COLLECTION", "PLAN_AVAILABLE")})
    _run(repo, runner)
    _patch(monkeypatch, [healthy], {"SAFE_NO_OP": _plan(healthy)})

    recovered = _run(repo, runner)

    assert recovered.notification.reasons == ["MATERIAL_RECOVERY"]
    assert any(incident.state == "RECOVERED" for incident in recovered.incidents)


def test_remediation_failure_always_notifies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _policy(repo, "automatic")
    _export(repo, "food-line", "2026-09-11", "2026-09-11T18:07:41Z")
    before = _status(next_action="INVESTIGATE_STATUS_EXPORT")
    after = _status(next_action="INVESTIGATE_STATUS_EXPORT")
    _patch(monkeypatch, [before, after], {"SAFE_NO_OP": _plan(before, "REBUILD_STATUS", "PLAN_AVAILABLE")})
    monkeypatch.setattr(operator, "apply_recovery_plan", lambda *_args, **_kwargs: _apply_result(before, outcome="FAILED"))

    result = _run(repo, runner)

    assert "REMEDIATION_FAILED" in result.notification.reasons
    assert result.incidents[0].remediation["outcome"] == "FAILED"


def test_execution_receipt_and_notification_schema_are_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runner = tmp_path / "runner"
    _export(repo, "food-line", "2026-09-20")
    failed = _status(state="FAILED", next_action="INVESTIGATE_COLLECTION")
    _patch(monkeypatch, [failed], {"FAILED": _plan(failed, "INVESTIGATE_COLLECTION", "PLAN_AVAILABLE")})

    _run(repo, runner)

    latest = json.loads((repo / "ops/operator/notification-latest.json").read_text(encoding="utf-8"))
    receipts = list((repo / "ops/operator/runs/2026-09-20").glob("*.json"))
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert latest["schema_version"] == "blue_fern_operator_notification_v1"
    assert set(latest["reasons"]) <= operator.NOTIFICATION_REASONS
    assert receipt["schema_version"] == "blue_fern_operator_run_receipt_v1"
    assert receipt["notification_required"] is True
    assert receipt["new_incidents"] == latest["incident_ids"]


def test_existing_lock_exits_before_duplicate_ledger_or_remediation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "ops/operator/.lock").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="Operator lock already held"):
        operator.check_operator(repo_root=repo, operator_root=repo / "ops/operator", config=_config(tmp_path / "runner"), now=NOW)

    assert not (repo / "ops/operator/history.jsonl").exists()


def test_wrapper_invokes_only_operator_entry_point() -> None:
    wrapper = Path("scripts/run_blue_fern_operator.ps1").read_text(encoding="utf-8")

    assert "scripts\\blue_fern_operator.py" in wrapper
    assert '"check", "--json"' in wrapper
    assert "run_and_notify.py" not in wrapper
    assert "run_gaza_dispatch.py" not in wrapper
    assert "--publish" not in wrapper


def test_scheduler_template_is_disabled_half_hour_operator_task() -> None:
    tree = ET.parse("ops/blue_fern_operator_task.xml")
    root = tree.getroot()
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    assert root.findtext(".//t:Settings/t:Enabled", namespaces=ns) == "false"
    assert root.findtext(".//t:Triggers/t:CalendarTrigger/t:Enabled", namespaces=ns) == "false"
    assert root.findtext(".//t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert root.findtext(".//t:Repetition/t:Interval", namespaces=ns) == "PT30M"
    assert "run_blue_fern_operator.ps1" in root.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=ns)


def test_operator_has_no_llm_dependency() -> None:
    source = Path("scripts/blue_fern_operator.py").read_text(encoding="utf-8").lower()

    assert "openai" not in source
    assert "llm" not in source
