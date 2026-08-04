from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_universal_entity_resolution"
down_revision = "0001_universal_event_foundation"
branch_labels = None
depends_on = None


PHASE2_TRIGGERS = (
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
)


PHASE2_TRIGGER_NAMES = (
    "trg_organization_merges_direct_cycle_update",
    "trg_organization_merges_direct_cycle_insert",
    "trg_entity_resolution_decisions_append_only_delete",
    "trg_entity_resolution_decisions_append_only_update",
)


def upgrade() -> None:
    op.add_column("locations", sa.Column("normalized_canonical_name", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("locations", sa.Column("address_line_1", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("locations", sa.Column("address_line_2", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("locations", sa.Column("postal_code", sa.String(length=32), nullable=False, server_default=""))
    op.add_column("locations", sa.Column("country_code", sa.String(length=2), nullable=False, server_default=""))
    op.add_column("locations", sa.Column("merged_into_location_id", sa.String(length=80), nullable=True))
    op.create_index(op.f("ix_locations_normalized_canonical_name"), "locations", ["normalized_canonical_name"], unique=False)
    op.create_index(op.f("ix_locations_merged_into_location_id"), "locations", ["merged_into_location_id"], unique=False)

    op.add_column("organizations", sa.Column("normalized_canonical_name", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("parent_organization_id", sa.String(length=80), nullable=True))
    op.add_column("organizations", sa.Column("primary_location_id", sa.String(length=80), nullable=True))
    op.add_column("organizations", sa.Column("operational_status", sa.String(length=80), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("canonical_domain", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("organizations", sa.Column("merged_into_organization_id", sa.String(length=80), nullable=True))
    op.add_column("organizations", sa.Column("merge_status", sa.String(length=40), nullable=False, server_default="active"))
    op.create_index(op.f("ix_organizations_normalized_canonical_name"), "organizations", ["normalized_canonical_name"], unique=False)
    op.create_index(op.f("ix_organizations_parent_organization_id"), "organizations", ["parent_organization_id"], unique=False)
    op.create_index(op.f("ix_organizations_primary_location_id"), "organizations", ["primary_location_id"], unique=False)
    op.create_index(op.f("ix_organizations_canonical_domain"), "organizations", ["canonical_domain"], unique=False)
    op.create_index(op.f("ix_organizations_merged_into_organization_id"), "organizations", ["merged_into_organization_id"], unique=False)

    op.create_table(
        "organization_aliases",
        sa.Column("alias_id", sa.String(length=80), primary_key=True),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("alias_type", sa.String(length=80), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(alias_name)) > 0", name=op.f("ck_organization_aliases_organization_aliases_alias_not_blank")),
        sa.CheckConstraint("length(trim(normalized_alias)) > 0", name=op.f("ck_organization_aliases_organization_aliases_normalized_not_blank")),
        sa.UniqueConstraint("organization_id", "normalized_alias", "alias_type", "source_item_id", name="uq_organization_aliases_org_alias_type_source"),
    )
    op.create_index(op.f("ix_organization_aliases_organization_id"), "organization_aliases", ["organization_id"], unique=False)
    op.create_index(op.f("ix_organization_aliases_normalized_alias"), "organization_aliases", ["normalized_alias"], unique=False)
    op.create_index(op.f("ix_organization_aliases_source_item_id"), "organization_aliases", ["source_item_id"], unique=False)

    op.create_table(
        "organization_identifiers",
        sa.Column("organization_identifier_id", sa.String(length=80), primary_key=True),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("identifier_scheme", sa.String(length=80), nullable=False),
        sa.Column("identifier_value", sa.String(length=255), nullable=False),
        sa.Column("normalized_value", sa.String(length=255), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_authoritative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(identifier_scheme)) > 0", name=op.f("ck_organization_identifiers_organization_identifiers_scheme_not_blank")),
        sa.CheckConstraint("length(trim(identifier_value)) > 0", name=op.f("ck_organization_identifiers_organization_identifiers_value_not_blank")),
        sa.CheckConstraint("length(trim(normalized_value)) > 0", name=op.f("ck_organization_identifiers_organization_identifiers_normalized_not_blank")),
        sa.UniqueConstraint("organization_id", "identifier_scheme", "normalized_value", "source_item_id", name="uq_organization_identifiers_org_scheme_value_source"),
    )
    op.create_index(op.f("ix_organization_identifiers_organization_id"), "organization_identifiers", ["organization_id"], unique=False)
    op.create_index(op.f("ix_organization_identifiers_source_item_id"), "organization_identifiers", ["source_item_id"], unique=False)
    op.create_index("ix_organization_identifiers_scheme_value", "organization_identifiers", ["identifier_scheme", "normalized_value"], unique=False)
    op.create_index(
        "uq_organization_identifiers_authoritative_scheme_value",
        "organization_identifiers",
        ["identifier_scheme", "normalized_value"],
        unique=True,
        sqlite_where=sa.text("is_authoritative = 1 AND valid_to IS NULL"),
    )

    op.create_table(
        "location_aliases",
        sa.Column("location_alias_id", sa.String(length=80), primary_key=True),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.Column("alias_type", sa.String(length=80), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(alias_name)) > 0", name=op.f("ck_location_aliases_location_aliases_alias_not_blank")),
        sa.CheckConstraint("length(trim(normalized_alias)) > 0", name=op.f("ck_location_aliases_location_aliases_normalized_not_blank")),
        sa.UniqueConstraint("location_id", "normalized_alias", "alias_type", "source_item_id", name="uq_location_aliases_location_alias_type_source"),
    )
    op.create_index(op.f("ix_location_aliases_location_id"), "location_aliases", ["location_id"], unique=False)
    op.create_index(op.f("ix_location_aliases_normalized_alias"), "location_aliases", ["normalized_alias"], unique=False)
    op.create_index(op.f("ix_location_aliases_source_item_id"), "location_aliases", ["source_item_id"], unique=False)

    op.create_table(
        "location_identifiers",
        sa.Column("location_identifier_id", sa.String(length=80), primary_key=True),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False),
        sa.Column("identifier_scheme", sa.String(length=80), nullable=False),
        sa.Column("identifier_value", sa.String(length=255), nullable=False),
        sa.Column("normalized_value", sa.String(length=255), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_authoritative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(identifier_scheme)) > 0", name=op.f("ck_location_identifiers_location_identifiers_scheme_not_blank")),
        sa.CheckConstraint("length(trim(identifier_value)) > 0", name=op.f("ck_location_identifiers_location_identifiers_value_not_blank")),
        sa.CheckConstraint("length(trim(normalized_value)) > 0", name=op.f("ck_location_identifiers_location_identifiers_normalized_not_blank")),
        sa.UniqueConstraint("location_id", "identifier_scheme", "normalized_value", "source_item_id", name="uq_location_identifiers_location_scheme_value_source"),
    )
    op.create_index(op.f("ix_location_identifiers_location_id"), "location_identifiers", ["location_id"], unique=False)
    op.create_index(op.f("ix_location_identifiers_source_item_id"), "location_identifiers", ["source_item_id"], unique=False)
    op.create_index("ix_location_identifiers_scheme_value", "location_identifiers", ["identifier_scheme", "normalized_value"], unique=False)
    op.create_index(
        "uq_location_identifiers_authoritative_scheme_value",
        "location_identifiers",
        ["identifier_scheme", "normalized_value"],
        unique=True,
        sqlite_where=sa.text("is_authoritative = 1"),
    )

    op.create_table(
        "entity_mentions",
        sa.Column("mention_id", sa.String(length=80), primary_key=True),
        sa.Column("candidate_id", sa.String(length=80), sa.ForeignKey("candidate_events.candidate_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("entity_kind", sa.String(length=40), nullable=False),
        sa.Column("mention_role", sa.String(length=80), nullable=False),
        sa.Column("raw_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("raw_address", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("address_line_1", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("address_line_2", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("locality", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("region", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("postal_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("country_code", sa.String(length=2), nullable=False, server_default=""),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("external_identifiers_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("entity_kind IN ('organization', 'location')", name=op.f("ck_entity_mentions_entity_mentions_valid_kind")),
        sa.CheckConstraint("length(trim(raw_name)) > 0", name=op.f("ck_entity_mentions_entity_mentions_raw_name_not_blank")),
        sa.CheckConstraint("length(trim(normalized_name)) > 0", name=op.f("ck_entity_mentions_entity_mentions_normalized_name_not_blank")),
        sa.UniqueConstraint("candidate_id", "source_item_id", "entity_kind", "mention_role", "normalized_name", "raw_address", name="uq_entity_mentions_stable_ingestion"),
    )
    op.create_index(op.f("ix_entity_mentions_candidate_id"), "entity_mentions", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_entity_mentions_source_item_id"), "entity_mentions", ["source_item_id"], unique=False)
    op.create_index(op.f("ix_entity_mentions_entity_kind"), "entity_mentions", ["entity_kind"], unique=False)
    op.create_index(op.f("ix_entity_mentions_normalized_name"), "entity_mentions", ["normalized_name"], unique=False)

    op.create_table(
        "entity_match_candidates",
        sa.Column("match_candidate_id", sa.String(length=80), primary_key=True),
        sa.Column("mention_id", sa.String(length=80), sa.ForeignKey("entity_mentions.mention_id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_kind", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=True),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("match_method", sa.String(length=80), nullable=False),
        sa.Column("match_features_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolver_version", sa.String(length=80), nullable=False),
        sa.CheckConstraint("entity_kind IN ('organization', 'location')", name=op.f("ck_entity_match_candidates_entity_match_candidates_valid_kind")),
        sa.CheckConstraint("((organization_id IS NOT NULL) AND (location_id IS NULL) AND entity_kind = 'organization') OR ((organization_id IS NULL) AND (location_id IS NOT NULL) AND entity_kind = 'location')", name=op.f("ck_entity_match_candidates_entity_match_candidates_one_matching_target")),
        sa.CheckConstraint("match_score >= 0.0 AND match_score <= 1.0", name=op.f("ck_entity_match_candidates_entity_match_candidates_score_bounds")),
        sa.UniqueConstraint("mention_id", "organization_id", "location_id", "resolver_version", name="uq_entity_match_candidates_mention_target_version"),
    )
    op.create_index(op.f("ix_entity_match_candidates_mention_id"), "entity_match_candidates", ["mention_id"], unique=False)
    op.create_index(op.f("ix_entity_match_candidates_organization_id"), "entity_match_candidates", ["organization_id"], unique=False)
    op.create_index(op.f("ix_entity_match_candidates_location_id"), "entity_match_candidates", ["location_id"], unique=False)

    op.create_table(
        "entity_resolution_decisions",
        sa.Column("resolution_decision_id", sa.String(length=80), primary_key=True),
        sa.Column("mention_id", sa.String(length=80), sa.ForeignKey("entity_mentions.mention_id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision_type", sa.String(length=40), nullable=False),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("selected_match_candidate_id", sa.String(length=80), sa.ForeignKey("entity_match_candidates.match_candidate_id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("decision_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("resolver_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_decision_id", sa.String(length=80), sa.ForeignKey("entity_resolution_decisions.resolution_decision_id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint("decision_type IN ('matched', 'created_new', 'deferred', 'rejected_match', 'corrected', 'unresolved')", name=op.f("ck_entity_resolution_decisions_entity_resolution_decisions_valid_type")),
        sa.CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name=op.f("ck_entity_resolution_decisions_entity_resolution_decisions_confidence_bounds")),
        sa.CheckConstraint("NOT (supersedes_decision_id = resolution_decision_id)", name=op.f("ck_entity_resolution_decisions_entity_resolution_decisions_no_self_supersession")),
        sa.CheckConstraint("((decision_type IN ('matched', 'created_new', 'corrected') AND ((organization_id IS NOT NULL AND location_id IS NULL) OR (organization_id IS NULL AND location_id IS NOT NULL))) OR (decision_type IN ('deferred', 'unresolved', 'rejected_match') AND organization_id IS NULL AND location_id IS NULL))", name=op.f("ck_entity_resolution_decisions_entity_resolution_decisions_target_rules")),
    )
    op.create_index(op.f("ix_entity_resolution_decisions_mention_id"), "entity_resolution_decisions", ["mention_id"], unique=False)
    op.create_index(op.f("ix_entity_resolution_decisions_decision_type"), "entity_resolution_decisions", ["decision_type"], unique=False)
    op.create_index(op.f("ix_entity_resolution_decisions_organization_id"), "entity_resolution_decisions", ["organization_id"], unique=False)
    op.create_index(op.f("ix_entity_resolution_decisions_location_id"), "entity_resolution_decisions", ["location_id"], unique=False)
    op.create_index(op.f("ix_entity_resolution_decisions_selected_match_candidate_id"), "entity_resolution_decisions", ["selected_match_candidate_id"], unique=False)
    op.create_index(op.f("ix_entity_resolution_decisions_supersedes_decision_id"), "entity_resolution_decisions", ["supersedes_decision_id"], unique=False)

    op.create_table(
        "organization_relationships",
        sa.Column("organization_relationship_id", sa.String(length=80), primary_key=True),
        sa.Column("from_organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(length=80), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("from_organization_id <> to_organization_id", name=op.f("ck_organization_relationships_organization_relationships_no_self_link")),
        sa.UniqueConstraint("from_organization_id", "to_organization_id", "relationship_type", "valid_from", name="uq_organization_relationships_stable"),
    )
    op.create_index(op.f("ix_organization_relationships_from_organization_id"), "organization_relationships", ["from_organization_id"], unique=False)
    op.create_index(op.f("ix_organization_relationships_to_organization_id"), "organization_relationships", ["to_organization_id"], unique=False)
    op.create_index(op.f("ix_organization_relationships_source_item_id"), "organization_relationships", ["source_item_id"], unique=False)

    op.create_table(
        "organization_location_relationships",
        sa.Column("organization_location_relationship_id", sa.String(length=80), primary_key=True),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="CASCADE"), nullable=False),
        sa.Column("relationship_type", sa.String(length=80), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("organization_id", "location_id", "relationship_type", "valid_from", name="uq_organization_location_relationships_stable"),
    )
    op.create_index(op.f("ix_organization_location_relationships_organization_id"), "organization_location_relationships", ["organization_id"], unique=False)
    op.create_index(op.f("ix_organization_location_relationships_location_id"), "organization_location_relationships", ["location_id"], unique=False)
    op.create_index(op.f("ix_organization_location_relationships_source_item_id"), "organization_location_relationships", ["source_item_id"], unique=False)

    op.create_table(
        "organization_merges",
        sa.Column("organization_merge_id", sa.String(length=80), primary_key=True),
        sa.Column("survivor_organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("merged_organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_item_id", sa.String(length=80), sa.ForeignKey("source_items.source_item_id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("survivor_organization_id <> merged_organization_id", name=op.f("ck_organization_merges_organization_merges_no_self_merge")),
        sa.UniqueConstraint("survivor_organization_id", "merged_organization_id", name="uq_organization_merges_pair"),
    )
    op.create_index(op.f("ix_organization_merges_survivor_organization_id"), "organization_merges", ["survivor_organization_id"], unique=False)
    op.create_index(op.f("ix_organization_merges_merged_organization_id"), "organization_merges", ["merged_organization_id"], unique=False)
    op.create_index(op.f("ix_organization_merges_source_item_id"), "organization_merges", ["source_item_id"], unique=False)

    op.create_table(
        "event_entity_links",
        sa.Column("event_entity_link_id", sa.String(length=80), primary_key=True),
        sa.Column("event_id", sa.String(length=80), sa.ForeignKey("events.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", sa.String(length=80), sa.ForeignKey("candidate_events.candidate_id", ondelete="CASCADE"), nullable=False),
        sa.Column("mention_id", sa.String(length=80), sa.ForeignKey("entity_mentions.mention_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolution_decision_id", sa.String(length=80), sa.ForeignKey("entity_resolution_decisions.resolution_decision_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("entity_kind", sa.String(length=40), nullable=False),
        sa.Column("entity_role", sa.String(length=80), nullable=False),
        sa.Column("organization_id", sa.String(length=80), sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("location_id", sa.String(length=80), sa.ForeignKey("locations.location_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("entity_kind IN ('organization', 'location')", name=op.f("ck_event_entity_links_event_entity_links_valid_kind")),
        sa.CheckConstraint("((organization_id IS NOT NULL) AND (location_id IS NULL) AND entity_kind = 'organization') OR ((organization_id IS NULL) AND (location_id IS NOT NULL) AND entity_kind = 'location')", name=op.f("ck_event_entity_links_event_entity_links_one_matching_target")),
        sa.UniqueConstraint("event_id", "mention_id", "entity_role", "organization_id", "location_id", name="uq_event_entity_links_stable"),
    )
    op.create_index(op.f("ix_event_entity_links_event_id"), "event_entity_links", ["event_id"], unique=False)
    op.create_index(op.f("ix_event_entity_links_candidate_id"), "event_entity_links", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_event_entity_links_mention_id"), "event_entity_links", ["mention_id"], unique=False)
    op.create_index(op.f("ix_event_entity_links_resolution_decision_id"), "event_entity_links", ["resolution_decision_id"], unique=False)
    op.create_index(op.f("ix_event_entity_links_organization_id"), "event_entity_links", ["organization_id"], unique=False)
    op.create_index(op.f("ix_event_entity_links_location_id"), "event_entity_links", ["location_id"], unique=False)

    for trigger_sql in PHASE2_TRIGGERS:
        op.execute(trigger_sql)


def downgrade() -> None:
    for trigger_name in PHASE2_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    op.drop_index(op.f("ix_event_entity_links_location_id"), table_name="event_entity_links")
    op.drop_index(op.f("ix_event_entity_links_organization_id"), table_name="event_entity_links")
    op.drop_index(op.f("ix_event_entity_links_resolution_decision_id"), table_name="event_entity_links")
    op.drop_index(op.f("ix_event_entity_links_mention_id"), table_name="event_entity_links")
    op.drop_index(op.f("ix_event_entity_links_candidate_id"), table_name="event_entity_links")
    op.drop_index(op.f("ix_event_entity_links_event_id"), table_name="event_entity_links")
    op.drop_table("event_entity_links")
    op.drop_index(op.f("ix_organization_merges_source_item_id"), table_name="organization_merges")
    op.drop_index(op.f("ix_organization_merges_merged_organization_id"), table_name="organization_merges")
    op.drop_index(op.f("ix_organization_merges_survivor_organization_id"), table_name="organization_merges")
    op.drop_table("organization_merges")
    op.drop_index(op.f("ix_organization_location_relationships_source_item_id"), table_name="organization_location_relationships")
    op.drop_index(op.f("ix_organization_location_relationships_location_id"), table_name="organization_location_relationships")
    op.drop_index(op.f("ix_organization_location_relationships_organization_id"), table_name="organization_location_relationships")
    op.drop_table("organization_location_relationships")
    op.drop_index(op.f("ix_organization_relationships_source_item_id"), table_name="organization_relationships")
    op.drop_index(op.f("ix_organization_relationships_to_organization_id"), table_name="organization_relationships")
    op.drop_index(op.f("ix_organization_relationships_from_organization_id"), table_name="organization_relationships")
    op.drop_table("organization_relationships")
    op.drop_index(op.f("ix_entity_resolution_decisions_supersedes_decision_id"), table_name="entity_resolution_decisions")
    op.drop_index(op.f("ix_entity_resolution_decisions_selected_match_candidate_id"), table_name="entity_resolution_decisions")
    op.drop_index(op.f("ix_entity_resolution_decisions_location_id"), table_name="entity_resolution_decisions")
    op.drop_index(op.f("ix_entity_resolution_decisions_organization_id"), table_name="entity_resolution_decisions")
    op.drop_index(op.f("ix_entity_resolution_decisions_decision_type"), table_name="entity_resolution_decisions")
    op.drop_index(op.f("ix_entity_resolution_decisions_mention_id"), table_name="entity_resolution_decisions")
    op.drop_table("entity_resolution_decisions")
    op.drop_index(op.f("ix_entity_match_candidates_location_id"), table_name="entity_match_candidates")
    op.drop_index(op.f("ix_entity_match_candidates_organization_id"), table_name="entity_match_candidates")
    op.drop_index(op.f("ix_entity_match_candidates_mention_id"), table_name="entity_match_candidates")
    op.drop_table("entity_match_candidates")
    op.drop_index(op.f("ix_entity_mentions_normalized_name"), table_name="entity_mentions")
    op.drop_index(op.f("ix_entity_mentions_entity_kind"), table_name="entity_mentions")
    op.drop_index(op.f("ix_entity_mentions_source_item_id"), table_name="entity_mentions")
    op.drop_index(op.f("ix_entity_mentions_candidate_id"), table_name="entity_mentions")
    op.drop_table("entity_mentions")
    op.drop_index("uq_location_identifiers_authoritative_scheme_value", table_name="location_identifiers")
    op.drop_index("ix_location_identifiers_scheme_value", table_name="location_identifiers")
    op.drop_index(op.f("ix_location_identifiers_source_item_id"), table_name="location_identifiers")
    op.drop_index(op.f("ix_location_identifiers_location_id"), table_name="location_identifiers")
    op.drop_table("location_identifiers")
    op.drop_index(op.f("ix_location_aliases_source_item_id"), table_name="location_aliases")
    op.drop_index(op.f("ix_location_aliases_normalized_alias"), table_name="location_aliases")
    op.drop_index(op.f("ix_location_aliases_location_id"), table_name="location_aliases")
    op.drop_table("location_aliases")
    op.drop_index("uq_organization_identifiers_authoritative_scheme_value", table_name="organization_identifiers")
    op.drop_index("ix_organization_identifiers_scheme_value", table_name="organization_identifiers")
    op.drop_index(op.f("ix_organization_identifiers_source_item_id"), table_name="organization_identifiers")
    op.drop_index(op.f("ix_organization_identifiers_organization_id"), table_name="organization_identifiers")
    op.drop_table("organization_identifiers")
    op.drop_index(op.f("ix_organization_aliases_source_item_id"), table_name="organization_aliases")
    op.drop_index(op.f("ix_organization_aliases_normalized_alias"), table_name="organization_aliases")
    op.drop_index(op.f("ix_organization_aliases_organization_id"), table_name="organization_aliases")
    op.drop_table("organization_aliases")

    op.drop_index(op.f("ix_organizations_merged_into_organization_id"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_canonical_domain"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_primary_location_id"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_parent_organization_id"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_normalized_canonical_name"), table_name="organizations")
    for column in (
        "merge_status",
        "merged_into_organization_id",
        "canonical_domain",
        "operational_status",
        "primary_location_id",
        "parent_organization_id",
        "normalized_canonical_name",
    ):
        op.drop_column("organizations", column)

    op.drop_index(op.f("ix_locations_merged_into_location_id"), table_name="locations")
    op.drop_index(op.f("ix_locations_normalized_canonical_name"), table_name="locations")
    for column in (
        "merged_into_location_id",
        "country_code",
        "postal_code",
        "address_line_2",
        "address_line_1",
        "normalized_canonical_name",
    ):
        op.drop_column("locations", column)
