from __future__ import annotations

from typing import Any

from bluefern_dispatches.food_line_signal_wire_preview import CURRENT_AS_OF, _event_from_record


def build_signal_wire_event_from_candidate(candidate: dict[str, Any], *, as_of: str | None = None) -> dict[str, Any]:
    as_of_value = str(as_of or CURRENT_AS_OF)
    pressure_category = str(
        candidate.get("pressure_category")
        or candidate.get("map_category")
        or candidate.get("pressure_type")
        or "food-access pressure"
    ).strip()
    summary = str(
        candidate.get("proposed_public_summary")
        or candidate.get("pressure_summary")
        or candidate.get("summary_or_snippet")
        or candidate.get("claim_supported")
        or candidate.get("summary")
        or ""
    ).strip()
    headline = str(
        candidate.get("headline")
        or candidate.get("proposed_public_headline")
        or candidate.get("title")
        or candidate.get("discovered_title")
        or summary
    ).strip()
    evidence = str(candidate.get("evidence_text") or candidate.get("exact_supporting_passage") or "").strip()
    return _event_from_record(
        {
            "canonical_source_url": candidate.get("canonical_source_url")
            or candidate.get("canonical_url")
            or candidate.get("source_url")
            or candidate.get("url"),
            "source_url": candidate.get("source_url")
            or candidate.get("canonical_source_url")
            or candidate.get("canonical_url")
            or candidate.get("url"),
            "publisher": candidate.get("publisher") or candidate.get("source_name") or candidate.get("discovered_publisher"),
            "title": headline,
            "source_published_at": candidate.get("source_published_at")
            or candidate.get("published_at")
            or candidate.get("source_published_date")
            or candidate.get("publication_date")
            or as_of_value,
            "location_name": candidate.get("location_name") or candidate.get("location") or candidate.get("metro") or "United States",
            "state": candidate.get("state") or candidate.get("state_abbrev") or candidate.get("state_or_territory") or "US",
            "location_scope": candidate.get("location_scope")
            or candidate.get("geographic_scope")
            or candidate.get("location_name")
            or "United States",
            "summary": summary,
            "exact_supporting_passage": evidence or summary or headline,
            "uncertainty_note": candidate.get("uncertainty_note") or candidate.get("limitations") or "",
            "why_it_matters": candidate.get("why_it_matters") or candidate.get("pressure_evidence_summary") or summary,
            "evidence_text_basis": candidate.get("evidence_text_basis") or candidate.get("pressure_verification_status") or "source_text_verified",
            "source_artifact_path": candidate.get("source_artifact_path") or candidate.get("artifact_path") or "",
            "review_status": candidate.get("review_status") or candidate.get("candidate_review_status"),
            "review_item_id": candidate.get("review_item_id") or candidate.get("candidate_id"),
        },
        pressure_category=pressure_category,
        kind="current_fixture",
        state_override=str(candidate.get("state") or candidate.get("state_abbrev") or candidate.get("state_or_territory") or "US").strip(),
        geography_override=str(candidate.get("geography_scope") or candidate.get("location_scope") or candidate.get("location_name") or candidate.get("state") or "United States").strip(),
        summary_override=summary,
        caveat_override=str(candidate.get("uncertainty_note") or candidate.get("limitations") or "").strip() or None,
        as_of=as_of_value,
        source_published_at_override=str(
            candidate.get("source_published_at")
            or candidate.get("published_at")
            or candidate.get("source_published_date")
            or candidate.get("publication_date")
            or as_of_value
        ),
    )
