from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.operational_status_exporter import StatusCheckoutGitState
from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


NOW = operator._parse_time("2026-09-21T12:00:00Z")
OLD_EXPORTED_AT = "2026-09-21T08:00:00Z"


def _config(runner_root: Path, status_root: Path) -> operator.OperatorConfig:
    return operator.OperatorConfig(
        status_freshness_threshold_minutes=120,
        status_root=status_root,
        dispatches={
            dispatch: operator.DispatchConfig(
                dispatch=dispatch,
                runner_root=runner_root / dispatch,
                enabled=True,
            )
            for dispatch in operator.DISPATCH_ORDER
        },
    )


def _status(*, dispatch: str = "food-line", state: str = "DEGRADED", next_action: str = "INVESTIGATE_FAILED_SOURCES") -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date="2026-09-21",
        state=state,
        collection=state,
        editorial="COMPLETE",
        publication="SAFE_NO_OP",
        public_state="NOT_VERIFIED",
        receipts="COMPLETE",
        recovery="HEALTHY",
        next_action=next_action,
        evidence=["receipt.json"],
        details={"task_statuses": {"collection": state}},
    )


def _incident(*, dispatch: str = "food-line", status_state: str = "DEGRADED") -> operator.Incident:
    return operator.Incident(
        incident_id=f"{dispatch}-stale",
        incident_key=f"{dispatch}|STALE_OBSERVABILITY|2026-09-21|",
        dispatch=dispatch,
        detected_at="2026-09-21T08:30:00Z",
        updated_at="2026-09-21T08:30:00Z",
        state=operator.IncidentState.OPEN.value,
        classification="STALE_OBSERVABILITY",
        status_state=status_state,
        recovery_disposition="OPERATOR_REVIEW_REQUIRED",
        recovery_action="INVESTIGATE_STATUS_EXPORT",
        evidence=[f"ops/status/{dispatch}/latest.json"],
        recommended_action="INVESTIGATE_STATUS_EXPORT",
        affected_date="2026-09-21",
    )


def _refresh_paths(dispatch: str) -> list[str]:
    paths = [
        "ops/status/food-line/history/2026-09-21.json",
        "ops/status/food-line/latest.json",
        "ops/status/system/latest.json",
    ]
    if dispatch in {"care-line", "ice"}:
        paths.extend(
            [
                f"ops/status/{dispatch}/history/2026-09-21.json",
                f"ops/status/{dispatch}/latest.json",
            ]
        )
    return sorted(paths)


def _write_status(root: Path, dispatch: str, exported_at: str) -> None:
    path = root / "ops" / "status" / dispatch / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "bluefern_external_operational_status_v1",
                "dispatch": dispatch,
                "observed_date": "2026-09-21",
                "aggregate_status": "FAILED" if dispatch == "care-line" else "COMPLETE" if dispatch == "ice" else "DEGRADED",
                "last_exported_at": exported_at,
            }
        ),
        encoding="utf-8",
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, status_root: Path, incident: operator.Incident) -> None:
    monkeypatch.setattr(operator, "check_operator", lambda **_kwargs: operator.OperatorResult("2026-09-21T12:00:00Z", [], [incident]))
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
    monkeypatch.setattr(operator, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(operator, "care_expected_instances_from_task_scheduler", lambda _date: [])
    monkeypatch.setattr(
        operator,
        "_run_command",
        lambda args, **_kwargs: operator.EngineeringCommandResult(0, "status-head\n")
        if args == ["git", "rev-parse", "HEAD"]
        else operator.EngineeringCommandResult(0),
    )
    _write_status(status_root, incident.dispatch, OLD_EXPORTED_AT)


@pytest.mark.parametrize(
    ("dispatch", "status_state", "expected_state", "expected_next_action"),
    [
        ("food-line", "DEGRADED", "DEGRADED", "INVESTIGATE_FAILED_SOURCES"),
        ("care-line", "FAILED", "FAILED", "INVESTIGATE_COLLECTION"),
        ("ice", "COMPLETE", "COMPLETE", "NONE"),
    ],
)
def test_refresh_status_export_forces_exact_target_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dispatch: str,
    status_state: str,
    expected_state: str,
    expected_next_action: str,
) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    runner_root = tmp_path / "runners"
    incident = _incident(dispatch=dispatch, status_state=status_state)
    observed_kwargs: dict[str, object] = {}
    _patch_common(monkeypatch, status_root, incident)
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status(dispatch=dispatch, state=expected_state, next_action=expected_next_action))

    def fake_export_status(**kwargs) -> dict:
        observed_kwargs.update(kwargs)
        paths = _refresh_paths(dispatch)
        for path in paths:
            target_dispatch = dispatch if f"ops/status/{dispatch}/" in path else "food-line"
            payload = {
                "dispatch": target_dispatch,
                "observed_date": kwargs["date"],
                "aggregate_status": status_state,
                "last_exported_at": kwargs["exported_at"],
            }
            if path == "ops/status/system/latest.json":
                payload = {"exported_at": kwargs["exported_at"]}
            operator._write_json(kwargs["status_checkout"] / path, payload)
        return {"paths": paths}

    monkeypatch.setattr(operator, "export_status", fake_export_status)

    receipt = operator.apply_remediation(
        dispatch=dispatch,
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(runner_root, status_root),
        now=NOW,
    )

    assert observed_kwargs["force_refresh_dispatches"] == {dispatch}
    assert receipt.accepted is True
    assert receipt.outcome == "REFRESHED"
    assert receipt.validation["force_refresh_dispatches"] == [dispatch]
    assert receipt.validation["target_exported_at_before"] == OLD_EXPORTED_AT
    assert receipt.validation["target_exported_at_after"] == "2026-09-21T12:00:00Z"
    assert receipt.validation["target_timestamp_advanced"] is True
    assert receipt.validation["stale_observability_after"] is False
    assert receipt.validation["underlying_incident_preserved"] is True
    assert receipt.validation["commit_created"] is False
    assert receipt.validation["push_attempted"] is False


def test_refresh_status_export_fails_when_target_timestamp_does_not_advance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    status_root = tmp_path / "status"
    status_root.mkdir()
    runner_root = tmp_path / "runners"
    incident = _incident(dispatch="care-line", status_state="FAILED")
    _patch_common(monkeypatch, status_root, incident)
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status(dispatch="care-line", state="FAILED", next_action="INVESTIGATE_COLLECTION"))

    def fake_export_status(**kwargs) -> dict:
        paths = _refresh_paths("care-line")
        for path in paths:
            exported_at = kwargs["exported_at"] if path == "ops/status/system/latest.json" else OLD_EXPORTED_AT
            payload = {
                "dispatch": "care-line" if "care-line" in path else "food-line",
                "observed_date": kwargs["date"],
                "aggregate_status": "FAILED",
                "last_exported_at": exported_at,
            }
            if path == "ops/status/system/latest.json":
                payload = {"exported_at": exported_at}
            operator._write_json(kwargs["status_checkout"] / path, payload)
        return {"paths": paths}

    monkeypatch.setattr(operator, "export_status", fake_export_status)

    receipt = operator.apply_remediation(
        dispatch="care-line",
        incident_id=incident.incident_id,
        action="REFRESH_STATUS_EXPORT",
        confirm="REFRESH_STATUS_EXPORT",
        repo_root=repo_root,
        operator_root=repo_root / "ops" / "operator",
        config=_config(runner_root, status_root),
        now=NOW,
    )

    assert receipt.accepted is False
    assert receipt.outcome == "FAILED"
    assert receipt.reason == "status export completed but target last_exported_at did not advance"
    assert receipt.validation["stale_observability_after"] is True
    assert receipt.validation["target_timestamp_advanced"] is False
    assert receipt.validation["target_exported_at_before"] == OLD_EXPORTED_AT
    assert receipt.validation["target_exported_at_after"] == OLD_EXPORTED_AT
    assert not (receipt.accepted and receipt.outcome == "REFRESHED" and receipt.validation["stale_observability_after"])
