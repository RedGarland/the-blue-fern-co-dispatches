from __future__ import annotations

import json
from pathlib import Path

import pytest

from bluefern_dispatches.ice_dispatch import (
    CollectionHealth,
    EventRelationship,
    IceCategory,
    LocationPrecision,
    PublicationDecision,
    Severity,
    VerificationStatus,
    build_collection_report,
    compare_event_observation,
    edition_publication_decision,
    event_public_eligibility,
    event_to_dict,
    load_source_registry,
    map_ready_event,
    normalize_ice_candidate,
    run_diagnostic,
    stable_event_fingerprint,
    validate_endpoint,
)


def candidate(**overrides):
    base = {
        "event_date": "2026-09-08",
        "event_type": "workplace enforcement operation",
        "primary_category": "enforcement",
        "secondary_categories": ["verified_community_impact"],
        "status": "reported",
        "location": {
            "state_or_territory": "CA",
            "county_or_equivalent": "Los Angeles County",
            "city": "Los Angeles",
            "facility_name": "Example Produce Warehouse",
            "location_precision": "city",
        },
        "impact": {"arrests_count": None, "detained_count": None, "removed_count": None},
        "agencies": {"ice": True, "dhs": True, "state_local_agencies": ["Los Angeles Police Department"]},
        "sources": [
            {
                "source_url": "https://www.ice.gov/news/releases/example-operation?utm_source=test",
                "publisher": "U.S. Immigration and Customs Enforcement",
                "source_type": "official_agency_newsroom",
                "tier": 1,
                "published_at": "2026-09-08",
                "exact_supporting_passage": "ICE announced an enforcement operation at a Los Angeles warehouse.",
            }
        ],
        "verification_status": "source_traceable",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        else:
            base[key] = value
    return base


def test_normalized_ice_event_schema_and_traceability():
    event = normalize_ice_candidate(candidate(), observed_at="2026-09-08T12:00:00Z")
    payload = event_to_dict(event)
    assert payload["dispatch"] == "ice"
    assert payload["schema_version"] == 1
    assert payload["primary_category"] == "enforcement"
    assert payload["sources"][0]["canonical_source_url"] == "https://ice.gov/news/releases/example-operation"
    assert payload["sources"][0]["exact_supporting_passage"].startswith("ICE announced")
    assert event.editorial.public_eligibility is True


def test_unknown_counts_remain_null_not_zero():
    event = normalize_ice_candidate(candidate(), observed_at="2026-09-08T12:00:00Z")
    payload = event_to_dict(event)
    assert payload["impact"]["arrests_count"] is None
    assert payload["impact"]["detained_count"] is None
    assert payload["impact"]["fatalities_count"] is None


def test_geography_precision_and_coordinate_provenance():
    event = normalize_ice_candidate(
        candidate(location={"location_precision": "exact_facility", "latitude": 34.05, "longitude": -118.24, "geography_source": "facility registry", "geography_provenance": "phase1 fixture"}),
        observed_at="2026-09-08T12:00:00Z",
    )
    assert event.location.location_precision == LocationPrecision.EXACT_FACILITY
    mapped = map_ready_event(event)
    assert mapped["latitude"] == 34.05
    assert mapped["geography_source"] == "facility registry"
    with pytest.raises(ValueError, match="Coordinates require geography_source"):
        normalize_ice_candidate(candidate(location={"latitude": 34.05, "longitude": -118.24}))


def test_severity_classification():
    fatal = normalize_ice_candidate(candidate(primary_category="fatalities_injuries_medical_events", event_type="death in custody", impact={"fatalities_count": 1}))
    assert fatal.severity == Severity.CRITICAL
    large = normalize_ice_candidate(candidate(impact={"arrests_count": 42}))
    assert large.severity == Severity.HIGH
    legal = normalize_ice_candidate(candidate(primary_category="legal_oversight_accountability", event_type="court order changes detention transfer policy"))
    assert legal.severity == Severity.MEDIUM


def test_public_eligibility_requires_traceable_support_and_tier3_corroboration():
    event = normalize_ice_candidate(candidate())
    ok, reason = event_public_eligibility(
        primary_category=event.primary_category,
        verification_status=VerificationStatus.SOURCE_TRACEABLE,
        sources=event.sources,
        corroboration_count=1,
        within_scope=True,
        duplicate=False,
    )
    assert ok is True
    tier3 = normalize_ice_candidate(candidate(sources=[{**candidate()["sources"][0], "tier": 3, "publisher": "Community Legal Group"}]))
    ok, reason = event_public_eligibility(
        primary_category=tier3.primary_category,
        verification_status=VerificationStatus.SOURCE_TRACEABLE,
        sources=tier3.sources,
        corroboration_count=1,
        within_scope=True,
        duplicate=False,
    )
    assert ok is False
    assert reason == "tier3_requires_corroboration"


def test_duplicate_event_identification_and_update_distinction():
    existing = normalize_ice_candidate(candidate(impact={"arrests_count": 5}), observed_at="2026-09-08T12:00:00Z")
    repeated = normalize_ice_candidate(candidate(impact={"arrests_count": 5}), observed_at="2026-09-08T13:00:00Z")
    relation, reasons = compare_event_observation(existing, repeated)
    assert relation == EventRelationship.DUPLICATE_COVERAGE
    updated = normalize_ice_candidate(candidate(impact={"arrests_count": 12}), observed_at="2026-09-08T14:00:00Z")
    relation, reasons = compare_event_observation(existing, updated)
    assert relation == EventRelationship.UPDATE_TO_EXISTING_EVENT
    assert "arrests_count changed" in reasons


def test_followup_with_new_source_preserves_same_event():
    existing = normalize_ice_candidate(candidate(impact={"arrests_count": 5}))
    second = candidate(impact={"arrests_count": 5})
    second["sources"] = [
        {
            "source_url": "https://apnews.com/article/example-ice-operation",
            "publisher": "Associated Press",
            "source_type": "wire_topic_page",
            "tier": 2,
            "published_at": "2026-09-08",
            "exact_supporting_passage": "AP reported the same Los Angeles enforcement operation.",
        }
    ]
    followup = normalize_ice_candidate(second)
    assert existing.lineage.fingerprint == followup.lineage.fingerprint
    relation, reasons = compare_event_observation(existing, followup)
    assert relation == EventRelationship.FOLLOW_UP_WITH_NEW_FACTS


def test_correction_lineage():
    existing = normalize_ice_candidate(candidate(impact={"arrests_count": 5}))
    corrected = normalize_ice_candidate(candidate(impact={"arrests_count": 4}, supersedes=[existing.event_id]))
    relation, reasons = compare_event_observation(existing, corrected)
    assert relation == EventRelationship.CORRECTION
    assert corrected.lineage.supersedes == (existing.event_id,)


def test_no_publication_needed_and_degraded_are_distinct():
    low = normalize_ice_candidate(candidate(severity="low"))
    decision, reason = edition_publication_decision([low], CollectionHealth.HEALTHY)
    assert decision == PublicationDecision.NO_PUBLICATION_NEEDED
    decision, reason = edition_publication_decision([low], CollectionHealth.COLLECTION_DEGRADED)
    assert decision == PublicationDecision.COLLECTION_DEGRADED


def test_edition_eligibility_for_high_or_medium_group():
    high = normalize_ice_candidate(candidate(impact={"arrests_count": 30}))
    decision, _ = edition_publication_decision([high], CollectionHealth.HEALTHY)
    assert decision == PublicationDecision.ELIGIBLE_FOR_EDITORIAL_REVIEW
    mediums = [normalize_ice_candidate(candidate(event_type=f"facility contract change {idx}", location={"city": f"City {idx}"})) for idx in range(3)]
    decision, _ = edition_publication_decision(mediums, CollectionHealth.HEALTHY)
    assert decision == PublicationDecision.ELIGIBLE_FOR_EDITORIAL_REVIEW


def test_provider_failure_isolation_and_shared_failure_aggregation():
    event = normalize_ice_candidate(candidate())
    providers = [
        {"source_id": "ice", "publisher": "ICE", "tier": 1, "attempted": True, "success": True, "accepted_records": 1},
        {"source_id": "ap", "publisher": "AP", "tier": 2, "attempted": True, "success": False, "error": "503"},
    ]
    from bluefern_dispatches.ice_dispatch import ProviderHealth
    health = [ProviderHealth(**p) for p in providers]
    report = build_collection_report("run", health, [event], started_at="2026-09-08T12:00:00Z", completed_at="2026-09-08T12:01:00Z")
    assert report.health == CollectionHealth.LIMITED_SOURCE_UPDATE
    degraded = build_collection_report("run", health, [event], started_at="2026-09-08T12:00:00Z", completed_at="2026-09-08T12:01:00Z", shared_service_failures=["google_news_rss"])
    assert degraded.health == CollectionHealth.COLLECTION_DEGRADED


def test_map_ready_event_serialization_fields():
    event = normalize_ice_candidate(candidate(location={"location_precision": "county"}))
    mapped = map_ready_event(event)
    assert set(mapped) >= {"event_id", "dispatch", "event_date", "primary_category", "severity", "state_or_territory", "facility_name", "event_type", "latitude", "longitude", "location_precision", "geography_source"}
    assert mapped["location_precision"] == "county"


def test_source_registry_contract_loads():
    sources = load_source_registry(Path("data/dispatches/ice/sources.yml"))
    assert {s["source_id"] for s in sources} >= {"ice-newsroom", "dhs-news", "doj-news", "ap-immigration"}
    assert all("tier" in s and "collection_mechanism" in s for s in sources)


def test_diagnostic_writes_only_when_output_dir_is_explicit(tmp_path):
    fixture = tmp_path / "candidates.json"
    fixture.write_text(json.dumps([candidate()]), encoding="utf-8")
    persistent_test_runs = Path("output/test-runs")
    before = sorted(p.relative_to(persistent_test_runs) for p in persistent_test_runs.rglob("*") if persistent_test_runs.exists())
    result = run_diagnostic(Path("data/dispatches/ice/sources.yml"), fixture_path=fixture)
    assert result["public_side_effects"] is False
    assert result["normalized_events"]
    after_without_output = sorted(p.relative_to(persistent_test_runs) for p in persistent_test_runs.rglob("*") if persistent_test_runs.exists())
    assert after_without_output == before
    out = tmp_path / "diagnostic"
    run_diagnostic(Path("data/dispatches/ice/sources.yml"), fixture_path=fixture, output_dir=out)
    assert (out / "run_manifest.json").exists()
    after_with_tmp_output = sorted(p.relative_to(persistent_test_runs) for p in persistent_test_runs.rglob("*") if persistent_test_runs.exists())
    assert after_with_tmp_output == before


def test_endpoint_validation_result_shape(monkeypatch):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response())
    result = validate_endpoint("https://www.ice.gov/newsroom")
    assert result == {"url": "https://www.ice.gov/newsroom", "ok": True, "status": 200, "error": None}


def test_stable_fingerprint_changes_for_distinct_event():
    one = stable_event_fingerprint(candidate(location={"city": "Los Angeles"}))
    two = stable_event_fingerprint(candidate(location={"city": "San Diego"}))
    assert one != two
