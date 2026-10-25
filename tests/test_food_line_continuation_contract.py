import importlib
import json
import subprocess
import sys
import contextlib
import io
import os
import shutil
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SRC))

wrapper = importlib.import_module("scripts.run_food_line_discovery_expansion")
scheduler = importlib.import_module("scripts.food_line_daily_scheduler")


def test_actual_state_builder_preserves_capped_status(tmp_path):
    state, _ = wrapper._bounded_state_from_result(
        root=tmp_path / "test-work",
        edition_date="2026-10-24",
        run_id="integration-capped",
        query_plan=[{"query_id": str(i)} for i in range(4)],
        result={
            "ok": False,
            "status": "incomplete_capped",
            "queries_completed": 2,
            "queries_attempted": 2,
            "queries_succeeded": 0,
            "queries_deferred": 2,
            "queries_failed": 1,
            "queries_timed_out": 1,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        },
    )
    assert state["status"] == "incomplete_capped"
    assert state["next_action"] == "Plan-bound continuation required before intake."
    assert state["queries_deferred"] == 2


def test_actual_terminal_contract_preserves_capped_status():
    result = wrapper._terminal_contract_result(
        {"ok": False, "status": "incomplete_capped", "queries_deferred": 1180},
        edition_date="2026-10-24",
        run_id="integration-contract",
    )
    assert result["status"] == "incomplete_capped"
    assert result["error_type"] == "incomplete_capped"


def test_actual_status_run_cli_reads_capped_state(tmp_path):
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-10-24" / "cli-capped"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run-state.json").write_text(json.dumps({"status": "incomplete_capped", "resumable": False}), encoding="utf-8")
    command = [sys.executable, str(ROOT / "scripts" / "run_food_line_discovery_expansion.py"), "--status-run", "cli-capped", "--run-id", "cli-capped", "--date", "2026-10-24"]
    completed = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, check=False)
    assert completed.returncode == 1, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["status"] == "incomplete_capped"
    assert payload["run_id"] == "cli-capped"
    assert payload["edition_date"] == "2026-10-24"
    assert payload["run_state_path"].replace("/", "\\").endswith("cli-capped\\run-state.json")


def test_actual_status_run_cli_preserves_state_without_date_argument(tmp_path):
    command = [sys.executable, str(ROOT / "scripts" / "run_food_line_discovery_expansion.py"), "--status-run", "cli-capped", "--run-id", "cli-capped"]
    completed = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, check=False)
    assert completed.returncode == 1, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["status"] == "child_process_failure"
    assert payload["edition_date"] is None
    assert "run_state_path" not in payload


def test_actual_run_resume_accepts_capped_status_inspection(tmp_path, monkeypatch):
    edition = "2026-10-24"
    run_id = "resume-capped"
    layout = scheduler.Layout(tmp_path)
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "scripts" / "run_food_line_discovery_expansion.py", tmp_path / "scripts" / "run_food_line_discovery_expansion.py")
    shutil.copy2(ROOT / "scripts" / "food_line_runtime_paths.py", tmp_path / "scripts" / "food_line_runtime_paths.py")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH", "")])) )
    run_dir = layout.run_dir(edition, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": scheduler.RUN_STATE_SCHEMA,
        "edition_date": edition,
        "run_id": run_id,
        "status": "incomplete_capped",
        "resumable": False,
        "resume_count": 0,
        "queries_total": 1380,
        "queries_bounded": 200,
        "queries_completed": 200,
        "queries_deferred": 1180,
        "queries_remaining": 1180,
        "queries_failed": 191,
        "queries_timed_out": 2,
        "partitions_total": 1,
        "partitions_completed": 0,
        "coverage": {"required_success_ratio": 7 / 1380, "direct_success_ratio": 7 / 1380},
        "options": {"required_coverage_threshold": 0.90, "direct_source_coverage_threshold": 0.75},
        "agent_export": {"status": "blocked_incomplete_collection"},
        "final_error": "plan-bound continuation required before intake",
        "candidates_discovered": 1,
    }
    (run_dir / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
    record = {
        "schema_version": scheduler.RUN_RECORD_SCHEMA,
        "edition_date": edition,
        "run_id": run_id,
        "source_watch_status": "incomplete_capped",
        "last_status": "incomplete_capped",
        "source_commit": "e790fb4f08fd2b4f2299758ed880efb9c990fe9f",
        "run_state_path": str(run_dir / "run-state.json"),
        "resume_attempted": False,
    }
    layout.run_record(edition).parent.mkdir(parents=True, exist_ok=True)
    layout.run_record(edition).write_text(json.dumps(record), encoding="utf-8")
    args = scheduler.build_parser().parse_args(["resume", "--repo-root", str(tmp_path), "--python", sys.executable, "--edition-date", edition, "--test-mode"])
    old_verify = scheduler.verify_checkout
    old_preflight = scheduler.run_preflight
    old_write_health = scheduler.write_operational_receipt
    try:
        scheduler.verify_checkout = lambda *a, **k: "e790fb4f08fd2b4f2299758ed880efb9c990fe9f"
        scheduler.run_preflight = lambda *a, **k: None
        scheduler.write_operational_receipt = lambda *a, **k: SimpleNamespace(receipt_path=tmp_path / "test-work" / "health.json")
        assert scheduler.run_resume(args) == 2
        receipts = list((layout.source_log_dir(edition)).glob("*-status-resume.json"))
        assert receipts
        resume_receipt = json.loads(receipts[-1].read_text(encoding="utf-8"))
        assert resume_receipt["resume_status"] == "continuation_required"
    finally:
        scheduler.verify_checkout = old_verify
        scheduler.run_preflight = old_preflight
        scheduler.write_operational_receipt = old_write_health


def test_actual_scheduler_parser_accepts_capped_status_payload():
    payload, parse_status = scheduler._parse_child_terminal_result(json.dumps({"status": "incomplete_capped"}))
    assert parse_status == "parsed"
    assert payload["status"] == "incomplete_capped"


def test_actual_scheduler_acceptance_binds_run_and_date():
    good = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout=json.dumps({"status": "incomplete_capped", "run_id": "r1", "edition_date": "2026-10-24", "run_state_path": "state.json"}),
        stderr="",
    )
    bad = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout=json.dumps({"status": "incomplete_capped", "run_id": "other", "edition_date": "2026-10-24", "run_state_path": "state.json"}),
        stderr="",
    )
    assert scheduler._accept_status_inspection_result(good, expected_run_id="r1", expected_edition_date="2026-10-24", expected_state_path="state.json")
    assert not scheduler._accept_status_inspection_result(bad, expected_run_id="r1", expected_edition_date="2026-10-24", expected_state_path="state.json")


def test_actual_scheduler_rejects_malformed_exit_zero_and_contradictions():
    cases = [
        subprocess.CompletedProcess([], 0, "not-json", ""),
        subprocess.CompletedProcess([], 0, json.dumps({"status": "incomplete_capped", "run_id": "r1", "edition_date": "2026-10-24", "run_state_path": "state.json", "fatal_error": "boom"}), ""),
        subprocess.CompletedProcess([], 1, json.dumps({"status": "incomplete_capped", "run_id": "r1", "edition_date": "2026-10-24", "run_state_path": "other.json", "resumable": True}), ""),
        subprocess.CompletedProcess([], 0, json.dumps({"run_id": "r1", "edition_date": "2026-10-24", "run_state_path": "state.json", "fatal_error": "boom"}), ""),
        subprocess.CompletedProcess([], 0, json.dumps({"status": "unknown", "run_id": "r1", "edition_date": "2026-10-24", "run_state_path": "state.json"}), ""),
        subprocess.CompletedProcess([], 0, json.dumps({"status": "incomplete_capped", "run_id": "r1", "edition_date": "2026-10-24", "run_state_path": "C:/wrong/run-state.json"}), ""),
    ]
    for case in cases:
        assert not scheduler._accept_status_inspection_result(case, expected_run_id="r1", expected_edition_date="2026-10-24", expected_state_path="state.json")


def test_actual_collector_contract_fields_are_truthful_capable():
    collector = importlib.import_module("bluefern_dispatches.food_line_discovery_expansion")
    rows = [{"query_error": ""}] * 7
    rows += [{"query_error": "HTTPError: HTTP Error 503: Service Unavailable"}] * 191
    rows += [{"query_error": "TimeoutError: The read operation timed out"}] * 2
    counts = collector._query_disposition_counts(rows)
    assert counts == {"queries_attempted": 200, "queries_succeeded": 7, "queries_failed": 191, "queries_timed_out": 2}


def test_actual_collector_distinguishes_empty_malformed_and_valid_empty_feeds(tmp_path):
    collector = importlib.import_module("bluefern_dispatches.food_line_discovery_expansion")
    assert collector._parse_google_news_rss(b"<rss><channel></channel></rss>") == []
    old_plan = collector.build_food_line_discovery_query_plan
    old_config = collector.load_food_line_discovery_expansion_config
    try:
        collector.build_food_line_discovery_query_plan = lambda *a, **k: [{"query_id": "q1", "query_text": "test", "query_family": "test", "discovery_channel": "google_news_rss"}]
        collector.load_food_line_discovery_expansion_config = lambda *a, **k: {"direct_sources": []}
        for label, payload, expected_error in (
            ("empty", b"", "EmptyResponseError: empty response body"),
            ("malformed", b"<rss>", "ParseError"),
            ("valid_empty", b"<rss><channel></channel></rss>", ""),
        ):
            result = collector.run_food_line_discovery_expansion(
                tmp_path / "test-work" / label,
                "2026-10-24",
                fetcher=lambda url, timeout=15, payload=payload: payload,
                max_queries=1,
            )
            row = result["query_rows"][0]
            assert (row.get("query_error") or "").startswith(expected_error)
    finally:
        collector.build_food_line_discovery_query_plan = old_plan
        collector.load_food_line_discovery_expansion_config = old_config


def test_actual_direct_empty_feed_uses_authoritative_fetch_outcome(tmp_path):
    collector = importlib.import_module("bluefern_dispatches.food_line_discovery_expansion")
    old_plan = collector.build_food_line_discovery_query_plan
    old_config = collector.load_food_line_discovery_expansion_config
    try:
        collector.build_food_line_discovery_query_plan = lambda *a, **k: [{
            "query_id": "direct-q1", "query_text": "direct", "query_family": "direct",
            "discovery_channel": "direct_rss", "direct_source_name": "Example",
            "direct_source_feed_url": "https://example.test/feed.xml", "direct_source_enabled": True,
            "edition_date": "2026-10-24", "historical_archive_templates": [],
        }]
        collector.load_food_line_discovery_expansion_config = lambda *a, **k: {"direct_sources": [{
            "source_name": "Example", "feed_url": "https://example.test/feed.xml", "source_url": "",
            "enabled": True, "allowed_domains": ["example.test"],
        }]}
        success = collector.run_food_line_discovery_expansion(
            tmp_path / "test-work" / "direct-empty-success", "2026-10-24",
            fetcher=lambda url, timeout=15: b"<rss><channel></channel></rss>", max_queries=1,
        )
        assert success["direct_source_fetch_attempt_count"] == 1
        assert success["direct_source_fetch_success_count"] == 1
        failure = collector.run_food_line_discovery_expansion(
            tmp_path / "test-work" / "direct-empty-failure", "2026-10-24",
            fetcher=lambda url, timeout=15: b"", max_queries=1,
        )
        assert failure["direct_source_fetch_attempt_count"] == 1
        assert failure["direct_source_fetch_success_count"] == 0
        assert failure["direct_source_fetch_failure_count"] == 1
        assert failure["ok"] is False
    finally:
        collector.build_food_line_discovery_query_plan = old_plan
        collector.load_food_line_discovery_expansion_config = old_config


def test_actual_coverage_uses_successes_not_attempts(tmp_path):
    state, _ = wrapper._bounded_state_from_result(
        root=tmp_path / "test-work",
        edition_date="2026-10-24",
        run_id="integration-coverage",
        query_plan=[{"query_id": str(i)} for i in range(1380)],
        result={"ok": False, "status": "incomplete_capped", "queries_completed": 200, "queries_attempted": 200, "queries_succeeded": 7, "queries_failed": 191, "queries_timed_out": 2, "queries_deferred": 1180, "direct_source_fetch_attempt_count": 0, "direct_source_fetch_success_count": 0},
    )
    assert state["coverage"]["required_success_ratio"] == 7 / 1380


def test_actual_direct_coverage_uses_direct_denominator_and_fails_closed_on_missing_counters(tmp_path):
    state, _ = wrapper._bounded_state_from_result(
        root=tmp_path / "test-work",
        edition_date="2026-10-24",
        run_id="integration-direct",
        query_plan=[{"query_id": str(i)} for i in range(200)],
        result={"ok": True, "status": "completed", "queries_completed": 200, "queries_attempted": 200, "queries_succeeded": 190, "queries_failed": 10, "queries_timed_out": 0, "direct_source_fetch_attempt_count": 10, "direct_source_fetch_success_count": 0},
    )
    assert state["coverage"]["direct_success_ratio"] == 0.0
    missing, _ = wrapper._bounded_state_from_result(
        root=tmp_path / "test-work",
        edition_date="2026-10-24",
        run_id="integration-missing-counters",
        query_plan=[{"query_id": "q1"}],
        result={"ok": True, "status": "completed", "queries_completed": 1},
    )
    assert missing["counter_integrity_status"] == "missing_or_inconsistent"
    assert missing["coverage"]["required_success_ratio"] == 0.0


def test_actual_legacy_contract_marks_deferred_rows_incomplete_without_timed_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_plan = wrapper.build_food_line_discovery_query_plan
    old_run = wrapper.run_food_line_discovery_expansion
    try:
        wrapper.build_food_line_discovery_query_plan = lambda *a, **k: [{"query_id": "q1"}, {"query_id": "q2"}]
        wrapper.run_food_line_discovery_expansion = lambda *a, **k: {
            "ok": True,
            "status": "completed",
            "queries_attempted": 1,
            "queries_completed": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "queries_deferred": 1,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        }
        args = wrapper.build_parser().parse_args(["--date", "2026-10-24", "--run-id", "legacy-capped", "--max-queries", "1"])
        result = wrapper._run_legacy_bounded_contract(args)
        assert result["status"] == "incomplete_capped"
        assert result["ok"] is False
        assert result["queries_deferred"] == 1
    finally:
        wrapper.build_food_line_discovery_query_plan = old_plan
        wrapper.run_food_line_discovery_expansion = old_run


def test_actual_blocked_export_has_no_inbox_side_effect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_plan = wrapper.build_food_line_discovery_query_plan
    old_run = wrapper.run_food_line_discovery_expansion
    inbox = tmp_path / "test-work" / "blocked-inbox"
    try:
        wrapper.build_food_line_discovery_query_plan = lambda *a, **k: [{"query_id": "q1"}, {"query_id": "q2"}]
        wrapper.run_food_line_discovery_expansion = lambda *a, **k: {
            "ok": True,
            "status": "completed",
            "queries_attempted": 1,
            "queries_completed": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "queries_deferred": 1,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        }
        args = wrapper.build_parser().parse_args([
            "--date", "2026-10-24", "--run-id", "blocked-export", "--max-queries", "1",
            "--export-agent-inbox", "--agent-inbox-dir", str(inbox),
        ])
        result = wrapper._run_legacy_bounded_contract(args)
        assert result["status"] == "incomplete_capped"
        assert not inbox.exists() or not any(inbox.rglob("*"))
    finally:
        wrapper.build_food_line_discovery_query_plan = old_plan
        wrapper.run_food_line_discovery_expansion = old_run


def test_actual_no_run_id_route_preserves_deferred_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_run = wrapper.run_food_line_discovery_expansion
    output = io.StringIO()
    try:
        wrapper.run_food_line_discovery_expansion = lambda *a, **k: {
            "ok": True, "status": "completed", "queries_attempted": 1,
            "queries_completed": 1, "queries_succeeded": 1, "queries_failed": 0,
            "queries_timed_out": 0, "queries_deferred": 1,
        }
        with contextlib.redirect_stdout(output):
            assert wrapper.main(["--date", "2026-10-24", "--max-queries", "1"]) == 1
        payload = json.loads(output.getvalue())
        assert payload["status"] == "incomplete_capped"
        assert payload["ok"] is False
    finally:
        wrapper.run_food_line_discovery_expansion = old_run


def test_actual_all_failed_uncapped_run_cannot_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_plan = wrapper.build_food_line_discovery_query_plan
    old_run = wrapper.run_food_line_discovery_expansion
    inbox = tmp_path / "test-work" / "all-failed-inbox"
    try:
        wrapper.build_food_line_discovery_query_plan = lambda *a, **k: [{"query_id": "q1"}, {"query_id": "q2"}]
        wrapper.run_food_line_discovery_expansion = lambda *a, **k: {
            "ok": False, "status": "completed", "queries_attempted": 2,
            "queries_completed": 2, "queries_succeeded": 0, "queries_failed": 2,
            "queries_timed_out": 0, "queries_deferred": 0,
            "query_plan_available_count": 2,
            "direct_source_fetch_attempt_count": 0, "direct_source_fetch_success_count": 0,
        }
        args = wrapper.build_parser().parse_args([
            "--date", "2026-10-24", "--run-id", "all-failed", "--export-agent-inbox",
            "--agent-inbox-dir", str(inbox),
        ])
        result = wrapper._run_legacy_bounded_contract(args)
        assert result["ok"] is False
        assert not inbox.exists() or not any(inbox.rglob("*"))
    finally:
        wrapper.build_food_line_discovery_query_plan = old_plan
        wrapper.run_food_line_discovery_expansion = old_run


def test_actual_missing_direct_counters_cannot_export_despite_successful_queries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_plan = wrapper.build_food_line_discovery_query_plan
    old_run = wrapper.run_food_line_discovery_expansion
    inbox = tmp_path / "test-work" / "missing-direct-counter-inbox"
    try:
        wrapper.build_food_line_discovery_query_plan = lambda *a, **k: [{"query_id": str(i)} for i in range(200)]
        wrapper.run_food_line_discovery_expansion = lambda *a, **k: {
            "ok": True, "status": "completed", "queries_attempted": 200,
            "queries_completed": 200, "queries_succeeded": 200, "queries_failed": 0,
            "queries_timed_out": 0, "queries_deferred": 0,
        }
        args = wrapper.build_parser().parse_args([
            "--date", "2026-10-24", "--run-id", "missing-direct", "--export-agent-inbox",
            "--agent-inbox-dir", str(inbox),
        ])
        result = wrapper._run_legacy_bounded_contract(args)
        assert result["ok"] is False
        assert result["status"] == "collection_failure"
        assert result["final_error"] == "query disposition counters missing or inconsistent"
        assert result["agent_export"]["status"] == "blocked_incomplete_collection"
        assert "agent_export_path" not in result
        assert not inbox.exists() or not any(inbox.rglob("*"))
        print("CAPTURE missing-direct", json.dumps({
            "exit": 1 if not result["ok"] else 0,
            "ok": result["ok"], "status": result["status"],
            "final_error": result["final_error"],
            "agent_export_status": result["agent_export"]["status"],
            "agent_export_file_present": bool(result.get("agent_export_path")) or (inbox.exists() and any(inbox.rglob("*"))),
        }, sort_keys=True))
    finally:
        wrapper.build_food_line_discovery_query_plan = old_plan
        wrapper.run_food_line_discovery_expansion = old_run


def test_actual_valid_state_keeps_success_and_export_control(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_plan = wrapper.build_food_line_discovery_query_plan
    old_run = wrapper.run_food_line_discovery_expansion
    inbox = tmp_path / "test-work" / "valid-inbox"
    try:
        wrapper.build_food_line_discovery_query_plan = lambda *a, **k: [{"query_id": str(i)} for i in range(10)]
        wrapper.run_food_line_discovery_expansion = lambda *a, **k: {
            "ok": True, "status": "completed", "candidate_count": 1,
            "queries_attempted": 10, "queries_completed": 10, "queries_succeeded": 10,
            "queries_failed": 0, "queries_timed_out": 0, "queries_deferred": 0,
            "direct_source_fetch_attempt_count": 0, "direct_source_fetch_success_count": 0,
        }
        args = wrapper.build_parser().parse_args([
            "--date", "2026-10-24", "--run-id", "valid-control", "--export-agent-inbox",
            "--agent-inbox-dir", str(inbox),
        ])
        result = wrapper._run_legacy_bounded_contract(args)
        assert result["ok"] is True
        assert result["status"] == "success"
        assert result["agent_export"]["status"] in {"success", "success_with_exclusions"}
        print("CAPTURE valid-control", json.dumps({
            "exit": 0 if result["ok"] else 1,
            "ok": result["ok"], "status": result["status"],
            "final_error": result.get("final_error") or "",
            "agent_export_status": result["agent_export"]["status"],
            "agent_export_file_present": bool(result.get("agent_export_path")) and inbox.exists(),
        }, sort_keys=True))
    finally:
        wrapper.build_food_line_discovery_query_plan = old_plan
        wrapper.run_food_line_discovery_expansion = old_run


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} integration regressions passed")



