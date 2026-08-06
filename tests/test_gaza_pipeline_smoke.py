import json
import shutil
import uuid
from pathlib import Path

from scripts.run_gaza_dispatch import compute_gaza_source_adequacy, curate_stories, normalize_sources, render_gaza_edition


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

    adequacy = compute_gaza_source_adequacy(normalized, stories, raw_candidate_count=len(records))
    html = render_gaza_edition(edition_date, stories, normalized, adequacy)
    assert "Dispatches From Gaza" in html
    assert "Sources" in html
    assert 'href="https://www.unrwa.org/newsroom/example"' in html
