import json
import ssl
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import scripts.publish_gaza_historical as historical
import scripts.run_daily_gaza as daily


def make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "daily"
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    pages = root / "bluefern-dispatches-pages"
    pages.mkdir()
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
                "publisher": "Example News",
                "published_at": f"{edition_date}T12:00:00Z",
                "retrieved_at": "2026-05-07T00:00:00Z",
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


def write_generated_output(root: Path, edition_date: str, source_links: bool = True) -> None:
    edition = root / "output" / "site" / "gaza" / "editions" / edition_date
    edition.mkdir(parents=True, exist_ok=True)
    link = '<p><strong>Sources</strong></p><a href="https://valid.test/gaza-source">Example News source</a>' if source_links else "<p>No links.</p>"
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
    assert summary["pages_branch"] == "gh-pages"
    assert summary["pushed"] is False
    assert "git push origin gh-pages" in summary["manual_push_command"]
    assert (root / "logs" / "gaza-daily-2026-05-07.log").exists()
    assert (root / "data" / "dispatches" / "gaza" / "editions" / "2026-05-07" / "run_manifest.json").exists()
    publish_calls = [call for call in calls if "publish_github_pages.py" in " ".join(call)]
    assert publish_calls
    assert all("--expect-date" in call and "2026-05-07" in call for call in publish_calls)
    assert all("--expect-dispatch" in call and "gaza" in call for call in publish_calls)


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
    assert "manual_sources.json was present but invalid" in summary["warnings"][0]


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
            return completed(args, payload={"ok": True, "errors": [], "copied": True, "commit_sha": "abc1234", "target_pages_branch": "gh-pages", "committed_branch": "gh-pages"})
        if "publish_github_pages.py" in command:
            return completed(args, payload={"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"})
        return completed(args, stdout="ok")

    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(["--date", "2026-05-07", "--skip-tests", "--push", "--pages-repo", str(root / "bluefern-dispatches-pages")])

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert ["git", "push", "origin", "gh-pages"] in calls
    assert summary["pushed"] is True


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
