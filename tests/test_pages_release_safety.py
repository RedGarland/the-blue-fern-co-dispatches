from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import bluefern_dispatches.pages_release_safety as pages_release_safety
from bluefern_dispatches.food_line_approved_proposal import build_release_manifest, write_json_deterministic


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")
    return result.stdout.strip()


def _init_repo(root: Path, branch: str, *, empty_commit: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "tests@example.test")
    _run_git(root, "config", "user.name", "Tests")
    if empty_commit:
        _run_git(root, "commit", "--allow-empty", "-m", "initial")
    else:
        (root / "README.md").write_text("repo", encoding="utf-8")
        _run_git(root, "add", "README.md")
        _run_git(root, "commit", "-m", "initial")
    _run_git(root, "checkout", "-b", branch)
    return root


def _write_food_line_site(source_root: Path, dates: list[str], *, approved_dates: set[str] | None = None) -> None:
    approved_dates = approved_dates or set()
    site_root = source_root / "output" / "site" / "food-line"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Food Line index</html>", encoding="utf-8")
    (site_root / "archive.html").write_text("<html>Archive</html>", encoding="utf-8")
    for date_text in dates:
        edition = site_root / "editions" / date_text
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<html>{date_text}</html>", encoding="utf-8")
        manifest = {"edition_date": date_text}
        if date_text in approved_dates:
            manifest.update(
                {
                    "generation_mode": "approved_current_review_proposal",
                    "publication_status": "unpublished",
                    "publication_approval": False,
                    "publication_eligible": False,
                    "pages_status": "not_synced",
                    "public_release_status": "not_published",
                    "pages_release_status": "not_synced",
                    "public_url": f"https://dispatches.thebluefernco.com/food-line/editions/{date_text}/",
                }
            )
        (edition / "edition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (edition / "sources_manifest.json").write_text(json.dumps([{"source_record_id": "src-1"}]), encoding="utf-8")
        (edition / "curation_manifest.json").write_text(json.dumps([{"story_id": "story-1"}]), encoding="utf-8")
        (edition / "source_table.html").write_text("<table><tr><td>1</td></tr></table>", encoding="utf-8")
        (edition / "claim_ledger.html").write_text("<html>Claims</html>", encoding="utf-8")


def _write_care_line_site(source_root: Path, dates: list[str], *, approved_dates: set[str] | None = None) -> None:
    approved_dates = approved_dates or set()
    site_root = source_root / "output" / "site" / "care-line"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html>Care Line index</html>", encoding="utf-8")
    (site_root / "archive.html").write_text("<html>Archive</html>", encoding="utf-8")
    (site_root / "rss.xml").write_text("<rss></rss>", encoding="utf-8")
    for date_text in dates:
        edition = site_root / "editions" / date_text
        edition.mkdir(parents=True, exist_ok=True)
        (edition / "index.html").write_text(f"<html>{date_text}</html>", encoding="utf-8")
        manifest = {"edition_date": date_text}
        if date_text in approved_dates:
            manifest.update(
                {
                    "dispatch_slug": "care-line",
                    "generation_mode": "approved_current_review_proposal",
                    "publication_status": "unpublished",
                    "pages_status": "not_synced",
                    "public_release_status": "not_published",
                    "pages_release_status": "not_synced",
                    "public_url": f"https://dispatches.thebluefernco.com/care-line/editions/{date_text}/",
                }
            )
        (edition / "edition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (edition / "sources_manifest.json").write_text(json.dumps([{"source_record_id": "src-1"}]), encoding="utf-8")
        (edition / "curation_manifest.json").write_text(json.dumps([{"story_id": "story-1"}]), encoding="utf-8")
        (edition / "source_table.html").write_text("<table><tr><td>1</td></tr></table>", encoding="utf-8")
        (edition / "claim_ledger.html").write_text("<html>Claims</html>", encoding="utf-8")


def _commit_repo(repo: Path, message: str = "update") -> None:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", message)


def _release_manifest(source: Path, pages: Path, date_text: str) -> Path:
    site = source / "output/site/food-line"
    edition = site / "editions" / date_text
    source_paths = [site / "index.html", site / "archive.html", *sorted(path for path in edition.iterdir() if path.is_file())]
    payload = build_release_manifest(
        root=source,
        pages_root=pages,
        edition_date=date_text,
        source_commit=_git_output(source, "rev-parse", "HEAD"),
        source_paths=source_paths,
    )
    path = source / "data/dispatches/food-line/review/releases" / f"{date_text}.json"
    write_json_deterministic(path, payload)
    return path


@pytest.fixture()
def release_repos(tmp_path: Path) -> tuple[Path, Path]:
    source = _init_repo(tmp_path / "source", "add/pages-repo-default")
    pages = _init_repo(tmp_path / "bluefern-dispatches-pages", "gh-pages", empty_commit=True)
    return source, pages


def test_dry_run_reports_planned_paths_for_food_line_dates(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19", "2026-06-20"])
    _commit_repo(source, "food line site")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19", "2026-06-20"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is True
    assert report["commit_status"] == "dry-run"
    assert report["push_status"] == "dry-run"
    assert report["copied_paths"] == [
        "food-line/index.html",
        "food-line/archive.html",
        "food-line/editions/2026-06-19/claim_ledger.html",
        "food-line/editions/2026-06-19/curation_manifest.json",
        "food-line/editions/2026-06-19/edition_manifest.json",
        "food-line/editions/2026-06-19/index.html",
        "food-line/editions/2026-06-19/source_table.html",
        "food-line/editions/2026-06-19/sources_manifest.json",
        "food-line/editions/2026-06-20/claim_ledger.html",
        "food-line/editions/2026-06-20/curation_manifest.json",
        "food-line/editions/2026-06-20/edition_manifest.json",
        "food-line/editions/2026-06-20/index.html",
        "food-line/editions/2026-06-20/source_table.html",
        "food-line/editions/2026-06-20/sources_manifest.json",
    ]
    assert report["planned_pages_paths"] == report["copied_paths"]
    assert not (pages / "food-line").exists()


def test_allowed_path_validation_rejects_unexpected_pages_diff(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")

    monkeypatch.setattr(pages_release_safety, "_pages_changed_paths", lambda _repo: ["food-line/index.html", "notes.txt"])

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )

    assert report["ok"] is False
    assert any("unexpected Pages repo changes outside the allowed food-line scope" in error for error in report["errors"])
    assert "notes.txt" in report["errors"][0]


def test_missing_source_artifact_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    (source / "output" / "site" / "food-line" / "archive.html").unlink()
    _commit_repo(source, "food line site without archive")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is False
    assert any("missing required source artifact" in error for error in report["errors"])


def test_wrong_source_branch_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    _run_git(source, "checkout", "-b", "unexpected-branch")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is False
    assert any("source branch mismatch" in error for error in report["errors"])


def test_wrong_pages_branch_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    _run_git(pages, "checkout", "-b", "not-gh-pages")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
    )

    assert report["ok"] is False
    assert any("pages repo branch mismatch" in error for error in report["errors"])


def test_pages_dirty_state_rejection(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    (pages / "dirty.txt").write_text("dirty", encoding="utf-8")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=False,
    )

    assert report["ok"] is False
    assert any("pages repo must be clean before sync" in error for error in report["errors"])


def test_commit_requires_clean_allowed_scope_and_cleans_repo(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        commit=True,
    )

    assert report["ok"] is True
    assert report["commit_status"] == "committed"
    assert report["push_status"] == "skipped"
    assert report["commit_hash"]
    assert pages_release_safety._repo_clean(pages)
    assert (pages / "food-line" / "editions" / "2026-06-19" / "index.html").exists()
    assert _git_output(pages, "branch", "--show-current") == "gh-pages"


def test_push_requires_commit(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        push=True,
    )

    assert report["ok"] is False
    assert "--push requires --commit." in report["errors"]


def test_live_check_url_construction_and_only_mode(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    seen_urls: list[str] = []

    def fake_status(url: str, timeout: int) -> int:
        seen_urls.append(url)
        return 200

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        live_check=True,
        live_check_only=True,
        cache_bust="abc 123",
        fetch_status=fake_status,
    )

    assert report["ok"] is True
    assert len(seen_urls) == 3
    assert seen_urls[0] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/?cache_bust=abc+123"
    assert seen_urls[1] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/sources_manifest.json?cache_bust=abc+123"
    assert seen_urls[2] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-19/curation_manifest.json?cache_bust=abc+123"


def test_date_validation_and_multiple_date_handling() -> None:
    assert pages_release_safety._parse_dates(["2026-06-19", "2026-06-20", "2026-06-19"]) == ("2026-06-19", "2026-06-20")
    assert pages_release_safety._parse_dates(["2026-06-19,2026-06-20"]) == ("2026-06-19", "2026-06-20")
    with pytest.raises(ValueError):
        pages_release_safety._parse_dates(["2026-06-31"])


def test_script_does_not_require_audio_map_podcast_assets_support(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    extra_root = source / "output" / "site" / "food-line"
    (extra_root / "audio").mkdir(parents=True, exist_ok=True)
    (extra_root / "audio" / "clip.mp3").write_bytes(b"audio")
    (extra_root / "map").mkdir(parents=True, exist_ok=True)
    (extra_root / "map" / "index.html").write_text("map", encoding="utf-8")
    (extra_root / "podcast.xml").write_text("<rss />", encoding="utf-8")
    (source / "output" / "site" / "assets").mkdir(parents=True, exist_ok=True)
    (source / "output" / "site" / "assets" / "site.css").write_text("body{}", encoding="utf-8")

    plan = pages_release_safety._build_copy_plan(source, pages, "food-line", ["2026-06-19"])
    planned = [path.relative_to(source).as_posix() for path in plan.source_paths]

    assert "output/site/food-line/audio/clip.mp3" not in planned
    assert "output/site/food-line/map/index.html" not in planned
    assert "output/site/food-line/podcast.xml" not in planned
    assert all("output/site/assets" not in path for path in planned)


def test_release_manifest_dry_run_ignores_unrelated_source_dirt_and_writes_nothing(
    release_repos: tuple[Path, Path],
) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    manifest = _release_manifest(source, pages, "2026-06-19")
    (source / "unrelated-dirty.txt").write_text("not in release", encoding="utf-8")
    before_status = _git_output(pages, "status", "--porcelain=v1", "--untracked-files=all")
    before_files = sorted(path.relative_to(pages).as_posix() for path in pages.rglob("*") if path.is_file() and ".git" not in path.parts)

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is True
    assert len(report["additions"]) == 8
    assert report["modifications"] == []
    assert report["deletions"] == []
    assert _git_output(pages, "status", "--porcelain=v1", "--untracked-files=all") == before_status
    assert sorted(path.relative_to(pages).as_posix() for path in pages.rglob("*") if path.is_file() and ".git" not in path.parts) == before_files


def test_release_manifest_requires_clean_pages_even_for_dry_run(release_repos: tuple[Path, Path]) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    manifest = _release_manifest(source, pages, "2026-06-19")
    (pages / "dirty.txt").write_text("dirty", encoding="utf-8")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-06-19"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
        dry_run=True,
        release_manifest=manifest,
    )

    assert report["ok"] is False
    assert any("pages repo must be clean before sync" in error for error in report["errors"])


def test_sync_marks_approved_proposal_pages_manifest_as_live_and_keeps_source_pre_release(
    release_repos: tuple[Path, Path]
) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-07-28"], approved_dates={"2026-07-28"})
    _commit_repo(source, "food line approved proposal")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-07-28"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )

    assert report["ok"] is True
    source_manifest = json.loads((source / "output" / "site" / "food-line" / "editions" / "2026-07-28" / "edition_manifest.json").read_text(encoding="utf-8"))
    pages_manifest = json.loads((pages / "food-line" / "editions" / "2026-07-28" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert source_manifest["publication_status"] == "unpublished"
    assert source_manifest["pages_status"] == "not_synced"
    assert source_manifest["public_release_status"] == "not_published"
    assert source_manifest["pages_release_status"] == "not_synced"
    assert pages_manifest["publication_status"] == "unpublished"
    assert pages_manifest["pages_status"] == "not_synced"
    assert pages_manifest["public_release_status"] == "published"
    assert pages_manifest["pages_release_status"] == "synced"


def test_sync_rerun_is_idempotent_for_live_approved_proposal_manifest(
    release_repos: tuple[Path, Path]
) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-07-28"], approved_dates={"2026-07-28"})
    _commit_repo(source, "food line approved proposal")

    first = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-07-28"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )
    assert first["ok"] is True

    second = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=["2026-07-28"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )
    assert second["ok"] is True
    assert second["commit_status"] == "no-changes"


def test_sync_marks_july_28_and_july_31_pages_manifests_live(
    release_repos: tuple[Path, Path]
) -> None:
    source, pages = release_repos
    dates = ["2026-07-28", "2026-07-31"]
    _write_food_line_site(source, dates, approved_dates=set(dates))
    _commit_repo(source, "food line july releases")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="food-line",
        dates=dates,
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )

    assert report["ok"] is True
    for date_text in dates:
        manifest = json.loads((pages / "food-line" / "editions" / date_text / "edition_manifest.json").read_text(encoding="utf-8"))
        assert manifest["publication_status"] == "unpublished"
        assert manifest["pages_status"] == "not_synced"
        assert manifest["public_release_status"] == "published"
        assert manifest["pages_release_status"] == "synced"


def test_sync_marks_care_line_pages_manifest_live_and_keeps_source_pre_release(
    release_repos: tuple[Path, Path]
) -> None:
    source, pages = release_repos
    _write_care_line_site(source, ["2026-08-05"], approved_dates={"2026-08-05"})
    _commit_repo(source, "care line approved release")

    report = pages_release_safety.sync_pages_from_source(
        dispatch="care-line",
        dates=["2026-08-05"],
        require_source_branch="add/pages-repo-default",
        source_repo=source,
        pages_repo=pages,
    )

    assert report["ok"] is True
    source_manifest = json.loads((source / "output" / "site" / "care-line" / "editions" / "2026-08-05" / "edition_manifest.json").read_text(encoding="utf-8"))
    pages_manifest = json.loads((pages / "care-line" / "editions" / "2026-08-05" / "edition_manifest.json").read_text(encoding="utf-8"))
    assert source_manifest["publication_status"] == "unpublished"
    assert source_manifest["pages_status"] == "not_synced"
    assert source_manifest["public_release_status"] == "not_published"
    assert source_manifest["pages_release_status"] == "not_synced"
    assert pages_manifest["publication_status"] == "unpublished"
    assert pages_manifest["pages_status"] == "not_synced"
    assert pages_manifest["public_release_status"] == "published"
    assert pages_manifest["pages_release_status"] == "synced"


def test_git_native_repository_validation_accepts_linked_worktree_and_rejects_invalid_shapes(
    release_repos: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source, _pages = release_repos
    linked = tmp_path / "linked-source"
    _run_git(source, "worktree", "add", "--detach", str(linked), "HEAD")
    try:
        assert pages_release_safety._repo_is_git_repo(source)
        assert pages_release_safety._repo_is_git_repo(linked)

        fake = tmp_path / "fake"
        fake.mkdir()
        (fake / ".git").write_text("not a gitdir", encoding="utf-8")
        assert not pages_release_safety._repo_is_git_repo(fake)

        nonrepo = tmp_path / "nonrepo"
        nonrepo.mkdir()
        assert not pages_release_safety._repo_is_git_repo(nonrepo)

        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True, text=True)
        assert not pages_release_safety._repo_is_git_repo(bare)
        assert not pages_release_safety._repo_is_git_repo(source / "output")
    finally:
        _run_git(source, "worktree", "remove", "--force", str(linked))


def test_detached_source_requires_exact_verified_remote_branch_head(
    release_repos: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source, pages = release_repos
    _write_food_line_site(source, ["2026-06-19"])
    _commit_repo(source, "food line site")
    linked = tmp_path / "linked-source"
    _run_git(source, "worktree", "add", "--detach", str(linked), "HEAD")
    try:
        manifest = _release_manifest(linked, pages, "2026-06-19")
        blocked = pages_release_safety.sync_pages_from_source(
            dispatch="food-line",
            dates=["2026-06-19"],
            require_source_branch="add/pages-repo-default",
            source_repo=linked,
            pages_repo=pages,
            dry_run=True,
            release_manifest=manifest,
            allow_detached_source_at_required_branch_head=True,
        )
        assert blocked["ok"] is False
        assert any("unable to resolve origin/add/pages-repo-default" in error for error in blocked["errors"])

        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True)
        _run_git(source, "remote", "add", "origin", str(origin))
        _run_git(source, "push", "origin", "add/pages-repo-default")
        manifest = _release_manifest(linked, pages, "2026-06-19")
        accepted = pages_release_safety.sync_pages_from_source(
            dispatch="food-line",
            dates=["2026-06-19"],
            require_source_branch="add/pages-repo-default",
            source_repo=linked,
            pages_repo=pages,
            dry_run=True,
            release_manifest=manifest,
            allow_detached_source_at_required_branch_head=True,
        )
        assert accepted["ok"] is True
        assert accepted["source_branch_verification"] == "detached_head_verified_against_required_remote_branch"
        assert accepted["additions"]
    finally:
        _run_git(source, "worktree", "remove", "--force", str(linked))
