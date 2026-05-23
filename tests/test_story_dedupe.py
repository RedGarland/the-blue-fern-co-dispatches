import json
import shutil
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.story_dedupe import dedupe_public_stories
from bluefern_dispatches.cascadia_render import render_cascadia_edition
from scripts.run_gaza_dispatch import run_gaza_dispatch


def make_root() -> Path:
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    shutil.copytree(repo / "assets", root / "assets")
    (root / "data" / "records").mkdir(parents=True)
    return root


@pytest.fixture()
def cascadia_work_root():
    root = make_root()
    (root / "data" / "dispatches" / "cascadia").mkdir(parents=True, exist_ok=True)
    return root


def story(
    story_id,
    title,
    url,
    summary="A source-backed summary.",
    category="humanitarian",
    publisher="Example News",
    source_type="news",
    region_scope="Gaza",
    state_hint=None,
    published_at="2026-05-08T00:00:00Z",
    reliability_tier="reported-public-source",
):
    return {
        "story_id": story_id,
        "title": title,
        "summary": summary,
        "category": category,
        "score": 80,
        "scoring_reasons": ["test source record"],
        "included_in_public_summary": True,
        "included_in_detail_dataset": False,
        "source_record_ids": [f"src-{story_id}"],
        "source_ids": [f"src-{story_id}"],
        "source_urls": [url],
        "publisher_names": [publisher],
        "source_records": [
            {
                "source_record_id": f"src-{story_id}",
                "canonical_url": url,
                "source_url": url,
                "title": title,
                "publisher": publisher,
                "published_at": published_at,
                "retrieved_at": "2026-05-08T01:00:00Z",
                "category_hint": category,
                "source_type": source_type,
                "region_scope": region_scope,
                "state_hint": state_hint,
                "reliability_tier": reliability_tier,
            }
        ],
    }


def write_prior_memory(root: Path, prior_story: dict, edition_date: str = "2026-05-07", dispatch_slug: str = "gaza"):
    row = {
        "dispatch_slug": dispatch_slug,
        "edition_date": edition_date,
        "story_id": prior_story["story_id"],
        "title": prior_story["title"],
        "normalized_title": prior_story["title"].lower(),
        "summary": prior_story["summary"],
        "source_urls": prior_story["source_urls"],
        "canonical_urls": prior_story["source_urls"],
        "publisher_names": prior_story["publisher_names"],
        "category": prior_story["category"],
        "geographies": ["WA"] if dispatch_slug == "cascadia" else ["Gaza"],
        "source_dates": ["2026-05-03T00:00:00Z"],
        "topic_fingerprint": "prior",
        "first_seen_date": edition_date,
        "last_seen_date": edition_date,
        "update_count": 0,
    }
    path = root / "data" / "records" / "story_memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([row], indent=2), encoding="utf-8")


def test_exact_url_duplicate_skipped():
    root = make_root()
    prior = story("old", "Aid convoy enters Gaza", "https://example.com/story")
    write_prior_memory(root, prior)

    result = dedupe_public_stories(root, "gaza", "2026-05-08", [story("new", "Aid convoy enters Gaza", "https://example.com/story")])

    assert result.stories == []
    assert result.report["duplicate_skipped"][0]["classification"] == "duplicate_skip"
    assert result.report["duplicate_skipped"][0]["public_rendered"] is False


def test_normalized_url_duplicate_skipped():
    root = make_root()
    prior = story("old", "Aid convoy enters Gaza", "https://www.example.com/story?utm_source=x")
    write_prior_memory(root, prior)

    result = dedupe_public_stories(root, "gaza", "2026-05-08", [story("new", "Aid convoy enters Gaza", "https://example.com/story?utm_campaign=y")])

    assert result.stories == []
    assert "exact_or_normalized_source_url" in result.report["duplicate_skipped"][0]["duplicate_reasons"]


def test_same_title_different_source_merged_within_edition():
    root = make_root()
    first = story("a", "Hospital fuel warning issued", "https://example.com/a", publisher="Publisher A")
    second = story("b", "Hospital fuel warning issued", "https://example.org/b", publisher="Publisher B")

    result = dedupe_public_stories(root, "gaza", "2026-05-08", [first, second])

    assert len(result.stories) == 1
    assert set(result.stories[0]["source_urls"]) == {"https://example.com/a", "https://example.org/b"}
    assert result.report["duplicate_groups"]


def test_gaza_flotilla_same_event_titles_merge_into_one_group():
    root = make_root()
    a = story(
        "a",
        "Israeli forces board Gaza-bound flotilla near Cyprus, activists say",
        "https://example.com/bbc-flotilla",
        publisher="BBC",
    )
    b = story(
        "b",
        "Israeli forces begin intercepting Gaza-bound aid flotilla near Cyprus",
        "https://example.com/aj-intercept",
        publisher="Al Jazeera",
    )
    c = story(
        "c",
        "Israeli forces storm Gaza-bound aid flotilla off Cyprus",
        "https://example.com/aj-storm",
        publisher="Al Jazeera",
    )
    result = dedupe_public_stories(root, "gaza", "2026-05-18", [a, b, c])
    assert len(result.stories) == 1
    merged_story = result.stories[0]
    assert len(merged_story["source_urls"]) == 3
    assert len(result.report["duplicate_groups"]) == 2
    assert all(group["duplicate_reason"] == "same_event_flotilla_interception" for group in result.report["duplicate_groups"])
    assert all(group["normalized_event_key"] == "gaza_flotilla_interception_israeli_forces_cyprus" for group in result.report["duplicate_groups"])
    merged_decisions = [item for item in result.decisions if item.get("include_decision") == "merge_into_existing"]
    assert len(merged_decisions) == 2
    assert all(item.get("public_rendered") is False for item in merged_decisions)


def test_same_normalized_title_without_material_update_skipped():
    root = make_root()
    prior = story("old", "Washington bridge inspection program", "https://example.com/old", summary="Officials described a bridge inspection program.", category="Transportation")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    candidate = story("new", "Update: Washington bridge inspection program", "https://example.com/new", summary="Officials described a bridge inspection program.", category="Transportation", region_scope="WA", state_hint="WA")

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [candidate])

    assert result.stories == []
    skipped = result.report["duplicate_skipped"][0]
    assert skipped["classification"] == "duplicate_skip"
    assert skipped["material_update"] is False
    assert skipped["include_decision"] == "skip"


def test_same_topic_without_material_update_skipped():
    root = make_root()
    prior = story("old", "Washington bridge inspection program", "https://example.com/old", summary="Officials described a bridge inspection program.", category="Transportation")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    candidate = story("new", "Washington bridge inspection program", "https://example.com/new", summary="Officials described a bridge inspection program with background context.", category="Transportation", region_scope="WA", state_hint="WA")

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [candidate])

    assert result.stories == []
    assert result.report["duplicate_skipped"][0]["classification"] == "duplicate_skip"
    assert "non_material_continuation" in result.report["duplicate_skipped"][0]["duplicate_reasons"]
    assert result.report["duplicate_skipped"][0]["material_update"] is False


def test_same_topic_new_official_source_included_as_material_continuation():
    root = make_root()
    prior = story("old", "Washington bridge inspection program", "https://example.com/old", summary="Officials described a bridge inspection program.", category="Transportation")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    candidate = story(
        "new",
        "Washington bridge inspection program",
        "https://agency.wa.gov/bridge",
        summary="Officials described a bridge inspection program with agency documentation.",
        category="Transportation",
        publisher="Washington Department of Transportation",
        source_type="official_page",
        region_scope="WA",
        state_hint="WA",
        reliability_tier="official-public",
    )

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [candidate])

    assert len(result.stories) == 1
    assert result.stories[0]["dedupe_classification"] == "continuing_development"
    assert "new_official_or_public_agency_source" in result.stories[0]["material_update_reasons"]


def test_same_topic_new_geography_included_as_material_continuation():
    root = make_root()
    prior = story("old", "Regional wildfire smoke advisory", "https://example.com/old", summary="Officials described wildfire smoke advisories.", category="Public safety")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    candidate = story("new", "Regional wildfire smoke advisory", "https://example.com/new", summary="Officials described wildfire smoke advisories for Oregon.", category="Public safety", region_scope="OR", state_hint="OR")

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [candidate])

    assert len(result.stories) == 1
    assert "new_geography" in result.stories[0]["material_update_reasons"]


def test_same_topic_update_verb_included_as_material_continuation():
    root = make_root()
    prior = story("old", "Oregon wildfire season planning", "https://example.com/old", summary="Officials discussed wildfire season planning.", category="Public safety")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    candidate = story("new", "Oregon declares wildfire readiness order", "https://example.com/new", summary="Officials declared a new wildfire readiness order.", category="Public safety", region_scope="OR", state_hint="OR")

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [candidate])

    assert len(result.stories) == 1
    assert result.stories[0]["dedupe_classification"] in {"continuing_development", "major_update"}
    assert "update_term_in_title_or_snippet" in result.stories[0]["material_update_reasons"]


def test_major_update_retained():
    root = make_root()
    prior = story("old", "Oregon wildfire evacuation route planning", "https://example.com/old", summary="Officials discussed evacuation route planning.", category="Public safety")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    candidate = story("new", "Oregon wildfire evacuation route planning", "https://example.com/new", summary="Officials announced new evacuation route orders for several communities.", category="Public safety")

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [candidate])

    assert len(result.stories) == 1
    assert result.stories[0]["dedupe_classification"] == "major_update"
    assert result.report["major_updates"]


def test_skipped_candidate_report_and_memory_update_counts():
    root = make_root()
    prior = story("old", "Washington bridge inspection program", "https://example.com/old", summary="Officials described a bridge inspection program.", category="Transportation")
    write_prior_memory(root, prior, edition_date="2026-05-03", dispatch_slug="cascadia")
    skipped = story("skip", "Washington bridge inspection program", "https://example.com/skip", summary="Officials described a bridge inspection program.", category="Transportation", region_scope="WA", state_hint="WA")
    included = story(
        "include",
        "Washington bridge inspection program announces lane closure",
        "https://agency.wa.gov/bridge-closure",
        summary="Officials announced a new lane closure for the bridge inspection program.",
        category="Transportation",
        publisher="Washington State Department of Transportation",
        source_type="official_page",
        region_scope="WA",
        state_hint="WA",
        reliability_tier="official-public",
    )

    result = dedupe_public_stories(root, "cascadia", "2026-05-10", [skipped, included])

    assert len(result.stories) == 1
    assert result.report["duplicate_skipped"][0]["story_id"] == "skip"
    memory = json.loads((root / "data" / "records" / "story_memory.json").read_text(encoding="utf-8"))
    new_rows = [row for row in memory if row["edition_date"] == "2026-05-10"]
    assert len(new_rows) == 1
    assert new_rows[0]["first_seen_date"] == "2026-05-03"
    assert new_rows[0]["update_count"] == 1
    assert "https://agency.wa.gov/bridge-closure" in new_rows[0]["source_urls"]
    assert new_rows[0]["latest_classification"] in {"continuing_development", "major_update"}


def test_gaza_layout_does_not_repeat_top_story_in_other_developments(monkeypatch):
    root = make_root()
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", root / "output" / "backups" / "gaza")
    source_dir = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-08"
    source_dir.mkdir(parents=True)
    source_dir.joinpath("manual_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-1",
                    "title": "Hospital fuel warning issued",
                    "url": "https://example.com/a",
                    "publisher": "Publisher A",
                    "published_at": "2026-05-08T00:00:00Z",
                    "retrieved_at": "2026-05-08T01:00:00Z",
                    "summary_or_snippet": "A source-backed hospital fuel warning.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "src-2",
                    "title": "Hospital fuel warning issued",
                    "url": "https://example.org/b",
                    "publisher": "Publisher B",
                    "published_at": "2026-05-08T02:00:00Z",
                    "retrieved_at": "2026-05-08T03:00:00Z",
                    "summary_or_snippet": "A second source-backed hospital fuel warning.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "src-3",
                    "title": "Aid crossing schedule changes",
                    "url": "https://example.net/c",
                    "publisher": "Publisher C",
                    "published_at": "2026-05-08T04:00:00Z",
                    "retrieved_at": "2026-05-08T05:00:00Z",
                    "summary_or_snippet": "A source-backed aid crossing schedule change.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_gaza_dispatch(root, "2026-05-08", from_manual_sources=True, dry_run=False, render=True, all_steps=False)

    html = (root / "output" / "site" / "gaza" / "editions" / "2026-05-08" / "index.html").read_text(encoding="utf-8")
    glance = html.split("<h2>At A Glance</h2>", 1)[1].split("</ul>", 1)[0]
    other = html.split("<h2>Other Gaza Developments</h2>", 1)[1]
    assert result["ok"] is True
    assert glance.count("Hospital fuel warning issued") == 1
    assert "Hospital fuel warning issued" not in other
    assert "https://example.com/a" in html
    assert "https://example.org/b" in html
    assert (root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-08" / "dedupe_report.json").exists()


def test_gaza_run_merges_flotilla_same_event_and_keeps_unrelated_story(monkeypatch):
    root = make_root()
    monkeypatch.setattr("scripts.run_gaza_dispatch.BACKUP_ROOT", root / "output" / "backups" / "gaza")
    source_dir = root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-18"
    source_dir.mkdir(parents=True)
    source_dir.joinpath("manual_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-05-18-bbc-middle-east-5c5de389ca71",
                    "title": "Israeli forces board Gaza-bound flotilla near Cyprus, activists say",
                    "url": "https://www.bbc.com/news/articles/abc",
                    "publisher": "BBC",
                    "published_at": "2026-05-18T01:00:00Z",
                    "retrieved_at": "2026-05-18T02:00:00Z",
                    "summary_or_snippet": "Israeli forces board Gaza-bound aid flotilla near Cyprus.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-2026-05-18-aljazeera-middle-east-48e69cdad3d6",
                    "title": "Israeli forces begin intercepting Gaza-bound aid flotilla near Cyprus",
                    "url": "https://www.aljazeera.com/video/newsfeed/2026/5/18/flotilla-intercept",
                    "publisher": "Al Jazeera",
                    "published_at": "2026-05-18T01:10:00Z",
                    "retrieved_at": "2026-05-18T02:10:00Z",
                    "summary_or_snippet": "Israeli forces begin intercepting Gaza-bound aid flotilla near Cyprus.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-2026-05-18-aljazeera-middle-east-e3f36973737f",
                    "title": "Israeli forces storm Gaza-bound aid flotilla off Cyprus",
                    "url": "https://www.aljazeera.com/news/2026/5/18/flotilla-storm",
                    "publisher": "Al Jazeera",
                    "published_at": "2026-05-18T01:20:00Z",
                    "retrieved_at": "2026-05-18T02:20:00Z",
                    "summary_or_snippet": "Israeli forces storm Gaza-bound aid flotilla off Cyprus.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "conflict",
                    "reliability_tier": "reported-public-source",
                },
                {
                    "source_record_id": "gaza-2026-05-18-other-story",
                    "title": "Growing bread queues in Gaza as Israel restricts fuel, flour imports",
                    "url": "https://www.aljazeera.com/news/2026/5/18/growing-bread-lines-gaza",
                    "publisher": "Al Jazeera",
                    "published_at": "2026-05-18T03:00:00Z",
                    "retrieved_at": "2026-05-18T04:00:00Z",
                    "summary_or_snippet": "Bread lines grow amid fuel and flour restrictions.",
                    "source_type": "news",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "reported-public-source",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_gaza_dispatch(root, "2026-05-18", from_manual_sources=True, dry_run=False, render=True, all_steps=False)
    assert result["ok"] is True
    html = (root / "output" / "site" / "gaza" / "editions" / "2026-05-18" / "index.html").read_text(encoding="utf-8")
    glance = html.split("<h2>At A Glance</h2>", 1)[1].split("</ul>", 1)[0]
    assert glance.count("flotilla") == 1
    assert "Growing bread queues in Gaza as Israel restricts fuel, flour imports" in html
    assert "https://www.bbc.com/news/articles/abc" in html
    assert "https://www.aljazeera.com/video/newsfeed/2026/5/18/flotilla-intercept" in html
    assert "https://www.aljazeera.com/news/2026/5/18/flotilla-storm" in html

    dedupe = json.loads((root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-18" / "dedupe_report.json").read_text(encoding="utf-8"))
    assert len(dedupe["duplicate_groups"]) >= 1
    assert any(group["duplicate_reason"] == "same_event_flotilla_interception" for group in dedupe["duplicate_groups"])
    curation = json.loads((root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-18" / "curation_manifest.json").read_text(encoding="utf-8"))
    merged_rows = [row for row in curation if row.get("include_decision") == "merge_into_existing"]
    assert merged_rows
    assert all(row.get("public_rendered") is False for row in merged_rows)


def test_cascadia_render_writes_dedupe_report_and_keeps_weekly_archive_only(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-10"
    curated_dir.mkdir(parents=True)
    base = story("weekly-a", "Washington bridge inspection program", "https://example.com/a", summary="Washington bridge inspection details.", category="Transportation")
    dup = story("weekly-b", "Washington bridge inspection program", "https://example.com/b", summary="Washington bridge inspection details from another source.", category="Transportation")
    curated_dir.joinpath("curation_manifest.json").write_text(json.dumps([base, dup], indent=2), encoding="utf-8")

    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-10",
        run_date="2026-05-11",
        coverage_start="2026-05-04",
        coverage_end="2026-05-10",
        briefing_type="weekly",
    )

    assert result["ok"] is True
    report = cascadia_work_root / "output" / "dispatches" / "cascadia" / "editions" / "2026-05-10" / "dedupe_report.json"
    assert report.exists()
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    rss = (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")
    assert "2026-05-10" in archive
    assert "2026-05-10" in rss
    assert "2026-05-09" not in archive
    assert "2026-05-09" not in rss
