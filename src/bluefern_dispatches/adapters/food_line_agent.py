from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..agent_findings import FoodLineAgentFinding, finding_from_payload
from ..food_line_sources import evaluate_food_line_pressure


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
    row = {"candidate_id": finding.finding_id, "title": finding.title, "publisher": finding.publisher, "url": finding.canonical_source_url, "canonical_url": finding.canonical_source_url, "source_url": finding.source_url, "published_at": finding.source_published_at, "source_published_date": finding.source_published_at[:10], "summary_or_snippet": finding.summary, "evidence_text": finding.exact_supporting_passage, "evidence_text_basis": "page_text_excerpt", "source_family": "local_news", "source_name": finding.publisher, "state": finding.state or "US", "location_name": finding.location_name, "location_scope": finding.location_scope, "source_role": finding.source_role, "pressure_type": finding.pressure_type, "agent_name": finding.agent_name, "agent_run_id": finding.agent_run_id, "agent_finding_id": finding.finding_id, "agent_duplicate_key": finding.duplicate_key, "review_status": "pending_review", "raw_agent_payload": finding.raw_agent_payload}
    evaluated = evaluate_food_line_pressure(row, edition_date=edition_date, pressure_required=True)
    for key in ("pressure_signal", "pressure_type", "pressure_summary", "pressure_reason", "affected_groups", "evidence_level", "freshness_role", "freshness_status", "freshness_disqualification_reason", "source_role", "location_scope", "source_role_allowed", "source_purpose", "promotable", "non_promotable_reason", "rejected", "rejection_reason", "map_eligible", "evidence_text_basis"): row[key] = evaluated.get(key)
    reasons = []
    if not finding.canonical_source_url: reasons.append("invalid_or_missing_https_url")
    if not finding.exact_supporting_passage: reasons.append("missing_exact_supporting_passage")
    if not evaluated.get("pressure_signal"): reasons.append(str(evaluated.get("pressure_reason") or "not_a_verified_pressure_signal"))
    if evaluated.get("freshness_disqualification_reason"): reasons.append(str(evaluated["freshness_disqualification_reason"]))
    row["exclusion_reason"] = "; ".join(dict.fromkeys(reasons))
    row["eligible_for_review"] = not reasons
    return row
