from __future__ import annotations

from pathlib import Path

from bluefern_dispatches.ice_dispatch import (
    CollectionHealth,
    EventRelationship,
    FetchResult,
    ProviderHealth,
    collect_live_candidates,
    compare_event_observation,
    evaluate_collection_health,
    event_public_eligibility,
    event_with_accumulated_sources,
    extract_publication_dates,
    load_source_registry,
    normalize_ice_candidate,
)


def source(**overrides):
    data = {
        "source_id": "ice-newsroom-detainee-death-notifications",
        "publisher": "U.S. Immigration and Customs Enforcement",
        "source_type": "official_agency_newsroom_query",
        "url": "https://www.ice.gov/newsroom?combine=detainee%20death",
        "enabled": True,
        "tier": 1,
        "geography": "US_and_territories",
        "categories": ["fatalities_injuries_medical_events", "detention"],
        "collection_mechanism": "html",
        "expected_cadence": "daily_monitor",
        "include_path_patterns": ["/news/releases/"],
        "exclude_path_patterns": ["/factsheets", "/detain/", "/identify-and-arrest", "/live"],
        "title_keywords": ["detainee", "passes away", "death", "custody"],
    }
    data.update(overrides)
    return data


def candidate(**overrides):
    data = {
        "event_date": "2026-09-08",
        "event_type": "ICE detainee passes away after medical emergency in New Jersey",
        "primary_category": "fatalities_injuries_medical_events",
        "status": "reported",
        "location": {
            "state_or_territory": "NJ",
            "location_precision": "state_or_territory",
            "geography_source": "source_text",
            "geography_provenance": "New Jersey",
        },
        "impact": {"fatalities_count": 1},
        "agencies": {"ice": True},
        "sources": [{
            "source_url": "https://www.ice.gov/news/releases/detainee-passes-following-medical-emergency-new-jersey",
            "publisher": "U.S. Immigration and Customs Enforcement",
            "source_type": "official_agency_newsroom_query",
            "tier": 1,
            "published_at": "2026-09-08T12:00:00Z",
            "exact_supporting_passage": "ICE said a detainee passed away after a medical emergency in New Jersey.",
        }],
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


def test_phase7_registry_adds_bounded_automated_sources():
    sources = load_source_registry(Path("data/dispatches/ice/sources.yml"))
    by_id = {item["source_id"]: item for item in sources}
    added = [
        "ice-newsroom-detainee-death-notifications",
        "ice-newsroom-worksite-enforcement",
        "ice-newsroom-detention-capacity",
        "ice-newsroom-287g",
        "ice-newsroom-removal-operations",
        "ice-newsroom-use-of-force",
    ]

    assert all(source_id in by_id for source_id in added)
    assert len([item for item in sources if item["enabled"] and item["collection_mechanism"] != "documented_manual"]) <= 15
    for source_id in added:
        row = by_id[source_id]
        assert row["tier"] == 1
        assert row["collection_mechanism"] == "html"
        assert row["include_path_patterns"] == ["/news/releases/"]
        assert row["title_keywords"]


def test_source_path_and_title_filters_remove_shared_navigation_before_fetch(monkeypatch):
    fetched = []

    def fake_fetch(url: str, *, timeout: float = 15.0):
        fetched.append(url)
        if "newsroom" in url:
            return FetchResult(url, True, 200, """
              <a href="/factsheets">Learn More About ICE</a>
              <a href="/detain/detention-management">Detention Management</a>
              <a href="/news/releases/detainee-passes-following-medical-emergency-new-jersey">Detainee passes following medical emergency in New Jersey</a>
            """, None, "test")
        return FetchResult(url, True, 200, '<meta property="article:published_time" content="2026-09-08T12:00:00Z"><p>ICE said the detainee passed away after a medical emergency in New Jersey.</p>', None, "test")

    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, _ = collect_live_candidates([source()], max_per_source=5, window_hours=72)

    assert len(raw) == 1
    assert "factsheets" not in " ".join(fetched)
    assert "detention-management" not in " ".join(fetched)
    assert exclusions == []
    assert health[0].accepted_records == 1


def test_targeted_source_preserves_date_evidence_and_currentness(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if "newsroom" in url:
            return FetchResult(url, True, 200, '<a href="/news/releases/criminal-illegal-alien-china-passes-away-saipan-ice-facility">Criminal illegal alien from China passes away at Saipan ICE facility</a>', None, "test")
        return FetchResult(url, True, 200, '<meta property="article:published_time" content="2026-09-08T12:00:00Z"><p>ICE said a detainee passed away at the Saipan ICE facility in the Northern Mariana Islands.</p>', None, "test")

    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, _, _, _ = collect_live_candidates([source(title_keywords=["passes away"])], max_per_source=1, window_hours=72)

    assert raw[0]["sources"][0]["date_source"] == "article_structured_metadata"
    assert raw[0]["currentness_status"] == "current_publication"
    assert raw[0]["location"]["state_or_territory"] == "MP"


def test_false_positive_static_cbp_and_generic_politics_are_controlled(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if "newsroom" in url:
            return FetchResult(url, True, 200, """
              <a href="/news/releases/cbp-only-border-arrests">CBP arrests 5 people at border checkpoint</a>
              <a href="/news/releases/ice-policy-debate">ICE policy debate draws political reactions</a>
            """, None, "test")
        return FetchResult(url, True, 200, "<p>ICE is discussed in a generic immigration politics article about campaign rhetoric and public opinion.</p>", None, "test")

    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, fetches = collect_live_candidates([source(title_keywords=["policy", "cbp"])], max_per_source=5, window_hours=72)

    assert raw == []
    assert [item["reason"] for item in exclusions] == ["generic_politics_without_concrete_ice_action"]
    assert health[0].accepted_records == 0
    assert len(fetches) == 2


def test_static_immigration_impact_explainer_is_excluded_before_fetch(monkeypatch):
    fetched = []

    def fake_fetch(url: str, *, timeout: float = 15.0):
        fetched.append(url)
        if "immigrationimpact" in url:
            return FetchResult(url, True, 200, """
              <a href="/about-immigration/mass-deportation/">Mass Deportation</a>
            """, None, "test")
        return FetchResult(url, True, 200, "<p>Mass deportation explainer.</p>", None, "test")

    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, _ = collect_live_candidates([
        source(
            source_id="immigration-impact",
            publisher="Immigration Impact",
            url="https://immigrationimpact.com/",
            tier=3,
            source_type="advocacy_legal_reporting",
            categories=["legal_oversight_accountability", "policy_operations"],
            include_path_patterns=[],
            exclude_path_patterns=[],
            title_keywords=[],
        )
    ], max_per_source=5, window_hours=168)

    assert raw == []
    assert [item["reason"] for item in exclusions] == ["non_article_or_static_link"]
    assert health[0].accepted_records == 0
    assert fetched == ["https://immigrationimpact.com/"]


def test_visible_dhs_release_date_after_large_template_is_extracted():
    html = "<nav>" + ("navigation " * 3_000) + (
        '</nav><span><strong>Release Date: </strong></span> '
        '<span class="news-release-date-value">September 9, 2026</span>'
    )

    dates = extract_publication_dates(html)

    assert dates["published_at"] == "2026-09-09T00:00:00Z"
    assert dates["modified_at"] is None


def test_stale_content_from_new_source_is_rejected(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if "newsroom" in url:
            return FetchResult(url, True, 200, '<a href="/news/releases/detainee-passes-following-medical-emergency-new-jersey">Detainee passes following medical emergency in New Jersey</a>', None, "test")
        return FetchResult(url, True, 200, '<meta property="article:published_time" content="2026-08-01T12:00:00Z"><p>ICE said the detainee passed away after a medical emergency.</p>', None, "test")

    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, _, _ = collect_live_candidates([source()], max_per_source=1, window_hours=72)

    assert raw == []
    assert exclusions[0]["reason"] == "stale_resurfaced_article"
    assert exclusions[0]["date_confidence"] == "structured_metadata"


def test_source_health_remains_meaningful_with_expanded_registry():
    health = [
        ProviderHealth(source_id="ice-newsroom", publisher="ICE", tier=1, attempted=True, success=True),
        ProviderHealth(source_id="ice-newsroom-detainee-death-notifications", publisher="ICE", tier=1, attempted=True, success=True),
        ProviderHealth(source_id="ice-newsroom-worksite-enforcement", publisher="ICE", tier=1, attempted=True, success=True),
        ProviderHealth(source_id="ice-newsroom-use-of-force", publisher="ICE", tier=1, attempted=True, success=False, error="timeout"),
    ]
    assert evaluate_collection_health(health) == CollectionHealth.LIMITED_SOURCE_UPDATE
    assert evaluate_collection_health(health, shared_service_failures=["ice.gov"]) == CollectionHealth.COLLECTION_DEGRADED


def test_dedupe_across_targeted_ice_source_families_preserves_sources():
    first = normalize_ice_candidate(candidate())
    second = normalize_ice_candidate(candidate(
        sources=[{
            "source_url": "https://www.ice.gov/news/releases/criminal-illegal-alien-china-passes-away-saipan-ice-facility",
            "publisher": "U.S. Immigration and Customs Enforcement",
            "source_type": "official_agency_newsroom_query",
            "tier": 1,
            "published_at": "2026-09-08T13:00:00Z",
            "exact_supporting_passage": "ICE said the detainee passed away after a medical emergency in New Jersey.",
        }]
    ))

    relationship, _ = compare_event_observation(first, second)
    assert relationship in {EventRelationship.DUPLICATE_COVERAGE, EventRelationship.FOLLOW_UP_WITH_NEW_FACTS}
    accumulated = event_with_accumulated_sources(first, second)
    assert len(accumulated.sources) == 2


def test_tier3_signal_promotes_when_targeted_official_source_matches_same_event():
    tier3 = normalize_ice_candidate(candidate(
        sources=[{
            "source_url": "https://advocacy.example/ice-detainee-medical-death",
            "publisher": "Advocacy Source",
            "source_type": "advocacy_legal_reporting",
            "tier": 3,
            "published_at": "2026-09-08T11:00:00Z",
            "exact_supporting_passage": "The source reports an ICE detainee passed away after a medical emergency in New Jersey.",
        }]
    ))
    assert event_public_eligibility(
        primary_category=tier3.primary_category,
        verification_status=tier3.editorial.verification_status,
        sources=tier3.sources,
        corroboration_count=1,
        within_scope=True,
        duplicate=False,
    ) == (False, "tier3_requires_corroboration")

    official = normalize_ice_candidate(candidate())
    assert official.lineage.fingerprint == tier3.lineage.fingerprint
    accumulated = event_with_accumulated_sources(tier3, official)
    assert event_public_eligibility(
        primary_category=accumulated.primary_category,
        verification_status=accumulated.editorial.verification_status,
        sources=accumulated.sources,
        corroboration_count=len(accumulated.sources),
        within_scope=True,
        duplicate=False,
    ) == (True, None)


def test_removal_query_titles_classify_as_removals():
    event = normalize_ice_candidate(candidate(
        event_type="ICE removes Cuban intelligence agent who fraudulently obtained lawful permanent resident status",
        primary_category="removals_deportations",
        sources=[{
            "source_url": "https://www.ice.gov/news/releases/ice-removes-cuban-intelligence-agent",
            "publisher": "U.S. Immigration and Customs Enforcement",
            "source_type": "official_agency_newsroom_query",
            "tier": 1,
            "published_at": "2026-09-03T00:00:00Z",
            "exact_supporting_passage": "ICE removes Cuban intelligence agent who fraudulently obtained lawful permanent resident status.",
        }],
    ))

    assert event.primary_category == "removals_deportations"
