from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import food_line_daily_scheduler as scheduler
from scripts import process_food_line_current_intake as current_intake_compat
from scripts import run_food_line_discovery_expansion as discovery_compat


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "scripts" / "food_line_daily_scheduler.py"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def test_food_line_daily_scheduler_imports_without_pythonpath_injection(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", "import scripts.food_line_daily_scheduler as m; print('IMPORT_OK')"],
        cwd=ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "IMPORT_OK" in completed.stdout
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr


def test_food_line_daily_scheduler_help_executes_from_other_cwd(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(SCHEDULER), "--help"],
        cwd=tmp_path,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    combined = completed.stdout + completed.stderr
    assert "usage:" in combined.lower()
    assert "ModuleNotFoundError" not in combined


def test_food_line_daily_scheduler_defaults_to_production_branch() -> None:
    parser = scheduler.build_parser()
    args = parser.parse_args(
        [
            "source-watch",
            "--repo-root",
            ".",
            "--python",
            "python",
            "--edition-date",
            "2026-08-17",
            "--run-id",
            "run-1",
        ]
    )

    assert args.branch == "add/pages-repo-default"


def test_legacy_discovery_wrapper_writes_run_state_and_query_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "dispatches" / "food-line").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_core(root: Path, edition_date: str, **kwargs: object) -> dict[str, object]:
        assert root == tmp_path
        assert edition_date == "2026-08-17"
        return {
            "ok": True,
            "candidate_count": 5,
            "public_eligible_candidate_count": 1,
            "rejected_news_count": 0,
            "fetch_failure_count_by_type": {},
            "direct_source_count": 5,
        }

    def fake_plan(root: Path, edition_date: str) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-17"
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    monkeypatch.setattr(discovery_compat, "run_food_line_discovery_expansion", fake_core)
    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-17",
            "--run-id",
            "food-line-scheduled-test",
            "--profile",
            "daily-current",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 0
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-08-17" / "food-line-scheduled-test"
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    plan = json.loads((run_dir / "query-plan.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == scheduler.RUN_STATE_SCHEMA
    assert state["status"] == "completed"
    assert state["queries_total"] == 1
    assert state["candidates_discovered"] == 5
    assert state["agent_export"]["status"] == "success"
    assert plan["schema_version"] == "food_line_bounded_query_plan_v1"
    assert plan["query_count"] == 1


def _valid_current_queue_item(date: str) -> dict[str, object]:
    source_date = "2026-08-16" if date == "2026-08-17" else date
    freshness_age_days = 1 if source_date != date else 0
    return {
        "review_item_id": "food-line-current-001",
        "source_finding_or_intake_id": "finding-current-001",
        "source_artifact_path": f"data/dispatches/food-line/agent-intake/{date}/run.json",
        "source_url": "https://example.org/current-food-pressure",
        "canonical_source_url": "https://example.org/current-food-pressure",
        "publisher": "Example News",
        "source_published_at": f"{source_date}T08:00:00-07:00",
        "title": "Pantry closes after supply loss",
        "exact_supporting_passage": "The pantry closed Friday after losing its remaining food supply.",
        "proposed_public_headline": "Local pantry closes after supply loss",
        "proposed_public_summary": "Example News reports that a local pantry closed after losing its food supply.",
        "location_name": "Example City",
        "state": "CA",
        "location_scope": "city",
        "pressure_type": "service_closure",
        "affected_groups": ["pantry clients"],
        "why_it_matters": "A food-access point is no longer operating.",
        "evidence_level": "direct_reporting",
        "confidence": "high",
        "uncertainty_note": "The duration of the closure is not yet known.",
        "duplicate_check": {"status": "not_published", "matched_records": []},
        "freshness_check": {"status": "current", "age_days": freshness_age_days, "edition_date": date},
        "proposed_section": "Core Food Pressure Signals",
        "proposed_rank": 1,
        "editorial_status": "approve",
        "editorial_note": "Approved for draft assembly only.",
        "publication_eligible": False,
    }


def test_legacy_current_intake_wrapper_writes_report_and_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    review_root = tmp_path / "data" / "dispatches" / "food-line" / "review"
    queue_path = review_root / "current-signal-review.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue = {
        "schema_version": "food_line_current_signal_review_v1",
        "queue_id": "food-line-current-review-2026-08-17",
        "edition_date": "2026-08-17",
        "production_scope": "current_nonhistorical_only",
        "historical_roots_excluded": ["data/agent-history", "data/agent-history-staging"],
        "allowed_decisions": ["approve", "approve_with_edit", "hold", "reject"],
        "items": [_valid_current_queue_item("2026-08-17")],
    }
    queue_path.write_text(json.dumps(queue, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    inbox = tmp_path / "data" / "dispatches" / "food-line" / "agent-inbox"
    (inbox / "2026-08-17").mkdir(parents=True, exist_ok=True)
    (inbox / "2026-08-17" / "finding.json").write_text("{}", encoding="utf-8")

    code = current_intake_compat.main(
        [
            "--edition-date",
            "2026-08-17",
            "--inbox",
            str(inbox),
            "--build-review-queue",
            "--build-proposed-edition",
        ]
    )

    assert code == 0
    report = json.loads(
        (review_root / "reports" / "2026-08-17" / "current-intake.json").read_text(encoding="utf-8")
    )
    assert report["schema_version"] == "food_line_current_intake_report_v1"
    assert report["status"] == "success"
    assert report["errors"] == []
    assert report["queue"]["item_count"] == 1
    assert report["proposal"]["draft_status"] == "draft_approved_pending_publication"
    assert Path(report["proposal"]["markdown_path"]).exists()


def test_legacy_discovery_timeout_helper_terminates_process_tree(tmp_path: Path) -> None:
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path)
    try:
        discovery_compat._terminate_process_tree(parent.pid)
        deadline = time.time() + 10
        while time.time() < deadline and scheduler.process_is_running(parent.pid):
            time.sleep(0.05)

        assert not scheduler.process_is_running(parent.pid)
    finally:
        if parent.poll() is None:
            parent.kill()


def test_legacy_discovery_wrapper_timeout_writes_timed_out_state_and_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery_root = tmp_path / "data" / "dispatches" / "food-line"
    discovery_root.mkdir(parents=True, exist_ok=True)
    (discovery_root / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-18"
        assert kwargs["lookback_days"] == 1
        assert kwargs["lookahead_days"] == 1
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    def fake_timeout(command: list[str], *, cwd: Path, timeout_seconds: float) -> tuple[subprocess.CompletedProcess[str], bool]:
        assert cwd == tmp_path
        assert timeout_seconds >= 1.0
        return subprocess.CompletedProcess(command, 124, "", "timed out"), True

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(discovery_compat, "_run_command_with_timeout", fake_timeout)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
            "--run-id",
            "food-line-scheduled-timeout-test",
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 1
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-08-18" / "food-line-scheduled-timeout-test"
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    plan = json.loads((run_dir / "query-plan.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == scheduler.RUN_STATE_SCHEMA
    assert state["status"] == "timed_out"
    assert state["resumable"] is True
    assert state["queries_total"] == 1
    assert state["queries_completed"] == 0
    assert state["queries_timed_out"] == 1
    assert plan["schema_version"] == "food_line_bounded_query_plan_v1"
    assert not (tmp_path / "status" / "food-line" / "locks" / "source-watch.lock").exists()
    assert "timeout" in capsys.readouterr().out


def test_legacy_discovery_wrapper_zero_result_completion_is_structured_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery_root = tmp_path / "data" / "dispatches" / "food-line"
    discovery_root.mkdir(parents=True, exist_ok=True)
    (discovery_root / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-18"
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    zero_result = {
        "ok": True,
        "candidate_count": 0,
        "public_eligible_candidate_count": 0,
        "rejected_news_count": 0,
        "fetch_failure_count_by_type": {},
        "direct_source_count": 0,
    }

    def fake_timeout(command: list[str], *, cwd: Path, timeout_seconds: float) -> tuple[subprocess.CompletedProcess[str], bool]:
        assert cwd == tmp_path
        assert timeout_seconds >= 1.0
        return subprocess.CompletedProcess(command, 0, json.dumps(zero_result), ""), False

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(discovery_compat, "_run_command_with_timeout", fake_timeout)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
            "--run-id",
            "food-line-scheduled-zero-result-test",
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "zero_result_completion" in stdout
    run_dir = tmp_path / "data" / "dispatches" / "food-line" / "discovery-runs" / "2026-08-18" / "food-line-scheduled-zero-result-test"
    state = json.loads((run_dir / "run-state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["candidates_discovered"] == 0
    assert not (tmp_path / "status" / "food-line" / "locks" / "source-watch.lock").exists()


def test_legacy_discovery_wrapper_reports_child_process_failure_with_structured_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    def boom(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(discovery_compat, "discovery_main", boom)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "child_process_failure"
    assert payload["ok"] is False
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "boom"


def test_legacy_discovery_wrapper_reports_malformed_child_output_and_stderr_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    discovery_root = tmp_path / "data" / "dispatches" / "food-line"
    discovery_root.mkdir(parents=True, exist_ok=True)
    (discovery_root / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "query_id": "q-1",
                "query_family": "core_hunger",
                "geography": "national",
                "discovery_channel": "google_news_rss",
                "query_text": '"food insecurity"',
            }
        ]

    def fake_timeout(command: list[str], *, cwd: Path, timeout_seconds: float) -> tuple[subprocess.CompletedProcess[str], bool]:
        return subprocess.CompletedProcess(command, 1, "not json", "diagnostic log on stderr"), False

    monkeypatch.setattr(discovery_compat, "build_food_line_discovery_query_plan", fake_plan)
    monkeypatch.setattr(discovery_compat, "_run_command_with_timeout", fake_timeout)

    code = discovery_compat.main(
        [
            "--date",
            "2026-08-18",
            "--run-id",
            "malformed-child-output-test",
            "--profile",
            "daily-current",
            "--max-run-minutes",
            "0.01",
            "--export-agent-inbox",
            "--agent-inbox-dir",
            str(tmp_path / "status" / "food-line" / "runtime" / "agent-inbox"),
        ]
    )

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "malformed_child_output"
    assert payload["ok"] is False
    assert payload["error_type"] == "malformed_child_output"
    assert "invalid JSON" in payload["error_message"]
