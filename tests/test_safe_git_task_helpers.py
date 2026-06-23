from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import bluefern_dispatches.safe_git_task as safe_git_task


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git_output(repo: Path, *args: str) -> str:
    return _git(repo, *args).stdout.strip()


def _write(repo: Path, relative: str, text: str = "content") -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _init_source_repo(root: Path) -> tuple[Path, Path]:
    origin = root / "source-origin.git"
    source = root / "source"
    _git(root, "init", "--bare", str(origin))
    source.mkdir(parents=True, exist_ok=True)
    _git(source, "init", "-b", "add/pages-repo-default")
    _git(source, "config", "user.email", "tests@example.test")
    _git(source, "config", "user.name", "Tests")
    _write(source, "README.md", "base")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "base")
    _git(source, "remote", "add", "origin", str(origin))
    _git(source, "push", "-u", "origin", "add/pages-repo-default")
    return source, origin


def _init_pages_repo(root: Path) -> Path:
    pages = root / "bluefern-dispatches-pages"
    pages.mkdir(parents=True, exist_ok=True)
    _git(pages, "init", "-b", "gh-pages")
    _git(pages, "config", "user.email", "tests@example.test")
    _git(pages, "config", "user.name", "Tests")
    _write(pages, "README.md", "pages")
    _git(pages, "add", "README.md")
    _git(pages, "commit", "-m", "pages")
    return pages


@pytest.fixture()
def task_repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    source, origin = _init_source_repo(tmp_path)
    pages = _init_pages_repo(tmp_path)
    return source, pages, origin


def test_start_helper_creates_or_switches_feature_branch_from_base(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)

    code = safe_git_task.main_start(["--branch", "add/example-task", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 0
    assert _git_output(source, "branch", "--show-current") == "add/example-task"
    assert _git_output(source, "rev-parse", "HEAD") == _git_output(source, "rev-parse", "origin/add/pages-repo-default")
    assert "Current branch: add/example-task" in captured.out
    assert "Base branch commit:" in captured.out
    assert "Feature branch commit:" in captured.out
    assert "Pages repo snapshot:" in captured.out


def test_start_helper_warns_when_existing_branch_is_not_based_on_current_origin_base(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, origin = task_repos
    monkeypatch.chdir(source)

    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "task.txt", "feature")
    _git(source, "add", "task.txt")
    _git(source, "commit", "-m", "feature commit")
    _git(source, "checkout", "add/pages-repo-default")
    _write(source, "base.txt", "base v2")
    _git(source, "add", "base.txt")
    _git(source, "commit", "-m", "base update")
    _git(source, "push", "origin", "add/pages-repo-default")

    code = safe_git_task.main_start(["--branch", "add/example-task", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 0
    assert _git_output(source, "branch", "--show-current") == "add/example-task"
    assert "WARNING: add/example-task is not based on current origin/add/pages-repo-default." in captured.out
    assert _git_output(source, "rev-parse", "origin/add/pages-repo-default") == _git_output(origin, "rev-parse", "refs/heads/add/pages-repo-default")


@pytest.mark.parametrize("unsafe_path", [".", "*"])
def test_stage_helper_refuses_literal_globs_and_dot_paths(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], unsafe_path: str) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")

    code = safe_git_task.main_stage(["--files", unsafe_path, "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 1
    assert "Refusing to stage unsafe paths." in captured.err


def test_stage_helper_refuses_generated_and_pages_paths(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")

    code_site = safe_git_task.main_stage(["--files", "output/site/example.txt", "--pages-repo", str(pages)])
    code_pages = safe_git_task.main_stage(["--files", "bluefern-dispatches-pages/example.txt", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code_site == 1
    assert code_pages == 1
    assert "Refusing forbidden path: output/site/example.txt" in captured.err
    assert "Refusing forbidden path: bluefern-dispatches-pages/example.txt" in captured.err


def test_stage_helper_allows_exact_safe_source_and_test_files(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "scripts/example.py", "print('ok')\n")
    _write(source, "tests/test_example.py", "def test_example():\n    assert True\n")

    code = safe_git_task.main_stage(["--files", "scripts/example.py", "tests/test_example.py", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 0
    assert _git_output(source, "diff", "--cached", "--name-only").splitlines() == ["scripts/example.py", "tests/test_example.py"]
    assert "Staged files:" in captured.out
    assert "scripts/example.py" in captured.out
    assert "tests/test_example.py" in captured.out
    assert _git_output(pages, "status", "--short") == ""


def test_stage_helper_allows_review_output_only_with_flag(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "output/review/gaza/review.txt", "review")

    blocked = safe_git_task.main_stage(["--files", "output/review/gaza/review.txt", "--pages-repo", str(pages)])
    allowed = safe_git_task.main_stage(
        ["--files", "output/review/gaza/review.txt", "--pages-repo", str(pages), "--allow-review-output"]
    )

    captured = capsys.readouterr()
    assert blocked == 1
    assert allowed == 0
    assert "Review output requires --allow-review-output" in captured.err
    assert "Review output allowed: output/review/gaza/review.txt" in captured.out


def test_pages_repo_dirty_status_blocks_start_helper(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _write(pages, "dirty.txt", "dirty")

    code = safe_git_task.main_start(["--branch", "add/example-task", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 1
    assert "Pages repo must be clean before running this helper" in captured.err


def test_commit_helper_refuses_base_branch(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _write(source, "scripts/example.py", "print('ok')\n")
    _git(source, "add", "scripts/example.py")

    code = safe_git_task.main_commit(["--message", "Add example task", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 1
    assert "Refusing to commit on protected branch add/pages-repo-default." in captured.err


def test_commit_helper_refuses_forbidden_staged_paths(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "output/site/blocked.txt", "blocked")
    _git(source, "add", "output/site/blocked.txt")

    code = safe_git_task.main_commit(["--message", "Add example task", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 1
    assert "Refusing staged forbidden path: output/site/blocked.txt" in captured.err


def test_commit_helper_commits_safe_staged_files(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "scripts/example.py", "print('ok')\n")
    _git(source, "add", "scripts/example.py")
    before = _git_output(source, "rev-parse", "HEAD")

    code = safe_git_task.main_commit(["--message", "Add example task", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    after = _git_output(source, "rev-parse", "HEAD")
    assert code == 0
    assert after != before
    assert _git_output(source, "diff", "--cached", "--name-only") == ""
    assert "Next PR info:" in captured.out
    assert "Base branch: add/pages-repo-default" in captured.out
    assert "Compare branch: add/example-task" in captured.out


def test_commit_helper_dry_run_does_not_commit(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "scripts/example.py", "print('ok')\n")
    _git(source, "add", "scripts/example.py")
    before = _git_output(source, "rev-parse", "HEAD")

    code = safe_git_task.main_commit(["--message", "Add example task", "--dry-run", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    after = _git_output(source, "rev-parse", "HEAD")
    assert code == 0
    assert after == before
    assert _git_output(source, "diff", "--cached", "--name-only") == "scripts/example.py"
    assert "Dry run: no commit created." in captured.out


def test_commit_helper_push_is_mocked_and_targets_current_feature_branch(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "scripts/example.py", "print('ok')\n")
    _git(source, "add", "scripts/example.py")

    original_run_git = safe_git_task._run_git
    pushes: list[tuple[str, ...]] = []

    def fake_run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "push":
            pushes.append(args)
            return subprocess.CompletedProcess(("git", *args), 0, "", "")
        return original_run_git(repo, *args, check=check)

    monkeypatch.setattr(safe_git_task, "_run_git", fake_run_git)

    code = safe_git_task.main_commit(["--message", "Add example task", "--push", "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 0
    assert pushes == [("push", "-u", "origin", "add/example-task")]
    assert "Pushed branch add/example-task to origin." in captured.out


def test_commit_helper_dry_run_with_review_output_requires_flag(task_repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    source, pages, _origin = task_repos
    monkeypatch.chdir(source)
    _git(source, "checkout", "-b", "add/example-task")
    _write(source, "output/review/gaza/review.txt", "review")
    _git(source, "add", "output/review/gaza/review.txt")

    blocked = safe_git_task.main_commit(["--message", "Add example task", "--dry-run", "--pages-repo", str(pages)])
    allowed = safe_git_task.main_commit(
        ["--message", "Add example task", "--dry-run", "--allow-review-output", "--pages-repo", str(pages)]
    )

    captured = capsys.readouterr()
    assert blocked == 1
    assert allowed == 0
    assert "Refusing staged output/review paths without --allow-review-output." in captured.err
