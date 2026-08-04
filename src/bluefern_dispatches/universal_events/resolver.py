from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from bluefern_dispatches.story_dedupe import similarity

from .normalization import normalize_identifier, normalize_name, normalized_address_parts
from .orm import (
    EntityMentionRow,
    LocationAliasRow,
    LocationIdentifierRow,
    LocationRow,
    OrganizationAliasRow,
    OrganizationIdentifierRow,
    OrganizationRow,
)


RESOLVER_VERSION = "entity-resolver-v1"


@dataclass(frozen=True)
class ResolverThresholds:
    auto_match_threshold: float = 0.92
    review_threshold: float = 0.55
    ambiguity_margin: float = 0.08
    top_n: int = 5


@dataclass(frozen=True)
class MatchResult:
    entity_kind: str
    organization_id: str | None
    location_id: str | None
    score: float
    method: str
    features: dict[str, Any]

    @property
    def target_id(self) -> str:
        return self.organization_id or self.location_id or ""


def _mention_identifier_pairs(mention: EntityMentionRow) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for scheme, value in (mention.external_identifiers_json or {}).items():
        normalized = normalize_identifier(scheme, value)
        if normalized:
            out.append((str(scheme).casefold(), normalized))
    return sorted(out)


def _address_key_from_location(location: LocationRow) -> str:
    return normalized_address_parts(
        address_line_1=location.address_line_1,
        address_line_2=location.address_line_2,
        locality=location.city,
        region=location.state or location.region,
        postal_code=location.postal_code,
        country_code=location.country_code or location.country,
    )


def _address_key_from_mention(mention: EntityMentionRow) -> str:
    return normalized_address_parts(
        address_line_1=mention.address_line_1 or mention.raw_address,
        address_line_2=mention.address_line_2,
        locality=mention.locality,
        region=mention.region,
        postal_code=mention.postal_code,
        country_code=mention.country_code,
    )


def generate_matches(
    session: Session,
    mention: EntityMentionRow,
    *,
    resolver_version: str = RESOLVER_VERSION,
    thresholds: ResolverThresholds = ResolverThresholds(),
) -> list[MatchResult]:
    if mention.entity_kind == "organization":
        return _organization_matches(session, mention, thresholds=thresholds)
    if mention.entity_kind == "location":
        return _location_matches(session, mention, thresholds=thresholds)
    raise ValueError(f"unsupported entity kind: {mention.entity_kind}")


def _organization_matches(session: Session, mention: EntityMentionRow, *, thresholds: ResolverThresholds) -> list[MatchResult]:
    rows = list(
        session.execute(
            select(OrganizationRow).options(
                selectinload(OrganizationRow.aliases),
                selectinload(OrganizationRow.identifiers),
                selectinload(OrganizationRow.primary_location),
            )
        ).scalars()
    )
    mention_name = mention.normalized_name or normalize_name(mention.raw_name)
    mention_identifiers = set(_mention_identifier_pairs(mention))
    mention_address = _address_key_from_mention(mention)
    results: list[MatchResult] = []
    for org in rows:
        if org.merge_status == "merged" or org.merged_into_organization_id:
            continue
        features: dict[str, Any] = {}
        score = 0.0
        method = "no_match"
        org_identifiers = {
            (identifier.identifier_scheme.casefold(), identifier.normalized_value)
            for identifier in org.identifiers
            if identifier.normalized_value
        }
        authoritative_conflict = False
        for scheme, value in mention_identifiers:
            for identifier in org.identifiers:
                if not identifier.is_authoritative or identifier.identifier_scheme.casefold() != scheme:
                    continue
                if identifier.normalized_value != value:
                    authoritative_conflict = True
        if mention_identifiers and mention_identifiers & org_identifiers:
            score = 1.0
            method = "exact_identifier"
            features["exact_identifier"] = sorted(mention_identifiers & org_identifiers)
        elif authoritative_conflict:
            score = 0.0
            method = "authoritative_identifier_conflict"
            features["blocking_rule"] = "authoritative_identifier_conflict"
        else:
            aliases = {alias.normalized_alias for alias in org.aliases if alias.normalized_alias}
            if mention_name and mention_name in aliases:
                score = max(score, 0.95)
                method = "exact_alias"
                features["exact_alias"] = mention_name
            canonical = org.normalized_canonical_name or normalize_name(org.canonical_name)
            if mention_name and mention_name == canonical:
                score = max(score, 0.9)
                method = "exact_canonical_name" if method == "no_match" else method
                features["exact_canonical_name"] = canonical
            if mention_address and org.primary_location and _address_key_from_location(org.primary_location) == mention_address:
                score = max(score, 0.88 if features else 0.72)
                method = "name_address" if features else "address_context"
                features["exact_address"] = mention_address
            if mention.locality and mention.region and org.primary_location:
                same_city_region = (
                    normalize_name(mention.locality) == normalize_name(org.primary_location.city)
                    and normalize_name(mention.region) == normalize_name(org.primary_location.state or org.primary_location.region)
                )
                if same_city_region and mention_name and similarity(mention_name, canonical) >= 0.86:
                    score = max(score, 0.82)
                    method = "name_city_region" if method == "no_match" else method
                    features["city_region"] = True
            if mention_name and canonical and similarity(mention_name, canonical) >= 0.9:
                score = max(score, 0.7)
                method = "fuzzy_name" if method == "no_match" else method
                features["name_similarity"] = round(similarity(mention_name, canonical), 3)
        if score >= thresholds.review_threshold:
            results.append(MatchResult("organization", org.organization_id, None, round(score, 4), method, features))
    return sorted(results, key=lambda item: (-item.score, item.target_id))[: thresholds.top_n]


def _location_matches(session: Session, mention: EntityMentionRow, *, thresholds: ResolverThresholds) -> list[MatchResult]:
    rows = list(
        session.execute(
            select(LocationRow).options(selectinload(LocationRow.aliases), selectinload(LocationRow.identifiers))
        ).scalars()
    )
    mention_name = mention.normalized_name or normalize_name(mention.raw_name)
    mention_identifiers = set(_mention_identifier_pairs(mention))
    mention_address = _address_key_from_mention(mention)
    results: list[MatchResult] = []
    for location in rows:
        if location.merged_into_location_id:
            continue
        features: dict[str, Any] = {}
        score = 0.0
        method = "no_match"
        identifiers = {
            (identifier.identifier_scheme.casefold(), identifier.normalized_value)
            for identifier in location.identifiers
            if identifier.normalized_value
        }
        if mention_identifiers and mention_identifiers & identifiers:
            score = 1.0
            method = "exact_identifier"
            features["exact_identifier"] = sorted(mention_identifiers & identifiers)
        elif mention_address and mention_address == _address_key_from_location(location):
            score = 0.94
            method = "exact_address"
            features["exact_address"] = mention_address
        else:
            aliases = {alias.normalized_alias for alias in location.aliases if alias.normalized_alias}
            canonical = location.normalized_canonical_name or normalize_name(location.canonical_name)
            region_conflict = bool(
                mention.region
                and (location.state or location.region)
                and normalize_name(mention.region)
                not in {
                    normalize_name(location.state),
                    normalize_name(location.region),
                }
            )
            if region_conflict:
                continue
            if mention_name and mention_name in aliases:
                score = max(score, 0.9)
                method = "exact_alias"
                features["exact_alias"] = mention_name
            if mention_name and mention_name == canonical:
                hierarchy_ok = not mention.region or normalize_name(mention.region) in {
                    normalize_name(location.state),
                    normalize_name(location.region),
                }
                if hierarchy_ok:
                    score = max(score, 0.88)
                    method = "exact_name_hierarchy"
                    features["administrative_hierarchy"] = True
            if mention_name and canonical and similarity(mention_name, canonical) >= 0.92:
                score = max(score, 0.65)
                method = "fuzzy_location_name" if method == "no_match" else method
                features["name_similarity"] = round(similarity(mention_name, canonical), 3)
        if score >= thresholds.review_threshold:
            results.append(MatchResult("location", None, location.location_id, round(score, 4), method, features))
    return sorted(results, key=lambda item: (-item.score, item.target_id))[: thresholds.top_n]


def can_auto_match(matches: list[MatchResult], *, thresholds: ResolverThresholds = ResolverThresholds()) -> bool:
    if not matches:
        return False
    top = matches[0]
    if top.method == "authoritative_identifier_conflict":
        return False
    if top.score < thresholds.auto_match_threshold:
        return False
    if len(matches) == 1:
        return True
    return (top.score - matches[1].score) >= thresholds.ambiguity_margin
