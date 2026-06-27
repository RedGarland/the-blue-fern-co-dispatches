from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.food_line_discovery_expansion import read_food_line_discovery_expansion_audit
from bluefern_dispatches.food_line_sources import canonical_url, infer_map_category, validate_date

DISPATCH_SLUG = "food-line"
DISCOVERY_DIR_NAME = "discovery"
DISCOVERY_CANDIDATES_FILE = "discovery_candidates.json"
DISCOVERY_MANUAL_FALLBACK_FILE = "manual_fallback.json"
DISCOVERY_SOURCE_INPUT_FILE = "discovery_sources.json"
DISCOVERY_INTAKE_REVIEW_FILE = "discovery_intake.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_url(url: str) -> str:
    value = _nonempty(url)
    if not value:
        return ""
    return canonical_url(value)


def _discovery_candidates_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / DISCOVERY_DIR_NAME / edition_date / DISCOVERY_CANDIDATES_FILE


def _discovery_manual_fallback_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / DISCOVERY_DIR_NAME / edition_date / DISCOVERY_MANUAL_FALLBACK_FILE


def _discovery_source_input_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "sources" / edition_date / DISCOVERY_SOURCE_INPUT_FILE


def _discovery_review_path(root: Path, edition_date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / edition_date / DISCOVERY_INTAKE_REVIEW_FILE


def _candidate_trace_key(candidate: dict[str, Any]) -> str:
    for key in ("final_trace_url", "canonical_url", "discovered_url"):
        value = _normalize_url(_nonempty(candidate.get(key)))
        if value:
            return value
    return ""


def _candidate_map_category(candidate: dict[str, Any], source_family: str) -> str:
    terms = [str(term).strip() for term in candidate.get("pressure_terms_detected") or [] if str(term).strip()]
    return infer_map_category(terms, source_family)


def _infer_state(candidate: dict[str, Any]) -> str:
    state_abbrev = _nonempty(candidate.get("state_abbrev") or candidate.get("state"))
    if len(state_abbrev) == 2:
        return state_abbrev.upper()
    state = _nonempty(candidate.get("state_or_territory"))
    if len(state) == 2:
        return state.upper()
    if state:
        return "US"
    return "US"


def _infer_location_name(candidate: dict[str, Any]) -> str:
    for key in ("manual_fallback_location", "metro", "state_or_territory", "discovered_publisher"):
        value = _nonempty(candidate.get(key))
        if value:
            return value
    return "United States"


def _infer_source_family(candidate: dict[str, Any]) -> str:
    if _nonempty(candidate.get("discovered_publisher")):
        return "local_news"
    return "local_news"


def _bridge_record_from_candidate(candidate: dict[str, Any], *, manual_fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    source_family = _infer_source_family(candidate)
    final_trace_url = _normalize_url(_nonempty(candidate.get("final_trace_url") or candidate.get("canonical_url") or candidate.get("discovered_url")))
    google_news_url = _normalize_url(_nonempty(candidate.get("google_news_url")))
    title = _nonempty(candidate.get("discovered_title") or candidate.get("title"))
    publisher = _nonempty(candidate.get("discovered_publisher") or candidate.get("publisher"))
    publication_date = _nonempty(candidate.get("publication_date"))
    manual_review_required = bool(candidate.get("manual_review_required", True))
    classification_status = _nonempty(candidate.get("classification_status") or "needs_review")
    review_status = _nonempty(candidate.get("review_status") or "needs_review")
    candidate_review_status = _nonempty(candidate.get("candidate_review_status") or review_status or "needs_review")
    exclusion_reason = _nonempty(candidate.get("exclusion_reason"))
    pressure_terms_detected = [str(term).strip() for term in candidate.get("pressure_terms_detected") or [] if str(term).strip()]
    location_terms_detected = [str(term).strip() for term in candidate.get("location_terms_detected") or [] if str(term).strip()]
    traceability_status = _nonempty(candidate.get("traceability_status"))
    if not traceability_status:
        if final_trace_url:
            traceability_status = "traceable"
        elif google_news_url:
            traceability_status = "source_wrapper_only"
        else:
            traceability_status = "missing_url"
    bridge_row = {
        "source_record_id": _nonempty(candidate.get("candidate_id")),
        "title": title,
        "publisher": publisher,
        "url": final_trace_url or _normalize_url(_nonempty(candidate.get("canonical_url") or candidate.get("discovered_url"))),
        "published_at": publication_date,
        "retrieved_at": _nonempty(candidate.get("retrieved_at") or _utc_now()),
        "summary_or_snippet": _nonempty(candidate.get("manually_reviewed_summary") or candidate.get("pressure_evidence_summary") or candidate.get("discovered_title") or title),
        "source_type": "page",
        "source_family": source_family,
        "state": _infer_state(candidate),
        "location_name": _infer_location_name(candidate),
        "location_scope": _nonempty(candidate.get("geographic_scope") or ("state_local" if _infer_state(candidate) != "US" else "national")),
        "map_category": _candidate_map_category(candidate, source_family),
        "source_origin": _nonempty(candidate.get("discovery_channel") or "food_line_discovery"),
        "registry_status": "discovery_candidate",
        "pressure_required": True,
        "pressure_verification_required": True,
        "source_traceability_role": "publisher_url",
        "source_purpose": "",
        "current_or_evergreen": "",
        "promotable": True,
        "non_promotable_reason": "",
        "source_record_origin": "discovery_candidate",
        "discovery_lane": _nonempty(candidate.get("discovery_lane")),
        "discovery_query": _nonempty(candidate.get("discovery_query") or candidate.get("query_text")),
        "discovery_source_type": _nonempty(candidate.get("discovery_source_type") or candidate.get("discovery_channel")),
        "discovery_candidate_id": _nonempty(candidate.get("candidate_id")),
        "discovery_date": _nonempty(candidate.get("discovery_date")),
        "discovered_at": _nonempty(candidate.get("discovered_at") or candidate.get("retrieved_at")),
        "query_family": _nonempty(candidate.get("query_family")),
        "query_text": _nonempty(candidate.get("query_text")),
        "query_url": _nonempty(candidate.get("query_url")),
        "discovery_channel": _nonempty(candidate.get("discovery_channel")),
        "discovered_title": title,
        "discovered_publisher": publisher,
        "discovered_url": _normalize_url(_nonempty(candidate.get("discovered_url"))),
        "canonical_url": _normalize_url(_nonempty(candidate.get("canonical_url"))),
        "source_url": _normalize_url(_nonempty(candidate.get("source_url") or final_trace_url)),
        "original_source_url": _normalize_url(_nonempty(candidate.get("original_source_url") or final_trace_url)),
        "google_news_url": google_news_url,
        "publication_date": publication_date,
        "source_published_date": _nonempty(candidate.get("source_published_date") or publication_date[:10]),
        "date_basis": _nonempty(candidate.get("date_basis")),
        "fetch_status": _nonempty(candidate.get("fetch_status")),
        "fetch_error": _nonempty(candidate.get("fetch_error")),
        "final_trace_url": final_trace_url,
        "duplicate_of": _nonempty(candidate.get("duplicate_of")),
        "review_status": review_status,
        "candidate_review_status": candidate_review_status,
        "classification_status": classification_status,
        "exclusion_reason": exclusion_reason,
        "pressure_terms_detected": pressure_terms_detected,
        "location_terms_detected": location_terms_detected,
        "manual_review_required": manual_review_required,
        "state_hint": _nonempty(candidate.get("state_hint")),
        "pressure_signal_hint": _nonempty(candidate.get("pressure_signal_hint")),
        "pressure_signal_type_hint": _nonempty(candidate.get("pressure_signal_type_hint")),
        "traceability_status": traceability_status,
        "public_claim_eligible": bool(candidate.get("public_claim_eligible")),
        "public_claim_blockers": list(candidate.get("public_claim_blockers") or []),
        "candidate_id": _nonempty(candidate.get("candidate_id")),
    }
    if manual_fallback:
        bridge_row.update(
            {
                "manual_fallback_applied": True,
                "manual_fallback_headline": _nonempty(manual_fallback.get("headline")),
                "manual_fallback_summary": _nonempty(manual_fallback.get("manually_reviewed_summary")),
                "manual_fallback_pressure_evidence_summary": _nonempty(manual_fallback.get("pressure_evidence_summary")),
                "manual_fallback_affected_groups": list(manual_fallback.get("affected_groups") or []),
                "manual_fallback_limitations": _nonempty(manual_fallback.get("limitations")),
                "manual_fallback_extraction_quality": _nonempty(manual_fallback.get("extraction_quality")),
                "manual_fallback_reviewer_or_source_note": _nonempty(manual_fallback.get("reviewer_or_source_note")),
                "summary_or_snippet": _nonempty(
                    manual_fallback.get("pressure_evidence_summary")
                    or manual_fallback.get("manually_reviewed_summary")
                    or bridge_row["summary_or_snippet"]
                ),
                "manual_review_required": False,
                "review_status": "manual_reviewed",
                "candidate_review_status": "needs_review",
                "classification_status": "manual_fallback",
                "exclusion_reason": "",
                "source_purpose": _nonempty(manual_fallback.get("source_purpose") or bridge_row["source_purpose"]),
                "current_or_evergreen": _nonempty(manual_fallback.get("current_or_evergreen") or bridge_row["current_or_evergreen"]),
            }
        )
    return bridge_row


def _load_manual_fallback_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must contain a JSON list")
    return [row for row in payload if isinstance(row, dict)]


def _merge_manual_fallbacks(
    candidates: list[dict[str, Any]],
    manual_fallback_records: list[dict[str, Any]],
    edition_date: str,
) -> tuple[list[dict[str, Any]], int, int]:
    by_trace: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        trace_key = _candidate_trace_key(candidate)
        if trace_key:
            by_trace[trace_key] = index
    appended = 0
    merged = 0
    for record in manual_fallback_records:
        if _nonempty(record.get("date")) and _nonempty(record.get("date")) != edition_date:
            raise ValueError("manual fallback record date does not match the discovery edition date")
        manual_trace = _normalize_url(_nonempty(record.get("final_trace_url") or record.get("canonical_url")))
        if not manual_trace:
            raise ValueError("manual fallback record must include a final_trace_url or canonical_url")
        candidate_index = by_trace.get(manual_trace)
        if candidate_index is None:
            candidates.append(
                {
                    "candidate_id": _nonempty(record.get("candidate_id") or f"manual-fallback-{len(candidates) + 1}"),
                    "discovery_date": edition_date,
                    "query_family": "manual_fallback",
                    "query_text": "",
                    "query_url": "",
                    "geographic_scope": "manual",
                    "state_or_territory": "",
                    "metro": "",
                    "discovery_channel": "manual_fallback",
                    "discovered_title": _nonempty(record.get("headline")),
                    "discovered_publisher": _nonempty(record.get("publisher")),
                    "discovered_url": manual_trace,
                    "canonical_url": _normalize_url(_nonempty(record.get("canonical_url")) or manual_trace),
                    "google_news_url": "",
                    "publication_date": edition_date,
                    "fetch_status": "manual_fallback",
                    "fetch_error": "",
                    "final_trace_url": manual_trace,
                    "duplicate_of": "",
                    "review_status": "manual_reviewed",
                    "classification_status": "manual_fallback",
                    "exclusion_reason": "",
                    "pressure_terms_detected": [],
                    "location_terms_detected": [],
                    "manual_review_required": False,
                    "manual_fallback_location": _nonempty(record.get("location")),
                    "manually_reviewed_summary": _nonempty(record.get("manually_reviewed_summary")),
                    "pressure_evidence_summary": _nonempty(record.get("pressure_evidence_summary")),
                    "affected_groups": list(record.get("affected_groups") or []),
                    "limitations": _nonempty(record.get("limitations")),
                    "extraction_quality": _nonempty(record.get("extraction_quality")) or "manual_fallback",
                    "reviewer_or_source_note": _nonempty(record.get("reviewer_or_source_note")),
                    "source_note": _nonempty(record.get("reviewer_or_source_note")),
                }
            )
            appended += 1
            continue
        merged += 1
        candidates[candidate_index].update(
            {
                "manually_reviewed_summary": _nonempty(record.get("manually_reviewed_summary")),
                "pressure_evidence_summary": _nonempty(record.get("pressure_evidence_summary")),
                "affected_groups": list(record.get("affected_groups") or []),
                "limitations": _nonempty(record.get("limitations")),
                "extraction_quality": _nonempty(record.get("extraction_quality")) or "manual_fallback",
                "reviewer_or_source_note": _nonempty(record.get("reviewer_or_source_note")),
                "source_note": _nonempty(record.get("reviewer_or_source_note")),
                "manual_review_required": False,
                "manual_fallback_location": _nonempty(record.get("location") or candidates[candidate_index].get("manual_fallback_location")),
                "review_status": "manual_reviewed",
                "classification_status": "manual_fallback",
                "exclusion_reason": "",
            }
        )
    return candidates, merged, appended


def _discovery_no_current_update_state(summary: dict[str, Any]) -> str:
    candidate_count = int(summary.get("discovery_candidate_count") or 0)
    qualified = int(summary.get("discovery_qualified_candidate_count") or 0)
    blocked = int(summary.get("discovery_blocked_candidate_count") or 0)
    manual_review_required = int(summary.get("discovery_candidates_manual_review_required") or 0)
    context = int(summary.get("discovery_context_candidate_count") or 0)
    continuing_pressure_count = int(summary.get("continuing_pressure_count") or 0)
    if candidate_count <= 0:
        if continuing_pressure_count > 0:
            return "continuing_pressure_only"
        return "no_candidates_found"
    if qualified > 0:
        return "qualified_candidates_found"
    if blocked > 0 and context == 0 and manual_review_required > 0:
        return "candidates_found_but_fetch_blocked"
    if manual_review_required > 0 and blocked == 0 and context == 0:
        return "candidates_found_but_review_incomplete"
    return "candidates_found_but_none_qualified"


def _discovery_no_current_update_reason(summary: dict[str, Any], state: str) -> str:
    candidate_count = int(summary.get("discovery_candidate_count") or 0)
    qualified = int(summary.get("discovery_qualified_candidate_count") or 0)
    if candidate_count <= 0:
        return "No discovery candidates were retained."
    if state == "qualified_candidates_found" and qualified > 0:
        return "Discovery retained qualified candidates, but none passed normal Food Line publication checks."
    if state in {"candidates_found_but_fetch_blocked", "candidates_found_but_review_incomplete"}:
        return "Discovery retained candidates, but blocked or manual-review-required candidates did not pass normal publication checks."
    return "Discovery retained candidates, but none were classified as qualified pressure signals."


def run_food_line_discovery_intake_bridge(
    root: Path,
    edition_date: str,
    *,
    manual_fallback_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    date_text = validate_date(edition_date)
    candidate_path = _discovery_candidates_path(root, date_text)
    source_input_path = _discovery_source_input_path(root, date_text)
    review_path = _discovery_review_path(root, date_text)
    audit = read_food_line_discovery_expansion_audit(root, date_text)
    if not candidate_path.exists():
        state = _discovery_no_current_update_state({"continuing_pressure_count": 0})
        summary = {
            "ok": True,
            "discovery_expansion_used": False,
            "discovery_candidate_count": 0,
            "discovery_qualified_candidate_count": 0,
            "discovery_context_candidate_count": 0,
            "discovery_blocked_candidate_count": 0,
            "discovery_duplicate_count": 0,
            "discovery_confidence": _nonempty(audit.get("discovery_confidence")),
            "discovery_confidence_reason": _nonempty(audit.get("discovery_confidence_reason")),
            "discovery_audit_path": str(audit.get("discovery_audit_json_path") or ""),
            "discovery_candidates_path": str(candidate_path),
            "discovery_candidates_intaked": 0,
            "discovery_candidates_excluded": 0,
            "discovery_candidates_manual_review_required": 0,
            "discovery_no_current_update_state": state,
            "discovery_no_current_update_reason": _discovery_no_current_update_reason({"discovery_candidate_count": 0}, state),
            "discovery_source_input_path": str(source_input_path),
            "discovery_review_path": str(review_path),
            "discovery_source_rows": [],
        }
        if not dry_run:
            _write_json(source_input_path, [])
            _write_json(review_path, summary)
        return summary
    payload = _read_json(candidate_path)
    if not isinstance(payload, list):
        raise ValueError(f"{candidate_path.name} must contain a JSON list")
    candidates = [row for row in payload if isinstance(row, dict)]
    manual_fallback_records = _load_manual_fallback_records(manual_fallback_path or _discovery_manual_fallback_path(root, date_text))
    candidates, merged_count, appended_count = _merge_manual_fallbacks(candidates, manual_fallback_records, date_text)
    candidate_count = len(candidates)
    qualified_count = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "qualified_pressure_signal")
    context_count = sum(1 for row in candidates if _nonempty(row.get("classification_status")) == "context_only")
    blocked_count = sum(1 for row in candidates if _nonempty(row.get("fetch_status")) not in {"ok", "manual_fallback"})
    manual_review_required_count = sum(1 for row in candidates if bool(row.get("manual_review_required")))
    duplicate_count = sum(1 for row in candidates if _nonempty(row.get("duplicate_of")))
    intaked_rows: list[dict[str, Any]] = []
    for row in candidates:
        discovery_row = _bridge_record_from_candidate(
            row,
            manual_fallback=row if _nonempty(row.get("classification_status")) == "manual_fallback" else None,
        )
        if _nonempty(discovery_row.get("duplicate_of")):
            continue
        intaked_rows.append(discovery_row)
    source_count = len(intaked_rows)
    excluded_count = max(0, candidate_count - source_count)
    no_current_update_state = _discovery_no_current_update_state(
        {
            "discovery_candidate_count": candidate_count,
            "discovery_qualified_candidate_count": qualified_count,
            "discovery_context_candidate_count": context_count,
            "discovery_blocked_candidate_count": blocked_count,
            "discovery_candidates_manual_review_required": manual_review_required_count,
            "continuing_pressure_count": int(audit.get("continuing_pressure_count") or 0),
        }
    )
    summary = {
        "ok": True,
        "generated_at": _utc_now(),
        "discovery_expansion_used": True,
        "discovery_candidate_count": candidate_count,
        "discovery_qualified_candidate_count": qualified_count,
        "discovery_context_candidate_count": context_count,
        "discovery_blocked_candidate_count": blocked_count,
        "discovery_duplicate_count": duplicate_count,
        "discovery_confidence": _nonempty(audit.get("discovery_confidence")) or "limited",
        "discovery_confidence_reason": _nonempty(audit.get("discovery_confidence_reason")) or "Discovery intake was read but no confidence summary was available.",
        "discovery_audit_path": _nonempty(audit.get("discovery_audit_json_path")) or str(root / "output" / "review" / DISPATCH_SLUG / date_text / "discovery_audit.json"),
        "discovery_candidates_path": str(candidate_path),
        "discovery_candidates_intaked": source_count,
        "discovery_candidates_excluded": excluded_count,
        "discovery_candidates_manual_review_required": manual_review_required_count,
        "discovery_no_current_update_state": no_current_update_state,
        "discovery_no_current_update_reason": _discovery_no_current_update_reason(
            {
                "discovery_candidate_count": candidate_count,
                "discovery_qualified_candidate_count": qualified_count,
                "discovery_context_candidate_count": context_count,
                "discovery_blocked_candidate_count": blocked_count,
                "discovery_candidates_manual_review_required": manual_review_required_count,
            },
            no_current_update_state,
        ),
        "discovery_source_input_path": str(source_input_path),
        "discovery_review_path": str(review_path),
        "discovery_source_rows": intaked_rows,
        "discovery_manual_fallback_merged_count": merged_count,
        "discovery_manual_fallback_appended_count": appended_count,
    }
    if not dry_run:
        _write_json(source_input_path, intaked_rows)
        _write_json(review_path, summary)
    return summary
