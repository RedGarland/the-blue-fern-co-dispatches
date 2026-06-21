from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.finalize_pages_publish as finalizer


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def git_output(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def configure_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "GIT_AUTHOR_NAME": "Codex",
        "GIT_AUTHOR_EMAIL": "codex@example.com",
        "GIT_COMMITTER_NAME": "Codex",
        "GIT_COMMITTER_EMAIL": "codex@example.com",
    }.items():
        monkeypatch.setenv(key, value)


def init_repo(path: Path, branch: str, filename: str = ".keep", contents: str = "keep\n", message: str = "initial commit") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init")
    git(path, "checkout", "-b", branch)
    file_path = path / filename
    file_path.write_text(contents, encoding="utf-8")
    git(path, "add", filename)
    git(path, "commit", "-m", message)
    return path


def write_gitlink(source_repo: Path, target_sha: str) -> None:
    git(source_repo, "update-index", "--add", "--cacheinfo", "160000", target_sha, "bluefern-dispatches-pages")
    git(source_repo, "commit", "-m", "Record Pages gitlink")


def make_repo_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str, str]:
    configure_git_identity(monkeypatch)
    source_repo = init_repo(tmp_path / "source", "add/pages-repo-default")
    pages_repo = init_repo(tmp_path / "bluefern-dispatches-pages", "main")
    pages_old_sha = git_output(pages_repo, "rev-parse", "HEAD")
    (pages_repo / "release.txt").write_text("release two\n", encoding="utf-8")
    git(pages_repo, "add", "release.txt")
    git(pages_repo, "commit", "-m", "publish release two")
    pages_new_sha = git_output(pages_repo, "rev-parse", "HEAD")
    write_gitlink(source_repo, pages_old_sha)
    return source_repo, pages_repo, pages_old_sha, pages_new_sha


def fake_verification(pages_sha: str) -> dict[str, object]:
    return {
        "ok": True,
        "edition_url": "https://dispatches.thebluefernco.com/gaza/editions/2026-06-21/",
        "archive_url": "https://dispatches.thebluefernco.com/gaza/archive.html",
        "edition_status": 200,
        "archive_status": 200,
        "archive_includes_date": True,
        "edition_body_contains_date": True,
        "pages_sha": pages_sha,
    }


def test_dry_run_reports_branch_pr_text_and_scope(tmp_path, monkeypatch, capsys):
    source_repo, pages_repo, source_sha, pages_sha = make_repo_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer, "verify_public_urls", lambda dispatch, date_text: fake_verification(pages_sha))
    monkeypatch.chdir(source_repo)

    code = finalizer.main(
        [
            "--dispatch",
            "gaza",
            "--date",
            "2026-06-21",
            "--pages-repo",
            str(pages_repo),
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "Branch: chore/update-pages-gitlink-gaza-2026-06-21" in output
    assert "PR title:" in output
    assert "Update Pages gitlink for Gaza June 21, 2026 publish" in output
    assert "Pages commit: `"+pages_sha+"`" in output
    assert "Source gitlink: " + source_sha in output
    assert "No generated output, logs, or source records are committed in this branch." in output
    assert "git add -- bluefern-dispatches-pages" in output


def test_refuses_when_source_repo_has_staged_files(tmp_path, monkeypatch, capsys):
    source_repo, pages_repo, _, pages_sha = make_repo_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer, "verify_public_urls", lambda dispatch, date_text: fake_verification(pages_sha))
    monkeypatch.chdir(source_repo)
    (source_repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(source_repo, "add", "staged.txt")

    code = finalizer.main(
        [
            "--dispatch",
            "gaza",
            "--date",
            "2026-06-21",
            "--pages-repo",
            str(pages_repo),
        ]
    )

    err = capsys.readouterr().err
    assert code == 1
    assert "source repo has staged files" in err


def test_refuses_when_pages_repo_is_dirty(tmp_path, monkeypatch, capsys):
    source_repo, pages_repo, _, pages_sha = make_repo_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer, "verify_public_urls", lambda dispatch, date_text: fake_verification(pages_sha))
    monkeypatch.chdir(source_repo)
    (pages_repo / "release.txt").write_text("dirty\n", encoding="utf-8")

    code = finalizer.main(
        [
            "--dispatch",
            "gaza",
            "--date",
            "2026-06-21",
            "--pages-repo",
            str(pages_repo),
        ]
    )

    err = capsys.readouterr().err
    assert code == 1
    assert "Pages repo must be clean before finalizing" in err


def test_cleanup_plan_and_dry_run_output(tmp_path, monkeypatch, capsys):
    source_repo, pages_repo, _, pages_new_sha = make_repo_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer, "verify_public_urls", lambda dispatch, date_text: fake_verification(pages_new_sha))
    monkeypatch.chdir(source_repo)
    git(source_repo, "update-index", "--add", "--cacheinfo", "160000", pages_new_sha, "bluefern-dispatches-pages")
    git(source_repo, "commit", "-m", "Sync Pages gitlink")
    tracked = source_repo / "output" / "site" / "gaza" / "editions" / "2026-06-21" / "index.html"
    tracked.parent.mkdir(parents=True, exist_ok=True)
    tracked.write_text("old edition\n", encoding="utf-8")
    git(source_repo, "add", "output/site/gaza/editions/2026-06-21/index.html")
    git(source_repo, "commit", "-m", "add edition")
    tracked.write_text("updated edition\n", encoding="utf-8")
    untracked_log = source_repo / "logs" / "gaza-daily-2026-06-21.log"
    untracked_log.parent.mkdir(parents=True, exist_ok=True)
    untracked_log.write_text("log\n", encoding="utf-8")

    plan = finalizer.plan_generated_cleanup(source_repo, "gaza", "2026-06-21")
    assert "output/site/gaza/editions/2026-06-21/index.html" in plan.restore_paths
    assert "logs/gaza-daily-2026-06-21.log" in plan.remove_paths

    code = finalizer.main(
        [
            "--dispatch",
            "gaza",
            "--date",
            "2026-06-21",
            "--pages-repo",
            str(pages_repo),
            "--clean-generated",
            "--dry-run",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "Cleanup plan for Gaza 2026-06-21" in output
    assert "restore_paths:" in output
    assert "remove_paths:" in output
    assert "git status --short" in output
    assert "python scripts/validate_repo_governance.py" in output


def test_pr_title_and_body_generation():
    verification = fake_verification("new-sha")
    title = finalizer._pr_title("gaza", "2026-06-21")
    body = finalizer._pr_body("gaza", "2026-06-21", "new-sha", "old-sha", verification)

    assert title == "Update Pages gitlink for Gaza June 21, 2026 publish"
    assert "Pages commit: `new-sha`" in body
    assert "Dispatch/date: `Gaza` / `2026-06-21`" in body
    assert "- Archive includes date: true" in body
    assert "Only the `bluefern-dispatches-pages` gitlink is committed here." in body


def test_creates_gitlink_only_commit_in_real_repo(tmp_path, monkeypatch):
    source_repo, pages_repo, _, pages_sha = make_repo_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer, "verify_public_urls", lambda dispatch, date_text: fake_verification(pages_sha))
    monkeypatch.setattr(finalizer, "_push_branch", lambda source_repo, branch_name: None)
    monkeypatch.setattr(finalizer, "_create_pr", lambda *args, **kwargs: None)
    monkeypatch.chdir(source_repo)

    code = finalizer.main(
        [
            "--dispatch",
            "gaza",
            "--date",
            "2026-06-21",
            "--pages-repo",
            str(pages_repo),
            "--branch-name",
            "chore/update-pages-gitlink-gaza-2026-06-21",
        ]
    )

    assert code == 0
    assert git_output(source_repo, "branch", "--show-current") == "chore/update-pages-gitlink-gaza-2026-06-21"
    commit_subject = git_output(source_repo, "log", "-1", "--pretty=%s")
    assert commit_subject == "Update Pages gitlink for Gaza 2026-06-21 publish"
    changed_paths = git_output(source_repo, "show", "--pretty=", "--name-only", "HEAD").splitlines()
    assert changed_paths == [finalizer.GITLINK_PATH]
