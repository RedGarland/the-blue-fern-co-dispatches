from __future__ import annotations

from bluefern_dispatches.ice_dispatch import (
    EventRelationship,
    FetchResult,
    IceCategory,
    LocationPrecision,
    MapReadiness,
    collect_live_candidates,
    compare_event_observation,
    currentness_for_dates,
    run_diagnostic,
    event_public_eligibility,
    event_with_accumulated_sources,
    extract_publication_dates,
    map_readiness_for_location,
    map_ready_event,
    normalize_ice_candidate,
    stable_event_fingerprint,
)


def source(**overrides):
    data = {
        "source_id": "ap-immigration",
        "publisher": "Associated Press",
        "source_type": "wire_topic_page",
        "url": "https://apnews.com/hub/immigration",
        "enabled": True,
        "tier": 2,
        "geography": "US_and_territories",
        "categories": ["use_of_force", "legal_oversight_accountability"],
        "collection_mechanism": "html",
        "expected_cadence": "daily_monitor",
    }
    data.update(overrides)
    return data


def candidate(**overrides):
    data = {
        "event_date": "2026-09-08",
        "event_type": "ICE officer charged in Minnesota shooting released on bond",
        "primary_category": "use_of_force",
        "status": "reported",
        "location": {
            "state_or_territory": "MN",
            "location_precision": "state_or_territory",
            "geography_source": "source_text",
            "geography_provenance": "Minnesota",
        },
        "impact": {},
        "agencies": {"ice": True},
        "sources": [
            {
                "source_url": "https://apnews.com/article/ice-officer-justice-department-false-statements-charge-ec67b349c8c01e6289e3f1b89a2821e4",
                "publisher": "Associated Press",
                "source_type": "wire_topic_page",
                "tier": 2,
                "published_at": "2026-09-08T16:00:00Z",
                "exact_supporting_passage": "An ICE officer charged after a Minnesota shooting was released on bond.",
            }
        ],
        "verification_status": "source_traceable",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            merged = dict(data[key])
            merged.update(value)
            data[key] = merged
        else:
            data[key] = value
    return data


def test_ap_article_and_video_share_canonical_event_and_preserve_sources():
    article = normalize_ice_candidate(candidate())
    video_payload = candidate(
        event_type="Lawyer for ICE officer who allegedly lied about Minnesota shooting says public should not rush to judgment",
        sources=[
            {
                "source_url": "https://apnews.com/video/lawyer-for-ice-officer-who-allegedly-lied-about-minnesota-shooting-says-public-should-not-rush-to-judgment-bb28ab903b374ba994431b34681e6eb2",
                "publisher": "Associated Press",
                "source_type": "wire_topic_page",
                "tier": 2,
                "published_at": "2026-09-08T17:00:00Z",
                "exact_supporting_passage": "Lawyer for an ICE officer allegedly involved in a Minnesota shooting says public should not rush to judgment.",
            }
        ],
    )
    video = normalize_ice_candidate(video_payload)
    assert article.lineage.fingerprint == video.lineage.fingerprint
    relationship, reasons = compare_event_observation(article, video)
    assert relationship == EventRelationship.DUPLICATE_COVERAGE
    assert "new source attached to same canonical event" in reasons
    accumulated = event_with_accumulated_sources(article, video)
    assert len({source.canonical_source_url for source in accumulated.sources}) == 2


def test_same_city_date_different_operations_do_not_collapse():
    one = candidate(event_type="ICE arrests 12 at Minnesota worksite enforcement operation", impact={"arrests_count": 12})
    two = candidate(event_type="ICE detention facility medical failure in Minnesota", primary_category="fatalities_injuries_medical_events")
    assert stable_event_fingerprint(one) != stable_event_fingerprint(two)


def test_same_detention_facility_different_incidents_do_not_collapse():
    death = candidate(
        event_type="ICE detainee death at Dodge County detention facility",
        primary_category="fatalities_injuries_medical_events",
        location={"facility_name": "Dodge County Detention Facility"},
        impact={"fatalities_count": 1},
    )
    contract = candidate(
        event_type="ICE detention contract inspection at Dodge County detention facility",
        primary_category="detention",
        location={"facility_name": "Dodge County Detention Facility"},
    )
    assert stable_event_fingerprint(death) != stable_event_fingerprint(contract)


def test_lawsuit_later_ruling_is_update_not_blind_duplicate():
    filed = normalize_ice_candidate(candidate(event_type="ICE detention lawsuit filed in Minnesota", primary_category="legal_oversight_accountability", status="filed"))
    ruling = normalize_ice_candidate(candidate(event_type="Judge issues ruling in ICE detention lawsuit in Minnesota", primary_category="legal_oversight_accountability", status="ruling"))
    relationship, reasons = compare_event_observation(filed, ruling)
    assert relationship == EventRelationship.UPDATE_TO_EXISTING_EVENT
    assert "status changed" in reasons


def test_revised_count_is_update_and_correction_lineage_is_correction():
    first = normalize_ice_candidate(candidate(event_type="ICE arrests in Minnesota operation", impact={"arrests_count": 12}))
    revised = normalize_ice_candidate(candidate(event_type="ICE arrests in Minnesota operation", impact={"arrests_count": 14}))
    relationship, reasons = compare_event_observation(first, revised)
    assert relationship == EventRelationship.UPDATE_TO_EXISTING_EVENT
    assert "arrests_count changed" in reasons
    corrected = normalize_ice_candidate(candidate(event_type="ICE arrests in Minnesota operation", impact={"arrests_count": 11}, supersedes=[first.event_id]))
    relationship, reasons = compare_event_observation(first, corrected)
    assert relationship == EventRelationship.CORRECTION


def test_publication_date_extraction_variants():
    html = """
    <script type="application/ld+json">{"datePublished":"2026-09-08T09:30:00-05:00","dateModified":"2026-09-08T11:00:00-05:00"}</script>
    <meta property="article:published_time" content="2026-09-07T12:00:00Z">
    """
    dates = extract_publication_dates(html)
    assert dates["published_at"] == "2026-09-07T12:00:00Z"
    assert dates["modified_at"] == "2026-09-08T16:00:00Z"
    assert extract_publication_dates("<html>No dates</html>") == {"published_at": None, "modified_at": None}
    assert extract_publication_dates('<meta property="article:published_time" content="not-a-date">') == {"published_at": None, "modified_at": None}


def test_current_window_distinguishes_stale_resurface_and_old_event_update():
    retrieved = "2026-09-09T00:00:00Z"
    assert currentness_for_dates(published_at="2026-09-08T00:00:00Z", modified_at=None, event_date=None, retrieved_at=retrieved, window_hours=72)["status"] == "current_publication"
    assert currentness_for_dates(published_at="2026-08-01T00:00:00Z", modified_at=None, event_date=None, retrieved_at=retrieved, window_hours=72)["status"] == "stale_resurfaced_article"
    assert currentness_for_dates(published_at="2026-08-01T00:00:00Z", modified_at="2026-09-08T00:00:00Z", event_date="2026-08-01", retrieved_at=retrieved, window_hours=72)["status"] == "current_update_to_older_event"
    assert currentness_for_dates(published_at=None, modified_at=None, event_date=None, retrieved_at=retrieved, window_hours=72) == {"status": "unresolved_date", "confidence": "undated_current_surface"}


def test_live_collection_excludes_stale_resurfaced_articles(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if url.endswith("/hub/immigration"):
            return FetchResult(url, True, 200, '<a href="/article/old-ice-shooting">ICE officer charged in Minnesota shooting</a>', None, "test")
        return FetchResult(url, True, 200, '<meta property="article:published_time" content="2026-08-01T00:00:00Z"><p>An ICE officer was charged in a Minnesota shooting.</p>', None, "test")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, _, _ = collect_live_candidates([source()], max_per_source=1, window_hours=72)
    assert raw == []
    assert exclusions[0]["reason"] == "stale_resurfaced_article"


def test_geography_provenance_territories_and_map_readiness():
    territory_codes = {
        "Puerto Rico": "PR",
        "Guam": "GU",
        "U.S. Virgin Islands": "VI",
        "Northern Mariana Islands": "MP",
        "American Samoa": "AS",
        "District of Columbia": "DC",
    }
    for place, code in territory_codes.items():
        event = normalize_ice_candidate(candidate(event_type=f"ICE detention notice in {place}", location={"state_or_territory": code, "location_precision": "state_or_territory", "geography_source": "source_text"}))
        assert event.location.state_or_territory == code
        assert map_readiness_for_location(event.location) == MapReadiness.MAPPABLE_STATE
        assert map_ready_event(event)["latitude"] is None
        assert map_ready_event(event)["longitude"] is None
    city = normalize_ice_candidate(candidate(location={"state_or_territory": "MN", "city": "Minneapolis", "location_precision": "city", "geography_source": "source_text"}))
    assert map_readiness_for_location(city.location) == MapReadiness.MAPPABLE_CITY
    exact_without_coordinates = normalize_ice_candidate(candidate(location={"state_or_territory": None, "facility_name": "Dodge County Detention Facility", "location_precision": "exact_facility", "geography_source": "source_text"}))
    assert map_readiness_for_location(exact_without_coordinates.location) == MapReadiness.NOT_YET_MAPPABLE


def test_tier3_corroboree_promotes_same_canonical_event_without_duplicate():
    tier3 = normalize_ice_candidate(candidate(
        event_type="6 Deaths in ICE Custody and 2 Fatal Shootings: A Horrific Start to 2026",
        primary_category="fatalities_injuries_medical_events",
        impact={"fatalities_count": 6},
        sources=[{
            "source_url": "https://www.americanimmigrationcouncil.org/blog/ice-deaths-shootings-2026/",
            "publisher": "Immigration Impact / American Immigration Council",
            "source_type": "advocacy_legal_reporting",
            "tier": 3,
            "exact_supporting_passage": "The source reports 6 deaths in ICE custody and 2 fatal shootings.",
        }],
    ))
    ok, reason = event_public_eligibility(
        primary_category=tier3.primary_category,
        verification_status=tier3.editorial.verification_status,
        sources=tier3.sources,
        corroboration_count=tier3.editorial.corroboration_count,
        within_scope=True,
        duplicate=False,
    )
    assert (ok, reason) == (False, "tier3_requires_corroboration")
    tier1 = normalize_ice_candidate(candidate(
        event_type="ICE custody deaths reported in 2026",
        primary_category="fatalities_injuries_medical_events",
        impact={"fatalities_count": 6},
        sources=[{
            "source_url": "https://www.ice.gov/news/releases/custody-deaths-2026",
            "publisher": "U.S. Immigration and Customs Enforcement",
            "source_type": "official_agency_newsroom",
            "tier": 1,
            "exact_supporting_passage": "ICE reported deaths in ICE custody in 2026.",
        }],
    ))
    assert tier3.lineage.fingerprint == tier1.lineage.fingerprint
    relationship, _ = compare_event_observation(tier3, tier1)
    assert relationship in {EventRelationship.DUPLICATE_COVERAGE, EventRelationship.UPDATE_TO_EXISTING_EVENT, EventRelationship.FOLLOW_UP_WITH_NEW_FACTS}
    accumulated = event_with_accumulated_sources(tier3, tier1)
    ok, reason = event_public_eligibility(
        primary_category=accumulated.primary_category,
        verification_status=accumulated.editorial.verification_status,
        sources=accumulated.sources,
        corroboration_count=len(accumulated.sources),
        within_scope=True,
        duplicate=False,
    )
    assert (ok, reason) == (True, None)


def test_diagnostic_reports_canonical_events_and_relationships(tmp_path):
    fixture = tmp_path / "candidates.json"
    fixture.write_text(
        __import__("json").dumps([
            candidate(),
            candidate(
                event_type="Lawyer for ICE officer who allegedly lied about Minnesota shooting says public should not rush to judgment",
                sources=[{
                    "source_url": "https://apnews.com/video/lawyer-for-ice-officer-who-allegedly-lied-about-minnesota-shooting-says-public-should-not-rush-to-judgment-bb28ab903b374ba994431b34681e6eb2",
                    "publisher": "Associated Press",
                    "source_type": "wire_topic_page",
                    "tier": 2,
                    "published_at": "2026-09-08T17:00:00Z",
                    "exact_supporting_passage": "Lawyer for an ICE officer allegedly involved in a Minnesota shooting says public should not rush to judgment.",
                }],
            ),
        ]),
        encoding="utf-8",
    )
    output_dir = tmp_path / "diagnostic"
    result = run_diagnostic(__import__("pathlib").Path("data/dispatches/ice/sources.yml"), fixture_path=fixture, output_dir=output_dir)
    assert len(result["normalized_events"]) == 2
    assert len(result["canonical_events"]) == 1
    assert result["event_relationships"][1]["relationship"] == "duplicate_coverage"
    assert len(result["canonical_events"][0]["sources"]) == 2
    assert result["map_readiness"][0]["map_readiness"] == "MAPPABLE_STATE"
    assert (output_dir / "canonical_events.json").exists()
    assert (output_dir / "event_relationships.json").exists()
    assert (output_dir / "map_readiness.json").exists()
