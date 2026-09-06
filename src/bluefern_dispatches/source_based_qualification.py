"""Shared, review-only qualification primitives for Food Line and Care Line.

This module decides whether a source-backed signal must be retained for human
review.  It does not grant editorial or publication authority.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from urllib.parse import urlsplit


SOURCE_DATE_FIELDS = (
    "source_published_at",
    "source_published_date",
    "published_at",
    "published_date",
    "publication_date",
)
EVENT_DATE_FIELDS = (
    ("newly_announced", "announcement_date"),
    ("newly_announced", "event_announcement_date"),
    ("newly_effective", "effective_date"),
    ("newly_effective", "event_effective_date"),
    ("newly_completed", "completed_date"),
    ("newly_confirmed", "confirmed_date"),
    ("newly_worsened", "worsened_date"),
    ("newly_documented", "updated_date"),
    ("newly_documented", "observed_date"),
    ("newly_effective", "operative_event_date"),
    ("newly_documented", "event_date"),
)
ACTIVE_FRESHNESS_ROLES = {
    "BREAKING",
    "CURRENT",
    "FUTURE_EFFECTIVE",
    "ONGOING_EVENT_UPDATE",
    "RESTORATION_UPDATE",
    "fresh_daily_signal",
    "dated_recent_signal",
}
FIRST_PARTY_MARKERS = (
    "first_party",
    "operator",
    "provider",
    "official",
    "government",
    "agency",
    "state_official",
    "federal_official",
    "food_bank_provider",
)

FOOD_STRAIN_PATTERNS = (
    ("service closure", r"\b(?:pantr(?:y|ies)|food (?:bank|distribution|program)|grocery store|supermarket|market)\b.{0,100}\b(?:clos(?:e|ed|es|ing|ure)|shut(?:s|ting)? down|ceas(?:e|ed|es|ing)|end(?:ed|ing|s)?)\b"),
    ("service closure", r"\b(?:clos(?:e|ed|es|ing|ure)|shut(?:s|ting)? down|ceas(?:e|ed|es|ing)|end(?:ed|ing|s)?)\b.{0,100}\b(?:pantr(?:y|ies)|food (?:bank|distribution|program)|grocery store|supermarket|market)\b"),
    ("service reduction", r"\b(?:food|pantry|distribution|meal|grocery).{0,100}\b(?:suspend(?:ed|s|ing)?|reduc(?:e|ed|es|ing|tion)|cut(?:s|ting)?|fewer hours|limited hours|halt(?:ed|s|ing)?)\b"),
    ("service reduction", r"\b(?:impossible|unable|could not|cannot|can't)\b.{0,60}\bcontinue operating\b.{0,80}\b(?:pantr(?:y|ies)|food (?:bank|distribution|program))\b"),
    ("inventory shortage", r"\b(?:shortage|shortages|running low|ran out|not enough (?:food|inventory|supply)|inventory (?:is )?(?:low|short|depleted)|insufficient (?:food|supply|inventory)|supply contraction)\b"),
    ("capacity shortage", r"\b(?:unable|cannot|can't|could not)\b.{0,90}\b(?:serve|supply|provide|meet)\b.{0,90}\b(?:everyone|all (?:families|households|people|demand|requests))\b"),
    ("capacity shortage", r"\b(?:waitlist|waiting list|capacity limit|over capacity|turned away|turnaways?|unmet demand)\b"),
    ("benefit access failure", r"\b(?:SNAP|WIC|food stamps?|replacement benefits?)\b.{0,120}\b(?:den(?:y|ied|ial)|interrupt(?:ed|ion)|delay(?:ed|s)?|failed|unable to access|not received|terminated|suspend(?:ed|ed)?)\b"),
    ("benefit access failure", r"\b(?:den(?:y|ied|ial)|interrupt(?:ed|ion)|delay(?:ed|s)?|failed|not received)\b.{0,120}\b(?:SNAP|WIC|food stamps?|replacement benefits?)\b"),
    ("disaster food loss", r"\b(?:lost|loss|spoiled|destroyed)\b.{0,80}\bfood\b.{0,120}\b(?:storm|hurricane|flood|fire|outage|disaster|power)\b"),
    ("disaster food loss", r"\b(?:storm|hurricane|flood|fire|outage|disaster|power)\b.{0,120}\b(?:lost|loss|spoiled|destroyed)\b.{0,80}\bfood\b"),
    ("disaster food loss", r"\bfood loss\b.{0,120}\b(?:storm|hurricane|cyclone|severe weather|flood|fire|outage|disaster|power)\b"),
    ("system cost pressure", r"\b(?:procurement|purchasing|operating) costs?\b.{0,120}\b(?:strain|pressure|threaten|cut|reduce|limit|shortage|continu(?:ity|e))\b"),
    ("geographic access gap", r"\b(?:food assistance|emergency food|pantr(?:y|ies)|grocery store|supermarket)\b.{0,120}\b(?:access gap|service gap|geographic gap|no nearby|without (?:a|any)|underserved|only .{0,30}(?:store|pantry))\b"),
)

CARE_STRAIN_PATTERNS = (
    ("facility or service closure", r"\b(?:hospitals?|clinics?|health centers?|medical centers?|maternity|obstetric|emergency|dialysis|psychiatric|primary care|specialty|service lines?|units?)\b.{0,100}\b(?:clos(?:e|ed|es|ing|ure)|shut(?:s|ting)? down|ceas(?:e|ed|es|ing)|end(?:ed|ing|s)?)\b"),
    ("facility or service closure", r"\b(?:clos(?:e|ed|es|ing|ure)|shut(?:s|ting)? down|ceas(?:e|ed|es|ing)|end(?:ed|ing|s)?)\b.{0,100}\b(?:hospitals?|clinics?|health centers?|medical centers?|maternity|obstetric|emergency|dialysis|psychiatric|primary care|specialty|service lines?|units?)\b"),
    ("service reduction", r"\b(?:hospitals?|clinics?|care|services?|units?|hours|appointments?)\b.{0,120}\b(?:suspend(?:ed|s|ing)?|reduc(?:e|ed|es|ing|tion)|cut(?:s|ting)?|fewer hours|limited hours|halt(?:ed|s|ing)?|withdraw(?:al|n|s)?)\b"),
    ("capacity shortage", r"\b(?:staffing shortage|bed shortage|capacity shortage|appointment backlog|waitlist|waiting list|over capacity|unable to meet (?:patient )?demand|cannot meet (?:patient )?demand|patients? turned away)\b"),
    ("geographic access gap", r"\b(?:care|healthcare|medical|clinic|hospital|provider)\b.{0,140}\b(?:access gap|service gap|geographic gap|no nearby|without (?:a|any)|underserved|travel farther|transportation barrier|provider withdrawal)\b"),
    ("coverage access failure", r"\b(?:insurance|coverage|Medicaid|Medicare|benefits?)\b.{0,120}\b(?:loss|lost|den(?:y|ied|ial)|termination|withdrawal|unable to access|care access)\b"),
)

ACTIVE_CONDITION_RE = re.compile(
    r"\b(?:permanently|indefinitely|currently|remains?|continues?|ongoing|still|without (?:a )?(?:reopening|replacement)|no (?:reopening|replacement) date|until further notice)\b",
    re.I,
)
ROUTINE_GAP_RE = re.compile(
    r"\b(?:routine|regularly scheduled|scheduled holiday|holiday hours|semester break|academic calendar|summer break|winter break)\b",
    re.I,
)
SPECULATIVE_RE = re.compile(r"\b(?:may|might|could|possibly|potentially)\b.{0,80}\b(?:close|reduce|lose|shortage|deny|suspend|end)\b", re.I)
CONCRETE_ACTION_RE = re.compile(r"\b(?:will|is|are|has|have|was|were|announced|confirmed|effective|began|starts?|ended|closed|suspended|denied)\b", re.I)
DEMAND_RE = re.compile(r"\b(?:rising|growing|increasing|record|higher) demand\b", re.I)


def _text(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _evidence_text(record: Mapping[str, Any]) -> str:
    parts = []
    for key in ("exact_supporting_passage", "evidence_text", "supporting_passage", "description", "summary_or_snippet", "summary"):
        value = str(record.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def _strain_match(patterns: tuple[tuple[str, str], ...], evidence: str) -> tuple[str, str] | None:
    for label, pattern in patterns:
        for match in re.finditer(pattern, evidence, re.I | re.S):
            prefix = evidence[max(0, match.start() - 55):match.start()]
            matched_text = match.group(0)
            if re.search(r"\b(?:no|not|never|did not|has not|have not|without confirmed)\b.{0,60}$", prefix, re.I | re.S):
                continue
            if re.search(
                r"\b(?:no|not|never|did not|has not|have not|without confirmed)\b.{0,60}\b(?:closure|closed|reduced|suspended|shortage|denied|unable|cannot|halted|ended)\b",
                matched_text,
                re.I | re.S,
            ):
                continue
            return label, pattern
    return None


def _parse_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw[:10], raw]
    for candidate in candidates:
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
            except ValueError:
                pass
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError, OverflowError):
        return None


def _date_from_text(text: str, *, reference_year: int) -> date | None:
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso_match:
        return _parse_date(iso_match.group(1))
    month_names = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
        "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    match = re.search(
        r"\b(" + "|".join(month_names) + r")\.?\s+(\d{1,2})(?:,?\s+(20\d{2}))?\b",
        text,
        re.I,
    )
    if not match:
        return None
    try:
        return date(int(match.group(3) or reference_year), month_names[match.group(1).casefold()], int(match.group(2)))
    except ValueError:
        return None


def _freshness(record: Mapping[str, Any], *, edition: date) -> dict[str, Any]:
    source_raw = _text(record, *SOURCE_DATE_FIELDS)
    source_date = _parse_date(source_raw)
    if source_date is not None:
        age = (edition - source_date).days
        if 0 <= age <= 3:
            return _freshness_result("newly_published", source_date, edition)

    for basis, field in EVENT_DATE_FIELDS:
        event_date = _parse_date(record.get(field))
        if event_date is not None and -120 <= (edition - event_date).days <= 31:
            return _freshness_result(basis, event_date, edition)

    evidence = _evidence_text(record)
    event_date = _date_from_text(evidence, reference_year=edition.year)
    if event_date is not None and -120 <= (edition - event_date).days <= 31:
        lowered = evidence.casefold()
        basis = "newly_effective" if any(word in lowered for word in ("effective", "takes effect", "will close", "will end", "scheduled to close")) else "newly_documented"
        return _freshness_result(basis, event_date, edition)

    hinted_role = _text(record, "freshness_role")
    hinted_date = _parse_date(_text(record, "freshness_basis_date", "retrieved_at", "discovered_at", "discovery_date", "target_date"))
    if hinted_role in ACTIVE_FRESHNESS_ROLES and hinted_date is not None and 0 <= (edition - hinted_date).days <= 3:
        return _freshness_result("existing_event_aware_currentness", hinted_date, edition)

    surfaced_date = _parse_date(_text(record, "retrieved_at", "discovered_at", "discovery_date", "target_date"))
    active = bool(ACTIVE_CONDITION_RE.search(evidence))
    role_text = " ".join((_text(record, "source_role"), _text(record, "source_family"), _text(record, "source_type"))).casefold()
    if surfaced_date is not None and 0 <= (edition - surfaced_date).days <= 3 and active:
        basis = "current_first_party_status" if any(marker in role_text for marker in FIRST_PARTY_MARKERS) else "newly_surfaced_active_condition"
        return _freshness_result(basis, surfaced_date, edition)

    reason = "no source-backed currentness basis"
    if source_raw and source_date is None:
        reason = "source publication date is unparseable and no event-aware currentness basis exists"
    elif source_date is not None:
        if source_date > edition:
            reason = f"source publication date {source_date.isoformat()} is future-dated and no supported event basis exists"
        else:
            reason = f"source publication date {source_date.isoformat()} is stale and no current event basis exists"
    return {"status": "not_current", "basis": "", "basis_date": "", "edition_date": edition.isoformat(), "age_days": None, "reason": reason}


def _freshness_result(basis: str, basis_date: date, edition: date) -> dict[str, Any]:
    return {
        "status": "current",
        "basis": basis,
        "basis_date": basis_date.isoformat(),
        "edition_date": edition.isoformat(),
        "age_days": (edition - basis_date).days,
        "reason": "",
    }


def validate_current_freshness_check(check: Any, *, edition_date: date) -> dict[str, Any]:
    """Validate an explicit currentness handoff without requiring an article date."""
    if not isinstance(check, dict) or check.get("status") != "current":
        raise ValueError("freshness_check must explicitly establish current status")
    basis = str(check.get("basis") or "").strip()
    basis_date = _parse_date(check.get("basis_date"))
    if not basis or basis_date is None:
        raise ValueError("freshness_check must include a basis and valid basis_date")
    if check.get("edition_date") != edition_date.isoformat():
        raise ValueError("freshness_check edition_date does not match the queue edition")
    age = (edition_date - basis_date).days
    try:
        reported_age = int(check.get("age_days"))
    except (TypeError, ValueError) as exc:
        raise ValueError("freshness_check.age_days must be an integer") from exc
    if reported_age != age:
        raise ValueError("freshness_check age_days does not match its basis date")
    if basis == "newly_published" and not 0 <= age <= 3:
        raise ValueError("newly published source is outside the 3-day current review window")
    if basis != "newly_published" and not -120 <= age <= 31:
        raise ValueError("event-aware freshness basis is outside the review window")
    return check


def assess_review_retention(record: Mapping[str, Any], *, dispatch: str, edition_date: str) -> dict[str, Any]:
    """Return a conservative, explainable review-retention decision."""
    if dispatch not in {"food-line", "care-line"}:
        raise ValueError("shared source-based qualification is limited to Food Line and Care Line")
    edition = date.fromisoformat(edition_date)
    url = _text(record, "canonical_source_url", "canonical_url", "final_trace_url", "source_url", "item_url", "url")
    publisher = _text(record, "publisher", "discovered_publisher", "source_publisher", "source_name", "direct_source_name", "operator", "agency")
    evidence = _evidence_text(record)
    parsed = urlsplit(url)
    trace_failures = []
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        trace_failures.append("invalid_or_missing_https_url")
    if not publisher:
        trace_failures.append("missing_identifiable_publisher_or_operator")
    if not evidence:
        trace_failures.append("missing_traceable_supporting_evidence")

    lowered = evidence.casefold()
    patterns = FOOD_STRAIN_PATTERNS if dispatch == "food-line" else CARE_STRAIN_PATTERNS
    match = _strain_match(patterns, evidence)
    structured_hint = bool(record.get("concrete_strain_hint"))
    structured_type = _text(record, "pressure_type", "pressure_reason", "qualification_reason").casefold()
    concrete_taxonomy_markers = (
        "closure",
        "reduction",
        "suspension",
        "shortage",
        "insufficient",
        "capacity",
        "benefit disruption",
        "benefit access",
        "access loss",
        "service gap",
        "supply contraction",
        "food loss",
        "affordability pressure",
        "cost pressure",
    )
    if (
        dispatch == "food-line"
        and _text(record, "classification_status") == "qualified_pressure_signal"
        and _text(record, "qualification_reason")
        and any(marker in structured_type for marker in concrete_taxonomy_markers)
    ):
        structured_hint = True
    if dispatch == "care-line" and _text(record, "event_type") and record.get("access_consequences"):
        structured_hint = True
    concrete = bool(match or structured_hint)
    qualification_basis = match[0] if match else (_text(record, "qualification_reason", "pressure_reason", "event_type") if structured_hint else "")

    generic_demand_only = bool(DEMAND_RE.search(evidence)) and not match
    routine_gap = bool(ROUTINE_GAP_RE.search(evidence)) and not ACTIVE_CONDITION_RE.search(evidence)
    speculative_only = bool(SPECULATIVE_RE.search(evidence)) and not CONCRETE_ACTION_RE.search(evidence)
    unsupported = bool(record.get("unsupported_claims")) or _text(record, "claim_support_status").casefold() in {"unsupported", "untraceable"}
    if generic_demand_only:
        concrete = False
        qualification_basis = ""
    if routine_gap or speculative_only:
        concrete = False
        qualification_basis = ""

    freshness = _freshness(record, edition=edition)
    failure_reasons = list(trace_failures)
    if not concrete:
        if generic_demand_only:
            failure_reasons.append("generic_rising_demand_without_access_or_capacity_consequence")
        elif routine_gap:
            failure_reasons.append("routine_scheduled_or_seasonal_gap_without_demonstrated_pressure")
        elif speculative_only:
            failure_reasons.append("speculative_future_impact_without_documented_condition")
        else:
            failure_reasons.append("no_explicit_concrete_strain_evidence")
    if freshness["status"] != "current":
        failure_reasons.append(str(freshness["reason"]))
    if unsupported:
        failure_reasons.append("claim_exceeds_traceable_source_support")

    eligible = not failure_reasons
    if trace_failures:
        disposition = "invalid_source_with_reason"
    elif freshness["status"] != "current" and concrete and not unsupported:
        disposition = "deferred_with_reason"
    elif failure_reasons:
        disposition = "rejected_with_reason"
    else:
        disposition = "retained_for_review"

    uncertainty_parts = []
    supplied_uncertainty = _text(record, "uncertainty_note", "limitations")
    if supplied_uncertainty:
        uncertainty_parts.append(supplied_uncertainty)
    if not _text(record, *SOURCE_DATE_FIELDS):
        uncertainty_parts.append("Source publication date unavailable; do not state or infer one.")
    if eligible:
        uncertainty_parts.append("Review wording must remain limited to the traceable supporting evidence.")
    return {
        "schema_version": "bluefern.shared_source_based_review_retention.v1",
        "dispatch": dispatch,
        "traceable_source": not trace_failures,
        "concrete_strain": concrete,
        "current_relevance": freshness["status"] == "current",
        "claim_restrained": not unsupported,
        "eligible_for_review": eligible,
        "qualification_basis": qualification_basis,
        "freshness_basis": freshness["basis"],
        "freshness_check": freshness,
        "uncertainty_note": " ".join(dict.fromkeys(uncertainty_parts)),
        "failure_reasons": list(dict.fromkeys(failure_reasons)),
        "disposition": disposition,
        "next_transition_owner": "human_editorial_review" if eligible else "",
    }
