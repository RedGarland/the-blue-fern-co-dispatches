from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.generator import BASE_URL
from scripts import run_care_line_publication_runner as runner


DATE = "2026-08-09"


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


def _write_release_inputs(repo: Path) -> None:
    review_root = repo / "data" / "dispatches" / "care-line" / "review"
    proposal = {
        "schema_version": "bluefern.care_line.proposed_edition.v1",
        "edition_date": DATE,
        "headline": "Care Line limited-source update",
        "edition_summary": "Approved Care Line healthcare-access developments remain traceable and release-ready.",
        "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
        "source_adequacy_label": "Limited-source update",
        "approved_signal_ids": ["care-line-candidate-001"],
    }
    snapshot = {
        "schema_version": "bluefern.care_line.review_snapshot.v2",
        "edition_date": DATE,
        "reviewed_at": "2026-08-09T00:30:00Z",
        "review_payload": {
            "edition_date": DATE,
            "items": [
                {
                    "candidate_id": "care-line-candidate-001",
                    "review_item_id": "review-001",
                    "source_name": "Example Health News",
                    "source_title": "Care Line approved headline",
                    "source_url": "https://example.test/care-line/1",
                    "source_date": DATE,
                    "reviewed_at": "2026-08-09T00:30:00Z",
                    "approved_geography": "Example State",
                    "approved_public_claim": "Approved healthcare access claim.",
                    "bounded_public_summary": "Approved healthcare access summary.",
                    "approved_service_line": "care_access",
                    "approved_event_type": "service_line_closure",
                    "approved_access_consequence": "reduced_access",
                    "exact_supporting_passage": "Exact supporting passage.",
                    "evidence_level": "article_excerpt",
                    "notes": "note",
                }
            ],
        },
    }
    (review_root / "proposed-editions").mkdir(parents=True, exist_ok=True)
    (review_root / "signal-reviews").mkdir(parents=True, exist_ok=True)
    (review_root / "proposed-editions" / f"{DATE}.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    (review_root / "signal-reviews" / f"{DATE}.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def _write_site(repo: Path) -> None:
    site = repo / "output" / "site" / "care-line"
    (site / "editions" / DATE).mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<html>care-line index</html>", encoding="utf-8")
    (site / "archive.html").write_text("<html>care-line archive</html>", encoding="utf-8")
    (site / "rss.xml").write_text("<rss />", encoding="utf-8")
    (site / "editions" / DATE / "index.html").write_text("<html>edition</html>", encoding="utf-8")
    (site / "editions" / DATE / "edition_manifest.json").write_text(
        json.dumps(
            {
                "edition_date": DATE,
                "public_url": BASE_URL + f"/care-line/editions/{DATE}/",
                "public_rendered": True,
                "public_signal_count": 1,
                "edition_mode": "current_update",
                "validation_status": "ok",
                "public_summary": "Approved Care Line healthcare-access developments remain traceable and release-ready.",
                "source_adequacy_label": "Limited-source update",
                "source_adequacy_status": "LIMITED_SOURCE_UPDATE",
            }
        ),
        encoding="utf-8",
    )
    (repo / "output" / "site" / "index.html").write_text("<html>root homepage</html>", encoding="utf-8")


@pytest.fixture()
def release_repos(tmp_path: Path) -> tuple[Path, Path]:
    source = _init_repo(tmp_path / "source", "add/pages-repo-default")
    pages = _init_repo(tmp_path / "bluefern-dispatches-pages", "gh-pages", empty_commit=True)
    (source / "assets").mkdir(parents=True, exist_ok=True)
    asset = Path(__file__).resolve().parents[1] / "assets" / "care-line-dispatch-social.png"
    (source / "assets" / "care-line-dispatch-social.png").write_bytes(asset.read_bytes())
    (source / "assets" / "care-line-logo.png").write_bytes((Path(__file__).resolve().parents[1] / "assets" / "care-line-logo.png").read_bytes())
    (source / "assets" / "care-line-mark.png").write_bytes((Path(__file__).resolve().parents[1] / "assets" / "care-line-mark.png").read_bytes())
    (source / "assets" / "bluefern.png").write_bytes((Path(__file__).resolve().parents[1] / "assets" / "bluefern.png").read_bytes())
    _write_release_inputs(source)
    _write_site(source)
    _run_git(source, "add", "assets", "output/site/care-line/index.html", "output/site/care-line/archive.html", "output/site/care-line/rss.xml", "output/site/care-line/editions", "output/site/index.html", "data/dispatches/care-line/review")
    _run_git(source, "commit", "-m", "tracked care line release inputs")
    return source, pages


def test_check_only_reports_release_ready_without_publish(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    monkeypatch.setattr(runner, "publish_pages", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publish must not run in check-only")))
    monkeypatch.setattr(runner, "build_site", lambda *args, **kwargs: {"ok": True, "warnings": [], "errors": [], "public_url": runner.BASE_URL + f"/care-line/editions/{DATE}/", "public_rendered": True, "public_signal_count": 1, "bluesky_post_text": "Care Line Dispatch"})
    result = runner._run_publish_flow(
        repo_root=source,
        pages_repo=pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        edition_date=DATE,
        check_only=True,
        dry_run_full=False,
        publish=False,
        push=False,
        post_bluesky=False,
    )
    assert result["ok"] is True
    assert result["status"] == "check_only_ready"
    assert result["release_ready"] is True


def test_dry_run_full_keeps_repos_clean_and_uses_shared_homepage_dispatch(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    source_head = _git_output(source, "rev-parse", "HEAD")
    pages_head = _git_output(pages, "rev-parse", "HEAD")
    calls: dict[str, object] = {}

    def fake_publish_pages(*args: object, **kwargs: object) -> dict[str, object]:
        calls["only_dispatches"] = kwargs.get("only_dispatches")
        calls["shared_homepage_dispatch"] = kwargs.get("shared_homepage_dispatch")
        calls["dry_run"] = kwargs.get("dry_run")
        return {"ok": True, "warnings": [], "errors": [], "build": {"public_rendered": True, "public_signal_count": 1}}

    monkeypatch.setattr(runner, "publish_pages", fake_publish_pages)
    monkeypatch.setattr(runner, "build_site", lambda *args, **kwargs: {"ok": True, "warnings": [], "errors": [], "public_url": runner.BASE_URL + f"/care-line/editions/{DATE}/", "public_rendered": True, "public_signal_count": 1, "bluesky_post_text": "Care Line Dispatch"})
    result = runner._run_publish_flow(
        repo_root=source,
        pages_repo=pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        edition_date=DATE,
        check_only=False,
        dry_run_full=True,
        publish=True,
        push=False,
        post_bluesky=True,
    )
    assert result["ok"] is True
    assert result["status"] == "dry_run_full_success"
    assert calls["only_dispatches"] == ("care-line",)
    assert calls["shared_homepage_dispatch"] == "care-line"
    assert calls["dry_run"] is True
    assert _git_output(source, "rev-parse", "HEAD") == source_head
    assert _git_output(pages, "rev-parse", "HEAD") == pages_head


def test_publication_path_posts_bluesky_after_pages_publish(release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    source, pages = release_repos
    monkeypatch.setattr(runner, "build_site", lambda *args, **kwargs: {"ok": True, "warnings": [], "errors": [], "public_url": runner.BASE_URL + f"/care-line/editions/{DATE}/", "public_rendered": True, "public_signal_count": 1, "bluesky_post_text": "Care Line Dispatch"})
    monkeypatch.setattr(
        runner,
        "publish_pages",
        lambda *args, **kwargs: {"ok": True, "warnings": [], "errors": [], "build": {"public_rendered": True, "public_signal_count": 1}, "pushed": False},
    )
    monkeypatch.setattr(
        runner,
        "maybe_post_care_line_dispatch_to_bluesky",
        lambda **kwargs: {"status": "success", "post_uri": "at://did:plc:test/app.bsky.feed.post/123", "post_cid": "cid", "reason": None},
    )
    result = runner._run_publish_flow(
        repo_root=source,
        pages_repo=pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        edition_date=DATE,
        check_only=False,
        dry_run_full=False,
        publish=True,
        push=False,
        post_bluesky=True,
    )
    assert result["ok"] is True
    assert result["status"] == "publication_success"
    assert result["bluesky_result"]["status"] == "success"
    assert result["bluesky_result"]["post_uri"] == "at://did:plc:test/app.bsky.feed.post/123"
