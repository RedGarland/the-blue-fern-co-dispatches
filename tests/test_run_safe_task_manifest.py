from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import bluefern_dispatches.safe_git_task as safe_git_task


REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _write_manifest(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _run_script(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_safe_task.py"), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _init_source_repo(root: Path) -> Path:
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
    return source


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
def task_repos(tmp_path: Path) -> tuple[Path, Path]:
    source = _init_source_repo(tmp_path)
    pages = _init_pages_repo(tmp_path)
    return source, pages


def test_dry_run_performs_no_branch_staging_commit_or_push_mutation(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, pages = task_repos
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "base": "add/pages-repo-default",
            "commit_message": "Add example task",
            "files": ["scripts/example.py", "tests/test_example.py"],
            "push": True,
            "allow_review_output": False,
        },
    )
    _write(source, "scripts/example.py", "print('ok')\n")
    _write(source, "tests/test_example.py", "def test_example():\n    assert True\n")
    before_head = _git_output(source, "rev-parse", "HEAD")
    before_branch = _git_output(source, "branch", "--show-current")

    monkeypatch.chdir(source)
    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 0
    assert _git_output(source, "rev-parse", "HEAD") == before_head
    assert _git_output(source, "branch", "--show-current") == before_branch
    assert _git_output(source, "diff", "--cached", "--name-only") == ""
    assert "Planned actions:" in captured.out
    assert "Push requested: yes" in captured.out
    assert "Next PR info:" in captured.out


def test_missing_manifest_fields_are_rejected(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, pages = task_repos
    manifest = _write_manifest(source / "manifest.json", {"branch": "add/example-task"})
    monkeypatch.chdir(source)

    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Manifest is missing required fields" in captured.err


@pytest.mark.parametrize(
    "files, expected",
    [
        (["output/site/blocked.txt"], "Refusing forbidden path: output/site/blocked.txt"),
        (["bluefern-dispatches-pages/blocked.txt"], "Refusing forbidden path: bluefern-dispatches-pages/blocked.txt"),
        (["logs/blocked.txt"], "Refusing forbidden path: logs/blocked.txt"),
        (["output/tmp-backups-pages/blocked.txt"], "Refusing forbidden path: output/tmp-backups-pages/blocked.txt"),
        ([".env"], "Refusing forbidden path: .env"),
        ([".env.local"], "Refusing forbidden path: .env.local"),
    ],
)
def test_forbidden_paths_are_rejected(
    task_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    files: list[str],
    expected: str,
) -> None:
    source, pages = task_repos
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": files,
        },
    )
    monkeypatch.chdir(source)

    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Refusing to stage unsafe paths." in captured.err
    assert expected in captured.err


def test_output_review_requires_explicit_allow_flag(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, pages = task_repos
    _write(source, "output/review/gaza/review.txt", "review")
    blocked_manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": ["output/review/gaza/review.txt"],
        },
    )
    allowed_manifest = _write_manifest(
        source / "manifest-allowed.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": ["output/review/gaza/review.txt"],
            "allow_review_output": True,
        },
    )
    monkeypatch.chdir(source)

    blocked = safe_git_task.main_run(["--manifest", str(blocked_manifest), "--pages-repo", str(pages), "--dry-run"])
    allowed = safe_git_task.main_run(["--manifest", str(allowed_manifest), "--pages-repo", str(pages), "--dry-run"])

    captured = capsys.readouterr()
    assert blocked == 1
    assert allowed == 0
    assert "Review output requires --allow-review-output" in captured.err
    assert "Review output allowed: output/review/gaza/review.txt" in captured.out


def test_exact_safe_files_are_staged_and_committed(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, pages = task_repos
    _write(source, "scripts/example.py", "print('ok')\n")
    _write(source, "tests/test_example.py", "def test_example():\n    assert True\n")
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": ["scripts/example.py", "tests/test_example.py"],
        },
    )
    monkeypatch.chdir(source)

    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages)])

    captured = capsys.readouterr()
    assert code == 0
    assert _git_output(source, "branch", "--show-current") == "add/example-task"
    assert _git_output(source, "diff", "--cached", "--name-only") == ""
    assert _git_output(source, "log", "-1", "--pretty=%s") == "Add example task"
    assert _git_output(source, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines() == [
        "scripts/example.py",
        "tests/test_example.py",
    ]
    assert "Next PR info:" in captured.out


@pytest.mark.parametrize("branch", ["add/pages-repo-default", "main", "master", "gh-pages"])
def test_protected_branch_names_are_rejected(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], branch: str
) -> None:
    source, pages = task_repos
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": branch,
            "commit_message": "Add example task",
            "files": ["scripts/example.py"],
        },
    )
    monkeypatch.chdir(source)

    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 1
    assert f"Refusing protected branch: {branch}" in captured.err


def test_push_behavior_can_be_mocked(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, pages = task_repos
    _write(source, "scripts/example.py", "print('ok')\n")
    _write(source, "tests/test_example.py", "def test_example():\n    assert True\n")
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": ["scripts/example.py", "tests/test_example.py"],
            "push": False,
        },
    )
    monkeypatch.chdir(source)

    original_run_git = safe_git_task._run_git
    pushes: list[tuple[str, ...]] = []

    def fake_run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "push":
            pushes.append(args)
            return subprocess.CompletedProcess(("git", *args), 0, "", "")
        return original_run_git(repo, *args, check=check)

    monkeypatch.setattr(safe_git_task, "_run_git", fake_run_git)

    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages), "--push"])

    captured = capsys.readouterr()
    assert code == 0
    assert pushes == [("push", "-u", "origin", "add/example-task")]
    assert "Pushed branch add/example-task to origin." in captured.out


def test_pages_repo_dirty_state_blocks_the_runner(
    task_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source, pages = task_repos
    _write(pages, "dirty.txt", "dirty")
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": ["scripts/example.py"],
        },
    )
    monkeypatch.chdir(source)

    code = safe_git_task.main_run(["--manifest", str(manifest), "--pages-repo", str(pages), "--dry-run"])

    captured = capsys.readouterr()
    assert code == 1
    assert "Pages repo must be clean before running this helper" in captured.err


def test_direct_script_execution_works_without_pythonpath(task_repos: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    source, pages = task_repos
    _write(source, "scripts/example.py", "print('ok')\n")
    _write(source, "tests/test_example.py", "def test_example():\n    assert True\n")
    manifest = _write_manifest(
        source / "manifest.json",
        {
            "branch": "add/example-task",
            "commit_message": "Add example task",
            "files": ["scripts/example.py", "tests/test_example.py"],
        },
    )

    result = _run_script(["--manifest", str(manifest), "--pages-repo", str(pages), "--dry-run"], cwd=source)

    assert result.returncode == 0
    assert "usage:" not in result.stdout.lower()
    assert "Next PR info:" in result.stdout
