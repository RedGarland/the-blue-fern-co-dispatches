from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import preflight_repo_state

ROOT = Path(__file__).resolve().parents[1]


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


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
    assert {entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]} == {
        "data/dispatches/food-line/source_performance_history.json",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/source_registry.json",
    }


def test_food_line_current_review_runtime_state_is_allowed(monkeypatch, tmp_path):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [" M data/dispatches/food-line/review/current-signal-review.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is True
    assert [entry["path"] for entry in report["source_repo"]["summary"]["allowed_entries"]] == [
        "data/dispatches/food-line/review/current-signal-review.json"
    ]


@pytest.mark.parametrize("status", ["M ", "MM", " D", "D "])
def test_food_line_source_performance_history_staged_or_deleted_state_remains_risky(
    monkeypatch, tmp_path, status
):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [f"{status} data/dispatches/food-line/source_performance_history.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/dispatches/food-line/source_performance_history.json"
    ]


@pytest.mark.parametrize("status", ["M ", "MM", " D", "D "])
def test_food_line_current_review_staged_or_deleted_state_remains_risky(monkeypatch, tmp_path, status):
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    monkeypatch.setattr(preflight_repo_state, "_detect_pages_repo", lambda _repo: None)
    monkeypatch.setattr(
        preflight_repo_state,
        "_run_git_status",
        lambda _repo: (0, [f"{status} data/dispatches/food-line/review/current-signal-review.json"]),
    )

    report = preflight_repo_state.build_preflight_report(source_repo)

    assert report["ok"] is False
    assert [entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]] == [
        "data/dispatches/food-line/review/current-signal-review.json"
    ]


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
        "data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json",
        "data/dispatches/food-line/discovery/2026-06-25/unexpected.json",
        "data/dispatches/food-line/discovery/foo/discovery_candidates.json",
    }
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json",
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


def test_preflight_script_imports_without_pythonpath_injection(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight_repo_state.py"), "--source-repo", str(tmp_path)],
        cwd=tmp_path,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode in {0, 1}, completed.stdout + completed.stderr
    assert "ModuleNotFoundError" not in completed.stdout + completed.stderr
