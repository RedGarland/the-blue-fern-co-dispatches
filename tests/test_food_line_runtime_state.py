from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bluefern_dispatches.food_line_runtime_state import (
    CURRENT_QUEUE_PATH,
    CURRENT_QUEUE_SEED,
    SOURCE_HISTORY_PATH,
    SOURCE_HISTORY_SEED,
    current_queue_path,
    migrate_food_line_runtime_state,
    source_performance_history_path,
)
from bluefern_dispatches.food_line_sources import load_food_line_source_performance_history, save_food_line_source_performance_history
from scripts.food_line_daily_scheduler import _unexpected_dirty_paths
from scripts.preflight_repo_state import build_preflight_report


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    for relative in (CURRENT_QUEUE_SEED, SOURCE_HISTORY_SEED):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"seed": relative.as_posix()}), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def test_migration_preserves_dirty_state_and_is_idempotent(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    queue_seed = root / CURRENT_QUEUE_SEED
    history_seed = root / SOURCE_HISTORY_SEED
    queue_seed.write_text(json.dumps({"current": "queue"}), encoding="utf-8")
    history_seed.write_text(json.dumps({"current": "history"}), encoding="utf-8")
    expected_queue = queue_seed.read_bytes()
    expected_history = history_seed.read_bytes()

    first = migrate_food_line_runtime_state(root)

    assert (root / CURRENT_QUEUE_PATH).read_bytes() == expected_queue
    assert (root / SOURCE_HISTORY_PATH).read_bytes() == expected_history
    assert queue_seed.read_bytes() == subprocess.check_output(["git", "show", f"HEAD:{CURRENT_QUEUE_SEED.as_posix()}"], cwd=root)
    assert history_seed.read_bytes() == subprocess.check_output(["git", "show", f"HEAD:{SOURCE_HISTORY_SEED.as_posix()}"], cwd=root)
    assert first["idempotent"] is False

    second = migrate_food_line_runtime_state(root)
    assert second["idempotent"] is True
    assert (root / CURRENT_QUEUE_PATH).read_bytes() == expected_queue
    assert (root / SOURCE_HISTORY_PATH).read_bytes() == expected_history


def test_runtime_read_write_persists_without_touching_tracked_seed(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    queue_seed = root / CURRENT_QUEUE_SEED
    history_seed = root / SOURCE_HISTORY_SEED
    queue_before = queue_seed.read_bytes()
    history_before = history_seed.read_bytes()

    queue_path = current_queue_path(root)
    queue_path.write_text(json.dumps({"updated": True}), encoding="utf-8")
    save_food_line_source_performance_history(root, {"source-a": {"runs_seen": 2}})

    assert json.loads(current_queue_path(root).read_text(encoding="utf-8"))["updated"] is True
    assert load_food_line_source_performance_history(root)["source-a"]["runs_seen"] == 2
    assert queue_seed.read_bytes() == queue_before
    assert history_seed.read_bytes() == history_before


def test_preflight_rejects_old_tracked_state_and_accepts_runtime_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "scripts.preflight_repo_state._run_git_status",
        lambda _repo: (0, [
            "## add/pages-repo-default",
            " M data/dispatches/food-line/source_performance_history.json",
            " M data/dispatches/food-line/review/current-signal-review.json",
            "?? status/food-line/runtime/source_performance_history.json",
            "?? status/food-line/runtime/current-signal-review.json",
        ]),
    )
    report = build_preflight_report(tmp_path)
    assert {entry["path"] for entry in report["source_repo"]["summary"]["risky_entries"]} == {
        "data/dispatches/food-line/source_performance_history.json",
        "data/dispatches/food-line/review/current-signal-review.json",
    }
    assert report["source_repo"]["summary"]["allowed_entries"]


def test_scheduler_stays_fail_closed_for_unrelated_dirt() -> None:
    assert _unexpected_dirty_paths(" M docs/unrelated.md\n") == ["docs/unrelated.md"]
    assert _unexpected_dirty_paths("?? status/food-line/runtime/current-signal-review.json\n") == []
