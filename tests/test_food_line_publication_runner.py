from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bluefern_dispatches.food_line_approved_proposal import ApprovedProposalBundle
from scripts import run_food_line_publication_runner as runner


DATE = "2026-08-15"


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


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_site(repo: Path) -> None:
    site = repo / "output" / "site" / "food-line"
    (site / "editions" / DATE).mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text("<html>food-line index</html>", encoding="utf-8")
    (site / "archive.html").write_text("<html>food-line archive</html>", encoding="utf-8")
    (site / "rss.xml").write_text("<rss />", encoding="utf-8")
    (site / "editions" / DATE / "index.html").write_text("<html>edition</html>", encoding="utf-8")
    (site / "editions" / DATE / "edition_manifest.json").write_text("{}", encoding="utf-8")
    (site / "editions" / DATE / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (site / "editions" / DATE / "curation_manifest.json").write_text("[]", encoding="utf-8")
    (site / "editions" / DATE / "source_table.html").write_text("<table></table>", encoding="utf-8")
    (site / "editions" / DATE / "claim_ledger.html").write_text("<html></html>", encoding="utf-8")
    (repo / "output" / "site" / "index.html").write_text("<html>root homepage</html>", encoding="utf-8")


def _write_release_inputs(repo: Path) -> tuple[Path, Path, Path]:
    review_root = repo / "data" / "dispatches" / "food-line" / "review"
    queue_path = review_root / "current-signal-review.json"
    proposal_path = review_root / "proposed-editions" / f"{DATE}.json"
    readiness_path = review_root / "release-readiness" / f"{DATE}.json"
    queue = {
        "schema_version": "food_line_current_signal_review_v1",
        "edition_date": DATE,
        "production_scope": "current_nonhistorical_only",
        "items": [
            {
                "proposed_rank": 1,
                "proposed_public_headline": "Central Illinois Food Bank says SNAP cuts are straining its ability to meet demand",
                "proposed_public_summary": "Common Dreams reports that the Central Illinois Food Bank says it cannot absorb the effects of federal SNAP cuts as food banks nationwide face rising demand.",
                "why_it_matters": "Common Dreams reported a SNAP benefit delay in Illinois, affecting SNAP households.",
                "uncertainty_note": "The available source record may not establish the full duration or scale of the pressure.",
                "source_url": "https://www.commondreams.org/news/food-banks-snap-cuts",
                "canonical_source_url": "https://www.commondreams.org/news/food-banks-snap-cuts",
                "editorial_status": "approve",
                "decision_audit": {
                    "decided_at": "2026-08-15T18:41:46.320762+00:00",
                    "decided_by": "willb",
                    "decision": "approve",
                },
                "review_item_id": "food-line-current-43c4d164b1aca2ffae3b785d",
                "source_finding_or_intake_id": "finding_dc027726f8e7a4770c3c651c",
                "source_artifact_path": "data/dispatches/food-line/agent-intake/2026-08-15/item.json",
                "source_published_at": "2026-08-14",
                "publisher": "Common Dreams",
                "confidence": "medium",
                "evidence_level": "news report",
                "duplicate_check": {"status": "not_published", "matched_records": []},
                "freshness_check": {"status": "current", "edition_date": DATE, "age_days": 1},
                "publication_eligible": False,
                "state": "IL",
                "location_scope": "state_local",
                "location_name": "Illinois",
                "pressure_type": "benefit disruption",
                "affected_groups": ["SNAP households"],
                "exact_supporting_passage": "Central Illinois Food Bank says SNAP cuts are straining its ability to meet demand.",
                "source_url": "https://www.commondreams.org/news/food-banks-snap-cuts",
                "source_published_at": "2026-08-14",
                "why_it_matters": "Common Dreams reported a SNAP benefit delay in Illinois, affecting SNAP households.",
            }
        ],
    }
    _write_json(queue_path, queue)
    proposal = {
        "schema_version": "food_line_proposed_edition_v1",
        "edition_date": DATE,
        "draft": True,
        "draft_status": "draft_approved_pending_publication",
        "published": False,
        "publication_eligible": False,
        "publication_approval": False,
        "selected_item_count": 1,
        "approved_item_count": 1,
        "pending_item_count": 0,
        "rejected_item_count": 0,
        "source_queue_path": "data/dispatches/food-line/review/current-signal-review.json",
        "source_queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
        "items": [
            {
                "rank": 1,
                "headline": "Central Illinois Food Bank says SNAP cuts are straining its ability to meet demand",
                "summary": "Common Dreams reports that the Central Illinois Food Bank says it cannot absorb the effects of federal SNAP cuts as food banks nationwide face rising demand.",
                "why_it_matters": "Common Dreams reported a SNAP benefit delay in Illinois, affecting SNAP households.",
                "uncertainty_note": "The available source record may not establish the full duration or scale of the pressure.",
                "source": "Common Dreams",
                "source_url": "https://www.commondreams.org/news/food-banks-snap-cuts",
                "source_published_at": "2026-08-14",
                "location_name": "Illinois",
                "state": "IL",
                "section": "Core Food Pressure Signals",
            }
        ],
    }
    _write_json(proposal_path, proposal)
    readiness = {
        "schema_version": "food_line_release_readiness_v1",
        "edition_date": DATE,
        "status": "approved_current_review_ready_for_source_generation",
        "approved_proposal_path": "data/dispatches/food-line/review/proposed-editions/2026-08-15.json",
        "approved_proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        "review_snapshot_path": "data/dispatches/food-line/review/current-signal-review.json",
        "review_snapshot_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
    }
    _write_json(readiness_path, readiness)
    return proposal_path, queue_path, readiness_path


@pytest.fixture()
def release_repos(tmp_path: Path) -> tuple[Path, Path]:
    source = _init_repo(tmp_path / "source", "agent/refine-care-line-signal-wire-public-rendering")
    pages = _init_repo(tmp_path / "bluefern-dispatches-pages", "gh-pages", empty_commit=True)
    _write_site(source)
    _write_release_inputs(source)
    _run_git(source, "add", "output/site/food-line/index.html", "output/site/food-line/archive.html", "output/site/food-line/rss.xml", "output/site/food-line/editions", "output/site/index.html")
    _run_git(source, "commit", "-m", "tracked food line output")
    return source, pages


def _bundle(source: Path) -> ApprovedProposalBundle:
    proposal_path = source / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{DATE}.json"
    queue_path = source / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    return ApprovedProposalBundle(
        proposal_path=proposal_path,
        proposal_sha256=hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        queue_path=queue_path,
        queue_sha256=hashlib.sha256(queue_path.read_bytes()).hexdigest(),
        proposal=proposal,
        queue=queue,
        matched_items=(),
        source_rows=(),
    )


def test_dry_run_full_materializes_only_validated_private_inputs_and_leaves_real_repos_unchanged(
    release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pages = release_repos
    source_head = _git_output(source, "rev-parse", "HEAD")
    pages_head = _git_output(pages, "rev-parse", "HEAD")
    proposal_path = source / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{DATE}.json"
    queue_path = source / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    readiness_path = source / "data" / "dispatches" / "food-line" / "review" / "release-readiness" / f"{DATE}.json"
    bundle = _bundle(source)
    captured: dict[str, Path] = {}

    def fake_load_approved_proposal(root: Path, proposal_input: Path | str, edition_date: str) -> ApprovedProposalBundle:
        assert edition_date == DATE
        assert Path(proposal_input) == proposal_path
        return bundle

    def fake_run_dispatch(working_source: Path, edition_date: str, **_: object) -> dict[str, object]:
        assert edition_date == DATE
        captured["proposal"] = working_source / proposal_path.relative_to(source)
        captured["queue"] = working_source / queue_path.relative_to(source)
        captured["readiness"] = working_source / readiness_path.relative_to(source)
        assert captured["proposal"].exists()
        assert captured["queue"].exists()
        assert captured["readiness"].exists()
        assert not (working_source / "data" / "dispatches" / "food-line" / "agent-intake").exists()
        release_manifest = working_source / "data" / "dispatches" / "food-line" / "release-manifest.json"
        release_manifest.parent.mkdir(parents=True, exist_ok=True)
        release_manifest.write_text(json.dumps({"schema_version": "food_line_release_manifest_v2"}), encoding="utf-8")
        return {
            "ok": True,
            "generator_source_commit": source_head,
            "public_signal_count": 1,
            "release_manifest_path": str(release_manifest),
            "errors": [],
        }

    def fake_sync_pages_from_source(**kwargs: object) -> dict[str, object]:
        assert kwargs["dry_run"] is True
        return {
            "ok": True,
            "additions": ["food-line/index.html", "index.html"],
            "modifications": ["food-line/archive.html"],
            "deletions": [],
            "planned_pages_paths": ["food-line/index.html", "food-line/archive.html", "index.html"],
        }

    monkeypatch.setattr(runner, "_validate_scope", lambda **kwargs: [])
    monkeypatch.setattr(runner, "load_approved_proposal", fake_load_approved_proposal)
    monkeypatch.setattr(runner, "run_food_line_dispatch", fake_run_dispatch)
    monkeypatch.setattr(runner, "sync_pages_from_source", fake_sync_pages_from_source)

    result = runner.run_publication(
        repo_root=source,
        pages_repo=pages,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        date=DATE,
        dry_run_full=True,
    )

    assert result["ok"] is True
    assert result["status"] == "dry_run_full_success"
    assert result["copied_private_inputs"] == [
        "data/dispatches/food-line/review/proposed-editions/2026-08-15.json",
        "data/dispatches/food-line/review/current-signal-review.json",
        "data/dispatches/food-line/review/release-readiness/2026-08-15.json",
    ]
    assert result["publication_report"]["planned_pages_paths"] == ["food-line/index.html", "food-line/archive.html", "index.html"]
    assert result["temp_workspace_removed"] is True
    assert _git_output(source, "rev-parse", "HEAD") == source_head
    assert _git_output(pages, "rev-parse", "HEAD") == pages_head
    assert _git_output(pages, "status", "--short") == ""
    assert source.joinpath("data/dispatches/food-line/review/proposed-editions", f"{DATE}.json").exists()
    assert source.joinpath("data/dispatches/food-line/review/current-signal-review.json").exists()
    assert source.joinpath("data/dispatches/food-line/review/release-readiness", f"{DATE}.json").exists()
    assert "root homepage" in (source / "output" / "site" / "index.html").read_text(encoding="utf-8")


def test_dry_run_full_fails_closed_when_validated_private_input_disappears(
    release_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pages = release_repos
    source_head = _git_output(source, "rev-parse", "HEAD")
    pages_head = _git_output(pages, "rev-parse", "HEAD")
    proposal_path = source / "data" / "dispatches" / "food-line" / "review" / "proposed-editions" / f"{DATE}.json"
    queue_path = source / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    readiness_path = source / "data" / "dispatches" / "food-line" / "review" / "release-readiness" / f"{DATE}.json"
    bundle = _bundle(source)

    def fake_load_approved_proposal(root: Path, proposal_input: Path | str, edition_date: str) -> ApprovedProposalBundle:
        assert edition_date == DATE
        return bundle

    def fake_clone_repo(*args: object, **kwargs: object) -> None:
        return None

    def fake_copy_validated_private_release_inputs_for_dry_run(**kwargs: object) -> list[str]:
        raise runner.PublicationRunnerError(f"validated private release input is missing: {readiness_path}")

    monkeypatch.setattr(runner, "load_approved_proposal", fake_load_approved_proposal)
    monkeypatch.setattr(runner, "_clone_repo", fake_clone_repo)
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    monkeypatch.setattr(runner, "sync_pages_from_source", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    monkeypatch.setattr(runner, "_copy_validated_private_release_inputs_for_dry_run", fake_copy_validated_private_release_inputs_for_dry_run)

    result = runner.run_publication(
        repo_root=source,
        pages_repo=pages,
        source_branch="agent/refine-care-line-signal-wire-public-rendering",
        pages_branch="gh-pages",
        date=DATE,
        dry_run_full=True,
    )

    assert result["ok"] is False
    assert result["status"] == "dry_run_full_failed"
    assert "validated private release input is missing" in "\n".join(result["errors"])
    assert _git_output(source, "rev-parse", "HEAD") == source_head
    assert _git_output(pages, "rev-parse", "HEAD") == pages_head
