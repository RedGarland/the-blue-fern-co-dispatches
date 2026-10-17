from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from ..agent_findings import FoodLineAgentFinding, finding_from_payload
from ..food_line_sources import evaluate_food_line_pressure, food_line_supported_geography_result
from ..source_based_qualification import assess_review_retention

def _normalize_food_line_geography(finding: FoodLineAgentFinding) -> tuple[str, str, str, str, str, str]:
    """Keep event geography separate from query/publisher hints.

    The agent envelope may contain legacy ``state``/``metro`` hints.  Those
    remain private audit fields and are not promoted to event geography.  A
    source body or reviewer may provide explicit event/impact fields.
    """

    raw = finding.raw_agent_payload
    country = str(raw.get("event_country") or raw.get("impact_country") or raw.get("country") or "").strip()
    state = str(raw.get("event_state") or raw.get("impact_state") or "").strip().upper()
    location_name = str(raw.get("event_location") or raw.get("impact_location") or "").strip()
    location_scope = str(raw.get("event_location_scope") or raw.get("impact_location_scope") or "").strip()
    query_state_hint = str(raw.get("state") or raw.get("state_abbrev") or raw.get("state_hint") or "").strip()
    query_location_hint = str(
        raw.get("metro")
        or raw.get("location")
        or raw.get("location_name")
        or raw.get("state_or_territory")
        or ""
    ).strip()
    return location_name, state, location_scope, country, query_state_hint, query_location_hint


def adapt_food_line_agent_output(payload: Any, *, agent_name: str, agent_run_id: str, discovered_at: str | None = None) -> list[FoodLineAgentFinding]:
    envelope = payload if isinstance(payload, dict) and "findings" in payload else {}
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = None
        for key in ("findings", "items", "results", "records"):
            if key in payload:
                rows = payload.get(key)
                break
    else:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("agent output must be a list or an object containing findings/items/results/records")
    stamp = discovered_at or envelope.get("completed_at") or datetime.now(timezone.utc).isoformat()
    effective_agent = agent_name or str(envelope.get("agent_name") or "")
    effective_run = agent_run_id or str(envelope.get("agent_run_id") or "")
    return [finding_from_payload(row, agent_name=effective_agent, agent_run_id=effective_run, discovered_at=stamp) for row in rows]


def map_finding_to_food_line_candidate(finding: FoodLineAgentFinding, *, edition_date: str) -> dict[str, Any]:
    raw = finding.raw_agent_payload
    location_name, state, location_scope, country, query_state_hint, query_location_hint = _normalize_food_line_geography(finding)
    raw_country = str(raw.get("country") or "").strip()
    raw_event_country = str(raw.get("event_country") or "").strip()
    raw_impact_country = str(raw.get("impact_country") or "").strip()
    raw_state = str(raw.get("state") or "").strip().upper()
    raw_event_state = str(raw.get("event_state") or "").strip().upper()
    raw_impact_state = str(raw.get("impact_state") or "").strip().upper()
    raw_location = str(raw.get("location") or "").strip()
    raw_event_location = str(raw.get("event_location") or "").strip()
    raw_impact_location = str(raw.get("impact_location") or "").strip()
    raw_location_name = str(raw.get("location_name") or "").strip()
    row = {
        "candidate_id": finding.finding_id,
        "title": finding.title,
        "publisher": finding.publisher,
        "url": finding.canonical_source_url,
        "canonical_url": finding.canonical_source_url,
        "source_url": finding.source_url,
        "published_at": finding.source_published_at,
        "source_published_at": finding.source_published_at,
        "source_published_date": finding.source_published_at[:10],
        "summary_or_snippet": finding.summary,
        "evidence_text": finding.exact_supporting_passage,
        "exact_supporting_passage": finding.exact_supporting_passage,
        "evidence_text_basis": str(raw.get("evidence_text_basis") or "page_text_excerpt"),
        "source_body_text": str(raw.get("source_body_text") or "").strip(),
        "source_body_excerpt": str(raw.get("source_body_excerpt") or "").strip(),
        "event_location_evidence": str(raw.get("event_location_evidence") or "").strip(),
        "source_family": str(raw.get("source_family") or "local_news"),
        "source_name": finding.publisher,
        "state": raw_state,
        "location_name": raw_location_name,
        "location_scope": location_scope,
        "country": raw_country,
        "event_country": raw_event_country,
        "impact_country": raw_impact_country,
        "event_state": raw_event_state,
        "impact_state": raw_impact_state,
        "location": raw_location,
        "event_location": raw_event_location,
        "impact_location": raw_impact_location,
        "query_state_hint": query_state_hint,
        "query_location_hint": query_location_hint,
        "query_geographic_scope": str(raw.get("geographic_scope") or "").strip(),
        "geography_evidence_basis": str(raw.get("geography_evidence_basis") or "").strip(),
        "source_role": finding.source_role,
        "pressure_type": finding.pressure_type,
        "agent_name": finding.agent_name,
        "agent_run_id": finding.agent_run_id,
        "agent_finding_id": finding.finding_id,
        "agent_duplicate_key": finding.duplicate_key,
        "review_status": "pending_review",
        "raw_agent_payload": raw,
    }
    evaluated = evaluate_food_line_pressure(row, edition_date=edition_date, pressure_required=True)
    for key in ("pressure_signal", "pressure_type", "pressure_summary", "pressure_reason", "affected_groups", "evidence_level", "freshness_role", "freshness_status", "freshness_disqualification_reason", "source_role", "location_scope", "source_role_allowed", "source_purpose", "promotable", "non_promotable_reason", "rejected", "rejection_reason", "map_eligible", "evidence_text_basis"): row[key] = evaluated.get(key)
    # Preserve explicit/inferred geography after pressure evaluation normalizes generic scope.
    row["state"] = state
    row["location_name"] = location_name
    row["location_scope"] = location_scope

    qualification_input = dict(raw)
    qualification_input.update(
        {
            "canonical_source_url": finding.canonical_source_url,
            "publisher": finding.publisher,
            "title": finding.title,
            "exact_supporting_passage": finding.exact_supporting_passage,
            "source_published_at": finding.source_published_at,
            "source_role": finding.source_role or evaluated.get("source_role"),
            "source_family": raw.get("source_family") or row["source_family"],
            "evaluator_pressure_signal": bool(evaluated.get("pressure_signal")),
            "pressure_reason": evaluated.get("pressure_reason"),
            "country": raw_country,
            "event_country": raw_event_country,
            "impact_country": raw_impact_country,
            "event_state": raw_event_state,
            "impact_state": raw_impact_state,
            "event_location": raw_event_location,
            "impact_location": raw_impact_location,
            # Assess the same explicit event fields that the candidate row
            # uses.  Legacy ``state``/``location_name`` values are retained as
            # query hints in the candidate, never as event geography.
            "location_name": location_name,
            "state": state,
        }
    )
    retention = assess_review_retention(qualification_input, dispatch="food-line", edition_date=edition_date)
    geography_result = food_line_supported_geography_result(row)
    if geography_result["supported"]:
        state = str(geography_result.get("normalized_state") or state).strip().upper()
        location_name = str(geography_result.get("normalized_location_name") or location_name).strip()
        location_scope = str(geography_result.get("normalized_location_scope") or location_scope).strip()
        country = str(geography_result.get("normalized_country") or country).strip()
        row["state"] = state
        row["location_name"] = location_name
        row["location_scope"] = location_scope
        row["country"] = country
        row["event_country"] = country
        row["event_state"] = state
        row["event_location"] = location_name
    if not geography_result["supported"]:
        retention["eligible_for_review"] = False
        retention["failure_reasons"] = list(
            dict.fromkeys(
                [
                    *retention["failure_reasons"],
                    "outside_food_line_us_territories_scope"
                    if geography_result["status"] == "outside"
                    else "food_line_event_geography_unverified",
                ]
            )
        )
        retention["disposition"] = "rejected_with_reason"
        retention["next_transition_owner"] = ""
    if not finding.exact_supporting_passage:
        retention["traceable_source"] = False
        retention["eligible_for_review"] = False
        retention["failure_reasons"] = list(
            dict.fromkeys([*retention["failure_reasons"], "missing_traceable_supporting_evidence"])
        )
        retention["disposition"] = "invalid_source_with_reason"
        retention["next_transition_owner"] = ""
    reasons = list(retention["failure_reasons"])
    eligible = bool(retention["eligible_for_review"])
    if eligible:
        row["pressure_signal"] = True
        row["pressure_type"] = (
            str(raw.get("pressure_type") or "").strip()
            or str(evaluated.get("pressure_type") or "").strip()
            or str(retention["qualification_basis"])
        )
        row["pressure_summary"] = (
            str(raw.get("pressure_summary") or "").strip()
            or str(evaluated.get("pressure_summary") or "").strip()
            or finding.summary
            or finding.exact_supporting_passage
        )
        row["freshness_status"] = "current"
        row["freshness_disqualification_reason"] = ""
    row["source_based_qualification"] = retention
    row["supported_product_geography"] = bool(geography_result["supported"])
    row["food_line_geography_status"] = geography_result["status"]
    row["food_line_geography_reason"] = geography_result["reason"]
    row["food_line_geography_evidence_field"] = geography_result["evidence_field"]
    row["qualification_reason"] = retention["qualification_basis"]
    row["freshness_basis"] = retention["freshness_basis"]
    row["freshness_check"] = retention["freshness_check"]
    row["uncertainty_note"] = retention["uncertainty_note"]
    row["review_retention_disposition"] = retention["disposition"]
    row["review_transition_owner"] = retention["next_transition_owner"]
    row["exclusion_reason"] = "; ".join(dict.fromkeys(reasons))
    row["eligible_for_review"] = eligible
    return row
