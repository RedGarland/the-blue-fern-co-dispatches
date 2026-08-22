from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from scripts import preflight_repo_state


def _preflight_report(lines: list[str], monkeypatch, tmp_path: Path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(preflight_repo_state, "_run_git_status", lambda _repo: (0, lines))
    return preflight_repo_state.build_preflight_report(source_repo)


def _load_scheduler_module(repo: Path):
    path = repo / "scripts" / "care_line_collection_scheduler.py"
    spec = importlib.util.spec_from_file_location("care_line_collection_scheduler", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_care_line_runtime_paths_are_allowed_but_nearby_paths_stay_risky(monkeypatch, tmp_path: Path) -> None:
    lines = [
        "## add/care-line-runtime",
        "?? data/dispatches/care-line/review/candidate-registry.json",
        "?? data/dispatches/care-line/review/current-review-queue.json",
        "?? data/dispatches/care-line/review/effective-date-follow-up-state.json",
        "?? data/dispatches/care-line/collection-runs/2026-08-22/run-manifest.json",
        "?? logs/care-line/collection-scheduler/2026-08-22.log",
        "?? status/care-line/locks/national-collection.lock",
        "?? status/care-line/scheduler-runs/2026-08-22/receipt.json",
        "?? status/care-line/effective-date-follow-up-state.json",
        "?? data/dispatches/care-line/reviewed/2026-08-22/reviewed_records.json",
        "?? data/dispatches/care-line/source_registry.json",
        " M src/bluefern_dispatches/care_line_national_pipeline.py",
    ]

    report = _preflight_report(lines, monkeypatch, tmp_path)

    assert report["ok"] is False
    assert {entry["category"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "review_state",
        "local_run_state",
        "logs",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/care-line/reviewed/2026-08-22/reviewed_records.json",
        "data/dispatches/care-line/source_registry.json",
        "src/bluefern_dispatches/care_line_national_pipeline.py",
    }
    assert report["allowlisted_categories"] == [
        "cache",
        "local_run_state",
        "logs",
        "review_output",
        "review_state",
        "virtualenv",
    ]


def test_care_line_runtime_path_classification_is_narrow_and_strict() -> None:
    assert preflight_repo_state.classify_path("data/dispatches/care-line/review/current-review-queue.json") == "review_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/collection-runs/2026-08-22/run-manifest.json") == "local_run_state"
    assert preflight_repo_state.classify_path("logs/care-line/collection-scheduler/2026-08-22.log") == "logs"
    assert preflight_repo_state.classify_path("status/care-line/effective-date-follow-up-state.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/care-line/reviewed/2026-08-22/reviewed_records.json") == "unknown"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/review/proposed-editions/file.json") == "review_output"


def test_care_line_scheduler_verify_checkout_allows_runtime_state_but_blocks_source_drift(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scheduler = _load_scheduler_module(Path(__file__).resolve().parents[1])

    def fake_run(command: list[str], *, cwd: Path):  # noqa: ANN001
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="\n".join(
                    [
                        "## add/pages-repo-default",
                        "?? data/dispatches/care-line/review/current-review-queue.json",
                        "?? data/dispatches/care-line/collection-runs/2026-08-22/run-manifest.json",
                        "?? logs/care-line/collection-scheduler/2026-08-22.log",
                        "?? status/care-line/effective-date-follow-up-state.json",
                        "?? status/care-line/locks/national-collection.lock",
                        "?? status/care-line/scheduler-runs/2026-08-22/receipt.json",
                    ]
                )
                + "\n",
                stderr="",
            )
        if command[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(command, 0, stdout="add/pages-repo-default\n", stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(scheduler, "_run", fake_run)
    assert scheduler.verify_checkout(repo, "add/pages-repo-default") == "abc123"

    def fake_run_dirty(command: list[str], *, cwd: Path):  # noqa: ANN001
        if command[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="## add/pages-repo-default\n?? data/dispatches/care-line/review/current-review-queue.json\n M src/bluefern_dispatches/care_line_national_pipeline.py\n",
                stderr="",
            )
        if command[:2] == ["git", "branch"]:
            return subprocess.CompletedProcess(command, 0, stdout="add/pages-repo-default\n", stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(scheduler, "_run", fake_run_dirty)
    try:
        scheduler.verify_checkout(repo, "add/pages-repo-default")
    except scheduler.SchedulerError as exc:  # type: ignore[attr-defined]
        assert "src/bluefern_dispatches/care_line_national_pipeline.py" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected dirty checkout to fail closed")
