from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def _load_scheduler_module(repo: Path):
    path = repo / "scripts" / "care_line_collection_scheduler.py"
    spec = importlib.util.spec_from_file_location("care_line_collection_scheduler", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_care_line_windows_wrapper_and_helper_are_present_and_bound_to_collection_contract(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    wrapper = repo / "scripts" / "windows" / "run_care_line_national_collection.ps1"
    helper = repo / "scripts" / "care_line_collection_scheduler.py"

    assert wrapper.exists()
    assert helper.exists()

    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "scripts\\care_line_collection_scheduler.py" in wrapper_text
    assert "$RepositoryRoot" in wrapper_text
    assert "$PythonExecutable" in wrapper_text
    assert "$SourceBranch" in wrapper_text
    assert "$RunDate" in wrapper_text
    assert "$SmokeTest" in wrapper_text
    assert "$MaxSources" in wrapper_text
    assert "$MaxItemsPerSource" in wrapper_text
    assert "$IncludeManualReview" in wrapper_text
    assert "$ExcludePartial" in wrapper_text
    assert "$AllowInsecureTls" in wrapper_text

    scheduler = _load_scheduler_module(repo)
    captured: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], *, cwd: Path):
        captured.append((command, cwd))
        payload = {
            "run_manifest": {
                "status": "success",
                "run_id": "run-1",
                "successful_attempt_count": 100,
                "failed_source_count": 4,
                "skipped_source_count": 2,
                "active_review_queue_count": 0,
                "manual_review_count": 0,
                "production_review_queue_mutation_disabled": True,
            }
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    class DummyLock:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.stale_recovered = False
            self.acquired = False

        def acquire(self, *, now=None):  # noqa: ANN001
            self.acquired = True
            return "acquired"

        def release(self) -> None:
            self.acquired = False

    scheduler.verify_checkout = lambda root, branch: "abc123"  # type: ignore[assignment]
    scheduler.run_preflight = lambda root: None  # type: ignore[assignment]
    scheduler._run = fake_run  # type: ignore[assignment]
    scheduler.SchedulerLock = DummyLock  # type: ignore[assignment]

    exit_code, receipt = scheduler.run_collection_once(
        tmp_path,
        run_date="2026-08-16",
        branch=scheduler.PRODUCTION_BRANCH,
        smoke_test=True,
        include_partial=False,
        include_manual_review=True,
        allow_insecure_tls=False,
        max_sources=2,
        fetch_timeout=17,
        max_items_per_source=3,
        active_queue_limit=111,
        low_priority_cap=9,
    )

    assert exit_code == 0
    assert receipt["ok"] is True
    assert receipt["collection_only"] is True
    assert receipt["smoke_test"] is True
    assert receipt["production_review_queue_mutation_disabled"] is True
    assert receipt["publication_side_effects"]["pages_sync"] is False
    assert receipt["publication_side_effects"]["bluesky_publication"] is False
    assert receipt["pipeline_status"] == "success"
    assert len(captured) == 1

    command, cwd = captured[0]
    assert cwd == tmp_path.resolve()
    assert Path(command[1]).name == "run_care_line_national_pipeline.py"
    assert "--collection-only" in command
    assert "--smoke-test" in command
    assert "--max-sources" in command
    assert "2" in command
    assert "--max-items-per-source" in command
    assert "3" in command
    assert "--include-manual-review" in command
    assert "--exclude-partial" in command
    assert "--allow-insecure-tls" not in command
