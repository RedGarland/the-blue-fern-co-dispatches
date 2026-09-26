from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.operational_status_exporter import StatusCheckoutGitState
from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


def _incident(
    *,
    incident_id: str = "bfo-v061",
    dispatch: str = "food-line",
    classification: str = "STALE_OBSERVABILITY",
    affected_date: str = "2026-09-21",
    status_state: str = "DEGRADED",
    recovery_action: str = "INVESTIGATE_STATUS_EXPORT",
    evidence: list[str] | None = None,
) -> operator.Incident:
    return operator.Incident(
        incident_id=incident_id,
        incident_key=f"{dispatch}|{classification}|{affected_date}|",
        dispatch=dispatch,
        detected_at="2026-09-21T12:00:00Z",
        updated_at="2026-09-21T12:00:00Z",
        state=operator.IncidentState.OPEN.value,
        classification=classification,
        status_state=status_state,
        recovery_disposition="OPERATOR_REVIEW_REQUIRED",
        recovery_action=recovery_action,
        evidence=evidence or [f"status/operational-health/{dispatch}/2026-09-21/runs/receipt.json"],
        recommended_action=recovery_action,
        affected_date=affected_date,
    )


def _status(
    *,
    dispatch: str = "food-line",
    date: str = "2026-09-21",
    state: str = "DEGRADED",
    next_action: str = "INVESTIGATE_FAILED_SOURCES",
    evidence: list[str] | None = None,
) -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date=date,
        state=state,
        collection=state,
        editorial="COMPLETE",
        publication="SAFE_NO_OP",
        public_state="NOT_VERIFIED",
        receipts="COMPLETE" if evidence is not None else "OBSERVED",
        recovery="HEALTHY",
        next_action=next_action,
        evidence=evidence if evidence is not None else ["receipt.json"],
        details={"task_statuses": {"task": state}},
    )


def _config(runner_root: Path, *, status_root: Path | None = None) -> operator.OperatorConfig:
    return operator.OperatorConfig(
        status_freshness_threshold_minutes=120,
        status_root=status_root,
        dispatches={
            dispatch: operator.DispatchConfig(dispatch=dispatch, runner_root=runner_root, enabled=True)
            for dispatch in operator.DISPATCH_ORDER
        },
    )


def _status_paths(dispatch: str, date: str) -> list[str]:
    paths = [
        "ops/status/food-line/latest.json",
        f"ops/status/food-line/history/{date}.json",
        "ops/status/system/latest.json",
    ]
    if dispatch in {"care-line", "ice"}:
        paths.extend(
            [
                f"ops/status/{dispatch}/latest.json",
                f"ops/status/{dispatch}/history/{date}.json",
            ]
        )
    return sorted(paths)


def _fake_export_status(*, aggregate_status: str = "FAILED", extra_paths: list[str] | None = None):
    def fake_export_status(**kwargs) -> dict:
        status_checkout = kwargs["status_checkout"]
        date = kwargs["date"]
        dispatch = "food-line"
        if kwargs.get("care_source_root") is not None:
            dispatch = "care-line"
        if kwargs.get("ice_source_root") is not None:
            dispatch = "ice"
        paths = _status_paths(dispatch, date)
        payload = {
            "schema_version": "bluefern_external_operational_status_v1",
            "dispatch": dispatch,
            "observed_date": date,
            "aggregate_status": aggregate_status,
            "last_exported_at": kwargs["exported_at"],
            "receipt_completeness": "OBSERVED",
            "recovery_lifecycle": "INCIDENT_OPEN",
            "publication_status": "failure",
            "next_expected_run": None,
        }
        for path in paths:
            if path == "ops/status/system/latest.json":
                operator._write_json(status_checkout / path, {"last_exported_at": kwargs["exported_at"]})
            else:
                operator._write_json(status_checkout / path, payload)
        for path in extra_paths or []:
            operator._write_json(status_checkout / path, {"unexpected": True})
        return {"paths": paths}

    return fake_export_status


def _patch_clean_status_checkout(monkeypatch) -> None:
    monkeypatch.setattr(
        operator,
        "classify_status_checkout_state",
        lambda _root: StatusCheckoutGitState(
            state="CLEAN",
            tracked_status_paths=[],
            untracked_status_paths=[],
            unexpected_paths=[],
        ),
    )


def test_food_degraded_stale_routes_to_refresh_not_rebuild(tmp_path: Path) -> None:
    status_root = tmp_path / "status"
    plan = operator.build_remediation_action_plan(
        _incident(dispatch="food-line", status_state="DEGRADED"),
        runner_root=tmp_path / "food",
        operator_root=tmp_path / "repo" / "ops" / "operator",
        current_status=_status(dispatch="food-line", state="DEGRADED", next_action="INVESTIGATE_FAILED_SOURCES"),
        status_root=status_root,
    )

    assert plan.proposed_action == "REFRESH_STATUS_EXPORT"
    assert plan.executable is True
    assert plan.proposed_action != "REBUILD_STATUS"
    assert plan.expected_mutation_scope == _status_paths("food-line", "2026-09-21")
    assert plan.safety_checks["source_runner_root"].endswith("food")
    assert plan.safety_checks["status_destination_root"].endswith("status")


def test_care_failed_stale_routes_to_refresh_not_rebuild(tmp_path: Path) -> None:
    status_root = tmp_path / "status"
    plan = operator.build_remediation_action_plan(
        _incident(dispatch="care-line", status_state="FAILED"),
        runner_root=tmp_path / "care",
        operator_root=tmp_path / "repo" / "ops" / "operator",
        current_status=_status(dispatch="care-line", state="FAILED", next_action="INVESTIGATE_COLLECTION"),
        status_root=status_root,
    )

    assert plan.proposed_action == "REFRESH_STATUS_EXPORT"
    assert plan.executable is True
    assert plan.proposed_action != "REBUILD_STATUS"
    assert plan.expected_mutation_scope == _status_paths("care-line", "2026-09-21")


def test_ice_complete_stale_routes_to_refresh_not_rebuild(tmp_path: Path) -> None:
    status_root = tmp_path / "status"
    plan = operator.build_remediation_action_plan(
        _incident(dispatch="ice", status_state="COMPLETE", affected_date="2026-09-20"),
        runner_root=tmp_path / "ice",
        operator_root=tmp_path / "repo" / "ops" / "operator",
        current_status=_status(dispatch="ice", date="2026-09-20", state="COMPLETE", next_action="NONE"),
        status_root=status_root,
    )

    assert plan.proposed_action == "REFRESH_STATUS_EXPORT"
    assert plan.executable is True
    assert plan.proposed_action != "REBUILD_STATUS"
    assert plan.expected_mutation_scope == _status_paths("ice", "2026-09-20")


def test_gaza_refresh_is_supported_by_external_status_builder(tmp_path: Path) -> None:
    status_root = tmp_path / "status"
    plan = operator.build_remediation_action_plan(
        _incident(dispatch="gaza", status_state="UNKNOWN"),
        runner_root=tmp_path / "gaza",
        operator_root=tmp_path / "repo" / "ops" / "operator",
        current_status=_status(dispatch="gaza", state="UNKNOWN", next_action="UNKNOWN_REQUIRES_OPERATOR"),
        status_root=status_root,
    )

    assert plan.proposed_action == "REFRESH_STATUS_EXPORT"
    assert plan.executable is True
    assert plan.safety_checks["refresh_supported"] is True


def test_rebuild_status_only_when_dispatch_ops_plan_available(tmp_path: Path) -> None:
    status = _status(
        dispatch="food-line",
        state="COMPLETE",
        next_action="INVESTIGATE_STATUS_EXPORT",
        evidence=["receipt.json"],
    )
    status = DispatchStatus(
        dispatch=status.dispatch,
        date=status.date,
        state=status.state,
        collection="COMPLETE",
        editorial=status.editorial,
        publication=status.publication,
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery=status.recovery,
        next_action=status.next_action,
        evidence=status.evidence,
        details={"status_export_state": "MISSING_OR_STALE", "status_rebuild_supported": True},
    )

    plan = operator.build_remediation_action_plan(
        _incident(dispatch="food-line", status_state="COMPLETE", recovery_action="REBUILD_STATUS"),
        runner_root=tmp_path / "food",
        operator_root=tmp_path / "repo" / "ops" / "operator",
        current_status=status,
    )

    assert plan.proposed_action == "REBUILD_STATUS"
    assert plan.executable is True


def test_executable_refresh_plan_can_be_immediately_applied(monkeypatch, tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    runner_root.mkdir()
    repo_root = tmp_path / "repo"
    operator_root = repo_root / "ops" / "operator"
    status_root = tmp_path / "status"
    status_root.mkdir()
    incident = _incident(dispatch="care-line", status_state="FAILED")
    status = _status(dispatch="care-line", state="FAILED", next_action="INVESTIGATE_COLLECTION")
    result = operator.OperatorResult(
        checked_at="2026-09-21T12:00:00Z",
        dispatches=[],
        incidents=[incident],
    )
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: result)
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operator, "care_expected_instances_from_task_scheduler", lambda _date: [])
    monkeypatch.setattr(operator, "export_status", _fake_export_status(aggregate_status="FAILED"))

    receipt = operator.apply_remediation(
        dispatch="care-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=operator_root,
        config=_config(runner_root, status_root=status_root),
        now=operator._parse_time("2026-09-21T12:00:00Z"),
    )

    assert receipt.accepted is True
    assert receipt.outcome == "REFRESHED"
    assert receipt.changed_paths == _status_paths("care-line", "2026-09-21")
    assert (status_root / "ops/status/care-line/latest.json").is_file()
    assert not (repo_root / "ops/status/care-line/latest.json").exists()
    assert not (runner_root / "ops/status/care-line/latest.json").exists()
    assert receipt.validation["source_runner_root"] == str(runner_root.resolve())
    assert receipt.validation["status_destination_root"] == str(status_root.resolve())
    assert receipt.validation["underlying_incident_preserved"] is True
    latest = json.loads((status_root / "ops/status/care-line/latest.json").read_text(encoding="utf-8"))
    assert latest["aggregate_status"] == "FAILED"


def test_refresh_does_not_mutate_runner_source(monkeypatch, tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    source = runner_root / "src" / "bluefern_dispatches" / "care_line.py"
    source.parent.mkdir(parents=True)
    source.write_text("original\n", encoding="utf-8")
    before = source.read_text(encoding="utf-8")
    repo_root = tmp_path / "repo"
    operator_root = repo_root / "ops" / "operator"
    status_root = tmp_path / "status"
    status_root.mkdir()
    incident = _incident(dispatch="food-line")
    status = _status(dispatch="food-line")
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operator, "export_status", _fake_export_status())

    operator.apply_remediation(
        dispatch="food-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=operator_root,
        config=_config(runner_root, status_root=status_root),
        now=operator._parse_time("2026-09-21T12:00:00Z"),
    )

    assert source.read_text(encoding="utf-8") == before


def test_unexpected_status_path_mutation_fails_closed(monkeypatch, tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    runner_root.mkdir()
    repo_root = tmp_path / "repo"
    operator_root = repo_root / "ops" / "operator"
    status_root = tmp_path / "status"
    status_root.mkdir()
    incident = _incident(dispatch="ice", affected_date="2026-09-20", status_state="COMPLETE")
    status = _status(dispatch="ice", date="2026-09-20", state="COMPLETE", next_action="NONE")
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        operator,
        "export_status",
        _fake_export_status(extra_paths=["ops/status/unexpected/latest.json"]),
    )

    receipt = operator.apply_remediation(
        dispatch="ice",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=operator_root,
        config=_config(runner_root, status_root=status_root),
        now=operator._parse_time("2026-09-21T12:00:00Z"),
    )

    assert receipt.accepted is False
    assert receipt.outcome == "FAILED"
    assert "ops/status/unexpected/latest.json" in receipt.changed_paths
    assert "ops/status/unexpected/latest.json" in receipt.warnings


def test_refresh_plan_and_receipt_prohibit_publication_replay_scheduler_and_editorial(monkeypatch, tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    runner_root.mkdir()
    repo_root = tmp_path / "repo"
    operator_root = repo_root / "ops" / "operator"
    status_root = tmp_path / "status"
    status_root.mkdir()
    incident = _incident(dispatch="food-line")
    status = _status(dispatch="food-line")
    plan = operator.build_remediation_action_plan(
        incident,
        runner_root=runner_root,
        operator_root=operator_root,
        current_status=status,
        status_root=status_root,
    )
    assert plan.safety_checks["public_side_effects"] is False
    assert plan.safety_checks["scheduler_changes"] is False
    assert plan.safety_checks["collection_rerun"] is False
    assert plan.safety_checks["editorial_mutation"] is False
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: status)
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operator, "export_status", _fake_export_status())

    receipt = operator.apply_remediation(
        dispatch="food-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=operator_root,
        config=_config(runner_root, status_root=status_root),
        now=operator._parse_time("2026-09-21T12:00:00Z"),
    )

    assert receipt.validation["public_side_effects"] is False
    assert receipt.validation["scheduler_changes"] is False
    assert receipt.validation["collection_rerun"] is False
    assert receipt.validation["editorial_mutation"] is False
