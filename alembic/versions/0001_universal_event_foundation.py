from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_universal_event_foundation"
down_revision = None
branch_labels = None
depends_on = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


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
)


SQLITE_INVARIANT_TRIGGER_NAMES = (
    "trg_duplicate_candidates_require_retained_target_update",
    "trg_duplicate_candidates_require_retained_target_insert",
    "trg_events_verified_requires_evidence_update",
    "trg_events_verified_requires_evidence_insert",
    "trg_reviews_append_only_delete",
    "trg_reviews_append_only_update",
)


def upgrade() -> None:
    candidate_status = _enum("candidate_status", "new", "needs_review", "duplicate", "rejected", "approved", "superseded")
    verification_status = _enum("verification_status", "unverified", "partially_verified", "verified", "disputed", "corrected", "withdrawn")
    event_status = _enum("event_status", "announced", "planned", "active", "completed", "delayed", "cancelled", "ongoing", "unknown")
    evidence_role = _enum("evidence_role", "primary", "corroborating", "context", "contradicting", "correction")
    event_domain = _enum("event_domain", "food_security", "healthcare_access", "workforce", "immigration_enforcement", "conflict_humanitarian")

    op.create_table(
        "sources",
        sa.Column("source_id", sa.String(length=80), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False, unique=True),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "locations",
        sa.Column("location_id", sa.String(length=80), primary_key=True),
        sa.Column("canonical_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("location_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("country", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("region", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("state", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("county", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("city", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.String(length=80), primary_key=True),
        sa.Column("canonical_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("organization_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_items",
        sa.Column("source_item_id", sa.String(length=80), primary_key=True),
        sa.Column("source_id", sa.String(length=80), sa.ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False, unique=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("supporting_passage", sa.Text(), nullable=False, server_default=""),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_source_items_source_id"), "source_items", ["source_id"], unique=False)
    op.create_table(
        "candidate_events",
        sa.Column("candidate_id", sa.String(length=80), primary_key=True),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("domain", event_domain, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("candidate_status", candidate_status, nullable=False, server_default="new"),
        sa.Column("verification_status", verification_status, nullable=False, server_default="unverified"),
        sa.Column("event_status", event_status, nullable=False, server_default="unknown"),
        sa.Column("source_item_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="SET NULL"), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("duplicate_of_candidate_id", sa.String(length=80), sa.ForeignKey("candidate_events.candidate_id", ondelete="SET NULL"), nullable=True),
        sa.Column("duplicate_of_event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="SET NULL"), nullable=True),
        sa.Column("verified_event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_candidate_events_source_item_id"), "candidate_events", ["source_item_id"], unique=False)
    op.create_index(op.f("ix_candidate_events_location_id"), "candidate_events", ["location_id"], unique=False)
    op.create_index(op.f("ix_candidate_events_organization_id"), "candidate_events", ["organization_id"], unique=False)
    op.create_index(op.f("ix_candidate_events_duplicate_of_candidate_id"), "candidate_events", ["duplicate_of_candidate_id"], unique=False)
    op.create_index(op.f("ix_candidate_events_duplicate_of_event_id"), "candidate_events", ["duplicate_of_event_id"], unique=False)
    op.create_index(op.f("ix_candidate_events_verified_event_id"), "candidate_events", ["verified_event_id"], unique=False)
    op.create_table(
        "reviews",
        sa.Column("review_id", sa.String(length=80), primary_key=True),
        sa.Column("candidate_id", sa.String(length=80), sa.ForeignKey("candidate_events.candidate_id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("decision", candidate_status, nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prior_candidate_status", candidate_status, nullable=False),
        sa.Column("resulting_candidate_status", candidate_status, nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_reviews_candidate_id"), "reviews", ["candidate_id"], unique=False)
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=80), primary_key=True),
        sa.Column("candidate_id", sa.String(length=80), sa.ForeignKey("candidate_events.candidate_id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("domain", event_domain, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", event_status, nullable=False, server_default="unknown"),
        sa.Column("verification_status", verification_status, nullable=False, server_default="verified"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="SET NULL"), nullable=True),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="SET NULL"), nullable=True),
        sa.Column("correction_history_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_events_candidate_id"), "events", ["candidate_id"], unique=True)
    op.create_index(op.f("ix_events_published_at"), "events", ["published_at"], unique=False)
    op.create_table(
        "event_evidence",
        sa.Column("evidence_id", sa.String(length=80), primary_key=True),
        sa.Column("event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("role", evidence_role, nullable=False),
        sa.Column("evidence_strength", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("is_primary_source", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supporting_passage", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_event_evidence_event_id"), "event_evidence", ["event_id"], unique=False)
    op.create_index(op.f("ix_event_evidence_source_item_id"), "event_evidence", ["source_item_id"], unique=False)
    op.create_table(
        "event_relationships",
        sa.Column("relationship_id", sa.String(length=80), primary_key=True),
        sa.Column("from_event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("from_event_id <> to_event_id", name=op.f("ck_event_relationships_event_relationships_no_self_reference")),
    )
    op.create_index(op.f("ix_event_relationships_from_event_id"), "event_relationships", ["from_event_id"], unique=False)
    op.create_index(op.f("ix_event_relationships_to_event_id"), "event_relationships", ["to_event_id"], unique=False)
    op.create_table(
        "event_attributes",
        sa.Column("attribute_id", sa.String(length=80), primary_key=True),
        sa.Column("event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", event_domain, nullable=False),
        sa.Column("attribute_key", sa.String(length=120), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_event_attributes_event_id"), "event_attributes", ["event_id"], unique=False)
    op.create_index(op.f("ix_event_attributes_attribute_key"), "event_attributes", ["attribute_key"], unique=False)
    op.create_index(op.f("ix_event_attributes_source_item_id"), "event_attributes", ["source_item_id"], unique=False)
    op.create_index(op.f("ix_events_location_id"), "events", ["location_id"], unique=False)
    op.create_index(op.f("ix_events_organization_id"), "events", ["organization_id"], unique=False)

    for trigger_sql in SQLITE_INVARIANT_TRIGGERS:
        op.execute(trigger_sql)


def downgrade() -> None:
    for trigger_name in SQLITE_INVARIANT_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    op.drop_index(op.f("ix_events_organization_id"), table_name="events")
    op.drop_index(op.f("ix_events_location_id"), table_name="events")
    op.drop_index(op.f("ix_event_attributes_source_item_id"), table_name="event_attributes")
    op.drop_index(op.f("ix_event_attributes_attribute_key"), table_name="event_attributes")
    op.drop_index(op.f("ix_event_attributes_event_id"), table_name="event_attributes")
    op.drop_table("event_attributes")
    op.drop_index(op.f("ix_event_relationships_to_event_id"), table_name="event_relationships")
    op.drop_index(op.f("ix_event_relationships_from_event_id"), table_name="event_relationships")
    op.drop_table("event_relationships")
    op.drop_index(op.f("ix_event_evidence_source_item_id"), table_name="event_evidence")
    op.drop_index(op.f("ix_event_evidence_event_id"), table_name="event_evidence")
    op.drop_table("event_evidence")
    op.drop_index(op.f("ix_events_published_at"), table_name="events")
    op.drop_index(op.f("ix_events_candidate_id"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_reviews_candidate_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_candidate_events_verified_event_id"), table_name="candidate_events")
    op.drop_index(op.f("ix_candidate_events_duplicate_of_event_id"), table_name="candidate_events")
    op.drop_index(op.f("ix_candidate_events_duplicate_of_candidate_id"), table_name="candidate_events")
    op.drop_index(op.f("ix_candidate_events_organization_id"), table_name="candidate_events")
    op.drop_index(op.f("ix_candidate_events_location_id"), table_name="candidate_events")
    op.drop_index(op.f("ix_candidate_events_source_item_id"), table_name="candidate_events")
    op.drop_table("candidate_events")
    op.drop_index(op.f("ix_source_items_source_id"), table_name="source_items")
    op.drop_table("source_items")
    op.drop_table("organizations")
    op.drop_table("locations")
    op.drop_table("sources")
