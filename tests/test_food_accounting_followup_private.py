from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import food_line_daily_scheduler as scheduler
from scripts import run_food_line_discovery_expansion as expansion


def _query_plan(count: int) -> list[dict[str, object]]:
    return [{"query_id": f"q-{index}", "query_text": f"query {index}"} for index in range(count)]


def _result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "status": "completed",
        "candidate_count": 0,
        "direct_source_count": 0,
        "queries_completed": 200,
        "queries_processed_this_invocation": 200,
        "queries_failed": 0,
        "queries_timed_out": 0,
        "query_plan_available_count": 1380,
        "query_plan_bounded_count": 200,
        "queries_deferred": 1180,
        "resumable": False,
    }
    result.update(overrides)
    return result


def test_terminal_contract_preserves_incomplete_capped_and_fails_closed() -> None:
    payload = expansion._terminal_contract_result(
        _result(status=expansion.INCOMPLETE_CAPPED_STATUS),
        edition_date="2026-10-24",
        run_id="bounded-200-of-1380",
    )

    assert payload["status"] == expansion.INCOMPLETE_CAPPED_STATUS
    assert payload["ok"] is False
    assert payload["error_type"] == expansion.INCOMPLETE_CAPPED_STATUS
    assert payload["error_message"] == "plan-bound continuation required before intake"
    assert payload["status"] != "zero_result_completion"


def test_write_state_files_records_deferred_partition_and_manual_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(expansion, "build_food_line_discovery_query_plan", lambda *args: _query_plan(1380))

    wrapped = expansion._write_state_files(
        tmp_path,
        "2026-10-24",
        "bounded-200-of-1380",
        _result(),
    )

    state = json.loads(Path(wrapped["run_state_path"]).read_text(encoding="utf-8"))
    assert wrapped["ok"] is False
    assert wrapped["status"] == expansion.INCOMPLETE_CAPPED_STATUS
    assert state["queries_total"] == 1380
    assert state["queries_bounded"] == 200
    assert state["queries_deferred"] == 1180
    assert state["queries_remaining"] == 1180
    assert state["resumable"] is False
    assert state["next_action"] == "Plan-bound continuation required before intake."
    assert state["agent_export"]["status"] == "blocked_incomplete_collection"


def test_full_completion_can_remain_a_zero_result_without_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(expansion, "build_food_line_discovery_query_plan", lambda *args: _query_plan(200))

    complete = _result(
        queries_completed=200,
        queries_processed_this_invocation=200,
        query_plan_available_count=200,
        query_plan_bounded_count=200,
        queries_deferred=0,
        resumable=False,
    )
    wrapped = expansion._write_state_files(tmp_path, "2026-10-24", "complete-200", complete)
    terminal = expansion._terminal_contract_result(
        wrapped,
        edition_date="2026-10-24",
        run_id="complete-200",
    )

    assert terminal["ok"] is True
    assert terminal["status"] == "zero_result_completion"
    state = json.loads(Path(wrapped["run_state_path"]).read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["next_action"] == "No collection action required."


def test_timeout_state_requires_continuation_and_is_not_a_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(expansion, "_query_plan_payload", lambda *args: {
        "schema_version": expansion.QUERY_PLAN_SCHEMA,
        "run_id": "timeout-run",
        "edition_date": "2026-10-24",
        "configuration_sha256": "",
        "query_count": 200,
        "queries": _query_plan(200),
        "query_plan_sha256": "test-plan",
    })

    payload = expansion._write_timed_out_state_files(
        tmp_path,
        "2026-10-24",
        "timeout-run",
        _query_plan(200),
        timeout_seconds=300,
    )

    assert payload["ok"] is False
    assert payload["status"] == "timed_out"
    state = json.loads(Path(payload["run_state_path"]).read_text(encoding="utf-8"))
    assert state["resumable"] is True
    assert state["next_action"] == "Plan-bound continuation required before intake."


def test_scheduler_rejects_incomplete_capped_child_even_when_state_is_consistent() -> None:
    state = {
        "status": expansion.INCOMPLETE_CAPPED_STATUS,
        "resumable": False,
        "final_error": "",
        "run_id": "bounded-200-of-1380",
        "edition_date": "2026-10-24",
        "agent_export": {"status": "blocked_incomplete_collection"},
    }
    child = {
        "ok": False,
        "status": expansion.INCOMPLETE_CAPPED_STATUS,
        "run_id": "bounded-200-of-1380",
        "edition_date": "2026-10-24",
        "error_type": expansion.INCOMPLETE_CAPPED_STATUS,
        "error_message": "plan-bound continuation required before intake",
    }
    result = subprocess.CompletedProcess(["food"], 1, json.dumps(child), "")
    audit = scheduler.child_result_audit(result, state)

    assert audit["child_terminal_status"] == expansion.INCOMPLETE_CAPPED_STATUS
    assert audit["child_terminal_ok"] is False
    assert scheduler.child_result_allows_scheduler_success(result, state, audit) is False


