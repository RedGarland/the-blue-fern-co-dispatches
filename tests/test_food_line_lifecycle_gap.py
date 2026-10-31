"""Regression coverage for the real source-watch/continuation lifecycle.

The fixture uses only synthetic query rows and in-memory child results.  It
does not invoke a provider, SQLite, the scheduler process, or any production
path.  The test deliberately starts with the source-watch ledger initializer,
then resumes the pending stable IDs, and finally commits the existing private
source-stage intake handoff.
"""
from __future__ import annotations

import json
import hashlib
import contextlib
import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote_plus, urlparse

import pytest


PACKAGE = Path(__file__).resolve().parents[1]
SRC = PACKAGE / "src"
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(SRC))

from scripts import food_line_continuation_ledger as ledger  # noqa: E402
from scripts import food_line_daily_scheduler as scheduler  # noqa: E402
from scripts import food_line_stable_source_watch as stable_watch  # noqa: E402
from scripts import run_food_line_continuation as continuation  # noqa: E402
from scripts import run_food_line_discovery_expansion as discovery_wrapper  # noqa: E402
from bluefern_dispatches import food_line_discovery_expansion as discovery_source  # noqa: E402


def _rows(count: int) -> list[dict[str, object]]:
    return [
        {
            "query_family": "synthetic",
            "query_text": f"synthetic query {index}",
            "discovery_channel": "google_news_rss",
            "edition_date": "2026-10-31",
            "source_family": "fixture",
        }
        for index in range(count)
    ]


def _outcome(*, candidates: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "status": "completed",
        "ok": True,
        "terminal": True,
        "uncertain": False,
        "required_coverage_success": True,
        "direct_source_attempted": False,
        "direct_source_success": False,
        "query_rows": [],
        "candidate_count": len(candidates or []),
        "exclusion_count": 0,
        "candidates": list(candidates or []),
        "collection_only": True,
    }


def test_first_source_watch_batch_uses_same_checkpointed_executor(tmp_path):
    rows = _rows(2)
    provider_calls = []
    checkpoints = []

    def fake_runner(_root, _edition_date, **kwargs):
        row = kwargs["query_plan_override"][0]
        provider_calls.append(row["query_text"])
        candidate = {"candidate_id": row["query_text"], "source_url": f"https://example.test/{row['query_text']}"}
        return {
            "status": "completed",
            "ok": True,
            "query_rows": [dict(row)],
            "candidates": [candidate],
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        }

    deadline = continuation.datetime.now(continuation.timezone.utc) + continuation.timedelta(minutes=30)
    observed_deadlines = []

    def deadline_runner(_root, _edition_date, **kwargs):
        observed_deadlines.append(kwargs.get("runtime_deadline"))
        return fake_runner(_root, _edition_date, **kwargs)

    aggregate = continuation.execute_stable_claimed_batch(
        tmp_path,
        "2026-10-31",
        rows,
        lambda query_id, outcome: checkpoints.append((query_id, outcome)),
        runner=deadline_runner,
        runtime_deadline=deadline,
    )
    assert provider_calls == ["synthetic query 0", "synthetic query 1"]
    assert len(checkpoints) == 2
    assert set(aggregate["outcomes"]) == {ledger.stable_query_id(row) for row in rows}
    assert len(aggregate["candidates"]) == 2
    assert observed_deadlines == [deadline, deadline]


def test_stable_executor_does_not_start_provider_attempt_after_deadline(tmp_path):
    rows = _rows(2)
    provider_calls = []

    def runner(_root, _edition_date, **_kwargs):
        provider_calls.append(True)
        return {"status": "completed", "ok": True, "query_rows": [], "candidates": []}

    with pytest.raises(continuation.StableBatchDeadlineExceeded):
        continuation.execute_stable_claimed_batch(
            tmp_path,
            "2026-10-31",
            rows,
            lambda *_args: None,
            runner=runner,
            runtime_deadline=continuation.datetime.now(continuation.timezone.utc),
        )
    assert provider_calls == []


def test_stable_executor_quarantines_collector_zero_attempt_timeout(tmp_path):
    row = _rows(1)[0]
    provider_calls = []
    checkpoints = []

    def runner(_root, _edition_date, **_kwargs):
        provider_calls.append(True)
        return {
            "status": "timed_out",
            "ok": False,
            "query_rows": [],
            "candidates": [],
            "queries_attempted": 0,
            "queries_timed_out": 0,
        }

    with pytest.raises(continuation.StableBatchDeadlineExceeded, match="before provider attempt"):
        continuation.execute_stable_claimed_batch(
            tmp_path,
            "2026-10-31",
            [row],
            lambda query_id, outcome: checkpoints.append((query_id, outcome)),
            runner=runner,
            runtime_deadline=continuation.datetime.now(continuation.timezone.utc) + continuation.timedelta(minutes=30),
        )
    assert provider_calls == [True]
    assert checkpoints == []


def test_stable_executor_counts_attempted_timeout_only_with_a_query_disposition(tmp_path):
    row = _rows(1)[0]
    checkpoints = []

    def runner(_root, _edition_date, **_kwargs):
        return {
            "status": "timed_out",
            "ok": False,
            "query_rows": [dict(row)],
            "candidates": [],
            "queries_attempted": 1,
            "queries_timed_out": 1,
            "queries_failed": 0,
        }

    aggregate = continuation.execute_stable_claimed_batch(
        tmp_path,
        "2026-10-31",
        [row],
        lambda query_id, outcome: checkpoints.append((query_id, outcome)),
        runner=runner,
        runtime_deadline=continuation.datetime.now(continuation.timezone.utc) + continuation.timedelta(minutes=30),
    )
    assert aggregate["queries_attempted"] == 1
    assert aggregate["queries_processed_this_invocation"] == 1
    assert aggregate["queries_timed_out"] == 1
    assert len(checkpoints) == 1
    assert checkpoints[0][1]["uncertain"] is True


def test_stable_outcome_marks_timeout_uncertain_and_checkpoint_validation_rejects_false_success():
    row = _rows(1)[0]
    outcome = continuation.stable_outcome_for(
        row,
        {"status": "timed_out", "ok": False, "query_rows": [], "candidates": []},
    )
    assert outcome["terminal"] is False
    assert outcome["uncertain"] is True
    assert outcome["required_coverage_success"] is False
    with pytest.raises(ledger.ContinuationLedgerError):
        continuation._validate_stable_checkpoint(
            ledger.stable_query_id(row),
            {**outcome, "uncertain": False, "terminal": True, "required_coverage_success": True, "ok": True},
        )


def test_export_gate_rejects_incomplete_and_accepts_complete_source_watch(tmp_path, monkeypatch):
    rows = _rows(205)
    monkeypatch.setattr(discovery_wrapper, "build_food_line_discovery_query_plan", lambda _root, _date: rows)

    base = {
        "ok": True,
        "queries_attempted": 200,
        "queries_succeeded": 200,
        "queries_failed": 0,
        "queries_timed_out": 0,
        "queries_completed": 200,
        "direct_source_fetch_attempt_count": 0,
        "direct_source_fetch_success_count": 0,
    }
    incomplete = {**base, "status": "incomplete_capped", "query_plan_available_count": 205, "query_plan_bounded_count": 200, "queries_deferred": 5}
    complete = {**base, "status": "completed", "queries_attempted": 205, "queries_succeeded": 205, "queries_completed": 205, "query_plan_available_count": 205, "query_plan_bounded_count": 205, "queries_deferred": 0}
    assert discovery_wrapper._export_allowed(tmp_path, "2026-10-31", "gate-incomplete", incomplete) is False
    assert discovery_wrapper._export_allowed(tmp_path, "2026-10-31", "gate-complete", complete) is True


def test_stable_source_watch_calls_export_only_after_gate(tmp_path, monkeypatch):
    root = tmp_path / "runner"
    source = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source.parent.mkdir(parents=True)
    config.parent.mkdir(parents=True)
    source.write_bytes(b"synthetic source\n")
    config.write_bytes(b"{}\n")
    directory = root / "continuation" / "2026-10-31" / "stable-watch"
    directory.mkdir(parents=True)
    rows = _rows(2)
    (directory / "continuation-query-plan.json").write_text(
        json.dumps({"query_ids": [ledger.stable_query_id(row) for row in rows], "query_rows": rows, "plan_sha256": "p"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(stable_watch, "load_ledger", lambda *_args, **_kwargs: {"in_flight_query_ids": [ledger.stable_query_id(rows[0])]})
    observed_deadlines = []

    def fake_batch(_root, _date, _rows, _checkpoint, *, runtime_deadline=None):
        observed_deadlines.append(runtime_deadline)
        return {
            "query_rows": [dict(_rows[0])],
            "candidates": [{"candidate_id": "kept"}],
            "queries_attempted": 1,
            "queries_processed_this_invocation": 1,
            "queries_completed": 1,
            "queries_succeeded": 1,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        }

    monkeypatch.setattr(stable_watch, "execute_stable_claimed_batch", fake_batch)
    monkeypatch.setattr(stable_watch, "_write_state_files", lambda *_args, **_kwargs: {"ok": True, "status": "incomplete_capped"})
    export_calls = []
    monkeypatch.setattr(stable_watch, "_export_allowed", lambda *_args: False)
    monkeypatch.setattr(stable_watch, "_export_agent_inbox", lambda *_args, **_kwargs: export_calls.append(True))
    args = SimpleNamespace(
        repo_root=str(root),
        continuation_run_directory=str(directory),
        edition_date="2026-10-31",
        run_id="stable-watch",
        source_identity="source",
        max_queries=1,
        max_run_minutes=30.0,
        collection_only=False,
        agent_inbox_dir=str(root / "inbox"),
    )
    result = stable_watch.run(args)
    assert result["status"] == "incomplete_capped"
    assert export_calls == []
    assert len(observed_deadlines) == 1
    assert 29.0 * 60 <= (observed_deadlines[0] - stable_watch.datetime.now(stable_watch.timezone.utc)).total_seconds() <= 30.0 * 60


def test_full_source_watch_resume_intake_preserves_first_batch_payloads(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-runner"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"synthetic source binding\n")
    config_path.write_bytes(b"{}\n")
    query_rows = _rows(205)
    monkeypatch.setattr(scheduler, "build_food_line_discovery_query_plan", lambda _root, _date: query_rows)

    edition_date = "2026-10-31"
    run_id = "fixture-run-205"
    source_identity = "a" * 40
    continuation_dir, initial = scheduler._initialize_source_watch_ledger(
        root=root,
        edition_date=edition_date,
        run_id=run_id,
        source_identity=source_identity,
    )
    assert len(initial["planned_query_ids"]) == 205
    claimed = ledger.claim_source_watch_batch(
        continuation_dir,
        binding=dict(initial["binding"]),
        batch_size=200,
    )

    first_candidates = [
        {"candidate_id": f"first-{index}", "source_url": f"https://example.test/{index}"}
        for index in range(3)
    ]
    for index, (query_id, row) in enumerate(zip(claimed["in_flight_query_ids"], query_rows[:200], strict=True)):
        checkpoint = _outcome(candidates=first_candidates if index == 0 else [])
        checkpoint["query_rows"] = [dict(row)]
        ledger.checkpoint_in_flight_outcome(
            continuation_dir,
            binding=dict(initial["binding"]),
            query_id=query_id,
            outcome=checkpoint,
        )
    child_payload = {
        "edition_date": edition_date,
        "run_id": run_id,
        "status": "incomplete_capped",
        "ok": False,
        "execution_mode": "export_enabled",
        "query_rows": [dict(row) for row in query_rows[:200]],
        "candidates": first_candidates,
    }
    captured = scheduler._capture_source_watch_ledger(
        continuation_directory=continuation_dir,
        ledger_state=initial,
        result=subprocess.CompletedProcess(["fixture"], 1, json.dumps(child_payload), ""),
        edition_date=edition_date,
        run_id=run_id,
    )
    assert len(captured["completed_query_ids"]) == 200
    assert len(captured["pending_query_ids"]) == 5
    assert len(captured["candidate_payloads"]) == 3

    assert captured["source_watch_capture"]["status"] == "captured"


    legacy_state_path = (
        root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id / "run-state.json"
    )
    legacy_state_path.parent.mkdir(parents=True)
    legacy_state_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_bounded_run_state_v1",
                "edition_date": edition_date,
                "run_id": run_id,
                "status": "incomplete_capped",
                "resumable": False,
                "started_at": "2026-10-31T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    binding = dict(captured["binding"])
    resumed_ids: list[str] = []

    def resume_fixture(rows):
        outcomes = {}
        for row in rows:
            query_id = ledger.stable_query_id(row)
            resumed_ids.append(query_id)
            outcomes[query_id] = _outcome(
                candidates=[
                    {
                        "candidate_id": f"resume-{row['query_text']}",
                        "source_url": f"https://example.test/{row['query_text'].replace(' ', '-')}",
                    }
                ]
            )
        return outcomes

    final_state = ledger.continue_ledger(
        continuation_dir,
        binding=binding,
        batch_size=200,
        batch_executor=resume_fixture,
    )
    assert len(resumed_ids) == 5
    assert set(resumed_ids).isdisjoint(set(captured["completed_query_ids"]))
    assert final_state["pending_query_ids"] == []
    assert final_state["in_flight_query_ids"] == []
    assert final_state["unknown_query_ids"] == []
    assert len(final_state["completed_query_ids"]) == 205
    assert len(final_state["candidate_payloads"]) == 8

    assessment = ledger.source_stage_handoff_assessment(final_state)
    assert assessment["intake_eligible"] is True
    assert assessment["completed_count"] == 205
    assert assessment["required_success_ratio"] == 1.0

    updated, handoff = continuation.commit_source_stage_handoff(
        root,
        edition_date=edition_date,
        run_id=run_id,
        continuation_directory=continuation_dir,
        ledger_state=final_state,
        source_identity=source_identity,
    )
    assert updated["intake_eligible"] is True
    assert handoff["intake_eligible"] is True
    export = json.loads((legacy_state_path.parent / "continuation-agent-export.json").read_text(encoding="utf-8"))
    assert {row["candidate_id"] for row in export["findings"]} == {
        *{row["candidate_id"] for row in first_candidates},
        *{f"resume-synthetic query {index}" for index in range(200, 205)},
    }


def test_entrypoint_chain_source_watch_resume_intake_uses_real_private_flow(tmp_path, monkeypatch):
    root = tmp_path / "entrypoint-runner"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    source_path.write_bytes(b"synthetic source binding\n")
    edition_date = "2026-10-31"
    run_id = "entrypoint-chain-205"
    families = [
        "food_bank_provider",
        "public_radio",
        "school_meals_child_nutrition",
        "nonprofit_report",
        "snap_state_notice",
        "united_way_211",
        "news_article",
    ]
    query_rows = [
        {
            "query_family": families[index % len(families)],
            "query_text": f"synthetic query {index}",
            "discovery_channel": "google_news_rss",
            "edition_date": edition_date,
            "source_family": "fixture",
        }
        for index in range(205)
    ]
    for module in (scheduler, stable_watch, continuation, discovery_wrapper):
        monkeypatch.setattr(module, "build_food_line_discovery_query_plan", lambda _root, _date, rows=query_rows: rows)
    monkeypatch.setattr(scheduler, "verify_checkout", lambda *_args, **_kwargs: "e" * 40)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda _path: [])

    network_urls: list[str] = []

    def fixture_fetcher(url: str, *, timeout: int = 15) -> bytes:
        del timeout
        network_urls.append(url)
        parsed = urlparse(url)
        if parsed.netloc == "news.google.com":
            query = parse_qs(parsed.query).get("q", [""])[0]
            query_index = int(unquote_plus(query).rsplit(" ", 1)[-1])
            article_url = f"https://example.test/article/{query_index}"
            return (
                "<?xml version=\"1.0\"?><rss><channel><item>"
                f"<title>Food pantry {query_index} reports empty shelves as demand rises</title>"
                f"<link>{article_url}</link>"
                "<description>Food pantry shortages and rising hunger demand are affecting families.</description>"
                "<pubDate>Sat, 31 Oct 2026 12:00:00 GMT</pubDate>"
                "<source url=\"https://example.test/news\">Synthetic News</source>"
                "</item></channel></rss>"
            ).encode("utf-8")
        if parsed.netloc == "example.test" and parsed.path.startswith("/article/"):
            article_url = f"https://example.test{parsed.path}"
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                f"<title>Food pantry {parsed.path.rsplit('/', 1)[-1]} reports shortages</title>"
                "</head><body>Food pantry shelves are empty as demand rises, with families facing hunger pressure.</body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected synthetic transport URL: {url}")

    monkeypatch.setattr(discovery_source, "resolve_food_line_fetcher", lambda _fetcher=None: fixture_fetcher)
    child_commands: list[list[str]] = []
    child_results: list[subprocess.CompletedProcess[str]] = []
    repository_root = Path(__file__).resolve().parents[1]
    intake_script = repository_root / "scripts" / "process_food_line_current_intake.py"
    intake_dependencies = (
        repository_root / "src" / "bluefern_dispatches" / "food_line_current_review.py",
        repository_root / "src" / "bluefern_dispatches" / "source_based_qualification.py",
        repository_root / "src" / "bluefern_dispatches" / "agent_findings.py",
        repository_root / "src" / "bluefern_dispatches" / "adapters" / "food_line_agent.py",
    )
    assert intake_script.is_file(), f"repository intake entrypoint missing: {intake_script}"
    assert all(path.is_file() for path in intake_dependencies)

    def dispatch_child(_python, _root, command):
        child_commands.append(list(command))
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                if command[0] == "scripts/food_line_stable_source_watch.py":
                    child_args = stable_watch.build_parser().parse_args(command[1:])
                    code = 0
                    payload = stable_watch.run(child_args)
                    print(json.dumps(payload, sort_keys=True))
                elif command[0] == "scripts/run_food_line_continuation.py":
                    child_args = continuation.build_parser().parse_args(command[1:])
                    code = continuation.run(child_args)
                    payload = None
                elif command[0] == "scripts/run_food_line_discovery_expansion.py" and "--status-run" in command:
                    # This is the existing read-only status CLI contract boundary;
                    # parser/validation behavior is covered by scheduler tests.
                    status_run_id = command[command.index("--status-run") + 1]
                    status_date = command[command.index("--date") + 1]
                    state_path = root / "data" / "dispatches" / "food-line" / "discovery-runs" / status_date / status_run_id / "run-state.json"
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    payload = {
                        "status": state["status"],
                        "run_id": status_run_id,
                        "edition_date": status_date,
                        "run_state_path": str(state_path),
                        "resumable": state.get("resumable"),
                    }
                    code = 0
                    print(json.dumps(payload, sort_keys=True))
                elif command[0] == "scripts/process_food_line_current_intake.py":
                    if not intake_script.is_file():
                        raise AssertionError(f"repository intake entrypoint missing: {intake_script}")
                    completed = subprocess.run(
                        [sys.executable, "-I", "-B", str(intake_script), *command[1:]],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    code = completed.returncode
                    stdout.write(completed.stdout)
                    stderr.write(completed.stderr)
                    payload = None
                else:
                    raise AssertionError(f"unexpected child command: {command}")
        except Exception as exc:  # noqa: BLE001
            code = 1
            payload = {"status": "child_process_failure", "error": str(exc)}
            print(json.dumps(payload, sort_keys=True), file=stdout)
        result = subprocess.CompletedProcess(command, code, stdout.getvalue(), stderr.getvalue())
        child_results.append(result)
        return result

    monkeypatch.setattr(scheduler, "_invoke_python", dispatch_child)
    source_args = SimpleNamespace(
        repo_root=str(root),
        python=sys.executable,
        edition_date=edition_date,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
        run_id=run_id,
        collection_only=False,
    )
    assert scheduler.run_source_watch(source_args) == 2
    continuation_dir = root / "data" / "dispatches" / "food-line" / "continuation" / edition_date / run_id
    source_ledger = json.loads((continuation_dir / ledger.LEDGER_NAME).read_text(encoding="utf-8"))
    assert len(source_ledger["completed_query_ids"]) == 200
    assert source_ledger["pending_query_ids"] == [ledger.stable_query_id(row) for row in query_rows[200:]]
    first_batch_candidate_ids = {
        str(payload["candidate_id"])
        for payload in source_ledger["candidate_payloads"].values()
        if isinstance(payload, dict)
    }
    assert first_batch_candidate_ids

    resume_args = scheduler.build_parser().parse_args(
        [
            "resume", "--repo-root", str(root), "--python", sys.executable,
            "--edition-date", edition_date, "--test-mode",
            "--continuation-run-root", str(root / scheduler.CONTINUATION_ROOT_RELATIVE),
        ]
    )
    assert scheduler.run_resume(resume_args) == 0
    resumed_ledger = json.loads((continuation_dir / ledger.LEDGER_NAME).read_text(encoding="utf-8"))
    assert resumed_ledger["pending_query_ids"] == []
    assert resumed_ledger["in_flight_query_ids"] == []
    assert resumed_ledger["unknown_query_ids"] == []
    assert len(resumed_ledger["completed_query_ids"]) == 205
    assert first_batch_candidate_ids <= {
        str(payload["candidate_id"])
        for payload in resumed_ledger["candidate_payloads"].values()
        if isinstance(payload, dict)
    }

    intake_args = scheduler.build_parser().parse_args(
        ["intake", "--repo-root", str(root), "--python", sys.executable, "--edition-date", edition_date, "--test-mode"]
    )
    intake_code = scheduler.run_intake(intake_args)
    if intake_code != 0:
        intake_failures = [
            result
            for result in child_results
            if result.args and result.args[0] == "scripts/process_food_line_current_intake.py"
        ]
        details = "\n".join(
            f"code={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
            for result in intake_failures
        )
        raise AssertionError(f"actual intake child failed before report creation:\n{details}")
    assert intake_code == 0
    intake_reports = list((root / "logs" / "food-line" / "current-intake" / edition_date).glob("*.json"))
    assert intake_reports
    intake_report = json.loads(intake_reports[-1].read_text(encoding="utf-8"))
    assert intake_report["exit_code"] == 0
    assert not any(intake_report["publication_side_effects"].values())

    def command_value(command: list[str], flag: str) -> str:
        assert flag in command, f"missing {flag} in child command: {command}"
        return command[command.index(flag) + 1]

    stable_commands = [
        command for command in child_commands
        if command[0] == "scripts/food_line_stable_source_watch.py"
    ]
    continuation_commands = [
        command for command in child_commands
        if command[0] == "scripts/run_food_line_continuation.py"
    ]
    status_commands = [
        command for command in child_commands
        if command[0] == "scripts/run_food_line_discovery_expansion.py"
    ]
    intake_commands = [
        command for command in child_commands
        if command[0] == "scripts/process_food_line_current_intake.py"
    ]
    assert len(stable_commands) == 1
    assert command_value(stable_commands[0], "--date") == edition_date
    assert command_value(stable_commands[0], "--run-id") == run_id
    assert command_value(stable_commands[0], "--source-identity") == "e" * 40
    assert command_value(stable_commands[0], "--max-queries") == "200"
    assert "--collection-only" not in stable_commands[0]
    assert len(continuation_commands) == 1
    assert command_value(continuation_commands[0], "--edition-date") == edition_date
    assert command_value(continuation_commands[0], "--run-id") == run_id
    assert command_value(continuation_commands[0], "--source-identity") == "e" * 40
    assert command_value(continuation_commands[0], "--max-queries") == "200"
    assert "--collection-only" in continuation_commands[0]
    assert any("--status-run" in command for command in status_commands)
    assert len(intake_commands) == 1
    assert command_value(intake_commands[0], "--edition-date") == edition_date
    assert "--build-review-queue" in intake_commands[0]
    assert "--build-proposed-edition" in intake_commands[0]
    assert "--source-file" in intake_commands[0]
    source_export_path = Path(command_value(intake_commands[0], "--source-file"))
    assert source_export_path.is_file()
    source_export = json.loads(source_export_path.read_text(encoding="utf-8"))
    exported_candidate_ids = {
        str(candidate["candidate_id"])
        for candidate in source_export["findings"]
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    assert first_batch_candidate_ids <= exported_candidate_ids

    network_count_after_completion = len(network_urls)
    google_query_urls = [url for url in network_urls if urlparse(url).netloc == "news.google.com"]
    assert len(google_query_urls) == 205
    assert len(set(google_query_urls)) == 205
    continuation_count = sum(command[0] == "scripts/run_food_line_continuation.py" for command in child_commands)
    assert continuation_count == 1
    assert scheduler.run_resume(resume_args) == 0
    assert len(network_urls) == network_count_after_completion
    assert len([url for url in network_urls if urlparse(url).netloc == "news.google.com"]) == 205
    assert sum(command[0] == "scripts/run_food_line_continuation.py" for command in child_commands) == continuation_count
    assert any(command[0] == "scripts/food_line_stable_source_watch.py" for command in child_commands)
    assert any(command[0] == "scripts/process_food_line_current_intake.py" for command in child_commands)
    assert len({query_id for query_id in resumed_ledger["completed_query_ids"]}) == 205


def test_source_watch_capture_rejects_missing_or_foreign_checkpoint(tmp_path):
    rows = _rows(2)
    directory = tmp_path / "missing-checkpoint"
    initial = ledger.prepare_ledger(
        directory,
        edition_date="2026-10-31",
        run_id="missing-checkpoint",
        source_identity="source",
        source_module_sha256="a" * 64,
        config_sha256="b" * 64,
        query_rows=rows,
    )
    claimed = ledger.claim_source_watch_batch(directory, binding=dict(initial["binding"]), batch_size=1)
    with pytest.raises(ledger.ContinuationLedgerError, match="lacks a durable checkpoint"):
        ledger.record_source_watch_batch(
            directory,
            binding=dict(initial["binding"]),
            child_payload={"status": "completed", "query_rows": [dict(rows[0])], "candidates": []},
        )
    assert claimed["in_flight_query_ids"] == [ledger.stable_query_id(rows[0])]
    with pytest.raises(ledger.ContinuationLedgerError, match="claimed query ID"):
        ledger.record_source_watch_batch(
            directory,
            binding=dict(initial["binding"]),
            child_payload={"status": "completed", "query_rows": [dict(rows[1])], "candidates": []},
        )


def test_source_watch_capture_rejects_foreign_or_pending_durable_ids(tmp_path):
    rows = _rows(2)
    foreign_directory = tmp_path / "foreign-durable-id"
    foreign_initial = ledger.prepare_ledger(
        foreign_directory,
        edition_date="2026-10-31",
        run_id="foreign-durable-id",
        source_identity="source",
        source_module_sha256="a" * 64,
        config_sha256="b" * 64,
        query_rows=rows,
    )
    ledger.claim_source_watch_batch(foreign_directory, binding=dict(foreign_initial["binding"]), batch_size=1)
    foreign_state = ledger.load_ledger(foreign_directory, binding=dict(foreign_initial["binding"]))
    foreign_state["query_results"]["not-in-plan"] = _outcome()
    ledger._write_json_atomic(foreign_directory / ledger.LEDGER_NAME, foreign_state)
    with pytest.raises(ledger.ContinuationLedgerError, match="foreign query result IDs"):
        ledger.record_source_watch_batch(
            foreign_directory,
            binding=dict(foreign_initial["binding"]),
            child_payload={"status": "completed", "query_rows": [], "candidates": []},
        )

    pending_directory = tmp_path / "pending-durable-id"
    pending_initial = ledger.prepare_ledger(
        pending_directory,
        edition_date="2026-10-31",
        run_id="pending-durable-id",
        source_identity="source",
        source_module_sha256="a" * 64,
        config_sha256="b" * 64,
        query_rows=rows,
    )
    ledger.claim_source_watch_batch(pending_directory, binding=dict(pending_initial["binding"]), batch_size=1)
    pending_state = ledger.load_ledger(pending_directory, binding=dict(pending_initial["binding"]))
    pending_id = pending_state["pending_query_ids"][0]
    pending_state["query_results"][pending_id] = _outcome()
    ledger._write_json_atomic(pending_directory / ledger.LEDGER_NAME, pending_state)
    with pytest.raises(ledger.ContinuationLedgerError, match="results for pending IDs"):
        ledger.record_source_watch_batch(
            pending_directory,
            binding=dict(pending_initial["binding"]),
            child_payload={"status": "completed", "query_rows": [], "candidates": []},
        )


def test_source_watch_capture_rejects_contradictory_checkpoint(tmp_path):
    rows = _rows(1)
    directory = tmp_path / "contradictory-checkpoint"
    initial = ledger.prepare_ledger(
        directory,
        edition_date="2026-10-31",
        run_id="contradictory-checkpoint",
        source_identity="source",
        source_module_sha256="a" * 64,
        config_sha256="b" * 64,
        query_rows=rows,
    )
    claimed = ledger.claim_source_watch_batch(directory, binding=dict(initial["binding"]), batch_size=1)
    query_id = claimed["in_flight_query_ids"][0]
    checkpoint = _outcome()
    checkpoint["query_rows"] = [dict(rows[0])]
    checkpoint["terminal"] = True
    checkpoint["uncertain"] = True
    ledger.checkpoint_in_flight_outcome(directory, binding=dict(initial["binding"]), query_id=query_id, outcome=checkpoint)
    with pytest.raises(ledger.ContinuationLedgerError, match="checkpoint is contradictory"):
        ledger.record_source_watch_batch(
            directory,
            binding=dict(initial["binding"]),
            child_payload={"status": "completed", "query_rows": [dict(rows[0])], "candidates": []},
        )


def test_source_watch_capture_preserves_terminal_failure_checkpoint(tmp_path):
    rows = _rows(1)
    directory = tmp_path / "terminal-failure-checkpoint"
    initial = ledger.prepare_ledger(
        directory,
        edition_date="2026-10-31",
        run_id="terminal-failure-checkpoint",
        source_identity="source",
        source_module_sha256="a" * 64,
        config_sha256="b" * 64,
        query_rows=rows,
    )
    claimed = ledger.claim_source_watch_batch(directory, binding=dict(initial["binding"]), batch_size=1)
    query_id = claimed["in_flight_query_ids"][0]
    checkpoint = _outcome()
    checkpoint.update({"status": "completed_with_exclusions", "ok": False, "required_coverage_success": False})
    checkpoint["query_rows"] = [dict(rows[0])]
    ledger.checkpoint_in_flight_outcome(directory, binding=dict(initial["binding"]), query_id=query_id, outcome=checkpoint)
    captured = ledger.record_source_watch_batch(
        directory,
        binding=dict(initial["binding"]),
        child_payload={"status": "completed_with_exclusions", "query_rows": [dict(rows[0])], "candidates": []},
    )
    assert captured["in_flight_query_ids"] == []
    assert captured["query_results"][query_id] == checkpoint
    assert captured["query_results"][query_id]["required_coverage_success"] is False


def test_source_watch_capture_quarantines_zero_attempt_timeout_without_checkpoint(tmp_path):
    rows = _rows(2)
    directory = tmp_path / "zero-attempt-timeout"
    initial = ledger.prepare_ledger(
        directory,
        edition_date="2026-10-31",
        run_id="zero-attempt-timeout",
        source_identity="source",
        source_module_sha256="a" * 64,
        config_sha256="b" * 64,
        query_rows=rows,
    )
    claimed = ledger.claim_source_watch_batch(directory, binding=dict(initial["binding"]), batch_size=2)
    captured = ledger.record_source_watch_batch(
        directory,
        binding=dict(initial["binding"]),
        child_payload={"status": "timed_out", "query_rows": [], "candidates": []},
    )
    assert captured["completed_query_ids"] == []
    assert captured["in_flight_query_ids"] == claimed["in_flight_query_ids"]
    assert captured["source_watch_capture"]["status"] == "quarantined"


def test_unknown_reconciliation_merges_candidates_and_handoff_does_not_replay(tmp_path):
    root = tmp_path / "reconciliation-runner"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"synthetic source binding\n")
    config_path.write_bytes(b"{}\n")
    continuation_dir = root / "data" / "dispatches" / "food-line" / "continuation" / "2026-10-31" / "reconcile"
    rows = _rows(1)
    initial = ledger.prepare_ledger(
        continuation_dir,
        edition_date="2026-10-31",
        run_id="reconcile",
        source_identity="a" * 40,
        source_module_sha256="b" * 64,
        config_sha256="c" * 64,
        query_rows=rows,
    )
    binding = dict(initial["binding"])
    batch_calls = 0

    def interrupted_batch(_rows):
        nonlocal batch_calls
        batch_calls += 1
        raise RuntimeError("synthetic interruption after provider uncertainty")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        ledger.continue_ledger(
            continuation_dir,
            binding=binding,
            batch_size=1,
            batch_executor=interrupted_batch,
        )
    uncertain = ledger.load_ledger(continuation_dir, binding=binding)
    query_id = uncertain["unknown_query_ids"][0]
    recovered = {
        "candidate_id": "recovered-after-unknown",
        "source_url": "https://example.test/recovered",
    }
    reconciled = ledger.reconcile_unknown(
        continuation_dir,
        binding=binding,
        verified_results={query_id: _outcome(candidates=[recovered])},
    )
    assert batch_calls == 1
    assert reconciled["unknown_query_ids"] == []
    assert reconciled["candidate_payloads"]["candidate:recovered-after-unknown"] == recovered
    assert ledger.source_stage_handoff_assessment(reconciled)["intake_eligible"] is True

    legacy_state_path = (
        root / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-10-31" / "reconcile" / "run-state.json"
    )
    legacy_state_path.parent.mkdir(parents=True)
    legacy_state_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_bounded_run_state_v1",
                "edition_date": "2026-10-31",
                "run_id": "reconcile",
                "status": "incomplete_capped",
                "resumable": False,
                "started_at": "2026-10-31T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    _, handoff = continuation.commit_source_stage_handoff(
        root,
        edition_date="2026-10-31",
        run_id="reconcile",
        continuation_directory=continuation_dir,
        ledger_state=reconciled,
        source_identity="a" * 40,
    )
    export = json.loads((legacy_state_path.parent / "continuation-agent-export.json").read_text(encoding="utf-8"))
    assert handoff["intake_eligible"] is True
    assert [row["candidate_id"] for row in export["findings"]] == ["recovered-after-unknown"]
    assert batch_calls == 1, "reconciliation and handoff must not replay the uncertain query"


def test_source_watch_claim_survives_crash_before_capture_without_replay(tmp_path):
    directory = tmp_path / "claimed-before-capture"
    rows = _rows(2)
    initial = ledger.prepare_ledger(
        directory,
        edition_date="2026-10-31",
        run_id="crash-before-capture",
        source_identity="a" * 40,
        source_module_sha256="b" * 64,
        config_sha256="c" * 64,
        query_rows=rows,
    )
    binding = dict(initial["binding"])
    claimed = ledger.claim_source_watch_batch(directory, binding=binding, batch_size=2)
    assert claimed["pending_query_ids"] == []
    assert len(claimed["in_flight_query_ids"]) == 2
    attempted = []
    with pytest.raises(ledger.ContinuationLedgerError, match="uncertain or in-flight"):
        ledger.continue_ledger(
            directory,
            binding=binding,
            batch_size=2,
            batch_executor=lambda batch: attempted.append(batch),
        )
    assert attempted == [], "a crashed source child must not replay its claimed IDs"
    verified = {
        query_id: _outcome(candidates=[{"candidate_id": f"verified-{index}"}])
        for index, query_id in enumerate(claimed["in_flight_query_ids"])
    }
    reconciled = ledger.reconcile_unknown(directory, binding=binding, verified_results=verified)
    assert reconciled["in_flight_query_ids"] == []
    assert reconciled["unknown_query_ids"] == []
    assert set(reconciled["completed_query_ids"]) == set(reconciled["planned_query_ids"])


def test_checkpointed_source_watch_candidate_survives_crash_before_parent_capture(tmp_path):
    directory = tmp_path / "checkpoint-before-capture"
    rows = _rows(2)
    initial = ledger.prepare_ledger(
        directory,
        edition_date="2026-10-31",
        run_id="checkpoint-before-capture",
        source_identity="a" * 40,
        source_module_sha256="b" * 64,
        config_sha256="c" * 64,
        query_rows=rows,
    )
    binding = dict(initial["binding"])
    claimed = ledger.claim_source_watch_batch(directory, binding=binding, batch_size=2)
    first_query = claimed["in_flight_query_ids"][0]
    first_candidate = {"candidate_id": "checkpointed-first", "source_url": "https://example.test/checkpointed"}
    checkpointed = _outcome(candidates=[first_candidate])
    ledger.checkpoint_in_flight_outcome(
        directory,
        binding=binding,
        query_id=first_query,
        outcome=checkpointed,
    )
    durable = ledger.load_ledger(directory, binding=binding)
    assert durable["candidate_payloads"]["candidate:checkpointed-first"] == first_candidate
    assert first_query in durable["in_flight_query_ids"]
    replayed = []
    with pytest.raises(ledger.ContinuationLedgerError, match="uncertain or in-flight"):
        ledger.continue_ledger(
            directory,
            binding=binding,
            batch_size=2,
            batch_executor=lambda batch: replayed.append(batch),
        )
    assert replayed == []
    verified = {
        first_query: checkpointed,
        claimed["in_flight_query_ids"][1]: _outcome(
            candidates=[{"candidate_id": "verified-second", "source_url": "https://example.test/second"}]
        ),
    }
    reconciled = ledger.reconcile_unknown(directory, binding=binding, verified_results=verified)
    assert reconciled["candidate_payloads"]["candidate:checkpointed-first"] == first_candidate
    assert "candidate:verified-second" in reconciled["candidate_payloads"]
    assert ledger.source_stage_handoff_assessment(reconciled)["intake_eligible"] is True


def test_real_continuation_entrypoint_checkpoints_each_provider_result(tmp_path, monkeypatch, capsys):
    root = tmp_path / "continuation-entrypoint"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"{}\n")
    edition = "2026-10-31"
    run_id = "entrypoint-checkpointed"
    rows = _rows(2)
    monkeypatch.setattr(continuation, "build_food_line_discovery_query_plan", lambda _root, _date: rows)
    source_module = continuation.SRC / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    source_module_sha256 = hashlib.sha256(source_module.read_bytes()).hexdigest()
    config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest()
    continuation_dir = root / "data" / "dispatches" / "food-line" / "continuation" / edition / run_id
    initial = ledger.prepare_ledger(
        continuation_dir,
        edition_date=edition,
        run_id=run_id,
        source_identity="source-identity",
        source_module_sha256=source_module_sha256,
        config_sha256=config_sha256,
        query_rows=rows,
    )
    calls = []

    def fake_source(*_args, **kwargs):
        row = list(kwargs["query_plan_override"])[0]
        calls.append(ledger.stable_query_id(row))
        return {
            "status": "completed",
            "ok": True,
            "query_rows": [dict(row)],
            "candidates": [{"candidate_id": f"entrypoint-{row['query_text']}", "source_url": "https://example.test/entrypoint"}],
            "candidate_count": 1,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
        }

    monkeypatch.setattr(continuation, "run_food_line_discovery_expansion", fake_source)
    legacy_state_path = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition / run_id / "run-state.json"
    legacy_state_path.parent.mkdir(parents=True)
    legacy_state_path.write_text(
        json.dumps(
            {
                "schema_version": "food_line_bounded_run_state_v1",
                "edition_date": edition,
                "run_id": run_id,
                "status": "incomplete_capped",
                "resumable": False,
                "started_at": "2026-10-31T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        collection_only=True,
        repo_root=str(root),
        edition_date=edition,
        run_id=run_id,
        continuation_run_directory=str(continuation_dir),
        source_identity="source-identity",
        max_queries=2,
    )
    assert continuation.run(args) == 0
    assert len(calls) == 2
    durable = ledger.load_ledger(
        continuation_dir,
        binding={**initial["binding"]},
    )
    assert durable["pending_query_ids"] == []
    assert durable["in_flight_query_ids"] == []
    assert durable["unknown_query_ids"] == []
    assert len(durable["candidate_payloads"]) == 2
    export = json.loads((legacy_state_path.parent / "continuation-agent-export.json").read_text(encoding="utf-8"))
    assert len(export["findings"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_scheduler_child_crash_leaves_claimed_ids_quarantined(tmp_path, monkeypatch):
    root = tmp_path / "scheduler-crash-window"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"synthetic source binding\n")
    config_path.write_bytes(b"{}\n")
    edition = "2026-10-31"
    run_id = "crashed-source-child"
    rows = _rows(2)
    monkeypatch.setattr(scheduler, "build_food_line_discovery_query_plan", lambda _root, _date: rows)
    monkeypatch.setattr(scheduler, "verify_checkout", lambda *_args, **_kwargs: "f" * 40)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda _path: [])
    monkeypatch.setattr(
        scheduler,
        "write_operational_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(receipt_path=root / "health.json"),
    )
    monkeypatch.setattr(
        scheduler,
        "_invoke_python",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["synthetic-child"], 1, "", "synthetic crash before parent capture"),
    )
    args = SimpleNamespace(
        repo_root=str(root),
        python=sys.executable,
        edition_date=edition,
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
        run_id=run_id,
        collection_only=False,
    )
    assert scheduler.run_source_watch(args) == 1
    continuation_dir = root / "data" / "dispatches" / "food-line" / "continuation" / edition / run_id
    raw = json.loads((continuation_dir / ledger.LEDGER_NAME).read_text(encoding="utf-8"))
    assert raw["pending_query_ids"] == []
    assert len(raw["in_flight_query_ids"]) == 2
    assert raw["completed_query_ids"] == []
    replay = []
    with pytest.raises(ledger.ContinuationLedgerError, match="uncertain or in-flight"):
        ledger.continue_ledger(
            continuation_dir,
            binding=dict(raw["binding"]),
            batch_size=2,
            batch_executor=lambda batch: replay.append(batch),
        )
    assert replay == []


def test_configured_root_repeated_resume_is_terminal_noop(tmp_path, monkeypatch):
    edition = "2026-10-31"
    run_id = "already-complete"
    layout = scheduler.Layout(tmp_path)
    run_dir = layout.run_dir(edition, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": scheduler.RUN_STATE_SCHEMA,
        "edition_date": edition,
        "run_id": run_id,
        "status": "completed",
        "resumable": False,
        "partitions_total": 1,
        "partitions_completed": 1,
        "coverage": {"required_success_ratio": 1.0, "direct_success_ratio": 1.0},
        "options": {"required_coverage_threshold": 0.90, "direct_source_coverage_threshold": 0.75},
        "agent_export": {"status": "no_exportable_findings"},
        "final_error": "",
        "execution_mode": "export_enabled",
    }
    state_path = run_dir / "run-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    record = {
        "schema_version": scheduler.RUN_RECORD_SCHEMA,
        "edition_date": edition,
        "run_id": run_id,
        "source_watch_status": "completed",
        "last_status": "completed",
        "source_commit": "d" * 40,
        "run_state_path": str(state_path),
        "resume_attempted": True,
    }
    layout.run_record(edition).parent.mkdir(parents=True, exist_ok=True)
    layout.run_record(edition).write_text(json.dumps(record), encoding="utf-8")
    calls = []

    def fake_invoke(_python, _root, command):
        calls.append(list(command))
        assert command[0] == "scripts/run_food_line_discovery_expansion.py"
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"status": "completed", "run_id": run_id, "edition_date": edition, "run_state_path": str(state_path)}),
            "",
        )

    monkeypatch.setattr(scheduler, "verify_checkout", lambda *_args, **_kwargs: "d" * 40)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda _path: [])
    monkeypatch.setattr(
        scheduler,
        "write_operational_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(receipt_path=tmp_path / "health.json"),
    )
    args = scheduler.build_parser().parse_args(
        [
            "resume",
            "--repo-root", str(tmp_path),
            "--python", sys.executable,
            "--edition-date", edition,
            "--test-mode",
            "--continuation-run-root", str(tmp_path / scheduler.CONTINUATION_ROOT_RELATIVE),
        ]
    )
    assert scheduler.run_resume(args) == 0
    assert len(calls) == 1, "completed configured-root resume must not invoke continuation"
    receipts = list(layout.source_log_dir(edition).glob("*-status-resume.json"))
    assert receipts
    receipt = json.loads(receipts[-1].read_text(encoding="utf-8"))
    assert receipt["resume_status"] == "resume_not_required"
    assert receipt["exit_code"] == 0


def test_small_uncapped_source_watch_then_configured_root_resume_is_noop(tmp_path, monkeypatch):
    root = tmp_path / "small-source-watch"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"synthetic source binding\n")
    config_path.write_bytes(b"{}\n")
    edition = "2026-10-31"
    run_id = "small-complete"
    query_rows = _rows(3)
    monkeypatch.setattr(scheduler, "build_food_line_discovery_query_plan", lambda _root, _date: query_rows)
    monkeypatch.setattr(scheduler, "verify_checkout", lambda *_args, **_kwargs: "e" * 40)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scheduler, "surviving_worker_pids", lambda _path: [])
    monkeypatch.setattr(
        scheduler,
        "write_operational_receipt",
        lambda *_args, **_kwargs: SimpleNamespace(receipt_path=root / "health.json"),
    )
    invocations = []

    def fake_invoke(_python, _root, command):
        invocations.append(list(command))
        run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition / run_id
        if "--status-run" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"status": "completed", "run_id": run_id, "edition_date": edition, "run_state_path": str(run_dir / "run-state.json")} ),
                "",
            )
        run_dir.mkdir(parents=True, exist_ok=True)
        continuation_dir = Path(command[command.index("--continuation-run-directory") + 1])
        raw_ledger = json.loads((continuation_dir / ledger.LEDGER_NAME).read_text(encoding="utf-8"))
        durable = ledger.load_ledger(continuation_dir, binding=dict(raw_ledger["binding"]))
        for query_id, row in zip(durable["in_flight_query_ids"], query_rows, strict=True):
            checkpoint = _outcome()
            checkpoint["query_rows"] = [dict(row)]
            ledger.checkpoint_in_flight_outcome(
                continuation_dir,
                binding=dict(durable["binding"]),
                query_id=query_id,
                outcome=checkpoint,
            )
        export_path = root / "status" / "food-line" / "runtime" / "agent-inbox"
        export_path.mkdir(parents=True, exist_ok=True)
        result = {
            "ok": True,
            "status": "completed",
            "queries_attempted": 3,
            "queries_succeeded": 3,
            "queries_failed": 0,
            "queries_timed_out": 0,
            "queries_completed": 3,
            "query_plan_available_count": 3,
            "query_plan_bounded_count": 3,
            "queries_deferred": 0,
            "direct_source_fetch_attempt_count": 0,
            "direct_source_fetch_success_count": 0,
            "agent_export_path": str(export_path),
        }
        durable, plan = discovery_wrapper._bounded_state_from_result(
            root=root,
            edition_date=edition,
            run_id=run_id,
            query_plan=query_rows,
            result=result,
            export_requested=True,
        )
        (run_dir / "query-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (run_dir / "run-state.json").write_text(json.dumps(durable), encoding="utf-8")
        child = {
            "edition_date": edition,
            "run_id": run_id,
            "status": "completed",
            "ok": True,
            "execution_mode": "export_enabled",
            "query_rows": [dict(row) for row in query_rows],
            "agent_export": durable["agent_export"],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(child), "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    source_args = scheduler.build_parser().parse_args(
        [
            "source-watch", "--repo-root", str(root), "--python", sys.executable,
            "--edition-date", edition, "--run-id", run_id, "--test-mode",
        ]
    )
    assert scheduler.run_source_watch(source_args) == 0
    resume_args = scheduler.build_parser().parse_args(
        [
            "resume", "--repo-root", str(root), "--python", sys.executable,
            "--edition-date", edition, "--test-mode",
            "--continuation-run-root", str(root / scheduler.CONTINUATION_ROOT_RELATIVE),
        ]
    )
    assert scheduler.run_resume(resume_args) == 0
    assert len(invocations) == 2
    assert all("--status-run" not in command for command in invocations[:1])
    assert "--status-run" in invocations[1]


def test_scheduler_source_watch_initializes_before_child_and_captures_result(tmp_path, monkeypatch):
    root = tmp_path / "scheduler-runner"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"synthetic source binding\n")
    config_path.write_bytes(b"{}\n")
    query_rows = _rows(205)
    monkeypatch.setattr(scheduler, "build_food_line_discovery_query_plan", lambda _root, _date: query_rows)
    monkeypatch.setattr(scheduler, "verify_checkout", lambda *_args, **_kwargs: "c" * 40)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *_args, **_kwargs: None)
    observed_child_payload = {
        "edition_date": "2026-10-31",
        "run_id": "scheduler-source-watch",
        "status": "incomplete_capped",
        "ok": False,
        "execution_mode": "export_enabled",
        "query_rows": [dict(row) for row in query_rows[:200]],
        "candidates": [{"candidate_id": "captured-before-resume", "source_url": "https://example.test/first"}],
    }

    def fake_invoke(_python, _root, command):
        assert command[0] == "scripts/food_line_stable_source_watch.py"
        assert command[command.index("--max-queries") + 1] == "200"
        assert command[command.index("--max-run-minutes") + 1] == "30"
        continuation_dir = Path(command[command.index("--continuation-run-directory") + 1])
        durable = ledger.load_ledger(continuation_dir, binding=dict(json.loads((continuation_dir / ledger.LEDGER_NAME).read_text(encoding="utf-8"))["binding"]))
        for index, (query_id, row) in enumerate(zip(durable["in_flight_query_ids"], query_rows[:200], strict=True)):
            checkpoint = _outcome(
                candidates=[{"candidate_id": "captured-before-resume", "source_url": "https://example.test/first"}]
                if index == 0
                else []
            )
            checkpoint["query_rows"] = [dict(row)]
            ledger.checkpoint_in_flight_outcome(
                continuation_dir,
                binding=dict(durable["binding"]),
                query_id=query_id,
                outcome=checkpoint,
            )
        run_dir = root / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-10-31" / "scheduler-source-watch"
        run_dir.mkdir(parents=True)
        state, plan = discovery_wrapper._bounded_state_from_result(
            root=root,
            edition_date="2026-10-31",
            run_id="scheduler-source-watch",
            query_plan=query_rows[:200],
            result={
                "ok": True,
                "status": "incomplete_capped",
                "queries_attempted": 200,
                "queries_succeeded": 200,
                "queries_failed": 0,
                "queries_timed_out": 0,
                "queries_completed": 200,
                "query_plan_available_count": 205,
                "query_plan_bounded_count": 200,
                "queries_deferred": 5,
                "direct_source_fetch_attempt_count": 0,
                "direct_source_fetch_success_count": 0,
            },
            export_requested=True,
        )
        (run_dir / "query-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (run_dir / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, json.dumps(observed_child_payload), "")

    monkeypatch.setattr(scheduler, "_invoke_python", fake_invoke)
    args = SimpleNamespace(
        repo_root=str(root),
        python=sys.executable,
        edition_date="2026-10-31",
        branch=scheduler.PRODUCTION_BRANCH,
        test_mode=True,
        stale_lock_minutes=45,
        run_id="scheduler-source-watch",
        collection_only=False,
    )
    exit_code = scheduler.run_source_watch(args)
    assert exit_code == 1
    continuation_dir = root / "data" / "dispatches" / "food-line" / "continuation" / "2026-10-31" / "scheduler-source-watch"
    persisted = json.loads((continuation_dir / "continuation-ledger.json").read_text(encoding="utf-8"))
    assert len(persisted["completed_query_ids"]) == 200
    assert len(persisted["pending_query_ids"]) == 5
    assert persisted["candidate_payloads"]["candidate:captured-before-resume"]["source_url"] == "https://example.test/first"


def test_resume_refuses_legacy_sibling_without_bound_ledger(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-runner"
    source_path = root / "src" / "bluefern_dispatches" / "food_line_discovery_expansion.py"
    config_path = root / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json"
    source_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"synthetic source binding\n")
    config_path.write_bytes(b"{}\n")
    rows = _rows(3)
    monkeypatch.setattr(continuation, "build_food_line_discovery_query_plan", lambda _root, _date: rows)
    args = SimpleNamespace(
        repo_root=str(root),
        edition_date="2026-10-31",
        run_id="legacy-only",
        continuation_run_directory=str(root / "data" / "continuation" / "2026-10-31" / "legacy-only"),
    )
    with pytest.raises(ledger.ContinuationLedgerError, match="bound continuation ledger is required"):
        continuation._load_or_prepare(args, "b" * 40, "c" * 64, "d" * 64)
    assert not Path(args.continuation_run_directory).exists()
