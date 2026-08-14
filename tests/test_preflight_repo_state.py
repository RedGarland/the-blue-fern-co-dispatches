from __future__ import annotations

from pathlib import Path

from scripts import preflight_repo_state


def test_classify_path_covers_expected_categories():
    assert preflight_repo_state.classify_path("scripts/preflight_repo_state.py") == "source"
    assert preflight_repo_state.classify_path("tests/test_preflight_repo_state.py") == "tests"
    assert preflight_repo_state.classify_path("docs/project-contract.md") == "docs"
    assert preflight_repo_state.classify_path("output/site/gaza/index.html") == "generated_public_output"
    assert preflight_repo_state.classify_path("output/dispatches/american-pressure/review/report.md") == "review_output"
    assert preflight_repo_state.classify_path("logs/gaza-daily-2026-06-22.log") == "logs"
    assert preflight_repo_state.classify_path(".pytest-temp-gaza-wide/") == "cache"
    assert preflight_repo_state.classify_path(".venv/Scripts/python.exe") == "virtualenv"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/source_performance_history.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/agent-intake/2026-08-13/run.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/agent-intake/reports/2026-08-13/run.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/agent-intake/processed/run.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/review/proposed-editions/run.json") == "review_output"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/review/reports/run.json") == "review_output"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/review/signal-reviews/run.json") == "review_output"
    assert preflight_repo_state.classify_path("logs/food-line/run.json") == "logs"
    assert preflight_repo_state.classify_path("status/food-line/run.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/dispatches/food-line/discovery-runs/2026-08-13/run.json") == "local_run_state"
    assert preflight_repo_state.classify_path("data/agent-history-staging/food-line/run.txt") == "local_run_state"
    assert preflight_repo_state.classify_path("some/unknown/path.txt") == "unknown"


def test_food_line_source_performance_history_is_allowed_but_other_data_paths_are_not(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/food-line-fix",
            " M data/dispatches/food-line/source_performance_history.json",
            " M data/dispatches/food-line/source_registry.json",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == set()
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/source_performance_history.json",
        "data/dispatches/food-line/source_registry.json",
    }


def test_food_line_discovery_candidates_path_is_allowed_but_nearby_paths_stay_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/food-line-runner-hygiene",
            "?? data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json",
            "?? data/dispatches/food-line/discovery/2026-06-25/unexpected.json",
            "?? data/dispatches/food-line/discovery/foo/discovery_candidates.json",
            "?? data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json"
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/discovery/2026-06-25/unexpected.json",
        "data/dispatches/food-line/discovery/foo/discovery_candidates.json",
        "data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json",
    }


def test_food_line_agent_intake_paths_are_allowed_but_nearby_paths_stay_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)

    def fake_run_git_status(_repo: Path):
        return 0, [
            "## add/food-line-intake-state",
            "?? data/dispatches/food-line/agent-intake/2026-08-13/run.json",
            "?? data/dispatches/food-line/agent-intake/reports/2026-08-13/run.json",
            "?? data/dispatches/food-line/agent-intake/processed/2026-08-13/run.json",
            "?? data/dispatches/food-line/agent-intake-notes/run.json",
            "?? data/dispatches/food-line/agent-inbox/run.json",
            "?? data/dispatches/food-line/review/proposed-editions/run.json",
            "?? data/dispatches/food-line/review/reports/run.json",
            "?? data/dispatches/food-line/review/signal-reviews/run.json",
            "?? logs/food-line/run.json",
            "?? status/food-line/run.json",
            "?? data/dispatches/food-line/discovery-runs/2026-08-13/run.json",
            "?? data/agent-history-staging/food-line/run.txt",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "data/dispatches/food-line/agent-intake/2026-08-13/run.json",
        "data/dispatches/food-line/agent-intake/reports/2026-08-13/run.json",
        "data/dispatches/food-line/agent-intake/processed/2026-08-13/run.json",
        "data/dispatches/food-line/agent-inbox/run.json",
        "data/dispatches/food-line/review/proposed-editions/run.json",
        "data/dispatches/food-line/review/reports/run.json",
        "data/dispatches/food-line/review/signal-reviews/run.json",
        "logs/food-line/run.json",
        "status/food-line/run.json",
        "data/dispatches/food-line/discovery-runs/2026-08-13/run.json",
        "data/agent-history-staging/food-line/run.txt",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/agent-intake-notes/run.json",
    }


def test_allowed_local_generated_entries_do_not_fail_preflight(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: pages_repo)

    def fake_run_git_status(repo: Path):
        if repo == source_repo:
            return 0, [
                "## add/gaza-wide-source-discovery-audit",
                "?? logs/gaza-daily-2026-06-22.log",
                "?? output/dispatches/american-pressure/review/report.md",
                "?? .pytest-temp-gaza-wide/",
                "?? .venv/Scripts/python.exe",
            ]
        return 0, ["## gh-pages...origin/gh-pages"]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is True
    assert report["source_repo"]["summary"]["risky_entries"] == []
    assert {entry["category"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "logs",
        "review_output",
        "cache",
        "virtualenv",
    }
    assert report["pages_repo_status"] == "clean"


def test_risky_dirty_files_fail_preflight_and_report_pages_repo(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    pages_repo = tmp_path / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: pages_repo)

    def fake_run_git_status(repo: Path):
        if repo == source_repo:
            return 0, [
                "## add/gaza-wide-source-discovery-audit",
                " M docs/dispatches-project.md",
                "?? src/bluefern_dispatches/gaza_wide_source_audit.py",
                "?? output/site/gaza/index.html",
                "?? tests/test_preflight_repo_state.py",
            ]
        return 0, [
            "## gh-pages...origin/gh-pages",
            "?? output/site/index.html",
        ]

    monkeypatch.setattr(preflight_repo_state, "_run_git_status", fake_run_git_status)

    report = preflight_repo_state.build_preflight_report(source_repo)
    rendered = preflight_repo_state.render_report(report)

    assert report["ok"] is False
    assert report["source_repo"]["summary"]["risky_entries"]
    assert report["pages_repo_status"] == "dirty"
    assert "generated_public_output" in rendered
    assert "risky entries" in rendered


def test_main_returns_nonzero_when_risky(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(preflight_repo_state, "_run_git_status", lambda _repo: (0, ["## add/branch", " M docs/project-contract.md"]))

    rc = preflight_repo_state.main(["--source-repo", str(source_repo)])

    assert rc == 1
