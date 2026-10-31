"""Portable pytest coverage for the Food stable-ID continuation candidate.

All imports resolve relative to this copied candidate tree.  The source fetch
is an in-memory fixture; no provider, SQLite, Scheduler, or production path is
used.
"""
from __future__ import annotations

import hashlib
import ast
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest


PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src"
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_discovery_expansion import (  # noqa: E402
    build_food_line_discovery_query_plan,
    run_food_line_discovery_expansion,
)
from scripts import food_line_continuation_ledger as ledger  # noqa: E402


class _SchedulerError(RuntimeError):
    pass


def _extract_scheduler_function(name: str):
    path = PACKAGE / "scripts" / "food_line_daily_scheduler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Path": Path,
        "SchedulerError": _SchedulerError,
        "CONTINUATION_ROOT_RELATIVE": Path("data") / "dispatches" / "food-line" / "continuation",
        "DIRECT_CONTINUATION_RELATIVE": Path("data") / "dispatches" / "food-line" / "discovery-runs",
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, count: int = 4):
    config_source = PACKAGE / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    root = tmp_path / "FoodLineCurrent6"
    config = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    config.parent.mkdir(parents=True)
    shutil.copyfile(config_source, config)
    rows = build_food_line_discovery_query_plan(root, "2026-10-30")[:count]
    assert len(rows) == count
    return root, config, rows


def _binding(config: Path, source: Path, run_id: str, plan_dir: Path | None = None) -> dict[str, str]:
    value = {
        "edition_date": "2026-10-30",
        "run_id": run_id,
        "source_identity": "sha256:synthetic-current-source",
        "source_module_sha256": _sha256(source),
        "config_sha256": _sha256(config),
    }
    if plan_dir is not None:
        value["plan_sha256"] = json.loads((plan_dir / ledger.PLAN_NAME).read_text(encoding="utf-8"))["plan_sha256"]
    return value


def _terminal_result(rows: list[dict], *, candidate_count: int = 0) -> dict[str, dict]:
    return {
        ledger.stable_query_id(row): {
            "status": "zero_result_completion" if candidate_count == 0 else "completed_with_candidates",
            "ok": True,
            "terminal": True,
            "uncertain": False,
            "required_coverage_success": True,
            "direct_source_attempted": True,
            "direct_source_success": True,
            "candidate_count": candidate_count,
            "candidates": ([{"title": "synthetic candidate", "source_url": "https://example.invalid/item"}] if candidate_count else []),
            "collection_only": True,
        }
        for row in rows
    }


def test_progresses_beyond_200_and_persists_payloads_without_replay(tmp_path):
    root, config, all_rows = _fixture(tmp_path, count=202)
    source = PACKAGE / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    run_dir = tmp_path / "continuation"
    ledger.prepare_ledger(
        run_dir,
        query_rows=all_rows,
        edition_date="2026-10-30",
        run_id="progress-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    binding = _binding(config, source, "progress-run", run_dir)
    calls: list[str] = []

    def execute(batch):
        calls.extend(ledger.stable_query_id(row) for row in batch)
        return _terminal_result(batch, candidate_count=1)

    first = ledger.continue_ledger(run_dir, binding=binding, batch_size=200, batch_executor=execute)
    second = ledger.continue_ledger(run_dir, binding=binding, batch_size=2, batch_executor=execute)
    before = list(calls)
    repeated = ledger.continue_ledger(run_dir, binding=binding, batch_size=200, batch_executor=execute)
    loaded = ledger.load_ledger(run_dir, binding=binding)
    assert len(first["completed_query_ids"]) == 200
    assert len(second["completed_query_ids"]) == 202
    assert len(calls) == 202
    assert calls == before
    assert repeated["pending_query_ids"] == []
    assert loaded["query_results"][calls[0]]["candidates"]


def test_mocked_transport_and_unknown_outcomes_are_quarantined(tmp_path):
    root, config, rows = _fixture(tmp_path, count=2)
    source = PACKAGE / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    calls: list[str] = []

    def fake_fetcher(url: str, *, timeout: int) -> bytes:
        calls.append(f"{url}|{timeout}")
        return b"<rss version='2.0'><channel><title>synthetic</title></channel></rss>"

    result = run_food_line_discovery_expansion(
        root,
        "2026-10-30",
        fetcher=fake_fetcher,
        query_plan_override=rows,
        resume_from_query_index=0,
        edition_mode="no_current_update",
        dry_run=True,
        max_queries=None,
    )
    assert len(result["query_rows"]) == 2
    assert len(calls) >= 2

    run_dir = tmp_path / "uncertain"
    ledger.prepare_ledger(
        run_dir,
        query_rows=rows,
        edition_date="2026-10-30",
        run_id="uncertain-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    binding = _binding(config, source, "uncertain-run", run_dir)

    def interrupted(batch):
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError):
        ledger.continue_ledger(run_dir, binding=binding, batch_size=2, batch_executor=interrupted)
    state = ledger.load_ledger(run_dir, binding=binding)
    assert len(state["unknown_query_ids"]) == 2
    with pytest.raises(ledger.ContinuationLedgerError):
        ledger.continue_ledger(run_dir, binding=binding, batch_size=2, batch_executor=lambda batch: {})
    invalid = {query_id: {"status": "owner_review_only"} for query_id in state["unknown_query_ids"]}
    with pytest.raises(ledger.ContinuationLedgerError):
        ledger.reconcile_unknown(run_dir, binding=binding, verified_results=invalid)
    verified = {
        query_id: {
            "status": "completed_with_exclusions",
            "ok": True,
            "terminal": True,
            "uncertain": False,
            "required_coverage_success": True,
            "collection_only": True,
        }
        for query_id in state["unknown_query_ids"]
    }
    reconciled = ledger.reconcile_unknown(run_dir, binding=binding, verified_results=verified)
    assert reconciled["unknown_query_ids"] == []


def test_binding_drift_and_legacy_positional_state_are_rejected(tmp_path):
    _root, config, rows = _fixture(tmp_path)
    source = PACKAGE / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    run_dir = tmp_path / "bound"
    ledger.prepare_ledger(
        run_dir,
        query_rows=rows,
        edition_date="2026-10-30",
        run_id="bound-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    binding = _binding(config, source, "bound-run", run_dir)
    with pytest.raises(ledger.ContinuationLedgerError):
        ledger.load_ledger(run_dir, binding={**binding, "source_identity": "sha256:changed"})
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "run-state.json").write_text('{"queries_completed": 200}', encoding="utf-8")
    with pytest.raises(ledger.ContinuationLedgerError):
        ledger.prepare_ledger(
            legacy,
            query_rows=rows,
            edition_date="2026-10-30",
            run_id="legacy-run",
            source_identity="sha256:synthetic-current-source",
            source_module_sha256=_sha256(source),
            config_sha256=_sha256(config),
        )


def test_competing_writer_and_day_rollover_are_fail_closed(tmp_path):
    _root, config, rows = _fixture(tmp_path)
    source = PACKAGE / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    run_dir = tmp_path / "lock"
    ledger.prepare_ledger(
        run_dir,
        query_rows=rows[:1],
        edition_date="2026-10-30",
        run_id="lock-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    binding = _binding(config, source, "lock-run", run_dir)
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with ledger._exclusive_lock(run_dir):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert entered.wait(5)
    with pytest.raises(ledger.ContinuationLockError):
        ledger.continue_ledger(run_dir, binding=binding, batch_size=1, batch_executor=lambda batch: {})
    release.set()
    thread.join(5)

    root = tmp_path / "root"
    recurring = root / "data" / "dispatches" / "food-line" / "continuation"
    derive_directory = _extract_scheduler_function("_derive_continuation_directory")
    one = derive_directory(root=root, edition_date="2026-10-30", run_id="run-a", continuation_run_root=str(recurring))
    two = derive_directory(root=root, edition_date="2026-10-31", run_id="run-b", continuation_run_root=str(recurring))
    assert one != two
    with pytest.raises(Exception):
        derive_directory(root=root, edition_date="2026-10-30", run_id="bad/run", continuation_run_root=str(recurring))


def test_only_complete_terminal_coverage_qualifies_intake(tmp_path):
    _root, config, rows = _fixture(tmp_path)
    source = PACKAGE / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    full_dir = tmp_path / "full"
    ledger.prepare_ledger(
        full_dir,
        query_rows=rows,
        edition_date="2026-10-30",
        run_id="full-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    binding = _binding(config, source, "full-run", full_dir)
    full_state = ledger.continue_ledger(full_dir, binding=binding, batch_size=len(rows), batch_executor=lambda batch: _terminal_result(batch))
    assessment = ledger.source_stage_handoff_assessment(full_state)
    assert assessment["intake_eligible"] is True
    assert assessment["no_news"] is True
    gates = ledger.gate_summary(full_state)
    assert gates["release_ready"] is False
    assert gates["export_status"] == "not_requested"

    pending_dir = tmp_path / "pending"
    pending_state = ledger.prepare_ledger(
        pending_dir,
        query_rows=rows,
        edition_date="2026-10-30",
        run_id="pending-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    pending = ledger.source_stage_handoff_assessment(pending_state)
    assert pending["intake_eligible"] is False
    assert pending["no_news"] is False

    failed_dir = tmp_path / "failed"
    ledger.prepare_ledger(
        failed_dir,
        query_rows=rows[:1],
        edition_date="2026-10-30",
        run_id="failed-run",
        source_identity="sha256:synthetic-current-source",
        source_module_sha256=_sha256(source),
        config_sha256=_sha256(config),
    )
    failed_binding = _binding(config, source, "failed-run", failed_dir)
    failed_state = ledger.continue_ledger(
        failed_dir,
        binding=failed_binding,
        batch_size=1,
        batch_executor=lambda batch: {
            ledger.stable_query_id(batch[0]): {
                "status": "collection_failure",
                "ok": False,
                "terminal": True,
                "uncertain": False,
                "required_coverage_success": False,
                "direct_source_attempted": True,
                "direct_source_success": False,
                "candidate_count": 0,
                "candidates": [],
            }
        },
    )
    failed = ledger.source_stage_handoff_assessment(failed_state)
    assert failed["intake_eligible"] is False
    assert failed["no_news"] is False
