from __future__ import annotations

import json
from pathlib import Path

from bluefern_dispatches.operational_status_exporter import StatusCheckoutGitState
from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


NOW = operator._parse_time("2026-09-21T12:00:00Z")


def _config(runner_root: Path, status_root: Path, *, enabled: tuple[str, ...] = ("food-line",)) -> operator.OperatorConfig:
    return operator.OperatorConfig(
        status_freshness_threshold_minutes=120,
        status_root=status_root,
        dispatches={
            dispatch: operator.DispatchConfig(
                dispatch=dispatch,
                runner_root=runner_root / dispatch,
                enabled=dispatch in enabled,
            )
            for dispatch in operator.DISPATCH_ORDER
        },
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
        receipts="COMPLETE",
        recovery="HEALTHY",
        next_action=next_action,
        evidence=evidence if evidence is not None else ["receipt.json"],
        details={"task_statuses": {"collection": state}},
    )


def _incident(
    *,
    dispatch: str = "food-line",
    incident_id: str = "bfo-v062",
    classification: str = "STALE_OBSERVABILITY",
    state: str = operator.IncidentState.OPEN.value,
    affected_date: str = "2026-09-21",
    status_state: str = "DEGRADED",
) -> operator.Incident:
    return operator.Incident(
        incident_id=incident_id,
        incident_key=f"{dispatch}|{classification}|{affected_date}|",
        dispatch=dispatch,
        detected_at="2026-09-21T12:00:00Z",
        updated_at="2026-09-21T12:00:00Z",
        state=state,
        classification=classification,
        status_state=status_state,
        recovery_disposition="OPERATOR_REVIEW_REQUIRED",
        recovery_action="INVESTIGATE_STATUS_EXPORT",
        evidence=[f"status/operational-health/{dispatch}/{affected_date}/runs/receipt.json"],
        recommended_action="INVESTIGATE_STATUS_EXPORT",
        affected_date=affected_date,
    )


def _write_external_status(root: Path, *, dispatch: str = "food-line", exported_at: str = "2026-09-21T11:30:00Z") -> None:
    payload = {
        "schema_version": "bluefern_external_operational_status_v1",
        "dispatch": dispatch,
        "observed_date": "2026-09-21",
        "aggregate_status": "COMPLETE",
        "last_exported_at": exported_at,
        "receipt_completeness": "COMPLETE",
        "recovery_lifecycle": "HEALTHY",
        "publication_status": "verified",
    }
    path = root / "ops" / "status" / dispatch / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _refresh_paths(dispatch: str, date: str) -> list[str]:
    paths = [
        "ops/status/food-line/history/" + date + ".json",
        "ops/status/food-line/latest.json",
        "ops/status/system/latest.json",
    ]
    if dispatch in {"care-line", "ice"}:
        paths.extend(
            [
                f"ops/status/{dispatch}/history/{date}.json",
                f"ops/status/{dispatch}/latest.json",
            ]
        )
    return sorted(paths)


def _fake_export_status(assertion=None):
    def fake_export_status(**kwargs) -> dict:
        if assertion is not None:
            assertion(kwargs)
        dispatch = "food-line"
        if kwargs.get("care_source_root") is not None:
            dispatch = "care-line"
        if kwargs.get("ice_source_root") is not None:
            dispatch = "ice"
        paths = _refresh_paths(dispatch, kwargs["date"])
        for path in paths:
            payload = {
                "dispatch": dispatch,
                "observed_date": kwargs["date"],
                "aggregate_status": "DEGRADED",
                "last_exported_at": kwargs["exported_at"],
            }
            if path == "ops/status/system/latest.json":
                payload = {"last_exported_at": kwargs["exported_at"]}
            operator._write_json(kwargs["status_checkout"] / path, payload)
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


def test_configured_status_root_is_authoritative_for_operator_check(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    runner_root = tmp_path / "runners"
    _write_external_status(status_root, dispatch="food-line")
    _write_external_status(repo_root, dispatch="food-line", exported_at="2026-09-20T00:00:00Z")
    calls: list[Path] = []

    def fake_build_status(dispatch: str, date: str, *, root: Path) -> DispatchStatus:
        calls.append(root)
        return _status(dispatch=dispatch, date=date, state="COMPLETE", next_action="NONE")

    monkeypatch.setattr(operator, "build_status", fake_build_status)

    result = operator.check_operator(
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(runner_root, status_root),
        now=NOW,
        write_ledger=False,
    )

    assert result.dispatches[0].classification is None
    assert result.dispatches[0].exported_status["source_root"] == str(status_root.resolve())
    assert calls == [(runner_root / "food-line")]


def test_refresh_plan_requires_status_root_distinct_from_operator_and_runner(tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    operator_root = tmp_path / "repo" / "ops" / "operator"
    plan_same_as_operator = operator.build_remediation_action_plan(
        _incident(),
        runner_root=runner_root,
        operator_root=operator_root,
        status_root=tmp_path / "repo",
        current_status=_status(),
    )
    plan_same_as_runner = operator.build_remediation_action_plan(
        _incident(),
        runner_root=runner_root,
        operator_root=operator_root,
        status_root=runner_root,
        current_status=_status(),
    )

    assert plan_same_as_operator.proposed_action == "REFRESH_STATUS_EXPORT"
    assert plan_same_as_operator.executable is False
    assert plan_same_as_operator.expected_mutation_scope == []
    assert plan_same_as_operator.safety_checks["roots_distinct"] is False
    assert plan_same_as_runner.executable is False
    assert plan_same_as_runner.safety_checks["roots_distinct"] is False


def test_refresh_writes_dedicated_status_checkout_only(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    runner_root = tmp_path / "runners"
    incident = _incident()
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status())
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operator, "export_status", _fake_export_status())

    receipt = operator.apply_remediation(
        dispatch="food-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(runner_root, status_root),
        now=NOW,
    )

    assert receipt.accepted is True
    assert receipt.changed_paths == _refresh_paths("food-line", "2026-09-21")
    assert (status_root / "ops/status/food-line/latest.json").is_file()
    assert (status_root / "ops/status/system/latest.json").is_file()
    assert not (repo_root / "ops/status/food-line/latest.json").exists()
    assert not ((runner_root / "food-line") / "ops/status/food-line/latest.json").exists()
    assert receipt.validation["commit_created"] is False
    assert receipt.validation["push_attempted"] is False


def test_apply_refuses_recovered_closed_or_suppressed_incident(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    recovered = _incident(state=operator.IncidentState.RECOVERED.value)
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [recovered]))

    receipt = operator.apply_remediation(
        dispatch="food-line",
        incident_id=recovered.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(tmp_path / "runners", status_root),
        now=NOW,
    )

    assert receipt.accepted is False
    assert receipt.outcome == "REFUSED"
    assert receipt.reason == "incident is no longer open"


def test_remediation_plan_uses_open_incidents_only(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    incidents = [
        _incident(incident_id="recovered", state=operator.IncidentState.RECOVERED.value),
        _incident(incident_id="closed", state=operator.IncidentState.CLOSED.value),
        _incident(incident_id="suppressed", state=operator.IncidentState.SUPPRESSED.value),
    ]
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], incidents))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status())

    plan = operator.build_remediation_plan(
        "food-line",
        "2026-09-21",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(tmp_path / "runners", status_root),
        now=NOW,
    )

    assert plan["plan_count"] == 0
    assert plan["plans"] == []


def test_care_refresh_uses_scheduler_expected_instances(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    runner_root = tmp_path / "runners"
    incident = _incident(dispatch="care-line", status_state="FAILED")
    expected_instances = [{"task": "care_line_collection", "time": "01:00"}]
    observed: dict[str, object] = {}
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status(dispatch="care-line", state="FAILED", next_action="INVESTIGATE_COLLECTION"))
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operator, "care_expected_instances_from_task_scheduler", lambda date: expected_instances)

    def assertion(kwargs: dict) -> None:
        observed["care_expected_instances"] = kwargs.get("care_expected_instances")
        observed["source_root"] = kwargs.get("source_root")
        observed["care_source_root"] = kwargs.get("care_source_root")

    monkeypatch.setattr(operator, "export_status", _fake_export_status(assertion))

    receipt = operator.apply_remediation(
        dispatch="care-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(runner_root, status_root, enabled=("food-line", "care-line")),
        now=NOW,
    )

    assert receipt.accepted is True
    assert observed["care_expected_instances"] == expected_instances
    assert observed["source_root"] == (runner_root / "food-line").resolve()
    assert observed["care_source_root"] == (runner_root / "care-line").resolve()


def test_food_care_ice_refreshes_share_dirty_status_checkout_without_commit_or_push(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    runner_root = tmp_path / "runners"
    current_incident = {"value": _incident(dispatch="food-line")}
    prepare_calls: list[dict] = []
    command_calls: list[list[str]] = []

    def fake_check_operator(**_kwargs) -> operator.OperatorResult:
        return operator.OperatorResult("2026-09-21T12:00:00Z", [], [current_incident["value"]])

    def fake_build_status(dispatch: str, date: str, *, root: Path) -> DispatchStatus:
        state = "FAILED" if dispatch == "care-line" else "COMPLETE" if dispatch == "ice" else "DEGRADED"
        next_action = "INVESTIGATE_COLLECTION" if dispatch == "care-line" else "NONE" if dispatch == "ice" else "INVESTIGATE_FAILED_SOURCES"
        return _status(dispatch=dispatch, date=date, state=state, next_action=next_action)

    def fake_prepare_status_checkout(*_args, **kwargs) -> None:
        prepare_calls.append(kwargs)

    def fake_run_command(args: list[str], **_kwargs) -> operator.EngineeringCommandResult:
        command_calls.append(args)
        if args == ["git", "rev-parse", "HEAD"]:
            return operator.EngineeringCommandResult(0, "status-head\n")
        return operator.EngineeringCommandResult(0)

    monkeypatch.setattr(operator, "check_operator", fake_check_operator)
    monkeypatch.setattr(operator, "build_status", fake_build_status)
    _patch_clean_status_checkout(monkeypatch)
    monkeypatch.setattr(operator, "prepare_status_checkout", fake_prepare_status_checkout)
    monkeypatch.setattr(operator, "care_expected_instances_from_task_scheduler", lambda _date: [])
    monkeypatch.setattr(operator, "export_status", _fake_export_status())
    monkeypatch.setattr(operator, "_run_command", fake_run_command)

    receipts = []
    for dispatch in ("food-line", "care-line", "ice"):
        current_incident["value"] = _incident(
            dispatch=dispatch,
            status_state="FAILED" if dispatch == "care-line" else "COMPLETE" if dispatch == "ice" else "DEGRADED",
            affected_date="2026-09-21",
        )
        receipts.append(
            operator.apply_remediation(
                dispatch=dispatch,
                incident_id=current_incident["value"].incident_id,
                action="REFRESH_STATUS_EXPORT",
                confirm="REFRESH_STATUS_EXPORT",
                repo_root=repo_root,
                operator_root=repo_root / "ops" / "operator",
                config=_config(runner_root, status_root, enabled=("food-line", "care-line", "ice")),
                now=NOW,
            )
        )

    assert [receipt.accepted for receipt in receipts] == [True, True, True]
    assert all(call["allow_local_status_changes"] is True for call in prepare_calls)
    assert (status_root / "ops/status/food-line/latest.json").is_file()
    assert (status_root / "ops/status/care-line/latest.json").is_file()
    assert (status_root / "ops/status/ice/latest.json").is_file()
    assert (status_root / "ops/status/system/latest.json").is_file()
    assert all(receipt.validation["commit_created"] is False for receipt in receipts)
    assert all(receipt.validation["push_attempted"] is False for receipt in receipts)
    flattened = [part for call in command_calls for part in call]
    assert "commit" not in flattened
    assert "push" not in flattened
    assert "reset" not in flattened
    assert "restore" not in flattened
    assert "clean" not in flattened
    assert "stash" not in flattened
    assert "rebase" not in flattened
    assert "--force" not in flattened


def test_refresh_receipt_records_status_checkout_state_and_remote_overlap(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    runner_root = tmp_path / "runners"
    incident = _incident()
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status())
    monkeypatch.setattr(operator, "_run_command", lambda args, **_kwargs: operator.EngineeringCommandResult(0, "status-head\n") if args == ["git", "rev-parse", "HEAD"] else operator.EngineeringCommandResult(0))
    monkeypatch.setattr(
        operator,
        "classify_status_checkout_state",
        lambda _root: StatusCheckoutGitState(
            state="SANCTIONED_STATUS_ONLY",
            tracked_status_paths=["ops/status/food-line/latest.json"],
            untracked_status_paths=[],
            unexpected_paths=[],
        ),
    )

    def fail_prepare(*_args, **_kwargs) -> None:
        raise RuntimeError("incoming status checkout changes overlap local ops/status changes: ops/status/food-line/latest.json")

    monkeypatch.setattr(operator, "prepare_status_checkout", fail_prepare)

    receipt = operator.apply_remediation(
        dispatch="food-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(runner_root, status_root),
        now=NOW,
    )

    assert receipt.accepted is False
    assert receipt.validation["status_checkout_state_before"]["state"] == "SANCTIONED_STATUS_ONLY"
    assert receipt.validation["status_checkout_state_after"]["state"] == "SANCTIONED_STATUS_ONLY"
    assert receipt.validation["sanctioned_dirty_paths_before"] == ["ops/status/food-line/latest.json"]
    assert receipt.validation["sanctioned_dirty_paths_after"] == ["ops/status/food-line/latest.json"]
    assert receipt.validation["remote_overlap_paths"] == ["ops/status/food-line/latest.json"]
    assert receipt.validation["status_checkout_head_before"] == "status-head"
    assert receipt.validation["status_checkout_head_after"] == "status-head"
    assert receipt.validation["commit_created"] is False
    assert receipt.validation["push_attempted"] is False


def test_refresh_scope_includes_system_latest_and_food_context_for_care_and_ice(tmp_path: Path) -> None:
    operator_root = tmp_path / "repo" / "ops" / "operator"
    status_root = tmp_path / "status"
    care = operator.build_remediation_action_plan(
        _incident(dispatch="care-line", status_state="FAILED"),
        runner_root=tmp_path / "care",
        operator_root=operator_root,
        status_root=status_root,
        current_status=_status(dispatch="care-line", state="FAILED", next_action="INVESTIGATE_COLLECTION"),
    )
    ice = operator.build_remediation_action_plan(
        _incident(dispatch="ice", status_state="COMPLETE", affected_date="2026-09-20"),
        runner_root=tmp_path / "ice",
        operator_root=operator_root,
        status_root=status_root,
        current_status=_status(dispatch="ice", date="2026-09-20", state="COMPLETE", next_action="NONE"),
    )

    assert care.expected_mutation_scope == _refresh_paths("care-line", "2026-09-21")
    assert ice.expected_mutation_scope == _refresh_paths("ice", "2026-09-20")


def test_gaza_refresh_uses_external_status_builder(tmp_path: Path) -> None:
    operator_root = tmp_path / "repo" / "ops" / "operator"
    status_root = tmp_path / "status"
    runner_root = tmp_path / "gaza"
    operator_root.mkdir(parents=True)
    status_root.mkdir()
    runner_root.mkdir()

    plan = operator.build_remediation_action_plan(
        _incident(dispatch="gaza", status_state="UNKNOWN"),
        runner_root=runner_root,
        operator_root=operator_root,
        status_root=status_root,
        current_status=_status(dispatch="gaza", state="UNKNOWN", next_action="UNKNOWN_REQUIRES_OPERATOR"),
    )

    assert plan.proposed_action == "REFRESH_STATUS_EXPORT"
    assert plan.executable is True
    assert plan.safety_checks["refresh_supported"] is True
    assert "ops/status/gaza/latest.json" in plan.expected_mutation_scope
    assert "ops/status/system/latest.json" in plan.expected_mutation_scope


def test_replanned_recovered_incident_does_not_offer_second_refresh(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    incident = _incident(state=operator.IncidentState.RECOVERED.value)
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status())

    plan = operator.build_remediation_plan(
        "food-line",
        "2026-09-21",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(tmp_path / "runners", status_root),
        now=NOW,
    )

    assert plan["plan_count"] == 0
