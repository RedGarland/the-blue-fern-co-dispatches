from __future__ import annotations

import json
from pathlib import Path

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


NOW = operator._parse_time("2026-09-26T12:00:00Z")


def _policy(**modes: str) -> operator.RemediationPolicy:
    return operator.RemediationPolicy(modes=modes)


def _status(*, dispatch: str = "care-line", state: str = "FAILED", evidence: list[str] | None = None) -> DispatchStatus:
    return DispatchStatus(
        dispatch=dispatch,
        date="2026-09-26",
        state=state,
        collection=state,
        editorial="COMPLETE",
        publication="SAFE_NO_OP",
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery="FAILED" if state == "FAILED" else "HEALTHY",
        next_action="INVESTIGATE_FAILED_SOURCES" if state == "FAILED" else "NONE",
        evidence=evidence or [f"status/operational-health/{dispatch}/2026-09-26/runs/receipt.json"],
        details={"task_statuses": {"collection": state}},
    )


def _incident(
    *,
    dispatch: str = "care-line",
    evidence: list[str] | None = None,
    remediation: dict | None = None,
) -> operator.Incident:
    return operator.Incident(
        incident_id=f"bfo-{dispatch}",
        incident_key=f"{dispatch}|FAILED_RUN|2026-09-26|collection",
        dispatch=dispatch,
        detected_at="2026-09-26T11:00:00Z",
        updated_at="2026-09-26T12:00:00Z",
        state=operator.IncidentState.OPEN.value,
        classification=operator.Classification.FAILED_RUN.value,
        status_state="FAILED",
        recovery_disposition="PLAN_AVAILABLE",
        recovery_action="INVESTIGATE_FAILED_SOURCES",
        evidence=evidence or [f"status/operational-health/{dispatch}/2026-09-26/runs/receipt.json"],
        recommended_action="INVESTIGATE_FAILED_SOURCES",
        affected_date="2026-09-26",
        remediation=remediation or {},
    )


def _source_failure(
    source_id: str = "feed-a",
    *,
    reason: str = "TimeoutError: timed out",
    failure_class: str = "TimeoutError",
    status_code: int | None = None,
    retry_after: object | None = None,
    run_id: str = "collection-run-1",
) -> dict:
    row = {
        "source_id": source_id,
        "source_name": source_id,
        "failure_class": failure_class,
        "failure_reason": reason,
        "run_id": run_id,
    }
    if status_code is not None:
        row["status_code"] = status_code
    if retry_after is not None:
        row["retry_after"] = retry_after
    return row


def _runner_root(tmp_path: Path, *, dispatch: str = "care-line", failures: list[dict] | None = None) -> Path:
    root = tmp_path / "runner" / dispatch
    receipt = root / "status" / "operational-health" / dispatch / "2026-09-26" / "runs" / "receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "task_key": {"care-line": "care_line_collection", "food-line": "food_line_source_watch", "ice": "ice_monitor", "gaza": "gaza_publication"}.get(dispatch, "collection"),
                "status": "FAILED",
                "edition_date": "2026-09-26",
                "pipeline_run_id": "collection-run-1",
                "failed_source_count": len(failures or []),
                "failed_source_diagnostics": failures or [_source_failure()],
            }
        ),
        encoding="utf-8",
    )
    return root


def _plan(
    tmp_path: Path,
    *,
    dispatch: str = "care-line",
    failures: list[dict] | None = None,
    remediation: dict | None = None,
    policy: operator.RemediationPolicy | None = None,
) -> operator.RemediationActionPlan:
    root = _runner_root(tmp_path, dispatch=dispatch, failures=failures)
    incident = _incident(dispatch=dispatch, remediation=remediation)
    status = _status(dispatch=dispatch)
    return operator.build_remediation_action_plan(
        incident,
        runner_root=root,
        operator_root=tmp_path / "ops" / "operator",
        current_status=status,
        status_root=tmp_path / "status",
        policy=policy or _policy(SOURCE_TRANSIENT_FETCH_RETRY="automatic"),
    )


def test_timeout_source_record_is_transient() -> None:
    assert operator.classify_source_failure_record(_source_failure()) == operator.RootCauseClassification.SOURCE_TRANSIENT.value


def test_http_503_source_record_is_transient() -> None:
    row = _source_failure(reason="HTTPError: 503 Service Unavailable", failure_class="HTTPError", status_code=503)
    assert operator.classify_source_failure_record(row) == operator.RootCauseClassification.SOURCE_TRANSIENT.value


def test_http_429_honors_retry_after_in_state(tmp_path: Path) -> None:
    row = _source_failure(reason="HTTPError: 429 Too Many Requests", failure_class="HTTPError", status_code=429, retry_after=1800)
    plan = _plan(tmp_path, failures=[row])
    state = plan.safety_checks["source_retry_state"]
    assert state["retry_after_minutes"] == 30
    assert state["minimum_backoff_minutes"] == 30


def test_http_403_is_external_restriction() -> None:
    row = _source_failure(reason="HTTPError: 403 Forbidden", failure_class="HTTPError", status_code=403)
    assert operator.classify_source_failure_record(row) == operator.RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value


def test_cloudflare_challenge_is_external_restriction() -> None:
    row = _source_failure(reason="Cloudflare access challenge", failure_class="HTTPError", status_code=403)
    assert operator.classify_source_failure_record(row) == operator.RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value


def test_parser_failure_is_internal() -> None:
    row = _source_failure(reason="ParseError: malformed XML", failure_class="ParseError")
    assert operator.classify_source_failure_record(row) == operator.RootCauseClassification.INTERNAL_FAILURE.value


def test_invalid_content_is_internal() -> None:
    row = _source_failure(reason="invalid content schema mismatch", failure_class="ValueError")
    assert operator.classify_source_failure_record(row) == operator.RootCauseClassification.INTERNAL_FAILURE.value


def test_http_400_is_not_retried() -> None:
    row = _source_failure(reason="HTTPError: 400 Bad Request", failure_class="HTTPError", status_code=400)
    assert operator.classify_source_failure_record(row) == operator.RootCauseClassification.SOURCE_EXTERNAL_RESTRICTION.value


def test_care_source_transient_plans_fail_closed_source_retry(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.proposed_action == operator.SOURCE_TRANSIENT_RETRY_ACTION
    assert plan.executable is False
    assert plan.safety_checks["handler"]["enabled"] is False
    assert plan.safety_checks["public_side_effects"] is False


def test_gaza_source_transient_is_excluded(tmp_path: Path) -> None:
    plan = _plan(tmp_path, dispatch="gaza")
    assert plan.proposed_action == "WAIT_FOR_NEXT_SCHEDULED_RUN"
    assert plan.safety_checks["handler"]["excluded"] is True


def test_source_retry_key_includes_source_run_date_and_class(tmp_path: Path) -> None:
    plan = _plan(tmp_path, failures=[_source_failure("feed-x", run_id="logical-run")])
    identity = plan.safety_checks["source_retry_state"]["logical_run_identity"]
    assert identity == {
        "dispatch": "care-line",
        "date": "2026-09-26",
        "source_id": "feed-x",
        "run_id": "logical-run",
        "failure_class": "TimeoutError",
    }


def test_restart_preserves_existing_first_failure_and_attempt_count(tmp_path: Path) -> None:
    existing = {
        "source_retry_state": {
            "source_retry_key": "2026-09-26|feed-a|collection-run-1|TimeoutError",
            "first_failure_at": "2026-09-26T10:00:00Z",
            "attempt_count": 1,
            "last_attempted_at": "2026-09-26T11:45:00Z",
        }
    }
    plan = _plan(tmp_path, remediation=existing)
    state = plan.safety_checks["source_retry_state"]
    assert state["first_failure_at"] == "2026-09-26T10:00:00Z"
    assert state["attempt_count"] == 1


def test_backoff_prevents_tight_source_retry_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(operator, "_utc_now", lambda: NOW)
    remediation = {
        "source_retry_state": {
            "attempt_count": 1,
            "last_attempted_at": "2026-09-26T11:45:00Z",
        }
    }
    plan = _plan(tmp_path, remediation=remediation)
    assert plan.safety_checks["source_retry_state"]["backoff_elapsed"] is False
    assert plan.executable is False


def test_source_retry_cap_is_terminal(tmp_path: Path) -> None:
    remediation = {"source_retry_state": {"attempt_count": 2, "last_attempted_at": "2026-09-26T11:00:00Z"}}
    plan = _plan(tmp_path, remediation=remediation)
    assert plan.safety_checks["source_retry_state"]["terminal_classification"] == "retry_limit_reached"
    assert plan.executable is False


def test_auto_source_retry_disabled_persists_state(tmp_path: Path) -> None:
    root = _runner_root(tmp_path)
    incident = _incident()
    status = _status()
    incident, _status_after, _plan_after, remediation, recovered = operator._auto_source_transient_retry_if_allowed(
        incident=incident,
        status=status,
        dispatch_root=root,
        repo_root=tmp_path,
        operator_root=tmp_path / "ops" / "operator",
        config=operator.OperatorConfig(120, {"care-line": operator.DispatchConfig("care-line", root)}, status_root=tmp_path / "status"),
        policy=_policy(SOURCE_TRANSIENT_FETCH_RETRY="automatic"),
        checked_at="2026-09-26T12:00:00Z",
        allow_automatic_remediation=False,
    )
    assert recovered is False
    assert incident.remediation["action"] == operator.SOURCE_TRANSIENT_RETRY_ACTION
    assert remediation["source_retry_state"]["source_id"] == "feed-a"


def test_policy_disabled_keeps_source_retry_recommendation_only(tmp_path: Path) -> None:
    root = _runner_root(tmp_path)
    incident, _status_after, _plan_after, remediation, _recovered = operator._auto_source_transient_retry_if_allowed(
        incident=_incident(),
        status=_status(),
        dispatch_root=root,
        repo_root=tmp_path,
        operator_root=tmp_path / "ops" / "operator",
        config=operator.OperatorConfig(120, {"care-line": operator.DispatchConfig("care-line", root)}, status_root=tmp_path / "status"),
        policy=_policy(SOURCE_TRANSIENT_FETCH_RETRY="recommend"),
        checked_at="2026-09-26T12:00:00Z",
        allow_automatic_remediation=True,
    )
    assert incident.remediation["attempted"] is False
    assert "policy mode is recommend" in remediation["reason"]


def test_isolated_source_retry_tracking_suppresses_notification() -> None:
    incident = _incident(remediation={"action": operator.SOURCE_TRANSIENT_RETRY_ACTION, "source_retry_state": {"source_id": "feed-a"}})
    result = operator.OperatorResult(checked_at="2026-09-26T12:00:00Z", dispatches=[], incidents=[incident])
    event = operator._build_notification_event(result, {}, _policy(SOURCE_TRANSIENT_FETCH_RETRY="automatic"))
    assert event.notification_required is False


def test_failed_source_retry_notifies() -> None:
    incident = _incident(
        remediation={
            "action": operator.SOURCE_TRANSIENT_RETRY_ACTION,
            "attempted": True,
            "outcome": "FAILED",
            "source_retry_state": {"source_id": "feed-a"},
        }
    )
    result = operator.OperatorResult(checked_at="2026-09-26T12:00:00Z", dispatches=[], incidents=[incident])
    event = operator._build_notification_event(result, {}, _policy(SOURCE_TRANSIENT_FETCH_RETRY="automatic"))
    assert operator.NotificationReason.REMEDIATION_FAILED.value in event.reasons


def test_evidence_summary_bounds_source_records() -> None:
    payload = {
        "failed_source_diagnostics": [_source_failure(f"feed-{index}") for index in range(30)],
        "failed_source_count": 30,
    }
    summary = operator._summarize_evidence_payload(payload)
    assert len(summary["source_failure_records"]) == 25
    assert summary["failed_source_count"] == 30


def test_apply_source_retry_receipt_refuses_without_handler(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    receipt = operator._apply_source_transient_fetch_retry(plan, operator_root=tmp_path / "ops" / "operator", now=NOW)
    assert receipt.accepted is False
    assert receipt.outcome == "REFUSED"
    assert receipt.validation["publication_attempted"] is False


def test_mixed_external_and_transient_tracks_transient_candidate(tmp_path: Path) -> None:
    external = _source_failure("blocked", reason="HTTPError: 403 Forbidden", failure_class="HTTPError", status_code=403)
    transient = _source_failure("retry-me", reason="HTTPError: 502 Bad Gateway", failure_class="HTTPError", status_code=502)
    plan = _plan(tmp_path, failures=[external, transient])
    assert plan.proposed_action == operator.SOURCE_TRANSIENT_RETRY_ACTION
    assert plan.safety_checks["source_retry_state"]["source_id"] == "retry-me"
