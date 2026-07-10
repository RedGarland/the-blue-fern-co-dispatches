from __future__ import annotations

from scripts.runner_repo_maintenance import build_cleanup_plan


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
