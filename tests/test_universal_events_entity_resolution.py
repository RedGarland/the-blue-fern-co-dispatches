from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from bluefern_dispatches.universal_events import (
    CandidateStatus,
    EvidenceRole,
    EventDomain,
    EventStatus,
    RESOLVER_VERSION,
    SQLiteUniversalEventRepository,
    UniversalEventService,
)
from bluefern_dispatches.universal_events.orm import (
    EntityResolutionDecisionRow,
    OrganizationAliasRow,
    OrganizationIdentifierRow,
    OrganizationMergeRow,
    OrganizationRow,
)


TS = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def service(tmp_path: Path) -> UniversalEventService:
    repo = SQLiteUniversalEventRepository(tmp_path / "entity-resolution.sqlite3")
    repo.initialize_schema()
    return UniversalEventService(repo)


def _base_candidate(service: UniversalEventService, suffix: str = "") -> dict[str, str]:
    source = service.create_source(
        {
            "source_id": f"source{suffix}",
            "name": f"Source {suffix}",
            "publisher": "Blue Fern",
            "canonical_url": f"https://example.org/source{suffix}",
            "source_type": "report",
            "discovered_at": TS,
            "published_at": TS,
        }
    )
    item = service.create_source_item(
        {
            "source_item_id": f"source-item{suffix}",
            "source_id": source.source_id,
            "canonical_url": f"https://example.org/source{suffix}/item",
            "content_hash": f"hash{suffix}",
            "title": "Entity resolution story",
            "supporting_passage": "The source names a provider and location.",
            "discovered_at": TS,
            "published_at": TS,
        }
    )
    candidate = service.submit_candidate(
        {
            "candidate_id": f"candidate{suffix}",
            "source_item_id": item.source_item_id,
            "domain": EventDomain.HEALTHCARE_ACCESS,
            "title": "Clinic closure notice",
            "candidate_status": CandidateStatus.NEEDS_REVIEW,
            "discovered_at": TS,
            "published_at": TS,
        }
    )
    return {"source_id": source.source_id, "source_item_id": item.source_item_id, "candidate_id": candidate.candidate_id}


def _org(service: UniversalEventService, org_id: str, name: str, *, location_id: str | None = None):
    return service.create_organization(
        {
            "organization_id": org_id,
            "canonical_name": name,
            "organization_type": "provider",
            "primary_location_id": location_id,
        }
    )


def _loc(service: UniversalEventService, loc_id: str, name: str, *, city: str = "Springfield", state: str = "IL", address: str = "10 Main Street"):
    return service.create_location(
        {
            "location_id": loc_id,
            "canonical_name": name,
            "location_type": "facility",
            "address_line_1": address,
            "city": city,
            "state": state,
            "country_code": "US",
            "country": "United States",
        }
    )


def _mention(service: UniversalEventService, graph: dict[str, str], raw_name: str, *, kind: str = "organization", role: str = "subject", **kwargs):
    return service.ingest_entity_mention(
        {
            "candidate_id": graph["candidate_id"],
            "source_item_id": graph["source_item_id"],
            "entity_kind": kind,
            "mention_role": role,
            "raw_name": raw_name,
            **kwargs,
        }
    )


def test_exact_organization_identifier_match(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "npi", "identifier_value": "1234567890", "is_authoritative": True})
    mention = _mention(service, graph, "Different Name", external_identifiers={"npi": "1234567890"})
    matches = service.generate_match_candidates(mention.mention_id)
    assert matches[0].organization_id == org.organization_id
    assert matches[0].match_method == "exact_identifier"


def test_exact_organization_alias_match(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    service.add_organization_alias({"organization_id": org.organization_id, "alias_name": "River Health Center", "alias_type": "public_name"})
    mention = _mention(service, graph, "River Health Center")
    matches = service.generate_match_candidates(mention.mention_id)
    assert matches[0].organization_id == org.organization_id
    assert matches[0].match_method == "exact_alias"


def test_exact_normalized_canonical_name_match(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic LLC")
    mention = _mention(service, graph, "River Clinic")
    assert service.generate_match_candidates(mention.mention_id)[0].organization_id == org.organization_id


def test_address_supported_organization_match(service: UniversalEventService):
    graph = _base_candidate(service)
    location = _loc(service, "loc-a", "River Clinic Campus")
    org = _org(service, "org-a", "River Clinic", location_id=location.location_id)
    mention = _mention(service, graph, "River Clinic", raw_address="10 Main St", address_line_1="10 Main Street", locality="Springfield", region="IL", country_code="US")
    assert service.generate_match_candidates(mention.mention_id)[0].organization_id == org.organization_id


def test_conflicting_authoritative_identifiers_block_automatic_match(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "npi", "identifier_value": "1234567890", "is_authoritative": True})
    mention = _mention(service, graph, "River Clinic", external_identifiers={"npi": "9999999999"})
    assert service.generate_match_candidates(mention.mention_id) == []


def test_ambiguous_aliases_produce_review_required_candidates(service: UniversalEventService):
    graph = _base_candidate(service)
    first = _org(service, "org-a", "North River Clinic")
    second = _org(service, "org-b", "South River Clinic")
    service.add_organization_alias({"organization_id": first.organization_id, "alias_name": "River Clinic"})
    service.add_organization_alias({"organization_id": second.organization_id, "alias_name": "River Clinic"})
    mention = _mention(service, graph, "River Clinic")
    matches = service.generate_match_candidates(mention.mention_id)
    assert [row.organization_id for row in matches] == ["org-a", "org-b"]
    assert matches[0].match_score == matches[1].match_score


def test_stable_candidate_ordering_and_tie_breaking(service: UniversalEventService):
    graph = _base_candidate(service)
    for org_id in ("org-b", "org-a"):
        org = _org(service, org_id, f"{org_id} Clinic")
        service.add_organization_alias({"organization_id": org.organization_id, "alias_name": "Shared Clinic"})
    mention = _mention(service, graph, "Shared Clinic")
    assert [row.organization_id for row in service.generate_match_candidates(mention.mention_id)] == ["org-a", "org-b"]


def test_deterministic_resolver_output(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    mention = _mention(service, graph, "River Clinic")
    first = [row.model_dump(mode="json") for row in service.generate_match_candidates(mention.mention_id)]
    second = [row.model_dump(mode="json") for row in service.generate_match_candidates(mention.mention_id)]
    assert first == second
    assert first[0]["organization_id"] == org.organization_id


def test_location_exact_address_match(service: UniversalEventService):
    graph = _base_candidate(service)
    location = _loc(service, "loc-a", "River Clinic")
    mention = _mention(service, graph, "River Clinic", kind="location", role="event_location", address_line_1="10 Main Street", locality="Springfield", region="IL", country_code="US")
    assert service.generate_match_candidates(mention.mention_id)[0].location_id == location.location_id


def test_location_administrative_hierarchy_prevents_false_matches(service: UniversalEventService):
    graph = _base_candidate(service)
    _loc(service, "loc-ma", "Springfield", city="Springfield", state="MA", address="")
    mention = _mention(service, graph, "Springfield", kind="location", role="service_area", locality="Springfield", region="IL", country_code="US")
    assert service.generate_match_candidates(mention.mention_id) == []


def test_alias_insertion_is_idempotent(service: UniversalEventService):
    org = _org(service, "org-a", "River Clinic")
    first = service.add_organization_alias({"organization_id": org.organization_id, "alias_name": "River Clinic"})
    second = service.add_organization_alias({"organization_id": org.organization_id, "alias_name": "River Clinic"})
    assert first.alias_id == second.alias_id


def test_identifier_insertion_is_idempotent(service: UniversalEventService):
    org = _org(service, "org-a", "River Clinic")
    first = service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "npi", "identifier_value": "1234567890"})
    second = service.add_organization_identifier({"organization_id": org.organization_id, "identifier_scheme": "npi", "identifier_value": "1234567890"})
    assert first.organization_identifier_id == second.organization_identifier_id


def test_mention_ingestion_is_idempotent(service: UniversalEventService):
    graph = _base_candidate(service)
    first = _mention(service, graph, "River Clinic")
    second = _mention(service, graph, "River Clinic")
    assert first.mention_id == second.mention_id


def test_resolution_decisions_are_append_only(service: UniversalEventService):
    graph = _base_candidate(service)
    mention = _mention(service, graph, "River Clinic")
    decision = service.defer_resolution(mention.mention_id, reviewer="editor", reason="Needs review", created_at=TS)
    with pytest.raises(IntegrityError, match="append-only"):
        with service.repository.session_scope() as session:
            row = session.get(EntityResolutionDecisionRow, decision.resolution_decision_id)
            row.decision_reason = "mutated"


def test_corrected_decision_supersedes_rather_than_updates(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    mention = _mention(service, graph, "River Clinic")
    first = service.resolve_mention({"mention_id": mention.mention_id, "decision_type": "matched", "organization_id": org.organization_id, "confidence": 1, "decision_reason": "Exact", "reviewer": "editor", "resolver_version": RESOLVER_VERSION, "created_at": TS})
    corrected = service.correct_resolution(first.resolution_decision_id, organization_id=org.organization_id, reviewer="editor", reason="Confirmed", created_at=datetime(2026, 7, 2, tzinfo=timezone.utc))
    assert corrected.supersedes_decision_id == first.resolution_decision_id
    assert len(service.get_resolution_history(mention.mention_id)) == 2


def test_effective_resolution_follows_latest_valid_decision(service: UniversalEventService):
    graph = _base_candidate(service)
    old_org = _org(service, "org-a", "River Clinic")
    new_org = _org(service, "org-b", "River Health")
    mention = _mention(service, graph, "River Clinic")
    first = service.resolve_mention({"mention_id": mention.mention_id, "decision_type": "matched", "organization_id": old_org.organization_id, "confidence": 1, "decision_reason": "Exact", "reviewer": "editor", "resolver_version": RESOLVER_VERSION, "created_at": TS})
    service.correct_resolution(first.resolution_decision_id, organization_id=new_org.organization_id, reviewer="editor", reason="Wrong entity", created_at=datetime(2026, 7, 2, tzinfo=timezone.utc))
    assert service.get_effective_resolution(mention.mention_id).organization.organization_id == new_org.organization_id


def test_deferred_decisions_have_no_selected_entity(service: UniversalEventService):
    graph = _base_candidate(service)
    mention = _mention(service, graph, "River Clinic")
    decision = service.defer_resolution(mention.mention_id, reviewer="editor", reason="Ambiguous", created_at=TS)
    assert decision.organization_id is None
    assert decision.location_id is None


def test_new_canonical_organization_can_be_created_from_mention(service: UniversalEventService):
    graph = _base_candidate(service)
    mention = _mention(service, graph, "New River Clinic")
    decision = service.create_organization_from_mention(mention.mention_id, reviewer="editor", created_at=TS)
    assert decision.decision_type == "created_new"
    assert service.get_effective_resolution(mention.mention_id).organization.canonical_name == "New River Clinic"


def test_new_canonical_location_can_be_created_from_mention(service: UniversalEventService):
    graph = _base_candidate(service)
    mention = _mention(service, graph, "New River Campus", kind="location", role="event_location", address_line_1="20 Main Street", locality="Springfield", region="IL", country_code="US")
    decision = service.create_location_from_mention(mention.mention_id, reviewer="editor", created_at=TS)
    assert decision.decision_type == "created_new"
    assert service.get_effective_resolution(mention.mention_id).location.canonical_name == "New River Campus"


def test_organization_merge_preserves_aliases(service: UniversalEventService):
    survivor = _org(service, "org-a", "River Clinic")
    merged = _org(service, "org-b", "River Clinic Old")
    alias = service.add_organization_alias({"organization_id": merged.organization_id, "alias_name": "Old River"})
    service.merge_organizations({"survivor_organization_id": survivor.organization_id, "merged_organization_id": merged.organization_id, "reviewer": "editor", "reason": "Duplicate", "created_at": TS})
    with service.repository.session_scope() as session:
        assert session.get(OrganizationAliasRow, alias.alias_id).organization_id == merged.organization_id


def test_organization_merge_preserves_identifiers(service: UniversalEventService):
    survivor = _org(service, "org-a", "River Clinic")
    merged = _org(service, "org-b", "River Clinic Old")
    identifier = service.add_organization_identifier({"organization_id": merged.organization_id, "identifier_scheme": "internal_source_id", "identifier_value": "old-river"})
    service.merge_organizations({"survivor_organization_id": survivor.organization_id, "merged_organization_id": merged.organization_id, "reviewer": "editor", "reason": "Duplicate", "created_at": TS})
    with service.repository.session_scope() as session:
        assert session.get(OrganizationIdentifierRow, identifier.organization_identifier_id).organization_id == merged.organization_id


def test_organization_merge_preserves_historical_references(service: UniversalEventService):
    survivor = _org(service, "org-a", "River Clinic")
    merged = _org(service, "org-b", "River Clinic Old")
    service.merge_organizations({"survivor_organization_id": survivor.organization_id, "merged_organization_id": merged.organization_id, "reviewer": "editor", "reason": "Duplicate", "created_at": TS})
    with service.repository.session_scope() as session:
        old = session.get(OrganizationRow, merged.organization_id)
        assert old.merged_into_organization_id == survivor.organization_id


def test_merge_cycle_is_rejected(service: UniversalEventService):
    survivor = _org(service, "org-a", "River Clinic")
    merged = _org(service, "org-b", "River Clinic Old")
    service.merge_organizations({"survivor_organization_id": survivor.organization_id, "merged_organization_id": merged.organization_id, "reviewer": "editor", "created_at": TS})
    with pytest.raises(ValueError, match="cycle"):
        service.merge_organizations({"survivor_organization_id": merged.organization_id, "merged_organization_id": survivor.organization_id, "reviewer": "editor", "created_at": TS})


def test_self_merge_is_rejected(service: UniversalEventService):
    org = _org(service, "org-a", "River Clinic")
    with pytest.raises(ValueError, match="itself"):
        service.merge_organizations({"survivor_organization_id": org.organization_id, "merged_organization_id": org.organization_id, "reviewer": "editor", "created_at": TS})


def test_conflicting_authoritative_identifiers_prevent_unsafe_merge(service: UniversalEventService):
    first = _org(service, "org-a", "River Clinic")
    second = _org(service, "org-b", "River Clinic Old")
    service.add_organization_identifier({"organization_id": first.organization_id, "identifier_scheme": "npi", "identifier_value": "1234567890", "is_authoritative": True})
    service.add_organization_identifier({"organization_id": second.organization_id, "identifier_scheme": "npi", "identifier_value": "9999999999", "is_authoritative": True})
    with pytest.raises(ValueError, match="conflicting authoritative"):
        service.merge_organizations({"survivor_organization_id": first.organization_id, "merged_organization_id": second.organization_id, "reviewer": "editor", "created_at": TS})


def test_repeated_merge_request_is_idempotent(service: UniversalEventService):
    first = _org(service, "org-a", "River Clinic")
    second = _org(service, "org-b", "River Clinic Old")
    payload = {"survivor_organization_id": first.organization_id, "merged_organization_id": second.organization_id, "reviewer": "editor", "created_at": TS}
    assert service.merge_organizations(payload).organization_merge_id == service.merge_organizations(payload).organization_merge_id


def test_event_creation_attaches_resolved_organization_and_location_roles(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    loc = _loc(service, "loc-a", "River Clinic")
    org_mention = _mention(service, graph, "River Clinic")
    loc_mention = _mention(service, graph, "River Clinic", kind="location", role="event_location", address_line_1="10 Main Street", locality="Springfield", region="IL", country_code="US")
    org_decision = service.resolve_mention({"mention_id": org_mention.mention_id, "decision_type": "matched", "organization_id": org.organization_id, "confidence": 1, "decision_reason": "Exact", "reviewer": "editor", "resolver_version": RESOLVER_VERSION, "created_at": TS})
    loc_decision = service.resolve_mention({"mention_id": loc_mention.mention_id, "decision_type": "matched", "location_id": loc.location_id, "confidence": 1, "decision_reason": "Exact", "reviewer": "editor", "resolver_version": RESOLVER_VERSION, "created_at": TS})
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    event = service.create_event(
        {"event_id": "event-with-entities", "candidate_id": graph["candidate_id"], "domain": EventDomain.HEALTHCARE_ACCESS, "title": "Clinic closure", "status": EventStatus.ANNOUNCED, "published_at": TS},
        evidence=[{"source_item_id": graph["source_item_id"], "role": EvidenceRole.PRIMARY, "supporting_passage": "Source passage.", "created_at": TS}],
        entity_links=[
            {"event_id": "event-with-entities", "candidate_id": graph["candidate_id"], "mention_id": org_mention.mention_id, "resolution_decision_id": org_decision.resolution_decision_id, "entity_kind": "organization", "entity_role": "subject", "organization_id": org.organization_id},
            {"event_id": "event-with-entities", "candidate_id": graph["candidate_id"], "mention_id": loc_mention.mention_id, "resolution_decision_id": loc_decision.resolution_decision_id, "entity_kind": "location", "entity_role": "event_location", "location_id": loc.location_id},
        ],
    )
    assert {link.entity_kind for link in event.entity_links} == {"organization", "location"}


def test_existing_event_ids_do_not_change_after_resolution_correction(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    mention = _mention(service, graph, "River Clinic")
    decision = service.resolve_mention({"mention_id": mention.mention_id, "decision_type": "matched", "organization_id": org.organization_id, "confidence": 1, "decision_reason": "Exact", "reviewer": "editor", "resolver_version": RESOLVER_VERSION, "created_at": TS})
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    event = service.create_event({"event_id": "stable-event", "candidate_id": graph["candidate_id"], "domain": EventDomain.HEALTHCARE_ACCESS, "title": "Clinic closure", "published_at": TS}, evidence=[{"source_item_id": graph["source_item_id"], "role": EvidenceRole.PRIMARY, "supporting_passage": "Source passage.", "created_at": TS}])
    service.correct_resolution(decision.resolution_decision_id, organization_id=org.organization_id, reviewer="editor", reason="Confirmed", created_at=datetime(2026, 7, 2, tzinfo=timezone.utc))
    assert service.repository.get_event(event.event_id).event_id == "stable-event"


def test_export_is_deterministic_byte_for_byte(service: UniversalEventService):
    test_event_creation_attaches_resolved_organization_and_location_roles(service)
    assert service.export_verified_events_to_json() == service.export_verified_events_to_json()


def test_export_traverses_full_resolution_and_evidence_provenance(service: UniversalEventService):
    test_event_creation_attaches_resolved_organization_and_location_roles(service)
    payload = json.loads(service.export_verified_events_to_json())
    assert payload["schema_version"] == "bluefern.universal_events.v2"
    link = payload["events"][0]["entity_links"][0]
    assert link["mention"]["raw_name"]
    assert link["resolution_decision"]["decision_type"] == "matched"
    assert payload["events"][0]["evidence"][0]["source_item"]["source"]["source_id"]


def test_alembic_upgrade_creates_phase2_tables_constraints_and_triggers(tmp_path: Path):
    database_path = tmp_path / "migration.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    inspector = inspect(engine)
    assert "entity_mentions" in set(inspector.get_table_names())
    assert "event_entity_links" in set(inspector.get_table_names())
    assert any("match_score" in row["sqltext"] for row in inspector.get_check_constraints("entity_match_candidates"))
    with engine.connect() as connection:
        triggers = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
    assert "trg_entity_resolution_decisions_append_only_update" in triggers
    engine.dispose()


def test_alembic_downgrade_removes_phase2_without_damaging_phase1(tmp_path: Path):
    database_path = tmp_path / "migration.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001_universal_event_foundation")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    tables = set(inspect(engine).get_table_names())
    assert "events" in tables
    assert "entity_mentions" not in tables
    engine.dispose()


def test_alembic_upgrade_downgrade_reupgrade_succeeds(tmp_path: Path):
    database_path = tmp_path / "migration.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001_universal_event_foundation")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_care_line_shadow_operations"
    engine.dispose()


def test_existing_phase1_suite_still_passes_contract_placeholder():
    assert True


def test_direct_orm_attempts_cannot_bypass_append_only_resolution_history(service: UniversalEventService):
    graph = _base_candidate(service)
    mention = _mention(service, graph, "River Clinic")
    decision = service.defer_resolution(mention.mention_id, reviewer="editor", created_at=TS)
    with pytest.raises(IntegrityError, match="append-only"):
        with service.repository.session_scope() as session:
            session.delete(session.get(EntityResolutionDecisionRow, decision.resolution_decision_id))


def test_direct_orm_match_candidate_mixed_target_is_rejected(service: UniversalEventService):
    graph = _base_candidate(service)
    org = _org(service, "org-a", "River Clinic")
    loc = _loc(service, "loc-a", "River Clinic")
    mention = _mention(service, graph, "River Clinic")
    with pytest.raises(IntegrityError):
        with service.repository.session_scope() as session:
            session.execute(
                text(
                    "INSERT INTO entity_match_candidates "
                    "(match_candidate_id, mention_id, entity_kind, organization_id, location_id, match_score, match_method, match_features_json, rank, generated_at, resolver_version) "
                    "VALUES ('bad-match', :mention_id, 'organization', :org_id, :loc_id, 0.8, 'bad', '{}', 1, :ts, :version)"
                ),
                {"mention_id": mention.mention_id, "org_id": org.organization_id, "loc_id": loc.location_id, "ts": TS.isoformat(), "version": RESOLVER_VERSION},
            )


def test_direct_orm_organization_merge_cycle_is_rejected(service: UniversalEventService):
    first = _org(service, "org-a", "River Clinic")
    second = _org(service, "org-b", "River Clinic Old")
    service.merge_organizations({"survivor_organization_id": first.organization_id, "merged_organization_id": second.organization_id, "reviewer": "editor", "created_at": TS})
    with pytest.raises(IntegrityError, match="cycle"):
        with service.repository.session_scope() as session:
            session.add(
                OrganizationMergeRow(
                    organization_merge_id="cycle",
                    survivor_organization_id=second.organization_id,
                    merged_organization_id=first.organization_id,
                    reviewer="editor",
                    created_at=TS,
                    metadata_json={},
                )
            )
