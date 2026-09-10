from __future__ import annotations

import subprocess

import pytest

from scripts.runner_repo_maintenance import build_cleanup_plan
from scripts.runner_repo_maintenance import postflight_runner_repos


def _git(repo, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cleanup_plan_limits_cleanup_to_approved_generated_and_temp_paths() -> None:
    entries = [
        {"path": "logs/runner-gaza.log", "is_untracked": True},
        {"path": "output/site/gaza/index.html", "is_untracked": False},
        {"path": "output/review/gaza/report.md", "is_untracked": True},
        {"path": ".pytest-temp-gaza/tmp.txt", "is_untracked": True},
        {"path": "data/dispatches/gaza/editions/2026-07-02/run_manifest.json", "is_untracked": False},
        {"path": "data/records/dispatches.json", "is_untracked": False},
        {"path": "src/bluefern_dispatches/generator.py", "is_untracked": False},
    ]

    plan = build_cleanup_plan(entries)

    assert plan["restore_paths"] == ["output/site/gaza/index.html"]
    assert plan["clean_paths"] == [
        ".pytest-temp-gaza/tmp.txt",
        "logs/runner-gaza.log",
        "output/review/gaza/report.md",
    ]
    assert plan["skipped_paths"] == [
        "data/dispatches/gaza/editions/2026-07-02/run_manifest.json",
        "data/records/dispatches.json",
        "src/bluefern_dispatches/generator.py",
    ]


def test_cleanup_plan_dedupes_paths() -> None:
    entries = [
        {"path": "logs/runner.log", "is_untracked": True},
        {"path": "logs/runner.log", "is_untracked": True},
        {"path": "output/dispatches/gaza/editions/2026-07-02/index.html", "is_untracked": False},
        {"path": "output/dispatches/gaza/editions/2026-07-02/index.html", "is_untracked": False},
    ]

    plan = build_cleanup_plan(entries)

    assert plan["restore_paths"] == ["output/dispatches/gaza/editions/2026-07-02/index.html"]
    assert plan["clean_paths"] == ["logs/runner.log"]


def test_cleanup_plan_keeps_food_line_source_performance_history_outside_auto_cleanup() -> None:
    entries = [
        {"path": "data/dispatches/food-line/source_performance_history.json", "is_untracked": False},
        {"path": "logs/runner-food-line.log", "is_untracked": True},
    ]

    plan = build_cleanup_plan(entries)

    assert plan["restore_paths"] == []
    assert plan["clean_paths"] == ["logs/runner-food-line.log"]
    assert plan["skipped_paths"] == ["data/dispatches/food-line/source_performance_history.json"]


def test_cleanup_plan_skips_protected_paths() -> None:
    entries = [
        {"path": "logs/runner-gaza-20260710-070329.log", "is_untracked": True},
        {"path": "logs/runner-gaza-20260710-070330.log", "is_untracked": True},
    ]

    plan = build_cleanup_plan(entries, protected_paths=["logs/runner-gaza-20260710-070329.log"])

    assert plan["clean_paths"] == ["logs/runner-gaza-20260710-070330.log"]
    assert plan["skipped_paths"] == ["logs/runner-gaza-20260710-070329.log"]


@pytest.mark.parametrize(
    "protected_path",
    [
        "{root}/logs/runner.log",
        "{root}/logs/./runner.log",
        "{root}/LOGS\\RUNNER.LOG",
        "{root}/logs/runner.log".replace("\\", "/"),
    ],
)
def test_cleanup_plan_normalizes_protected_path_forms(tmp_path, protected_path: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    rendered = protected_path.format(root=str(repo))

    plan = build_cleanup_plan(
        [{"path": "logs/runner.log", "is_untracked": True}],
        protected_paths=[rendered],
        repo=repo,
    )

    assert plan["clean_paths"] == []
    assert plan["skipped_paths"] == ["logs/runner.log"]


def test_cleanup_plan_rejects_protected_path_outside_repository(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(ValueError, match="escapes repository root"):
        build_cleanup_plan(
            [{"path": "logs/runner.log", "is_untracked": True}],
            protected_paths=[str(tmp_path / "outside.log")],
            repo=repo,
        )


def test_cleanup_plan_does_not_treat_prefix_collision_as_protected(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    plan = build_cleanup_plan(
        [{"path": "logs/runner.log.backup", "is_untracked": True}],
        protected_paths=[str(repo / "logs/runner.log")],
        repo=repo,
    )

    assert plan["clean_paths"] == ["logs/runner.log.backup"]
    assert plan["skipped_paths"] == []


def test_postflight_preserves_absolute_protected_log_and_reconciles_generated_output(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")

    tracked = repo / "output" / "site" / "gaza" / "index.html"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("committed\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    tracked.write_text("generated drift\n", encoding="utf-8")

    protected_log = repo / "logs" / "runner-gaza-20260910-060035.log"
    protected_log.parent.mkdir()
    protected_log.write_text("historical failure\n", encoding="utf-8")
    generated = repo / "output" / "site" / "gaza" / "generated.html"
    generated.write_text("temporary output\n", encoding="utf-8")

    result = postflight_runner_repos(repo, repo, protected_paths=[str(protected_log)])

    assert result["ok"] is True
    assert tracked.read_text(encoding="utf-8") == "committed\n"
    assert not generated.exists()
    assert protected_log.exists()
    assert result["cleanup_plan"]["skipped_paths"] == ["logs/runner-gaza-20260910-060035.log"]


def test_cleanup_plan_cleans_only_date_scoped_food_line_discovery_candidates() -> None:
    entries = [
        {"path": "data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json", "is_untracked": True},
        {"path": "data/dispatches/food-line/discovery/2026-06-25/unexpected.json", "is_untracked": True},
        {"path": "data/dispatches/food-line/discovery/foo/discovery_candidates.json", "is_untracked": True},
        {"path": "data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json", "is_untracked": True},
    ]

    plan = build_cleanup_plan(entries)

    assert plan["restore_paths"] == []
    assert plan["clean_paths"] == ["data/dispatches/food-line/discovery/2026-06-25/discovery_candidates.json"]
    assert plan["skipped_paths"] == [
        "data/dispatches/food-line/discovery/2026-06-25/unexpected.json",
        "data/dispatches/food-line/discovery/foo/discovery_candidates.json",
        "data/dispatches/gaza/discovery/2026-06-25/discovery_candidates.json",
    ]
