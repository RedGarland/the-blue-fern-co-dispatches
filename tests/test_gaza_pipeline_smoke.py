import json
import shutil
import uuid
from pathlib import Path

import scripts.run_gaza_dispatch as gaza_dispatch
from scripts.run_gaza_dispatch import normalize_sources, curate_stories, render_gaza_edition


def test_gaza_pipeline_smoke_normalize_rank_compose_render_validate_links():
    edition_date = "2026-05-14"
    now = "2026-05-14T12:00:00+00:00"
    records = [
        {
            "source_record_id": "smoke-001",
            "title": "UNRWA says aid convoy crossing access expands in Gaza hospitals",
            "url": "https://www.unrwa.org/newsroom/example",
            "publisher": "UNRWA",
            "published_at": "2026-05-14T08:00:00+00:00",
            "retrieved_at": now,
            "summary_or_snippet": "Humanitarian access and health infrastructure update in Gaza.",
            "source_type": "rss",
            "region_scope": "Gaza",
            "category_hint": "humanitarian",
            "reliability_tier": "official-humanitarian-source",
        }
    ]

    normalized, warnings, errors = normalize_sources(records, edition_date, now)
    assert not warnings
    assert not errors
    assert normalized[0]["candidate_score"] > 0

    stories, relevance_decisions, _top_story_candidates = curate_stories(normalized, edition_date, now)
    assert stories
    assert relevance_decisions == []
    assert stories[0]["score"] >= normalized[0]["candidate_score"]

    adequacy = {
        "status": "daily_briefing",
        "publisher_count": 1,
        "publishers": ["UNRWA"],
        "warnings": [],
    }

    html = render_gaza_edition(edition_date, stories, normalized, adequacy)
    assert "Dispatches From Gaza" in html
    assert "Sources" in html
    assert 'href="https://www.unrwa.org/newsroom/example"' in html


def test_gaza_coverage_rescue_is_thin_only_when_publisher_diversity_drops():
    healthy_records = [
        {"publisher": "Reuters", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "AP", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "BBC", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "Al Jazeera", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "Reuters", "category_hint": "aid_access", "source_type": "rss"},
        {"publisher": "AP", "category_hint": "aid_access", "source_type": "rss"},
        {"publisher": "BBC", "category_hint": "civilian_harm", "source_type": "rss"},
        {"publisher": "Al Jazeera", "category_hint": "civilian_harm", "source_type": "rss"},
    ]
    thin_records = [
        {"publisher": "Reuters", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "Reuters", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "AP", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "AP", "category_hint": "humanitarian", "source_type": "rss"},
        {"publisher": "Reuters", "category_hint": "aid_access", "source_type": "rss"},
        {"publisher": "BBC", "category_hint": "aid_access", "source_type": "rss"},
        {"publisher": "Reuters", "category_hint": "civilian_harm", "source_type": "rss"},
        {"publisher": "Reuters", "category_hint": "civilian_harm", "source_type": "rss"},
    ]

    healthy = gaza_dispatch._gaza_should_run_coverage_rescue(healthy_records)
    thin = gaza_dispatch._gaza_should_run_coverage_rescue(thin_records)

    assert healthy["thin_collection"] is False
    assert healthy["reasons"] == []
    assert thin["thin_collection"] is True
    assert "publisher_diversity_below_target" in thin["reasons"]


def test_gaza_coverage_rescue_merges_equivalent_event_and_prefers_accessible_source():
    base = {
        "source_record_id": "gaza-reuters-001",
        "title": "Zawayda and Nuseirat strike killed at least two civilians",
        "url": "https://reuters.example/gaza-strike",
        "publisher": "Reuters",
        "published_at": "2026-08-23T10:00:00Z",
        "summary_or_snippet": "Civilian casualties were reported after strikes in Zawayda and Nuseirat.",
        "category_hint": "civilian_harm",
        "reliability_tier": "reported-public-source",
        "source_type": "rss",
    }
    rescued = {
        "source_record_id": "gaza-ap-001",
        "title": "Zawayda and Nuseirat strike killed at least two civilians",
        "url": "https://ap.example/gaza-strike",
        "publisher": "AP",
        "published_at": "2026-08-23T10:05:00Z",
        "summary_or_snippet": "Civilian casualties were reported after strikes in Zawayda and Nuseirat.",
        "category_hint": "civilian_harm",
        "reliability_tier": "reported-public-source",
        "source_type": "rss",
    }

    merged, report = gaza_dispatch._gaza_merge_rescued_candidates([base], [rescued], edition_date="2026-08-23")

    assert len(merged) == 1
    assert merged[0]["publisher"] == "AP"
    assert merged[0]["source_record_ids"] == ["gaza-reuters-001", "gaza-ap-001"]
    assert merged[0]["source_urls"] == ["https://reuters.example/gaza-strike", "https://ap.example/gaza-strike"]
    assert report["rescued_candidate_count"] == 1
    assert report["rescued_duplicate_count"] == 1
    assert report["status"] == "coverage_rescue_completed"


def test_gaza_event_equivalence_key_keeps_distinct_same_day_events_separate():
    first = {
        "source_record_id": "gaza-event-001",
        "title": "Zawayda strike killed at least two civilians",
        "url": "https://example.org/a",
        "publisher": "AP",
        "published_at": "2026-08-23T09:00:00Z",
        "summary_or_snippet": "A strike killed two civilians in Zawayda.",
        "category_hint": "civilian_harm",
        "source_type": "rss",
    }
    second = {
        "source_record_id": "gaza-event-002",
        "title": "Kerem Shalom crossing reopened for aid trucks",
        "url": "https://example.org/b",
        "publisher": "BBC",
        "published_at": "2026-08-23T09:30:00Z",
        "summary_or_snippet": "Aid trucks resumed movement at the crossing.",
        "category_hint": "aid_access",
        "source_type": "rss",
    }

    merged, report = gaza_dispatch._gaza_merge_rescued_candidates([first], [second], edition_date="2026-08-23")

    assert len(merged) == 2
    assert report["rescued_candidate_count"] == 1
    assert report["event_group_count"] == 1
