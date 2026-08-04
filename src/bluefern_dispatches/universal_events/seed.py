from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .service import UniversalEventService


SEED_BUNDLE_PATH = Path("data") / "universal_events" / "seed" / "universal_events_seed.json"


def load_seed_bundle(path: Path | str = SEED_BUNDLE_PATH) -> dict[str, list[dict[str, Any]]]:
    seed_path = Path(path)
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("seed bundle must be a JSON object")
    bundle: dict[str, list[dict[str, Any]]] = {}
    for key, value in payload.items():
        if isinstance(value, list):
            bundle[key] = [item for item in value if isinstance(item, dict)]
    return bundle


def seed_database(service: UniversalEventService, path: Path | str = SEED_BUNDLE_PATH) -> dict[str, int]:
    bundle = load_seed_bundle(path)
    counts = {
        "sources": 0,
        "source_items": 0,
        "locations": 0,
        "organizations": 0,
        "organization_aliases": 0,
        "organization_identifiers": 0,
        "location_aliases": 0,
        "location_identifiers": 0,
        "entity_mentions": 0,
        "match_candidates": 0,
        "resolution_decisions": 0,
        "organization_relationships": 0,
        "organization_location_relationships": 0,
        "organization_merges": 0,
        "event_entity_links": 0,
        "candidates": 0,
        "reviews": 0,
        "events": 0,
        "evidence": 0,
        "relationships": 0,
        "attributes": 0,
    }
    for row in bundle.get("sources", []):
        service.create_source(row)
        counts["sources"] += 1
    for row in bundle.get("source_items", []):
        service.create_source_item(row)
        counts["source_items"] += 1
    for row in bundle.get("locations", []):
        service.create_location(row)
        counts["locations"] += 1
    for row in bundle.get("organizations", []):
        service.create_organization(row)
        counts["organizations"] += 1
    for row in bundle.get("organization_aliases", []):
        service.add_organization_alias(row)
        counts["organization_aliases"] += 1
    for row in bundle.get("organization_identifiers", []):
        service.add_organization_identifier(row)
        counts["organization_identifiers"] += 1
    for row in bundle.get("location_aliases", []):
        service.add_location_alias(row)
        counts["location_aliases"] += 1
    for row in bundle.get("location_identifiers", []):
        service.add_location_identifier(row)
        counts["location_identifiers"] += 1
    for row in bundle.get("candidates", []):
        service.submit_candidate(row)
        counts["candidates"] += 1
    for row in bundle.get("entity_mentions", []):
        service.ingest_entity_mention(row)
        counts["entity_mentions"] += 1
    for row in bundle.get("match_candidate_generation", []):
        counts["match_candidates"] += len(service.generate_match_candidates(str(row["mention_id"])))
    for row in bundle.get("resolution_decisions", []):
        service.resolve_mention(row)
        counts["resolution_decisions"] += 1
    for row in bundle.get("reviews", []):
        decision = str(row.get("decision") or "approved")
        if decision == "approved":
            service.approve_candidate(str(row["candidate_id"]), reviewer=str(row.get("reviewer") or "seed"), notes=str(row.get("notes") or ""), reviewed_at=row.get("reviewed_at"))
        elif decision == "rejected":
            service.reject_candidate(str(row["candidate_id"]), reviewer=str(row.get("reviewer") or "seed"), notes=str(row.get("notes") or ""), reviewed_at=row.get("reviewed_at"))
        else:
            service.merge_candidate(
                str(row["candidate_id"]),
                retained_candidate_id=row.get("retained_candidate_id"),
                retained_event_id=row.get("retained_event_id"),
                reviewer=str(row.get("reviewer") or "seed"),
                notes=str(row.get("notes") or ""),
                reviewed_at=row.get("reviewed_at"),
            )
        counts["reviews"] += 1
    for row in bundle.get("events", []):
        event_payload = dict(row)
        evidence = list(event_payload.pop("evidence", []))
        entity_links = list(event_payload.pop("entity_links", []))
        service.create_event(event_payload, evidence=evidence, entity_links=entity_links)
        counts["events"] += 1
    for row in bundle.get("evidence", []):
        service.attach_evidence(row)
        counts["evidence"] += 1
    for row in bundle.get("relationships", []):
        service.add_event_relationship(row)
        counts["relationships"] += 1
    for row in bundle.get("organization_relationships", []):
        service.add_organization_relationship(row)
        counts["organization_relationships"] += 1
    for row in bundle.get("organization_location_relationships", []):
        service.add_organization_location_relationship(row)
        counts["organization_location_relationships"] += 1
    for row in bundle.get("organization_merges", []):
        service.merge_organizations(row)
        counts["organization_merges"] += 1
    for row in bundle.get("event_entity_links", []):
        service.attach_event_entity_link(row)
        counts["event_entity_links"] += 1
    for row in bundle.get("attributes", []):
        service.add_event_attribute(row)
        counts["attributes"] += 1
    return counts
