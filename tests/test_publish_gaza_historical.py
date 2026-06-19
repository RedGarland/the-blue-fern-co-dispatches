import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import scripts.publish_gaza_historical as publish


def write_manual_sources(root: Path, edition_date: str, records: list[dict] | None = None) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = records
    if payload is None:
        payload = [
            {
                "source_record_id": f"gaza-src-{edition_date}-001",
                "title": "UN says durable shelter materials remain blocked from Gaza",
                "url": "https://www.aa.com.tr/en/middle-east/un-says-israel-blocks-durable-shelter-materials-from-entering-gaza/3923572",
                "publisher": "Anadolu Agency",
                "published_at": f"{edition_date}T12:00:00Z",
                "retrieved_at": "2026-05-07T00:00:00Z",
                "summary_or_snippet": "The source reports UN comments that aid agencies could not bring durable shelter materials into Gaza.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_generated_output(root: Path, edition_date: str) -> None:
    edition = root / "output" / "site" / "gaza" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    (edition / "index.html").write_text(
        """<html><body><article><h2>Stories</h2><p>Backed story.</p>
        <p><strong>Sources</strong></p>
        <a href="https://example.com/source">Example Source</a>
        </article></body></html>""",
        encoding="utf-8",
    )
    (edition / "edition_manifest.json").write_text(
        json.dumps({"source_count": 1, "story_count": 1}, indent=2),
        encoding="utf-8",
    )
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "src-001", "source_id": "src-001", "url": "https://example.com/source"}], indent=2),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-001",
                    "included_in_public_summary": True,
                    "source_ids": ["src-001"],
                    "source_record_ids": ["src-001"],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    gaza = root / "output" / "site" / "gaza"
    gaza.mkdir(parents=True, exist_ok=True)
    (gaza / "archive.html").write_text(f"<a>{edition_date}</a>", encoding="utf-8")
    (gaza / "rss.xml").write_text(f"<item>{edition_date}</item>", encoding="utf-8")


def write_pages_output(pages_repo: Path, edition_date: str) -> None:
    (pages_repo / "gaza" / "editions" / edition_date).mkdir(parents=True, exist_ok=True)
    (pages_repo / "gaza" / "archive.html").write_text(edition_date, encoding="utf-8")
    (pages_repo / "gaza" / "rss.xml").write_text(edition_date, encoding="utf-8")
    (pages_repo / "gaza" / "editions" / edition_date / "index.html").write_text("edition", encoding="utf-8")


@pytest.fixture()
def isolated_root(monkeypatch):
    root = Path(__file__).resolve().parents[1] / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    root.mkdir(parents=True)
    pages_repo = root / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    monkeypatch.setattr(publish, "ROOT", root)
    monkeypatch.setattr(publish, "DEFAULT_PAGES_REPO", pages_repo)
    try:
        yield root, pages_repo
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def completed(args: list[str], returncode: int = 0, payload: dict | None = None, stdout: str | None = None):
    text = stdout if stdout is not None else json.dumps(payload or {"ok": True, "errors": []})
    return subprocess.CompletedProcess(args, returncode, stdout=text, stderr="")


def test_missing_manual_sources_fails_clearly(isolated_root, capsys):
    root, pages_repo = isolated_root

    code = publish.main(["--date", "2026-04-30", "--pages-repo", str(pages_repo)])

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert "Create the source file first" in summary["errors"][0]
    assert str(root / "data" / "dispatches" / "gaza" / "sources" / "2026-04-30" / "manual_sources.json") in summary["errors"][0]


def test_empty_manual_sources_fails_clearly(isolated_root, capsys):
    _, pages_repo = isolated_root
    write_manual_sources(publish.ROOT, "2026-04-30", [])

    code = publish.main(["--date", "2026-04-30", "--pages-repo", str(pages_repo)])

    assert code == 1
    summary = json.loads(capsys.readouterr().out)
    assert "manual_sources.json contains no valid source records" in summary["errors"]


def test_valid_manual_sources_runs_generation_and_pages_dry_run(isolated_root, monkeypatch, capsys):
    root, pages_repo = isolated_root
    write_manual_sources(root, "2026-04-30")
    calls: list[list[str]] = []

    def fake_run(args, cwd=publish.ROOT):
        calls.append(args)
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-04-30")
            return completed(args, payload={"ok": True})
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        if "publish_github_pages.py" in command and "--dry-run" in args:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True})
        if "publish_github_pages.py" in command:
            write_pages_output(pages_repo, "2026-04-30")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234"})
        raise AssertionError(args)

    monkeypatch.setattr(publish, "run_command", fake_run)

    code = publish.main(["--date", "2026-04-30", "--pages-repo", str(pages_repo), "--skip-tests"])

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["generated"] is True
    assert summary["archive_updated"] is True
    assert summary["rss_updated"] is True
    assert summary["source_count"] == 1
    assert summary["public_story_count"] == 1
    assert summary["pages_repo_updated"] is True
    assert summary["pages_commit_sha"] == "abc1234"
    assert summary["target_pages_branch"] == "gh-pages"
    assert any("run_gaza_dispatch.py" in " ".join(call) and "--historical" in call for call in calls)
    assert any("publish_github_pages.py" in " ".join(call) and "--dry-run" in call for call in calls)
    assert any("publish_github_pages.py" in " ".join(call) and "--pages-branch" in call and "gh-pages" in call for call in calls)
    assert any("publish_github_pages.py" in " ".join(call) and "--expect-date" in call and "2026-04-30" in call for call in calls)
    assert any("publish_github_pages.py" in " ".join(call) and "--expect-dispatch" in call and "gaza" in call for call in calls)
    assert any("publish_github_pages.py" in " ".join(call) and "--only-dispatch" in call and "gaza" in call for call in calls)


def test_generated_edition_requires_visible_source_links(isolated_root):
    root, _ = isolated_root
    write_generated_output(root, "2026-04-30")
    (root / "output" / "site" / "gaza" / "editions" / "2026-04-30" / "index.html").write_text(
        "<html><body><p>No links here.</p></body></html>",
        encoding="utf-8",
    )

    result = publish.validate_generated_output("2026-04-30")

    assert "rendered HTML contains no visible public source links" in result["errors"]
    assert "rendered HTML does not include visible source labeling" in result["errors"]


def test_public_story_without_source_ids_fails(isolated_root):
    root, _ = isolated_root
    write_generated_output(root, "2026-04-30")
    curation = root / "output" / "site" / "gaza" / "editions" / "2026-04-30" / "curation_manifest.json"
    curation.write_text(json.dumps([{"story_id": "story-001", "included_in_public_summary": True}], indent=2), encoding="utf-8")

    result = publish.validate_generated_output("2026-04-30")

    assert "story-001 is public but has no source IDs" in result["errors"]


def test_default_behavior_does_not_push(isolated_root, monkeypatch, capsys):
    root, pages_repo = isolated_root
    write_manual_sources(root, "2026-04-30")

    def fake_run(args, cwd=publish.ROOT):
        command = " ".join(args)
        assert "git push" not in command
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-04-30")
            return completed(args)
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        if "publish_github_pages.py" in command and "--dry-run" in args:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True})
        if "publish_github_pages.py" in command:
            write_pages_output(pages_repo, "2026-04-30")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "def5678"})
        return completed(args)

    monkeypatch.setattr(publish, "run_command", fake_run)

    code = publish.main(["--date", "2026-04-30", "--pages-repo", str(pages_repo), "--skip-tests"])

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["pushed"] is False
    assert summary["would_push"] is False
    assert "git push origin gh-pages" in summary["manual_push_command"]


def test_push_requires_explicit_flag(isolated_root, monkeypatch, capsys):
    root, pages_repo = isolated_root
    write_manual_sources(root, "2026-04-30")
    git_calls: list[list[str]] = []

    def fake_run(args, cwd=publish.ROOT):
        command = " ".join(args)
        if args[:2] == ["git", "status"] or args[:3] == ["git", "push", "origin"]:
            git_calls.append(args)
            return completed(args, stdout="ok")
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-04-30")
            return completed(args)
        if "publish_github_pages.py" in command and "--dry-run" in args:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True})
        if "publish_github_pages.py" in command:
            write_pages_output(pages_repo, "2026-04-30")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234"})
        return completed(args, stdout="1 passed")

    monkeypatch.setattr(publish, "run_command", fake_run)

    code = publish.main(["--date", "2026-04-30", "--pages-repo", str(pages_repo), "--skip-tests", "--push"])

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["pushed"] is True
    assert ["git", "status"] in git_calls
    assert ["git", "push", "origin", "gh-pages"] in git_calls


def test_no_old_gaza_project_path_is_referenced():
    script_text = Path(publish.__file__).read_text(encoding="utf-8")
    forbidden = [
        "Gaza " + "Dispatch V4",
        "old " + "rendered HTML",
        "old " + "Markdown",
    ]
    assert not any(value in script_text for value in forbidden)


def test_no_paid_detail_files_in_public_site(isolated_root):
    root, _ = isolated_root
    (root / "output" / "site" / "detail").mkdir(parents=True)
    (root / "output" / "site" / "detail" / "private.json").write_text("{}", encoding="utf-8")

    errors = publish.validate_public_site_has_no_detail_files()

    assert errors
    assert "paid/detail file is in public output" in errors[0]
