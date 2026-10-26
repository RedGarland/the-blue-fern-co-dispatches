from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import food_line_daily_scheduler as scheduler
from scripts import run_food_line_discovery_expansion as discovery
from scripts.run_food_line_discovery_expansion import (
    _bounded_state_from_result,
    _terminal_contract_result,
)


EDITION = "2026-10-26"
EXPECT_COLLECTION_ONLY = os.environ.get("EXPECT_COLLECTION_ONLY") == "1"


def _args(root: Path, *, run_id: str = "private-port", collection_only: bool = False) -> scheduler.argparse.Namespace:
    return scheduler.argparse.Namespace(
        repo_root=str(root),
        python=str(root / ".venv" / "Scripts" / "python.exe"),
        edition_date=EDITION,
        run_id=run_id,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
        collection_only=collection_only,
    )


def _state_artifacts(
    root: Path,
    *,
    run_id: str,
    status: str = "completed",
    export_status: str = "success",
    final_error: str = "",
) -> None:
    run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / EDITION / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "query-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "food_line_bounded_query_plan_v1",
                "run_id": run_id,
                "edition_date": EDITION,
                "configuration_sha256": "config-sha",
                "query_count": 1,
                "queries": [{"query_id": "q-1"}],
                "query_plan_sha256": "plan-sha",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    export_path = root / "data" / "dispatches" / "food-line" / "agent-inbox" / "private.json"
    if export_status != "not_requested":
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text("{}\n", encoding="utf-8")
    state = {
        "schema_version": scheduler.RUN_STATE_SCHEMA,
        "run_id": run_id,
        "edition_date": EDITION,
        "started_at": "2026-10-26T12:00:00Z",
        "completed_at": "2026-10-26T12:01:00Z",
        "status": status,
        "resumable": False,
        "resume_count": 0,
        "partitions_total": 1,
        "partitions_completed": 1 if status == "completed" else 0,
        "queries_total": 1,
        "queries_bounded": 1,
        "queries_completed": 1 if status == "completed" else 0,
        "queries_succeeded": 1 if status == "completed" else 0,
        "counter_integrity_status": "valid",
        "queries_deferred": 0,
        "queries_remaining": 0,
        "queries_failed": 0 if status == "completed" else 1,
        "queries_timed_out": 0,
        "candidates_discovered": 1,
        "query_plan_sha256": "plan-sha",
        "final_error": final_error,
        "options": {"required_coverage_threshold": 0.9, "direct_source_coverage_threshold": 0.75},
        "coverage": {"required_success_ratio": 1.0, "direct_success_ratio": 1.0},
        "agent_export": (
            {"status": "not_requested", "path": None, "sha256": None}
            if export_status == "not_requested"
            else {"status": export_status, "path": str(export_path), "sha256": "export-sha"}
        ),
        "execution_mode": "collection_only" if export_status == "not_requested" else "export_enabled",
        "release_ready": False if export_status == "not_requested" else None,
        "intake_eligible": False if export_status == "not_requested" else None,
        "next_action": "No collection action required.",
    }
    (run_dir / "run-state.json").write_text(json.dumps(state) + "\n", encoding="utf-8")


def _child_payload(*, ok: bool, status: str, export_status: str, error: str = "", updates: dict[str, object] | None = None, remove: tuple[str, ...] = ()) -> str:
    payload: dict[str, object] = {
            "ok": ok,
            "status": status,
            "run_id": "private-port",
            "edition_date": EDITION,
            "agent_export": {"status": export_status, "path": None, "sha256": None},
            "execution_mode": "collection_only" if export_status == "not_requested" else "export_enabled",
            "final_error": error,
            "fatal_error": error,
            "publication_attempted": False,
            "public_side_effects": {},
        }
    payload.update(updates or {})
    for key in remove:
        payload.pop(key, None)
    return json.dumps(payload)


def _run_source_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    collection_only: bool,
    child_ok: bool = True,
    calls: list[list[str]] | None = None,
    child_payload_updates: dict[str, object] | None = None,
    child_payload_remove: tuple[str, ...] = (),
) -> tuple[int, dict[str, object]]:
    monkeypatch.setattr(scheduler, "verify_checkout", lambda *args, **kwargs: "installed-source")
    monkeypatch.setattr(scheduler, "run_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda *args, **kwargs: [])

    def invoke(python: Path, root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(list(arguments))
        _state_artifacts(
            root,
            run_id="private-port",
            status="completed" if child_ok else "failed",
            export_status="not_requested" if collection_only else "success",
            final_error="bounded failure" if not child_ok else "",
        )
        return subprocess.CompletedProcess(
            [str(python), *arguments],
            0 if child_ok else 1,
            stdout=_child_payload(
                ok=child_ok,
                status="completed" if child_ok else "failed",
                export_status="not_requested" if collection_only else "success",
                error="bounded failure" if not child_ok else "",
                updates=child_payload_updates,
                remove=child_payload_remove,
            ),
            stderr="",
        )

    monkeypatch.setattr(scheduler, "_invoke_python", invoke)
    code = scheduler.run_source_watch(_args(tmp_path, collection_only=collection_only))
    receipt_path = next((tmp_path / "logs" / "food-line" / "source-watch" / EDITION).glob("*-source-watch.json"))
    return code, json.loads(receipt_path.read_text(encoding="utf-8"))


def test_normal_mode_remains_export_enabled_on_both_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    code, receipt = _run_source_watch(tmp_path, monkeypatch, collection_only=False, calls=calls)
    assert code == 0
    if EXPECT_COLLECTION_ONLY:
        assert receipt["execution_mode"] == "export_enabled"
    else:
        assert "execution_mode" not in receipt
    assert "--export-agent-inbox" in calls[0]
    assert "--agent-inbox-dir" in calls[0]


def test_parser_binding_matches_expected_snapshot() -> None:
    parser = scheduler.build_parser()
    if EXPECT_COLLECTION_ONLY:
        args = parser.parse_args(["source-watch", "--repo-root", ".", "--python", "python", "--edition-date", EDITION, "--run-id", "x", "--collection-only"])
        assert args.collection_only is True
    else:
        with pytest.raises(SystemExit):
            parser.parse_args(["source-watch", "--repo-root", ".", "--python", "python", "--edition-date", EDITION, "--run-id", "x", "--collection-only"])


def test_collection_only_success_contract_or_installed_baseline_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not EXPECT_COLLECTION_ONLY:
        with pytest.raises(SystemExit):
            scheduler.build_parser().parse_args(["source-watch", "--collection-only"])
        return
    calls: list[list[str]] = []
    code, receipt = _run_source_watch(tmp_path, monkeypatch, collection_only=True, calls=calls)
    assert code == 0
    assert receipt["execution_mode"] == "collection_only"
    assert receipt["export_status"] == "not_requested"
    assert receipt["child_publication_attempted"] is False
    assert receipt["child_public_side_effects"] == {}
    assert receipt["publication_attempted"] is False
    assert receipt["publication_side_effects"] == {}
    operational = json.loads(Path(receipt["operational_health_receipt_path"]).read_text(encoding="utf-8"))
    assert operational["publication_attempted"] is False
    assert operational["details"]["publication_attempted_evidence_present"] is True
    assert operational["details"]["publication_side_effects_evidence_present"] is True
    assert operational["details"]["release_ready"] is False
    assert operational["details"]["intake_eligible"] is False
    assert "--export-agent-inbox" not in calls[0]
    assert "--agent-inbox-dir" not in calls[0]
    assert not (tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox").exists()


def test_collection_only_failure_is_terminal_and_nonpublishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    calls: list[list[str]] = []
    code, receipt = _run_source_watch(tmp_path, monkeypatch, collection_only=True, child_ok=False, calls=calls)
    assert code == 1
    assert receipt["execution_mode"] == "collection_only"
    assert receipt["final_status"] == "failed"
    assert receipt["export_status"] == "not_requested"
    assert receipt["child_publication_attempted"] is False
    assert receipt["child_public_side_effects"] == {}
    assert "--export-agent-inbox" not in calls[0]


def test_collection_only_lock_conflict_is_labeled_without_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    lock_dir = tmp_path / "status" / "food-line" / "locks" / "source-watch.lock"
    lock_dir.mkdir(parents=True)
    (lock_dir / "owner.json").write_text(json.dumps({"task": "test", "pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(scheduler, "process_is_running", lambda pid: True)
    code = scheduler.run_source_watch(_args(tmp_path, collection_only=True))
    assert code == 10
    receipt_path = next((tmp_path / "logs" / "food-line" / "source-watch" / EDITION).glob("*-source-watch.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["execution_mode"] == "collection_only"
    assert receipt["exit_code"] == 10
    assert lock_dir.exists()


@pytest.mark.parametrize(
    ("updates", "remove"),
    [
        ({}, ("publication_attempted", "public_side_effects")),
        ({"publication_attempted": 0}, ()),
        ({"publication_attempted": None}, ()),
        ({"publication_attempted": False}, ("public_side_effects",)),
        ({"publication_attempted": False, "public_side_effects": {"pages": True}}, ()),
        ({"publication_attempted": False, "public_side_effects": None}, ()),
    ],
)
def test_collection_only_rejects_missing_unknown_or_nonboolean_side_effect_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, object],
    remove: tuple[str, ...],
) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    code, receipt = _run_source_watch(
        tmp_path,
        monkeypatch,
        collection_only=True,
        child_payload_updates=updates,
        child_payload_remove=remove,
    )
    assert code == 2
    assert receipt["exit_code"] == 2
    expected_publication_attempted = (
        updates["publication_attempted"]
        if "publication_attempted" in updates
        else None
        if "publication_attempted" in remove
        else False
    )
    assert receipt["publication_attempted"] == expected_publication_attempted
    assert type(receipt["publication_attempted"]) is type(expected_publication_attempted)


def test_actual_collection_only_child_path_never_exports_or_attests_normal_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    monkeypatch.chdir(tmp_path)
    query_plan = [{"query_id": "q-1", "query_text": "bounded test", "discovery_channel": "direct_rss"}]
    monkeypatch.setattr(discovery, "build_food_line_discovery_query_plan", lambda root, edition_date: query_plan)

    def fake_discovery(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "status": "completed",
            "candidate_count": 0,
            "direct_source_count": 0,
            "queries_attempted": 1,
            "queries_processed_this_invocation": 1,
            "queries_completed": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
            "partitions_completed": 1,
            "resumable": False,
        }

    monkeypatch.setattr(discovery, "run_food_line_discovery_expansion", fake_discovery)
    monkeypatch.setattr(discovery, "_export_agent_inbox", lambda *args, **kwargs: pytest.fail("collection-only child exported findings"))
    code = discovery.main(
        [
            "--date", EDITION,
            "--run-id", "actual-child",
            "--profile", "daily-current",
            "--max-run-minutes", "1",
            "--collection-only",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["execution_mode"] == "collection_only"
    assert payload["publication_attempted"] is False
    assert payload["public_side_effects"] == {}
    assert payload["agent_export"]["status"] == "not_requested"
    assert not (tmp_path / "status" / "food-line" / "runtime" / "agent-inbox").exists()


def test_collection_only_rejects_conflicting_export_flags_before_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    calls: list[object] = []

    def unexpected_collection(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append((args, kwargs))
        raise AssertionError("collection started before conflicting mode was rejected")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(discovery, "run_food_line_discovery_expansion", unexpected_collection)
    code = discovery.main(
        [
            "--date", EDITION,
            "--run-id", "conflicting-export",
            "--profile", "daily-current",
            "--max-run-minutes", "1",
            "--collection-only",
            "--export-agent-inbox",
            "--agent-inbox-dir", str(tmp_path / "agent-inbox"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert calls == []
    assert payload["error_type"] == "ValueError"
    assert "cannot be combined" in payload["error_message"]
    assert not (tmp_path / "agent-inbox").exists()


def test_collection_only_preserves_and_rejects_truthy_producer_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    monkeypatch.chdir(tmp_path)
    query_plan = [{"query_id": "q-1", "query_text": "bounded test", "discovery_channel": "direct_rss"}]
    monkeypatch.setattr(discovery, "build_food_line_discovery_query_plan", lambda root, edition_date: query_plan)

    def fake_discovery(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "status": "completed",
            "queries_attempted": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
            "queries_completed": 1,
            "partitions_completed": 1,
            "candidate_count": 1,
            "publication_attempted": True,
            "public_side_effects": {"pages": True},
        }

    monkeypatch.setattr(discovery, "run_food_line_discovery_expansion", fake_discovery)
    monkeypatch.setattr(discovery, "_export_agent_inbox", lambda *args, **kwargs: pytest.fail("exported contradictory child"))
    code = discovery.main(
        [
            "--date", EDITION,
            "--run-id", "truthy-producer-evidence",
            "--profile", "daily-current",
            "--max-run-minutes", "1",
            "--collection-only",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["status"] == "collection_only_publication_evidence_conflict"
    assert payload["publication_attempted"] is True
    assert payload["public_side_effects"] == {"pages": True}
    assert payload["error_type"] == "contradictory_publication_evidence"


def test_collection_only_conflict_is_persisted_as_terminal_failure_before_state_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not EXPECT_COLLECTION_ONLY:
        return
    monkeypatch.chdir(tmp_path)
    query_plan = [{"query_id": "q-1", "query_text": "bounded test", "discovery_channel": "direct_rss"}]
    monkeypatch.setattr(discovery, "build_food_line_discovery_query_plan", lambda root, edition_date: query_plan)

    def fake_discovery(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "status": "completed",
            "candidate_count": 1,
            "queries_attempted": 1,
            "queries_completed": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
            "partitions_completed": 1,
            "publication_attempted": True,
            "public_side_effects": {"pages": True},
        }

    monkeypatch.setattr(discovery, "run_food_line_discovery_expansion", fake_discovery)
    code = discovery.main(
        [
            "--date", EDITION,
            "--run-id", "durable-conflict",
            "--profile", "daily-current",
            "--max-run-minutes", "1",
            "--collection-only",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    state_path = (
        tmp_path
        / "data"
        / "dispatches"
        / "food-line"
        / "discovery-runs"
        / EDITION
        / "durable-conflict"
        / "run-state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["status"] == "collection_only_publication_evidence_conflict"
    assert state["status"] == "collection_only_publication_evidence_conflict"
    assert state["status"] not in {"completed", "completed_with_exclusions", "zero_result_completion"}
    assert state["execution_mode"] == "collection_only"


def test_child_state_and_terminal_defaults_are_exact(tmp_path: Path) -> None:
    query_plan = [{"query_id": "q-1", "query_text": "food access strain", "discovery_channel": "direct_rss"}]
    state_kwargs = dict(
        root=tmp_path,
        edition_date=EDITION,
        run_id="state-test",
        query_plan=query_plan,
        result={
            "ok": True,
            "status": "completed",
            "candidate_count": 0,
            "direct_source_count": 0,
            "queries_attempted": 1,
            "queries_completed": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
            "partitions_completed": 1,
            "resumable": False,
        },
    )
    if EXPECT_COLLECTION_ONLY:
        state_kwargs["export_requested"] = False
    state, _ = _bounded_state_from_result(**state_kwargs)
    if EXPECT_COLLECTION_ONLY:
        assert state["execution_mode"] == "collection_only"
        assert state["agent_export"] == {"status": "not_requested", "path": None, "sha256": None}
        assert state["release_ready"] is False
        assert state["intake_eligible"] is False
    else:
        assert "execution_mode" not in state
    terminal_kwargs = {"collection_only": True} if EXPECT_COLLECTION_ONLY else {}
    terminal = _terminal_contract_result(state, edition_date=EDITION, run_id="state-test", **terminal_kwargs)
    if EXPECT_COLLECTION_ONLY:
        assert terminal["publication_attempted"] is False
        assert terminal["public_side_effects"] == {}
    else:
        assert "publication_attempted" not in terminal
        assert "public_side_effects" not in terminal


def test_wrapper_contract_matches_expected_snapshot() -> None:
    wrapper = (Path(__file__).resolve().parents[1] / "scripts" / "windows" / "run_food_line_daily_current.ps1").read_text(encoding="utf-8")
    if EXPECT_COLLECTION_ONLY:
        assert "[switch]$CollectionOnly" in wrapper
        assert 'if ($CollectionOnly) { $arguments += "--collection-only" }' in wrapper
    else:
        assert "[switch]$CollectionOnly" not in wrapper

