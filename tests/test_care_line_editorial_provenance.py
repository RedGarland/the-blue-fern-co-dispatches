from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.care_line_editorial_provenance import (
    APPROVED_DECISIONS,
    PROPOSAL_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    REVIEW_SNAPSHOT_ROOT,
    approved_items,
    build_private_draft_artifacts,
    build_proposed_edition,
    build_release_readiness_record,
    determine_readiness,
    load_validated_review_snapshot,
    payload_sha256,
    review_snapshot_path,
    validate_review_payload,
    write_json_atomic,
    write_review_snapshot,
)


def _review_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "candidate_id": "care-line-candidate-001",
        "cluster_id": "care-line-cluster-001",
        "facility": "Miles Hospital",
        "facility_name": "Miles Hospital",
        "jurisdiction": "Maine",
        "source_name": "NPR",
        "source_title": "One Maine community's fight to save a birthing center",
        "source_url": "https://www.npr.org/2026/08/04/nx-s1-5910852/maine-birthing-center-fight",
        "source_date": "2026-08-04",
        "source_type": "article",
        "authority_level": "secondary",
        "automated_priority": "CRITICAL",
        "review_decision": "APPROVE_WITH_CORRECTION",
        "reviewer_rationale": "The source supports a bounded proposed labor-and-delivery closure signal, not a full hospital closure.",
        "approved_public_claim": "NPR reports that a coalition in mid-coast Maine is fighting a proposed closure of Miles Hospital’s labor and delivery center.",
        "bounded_public_summary": "Mid-coast Maine faces a proposed Miles Hospital labor-and-delivery closure, according to NPR.",
        "approved_event_type": "proposed_service_line_closure",
        "approved_service_line": "labor_and_delivery",
        "approved_access_consequence": "proposed_loss_of_local_labor_and_delivery_access",
        "approved_geography": "mid-coast Maine",
        "exact_supporting_passage": "In mid-coast Maine a grassroots coalition is fighting to prevent the proposed closure of Miles Hospital's labor and delivery center.",
        "verification_state": "source_opened_and_bounded",
        "corrections_applied": ["event_type", "access_consequence"],
        "exclusion_reason": "",
        "review_date": "2026-08-05",
        "reviewer_identity": "codex_phase_e",
        "source_artifact_lineage": "care-line-phase-d3/currentness-qualified-candidates.csv",
        "evidence_level": "article_excerpt",
        "notes": "Effective closure date not yet final in source.",
        "lineage_note": "Phase D.3 qualified candidate review.",
        "role_in_edition": "core_access_signal",
    }
    item.update(overrides)
    return item


def _review_payload(items: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "edition_date": "2026-08-05",
        "reviewed_at": "2026-08-05T18:00:00Z",
        "items": list(items or []),
    }


def test_automated_candidate_is_not_auto_approved() -> None:
    payload = _review_payload(
        [
            _review_item(
                review_decision="HOLD_FOR_VERIFICATION",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
                exclusion_reason="needs more source verification",
            ),
            _review_item(
                candidate_id="care-line-candidate-002",
                review_decision="EXCLUDE",
                source_url="https://www.texastribune.org/2026/07/23/texas-midwife-ken-paxton-appeals-court-ruling/",
                source_name="Texas Tribune",
                source_title="Appeals court overturns order closing Texas midwife clinics accused of illegal abortions",
                source_date="2026-07-23",
                reviewer_rationale="The story does not establish current clinic operations after the appeal decision.",
                exclusion_reason="current access consequence not confirmed",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
            ),
            _review_item(
                candidate_id="care-line-candidate-003",
                review_decision="CONTEXT_ONLY",
                source_url="https://virginiamercury.com/2026/07/23/va-hospitals-predict-31b-reduction-in-states-medicaid-funding-if-proposed-cms-rule-passes/",
                source_name="Virginia Mercury",
                source_title="Va. hospitals predict $31B reduction in state’s Medicaid funding if proposed CMS rule passes",
                source_date="2026-07-23",
                reviewer_rationale="The source is predictive policy context, not a bounded current access change.",
                exclusion_reason="policy proposal context only",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
            ),
        ]
    )
    readiness = determine_readiness(payload)
    assert readiness["edition_mode"] == "no_current_update"
    assert readiness["verdict"] == "NOT_READY_PROVENANCE_GAP"
    assert approved_items(payload) == []


def test_review_decisions_serialize_and_validate() -> None:
    payload = _review_payload([_review_item()])
    meta = validate_review_payload(payload)
    assert meta["approved_count"] == 1
    assert payload_sha256(payload) == payload_sha256(json.loads(json.dumps(payload)))
    assert _review_item()["review_decision"] in APPROVED_DECISIONS


def test_snapshot_hashing_and_fail_closed(tmp_path: Path) -> None:
    payload = _review_payload([_review_item()])
    info = write_review_snapshot(tmp_path, payload)
    loaded = load_validated_review_snapshot(tmp_path, snapshot_path=info["snapshot_path"], snapshot_sha256=info["snapshot_sha256"])
    assert loaded["review_payload"]["schema_version"] == REVIEW_SCHEMA_VERSION

    with pytest.raises(ValueError, match="unable to read review snapshot"):
        load_validated_review_snapshot(tmp_path, snapshot_path=review_snapshot_path("2026-08-04").as_posix(), snapshot_sha256="x")

    snapshot_file = tmp_path / info["snapshot_path"]
    tampered = json.loads(snapshot_file.read_text(encoding="utf-8"))
    tampered["edition_date"] = "2026-08-04"
    write_json_atomic(snapshot_file, tampered)
    with pytest.raises(ValueError, match="review snapshot SHA-256 is stale"):
        load_validated_review_snapshot(tmp_path, snapshot_path=info["snapshot_path"], snapshot_sha256=info["snapshot_sha256"])


def test_proposal_binds_snapshot_and_keeps_publication_false(tmp_path: Path) -> None:
    payload = _review_payload([_review_item()])
    snapshot = write_review_snapshot(tmp_path, payload)
    proposal = build_proposed_edition(
        payload,
        snapshot_path=snapshot["snapshot_path"],
        snapshot_sha256=snapshot["snapshot_sha256"],
        proposal_created_at="2026-08-05T18:10:00Z",
    )
    assert proposal["schema_version"] == PROPOSAL_SCHEMA_VERSION
    assert proposal["review_snapshot_path"] == snapshot["snapshot_path"]
    assert proposal["review_snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert proposal["publication_authorization_status"] is False
    assert proposal["publication_datetime"] is None
    assert proposal["edition_mode"] == "current_update"


def test_private_draft_includes_only_approved_records_and_is_deterministic(tmp_path: Path) -> None:
    payload = _review_payload(
        [
            _review_item(),
            _review_item(
                candidate_id="care-line-candidate-002",
                review_decision="HOLD_FOR_VERIFICATION",
                source_url="https://www.texastribune.org/2026/07/23/texas-midwife-ken-paxton-appeals-court-ruling/",
                source_name="Texas Tribune",
                source_title="Appeals court overturns order closing Texas midwife clinics accused of illegal abortions",
                source_date="2026-07-23",
                reviewer_rationale="Current operations are not confirmed.",
                exclusion_reason="",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
            ),
        ]
    )
    snapshot = write_review_snapshot(tmp_path, payload)
    proposal = build_proposed_edition(payload, snapshot_path=snapshot["snapshot_path"], snapshot_sha256=snapshot["snapshot_sha256"], proposal_created_at="2026-08-05T18:20:00Z")

    out = tmp_path / "private-draft"
    first = build_private_draft_artifacts(tmp_path, payload=payload, proposal=proposal, output_dir=out)
    first_manifest = (out / "draft-manifest.json").read_text(encoding="utf-8")
    second = build_private_draft_artifacts(tmp_path, payload=payload, proposal=proposal, output_dir=out)
    second_manifest = (out / "draft-manifest.json").read_text(encoding="utf-8")

    assert first == second
    assert first_manifest == second_manifest
    assert (out / "index.html").exists()
    assert (out / "source_table.html").exists()
    assert (out / "claim_ledger.html").exists()
    assert "Texas Tribune" not in (out / "claim_ledger.html").read_text(encoding="utf-8")
    assert "output/site" not in (out / "index.html").read_text(encoding="utf-8")
    assert not (tmp_path / "output" / "site").exists()


def test_no_current_update_compatibility_and_no_pages_side_effects(tmp_path: Path) -> None:
    payload = _review_payload(
        [
            _review_item(
                review_decision="EXCLUDE",
                reviewer_rationale="Current access change is not established.",
                exclusion_reason="current access consequence not confirmed",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
            ),
            _review_item(
                candidate_id="care-line-candidate-002",
                source_url="https://www.texastribune.org/2026/07/23/texas-midwife-ken-paxton-appeals-court-ruling/",
                source_name="Texas Tribune",
                source_title="Appeals court overturns order closing Texas midwife clinics accused of illegal abortions",
                source_date="2026-07-23",
                review_decision="EXCLUDE",
                reviewer_rationale="The article does not confirm clinics reopened.",
                exclusion_reason="current access consequence not confirmed",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
            ),
            _review_item(
                candidate_id="care-line-candidate-003",
                source_url="https://virginiamercury.com/2026/07/23/va-hospitals-predict-31b-reduction-in-states-medicaid-funding-if-proposed-cms-rule-passes/",
                source_name="Virginia Mercury",
                source_title="Va. hospitals predict $31B reduction in state’s Medicaid funding if proposed CMS rule passes",
                source_date="2026-07-23",
                review_decision="CONTEXT_ONLY",
                reviewer_rationale="Predictive policy context only.",
                exclusion_reason="policy proposal context only",
                approved_public_claim="",
                bounded_public_summary="",
                approved_event_type="",
                approved_service_line="",
                approved_access_consequence="",
                approved_geography="",
                exact_supporting_passage="",
            ),
        ]
    )
    snapshot = write_review_snapshot(tmp_path, payload)
    proposal = build_proposed_edition(payload, snapshot_path=snapshot["snapshot_path"], snapshot_sha256=snapshot["snapshot_sha256"], proposal_created_at="2026-08-05T18:25:00Z")
    assert proposal["edition_mode"] == "no_current_update"
    assert proposal["readiness_verdict"] == "NO_CURRENT_UPDATE_SUPPORTED"

    out = tmp_path / "private-draft"
    counts = build_private_draft_artifacts(tmp_path, payload=payload, proposal=proposal, output_dir=out)
    assert counts["approved_count"] == 0
    assert "No approved current signal" in (out / "index.html").read_text(encoding="utf-8")
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_release_readiness_record_tracks_private_hashes(tmp_path: Path) -> None:
    payload = _review_payload([_review_item()])
    snapshot = write_review_snapshot(tmp_path, payload)
    proposal = build_proposed_edition(payload, snapshot_path=snapshot["snapshot_path"], snapshot_sha256=snapshot["snapshot_sha256"], proposal_created_at="2026-08-05T18:30:00Z")
    out = tmp_path / "private-draft"
    build_private_draft_artifacts(tmp_path, payload=payload, proposal=proposal, output_dir=out)
    proposal_path = tmp_path / "proposal.json"
    write_json_atomic(proposal_path, proposal)
    readiness = build_release_readiness_record(
        proposal=proposal,
        proposal_path_value="data/dispatches/care-line/review/proposed-editions/2026-08-05.json",
        proposal_sha256=payload_sha256(proposal),
        snapshot_path_value=snapshot["snapshot_path"],
        snapshot_sha256=snapshot["snapshot_sha256"],
        draft_dir=out,
        validation_results={"all_claims_traceable": True, "private_path_exclusion": True},
        blocking_issues=["human editorial approval required"],
    )
    assert readiness["publication_authorization"] is False
    assert readiness["pages_synchronization_status"] == "not_synced"
    assert "index.html" in readiness["private_draft_artifact_hashes"]


def test_july_24_signal_wire_parity_smoke(tmp_path: Path) -> None:
    reviewed_path = Path("data/dispatches/care-line/reviewed/2026-07-22/reviewed_records.json")
    payload = json.loads(reviewed_path.read_text(encoding="utf-8"))
    rows = payload["records"] if isinstance(payload, dict) and "records" in payload else payload
    ready = [row for row in rows if row.get("producer_record_id") in {"care-line-direct-discovery-4a1461b9eccb0219", "care-line-direct-discovery-fe1cba7829f11dc2"}]
    review_payload = _review_payload(
        [
            _review_item(
                candidate_id=str(row["producer_record_id"]),
                cluster_id=str(row["producer_record_id"]),
                facility=row.get("facility_name", ""),
                facility_name=row.get("facility_name", ""),
                jurisdiction=row.get("state", ""),
                source_name=row.get("publisher", "Becker's"),
                source_title=row.get("source_title", ""),
                source_url=row.get("source_url", ""),
                source_date=row.get("source_publication_date", "2026-07-24"),
                approved_public_claim=row.get("supporting_passage", ""),
                bounded_public_summary=row.get("supporting_passage", ""),
                approved_event_type=row.get("event_type", ""),
                approved_service_line=row.get("service_line", ""),
                approved_access_consequence="service_change",
                approved_geography=f"{row.get('city', '')}, {row.get('state', '')}".strip(", "),
                exact_supporting_passage=row.get("supporting_passage", ""),
                corrections_applied=[],
                reviewer_rationale="Existing reviewed Signal Wire record remains source-traceable for parity.",
                review_decision="APPROVE",
                review_date="2026-08-05",
            )
            for row in ready
        ]
    )
    assert len(ready) == 2
    snapshot = write_review_snapshot(tmp_path, review_payload)
    proposal = build_proposed_edition(review_payload, snapshot_path=snapshot["snapshot_path"], snapshot_sha256=snapshot["snapshot_sha256"], proposal_created_at="2026-08-05T18:35:00Z")
    out = tmp_path / "private-draft"
    counts = build_private_draft_artifacts(tmp_path, payload=review_payload, proposal=proposal, output_dir=out)
    assert counts["approved_count"] == 2
    assert "care-line-direct-discovery-4a1461b9eccb0219" in (out / "claim_ledger.html").read_text(encoding="utf-8")
    assert "care-line-direct-discovery-fe1cba7829f11dc2" in (out / "claim_ledger.html").read_text(encoding="utf-8")
