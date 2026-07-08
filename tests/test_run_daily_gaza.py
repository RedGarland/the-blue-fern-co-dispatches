import argparse
import json
import ssl
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import scripts.publish_gaza_historical as historical
import scripts.run_gaza_daily_operator as operator
import scripts.run_daily_gaza as daily


def make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "daily"
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    pages = root / "bluefern-dispatches-pages"
    pages.mkdir()
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    return root


def write_manual_sources(root: Path, edition_date: str, text: str | None = None) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        payload = [
            {
                "source_record_id": f"gaza-src-{edition_date}-001",
                "title": "UN says durable shelter materials remain blocked from Gaza",
                "url": "https://valid.test/gaza-source",
                "publisher": "Reuters",
                "published_at": f"{edition_date}T12:00:00Z",
                "retrieved_at": f"{edition_date}T00:00:00Z",
                "summary_or_snippet": "A source-backed Gaza update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ]
        text = json.dumps(payload, indent=2)
    path.write_text(text, encoding="utf-8")
    return path


def make_manual_source_record(edition_date: str, index: int = 1) -> dict[str, str]:
    return {
        "source_record_id": f"gaza-src-{edition_date}-{index:03d}",
        "title": f"Gaza source {index}",
        "url": f"https://valid.test/gaza-source-{index}",
        "publisher": "Reuters",
        "published_at": f"{edition_date}T12:00:00Z",
        "retrieved_at": f"{edition_date}T00:00:00Z",
        "summary_or_snippet": "A source-backed Gaza update.",
        "source_type": "news",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "reported-public-source",
        "traceability_note": f"Traceable to Reuters via a direct publisher URL dated {edition_date}T12:00:00Z; title, publisher, URL, and published_at are preserved in the record.",
        "attribution_mode": "reported_public_source",
        "claim_status": "reported_public_source",
    }


def write_generated_output(root: Path, edition_date: str, source_links: bool = True) -> None:
    edition = root / "output" / "site" / "gaza" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    link = '<p><strong>Sources</strong></p><a href="https://valid.test/gaza-source">Reuters source</a>' if source_links else "<p>No links.</p>"
    (edition / "index.html").write_text(f"<html><body><article>{link}</article></body></html>", encoding="utf-8")
    (edition / "sources_manifest.json").write_text(
        json.dumps([{"source_record_id": "gaza-src", "source_id": "gaza-src", "url": "https://valid.test/gaza-source"}]),
        encoding="utf-8",
    )
    (edition / "curation_manifest.json").write_text(
        json.dumps([{"story_id": "story", "included_in_public_summary": True, "source_ids": ["gaza-src"]}]),
        encoding="utf-8",
    )
    (edition / "edition_manifest.json").write_text(json.dumps({"source_count": 1}), encoding="utf-8")
    gaza = root / "output" / "site" / "gaza"
    (gaza / "archive.html").write_text(edition_date, encoding="utf-8")
    (gaza / "rss.xml").write_text(edition_date, encoding="utf-8")


def write_pages_output(root: Path, edition_date: str) -> None:
    pages = root / "bluefern-dispatches-pages"
    (pages / "gaza" / "editions" / edition_date).mkdir(parents=True, exist_ok=True)
    (pages / "gaza" / "archive.html").write_text(edition_date, encoding="utf-8")
    (pages / "gaza" / "rss.xml").write_text(edition_date, encoding="utf-8")
    (pages / "gaza" / "editions" / edition_date / "index.html").write_text("edition", encoding="utf-8")


def completed(args, returncode=0, payload=None, stdout=None):
    text = stdout if stdout is not None else json.dumps(payload or {"ok": True, "errors": []})
    return subprocess.CompletedProcess(args, returncode, text, "")


def live_response(body: str, status: int = 200):
    class _Response:
        def __init__(self, payload: str, response_status: int):
            self._payload = payload.encode("utf-8")
            self.status = response_status

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    return _Response(body, status)


@pytest.fixture()
def isolated(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    root = make_root(repo)
    monkeypatch.setattr(daily, "ROOT", root)
    monkeypatch.setattr(daily, "DEFAULT_PAGES_REPO", root / "bluefern-dispatches-pages")
    monkeypatch.setattr(historical, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_date_only_command_works_with_fixture_sources(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    calls = []

    def fake_run(args, cwd=daily.ROOT):
        calls.append(args)
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "warnings": [],
                    "source_adequacy_status": "limited_source_update",
                    "publisher_count": 1,
                    "publishers": ["Reuters"],
                    "source_adequacy_warnings": [
                        "This is a limited-source update generated from 4 saved source records from 1 publisher(s). It should be read as a partial update, not a full daily briefing."
                    ],
                },
            )
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages"})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        return completed(args)

    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["ok"] is True
    assert summary["source_mode"] == "both"
    assert summary["validation_profile"] == "gaza_daily"
    assert summary["skipped_unrelated_tests"] is True
    assert summary["pages_repo_updated"] is True
    assert summary["source_adequacy_status"] == "limited_source_update"
    assert summary["publisher_count"] == 1
    assert summary["publishers"] == ["Reuters"]
    assert any("limited-source update generated" in warning for warning in summary["warnings"])
    assert summary["pages_branch"] == "gh-pages"
    assert summary["pushed"] is False
    assert "git push origin gh-pages" in summary["manual_push_command"]
    assert (root / "logs" / "gaza-daily-2026-05-07.log").exists()
    assert (root / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json").exists()
    publish_calls = [call for call in calls if "publish_github_pages.py" in " ".join(call)]
    assert publish_calls
    assert all("--expect-date" in call and "2026-05-07" in call for call in publish_calls)
    assert all("--expect-dispatch" in call and "gaza" in call for call in publish_calls)
    assert all("--only-dispatch" in call and "gaza" in call for call in publish_calls)


def test_date_only_command_forwards_post_edition_date_override(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            assert "--allow-post-edition-date-sources" in args
            write_generated_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "warnings": [],
                    "source_adequacy_status": "limited_source_update",
                    "publisher_count": 1,
                    "publishers": ["Reuters"],
                    "source_adequacy_warnings": [],
                },
            )
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages"})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        return completed(args)

    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main([
        "--date",
        "2026-05-07",
        "--skip-tests",
        "--allow-post-edition-date-sources",
        "--pages-repo",
        str(root / "bluefern-dispatches-pages"),
    ])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["ok"] is True


def test_daily_summary_includes_source_window_and_later_same_day_update_metadata(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(
        root,
        "2026-05-07",
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-2026-05-07-001",
                    "title": "Gaza aid access update",
                    "url": "https://valid.test/gaza-source-1",
                    "publisher": "Reuters",
                    "published_at": "2026-05-07T08:15:00Z",
                    "retrieved_at": "2026-05-07T08:20:00Z",
                    "summary_or_snippet": "A source-backed Gaza update.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-src-2026-05-07-002",
                    "title": "Gaza hospital access update",
                    "url": "https://valid.test/gaza-source-2",
                    "publisher": "BBC News",
                    "published_at": "2026-05-07T12:30:00Z",
                    "retrieved_at": "2026-05-07T15:45:00Z",
                    "summary_or_snippet": "A later same-day source-backed Gaza update.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                },
            ],
            indent=2,
        ),
    )

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "warnings": [], "errors": []})
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages"})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        return completed(args)

    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    run_manifest = json.loads((root / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json").read_text(encoding="utf-8"))
    assert code == 0
    assert summary["scheduled_run_local_time"] == "2026-05-07T06:00:00-07:00"
    assert summary["actual_run_local_time"]
    assert summary["source_window_start_utc"] == "2026-05-07T08:15:00Z"
    assert summary["source_window_end_utc"] == "2026-05-07T12:30:00Z"
    assert summary["first_source_retrieved_at"] == "2026-05-07T08:20:00Z"
    assert summary["last_source_retrieved_at"] == "2026-05-07T15:45:00Z"
    assert summary["contains_later_same_day_update"] is True
    assert summary["later_same_day_update_count"] == 1
    assert summary["later_same_day_update_batch_count"] == 1
    assert summary["later_same_day_update_source_count"] == 1
    assert summary["contains_post_edition_date_update"] is False
    assert summary["post_edition_date_update_count"] == 0
    assert summary["post_edition_date_update_batch_count"] == 0
    assert summary["post_edition_date_update_source_count"] == 0
    assert len(summary["retrieval_batches"]) == 2
    assert run_manifest["contains_later_same_day_update"] is True


def test_missing_source_file_triggers_auto_collection_when_both(isolated, monkeypatch, capsys):
    root = isolated

    def fake_collect(root_arg, edition_date, max_sources, min_sources, output_filename, **kwargs):
        path = write_manual_sources(root_arg, edition_date)
        return {"ok": True, "source_file": str(path), "source_count": 1, "sources": json.loads(path.read_text(encoding="utf-8")), "warnings": [], "errors": []}

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", lambda args, cwd=daily.ROOT: completed(args, payload={"ok": False, "errors": ["stop after source test"]}, returncode=1))

    code = daily.main(["--date", "2026-05-07", "--source-mode", "both", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["source_count"] == 1
    assert summary["validation_profile"] == "gaza_daily"
    assert Path(summary["source_file"]).exists()


def test_no_sources_collected_fails_safely(isolated, monkeypatch, capsys):
    root = isolated
    monkeypatch.setattr(daily, "collect_gaza_sources", lambda *args, **kwargs: {"ok": False, "sources": [], "warnings": [], "errors": ["source count 0 is below minimum 1"]})

    code = daily.main(["--date", "2026-05-07", "--source-mode", "auto", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["generated"] is False
    assert summary["pages_repo_updated"] is False


def test_invalid_json_fails_clearly(isolated, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07", "{bad json")

    code = daily.main(["--date", "2026-05-07", "--source-mode", "manual", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "Expecting property name" in summary["errors"][0]


def test_invalid_manual_falls_back_to_auto_when_both(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07", "{bad json")

    def fake_collect(root_arg, edition_date, max_sources, min_sources, output_filename, **kwargs):
        path = write_manual_sources(root_arg, edition_date)
        return {"ok": True, "source_file": str(path), "source_count": 1, "sources": json.loads(path.read_text(encoding="utf-8")), "warnings": [], "errors": [], "failed_source_ids": []}

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", lambda args, cwd=daily.ROOT: completed(args, payload={"ok": False, "errors": ["stop after source test"]}, returncode=1))

    code = daily.main(["--date", "2026-05-07", "--source-mode", "both", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["source_count"] == 1
    warning = summary["warnings"][0]
    assert "manual_sources.json at" in warning
    assert "was present but invalid and was skipped" in warning
    assert "Run: python scripts/add_gaza_manual_source.py --date 2026-05-07 --validate-only" in warning


def test_invalid_manual_source_fields_are_reported_in_both_mode(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    write_manual_sources(
        root,
        "2026-05-07",
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-2026-05-07-001",
                    "title": "",
                    "url": "https://valid.test/gaza-source",
                    "publisher": "",
                    "published_at": "2026-05-07T12:00:00Z",
                    "retrieved_at": "2026-05-07T12:00:00Z",
                    "summary_or_snippet": "A source-backed Gaza update.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                }
            ]
        ),
    )

    def fake_collect(root_arg, edition_date, max_sources, min_sources, output_filename, **kwargs):
        _ = root_arg, edition_date, max_sources, min_sources, output_filename, kwargs
        path = write_manual_sources(root, "2026-05-07")
        return {"ok": True, "source_file": str(path), "source_count": 1, "sources": json.loads(path.read_text(encoding="utf-8")), "warnings": [], "errors": [], "failed_source_ids": []}

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", lambda args, cwd=daily.ROOT: completed(args, payload={"ok": False, "errors": ["stop after source test"]}, returncode=1) if "run_gaza_dispatch.py" in " ".join(args) else completed(args))

    code = daily.main(["--date", "2026-05-07", "--source-mode", "both", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    warning = summary["warnings"][0]
    assert str(manual_path) in warning
    assert "source record 1 missing required fields: publisher, title" in warning
    assert "Run: python scripts/add_gaza_manual_source.py --date 2026-05-07 --validate-only" in warning


def test_collect_or_load_sources_both_mode_writes_manual_sources_before_generation(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    records = [make_manual_source_record("2026-05-07", 1), make_manual_source_record("2026-05-07", 2)]
    calls = []

    def fake_collect(*args, **kwargs):
        _ = args, kwargs
        return {"ok": True, "source_file": str(manual_path), "source_count": len(records), "sources": records, "warnings": [], "errors": [], "failed_source_ids": []}

    def fake_run(args, cwd=daily.ROOT):
        calls.append(args)
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            assert manual_path.exists()
            persisted = json.loads(manual_path.read_text(encoding="utf-8"))
            assert len(persisted) == len(records)
            write_generated_output(root, "2026-05-07")
            (root / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "sources_manifest.json").write_text(
                json.dumps(
                    [
                        {"source_record_id": records[0]["source_record_id"], "source_id": records[0]["source_record_id"], "url": records[0]["url"]},
                        {"source_record_id": records[1]["source_record_id"], "source_id": records[1]["source_record_id"], "url": records[1]["url"]},
                    ]
                ),
                encoding="utf-8",
            )
            (root / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "edition_manifest.json").write_text(
                json.dumps({"source_count": len(records)}),
                encoding="utf-8",
            )
            (root / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "curation_manifest.json").write_text(
                json.dumps(
                    [
                        {
                            "story_id": "story",
                            "included_in_public_summary": True,
                            "source_ids": [records[0]["source_record_id"]],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            return completed(
                args,
                payload={
                    "ok": True,
                    "warnings": [],
                    "errors": [],
                    "source_adequacy_status": "limited_source_update",
                    "publisher_count": 1,
                    "publishers": ["Reuters"],
                    "source_adequacy_warnings": [],
                },
            )
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "copied": True,
                    "committed": True,
                    "commit_sha": "abc1234",
                    "target_pages_branch": "gh-pages",
                    "committed_branch": "gh-pages",
                },
            )
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        return completed(args)

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["ok"] is True
    assert summary["source_count"] == len(records)
    assert summary["source_file"] == str(manual_path)
    assert manual_path.exists()
    assert len(json.loads(manual_path.read_text(encoding="utf-8"))) == len(records)
    assert any("run_gaza_dispatch.py" in " ".join(call) for call in calls)


def test_collect_or_load_sources_both_mode_restores_preexisting_manual_sources_on_success(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    records = [make_manual_source_record("2026-05-07", 1), make_manual_source_record("2026-05-07", 2)]
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    original_text = manual_path.read_text(encoding="utf-8")

    def fake_collect(*args, **kwargs):
        _ = args, kwargs
        return {"ok": True, "source_file": str(manual_path), "source_count": len(records), "sources": records, "warnings": [], "errors": [], "failed_source_ids": []}

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
            (root / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "sources_manifest.json").write_text(
                json.dumps(
                    [
                        {"source_record_id": records[0]["source_record_id"], "source_id": records[0]["source_record_id"], "url": records[0]["url"]},
                        {"source_record_id": records[1]["source_record_id"], "source_id": records[1]["source_record_id"], "url": records[1]["url"]},
                    ]
                ),
                encoding="utf-8",
            )
            (root / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "edition_manifest.json").write_text(
                json.dumps({"source_count": len(records)}),
                encoding="utf-8",
            )
            (root / "output" / "site" / "gaza" / "editions" / "2026-05-07" / "curation_manifest.json").write_text(
                json.dumps(
                    [
                        {
                            "story_id": "story",
                            "included_in_public_summary": True,
                            "source_ids": [records[0]["source_record_id"]],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            return completed(
                args,
                payload={
                    "ok": True,
                    "warnings": [],
                    "errors": [],
                    "source_adequacy_status": "limited_source_update",
                    "publisher_count": 1,
                    "publishers": ["Reuters"],
                    "source_adequacy_warnings": [],
                },
            )
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "copied": True,
                    "committed": True,
                    "commit_sha": "abc1234",
                    "target_pages_branch": "gh-pages",
                    "committed_branch": "gh-pages",
                },
            )
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        return completed(args)

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["ok"] is True
    assert manual_path.read_text(encoding="utf-8") == original_text


def test_collect_or_load_sources_both_mode_fails_when_manual_sources_are_not_persisted(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    records = [make_manual_source_record("2026-05-07", 1), make_manual_source_record("2026-05-07", 2)]
    calls = []

    def fake_collect(*args, **kwargs):
        _ = args, kwargs
        return {"ok": True, "source_file": str(manual_path), "source_count": len(records), "sources": records, "warnings": [], "errors": [], "failed_source_ids": []}

    def fake_write(*args, **kwargs):
        _ = args, kwargs
        return manual_path

    def fake_run(args, cwd=daily.ROOT):
        calls.append(args)
        return completed(args)

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "write_source_records", fake_write)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["generated"] is False
    assert "failed to persist manual_sources.json" in summary["errors"][0]
    assert not manual_path.exists()
    assert all("run_gaza_dispatch.py" not in " ".join(call) for call in calls)


def test_collect_or_load_sources_both_mode_reports_persisted_file_count(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    in_memory_records = [make_manual_source_record("2026-05-07", 1), make_manual_source_record("2026-05-07", 2)]
    persisted_record = make_manual_source_record("2026-05-07", 1)

    def fake_collect(*args, **kwargs):
        _ = args, kwargs
        return {"ok": True, "source_file": str(manual_path), "source_count": len(in_memory_records), "sources": in_memory_records, "warnings": [], "errors": [], "failed_source_ids": []}

    def fake_write(*args, **kwargs):
        _ = args, kwargs
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        manual_path.write_text(json.dumps([persisted_record], indent=2), encoding="utf-8")
        return manual_path

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "write_source_records", fake_write)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["generated"] is False
    assert "manual_sources.json contains 1 records" in summary["errors"][0]


def test_collect_or_load_sources_both_mode_preserves_governance_fields(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    original_record = {
        "source_record_id": "gaza-src-2026-05-07-001",
        "title": "Gaza aid access update",
        "url": "https://valid.test/gaza-source-1",
        "publisher": "Reuters",
        "published_at": "2026-05-07T08:15:00Z",
        "retrieved_at": "2026-05-07T08:20:00Z",
        "summary_or_snippet": "A source-backed Gaza update.",
        "source_type": "news",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "reported-public-source",
        "traceability_note": "Traceable to Reuters via a direct publisher URL dated 2026-05-07T08:15:00Z; title, publisher, URL, and published_at are preserved in the record.",
        "attribution_mode": "reported_public_source",
        "claim_status": "reported_public_source",
    }
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(json.dumps([original_record], indent=2), encoding="utf-8")
    auto_record = dict(original_record)
    auto_record["retrieved_at"] = "2026-05-07T12:00:00Z"
    auto_record.pop("traceability_note")
    auto_record.pop("attribution_mode")
    auto_record.pop("claim_status")

    def fake_collect(*args, **kwargs):
        _ = args, kwargs
        return {
            "ok": True,
            "source_file": str(manual_path),
            "source_count": 1,
            "sources": [auto_record],
            "warnings": [],
            "errors": [],
            "failed_source_ids": [],
        }

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            persisted = json.loads(manual_path.read_text(encoding="utf-8"))
            assert persisted[0]["source_record_id"] == original_record["source_record_id"]
            assert persisted[0]["traceability_note"] == original_record["traceability_note"]
            assert persisted[0]["attribution_mode"] == original_record["attribution_mode"]
            assert persisted[0]["claim_status"] == original_record["claim_status"]
            write_generated_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "warnings": [],
                    "errors": [],
                    "source_adequacy_status": "limited_source_update",
                    "publisher_count": 1,
                    "publishers": ["Reuters"],
                    "source_adequacy_warnings": [],
                },
            )
        if "publish_github_pages.py" in command and "--dry-run" in args:
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "paid_detail_excluded_from_public": True,
                    "target_pages_branch": "gh-pages",
                },
            )
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    persisted = json.loads(manual_path.read_text(encoding="utf-8"))
    assert code == 0
    assert summary["ok"] is True
    assert persisted[0]["traceability_note"] == original_record["traceability_note"]
    assert persisted[0]["attribution_mode"] == original_record["attribution_mode"]
    assert persisted[0]["claim_status"] == original_record["claim_status"]
    assert persisted[0]["retrieved_at"] == original_record["retrieved_at"]


def test_collect_or_load_sources_both_mode_restores_manual_sources_on_failure(isolated, monkeypatch, capsys):
    root = isolated
    manual_path = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-07" / "manual_sources.json"
    original_text = json.dumps(
        [
            {
                "source_record_id": "gaza-src-2026-05-07-001",
                "title": "Gaza aid access update",
                "url": "https://valid.test/gaza-source-1",
                "publisher": "Reuters",
                "published_at": "2026-05-07T08:15:00Z",
                "retrieved_at": "2026-05-07T08:20:00Z",
                "summary_or_snippet": "A source-backed Gaza update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
                "traceability_note": "Traceable to Reuters via a direct publisher URL dated 2026-05-07T08:15:00Z; title, publisher, URL, and published_at are preserved in the record.",
                "attribution_mode": "reported_public_source",
                "claim_status": "reported_public_source",
            }
        ],
        indent=2,
    )
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(original_text, encoding="utf-8")
    degraded_record = json.loads(original_text)[0]
    degraded_record.pop("traceability_note")
    degraded_record.pop("attribution_mode")
    degraded_record.pop("claim_status")

    def fake_collect(*args, **kwargs):
        _ = args, kwargs
        return {
            "ok": True,
            "source_file": str(manual_path),
            "source_count": 1,
            "sources": [degraded_record],
            "warnings": [],
            "errors": [],
            "failed_source_ids": [],
        }

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            return completed(
                args,
                payload={"ok": False, "warnings": [], "errors": ["generation boom"]},
                returncode=1,
            )
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["ok"] is False
    assert manual_path.read_text(encoding="utf-8") == original_text


def test_generated_page_requires_visible_source_links(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    monkeypatch.setattr(
        daily,
        "run_command",
        lambda args, cwd=daily.ROOT: (write_generated_output(root, "2026-05-07", source_links=False) or completed(args))
        if "run_gaza_dispatch.py" in " ".join(args)
        else completed(args),
    )

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "rendered HTML contains no visible public source links" in summary["errors"]


def test_push_is_opt_in(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    calls = []

    def fake_run(args, cwd=daily.ROOT):
        calls.append(args)
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages", "committed": True})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return completed(args, stdout="")
        if args[:3] == ["git", "fetch", "origin"]:
            return completed(args, stdout="fetched")
        if args[:2] == ["git", "status"]:
            return completed(args, stdout="On branch gh-pages")
        if args[:3] == ["git", "push", "origin"]:
            return completed(args, stdout="pushed")
        if args[:2] == ["git", "rev-parse"] and args[-1] == "origin/gh-pages":
            return completed(args, stdout="remote-sha")
        if args[:2] == ["git", "ls-tree"]:
            return completed(args, stdout=args[-1])
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "run_command", fake_run)
    monkeypatch.setattr(
        daily.urllib.request,
        "urlopen",
        lambda request, timeout=30, context=None: live_response(
            "<html><body>Dispatches From Gaza Today's Read Source Mix 2026-05-07</body></html>"
            if "/gaza/editions/" in request.full_url
            else "<html><body>2026-05-07</body></html>"
        ),
    )

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--push", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert ["git", "push", "origin", "gh-pages"] in calls
    assert summary["pushed"] is True
    assert summary["pages_push_ok"] is True
    assert summary["remote_tree_verify_ok"] is True
    assert summary["live_http_ok"] is True
    assert summary["live_archive_ok"] is True
    assert summary["overall_ok"] is True


def test_gaza_daily_scoped_publish_stays_inside_gaza_and_reaches_push_stage(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-06-19")
    dry_run_payloads: list[dict[str, object]] = []
    calls: list[list[str]] = []

    def fake_run(args, cwd=daily.ROOT):
        calls.append(args)
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-06-19")
            return completed(args, payload={"ok": True})
        if "publish_github_pages.py" in command and "--dry-run" in args:
            payload = {
                "ok": True,
                "errors": [],
                "paid_detail_excluded_from_public": True,
                "target_pages_branch": "gh-pages",
                "files_that_would_be_copied": [
                    "C:\\repo\\gaza\\index.html",
                    "C:\\repo\\gaza\\archive.html",
                    "C:\\repo\\gaza\\rss.xml",
                    "C:\\repo\\gaza\\editions\\2026-06-19\\index.html",
                ],
            }
            dry_run_payloads.append(payload)
            return completed(args, payload=payload)
        if "publish_github_pages.py" in command:
            write_pages_output(root, "2026-06-19")
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "copied": True,
                    "committed": True,
                    "commit_sha": "abc1234",
                    "target_pages_branch": "gh-pages",
                    "committed_branch": "gh-pages",
                },
            )
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return completed(args, stdout="")
        if args[:3] == ["git", "fetch", "origin"]:
            return completed(args, stdout="fetched")
        if args[:2] == ["git", "status"]:
            return completed(args, stdout="On branch gh-pages")
        if args[:3] == ["git", "push", "origin"]:
            return completed(args, stdout="pushed")
        if args[:2] == ["git", "rev-parse"] and args[-1] == "origin/gh-pages":
            return completed(args, stdout="remote-sha")
        if args[:2] == ["git", "ls-tree"]:
            return completed(args, stdout=args[-1])
        return completed(args, stdout="ok")

    def fake_urlopen(request, timeout=30, context=None):
        if "/gaza/editions/2026-06-19/" in request.full_url:
            return live_response("<html><body>offline</body></html>", status=503)
        return live_response("<html><body>2026-06-19</body></html>", status=200)

    monkeypatch.setattr(daily, "run_command", fake_run)
    monkeypatch.setattr(daily.urllib.request, "urlopen", fake_urlopen)

    code = daily.main(["--date", "2026-06-19", "--skip-tests", "--push", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert dry_run_payloads
    dry_run_files = dry_run_payloads[0]["files_that_would_be_copied"]
    assert any("gaza/index.html" in str(path).replace("\\", "/") for path in dry_run_files)
    assert any("gaza/archive.html" in str(path).replace("\\", "/") for path in dry_run_files)
    assert any("gaza/rss.xml" in str(path).replace("\\", "/") for path in dry_run_files)
    assert any("gaza/editions/2026-06-19/index.html" in str(path).replace("\\", "/") for path in dry_run_files)
    assert not any("/care-line/" in str(path).replace("\\", "/") for path in dry_run_files)
    assert not any("/cascadia/" in str(path).replace("\\", "/") for path in dry_run_files)
    assert not any("/food-line/" in str(path).replace("\\", "/") for path in dry_run_files)
    assert not any("/american-pressure/" in str(path).replace("\\", "/") for path in dry_run_files)
    assert not any(str(path).endswith("/index.html") and "/gaza/" not in str(path).replace("\\", "/") for path in dry_run_files)
    assert any("--only-dispatch" in call and "gaza" in call for call in calls if "publish_github_pages.py" in " ".join(call))
    assert summary["local_pages_copy_ok"] is True
    assert summary["pages_commit_ok"] is True
    assert summary["pages_push_ok"] is True
    assert summary["remote_tree_verify_ok"] is True
    assert summary["live_http_ok"] is False
    assert summary["live_archive_ok"] is True
    assert summary["overall_ok"] is False


def test_push_rejection_appends_manual_repair_guidance(isolated, monkeypatch):
    root = isolated
    pages_repo = root / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True, exist_ok=True)
    calls = []

    def fake_run(args, cwd=daily.ROOT):
        calls.append(args)
        if args[:2] == ["git", "status"]:
            return completed(args, stdout="On branch gh-pages")
        if args[:3] == ["git", "push", "origin"]:
            return completed(args, returncode=1, stdout="rejected non-fast-forward")
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "run_command", fake_run)

    ok, messages, detail = daily.push_pages_repo(pages_repo, "gh-pages")

    assert ok is False
    assert messages
    assert calls[-1] == ["git", "push", "origin", "gh-pages"]
    assert "pages_repo_not_synced_with_origin" in detail


def test_email_report_sends_on_success(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    sent = []

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            write_pages_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages"})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "run_command", fake_run)
    monkeypatch.setattr(daily, "send_email", lambda subject, body, date_str: sent.append((subject, body, date_str)))

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["ok"] is True
    assert summary["pipeline_ok"] is True
    assert summary["email_requested"] is True
    assert summary["email_ok"] is True
    assert summary["notification_error"] is None
    assert summary["overall_ok"] is True
    assert sent[0][0] == "[Blue Fern Dispatches] Gaza daily succeeded - 2026-05-07"
    assert "source_count: 1" in sent[0][1]
    assert "run manifest path:" in sent[0][1]


def test_email_report_failure_after_success_is_nonfatal(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    tls_exc = ssl.SSLCertVerificationError("certificate verify failed")

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "warnings": [],
                    "source_adequacy_status": "limited_source_update",
                    "publisher_count": 1,
                    "publishers": ["Reuters"],
                    "source_adequacy_warnings": [],
                },
            )
        if "publish_github_pages.py" in command and "--dry-run" in args:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if "publish_github_pages.py" in command:
            write_pages_output(root, "2026-05-07")
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "copied": True,
                    "committed": True,
                    "commit_sha": "abc1234",
                    "target_pages_branch": "gh-pages",
                    "committed_branch": "gh-pages",
                },
            )
        if args[:2] == ["git", "status"]:
            return completed(args, stdout="## gh-pages...origin/gh-pages")
        if args[:3] == ["git", "push", "origin"]:
            return completed(args, stdout="pushed")
        return completed(args)

    monkeypatch.setattr(daily, "run_command", fake_run)
    monkeypatch.setattr(daily, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(tls_exc))
    monkeypatch.setattr(daily, "verify_remote_pages_tree", lambda *args, **kwargs: {"ok": True, "remote_commit_sha": "abc1234", "errors": []})
    monkeypatch.setattr(daily, "verify_live_public_urls", lambda *args, **kwargs: {"ok": True, "live_http_ok": True, "live_archive_ok": True})
    monkeypatch.setattr(
        daily,
        "maybe_post_gaza_dispatch_to_bluesky",
        lambda **kwargs: {"status": "success", "post_uri": "at://did:example/app.bsky.feed.post/1", "reason": None},
    )

    code = daily.main(
        [
            "--date",
            "2026-05-07",
            "--skip-tests",
            "--push",
            "--post-bluesky",
            "--email-report",
            "--pages-repo",
            str(root / "bluefern-dispatches-pages"),
        ]
    )

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["ok"] is True
    assert summary["overall_ok"] is True
    assert summary["email_ok"] is False
    assert summary["notification_error"]
    assert any("Email report failed:" in warning for warning in summary["warnings"])


def test_email_report_sends_on_failure_with_warnings_and_errors(isolated, monkeypatch, capsys):
    root = isolated
    sent = []
    monkeypatch.setattr(
        daily,
        "collect_gaza_sources",
        lambda *args, **kwargs: {
            "ok": False,
            "sources": [],
            "source_count": 0,
            "warnings": ["bad-feed: non-XML feed response"],
            "errors": ["source count 0 is below minimum 1"],
            "failed_source_ids": [{"source_id": "bad-feed", "reason": "non-XML feed response"}],
        },
    )
    monkeypatch.setattr(daily, "send_email", lambda subject, body, date_str: sent.append((subject, body, date_str)))

    code = daily.main(["--date", "2026-05-07", "--source-mode", "auto", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["source_count"] == 0
    assert summary["generated"] is False
    assert sent[0][0] == "[Blue Fern Dispatches] Gaza daily failed: source issue - 2026-05-07"
    assert "- bad-feed: non-XML feed response" in sent[0][1]
    assert "- source count 0 is below minimum 1" in sent[0][1]
    assert "manual push command:" in sent[0][1]


def test_email_report_missing_smtp_config_returns_2(isolated, monkeypatch, capsys):
    root = isolated
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(
        daily,
        "collect_gaza_sources",
        lambda *args, **kwargs: {"ok": False, "sources": [], "source_count": 0, "warnings": [], "errors": ["source count 0 is below minimum 1"], "failed_source_ids": []},
    )

    code = daily.main(["--date", "2026-05-07", "--source-mode", "auto", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "source count 0 is below minimum 1" in summary["errors"]
    assert any("Missing required env vars: SMTP_HOST, EMAIL_TO" in error for error in summary["errors"])


def test_source_collection_exception_is_reported_with_email(isolated, monkeypatch, capsys):
    root = isolated
    sent = []
    monkeypatch.setattr(daily, "collect_gaza_sources", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("Gaza sources config does not exist")))
    monkeypatch.setattr(daily, "send_email", lambda subject, body, date_str: sent.append((subject, body, date_str)))

    code = daily.main(["--date", "2026-05-07", "--source-mode", "auto", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["generated"] is False
    assert "Gaza sources config does not exist" in summary["errors"]
    assert sent


def test_email_report_failure_does_not_log_smtp_password(isolated, monkeypatch, capsys):
    root = isolated
    secret = "secret-app-password"
    monkeypatch.setenv("SMTP_PASSWORD", secret)
    write_manual_sources(root, "2026-05-07")
    monkeypatch.setattr(daily, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("network down")))
    monkeypatch.setattr(
        daily,
        "run_command",
        lambda args, cwd=daily.ROOT: (write_generated_output(root, "2026-05-07") or completed(args))
        if "run_gaza_dispatch.py" in " ".join(args)
        else completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}),
    )

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    captured = capsys.readouterr().out
    summary = json.loads(captured)
    assert code == 2
    assert summary["pipeline_ok"] is True
    assert summary["email_ok"] is False
    assert summary["overall_ok"] is False
    assert isinstance(summary["notification_error"], str) and summary["notification_error"]
    assert secret not in captured
    assert secret not in (root / "logs" / "gaza-daily-2026-05-07.log").read_text(encoding="utf-8")


def test_pages_publish_failure_is_not_classified_as_source_issue(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    sent = []

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command and "--dry-run" not in args:
            return completed(
                args,
                returncode=1,
                payload={"ok": False, "errors": ["fatal: Unable to create .git/index.lock: Permission denied"], "warnings": []},
            )
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        if "pytest" in command:
            return completed(args, stdout="1 passed")
        return completed(args)

    monkeypatch.setattr(daily, "run_command", fake_run)
    monkeypatch.setattr(daily, "send_email", lambda subject, body, date_str: sent.append((subject, body, date_str)))

    code = daily.main(["--date", "2026-05-07", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["source_count"] == 1
    assert sent[0][0] == "[Blue Fern Dispatches] Gaza daily failed - 2026-05-07"
    assert "Pages publish failed: fatal: Unable to create .git/index.lock: Permission denied" in summary["errors"]


def test_generation_warnings_are_carried_into_daily_summary(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
            return completed(args, payload={"ok": True, "warnings": ["source diversity warning: rendered_story_count>=4_with_low_unique_rendered_publishers(<3)"]})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert any("source diversity warning" in warning for warning in summary["warnings"])


def test_email_report_exit_2_when_email_fails_after_pipeline_failure(isolated, monkeypatch, capsys):
    root = isolated
    monkeypatch.setattr(
        daily,
        "collect_gaza_sources",
        lambda *args, **kwargs: {"ok": False, "sources": [], "source_count": 0, "warnings": [], "errors": ["source count 0 is below minimum 1"], "failed_source_ids": []},
    )
    monkeypatch.setattr(daily, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("smtp down")))

    code = daily.main(["--date", "2026-05-07", "--source-mode", "auto", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 2
    assert summary["pipeline_ok"] is False
    assert summary["email_ok"] is False
    assert summary["overall_ok"] is False
    assert isinstance(summary["notification_error"], str) and summary["notification_error"]
    assert "source count 0 is below minimum 1" in summary["errors"]
    assert "smtp down" in summary["notification_error"]


def test_pipeline_success_email_failure_sets_notification_error_and_keeps_pipeline_ok(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    monkeypatch.setattr(daily, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("smtp outage")))
    monkeypatch.setattr(
        daily,
        "run_command",
        lambda args, cwd=daily.ROOT: (write_generated_output(root, "2026-05-07") or completed(args))
        if "run_gaza_dispatch.py" in " ".join(args)
        else completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}),
    )

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])
    summary = json.loads(capsys.readouterr().out)
    assert code == 2
    assert summary["pipeline_ok"] is True
    assert summary["generation_ok"] is True
    assert summary["validation_ok"] is True
    assert summary["publish_ok"] is True
    assert summary["email_requested"] is True
    assert summary["email_ok"] is False
    assert summary["overall_ok"] is False
    assert "smtp outage" in str(summary["notification_error"])


def test_pipeline_success_email_tls_failure_includes_safe_guidance(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    tls_exc = ssl.SSLCertVerificationError("certificate verify failed")
    monkeypatch.setattr(daily, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(tls_exc))
    monkeypatch.setattr(
        daily,
        "run_command",
        lambda args, cwd=daily.ROOT: (write_generated_output(root, "2026-05-07") or completed(args))
        if "run_gaza_dispatch.py" in " ".join(args)
        else completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}),
    )

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--email-report", "--pages-repo", str(root / "bluefern-dispatches-pages")])
    summary = json.loads(capsys.readouterr().out)
    assert code == 2
    err = str(summary["notification_error"])
    assert "SMTP TLS certificate verification failed" in err
    assert "SMTP_CA_FILE" in err and "SMTP_CA_BUNDLE" in err
    assert "SMTP_RELAX_X509_STRICT=1" in err


def test_no_old_gaza_project_path_is_referenced():
    forbidden = "Gaza " + "Dispatch V4"
    assert forbidden not in Path(daily.__file__).read_text(encoding="utf-8")


def test_bluesky_not_attempted_when_pipeline_fails(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    called = {"count": 0}

    def fake_bluesky(**kwargs):
        _ = kwargs
        called["count"] += 1
        return {"status": "success", "post_uri": "at://example", "reason": None}

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            return completed(args, returncode=1, stdout="generation failed")
        return completed(args)

    monkeypatch.setattr(daily, "maybe_post_gaza_dispatch_to_bluesky", fake_bluesky)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--post-bluesky", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])
    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert called["count"] == 0
    assert summary["bluesky_status"] == "skipped"


def test_force_bluesky_post_flag_is_forwarded(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    captured = {"kwargs": None}

    def fake_bluesky(**kwargs):
        captured["kwargs"] = kwargs
        return {"status": "skipped", "post_uri": None, "reason": "disabled_by_env"}

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command:
            payload = {"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}
            if "--dry-run" not in args:
                write_pages_output(root, "2026-05-07")
                payload.update({"copied": True, "commit_sha": "abc1234", "committed_branch": "gh-pages"})
            return completed(args, payload=payload)
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "maybe_post_gaza_dispatch_to_bluesky", fake_bluesky)
    monkeypatch.setattr(daily, "run_command", fake_run)
    code = daily.main(
        [
            "--date",
            "2026-05-07",
            "--skip-tests",
            "--post-bluesky",
            "--force-bluesky-post",
            "--pages-repo",
            str(root / "bluefern-dispatches-pages"),
        ]
    )
    _summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert captured["kwargs"] is not None
    assert captured["kwargs"]["force_post"] is True


def test_daily_summary_uses_existing_receipt_skip_result(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")

    def fake_bluesky(**kwargs):
        _ = kwargs
        return {
            "status": "skipped",
            "post_uri": "at://did:plc:abc123/app.bsky.feed.post/existing",
            "reason": "skipped_existing_receipt",
            "embed_type": "app.bsky.embed.external",
            "card_title": "Dispatches from Gaza - May 7, 2026",
            "card_description": "Existing receipt description.",
            "thumb_status": "uploaded",
        }

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command:
            payload = {"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}
            if "--dry-run" not in args:
                write_pages_output(root, "2026-05-07")
                payload.update({"copied": True, "commit_sha": "abc1234", "committed_branch": "gh-pages"})
            return completed(args, payload=payload)
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "maybe_post_gaza_dispatch_to_bluesky", fake_bluesky)
    monkeypatch.setattr(daily, "run_command", fake_run)
    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--post-bluesky", "--pages-repo", str(root / "bluefern-dispatches-pages")])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["bluesky_status"] == "skipped"
    assert summary["bluesky_reason"] == "skipped_existing_receipt"
    assert summary["bluesky_post_uri"] == "at://did:plc:abc123/app.bsky.feed.post/existing"
    assert summary["bluesky_embed_type"] == "app.bsky.embed.external"


def test_dry_run_bluesky_records_preview_without_publishing(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    captured = {"kwargs": None}

    def fake_bluesky(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "status": "skipped",
            "post_uri": None,
            "reason": "dry_run",
            "post_text": "In the June 7 Gaza briefing: Khan Younis strikes; Gaza civil defence reported 10 killed; and West Bank developments.",
            "embed_type": "app.bsky.embed.external",
            "card_title": "Dispatches from Gaza - June 7, 2026",
            "card_description": "Palestinians inspect the aftermath of an Israeli strike in Khan Younis.",
            "source_artifact_paths": ["output/dispatches/gaza/editions/2026-06-07/curation_manifest.json"],
            "edition_date_verified": True,
            "stale_content_guard_status": "passed",
            "thumb_status": "not_attempted",
        }

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command:
            payload = {"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}
            if "--dry-run" not in args:
                write_pages_output(root, "2026-05-07")
                payload.update({"copied": True, "commit_sha": "abc1234", "committed_branch": "gh-pages"})
            return completed(args, payload=payload)
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "maybe_post_gaza_dispatch_to_bluesky", fake_bluesky)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--dry-run", "--post-bluesky", "--pages-repo", str(root / "bluefern-dispatches-pages")])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert captured["kwargs"] is not None
    assert captured["kwargs"]["allow_publish"] is False
    assert summary["bluesky_status"] == "skipped"
    assert summary["bluesky_reason"] == "dry_run"
    assert summary["bluesky_post_text"] == "In the June 7 Gaza briefing: Khan Younis strikes; Gaza civil defence reported 10 killed; and West Bank developments."
    assert summary["bluesky_edition_date_verified"] is True
    assert summary["bluesky_stale_content_guard_status"] == "passed"


def test_generate_audio_none_runs_after_generation(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    called = {"count": 0, "provider": None}

    class DummyAudio:
        edition_date = "2026-05-07"
        transcript_path = root / "output" / "site" / "gaza" / "audio" / "2026-05-07-transcript.html"
        metadata_path = root / "output" / "site" / "gaza" / "audio" / "2026-05-07.json"
        podcast_path = root / "output" / "site" / "gaza" / "podcast.xml"
        flash_briefing_path = root / "output" / "site" / "gaza" / "flash-briefing.json"
        audio_status = "script_ready_no_audio_file"
        audio_file = None
        audio_url = None
        tts_provider = "none"
        tts_model = None
        tts_voice = None
        tts_error = None
        story_count = 3

    def fake_audio(*args, **kwargs):
        _ = args
        called["count"] += 1
        called["provider"] = kwargs.get("tts_provider")
        return DummyAudio()

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command:
            payload = {"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}
            if "--dry-run" not in args:
                write_pages_output(root, "2026-05-07")
                payload.update({"copied": True, "commit_sha": "abc1234", "committed_branch": "gh-pages"})
            return completed(args, payload=payload)
        return completed(args, stdout="ok")

    monkeypatch.setattr("bluefern_dispatches.gaza_audio.write_gaza_audio_outputs", fake_audio)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(
        [
            "--date",
            "2026-05-07",
            "--skip-tests",
            "--generate-audio",
            "--tts-provider",
            "none",
            "--pages-repo",
            str(root / "bluefern-dispatches-pages"),
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert called["count"] == 1
    assert called["provider"] == "none"
    assert summary["ok"] is True


def test_dry_run_without_audio_refreshes_public_audio_surfaces(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "gaza-logo.png").write_bytes(b"png")
    pages_audio_root = root / "bluefern-dispatches-pages" / "gaza" / "audio"
    pages_audio_root.mkdir(parents=True, exist_ok=True)
    for edition_date in ("2026-05-06", "2026-05-05"):
        (pages_audio_root / f"{edition_date}-transcript.html").write_text("<html>Archived transcript</html>", encoding="utf-8")
        (pages_audio_root / f"{edition_date}.json").write_text(
            json.dumps(
                {
                    "edition_date": edition_date,
                    "transcript_url": f"https://dispatches.thebluefernco.com/gaza/audio/{edition_date}-transcript.html",
                    "script_text": f"Archived Gaza audio summary for {edition_date}.",
                    "audio_file": None,
                    "audio_url": None,
                    "audio_mime_type": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
            return completed(args, payload={"ok": True})
        if "publish_github_pages.py" in command and "--dry-run" in args:
            return completed(
                args,
                payload={
                    "ok": True,
                    "errors": [],
                    "paid_detail_excluded_from_public": True,
                    "target_pages_branch": "gh-pages",
                },
            )
        if args[:3] == ["git", "status", "--porcelain=v1"]:
            return completed(args, stdout="")
        if args[:3] == ["git", "fetch", "origin"]:
            return completed(args, stdout="fetched")
        if args[:2] == ["git", "status"]:
            return completed(args, stdout="On branch gh-pages")
        if args[:3] == ["git", "push", "origin"]:
            return completed(args, stdout="pushed")
        if args[:2] == ["git", "rev-parse"] and args[-1] == "origin/gh-pages":
            return completed(args, stdout="remote-sha")
        if args[:2] == ["git", "ls-tree"]:
            return completed(args, stdout=args[-1])
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--dry-run", "--skip-tests", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    audio_index = root / "output" / "site" / "gaza" / "audio" / "index.html"
    podcast = root / "output" / "site" / "gaza" / "audio" / "podcast.xml"
    assert code == 0
    assert audio_index.exists()
    assert podcast.exists()
    assert "2026-05-06" in audio_index.read_text(encoding="utf-8")
    assert "2026-05-05" in podcast.read_text(encoding="utf-8")
    assert summary["ok"] is True


def test_gaza_daily_operator_defaults_to_audio_generation_on_publish(isolated, monkeypatch):
    monkeypatch.setattr(operator, "ROOT", isolated)
    args = operator.parse_args(["--date", "2026-05-07", "--pages-repo", str(isolated / "bluefern-dispatches-pages")])
    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(operator, "_git_status_lines", lambda repo: [])
    monkeypatch.setattr(operator, "_git_branch", lambda repo: "add/pages-repo-default" if repo == isolated else "gh-pages")
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda pages_repo, pages_branch: (True, None))
    monkeypatch.setattr(operator, "_sync_pages_repo", lambda pages_repo, pages_branch: {"ok": True, "commands": []})
    monkeypatch.setattr(operator, "validate_or_repair_manual_sources", lambda edition_date: {"ok": True, "status": "valid", "errors": []})

    def fake_daily(run_args: list[str]):
        captured["daily_args"] = run_args
        return 0, {
            "source_count": 5,
            "publisher_count": 3,
            "public_story_count": 5,
            "generation_ok": True,
            "validation_ok": True,
            "tests_ok": True,
            "public_url": f"https://dispatches.thebluefernco.com/gaza/editions/{args.date}/",
        }, "{}"

    def fake_run(args_list: list[str], *, cwd: Path = operator.ROOT):
        _ = cwd
        command = " ".join(args_list)
        if "publish_github_pages.py" in command and "--dry-run" in args_list:
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "ok": True,
                            "errors": [],
                            "paid_detail_excluded_from_public": True,
                            "target_pages_branch": "gh-pages",
                        }
                    ),
                    "stderr": "",
                },
            )()
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(operator, "_capture_daily_run", fake_daily)
    monkeypatch.setattr(operator, "_run_command", fake_run)
    monkeypatch.setattr(operator, "_clean_source_generated_artifacts", lambda: {"ok": True, "status": "cleaned", "commands": []})
    monkeypatch.setattr(operator, "_git_status_branch", lambda repo: "## clean")

    result = operator.run_operator(args)

    assert result["ok"] is True
    assert result["audio_status"] == "audio_generated"
    assert "--generate-audio" in captured["daily_args"]
    assert "--tts-provider" in captured["daily_args"]
    assert "none" in captured["daily_args"]


def test_daily_audio_flags_are_forwarded_to_gaza_audio_writer(isolated, monkeypatch, capsys):
    root = isolated
    write_manual_sources(root, "2026-05-07")
    captured: dict[str, object] = {}

    class DummyAudio:
        edition_date = "2026-05-07"
        transcript_path = root / "output" / "site" / "gaza" / "audio" / "2026-05-07-transcript.html"
        metadata_path = root / "output" / "site" / "gaza" / "audio" / "2026-05-07.json"
        podcast_path = root / "output" / "site" / "gaza" / "podcast.xml"
        flash_briefing_path = root / "output" / "site" / "gaza" / "flash-briefing.json"
        audio_status = "audio_file_ready"
        audio_file = "2026-05-07.mp3"
        audio_url = "/gaza/audio/2026-05-07.mp3"
        tts_provider = "openai"
        tts_model = "gpt-4o-mini-tts"
        tts_voice = "alloy"
        tts_error = None
        story_count = 3

    def fake_audio(*args, **kwargs):
        _ = args
        captured.update(kwargs)
        return DummyAudio()

    def fake_run(args, cwd=daily.ROOT):
        command = " ".join(args)
        if "run_gaza_dispatch.py" in command:
            write_generated_output(root, "2026-05-07")
        if "publish_github_pages.py" in command:
            payload = {"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}
            if "--dry-run" not in args:
                write_pages_output(root, "2026-05-07")
                payload.update({"copied": True, "commit_sha": "abc1234", "committed_branch": "gh-pages"})
            return completed(args, payload=payload)
        return completed(args, stdout="ok")

    monkeypatch.setattr("bluefern_dispatches.gaza_audio.write_gaza_audio_outputs", fake_audio)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(
        [
            "--date",
            "2026-05-07",
            "--skip-tests",
            "--generate-audio",
            "--tts-provider",
            "openai",
            "--audio-model",
            "gpt-4o-mini-tts",
            "--audio-voice",
            "alloy",
            "--audio-format",
            "mp3",
            "--pages-repo",
            str(root / "bluefern-dispatches-pages"),
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert captured["tts_provider"] == "openai"
    assert captured["tts_model"] == "gpt-4o-mini-tts"
    assert captured["tts_voice"] == "alloy"
    assert captured["audio_format"] == "mp3"
    assert summary["ok"] is True
