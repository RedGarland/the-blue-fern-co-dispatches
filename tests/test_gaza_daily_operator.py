from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import scripts.run_gaza_daily_operator as operator


def make_root(repo: Path) -> Path:
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "gaza-operator"
    root.mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "bluefern-dispatches-pages").mkdir()
    return root


def write_manual_sources(root: Path, edition_date: str, payload: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_manual_sources_with_bom(root: Path, edition_date: str, payload: list[dict]) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources" / edition_date / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8-sig")
    return path


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = make_root(repo)
    monkeypatch.setattr(operator, "ROOT", root)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_manual_source_validation_accepts_template_shaped_record(isolated: Path) -> None:
    write_manual_sources(
        isolated,
        "2026-06-26",
        [
            {
                "source_record_id": "gaza-src-2026-06-26-001",
                "title": "Aid access update",
                "url": "https://valid.test/gaza-aid",
                "publisher": "Example News",
                "published_at": "2026-06-26T08:00:00Z",
                "retrieved_at": "2026-06-26T09:00:00Z",
                "summary_or_snippet": "Source-backed update.",
                "source_type": "news",
                "provider_id": "manual-supplement",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
                "attribution_mode": "reported_public_source",
                "claim_status": "reported_public_source",
                "traceability_note": "Manual supplement from a public report.",
            }
        ],
    )
    result = operator.validate_or_repair_manual_sources("2026-06-26")
    assert result["ok"] is True
    assert result["status"] == "valid"


def test_manual_source_validation_rejects_missing_required_fields_with_precise_messages(isolated: Path) -> None:
    write_manual_sources(
        isolated,
        "2026-06-26",
        [
            {
                "publisher": "Example News",
                "published_at": "2026-06-26T08:00:00Z",
                "summary_or_snippet": "Source-backed update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
            }
        ],
    )
    result = operator.validate_or_repair_manual_sources("2026-06-26")
    assert result["ok"] is False
    assert "source record 1 missing required fields: title, url, traceability_note" in result["errors"][0]


def test_manual_source_validation_accepts_bom_prefixed_json(isolated: Path) -> None:
    write_manual_sources_with_bom(
        isolated,
        "2026-06-26",
        [
            {
                "source_record_id": "gaza-src-2026-06-26-001",
                "title": "Aid access update",
                "url": "https://valid.test/gaza-aid",
                "publisher": "Example News",
                "published_at": "2026-06-26T08:00:00Z",
                "retrieved_at": "2026-06-26T09:00:00Z",
                "summary_or_snippet": "Source-backed update.",
                "source_type": "news",
                "provider_id": "manual-supplement",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
                "attribution_mode": "reported_public_source",
                "claim_status": "reported_public_source",
                "traceability_note": "Manual supplement from a public report.",
            }
        ],
    )
    result = operator.validate_or_repair_manual_sources("2026-06-26")
    assert result["ok"] is True
    assert result["status"] == "valid"


def test_no_publishable_source_run_is_operator_success(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = operator.parse_args(["--date", "2026-06-26", "--pages-repo", str(isolated / "bluefern-dispatches-pages")])
    monkeypatch.setattr(operator, "_git_status_lines", lambda repo: [])
    monkeypatch.setattr(operator, "_git_branch", lambda repo: "add/pages-repo-default" if repo == isolated else "gh-pages")
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda pages_repo, pages_branch: (True, None))
    monkeypatch.setattr(operator, "_sync_pages_repo", lambda pages_repo, pages_branch: {"ok": True, "commands": ["git fetch", "git reset"]})
    monkeypatch.setattr(operator, "validate_or_repair_manual_sources", lambda edition_date: {"ok": True, "status": "not_present", "errors": []})
    monkeypatch.setattr(
        operator,
        "_capture_daily_run",
        lambda run_args: (
            1,
            {
                "source_count": 2,
                "publisher_count": 2,
                "public_story_count": 0,
                "generation_ok": False,
                "validation_ok": False,
                "tests_ok": False,
                "errors": ["No valid traceable Gaza sources survived normalization and dedupe; refusing public edition generation."],
            },
            "{}",
        ),
    )
    monkeypatch.setattr(operator, "_clean_source_generated_artifacts", lambda: {"ok": True, "status": "cleaned", "commands": []})
    monkeypatch.setattr(operator, "_git_status_branch", lambda repo: "## clean")
    result = operator.run_operator(args)
    assert result["ok"] is True
    assert result["operator_status"] == "NO_PUBLICATION_NEEDED"


def test_pages_sync_uses_fetch_and_reset_safely(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def fake_run(args: list[str], *, cwd: Path = operator.ROOT):
        _ = cwd
        calls.append(args)
        return Completed()

    monkeypatch.setattr(operator, "_run_command", fake_run)
    result = operator._sync_pages_repo(isolated / "bluefern-dispatches-pages", "gh-pages")
    assert result["ok"] is True
    assert any("fetch" in " ".join(call) for call in calls)
    assert any("reset --hard origin/gh-pages" in " ".join(call) for call in calls)
    assert all("pull" not in " ".join(call) for call in calls)


def test_live_verification_retries_and_classifies_propagation_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def fake_fetch(url: str):
        attempts["count"] += 1
        if "/editions/" in url:
            return 200, "<html><body>Dispatches From Gaza stale page without date</body></html>", None
        return 200, "<html><body>stale archive</body></html>", None

    monkeypatch.setattr(operator, "_fetch_url", fake_fetch)
    result = operator.verify_live_publication(
        edition_date="2026-06-26",
        public_urls={
            "edition": "https://dispatches.thebluefernco.com/gaza/editions/2026-06-26/",
            "archive": "https://dispatches.thebluefernco.com/gaza/archive.html",
        },
        cache_token="sha123",
        remote_tree_ok=True,
        sleep_fn=lambda _seconds: None,
    )
    assert result["status"] == "PAGES_PROPAGATION_PENDING"
    assert len(result["attempts"]) > 1


def test_post_only_does_not_publish_push_or_regenerate(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = operator.parse_args(
        ["--date", "2026-06-26", "--post-bluesky-only", "--pages-repo", str(isolated / "bluefern-dispatches-pages")]
    )
    monkeypatch.setattr(operator, "_git_status_lines", lambda repo: [])
    monkeypatch.setattr(operator, "_git_branch", lambda repo: "add/pages-repo-default" if repo == isolated else "gh-pages")
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda pages_repo, pages_branch: (True, None))
    monkeypatch.setattr(operator, "validate_or_repair_manual_sources", lambda edition_date: {"ok": True, "status": "not_present", "errors": []})
    monkeypatch.setattr(
        operator,
        "verify_live_publication",
        lambda **kwargs: {
            "status": "LIVE_OK",
            "live_http_ok": True,
            "live_archive_ok": True,
            "live_homepage_ok": True,
        },
    )
    monkeypatch.setattr(
        operator,
        "maybe_post_gaza_dispatch_to_bluesky",
        lambda **kwargs: {"status": "success", "post_uri": "at://example/post/1"},
    )
    monkeypatch.setattr(operator, "_clean_source_generated_artifacts", lambda: {"ok": True, "status": "cleaned", "commands": []})
    monkeypatch.setattr(operator, "_git_status_branch", lambda repo: "## clean")
    monkeypatch.setattr(
        operator,
        "_capture_daily_run",
        lambda run_args: (_ for _ in ()).throw(AssertionError("daily generation should not run in post-only mode")),
    )
    result = operator.run_operator(args)
    assert result["ok"] is True
    assert result["operator_status"] == "PUBLISHED_AND_POSTED"
    assert all("run_daily_gaza.py" not in command for command in result["commands_run"])
    assert all("push origin" not in command for command in result["commands_run"])


@pytest.mark.parametrize("operator_status", ["ALREADY_POSTED", "PUBLISHED_AND_POSTED"])
def test_email_failure_after_successful_publish_is_nonfatal(isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operator_status: str) -> None:
    args = operator.parse_args(["--date", "2026-06-26", "--email-report", "--pages-repo", str(isolated / "bluefern-dispatches-pages")])
    monkeypatch.setattr(
        operator,
        "run_operator",
        lambda parsed_args: {
            "ok": True,
            "operator_status": operator_status,
            "date": parsed_args.date,
            "source_count": 6,
            "publisher_count": 3,
            "public_story_count": 6,
            "pages_commit_sha": "abc1234",
            "bluesky_status": "skipped",
            "audio_status": "audio_generated",
            "email_status": "not_requested",
            "cleanup_status": "cleaned",
            "public_url": f"https://dispatches.thebluefernco.com/gaza/editions/{parsed_args.date}/",
            "next_action": "Publication completed.",
            "source_repo_status_after": "## clean",
            "pages_repo_status_after": "## clean",
        },
    )
    monkeypatch.setattr(operator, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("smtp outage")))

    code = operator.main(["--date", "2026-06-26", "--email-report", "--pages-repo", str(isolated / "bluefern-dispatches-pages")])

    output = capsys.readouterr().out
    payload = json.loads(output[output.find("{") :])
    assert code == 0
    assert payload["ok"] is True
    assert payload["email_status"].startswith("failed:")
    assert "smtp outage" in output


def test_email_failure_remains_fatal_on_dry_run(isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        operator,
        "run_operator",
        lambda parsed_args: {
            "ok": True,
            "operator_status": "DRY_RUN_READY",
            "date": parsed_args.date,
            "source_count": 6,
            "publisher_count": 3,
            "public_story_count": 6,
            "pages_commit_sha": None,
            "bluesky_status": "skipped",
            "audio_status": "audio_skipped",
            "email_status": "not_requested",
            "cleanup_status": "cleaned",
            "public_url": f"https://dispatches.thebluefernco.com/gaza/editions/{parsed_args.date}/",
            "next_action": "Review the dry-run summary; rerun with --push for live publication.",
            "source_repo_status_after": "## clean",
            "pages_repo_status_after": "## clean",
        },
    )
    monkeypatch.setattr(operator, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("smtp outage")))

    code = operator.main(["--date", "2026-06-26", "--dry-run", "--email-report", "--pages-repo", str(isolated / "bluefern-dispatches-pages")])

    output = capsys.readouterr().out
    payload = json.loads(output[output.find("{") :])
    assert code == 1
    assert payload["ok"] is False
    assert payload["email_status"].startswith("failed:")


def test_audio_retry_reuses_existing_audio(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated / "output" / "site" / "gaza" / "audio").mkdir(parents=True, exist_ok=True)
    (isolated / "output" / "site" / "gaza" / "audio" / "2026-06-26.mp3").write_bytes(b"audio")
    args = operator.parse_args(
        ["--date", "2026-06-26", "--generate-audio", "--pages-repo", str(isolated / "bluefern-dispatches-pages")]
    )
    monkeypatch.setattr(operator, "_git_status_lines", lambda repo: [])
    monkeypatch.setattr(operator, "_git_branch", lambda repo: "add/pages-repo-default" if repo == isolated else "gh-pages")
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda pages_repo, pages_branch: (True, None))
    monkeypatch.setattr(operator, "_sync_pages_repo", lambda pages_repo, pages_branch: {"ok": True, "commands": []})
    monkeypatch.setattr(operator, "validate_or_repair_manual_sources", lambda edition_date: {"ok": True, "status": "not_present", "errors": []})
    captured: dict[str, list[str]] = {}

    def fake_daily(run_args: list[str]):
        captured["args"] = run_args
        return 0, {"generation_ok": True, "validation_ok": True, "tests_ok": True, "source_count": 1, "publisher_count": 1, "public_story_count": 1}, "{}"

    monkeypatch.setattr(operator, "_capture_daily_run", fake_daily)
    monkeypatch.setattr(operator, "_clean_source_generated_artifacts", lambda: {"ok": True, "status": "cleaned", "commands": []})
    monkeypatch.setattr(operator, "_git_status_branch", lambda repo: "## clean")
    result = operator.run_operator(args)
    assert result["audio_status"] == "audio_reused_existing"
    assert "--generate-audio" not in captured["args"]


def test_cleanup_preserves_manual_sources_file(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_manual_sources(
        isolated,
        "2026-06-26",
        [
            {
                "title": "Aid access update",
                "url": "https://valid.test/gaza-aid",
                "publisher": "Example News",
                "published_at": "2026-06-26T08:00:00Z",
                "summary_or_snippet": "Source-backed update.",
                "source_type": "news",
                "region_scope": "Gaza",
                "category_hint": "humanitarian",
                "reliability_tier": "reported-public-source",
                "traceability_note": "Manual supplement from a public report.",
            }
        ],
    )
    args = operator.parse_args(["--date", "2026-06-26", "--pages-repo", str(isolated / "bluefern-dispatches-pages")])
    monkeypatch.setattr(operator, "_git_status_lines", lambda repo: [])
    monkeypatch.setattr(operator, "_git_branch", lambda repo: "add/pages-repo-default" if repo == isolated else "gh-pages")
    monkeypatch.setattr(operator, "_validate_pages_repo", lambda pages_repo, pages_branch: (True, None))
    monkeypatch.setattr(operator, "_sync_pages_repo", lambda pages_repo, pages_branch: {"ok": True, "commands": []})
    monkeypatch.setattr(
        operator,
        "_capture_daily_run",
        lambda run_args: (
            1,
            {
                "source_count": 1,
                "errors": ["No valid traceable Gaza sources survived normalization and dedupe; refusing public edition generation."],
            },
            "{}",
        ),
    )
    monkeypatch.setattr(operator, "_git_status_branch", lambda repo: "## clean")

    def fake_cleanup():
        assert path.exists()
        return {"ok": True, "status": "cleaned", "commands": []}

    monkeypatch.setattr(operator, "_clean_source_generated_artifacts", fake_cleanup)
    result = operator.run_operator(args)
    assert result["cleanup_status"] == "cleaned"
    assert path.exists()


def test_wrapper_passes_arguments_with_array_invocation() -> None:
    wrapper = Path(__file__).resolve().parents[1] / "scripts" / "run_gaza_today_safe.ps1"
    text = wrapper.read_text(encoding="utf-8")
    assert "@pythonArgs" in text
    assert "'--date', $Date" in text
    assert "& $PythonExe @pythonArgs" in text
    assert "Invoke-Expression" not in text
