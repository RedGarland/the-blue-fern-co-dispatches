from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import bluefern_dispatches.food_line_bounded_discovery as bounded
from bluefern_dispatches.food_line_bounded_discovery import (
    SubprocessQueryExecutor,
    _terminate_process,
    atomic_write_json,
    build_bounded_query_plan,
    inspect_bounded_run,
    profile_options,
    run_bounded_food_line_discovery,
)
from bluefern_dispatches.food_line_bounded_worker import RetryingNetworkFetcher


def _query(
    text: str,
    *,
    family: str = "core_hunger",
    scope: str = "national",
    channel: str = "google_news_rss",
    state: str = "",
    metro: str = "",
) -> dict[str, Any]:
    return {
        "query_family": family,
        "query_text": text,
        "query_template": text,
        "geographic_scope": scope,
        "state_or_territory": state,
        "state_abbrev": "",
        "metro": metro,
        "discovery_channel": channel,
        "search_provider": channel,
        "source_family": "local_news",
        "edition_date": "2026-08-01",
        "after": "2026-07-31",
        "before": "2026-08-02",
        "direct_source_name": "Direct Source" if channel != "google_news_rss" else "",
        "direct_source_url": "https://example.com/feed" if channel != "google_news_rss" else "",
    }


def _small_plan() -> list[dict[str, Any]]:
    return [
        _query("Direct Source", family="news_article", channel="direct_rss"),
        _query("national SNAP benefit interruption after:2026-07-31 before:2026-08-02"),
        _query("California food pantry demand", family="state_territory", scope="state_or_territory", state="California"),
        _query("Texas food pantry closure", family="state_territory", scope="state_or_territory", state="Texas"),
        _query("Seattle food bank demand", family="metro", scope="metro", metro="Seattle"),
    ]


def _candidate(candidate_id: str = "candidate-1") -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "source_url": f"https://example.com/{candidate_id}",
        "canonical_url": f"https://example.com/{candidate_id}",
        "final_trace_url": f"https://example.com/{candidate_id}",
        "source_published_date": "2026-08-01",
        "evidence_text": "The pantry closed for the day after demand exceeded available food.",
        "evidence_text_basis": "page_text_excerpt",
        "metro": "Seattle",
        "state_abbrev": "WA",
        "classification_status": "qualified_pressure_signal",
        "pressure_signal": True,
        "pressure_type": "service interruption",
        "pressure_summary": "Pantry access was interrupted after demand exceeded supply.",
        "public_claim_eligible": True,
        "public_claim_blockers": [],
        "selected_title": "Pantry closes after demand exceeds supply",
        "discovered_title": "Pantry closes after demand exceeds supply",
        "discovered_publisher": "Example News",
        "query_family": "core_hunger",
        "query_text": "pantry closure",
        "discovery_channel": "google_news_rss",
        "discovery_date": "2026-08-01",
        "geographic_scope": "metro",
        "source_role": "local_reporting",
        "evidence_level": "direct",
    }


def test_daily_current_profile_uses_review_window_for_public_claims() -> None:
    options = profile_options("daily-current")
    assert options["query_lookback_days"] == 1
    assert options["query_lookahead_days"] == 1
    assert options["public_claim_lookback_days"] == 3
    assert options["public_claim_lookahead_days"] == 0


class FakeExecutor:
    def __init__(self, statuses: dict[str, list[str]] | None = None, *, candidate: dict[str, Any] | None = None, delay: float = 0.0) -> None:
        self.statuses = {key: list(value) for key, value in (statuses or {}).items()}
        self.candidate = candidate
        self.delay = delay
        self.calls: list[str] = []
        self.cancelled = False

    def execute(self, query: dict[str, Any], *, run_dir: Path, options: dict[str, Any]) -> dict[str, Any]:
        assert (run_dir / "query-plan.json").exists()
        assert (run_dir / "run-state.json").exists()
        if self.delay:
            time.sleep(self.delay)
        query_id = str(query["query_id"])
        text = str(query["query"]["query_text"])
        self.calls.append(query_id)
        choices = self.statuses.get(text) or ["completed"]
        status = choices.pop(0) if len(choices) > 1 else choices[0]
        candidates = [dict(self.candidate)] if self.candidate and status == "completed" else []
        return {
            "query_id": query_id,
            "status": status,
            "error": "" if status == "completed" else f"fake {status}",
            "candidates": candidates,
            "query_rows": [],
            "summary": {"candidate_count": len(candidates)},
        }

    def cancel_all(self) -> None:
        self.cancelled = True


class CancellingExecutor(FakeExecutor):
    def execute(self, query: dict[str, Any], *, run_dir: Path, options: dict[str, Any]) -> dict[str, Any]:
        raise KeyboardInterrupt


@pytest.fixture
def bounded_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "data/dispatches/food-line/discovery_expansion_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(bounded, "build_food_line_discovery_query_plan", lambda root, date: [dict(row) for row in _small_plan()])
    return tmp_path


def _run(
    root: Path,
    executor: FakeExecutor,
    *,
    run_id: str = "test-run",
    export: bool = False,
    max_partitions: int | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = {
        "partition_size": 2,
        "max_workers": 1,
        "max_run_minutes": 1,
        "max_partition_minutes": 1,
        "max_query_seconds": 1,
        "progress_interval_seconds": 1,
        "required_coverage_threshold": 1.0,
        "direct_source_coverage_threshold": 1.0,
    }
    defaults.update(overrides or {})
    return run_bounded_food_line_discovery(
        root,
        "2026-08-01",
        profile="daily-current",
        run_id=run_id,
        export_agent_inbox=export,
        agent_inbox_dir=root / "data/dispatches/food-line/agent-inbox",
        max_partitions=max_partitions,
        overrides=defaults,
        executor=executor,
    )


def test_plan_is_stable_partitioned_and_created_before_collection(bounded_root: Path) -> None:
    first, report = build_bounded_query_plan(
        bounded_root,
        "2026-08-01",
        run_id="one",
        partition_size=2,
        required_tiers=["tier1"],
        requested_tiers=["tier1"],
        created_at="2026-08-01T00:00:00Z",
    )
    second, _ = build_bounded_query_plan(
        bounded_root,
        "2026-08-01",
        run_id="two",
        partition_size=2,
        required_tiers=["tier1"],
        requested_tiers=["tier1"],
        created_at="2026-08-02T00:00:00Z",
    )
    assert [row["query_id"] for row in first["queries"]] == [row["query_id"] for row in second["queries"]]
    assert [row["partition_id"] for row in first["partitions"]] == [row["partition_id"] for row in second["partitions"]]
    assert first["query_plan_sha256"] == second["query_plan_sha256"]
    assert report["original_query_count"] == 5
    result = _run(bounded_root, FakeExecutor())
    assert result["status"] == "completed", result


def test_exact_duplicate_report_is_transparent(bounded_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _small_plan()
    rows.append(dict(rows[0]))
    monkeypatch.setattr(bounded, "build_food_line_discovery_query_plan", lambda root, date: rows)
    manifest, report = build_bounded_query_plan(
        bounded_root, "2026-08-01", run_id="dupes", partition_size=2,
        required_tiers=["tier1"], requested_tiers=["tier1"],
    )
    assert manifest["original_query_count"] == 6
    assert manifest["total_query_count"] == 5
    assert report["exact_duplicates_removed"] == 1
    assert report["semantically_redundant_queries_consolidated"] == 0


def test_partition_checkpoint_is_atomic_and_checksummed(bounded_root: Path) -> None:
    result = _run(bounded_root, FakeExecutor(), run_id="checkpoint")
    run_dir = bounded_root / "data/dispatches/food-line/discovery-runs/2026-08-01/checkpoint"
    artifacts = list((run_dir / "partitions").glob("*.json"))
    assert artifacts
    payload = json.loads(artifacts[0].read_text(encoding="utf-8"))
    checksum = payload.pop("checksum")
    assert bounded._sha256(payload) == checksum
    assert not list(run_dir.rglob("*.tmp"))
    assert result["last_completed_partition"]


def test_partial_failure_blocks_export_and_is_resumable(bounded_root: Path) -> None:
    failing_text = _small_plan()[1]["query_text"]
    result = _run(
        bounded_root,
        FakeExecutor({failing_text: ["failed"]}),
        run_id="partial",
        export=True,
    )
    assert result["status"] == "partial"
    assert result["agent_export"]["status"] == "blocked_incomplete_collection"
    assert result["resumable"] is True
    assert not list((bounded_root / "data/dispatches/food-line/agent-inbox").glob("*.json"))


def test_resume_skips_completed_and_retries_failed_query(bounded_root: Path) -> None:
    failing_text = _small_plan()[1]["query_text"]
    first_executor = FakeExecutor({failing_text: ["failed"]})
    first = _run(bounded_root, first_executor, run_id="resume")
    assert first["status"] == "partial"
    second_executor = FakeExecutor()
    second = run_bounded_food_line_discovery(
        bounded_root,
        "2026-08-01",
        profile="daily-current",
        resume_run="resume",
        overrides={
            "partition_size": 2, "max_workers": 1, "max_run_minutes": 1,
            "max_partition_minutes": 1, "max_query_seconds": 1,
            "required_coverage_threshold": 1.0, "direct_source_coverage_threshold": 1.0,
        },
        executor=second_executor,
    )
    assert second["status"] == "completed"
    assert len(second_executor.calls) == 1
    manifest = json.loads(
        (bounded_root / "data/dispatches/food-line/discovery-runs/2026-08-01/resume/query-plan.json").read_text(encoding="utf-8")
    )
    attempts = sorted(row["execution"]["attempt_count"] for row in manifest["queries"] if row["required"])
    assert attempts == [1, 2]


def test_resume_rejects_configuration_or_plan_mismatch(bounded_root: Path) -> None:
    _run(bounded_root, FakeExecutor(), run_id="mismatch", max_partitions=0)
    config = bounded_root / "data/dispatches/food-line/discovery_expansion_config.json"
    config.write_text('{"changed": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="configuration"):
        run_bounded_food_line_discovery(
            bounded_root, "2026-08-01", profile="daily-current",
            resume_run="mismatch", executor=FakeExecutor(),
        )


def test_whole_run_timeout_is_durable_and_never_exports(bounded_root: Path) -> None:
    executor = FakeExecutor()
    result = _run(
        bounded_root,
        executor,
        run_id="whole-timeout",
        export=True,
        overrides={"max_run_minutes": 0.0000001},
    )
    persisted = json.loads(
        (bounded_root / "data/dispatches/food-line/discovery-runs/2026-08-01/whole-timeout/run-state.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "timed_out"
    assert persisted["status"] == "timed_out"
    assert result["agent_export"]["status"] == "blocked_incomplete_collection"
    assert executor.calls == []


def test_partition_timeout_cancels_and_checkpoints_partial_state(bounded_root: Path) -> None:
    executor = FakeExecutor(delay=0.05)
    result = _run(
        bounded_root,
        executor,
        run_id="partition-timeout",
        overrides={"max_partition_minutes": 0.0001},
    )
    assert result["status"] in {"partial", "timed_out"}
    assert result["queries_timed_out"] >= 1
    assert executor.cancelled is True


def test_cancellation_is_durable_and_blocks_export(bounded_root: Path) -> None:
    result = _run(bounded_root, CancellingExecutor(), run_id="cancelled", export=True)
    assert result["status"] == "cancelled"
    assert result["agent_export"]["status"] == "blocked_incomplete_collection"


def test_completed_empty_run_may_report_no_exportable_findings(bounded_root: Path) -> None:
    result = _run(bounded_root, FakeExecutor(), run_id="empty", export=True)
    assert result["status"] == "completed"
    assert result["agent_export"]["status"] == "no_exportable_findings"


def test_completed_with_exclusions_may_export_and_discloses_failures(bounded_root: Path) -> None:
    rows = [_query(f"national SNAP query {index}") for index in range(10)]
    bounded.build_food_line_discovery_query_plan = lambda root, date: rows
    result = _run(
        bounded_root,
        FakeExecutor({rows[0]["query_text"]: ["failed"]}, candidate=_candidate()),
        run_id="bounded-exclusions",
        export=True,
        overrides={"required_coverage_threshold": 0.8, "direct_source_coverage_threshold": 0.0},
    )
    assert result["status"] == "completed_with_exclusions"
    assert result["agent_export"]["status"] in {"success", "success_with_exclusions"}
    assert result["agent_export"]["agent_run_id"] == "bounded-exclusions"


def test_candidate_deduplication_across_partitions_and_resume_is_idempotent(bounded_root: Path) -> None:
    candidate = _candidate("same")
    first = _run(bounded_root, FakeExecutor(candidate=candidate), run_id="dedupe")
    run_dir = bounded_root / "data/dispatches/food-line/discovery-runs/2026-08-01/dedupe"
    candidates_before = (run_dir / "final-candidates.json").read_bytes()
    assert len(json.loads(candidates_before)) == 1
    second = run_bounded_food_line_discovery(
        bounded_root, "2026-08-01", profile="daily-current",
        resume_run="dedupe", executor=FakeExecutor(candidate=candidate),
    )
    assert first["status"] == second["status"] == "completed"
    assert (run_dir / "final-candidates.json").read_bytes() == candidates_before


def test_optional_tiers_are_explicitly_deferred(bounded_root: Path) -> None:
    result = _run(bounded_root, FakeExecutor(), run_id="deferred")
    assert result["coverage"]["deferred_optional_query_count"] == 3
    assert "--profile supplemental" in result["next_action"]


def test_status_command_data_has_exact_next_action(bounded_root: Path) -> None:
    _run(bounded_root, FakeExecutor(), run_id="status", max_partitions=0)
    status = inspect_bounded_run(bounded_root, "status")
    assert status["status"] == "partial"
    assert status["remaining_partition_count"] > 0
    assert "--resume-run status" in status["next_action"]


def test_retrying_fetcher_honors_timeout_and_retry_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fail(request: Any, timeout: int, context: Any = None) -> Any:
        del request, context
        calls.append(timeout)
        raise TimeoutError("bounded")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    fetcher = RetryingNetworkFetcher(timeout_seconds=3, max_retries=1, backoff_seconds=0)
    with pytest.raises(TimeoutError):
        fetcher("https://example.com")
    assert calls == [3, 3]


def test_process_tree_termination_leaves_no_worker() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    _terminate_process(process, grace_seconds=1)
    assert process.poll() is not None


def test_small_synthetic_plan_runs_through_real_worker_process(bounded_root: Path) -> None:
    feed = bounded_root / "feed.xml"
    feed.write_text("<rss><channel><title>Empty</title></channel></rss>\n", encoding="utf-8")
    query = _query("Local direct feed", family="news_article", channel="direct_rss")
    query["direct_source_feed_url"] = feed.resolve().as_uri()
    query["direct_source_url"] = feed.resolve().as_uri()
    wrapped = {"query_id": "q-local-worker", "query": query}
    executor = SubprocessQueryExecutor(bounded_root)
    run_dir = bounded_root / "worker-run"
    result = executor.execute(
        wrapped,
        run_dir=run_dir,
        options={
            "edition_date": "2026-08-01",
            "per_request_timeout_seconds": 2,
            "max_retries": 0,
            "max_results_per_query": 1,
            "query_lookback_days": 1,
            "query_lookahead_days": 1,
            "public_claim_lookback_days": 0,
            "public_claim_lookahead_days": 0,
            "max_query_seconds": 10,
        },
    )
    assert result["status"] == "completed", result
    assert result["summary"]["candidate_count"] == 0
    assert executor.active_pids() == []


def test_atomic_write_replaces_complete_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    atomic_write_json(path, {"status": "planned"})
    atomic_write_json(path, {"status": "running"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "running"}
    assert not list(tmp_path.glob("*.tmp"))


def test_bounded_run_has_no_publication_or_schedule_effects(bounded_root: Path) -> None:
    _run(bounded_root, FakeExecutor(), run_id="side-effects")
    assert not (bounded_root / "output/site").exists()
    assert not (bounded_root / "schedules").exists()
    assert not (bounded_root / "data/bluesky").exists()
    assert not (bounded_root / "output/audio").exists()


def test_progress_is_concise_and_durable(bounded_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _run(
        bounded_root,
        FakeExecutor(),
        run_id="progress",
        overrides={"progress_interval_seconds": 1},
    )
    output = capsys.readouterr().out
    assert "run=progress partition=" in output
    progress = json.loads(
        (bounded_root / "data/dispatches/food-line/discovery-runs/2026-08-01/progress/progress.json").read_text(encoding="utf-8")
    )
    assert progress["run_id"] == "progress"
