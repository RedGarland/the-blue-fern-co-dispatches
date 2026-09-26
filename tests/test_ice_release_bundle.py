from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.ice_current_review import apply_decision, payload_sha256
from bluefern_dispatches.ice_dispatch import event_to_dict, generate_ice_edition_candidate, normalize_ice_candidate
from bluefern_dispatches.ice_release_bundle import IceReleaseBundleError, build_release_bundle


NOW = "2026-09-25T20:00:00Z"


def _setup(tmp_path: Path) -> tuple[Path, str, str]:
    event = event_to_dict(
        normalize_ice_candidate(
            {
                "event_date": "2026-09-25",
                "event_type": "ICE detention facility oversight failure",
                "primary_category": "detention",
                "severity": "high",
                "status": "reported",
                "location": {
                    "state_or_territory": "WA",
                    "city": "Tacoma",
                    "location_precision": "city",
                    "geography_source": "official notice",
                    "geography_provenance": "official notice",
                },
                "impact": {"detained_count": 42},
                "agencies": {"ice": True},
                "sources": [
                    {
                        "source_url": "https://official.example/ice/event-1",
                        "publisher": "Official Source",
                        "source_type": "official",
                        "tier": 1,
                        "published_at": "2026-09-25T18:00:00Z",
                        "retrieved_at": NOW,
                        "exact_supporting_passage": "Official source documents a material ICE detention oversight event.",
                    }
                ],
                "verification_status": "source_traceable",
                "corroboration_count": 1,
                "currentness_status": "current",
                "currentness_confidence": "high",
            },
            observed_at=NOW,
        )
    )
    fingerprint = event["lineage"]["fingerprint"]
    event_id = event["event_id"]
    run_dir = tmp_path / "data/dispatches/ice/monitor/runs/2026-09-25/run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "canonical_events.json").write_text(json.dumps([event]), encoding="utf-8")
    (run_dir / "raw_candidates.json").write_text("[]\n", encoding="utf-8")
    (run_dir / "provider_health.json").write_text("{}\n", encoding="utf-8")

    queue = {
        "schema_version": "bluefern.ice.monitor.review_queue.v1",
        "items": [
            {
                "canonical_event_id": event_id,
                "event_fingerprint": fingerprint,
                "first_seen": NOW,
                "last_seen": NOW,
                "last_run_id": "run-1",
                "event_date": "2026-09-25",
                "published_at": "2026-09-25T18:00:00Z",
                "modified_at": None,
                "category": "detention",
                "severity": "high",
                "geography": {"state_or_territory": "WA", "city": "Tacoma"},
                "source_set": ["https://official.example/ice/event-1"],
                "source_tiers": [1],
                "corroboration_status": "corroborated",
                "currentness": "current",
                "map_readiness": "MAPPABLE_CITY",
                "review_status": "NEW",
                "editorial_eligibility_candidate": True,
                "evidence_references": {
                    "run_dir": "data/dispatches/ice/monitor/runs/2026-09-25/run-1",
                    "canonical_events": "data/dispatches/ice/monitor/runs/2026-09-25/run-1/canonical_events.json",
                    "raw_candidates": "data/dispatches/ice/monitor/runs/2026-09-25/run-1/raw_candidates.json",
                    "provider_health": "data/dispatches/ice/monitor/runs/2026-09-25/run-1/provider_health.json",
                },
                "relationship_to_previous": "new_event",
                "relationship_reasons": ["not previously seen by monitor"],
            }
        ],
    }
    queue_path = tmp_path / "data/dispatches/ice/monitor/review_queue.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(queue), encoding="utf-8")
    apply_decision(
        tmp_path,
        queue_path,
        event_fingerprint=fingerprint,
        decision="APPROVE_FOR_RELEASE_REVIEW",
        decided_by="reviewer",
        rationale="Verified high-severity event with traceable official evidence.",
        expected_queue_sha256=payload_sha256(queue),
        decided_at="2026-09-25T21:00:00Z",
    )
    approved_queue = json.loads(queue_path.read_text(encoding="utf-8"))
    return queue_path, payload_sha256(approved_queue), fingerprint


def test_reviewed_bundle_feeds_existing_nonpublic_edition_candidate(tmp_path: Path) -> None:
    queue_path, queue_sha, fingerprint = _setup(tmp_path)

    bundle = build_release_bundle(
        tmp_path,
        queue_path,
        event_fingerprints=[fingerprint],
        edition_date="2026-09-25",
        expected_queue_sha256=queue_sha,
        generated_at="2026-09-25T21:05:00Z",
    )

    assert bundle["status"] == "bundle_created"
    assert bundle["publication_authorized"] is False
    reviewed_path = tmp_path / bundle["reviewed_events_ref"]
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    assert len(reviewed) == 1
    assert reviewed[0]["review_status"] == "QUALIFIES"

    edition = generate_ice_edition_candidate(
        reviewed,
        edition_date="2026-09-25",
        collection_state="healthy",
        output_dir=tmp_path / "private-stage",
        generated_at="2026-09-25T21:06:00Z",
    )
    assert edition["manifest"]["editorial_decision"] == "ELIGIBLE_FOR_EDITION"
    assert edition["manifest"]["accepted_event_count"] == 1
    assert edition["public_side_effects"] is False
    assert (tmp_path / "private-stage/index.html").is_file()


def test_bundle_fails_if_queue_changed_after_selection(tmp_path: Path) -> None:
    queue_path, _, fingerprint = _setup(tmp_path)
    with pytest.raises(IceReleaseBundleError, match="changed since bundle selection"):
        build_release_bundle(
            tmp_path,
            queue_path,
            event_fingerprints=[fingerprint],
            edition_date="2026-09-25",
            expected_queue_sha256="0" * 64,
        )


def test_bundle_fails_if_canonical_event_changed_after_review(tmp_path: Path) -> None:
    queue_path, queue_sha, fingerprint = _setup(tmp_path)
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    candidate_ref = queue["items"][0]["release_candidate_ref"]
    candidate = json.loads((tmp_path / candidate_ref).read_text(encoding="utf-8"))
    canonical_path = tmp_path / candidate["evidence_references"]["canonical_events"]
    events = json.loads(canonical_path.read_text(encoding="utf-8"))
    events[0]["status"] = "materially changed after review"
    canonical_path.write_text(json.dumps(events), encoding="utf-8")

    with pytest.raises(IceReleaseBundleError, match="changed since review approval"):
        build_release_bundle(
            tmp_path,
            queue_path,
            event_fingerprints=[fingerprint],
            edition_date="2026-09-25",
            expected_queue_sha256=queue_sha,
        )
