from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

DISPATCH_ID = "ice"
SCHEMA_VERSION = 1

ICE_KEYWORDS = (
    "ice", "immigration and customs enforcement", "detention", "detained", "detainee", "deportation", "deported",
    "removal", "removed", "raid", "raids", "arrest", "arrests", "custody", "287(g)", "worksite", "workplace",
    "enforcement operation", "federal register", "immigration enforcement",
)
ICE_KEYWORD_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bice\b",
        r"\bimmigration and customs enforcement\b",
        r"\bimmigration enforcement\b",
        r"\bdetention\b",
        r"\bdetained\b",
        r"\bdetainee\b",
        r"\bdeport(?:ation|ed|s)?\b",
        r"\bremov(?:al|ed|als|e|es|ing)\b",
        r"\b287\(g\)\b",
        r"\bworksite\b",
        r"\bworkplace\b",
        r"\bimmigration enforcement operation\b",
        r"\bfederal register\b",
    )
)
ICE_EVENT_SIGNAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\barrest(?:s|ed|ing)?\b",
        r"\bdetain(?:ed|s|ing)?\b",
        r"\bdetainee\b",
        r"\bdeport(?:ation|ed|s)?\b",
        r"\bremov(?:al|ed|als|e|es|ing)\b",
        r"\braid(?:s)?\b",
        r"\bcustody\b",
        r"\bfacilit(?:y|ies)\b",
        r"\bcontract(?:s|ed|ing)?\b",
        r"\blawsuit\b",
        r"\bjudge\b",
        r"\bcourt\b",
        r"\binvestigation\b",
        r"\baudit\b",
        r"\breport\b",
        r"\bnotice\b",
        r"\brule(?:making)?\b",
        r"\bpolicy\b",
        r"\boperation\b",
        r"\b287\(g\)\b",
        r"\bmedical\b",
        r"\bdeath\b",
        r"\bdied\b",
        r"\bforce\b",
        r"\bshooting\b",
        r"\bcharged?\b",
        r"\breleased on bond\b",
        r"\bhindered\b",
        r"\bdoes not fully address\b",
        r"\bdeficien(?:cy|cies)\b",
    )
)
STATIC_LINK_PATH_FRAGMENTS = (
    "/topics/",
    "/reader-aids/",
    "/practice-advisories/",
)
STATIC_LINK_TITLES = {
    "1-866-dhs-2-ice",
    "ice fallen officers",
    "immigration and customs enforcement",
    "office of the federal register announcements",
    "understanding the federal register",
    "practice advisories",
}
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC", "puerto rico": "PR",
    "guam": "GU", "u.s. virgin islands": "VI", "virgin islands": "VI", "northern mariana islands": "MP", "american samoa": "AS",
}
US_AND_TERRITORIES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "GU", "VI", "MP", "AS",
}

class IceCategory(str, Enum):
    ENFORCEMENT = "enforcement"
    DETENTION = "detention"
    REMOVALS = "removals_deportations"
    FATALITIES_MEDICAL = "fatalities_injuries_medical_events"
    USE_OF_FORCE = "use_of_force"
    LEGAL_OVERSIGHT = "legal_oversight_accountability"
    POLICY_OPERATIONS = "policy_operations"
    VERIFIED_COMMUNITY_IMPACT = "verified_community_impact"

class LocationPrecision(str, Enum):
    EXACT_FACILITY = "exact_facility"
    STREET_OR_SITE = "street_or_site"
    CITY = "city"
    COUNTY = "county"
    STATE_OR_TERRITORY = "state_or_territory"
    MULTI_LOCATION = "multi_location"
    UNKNOWN = "unknown"

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    SOURCE_TRACEABLE = "source_traceable"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"
    EXCLUDED = "excluded"

class CollectionHealth(str, Enum):
    HEALTHY = "healthy"
    LIMITED_SOURCE_UPDATE = "limited_source_update"
    COLLECTION_DEGRADED = "collection_degraded"
    COLLECTION_FAILED = "collection_failed"

class PublicationDecision(str, Enum):
    ELIGIBLE_FOR_EDITORIAL_REVIEW = "eligible_for_editorial_review"
    NO_PUBLICATION_NEEDED = "no_publication_needed"
    COLLECTION_DEGRADED = "collection_degraded"
    COLLECTION_FAILED = "collection_failed"

class EventRelationship(str, Enum):
    NEW_EVENT = "new_event"
    UPDATE_TO_EXISTING_EVENT = "update_to_existing_event"
    DUPLICATE_COVERAGE = "duplicate_coverage"
    CORRECTION = "correction"
    FOLLOW_UP_WITH_NEW_FACTS = "follow_up_with_new_material_facts"

@dataclass(frozen=True)
class IceSourceObservation:
    source_url: str
    canonical_source_url: str
    publisher: str
    source_type: str
    tier: int
    published_at: str | None = None
    retrieved_at: str | None = None
    exact_supporting_passage: str | None = None
    archive_url: str | None = None
    reference_metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class IceLocation:
    country: str = "US"
    state_or_territory: str | None = None
    county_or_equivalent: str | None = None
    city: str | None = None
    facility_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_precision: LocationPrecision = LocationPrecision.UNKNOWN
    geography_source: str | None = None
    geography_provenance: str | None = None

@dataclass(frozen=True)
class IceImpact:
    arrests_count: int | None = None
    detained_count: int | None = None
    removed_count: int | None = None
    fatalities_count: int | None = None
    injuries_count: int | None = None
    hospitalized_count: int | None = None
    children_affected_count: int | None = None
    other_quantitative_impact: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class IceAgencies:
    ice: bool = False
    dhs: bool = False
    cbp: bool = False
    state_local_agencies: tuple[str, ...] = ()
    other_federal_agencies: tuple[str, ...] = ()
    contractors: tuple[str, ...] = ()
    facility_operators: tuple[str, ...] = ()

@dataclass(frozen=True)
class IceEditorialState:
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    corroboration_count: int = 0
    source_quality: str | None = None
    geographic_confidence: str | None = None
    event_confidence: str | None = None
    public_eligibility: bool = False
    exclusion_reason: str | None = None
    curation_notes: str | None = None

@dataclass(frozen=True)
class IceLineage:
    fingerprint: str
    canonical_event_id: str | None = None
    related_event_ids: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    corrected_by: tuple[str, ...] = ()
    edition_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class IceEvent:
    event_id: str
    dispatch: str
    event_date: str | None
    event_time: str | None
    first_observed_at: str
    last_updated_at: str
    location: IceLocation
    primary_category: IceCategory
    secondary_categories: tuple[IceCategory, ...]
    event_type: str
    severity: Severity
    status: str
    impact: IceImpact
    agencies: IceAgencies
    sources: tuple[IceSourceObservation, ...]
    editorial: IceEditorialState
    lineage: IceLineage
    schema_version: int = SCHEMA_VERSION

@dataclass(frozen=True)
class CorrectionRecord:
    corrected_event_id: str
    superseded_fact: str
    revised_fact: str
    correction_evidence_url: str
    correction_evidence_passage: str
    corrected_at: str
    affected_edition_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class ProviderHealth:
    source_id: str
    publisher: str
    tier: int
    attempted: bool
    success: bool
    accepted_records: int = 0
    failed_records: int = 0
    http_status: int | None = None
    retry_attempts: int = 0
    error: str | None = None

@dataclass(frozen=True)
class CollectionReport:
    run_id: str
    started_at: str
    completed_at: str
    configured_providers: int
    attempted_providers: int
    successful_providers: int
    failed_providers: int
    accepted_records: int
    publisher_count: int
    source_tier_diversity: tuple[int, ...]
    geography_coverage: tuple[str, ...]
    category_coverage: tuple[str, ...]
    shared_service_failures: tuple[str, ...]
    provider_health: tuple[ProviderHealth, ...]
    health: CollectionHealth


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _slug(value: Any) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "unknown"


def canonicalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(_clean(url))
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path or "/")
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    )
    return urllib.parse.urlunsplit((scheme, netloc, path.rstrip("/") or "/", query, ""))


def _hash_payload(payload: Any, length: int = 16) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:length]


def stable_event_fingerprint(candidate: dict[str, Any]) -> str:
    location = candidate.get("location") or {}
    agencies = candidate.get("agencies") or {}
    payload = {
        "dispatch": DISPATCH_ID,
        "event_type": _slug(candidate.get("event_type")),
        "event_date": candidate.get("event_date"),
        "state_or_territory": _slug(location.get("state_or_territory")),
        "county_or_equivalent": _slug(location.get("county_or_equivalent")),
        "city": _slug(location.get("city")),
        "facility_name": _slug(location.get("facility_name")),
        "agencies": sorted([_slug(x) for x in agencies.get("state_local_agencies", []) + agencies.get("other_federal_agencies", [])]),
    }
    return f"ice-{_hash_payload(payload, 20)}"


def _parse_category(value: str) -> IceCategory:
    normalized = _slug(value).replace("-", "_")
    aliases = {"removals": "removals_deportations", "legal": "legal_oversight_accountability", "policy": "policy_operations"}
    normalized = aliases.get(normalized, normalized)
    return IceCategory(normalized)


def _parse_precision(value: str | None) -> LocationPrecision:
    if not value:
        return LocationPrecision.UNKNOWN
    return LocationPrecision(_slug(value).replace("-", "_"))


def _nonnull_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def normalize_ice_candidate(candidate: dict[str, Any], *, observed_at: str | None = None) -> IceEvent:
    observed = observed_at or utc_now()
    sources_in = candidate.get("sources") or []
    if not sources_in:
        raise ValueError("ICE events require at least one traceable source")
    sources = tuple(
        IceSourceObservation(
            source_url=_clean(src["source_url"]),
            canonical_source_url=_clean(src.get("canonical_source_url") or canonicalize_url(src["source_url"])),
            publisher=_clean(src["publisher"]),
            source_type=_clean(src.get("source_type") or "unknown"),
            tier=int(src.get("tier") or 3),
            published_at=src.get("published_at"),
            retrieved_at=src.get("retrieved_at") or observed,
            exact_supporting_passage=src.get("exact_supporting_passage"),
            archive_url=src.get("archive_url"),
            reference_metadata=dict(src.get("reference_metadata") or {}),
        )
        for src in sources_in
    )
    location_in = candidate.get("location") or {}
    state = location_in.get("state_or_territory")
    if state and str(state).upper() not in US_AND_TERRITORIES:
        raise ValueError(f"ICE event outside supported U.S./territory geography: {state}")
    lat = location_in.get("latitude")
    lon = location_in.get("longitude")
    if (lat is not None or lon is not None) and not location_in.get("geography_source"):
        raise ValueError("Coordinates require geography_source provenance")
    location = IceLocation(
        country=location_in.get("country") or "US",
        state_or_territory=str(state).upper() if state else None,
        county_or_equivalent=location_in.get("county_or_equivalent"),
        city=location_in.get("city"),
        facility_name=location_in.get("facility_name"),
        latitude=float(lat) if lat is not None else None,
        longitude=float(lon) if lon is not None else None,
        location_precision=_parse_precision(location_in.get("location_precision")),
        geography_source=location_in.get("geography_source"),
        geography_provenance=location_in.get("geography_provenance"),
    )
    impact_in = candidate.get("impact") or {}
    impact = IceImpact(
        arrests_count=_nonnull_int(impact_in.get("arrests_count")),
        detained_count=_nonnull_int(impact_in.get("detained_count")),
        removed_count=_nonnull_int(impact_in.get("removed_count")),
        fatalities_count=_nonnull_int(impact_in.get("fatalities_count")),
        injuries_count=_nonnull_int(impact_in.get("injuries_count")),
        hospitalized_count=_nonnull_int(impact_in.get("hospitalized_count")),
        children_affected_count=_nonnull_int(impact_in.get("children_affected_count")),
        other_quantitative_impact=dict(impact_in.get("other_quantitative_impact") or {}),
    )
    agencies_in = candidate.get("agencies") or {}
    agencies = IceAgencies(
        ice=bool(agencies_in.get("ice")),
        dhs=bool(agencies_in.get("dhs")),
        cbp=bool(agencies_in.get("cbp")),
        state_local_agencies=tuple(agencies_in.get("state_local_agencies") or ()),
        other_federal_agencies=tuple(agencies_in.get("other_federal_agencies") or ()),
        contractors=tuple(agencies_in.get("contractors") or ()),
        facility_operators=tuple(agencies_in.get("facility_operators") or ()),
    )
    primary = _parse_category(candidate["primary_category"])
    secondary = tuple(_parse_category(v) for v in candidate.get("secondary_categories") or ())
    severity = classify_severity(primary, impact, candidate.get("event_type", ""), candidate.get("severity"))
    verification = VerificationStatus(candidate.get("verification_status") or VerificationStatus.SOURCE_TRACEABLE.value)
    corroboration = int(candidate.get("corroboration_count") or len({s.canonical_source_url for s in sources}))
    source_quality = source_quality_for_tier(min(s.tier for s in sources))
    public_eligible, exclusion = event_public_eligibility(
        primary_category=primary,
        verification_status=verification,
        sources=sources,
        corroboration_count=corroboration,
        within_scope=bool(candidate.get("within_scope", True)),
        duplicate=bool(candidate.get("duplicate", False)),
    )
    editorial = IceEditorialState(
        verification_status=verification,
        corroboration_count=corroboration,
        source_quality=source_quality,
        geographic_confidence=candidate.get("geographic_confidence"),
        event_confidence=candidate.get("event_confidence"),
        public_eligibility=public_eligible,
        exclusion_reason=exclusion,
        curation_notes=candidate.get("curation_notes"),
    )
    fingerprint = stable_event_fingerprint(candidate)
    event_id = candidate.get("event_id") or fingerprint
    lineage = IceLineage(
        fingerprint=fingerprint,
        canonical_event_id=candidate.get("canonical_event_id") or event_id,
        related_event_ids=tuple(candidate.get("related_event_ids") or ()),
        supersedes=tuple(candidate.get("supersedes") or ()),
        corrected_by=tuple(candidate.get("corrected_by") or ()),
        edition_ids=tuple(candidate.get("edition_ids") or ()),
    )
    return IceEvent(
        event_id=event_id,
        dispatch=DISPATCH_ID,
        event_date=candidate.get("event_date"),
        event_time=candidate.get("event_time"),
        first_observed_at=candidate.get("first_observed_at") or observed,
        last_updated_at=candidate.get("last_updated_at") or observed,
        location=location,
        primary_category=primary,
        secondary_categories=secondary,
        event_type=_clean(candidate["event_type"]),
        severity=severity,
        status=_clean(candidate.get("status") or "reported"),
        impact=impact,
        agencies=agencies,
        sources=sources,
        editorial=editorial,
        lineage=lineage,
    )


def source_quality_for_tier(tier: int) -> str:
    return {1: "official_or_primary", 2: "established_reporting", 3: "advocacy_or_community_requires_corroboration"}.get(tier, "unknown")


def classify_severity(category: IceCategory, impact: IceImpact, event_type: str, explicit: str | None = None) -> Severity:
    if explicit:
        return Severity(explicit)
    event_text = _clean(event_type).lower()
    if (impact.fatalities_count or 0) > 0 or "death" in event_text or "fatal" in event_text:
        return Severity.CRITICAL
    if (impact.injuries_count or 0) > 0 or (impact.hospitalized_count or 0) > 0 or "shooting" in event_text or "firearm" in event_text:
        return Severity.HIGH
    if category in {IceCategory.ENFORCEMENT, IceCategory.DETENTION, IceCategory.REMOVALS, IceCategory.LEGAL_OVERSIGHT, IceCategory.POLICY_OPERATIONS}:
        if any((impact.arrests_count, impact.detained_count, impact.removed_count)):
            total = sum(v or 0 for v in (impact.arrests_count, impact.detained_count, impact.removed_count))
            if total >= 25:
                return Severity.HIGH
        return Severity.MEDIUM
    return Severity.LOW


def event_public_eligibility(*, primary_category: IceCategory, verification_status: VerificationStatus, sources: Iterable[IceSourceObservation], corroboration_count: int, within_scope: bool, duplicate: bool) -> tuple[bool, str | None]:
    source_list = list(sources)
    if not within_scope:
        return False, "outside_ice_scope"
    if duplicate:
        return False, "duplicate_coverage"
    if not source_list or any(not s.source_url or not s.exact_supporting_passage for s in source_list):
        return False, "missing_traceable_support"
    if verification_status not in {VerificationStatus.SOURCE_TRACEABLE, VerificationStatus.CORROBORATED, VerificationStatus.VERIFIED}:
        return False, "not_verified"
    if min(s.tier for s in source_list) >= 3 and corroboration_count < 2:
        return False, "tier3_requires_corroboration"
    return True, None


def edition_publication_decision(events: Iterable[IceEvent], collection_health: CollectionHealth) -> tuple[PublicationDecision, str]:
    event_list = list(events)
    if collection_health == CollectionHealth.COLLECTION_FAILED:
        return PublicationDecision.COLLECTION_FAILED, "collection failed before editorial assessment"
    if collection_health == CollectionHealth.COLLECTION_DEGRADED:
        return PublicationDecision.COLLECTION_DEGRADED, "collection degraded; do not convert failure into no-publication"
    eligible = [e for e in event_list if e.editorial.public_eligibility]
    if any(e.severity in {Severity.CRITICAL, Severity.HIGH} for e in eligible):
        return PublicationDecision.ELIGIBLE_FOR_EDITORIAL_REVIEW, "verified high/critical event present"
    medium = [e for e in eligible if e.severity == Severity.MEDIUM]
    if len(medium) >= 3:
        return PublicationDecision.ELIGIBLE_FOR_EDITORIAL_REVIEW, "coherent group of multiple medium verified events"
    if any(e.primary_category == IceCategory.LEGAL_OVERSIGHT and e.severity == Severity.MEDIUM for e in eligible):
        return PublicationDecision.ELIGIBLE_FOR_EDITORIAL_REVIEW, "material legal/oversight development requires review"
    return PublicationDecision.NO_PUBLICATION_NEEDED, "no verified event group meets ICE event-driven publication threshold"


def compare_event_observation(existing: IceEvent | None, candidate: IceEvent) -> tuple[EventRelationship, list[str]]:
    if existing is None:
        return EventRelationship.NEW_EVENT, ["no matching event fingerprint"]
    if existing.lineage.fingerprint != candidate.lineage.fingerprint:
        return EventRelationship.NEW_EVENT, ["different event fingerprint"]
    reasons: list[str] = []
    if candidate.status != existing.status:
        reasons.append("status changed")
    if candidate.event_date and candidate.event_date != existing.event_date:
        reasons.append("event date changed")
    for field_name in ("arrests_count", "detained_count", "removed_count", "fatalities_count", "injuries_count", "hospitalized_count"):
        if getattr(candidate.impact, field_name) is not None and getattr(candidate.impact, field_name) != getattr(existing.impact, field_name):
            reasons.append(f"{field_name} changed")
    new_urls = {s.canonical_source_url for s in candidate.sources} - {s.canonical_source_url for s in existing.sources}
    if candidate.lineage.supersedes:
        return EventRelationship.CORRECTION, ["candidate supersedes prior fact"] + reasons
    if reasons:
        return EventRelationship.UPDATE_TO_EXISTING_EVENT, reasons
    if new_urls:
        return EventRelationship.FOLLOW_UP_WITH_NEW_FACTS, ["new source attached to same event"]
    return EventRelationship.DUPLICATE_COVERAGE, ["same event fingerprint and source set"]


def map_ready_event(event: IceEvent) -> dict[str, Any]:
    location = event.location
    return {
        "event_id": event.event_id,
        "dispatch": event.dispatch,
        "event_date": event.event_date,
        "primary_category": event.primary_category.value,
        "severity": event.severity.value,
        "state_or_territory": location.state_or_territory,
        "facility_name": location.facility_name,
        "event_type": event.event_type,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "location_precision": location.location_precision.value,
        "geography_source": location.geography_source,
        "geography_provenance": location.geography_provenance,
    }


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def event_to_dict(event: IceEvent) -> dict[str, Any]:
    return _plain(event)


def evaluate_collection_health(provider_health: Iterable[ProviderHealth], *, shared_service_failures: Iterable[str] = ()) -> CollectionHealth:
    providers = list(provider_health)
    if not providers:
        return CollectionHealth.COLLECTION_FAILED
    attempted = [p for p in providers if p.attempted]
    if not attempted:
        return CollectionHealth.COLLECTION_FAILED
    successes = [p for p in attempted if p.success]
    failures = [p for p in attempted if not p.success]
    if not successes:
        return CollectionHealth.COLLECTION_FAILED
    if shared_service_failures or len(failures) >= max(2, len(attempted) // 2):
        return CollectionHealth.COLLECTION_DEGRADED
    if failures or sum(p.accepted_records for p in successes) == 0:
        return CollectionHealth.LIMITED_SOURCE_UPDATE
    return CollectionHealth.HEALTHY


def build_collection_report(run_id: str, provider_health: list[ProviderHealth], events: list[IceEvent], *, started_at: str, completed_at: str, shared_service_failures: Iterable[str] = ()) -> CollectionReport:
    attempted = [p for p in provider_health if p.attempted]
    successful = [p for p in attempted if p.success]
    failed = [p for p in attempted if not p.success]
    return CollectionReport(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        configured_providers=len(provider_health),
        attempted_providers=len(attempted),
        successful_providers=len(successful),
        failed_providers=len(failed),
        accepted_records=len(events),
        publisher_count=len({s.publisher for e in events for s in e.sources}),
        source_tier_diversity=tuple(sorted({s.tier for e in events for s in e.sources})),
        geography_coverage=tuple(sorted({e.location.state_or_territory for e in events if e.location.state_or_territory})),
        category_coverage=tuple(sorted({e.primary_category.value for e in events})),
        shared_service_failures=tuple(shared_service_failures),
        provider_health=tuple(provider_health),
        health=evaluate_collection_health(provider_health, shared_service_failures=shared_service_failures),
    )


def load_source_registry(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text) or {}
    sources = payload.get("sources") or []
    if not isinstance(sources, list):
        raise ValueError("ICE source registry must contain a sources list")
    ids: set[str] = set()
    for source in sources:
        required = {"source_id", "publisher", "source_type", "enabled", "tier", "geography", "categories", "collection_mechanism", "expected_cadence"}
        missing = sorted(required - set(source))
        if missing:
            raise ValueError(f"source {source.get('source_id') or '<unknown>'} missing {missing}")
        if source["source_id"] in ids:
            raise ValueError(f"duplicate source_id: {source['source_id']}")
        ids.add(source["source_id"])
        if source["collection_mechanism"] in {"rss", "html", "api"} and not source.get("url"):
            raise ValueError(f"source {source['source_id']} requires url")
        if source["collection_mechanism"] == "documented_manual" and not source.get("documentation_url"):
            raise ValueError(f"source {source['source_id']} requires documentation_url")
    return sources


def validate_endpoint(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "BlueFern-ICE-Phase1-Diagnostic/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - operator-selected registry URL validation
            return {"url": url, "ok": 200 <= int(response.status) < 400, "status": int(response.status), "error": None}
    except urllib.error.HTTPError as exc:
        return {"url": url, "ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - environment/network dependent
        return {"url": url, "ok": False, "status": None, "error": str(exc)}



@dataclass(frozen=True)
class FetchResult:
    url: str
    ok: bool
    status: int | None
    content: str
    error: str | None
    fetch_stack: str


def _decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "windows-1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def fetch_url_secure(url: str, *, timeout: float = 15.0) -> FetchResult:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "BlueFern-ICE-Phase2-Diagnostic/1.0"})
    urllib_error: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec - TLS remains verified by Python
            body = response.read(750_000)
            return FetchResult(url=url, ok=200 <= int(response.status) < 400, status=int(response.status), content=_decode_bytes(body), error=None, fetch_stack="python_urllib")
    except Exception as exc:  # pragma: no cover - platform/network dependent
        urllib_error = str(exc)
    ps = """
$ProgressPreference = 'SilentlyContinue'
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $targetUrl = [string]$env:BLUEFERN_ICE_FETCH_URL
  $timeoutSec = [int]$env:BLUEFERN_ICE_FETCH_TIMEOUT
  $resp = Invoke-WebRequest -Uri $targetUrl -Method Get -UseBasicParsing -TimeoutSec $timeoutSec -MaximumRedirection 5
  $content = [string]$resp.Content
  if ($content.Length -gt 750000) { $content = $content.Substring(0, 750000) }
  @{ok=($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 400); status=[int]$resp.StatusCode; url=[string]$resp.BaseResponse.ResponseUri.AbsoluteUri; content=$content; error=$null} | ConvertTo-Json -Compress -Depth 3
  exit 0
} catch {
  $status = $null
  if ($_.Exception.Response -and $_.Exception.Response.StatusCode) { $status = [int]$_.Exception.Response.StatusCode }
  @{ok=$false; status=$status; url=[string]$env:BLUEFERN_ICE_FETCH_URL; content=''; error=$_.Exception.Message} | ConvertTo-Json -Compress -Depth 3
  exit 0
}
"""
    try:
        env = dict(os.environ)
        env["BLUEFERN_ICE_FETCH_URL"] = url
        env["BLUEFERN_ICE_FETCH_TIMEOUT"] = str(int(timeout))
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout + 5,
            env=env,
        )
        payload = json.loads(completed.stdout.strip()) if completed.stdout.strip() else {}
        return FetchResult(
            url=str(payload.get("url") or url),
            ok=bool(payload.get("ok")),
            status=payload.get("status"),
            content=str(payload.get("content") or ""),
            error=payload.get("error") or urllib_error,
            fetch_stack="windows_invoke_webrequest_after_urllib_failure",
        )
    except Exception as exc:  # pragma: no cover - platform/network dependent
        return FetchResult(url=url, ok=False, status=None, content="", error=f"urllib failed: {urllib_error}; powershell failed: {exc}", fetch_stack="failed")


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _clean(html.unescape(text))


def _anchor_candidates(source: dict[str, Any], html_text: str) -> list[dict[str, str]]:
    base = source.get("url") or source.get("documentation_url") or ""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'(?is)<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text):
        href, label_html = match.groups()
        title = _strip_html(label_html)
        if len(title) < 12:
            continue
        absolute = urllib.parse.urljoin(base, html.unescape(href))
        canonical = canonicalize_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append({"title": title, "url": absolute, "canonical_url": canonical})
    return rows


def _keyword_hit(text: str) -> bool:
    return any(pattern.search(text) for pattern in ICE_KEYWORD_PATTERNS)


def _event_signal_hit(text: str) -> bool:
    return _keyword_hit(text) and any(pattern.search(text) for pattern in ICE_EVENT_SIGNAL_PATTERNS)


def _is_fetchable_article_link(link: dict[str, str]) -> bool:
    parsed = urllib.parse.urlparse(link["canonical_url"])
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    title = _clean(link["title"]).lower()
    if title in STATIC_LINK_TITLES:
        return False
    if any(fragment in path for fragment in STATIC_LINK_PATH_FRAGMENTS):
        return False
    if parsed.netloc.endswith("federalregister.gov") and not re.search(r"/documents/\d{4}/", path):
        return False
    return True


def _extract_passage(html_text: str, title: str) -> str | None:
    plain = _strip_html(html_text)
    title_words = [w for w in re.findall(r"[A-Za-z0-9]+", title.lower()) if len(w) > 3]
    sentences = re.split(r"(?<=[.!?])\s+", plain)
    for sentence in sentences:
        lower = sentence.lower()
        if _keyword_hit(lower) and (not title_words or any(word in lower for word in title_words[:8])):
            return sentence[:700]
    for sentence in sentences:
        if _keyword_hit(sentence):
            return sentence[:700]
    return title[:700]


def _infer_category(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ("death", "died", "fatal", "medical", "hospital", "suicide")):
        return IceCategory.FATALITIES_MEDICAL.value
    if any(term in lower for term in ("shooting", "firearm", "force", "taser", "pursuit")):
        return IceCategory.USE_OF_FORCE.value
    if any(term in lower for term in ("detention", "detainee", "facility", "custody", "contract", "jail")):
        return IceCategory.DETENTION.value
    if any(term in lower for term in ("deport", "removal", "removed", "repatriation", "flight")):
        return IceCategory.REMOVALS.value
    if any(term in lower for term in ("court", "lawsuit", "judge", "settlement", "investigation", "oig", "rights", "oversight")):
        return IceCategory.LEGAL_OVERSIGHT.value
    if any(term in lower for term in ("policy", "rule", "federal register", "287(g)", "standards")):
        return IceCategory.POLICY_OPERATIONS.value
    if any(term in lower for term in ("school", "farm", "workforce", "business", "community")):
        return IceCategory.VERIFIED_COMMUNITY_IMPACT.value
    return IceCategory.ENFORCEMENT.value


def _infer_location(text: str) -> dict[str, Any]:
    lower = text.lower()
    for name, code in sorted(STATE_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return {"state_or_territory": code, "location_precision": LocationPrecision.STATE_OR_TERRITORY.value}
    for code in US_AND_TERRITORIES:
        if re.search(rf"\b{re.escape(code)}\b", text):
            return {"state_or_territory": code, "location_precision": LocationPrecision.STATE_OR_TERRITORY.value}
    return {"location_precision": LocationPrecision.UNKNOWN.value}


def _infer_impact(text: str) -> dict[str, int | None]:
    impact: dict[str, int | None] = {}
    patterns = {
        "arrests_count": r"\b(\d{1,4})\s+(?:people\s+)?(?:were\s+)?arrest",
        "detained_count": r"\b(\d{1,4})\s+(?:people\s+)?(?:were\s+)?detain",
        "removed_count": r"\b(\d{1,4})\s+(?:people\s+)?(?:were\s+)?(?:removed|deported)",
        "fatalities_count": r"\b(\d{1,3})\s+(?:people\s+)?(?:died|deaths|fatalit)",
        "injuries_count": r"\b(\d{1,3})\s+(?:people\s+)?(?:injured|injur)",
        "hospitalized_count": r"\b(\d{1,3})\s+(?:people\s+)?(?:hospitalized|sent to hospital|taken to hospital)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            impact[field] = int(match.group(1))
    return impact


def _candidate_from_link(source: dict[str, Any], link: dict[str, str], article: FetchResult, retrieved_at: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not _is_fetchable_article_link(link):
        return None, {"source_id": source["source_id"], "source_url": link["url"], "title": link["title"], "reason": "non_article_or_static_link"}
    passage = _extract_passage(article.content, link["title"])
    combined = f"{link['title']} {passage or ''}"
    if not _keyword_hit(combined):
        return None, {"source_id": source["source_id"], "source_url": link["url"], "title": link["title"], "reason": "no_ice_keyword_in_title_or_passage"}
    if not _event_signal_hit(combined):
        return None, {"source_id": source["source_id"], "source_url": link["url"], "title": link["title"], "reason": "no_event_level_ice_signal"}
    category = _infer_category(combined)
    candidate = {
        "event_date": None,
        "event_type": link["title"],
        "primary_category": category,
        "status": "reported",
        "location": _infer_location(combined),
        "impact": _infer_impact(combined),
        "agencies": {"ice": _keyword_hit(combined), "dhs": "dhs" in combined.lower(), "cbp": "cbp" in combined.lower()},
        "sources": [{
            "source_url": link["url"],
            "canonical_source_url": link["canonical_url"],
            "publisher": source["publisher"],
            "source_type": source["source_type"],
            "tier": int(source["tier"]),
            "published_at": None,
            "retrieved_at": retrieved_at,
            "exact_supporting_passage": passage,
            "reference_metadata": {"source_id": source["source_id"], "index_url": source.get("url"), "fetch_stack": article.fetch_stack},
        }],
        "verification_status": "source_traceable",
        "geographic_confidence": "textual_inference" if (_infer_location(combined).get("state_or_territory")) else "unknown",
        "event_confidence": "needs_manual_review",
    }
    return candidate, None


def collect_live_candidates(sources: list[dict[str, Any]], *, max_per_source: int = 3, timeout: float = 15.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ProviderHealth], list[dict[str, Any]]]:
    retrieved_at = utc_now()
    raw_candidates: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    provider_health: list[ProviderHealth] = []
    fetch_results: list[dict[str, Any]] = []
    for source in sources:
        attempted = bool(source.get("enabled")) and source.get("collection_mechanism") != "documented_manual" and bool(source.get("url"))
        if not attempted:
            provider_health.append(ProviderHealth(source_id=source["source_id"], publisher=source["publisher"], tier=int(source["tier"]), attempted=False, success=False))
            continue
        index = fetch_url_secure(source["url"], timeout=timeout)
        fetch_results.append({"source_id": source["source_id"], "url": index.url, "ok": index.ok, "status": index.status, "fetch_stack": index.fetch_stack, "error": index.error})
        if not index.ok:
            provider_health.append(ProviderHealth(source_id=source["source_id"], publisher=source["publisher"], tier=int(source["tier"]), attempted=True, success=False, http_status=index.status, error=index.error))
            continue
        links = [link for link in _anchor_candidates(source, index.content) if _keyword_hit(link["title"])]
        accepted = 0
        failed_records = 0
        for link in links[:max_per_source]:
            if not _is_fetchable_article_link(link):
                exclusions.append({"source_id": source["source_id"], "source_url": link["url"], "title": link["title"], "reason": "non_article_or_static_link"})
                continue
            article = fetch_url_secure(link["url"], timeout=timeout)
            fetch_results.append({"source_id": source["source_id"], "url": article.url, "ok": article.ok, "status": article.status, "fetch_stack": article.fetch_stack, "error": article.error})
            if not article.ok:
                failed_records += 1
                exclusions.append({"source_id": source["source_id"], "source_url": link["url"], "title": link["title"], "reason": "article_fetch_failed", "status": article.status, "error": article.error})
                continue
            candidate, exclusion = _candidate_from_link(source, link, article, retrieved_at)
            if candidate:
                raw_candidates.append(candidate)
                accepted += 1
            elif exclusion:
                exclusions.append(exclusion)
        provider_health.append(ProviderHealth(source_id=source["source_id"], publisher=source["publisher"], tier=int(source["tier"]), attempted=True, success=True, accepted_records=accepted, failed_records=failed_records, http_status=index.status))
    return raw_candidates, exclusions, provider_health, fetch_results

def run_diagnostic(registry_path: Path, *, fixture_path: Path | None = None, output_dir: Path | None = None, validate_endpoints: bool = False, live: bool = False, max_per_source: int = 3) -> dict[str, Any]:
    started = utc_now()
    sources = load_source_registry(registry_path)
    endpoint_results = []
    fetch_results: list[dict[str, Any]] = []
    provider_health: list[ProviderHealth] = []
    raw_candidates: list[dict[str, Any]] = []
    collection_exclusions: list[dict[str, Any]] = []
    if live:
        raw_candidates, collection_exclusions, provider_health, fetch_results = collect_live_candidates(sources, max_per_source=max_per_source)
    else:
        for source in sources:
            attempted = bool(source.get("enabled")) and source.get("collection_mechanism") != "documented_manual"
            endpoint_result = None
            if validate_endpoints and attempted and source.get("url"):
                endpoint_result = validate_endpoint(source["url"])
                endpoint_results.append({"source_id": source["source_id"], **endpoint_result})
            success = bool(endpoint_result["ok"]) if endpoint_result else attempted
            provider_health.append(
                ProviderHealth(
                    source_id=source["source_id"],
                    publisher=source["publisher"],
                    tier=int(source["tier"]),
                    attempted=attempted,
                    success=success,
                    accepted_records=0,
                    http_status=endpoint_result.get("status") if endpoint_result else None,
                    error=endpoint_result.get("error") if endpoint_result else None,
                )
            )
        if fixture_path:
            raw_candidates = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
    normalized: list[IceEvent] = []
    exclusions: list[dict[str, Any]] = list(collection_exclusions)
    for candidate in raw_candidates:
        try:
            normalized.append(normalize_ice_candidate(candidate, observed_at=started))
        except Exception as exc:
            exclusions.append({"candidate": candidate, "reason": str(exc)})
    if fixture_path and not live:
        # attribute fixture candidates to the first enabled provider for a deterministic non-public diagnostic
        for idx, health in enumerate(provider_health):
            if health.attempted and normalized:
                provider_health[idx] = ProviderHealth(**{**asdict(health), "accepted_records": len(normalized)})
                break
    completed = utc_now()
    report = build_collection_report(
        run_id=f"ice-diagnostic-{_hash_payload({'started': started, 'events': [e.event_id for e in normalized]}, 12)}",
        provider_health=provider_health,
        events=normalized,
        started_at=started,
        completed_at=completed,
    )
    decision, reason = edition_publication_decision(normalized, report.health)
    result = {
        "run_manifest": _plain(report),
        "raw_candidates": raw_candidates,
        "normalized_events": [event_to_dict(e) for e in normalized],
        "exclusions": exclusions,
        "endpoint_validation": endpoint_results,
        "fetch_results": fetch_results,
        "publication_decision": decision.value,
        "publication_decision_reason": reason,
        "public_side_effects": False,
    }
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "run_manifest.json").write_text(json.dumps(result["run_manifest"], indent=2, sort_keys=True), encoding="utf-8")
        (output_dir / "raw_candidates.json").write_text(json.dumps(raw_candidates, indent=2, sort_keys=True), encoding="utf-8")
        (output_dir / "normalized_events.json").write_text(json.dumps(result["normalized_events"], indent=2, sort_keys=True), encoding="utf-8")
        (output_dir / "exclusions.json").write_text(json.dumps(exclusions, indent=2, sort_keys=True), encoding="utf-8")
        (output_dir / "fetch_results.json").write_text(json.dumps(fetch_results, indent=2, sort_keys=True), encoding="utf-8")
    return result

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a non-public ICE Dispatch Phase 1 collection diagnostic.")
    parser.add_argument("--registry", type=Path, default=Path("data/dispatches/ice/sources.yml"))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-endpoints", action="store_true")
    parser.add_argument("--live", action="store_true", help="Fetch and extract bounded live candidates from enabled registry sources.")
    parser.add_argument("--max-per-source", type=int, default=3)
    args = parser.parse_args(argv)
    result = run_diagnostic(args.registry, fixture_path=args.fixture, output_dir=args.output_dir, validate_endpoints=args.validate_endpoints, live=args.live, max_per_source=args.max_per_source)
    print(json.dumps({"run_id": result["run_manifest"]["run_id"], "health": result["run_manifest"]["health"], "publication_decision": result["publication_decision"], "normalized_events": len(result["normalized_events"]), "exclusions": len(result["exclusions"]), "public_side_effects": False}, sort_keys=True))
    return 0

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
