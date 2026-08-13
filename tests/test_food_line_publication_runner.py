from __future__ import annotations

import subprocess
from pathlib import Path

import scripts.run_food_line_publication_runner as runner


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_repo(repo: Path, branch: str) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "codex@example.com")
    _git(repo, "config", "user.name", "Codex")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "checkout", "-b", branch)
    (repo / "tracked.txt").write_bytes(b"tracked\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial", "--no-gpg-sign")


def _state(head: str, branch: str) -> dict[str, object]:
    return {
        "root": "repo",
        "branch": branch,
        "head": head,
        "clean": True,
    }


def test_safe_directory_configuration_is_process_local_and_does_not_persist(monkeypatch, tmp_path: Path) -> None:
    source_repo = tmp_path / "source"
    pages_repo = tmp_path / "pages"
    _init_repo(source_repo, "source-branch")
    _init_repo(pages_repo, "gh-pages")

    global_config = tmp_path / "global.gitconfig"
    system_config = tmp_path / "system.gitconfig"
    global_config.write_text("[test]\n\tmarker = global\n", encoding="utf-8")
    system_config.write_text("[test]\n\tmarker = system\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system_config))

    config_paths = [
        global_config,
        system_config,
        source_repo / ".git" / "config",
        pages_repo / ".git" / "config",
    ]
    before = {path: path.read_bytes() for path in config_paths}

    monkeypatch.setattr(
        runner,
        "_load_release_readiness",
        lambda root, edition_date, approved_proposal_path: {
            "path": root / "data/dispatches/food-line/review/release-readiness" / f"{edition_date}.json",
            "payload": {"status": runner.APPROVED_STATUS},
        },
    )
    monkeypatch.setattr(runner, "load_approved_proposal", lambda *args: type("Bundle", (), {"proposal_sha256": "sha256"})())
    monkeypatch.setattr(runner, "validate_publish_scope", lambda **kwargs: [])

    result = runner.run_publication(
        repo_root=source_repo,
        pages_repo=pages_repo,
        source_branch="source-branch",
        pages_branch="gh-pages",
        date="2026-08-05",
        check_only=True,
    )

    assert result["ok"] is True
    assert {path: path.read_bytes() for path in config_paths} == before


def test_check_only_validates_without_generation_or_sync(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(runner, "_repo_state", lambda repo_root, *, required_branch, label: _state("abc123", required_branch))
    monkeypatch.setattr(
        runner,
        "_load_release_readiness",
        lambda root, edition_date, approved_proposal_path: {
            "path": root / "data/dispatches/food-line/review/release-readiness" / f"{edition_date}.json",
            "payload": {
                "schema_version": "food_line_release_readiness_v1",
                "status": runner.APPROVED_STATUS,
                "approved_proposal_path": approved_proposal_path.relative_to(root).as_posix(),
                "edition_date": edition_date,
            },
        },
    )
    monkeypatch.setattr(runner, "load_approved_proposal", lambda root, proposal_path, edition_date: type("Bundle", (), {"proposal_sha256": "sha256"})())
    monkeypatch.setattr(runner, "validate_publish_scope", lambda **kwargs: calls.append(("scope", kwargs)) or [])
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generation must not run in check-only mode")))
    monkeypatch.setattr(runner, "sync_pages_from_source", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sync must not run in check-only mode")))

    result = runner.run_publication(
        repo_root=Path(r"C:\BlueFernRunner\FoodLineDispatches"),
        pages_repo=Path(r"C:\BlueFernRunner\FoodLineDispatches\bluefern-dispatches-pages"),
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        date="2026-08-05",
        check_only=True,
    )

    assert result["ok"] is True
    assert result["status"] == "check_only_success"
    assert result["mode"] == "check_only"
    assert result["proposed_modified_paths"] == []
    assert result["proposed_deleted_paths"] == []
    assert result["temp_workspace"] is None
    assert result["temp_workspace_removed"] is False
    assert calls and calls[0][0] == "scope"
    assert calls[0][1]["strict"] is True


def test_dry_run_full_reports_temp_cleanup_and_proposed_paths(monkeypatch, tmp_path: Path) -> None:
    clone_calls: list[Path] = []
    scope_calls: list[dict[str, object]] = []
    sync_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runner, "_repo_state", lambda repo_root, *, required_branch, label: _state("abc123", required_branch))
    monkeypatch.setattr(
        runner,
        "_load_release_readiness",
        lambda root, edition_date, approved_proposal_path: {
            "path": root / "data/dispatches/food-line/review/release-readiness" / f"{edition_date}.json",
            "payload": {
                "schema_version": "food_line_release_readiness_v1",
                "status": runner.APPROVED_STATUS,
                "approved_proposal_path": approved_proposal_path.relative_to(root).as_posix(),
                "edition_date": edition_date,
            },
        },
    )
    monkeypatch.setattr(runner, "load_approved_proposal", lambda root, proposal_path, edition_date: type("Bundle", (), {"proposal_sha256": "sha256"})())
    monkeypatch.setattr(runner, "_clone_repo", lambda source_repo, clone_repo, *, branch: (clone_repo.mkdir(parents=True, exist_ok=True), clone_calls.append(clone_repo)))
    monkeypatch.setattr(runner, "_validate_scope", lambda **kwargs: scope_calls.append(kwargs) or [])

    def fake_generation(root: Path, date: str, **kwargs):
        assert kwargs["generate_audio"] is False
        release_manifest = root / "data/dispatches/food-line/review/releases" / f"{date}.json"
        release_manifest.parent.mkdir(parents=True, exist_ok=True)
        release_manifest.write_text("{}", encoding="utf-8")
        return {
            "ok": True,
            "generator_source_commit": "abc123",
            "release_manifest_path": str(release_manifest),
            "errors": [],
        }

    def fake_sync(**kwargs):
        sync_calls.append(kwargs)
        return {
            "ok": True,
            "commit_status": "dry-run",
            "push_status": "dry-run",
            "additions": ["food-line/index.html"],
            "modifications": ["food-line/editions/2026-08-05/edition_manifest.json"],
            "deletions": [],
            "pushed": False,
            "errors": [],
        }

    monkeypatch.setattr(runner, "run_food_line_dispatch", fake_generation)
    monkeypatch.setattr(runner, "sync_pages_from_source", fake_sync)

    result = runner.run_publication(
        repo_root=tmp_path / "repo",
        pages_repo=tmp_path / "pages",
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        date="2026-08-05",
        dry_run_full=True,
    )

    assert result["ok"] is True
    assert result["status"] == "dry_run_full_success"
    assert result["mode"] == "dry_run_full"
    assert result["proposed_modified_paths"] == [
        "food-line/editions/2026-08-05/edition_manifest.json",
        "food-line/index.html",
    ]
    assert result["proposed_deleted_paths"] == []
    assert result["temp_workspace_removed"] is True
    assert result["publication_report"]["commit_status"] == "dry-run"
    assert scope_calls and scope_calls[0]["release_manifest"].name == "2026-08-05.json"
    assert sync_calls and sync_calls[0]["dry_run"] is True


def test_publication_with_push_forwards_commit_and_push(monkeypatch, tmp_path: Path) -> None:
    sync_calls: list[dict[str, object]] = []
    scope_calls: list[dict[str, object]] = []

    monkeypatch.setattr(runner, "_repo_state", lambda repo_root, *, required_branch, label: _state("abc123", required_branch))
    monkeypatch.setattr(
        runner,
        "_load_release_readiness",
        lambda root, edition_date, approved_proposal_path: {
            "path": root / "data/dispatches/food-line/review/release-readiness" / f"{edition_date}.json",
            "payload": {
                "schema_version": "food_line_release_readiness_v1",
                "status": runner.APPROVED_STATUS,
                "approved_proposal_path": approved_proposal_path.relative_to(root).as_posix(),
                "edition_date": edition_date,
            },
        },
    )
    monkeypatch.setattr(runner, "load_approved_proposal", lambda root, proposal_path, edition_date: type("Bundle", (), {"proposal_sha256": "sha256"})())
    monkeypatch.setattr(runner, "_validate_scope", lambda **kwargs: scope_calls.append(kwargs) or [])

    def fake_generation(root: Path, date: str, **kwargs):
        assert kwargs["generate_audio"] is False
        release_manifest = root / "data/dispatches/food-line/review/releases" / f"{date}.json"
        release_manifest.parent.mkdir(parents=True, exist_ok=True)
        release_manifest.write_text("{}", encoding="utf-8")
        return {
            "ok": True,
            "generator_source_commit": "abc123",
            "release_manifest_path": str(release_manifest),
            "errors": [],
        }

    def fake_sync(**kwargs):
        sync_calls.append(kwargs)
        return {
            "ok": True,
            "commit_status": "committed",
            "push_status": "pushed",
            "pushed": True,
            "additions": [],
            "modifications": ["food-line/index.html"],
            "deletions": [],
            "errors": [],
        }

    monkeypatch.setattr(runner, "run_food_line_dispatch", fake_generation)
    monkeypatch.setattr(runner, "sync_pages_from_source", fake_sync)

    result = runner.run_publication(
        repo_root=tmp_path / "repo",
        pages_repo=tmp_path / "pages",
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        date="2026-08-05",
        push=True,
    )

    assert result["ok"] is True
    assert result["status"] == "publication_success"
    assert result["push_performed"] is True
    assert result["publication_report"]["push_status"] == "pushed"
    assert scope_calls and scope_calls[0]["release_manifest"].name == "2026-08-05.json"
    assert sync_calls and sync_calls[0]["commit"] is True
    assert sync_calls[0]["push"] is True
