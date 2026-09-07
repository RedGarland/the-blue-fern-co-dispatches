from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import bluefern_dispatches.archive_pages_preparation as prep
from bluefern_dispatches.archive_pages_preparation import (
    ArchivePagesPreparationError,
    prepare_dispatch_archive_pages,
)


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise AssertionError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _init_source(root: Path) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-b", "add/pages-repo-default")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Tests")
    _write(root / "README.md", "source\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


def _init_pages_with_remote(root: Path, remote: Path, *, branch: str = "gh-pages", remote_url: str | None = None) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-b", branch)
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Tests")
    _git(root, "remote", "add", "origin", str(remote_url or remote))
    _write(root / "CNAME", "dispatches.thebluefernco.com\n")
    _write(root / "README.md", "pages\n")
    _git(root, "add", "CNAME", "README.md")
    _git(root, "commit", "-m", "initial pages")
    _git(root, "push", "-u", "origin", branch)
    return root


def _pages_repo(tmp_path: Path, *, branch: str = "gh-pages", remote_url: str | None = None) -> tuple[Path, Path]:
    remote = tmp_path / "RedGarland" / "the-blue-fern-co-dispatches.git"
    remote.parent.mkdir(parents=True)
    _git(tmp_path, "init", "--bare", str(remote))
    pages = _init_pages_with_remote(tmp_path / "pages", remote, branch=branch, remote_url=remote_url)
    return pages, remote


def _archive_html(dispatch: str, marker: str = "") -> str:
    label = "Food Line Archive" if dispatch == "food-line" else "The Care Line Dispatch Archive"
    count = "16" if dispatch == "food-line" else "4"
    return (
        "<!doctype html><html><head>"
        f'<link rel="canonical" href="https://dispatches.thebluefernco.com/{dispatch}/archive.html">'
        f"<title>{label}</title></head><body>"
        "<h2>Archive</h2><ul class=\"edition-list\">"
        "<li><span class=\"edition-date\">2026-08-20</span><a href=\"editions/2026-08-20/\">Daily edition</a></li>"
        "</ul><section class=\"retrospective-recoveries\">"
        "<h2>Retrospective recoveries</h2>"
        f"<p>August 2026 retrospective — {count} source-backed developments</p>"
        f"<a href=\"source-based-retrospectives/{dispatch}-august-2026-source-based-publication/\">Read retrospective</a>"
        f"</section>{marker}</body></html>"
    )


def _source_archive(source: Path, dispatch: str, marker: str = "") -> Path:
    path = source / "output" / "site" / dispatch / "archive.html"
    _write(path, _archive_html(dispatch, marker))
    return path


def _pages_archive(pages: Path, dispatch: str, marker: str = "old") -> Path:
    path = pages / dispatch / "archive.html"
    _write(path, _archive_html(dispatch, marker))
    _git(pages, "add", f"{dispatch}/archive.html")
    _git(pages, "commit", "-m", f"add {dispatch} archive")
    _git(pages, "push", "origin", "gh-pages")
    return path


def test_valid_food_archive_prepares_only_food_archive_and_receipt(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    source_archive = _source_archive(source, "food-line", "new")
    pages, _ = _pages_repo(tmp_path)
    _pages_archive(pages, "food-line", "old")
    _write(pages / "food-line" / "rss.xml", "rss sentinel\n")
    _git(pages, "add", "food-line/rss.xml")
    _git(pages, "commit", "-m", "add rss sentinel")
    _git(pages, "push", "origin", "gh-pages")
    rss_before = (pages / "food-line" / "rss.xml").read_bytes()

    result = prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=pages)

    assert result["status"] == "prepared_not_committed"
    assert result["pages_push_performed"] is False
    assert result["pages_changed_paths"] == ["food-line/archive.html"]
    assert result["source_archive_sha256"]["output/site/food-line/archive.html"] == _sha(source_archive)
    assert result["destination_archive_sha256"]["food-line/archive.html"] == _sha(pages / "food-line/archive.html")
    assert (pages / "food-line/rss.xml").read_bytes() == rss_before
    receipt = json.loads((source / result["preparation_receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["deployment_status"] == "prepared_not_committed"
    assert receipt["pages_push_performed"] is False


def test_valid_care_archive_prepares_only_care_archive_without_queue_mutation(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    _source_archive(source, "care-line", "new")
    pages, _ = _pages_repo(tmp_path)
    _pages_archive(pages, "care-line", "old")

    result = prepare_dispatch_archive_pages(root=source, dispatch="care-line", pages_repo=pages)

    assert result["pages_changed_paths"] == ["care-line/archive.html"]
    assert not (source / "data/dispatches/care-line/queue").exists()
    assert not (pages / "care-line/index.html").exists()


def test_combined_archive_preparation_and_commit_is_exact_scope_and_no_push(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    _source_archive(source, "food-line", "new-food")
    _source_archive(source, "care-line", "new-care")
    pages, remote = _pages_repo(tmp_path)
    _pages_archive(pages, "food-line", "old-food")
    _pages_archive(pages, "care-line", "old-care")
    remote_before = _git(remote, "rev-parse", "gh-pages")

    result = prepare_dispatch_archive_pages(root=source, dispatch="both", pages_repo=pages, commit=True)

    assert result["status"] == "prepared_not_pushed"
    assert result["pages_local_commit_created"] is True
    assert sorted(result["pages_changed_paths"]) == ["care-line/archive.html", "food-line/archive.html"]
    assert _git(pages, "show", "--name-only", "--format=", "HEAD").splitlines() == [
        "care-line/archive.html",
        "food-line/archive.html",
    ]
    assert _git(remote, "rev-parse", "gh-pages") == remote_before


def test_no_op_archive_preparation_does_not_create_empty_commit(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    _source_archive(source, "food-line", "same")
    pages, _ = _pages_repo(tmp_path)
    _pages_archive(pages, "food-line", "same")
    before = _git(pages, "rev-parse", "HEAD")

    result = prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=pages, commit=True)

    assert result["status"] == "prepared_already_current"
    assert result["pages_commit_sha"] == before
    assert result["pages_changed_paths"] == []
    assert _git(pages, "rev-parse", "HEAD") == before


def test_source_failures_block_before_pages_mutation(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    pages, _ = _pages_repo(tmp_path)
    _pages_archive(pages, "food-line", "old")
    before = (pages / "food-line/archive.html").read_bytes()

    with pytest.raises(ArchivePagesPreparationError, match="source archive is missing"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=pages)
    assert (pages / "food-line/archive.html").read_bytes() == before

    _source_archive(source, "care-line", "wrong")
    with pytest.raises(ArchivePagesPreparationError, match="source archive is missing|canonical identity|dispatch identity"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=pages)


def test_pages_wrong_branch_dirty_remote_divergence_and_cname_fail(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    _source_archive(source, "food-line", "new")
    wrong_branch, _ = _pages_repo(tmp_path / "wrong", branch="main")
    with pytest.raises(ArchivePagesPreparationError, match="must be on gh-pages"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=wrong_branch)

    dirty, _ = _pages_repo(tmp_path / "dirty")
    _write(dirty / "unrelated.html", "dirty\n")
    with pytest.raises(ArchivePagesPreparationError, match="unexpected dirty paths"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=dirty)

    bad_cname, _ = _pages_repo(tmp_path / "bad-cname")
    _write(bad_cname / "CNAME", "wrong.example\n")
    _git(bad_cname, "add", "CNAME")
    _git(bad_cname, "commit", "-m", "bad cname")
    _git(bad_cname, "push", "origin", "gh-pages")
    with pytest.raises(ArchivePagesPreparationError, match="CNAME"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=bad_cname)

    behind, remote = _pages_repo(tmp_path / "behind")
    clone = tmp_path / "remote-advance"
    _git(tmp_path, "clone", "-b", "gh-pages", str(remote), str(clone))
    _git(clone, "config", "user.email", "tests@example.test")
    _git(clone, "config", "user.name", "Tests")
    _write(clone / "advance.html", "advance\n")
    _git(clone, "add", "advance.html")
    _git(clone, "commit", "-m", "advance remote")
    _git(clone, "push", "origin", "HEAD:gh-pages")
    with pytest.raises(ArchivePagesPreparationError, match="must match origin/gh-pages"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=behind)


def test_wrong_remote_unrelated_pages_diff_and_staged_path_fail_closed(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    _source_archive(source, "food-line", "new")
    wrong_remote, _ = _pages_repo(tmp_path / "wrong-remote")
    _git(wrong_remote, "remote", "set-url", "origin", str(tmp_path / "other.git"))
    with pytest.raises(ArchivePagesPreparationError, match="expected Pages repository"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=wrong_remote)

    pages, _ = _pages_repo(tmp_path / "staged")
    _pages_archive(pages, "food-line", "old")
    _write(pages / "care-line/archive.html", _archive_html("care-line", "staged"))
    _git(pages, "add", "care-line/archive.html")
    with pytest.raises(ArchivePagesPreparationError, match="unexpected dirty paths"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=pages)


def test_hash_mismatch_after_copy_fails_closed_and_restores_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _init_source(tmp_path / "source")
    _source_archive(source, "food-line", "new")
    pages, _ = _pages_repo(tmp_path)
    _pages_archive(pages, "food-line", "old")
    before = (pages / "food-line/archive.html").read_bytes()
    real_copyfile = prep.shutil.copyfile

    def corrupting_copy(source_path: Path, destination_path: Path) -> Path:
        result = real_copyfile(source_path, destination_path)
        Path(destination_path).write_text("corrupted\n", encoding="utf-8")
        return result

    monkeypatch.setattr(prep.shutil, "copyfile", corrupting_copy)

    with pytest.raises(ArchivePagesPreparationError, match="hash mismatch after copy"):
        prepare_dispatch_archive_pages(root=source, dispatch="food-line", pages_repo=pages)

    assert (pages / "food-line/archive.html").read_bytes() == before
    assert _git(pages, "status", "--short") == ""


def test_unsupported_dispatch_and_owner_cli_have_no_push_option(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    pages, _ = _pages_repo(tmp_path)
    with pytest.raises(ArchivePagesPreparationError, match="dispatch must be"):
        prepare_dispatch_archive_pages(root=source, dispatch="gaza", pages_repo=pages)

    from bluefern_dispatches.archive_pages_preparation import build_parser

    parser_actions = [action.dest for action in build_parser()._actions]
    assert "push" not in parser_actions


def test_archive_content_regression_preserves_pr320_retrospective_output(tmp_path: Path) -> None:
    source = _init_source(tmp_path / "source")
    food = _source_archive(source, "food-line", "new")
    care = _source_archive(source, "care-line", "new")
    pages, _ = _pages_repo(tmp_path)
    _pages_archive(pages, "food-line", "old")
    _pages_archive(pages, "care-line", "old")

    prepare_dispatch_archive_pages(root=source, dispatch="both", pages_repo=pages)

    assert (pages / "food-line/archive.html").read_bytes() == food.read_bytes()
    assert (pages / "care-line/archive.html").read_bytes() == care.read_bytes()
    food_text = (pages / "food-line/archive.html").read_text(encoding="utf-8")
    care_text = (pages / "care-line/archive.html").read_text(encoding="utf-8")
    assert "Retrospective recoveries" in food_text
    assert "August 2026 retrospective" in food_text
    assert "16 source-backed developments" in food_text
    assert "Retrospective recoveries" in care_text
    assert "August 2026 retrospective" in care_text
    assert "4 source-backed developments" in care_text


def test_dispatch_archive_publish_scope_family_is_exact() -> None:
    from scripts.validate_publish_scope import validate_publish_scope

    assert validate_publish_scope(
        dispatch="food-line",
        source_artifact_family="dispatch-archive",
        source_changed_paths=["output/site/food-line/archive.html"],
        pages_changed_paths=["food-line/archive.html"],
        strict=True,
    ) == []
    errors = validate_publish_scope(
        dispatch="food-line",
        source_artifact_family="dispatch-archive",
        source_changed_paths=["output/site/food-line/index.html"],
        pages_changed_paths=["care-line/archive.html"],
        strict=True,
    )
    assert any("outside the exact food-line archive scope" in error for error in errors)
