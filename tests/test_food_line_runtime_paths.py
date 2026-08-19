from __future__ import annotations

from pathlib import Path

from scripts import food_line_daily_scheduler, preflight_repo_state


def _preflight_report(lines: list[str], monkeypatch, tmp_path: Path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(preflight_repo_state, "_run_git_status", lambda _repo: (0, lines))
    return preflight_repo_state.build_preflight_report(source_repo)


def test_food_runtime_roots_are_shared_between_scheduler_and_preflight(monkeypatch, tmp_path):
    lines = [
        "## add/food-line-runtime",
        "?? data/dispatches/food-line/agent-inbox/file.json",
        "?? data/dispatches/food-line/agent-intake/2026-08-13/file.json",
        "?? data/dispatches/food-line/agent-intake/reports/2026-08-13/file.json",
        "?? data/dispatches/food-line/review/proposed-editions/file.json",
        "?? data/dispatches/food-line/review/reports/file.json",
        "?? data/dispatches/food-line/review/signal-reviews/file.json",
        "?? data/dispatches/food-line/discovery/2026-08-19/discovery_candidates.json",
        "?? logs/food-line/file.json",
        "?? output/review/food-line/2026-08-19/discovery_report.json",
        "?? status/food-line/file.json",
        "?? data/dispatches/food-line/discovery-runs/2026-08-13/file.json",
        "?? data/agent-history-staging/food-line/file.txt",
    ]

    report = _preflight_report(lines, monkeypatch, tmp_path)
    assert report["ok"] is True
    assert report["source_repo"]["summary"]["risky_entries"] == []

    unexpected = food_line_daily_scheduler._unexpected_dirty_paths("\n".join(lines))
    assert unexpected == []


def test_unrelated_untracked_and_tracked_runtime_paths_fail_closed(monkeypatch, tmp_path):
    lines = [
        "## add/food-line-runtime",
        "?? data/dispatches/food-line/random/file.json",
        "?? data/dispatches/food-line/agent-intake-notes/file.json",
        " M data/dispatches/food-line/agent-intake/2026-08-13/file.json",
    ]

    report = _preflight_report(lines, monkeypatch, tmp_path)
    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/random/file.json",
        "data/dispatches/food-line/agent-intake-notes/file.json",
        "data/dispatches/food-line/agent-intake/2026-08-13/file.json",
    }

    unexpected = food_line_daily_scheduler._unexpected_dirty_paths("\n".join(lines))
    assert unexpected == [
        "data/dispatches/food-line/agent-intake-notes/file.json",
        "data/dispatches/food-line/agent-intake/2026-08-13/file.json",
        "data/dispatches/food-line/random/file.json",
    ]


def test_expected_food_runtime_roots_have_shared_categories():
    cases = {
        "data/dispatches/food-line/agent-inbox/file.json": "local_run_state",
        "data/dispatches/food-line/agent-intake/2026-08-13/file.json": "local_run_state",
        "data/dispatches/food-line/agent-intake/reports/2026-08-13/file.json": "local_run_state",
        "data/dispatches/food-line/review/proposed-editions/file.json": "review_output",
        "data/dispatches/food-line/review/reports/file.json": "review_output",
        "data/dispatches/food-line/review/signal-reviews/file.json": "review_output",
        "data/dispatches/food-line/discovery/2026-08-19/discovery_candidates.json": "local_run_state",
        "logs/food-line/file.json": "logs",
        "output/review/food-line/2026-08-19/discovery_report.json": "review_output",
        "status/food-line/file.json": "local_run_state",
        "data/dispatches/food-line/discovery-runs/2026-08-13/file.json": "local_run_state",
        "data/agent-history-staging/food-line/file.txt": "local_run_state",
    }

    for path, expected in cases.items():
        assert preflight_repo_state.classify_path(path) == expected
        assert food_line_daily_scheduler.classify_food_line_runtime_path(path) == expected
