from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.ice_public_pilot_gate import (
    IcePublicPilotGateError,
    validate_ice_public_pilot_gate,
)


SOURCE_HEAD = "b2da038651892d970b028add261e8736f1a43796"


def _write(path: Path, payload: dict | list | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _repo(tmp_path: Path, *, candidate_overrides: dict | None = None) -> tuple[Path, Path, Path]:
    root = tmp_path
    candidate = {
        "schema_version": "bluefern.ice.monitor.release_candidate.v1",
        "canonical_event_id": "ice-event-1",
        "event_fingerprint": "ice-event-1",
        "review_decision_id": "decision-1",
        "reviewed_at": "2026-10-06T18:00:00Z",
        "reviewed_by": "reviewer",
        "category": "detention",
        "severity": "high",
        "event_date": "2026-10-06",
        "geography": {"state_or_territory": "WA"},
        "source_set": ["https://official.example/ice/event-1"],
        "source_tiers": [1],
        "corroboration_status": "corroborated",
        "relationship_to_previous": "new_event",
        "relationship_reasons": [],
        "review_queue_last_seen": "2026-10-06T18:00:00Z",
        "canonical_event_snapshot_sha256": "a" * 64,
        "evidence_references": {
            "canonical_events": "data/dispatches/ice/monitor/runs/2026-10-06/run/canonical_events.json"
        },
        "release_review_ready": True,
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
    }
    candidate.update(candidate_overrides or {})
    candidate_ref = "data/dispatches/ice/monitor/release-candidates/ice-event-1.json"
    _write(root / candidate_ref, candidate)

    bundle = {
        "schema_version": "bluefern.ice.monitor.release_bundle.v1",
        "bundle_id": "ice-release-bundle-test",
        "edition_date": "2026-10-06",
        "generated_at": "2026-10-06T18:05:00Z",
        "queue_sha256": "b" * 64,
        "event_count": 1,
        "event_fingerprints": ["ice-event-1"],
        "review_decision_ids": ["decision-1"],
        "release_candidate_refs": [candidate_ref],
        "reviewed_events_sha256": "c" * 64,
        "edition_candidate_input_ready": True,
        "publication_eligible": False,
        "publication_approval": False,
        "publication_authorized": False,
        "pages_authorized": False,
        "social_authorized": False,
    }
    bundle_path = root / "data/dispatches/ice/monitor/release-bundles/2026-10-06/ice-release-bundle-test/manifest.json"
    _write(bundle_path, bundle)

    staging = root / "private-stage"
    for name in ("index.html", "archive.html", "rss.xml", "archive_entries.json"):
        _write(staging / "ice" / name, "{}" if name.endswith(".json") else "<html></html>")
    for name in ("index.html", "manifest.json", "map_payload.json"):
        _write(staging / "ice" / "editions" / "2026-10-06" / name, "{}" if name.endswith(".json") else "<html></html>")

    approval = {
        "schema_version": "bluefern.ice.public_pilot_approval.v1",
        "approval_type": "ice_public_pilot",
        "dispatch": "ice",
        "bundle_id": "ice-release-bundle-test",
        "edition_date": "2026-10-06",
        "queue_sha256": "b" * 64,
        "reviewed_events_sha256": "c" * 64,
        "event_fingerprints": ["ice-event-1"],
        "source_head": SOURCE_HEAD,
        "publication_authorized": True,
        "pages_authorized": True,
        "social_authorized": False,
    }
    approval_path = root / "approvals/ice/ice-public-pilot-test-approval.json"
    _write(approval_path, approval)
    return bundle_path, staging, approval_path


def test_public_pilot_requires_explicit_human_approval_artifact(tmp_path: Path) -> None:
    bundle_path, staging, _approval_path = _repo(tmp_path)

    with pytest.raises(IcePublicPilotGateError, match="missing explicit ICE public pilot approval artifact"):
        validate_ice_public_pilot_gate(
            repo_root=tmp_path,
            bundle_manifest_path=bundle_path,
            staging_root=staging,
            approval_path=None,
            source_head=SOURCE_HEAD,
        )


def test_gate_accepts_reviewed_corroborated_bundle_with_bounded_scope(tmp_path: Path) -> None:
    bundle_path, staging, approval_path = _repo(tmp_path)

    result = validate_ice_public_pilot_gate(
        repo_root=tmp_path,
        bundle_manifest_path=bundle_path,
        staging_root=staging,
        approval_path=approval_path,
        source_head=SOURCE_HEAD,
    )

    assert result.ok is True
    assert result.publication_authorized is True
    assert result.pages_authorized is True
    assert result.social_authorized is False
    assert set(result.allowed_public_paths) == {
        "ice/archive.html",
        "ice/archive_entries.json",
        "ice/editions/2026-10-06/index.html",
        "ice/editions/2026-10-06/manifest.json",
        "ice/editions/2026-10-06/map_payload.json",
        "ice/index.html",
        "ice/rss.xml",
    }


def test_bundle_items_must_not_need_corroboration(tmp_path: Path) -> None:
    bundle_path, staging, approval_path = _repo(tmp_path, candidate_overrides={"corroboration_status": "needs_corroboration"})

    with pytest.raises(IcePublicPilotGateError, match="still needs corroboration"):
        validate_ice_public_pilot_gate(
            repo_root=tmp_path,
            bundle_manifest_path=bundle_path,
            staging_root=staging,
            approval_path=approval_path,
            source_head=SOURCE_HEAD,
        )


def test_bundle_items_must_be_release_reviewed(tmp_path: Path) -> None:
    bundle_path, staging, approval_path = _repo(tmp_path, candidate_overrides={"release_review_ready": False})

    with pytest.raises(IcePublicPilotGateError, match="not release-review ready"):
        validate_ice_public_pilot_gate(
            repo_root=tmp_path,
            bundle_manifest_path=bundle_path,
            staging_root=staging,
            approval_path=approval_path,
            source_head=SOURCE_HEAD,
        )


def test_public_output_scope_is_bounded_to_ice_pilot_paths(tmp_path: Path) -> None:
    bundle_path, staging, approval_path = _repo(tmp_path)
    _write(staging / "ice" / "unexpected.html", "<html></html>")

    with pytest.raises(IcePublicPilotGateError, match="scope is not bounded"):
        validate_ice_public_pilot_gate(
            repo_root=tmp_path,
            bundle_manifest_path=bundle_path,
            staging_root=staging,
            approval_path=approval_path,
            source_head=SOURCE_HEAD,
        )


def test_social_post_requires_separate_social_authorization(tmp_path: Path) -> None:
    bundle_path, staging, approval_path = _repo(tmp_path)

    with pytest.raises(IcePublicPilotGateError, match="separate social_authorized=true"):
        validate_ice_public_pilot_gate(
            repo_root=tmp_path,
            bundle_manifest_path=bundle_path,
            staging_root=staging,
            approval_path=approval_path,
            source_head=SOURCE_HEAD,
            social_requested=True,
        )

