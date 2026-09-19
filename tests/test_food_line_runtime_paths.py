from __future__ import annotations

import shutil
import subprocess
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
        "?? src/bluefern_dispatches/__pycache__/operational_health.cpython-313.pyc",
        "?? output/review/food-line/2026-08-19/discovery_report.json",
        "?? status/food-line/file.json",
        "?? status/operational-health/food-line/2026-09-10/runs/food_line_current_intake-source-watch-run.json",
        "?? status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_current_intake/recovery.lock",
        "?? data/dispatches/food-line/discovery-runs/2026-08-13/file.json",
        "?? data/agent-history-staging/food-line/file.txt",
        " M data/dispatches/food-line/coverage-gaps/2026-09-09.json",
        "?? data/dispatches/food-line/date-reconciliation/2026-09-09.json",
        "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/reconstruction.json",
        "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/research-input.json",
        "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/review/candidates.json",
        "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/review/decisions/food-recon-20260909-002-lansingburgh-pantry.json",
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
        "?? data/dispatches/food-line/historical-reconstruction/2026-09-09/review/notes.json",
        "?? data/dispatches/food-line/historical-reconstruction/not-a-date/reconstruction.json",
        "?? data/dispatches/food-line/date-reconciliation/latest.json",
        "?? data/dispatches/food-line/coverage-gaps/readme.json",
    ]

    report = _preflight_report(lines, monkeypatch, tmp_path)
    assert report["ok"] is False
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/random/file.json",
        "data/dispatches/food-line/agent-intake-notes/file.json",
        "data/dispatches/food-line/agent-intake/2026-08-13/file.json",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/notes.json",
        "data/dispatches/food-line/historical-reconstruction/not-a-date/reconstruction.json",
        "data/dispatches/food-line/date-reconciliation/latest.json",
        "data/dispatches/food-line/coverage-gaps/readme.json",
    }

    unexpected = food_line_daily_scheduler._unexpected_dirty_paths("\n".join(lines))
    assert unexpected == [
        "data/dispatches/food-line/agent-intake-notes/file.json",
        "data/dispatches/food-line/agent-intake/2026-08-13/file.json",
        "data/dispatches/food-line/coverage-gaps/readme.json",
        "data/dispatches/food-line/date-reconciliation/latest.json",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/notes.json",
        "data/dispatches/food-line/historical-reconstruction/not-a-date/reconstruction.json",
        "data/dispatches/food-line/random/file.json",
    ]


def test_operational_status_exporter_receipts_are_gitignored_but_other_logs_are_visible(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copyfile(root / ".gitignore", repo / ".gitignore")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "status-test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Status Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True)

    receipt = repo / "logs" / "operational-status-exporter" / "run.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}\n", encoding="utf-8")
    unrelated = repo / "logs" / "unrelated" / "run.json"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("{}\n", encoding="utf-8")

    ignored = subprocess.run(
        ["git", "check-ignore", "logs/operational-status-exporter/run.json"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    assert ignored.stdout.strip() == "logs/operational-status-exporter/run.json"
    assert "logs/operational-status-exporter/run.json" not in status.stdout
    assert "?? logs/unrelated/run.json" in status.stdout


def test_food_line_scheduler_still_fails_closed_for_unrelated_log_dirt() -> None:
    unexpected = food_line_daily_scheduler._unexpected_dirty_paths("?? logs/unrelated/run.json\n")
    assert unexpected == ["logs/unrelated/run.json"]


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
        "src/bluefern_dispatches/__pycache__/operational_health.cpython-313.pyc": "cache",
        "output/review/food-line/2026-08-19/discovery_report.json": "review_output",
        "status/food-line/file.json": "local_run_state",
        "status/operational-health/food-line/2026-09-10/runs/food_line_current_intake-source-watch-run.json": "local_run_state",
        "status/operational-recovery/food-line/2026-09-10/2026-09-10-food_line_current_intake/recovery.lock": "local_run_state",
        "data/dispatches/food-line/discovery-runs/2026-08-13/file.json": "local_run_state",
        "data/agent-history-staging/food-line/file.txt": "local_run_state",
        "data/dispatches/food-line/coverage-gaps/2026-09-09.json": "local_run_state",
        "data/dispatches/food-line/date-reconciliation/2026-09-09.json": "local_run_state",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/reconstruction.json": "review_output",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/research-input.json": "review_output",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/candidates.json": "review_output",
        "data/dispatches/food-line/historical-reconstruction/2026-09-09/review/decisions/food-recon-20260909-002-lansingburgh-pantry.json": "review_output",
    }

    for path, expected in cases.items():
        assert preflight_repo_state.classify_path(path) == expected
        assert food_line_daily_scheduler.classify_food_line_runtime_path(path) == expected
