from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches import generator
from bluefern_dispatches.generator import BASE_URL
from scripts import run_care_line_publication_runner as runner


DATE = "2026-08-19"
LATEST_DATE = "2026-08-20"


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


def _write_valid_care_line_edition(public_root: Path, edition_date: str, title: str) -> Path:
    edition = public_root / "care-line" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text(f"<html><body><article><h3>{title}</h3></article></body></html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "care-line",
                "edition_date": edition_date,
                "public_url": BASE_URL + f"/care-line/editions/{edition_date}/",
                "public_rendered": True,
                "source_count": 1,
                "story_count": 1,
                "claim_count": 1,
                "qualified_public_claim_count": 1,
                "lead_signal_count": 1,
                "edition_mode": "current_update",
                "public_archive_title": title,
                "skip_reason": "",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": f"care-{edition_date}", "title": title}], indent=2),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": f"care-story-{edition_date}"}], indent=2),
        encoding="utf-8",
    )
    (edition / "source_table.html").write_text(f"<html><body>sources {edition_date}</body></html>", encoding="utf-8")
    (edition / "claim_ledger.html").write_text(f"<html><body>claims {edition_date}</body></html>", encoding="utf-8")
    return edition


def _write_other_public_release(public_root: Path, slug: str, title: str) -> None:
    edition = public_root / slug / "editions" / LATEST_DATE
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text(f"<html><body><article><h3>{title}</h3></article></body></html>", encoding="utf-8")
    (edition / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": slug, "edition_date": LATEST_DATE, "source_count": 1, "public_archive_title": title}),
        encoding="utf-8",
    )
    (public_root / slug / "archive.html").write_text(
        f'<a href="editions/{LATEST_DATE}/">{title}</a>', encoding="utf-8"
    )


def _active_card(slug: str, product_name: str, title: str) -> str:
    return (
        '<article class="dispatch-card dispatch-card--featured"><p class="status">Active</p>'
        '<p class="latest-label">Latest public development</p>'
        f'<h3 class="latest-headline"><a href="/{slug}/editions/{LATEST_DATE}/">{title}</a></h3>'
        f'<p class="date-line">{product_name} &middot; August 20, 2026</p>'
        f'<h2>{"The Care Line Dispatch" if slug == "care-line" else product_name}</h2>'
        f'<a class="button" href="/{slug}/editions/{LATEST_DATE}/">Read latest</a></article>'
    )


def _write_shared_surfaces(pages: Path) -> None:
    care_title = "Aug. 20 Care Line release"
    pages.joinpath("index.html").write_text(
        '<html><body><section class="section-block"><div class="section-heading">'
        '<p class="eyebrow">The current edition desk</p><h2>Latest published developments</h2></div>'
        '<div class="edition-grid"><article class="edition-card edition-card--care-line">'
        '<p class="topic-badge topic-badge--care-line">CARE LINE</p>'
        f'<h3><a href="/care-line/editions/{LATEST_DATE}/">{care_title}</a></h3>'
        '<p class="edition-source">Care Line &middot; August 20, 2026</p>'
        '<p class="edition-provenance">Based on public source reporting</p>'
        '<p class="edition-meta">1 public source</p></article></div></section>'
        + _active_card("care-line", "Care Line", care_title)
        + '<footer><a href="/methodology/">How we work</a> &middot; <a href="/about/">About this project</a></footer>'
        '</body></html>',
        encoding="utf-8",
    )
    directory = pages / "dispatches" / "index.html"
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text(
        '<html><body>'
        + _active_card("gaza", "Dispatches From Gaza", "Aug. 20 Gaza release")
        + _active_card("food-line", "Food Line Dispatch", "Aug. 20 Food Line release")
        + _active_card("care-line", "Care Line", care_title)
        + '<footer><a href="/methodology/">How we work</a> &middot; <a href="/about/">About this project</a></footer>'
        '</body></html>',
        encoding="utf-8",
    )


def _write_care_line_history(public_root: Path, dates: list[str]) -> None:
    links = "".join(f'<a href="editions/{edition_date}/">{edition_date}</a>' for edition_date in dates)
    items = "".join(
        f'<item><link>{BASE_URL}/care-line/editions/{edition_date}/</link></item>' for edition_date in dates
    )
    care_root = public_root / "care-line"
    care_root.mkdir(parents=True, exist_ok=True)
    (care_root / "index.html").write_text(f"<html><body>{links}</body></html>", encoding="utf-8")
    (care_root / "archive.html").write_text(f"<html><body>{links}</body></html>", encoding="utf-8")
    (care_root / "rss.xml").write_text(f"<rss><channel>{items}</channel></rss>", encoding="utf-8")


def _successful_build() -> dict[str, object]:
    return {
        "ok": True,
        "warnings": [],
        "errors": [],
        "public_urls": [BASE_URL + f"/care-line/editions/{DATE}/"],
        "public_url": BASE_URL + f"/care-line/editions/{DATE}/",
        "public_rendered": True,
        "public_signal_count": 1,
        "bluesky_post_text": "Care Line Dispatch",
        "backfilled_public_editions": [],
        "gaza_editions_discovered": [],
        "gaza_editions_backfilled": [],
        "gaza_editions_skipped": [],
        "gaza_archive_entries_written": [],
    }


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


@pytest.fixture()
def production_release_repos(release_repos: tuple[Path, Path]) -> tuple[Path, Path]:
    source, pages = release_repos
    site_root = source / "output" / "site"
    _write_valid_care_line_edition(site_root, DATE, "Santa Paula Hospital")
    latest_source = _write_valid_care_line_edition(site_root, LATEST_DATE, "Aug. 20 Care Line release")
    latest_pages = pages / "care-line" / "editions" / LATEST_DATE
    shutil.copytree(latest_source, latest_pages)
    _write_care_line_history(site_root, [LATEST_DATE, DATE])
    _write_care_line_history(pages, [LATEST_DATE])
    _write_other_public_release(pages, "gaza", "Aug. 20 Gaza release")
    _write_other_public_release(pages, "food-line", "Aug. 20 Food Line release")
    (pages / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    _write_shared_surfaces(pages)
    _run_git(source, "add", "output/site")
    _run_git(source, "commit", "-m", "generated Care Line backfill candidate")
    _run_git(pages, "add", "-A")
    _run_git(pages, "commit", "-m", "published Aug. 20 inventory")
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
    assert calls["dry_run"] is False
    assert _git_output(source, "rev-parse", "HEAD") == source_head
    assert _git_output(pages, "rev-parse", "HEAD") == pages_head
    assert _git_output(source, "status", "--short") == ""
    assert _git_output(pages, "status", "--short") == ""


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


def test_isolated_publication_does_not_generate_into_long_lived_source(
    release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pages = release_repos
    source_head = _git_output(source, "rev-parse", "HEAD")
    publication_roots: list[Path] = []

    monkeypatch.setattr(runner, "build_site", lambda *args, **kwargs: _successful_build())

    def fake_publish(publication_root: Path, *args: object, **kwargs: object) -> dict[str, object]:
        publication_roots.append(publication_root)
        return {
            "ok": True,
            "warnings": [],
            "errors": [],
            "build": {"public_rendered": True, "public_signal_count": 1},
            "pushed": True,
        }

    monkeypatch.setattr(runner, "publish_pages", fake_publish)
    result = runner._run_publish_flow(
        repo_root=source,
        pages_repo=pages,
        source_branch="add/pages-repo-default",
        pages_branch="gh-pages",
        edition_date=DATE,
        check_only=False,
        dry_run_full=False,
        publish=True,
        push=True,
        post_bluesky=False,
        isolated_source=True,
    )

    assert result["ok"] is True
    assert result["isolated_source"] is True
    assert len(publication_roots) == 1
    assert publication_roots[0].resolve() != source.resolve()
    assert _git_output(source, "rev-parse", "HEAD") == source_head
    assert _git_output(source, "status", "--short") == ""


def test_sanctioned_publish_adds_new_care_line_backfill_without_replacing_newer_release(
    production_release_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, pages = production_release_repos
    latest_dir = pages / "care-line" / "editions" / LATEST_DATE
    latest_before = {
        path.relative_to(latest_dir).as_posix(): path.read_bytes()
        for path in latest_dir.rglob("*")
        if path.is_file()
    }
    archive_before = generator._care_line_public_surface_date_sets(pages)
    pages_parent = _git_output(pages, "rev-parse", "HEAD")
    post_copy_calls: list[bool] = []
    original_post_copy = generator.validate_pages_repo_after_copy

    def validate_after_copy(*args: object, **kwargs: object) -> list[str]:
        post_copy_calls.append((pages / "care-line" / "editions" / DATE / "index.html").exists())
        return original_post_copy(*args, **kwargs)

    monkeypatch.setattr(runner, "build_site", lambda *args, **kwargs: _successful_build())
    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: _successful_build())
    monkeypatch.setattr(generator, "validate_pages_repo_after_copy", validate_after_copy)

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
        post_bluesky=False,
    )

    expected_dir = pages / "care-line" / "editions" / DATE
    latest_after = {
        path.relative_to(latest_dir).as_posix(): path.read_bytes()
        for path in latest_dir.rglob("*")
        if path.is_file()
    }
    history_after = generator._care_line_public_surface_date_sets(pages)
    changed_paths = _git_output(pages, "diff", "--name-only", pages_parent, "HEAD").splitlines()

    assert result["ok"] is True
    assert result["status"] == "publication_success"
    assert result["pages_publish_copied"] is True
    assert post_copy_calls == [True]
    assert expected_dir.is_dir()
    for name in ("index.html", "edition_manifest.json", "sources_manifest.json", "curation_manifest.json", "source_table.html", "claim_ledger.html"):
        assert (expected_dir / name).read_bytes() == (source / "output" / "site" / "care-line" / "editions" / DATE / name).read_bytes()
    assert latest_after == latest_before
    assert history_after["care-line/archive.html"] == {LATEST_DATE, DATE}
    assert history_after["care-line/rss.xml"] == {LATEST_DATE, DATE}
    assert archive_before["care-line/archive.html"] == {LATEST_DATE}
    assert archive_before["care-line/rss.xml"] == {LATEST_DATE}
    assert f"/care-line/editions/{LATEST_DATE}/" in (pages / "index.html").read_text(encoding="utf-8")
    assert f"/care-line/editions/{LATEST_DATE}/" in (pages / "dispatches" / "index.html").read_text(encoding="utf-8")
    assert not any(path.startswith("gaza/") or path.startswith("food-line/") for path in changed_paths)
    assert all("audio" not in path for path in changed_paths)


def test_expected_generated_care_line_edition_must_exist(
    production_release_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, pages = production_release_repos
    shutil.rmtree(source / "output" / "site" / "care-line" / "editions" / DATE)
    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: _successful_build())

    result = generator.publish_pages(
        source,
        pages,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        expect_date=DATE,
        expect_dispatches=("care-line",),
        only_dispatches=("care-line",),
        shared_homepage_dispatch="care-line",
    )

    assert result["ok"] is False
    assert any(f"edition missing: {DATE}" in error for error in result["errors"])
    assert not (pages / "care-line" / "editions" / DATE).exists()


def test_post_copy_validation_fails_if_copy_skips_expected_care_line_edition(
    production_release_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, pages = production_release_repos
    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: _successful_build())
    monkeypatch.setattr(generator, "copy_public_site_to_pages", lambda *args, **kwargs: ([], []))

    result = generator.publish_pages(
        source,
        pages,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        expect_date=DATE,
        expect_dispatches=("care-line",),
        only_dispatches=("care-line",),
        shared_homepage_dispatch="care-line",
    )

    assert result["ok"] is False
    assert f"expected Care Line edition missing: {DATE}" in result["errors"]


def test_wrong_expected_care_line_date_fails_before_copy(
    production_release_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, pages = production_release_repos
    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: _successful_build())

    result = generator.publish_pages(
        source,
        pages,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        expect_date="2026-08-18",
        expect_dispatches=("care-line",),
        only_dispatches=("care-line",),
        shared_homepage_dispatch="care-line",
    )

    assert result["ok"] is False
    assert any("edition missing: 2026-08-18" in error for error in result["errors"])
    assert not (pages / "care-line" / "editions" / "2026-08-18").exists()


def test_care_line_publish_rejects_archive_and_rss_history_shrink(
    production_release_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, pages = production_release_repos
    _write_care_line_history(source / "output" / "site", [DATE])
    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: _successful_build())

    result = generator.publish_pages(
        source,
        pages,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        expect_date=DATE,
        expect_dispatches=("care-line",),
        only_dispatches=("care-line",),
        shared_homepage_dispatch="care-line",
    )

    assert result["ok"] is False
    assert any("care-line public history shrink detected for care-line/archive.html" in error for error in result["errors"])
    assert any("care-line public history shrink detected for care-line/rss.xml" in error for error in result["errors"])
    assert generator._care_line_public_surface_date_sets(pages)["care-line/archive.html"] == {LATEST_DATE}


def test_care_line_publish_rejects_unrelated_pages_change_created_during_copy(
    production_release_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, pages = production_release_repos
    original_copy = generator.copy_public_site_to_pages

    def copy_with_unrelated_change(*args: object, **kwargs: object) -> tuple[list[str], list[str]]:
        copied, skipped = original_copy(*args, **kwargs)
        unrelated = pages / "gaza" / "unexpected.html"
        unrelated.write_text("unrelated", encoding="utf-8")
        return copied + [str(unrelated)], skipped

    monkeypatch.setattr(generator, "build_site", lambda *args, **kwargs: _successful_build())
    monkeypatch.setattr(generator, "copy_public_site_to_pages", copy_with_unrelated_change)

    result = generator.publish_pages(
        source,
        pages,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        expect_date=DATE,
        expect_dispatches=("care-line",),
        only_dispatches=("care-line",),
        shared_homepage_dispatch="care-line",
    )

    assert result["ok"] is False
    assert any("pages_publish_unrelated_changes_detected" in error and "gaza/unexpected.html" in error for error in result["errors"])


def test_care_line_build_reconciliation_preserves_newer_pages_edition_bytes(
    production_release_repos: tuple[Path, Path],
) -> None:
    source, pages = production_release_repos
    site_root = source / "output" / "site"
    shutil.rmtree(site_root / "care-line" / "editions" / LATEST_DATE)
    wrote: list[str] = []

    report = generator.reconcile_care_line_public_editions_from_pages(
        site_root,
        pages,
        dry_run=False,
        wrote=wrote,
    )

    source_latest = site_root / "care-line" / "editions" / LATEST_DATE
    pages_latest = pages / "care-line" / "editions" / LATEST_DATE
    assert report["backfilled"] == [
        {
            "edition_date": LATEST_DATE,
            "source": "pages_repo",
            "source_path": str(pages_latest),
            "target_path": str(source_latest),
        }
    ]
    assert {
        path.relative_to(source_latest).as_posix(): path.read_bytes()
        for path in source_latest.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(pages_latest).as_posix(): path.read_bytes()
        for path in pages_latest.rglob("*")
        if path.is_file()
    }
