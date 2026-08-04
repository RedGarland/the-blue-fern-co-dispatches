from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import CandidateStatus, EvidenceRole, EventDomain, EventStatus, VerificationStatus


class EventBaseModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class SourceCreate(EventBaseModel):
    source_id: str | None = None
    name: str
    publisher: str
    canonical_url: str
    source_type: str
    content_hash: str | None = None
    discovered_at: datetime
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class SourceRead(SourceCreate):
    source_id: str
    created_at: datetime
    updated_at: datetime


class SourceItemCreate(EventBaseModel):
    source_item_id: str | None = None
    source_id: str
    canonical_url: str
    content_hash: str
    title: str
    supporting_passage: str = ""
    discovered_at: datetime
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class SourceItemRead(SourceItemCreate):
    source_item_id: str
    created_at: datetime
    updated_at: datetime
    source: SourceRead | None = None


class CandidateEventCreate(EventBaseModel):
    candidate_id: str | None = None
    source_item_id: str
    domain: EventDomain
    title: str
    summary: str = ""
    candidate_status: CandidateStatus = CandidateStatus.NEW
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    event_status: EventStatus = EventStatus.UNKNOWN
    source_item_ids: list[str] = Field(default_factory=list, alias="source_item_ids_json")
    location_id: str | None = None
    organization_id: str | None = None
    discovered_at: datetime
    published_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")
    duplicate_of_candidate_id: str | None = None
    duplicate_of_event_id: str | None = None
    verified_event_id: str | None = None


class CandidateEventRead(CandidateEventCreate):
    candidate_id: str
    created_at: datetime
    updated_at: datetime


class ReviewCreate(EventBaseModel):
    review_id: str | None = None
    candidate_id: str
    reviewer: str
    decision: CandidateStatus
    notes: str = ""
    reviewed_at: datetime
    prior_candidate_status: CandidateStatus
    resulting_candidate_status: CandidateStatus
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class ReviewRead(ReviewCreate):
    review_id: str
    created_at: datetime


class LocationCreate(EventBaseModel):
    location_id: str | None = None
    canonical_name: str
    normalized_canonical_name: str = ""
    display_name: str | None = None
    address_line_1: str = ""
    address_line_2: str = ""
    postal_code: str = ""
    country_code: str = ""
    location_type: str = ""
    country: str = ""
    region: str = ""
    state: str = ""
    county: str = ""
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    merged_into_location_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class LocationRead(LocationCreate):
    location_id: str
    created_at: datetime
    updated_at: datetime


class OrganizationCreate(EventBaseModel):
    organization_id: str | None = None
    canonical_name: str
    normalized_canonical_name: str = ""
    display_name: str | None = None
    organization_type: str = ""
    parent_organization_id: str | None = None
    primary_location_id: str | None = None
    operational_status: str = ""
    canonical_domain: str = ""
    merged_into_organization_id: str | None = None
    merge_status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class OrganizationRead(OrganizationCreate):
    organization_id: str
    created_at: datetime
    updated_at: datetime


class EventCreate(EventBaseModel):
    event_id: str | None = None
    candidate_id: str
    domain: EventDomain
    title: str
    summary: str = ""
    status: EventStatus = EventStatus.UNKNOWN
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    published_at: datetime
    location_id: str | None = None
    organization_id: str | None = None
    correction_history: list[dict[str, Any]] = Field(default_factory=list, alias="correction_history_json")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class EventRead(EventCreate):
    event_id: str
    created_at: datetime
    updated_at: datetime
    evidence: list["EventEvidenceRead"] = Field(default_factory=list)
    attributes: list["EventAttributeRead"] = Field(default_factory=list)
    relationships: list["EventRelationshipRead"] = Field(default_factory=list, alias="outgoing_relationships")
    entity_links: list["EventEntityLinkRead"] = Field(default_factory=list)
    location: LocationRead | None = None
    organization: OrganizationRead | None = None


class EventEvidenceCreate(EventBaseModel):
    evidence_id: str | None = None
    event_id: str | None = None
    source_item_id: str
    role: EvidenceRole
    evidence_strength: str = ""
    is_primary_source: bool = False
    supporting_passage: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class EventEvidenceRead(EventEvidenceCreate):
    evidence_id: str
    source_item: SourceItemRead | None = None


class EventRelationshipCreate(EventBaseModel):
    relationship_id: str | None = None
    from_event_id: str
    to_event_id: str
    relationship_type: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class EventRelationshipRead(EventRelationshipCreate):
    relationship_id: str


class EventAttributeCreate(EventBaseModel):
    attribute_id: str | None = None
    event_id: str
    domain: EventDomain
    attribute_key: str
    value: Any = Field(alias="value_json")
    source_item_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class EventAttributeRead(EventAttributeCreate):
    attribute_id: str


class OrganizationAliasCreate(EventBaseModel):
    alias_id: str | None = None
    organization_id: str
    alias_name: str
    normalized_alias: str = ""
    alias_type: str = "source_name"
    source_item_id: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    is_primary: bool = False


class OrganizationAliasRead(OrganizationAliasCreate):
    alias_id: str
    created_at: datetime


class OrganizationIdentifierCreate(EventBaseModel):
    organization_identifier_id: str | None = None
    organization_id: str
    identifier_scheme: str
    identifier_value: str
    normalized_value: str = ""
    source_item_id: str | None = None
    is_authoritative: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class OrganizationIdentifierRead(OrganizationIdentifierCreate):
    organization_identifier_id: str
    created_at: datetime


class LocationAliasCreate(EventBaseModel):
    location_alias_id: str | None = None
    location_id: str
    alias_name: str
    normalized_alias: str = ""
    alias_type: str = "source_name"
    source_item_id: str | None = None


class LocationAliasRead(LocationAliasCreate):
    location_alias_id: str
    created_at: datetime


class LocationIdentifierCreate(EventBaseModel):
    location_identifier_id: str | None = None
    location_id: str
    identifier_scheme: str
    identifier_value: str
    normalized_value: str = ""
    source_item_id: str | None = None
    is_authoritative: bool = False


class LocationIdentifierRead(LocationIdentifierCreate):
    location_identifier_id: str
    created_at: datetime


class EntityMentionCreate(EventBaseModel):
    mention_id: str | None = None
    candidate_id: str
    source_item_id: str | None = None
    entity_kind: str
    mention_role: str
    raw_name: str
    normalized_name: str = ""
    raw_address: str = ""
    address_line_1: str = ""
    address_line_2: str = ""
    locality: str = ""
    region: str = ""
    postal_code: str = ""
    country_code: str = ""
    latitude: float | None = None
    longitude: float | None = None
    external_identifiers: dict[str, Any] = Field(default_factory=dict, alias="external_identifiers_json")


class EntityMentionRead(EntityMentionCreate):
    mention_id: str
    created_at: datetime


class EntityMatchCandidateCreate(EventBaseModel):
    match_candidate_id: str | None = None
    mention_id: str
    entity_kind: str
    organization_id: str | None = None
    location_id: str | None = None
    match_score: float
    match_method: str
    match_features: dict[str, Any] = Field(default_factory=dict, alias="match_features_json")
    rank: int
    generated_at: datetime
    resolver_version: str

    @model_validator(mode="after")
    def _validate_target(self):
        if self.entity_kind == "organization" and not (self.organization_id and not self.location_id):
            raise ValueError("organization match candidates require organization_id only")
        if self.entity_kind == "location" and not (self.location_id and not self.organization_id):
            raise ValueError("location match candidates require location_id only")
        if self.entity_kind not in {"organization", "location"}:
            raise ValueError("entity_kind must be organization or location")
        if not 0.0 <= self.match_score <= 1.0:
            raise ValueError("match_score must be between 0 and 1")
        return self


class EntityMatchCandidateRead(EntityMatchCandidateCreate):
    match_candidate_id: str


class EntityResolutionDecisionCreate(EventBaseModel):
    resolution_decision_id: str | None = None
    mention_id: str
    decision_type: str
    organization_id: str | None = None
    location_id: str | None = None
    selected_match_candidate_id: str | None = None
    confidence: float = 0.0
    decision_reason: str = ""
    reviewer: str
    resolver_version: str
    created_at: datetime
    supersedes_decision_id: str | None = None

    @model_validator(mode="after")
    def _validate_decision_target(self):
        targeted = self.decision_type in {"matched", "created_new", "corrected"}
        has_org = bool(self.organization_id)
        has_location = bool(self.location_id)
        if targeted and has_org == has_location:
            raise ValueError("matched, created_new, and corrected decisions require exactly one target")
        if not targeted and (has_org or has_location):
            raise ValueError("deferred, unresolved, and rejected_match decisions cannot select a target")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.supersedes_decision_id and self.supersedes_decision_id == self.resolution_decision_id:
            raise ValueError("resolution decisions cannot supersede themselves")
        return self


class EntityResolutionDecisionRead(EntityResolutionDecisionCreate):
    resolution_decision_id: str


class EffectiveResolutionRead(EventBaseModel):
    mention_id: str
    decision: EntityResolutionDecisionRead | None = None
    organization: OrganizationRead | None = None
    location: LocationRead | None = None


class OrganizationRelationshipCreate(EventBaseModel):
    organization_relationship_id: str | None = None
    from_organization_id: str
    to_organization_id: str
    relationship_type: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_item_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class OrganizationRelationshipRead(OrganizationRelationshipCreate):
    organization_relationship_id: str
    created_at: datetime


class OrganizationLocationRelationshipCreate(EventBaseModel):
    organization_location_relationship_id: str | None = None
    organization_id: str
    location_id: str
    relationship_type: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_item_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class OrganizationLocationRelationshipRead(OrganizationLocationRelationshipCreate):
    organization_location_relationship_id: str
    created_at: datetime


class OrganizationMergeCreate(EventBaseModel):
    organization_merge_id: str | None = None
    survivor_organization_id: str
    merged_organization_id: str
    reviewer: str
    reason: str = ""
    source_item_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")


class OrganizationMergeRead(OrganizationMergeCreate):
    organization_merge_id: str


class EventEntityLinkCreate(EventBaseModel):
    event_entity_link_id: str | None = None
    event_id: str
    candidate_id: str
    mention_id: str
    resolution_decision_id: str
    entity_kind: str
    entity_role: str
    organization_id: str | None = None
    location_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_json")

    @model_validator(mode="after")
    def _validate_target(self):
        if self.entity_kind == "organization" and not (self.organization_id and not self.location_id):
            raise ValueError("organization links require organization_id only")
        if self.entity_kind == "location" and not (self.location_id and not self.organization_id):
            raise ValueError("location links require location_id only")
        if self.entity_kind not in {"organization", "location"}:
            raise ValueError("entity_kind must be organization or location")
        return self


class EventEntityLinkRead(EventEntityLinkCreate):
    event_entity_link_id: str
    created_at: datetime
    mention: EntityMentionRead | None = None
    resolution_decision: EntityResolutionDecisionRead | None = None
    organization: OrganizationRead | None = None
    location: LocationRead | None = None


class VerifiedEventExport(EventRead):
    pass


class ExportBundle(EventBaseModel):
    events: list[VerifiedEventExport]


EventRead.model_rebuild()
VerifiedEventExport.model_rebuild()
