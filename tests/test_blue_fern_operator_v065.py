from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


NOW = operator._parse_time("2026-09-26T12:00:00Z")


class FakeRunner:
    def __init__(
        self,
        *,
        head: str = "protected-head",
        tracked_dirty: str = "",
        dns_ok: bool = True,
        git_probe_ok: bool = True,
        preflight_ok: bool = True,
        wrapper_ok: bool = True,
    ) -> None:
        self.head = head
        self.tracked_dirty = tracked_dirty
        self.dns_ok = dns_ok
        self.git_probe_ok = git_probe_ok
        self.preflight_ok = preflight_ok
        self.wrapper_ok = wrapper_ok
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> operator.EngineeringCommandResult:
        self.commands.append([str(arg) for arg in args])
        if args[:2] == ["git", "status"]:
            return operator.EngineeringCommandResult(0, self.tracked_dirty)
        if args == ["git", "branch", "--show-current"]:
            return operator.EngineeringCommandResult(0, "add/pages-repo-default\n")
        if args == ["git", "rev-parse", "HEAD"]:
            return operator.EngineeringCommandResult(0, f"{self.head}\n")
        if args[:3] == ["git", "ls-remote", "--heads"]:
            return operator.EngineeringCommandResult(0 if self.git_probe_ok else 128, "ok\n" if self.git_probe_ok else "", "" if self.git_probe_ok else "network down")
        if args and str(args[0]).endswith("python.exe"):
            return operator.EngineeringCommandResult(0 if self.preflight_ok else 1, "preflight ok\n" if self.preflight_ok else "", "" if self.preflight_ok else "preflight failed")
        if args[:3] == ["powershell", "-NoProfile", "-NonInteractive"] and "-Command" in args:
            return operator.EngineeringCommandResult(0 if self.dns_ok else 1, "" if self.dns_ok else "", "" if self.dns_ok else "dns failure")
        if args[:3] == ["powershell", "-NoProfile", "-NonInteractive"] and "-File" in args:
            return operator.EngineeringCommandResult(0 if self.wrapper_ok else 1, "wrapper ok\n" if self.wrapper_ok else "", "" if self.wrapper_ok else "wrapper failed")
        return operator.EngineeringCommandResult(0)


def _policy(**modes: str) -> operator.RemediationPolicy:
    return operator.RemediationPolicy(modes=modes)


def _status(
    *,
    dispatch: str = "food-line",
    state: str = "FAILED",
    next_action: str = "INVESTIGATE_FAILED_SOURCES",
    evidence: list[str] | None = None,
) -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date="2026-09-26",
        state=state,
        collection=state,
        editorial="COMPLETE",
        publication="SAFE_NO_OP",
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery="HEALTHY" if state in {"COMPLETE", "DEGRADED"} else "FAILED",
        next_action=next_action,
        evidence=evidence or ["status/operational-health/food-line/2026-09-26/runs/receipt.json"],
        details={"task_statuses": {"collection": state}},
    )


def _incident(
    *,
    dispatch: str = "food-line",
    classification: str = "FAILED_RUN",
    recovery_action: str = "INVESTIGATE_FAILED_SOURCES",
    evidence: list[str] | None = None,
    remediation: dict | None = None,
) -> operator.Incident:
    return operator.Incident(
        incident_id=f"bfo-{dispatch}",
        incident_key=f"{dispatch}|{classification}|2026-09-26|collection",
        dispatch=dispatch,
        detected_at="2026-09-26T12:00:00Z",
        updated_at="2026-09-26T12:00:00Z",
        state=operator.IncidentState.OPEN.value,
        classification=classification,
        status_state="FAILED",
        recovery_disposition="PLAN_AVAILABLE",
        recovery_action=recovery_action,
        evidence=evidence or ["status/operational-health/food-line/2026-09-26/runs/receipt.json"],
        recommended_action=recovery_action,
        affected_date="2026-09-26",
        remediation=remediation or {},
    )


def _runner_root(tmp_path: Path, dispatch: str = "food-line", payload: dict | None = None) -> Path:
    root = tmp_path / "runner" / dispatch
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (root / "scripts" / "windows").mkdir(parents=True)
    (root / "scripts" / "preflight_repo_state.py").write_text("", encoding="utf-8")
    (root / "scripts" / "doctor.py").write_text("", encoding="utf-8")
    for wrapper in ("run_food_line_current_intake.ps1", "run_care_line_national_collection.ps1", "run_ice_monitor.ps1"):
        (root / "scripts" / "windows" / wrapper).write_text("", encoding="utf-8")
    receipt = root / "status" / "operational-health" / dispatch / "2026-09-26" / "runs" / "receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(payload or _dns_payload(dispatch)), encoding="utf-8")
    return root


def _dns_payload(dispatch: str = "food-line", task_key: str | None = None) -> dict:
    return {
        "task_key": task_key or {"food-line": "food_line_source_watch", "care-line": "care_line_collection", "ice": "ice_monitor"}.get(dispatch, "gaza_publication"),
        "status": "FAILED",
        "failure_stage": "pre_run_network",
        "wrapper_exception_message": "Temporary failure in name resolution while reaching github.com",
        "exit_code": 1,
    }


def _plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dispatch: str = "food-line",
    payload: dict | None = None,
    runner: FakeRunner | None = None,
    remediation: dict | None = None,
) -> tuple[operator.RemediationActionPlan, FakeRunner, Path, operator.Incident, DispatchStatus]:
    runner = runner or FakeRunner()
    runner_root = _runner_root(tmp_path, dispatch, payload)
    monkeypatch.setattr(operator, "_git_head", lambda _root: runner.head)
    incident = _incident(dispatch=dispatch, evidence=[f"status/operational-health/{dispatch}/2026-09-26/runs/receipt.json"], remediation=remediation)
    status = _status(dispatch=dispatch, evidence=incident.evidence)
    plan = operator.build_remediation_action_plan(
        incident,
        runner_root=runner_root,
        operator_root=tmp_path / "repo" / "ops" / "operator",
        current_status=status,
        status_root=tmp_path / "status",
        policy=_policy(TRANSIENT_NETWORK_TASK_RETRY="automatic"),
        runner=runner,
    )
    return plan, runner, runner_root, incident, status


def test_dns_failure_classifies_as_transient_network() -> None:
    incident = _incident(evidence=["temporary failure in name resolution github.com"])
    assert operator.classify_incident_root_cause(incident) == operator.RootCauseClassification.TRANSIENT_NETWORK.value


def test_publisher_403_is_source_external_not_transient_network() -> None:
    incident = _incident(evidence=["publisher feed returned HTTP 403 forbidden"])
    assert operator.classify_incident_root_cause(incident) == operator.RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value


def test_one_source_timeout_is_source_transient_only() -> None:
    incident = _incident(evidence=["one feed timeout 503"])
    dispatch = operator.DispatchResult("food-line", "FAILED", "FAILED_RUN", "INVESTIGATE_FAILED_SOURCES", "FAILED", "2026-09-26", "PLAN_AVAILABLE", "INVESTIGATE_FAILED_SOURCES", incident.incident_id, [], {"source_failure_summary": {"failed_source_count": 1}})
    assert operator.classify_incident_root_cause(incident, dispatch) == operator.RootCauseClassification.SOURCE_TRANSIENT.value


def test_internal_parser_failure_is_not_network_retry() -> None:
    incident = _incident(evidence=["Traceback TypeError in parser"])
    assert operator.classify_incident_root_cause(incident) == operator.RootCauseClassification.INTERNAL_FAILURE.value


def test_food_dns_recovery_plans_registered_non_public_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch)
    assert plan.proposed_action == operator.TRANSIENT_NETWORK_RETRY_ACTION
    assert plan.executable is True
    assert plan.safety_checks["handler"]["wrapper"] == "scripts/windows/run_food_line_current_intake.ps1"
    assert plan.safety_checks["publication_attempted"] is False


def test_dns_failure_persists_blocks_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, runner=FakeRunner(dns_ok=False))
    assert plan.proposed_action == operator.TRANSIENT_NETWORK_RETRY_ACTION
    assert plan.executable is False
    assert plan.safety_checks["connectivity_proof"]["ok"] is False


def test_runner_source_drift_stops_before_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, runner=FakeRunner(tracked_dirty=" M scripts/blue_fern_operator.py\n"))
    assert plan.executable is False
    assert plan.safety_checks["runner_safety"]["tracked_dirty_paths"] == ["scripts/blue_fern_operator.py"]


def test_persistent_403_never_routes_to_network_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"task_key": "food_line_source_watch", "status": "FAILED", "error": "HTTP 403 forbidden Cloudflare challenge"}
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, payload=payload)
    assert plan.proposed_action != operator.TRANSIENT_NETWORK_RETRY_ACTION


def test_single_source_timeout_requires_source_specific_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"task_key": "food_line_source_watch", "status": "FAILED", "failed_source_count": 1, "error": "one feed timeout 503"}
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, payload=payload)
    assert plan.proposed_action == operator.SOURCE_TRANSIENT_RETRY_ACTION
    assert plan.executable is False
    assert plan.safety_checks["handler"]["enabled"] is False


def test_internal_failure_does_not_route_to_network_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"task_key": "food_line_source_watch", "status": "FAILED", "error": "Traceback: TypeError in parser"}
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, payload=payload)
    assert plan.proposed_action == "ENGINEER_PREPARE_FIX"


def test_gaza_publication_is_not_replayed_by_transient_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, dispatch="gaza", payload=_dns_payload("gaza"))
    assert plan.proposed_action != operator.TRANSIENT_NETWORK_RETRY_ACTION
    assert plan.proposed_action not in {"PUBLISH_APPROVED_RELEASE", "PUBLISH_NO_UPDATE", "REPLAY_COLLECTION"}


@pytest.mark.parametrize(
    ("dispatch", "wrapper"),
    [
        ("care-line", "scripts/windows/run_care_line_national_collection.ps1"),
        ("ice", "scripts/windows/run_ice_monitor.ps1"),
    ],
)
def test_supported_dispatch_handlers_are_non_public(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatch: str, wrapper: str) -> None:
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, dispatch=dispatch, payload=_dns_payload(dispatch))
    assert plan.proposed_action == operator.TRANSIENT_NETWORK_RETRY_ACTION
    assert plan.safety_checks["handler"]["wrapper"] == wrapper
    assert plan.safety_checks["public_side_effects"] is False


def test_retry_cap_is_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remediation = operator._remediation_payload(action=operator.TRANSIENT_NETWORK_RETRY_ACTION, attempted=True, attempt_count=2, attempted_at="2026-09-26T11:00:00Z")
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, remediation=remediation)
    assert plan.executable is False
    assert plan.safety_checks["retry_state"]["retry_budget_remaining"] is False


def test_backoff_prevents_tight_retry_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remediation = operator._remediation_payload(action=operator.TRANSIENT_NETWORK_RETRY_ACTION, attempted=True, attempt_count=1, attempted_at="2026-09-26T11:45:00Z")
    monkeypatch.setattr(operator, "_utc_now", lambda: NOW)
    plan, _runner, _root, _incident_row, _status_row = _plan(tmp_path, monkeypatch, remediation=remediation)
    assert plan.executable is False
    assert plan.safety_checks["retry_state"]["backoff_elapsed"] is False


def test_apply_success_runs_wrapper_and_refreshes_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, runner, runner_root, _incident_row, failed_status = _plan(tmp_path, monkeypatch)
    complete = _status(state="COMPLETE", next_action="NONE", evidence=["terminal.json"])
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: complete)
    monkeypatch.setattr(
        operator,
        "_apply_refresh_status_export",
        lambda *args, **kwargs: operator.RemediationReceipt(
            dispatch=plan.dispatch,
            incident_id=plan.incident_id,
            action="REFRESH_STATUS_EXPORT",
            accepted=True,
            outcome="REFRESHED",
            reason="refreshed",
            started_at="2026-09-26T12:00:00Z",
            completed_at="2026-09-26T12:00:00Z",
            validation={"stale_observability_after": False, "underlying_status_after": complete.to_json_payload()},
        ),
    )
    receipt = operator._apply_transient_network_task_retry(
        plan,
        repo_root=tmp_path,
        runner_root=runner_root,
        operator_root=tmp_path / "repo" / "ops" / "operator",
        config=operator.OperatorConfig(120, {"food-line": operator.DispatchConfig("food-line", runner_root)}, status_root=tmp_path / "status"),
        current_status=failed_status,
        runner=runner,
        now=NOW,
    )
    assert receipt.accepted is True
    assert receipt.validation["terminal_proof"] is True
    assert any("run_food_line_current_intake.ps1" in " ".join(command) for command in runner.commands)
    assert receipt.validation["status_refresh_receipt"]["accepted"] is True


def test_existing_terminal_receipt_does_not_duplicate_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, runner, runner_root, _incident_row, _failed_status = _plan(tmp_path, monkeypatch)
    complete = _status(state="COMPLETE", next_action="NONE", evidence=["terminal.json"])
    receipt = operator._apply_transient_network_task_retry(
        plan,
        repo_root=tmp_path,
        runner_root=runner_root,
        operator_root=tmp_path / "repo" / "ops" / "operator",
        config=operator.OperatorConfig(120, {"food-line": operator.DispatchConfig("food-line", runner_root)}, status_root=tmp_path / "status"),
        current_status=complete,
        runner=runner,
        now=NOW,
    )
    assert receipt.accepted is True
    assert receipt.outcome == "NO_ACTION"
    assert not any("run_food_line_current_intake.ps1" in " ".join(command) for command in runner.commands)


def test_healthy_scheduled_run_has_no_transient_remediation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner_root = _runner_root(tmp_path)
    status_root = tmp_path / "status"
    status_root.mkdir()
    latest = status_root / "ops" / "status" / "food-line" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(json.dumps({"dispatch": "food-line", "observed_date": "2026-09-26", "last_exported_at": "2026-09-26T12:00:00Z"}), encoding="utf-8")
    monkeypatch.setattr(operator, "build_status", lambda *_args, **_kwargs: _status(state="COMPLETE", next_action="NONE"))
    result = operator.check_operator(
        repo_root=tmp_path,
        operator_root=tmp_path / "ops" / "operator",
        config=operator.OperatorConfig(120, {"food-line": operator.DispatchConfig("food-line", runner_root), "care-line": operator.DispatchConfig("care-line", runner_root, False), "gaza": operator.DispatchConfig("gaza", runner_root, False), "ice": operator.DispatchConfig("ice", runner_root, False)}, status_root=status_root),
        now=NOW,
        write_ledger=False,
    )
    assert result.incidents == []
    assert all(row.remediation == {} for row in result.dispatches)
