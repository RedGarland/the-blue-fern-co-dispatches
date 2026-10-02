from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.ingest_watch_heartbeat as ingest
import scripts.submit_watch_heartbeat as submit
import bluefern_dispatches.watch_ledger as watch_ledger
from bluefern_dispatches.watch_ledger import (
    LEDGER_ROOT,
    RECONCILIATION_SCHEMA,
    SCHEMA_VERSION,
    WatchLedgerError,
    commit_and_push_watch_ledger,
    persist_watch_run,
    reconcile_day,
    validate_watch_run,
    validate_watch_ledger_paths,
)


def _run(*, dispatch: str = "care-line", outcome: str = "findings", status: str = "SUCCESS") -> dict:
    findings = [] if outcome != "findings" else [
        {
            "finding_id": "care-123",
            "canonical_source_url": "https://example.org/closure",
            "title": "Clinic closes",
        }
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "dispatch": dispatch,
        "watch_name": "Care Line Watch",
        "run_id": "20260925T120000Z-care",
        "scheduled_for": "2026-09-25T12:00:00Z",
        "started_at": "2026-09-25T12:00:02Z",
        "completed_at": "2026-09-25T12:01:00Z",
        "status": status,
        "outcome": outcome,
        "search_window": {"date_from": "2026-09-25", "date_to": "2026-09-25", "edition_date": "2026-09-25"},
        "findings": findings,
        "coverage_notes": "fixture",
    }


def test_zero_result_run_is_valid_and_durable(tmp_path: Path) -> None:
    payload = _run(outcome="no_findings")
    assert validate_watch_run(payload) == []
    result = persist_watch_run(tmp_path, payload)
    assert result["status"] == "SUCCESS"
    assert result["path"].startswith("ops/watch-ledger/")
    retry = persist_watch_run(tmp_path, payload)
    assert retry["status"] == "SAFE_NO_OP"


def test_conflicting_same_run_id_fails_closed(tmp_path: Path) -> None:
    payload = _run(outcome="no_findings")
    persist_watch_run(tmp_path, payload)
    payload["coverage_notes"] = "changed"
    with pytest.raises(FileExistsError):
        persist_watch_run(tmp_path, payload)


def test_failed_run_requires_failed_outcome() -> None:
    payload = _run(outcome="no_findings", status="FAILED")
    assert any("FAILED status requires failed outcome" in item for item in validate_watch_run(payload))


def test_reconciliation_flags_unaccounted_then_accounts_matching_production(tmp_path: Path) -> None:
    payload = _run()
    persist_watch_run(tmp_path, payload)

    report = reconcile_day(tmp_path, "care-line", "2026-09-25")
    assert report["schema_version"] == RECONCILIATION_SCHEMA
    assert report["run_count"] == 1
    assert report["unreconciled_count"] == 1
    assert report["publication_authorized"] is False

    production = tmp_path / "data/dispatches/care-line/review"
    production.mkdir(parents=True)
    (production / "candidate-registry.json").write_text(
        json.dumps({"source": "https://example.org/closure"}),
        encoding="utf-8",
    )
    report = reconcile_day(tmp_path, "care-line", "2026-09-25", write=False)
    assert report["accounted_count"] == 1
    assert report["unreconciled_count"] == 0


def test_reconciliation_records_zero_finding_heartbeat(tmp_path: Path) -> None:
    persist_watch_run(tmp_path, _run(dispatch="gaza", outcome="no_findings"))
    report = reconcile_day(tmp_path, "gaza", "2026-09-25", write=False)
    assert report["run_count"] == 1
    assert report["zero_finding_run_count"] == 1
    assert report["finding_count"] == 0


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _init_ledger_checkout(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "--bare", remote.as_posix()], check=True, capture_output=True, text=True)
    subprocess.run(["git", "clone", remote.as_posix(), checkout.as_posix()], check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "watch@example.test"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.name", "Watch Test"], cwd=checkout, check=True)
    subprocess.run(["git", "switch", "-c", "ops/watch-ledger"], cwd=checkout, check=True, capture_output=True, text=True)
    (checkout / "README.md").write_text("watch ledger branch\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-m", "seed watch ledger branch"], cwd=checkout, check=True, capture_output=True, text=True)
    subprocess.run(["git", "push", "-u", "origin", "ops/watch-ledger"], cwd=checkout, check=True, capture_output=True, text=True)
    return checkout, remote


def test_successful_ingestion_appends_and_pushes_authoritative_ops_watch_ledger(tmp_path: Path) -> None:
    checkout, remote = _init_ledger_checkout(tmp_path)

    result = ingest.ingest_watch_heartbeat(_run(outcome="no_findings"), repo_root=checkout, commit=True, push=True)

    assert result["status"] == "SUCCESS"
    assert result["ledger_path"].startswith(f"{LEDGER_ROOT.as_posix()}/care-line/2026-09-25/")
    assert result["push_succeeded"] is True
    assert result["schedule_mutated"] is False
    remote_head = subprocess.run(
        ["git", "--git-dir", remote.as_posix(), "rev-parse", "refs/heads/ops/watch-ledger"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert remote_head == result["commit"]


def test_runner_submission_uses_workflow_dispatch_without_direct_ledger_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _run(outcome="no_findings")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="queued\n", stderr="")

    monkeypatch.setattr(submit.subprocess, "run", fake_run)

    result = submit.dispatch_heartbeat_workflow(payload, repo="owner/repo")

    assert result["status"] == "WORKFLOW_DISPATCHED"
    assert result["direct_ledger_mutation"] is False
    assert result["fallback_branch_authoritative"] is False
    assert calls and calls[0][:4] == ["gh", "workflow", "run", "watch-heartbeat-ingest.yml"]
    assert not (tmp_path / LEDGER_ROOT).exists()


def test_runner_submission_accepts_windows_utf8_bom_payload(tmp_path: Path) -> None:
    payload_path = tmp_path / "heartbeat.json"
    payload_path.write_text(json.dumps(_run(outcome="no_findings")), encoding="utf-8-sig")

    assert submit._load_payload(payload_path)["run_id"] == "20260925T120000Z-care"


def test_final_ref_update_blocked_fails_heartbeat_without_schedule_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _run(outcome="no_findings")
    persist_watch_run(tmp_path, payload)

    def fake_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "status":
            return subprocess.CompletedProcess(["git", *args], 0, stdout=f"?? {LEDGER_ROOT.as_posix()}/\n", stderr="")
        if args[0] == "add":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="")
        if args[:3] == ("diff", "--cached", "--name-only"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout=watch_ledger.ledger_path(root, payload).relative_to(root).as_posix() + "\n", stderr="")
        if args[0] == "commit":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="[ops/watch-ledger abc] commit\n", stderr="")
        if args[0] == "branch":
            return subprocess.CompletedProcess(["git", *args], 0, stdout="ops/watch-ledger\n", stderr="")
        if args[0] == "push":
            return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr="protected ref update blocked")
        raise AssertionError(f"unexpected git command: {args}")

    monkeypatch.setattr(watch_ledger, "_git", fake_git)

    with pytest.raises(WatchLedgerError, match="protected ref update blocked"):
        commit_and_push_watch_ledger(tmp_path, paths=[watch_ledger.ledger_path(tmp_path, payload).relative_to(tmp_path).as_posix()], message="append")


def test_watch_ledger_path_guard_blocks_non_ledger_paths() -> None:
    validate_watch_ledger_paths(["ops/watch-ledger/gaza/2026-09-25/run.json"])
    with pytest.raises(WatchLedgerError):
        validate_watch_ledger_paths(["ops/status/system/latest.json"])


def test_ingestion_sources_do_not_define_disable_or_pause_watch_recovery_paths() -> None:
    workflow = Path(".github/workflows/watch-heartbeat-ingest.yml").read_text(encoding="utf-8")
    ingester = Path("scripts/ingest_watch_heartbeat.py").read_text(encoding="utf-8")
    submitter = Path("scripts/submit_watch_heartbeat.py").read_text(encoding="utf-8")
    combined = f"{workflow}\n{ingester}\n{submitter}".lower()
    assert "schtasks" not in combined
    assert "disable-scheduledtask" not in combined
    assert "disable watch" not in combined
    assert "pause watch" not in combined
