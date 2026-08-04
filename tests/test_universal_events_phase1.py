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
    SQLiteUniversalEventRepository,
    SourceCreate,
    SourceItemCreate,
    UniversalEventService,
    VerificationStatus,
    load_seed_bundle,
    seed_database,
)
from bluefern_dispatches.universal_events.orm import CandidateEventRow, EventRelationshipRow, EventRow, ReviewRow


TS = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def service(tmp_path: Path) -> UniversalEventService:
    repo = SQLiteUniversalEventRepository(tmp_path / "universal-events.sqlite3")
    repo.initialize_schema()
    return UniversalEventService(repo)


def _create_base_graph(service: UniversalEventService, *, suffix: str = "") -> dict[str, str]:
    source = service.create_source(
        SourceCreate(
            source_id=f"source{suffix}",
            name=f"Source{suffix}",
            publisher="Blue Fern",
            canonical_url=f"https://example.org/source{suffix}",
            source_type="report",
            discovered_at=TS,
            published_at=TS,
            retrieved_at=TS,
        )
    )
    source_item = service.create_source_item(
        SourceItemCreate(
            source_item_id=f"source-item{suffix}",
            source_id=source.source_id,
            canonical_url=f"https://example.org/source{suffix}/item",
            content_hash=f"hash{suffix}",
            title=f"Example story{suffix}",
            supporting_passage="A source item supporting passage.",
            discovered_at=TS,
            published_at=TS,
            retrieved_at=TS,
        )
    )
    location = service.create_location(
        {
            "location_id": f"location{suffix}",
            "canonical_name": f"Sample Region{suffix}",
            "display_name": f"Sample Region{suffix}",
            "location_type": "region",
            "country": "United States",
        }
    )
    organization = service.create_organization(
        {
            "organization_id": f"organization{suffix}",
            "canonical_name": f"Blue Fern Org{suffix}",
            "display_name": f"Blue Fern Org{suffix}",
            "organization_type": "publisher",
        }
    )
    candidate = service.submit_candidate(
        {
            "candidate_id": f"candidate{suffix}",
            "source_item_id": source_item.source_item_id,
            "domain": EventDomain.FOOD_SECURITY,
            "title": f"Example story{suffix}",
            "summary": "Seeded candidate summary.",
            "candidate_status": CandidateStatus.NEEDS_REVIEW,
            "verification_status": VerificationStatus.UNVERIFIED,
            "event_status": EventStatus.UNKNOWN,
            "source_item_ids": [source_item.source_item_id],
            "location_id": location.location_id,
            "organization_id": organization.organization_id,
            "discovered_at": TS,
            "published_at": TS,
        }
    )
    return {
        "source_id": source.source_id,
        "source_item_id": source_item.source_item_id,
        "location_id": location.location_id,
        "organization_id": organization.organization_id,
        "candidate_id": candidate.candidate_id,
    }


def test_candidate_creation_and_duplicate_handling(service: UniversalEventService):
    primary = _create_base_graph(service)
    duplicate = _create_base_graph(service, suffix="-dup")
    dup_matches = service.find_possible_duplicates(duplicate["candidate_id"])
    assert dup_matches
    assert dup_matches[0]["kind"] == "candidate"
    assert dup_matches[0]["candidate_id"] == primary["candidate_id"]
    merged = service.merge_candidate(
        duplicate["candidate_id"],
        retained_candidate_id=primary["candidate_id"],
        reviewer="editor",
        notes="Duplicate story merged to retained candidate.",
    )
    assert merged.candidate_status == CandidateStatus.DUPLICATE
    assert merged.duplicate_of_candidate_id == primary["candidate_id"]


def test_candidate_approval_and_event_creation_with_evidence(service: UniversalEventService):
    graph = _create_base_graph(service)
    approved = service.approve_candidate(graph["candidate_id"], reviewer="editor", notes="Looks good.")
    assert approved.candidate_status == CandidateStatus.APPROVED
    assert approved.verification_status == VerificationStatus.PARTIALLY_VERIFIED
    event = service.create_event(
        {
            "candidate_id": approved.candidate_id,
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Example story",
            "summary": "Verified event summary.",
            "status": EventStatus.ONGOING,
            "published_at": TS,
            "location_id": graph["location_id"],
            "organization_id": graph["organization_id"],
        },
        evidence=[
            {
                "event_id": "unused",
                "source_item_id": graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "evidence_strength": "direct",
                "is_primary_source": True,
                "supporting_passage": "A source item supporting passage.",
                "created_at": TS,
            }
        ],
    )
    assert event.verification_status == VerificationStatus.VERIFIED
    assert event.evidence
    assert event.evidence[0].role == EvidenceRole.PRIMARY
    assert event.candidate_id == approved.candidate_id
    assert event.location.location_id == graph["location_id"]
    exported = json.loads(service.export_verified_events_to_json())
    assert exported["schema_version"] == "bluefern.universal_events.v1"
    assert exported["events"]
    assert exported["events"][0]["event_id"] == event.event_id
    evidence = exported["events"][0]["evidence"][0]
    assert evidence["evidence_strength"] == "direct"
    assert evidence["is_primary_source"] is True
    assert evidence["source_item"]["canonical_url"] == "https://example.org/source/item"
    assert evidence["source_item"]["source"]["source_id"] == graph["source_id"]


def test_candidate_rejection_preserves_review_history(service: UniversalEventService):
    graph = _create_base_graph(service)
    first = service.reject_candidate(graph["candidate_id"], reviewer="editor", notes="Not ready.")
    second = service.approve_candidate(graph["candidate_id"], reviewer="editor", notes="Reconsidered.")
    assert first.candidate_status == CandidateStatus.REJECTED
    assert second.candidate_status == CandidateStatus.APPROVED
    candidate = service.repository.get_candidate(graph["candidate_id"])
    assert candidate is not None
    assert len(candidate.reviews) == 2
    assert candidate.reviews[0].decision == CandidateStatus.REJECTED
    assert candidate.reviews[1].decision == CandidateStatus.APPROVED


def test_evidence_free_verified_events_are_blocked(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    with pytest.raises(ValueError, match="evidence record"):
        service.create_event(
            {
                "candidate_id": graph["candidate_id"],
                "domain": EventDomain.FOOD_SECURITY,
                "title": "Example story",
                "summary": "Missing evidence.",
                "status": EventStatus.UNKNOWN,
                "published_at": TS,
            },
            evidence=[],
        )


def test_event_correction_preserves_history(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    event = service.create_event(
        {
            "candidate_id": graph["candidate_id"],
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Example story",
            "summary": "Original summary.",
            "status": EventStatus.ANNOUNCED,
            "published_at": TS,
        },
        evidence=[
            {
                "event_id": "unused",
                "source_item_id": graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "supporting_passage": "A source item supporting passage.",
                "created_at": TS,
            }
        ],
    )
    corrected = service.correct_event(
        event.event_id,
        updates={"title": "Example story corrected", "summary": "Corrected summary."},
        note="Fix the summary after source review.",
        reviewer="editor",
        source_item_id=graph["source_item_id"],
        supporting_passage="Correction passage.",
    )
    assert corrected.verification_status == VerificationStatus.CORRECTED
    assert corrected.title == "Example story corrected"
    assert corrected.summary == "Corrected summary."
    assert len(corrected.correction_history) == 1
    assert corrected.correction_history[0]["before"]["summary"] == "Original summary."
    assert corrected.correction_history[0]["after"]["summary"] == "Corrected summary."
    assert corrected.correction_history[0]["changed_fields"]["summary"] == {
        "before": "Original summary.",
        "after": "Corrected summary.",
    }
    assert corrected.event_id == event.event_id


def test_event_relationships_and_domain_attributes(service: UniversalEventService):
    first_graph = _create_base_graph(service)
    second_graph = _create_base_graph(service, suffix="-second")
    service.approve_candidate(first_graph["candidate_id"], reviewer="editor")
    service.approve_candidate(second_graph["candidate_id"], reviewer="editor")
    first_event = service.create_event(
        {
            "candidate_id": first_graph["candidate_id"],
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Example story",
            "summary": "First event.",
            "status": EventStatus.ACTIVE,
            "published_at": TS,
        },
        evidence=[
            {
                "event_id": "unused",
                "source_item_id": first_graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "supporting_passage": "A source item supporting passage.",
                "created_at": TS,
            }
        ],
    )
    second_event = service.create_event(
        {
            "candidate_id": second_graph["candidate_id"],
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Example story-second",
            "summary": "Second event.",
            "status": EventStatus.ACTIVE,
            "published_at": TS,
        },
        evidence=[
            {
                "event_id": "unused",
                "source_item_id": second_graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "supporting_passage": "Another source item supporting passage.",
                "created_at": TS,
            }
        ],
    )
    relationship = service.add_event_relationship(
        {
            "from_event_id": first_event.event_id,
            "to_event_id": second_event.event_id,
            "relationship_type": "related_to",
            "created_at": TS,
        }
    )
    attribute = service.add_event_attribute(
        {
            "event_id": first_event.event_id,
            "domain": EventDomain.FOOD_SECURITY,
            "attribute_key": "pressure_signal",
            "value": {"direction": "rising"},
            "created_at": TS,
        }
    )
    queried = service.query_events()
    chosen = next(event for event in queried if event.event_id == first_event.event_id)
    assert chosen.relationships or chosen.attributes
    assert relationship.relationship_type == "related_to"
    assert attribute.attribute_key == "pressure_signal"


def test_json_export_and_verification_filtering(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    verified_event = service.create_event(
        {
            "candidate_id": graph["candidate_id"],
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Example story",
            "summary": "Verified summary.",
            "status": EventStatus.ONGOING,
            "published_at": TS,
        },
        evidence=[
            {
                "event_id": "unused",
                "source_item_id": graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "supporting_passage": "A source item supporting passage.",
                "created_at": TS,
            }
        ],
    )
    other_graph = _create_base_graph(service, suffix="-corrected")
    service.approve_candidate(other_graph["candidate_id"], reviewer="editor")
    corrected_event = service.create_event(
        {
            "candidate_id": other_graph["candidate_id"],
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Example story-corrected",
            "summary": "Needs correction.",
            "status": EventStatus.ONGOING,
            "published_at": TS,
        },
        evidence=[
            {
                "event_id": "unused",
                "source_item_id": other_graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "supporting_passage": "A source item supporting passage.",
                "created_at": TS,
            }
        ],
    )
    service.correct_event(
        corrected_event.event_id,
        updates={"summary": "Corrected summary."},
        note="Correction for export test.",
        reviewer="editor",
        source_item_id=other_graph["source_item_id"],
    )
    export_default = json.loads(service.export_verified_events_to_json())
    assert export_default["contract"]["ordering"] == "published_at_asc_event_id_asc"
    assert [row["event_id"] for row in export_default["events"]] == [verified_event.event_id]
    export_expanded = json.loads(
        service.export_verified_events_to_json(include_statuses=(VerificationStatus.VERIFIED, VerificationStatus.CORRECTED))
    )
    exported_statuses = {row["verification_status"] for row in export_expanded["events"]}
    assert "verified" in exported_statuses
    assert "corrected" in exported_statuses
    assert service.export_verified_events_to_json() == service.export_verified_events_to_json()


def test_alembic_upgrade_downgrade_reupgrade_round_trip(tmp_path: Path):
    database_path = tmp_path / "migration.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "sources",
        "source_items",
        "candidate_events",
        "reviews",
        "events",
        "event_evidence",
        "event_relationships",
        "event_attributes",
    }.issubset(tables)
    evidence_columns = {column["name"]: column for column in inspector.get_columns("event_evidence")}
    assert evidence_columns["evidence_strength"]["nullable"] is False
    assert evidence_columns["is_primary_source"]["nullable"] is False
    relationship_checks = inspector.get_check_constraints("event_relationships")
    assert any("from_event_id <> to_event_id" in row["sqltext"] for row in relationship_checks)
    with engine.connect() as connection:
        trigger_names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_%'")
            )
        }
    assert "trg_events_verified_requires_evidence_insert" in trigger_names

    command.downgrade(cfg, "base")
    engine.dispose()
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    inspector = inspect(engine)
    assert "events" not in set(inspector.get_table_names())

    command.upgrade(cfg, "head")
    engine.dispose()
    engine = create_engine(f"sqlite:///{database_path.as_posix()}", future=True)
    inspector = inspect(engine)
    assert "events" in set(inspector.get_table_names())
    engine.dispose()


def test_repository_enforces_sqlite_foreign_keys(tmp_path: Path):
    repo = SQLiteUniversalEventRepository(tmp_path / "fk.sqlite3")
    repo.initialize_schema()
    with repo.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_direct_orm_verified_event_without_evidence_is_rejected(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")

    with pytest.raises(IntegrityError, match="verified events require evidence"):
        with service.repository.session_scope() as session:
            session.add(
                EventRow(
                    event_id="event-without-evidence",
                    candidate_id=graph["candidate_id"],
                    domain=EventDomain.FOOD_SECURITY,
                    title="Direct bad event",
                    summary="This bypass attempt should fail.",
                    status=EventStatus.ACTIVE,
                    verification_status=VerificationStatus.VERIFIED,
                    published_at=TS,
                )
            )


def test_event_creation_rolls_back_when_evidence_insert_fails(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")

    with pytest.raises(ValueError, match="source item not found"):
        service.create_event(
            {
                "candidate_id": graph["candidate_id"],
                "domain": EventDomain.FOOD_SECURITY,
                "title": "Rollback event",
                "summary": "Should not persist.",
                "status": EventStatus.ACTIVE,
                "published_at": TS,
            },
            evidence=[
                {
                    "source_item_id": "missing-source-item",
                    "role": EvidenceRole.PRIMARY,
                    "supporting_passage": "Missing.",
                    "created_at": TS,
                }
            ],
        )

    assert service.repository.list_events(verification_status=None) == []
    candidate = service.repository.get_candidate(graph["candidate_id"])
    assert candidate is not None
    assert candidate.verified_event_id is None


def test_approved_status_without_review_cannot_create_event(service: UniversalEventService):
    graph = _create_base_graph(service)
    with service.repository.session_scope() as session:
        candidate = session.get(CandidateEventRow, graph["candidate_id"])
        candidate.candidate_status = CandidateStatus.APPROVED

    with pytest.raises(ValueError, match="approval review"):
        service.create_event(
            {
                "candidate_id": graph["candidate_id"],
                "domain": EventDomain.FOOD_SECURITY,
                "title": "No review event",
                "summary": "Should not persist.",
                "status": EventStatus.ACTIVE,
                "published_at": TS,
            },
            evidence=[
                {
                    "source_item_id": graph["source_item_id"],
                    "role": EvidenceRole.PRIMARY,
                    "supporting_passage": "Valid evidence.",
                    "created_at": TS,
                }
            ],
        )


def test_review_rows_are_append_only_under_supported_repository(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    with service.repository.session_scope() as session:
        review_id = session.execute(select(ReviewRow.review_id)).scalar_one()

    with pytest.raises(IntegrityError, match="reviews are append-only"):
        with service.repository.session_scope() as session:
            review = session.get(ReviewRow, review_id)
            review.notes = "Mutated note."

    with pytest.raises(IntegrityError, match="reviews are append-only"):
        with service.repository.session_scope() as session:
            review = session.get(ReviewRow, review_id)
            session.delete(review)


def test_duplicate_candidates_require_valid_retained_target(service: UniversalEventService):
    graph = _create_base_graph(service)
    with pytest.raises(IntegrityError, match="duplicate candidates require"):
        service.submit_candidate(
            {
                "candidate_id": "bad-duplicate",
                "source_item_id": graph["source_item_id"],
                "domain": EventDomain.FOOD_SECURITY,
                "title": "Bad duplicate",
                "candidate_status": CandidateStatus.DUPLICATE,
                "discovered_at": TS,
            }
        )

    with pytest.raises(ValueError, match="retained candidate not found"):
        service.merge_candidate(graph["candidate_id"], retained_candidate_id="missing-candidate", reviewer="editor")


def test_self_relationships_are_rejected(service: UniversalEventService):
    graph = _create_base_graph(service)
    service.approve_candidate(graph["candidate_id"], reviewer="editor")
    event = service.create_event(
        {
            "candidate_id": graph["candidate_id"],
            "domain": EventDomain.FOOD_SECURITY,
            "title": "Self relationship event",
            "published_at": TS,
        },
        evidence=[
            {
                "source_item_id": graph["source_item_id"],
                "role": EvidenceRole.PRIMARY,
                "supporting_passage": "Valid evidence.",
                "created_at": TS,
            }
        ],
    )

    with pytest.raises(ValueError, match="same event"):
        service.add_event_relationship(
            {
                "from_event_id": event.event_id,
                "to_event_id": event.event_id,
                "relationship_type": "related_to",
                "created_at": TS,
            }
        )
    with pytest.raises(IntegrityError):
        with service.repository.session_scope() as session:
            session.add(
                EventRelationshipRow(
                    relationship_id="bad-self-relationship",
                    from_event_id=event.event_id,
                    to_event_id=event.event_id,
                    relationship_type="related_to",
                    created_at=TS,
                    metadata_json={},
                )
            )


def test_seed_bundle_contains_all_required_domains():
    bundle = load_seed_bundle()
    candidate_domains = {row["domain"] for row in bundle["candidates"]}
    assert candidate_domains == {
        "food_security",
        "healthcare_access",
        "workforce",
        "immigration_enforcement",
        "conflict_humanitarian",
    }


def test_seed_database_populates_five_domain_events(tmp_path: Path):
    repo = SQLiteUniversalEventRepository(tmp_path / "seed.sqlite3")
    repo.initialize_schema()
    service = UniversalEventService(repo)
    counts = seed_database(service)
    assert counts["events"] == 5
    verified = service.query_events()
    assert {event.domain.value for event in verified} == {
        "food_security",
        "healthcare_access",
        "workforce",
        "immigration_enforcement",
        "conflict_humanitarian",
    }
