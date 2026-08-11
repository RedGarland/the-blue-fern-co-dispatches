from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import run_food_line_dispatch as runner


DATE = "2026-08-10"
SOURCE_URL = "https://example.com/food-line-check-only"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_valid_candidate(repo_root: Path) -> None:
    review_root = repo_root / "data" / "dispatches" / "food-line" / "review"
    queue_path = review_root / "current-signal-review.json"
    proposal_path = review_root / "proposed-editions" / f"{DATE}.json"
    readiness_path = review_root / "release-readiness" / f"{DATE}.json"
    item = {
        "affected_groups": ["local households"],
        "canonical_source_url": SOURCE_URL,
        "confidence": "high",
        "decision_audit": {
            "decided_at": "2026-08-10T10:00:00+00:00",
            "decided_by": "Operator",
            "decision": "approve",
        },
        "duplicate_check": {"status": "not_published", "matched_records": []},
        "editorial_status": "approve",
        "evidence_level": "direct_reporting",
        "exact_supporting_passage": "A pantry closed after a service disruption.",
        "freshness_check": {"status": "current", "edition_date": DATE, "age_days": 1},
        "location_scope": "US",
        "pressure_type": "service_reduction",
        "proposed_public_headline": "Pantry closes after service disruption",
        "proposed_public_summary": "A pantry closed after a service disruption.",
        "proposed_rank": 1,
        "publication_eligible": False,
        "publisher": "Example Publisher",
        "review_item_id": "review-item-1",
        "source_artifact_path": "data/dispatches/food-line/agent-intake/2026-08-10/item.json",
        "source_finding_or_intake_id": "finding-1",
        "source_published_at": "2026-08-10T09:00:00+00:00",
        "source_url": SOURCE_URL,
        "state": "Washington",
        "uncertainty_note": "Capacity details remain unclear.",
        "why_it_matters": "It affects local access.",
    }
    queue = {
        "schema_version": "food_line_current_signal_review_v1",
        "edition_date": DATE,
        "production_scope": "current_nonhistorical_only",
        "items": [item],
    }
    _write_json(queue_path, queue)
    proposal = {
        "schema_version": "food_line_proposed_edition_v1",
        "edition_date": DATE,
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
                "headline": item["proposed_public_headline"],
                "summary": item["proposed_public_summary"],
                "why_it_matters": item["why_it_matters"],
                "uncertainty_note": item["uncertainty_note"],
                "source": item["publisher"],
                "source_url": item["source_url"],
                "source_published_at": item["source_published_at"],
                "location_name": "Local",
                "state": item["state"],
                "section": "Core Food Pressure Signals",
            }
        ],
    }
    _write_json(proposal_path, proposal)
    readiness = {
        "schema_version": "food_line_release_readiness_v1",
        "edition_date": DATE,
        "status": "approved_current_review_ready_for_source_generation",
        "approved_proposal_path": "data/dispatches/food-line/review/proposed-editions/2026-08-10.json",
        "approved_proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
        "review_snapshot_path": "data/dispatches/food-line/review/signal-reviews/2026-08-10.json",
        "review_snapshot_sha256": "snapshot-sha",
    }
    _write_json(readiness_path, readiness)


def test_check_only_no_release_candidate_succeeds_without_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    code = runner.main(["--date", DATE, "--check-only"])
    assert code == 0


def test_check_only_fails_closed_when_readiness_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_valid_candidate(tmp_path)
    (tmp_path / "data" / "dispatches" / "food-line" / "review" / "release-readiness" / f"{DATE}.json").unlink()
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    code = runner.main(["--date", DATE, "--check-only"])
    assert code == 1


def test_check_only_succeeds_with_valid_release_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_valid_candidate(tmp_path)
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    code = runner.main(["--date", DATE, "--check-only"])
    assert code == 0


def test_check_only_fails_closed_on_invalid_readiness_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_valid_candidate(tmp_path)
    readiness_path = tmp_path / "data" / "dispatches" / "food-line" / "review" / "release-readiness" / f"{DATE}.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["schema_version"] = "unexpected"
    _write_json(readiness_path, readiness)
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    code = runner.main(["--date", DATE, "--check-only"])
    assert code == 1


def test_check_only_never_publishes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_valid_candidate(tmp_path)
    monkeypatch.setattr(runner, "run_food_line_dispatch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")))
    code = runner.main(["--date", DATE, "--check-only"])
    assert code == 0
    assert not (tmp_path / "output" / "site").exists()
