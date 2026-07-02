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
