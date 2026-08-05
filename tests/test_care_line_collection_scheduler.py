from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.care_line_collection_scheduler as scheduler


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "scripts" / "windows" / "setup_care_line_collection_tasks.ps1"
RUNNER_SCRIPT = ROOT / "scripts" / "windows" / "run_care_line_national_collection.ps1"
DOC = ROOT / "docs" / "care-line-national-collection-scheduler.md"
PIPELINE_SCRIPT = ROOT / "scripts" / "run_care_line_national_pipeline.py"


def test_setup_script_uses_pacific_schedule_and_collection_only_task() -> None:
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert '$TaskName = "Blue Fern Care Line National Collection"' in text
    assert 'C:\\BlueFernRunner\\CareLineNational' in text
    assert 'Pacific Standard Time' in text
    assert 'New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(6))' in text
    assert 'New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(12))' in text
    assert 'New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours(18))' in text
    assert 'MultipleInstances IgnoreNew' in text
    assert 'automatic_publication = $false' in text
    assert '--smoke-test' not in text


def test_runner_script_uses_pacific_date_and_no_publish_flags() -> None:
    text = RUNNER_SCRIPT.read_text(encoding="utf-8")
    assert 'ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, "Pacific Standard Time")' in text
    assert '--run-date", $RunDate' in text
    assert '--branch", $SourceBranch' in text
    assert '[switch]$SmokeTest' in text
    assert '[int]$MaxSources = 0' in text
    assert 'Smoke-test mode requires a positive -MaxSources value.' in text
    assert 'MaxSources and MaxItemsPerSource are smoke-test-only parameters.' in text
    assert '--allow-insecure-tls' in text
    assert '--publish' not in text
    assert '--push' not in text


def test_collection_scheduler_doc_names_task_and_non_public_scope() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert r"\Blue Fern Co.\Blue Fern Care Line National Collection" in text
    assert "06:00" in text and "12:00" in text and "18:00" in text
    assert "Never approves, generates editions, syncs Pages, publishes" in text or "never approves" in text.lower()
    assert "smoke-test" in text.lower()


def test_pipeline_script_requires_collection_only_flag() -> None:
    text = PIPELINE_SCRIPT.read_text(encoding="utf-8")
    assert 'parser.add_argument("--collection-only", action="store_true")' in text
    assert 'requires --collection-only' in text
    assert 'parser.add_argument("--smoke-test", action="store_true")' in text
    assert 'smoke-test mode requires --max-sources and --max-items-per-source' in text


def test_scheduler_rejects_smoke_limits_without_explicit_smoke_mode(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(scheduler.SchedulerError, match="require explicit smoke-test mode"):
        scheduler.run_collection_once(
            repo,
            run_date="2026-08-05",
            branch="agent/refine-care-line-signal-wire-public-rendering",
            smoke_test=False,
            include_partial=True,
            include_manual_review=False,
            allow_insecure_tls=False,
            max_sources=1,
            fetch_timeout=20,
            max_items_per_source=1,
            active_queue_limit=150,
            low_priority_cap=25,
        )


def test_scheduler_rejects_insecure_tls_in_smoke_mode(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(scheduler.SchedulerError, match="rejects insecure TLS"):
        scheduler.run_collection_once(
            repo,
            run_date="2026-08-05",
            branch="agent/refine-care-line-signal-wire-public-rendering",
            smoke_test=True,
            include_partial=True,
            include_manual_review=False,
            allow_insecure_tls=True,
            max_sources=1,
            fetch_timeout=20,
            max_items_per_source=1,
            active_queue_limit=150,
            low_priority_cap=25,
        )


def test_scheduler_rejects_excessive_smoke_limits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(scheduler.SchedulerError, match="source ceiling is 3"):
        scheduler.run_collection_once(
            repo,
            run_date="2026-08-05",
            branch="agent/refine-care-line-signal-wire-public-rendering",
            smoke_test=True,
            include_partial=True,
            include_manual_review=False,
            allow_insecure_tls=False,
            max_sources=4,
            fetch_timeout=20,
            max_items_per_source=1,
            active_queue_limit=150,
            low_priority_cap=25,
        )


def test_scheduler_returns_success_for_partial_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    (repo / "scripts" / "run_care_line_national_pipeline.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(scheduler, "verify_checkout", lambda root, branch: "abc123")
    monkeypatch.setattr(scheduler, "run_preflight", lambda root: None)

    payload = {
        "run_manifest": {
            "status": "partial_success",
            "run_id": "run-1",
            "selected_source_ids": ["acp-news", "cdc-newsroom"],
            "production_review_queue_mutation_disabled": True,
            "run_manifest_path": "data/dispatches/care-line/collection-runs/smoke/2026-08-05/run-1/run-manifest.json",
            "review_queue_path": "data/dispatches/care-line/review/smoke/current-review-queue.json",
            "candidate_registry_path": "data/dispatches/care-line/review/smoke/candidate-registry.json",
            "successful_attempt_count": 2,
            "failed_source_count": 1,
            "skipped_source_count": 0,
            "active_review_queue_count": 3,
            "manual_review_count": 1,
        }
    }

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(scheduler, "_run", lambda command, cwd: Result())
    monkeypatch.setattr(scheduler, "_write_log", lambda root, run_date, run_id, command, result, smoke_test: root / "log.txt")

    exit_code, receipt = scheduler.run_collection_once(
        repo,
        run_date="2026-08-05",
        branch="agent/refine-care-line-signal-wire-public-rendering",
        smoke_test=True,
        include_partial=True,
        include_manual_review=False,
        allow_insecure_tls=False,
        max_sources=2,
        fetch_timeout=20,
        max_items_per_source=2,
        active_queue_limit=150,
        low_priority_cap=25,
    )
    assert exit_code == 0
    assert receipt["ok"] is True
    assert receipt["pipeline_status"] == "partial_success"
    assert receipt["smoke_test"] is True
    assert receipt["selected_source_ids"] == ["acp-news", "cdc-newsroom"]
    assert receipt["production_review_queue_mutation_disabled"] is True
    assert "scheduler-runs/smoke" in receipt["receipt_path"].replace("\\", "/")
    assert not (repo / scheduler.SMOKE_LOCK_PATH).exists()


def test_scheduler_returns_failure_for_pipeline_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    (repo / "scripts" / "run_care_line_national_pipeline.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(scheduler, "verify_checkout", lambda root, branch: "abc123")
    monkeypatch.setattr(scheduler, "run_preflight", lambda root: None)

    class Result:
        returncode = 1
        stdout = json.dumps({"run_manifest": {"status": "failure", "run_id": "run-2"}})
        stderr = "fatal"

    monkeypatch.setattr(scheduler, "_run", lambda command, cwd: Result())
    monkeypatch.setattr(scheduler, "_write_log", lambda root, run_date, run_id, command, result, smoke_test: root / "log.txt")

    exit_code, receipt = scheduler.run_collection_once(
        repo,
        run_date="2026-08-05",
        branch="agent/refine-care-line-signal-wire-public-rendering",
        smoke_test=False,
        include_partial=True,
        include_manual_review=False,
        allow_insecure_tls=False,
        max_sources=None,
        fetch_timeout=20,
        max_items_per_source=None,
        active_queue_limit=150,
        low_priority_cap=25,
    )
    assert exit_code == 1
    assert receipt["ok"] is False
    assert receipt["pipeline_status"] == "failure"
