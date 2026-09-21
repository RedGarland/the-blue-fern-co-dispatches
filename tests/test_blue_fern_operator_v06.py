from __future__ import annotations

from pathlib import Path

from scripts import blue_fern_operator as operator
from scripts.dispatch_ops import DispatchStatus


def _incident(
    *,
    incident_id: str = "bfo-v06",
    dispatch: str = "care-line",
    classification: str = "FAILED_RUN",
    affected_date: str = "2026-09-20",
    status_state: str = "FAILED",
    recovery_action: str = "INVESTIGATE_COLLECTION",
    recovery_disposition: str = "PLAN_AVAILABLE",
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
        recovery_disposition=recovery_disposition,
        recovery_action=recovery_action,
        evidence=evidence or ["status/operational-health/care-line/2026-09-20/runs/care_line_collection.json"],
        recommended_action=recovery_action,
        affected_date=affected_date,
    )


def _status(*, date: str = "2026-09-21", state: str = "COMPLETE", next_action: str = "NONE") -> DispatchStatus:
    return DispatchStatus(
        dispatch="care-line",
        date=date,
        state=state,
        collection="COMPLETE",
        editorial="COMPLETE",
        publication="COMPLETE",
        public_state="VERIFIED",
        receipts="COMPLETE",
        recovery="HEALTHY",
        next_action=next_action,
        evidence=["status proof"],
    )


class FakeRollForwardRunner:
    def __init__(
        self,
        *,
        status: str = "?? data/dispatches/care-line/collection-runs/\n?? logs/care-line/\n",
        branch: str = "add/pages-repo-default",
        head: str = "old-sha",
        target: str = "new-sha",
        ancestor: bool = True,
        incoming: str = "scripts/blue_fern_operator.py\n",
    ) -> None:
        self.status = status
        self.branch = branch
        self.head = head
        self.target = target
        self.ancestor = ancestor
        self.incoming = incoming
        self.commands: list[list[str]] = []

    def __call__(self, args: list[str], *, cwd: Path) -> operator.EngineeringCommandResult:
        self.commands.append(args)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return operator.EngineeringCommandResult(0, self.status)
        if args == ["git", "branch", "--show-current"]:
            return operator.EngineeringCommandResult(0, f"{self.branch}\n")
        if args == ["git", "rev-parse", "HEAD"]:
            return operator.EngineeringCommandResult(0, f"{self.head}\n")
        if args == ["git", "rev-parse", "origin/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0, f"{self.target}\n")
        if args == ["git", "merge-base", "--is-ancestor", "HEAD", "origin/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0 if self.ancestor else 1)
        if args == ["git", "diff", "--name-only", "HEAD..origin/add/pages-repo-default"]:
            return operator.EngineeringCommandResult(0, self.incoming)
        if args == ["git", "fetch", "origin"]:
            return operator.EngineeringCommandResult(0)
        if args == ["git", "merge", "--ff-only", "origin/add/pages-repo-default"]:
            self.head = self.target
            return operator.EngineeringCommandResult(0)
        if args[:2] == ["python", "scripts/preflight_repo_state.py"] or args[:1] == ["python"]:
            return operator.EngineeringCommandResult(0, "ok\n")
        if args[:1] and args[0].endswith("python.exe"):
            return operator.EngineeringCommandResult(0, "ok\n")
        return operator.EngineeringCommandResult(0)


def test_care_checkout_incident_routes_to_runner_roll_forward(tmp_path: Path) -> None:
    runner = FakeRollForwardRunner()
    incident = _incident(evidence=["failure_stage=verify_checkout", "dirty checkout"])

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=runner)

    assert plan.proposed_action == "RUNNER_ROLL_FORWARD"
    assert plan.executable is True
    assert plan.approval_required is True
    assert plan.safety_checks["safe"] is True
    assert plan.safety_checks["sanctioned_runtime_untracked_preserved"] is True


def test_code_defect_routes_to_engineer_prepare_fix(tmp_path: Path) -> None:
    incident = _incident(evidence=["Traceback: TypeError in care_line_collection parser"])

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=FakeRollForwardRunner())

    assert plan.proposed_action == "ENGINEER_PREPARE_FIX"
    assert plan.executable is False
    assert "OperatorWorktrees" in " ".join(plan.expected_mutation_scope)


def test_source_failures_do_not_route_to_engineer_mode(tmp_path: Path) -> None:
    incident = _incident(
        classification="DEGRADED_RUN",
        status_state="DEGRADED",
        recovery_action="INVESTIGATE_FAILED_SOURCES",
        evidence=["failed-extractions.json", "source timeout"],
    )

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=FakeRollForwardRunner())

    assert plan.proposed_action == "INVESTIGATE_SOURCE_FAILURES"
    assert plan.executable is False


def test_stale_status_routes_to_rebuild_status(tmp_path: Path) -> None:
    incident = _incident(classification="STALE_OBSERVABILITY", recovery_action="REBUILD_STATUS", status_state="COMPLETE")

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=FakeRollForwardRunner())

    assert plan.proposed_action == "REBUILD_STATUS"
    assert plan.executable is True
    assert plan.expected_mutation_scope == [
        "ops/status/care-line/latest.json",
        "ops/status/care-line/history/2026-09-20.json",
    ]


def test_historical_failure_with_healthy_current_runner_waits(tmp_path: Path) -> None:
    incident = _incident(evidence=["historical failure receipt"])

    plan = operator.build_remediation_action_plan(
        incident,
        runner_root=tmp_path,
        current_status=_status(date="2026-09-21", state="COMPLETE", next_action="NONE"),
        runner=FakeRollForwardRunner(),
    )

    assert plan.proposed_action == "WAIT_FOR_NEXT_SCHEDULED_RUN"
    assert plan.executable is False


def test_dirty_tracked_runner_refuses_rollout(tmp_path: Path) -> None:
    incident = _incident(evidence=["failure_stage verify_checkout"])
    runner = FakeRollForwardRunner(status=" M src/bluefern_dispatches/care_line.py\n")

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=runner)

    assert plan.proposed_action == "RUNNER_ROLL_FORWARD"
    assert plan.executable is False
    assert plan.safety_checks["tracked_dirty_paths"] == ["src/bluefern_dispatches/care_line.py"]


def test_divergent_branch_refuses_rollout(tmp_path: Path) -> None:
    incident = _incident(evidence=["failure_stage verify_checkout"])
    runner = FakeRollForwardRunner(ancestor=False)

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=runner)

    assert plan.proposed_action == "RUNNER_ROLL_FORWARD"
    assert plan.executable is False
    assert plan.safety_checks["current_head_is_ancestor"] is False


def test_untracked_collision_refuses_rollout(tmp_path: Path) -> None:
    incident = _incident(evidence=["failure_stage verify_checkout"])
    runner = FakeRollForwardRunner(status="?? scripts/\n", incoming="scripts/blue_fern_operator.py\n")

    plan = operator.build_remediation_action_plan(incident, runner_root=tmp_path, runner=runner)

    assert plan.proposed_action == "RUNNER_ROLL_FORWARD"
    assert plan.executable is False
    assert plan.safety_checks["untracked_incoming_overlaps"] == ["scripts/blue_fern_operator.py"]


def test_apply_runner_roll_forward_mutation_scope_is_exact(tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    operator_root = tmp_path / "ops/operator"
    runner_root.mkdir()
    plan = operator.build_remediation_action_plan(
        _incident(evidence=["failure_stage verify_checkout"]),
        runner_root=runner_root,
        runner=FakeRollForwardRunner(),
    )
    fake = FakeRollForwardRunner()

    receipt = operator._apply_runner_roll_forward(plan, runner_root=runner_root, operator_root=operator_root, runner=fake)

    assert receipt.accepted is True
    assert receipt.before_head == "old-sha"
    assert receipt.after_head == "new-sha"
    assert receipt.changed_paths == ["HEAD"]
    assert receipt.expected_mutation_scope == ["production runner Git HEAD only", "Python __pycache__ from validation if needed"]
    assert receipt.receipt_path
    assert Path(receipt.receipt_path).is_file()


def test_approval_token_required_before_apply(monkeypatch, tmp_path: Path) -> None:
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("check_operator should not run without confirmation")

    monkeypatch.setattr(operator, "check_operator", fail_if_called)

    receipt = operator.apply_remediation(
        dispatch="care-line",
        incident_id="bfo-v06",
        action="RUNNER_ROLL_FORWARD",
        confirm="YES",
        operator_root=tmp_path / "ops/operator",
    )

    assert receipt.accepted is False
    assert "RUNNER_ROLL_FORWARD" in receipt.reason
    assert called is False


def test_close_non_code_incident_route_for_existing_checkout_work_item(tmp_path: Path) -> None:
    operator_root = tmp_path / "ops/operator"
    item = operator.EngineeringWorkItem(
        work_id="bfoe-v06",
        incident_id="bfo-v06",
        dispatch="care-line",
        classification="FAILED_RUN",
        state="BLOCKED",
        branch="operator/care-line/bfo-v06",
        worktree=str(tmp_path / "worktree"),
        evidence=["failure_stage verify_checkout"],
        allowed_paths=[],
        tests_required=[],
        merge_allowed=False,
    )
    operator._save_engineering_work_item(operator_root, item)

    plan = operator.build_remediation_action_plan(
        _incident(evidence=["failure_stage verify_checkout"]),
        runner_root=tmp_path,
        operator_root=operator_root,
        runner=FakeRollForwardRunner(),
    )

    assert plan.proposed_action == "CLOSE_NON_CODE_INCIDENT"
    assert plan.approval_required is True


def test_apply_does_not_publish_replay_scheduler_or_editorial_mutate(tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    operator_root = tmp_path / "ops/operator"
    runner_root.mkdir()
    fake = FakeRollForwardRunner()
    plan = operator.build_remediation_action_plan(
        _incident(evidence=["failure_stage verify_checkout"]),
        runner_root=runner_root,
        runner=fake,
    )

    operator._apply_runner_roll_forward(plan, runner_root=runner_root, operator_root=operator_root, runner=fake)

    flattened = [" ".join(command).lower() for command in fake.commands]
    assert not any("publish" in command for command in flattened)
    assert not any("collection" in command and "run" in command for command in flattened)
    assert not any("scheduledtask" in command for command in flattened)
    assert not any("review" in command and "write" in command for command in flattened)
