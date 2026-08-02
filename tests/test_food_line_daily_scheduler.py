from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import food_line_daily_scheduler as scheduler


DATE = "2026-08-01"
RUN_ID = "food-line-scheduled-test"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "scripts").mkdir()
    return root


def _state(
    status: str = "completed",
    *,
    export_status: str = "success",
    resume_count: int = 0,
    resumable: bool = True,
) -> dict:
    completed = 3 if status in scheduler.QUALIFYING_COLLECTION_STATUSES else 2
    return {
        "schema_version": scheduler.RUN_STATE_SCHEMA,
        "run_id": RUN_ID,
        "edition_date": DATE,
        "status": status,
        "resumable": resumable,
        "resume_count": resume_count,
        "partitions_total": 3,
        "partitions_completed": completed,
        "queries_total": 57,
        "queries_completed": 57 if completed == 3 else 40,
        "queries_failed": 0,
        "queries_timed_out": 0,
        "candidates_discovered": 0,
        "query_plan_sha256": "plan-sha",
        "final_error": "",
        "options": {
            "required_coverage_threshold": 0.9,
            "direct_source_coverage_threshold": 0.75,
        },
        "coverage": {
            "required_success_ratio": 1.0 if completed == 3 else 0.70,
            "direct_success_ratio": 1.0,
        },
        "agent_export": {"status": export_status, "path": None, "sha256": "export-sha"},
    }


def _write_run(root: Path, state: dict, *, resume_attempted: bool = False) -> scheduler.Layout:
    layout = scheduler.Layout(root)
    state_path = layout.run_dir(DATE, RUN_ID) / "run-state.json"
    scheduler.atomic_write_json(state_path, state)
    scheduler.atomic_write_json(
        layout.run_record(DATE),
        {
            "schema_version": scheduler.RUN_RECORD_SCHEMA,
            "edition_date": DATE,
            "run_id": RUN_ID,
            "source_commit": "test-mode-source-commit",
            "source_branch": scheduler.PRODUCTION_BRANCH,
            "run_state_path": str(state_path),
            "resume_attempted": resume_attempted,
        },
    )
    return layout


def _resume_args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=str(root),
        python=str(Path(__file__)),
        edition_date=DATE,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
    )


def _source_args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=str(root),
        python=str(Path(__file__)),
        edition_date=DATE,
        run_id=RUN_ID,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
    )


def _intake_args(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repo_root=str(root),
        python=str(Path(__file__)),
        edition_date=DATE,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
        lock_wait_seconds=0,
        lock_poll_seconds=0.01,
    )


def _completed_intake_report(root: Path, *, blocked: bool = False, side_effect: bool = False) -> None:
    report = {
        "schema_version": "food_line_current_intake_report_v1",
        "status": "success",
        "errors": [],
        "discovered_file_count": 0 if blocked else 1,
        "accepted_file_count": 0 if blocked else 1,
        "import_count": 0 if blocked else 1,
        "queue": {"item_count": 0 if blocked else 1},
        "proposal": {
            "draft_status": "blocked_no_reviewable_current_signals" if blocked else "draft_pending_editorial_review",
            "markdown_path": str(root / "data/dispatches/food-line/review/proposed-editions" / f"{DATE}.md"),
        },
        "publication_side_effects": {
            "public_output": side_effect,
            "pages": False,
            "bluesky": False,
            "audio": False,
            "maps": False,
            "schedule": False,
        },
    }
    scheduler.atomic_write_json(
        root / "data" / "dispatches" / "food-line" / "review" / "reports" / DATE / "current-intake.json",
        report,
    )


def test_completed_collection_qualifies() -> None:
    assert scheduler.collection_qualifies(_state())


def test_completed_with_exclusions_qualifies() -> None:
    assert scheduler.collection_qualifies(_state("completed_with_exclusions", export_status="success_with_exclusions"))


def test_qualifying_empty_collection_qualifies() -> None:
    assert scheduler.collection_qualifies(_state(export_status="no_exportable_findings"))


def test_source_watch_records_qualifying_empty_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    layout = scheduler.Layout(root)

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        run_dir = layout.run_dir(DATE, RUN_ID)
        scheduler.atomic_write_json(run_dir / "run-state.json", _state(export_status="no_exportable_findings"))
        scheduler.atomic_write_json(run_dir / "query-plan.json", {"configuration_sha256": "config-sha"})
        return subprocess.CompletedProcess(arguments, 0, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_source_watch(_source_args(root)) == 0
    record = scheduler.read_json(layout.run_record(DATE))
    receipt = scheduler.read_json(Path(record["source_receipt_path"]))
    assert receipt["source_commit"] == "test-mode-source-commit"
    assert receipt["export_status"] == "no_exportable_findings"
    assert receipt["exit_code"] == 0


def test_source_watch_preserves_nonqualifying_command_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    layout = scheduler.Layout(root)

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        run_dir = layout.run_dir(DATE, RUN_ID)
        scheduler.atomic_write_json(run_dir / "run-state.json", _state("partial", export_status="blocked_incomplete_collection"))
        scheduler.atomic_write_json(run_dir / "query-plan.json", {"configuration_sha256": "config-sha"})
        return subprocess.CompletedProcess(arguments, 2, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_source_watch(_source_args(root)) == 2
    assert list(layout.attention_dir(DATE).glob("*-source_watch_nonqualifying.json"))


def test_source_watch_fails_closed_on_surviving_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    layout = scheduler.Layout(root)

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        run_dir = layout.run_dir(DATE, RUN_ID)
        scheduler.atomic_write_json(run_dir / "run-state.json", _state())
        scheduler.atomic_write_json(run_dir / "query-plan.json", {"configuration_sha256": "config-sha"})
        return subprocess.CompletedProcess(arguments, 0, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda run_dir: [4242])
    assert scheduler.run_source_watch(_source_args(root)) == 10
    reports = list(layout.attention_dir(DATE).glob("*-source_watch_failed.json"))
    assert reports
    assert "surviving worker" in scheduler.read_json(reports[0])["message"]


@pytest.mark.parametrize("status", ["planned", "running", "partial", "timed_out", "cancelled", "failed"])
def test_nonterminal_or_failed_collection_does_not_qualify(status: str) -> None:
    assert not scheduler.collection_qualifies(_state(status, export_status="blocked_incomplete_collection"))


def test_existing_lock_blocks_overlap_and_writes_attention(tmp_path: Path) -> None:
    layout = scheduler.Layout(_root(tmp_path))
    layout.lock_dir.mkdir(parents=True)
    with pytest.raises(scheduler.SchedulerError, match="lock exists"):
        with scheduler.source_lock(layout, DATE, "test"):
            pass
    assert list(layout.attention_dir(DATE).glob("*-overlapping_run.json"))


def test_stale_lock_is_reported(tmp_path: Path) -> None:
    layout = scheduler.Layout(_root(tmp_path))
    layout.lock_dir.mkdir(parents=True)
    old = scheduler.time.time() - 3600
    scheduler.os.utime(layout.lock_dir, (old, old))
    with pytest.raises(scheduler.SchedulerError, match="stale_lock"):
        with scheduler.source_lock(layout, DATE, "test", stale_minutes=1):
            pass
    assert list(layout.attention_dir(DATE).glob("*-stale_lock.json"))


def test_missing_run_record_blocks_intake(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert scheduler.run_intake(_intake_args(root)) == 10
    assert list(scheduler.Layout(root).attention_dir(DATE).glob("*-current_intake_failed.json"))


def test_corrupt_run_state_blocks_intake(tmp_path: Path) -> None:
    root = _root(tmp_path)
    layout = scheduler.Layout(root)
    state_path = layout.run_dir(DATE, RUN_ID) / "run-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{not-json", encoding="utf-8")
    scheduler.atomic_write_json(
        layout.run_record(DATE),
        {
            "schema_version": scheduler.RUN_RECORD_SCHEMA,
            "edition_date": DATE,
            "run_id": RUN_ID,
            "source_commit": "test-mode-source-commit",
            "run_state_path": str(state_path),
        },
    )
    assert scheduler.run_intake(_intake_args(root)) == 10


def test_timed_out_run_invokes_one_same_id_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    layout = _write_run(root, _state("timed_out", export_status="blocked_incomplete_collection"))
    calls: list[list[str]] = []

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(arguments))
        if "--resume-run" in arguments:
            resumed = _state()
            resumed["resume_count"] = 1
            scheduler.atomic_write_json(layout.run_dir(DATE, RUN_ID) / "run-state.json", resumed)
        return subprocess.CompletedProcess(arguments, 0, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_resume(_resume_args(root)) == 0
    assert sum("--resume-run" in call for call in calls) == 1
    assert all(call[call.index("--resume-run") + 1] == RUN_ID for call in calls if "--resume-run" in call)


def test_second_failure_after_resume_blocks_without_new_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _write_run(root, _state("partial", export_status="blocked_incomplete_collection", resume_count=1), resume_attempted=True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        scheduler,
        "_invoke_python",
        lambda python, cwd, arguments: calls.append(list(arguments)) or subprocess.CompletedProcess(arguments, 0, "{}", ""),
    )
    assert scheduler.run_resume(_resume_args(root)) == 10
    assert not any("--resume-run" in call for call in calls)


def test_completed_run_proceeds_to_private_intake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _write_run(root, _state())

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        _completed_intake_report(root)
        return subprocess.CompletedProcess(arguments, 0, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_intake(_intake_args(root)) == 0
    receipt = next(scheduler.Layout(root).intake_log_dir(DATE).glob("*.json"))
    assert scheduler.read_json(receipt)["proposal_status"] == "draft_pending_editorial_review"


def test_qualifying_empty_run_produces_controlled_blocked_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _write_run(root, _state(export_status="no_exportable_findings"))

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        _completed_intake_report(root, blocked=True)
        return subprocess.CompletedProcess(arguments, 0, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_intake(_intake_args(root)) == 0
    receipt = next(scheduler.Layout(root).intake_log_dir(DATE).glob("*.json"))
    assert scheduler.read_json(receipt)["proposal_status"] == "blocked_no_reviewable_current_signals"


def test_partial_run_blocks_intake_before_processor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _write_run(root, _state("partial", export_status="blocked_incomplete_collection"))
    called = False

    def fake_invoke(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("processor must not run")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_intake(_intake_args(root)) == 10
    assert not called


def test_unexpected_publication_side_effect_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    _write_run(root, _state())

    def fake_invoke(python: Path, cwd: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        _completed_intake_report(root, side_effect=True)
        return subprocess.CompletedProcess(arguments, 0, "{}", "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    assert scheduler.run_intake(_intake_args(root)) == 10


def test_dirty_checkout_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "git-repo"
    subprocess.run(["git", "init", "-b", scheduler.PRODUCTION_BRANCH, str(root)], check=True, capture_output=True)
    (root / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(scheduler.SchedulerError, match="dirty"):
        scheduler.verify_checkout(root, scheduler.PRODUCTION_BRANCH, update=False)


def test_non_fast_forward_checkout_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)

    def fake_run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        joined = " ".join(command)
        if "status --porcelain" in joined:
            return subprocess.CompletedProcess(command, 0, "", "")
        if "branch --show-current" in joined:
            return subprocess.CompletedProcess(command, 0, scheduler.PRODUCTION_BRANCH + "\n", "")
        if "merge-base" in joined:
            return subprocess.CompletedProcess(command, 1, "", "diverged")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(scheduler, "_run", fake_run)
    with pytest.raises(scheduler.SchedulerError, match="cannot fast-forward"):
        scheduler.verify_checkout(root, scheduler.PRODUCTION_BRANCH, update=True)


def test_runtime_and_setup_files_contain_no_publication_command() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_files = [
        root / "scripts" / "food_line_daily_scheduler.py",
        root / "scripts" / "windows" / "run_food_line_daily_current.ps1",
        root / "scripts" / "windows" / "resume_food_line_daily_current.ps1",
        root / "scripts" / "windows" / "run_food_line_current_intake.ps1",
    ]
    forbidden = ["--publish", "--push", "--post-bluesky", "--generate-audio", "sync_pages_from_source"]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in runtime_files)
    for token in forbidden:
        assert token not in combined


def test_setup_is_idempotent_and_disables_legacy_task() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts" / "windows" / "setup_food_line_daily_tasks.ps1").read_text(encoding="utf-8")
    assert "Get-ScheduledTask -TaskPath $TaskPath -TaskName $definition.Name" in text
    assert "Set-ScheduledTask -TaskPath $TaskPath -TaskName $definition.Name" in text
    assert "Register-ScheduledTask -TaskPath $TaskPath" in text
    assert "Disable-ScheduledTask -TaskPath $TaskPath -TaskName $LegacyTaskName" in text
    assert "[switch]$CheckOnly" in text
