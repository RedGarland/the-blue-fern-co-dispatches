from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bluefern_dispatches.story_dedupe import normalize_text, normalize_url, similarity

from .enums import CandidateStatus, EvidenceRole, EventDomain, EventStatus, VerificationStatus
from .normalization import normalize_country_code, normalize_identifier, normalize_name
from .orm import (
    CandidateEventRow,
    EntityMatchCandidateRow,
    EntityMentionRow,
    EntityResolutionDecisionRow,
    EventAttributeRow,
    EventEntityLinkRow,
    EventEvidenceRow,
    EventRelationshipRow,
    EventRow,
    LocationRow,
    LocationAliasRow,
    LocationIdentifierRow,
    OrganizationAliasRow,
    OrganizationIdentifierRow,
    OrganizationLocationRelationshipRow,
    OrganizationMergeRow,
    OrganizationRelationshipRow,
    OrganizationRow,
    ReviewRow,
    SourceItemRow,
    SourceRow,
    utc_now,
)
from .repository import SQLiteUniversalEventRepository
from .resolver import RESOLVER_VERSION, ResolverThresholds, can_auto_match, generate_matches
from .schemas import (
    CandidateEventCreate,
    CandidateEventRead,
    EffectiveResolutionRead,
    EntityMatchCandidateRead,
    EntityMentionCreate,
    EntityMentionRead,
    EntityResolutionDecisionCreate,
    EntityResolutionDecisionRead,
    EventAttributeCreate,
    EventAttributeRead,
    EventCreate,
    EventEntityLinkCreate,
    EventEntityLinkRead,
    EventEvidenceCreate,
    EventEvidenceRead,
    EventRead,
    EventRelationshipCreate,
    EventRelationshipRead,
    LocationCreate,
    LocationAliasCreate,
    LocationAliasRead,
    LocationIdentifierCreate,
    LocationIdentifierRead,
    LocationRead,
    OrganizationCreate,
    OrganizationAliasCreate,
    OrganizationAliasRead,
    OrganizationIdentifierCreate,
    OrganizationIdentifierRead,
    OrganizationLocationRelationshipCreate,
    OrganizationLocationRelationshipRead,
    OrganizationMergeCreate,
    OrganizationMergeRead,
    OrganizationRelationshipCreate,
    OrganizationRelationshipRead,
    OrganizationRead,
    SourceCreate,
    SourceItemCreate,
    SourceItemRead,
    SourceRead,
)


EXPORT_SCHEMA_VERSION = "bluefern.universal_events.v1"
ENTITY_RESOLUTION_EXPORT_SCHEMA_VERSION = "bluefern.universal_events.v2"


def _stable_id(prefix: str, *parts: Any) -> str:
    def _part_text(part: Any) -> str:
        if isinstance(part, (dict, list, tuple)):
            return json.dumps(part, sort_keys=True, default=str)
        return str(part or "").strip()

    raw = "|".join(_part_text(part) for part in parts)
    digest = sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _ensure_model(model: Any, model_cls):
    if isinstance(model, model_cls):
        return model
    if isinstance(model, Mapping):
        return model_cls.model_validate(model)
    if is_dataclass(model):
        return model_cls.model_validate(asdict(model))
    return model_cls.model_validate(model)


def _review_decision_for_status(status: CandidateStatus) -> CandidateStatus:
    return status


def _event_filter_values(values: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for value in values:
        if isinstance(value, str):
            out.add(value)
        else:
            out.add(str(value))
    return out


def _coerce_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class UniversalEventService:
    def __init__(self, repository: SQLiteUniversalEventRepository):
        self.repository = repository

    def create_source(self, source: SourceCreate | Mapping[str, Any]) -> SourceRead:
        payload = _ensure_model(source, SourceCreate)
        canonical_url = normalize_url(payload.canonical_url) or payload.canonical_url.strip()
        source_id = payload.source_id or _stable_id("source", canonical_url, payload.name, payload.publisher)
        with self.repository.session_scope() as session:
            existing = session.get(SourceRow, source_id)
            if existing is None and canonical_url:
                existing = session.execute(select(SourceRow).where(SourceRow.canonical_url == canonical_url)).scalar_one_or_none()
            if existing is not None:
                return SourceRead.model_validate(existing)
            row = SourceRow(
                source_id=source_id,
                name=payload.name,
                publisher=payload.publisher,
                canonical_url=canonical_url,
                source_type=payload.source_type,
                content_hash=payload.content_hash,
                discovered_at=payload.discovered_at,
                published_at=payload.published_at,
                retrieved_at=payload.retrieved_at,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return SourceRead.model_validate(row)

    def create_source_item(self, source_item: SourceItemCreate | Mapping[str, Any]) -> SourceItemRead:
        payload = _ensure_model(source_item, SourceItemCreate)
        canonical_url = normalize_url(payload.canonical_url) or payload.canonical_url.strip()
        source_item_id = payload.source_item_id or _stable_id("source_item", payload.source_id, canonical_url, payload.content_hash)
        with self.repository.session_scope() as session:
            if session.get(SourceItemRow, source_item_id) is not None:
                return SourceItemRead.model_validate(session.get(SourceItemRow, source_item_id))
            source = session.get(SourceRow, payload.source_id)
            if source is None:
                raise ValueError(f"source not found: {payload.source_id}")
            existing = session.execute(
                select(SourceItemRow).where(
                    (SourceItemRow.canonical_url == canonical_url)
                    | (SourceItemRow.content_hash == payload.content_hash)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return SourceItemRead.model_validate(existing)
            row = SourceItemRow(
                source_item_id=source_item_id,
                source_id=payload.source_id,
                source_url=payload.source_url or canonical_url,
                canonical_url=canonical_url,
                content_hash=payload.content_hash,
                title=payload.title,
                supporting_passage=payload.supporting_passage,
                discovered_at=payload.discovered_at,
                published_at=payload.published_at,
                retrieved_at=payload.retrieved_at,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return SourceItemRead.model_validate(row)

    def create_location(self, location: LocationCreate | Mapping[str, Any]) -> LocationRead:
        payload = _ensure_model(location, LocationCreate)
        normalized_canonical_name = payload.normalized_canonical_name or normalize_name(payload.canonical_name)
        country_code = normalize_country_code(payload.country_code or payload.country)
        location_id = payload.location_id or _stable_id(
            "location",
            normalized_canonical_name,
            payload.location_type,
            payload.address_line_1,
            payload.city,
            payload.state or payload.region,
            payload.postal_code,
            country_code,
        )
        canonical_name = payload.canonical_name.strip()
        with self.repository.session_scope() as session:
            existing = session.get(LocationRow, location_id)
            if existing is None:
                existing = session.execute(select(LocationRow).where(LocationRow.canonical_name == canonical_name)).scalar_one_or_none()
            if existing is not None:
                return LocationRead.model_validate(existing)
            row = LocationRow(
                location_id=location_id,
                canonical_name=canonical_name,
                normalized_canonical_name=normalized_canonical_name,
                display_name=payload.display_name,
                address_line_1=payload.address_line_1,
                address_line_2=payload.address_line_2,
                postal_code=payload.postal_code,
                country_code=country_code,
                location_type=payload.location_type,
                country=payload.country,
                region=payload.region,
                state=payload.state,
                county=payload.county,
                city=payload.city,
                latitude=payload.latitude,
                longitude=payload.longitude,
                merged_into_location_id=payload.merged_into_location_id,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return LocationRead.model_validate(row)

    def create_organization(self, organization: OrganizationCreate | Mapping[str, Any]) -> OrganizationRead:
        payload = _ensure_model(organization, OrganizationCreate)
        normalized_canonical_name = payload.normalized_canonical_name or normalize_name(payload.canonical_name)
        canonical_domain = normalize_identifier("domain", payload.canonical_domain) if payload.canonical_domain else ""
        organization_id = payload.organization_id or _stable_id("org", normalized_canonical_name, canonical_domain, payload.organization_type)
        with self.repository.session_scope() as session:
            existing = session.get(OrganizationRow, organization_id)
            if existing is None:
                existing = session.execute(select(OrganizationRow).where(OrganizationRow.canonical_name == payload.canonical_name.strip())).scalar_one_or_none()
            if existing is not None:
                return OrganizationRead.model_validate(existing)
            row = OrganizationRow(
                organization_id=organization_id,
                canonical_name=payload.canonical_name.strip(),
                normalized_canonical_name=normalized_canonical_name,
                display_name=payload.display_name,
                organization_type=payload.organization_type,
                parent_organization_id=payload.parent_organization_id,
                primary_location_id=payload.primary_location_id,
                operational_status=payload.operational_status,
                canonical_domain=canonical_domain,
                merged_into_organization_id=payload.merged_into_organization_id,
                merge_status=payload.merge_status,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return OrganizationRead.model_validate(row)

    def add_organization_alias(self, alias: OrganizationAliasCreate | Mapping[str, Any]) -> OrganizationAliasRead:
        payload = _ensure_model(alias, OrganizationAliasCreate)
        normalized_alias = payload.normalized_alias or normalize_name(payload.alias_name)
        if not normalized_alias:
            raise ValueError("organization alias cannot be blank")
        alias_id = payload.alias_id or _stable_id("org_alias", payload.organization_id, normalized_alias, payload.alias_type, payload.source_item_id)
        with self.repository.session_scope() as session:
            existing = session.get(OrganizationAliasRow, alias_id)
            if existing is not None:
                return OrganizationAliasRead.model_validate(existing)
            if session.get(OrganizationRow, payload.organization_id) is None:
                raise ValueError(f"organization not found: {payload.organization_id}")
            row = OrganizationAliasRow(
                alias_id=alias_id,
                organization_id=payload.organization_id,
                alias_name=payload.alias_name,
                normalized_alias=normalized_alias,
                alias_type=payload.alias_type,
                source_item_id=payload.source_item_id,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                is_primary=payload.is_primary,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return OrganizationAliasRead.model_validate(row)

    def add_organization_identifier(self, identifier: OrganizationIdentifierCreate | Mapping[str, Any]) -> OrganizationIdentifierRead:
        payload = _ensure_model(identifier, OrganizationIdentifierCreate)
        scheme = payload.identifier_scheme.strip().casefold()
        normalized_value = payload.normalized_value or normalize_identifier(scheme, payload.identifier_value)
        if not scheme or not normalized_value:
            raise ValueError("organization identifier scheme and value are required")
        identifier_id = payload.organization_identifier_id or _stable_id(
            "org_identifier", payload.organization_id, scheme, normalized_value, payload.source_item_id
        )
        with self.repository.session_scope() as session:
            existing = session.get(OrganizationIdentifierRow, identifier_id)
            if existing is not None:
                return OrganizationIdentifierRead.model_validate(existing)
            if session.get(OrganizationRow, payload.organization_id) is None:
                raise ValueError(f"organization not found: {payload.organization_id}")
            row = OrganizationIdentifierRow(
                organization_identifier_id=identifier_id,
                organization_id=payload.organization_id,
                identifier_scheme=scheme,
                identifier_value=payload.identifier_value,
                normalized_value=normalized_value,
                source_item_id=payload.source_item_id,
                is_authoritative=payload.is_authoritative,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return OrganizationIdentifierRead.model_validate(row)

    def add_location_alias(self, alias: LocationAliasCreate | Mapping[str, Any]) -> LocationAliasRead:
        payload = _ensure_model(alias, LocationAliasCreate)
        normalized_alias = payload.normalized_alias or normalize_name(payload.alias_name)
        if not normalized_alias:
            raise ValueError("location alias cannot be blank")
        alias_id = payload.location_alias_id or _stable_id("location_alias", payload.location_id, normalized_alias, payload.alias_type, payload.source_item_id)
        with self.repository.session_scope() as session:
            existing = session.get(LocationAliasRow, alias_id)
            if existing is not None:
                return LocationAliasRead.model_validate(existing)
            if session.get(LocationRow, payload.location_id) is None:
                raise ValueError(f"location not found: {payload.location_id}")
            row = LocationAliasRow(
                location_alias_id=alias_id,
                location_id=payload.location_id,
                alias_name=payload.alias_name,
                normalized_alias=normalized_alias,
                alias_type=payload.alias_type,
                source_item_id=payload.source_item_id,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return LocationAliasRead.model_validate(row)

    def add_location_identifier(self, identifier: LocationIdentifierCreate | Mapping[str, Any]) -> LocationIdentifierRead:
        payload = _ensure_model(identifier, LocationIdentifierCreate)
        scheme = payload.identifier_scheme.strip().casefold()
        normalized_value = payload.normalized_value or normalize_identifier(scheme, payload.identifier_value)
        if not scheme or not normalized_value:
            raise ValueError("location identifier scheme and value are required")
        identifier_id = payload.location_identifier_id or _stable_id("location_identifier", payload.location_id, scheme, normalized_value, payload.source_item_id)
        with self.repository.session_scope() as session:
            existing = session.get(LocationIdentifierRow, identifier_id)
            if existing is not None:
                return LocationIdentifierRead.model_validate(existing)
            if session.get(LocationRow, payload.location_id) is None:
                raise ValueError(f"location not found: {payload.location_id}")
            row = LocationIdentifierRow(
                location_identifier_id=identifier_id,
                location_id=payload.location_id,
                identifier_scheme=scheme,
                identifier_value=payload.identifier_value,
                normalized_value=normalized_value,
                source_item_id=payload.source_item_id,
                is_authoritative=payload.is_authoritative,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return LocationIdentifierRead.model_validate(row)

    def ingest_entity_mention(self, mention: EntityMentionCreate | Mapping[str, Any]) -> EntityMentionRead:
        payload = _ensure_model(mention, EntityMentionCreate)
        if payload.entity_kind not in {"organization", "location"}:
            raise ValueError("entity_kind must be organization or location")
        normalized_name = payload.normalized_name or normalize_name(payload.raw_name)
        if not normalized_name:
            raise ValueError("entity mention raw_name cannot be blank")
        mention_id = payload.mention_id or _stable_id(
            "mention",
            payload.candidate_id,
            payload.source_item_id,
            payload.entity_kind,
            payload.mention_role,
            normalized_name,
            payload.raw_address,
        )
        with self.repository.session_scope() as session:
            existing = session.get(EntityMentionRow, mention_id)
            if existing is not None:
                return EntityMentionRead.model_validate(existing)
            if session.get(CandidateEventRow, payload.candidate_id) is None:
                raise ValueError(f"candidate not found: {payload.candidate_id}")
            if payload.source_item_id and session.get(SourceItemRow, payload.source_item_id) is None:
                raise ValueError(f"source item not found: {payload.source_item_id}")
            row = EntityMentionRow(
                mention_id=mention_id,
                candidate_id=payload.candidate_id,
                source_item_id=payload.source_item_id,
                entity_kind=payload.entity_kind,
                mention_role=payload.mention_role,
                raw_name=payload.raw_name,
                normalized_name=normalized_name,
                raw_address=payload.raw_address,
                address_line_1=payload.address_line_1,
                address_line_2=payload.address_line_2,
                locality=payload.locality,
                region=payload.region,
                postal_code=payload.postal_code,
                country_code=normalize_country_code(payload.country_code),
                latitude=payload.latitude,
                longitude=payload.longitude,
                external_identifiers_json=dict(payload.external_identifiers),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return EntityMentionRead.model_validate(row)

    def generate_match_candidates(
        self,
        mention_id: str,
        *,
        resolver_version: str = RESOLVER_VERSION,
        thresholds: ResolverThresholds = ResolverThresholds(),
    ) -> list[EntityMatchCandidateRead]:
        with self.repository.session_scope() as session:
            mention = session.get(EntityMentionRow, mention_id)
            if mention is None:
                raise ValueError(f"mention not found: {mention_id}")
            generated_at = mention.created_at
            matches = generate_matches(session, mention, resolver_version=resolver_version, thresholds=thresholds)
            reads: list[EntityMatchCandidateRead] = []
            for index, match in enumerate(matches, start=1):
                match_id = _stable_id("match", mention_id, match.organization_id, match.location_id, resolver_version)
                row = session.get(EntityMatchCandidateRow, match_id)
                if row is None:
                    row = EntityMatchCandidateRow(
                        match_candidate_id=match_id,
                        mention_id=mention_id,
                        entity_kind=match.entity_kind,
                        organization_id=match.organization_id,
                        location_id=match.location_id,
                        match_score=match.score,
                        match_method=match.method,
                        match_features_json=dict(match.features),
                        rank=index,
                        generated_at=generated_at,
                        resolver_version=resolver_version,
                    )
                    session.add(row)
                    session.flush()
                reads.append(EntityMatchCandidateRead.model_validate(row))
            return sorted(reads, key=lambda row: (row.rank, row.match_candidate_id))

    def resolve_mention(
        self,
        decision: EntityResolutionDecisionCreate | Mapping[str, Any],
    ) -> EntityResolutionDecisionRead:
        payload = _ensure_model(decision, EntityResolutionDecisionCreate)
        decision_id = payload.resolution_decision_id or _stable_id(
            "resolution",
            payload.mention_id,
            payload.decision_type,
            payload.organization_id,
            payload.location_id,
            payload.selected_match_candidate_id,
            payload.created_at.isoformat(),
            payload.reviewer,
        )
        with self.repository.session_scope() as session:
            existing = session.get(EntityResolutionDecisionRow, decision_id)
            if existing is not None:
                return EntityResolutionDecisionRead.model_validate(existing)
            mention = session.get(EntityMentionRow, payload.mention_id)
            if mention is None:
                raise ValueError(f"mention not found: {payload.mention_id}")
            if payload.supersedes_decision_id and payload.supersedes_decision_id == decision_id:
                raise ValueError("resolution decisions cannot supersede themselves")
            if payload.supersedes_decision_id and session.get(EntityResolutionDecisionRow, payload.supersedes_decision_id) is None:
                raise ValueError(f"superseded decision not found: {payload.supersedes_decision_id}")
            if payload.organization_id and mention.entity_kind != "organization":
                raise ValueError("organization decision does not match mention kind")
            if payload.location_id and mention.entity_kind != "location":
                raise ValueError("location decision does not match mention kind")
            if payload.organization_id and session.get(OrganizationRow, payload.organization_id) is None:
                raise ValueError(f"organization not found: {payload.organization_id}")
            if payload.location_id and session.get(LocationRow, payload.location_id) is None:
                raise ValueError(f"location not found: {payload.location_id}")
            row = EntityResolutionDecisionRow(
                resolution_decision_id=decision_id,
                mention_id=payload.mention_id,
                decision_type=payload.decision_type,
                organization_id=payload.organization_id,
                location_id=payload.location_id,
                selected_match_candidate_id=payload.selected_match_candidate_id,
                confidence=payload.confidence,
                decision_reason=payload.decision_reason,
                reviewer=payload.reviewer,
                resolver_version=payload.resolver_version,
                created_at=payload.created_at,
                supersedes_decision_id=payload.supersedes_decision_id,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return EntityResolutionDecisionRead.model_validate(row)

    def defer_resolution(self, mention_id: str, *, reviewer: str, reason: str = "", created_at: Any | None = None) -> EntityResolutionDecisionRead:
        return self.resolve_mention(
            {
                "mention_id": mention_id,
                "decision_type": "deferred",
                "confidence": 0.0,
                "decision_reason": reason,
                "reviewer": reviewer,
                "resolver_version": RESOLVER_VERSION,
                "created_at": _coerce_datetime(created_at) or utc_now(),
            }
        )

    def reject_match(
        self,
        mention_id: str,
        *,
        selected_match_candidate_id: str | None = None,
        reviewer: str,
        reason: str = "",
        created_at: Any | None = None,
    ) -> EntityResolutionDecisionRead:
        return self.resolve_mention(
            {
                "mention_id": mention_id,
                "decision_type": "rejected_match",
                "selected_match_candidate_id": selected_match_candidate_id,
                "confidence": 0.0,
                "decision_reason": reason,
                "reviewer": reviewer,
                "resolver_version": RESOLVER_VERSION,
                "created_at": _coerce_datetime(created_at) or utc_now(),
            }
        )

    def correct_resolution(
        self,
        prior_decision_id: str,
        *,
        organization_id: str | None = None,
        location_id: str | None = None,
        reviewer: str,
        reason: str,
        created_at: Any | None = None,
    ) -> EntityResolutionDecisionRead:
        with self.repository.session_scope() as session:
            prior = session.get(EntityResolutionDecisionRow, prior_decision_id)
            if prior is None:
                raise ValueError(f"prior resolution decision not found: {prior_decision_id}")
            mention_id = prior.mention_id
        return self.resolve_mention(
            {
                "mention_id": mention_id,
                "decision_type": "corrected",
                "organization_id": organization_id,
                "location_id": location_id,
                "confidence": 1.0,
                "decision_reason": reason,
                "reviewer": reviewer,
                "resolver_version": RESOLVER_VERSION,
                "created_at": _coerce_datetime(created_at) or utc_now(),
                "supersedes_decision_id": prior_decision_id,
            }
        )

    def get_resolution_history(self, mention_id: str) -> list[EntityResolutionDecisionRead]:
        with self.repository.session_scope() as session:
            rows = list(
                session.execute(
                    select(EntityResolutionDecisionRow)
                    .where(EntityResolutionDecisionRow.mention_id == mention_id)
                    .order_by(EntityResolutionDecisionRow.created_at, EntityResolutionDecisionRow.resolution_decision_id)
                ).scalars()
            )
            return [EntityResolutionDecisionRead.model_validate(row) for row in rows]

    def get_effective_resolution(self, mention_id: str) -> EffectiveResolutionRead:
        with self.repository.session_scope() as session:
            mention = session.get(EntityMentionRow, mention_id)
            if mention is None:
                raise ValueError(f"mention not found: {mention_id}")
            row = session.execute(
                select(EntityResolutionDecisionRow)
                .where(EntityResolutionDecisionRow.mention_id == mention_id)
                .order_by(EntityResolutionDecisionRow.created_at.desc(), EntityResolutionDecisionRow.resolution_decision_id.desc())
            ).scalars().first()
            organization = session.get(OrganizationRow, row.organization_id) if row and row.organization_id else None
            location = session.get(LocationRow, row.location_id) if row and row.location_id else None
            return EffectiveResolutionRead(
                mention_id=mention_id,
                decision=EntityResolutionDecisionRead.model_validate(row) if row else None,
                organization=OrganizationRead.model_validate(organization) if organization else None,
                location=LocationRead.model_validate(location) if location else None,
            )

    def create_organization_from_mention(self, mention_id: str, *, reviewer: str, created_at: Any | None = None) -> EntityResolutionDecisionRead:
        with self.repository.session_scope() as session:
            mention = session.get(EntityMentionRow, mention_id)
            if mention is None:
                raise ValueError(f"mention not found: {mention_id}")
            if mention.entity_kind != "organization":
                raise ValueError("mention is not an organization")
        org = self.create_organization(
            {
                "canonical_name": mention.raw_name,
                "organization_type": "unknown",
            }
        )
        self.add_organization_alias({"organization_id": org.organization_id, "alias_name": mention.raw_name, "alias_type": "source_name", "source_item_id": mention.source_item_id})
        return self.resolve_mention(
            {
                "mention_id": mention_id,
                "decision_type": "created_new",
                "organization_id": org.organization_id,
                "confidence": 1.0,
                "decision_reason": "Created canonical organization from source mention.",
                "reviewer": reviewer,
                "resolver_version": RESOLVER_VERSION,
                "created_at": _coerce_datetime(created_at) or utc_now(),
            }
        )

    def create_location_from_mention(self, mention_id: str, *, reviewer: str, created_at: Any | None = None) -> EntityResolutionDecisionRead:
        with self.repository.session_scope() as session:
            mention = session.get(EntityMentionRow, mention_id)
            if mention is None:
                raise ValueError(f"mention not found: {mention_id}")
            if mention.entity_kind != "location":
                raise ValueError("mention is not a location")
        location = self.create_location(
            {
                "canonical_name": mention.raw_name,
                "address_line_1": mention.address_line_1 or mention.raw_address,
                "city": mention.locality,
                "state": mention.region,
                "postal_code": mention.postal_code,
                "country_code": mention.country_code,
                "country": mention.country_code,
                "location_type": "unknown",
                "latitude": mention.latitude,
                "longitude": mention.longitude,
            }
        )
        self.add_location_alias({"location_id": location.location_id, "alias_name": mention.raw_name, "alias_type": "source_name", "source_item_id": mention.source_item_id})
        return self.resolve_mention(
            {
                "mention_id": mention_id,
                "decision_type": "created_new",
                "location_id": location.location_id,
                "confidence": 1.0,
                "decision_reason": "Created canonical location from source mention.",
                "reviewer": reviewer,
                "resolver_version": RESOLVER_VERSION,
                "created_at": _coerce_datetime(created_at) or utc_now(),
            }
        )

    def add_organization_relationship(self, relationship: OrganizationRelationshipCreate | Mapping[str, Any]) -> OrganizationRelationshipRead:
        payload = _ensure_model(relationship, OrganizationRelationshipCreate)
        if payload.from_organization_id == payload.to_organization_id:
            raise ValueError("organization relationships cannot self-link")
        relationship_id = payload.organization_relationship_id or _stable_id(
            "org_relationship", payload.from_organization_id, payload.to_organization_id, payload.relationship_type, payload.valid_from
        )
        with self.repository.session_scope() as session:
            existing = session.get(OrganizationRelationshipRow, relationship_id)
            if existing is not None:
                return OrganizationRelationshipRead.model_validate(existing)
            if session.get(OrganizationRow, payload.from_organization_id) is None:
                raise ValueError(f"organization not found: {payload.from_organization_id}")
            if session.get(OrganizationRow, payload.to_organization_id) is None:
                raise ValueError(f"organization not found: {payload.to_organization_id}")
            row = OrganizationRelationshipRow(
                organization_relationship_id=relationship_id,
                from_organization_id=payload.from_organization_id,
                to_organization_id=payload.to_organization_id,
                relationship_type=payload.relationship_type,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                source_item_id=payload.source_item_id,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return OrganizationRelationshipRead.model_validate(row)

    def add_organization_location_relationship(self, relationship: OrganizationLocationRelationshipCreate | Mapping[str, Any]) -> OrganizationLocationRelationshipRead:
        payload = _ensure_model(relationship, OrganizationLocationRelationshipCreate)
        relationship_id = payload.organization_location_relationship_id or _stable_id(
            "org_location", payload.organization_id, payload.location_id, payload.relationship_type, payload.valid_from
        )
        with self.repository.session_scope() as session:
            existing = session.get(OrganizationLocationRelationshipRow, relationship_id)
            if existing is not None:
                return OrganizationLocationRelationshipRead.model_validate(existing)
            if session.get(OrganizationRow, payload.organization_id) is None:
                raise ValueError(f"organization not found: {payload.organization_id}")
            if session.get(LocationRow, payload.location_id) is None:
                raise ValueError(f"location not found: {payload.location_id}")
            row = OrganizationLocationRelationshipRow(
                organization_location_relationship_id=relationship_id,
                organization_id=payload.organization_id,
                location_id=payload.location_id,
                relationship_type=payload.relationship_type,
                valid_from=payload.valid_from,
                valid_to=payload.valid_to,
                source_item_id=payload.source_item_id,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return OrganizationLocationRelationshipRead.model_validate(row)

    def merge_organizations(self, merge: OrganizationMergeCreate | Mapping[str, Any]) -> OrganizationMergeRead:
        payload = _ensure_model(merge, OrganizationMergeCreate)
        if payload.survivor_organization_id == payload.merged_organization_id:
            raise ValueError("cannot merge an organization into itself")
        merge_id = payload.organization_merge_id or _stable_id("org_merge", payload.survivor_organization_id, payload.merged_organization_id)
        with self.repository.session_scope() as session:
            existing = session.get(OrganizationMergeRow, merge_id)
            if existing is not None:
                return OrganizationMergeRead.model_validate(existing)
            survivor = session.get(OrganizationRow, payload.survivor_organization_id)
            merged = session.get(OrganizationRow, payload.merged_organization_id)
            if survivor is None:
                raise ValueError(f"survivor organization not found: {payload.survivor_organization_id}")
            if merged is None:
                raise ValueError(f"merged organization not found: {payload.merged_organization_id}")
            if survivor.merged_into_organization_id == merged.organization_id:
                raise ValueError("organization merge cycle")
            survivor_ids = {
                (identifier.identifier_scheme, identifier.normalized_value)
                for identifier in survivor.identifiers
                if identifier.is_authoritative
            }
            merged_ids = {
                (identifier.identifier_scheme, identifier.normalized_value)
                for identifier in merged.identifiers
                if identifier.is_authoritative
            }
            schemes = {scheme for scheme, _ in survivor_ids & {(scheme, value) for scheme, value in survivor_ids}}
            for scheme in {item[0] for item in survivor_ids} & {item[0] for item in merged_ids}:
                if {value for s, value in survivor_ids if s == scheme} != {value for s, value in merged_ids if s == scheme}:
                    raise ValueError("conflicting authoritative identifiers prevent unsafe merge")
            row = OrganizationMergeRow(
                organization_merge_id=merge_id,
                survivor_organization_id=payload.survivor_organization_id,
                merged_organization_id=payload.merged_organization_id,
                reviewer=payload.reviewer,
                reason=payload.reason,
                source_item_id=payload.source_item_id,
                created_at=payload.created_at,
                metadata_json=dict(payload.metadata),
            )
            merged.merged_into_organization_id = survivor.organization_id
            merged.merge_status = "merged"
            session.add(row)
            session.flush()
            session.refresh(row)
            return OrganizationMergeRead.model_validate(row)

    def attach_event_entity_link(self, link: EventEntityLinkCreate | Mapping[str, Any]) -> EventEntityLinkRead:
        payload = _ensure_model(link, EventEntityLinkCreate)
        link_id = payload.event_entity_link_id or _stable_id(
            "event_entity", payload.event_id, payload.mention_id, payload.entity_role, payload.organization_id, payload.location_id
        )
        with self.repository.session_scope() as session:
            existing = session.get(EventEntityLinkRow, link_id)
            if existing is not None:
                return EventEntityLinkRead.model_validate(existing)
            event = session.get(EventRow, payload.event_id)
            if event is None:
                raise ValueError(f"event not found: {payload.event_id}")
            if event.candidate_id != payload.candidate_id:
                raise ValueError("event candidate_id does not match link candidate_id")
            decision = session.get(EntityResolutionDecisionRow, payload.resolution_decision_id)
            if decision is None:
                raise ValueError(f"resolution decision not found: {payload.resolution_decision_id}")
            if decision.mention_id != payload.mention_id:
                raise ValueError("resolution decision does not belong to mention")
            organization_id = payload.organization_id
            location_id = payload.location_id
            if organization_id:
                organization = session.get(OrganizationRow, organization_id)
                if organization is None:
                    raise ValueError(f"organization not found: {organization_id}")
                if organization.merged_into_organization_id:
                    organization_id = organization.merged_into_organization_id
            if location_id:
                location = session.get(LocationRow, location_id)
                if location is None:
                    raise ValueError(f"location not found: {location_id}")
                if location.merged_into_location_id:
                    location_id = location.merged_into_location_id
            row = EventEntityLinkRow(
                event_entity_link_id=link_id,
                event_id=payload.event_id,
                candidate_id=payload.candidate_id,
                mention_id=payload.mention_id,
                resolution_decision_id=payload.resolution_decision_id,
                entity_kind=payload.entity_kind,
                entity_role=payload.entity_role,
                organization_id=organization_id,
                location_id=location_id,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return EventEntityLinkRead.model_validate(row)

    def submit_candidate(self, candidate: CandidateEventCreate | Mapping[str, Any]) -> CandidateEventRead:
        payload = _ensure_model(candidate, CandidateEventCreate)
        source_item_id = payload.source_item_id.strip()
        candidate_id = payload.candidate_id or _stable_id(
            "candidate",
            source_item_id,
            payload.domain.value,
            normalize_text(payload.title),
            normalize_url(payload.metadata.get("canonical_url") or ""),
        )
        source_item_ids = list(dict.fromkeys([source_item_id, *payload.source_item_ids]))
        with self.repository.session_scope() as session:
            existing = session.get(CandidateEventRow, candidate_id)
            if existing is not None:
                return CandidateEventRead.model_validate(existing)
            source_item = session.get(SourceItemRow, source_item_id)
            if source_item is None:
                raise ValueError(f"source item not found: {source_item_id}")
            location = session.get(LocationRow, payload.location_id) if payload.location_id else None
            organization = session.get(OrganizationRow, payload.organization_id) if payload.organization_id else None
            row = CandidateEventRow(
                candidate_id=candidate_id,
                source_item_id=source_item_id,
                domain=payload.domain,
                title=payload.title,
                summary=payload.summary,
                candidate_status=payload.candidate_status,
                verification_status=payload.verification_status,
                event_status=payload.event_status,
                source_item_ids_json=source_item_ids,
                location_id=location.location_id if location else None,
                organization_id=organization.organization_id if organization else None,
                discovered_at=payload.discovered_at,
                published_at=payload.published_at,
                metadata_json=dict(payload.metadata),
                duplicate_of_candidate_id=payload.duplicate_of_candidate_id,
                duplicate_of_event_id=payload.duplicate_of_event_id,
                verified_event_id=payload.verified_event_id,
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return CandidateEventRead.model_validate(row)

    def find_possible_duplicates(self, candidate_id: str) -> list[dict[str, Any]]:
        with self.repository.session_scope() as session:
            candidate = session.get(CandidateEventRow, candidate_id)
            if candidate is None:
                raise ValueError(f"candidate not found: {candidate_id}")
            source_item = session.get(SourceItemRow, candidate.source_item_id)
            if source_item is None:
                return []
            cands = session.execute(
                select(CandidateEventRow)
                .where(CandidateEventRow.candidate_id != candidate_id)
                .where(CandidateEventRow.domain == candidate.domain)
                .options(selectinload(CandidateEventRow.source_item))
            ).scalars().all()
            events = session.execute(
                select(EventRow)
                .where(EventRow.domain == candidate.domain)
                .options(selectinload(EventRow.evidence).selectinload(EventEvidenceRow.source_item))
            ).scalars().all()

        matches: list[dict[str, Any]] = []
        candidate_title = normalize_text(candidate.title)
        candidate_url = normalize_url(source_item.canonical_url)
        candidate_hash = source_item.content_hash
        for other in cands:
            other_item = other.source_item
            if other_item is None:
                continue
            reason: str | None = None
            if candidate_url and candidate_url == normalize_url(other_item.canonical_url):
                reason = "same_canonical_url"
            elif candidate_hash and candidate_hash == other_item.content_hash:
                reason = "same_content_hash"
            elif candidate_title and similarity(candidate.title, other.title) >= 0.8:
                reason = "title_similarity"
            if reason:
                matches.append(
                    {
                        "kind": "candidate",
                        "candidate_id": other.candidate_id,
                        "reason": reason,
                        "candidate_status": other.candidate_status.value if hasattr(other.candidate_status, "value") else str(other.candidate_status),
                    }
                )
        for event in events:
            reasons: list[str] = []
            for evidence in event.evidence:
                if evidence.source_item is None:
                    continue
                if candidate_url and candidate_url == normalize_url(evidence.source_item.canonical_url):
                    reasons.append("same_canonical_url")
                if candidate_hash and candidate_hash == evidence.source_item.content_hash:
                    reasons.append("same_content_hash")
            if candidate_title and similarity(candidate.title, event.title) >= 0.8:
                reasons.append("title_similarity")
            if reasons:
                matches.append(
                    {
                        "kind": "event",
                        "event_id": event.event_id,
                        "reason": ",".join(sorted(set(reasons))),
                        "verification_status": event.verification_status.value if hasattr(event.verification_status, "value") else str(event.verification_status),
                    }
                )
        return matches

    def approve_candidate(self, candidate_id: str, *, reviewer: str, notes: str = "", reviewed_at: Any | None = None) -> CandidateEventRead:
        reviewed_at = _coerce_datetime(reviewed_at) or utc_now()
        with self.repository.session_scope() as session:
            candidate = session.get(CandidateEventRow, candidate_id)
            if candidate is None:
                raise ValueError(f"candidate not found: {candidate_id}")
            prior = candidate.candidate_status
            candidate.candidate_status = CandidateStatus.APPROVED
            candidate.verification_status = VerificationStatus.PARTIALLY_VERIFIED
            review = ReviewRow(
                review_id=_stable_id("review", candidate_id, reviewed_at.isoformat(), reviewer, "approved"),
                candidate_id=candidate_id,
                reviewer=reviewer,
                decision=CandidateStatus.APPROVED,
                notes=notes,
                reviewed_at=reviewed_at,
                prior_candidate_status=prior,
                resulting_candidate_status=CandidateStatus.APPROVED,
                metadata_json={},
            )
            session.add(review)
            session.flush()
            session.refresh(candidate)
            return CandidateEventRead.model_validate(candidate)

    def reject_candidate(self, candidate_id: str, *, reviewer: str, notes: str = "", reviewed_at: Any | None = None) -> CandidateEventRead:
        reviewed_at = _coerce_datetime(reviewed_at) or utc_now()
        with self.repository.session_scope() as session:
            candidate = session.get(CandidateEventRow, candidate_id)
            if candidate is None:
                raise ValueError(f"candidate not found: {candidate_id}")
            prior = candidate.candidate_status
            candidate.candidate_status = CandidateStatus.REJECTED
            candidate.verification_status = VerificationStatus.WITHDRAWN
            review = ReviewRow(
                review_id=_stable_id("review", candidate_id, reviewed_at.isoformat(), reviewer, "rejected"),
                candidate_id=candidate_id,
                reviewer=reviewer,
                decision=CandidateStatus.REJECTED,
                notes=notes,
                reviewed_at=reviewed_at,
                prior_candidate_status=prior,
                resulting_candidate_status=CandidateStatus.REJECTED,
                metadata_json={},
            )
            session.add(review)
            session.flush()
            session.refresh(candidate)
            return CandidateEventRead.model_validate(candidate)

    def merge_candidate(
        self,
        candidate_id: str,
        *,
        retained_candidate_id: str | None = None,
        retained_event_id: str | None = None,
        reviewer: str,
        notes: str = "",
        reviewed_at: Any | None = None,
    ) -> CandidateEventRead:
        if not retained_candidate_id and not retained_event_id:
            raise ValueError("merge_candidate requires retained_candidate_id or retained_event_id")
        reviewed_at = _coerce_datetime(reviewed_at) or utc_now()
        with self.repository.session_scope() as session:
            candidate = session.get(CandidateEventRow, candidate_id)
            if candidate is None:
                raise ValueError(f"candidate not found: {candidate_id}")
            if retained_candidate_id and session.get(CandidateEventRow, retained_candidate_id) is None:
                raise ValueError(f"retained candidate not found: {retained_candidate_id}")
            if retained_event_id and session.get(EventRow, retained_event_id) is None:
                raise ValueError(f"retained event not found: {retained_event_id}")
            prior = candidate.candidate_status
            candidate.candidate_status = CandidateStatus.DUPLICATE
            candidate.verification_status = VerificationStatus.WITHDRAWN
            candidate.duplicate_of_candidate_id = retained_candidate_id
            candidate.duplicate_of_event_id = retained_event_id
            review = ReviewRow(
                review_id=_stable_id("review", candidate_id, reviewed_at.isoformat(), reviewer, "duplicate"),
                candidate_id=candidate_id,
                reviewer=reviewer,
                decision=CandidateStatus.DUPLICATE,
                notes=notes,
                reviewed_at=reviewed_at,
                prior_candidate_status=prior,
                resulting_candidate_status=CandidateStatus.DUPLICATE,
                metadata_json={},
            )
            session.add(review)
            session.flush()
            session.refresh(candidate)
            return CandidateEventRead.model_validate(candidate)

    def create_event(
        self,
        event: EventCreate | Mapping[str, Any],
        *,
        evidence: list[EventEvidenceCreate | Mapping[str, Any]],
        entity_links: list[EventEntityLinkCreate | Mapping[str, Any]] | None = None,
    ) -> EventRead:
        payload = _ensure_model(event, EventCreate)
        evidence_models = [_ensure_model(item, EventEvidenceCreate) for item in evidence]
        entity_link_models = [_ensure_model(item, EventEntityLinkCreate) for item in (entity_links or [])]
        if not evidence_models:
            raise ValueError("verified events require at least one evidence record")
        event_id = payload.event_id or _stable_id("event", payload.candidate_id)
        with self.repository.session_scope() as session:
            candidate = session.get(CandidateEventRow, payload.candidate_id)
            if candidate is None:
                raise ValueError(f"candidate not found: {payload.candidate_id}")
            if candidate.candidate_status != CandidateStatus.APPROVED:
                raise ValueError("only approved candidates may become events")
            approval_review = session.execute(
                select(ReviewRow).where(
                    ReviewRow.candidate_id == payload.candidate_id,
                    ReviewRow.decision == CandidateStatus.APPROVED,
                )
            ).scalar_one_or_none()
            if approval_review is None:
                raise ValueError("approved candidates require an approval review before event creation")
            existing = session.get(EventRow, event_id)
            if existing is not None:
                return EventRead.model_validate(existing)
            row = EventRow(
                event_id=event_id,
                candidate_id=payload.candidate_id,
                domain=payload.domain,
                title=payload.title,
                summary=payload.summary,
                status=payload.status,
                verification_status=VerificationStatus.UNVERIFIED,
                published_at=payload.published_at,
                location_id=payload.location_id,
                organization_id=payload.organization_id,
                correction_history_json=list(payload.correction_history),
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            for evidence_model in evidence_models:
                source_item = session.get(SourceItemRow, evidence_model.source_item_id)
                if source_item is None:
                    raise ValueError(f"source item not found: {evidence_model.source_item_id}")
                evidence_id = evidence_model.evidence_id or _stable_id(
                    "evidence",
                    event_id,
                    evidence_model.source_item_id,
                    evidence_model.role.value,
                    evidence_model.supporting_passage,
                )
                session.add(
                    EventEvidenceRow(
                        evidence_id=evidence_id,
                        event_id=event_id,
                        source_item_id=evidence_model.source_item_id,
                        role=evidence_model.role,
                        evidence_strength=evidence_model.evidence_strength,
                        is_primary_source=evidence_model.is_primary_source,
                        supporting_passage=evidence_model.supporting_passage,
                        metadata_json=dict(evidence_model.metadata),
                    )
                )
            session.flush()
            row.verification_status = VerificationStatus.VERIFIED
            candidate.verified_event_id = event_id
            candidate.verification_status = VerificationStatus.VERIFIED
            for link_model in entity_link_models:
                if link_model.event_id != event_id:
                    raise ValueError("event entity link event_id must match created event")
                if link_model.candidate_id != payload.candidate_id:
                    raise ValueError("event entity link candidate_id must match event candidate_id")
                decision = session.get(EntityResolutionDecisionRow, link_model.resolution_decision_id)
                if decision is None:
                    raise ValueError(f"resolution decision not found: {link_model.resolution_decision_id}")
                if decision.mention_id != link_model.mention_id:
                    raise ValueError("resolution decision does not belong to mention")
                organization_id = link_model.organization_id
                location_id = link_model.location_id
                if organization_id:
                    organization = session.get(OrganizationRow, organization_id)
                    if organization is None:
                        raise ValueError(f"organization not found: {organization_id}")
                    if organization.merged_into_organization_id:
                        organization_id = organization.merged_into_organization_id
                if location_id:
                    location = session.get(LocationRow, location_id)
                    if location is None:
                        raise ValueError(f"location not found: {location_id}")
                    if location.merged_into_location_id:
                        location_id = location.merged_into_location_id
                link_id = link_model.event_entity_link_id or _stable_id(
                    "event_entity", event_id, link_model.mention_id, link_model.entity_role, organization_id, location_id
                )
                if session.get(EventEntityLinkRow, link_id) is None:
                    session.add(
                        EventEntityLinkRow(
                            event_entity_link_id=link_id,
                            event_id=event_id,
                            candidate_id=payload.candidate_id,
                            mention_id=link_model.mention_id,
                            resolution_decision_id=link_model.resolution_decision_id,
                            entity_kind=link_model.entity_kind,
                            entity_role=link_model.entity_role,
                            organization_id=organization_id,
                            location_id=location_id,
                            metadata_json=dict(link_model.metadata),
                        )
                    )
            session.flush()
            session.refresh(row)
            return self._load_event_read(session, row.event_id)

    def attach_evidence(self, evidence: EventEvidenceCreate | Mapping[str, Any]) -> EventEvidenceRead:
        payload = _ensure_model(evidence, EventEvidenceCreate)
        if not payload.event_id:
            raise ValueError("event_id is required to attach evidence")
        with self.repository.session_scope() as session:
            event = session.get(EventRow, payload.event_id)
            if event is None:
                raise ValueError(f"event not found: {payload.event_id}")
            source_item = session.get(SourceItemRow, payload.source_item_id)
            if source_item is None:
                raise ValueError(f"source item not found: {payload.source_item_id}")
            evidence_id = payload.evidence_id or _stable_id(
                "evidence",
                payload.event_id,
                payload.source_item_id,
                payload.role.value,
                payload.supporting_passage,
            )
            existing = session.get(EventEvidenceRow, evidence_id)
            if existing is not None:
                return EventEvidenceRead.model_validate(existing)
            row = EventEvidenceRow(
                evidence_id=evidence_id,
                event_id=payload.event_id,
                source_item_id=payload.source_item_id,
                role=payload.role,
                evidence_strength=payload.evidence_strength,
                is_primary_source=payload.is_primary_source,
                supporting_passage=payload.supporting_passage,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            if event.verification_status == VerificationStatus.UNVERIFIED:
                event.verification_status = (
                    VerificationStatus.DISPUTED if payload.role == EvidenceRole.CONTRADICTING else VerificationStatus.PARTIALLY_VERIFIED
                )
            elif payload.role == EvidenceRole.CONTRADICTING:
                event.verification_status = VerificationStatus.DISPUTED
            elif payload.role == EvidenceRole.CORRECTION:
                event.verification_status = VerificationStatus.CORRECTED
            session.flush()
            session.refresh(row)
            return EventEvidenceRead.model_validate(row)

    def add_event_attribute(self, attribute: EventAttributeCreate | Mapping[str, Any]) -> EventAttributeRead:
        payload = _ensure_model(attribute, EventAttributeCreate)
        with self.repository.session_scope() as session:
            event = session.get(EventRow, payload.event_id)
            if event is None:
                raise ValueError(f"event not found: {payload.event_id}")
            attribute_id = payload.attribute_id or _stable_id("attribute", payload.event_id, payload.domain.value, payload.attribute_key, payload.value)
            existing = session.get(EventAttributeRow, attribute_id)
            if existing is not None:
                return EventAttributeRead.model_validate(existing)
            row = EventAttributeRow(
                attribute_id=attribute_id,
                event_id=payload.event_id,
                domain=payload.domain,
                attribute_key=payload.attribute_key,
                value_json=payload.value,
                source_item_id=payload.source_item_id,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return EventAttributeRead.model_validate(row)

    def add_event_relationship(self, relationship: EventRelationshipCreate | Mapping[str, Any]) -> EventRelationshipRead:
        payload = _ensure_model(relationship, EventRelationshipCreate)
        if payload.from_event_id == payload.to_event_id:
            raise ValueError("event relationships cannot reference the same event")
        with self.repository.session_scope() as session:
            from_event = session.get(EventRow, payload.from_event_id)
            to_event = session.get(EventRow, payload.to_event_id)
            if from_event is None or to_event is None:
                missing = payload.from_event_id if from_event is None else payload.to_event_id
                raise ValueError(f"event not found: {missing}")
            relationship_id = payload.relationship_id or _stable_id(
                "relationship",
                payload.from_event_id,
                payload.to_event_id,
                payload.relationship_type,
            )
            existing = session.get(EventRelationshipRow, relationship_id)
            if existing is not None:
                return EventRelationshipRead.model_validate(existing)
            row = EventRelationshipRow(
                relationship_id=relationship_id,
                from_event_id=payload.from_event_id,
                to_event_id=payload.to_event_id,
                relationship_type=payload.relationship_type,
                created_at=payload.created_at,
                metadata_json=dict(payload.metadata),
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return EventRelationshipRead.model_validate(row)

    def correct_event(
        self,
        event_id: str,
        *,
        updates: Mapping[str, Any],
        note: str,
        reviewer: str | None = None,
        source_item_id: str | None = None,
        supporting_passage: str = "",
    ) -> EventRead:
        with self.repository.session_scope() as session:
            event = session.get(EventRow, event_id)
            if event is None:
                raise ValueError(f"event not found: {event_id}")
            before = {
                "title": event.title,
                "summary": event.summary,
                "status": event.status.value if hasattr(event.status, "value") else str(event.status),
                "verification_status": event.verification_status.value if hasattr(event.verification_status, "value") else str(event.verification_status),
                "location_id": event.location_id,
                "organization_id": event.organization_id,
                "published_at": event.published_at.isoformat() if event.published_at else None,
            }
            allowed_fields = {"title", "summary", "status", "verification_status", "location_id", "organization_id", "published_at"}
            for key, value in updates.items():
                if key not in allowed_fields:
                    continue
                if key == "status" and value is not None:
                    event.status = EventStatus(value)
                elif key == "verification_status" and value is not None:
                    event.verification_status = VerificationStatus(value)
                else:
                    setattr(event, key, value)
            event.verification_status = VerificationStatus.CORRECTED
            after = {
                "title": event.title,
                "summary": event.summary,
                "status": event.status.value if hasattr(event.status, "value") else str(event.status),
                "verification_status": event.verification_status.value if hasattr(event.verification_status, "value") else str(event.verification_status),
                "location_id": event.location_id,
                "organization_id": event.organization_id,
                "published_at": event.published_at.isoformat() if event.published_at else None,
            }
            changed_fields = {
                key: {"before": before.get(key), "after": after.get(key)}
                for key in sorted(set(before) | set(after))
                if before.get(key) != after.get(key)
            }
            history = list(event.correction_history_json or [])
            history.append(
                {
                    "correction_id": _stable_id("correction", event_id, len(history) + 1, note, reviewer),
                    "corrected_at": utc_now().isoformat(),
                    "reviewer": reviewer,
                    "note": note,
                    "changed_fields": changed_fields,
                    "source_item_id": source_item_id,
                    "before": before,
                    "after": after,
                }
            )
            event.correction_history_json = history
            if source_item_id:
                source_item = session.get(SourceItemRow, source_item_id)
                if source_item is None:
                    raise ValueError(f"source item not found: {source_item_id}")
                evidence_id = _stable_id("evidence", event_id, source_item_id, EvidenceRole.CORRECTION.value, supporting_passage or note)
                if session.get(EventEvidenceRow, evidence_id) is None:
                    session.add(
                        EventEvidenceRow(
                            evidence_id=evidence_id,
                            event_id=event_id,
                            source_item_id=source_item_id,
                            role=EvidenceRole.CORRECTION,
                            evidence_strength="correction",
                            is_primary_source=False,
                            supporting_passage=supporting_passage or note,
                            metadata_json={"note": note},
                        )
                    )
            session.flush()
            return self._load_event_read(session, event_id)

    def supersede_event(self, event_id: str, *, replacement_event_id: str, note: str = "") -> EventRead:
        with self.repository.session_scope() as session:
            event = session.get(EventRow, event_id)
            replacement = session.get(EventRow, replacement_event_id)
            if event is None:
                raise ValueError(f"event not found: {event_id}")
            if replacement is None:
                raise ValueError(f"replacement event not found: {replacement_event_id}")
            relationship_id = _stable_id("relationship", replacement_event_id, event_id, "supersedes")
            if session.get(EventRelationshipRow, relationship_id) is None:
                session.add(
                    EventRelationshipRow(
                        relationship_id=relationship_id,
                        from_event_id=replacement_event_id,
                        to_event_id=event_id,
                        relationship_type="supersedes",
                        created_at=utc_now(),
                        metadata_json={"note": note},
                    )
                )
            event.verification_status = VerificationStatus.WITHDRAWN
            history = list(event.correction_history_json or [])
            history.append(
                {
                    "superseded_at": utc_now().isoformat(),
                    "replacement_event_id": replacement_event_id,
                    "note": note,
                }
            )
            event.correction_history_json = history
            session.flush()
            return self._load_event_read(session, event_id)

    def query_events(
        self,
        *,
        verification_status: VerificationStatus | str | None = VerificationStatus.VERIFIED,
        domain: EventDomain | str | None = None,
        include_relationships: bool = True,
    ) -> list[EventRead]:
        with self.repository.session_scope() as session:
            stmt = select(EventRow)
            if verification_status is not None:
                value = verification_status.value if hasattr(verification_status, "value") else str(verification_status)
                stmt = stmt.where(EventRow.verification_status == value)
            if domain is not None:
                value = domain.value if hasattr(domain, "value") else str(domain)
                stmt = stmt.where(EventRow.domain == value)
            if include_relationships:
                stmt = stmt.options(
                    selectinload(EventRow.candidate).selectinload(CandidateEventRow.source_item),
                    selectinload(EventRow.location),
                    selectinload(EventRow.organization),
                    selectinload(EventRow.evidence).selectinload(EventEvidenceRow.source_item).selectinload(SourceItemRow.source),
                    selectinload(EventRow.attributes).selectinload(EventAttributeRow.source_item),
                    selectinload(EventRow.outgoing_relationships).selectinload(EventRelationshipRow.to_event),
                    selectinload(EventRow.incoming_relationships).selectinload(EventRelationshipRow.from_event),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.mention),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.resolution_decision),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.organization),
                    selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.location),
                )
            rows = list(session.execute(stmt).scalars().all())
            return [EventRead.model_validate(row) for row in rows]

    def export_verified_events_to_json(
        self,
        *,
        path: Path | None = None,
        include_statuses: Iterable[VerificationStatus | str] = (VerificationStatus.VERIFIED,),
        domain: EventDomain | str | None = None,
    ) -> str:
        statuses = _event_filter_values(include_statuses)
        events: list[EventRead] = []
        for status in statuses:
            events.extend(self.query_events(verification_status=status, domain=domain))
        events = sorted(events, key=lambda event: (event.published_at.isoformat(), event.event_id))
        schema_version = ENTITY_RESOLUTION_EXPORT_SCHEMA_VERSION if any(event.entity_links for event in events) else EXPORT_SCHEMA_VERSION
        payload = {
            "schema_version": schema_version,
            "contract": {
                "generated_at": None,
                "ordering": "published_at_asc_event_id_asc",
                "eligible_verification_statuses": sorted(statuses),
                "null_policy": "optional fields serialize as JSON null; absent collections serialize as empty arrays",
                "entity_resolution": "included_when_event_entity_links_exist",
            },
            "events": [event.model_dump(mode="json") for event in events],
        }
        text = json.dumps(payload, indent=2, sort_keys=True)
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return text

    def _load_event_read(self, session, event_id: str) -> EventRead:
        stmt = (
            select(EventRow)
            .where(EventRow.event_id == event_id)
            .options(
                selectinload(EventRow.candidate).selectinload(CandidateEventRow.source_item),
                selectinload(EventRow.location),
                selectinload(EventRow.organization),
                selectinload(EventRow.evidence).selectinload(EventEvidenceRow.source_item).selectinload(SourceItemRow.source),
                selectinload(EventRow.attributes).selectinload(EventAttributeRow.source_item),
                selectinload(EventRow.outgoing_relationships).selectinload(EventRelationshipRow.to_event),
                selectinload(EventRow.incoming_relationships).selectinload(EventRelationshipRow.from_event),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.mention),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.resolution_decision),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.organization),
                selectinload(EventRow.entity_links).selectinload(EventEntityLinkRow.location),
            )
        )
        row = session.execute(stmt).scalar_one()
        return EventRead.model_validate(row)
