from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import care_line_publication_scheduler as scheduler


DATE = "2026-09-05"


class DummyLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stale_recovered = False

    def acquire(self) -> str:
        return "acquired"

    def release(self) -> None:
        return None


def _candidate(*, published: bool = False) -> scheduler.ReleaseCandidate:
    bundle = SimpleNamespace(
        proposal_sha256="proposal-sha256",
        review_snapshot_sha256="review-sha256",
    )
    return scheduler.ReleaseCandidate(DATE, bundle, published)  # type: ignore[arg-type]


def _state(root: Path, label: str, head: str) -> dict[str, object]:
    return {
        "root": str(root),
        "branch": scheduler.PAGES_BRANCH if label == "Pages repo" else scheduler.PRODUCTION_BRANCH,
        "head": head,
        "remote_head": head,
        "unexpected_dirty_paths": [],
    }


def _install_healthy_checks(monkeypatch: pytest.MonkeyPatch, *, pages_after: str = "pages-old") -> None:
    monkeypatch.setattr(scheduler, "RECEIPT_ROOT", Path("receipts"))
    monkeypatch.setattr(scheduler, "LOG_ROOT", Path("logs"))
    monkeypatch.setattr(scheduler, "LOCK_PATH", Path("locks/publication.lock"))
    page_calls = 0

    def verify(root: Path, *, branch: str, label: str, allow_care_line_runtime: bool):  # noqa: ANN001
        nonlocal page_calls
        if label == "Pages repo":
            page_calls += 1
            return _state(root, label, "pages-old" if page_calls == 1 else pages_after)
        return _state(root, label, "source-head")

    monkeypatch.setattr(scheduler, "verify_repo", verify)
    monkeypatch.setattr(scheduler, "run_preflight", lambda *args: None)
    monkeypatch.setattr(scheduler, "SchedulerLock", DummyLock)


def test_no_release_is_a_healthy_no_op_with_durable_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    source.mkdir()
    pages.mkdir()
    _install_healthy_checks(monkeypatch)
    monkeypatch.setattr(scheduler, "discover_release_candidates", lambda *args: [])
    monkeypatch.setattr(
        scheduler,
        "_run_child",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no-release run must not launch publication")),
    )

    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch=scheduler.PRODUCTION_BRANCH,
        pages_branch=scheduler.PAGES_BRANCH,
        run_date=DATE,
        run_id="no-release",
    )

    assert exit_code == 0
    assert receipt["status"] == "safe_no_op"
    assert receipt["no_op_reason"] == "no_approved_release"
    assert receipt["publication_attempted"] is False
    assert receipt["pages_changed"] is False
    assert receipt["source_changed"] is False
    assert not any(receipt["unauthorized_side_effects"].values())
    stored = json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))
    assert stored["status"] == "safe_no_op"
    assert Path(receipt["log_path"]).is_file()


def test_release_ready_handoff_invokes_only_guarded_isolated_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    source.mkdir()
    pages.mkdir()
    _install_healthy_checks(monkeypatch, pages_after="pages-new")
    monkeypatch.setattr(scheduler, "discover_release_candidates", lambda *args: [_candidate()])
    monkeypatch.setattr(scheduler, "_already_published", lambda *args: True)
    captured: list[str] = []

    def run_child(command: list[str], *, cwd: Path) -> scheduler.ChildExecution:
        captured.extend(command)
        result = {
            "ok": True,
            "status": "publication_success",
            "edition_date": DATE,
            "publication_attempted": True,
            "pushed": True,
            "bluesky_result": {"requested": False, "status": "skipped"},
        }
        return scheduler.ChildExecution(4321, 0, json.dumps(result), "")

    monkeypatch.setattr(scheduler, "_run_child", run_child)
    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch=scheduler.PRODUCTION_BRANCH,
        pages_branch=scheduler.PAGES_BRANCH,
        run_date=DATE,
        run_id="release-ready",
    )

    assert exit_code == 0
    assert receipt["status"] == "publication_success"
    assert receipt["edition_date"] == DATE
    assert receipt["source_changed"] is False
    assert receipt["pages_changed"] is True
    assert "--publish" in captured
    assert "--push" in captured
    assert "--isolated-source" in captured
    assert "--post-bluesky" not in captured
    assert not any(receipt["unauthorized_side_effects"].values())


@pytest.mark.parametrize("dirty_label", ["source repo", "Pages repo"])
def test_dirty_source_or_pages_fails_closed_with_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty_label: str
) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    source.mkdir()
    pages.mkdir()
    monkeypatch.setattr(scheduler, "RECEIPT_ROOT", Path("receipts"))
    monkeypatch.setattr(scheduler, "LOG_ROOT", Path("logs"))
    monkeypatch.setattr(scheduler, "LOCK_PATH", Path("locks/publication.lock"))
    monkeypatch.setattr(scheduler, "SchedulerLock", DummyLock)

    def verify(root: Path, *, branch: str, label: str, allow_care_line_runtime: bool):  # noqa: ANN001
        if label == dirty_label:
            raise scheduler.PublicationSchedulerError(f"{label} contains risky dirty paths: unsafe.txt")
        return _state(root, label, "source-head" if label == "source repo" else "pages-old")

    monkeypatch.setattr(scheduler, "verify_repo", verify)
    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch=scheduler.PRODUCTION_BRANCH,
        pages_branch=scheduler.PAGES_BRANCH,
        run_date=DATE,
        run_id=f"dirty-{dirty_label.split()[0]}",
    )

    assert exit_code == 1
    assert receipt["status"] == "failure"
    assert "risky dirty paths" in receipt["error"]
    assert receipt["publication_attempted"] is False
    assert Path(receipt["receipt_path"]).is_file()


def test_wrong_branch_fails_before_release_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    source.mkdir()
    pages.mkdir()
    monkeypatch.setattr(scheduler, "RECEIPT_ROOT", Path("receipts"))
    monkeypatch.setattr(scheduler, "LOG_ROOT", Path("logs"))
    monkeypatch.setattr(scheduler, "LOCK_PATH", Path("locks/publication.lock"))
    monkeypatch.setattr(scheduler, "SchedulerLock", DummyLock)
    calls = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="wrong-branch\n", stderr=""),
        ]
    )
    monkeypatch.setattr(scheduler, "_run", lambda *args, **kwargs: next(calls))

    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch=scheduler.PRODUCTION_BRANCH,
        pages_branch=scheduler.PAGES_BRANCH,
        run_date=DATE,
        run_id="wrong-branch",
    )

    assert exit_code == 1
    assert receipt["failure_stage"] == "source_state"
    assert "branch mismatch" in receipt["error"]


def test_already_published_release_is_idempotent_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    source.mkdir()
    pages.mkdir()
    _install_healthy_checks(monkeypatch)
    monkeypatch.setattr(scheduler, "discover_release_candidates", lambda *args: [_candidate(published=True)])
    monkeypatch.setattr(
        scheduler,
        "_run_child",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("published release must not launch child")),
    )

    exit_code, receipt = scheduler.run_publication_once(
        source,
        pages,
        source_branch=scheduler.PRODUCTION_BRANCH,
        pages_branch=scheduler.PAGES_BRANCH,
        run_date=DATE,
        run_id="already-published",
    )

    assert exit_code == 0
    assert receipt["status"] == "safe_no_op"
    assert receipt["no_op_reason"] == "already_published"
    assert receipt["already_published_releases"] == [DATE]
    assert receipt["pages_changed"] is False


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8")


def test_release_discovery_requires_protected_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    pages = tmp_path / "pages"
    proposal_root = source / "data" / "dispatches" / "care-line" / "review" / "proposed-editions"
    review_root = source / "data" / "dispatches" / "care-line" / "review" / "signal-reviews"
    proposal_root.mkdir(parents=True)
    review_root.mkdir(parents=True)
    pages.mkdir()
    proposal = {
        "schema_version": "bluefern.care_line.proposed_edition.v1",
        "edition_date": DATE,
        "release_ready": True,
        "approved_signal_ids": ["candidate-1"],
    }
    snapshot = {
        "schema_version": "bluefern.care_line.review_snapshot.v2",
        "edition_date": DATE,
        "release_ready": True,
        "review_payload": {"items": [{"candidate_id": "candidate-1"}]},
    }
    proposal_path = proposal_root / f"{DATE}.json"
    review_path = review_root / f"{DATE}.json"
    proposal_path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    review_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    _git(source, "init")
    _git(source, "config", "user.email", "tests@example.test")
    _git(source, "config", "user.name", "Tests")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "protected approved release")
    monkeypatch.setattr(scheduler, "_already_published", lambda *args: False)

    candidates = scheduler.discover_release_candidates(source, pages)
    assert [candidate.edition_date for candidate in candidates] == [DATE]

    proposal["headline"] = "local-only change"
    proposal_path.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(scheduler.PublicationSchedulerError, match="differs from protected HEAD"):
        scheduler.discover_release_candidates(source, pages)


def test_wrapper_has_no_schedule_or_unauthorized_pipeline_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "scripts" / "windows" / "run_care_line_approved_release_publication.ps1"
    text = wrapper.read_text(encoding="utf-8")
    assert "scripts\\care_line_publication_scheduler.py" in text
    assert "Register-ScheduledTask" not in text
    assert "New-ScheduledTaskTrigger" not in text
    assert "run_care_line_national_pipeline" not in text
    assert "run_care_line_reviewed_event_queue" not in text
    assert "bluesky" not in text.lower()
    assert "--post-bluesky" not in text
