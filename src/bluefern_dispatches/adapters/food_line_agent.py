from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from ..agent_findings import FoodLineAgentFinding, finding_from_payload
from ..food_line_sources import evaluate_food_line_pressure
from ..source_based_qualification import assess_review_retention

_STATE_HINTS = {
    "north carolina": "NC",
    "n.c.": "NC",
    "nc": "NC",
}
_FOREIGN_GEOGRAPHY_TERMS = (
    "latin america",
    "latin america and the caribbean",
    "caribbean",
    "bangladesh",
)


def _combined_geography_text(finding: FoodLineAgentFinding) -> str:
    raw = finding.raw_agent_payload
    parts = [
        finding.location_name,
        finding.state,
        finding.title,
        finding.summary,
        finding.exact_supporting_passage,
        str(raw.get("location") or ""),
        str(raw.get("state_or_territory") or ""),
        str(raw.get("state_hint") or ""),
        str(raw.get("geographic_scope") or ""),
    ]
    return " ".join(part for part in parts if part).casefold()


def _is_clearly_foreign_food_line_geography(finding: FoodLineAgentFinding) -> bool:
    text = _combined_geography_text(finding)
    return any(term in text for term in _FOREIGN_GEOGRAPHY_TERMS)


def _normalize_food_line_geography(finding: FoodLineAgentFinding) -> tuple[str, str, str]:
    text = _combined_geography_text(finding)
    state = (finding.state or "").upper()
    location_name = finding.location_name
    location_scope = finding.location_scope
    if _is_clearly_foreign_food_line_geography(finding):
        if "bangladesh" in text:
            return "Bangladesh", "", "international"
        if "latin america" in text and "caribbean" in text:
            return "Latin America and the Caribbean", "", "international"
        if "latin america" in text:
            return "Latin America", "", "international"
        return location_name or "International", "", "international"
    if not state:
        for marker, abbrev in _STATE_HINTS.items():
            if re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", text):
                state = abbrev
                break
    if state == "NC" and (not location_name or location_name.casefold() == "national"):
        location_name = "North Carolina"
    return location_name, state, location_scope


def adapt_food_line_agent_output(payload: Any, *, agent_name: str, agent_run_id: str, discovered_at: str | None = None) -> list[FoodLineAgentFinding]:
    envelope = payload if isinstance(payload, dict) and "findings" in payload else {}
    rows = payload if isinstance(payload, list) else (envelope.get("findings") or payload.get("items") or payload.get("results") or payload.get("records") if isinstance(payload, dict) else None)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("agent output must be a list or an object containing findings/items/results/records")
    stamp = discovered_at or envelope.get("completed_at") or datetime.now(timezone.utc).isoformat()
    effective_agent = agent_name or str(envelope.get("agent_name") or "")
    effective_run = agent_run_id or str(envelope.get("agent_run_id") or "")
    return [finding_from_payload(row, agent_name=effective_agent, agent_run_id=effective_run, discovered_at=stamp) for row in rows]


def map_finding_to_food_line_candidate(finding: FoodLineAgentFinding, *, edition_date: str) -> dict[str, Any]:
    raw = finding.raw_agent_payload
    location_name, state, location_scope = _normalize_food_line_geography(finding)
    foreign_scope = _is_clearly_foreign_food_line_geography(finding)
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
        "evidence_text_basis": str(raw.get("evidence_text_basis") or "page_text_excerpt"),
        "source_family": str(raw.get("source_family") or "local_news"),
        "source_name": finding.publisher,
        "state": state,
        "location_name": location_name,
        "location_scope": location_scope,
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
        }
    )
    retention = assess_review_retention(qualification_input, dispatch="food-line", edition_date=edition_date)
    if foreign_scope:
        retention["eligible_for_review"] = False
        retention["failure_reasons"] = list(
            dict.fromkeys([*retention["failure_reasons"], "outside_food_line_us_territories_scope"])
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
    row["qualification_reason"] = retention["qualification_basis"]
    row["freshness_basis"] = retention["freshness_basis"]
    row["freshness_check"] = retention["freshness_check"]
    row["uncertainty_note"] = retention["uncertainty_note"]
    row["review_retention_disposition"] = retention["disposition"]
    row["review_transition_owner"] = retention["next_transition_owner"]
    row["exclusion_reason"] = "; ".join(dict.fromkeys(reasons))
    row["eligible_for_review"] = eligible
    return row
