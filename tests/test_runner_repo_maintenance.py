from __future__ import annotations

import subprocess

import pytest

import scripts.runner_repo_maintenance as maintenance
from scripts.runner_repo_maintenance import build_cleanup_plan
from scripts.runner_repo_maintenance import postflight_runner_repos
from scripts.runner_repo_maintenance import sync_runner_repos


def _git(repo, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _git_output(repo, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _init_repo(repo, branch: str) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", branch)
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")


def _write(path, text: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_all(repo, message: str = "fixture") -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _fake_sync_commands(monkeypatch):
    real_run_command = maintenance._run_command
    sync_commands: list[list[str]] = []

    def fake_run_command(args, *, cwd):
        if args[:2] == ["git", "fetch"] or args[:2] == ["git", "reset"]:
            sync_commands.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run_command(args, cwd=cwd)

    monkeypatch.setattr(maintenance, "_run_command", fake_run_command)
    return sync_commands


def _fake_sync_and_record_cleanup(monkeypatch):
    real_run_command = maintenance._run_command
    sync_commands: list[list[str]] = []
    cleanup_commands: list[list[str]] = []

    def fake_run_command(args, *, cwd):
        if args[:2] == ["git", "fetch"] or args[:2] == ["git", "reset"]:
            sync_commands.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["git", "clean"] or args[:2] == ["git", "restore"]:
            cleanup_commands.append(list(args))
        return real_run_command(args, cwd=cwd)

    monkeypatch.setattr(maintenance, "_run_command", fake_run_command)
    return sync_commands, cleanup_commands


def _command_path_args(args: list[str]) -> list[str]:
    separator = args.index("--")
    return args[separator + 1 :]


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


def test_sync_precleans_production_shaped_generated_residue_before_strict_preflight(monkeypatch, tmp_path) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "add/pages-repo-default")
    _init_repo(pages_repo, "gh-pages")

    tracked_gaza = source_repo / "output" / "site" / "gaza" / "index.html"
    tracked_asset = source_repo / "output" / "site" / "assets" / "site.css"
    _write(tracked_gaza, "committed gaza\n")
    _write(tracked_asset, "committed css\n")
    _commit_all(source_repo)
    _write(pages_repo / "index.html", "pages\n")
    _commit_all(pages_repo)

    _write(tracked_gaza, "generated gaza drift\n")
    _write(tracked_asset, "generated css drift\n")
    stale_site = source_repo / "output" / "site" / "gaza" / "editions" / "2026-09-21" / "index.html"
    stale_dispatch = source_repo / "output" / "dispatches" / "gaza" / "editions" / "2026-09-21" / "index.html"
    _write(stale_site, "stale site\n")
    _write(stale_dispatch, "stale dispatch\n")

    sync_commands = _fake_sync_commands(monkeypatch)

    result = sync_runner_repos(source_repo, pages_repo)

    assert result["ok"] is True
    assert result["preflight_initial"]["ok"] is False
    assert result["pre_sync_cleanup"]["attempted"] is True
    assert result["pre_sync_cleanup"]["plan"]["restore_paths"] == [
        "output/site/assets/site.css",
        "output/site/gaza/index.html",
    ]
    assert result["pre_sync_cleanup"]["plan"]["clean_paths"] == [
        "output/dispatches/gaza/editions/2026-09-21/index.html",
        "output/site/gaza/editions/2026-09-21/index.html",
    ]
    assert result["preflight_before"]["ok"] is True
    assert tracked_gaza.read_text(encoding="utf-8") == "committed gaza\n"
    assert tracked_asset.read_text(encoding="utf-8") == "committed css\n"
    assert not stale_site.exists()
    assert not stale_dispatch.exists()
    assert "output/site/gaza/editions/2026-09-21/index.html" not in _git_output(source_repo, "status", "--short", "--untracked-files=all")
    assert sync_commands == [
        ["git", "fetch", "origin", "add/pages-repo-default"],
        ["git", "reset", "--hard", "origin/add/pages-repo-default"],
        ["git", "fetch", "origin", "gh-pages"],
        ["git", "reset", "--hard", "origin/gh-pages"],
    ]
    assert result["pre_sync_cleanup"]["result"]["commands"] == [
        "git restore --source=HEAD --staged --worktree -- output/site/assets/site.css output/site/gaza/index.html",
        "git clean -fd -- output/dispatches/gaza/editions/2026-09-21/index.html output/site/gaza/editions/2026-09-21/index.html",
    ]


@pytest.mark.parametrize(
    ("dirty_paths", "expected_blocked"),
    [
        (
            [
                ("output/site/gaza/index.html", "modified"),
                ("scripts/run_daily_gaza.py", "modified"),
            ],
            {"scripts/run_daily_gaza.py"},
        ),
        (
            [
                ("output/dispatches/gaza/editions/2026-09-21/index.html", "untracked"),
                ("notes.txt", "untracked"),
            ],
            {"notes.txt"},
        ),
        (
            [
                ("output/site/gaza/index.html", "staged"),
            ],
            {"output/site/gaza/index.html"},
        ),
        (
            [
                ("output/site/gaza/index.html", "deleted"),
            ],
            {"output/site/gaza/index.html"},
        ),
    ],
)
def test_sync_blocks_mixed_or_ambiguous_dirty_state_without_cleanup_or_sync(
    monkeypatch, tmp_path, dirty_paths, expected_blocked
) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "add/pages-repo-default")
    _init_repo(pages_repo, "gh-pages")
    _write(source_repo / "README.md", "source fixture\n")

    for path, mode in dirty_paths:
        if mode in {"modified", "staged", "deleted"}:
            _write(source_repo / path, "committed\n")
    _write(pages_repo / "index.html", "pages\n")
    _commit_all(source_repo)
    _commit_all(pages_repo)

    for path, mode in dirty_paths:
        file_path = source_repo / path
        if mode == "modified":
            _write(file_path, "modified\n")
        elif mode == "untracked":
            _write(file_path, "untracked\n")
        elif mode == "staged":
            _write(file_path, "staged\n")
            _git(source_repo, "add", path)
        elif mode == "deleted":
            file_path.unlink()

    sync_commands = _fake_sync_commands(monkeypatch)

    result = sync_runner_repos(source_repo, pages_repo)

    assert result["ok"] is False
    assert result["pre_sync_cleanup"]["attempted"] is False
    assert {entry["path"] for entry in result["pre_sync_cleanup"]["blocked_entries"]} == expected_blocked
    assert sync_commands == []
    assert result["commands_run"] == []
    assert result["errors"] == ["runner repo state is outside bounded pre-sync cleanup scope"]


def test_sync_blocks_when_bounded_cleanup_fails(monkeypatch, tmp_path) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "add/pages-repo-default")
    _init_repo(pages_repo, "gh-pages")
    tracked_gaza = source_repo / "output" / "site" / "gaza" / "index.html"
    _write(tracked_gaza, "committed\n")
    _write(pages_repo / "index.html", "pages\n")
    _commit_all(source_repo)
    _commit_all(pages_repo)
    _write(tracked_gaza, "generated drift\n")

    sync_commands = _fake_sync_commands(monkeypatch)

    def fake_apply_cleanup_plan(_repo, _plan):
        return {"ok": False, "commands": ["git restore --source=HEAD --staged --worktree -- output/site/gaza/index.html"], "messages": ["restore failed"]}

    monkeypatch.setattr(maintenance, "apply_cleanup_plan", fake_apply_cleanup_plan)

    result = sync_runner_repos(source_repo, pages_repo)

    assert result["ok"] is False
    assert result["pre_sync_cleanup"]["attempted"] is True
    assert result["pre_sync_cleanup"]["result"]["ok"] is False
    assert result["preflight_before"] is None
    assert sync_commands == []
    assert result["commands_run"] == []
    assert result["errors"] == ["runner repo state is outside bounded pre-sync cleanup scope"]


def test_sync_batches_production_scale_untracked_generated_cleanup(monkeypatch, tmp_path) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "add/pages-repo-default")
    _init_repo(pages_repo, "gh-pages")
    _write(source_repo / "README.md", "source fixture\n")
    _write(pages_repo / "index.html", "pages\n")
    _commit_all(source_repo)
    _commit_all(pages_repo)

    approved_paths = [
        f"output/site/gaza/editions/2026-09-{day:02d}/residue-{index:04d}.json"
        for index in range(700)
        for day in [(index % 28) + 1]
    ]
    for path in approved_paths:
        _write(source_repo / path, "generated\n")

    sync_commands, cleanup_commands = _fake_sync_and_record_cleanup(monkeypatch)

    result = sync_runner_repos(source_repo, pages_repo)

    clean_commands = [args for args in cleanup_commands if args[:2] == ["git", "clean"]]
    cleaned_paths = [path for args in clean_commands for path in _command_path_args(args)]
    assert result["ok"] is True
    assert len(clean_commands) > 1
    assert all(maintenance._command_length(args) <= maintenance.MAX_GIT_CLEANUP_COMMAND_CHARS for args in clean_commands)
    assert sorted(cleaned_paths) == sorted(approved_paths)
    assert len(cleaned_paths) == len(set(cleaned_paths))
    assert all(path in approved_paths for path in cleaned_paths)
    assert result["preflight_before"]["ok"] is True
    assert _git_output(source_repo, "status", "--short", "--untracked-files=all") == ""
    assert sync_commands


def test_sync_batches_production_scale_tracked_generated_restore(monkeypatch, tmp_path) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "add/pages-repo-default")
    _init_repo(pages_repo, "gh-pages")
    tracked_paths = [
        f"output/site/gaza/editions/2026-09-{(index % 28) + 1:02d}/tracked-{index:04d}.html"
        for index in range(700)
    ]
    for path in tracked_paths:
        _write(source_repo / path, "committed\n")
    _write(pages_repo / "index.html", "pages\n")
    _commit_all(source_repo)
    _commit_all(pages_repo)
    for path in tracked_paths:
        _write(source_repo / path, "generated drift\n")

    _sync_commands, cleanup_commands = _fake_sync_and_record_cleanup(monkeypatch)

    result = sync_runner_repos(source_repo, pages_repo)

    restore_commands = [args for args in cleanup_commands if args[:2] == ["git", "restore"]]
    restored_paths = [path for args in restore_commands for path in _command_path_args(args)]
    assert result["ok"] is True
    assert len(restore_commands) > 1
    assert all(maintenance._command_length(args) <= maintenance.MAX_GIT_CLEANUP_COMMAND_CHARS for args in restore_commands)
    assert sorted(restored_paths) == sorted(tracked_paths)
    assert len(restored_paths) == len(set(restored_paths))
    assert all(path in tracked_paths for path in restored_paths)
    assert _git_output(source_repo, "status", "--short", "--untracked-files=all") == ""


def test_cleanup_stops_after_first_failed_batch(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo, "main")
    paths = [
        f"output/site/gaza/editions/2026-09-21/long-cleanup-path-{index:02d}.json"
        for index in range(6)
    ]
    calls: list[list[str]] = []
    monkeypatch.setattr(maintenance, "MAX_GIT_CLEANUP_COMMAND_CHARS", 120)

    def fake_run_command(args, *, cwd):
        _ = cwd
        calls.append(list(args))
        if len(calls) == 2:
            return subprocess.CompletedProcess(args, 1, "", "batch two failed")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(maintenance, "_run_command", fake_run_command)

    result = maintenance._run_git_clean(repo, paths)

    assert result["ok"] is False
    assert len(calls) == 2
    assert "cleanup batch 2/" in result["messages"][-1]
    assert "batch two failed" in result["messages"][-1]
    assert result["commands"] == [maintenance._render_command(args) for args in calls]


def test_sync_returns_structured_failure_when_cleanup_process_creation_fails(monkeypatch, tmp_path) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "add/pages-repo-default")
    _init_repo(pages_repo, "gh-pages")
    _write(source_repo / "README.md", "source fixture\n")
    _write(pages_repo / "index.html", "pages\n")
    _commit_all(source_repo)
    _commit_all(pages_repo)
    _write(source_repo / "output" / "site" / "gaza" / "editions" / "2026-09-21" / "index.html", "generated\n")
    sync_commands: list[list[str]] = []
    real_run_command = maintenance._run_command

    def fake_run_command(args, *, cwd):
        if args[:2] == ["git", "clean"]:
            raise OSError(206, "The filename or extension is too long")
        if args[:2] == ["git", "fetch"] or args[:2] == ["git", "reset"]:
            sync_commands.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run_command(args, cwd=cwd)

    monkeypatch.setattr(maintenance, "_run_command", fake_run_command)

    result = sync_runner_repos(source_repo, pages_repo)

    assert result["ok"] is False
    assert result["pre_sync_cleanup"]["result"]["ok"] is False
    assert any("OSError" in message and "206" in message for message in result["pre_sync_cleanup"]["result"]["messages"])
    assert result["preflight_before"] is None
    assert sync_commands == []
    assert result["commands_run"] == []
    assert result["errors"] == ["runner repo state is outside bounded pre-sync cleanup scope"]
    assert isinstance(result, dict)
