from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Text, UniqueConstraint, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import CandidateStatus, EvidenceRole, EventDomain, EventStatus, VerificationStatus


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _enum_type(enum_cls: type, name: str) -> SQLEnum:
    return SQLEnum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda enum: [item.value for item in enum],
        validate_strings=True,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class SourceRow(Base, TimestampMixin):
    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    source_items: Mapped[list["SourceItemRow"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class SourceItemRow(Base, TimestampMixin):
    __tablename__ = "source_items"

    source_item_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    supporting_passage: Mapped[str] = mapped_column(Text, nullable=False, default="")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    source: Mapped["SourceRow"] = relationship(back_populates="source_items")
    candidate_events: Mapped[list["CandidateEventRow"]] = relationship(back_populates="source_item")
    evidence: Mapped[list["EventEvidenceRow"]] = relationship(back_populates="source_item")


class LocationRow(Base, TimestampMixin):
    __tablename__ = "locations"

    location_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    normalized_canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address_line_2: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    postal_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    location_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    county: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    merged_into_location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True, index=True)

    candidate_events: Mapped[list["CandidateEventRow"]] = relationship(back_populates="location")
    events: Mapped[list["EventRow"]] = relationship(back_populates="location")
    aliases: Mapped[list["LocationAliasRow"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    identifiers: Mapped[list["LocationIdentifierRow"]] = relationship(back_populates="location", cascade="all, delete-orphan")


class OrganizationRow(Base, TimestampMixin):
    __tablename__ = "organizations"

    organization_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    normalized_canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    organization_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    parent_organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="SET NULL"), nullable=True, index=True)
    primary_location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True, index=True)
    operational_status: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    canonical_domain: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    merged_into_organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="SET NULL"), nullable=True, index=True)
    merge_status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    candidate_events: Mapped[list["CandidateEventRow"]] = relationship(back_populates="organization")
    events: Mapped[list["EventRow"]] = relationship(back_populates="organization")
    aliases: Mapped[list["OrganizationAliasRow"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    identifiers: Mapped[list["OrganizationIdentifierRow"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    primary_location: Mapped["LocationRow | None"] = relationship(foreign_keys=[primary_location_id])


class CandidateEventRow(Base, TimestampMixin):
    __tablename__ = "candidate_events"

    candidate_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_item_id: Mapped[str] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="RESTRICT"), nullable=False, index=True)
    domain: Mapped[EventDomain] = mapped_column(_enum_type(EventDomain, "event_domain"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    candidate_status: Mapped[CandidateStatus] = mapped_column(
        _enum_type(CandidateStatus, "candidate_status"),
        nullable=False,
        default=CandidateStatus.NEW.value,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum_type(VerificationStatus, "verification_status"),
        nullable=False,
        default=VerificationStatus.UNVERIFIED.value,
    )
    event_status: Mapped[EventStatus] = mapped_column(
        _enum_type(EventStatus, "event_status"),
        nullable=False,
        default=EventStatus.UNKNOWN.value,
    )
    source_item_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="SET NULL"), nullable=True, index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    duplicate_of_candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidate_events.candidate_id", ondelete="SET NULL"), nullable=True, index=True)
    duplicate_of_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.event_id", ondelete="SET NULL"), nullable=True, index=True)
    verified_event_id: Mapped[str | None] = mapped_column(ForeignKey("events.event_id", ondelete="SET NULL"), nullable=True, index=True)

    source_item: Mapped["SourceItemRow"] = relationship(back_populates="candidate_events")
    location: Mapped["LocationRow | None"] = relationship(back_populates="candidate_events")
    organization: Mapped["OrganizationRow | None"] = relationship(back_populates="candidate_events")
    reviews: Mapped[list["ReviewRow"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="ReviewRow.created_at",
    )


class ReviewRow(Base):
    __tablename__ = "reviews"

    review_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_events.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[CandidateStatus] = mapped_column(
        _enum_type(CandidateStatus, "review_decision"),
        nullable=False,
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prior_candidate_status: Mapped[CandidateStatus] = mapped_column(_enum_type(CandidateStatus, "review_prior_status"), nullable=False)
    resulting_candidate_status: Mapped[CandidateStatus] = mapped_column(_enum_type(CandidateStatus, "review_resulting_status"), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    candidate: Mapped["CandidateEventRow"] = relationship(back_populates="reviews")


class EventRow(Base, TimestampMixin):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_events.candidate_id", ondelete="RESTRICT"), nullable=False, unique=True, index=True)
    domain: Mapped[EventDomain] = mapped_column(_enum_type(EventDomain, "event_domain_event"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[EventStatus] = mapped_column(
        _enum_type(EventStatus, "event_status_event"),
        nullable=False,
        default=EventStatus.UNKNOWN.value,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum_type(VerificationStatus, "verification_status_event"),
        nullable=False,
        default=VerificationStatus.VERIFIED.value,
    )
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="SET NULL"), nullable=True, index=True)
    correction_history_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    candidate: Mapped["CandidateEventRow"] = relationship(foreign_keys=[candidate_id])
    location: Mapped["LocationRow | None"] = relationship(back_populates="events")
    organization: Mapped["OrganizationRow | None"] = relationship(back_populates="events")
    evidence: Mapped[list["EventEvidenceRow"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventEvidenceRow.created_at",
    )
    attributes: Mapped[list["EventAttributeRow"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventAttributeRow.created_at",
    )
    outgoing_relationships: Mapped[list["EventRelationshipRow"]] = relationship(
        back_populates="from_event",
        cascade="all, delete-orphan",
        foreign_keys=lambda: [EventRelationshipRow.from_event_id],
        order_by="EventRelationshipRow.created_at",
    )
    incoming_relationships: Mapped[list["EventRelationshipRow"]] = relationship(
        back_populates="to_event",
        cascade="all, delete-orphan",
        foreign_keys=lambda: [EventRelationshipRow.to_event_id],
        order_by="EventRelationshipRow.created_at",
    )
    entity_links: Mapped[list["EventEntityLinkRow"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventEntityLinkRow.created_at",
    )


class EventEvidenceRow(Base, TimestampMixin):
    __tablename__ = "event_evidence"

    evidence_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    source_item_id: Mapped[str] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="RESTRICT"), nullable=False, index=True)
    role: Mapped[EvidenceRole] = mapped_column(_enum_type(EvidenceRole, "evidence_role"), nullable=False)
    evidence_strength: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    is_primary_source: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supporting_passage: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    event: Mapped["EventRow"] = relationship(back_populates="evidence")
    source_item: Mapped["SourceItemRow"] = relationship(back_populates="evidence")


class EventRelationshipRow(Base):
    __tablename__ = "event_relationships"
    __table_args__ = (
        CheckConstraint("from_event_id <> to_event_id", name="event_relationships_no_self_reference"),
    )

    relationship_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    from_event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    to_event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    from_event: Mapped["EventRow"] = relationship(
        back_populates="outgoing_relationships",
        foreign_keys=[from_event_id],
    )
    to_event: Mapped["EventRow"] = relationship(
        back_populates="incoming_relationships",
        foreign_keys=[to_event_id],
    )


class EventAttributeRow(Base, TimestampMixin):
    __tablename__ = "event_attributes"

    attribute_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    domain: Mapped[EventDomain] = mapped_column(_enum_type(EventDomain, "event_attribute_domain"), nullable=False)
    attribute_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    event: Mapped["EventRow"] = relationship(back_populates="attributes")
    source_item: Mapped["SourceItemRow | None"] = relationship()


class OrganizationAliasRow(Base):
    __tablename__ = "organization_aliases"
    __table_args__ = (
        CheckConstraint("length(trim(alias_name)) > 0", name="organization_aliases_alias_not_blank"),
        CheckConstraint("length(trim(normalized_alias)) > 0", name="organization_aliases_normalized_not_blank"),
        UniqueConstraint("organization_id", "normalized_alias", "alias_type", "source_item_id", name="uq_organization_aliases_org_alias_type_source"),
    )

    alias_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False, index=True)
    alias_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    alias_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization: Mapped["OrganizationRow"] = relationship(back_populates="aliases")
    source_item: Mapped["SourceItemRow | None"] = relationship()


class OrganizationIdentifierRow(Base):
    __tablename__ = "organization_identifiers"
    __table_args__ = (
        CheckConstraint("length(trim(identifier_scheme)) > 0", name="organization_identifiers_scheme_not_blank"),
        CheckConstraint("length(trim(identifier_value)) > 0", name="organization_identifiers_value_not_blank"),
        CheckConstraint("length(trim(normalized_value)) > 0", name="organization_identifiers_normalized_not_blank"),
        UniqueConstraint("organization_id", "identifier_scheme", "normalized_value", "source_item_id", name="uq_organization_identifiers_org_scheme_value_source"),
        Index(
            "uq_organization_identifiers_authoritative_scheme_value",
            "identifier_scheme",
            "normalized_value",
            unique=True,
            sqlite_where=text("is_authoritative = 1 AND valid_to IS NULL"),
        ),
    )

    organization_identifier_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False, index=True)
    identifier_scheme: Mapped[str] = mapped_column(String(80), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization: Mapped["OrganizationRow"] = relationship(back_populates="identifiers")
    source_item: Mapped["SourceItemRow | None"] = relationship()


class LocationAliasRow(Base):
    __tablename__ = "location_aliases"
    __table_args__ = (
        CheckConstraint("length(trim(alias_name)) > 0", name="location_aliases_alias_not_blank"),
        CheckConstraint("length(trim(normalized_alias)) > 0", name="location_aliases_normalized_not_blank"),
        UniqueConstraint("location_id", "normalized_alias", "alias_type", "source_item_id", name="uq_location_aliases_location_alias_type_source"),
    )

    location_alias_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False, index=True)
    alias_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    alias_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    location: Mapped["LocationRow"] = relationship(back_populates="aliases")
    source_item: Mapped["SourceItemRow | None"] = relationship()


class LocationIdentifierRow(Base):
    __tablename__ = "location_identifiers"
    __table_args__ = (
        CheckConstraint("length(trim(identifier_scheme)) > 0", name="location_identifiers_scheme_not_blank"),
        CheckConstraint("length(trim(identifier_value)) > 0", name="location_identifiers_value_not_blank"),
        CheckConstraint("length(trim(normalized_value)) > 0", name="location_identifiers_normalized_not_blank"),
        UniqueConstraint("location_id", "identifier_scheme", "normalized_value", "source_item_id", name="uq_location_identifiers_location_scheme_value_source"),
        Index(
            "uq_location_identifiers_authoritative_scheme_value",
            "identifier_scheme",
            "normalized_value",
            unique=True,
            sqlite_where=text("is_authoritative = 1"),
        ),
    )

    location_identifier_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False, index=True)
    identifier_scheme: Mapped[str] = mapped_column(String(80), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    is_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    location: Mapped["LocationRow"] = relationship(back_populates="identifiers")
    source_item: Mapped["SourceItemRow | None"] = relationship()


class EntityMentionRow(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        CheckConstraint("entity_kind IN ('organization', 'location')", name="entity_mentions_valid_kind"),
        CheckConstraint("length(trim(raw_name)) > 0", name="entity_mentions_raw_name_not_blank"),
        CheckConstraint("length(trim(normalized_name)) > 0", name="entity_mentions_normalized_name_not_blank"),
        UniqueConstraint("candidate_id", "source_item_id", "entity_kind", "mention_role", "normalized_name", "raw_address", name="uq_entity_mentions_stable_ingestion"),
    )

    mention_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_events.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    entity_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    mention_role: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    raw_address: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address_line_2: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    locality: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    postal_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    external_identifiers_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    candidate: Mapped["CandidateEventRow"] = relationship()
    source_item: Mapped["SourceItemRow | None"] = relationship()


class EntityMatchCandidateRow(Base):
    __tablename__ = "entity_match_candidates"
    __table_args__ = (
        CheckConstraint("entity_kind IN ('organization', 'location')", name="entity_match_candidates_valid_kind"),
        CheckConstraint("((organization_id IS NOT NULL) AND (location_id IS NULL) AND entity_kind = 'organization') OR ((organization_id IS NULL) AND (location_id IS NOT NULL) AND entity_kind = 'location')", name="entity_match_candidates_one_matching_target"),
        CheckConstraint("match_score >= 0.0 AND match_score <= 1.0", name="entity_match_candidates_score_bounds"),
        UniqueConstraint("mention_id", "organization_id", "location_id", "resolver_version", name="uq_entity_match_candidates_mention_target_version"),
    )

    match_candidate_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mention_id: Mapped[str] = mapped_column(ForeignKey("entity_mentions.mention_id", ondelete="CASCADE"), nullable=False, index=True)
    entity_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=True, index=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=True, index=True)
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_method: Mapped[str] = mapped_column(String(80), nullable=False)
    match_features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rank: Mapped[int] = mapped_column(default=0, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(80), nullable=False)

    mention: Mapped["EntityMentionRow"] = relationship()
    organization: Mapped["OrganizationRow | None"] = relationship()
    location: Mapped["LocationRow | None"] = relationship()


class EntityResolutionDecisionRow(Base):
    __tablename__ = "entity_resolution_decisions"
    __table_args__ = (
        CheckConstraint("decision_type IN ('matched', 'created_new', 'deferred', 'rejected_match', 'corrected', 'unresolved')", name="entity_resolution_decisions_valid_type"),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="entity_resolution_decisions_confidence_bounds"),
        CheckConstraint("NOT (supersedes_decision_id = resolution_decision_id)", name="entity_resolution_decisions_no_self_supersession"),
        CheckConstraint("((decision_type IN ('matched', 'created_new', 'corrected') AND ((organization_id IS NOT NULL AND location_id IS NULL) OR (organization_id IS NULL AND location_id IS NOT NULL))) OR (decision_type IN ('deferred', 'unresolved', 'rejected_match') AND organization_id IS NULL AND location_id IS NULL))", name="entity_resolution_decisions_target_rules"),
    )

    resolution_decision_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mention_id: Mapped[str] = mapped_column(ForeignKey("entity_mentions.mention_id", ondelete="CASCADE"), nullable=False, index=True)
    decision_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=True, index=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=True, index=True)
    selected_match_candidate_id: Mapped[str | None] = mapped_column(ForeignKey("entity_match_candidates.match_candidate_id", ondelete="SET NULL"), nullable=True, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_decision_id: Mapped[str | None] = mapped_column(ForeignKey("entity_resolution_decisions.resolution_decision_id", ondelete="SET NULL"), nullable=True, index=True)

    mention: Mapped["EntityMentionRow"] = relationship()
    organization: Mapped["OrganizationRow | None"] = relationship()
    location: Mapped["LocationRow | None"] = relationship()
    selected_match_candidate: Mapped["EntityMatchCandidateRow | None"] = relationship()


class OrganizationRelationshipRow(Base):
    __tablename__ = "organization_relationships"
    __table_args__ = (
        CheckConstraint("from_organization_id <> to_organization_id", name="organization_relationships_no_self_link"),
        UniqueConstraint("from_organization_id", "to_organization_id", "relationship_type", "valid_from", name="uq_organization_relationships_stable"),
    )

    organization_relationship_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    from_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False, index=True)
    to_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class OrganizationLocationRelationshipRow(Base):
    __tablename__ = "organization_location_relationships"
    __table_args__ = (
        UniqueConstraint("organization_id", "location_id", "relationship_type", "valid_from", name="uq_organization_location_relationships_stable"),
    )

    organization_location_relationship_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False, index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class OrganizationMergeRow(Base):
    __tablename__ = "organization_merges"
    __table_args__ = (
        CheckConstraint("survivor_organization_id <> merged_organization_id", name="organization_merges_no_self_merge"),
        UniqueConstraint("survivor_organization_id", "merged_organization_id", name="uq_organization_merges_pair"),
    )

    organization_merge_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    survivor_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=False, index=True)
    merged_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=False, index=True)
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_item_id: Mapped[str | None] = mapped_column(ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class EventEntityLinkRow(Base):
    __tablename__ = "event_entity_links"
    __table_args__ = (
        CheckConstraint("entity_kind IN ('organization', 'location')", name="event_entity_links_valid_kind"),
        CheckConstraint("((organization_id IS NOT NULL) AND (location_id IS NULL) AND entity_kind = 'organization') OR ((organization_id IS NULL) AND (location_id IS NOT NULL) AND entity_kind = 'location')", name="event_entity_links_one_matching_target"),
        UniqueConstraint("event_id", "mention_id", "entity_role", "organization_id", "location_id", name="uq_event_entity_links_stable"),
    )

    event_entity_link_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidate_events.candidate_id", ondelete="CASCADE"), nullable=False, index=True)
    mention_id: Mapped[str] = mapped_column(ForeignKey("entity_mentions.mention_id", ondelete="RESTRICT"), nullable=False, index=True)
    resolution_decision_id: Mapped[str] = mapped_column(ForeignKey("entity_resolution_decisions.resolution_decision_id", ondelete="RESTRICT"), nullable=False, index=True)
    entity_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_role: Mapped[str] = mapped_column(String(80), nullable=False)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=True, index=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    event: Mapped["EventRow"] = relationship(back_populates="entity_links")
    mention: Mapped["EntityMentionRow"] = relationship()
    resolution_decision: Mapped["EntityResolutionDecisionRow"] = relationship()
    organization: Mapped["OrganizationRow | None"] = relationship()
    location: Mapped["LocationRow | None"] = relationship()


class ShadowIngestionRunRow(Base):
    __tablename__ = "shadow_ingestion_runs"

    shadow_run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    producer: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    input_manifest_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    date_from: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    date_to: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    adapter_version: Mapped[str] = mapped_column(String(80), nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(80), nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    input_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ambiguous_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unresolved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    automatic_matchable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class ShadowIngestionExecutionRow(Base):
    __tablename__ = "shadow_ingestion_executions"

    execution_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    shadow_run_id: Mapped[str] = mapped_column(ForeignKey("shadow_ingestion_runs.shadow_run_id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    host_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    command_options_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    report_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    error_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ShadowIngestionRecordResultRow(Base):
    __tablename__ = "shadow_ingestion_record_results"
    __table_args__ = (
        CheckConstraint("result_type IN ('created', 'existing', 'updated', 'excluded', 'withdrawn', 'error')", name="shadow_record_results_valid_type"),
        UniqueConstraint("shadow_run_id", "producer_record_id", "input_record_hash", "result_type", name="uq_shadow_record_results_stable"),
    )

    record_result_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    shadow_run_id: Mapped[str] = mapped_column(ForeignKey("shadow_ingestion_runs.shadow_run_id", ondelete="CASCADE"), nullable=False, index=True)
    producer_record_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source_file: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    input_record_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    result_type: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidate_events.candidate_id", ondelete="SET NULL"), nullable=True, index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    warning_codes_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


SQLITE_INVARIANT_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_reviews_append_only_update
    BEFORE UPDATE ON reviews
    BEGIN
        SELECT RAISE(ABORT, 'reviews are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_reviews_append_only_delete
    BEFORE DELETE ON reviews
    BEGIN
        SELECT RAISE(ABORT, 'reviews are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_events_verified_requires_evidence_insert
    AFTER INSERT ON events
    WHEN NEW.verification_status IN ('verified', 'disputed', 'corrected')
         AND NOT EXISTS (
             SELECT 1 FROM event_evidence WHERE event_evidence.event_id = NEW.event_id
         )
    BEGIN
        SELECT RAISE(ABORT, 'verified events require evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_events_verified_requires_evidence_update
    AFTER UPDATE OF verification_status ON events
    WHEN NEW.verification_status IN ('verified', 'disputed', 'corrected')
         AND NOT EXISTS (
             SELECT 1 FROM event_evidence WHERE event_evidence.event_id = NEW.event_id
         )
    BEGIN
        SELECT RAISE(ABORT, 'verified events require evidence');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_duplicate_candidates_require_retained_target_insert
    AFTER INSERT ON candidate_events
    WHEN NEW.candidate_status = 'duplicate'
         AND NEW.duplicate_of_candidate_id IS NULL
         AND NEW.duplicate_of_event_id IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'duplicate candidates require a retained candidate or event');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_duplicate_candidates_require_retained_target_update
    AFTER UPDATE OF candidate_status, duplicate_of_candidate_id, duplicate_of_event_id ON candidate_events
    WHEN NEW.candidate_status = 'duplicate'
         AND NEW.duplicate_of_candidate_id IS NULL
         AND NEW.duplicate_of_event_id IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'duplicate candidates require a retained candidate or event');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_entity_resolution_decisions_append_only_update
    BEFORE UPDATE ON entity_resolution_decisions
    BEGIN
        SELECT RAISE(ABORT, 'entity resolution decisions are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_entity_resolution_decisions_append_only_delete
    BEFORE DELETE ON entity_resolution_decisions
    BEGIN
        SELECT RAISE(ABORT, 'entity resolution decisions are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_organization_merges_direct_cycle_insert
    BEFORE INSERT ON organization_merges
    WHEN EXISTS (
        SELECT 1 FROM organization_merges
        WHERE survivor_organization_id = NEW.merged_organization_id
          AND merged_organization_id = NEW.survivor_organization_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'organization merge cycle');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_organization_merges_direct_cycle_update
    BEFORE UPDATE ON organization_merges
    WHEN EXISTS (
        SELECT 1 FROM organization_merges
        WHERE survivor_organization_id = NEW.merged_organization_id
          AND merged_organization_id = NEW.survivor_organization_id
          AND organization_merge_id <> NEW.organization_merge_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'organization merge cycle');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_ingestion_executions_append_only_update
    BEFORE UPDATE ON shadow_ingestion_executions
    BEGIN
        SELECT RAISE(ABORT, 'shadow ingestion executions are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_ingestion_executions_append_only_delete
    BEFORE DELETE ON shadow_ingestion_executions
    BEGIN
        SELECT RAISE(ABORT, 'shadow ingestion executions are append-only');
    END
    """,
)
