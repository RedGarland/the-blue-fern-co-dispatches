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


def test_gaza_ceasefire_casualty_repeated_reports_merge_into_one_group():
    root = make_root()
    first = story(
        "a",
        "Israel has killed more than 1,000 people in Gaza since ceasefire",
        "https://example.com/newarab",
        summary="The number of Palestinians killed by Israel since the October ceasefire was 1,008, the health ministry said.",
        publisher="The New Arab",
    )
    second = story(
        "b",
        "Israel kills at least three Palestinians in Gaza City drone strike",
        "https://example.com/aljazeera",
        summary="Gaza's Health Ministry says at least 1,007 Palestinians have been killed by Israel since the ceasefire.",
        publisher="Al Jazeera",
    )
    third = story(
        "c",
        "Over 1,000 people killed during Gaza ceasefire, Palestinian authorities say",
        "https://example.com/npr",
        summary="Israeli operations in the Gaza Strip have killed 1,005 Palestinians since a ceasefire was reached last October.",
        publisher="NPR",
    )

    result = dedupe_public_stories(root, "gaza", "2026-06-18", [first, second, third])

    assert len(result.stories) == 1
    merged_story = result.stories[0]
    assert len(merged_story["source_urls"]) == 3
    assert result.report["duplicate_groups"]
    assert all(group["duplicate_reason"] == "gaza_ceasefire_casualty" for group in result.report["duplicate_groups"])
    assert len([item for item in result.decisions if item.get("include_decision") == "merge_into_existing"]) == 2


def test_gaza_distinct_developments_stay_separate_even_with_shared_gaza_israel_terms():
    root = make_root()
    first = story(
        "a",
        "Patients die in Gaza waiting for medical evacuations Israel keeps blocking",
        "https://example.com/medical",
        summary="Despite referrals to leave Gaza, Palestinians are not allowed to leave for medical care.",
        publisher="Al Jazeera",
        category="conflict",
    )
    second = story(
        "b",
        "Israel orders demolition of 9 Palestinian homes in Hebron amid West Bank escalation",
        "https://example.com/hebron",
        summary="West Bank has seen increase in attacks by Israeli forces against Palestinians since October 2023.",
        publisher="Anadolu Agency",
        category="palestinian_development",
    )

    result = dedupe_public_stories(root, "gaza", "2026-06-18", [first, second])

    assert len(result.stories) == 2
    assert not result.report["duplicate_groups"]
    assert {story_row["story_id"] for story_row in result.stories} == {"a", "b"}


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


def test_pages_repo_prior_curation_is_used_for_july_4_wrapper_duplicate():
    root = make_root()
    pages_edition = root / "bluefern-dispatches-pages" / "gaza" / "editions" / "2026-07-03"
    pages_edition.mkdir(parents=True, exist_ok=True)
    pages_edition.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                story(
                    "prior",
                    "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'",
                    "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
                    summary="A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'.",
                    category="humanitarian_conditions",
                    publisher="EL PAIS English",
                    published_at="2026-07-03T04:28:00+02:00",
                )
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    candidate = story(
        "candidate",
        "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water' - EL PAÍS English",
        "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVk?oc=5",
        summary="A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'.",
        category="humanitarian_conditions",
        publisher="EL PAÍS English",
        published_at="2026-07-03T04:28:00+02:00",
    )
    candidate["source_records"][0]["canonical_url"] = ""

    result = dedupe_public_stories(root, "gaza", "2026-07-04", [candidate])

    assert result.stories == []
    skipped = result.report["duplicate_skipped"][0]
    assert skipped["classification"] == "duplicate_skip"
    assert skipped["prior_edition_date"] == "2026-07-03"
    assert skipped["prior_story_matched"] == "prior"


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
    assert result["ok"] is True
    assert glance.count("Hospital fuel warning issued") == 1
    assert html.count("Hospital fuel warning issued") >= 1
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


def test_gaza_funeral_same_event_titles_merge_into_one_group():
    root = make_root()
    stories = [
        story(
            "guardian-funeral",
            "Mass funeral held in Gaza for victims of 2023 Israeli strike",
            "https://www.theguardian.com/world/2026/aug/04/mass-funeral-gaza-victims-2023-israeli-strike",
            summary="Remains of 112 victims, including 40 children, recovered from rubble more than two years after residential block was destroyed in central Gaza.",
            category="conflict",
            publisher="The Guardian",
            region_scope="Gaza",
            published_at="2026-08-04T17:24:41+00:00",
        ),
        story(
            "bbc-funeral",
            "Mass funeral in Gaza for 112 Palestinians killed in 2023 Israeli strike",
            "https://www.bbc.co.uk/news/articles/cn0n99npjejo?at_medium=RSS&at_campaign=rss",
            summary="The bodies of two extended families were recently recovered from rubble in Gaza City after the 2023 strike.",
            category="conflict",
            publisher="BBC News",
            region_scope="Gaza",
            published_at="2026-08-04T15:07:52+00:00",
        ),
    ]

    result = dedupe_public_stories(root, "gaza", "2026-08-05", stories)

    assert len(result.stories) == 1
    merged = result.stories[0]
    assert merged["source_record_ids"] == ["src-guardian-funeral", "src-bbc-funeral"]
    assert merged["source_urls"] == [
        "https://www.theguardian.com/world/2026/aug/04/mass-funeral-gaza-victims-2023-israeli-strike",
        "https://www.bbc.co.uk/news/articles/cn0n99npjejo?at_medium=RSS&at_campaign=rss",
    ]
    assert merged["publisher_names"] == ["The Guardian", "BBC News"]
    assert any(group["duplicate_reason"] == "same_event_funeral_recovery" for group in result.report["duplicate_groups"])
    assert result.report["duplicate_groups"][0]["normalized_event_key"].startswith("gaza_funeral_recovery_")


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
