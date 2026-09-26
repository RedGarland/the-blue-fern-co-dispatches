from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.ice_current_review import (
    IceReviewError,
    apply_decision,
    load_queue,
    payload_sha256,
)


def _fixture(tmp_path: Path, *, corroborated: bool = True, eligible: bool = True) -> tuple[Path, str]:
    root = tmp_path
    run_dir = root / "data/dispatches/ice/monitor/runs/2026-09-25/run-1"
    run_dir.mkdir(parents=True)
    event = {
        "event_id": "ice-event-1",
        "event_date": "2026-09-25",
        "primary_category": "detention",
        "severity": "high",
        "location": {"state_or_territory": "WA"},
        "sources": [{"source_url": "https://example.org/a", "publisher": "Example", "tier": 1}],
    }
    (run_dir / "canonical_events.json").write_text(json.dumps([event]), encoding="utf-8")
    (run_dir / "raw_candidates.json").write_text("[]\n", encoding="utf-8")
    (run_dir / "provider_health.json").write_text("{}\n", encoding="utf-8")

    queue_path = root / "data/dispatches/ice/monitor/review_queue.json"
    queue_path.parent.mkdir(parents=True)
    queue = {
        "schema_version": "bluefern.ice.monitor.review_queue.v1",
        "items": [
            {
                "canonical_event_id": "ice-event-1",
                "event_fingerprint": "fp-1",
                "first_seen": "2026-09-25T20:00:00Z",
                "last_seen": "2026-09-25T20:00:00Z",
                "last_run_id": "run-1",
                "event_date": "2026-09-25",
                "published_at": "2026-09-25T19:00:00Z",
                "modified_at": None,
                "category": "detention",
                "severity": "high",
                "geography": {"state_or_territory": "WA"},
                "source_set": ["https://example.org/a"],
                "source_tiers": [1],
                "corroboration_status": "corroborated" if corroborated else "needs_corroboration",
                "currentness": "current_publication",
                "map_readiness": "ready",
                "review_status": "NEW",
                "editorial_eligibility_candidate": eligible,
                "evidence_references": {
                    "run_dir": "data/dispatches/ice/monitor/runs/2026-09-25/run-1",
                    "canonical_events": "data/dispatches/ice/monitor/runs/2026-09-25/run-1/canonical_events.json",
                    "raw_candidates": "data/dispatches/ice/monitor/runs/2026-09-25/run-1/raw_candidates.json",
                    "provider_health": "data/dispatches/ice/monitor/runs/2026-09-25/run-1/provider_health.json",
                },
                "relationship_to_previous": "new_event",
                "relationship_reasons": ["new"],
            }
        ],
    }
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    return queue_path, payload_sha256(queue)


def test_review_approval_creates_private_release_candidate_without_public_authority(tmp_path: Path) -> None:
    queue_path, queue_sha = _fixture(tmp_path)

    result = apply_decision(
        tmp_path,
        queue_path,
        event_fingerprint="fp-1",
        decision="APPROVE_FOR_RELEASE_REVIEW",
        decided_by="reviewer",
        rationale="Verified high-severity event with traceable official source.",
        expected_queue_sha256=queue_sha,
        decided_at="2026-09-25T21:00:00Z",
    )

    assert result["status"] == "decision_recorded"
    assert result["review_status"] == "REVIEWED"
    assert result["publication_authorized"] is False
    queue = load_queue(queue_path)
    item = queue["items"][0]
    assert item["review_status"] == "REVIEWED"
    assert item["reviewed_by"] == "reviewer"
    release = json.loads((tmp_path / item["release_candidate_ref"]).read_text(encoding="utf-8"))
    assert release["release_review_ready"] is True
    assert release["publication_eligible"] is False
    assert release["publication_approval"] is False
    assert release["publication_authorized"] is False
    assert release["pages_authorized"] is False
    audit = json.loads((tmp_path / result["audit_ref"]).read_text(encoding="utf-8"))
    assert audit["publication_authorized"] is False
    assert audit["release_candidate_created"] is True


def test_stale_queue_hash_fails_closed(tmp_path: Path) -> None:
    queue_path, _ = _fixture(tmp_path)
    with pytest.raises(IceReviewError, match="changed since inspection"):
        apply_decision(
            tmp_path,
            queue_path,
            event_fingerprint="fp-1",
            decision="REJECT",
            decided_by="reviewer",
            rationale="Not material.",
            expected_queue_sha256="0" * 64,
        )


def test_release_review_requires_corroboration_and_editorial_eligibility(tmp_path: Path) -> None:
    queue_path, queue_sha = _fixture(tmp_path, corroborated=False)
    with pytest.raises(IceReviewError, match="requires corroboration"):
        apply_decision(
            tmp_path,
            queue_path,
            event_fingerprint="fp-1",
            decision="APPROVE_FOR_RELEASE_REVIEW",
            decided_by="reviewer",
            rationale="Attempted approval.",
            expected_queue_sha256=queue_sha,
        )

    other = tmp_path / "other"
    queue_path, queue_sha = _fixture(other, eligible=False)
    with pytest.raises(IceReviewError, match="not an editorial eligibility candidate"):
        apply_decision(
            other,
            queue_path,
            event_fingerprint="fp-1",
            decision="APPROVE_FOR_RELEASE_REVIEW",
            decided_by="reviewer",
            rationale="Attempted approval.",
            expected_queue_sha256=queue_sha,
        )


def test_reject_is_terminal_but_non_public(tmp_path: Path) -> None:
    queue_path, queue_sha = _fixture(tmp_path)
    result = apply_decision(
        tmp_path,
        queue_path,
        event_fingerprint="fp-1",
        decision="REJECT",
        decided_by="reviewer",
        rationale="Duplicate or insufficiently material.",
        expected_queue_sha256=queue_sha,
        decided_at="2026-09-25T21:00:00Z",
    )
    assert result["review_status"] == "REJECTED"
    assert result["release_candidate_ref"] is None
    assert result["publication_authorized"] is False
