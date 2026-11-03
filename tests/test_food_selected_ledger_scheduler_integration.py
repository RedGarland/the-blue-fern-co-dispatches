from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_ROOT))
sys.path.insert(1, str(CANDIDATE_ROOT / "src"))

_ledger_spec = importlib.util.spec_from_file_location(
    "scripts.food_line_continuation_ledger",
    CANDIDATE_ROOT / "scripts" / "food_line_continuation_ledger.py",
)
assert _ledger_spec and _ledger_spec.loader
ledger = importlib.util.module_from_spec(_ledger_spec)
sys.modules[_ledger_spec.name] = ledger
_ledger_spec.loader.exec_module(ledger)

_scheduler_spec = importlib.util.spec_from_file_location(
    "food_selected_ledger_scheduler_candidate",
    CANDIDATE_ROOT / "scripts" / "food_line_daily_scheduler.py",
)
assert _scheduler_spec and _scheduler_spec.loader
scheduler = importlib.util.module_from_spec(_scheduler_spec)
sys.modules[_scheduler_spec.name] = scheduler
_scheduler_spec.loader.exec_module(scheduler)

assert Path(ledger.__file__).resolve() == (CANDIDATE_ROOT / "scripts" / "food_line_continuation_ledger.py").resolve()
assert scheduler.ROOT.resolve() == CANDIDATE_ROOT.resolve()
_continuation_spec = importlib.util.spec_from_file_location(
    "food_selected_continuation_runner_candidate",
    CANDIDATE_ROOT / "scripts" / "run_food_line_continuation.py",
)
assert _continuation_spec and _continuation_spec.loader
continuation_runner = importlib.util.module_from_spec(_continuation_spec)
sys.modules[_continuation_spec.name] = continuation_runner
_continuation_spec.loader.exec_module(continuation_runner)
assert Path(continuation_runner.__file__).resolve() == (CANDIDATE_ROOT / "scripts" / "run_food_line_continuation.py").resolve()


EDITION_DATE = "2026-11-02"
SOURCE_IDENTITY = "a" * 40


def _write_json(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(data)
    return data


def _outcome(row: dict) -> dict:
    return {
        "status": "completed",
        "ok": True,
        "terminal": True,
        "uncertain": False,
        "required_coverage_success": True,
        "direct_source_attempted": True,
        "direct_source_success": True,
        "candidate_count": 0,
        "exclusion_count": 0,
        "candidates": [],
    }


def _executor(rows: list[dict]) -> dict[str, dict]:
    return {ledger.stable_query_id(row): _outcome(row) for row in rows}


def _make_fixture(tmp_path: Path, *, total_rows: int = 8, seed_batch: int = 2) -> dict:
    root = tmp_path / "food-runner"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("selected-ledger source fixture\n", encoding="utf-8")
    config_path.write_text('{"selected_ledger_fixture": true}\n', encoding="utf-8")
    rows = [{"topic": f"topic-{index}", "source": "fixture", "ordinal": index} for index in range(total_rows)]
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    run_id = "source-watch-A"
    continuation = root / "data" / "dispatches" / "food-line" / "discovery-runs" / EDITION_DATE / run_id / "continuation"
    continuation.mkdir(parents=True, exist_ok=True)
    prepared = ledger.prepare_ledger(
        continuation,
        edition_date=EDITION_DATE,
        run_id=run_id,
        source_identity=SOURCE_IDENTITY,
        source_module_sha256=source_sha,
        config_sha256=config_sha,
        query_rows=rows,
    )
    ledger.continue_ledger(
        continuation,
        binding=dict(prepared["binding"]),
        batch_size=seed_batch,
        batch_executor=_executor,
    )
    layout = scheduler.Layout(root)
    state_path = layout.run_dir(EDITION_DATE, run_id) / "run-state.json"
    _write_json(
        state_path,
        {
            "schema_version": scheduler.RUN_STATE_SCHEMA,
            "edition_date": EDITION_DATE,
            "run_id": run_id,
            "status": scheduler.INCOMPLETE_CAPPED_STATUS,
            "resumable": False,
            "execution_mode": "export_enabled",
            "scheduled_start_at": "2026-11-02T00:00:00Z",
            "source_commit": SOURCE_IDENTITY,
            "final_error": "",
            "coverage": {},
            "options": {},
            "agent_export": {"status": "not_requested"},
            "release_ready": False,
            "intake_eligible": False,
        },
    )
    b_run_id = "source-watch-B"
    b_state_path = layout.run_dir(EDITION_DATE, b_run_id) / "run-state.json"
    _write_json(
        b_state_path,
        {
            "schema_version": scheduler.RUN_STATE_SCHEMA,
            "edition_date": EDITION_DATE,
            "run_id": b_run_id,
            "status": scheduler.RUNNING_STATUS,
        },
    )
    b_record_path = layout.run_record(EDITION_DATE)
    b_record = {
        "schema_version": scheduler.RUN_RECORD_SCHEMA,
        "edition_date": EDITION_DATE,
        "run_id": b_run_id,
        "source_branch": scheduler.PRODUCTION_BRANCH,
        "source_commit": "b" * 40,
        "run_state_path": str(b_state_path),
        "scheduled_start_at": "2026-11-02T01:00:00Z",
        "source_watch_status": scheduler.RUNNING_STATUS,
        "last_status": scheduler.RUNNING_STATUS,
    }
    before_b = _write_json(b_record_path, b_record)
    return {
        "root": root,
        "layout": layout,
        "run_id": run_id,
        "continuation": continuation,
        "state_path": state_path,
        "binding": dict(prepared["binding"]),
        "b_record_path": b_record_path,
        "before_b": before_b,
        "source_path": source_path,
        "config_path": config_path,
        "rows": rows,
    }


def _args(fixture: dict, *, selected: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        repo_root=str(fixture["root"]),
        python=sys.executable,
        edition_date=EDITION_DATE,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
        continuation_run_directory=str(fixture["continuation"]) if selected else "",
        continuation_run_root="",
        expected_protected_head=SOURCE_IDENTITY if selected else "",
        expected_runtime_head="d" * 40 if selected else "",
    )


def _invoke_stub(fixture: dict, calls: list[list[str]], batches: list[list[str]]):
    binding = dict(fixture["binding"])

    def provider(_root: Path, _edition_date: str, **kwargs) -> dict:
        row = list(kwargs["query_plan_override"])[0]
        return {
            "status": "completed",
            "ok": True,
            "query_rows": [{**row, "direct_fetch_status": "ok"}],
            "candidates": [],
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        }

    def invoke(_python: Path, _root: Path, arguments) -> subprocess.CompletedProcess[str]:
        argv = [str(item) for item in arguments]
        calls.append(argv)
        if "--status-run" in argv:
            payload = {
                "run_id": fixture["run_id"],
                "edition_date": EDITION_DATE,
                "run_state_path": str(fixture["state_path"]),
                "status": scheduler.INCOMPLETE_CAPPED_STATUS,
                "resumable": False,
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if "scripts/run_food_line_continuation.py" not in argv:
            raise AssertionError(f"unexpected child command: {argv}")
        before = ledger.load_ledger(fixture["continuation"], binding=binding)
        child_ids = list(before["pending_query_ids"][:200])
        batches.append(child_ids)
        continuation_runner.ROOT = fixture["root"]
        continuation_runner.SRC = fixture["root"] / "src"
        continuation_runner.build_food_line_discovery_query_plan = lambda _root, _date: list(fixture["rows"])
        continuation_runner.run_food_line_discovery_expansion = provider
        child_args = continuation_runner.build_parser().parse_args(argv[1:])
        output = io.StringIO()
        with redirect_stdout(output):
            child_exit = continuation_runner.run(child_args)
        return subprocess.CompletedProcess(argv, child_exit, output.getvalue(), "")

    return invoke


def test_selected_recovery_advances_a_without_replaying_or_mutating_latest_b(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, total_rows=1000, seed_batch=200)
    calls: list[list[str]] = []
    batches: list[list[str]] = []
    invoke = _invoke_stub(fixture, calls, batches)
    with patch.object(scheduler, "_load_run_record", side_effect=AssertionError("selected recovery consulted latest record")), patch.object(scheduler, "_invoke_python", side_effect=invoke):
        first = scheduler.run_resume(_args(fixture))
        second = scheduler.run_resume(_args(fixture))
    assert first == 2
    assert second == 2
    assert len([call for call in calls if "--status-run" in call]) == 2
    assert batches[0] and batches[1] and set(batches[0]).isdisjoint(batches[1])
    assert len(set(batches[0] + batches[1])) == 400
    assert fixture["b_record_path"].read_bytes() == fixture["before_b"]
    assert not (fixture["root"] / "data" / "dispatches" / "food-line" / "discovery-runs" / EDITION_DATE / "source-watch-B" / "continuation").exists()
    assert not (fixture["root"] / "logs" / "food-line" / "current-intake").exists()
    assert not (fixture["root"] / "logs" / "care-line").exists()


def test_selected_recovery_refuses_active_watch_before_child(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    lock_dir = fixture["layout"].lock_dir
    lock_dir.mkdir(parents=True)
    _write_json(lock_dir / "owner.json", {"task": "active-source-watch", "pid": os.getpid(), "acquired_at": scheduler.utc_now()})
    calls: list[list[str]] = []
    with patch.object(scheduler, "_invoke_python", side_effect=_invoke_stub(fixture, calls, [])):
        result = scheduler.run_resume(_args(fixture))
    assert result == 10
    assert not any("scripts/run_food_line_continuation.py" in call for call in calls)
    assert fixture["b_record_path"].read_bytes() == fixture["before_b"]


def test_selected_recovery_refuses_binding_drift_before_child(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    fixture["config_path"].write_text('{"selected_ledger_fixture": false}\n', encoding="utf-8")
    calls: list[list[str]] = []
    with patch.object(scheduler, "_invoke_python", side_effect=_invoke_stub(fixture, calls, [])):
        result = scheduler.run_resume(_args(fixture))
    assert result == 10
    assert not calls
    assert fixture["b_record_path"].read_bytes() == fixture["before_b"]


@pytest.mark.parametrize("rejection", ["head", "unknown", "in_flight"])
def test_selected_recovery_rejects_head_or_uncertain_ledger_before_child(tmp_path: Path, rejection: str) -> None:
    fixture = _make_fixture(tmp_path)
    args = _args(fixture)
    if rejection == "head":
        args.expected_protected_head = "c" * 40
    else:
        ledger_path = fixture["continuation"] / ledger.LEDGER_NAME
        state = json.loads(ledger_path.read_text(encoding="utf-8"))
        pending = list(state["pending_query_ids"])
        state["pending_query_ids"] = pending[1:]
        state["unknown_query_ids" if rejection == "unknown" else "in_flight_query_ids"] = [pending[0]]
        ledger_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    calls: list[list[str]] = []
    with patch.object(scheduler, "_invoke_python", side_effect=_invoke_stub(fixture, calls, [])):
        result = scheduler.run_resume(args)
    assert result == 10
    assert not calls
    assert fixture["b_record_path"].read_bytes() == fixture["before_b"]


def test_default_resume_still_reads_latest_record_path(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path)
    calls: list[tuple[Path, str]] = []
    latest = json.loads(fixture["before_b"].decode("utf-8"))

    def load_latest(layout, edition_date):
        calls.append((layout.root, edition_date))
        return latest

    with patch.object(scheduler, "_load_run_record", side_effect=load_latest):
        result = scheduler.run_resume(_args(fixture, selected=False))
    assert result == 0
    assert calls == [(fixture["root"].resolve(), EDITION_DATE)]
    receipts = sorted((fixture["root"] / "logs" / "food-line" / "source-watch" / EDITION_DATE).glob("*-status-resume.json"))
    assert receipts
    receipt = json.loads(receipts[-1].read_text(encoding="utf-8"))
    assert receipt["run_id"] == "source-watch-B"
    assert receipt["resume_status"] == scheduler.UPSTREAM_IN_PROGRESS_STATUS


def test_selected_final_batch_is_collection_only_and_forwards_bound_receipt(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, total_rows=400, seed_batch=200)
    legacy_before = fixture["state_path"].read_bytes()
    calls: list[list[str]] = []
    batches: list[list[str]] = []
    output = io.StringIO()
    with patch.object(scheduler, "_invoke_python", side_effect=_invoke_stub(fixture, calls, batches)):
        with redirect_stdout(output):
            result = scheduler.run_resume(_args(fixture))
    assert result == 0
    assert batches and len(batches[0]) == 200
    child_calls = [call for call in calls if "scripts/run_food_line_continuation.py" in call]
    assert len(child_calls) == 1
    assert "--suppress-source-stage-handoff" in child_calls[0]
    assert fixture["state_path"].read_bytes() == legacy_before
    assert not (fixture["state_path"].parent / "continuation-agent-export.json").exists()
    receipts = sorted((fixture["root"] / "logs" / "food-line" / "source-watch" / EDITION_DATE).glob("*-status-resume.json"))
    assert receipts
    payload = json.loads(receipts[-1].read_text(encoding="utf-8"))
    assert payload["schema_version"] == scheduler.SOURCE_RECEIPT_SCHEMA
    assert payload["action"] == "status_resume"
    assert payload["execution_mode"] == "collection_only"
    child = payload["child_continuation_receipt"]
    assert child["schema"] == "food_line_continuation_receipt_v1"
    assert child["status"] == "completed"
    assert child["gates"]["intake_eligible"] is False
    assert child["gates"]["export_status"] == "not_requested"
    assert payload["intake_eligible"] is False
    assert payload["release_ready"] is False
    assert payload["export_status"] == "not_requested"
    assert payload["inbox_export_path"] is None
    assert payload["continuation_progress"]["collection_triggered"] is True
    assert payload["continuation_progress"]["durable_progress"] is True
    assert len(payload["continuation_progress"]["new_completed_query_ids"]) == 200
    assert payload["publication_side_effects"] == {}


def test_selected_zero_pending_is_complete_without_collection_or_handoff(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, total_rows=200, seed_batch=200)
    legacy_before = fixture["state_path"].read_bytes()
    calls: list[list[str]] = []
    batches: list[list[str]] = []
    output = io.StringIO()
    with patch.object(scheduler, "_invoke_python", side_effect=_invoke_stub(fixture, calls, batches)):
        with redirect_stdout(output):
            result = scheduler.run_resume(_args(fixture))
    assert result == 0
    assert batches == [[]]
    assert fixture["state_path"].read_bytes() == legacy_before
    assert not (fixture["state_path"].parent / "continuation-agent-export.json").exists()
    receipts = sorted((fixture["root"] / "logs" / "food-line" / "source-watch" / EDITION_DATE).glob("*-status-resume.json"))
    assert receipts
    payload = json.loads(receipts[-1].read_text(encoding="utf-8"))
    assert payload["resume_status"] == "continuation_complete"
    assert payload["child_continuation_receipt"]["status"] == "completed"
    assert payload["continuation_progress"]["collection_triggered"] is False
    assert payload["continuation_progress"]["durable_progress"] is False
    assert payload["continuation_progress"]["new_completed_query_ids"] == []


def test_default_child_preserves_incomplete_status_when_coverage_fails(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, total_rows=2, seed_batch=1)
    legacy_before = fixture["state_path"].read_bytes()
    calls = 0

    def failing_provider(_root: Path, _edition_date: str, **kwargs) -> dict:
        nonlocal calls
        calls += 1
        row = list(kwargs["query_plan_override"])[0]
        return {
            "status": "completed",
            "ok": False,
            "query_rows": [{**row, "query_error": "bounded provider failure", "direct_fetch_status": "failed"}],
            "candidates": [],
            "queries_succeeded": 0,
            "queries_failed": 1,
            "queries_timed_out": 0,
        }

    continuation_runner.ROOT = fixture["root"]
    continuation_runner.SRC = fixture["root"] / "src"
    continuation_runner.build_food_line_discovery_query_plan = lambda _root, _date: list(fixture["rows"])
    continuation_runner.run_food_line_discovery_expansion = failing_provider
    child_args = continuation_runner.build_parser().parse_args(
        [
            "--repo-root", str(fixture["root"]),
            "--edition-date", EDITION_DATE,
            "--run-id", fixture["run_id"],
            "--continuation-run-directory", str(fixture["continuation"]),
            "--source-identity", SOURCE_IDENTITY,
            "--max-queries", "200",
            "--collection-only",
        ]
    )
    output = io.StringIO()
    with redirect_stdout(output):
        result = continuation_runner.run(child_args)
    assert result == 2
    assert calls == 1
    payload = json.loads(output.getvalue().strip().splitlines()[-1])
    assert payload["status"] == "incomplete_pending"
    assert payload["source_stage_handoff"]["intake_eligible"] is False
    assert fixture["state_path"].read_bytes() == legacy_before
    assert not (fixture["state_path"].parent / "continuation-agent-export.json").exists()


def test_selected_failure_before_terminal_receipt_has_no_false_progress_marker(tmp_path: Path) -> None:
    fixture = _make_fixture(tmp_path, total_rows=4, seed_batch=2)
    calls: list[list[str]] = []
    status_stub = _invoke_stub(fixture, calls, [])

    def failing_invoke(_python: Path, _root: Path, arguments) -> subprocess.CompletedProcess[str]:
        argv = [str(item) for item in arguments]
        calls.append(argv)
        if "--status-run" in argv:
            return status_stub(_python, _root, arguments)
        return subprocess.CompletedProcess(argv, 1, "", "child failed before terminal receipt")

    with patch.object(scheduler, "_invoke_python", side_effect=failing_invoke):
        result = scheduler.run_resume(_args(fixture))
    assert result == 10
    receipts = sorted((fixture["root"] / "logs" / "food-line" / "source-watch" / EDITION_DATE).glob("*-status-resume.json"))
    assert receipts
    failure = json.loads(receipts[-1].read_text(encoding="utf-8"))
    assert failure["resume_status"] == "status_resume_failed"
    assert "continuation_progress" not in failure
    assert any("--suppress-source-stage-handoff" in call for call in calls if "scripts/run_food_line_continuation.py" in call)


@pytest.mark.parametrize("corruption", ["overlap", "duplicate", "missing"])
def test_partition_corruption_fails_before_provider_or_ledger_mutation(tmp_path: Path, corruption: str) -> None:
    fixture = _make_fixture(tmp_path, total_rows=8, seed_batch=2)
    ledger_path = fixture["continuation"] / ledger.LEDGER_NAME
    state = json.loads(ledger_path.read_text(encoding="utf-8"))
    if corruption == "overlap":
        state["pending_query_ids"].append(state["completed_query_ids"][0])
    elif corruption == "duplicate":
        state["pending_query_ids"].append(state["pending_query_ids"][0])
    else:
        state["pending_query_ids"] = state["pending_query_ids"][1:]
    _write_json(ledger_path, state)
    malformed_bytes = ledger_path.read_bytes()
    provider_calls = 0

    def provider(_rows: list[dict], _checkpoint: object | None = None) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return {}

    with pytest.raises(ledger.ContinuationLedgerError):
        ledger.continue_ledger(
            fixture["continuation"],
            binding=fixture["binding"],
            batch_size=2,
            batch_executor=provider,
            checkpoint_outcomes=True,
        )
    assert provider_calls == 0
    assert ledger_path.read_bytes() == malformed_bytes


def test_operator_and_resume_wrapper_keep_selected_branch_before_default_side_effects() -> None:
    operator_text = (CANDIDATE_ROOT / "scripts" / "windows" / "run_food_care_proof_continuation.ps1").read_text(encoding="utf-8")
    selected_start = operator_text.index("    if ($ContinuationRunDirectory) {")
    default_start = operator_text.index("    } else {", selected_start)
    selected_branch = operator_text[selected_start:default_start]
    assert "-ContinuationRunDirectory" in selected_branch
    assert "-ExpectedProtectedHead" in selected_branch
    assert "guarded_runner_sync" not in selected_branch
    assert "food_source_watch" not in selected_branch
    assert "food_current_intake" not in selected_branch
    assert "care_approved_release_proof_only" not in selected_branch
    assert "final_operational_status_export" not in selected_branch
    wrapper_text = (CANDIDATE_ROOT / "scripts" / "windows" / "resume_food_line_daily_current.ps1").read_text(encoding="utf-8")
    assert "--expected-protected-head" in wrapper_text
    assert "--continuation-run-directory" in wrapper_text


def test_operator_receipt_preserves_unknown_collection_trigger_until_bound_envelope() -> None:
    operator_text = (CANDIDATE_ROOT / "scripts" / "windows" / "run_food_care_proof_continuation.ps1").read_text(encoding="utf-8")
    assert "$CollectionTriggered = $null" in operator_text
    assert "$collectionTriggeredValue = $null" in operator_text
    assert "collection_triggered = $collectionTriggeredValue" in operator_text
    assert "$CollectionTriggered = [bool]$schedulerReceipt.continuation_progress.collection_triggered" in operator_text
    assert "food_resume receipt lacks the bound child continuation receipt" in operator_text

