import json
import shutil
import uuid
import gzip
import urllib.error
from pathlib import Path

import pytest

from bluefern_dispatches import gaza_sources
from bluefern_dispatches.story_dedupe import dedupe_public_stories
from scripts.run_gaza_dispatch import curate_stories, normalize_sources
from scripts import check_gaza_sources


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    entries = []
    for item in items:
        entries.append(
            "<item>"
            f"<title>{item.get('title','')}</title>"
            f"<link>{item.get('url','')}</link>"
            f"<pubDate>{item.get('published_at','')}</pubDate>"
            f"<description>{item.get('summary_or_snippet','')}</description>"
            "</item>"
        )
    return ("<?xml version='1.0'?><rss><channel>" + "".join(entries) + "</channel></rss>").encode("utf-8")


def write_config(root: Path) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: test-rss
    name: Test RSS
    url: https://example.com/rss.xml
    type: rss
    enabled: true
    publisher: Example Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
  - source_id: manual
    name: Manual
    url: data/dispatches/gaza/sources/YYYY-MM-DD/manual_sources.json
    type: manual
    enabled: true
    publisher: Blue Fern Dispatch Records
    reliability_tier: editorial-record
    category_hint: manual
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    return path


def write_two_feed_config(root: Path) -> Path:
    path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: bad-rss
    name: Bad RSS
    url: https://example.com/bad.xml
    type: rss
    enabled: true
    publisher: Bad Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
  - source_id: good-rss
    name: Good RSS
    url: https://example.com/good.xml
    type: rss
    enabled: true
    publisher: Good Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def work_root():
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "sources"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_sources_yml_loads(work_root):
    config = write_config(work_root)

    sources = gaza_sources.load_sources_config(config)

    assert sources[0].source_id == "test-rss"
    assert sources[0].type == "rss"
    assert sources[0].region_scope == "Gaza"


def test_repo_gaza_sources_config_includes_targeted_query_providers():
    config = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "gaza" / "sources.yml"

    sources = gaza_sources.load_sources_config(config)
    by_id = {source.source_id: source for source in sources}

    assert by_id["bbc-gaza-health-query"].type == "google_news_rss"
    assert by_id["elpais-gaza-humanitarian-query"].type == "google_news_rss"
    assert by_id["jpost-gaza-accountability-query"].type == "google_news_rss"
    assert by_id["wafa-gaza-query"].publisher == "WAFA"
    assert by_id["wafa-gaza-query"].type == "google_news_rss"
    assert by_id["wafa-gaza-casualty-locations-query"].query == "site:hebrew.wafa.ps אל־קרארה עאבדין"
    assert by_id["wafa-gaza-displacement-tent-query"].query == 'site:wafa.ps Gaza City "Al-Mashahara" tent displaced'
    assert by_id["wafa-gaza-motorbike-query"].query == 'site:wafa.ps Gaza City "Tal al-Hawa" (motorcycle OR motorbike)'
    assert by_id["wafa-gaza-health-infrastructure-query"].query == (
        'site:wafa.ps Gaza ("Al-Aqsa" OR "Deir al-Balah") (hospital OR warehouse)'
    )
    assert by_id["who-gaza-evacuation-query"].publisher == "WHO"
    assert by_id["unicef-gaza-water-query"].publisher == "UNICEF"
    assert by_id["ap-gaza-attribution-query"].publisher == "Associated Press"
    assert by_id["aljazeera-board-of-peace-isf-query"].type == "google_news_rss"
    assert by_id["aljazeera-board-of-peace-isf-query"].publisher == "Al Jazeera"
    assert "Mladenov" in by_id["aljazeera-board-of-peace-isf-query"].query
    assert "International Stabilization Force" in by_id["aljazeera-board-of-peace-isf-query"].query
    assert "deployment" in by_id["aljazeera-board-of-peace-isf-query"].query
    assert by_id["haaretz-gaza-query"].publisher == "Haaretz"
    assert by_id["dirco-icj-query"].publisher == "DIRCO"
    assert by_id["ocha-opt-updates"].type == "ocha_report_index"
    assert by_id["ocha-opt-updates"].source_state == "enabled"
    assert by_id["ocha-opt-updates"].url == "https://www.ochaopt.org/publications/situation-reports"
    assert all(source.url != "https://www.ochaopt.org/updates/rss.xml" for source in sources if source.source_state == "enabled")


def test_rss_source_records_normalize_and_write(work_root, monkeypatch):
    write_config(work_root)

    def fake_fetch(source_id, url, timeout=20):
        assert source_id == "test-rss"
        assert url == "https://example.com/rss.xml"
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([
                {
                    "title": "Aid convoys enter Gaza",
                    "url": "https://valid.test/gaza-aid",
                    "published_at": "2026-05-07T08:00:00+00:00",
                    "summary_or_snippet": "Humanitarian update for Gaza.",
                }
            ]),
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=1)

    assert result["ok"] is True
    assert result["source_count"] == 1
    record = result["sources"][0]
    assert set(gaza_sources.REQUIRED_SOURCE_FIELDS).issubset(record)
    assert record["traceability_note"]
    assert record["attribution_mode"] == "reported_public_source"
    assert record["claim_status"] == "reported_public_source"
    assert record["title"] == "Aid convoys enter Gaza"
    assert record["publisher"] == "Example Publisher"
    assert json.loads(Path(result["source_file"]).read_text(encoding="utf-8"))[0]["url"] == "https://valid.test/gaza-aid"


def test_gaza_relevance_and_date_filtering(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", lambda *_args, **_kwargs: {
        "ok": True,
        "source_id": "test-rss",
        "url": "https://example.com/rss.xml",
        "status_code": 200,
        "failure_reason": None,
        "exception_type": None,
        "tls_error": False,
        "backend_used": "python",
        "content_type": "application/rss+xml",
        "content_encoding": "",
        "content_bytes": _rss_payload([
            {
                "title": "Regional weather update",
                "url": "https://valid.test/weather",
                "published_at": "2026-05-07T08:00:00+00:00",
                "summary_or_snippet": "No relevant geography.",
            },
            {
                "title": "Gaza hospital update",
                "url": "https://valid.test/old-gaza",
                "published_at": "2026-05-06T08:00:00+00:00",
                "summary_or_snippet": "Gaza update from a previous date.",
            },
            {
                "title": "Gaza hospital update",
                "url": "https://valid.test/gaza",
                "published_at": "2026-05-07T08:00:00+00:00",
                "summary_or_snippet": "Gaza update.",
            },
        ]),
        "content_text": None,
    })

    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=1)

    assert result["ok"] is True
    assert [record["url"] for record in result["sources"]] == ["https://valid.test/old-gaza", "https://valid.test/gaza"]


def test_missing_published_at_is_not_accepted_as_fresh_in_collection(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", lambda *_args, **_kwargs: {
        "ok": True,
        "source_id": "test-rss",
        "url": "https://example.com/rss.xml",
        "status_code": 200,
        "failure_reason": None,
        "exception_type": None,
        "tls_error": False,
        "backend_used": "python",
        "content_type": "application/rss+xml",
        "content_encoding": "",
        "content_bytes": _rss_payload([
            {
                "title": "Gaza crossing update",
                "url": "https://valid.test/gaza-no-date",
                "published_at": "",
                "summary_or_snippet": "Date missing.",
            }
        ]),
        "content_text": None,
    })
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=0)
    assert result["source_count"] == 0
    assert result["rejected_by_reason"]["rejected_missing_published_at"] == 1


def test_no_fake_sources_are_invented(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", lambda *_args, **_kwargs: {"ok": True, "content_bytes": _rss_payload([]), "content_type": "application/rss+xml", "content_encoding": "", "backend_used": "python", "tls_error": False})

    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=1)

    assert result["ok"] is False
    assert result["sources"] == []
    assert "below minimum" in result["errors"][0]


def test_bad_feed_is_skipped_with_failed_source_id(work_root, monkeypatch):
    write_two_feed_config(work_root)

    def fake_fetch(source_id, url, timeout=20):
        if url.endswith("/bad.xml"):
            return {
                "ok": False,
                "source_id": source_id,
                "url": url,
                "status_code": 200,
                "failure_reason": "ValueError: non-XML feed response (content-type=text/html)",
                "exception_type": "ValueError",
                "tls_error": False,
                "backend_used": "python",
                "content_bytes": None,
                "content_text": None,
            }
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([
            {
                "title": "Gaza aid crossing update",
                "url": "https://valid.test/gaza-aid",
                "published_at": "2026-05-07T08:00:00+00:00",
                "summary_or_snippet": "Humanitarian update for Gaza.",
            }
            ]),
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=1)

    assert result["ok"] is True
    assert result["source_count"] == 1
    assert result["failed_source_ids"][0]["source_id"] == "bad-rss"
    assert "non-XML feed response" in result["failed_source_ids"][0]["reason"]
    assert "bad-rss: ValueError: non-XML feed response" in result["warnings"][0]


def test_non_xml_response_is_reported_before_xml_parse(monkeypatch):
    class FakeHeaders:
        def get(self, name):
            if name == "Content-Type":
                return "text/html; charset=utf-8"
            return None

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"not xml"

    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    with pytest.raises(ValueError, match="non-XML feed response"):
        gaza_sources.fetch_rss_items("https://example.com/feed")


def test_gzip_feed_response_is_decompressed(monkeypatch):
    rss = b"""<?xml version="1.0"?>
<rss><channel><item><title>Gaza aid update</title><link>https://valid.test/gaza</link><pubDate>Thu, 07 May 2026 08:00:00 GMT</pubDate><description>Gaza update.</description></item></channel></rss>"""

    class FakeHeaders:
        def get(self, name):
            if name == "Content-Type":
                return "application/rss+xml; charset=utf-8"
            if name == "Content-Encoding":
                return "gzip"
            return None

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return gzip.compress(rss)

    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    items = gaza_sources.fetch_rss_items("https://example.com/feed")

    assert items[0]["title"] == "Gaza aid update"


def test_valid_manual_sources_are_first_choice(work_root, monkeypatch):
    write_config(work_root)
    manual_path = gaza_sources.manual_source_path(work_root, "2026-05-07")
    manual_path.parent.mkdir(parents=True, exist_ok=True)
    manual_path.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-05-07-manual-001",
                    "title": "Manual Gaza hospital source",
                    "url": "https://valid.test/manual-gaza",
                    "publisher": "Manual Publisher",
                    "published_at": "2026-05-07T08:00:00+00:00",
                    "retrieved_at": "2026-05-07T09:00:00+00:00",
                    "summary_or_snippet": "Manual source-backed Gaza update.",
                    "source_type": "manual",
                    "region_scope": "Gaza",
                    "category_hint": "humanitarian",
                    "reliability_tier": "editorial-record",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gaza_sources, "fetch_rss_items", lambda _url: pytest.fail("RSS should not be fetched"))

    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=1)

    assert result["ok"] is True
    assert result["source_mode_used"] == "manual"
    assert result["source_file"] == str(manual_path)


def test_cross_edition_dedupe_suppresses_repeated_url_and_writes_diagnostic(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-001",
                    "title": "Gaza aid crossing update",
                    "url": "https://news.google.com/rss/articles/abc123?utm_source=rss",
                    "canonical_url": "",
                    "publisher": "Example News",
                    "published_at": "",
                    "retrieved_at": "2026-05-10T08:00:00+00:00",
                    "category_hint": "humanitarian",
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-src-002",
            "title": "Gaza aid crossing update",
            "url": "https://news.google.com/rss/articles/abc123?utm_source=other",
            "canonical_url": "",
            "publisher": "Example News",
            "published_at": "",
            "retrieved_at": "2026-05-11T08:00:00+00:00",
            "category_hint": "humanitarian",
        }
    ]

    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-11", candidates)

    assert len(kept) == 1
    assert kept[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert kept[0]["prior_duplicate_edition_date"] == "2026-05-10"
    assert report["suppressed_candidate_count"] == 1
    assert report["suppressed_candidates"][0]["matched_prior_edition"] == "2026-05-10"
    assert report["suppressed_candidates"][0]["matched_key_type"] in {
        "canonical_url",
        "normalized_url",
        "publisher_title",
        "title_fingerprint",
        "claim_fingerprint",
    }


def test_retrieved_at_alone_does_not_make_repeated_source_fresh(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-001",
                    "title": "Hospital fuel warning in Gaza",
                    "url": "https://example.com/story",
                    "canonical_url": "https://example.com/story",
                    "publisher": "Wire",
                    "published_at": "2026-05-10T07:00:00+00:00",
                    "retrieved_at": "2026-05-10T08:00:00+00:00",
                    "category_hint": "humanitarian",
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-src-003",
            "title": "Hospital fuel warning in Gaza",
            "url": "https://example.com/story",
            "canonical_url": "https://example.com/story",
            "publisher": "Wire",
            "published_at": "2026-05-10T07:00:00+00:00",
            "retrieved_at": "2026-05-11T08:00:00+00:00",
            "category_hint": "humanitarian",
        }
    ]

    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-11", candidates)

    assert len(kept) == 1
    assert kept[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert report["suppressed_candidate_count"] == 1


def test_google_wrapper_with_different_wrapper_urls_suppresses_by_canonical(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "title": "Aid update",
                    "url": "https://news.google.com/rss/articles/abc?url=https%3A%2F%2Freuters.com%2Fa",
                    "canonical_url": "https://reuters.com/a",
                    "publisher": "Reuters",
                    "published_at": "2026-05-10T07:00:00+00:00",
                    "category_hint": "humanitarian",
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "title": "Aid update",
            "url": "https://news.google.com/rss/articles/xyz?url=https%3A%2F%2Freuters.com%2Fa",
            "canonical_url": "https://reuters.com/a",
            "publisher": "Reuters",
            "published_at": "2026-05-10T07:00:00+00:00",
            "category_hint": "humanitarian",
        }
    ]
    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-11", candidates)
    assert len(kept) == 1
    assert kept[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert report["suppressed_candidate_count"] == 1


def test_missing_published_at_is_stale_risk_when_repeated(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "title": "Ceasefire talks update",
                    "url": "https://example.com/talks",
                    "canonical_url": "https://example.com/talks",
                    "publisher": "Daily Desk",
                    "published_at": "",
                    "retrieved_at": "2026-05-10T08:00:00+00:00",
                    "category_hint": "diplomatic",
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "title": "Ceasefire talks update",
            "url": "https://example.com/talks",
            "canonical_url": "https://example.com/talks",
            "publisher": "Daily Desk",
            "published_at": "",
            "retrieved_at": "2026-05-11T08:00:00+00:00",
            "category_hint": "diplomatic",
        }
    ]

    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-11", candidates)

    assert len(kept) == 1
    assert kept[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert report["stale_risk_candidates"]


def test_new_distinct_source_passes_cross_edition_dedupe(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "title": "Ceasefire talks update",
                    "url": "https://example.com/talks",
                    "canonical_url": "https://example.com/talks",
                    "publisher": "Daily Desk",
                    "published_at": "",
                    "retrieved_at": "2026-05-10T08:00:00+00:00",
                    "category_hint": "diplomatic",
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "title": "Aid corridor inspection opens in northern Gaza",
            "url": "https://example.com/new-aid",
            "canonical_url": "https://example.com/new-aid",
            "publisher": "Daily Desk",
            "published_at": "2026-05-11T09:00:00+00:00",
            "retrieved_at": "2026-05-11T09:30:00+00:00",
            "category_hint": "humanitarian",
        }
    ]

    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-11", candidates)

    assert len(kept) == 1
    assert "story_selection_excluded_reason" not in kept[0]
    assert report["suppressed_candidate_count"] == 0


def test_cross_edition_dedupe_reads_pages_repo_and_suppresses_retrieved_at_only_replays(work_root):
    prior_manifest = work_root / "bluefern-dispatches-pages" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_sources = [
        {
            "source_record_id": "gaza-src-prior-001",
            "title": "How Israel Is Using the Same Tactics in Lebanon That It Did in Gaza",
            "url": "https://news.google.com/rss/articles/CBMirwFBVV95cUxNZlljbzhabF9fQVBUakFVMl9yQ2RfSWdEM3l5bzJpZThveWtVX3lfaWhHQkRqaklxSWtBZE5CYlZSdC16SDhUbW5NTWs2bFo5aW45dlB2UDEwU2dOc1VBWmlRcmVfbzlvbjdUZG9BejJSeTZFdW9qUUd3WDdkMm1mNkpVUmpSZXFDQnllUHZ1SzBFbUpyNlBXRHdwMVZMeXVDcWV6UG1hT1Z2QmdzWkRF",
            "publisher": "The New York Times",
            "published_at": "",
            "retrieved_at": "2026-05-10T12:00:00+00:00",
            "category_hint": "humanitarian",
        },
        {
            "source_record_id": "gaza-src-prior-002",
            "title": "U.S. to close Israel command center overseeing Gaza truce as Trump plan stalls",
            "url": "https://news.google.com/rss/articles/CBMi8wFBVV95cUxOM2t6STREVWZmdHkydFBaX21aLUw3RDdSRHBKcWdrTmw5WHV6RFlOcjhJMmxTOWxKbDNlclEwelE1U2toVGFtNjMzSnBmVXAzc05hVF85eHl3OHZiZUxoMWtXc01LR3NaNUJ5cEh4NF9UMENTNVJrd2F2bm4zLWY4U2taekRkVXdtRWFNZV9zalFkMkV2bHF6MGgwYlU4RTM0UEpOTEZONFNiaHo3cVFyT0pwcFFocGl6S01seG1Fb08zY3N4aTFFUGtZZXVzR2FIX0lEbmlqUG1XXzBjVVNvRGtZSmdwSjlUdzNDbFJmMm1mSUE",
            "publisher": "Haaretz",
            "published_at": "",
            "retrieved_at": "2026-05-10T12:01:00+00:00",
            "category_hint": "humanitarian",
        },
        {
            "source_record_id": "gaza-src-prior-003",
            "title": "Court extends detention of 2 Gaza flotilla activists accused of Hamas links",
            "url": "https://news.google.com/rss/articles/CBMiqgFBVV95cUxNeE1nbHF0MXR5cUNKMTBrcmhINFc3Q3lEV053ZTVDVXVVaW9KVndOT0YwWC15UlZnYTBRd0ZTTXI2Slc1bEtEYmpVOTFiZ0JQR3B3U0JSdkJUV2NKZU9iNUU1WTlTMzhyRENiN1J1NkVDcEQ0Q0ZHRnhBRjF3SUF5b2VhcGotWWswcTlzaHlsSFBtZ3BvZERyZFMtUmwtWTBseWRJd1prV2tLd9IBrwFBVV95cUxNZm5UX0N1NFc3TnZsN3J1d0ZHLUFaYmp0RDhLZFYzb2NoZ245dHJINUZ2WFVUT1BvLWV6VzUyTGV2SUhCVHl4cFR2Vk1KQUl4dmZ3MkM0WDdadXh6Z0FwV0tYTE9DOUFQMXk3c2JPMU94cEU4aWhScHlyWDFMLUlaM1c1Z3NHeHpoaWRLb0ZDdXdpRHJFcllhaUdxNkdkblpGWngxdkFhUmZpT184V2pR",
            "publisher": "The Times of Israel",
            "published_at": "",
            "retrieved_at": "2026-05-10T12:02:00+00:00",
            "category_hint": "humanitarian",
        },
    ]
    prior_manifest.write_text(json.dumps(prior_sources, indent=2), encoding="utf-8")
    candidates = [
        {**record, "retrieved_at": "2026-05-12T08:00:00+00:00", "source_record_id": f"gaza-src-current-{index:03d}"}
        for index, record in enumerate(prior_sources, start=1)
    ]

    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-12", candidates)

    assert len(kept) == 3
    assert all(item["story_selection_excluded_reason"] == "duplicate_recent_story" for item in kept)
    assert report["kept_candidate_count"] == 0
    assert report["suppressed_candidate_count"] == 3
    assert all(item["matched_prior_edition"] == "2026-05-10" for item in report["suppressed_candidates"])


def test_cross_edition_dedupe_reads_output_site_manifest(work_root):
    prior_manifest = work_root / "output" / "site" / "gaza" / "editions" / "2026-05-10" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-src-prior-site-001",
                    "title": "Aid convoy checkpoint update in Gaza",
                    "url": "https://example.com/gaza-checkpoint?utm_source=rss",
                    "publisher": "Example Desk",
                    "published_at": "",
                    "retrieved_at": "2026-05-10T12:00:00+00:00",
                    "category_hint": "humanitarian",
                }
            ]
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-src-current-site-001",
            "title": "Aid convoy checkpoint update in Gaza",
            "url": "https://example.com/gaza-checkpoint?utm_source=other",
            "publisher": "Example Desk",
            "published_at": "",
            "retrieved_at": "2026-05-12T08:00:00+00:00",
            "category_hint": "humanitarian",
        }
    ]

    kept, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-05-12", candidates)

    assert len(kept) == 1
    assert kept[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert report["suppressed_candidate_count"] == 1
    assert report["suppressed_candidates"][0]["matched_prior_edition"] == "2026-05-10"


def test_normalize_title_strips_publisher_suffix_and_folds_diacritics():
    title = "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water' - EL PAÍS English"

    assert gaza_sources.normalize_title(title, publisher="EL PAIS English") == (
        "a heatwave in a miserable tent in gaza i dream of a glass of cold water"
    )


def test_july_3_july_4_elpais_duplicate_marks_recent_story_but_keeps_audit_record(work_root):
    prior_manifest = work_root / "bluefern-dispatches-pages" / "gaza" / "editions" / "2026-07-03" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-07-03-elpais-heatwave-water-displacement",
                    "title": "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'",
                    "url": "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
                    "canonical_url": "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
                    "publisher": "EL PAIS English",
                    "published_at": "2026-07-03T04:28:00+02:00",
                    "retrieved_at": "2026-07-03T04:28:00+02:00",
                    "category_hint": "humanitarian_conditions",
                    "dispatch_slug": "gaza",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-2026-07-04-elpais-wrapper-001",
            "title": "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water' - EL PAÍS English",
            "url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVk?oc=5",
            "canonical_url": "",
            "publisher": "EL PAÍS English",
            "published_at": "2026-07-03T04:28:00+02:00",
            "retrieved_at": "2026-07-04T07:15:00+00:00",
            "category_hint": "humanitarian_conditions",
            "dispatch_slug": "gaza",
        }
    ]

    normalized, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-07-04", candidates)

    assert len(normalized) == 1
    assert normalized[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert normalized[0]["prior_duplicate_edition_date"] == "2026-07-03"
    assert normalized[0]["prior_duplicate_source_record_id"] == "gaza-2026-07-03-elpais-heatwave-water-displacement"
    assert normalized[0]["wrapper_url"].startswith("https://news.google.com/rss/articles/")
    assert report["suppressed_candidate_count"] == 1
    assert report["suppressed_candidates"][0]["matched_prior_source_record_id"] == "gaza-2026-07-03-elpais-heatwave-water-displacement"
    assert report["suppressed_candidates"][0]["story_selection_excluded_reason"] == "duplicate_recent_story"


def test_recent_duplicate_keeps_changed_ceasefire_implementation_assessment(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-08-25" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-08-25-un-roadmap-001",
                    "title": "Board of Peace outlines Gaza ceasefire roadmap",
                    "url": "https://www.un.org/securitycouncil/meeting/board-of-peace-gaza-roadmap",
                    "canonical_url": "https://www.un.org/securitycouncil/meeting/board-of-peace-gaza-roadmap",
                    "publisher": "UN Security Council",
                    "published_at": "",
                    "retrieved_at": "2026-08-25T10:00:00+00:00",
                    "summary_or_snippet": "Officials described a roadmap for ceasefire implementation and transition steps that remain on track.",
                    "category_hint": "diplomatic",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-2026-08-26-un-roadmap-002",
            "title": "Board of Peace outlines Gaza ceasefire roadmap",
            "url": "https://www.un.org/securitycouncil/meeting/board-of-peace-gaza-roadmap",
            "canonical_url": "https://www.un.org/securitycouncil/meeting/board-of-peace-gaza-roadmap",
            "publisher": "UN Security Council",
            "published_at": "",
            "retrieved_at": "2026-08-26T10:00:00+00:00",
            "summary_or_snippet": "The envoy warned Hamas had not yet fully met its ceasefire commitment and that the implementation roadmap could collapse.",
            "category_hint": "diplomatic",
        }
    ]

    normalized, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-08-26", candidates)

    assert len(normalized) == 1
    assert "story_selection_excluded_reason" not in normalized[0]
    assert report["suppressed_candidate_count"] == 0
    assert report["kept_candidate_count"] == 1


def test_recent_duplicate_keeps_mladenov_isf_state_change(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-08-27" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-08-27-bbc-middle-east-b42465192e5a",
                    "title": "Board of Peace's Gaza envoy criticises Israeli strikes and Hamas actions",
                    "url": "https://www.bbc.com/news/articles/b42465192e5a",
                    "canonical_url": "https://www.bbc.com/news/articles/b42465192e5a",
                    "publisher": "BBC News",
                    "published_at": "2026-08-27T08:00:00+00:00",
                    "retrieved_at": "2026-08-27T08:05:00+00:00",
                    "summary_or_snippet": "The envoy criticised Israeli strikes and Hamas actions in Gaza.",
                    "category_hint": "diplomatic",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-2026-08-28-bbc-middle-east-b42465192e5a",
            "title": "Board of Peace's Gaza envoy criticises Israeli strikes and Hamas actions",
            "url": "https://www.bbc.com/news/articles/b42465192e5a",
            "canonical_url": "https://www.bbc.com/news/articles/b42465192e5a",
            "publisher": "BBC News",
            "published_at": "2026-08-28T08:00:00+00:00",
            "retrieved_at": "2026-08-28T08:05:00+00:00",
            "summary_or_snippet": "Mladenov said the Board of Peace had determined the deployment mechanism and deployment locations for the ISF and said advance elements should arrive soon.",
            "category_hint": "diplomatic",
        }
    ]

    normalized, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-08-28", candidates)

    assert len(normalized) == 1
    assert "story_selection_excluded_reason" not in normalized[0]
    assert report["suppressed_candidate_count"] == 0
    assert report["kept_candidate_count"] == 1


def test_recent_duplicate_can_pass_with_explicit_material_update_override(work_root):
    prior_manifest = work_root / "bluefern-dispatches-pages" / "gaza" / "editions" / "2026-07-03" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior_manifest.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "gaza-2026-07-03-elpais-heatwave-water-displacement",
                    "title": "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water'",
                    "url": "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
                    "canonical_url": "https://english.elpais.com/international/2026/07/03/a-heatwave-in-a-miserable-tent-in-gaza-i-dream-of-a-glass-of-cold-water.html",
                    "publisher": "EL PAIS English",
                    "published_at": "2026-07-03T04:28:00+02:00",
                    "retrieved_at": "2026-07-03T04:28:00+02:00",
                    "category_hint": "humanitarian_conditions",
                    "dispatch_slug": "gaza",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    candidates = [
        {
            "source_record_id": "gaza-2026-07-04-elpais-wrapper-002",
            "title": "A heatwave in a miserable tent in Gaza: 'I dream of a glass of cold water' - EL PAÍS English",
            "url": "https://news.google.com/rss/articles/CBMiX2h0dHBzOi8vbmV3cy5nb29nbGUuY29tL3Jzcy9hcnRpY2xlcy91bnJlc29sdmVk?oc=5",
            "canonical_url": "",
            "publisher": "EL PAÍS English",
            "published_at": "2026-07-04T08:00:00+02:00",
            "retrieved_at": "2026-07-04T08:10:00+02:00",
            "category_hint": "humanitarian_conditions",
            "dispatch_slug": "gaza",
            "allow_recent_duplicate_story": True,
        }
    ]

    normalized, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-07-04", candidates)

    assert len(normalized) == 1
    assert "story_selection_excluded_reason" not in normalized[0]
    assert report["suppressed_candidate_count"] == 0
    assert report["kept_candidate_count"] == 1


def test_rank_gaza_candidates_prioritizes_humanitarian_high_value_items():
    ranked = gaza_sources.rank_gaza_candidates(
        [
            {
                "title": "Sports update elsewhere",
                "summary_or_snippet": "No Gaza relevance terms",
                "category_hint": "general",
                "publisher": "Blog",
                "reliability_tier": "editorial-record",
                "published_at": "",
            },
            {
                "title": "UNRWA says aid convoy crossing access expands in Gaza hospitals",
                "summary_or_snippet": "Humanitarian aid, civilian harm and health infrastructure pressure",
                "category_hint": "humanitarian",
                "publisher": "UNRWA",
                "reliability_tier": "official-humanitarian-source",
                "published_at": "2026-05-07T08:00:00+00:00",
            },
        ],
        "2026-05-07",
    )
    by_title = {row["title"]: row for row in ranked}
    high = by_title["UNRWA says aid convoy crossing access expands in Gaza hospitals"]
    low = by_title["Sports update elsewhere"]
    assert high["candidate_score"] > low["candidate_score"]
    assert high["candidate_score_breakdown"]["source_reliability"] >= 20
    assert high["candidate_score_breakdown"]["date_confidence"] >= 18


def test_select_gaza_candidates_is_deterministic_and_preserves_under_cap_order():
    records = [
        {
            "source_record_id": f"source-{index:02d}",
            "provider_id": f"provider-{index:02d}",
            "title": f"Gaza ceasefire update {index}",
            "summary_or_snippet": "A Palestinian ceasefire development was reported.",
            "category_hint": "conflict",
            "publisher": f"Publisher {index}",
            "reliability_tier": "editorial-record",
            "published_at": "2026-08-27T08:00:00+00:00",
            "url": f"https://news.test.invalid/gaza-{index}",
        }
        for index in range(12)
    ]
    under_cap, under_cap_excluded = gaza_sources.select_gaza_candidates(records[:3], "2026-08-29", 12)
    assert [row["source_record_id"] for row in under_cap] == ["source-00", "source-01", "source-02"]
    assert under_cap_excluded == []

    late_high_value = {
        "source_record_id": "source-late-high",
        "provider_id": "provider-late",
        "title": "UNRWA says Gaza hospital aid access expands after humanitarian emergency",
        "summary_or_snippet": "Humanitarian aid reached civilians facing displacement and hospital pressure.",
        "category_hint": "humanitarian",
        "publisher": "UNRWA",
        "reliability_tier": "official-humanitarian-source",
        "published_at": "2026-08-29T12:00:00+00:00",
        "url": "https://www.unrwa.org/newsroom/gaza-late-high",
    }
    first, first_excluded = gaza_sources.select_gaza_candidates([*records, late_high_value], "2026-08-29", 12)
    second, second_excluded = gaza_sources.select_gaza_candidates([*records, late_high_value], "2026-08-29", 12)
    assert len(first) == 12
    assert "source-late-high" in {row["source_record_id"] for row in first}
    assert [row["source_record_id"] for row in first] == [row["source_record_id"] for row in second]
    assert [row["source_record_id"] for row in first_excluded] == [row["source_record_id"] for row in second_excluded]


def test_collection_attempts_late_provider_after_early_saturation_and_reports_cap_exclusion(work_root, monkeypatch):
    config_path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """sources:
  - source_id: early-provider
    name: Early Provider
    url: https://early.test.invalid/rss
    type: rss
    enabled: true
    source_state: enabled
    publisher: Early Publisher
    reliability_tier: editorial-record
    category_hint: conflict
    region_scope: Gaza
  - source_id: late-high-provider
    name: Late High Provider
    url: https://www.unrwa.org/rss
    type: rss
    enabled: true
    source_state: enabled
    publisher: UNRWA
    reliability_tier: official-humanitarian-source
    category_hint: humanitarian
    region_scope: Gaza
  - source_id: late-low-provider
    name: Late Low Provider
    url: https://late.test.invalid/rss
    type: rss
    enabled: true
    source_state: enabled
    publisher: Late Publisher
    reliability_tier: editorial-record
    category_hint: conflict
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    feed_calls = []

    def item(index, *, title="Gaza ceasefire update", published_at="2026-08-28T08:00:00+00:00", host="early.test.invalid"):
        return {
            "title": f"{title} {index}",
            "url": f"https://{host}/news/gaza-{index}",
            "published_at": published_at,
            "summary_or_snippet": "A Palestinian ceasefire development was reported.",
        }

    def fake_fetch(source_id, _url, *_args, **_kwargs):
        feed_calls.append(source_id)
        if source_id == "early-provider":
            items = [item(index) for index in range(13)]
        elif source_id == "late-high-provider":
            items = [
                item(
                    100,
                    title="UNRWA Gaza hospital humanitarian aid access expands for displaced civilians",
                    published_at="2026-08-29T12:00:00+00:00",
                    host="www.unrwa.org",
                )
            ]
        else:
            items = [item(200, published_at="2026-08-27T08:00:00+00:00", host="late.test.invalid")]
        return {
            "ok": True,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(items),
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_fetch)
    result = gaza_sources.collect_gaza_sources(
        work_root,
        "2026-08-29",
        max_sources=12,
        min_sources=0,
        prefer_manual=False,
        write_output=False,
    )

    assert feed_calls == ["early-provider", "late-high-provider", "late-low-provider"]
    assert result["source_count"] == 12
    assert result["stage_counts"]["accepted_before_global_source_cap"] == 14
    assert result["stage_counts"]["excluded_by_global_source_cap"] == 2
    assert result["stage_counts"]["final_retained_sources"] == 12
    assert any(row["provider_id"] == "late-high-provider" for row in result["sources"])
    early = next(row for row in result["provider_diagnostics"] if row["source_id"] == "early-provider")
    assert early["raw_items"] == 13
    assert early["accepted_before_global_source_cap"] == 12
    assert early["provider_candidate_cap"] == 12
    assert early["items_not_inspected_after_provider_candidate_cap"] == 1
    late_low = next(row for row in result["provider_diagnostics"] if row["source_id"] == "late-low-provider")
    assert late_low["status"] == "ok"
    assert late_low["raw_items"] == 1
    assert late_low["accepted_before_global_source_cap"] == 1
    assert late_low["retained_after_global_source_cap"] == 0
    assert late_low["excluded_by_global_source_cap"] == 1
    assert not any(row["source_id"] == "late-low-provider" for row in result["failed_source_ids"])


def test_relevance_rejects_guardian_australia_coal_live_blog():
    source = gaza_sources.SourceDefinition(
        source_id="guardian-world",
        name="Guardian World",
        url="https://www.theguardian.com/world/rss",
        type="rss",
        enabled=True,
        publisher="The Guardian",
        reliability_tier="reported-public-source",
        category_hint="conflict",
        region_scope="Gaza",
    )
    item = {
        "title": "Taylor vows to run coal long and hard as election campaign heats up",
        "url": "https://www.theguardian.com/australia-news/live/2026/may/14/election-live-blog",
        "summary_or_snippet": "Live blog includes a sidebar mention of Gaza among many unrelated domestic updates.",
    }
    accepted, reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is False
    assert reason in {"weak_liveblog_unrelated_topic", "gaza_mention_only_without_strong_topic_signal"}


def test_relevance_keeps_gaza_liveblog_with_ground_development_in_summary():
    source = gaza_sources.SourceDefinition(
        source_id="guardian-world",
        name="Guardian World",
        url="https://www.theguardian.com/world/rss",
        type="rss",
        enabled=True,
        publisher="The Guardian",
        reliability_tier="reported-public-source",
        category_hint="conflict",
        region_scope="Gaza",
    )
    item = {
        "title": "Live updates: Middle East conflict",
        "url": "https://www.theguardian.com/world/live/2026/aug/26/middle-east-live-blog",
        "summary_or_snippet": "Israeli strike in Khan Younis kills one and wounds several in Gaza, military says operative was targeted.",
    }
    accepted, reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is True
    assert reason == "strong_summary"


def test_relevance_keeps_guardian_unrwa_archive_story():
    source = gaza_sources.SourceDefinition(
        source_id="guardian-world",
        name="Guardian World",
        url="https://www.theguardian.com/world/rss",
        type="rss",
        enabled=True,
        publisher="The Guardian",
        reliability_tier="reported-public-source",
        category_hint="humanitarian",
        region_scope="Gaza",
    )
    item = {
        "title": "UNRWA says Gaza aid access worsens as archive records grow",
        "url": "https://www.theguardian.com/world/2026/may/14/unrwa-gaza-aid-access",
        "summary_or_snippet": "UNRWA details direct Gaza humanitarian access constraints.",
    }
    accepted, _reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is True


def test_relevance_keeps_bbc_gaza_rubble_story():
    source = gaza_sources.SourceDefinition(
        source_id="bbc-middle-east",
        name="BBC Middle East",
        url="https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
        type="rss",
        enabled=True,
        publisher="BBC News",
        reliability_tier="reported-public-source",
        category_hint="humanitarian",
        region_scope="Gaza",
    )
    item = {
        "title": "Gaza rubble is being reused as families rebuild homes",
        "url": "https://www.bbc.com/news/articles/gaza-rubble-reuse-bricks",
        "summary_or_snippet": "Residents in Gaza describe rebuilding and salvage challenges.",
    }
    accepted, _reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is True


def test_relevance_keeps_traceable_gaza_strike_when_civilian_status_is_unresolved():
    source = gaza_sources.SourceDefinition(
        source_id="ap-middle-east",
        name="AP Middle East",
        url="https://apnews.com/hub/middle-east",
        type="rss",
        enabled=True,
        publisher="Associated Press",
        reliability_tier="reported-public-source",
        category_hint="conflict",
        region_scope="Gaza",
    )
    item = {
        "title": "Israeli strike in Khan Younis kills one and wounds several in Gaza, military says operative was targeted",
        "url": "https://apnews.com/article/gaza-khan-younis-strike-operative-targeted",
        "summary_or_snippet": "One person was killed and several were injured. The military said the target was an operative, while civilian status remained unresolved in the reporting.",
    }

    accepted, reason = gaza_sources.gaza_relevance_decision(item, source)

    assert accepted is True
    assert reason in {"strong_title_or_url", "strong_summary"}


def test_khan_younis_casualty_fixture_survives_full_gaza_path(work_root):
    edition_date = "2026-08-26"
    now = "2026-08-26T12:00:00+00:00"
    record = {
        "source_record_id": "gaza-khan-younis-strike-operative-targeted",
        "title": "Israeli strike in Khan Younis kills one and wounds several in Gaza, military says operative was targeted",
        "url": "https://apnews.com/article/gaza-khan-younis-strike-operative-targeted",
        "publisher": "Associated Press",
        "published_at": "2026-08-26T09:15:00+00:00",
        "retrieved_at": now,
        "summary_or_snippet": "One person was killed and several were injured. The military said the target was an operative, while civilian status remained unresolved in the reporting.",
        "source_type": "rss",
        "region_scope": "Gaza",
        "category_hint": "conflict",
        "reliability_tier": "reported-public-source",
    }

    normalized, warnings, errors = normalize_sources([record], edition_date, now)
    assert not warnings
    assert not errors
    assert len(normalized) == 1
    assert normalized[0]["story_selection_excluded_reason"] in {None, ""}
    assert normalized[0]["canonical_url"].startswith("https://apnews.com/article/")

    deduped_sources, report = gaza_sources.filter_recent_duplicate_sources(work_root, edition_date, normalized)
    assert len(deduped_sources) == 1
    assert report["suppressed_candidate_count"] == 0

    stories, relevance_decisions, _top_story_candidates = curate_stories(deduped_sources, edition_date, now)
    assert any(item["reason"] == "incidental_topic_or_flotilla_only" for item in relevance_decisions)
    assert len(stories) == 1
    assert stories[0]["title"] == normalized[0]["title"]

    dedupe_result = dedupe_public_stories(work_root, "gaza", edition_date, stories)
    assert len(dedupe_result.stories) == 1
    assert dedupe_result.stories[0]["dedupe_classification"] in {"new", "major_update", "continuing_development"}
    assert not dedupe_result.report["duplicate_skipped"]
    assert dedupe_result.report["duplicate_groups"] == []


def test_one_source_one_development_stays_one_candidate():
    source = {
        "source_record_id": "simple-001",
        "title": "Israeli strike kills one in Gaza City",
        "publisher": "WAFA",
        "published_at": "2026-08-28T12:00:00Z",
        "retrieved_at": "2026-08-28T12:05:00Z",
        "summary_or_snippet": "A Palestinian was killed in Gaza City.",
        "source_type": "news",
        "region_scope": "Gaza",
        "category_hint": "conflict",
        "reliability_tier": "reported-public-source",
        "url": "https://example.com/simple",
        "candidate_score": 80,
        "ranking_reasons": ["test"],
        "candidate_score_breakdown": {},
    }
    stories, _, _ = curate_stories([source], "2026-08-28", "2026-08-28T12:00:00Z")
    assert len(stories) == 1
    assert stories[0]["development_type"] == "primary_report"


def test_unicef_multi_development_release_splits_into_two_candidates():
    source = {
        "source_record_id": "unicef-001",
        "title": "Four children killed as separate attacks destroy humanitarian supplies and damage critical water infrastructure in Gaza",
        "publisher": "UNICEF",
        "published_at": "2026-08-28T12:00:00Z",
        "retrieved_at": "2026-08-28T12:05:00Z",
        "summary_or_snippet": (
            "The vital nutrition supplies were intended to support more than 40,500 vulnerable children and 4,900 pregnant and breastfeeding women. "
            "Just one day earlier on August 24, a UNICEF-supported water well and desalination facility in Deir al Balah was severely damaged by a nearby strike. "
            "More than 4,000 people, including around 2,000 children, rely on safe drinking water from the facility. UNICEF and partners are providing emergency water trucking."
        ),
        "source_type": "news",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "reported-public-source",
        "url": "https://example.com/unicef-multi",
        "candidate_score": 90,
        "ranking_reasons": ["test"],
        "candidate_score_breakdown": {},
    }
    stories, _, _ = curate_stories([source], "2026-08-28", "2026-08-28T12:00:00Z")
    assert len(stories) == 2
    assert {story["development_type"] for story in stories} == {"primary_report", "infrastructure_service_loss"}
    water_story = next(story for story in stories if story["development_type"] == "infrastructure_service_loss")
    assert "Deir al-Balah" in water_story["location"]
    assert "water infrastructure" == water_story["affected_system"]


def test_civil_defense_recovery_milestone_keeps_recovered_remains_separate():
    source = {
        "source_record_id": "civil-defense-001",
        "title": "Gaza Civil Defense reports recovered bodies from destroyed homes",
        "publisher": "Gaza Civil Defense",
        "published_at": "2026-08-27T12:00:00Z",
        "retrieved_at": "2026-08-27T12:05:00Z",
        "summary_or_snippet": "Civil Defense reported 233 bodies/remains recovered from 20 destroyed homes, including 73 children and 106 women, with 174 still unrecovered.",
        "source_type": "news",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "reported-public-source",
        "url": "https://example.com/recovery",
        "candidate_score": 88,
        "ranking_reasons": ["test"],
        "candidate_score_breakdown": {},
    }
    stories, _, _ = curate_stories([source], "2026-08-27", "2026-08-27T12:00:00Z")
    assert len(stories) == 2
    recovery_story = next(story for story in stories if story["development_type"] == "recovery_milestone")
    counts = recovery_story["casualty_counts"]
    assert counts["new_deaths"] == 0
    assert counts["recovered_remains"] == 233
    assert counts["still_missing"] == 174
    assert counts["children_recovered"] == 73
    assert counts["women_recovered"] == 106


def test_relevance_rejects_equatorial_guinea_asylum_without_palestinian_anchor():
    source = gaza_sources.SourceDefinition(
        source_id="generic-world",
        name="Generic World",
        url="https://example.com/world/rss.xml",
        type="rss",
        enabled=True,
        publisher="Example",
        reliability_tier="reported-public-source",
        category_hint="rights",
        region_scope="Global",
    )
    item = {
        "title": "UN pleads for Equatorial Guinea not to send US asylum seekers to their home countries",
        "url": "https://example.com/equatorial-guinea-asylum",
        "summary_or_snippet": "The UN says deportees face persecution and life in danger.",
    }
    accepted, reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is False
    assert reason == "rejected_no_palestinian_anchor"


def test_relevance_rejects_generic_un_human_rights_without_palestinian_anchor():
    source = gaza_sources.SourceDefinition(
        source_id="generic-un",
        name="Generic UN",
        url="https://example.com/un/rss.xml",
        type="rss",
        enabled=True,
        publisher="Example",
        reliability_tier="reported-public-source",
        category_hint="rights",
        region_scope="Global",
    )
    item = {
        "title": "UN experts warn of refoulement risks in global deportation cases",
        "url": "https://example.com/un-refoulement",
        "summary_or_snippet": "A broad human rights statement unrelated to this publication scope.",
    }
    accepted, reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is False
    assert reason == "rejected_no_palestinian_anchor"


def test_relevance_keeps_foreign_protest_when_directly_tied_to_palestinian_accountability():
    source = gaza_sources.SourceDefinition(
        source_id="global-rights",
        name="Global Rights",
        url="https://example.com/global/rss.xml",
        type="rss",
        enabled=True,
        publisher="Example",
        reliability_tier="reported-public-source",
        category_hint="rights",
        region_scope="Global",
    )
    item = {
        "title": "Foreign protest targets legal accountability for Palestinian detainees",
        "url": "https://example.com/protest-palestinian-detainees",
        "summary_or_snippet": "Protesters demand accountability tied to Israeli detention policy affecting Palestinians.",
    }
    accepted, reason = gaza_sources.gaza_relevance_decision(item, source)
    assert accepted is True
    assert reason == "palestinian_development_material"


def test_feed_html_summary_is_cleaned_to_plain_text():
    cleaned = gaza_sources.clean_feed_text(
        "&lt;p&gt;Aid update in Gaza&lt;/p&gt; &lt;a href='https://x'&gt;Continue reading...&lt;/a&gt; &#x27;quoted&#x27;"
    )
    assert "<p>" not in cleaned
    assert "</p>" not in cleaned
    assert "<a href=" not in cleaned
    assert "Continue reading..." not in cleaned
    assert "'quoted'" in cleaned


def test_tiered_sources_yml_loads(work_root):
    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """tiers:
  official_humanitarian:
    - source_id: ocha
      name: OCHA
      url: https://example.com/ocha.xml
      type: rss
      enabled: true
      publisher: OCHA
      reliability_tier: official-humanitarian-source
      category_hint: humanitarian
      region_scope: Gaza
      source_group: institutional
""",
        encoding="utf-8",
    )
    sources = gaza_sources.load_sources_config(path)
    assert len(sources) == 1
    assert sources[0].source_tier == "official_humanitarian"
    assert sources[0].source_group == "institutional"


def test_low_relevance_symbolic_sports_item_is_downgraded():
    ranked = gaza_sources.rank_gaza_candidates(
        [
            {
                "title": "Football star waves Palestinian flag after final",
                "summary_or_snippet": "Sports celebration with symbolic flag mention.",
                "category_hint": "culture",
                "publisher": "Example",
                "reliability_tier": "reported-public-source",
                "published_at": "2026-05-07T08:00:00+00:00",
            },
            {
                "title": "Aid convoy reaches Gaza hospitals after ceasefire talks",
                "summary_or_snippet": "Humanitarian and ceasefire development in Gaza.",
                "category_hint": "humanitarian",
                "publisher": "Reuters",
                "reliability_tier": "reported-public-source",
                "published_at": "2026-05-07T09:00:00+00:00",
            },
        ],
        "2026-05-07",
    )
    by_title = {row["title"]: row for row in ranked}
    assert by_title["Football star waves Palestinian flag after final"]["relevance_band"] == "low"
    assert by_title["Aid convoy reaches Gaza hospitals after ceasefire talks"]["relevance_band"] == "core"
    assert by_title["Aid convoy reaches Gaza hospitals after ceasefire talks"]["candidate_score"] > by_title["Football star waves Palestinian flag after final"]["candidate_score"]


def test_disabled_diagnostics_manual_states_skipped_in_collection(work_root, monkeypatch):
    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: disabled-src
    name: Disabled
    url: https://example.com/disabled.xml
    type: rss
    enabled: false
    source_state: disabled
    publisher: Disabled
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
  - source_id: diag-src
    name: Diag
    url: https://example.com/diag.xml
    type: rss
    enabled: false
    source_state: diagnostics_only
    publisher: Diag
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
  - source_id: manual-src
    name: Manual
    url: https://example.com/manual.xml
    type: rss
    enabled: false
    source_state: manual_only
    publisher: Manual
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
  - source_id: enabled-src
    name: Enabled
    url: https://example.com/enabled.xml
    type: rss
    enabled: true
    source_state: enabled
    publisher: Enabled
    reliability_tier: reported-public-source
    category_hint: humanitarian
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda source_id, url, timeout=20: (
            {
                "ok": True,
                "source_id": source_id,
                "url": url,
                "status_code": 200,
                "failure_reason": None,
                "exception_type": None,
                "tls_error": False,
                "backend_used": "python",
                "content_type": "application/rss+xml",
                "content_encoding": "",
                "content_bytes": _rss_payload([{"title": "Gaza aid update", "url": "https://ok.example/a", "published_at": "2026-05-07T00:00:00+00:00", "summary_or_snippet": "aid"}]),
                "content_text": None,
            }
            if url.endswith("enabled.xml")
            else pytest.fail("only enabled source should be fetched")
        ),
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 1
    assert result["stage_counts"]["providers_attempted"] == 1
    assert len(result["skipped_providers"]) == 3


def test_health_checker_recommendations_for_404_and_403_401(monkeypatch):
    class S:
        def __init__(self, source_id: str, source_state: str = "enabled", url: str = "https://x"):
            self.source_id = source_id
            self.source_tier = "tier"
            self.source_state = source_state
            self.url = url
            self.type = "rss"

    monkeypatch.setattr(check_gaza_sources, "load_sources_config", lambda _p: [S("s404"), S("s403"), S("s401"), S("ok")])

    def fake_fetch(source_id: str, url: str, timeout: int = 20):
        if "s404" in url:
            return {"ok": False, "source_id": source_id, "url": url, "status_code": 404, "failure_reason": "HTTPError: 404", "exception_type": "HTTPError", "tls_error": False, "backend_used": "python"}
        if "s403" in url:
            return {"ok": False, "source_id": source_id, "url": url, "status_code": 403, "failure_reason": "HTTPError: 403", "exception_type": "HTTPError", "tls_error": False, "backend_used": "python"}
        if "s401" in url:
            return {"ok": False, "source_id": source_id, "url": url, "status_code": 401, "failure_reason": "HTTPError: 401", "exception_type": "HTTPError", "tls_error": False, "backend_used": "python"}
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Gaza aid", "url": "https://ok.example/a", "published_at": "2026-05-07T00:00:00Z", "summary_or_snippet": "aid"}]),
            "content_text": None,
        }

    monkeypatch.setattr(check_gaza_sources, "fetch_feed_payload", fake_fetch)
    monkeypatch.setattr(
        check_gaza_sources,
        "load_sources_config",
        lambda _p: [
            S("s404", url="https://x/s404"),
            S("s403", url="https://x/s403"),
            S("s401", url="https://x/s401"),
            S("ok", url="https://x/ok"),
        ],
    )
    report = check_gaza_sources.build_report()
    by_id = {row["source_id"]: row for row in report["providers"]}
    assert by_id["s404"]["recommendation"] == "disabled_dead_source"
    assert by_id["s403"]["recommendation"] == "manual_only_or_diagnostics_only"
    assert by_id["s401"]["recommendation"] == "manual_only_or_diagnostics_only"
    assert by_id["ok"]["status"] == "ok"


def test_fetch_payload_tls_failure_classified(monkeypatch):
    class FakeUrlErr(Exception):
        pass

    monkeypatch.setenv("GAZA_FETCH_BACKEND", "python")
    monkeypatch.setattr(
        gaza_sources.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] boom")),
    )
    payload = gaza_sources.fetch_feed_payload("x", "https://example.com/rss.xml")
    assert payload["ok"] is False
    assert payload["tls_error"] is True
    assert payload["failure_reason"] == gaza_sources.TLS_FAILURE_REASON


def test_fetch_payload_auto_uses_curl_after_python_tls_failure(monkeypatch):
    monkeypatch.setenv("GAZA_FETCH_BACKEND", "auto")
    monkeypatch.setenv("GAZA_ALLOW_CURL_NO_REVOKE", "1")
    monkeypatch.setattr(
        gaza_sources.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] boom")),
    )

    class Proc:
        returncode = 0
        stdout = _rss_payload([{"title": "Gaza aid", "url": "https://ok.example/a", "published_at": "2026-05-07T00:00:00Z", "summary_or_snippet": "aid"}])
        stderr = b""

    monkeypatch.setattr(gaza_sources.subprocess, "run", lambda *args, **kwargs: Proc())
    payload = gaza_sources.fetch_feed_payload("x", "https://example.com/rss.xml")
    assert payload["ok"] is True
    assert payload["backend_used"] == "curl"


def test_fetch_payload_no_curl_fallback_when_python_backend_forced(monkeypatch):
    monkeypatch.setenv("GAZA_FETCH_BACKEND", "python")
    monkeypatch.setattr(
        gaza_sources.urllib.request,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] boom")),
    )
    payload = gaza_sources.fetch_feed_payload("x", "https://example.com/rss.xml")
    assert payload["ok"] is False
    assert payload["backend_used"] == "python"


def test_checker_and_collection_use_shared_fetch_helper(work_root, monkeypatch):
    write_config(work_root)
    calls: list[tuple[str, str]] = []

    def fake_payload(source_id: str, url: str, timeout: int = 20):
        calls.append((source_id, url))
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Aid convoys enter Gaza", "url": "https://valid.test/gaza-aid", "published_at": "2026-05-07T08:00:00+00:00", "summary_or_snippet": "Humanitarian update for Gaza."}]),
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_payload)
    monkeypatch.setattr(check_gaza_sources, "fetch_feed_payload", fake_payload)
    monkeypatch.setattr(check_gaza_sources, "ROOT", work_root)

    collect = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=0, prefer_manual=False)
    report = check_gaza_sources.build_report()
    assert collect["source_count"] >= 1
    assert report["providers_attempted"] >= 1
    assert len(calls) >= 2


def test_guardian_gaza_title_item_accepted(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Gaza aid access worsens, says aid agencies", "url": "https://www.theguardian.com/world/2026/may/15/gaza-aid", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "Humanitarian update."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 1


def test_google_news_query_provider_builds_wrapper_feed_and_extracts_canonical_url(work_root, monkeypatch):
    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: elpais-query
    name: EL PAIS Query
    query: site:english.elpais.com Gaza heatwave water displacement
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: EL PAIS English
    reliability_tier: reported-public-source
    category_hint: humanitarian_conditions
    region_scope: Gaza
""",
        encoding="utf-8",
    )

    def fake_fetch(source_id, url, timeout=20):
        assert source_id == "elpais-query"
        assert url.startswith("https://news.google.com/rss/search?q=")
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([
                {
                    "title": "Heatwave in Gaza tents",
                    "url": "https://news.google.com/rss/articles/abc123?url=https%3A%2F%2Fenglish.elpais.com%2Finternational%2F2026%2F07%2F03%2Fgaza-heatwave.html",
                    "published_at": "2026-05-07T08:00:00+00:00",
                    "summary_or_snippet": "Heat and water shortages in Gaza.",
                }
            ]),
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", max_sources=12, min_sources=1, prefer_manual=False)

    assert result["ok"] is True
    assert result["source_count"] == 1
    record = result["sources"][0]
    assert record["traceability_note"]
    assert record["attribution_mode"] == "reported_public_source"
    assert record["claim_status"] == "reported_public_source"
    record = result["sources"][0]
    assert record["canonical_url"] == "https://english.elpais.com/international/2026/07/03/gaza-heatwave.html"
    assert record["collector_source_type"] == "google_news_rss"
    assert record["provider_id"] == "elpais-query"


def test_aljazeera_gaza_or_palestine_context_item_accepted(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Palestine aid talks continue as Gaza hospitals collapse", "url": "https://www.aljazeera.com/news/2026/05/07/palestine-aid-gaza", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "Aid and war context."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 1


ALJAZEERA_WRAPPER_TOKEN = "CBMiOpaqueMladenovProductionArticleId"
ALJAZEERA_WRAPPER_URL = f"https://news.google.com/rss/articles/{ALJAZEERA_WRAPPER_TOKEN}?oc=5"
ALJAZEERA_ARTICLE_URL = "https://www.aljazeera.com/news/2026/8/28/board-of-peace-envoy-mladenov-warns-gaza-ceasefire-risks-collapse"
ALJAZEERA_FEED_TITLE = "Board of Peace envoy Mladenov warns Gaza ceasefire risks 'collapse' - Al Jazeera"
ALJAZEERA_FEED_SUMMARY = "Board of Peace envoy Mladenov warns Gaza ceasefire risks 'collapse' Al Jazeera"
ALJAZEERA_ARTICLE_TEXT = (
    "The Board of Peace has determined the mechanism for deploying the International Stabilization Force in Gaza "
    "and its deployment locations. Advance elements should arrive soon."
)
ALJAZEERA_ARTICLE_FIXTURE = Path(__file__).parent / "fixtures" / "aljazeera_mladenov_article.html"


def _google_wrapper_html() -> str:
    return (
        f'<html><body><div data-n-a-id="{ALJAZEERA_WRAPPER_TOKEN}" '
        'data-n-a-ts="1782841548" data-n-a-sg="mock-signature"></div></body></html>'
    )


def test_aljazeera_board_of_peace_isf_article_enrichment_adds_material_facts(work_root, monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: aljazeera-board-of-peace-isf-query
    name: Al Jazeera Board of Peace ISF Query
    query: site:aljazeera.com Gaza Mladenov "Board of Peace" "International Stabilization Force" deployment
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: Al Jazeera
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "aljazeera-board-of-peace-isf-query",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {
                        "title": ALJAZEERA_FEED_TITLE,
                        "url": ALJAZEERA_WRAPPER_URL,
                        "published_at": "2026-08-28T16:48:33+00:00",
                        "summary_or_snippet": ALJAZEERA_FEED_SUMMARY,
                    }
                ]
            ),
            "content_text": None,
        },
    )
    rpc_requests = []
    article_fetches = []
    rpc_response_text = (Path(__file__).parent / "fixtures" / "google_news_rpc_garturlres_with_amp.txt").read_text(encoding="utf-8")

    class _RpcResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit: int) -> bytes:
            return rpc_response_text.encode("utf-8")

    def fake_rpc_urlopen(request, *args, **kwargs):
        rpc_requests.append(request)
        return _RpcResponse()

    monkeypatch.setattr(food_line_discovery_expansion.urllib.request, "urlopen", fake_rpc_urlopen)

    def fake_article_fetch(url, *_args, **_kwargs):
        article_fetches.append(url)
        if url == ALJAZEERA_WRAPPER_URL:
            wrapper_html = _google_wrapper_html()
            return {
                "ok": True,
                "url": url,
                "final_url": url,
                "status_code": 200,
                "content_type": "text/html; charset=utf-8",
                "content_bytes": wrapper_html.encode("utf-8"),
                "content_text": wrapper_html,
            }
        assert url == ALJAZEERA_ARTICLE_URL
        article_text = ALJAZEERA_ARTICLE_FIXTURE.read_text(encoding="utf-8")
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "content_bytes": article_text.encode("utf-8"),
            "content_text": article_text,
        }

    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-08-29", min_sources=0, prefer_manual=False)

    assert result["source_count"] == 1
    record = result["sources"][0]
    assert len(rpc_requests) == 1
    assert rpc_requests[0].full_url.endswith("/batchexecute?rpcids=Fbv4je")
    assert article_fetches == [ALJAZEERA_WRAPPER_URL, ALJAZEERA_ARTICLE_URL]
    assert record["source_record_id"].startswith("gaza-2026-08-29-aljazeera-board-of-peace-isf-query-")
    assert record["title"] == ALJAZEERA_FEED_TITLE
    assert record["published_at"] == "2026-08-28T16:48:33+00:00"
    assert record["url"] == ALJAZEERA_WRAPPER_URL
    assert record["wrapper_url"] == ALJAZEERA_WRAPPER_URL
    assert record["canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert record["resolved_canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert record["canonicalization_method"] == "google_news_rpc"
    assert record["canonicalization_status"] == "google_news_resolved_same_domain"
    assert record["canonicalization_failure_reason"] is None
    assert record["enrichment_attempted"] is True
    assert record["enrichment_status"] == "enriched_material_excerpt"
    assert record["enrichment_failure_reason"] is None
    assert "mechanism for deploying" in record["summary_or_snippet"].lower()
    assert "deployment locations" in record["summary_or_snippet"].lower()
    assert "advance elements" in record["summary_or_snippet"].lower()
    assert "deployment mechanism" not in record["feed_summary_or_snippet"].lower()
    assert "deployment locations" not in record["feed_summary_or_snippet"].lower()
    assert "advance elements" not in record["feed_summary_or_snippet"].lower()
    assert "mechanism for deploying" in str(record["content_text"]).lower()

    raw_record = json.loads(Path(result["source_file"]).read_text(encoding="utf-8"))[0]
    assert raw_record["wrapper_url"] == ALJAZEERA_WRAPPER_URL
    assert raw_record["canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert "deployment locations" in raw_record["summary_or_snippet"].lower()
    normalized, warnings, errors = normalize_sources(
        [raw_record],
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )
    assert warnings == []
    assert errors == []
    assert normalized[0]["wrapper_url"] == ALJAZEERA_WRAPPER_URL
    assert normalized[0]["canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert "mechanism for deploying" in normalized[0]["summary_or_snippet"].lower()
    assert "deployment locations" in normalized[0]["summary_or_snippet"].lower()
    assert "advance elements" in normalized[0]["summary_or_snippet"].lower()


def test_production_shaped_provider_saturation_still_runs_and_retains_isf_candidate(work_root, monkeypatch):
    specs = [
        ("bbc-middle-east", "BBC News", "rss", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", ""),
        ("wafa-gaza-casualty-locations-query", "WAFA", "google_news_rss", "", "site:hebrew.wafa.ps Al-Qarara Abdeen"),
        ("wafa-gaza-displacement-tent-query", "WAFA", "google_news_rss", "", 'site:wafa.ps Gaza City "Al-Mashahara" tent displaced'),
        ("wafa-gaza-motorbike-query", "WAFA", "google_news_rss", "", 'site:wafa.ps Gaza City "Tal al-Hawa" motorbike'),
        ("wafa-gaza-health-infrastructure-query", "WAFA", "google_news_rss", "", 'site:wafa.ps Gaza "Al-Aqsa" warehouse'),
        ("unicef-gaza-water-query", "UNICEF", "rss", "https://www.unicef.org/press-releases/rss.xml", ""),
        ("guardian-world", "The Guardian", "rss", "https://www.theguardian.com/world/rss", ""),
        ("aljazeera-middle-east", "Al Jazeera", "rss", "https://www.aljazeera.com/xml/rss/all.xml", ""),
        ("ap-gaza-attribution-query", "Associated Press", "rss", "https://apnews.com/hub/middle-east", ""),
        (
            "aljazeera-board-of-peace-isf-query",
            "Al Jazeera",
            "google_news_rss",
            "",
            'site:aljazeera.com Gaza Mladenov "Board of Peace" "International Stabilization Force" deployment',
        ),
    ]
    config_lines = ["sources:"]
    for source_id, publisher, source_type, url, query in specs:
        config_lines.extend(
            [
                f"  - source_id: {source_id}",
                f"    name: {source_id}",
                f"    type: {source_type}",
                "    enabled: true",
                "    source_state: enabled",
                f"    publisher: {publisher}",
                "    reliability_tier: reported-public-source",
                "    category_hint: conflict",
                "    region_scope: Gaza",
            ]
        )
        if url:
            config_lines.append(f"    url: {url}")
        if query:
            config_lines.append(f"    query: {query}")
    config_path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")

    def feed_item(title, url, published_at="2026-08-29T12:00:00+00:00", summary="Palestinian Gaza development reported."):
        return {
            "title": title,
            "url": url,
            "published_at": published_at,
            "summary_or_snippet": summary,
        }

    feed_items = {
        "bbc-middle-east": [
            feed_item(
                "Gaza ceasefire update",
                "https://www.bbc.co.uk/news/articles/gaza-low-priority",
                "2026-08-27T08:00:00+00:00",
                "A Palestinian ceasefire development was reported.",
            ),
            feed_item(
                "Gaza hospital aid strike kills civilians",
                "https://www.bbc.co.uk/news/articles/gaza-hospital-aid",
                summary="Humanitarian aid and civilian harm affected a Gaza hospital.",
            ),
        ],
        "wafa-gaza-casualty-locations-query": [
            feed_item(
                "Three members of the Abdeen family killed near Al-Qarara in Gaza",
                "https://hebrew.wafa.ps/Pages/Details/24313",
                "2026-08-28T12:09:40+00:00",
                "Local sources reported three members of the Abdeen family killed north of Khan Younis.",
            )
        ],
        "wafa-gaza-displacement-tent-query": [
            feed_item(
                "One killed and several injured in Al-Mashahara displacement tent strike in Gaza",
                "https://english.wafa.ps/Pages/Details/174161",
                "2026-08-28T14:39:18+00:00",
                "A tent sheltering displaced people in east Gaza City was struck.",
            )
        ],
        "wafa-gaza-motorbike-query": [
            feed_item(
                "One killed and two injured in Tal al-Hawa motorbike strike in Gaza",
                "https://english.wafa.ps/Pages/Details/174181",
                summary="WAFA reported casualties after a motorbike strike in Gaza City.",
            )
        ],
        "wafa-gaza-health-infrastructure-query": [
            feed_item(
                "Three injured in Al-Aqsa hospital medicine warehouse strike in Gaza",
                "https://english.wafa.ps/Pages/Details/174180",
                summary="A medicine warehouse at Al-Aqsa Martyrs Hospital in Deir al-Balah was struck.",
            )
        ],
        "unicef-gaza-water-query": [
            feed_item(
                "UNICEF reports Gaza water infrastructure destroyed in strike",
                "https://www.unicef.org/press-releases/gaza-water-infrastructure",
                summary="Children and displaced civilians lost humanitarian water access.",
            )
        ],
        "guardian-world": [
            feed_item(
                "Gaza humanitarian aid access worsens after hospital strike",
                "https://www.theguardian.com/world/2026/aug/29/gaza-humanitarian-aid",
                summary="Civilian harm and displacement increased after the strike.",
            )
        ],
        "aljazeera-middle-east": [
            feed_item(
                f"Gaza hospital and humanitarian aid development {index}",
                f"https://www.aljazeera.com/news/2026/8/29/gaza-humanitarian-development-{index}",
                summary="Humanitarian aid, hospital access and civilian harm were reported in Gaza.",
            )
            for index in range(3)
        ],
        "ap-gaza-attribution-query": [
            feed_item(
                "AP reports Gaza strike casualties and hospital pressure",
                "https://apnews.com/article/gaza-strike-casualties-hospital",
                summary="The Associated Press reported civilian harm and hospital pressure in Gaza.",
            )
        ],
        "aljazeera-board-of-peace-isf-query": [
            feed_item(
                ALJAZEERA_FEED_TITLE,
                ALJAZEERA_WRAPPER_URL,
                "2026-08-28T16:48:33+00:00",
                ALJAZEERA_FEED_SUMMARY,
            )
        ],
    }
    feed_calls = []

    def fake_feed_fetch(source_id, _url, *_args, **_kwargs):
        feed_calls.append(source_id)
        return {
            "ok": True,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(feed_items[source_id]),
            "content_text": None,
        }

    resolved_wrappers = []

    def fake_aljazeera_resolver(url):
        resolved_wrappers.append(url)
        return {
            "resolved_url": ALJAZEERA_ARTICLE_URL,
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        }

    article_fetches = []

    def fake_article_fetch(url, *_args, **_kwargs):
        article_fetches.append(url)
        article_text = ALJAZEERA_ARTICLE_FIXTURE.read_text(encoding="utf-8")
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "content_bytes": article_text.encode("utf-8"),
            "content_text": article_text,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_feed_fetch)
    monkeypatch.setattr(gaza_sources, "_resolve_aljazeera_google_news_wrapper", fake_aljazeera_resolver)
    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)
    monkeypatch.setattr(
        gaza_sources,
        "_fetch_wafa_article_payload",
        lambda url, *_args, **_kwargs: {
            "ok": False,
            "url": url,
            "final_url": url,
            "failure_reason": "fixture body unavailable",
            "backend_used": "python",
        },
    )

    result = gaza_sources.collect_gaza_sources(
        work_root,
        "2026-08-29",
        max_sources=12,
        min_sources=0,
        prefer_manual=False,
        write_output=False,
    )

    assert feed_calls == [source_id for source_id, *_rest in specs]
    assert feed_calls[-1] == "aljazeera-board-of-peace-isf-query"
    assert resolved_wrappers == [ALJAZEERA_WRAPPER_URL]
    assert article_fetches == [
        "https://apnews.com/article/gaza-strike-casualties-hospital",
        ALJAZEERA_ARTICLE_URL,
    ]
    assert result["stage_counts"]["accepted_before_global_source_cap"] == 13
    assert result["stage_counts"]["excluded_by_global_source_cap"] == 1
    assert result["source_count"] == 12
    isf_diag = next(
        row for row in result["provider_diagnostics"] if row["source_id"] == "aljazeera-board-of-peace-isf-query"
    )
    assert isf_diag["status"] == "ok"
    assert isf_diag["raw_items"] == 1
    assert isf_diag["accepted_before_global_source_cap"] == 1
    assert isf_diag["retained_after_global_source_cap"] == 1
    assert isf_diag["excluded_by_global_source_cap"] == 0
    isf_record = next(row for row in result["sources"] if row["provider_id"] == "aljazeera-board-of-peace-isf-query")
    assert isf_record["canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert isf_record["enrichment_status"] == "enriched_material_excerpt"
    assert "mechanism for deploying" in isf_record["summary_or_snippet"].lower()
    assert "deployment locations" in isf_record["summary_or_snippet"].lower()
    assert "advance elements" in isf_record["summary_or_snippet"].lower()
    normalized, warnings, errors = normalize_sources(
        result["sources"],
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )
    assert warnings == []
    assert errors == []
    normalized_isf = next(row for row in normalized if row["provider_id"] == "aljazeera-board-of-peace-isf-query")
    assert "advance elements" in normalized_isf["summary_or_snippet"].lower()


def test_aljazeera_article_prose_fixture_recovers_all_required_deployment_facts():
    excerpt = gaza_sources._extract_aljazeera_isf_excerpt(ALJAZEERA_ARTICLE_FIXTURE.read_text(encoding="utf-8"))

    assert "mechanism for deploying" in excerpt.lower()
    assert "deployment locations" in excerpt.lower()
    assert "advance elements should arrive" in excerpt.lower()
    assert "window.pagedata" not in excerpt.lower()
    assert "latest coverage" not in excerpt.lower()


@pytest.mark.parametrize(
    "article_text",
    [
        "<html><script>Board of Peace advance elements</script><body><nav>Board of Peace</nav><p>Generic ceasefire coverage.</p></body></html>",
        "The Board of Peace discussed the International Stabilization Force and the Gaza ceasefire.",
        "The deployment mechanism was discussed, but no location or arrival state was reported.",
    ],
)
def test_aljazeera_article_enrichment_rejects_incomplete_or_script_only_facts(article_text):
    assert gaza_sources._extract_aljazeera_isf_excerpt(article_text) == ""


def test_aljazeera_article_enrichment_accepts_plain_text_only_with_all_three_concepts():
    excerpt = gaza_sources._extract_aljazeera_isf_excerpt(ALJAZEERA_ARTICLE_TEXT)

    assert excerpt == ALJAZEERA_ARTICLE_TEXT


def test_aljazeera_script_heavy_article_without_all_facts_is_not_marked_enriched(monkeypatch):
    source = gaza_sources.SourceDefinition(
        source_id=gaza_sources.ALJAZEERA_ISF_QUERY_SOURCE_ID,
        name="Al Jazeera Board of Peace ISF Query",
        url="https://news.google.com/rss/search?q=aljazeera+gaza+isf",
        type="google_news_rss",
        enabled=True,
        publisher="Al Jazeera",
        reliability_tier="reported-public-source",
        category_hint="conflict",
        region_scope="Gaza",
    )
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_aljazeera_google_news_wrapper",
        lambda _url: {
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
            "resolved_url": ALJAZEERA_ARTICLE_URL,
        },
    )
    article_text = (
        "<html><script>Board of Peace advance elements should arrive soon</script>"
        "<body><div class='wysiwyg wysiwyg--all-content'><p>The Board of Peace discussed Gaza and the ISF.</p>"
        "</div></body></html>"
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda _url: {
            "ok": True,
            "final_url": ALJAZEERA_ARTICLE_URL,
            "content_text": article_text,
        },
    )

    record = gaza_sources.normalize_rss_item(
        {
            "title": ALJAZEERA_FEED_TITLE,
            "url": ALJAZEERA_WRAPPER_URL,
            "published_at": "2026-08-28T16:48:33+00:00",
            "summary_or_snippet": ALJAZEERA_FEED_SUMMARY,
        },
        source,
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )

    assert record is not None
    assert record["enrichment_attempted"] is True
    assert record["enrichment_status"] == "insufficient_article_content"
    assert record["enrichment_failure_reason"] == "article body did not contain all required deployment-state facts"
    assert record["content_text"] is None
    assert record["summary_or_snippet"] == ALJAZEERA_FEED_SUMMARY


def test_aljazeera_wrapper_retries_transient_timeout_then_resolves(monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    fetch_attempts = []
    sleep_delays = []

    def fake_article_fetch(url, *_args, **_kwargs):
        fetch_attempts.append(url)
        if len(fetch_attempts) == 1:
            return {
                "ok": False,
                "url": url,
                "final_url": url,
                "status_code": None,
                "content_type": "",
                "content_bytes": None,
                "content_text": None,
                "failure_reason": "URLError: <urlopen error _ssl.c:1015: The handshake operation timed out>",
            }
        wrapper_html = _google_wrapper_html()
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "content_bytes": wrapper_html.encode("utf-8"),
            "content_text": wrapper_html,
        }

    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", sleep_delays.append)
    monkeypatch.setattr(food_line_discovery_expansion, "_google_news_rpc_request", lambda *_args: (ALJAZEERA_ARTICLE_URL, ""))

    result = gaza_sources._resolve_aljazeera_google_news_wrapper(ALJAZEERA_WRAPPER_URL)

    assert fetch_attempts == [ALJAZEERA_WRAPPER_URL, ALJAZEERA_WRAPPER_URL]
    assert sleep_delays == [1.0]
    assert result["resolved_url"] == ALJAZEERA_ARTICLE_URL
    assert result["canonicalization_status"] == "google_news_resolved_same_domain"
    assert result["failure_reason"] == ""


def test_aljazeera_wrapper_does_not_retry_deterministic_rpc_failure(monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    fetch_attempts = []

    def fake_article_fetch(url, *_args, **_kwargs):
        fetch_attempts.append(url)
        wrapper_html = _google_wrapper_html()
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "content_bytes": wrapper_html.encode("utf-8"),
            "content_text": wrapper_html,
        }

    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", lambda _delay: pytest.fail("deterministic parser failures must not sleep"))
    monkeypatch.setattr(food_line_discovery_expansion, "_google_news_rpc_request", lambda *_args: ("", "rpc_without_article_url"))

    result = gaza_sources._resolve_aljazeera_google_news_wrapper(ALJAZEERA_WRAPPER_URL)

    assert fetch_attempts == [ALJAZEERA_WRAPPER_URL]
    assert result["resolved_url"] == ""
    assert result["canonicalization_status"] == "google_news_failed_no_resolved_url"
    assert result["failure_reason"] == "no_resolved_url"


def test_aljazeera_wrapper_resolution_failure_keeps_feed_fallback(work_root, monkeypatch):
    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: aljazeera-board-of-peace-isf-query
    name: Al Jazeera Board of Peace ISF Query
    query: site:aljazeera.com Gaza Mladenov "Board of Peace" "International Stabilization Force" deployment
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: Al Jazeera
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "aljazeera-board-of-peace-isf-query",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {
                        "title": ALJAZEERA_FEED_TITLE,
                        "url": ALJAZEERA_WRAPPER_URL,
                        "published_at": "2026-08-28T16:48:33+00:00",
                        "summary_or_snippet": ALJAZEERA_FEED_SUMMARY,
                    }
                ]
            ),
            "content_text": None,
        },
    )
    fetch_attempts = []
    sleep_delays = []

    def failing_article_fetch(*_args, **_kwargs):
        fetch_attempts.append(len(fetch_attempts) + 1)
        return {
            "ok": False,
            "url": ALJAZEERA_WRAPPER_URL,
            "final_url": ALJAZEERA_WRAPPER_URL,
            "status_code": None,
            "content_type": "",
            "content_bytes": None,
            "content_text": None,
            "failure_reason": f"TimeoutError: wrapper timed out on attempt {fetch_attempts[-1]}",
        }

    monkeypatch.setattr(gaza_sources, "fetch_article_payload", failing_article_fetch)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", sleep_delays.append)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-08-29", min_sources=0, prefer_manual=False)

    assert result["source_count"] == 1
    record = result["sources"][0]
    assert fetch_attempts == [1, 2, 3]
    assert sleep_delays == [1.0, 2.0]
    assert record["canonical_url"] == gaza_sources.canonicalize_url(ALJAZEERA_WRAPPER_URL)
    assert record["canonicalization_status"] == "google_news_failed_fetch_error"
    assert "wrapper timed out on attempt 3" in record["canonicalization_failure_reason"]
    assert record["enrichment_attempted"] is False
    assert record["enrichment_status"] == "skipped_canonical_resolution_failed"
    assert record["summary_or_snippet"] == ALJAZEERA_FEED_SUMMARY
    assert record["feed_summary_or_snippet"] == ALJAZEERA_FEED_SUMMARY
    assert record["content_text"] is None


def test_aljazeera_wrapper_rejects_wrong_domain_resolution(work_root, monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: aljazeera-board-of-peace-isf-query
    name: Al Jazeera Board of Peace ISF Query
    query: site:aljazeera.com Gaza Mladenov "Board of Peace" "International Stabilization Force" deployment
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: Al Jazeera
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "aljazeera-board-of-peace-isf-query",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {
                        "title": ALJAZEERA_FEED_TITLE,
                        "url": ALJAZEERA_WRAPPER_URL,
                        "published_at": "2026-08-28T16:48:33+00:00",
                        "summary_or_snippet": ALJAZEERA_FEED_SUMMARY,
                    }
                ]
            ),
            "content_text": None,
        },
    )
    fetched = []

    def fake_article_fetch(url, *_args, **_kwargs):
        fetched.append(url)
        assert url == ALJAZEERA_WRAPPER_URL
        wrapper_html = _google_wrapper_html()
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "content_bytes": wrapper_html.encode("utf-8"),
            "content_text": wrapper_html,
        }

    monkeypatch.setattr(
        food_line_discovery_expansion,
        "_google_news_rpc_request",
        lambda *_args: ("https://attacker.example/not-al-jazeera", ""),
    )
    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-08-29", min_sources=0, prefer_manual=False)

    record = result["sources"][0]
    assert fetched == [ALJAZEERA_WRAPPER_URL]
    assert record["url"] == ALJAZEERA_WRAPPER_URL
    assert record["wrapper_url"] == ALJAZEERA_WRAPPER_URL
    assert record["canonical_url"] == gaza_sources.canonicalize_url(ALJAZEERA_WRAPPER_URL)
    assert record["resolved_canonical_url"] is None
    assert record["canonicalization_status"] == "rejected_wrong_publisher_domain"
    assert "not aljazeera.com" in record["canonicalization_failure_reason"]
    assert record["enrichment_attempted"] is False
    assert record["enrichment_status"] == "skipped_canonical_resolution_failed"
    assert record["summary_or_snippet"] == ALJAZEERA_FEED_SUMMARY
    assert record["content_text"] is None


def test_aljazeera_article_fetch_failure_keeps_feed_fallback(work_root, monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    path = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: aljazeera-board-of-peace-isf-query
    name: Al Jazeera Board of Peace ISF Query
    query: site:aljazeera.com Gaza Mladenov "Board of Peace" "International Stabilization Force" deployment
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: Al Jazeera
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "aljazeera-board-of-peace-isf-query",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {
                        "title": ALJAZEERA_FEED_TITLE,
                        "url": ALJAZEERA_WRAPPER_URL,
                        "published_at": "2026-08-28T16:48:33+00:00",
                        "summary_or_snippet": ALJAZEERA_FEED_SUMMARY,
                    }
                ]
            ),
            "content_text": None,
        },
    )
    fetched = []
    monkeypatch.setattr(
        food_line_discovery_expansion,
        "_google_news_rpc_request",
        lambda *_args: (ALJAZEERA_ARTICLE_URL, ""),
    )

    def fake_article_fetch(url, *_args, **_kwargs):
        fetched.append(url)
        if url == ALJAZEERA_WRAPPER_URL:
            wrapper_html = _google_wrapper_html()
            return {
                "ok": True,
                "url": url,
                "final_url": url,
                "status_code": 200,
                "content_type": "text/html; charset=utf-8",
                "content_bytes": wrapper_html.encode("utf-8"),
                "content_text": wrapper_html,
            }
        assert url == ALJAZEERA_ARTICLE_URL
        return {
            "ok": False,
            "url": url,
            "final_url": url,
            "status_code": 403,
            "content_type": "text/html",
            "content_bytes": None,
            "content_text": None,
            "failure_reason": "HTTPError: 403 Forbidden",
        }

    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-08-29", min_sources=0, prefer_manual=False)

    record = result["sources"][0]
    assert fetched == [ALJAZEERA_WRAPPER_URL, ALJAZEERA_ARTICLE_URL]
    assert record["canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert record["resolved_canonical_url"] == ALJAZEERA_ARTICLE_URL
    assert record["enrichment_attempted"] is True
    assert record["enrichment_status"] == "failed_article_fetch"
    assert record["enrichment_failure_reason"] == "HTTPError: 403 Forbidden"
    assert record["summary_or_snippet"] == ALJAZEERA_FEED_SUMMARY
    assert record["feed_summary_or_snippet"] == ALJAZEERA_FEED_SUMMARY
    assert record["content_text"] is None


def test_ordinary_google_news_source_keeps_existing_wrapper_behavior(monkeypatch):
    source = gaza_sources.SourceDefinition(
        source_id="bbc-gaza-query",
        name="BBC Gaza Query",
        url="https://news.google.com/rss/search?q=site%3Abbc.com+Gaza",
        type="google_news_rss",
        enabled=True,
        publisher="BBC",
        reliability_tier="reported-public-source",
        category_hint="humanitarian_conditions",
        region_scope="Gaza",
    )
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_aljazeera_google_news_wrapper",
        lambda *_args, **_kwargs: pytest.fail("ordinary providers must not invoke Al Jazeera resolution"),
    )

    record = gaza_sources.normalize_rss_item(
        {
            "title": "Aid convoys enter Gaza - BBC",
            "url": ALJAZEERA_WRAPPER_URL,
            "published_at": "2026-08-29T10:00:00+00:00",
            "summary_or_snippet": "Aid convoys entered Gaza through the crossing.",
        },
        source,
        "2026-08-29",
        "2026-08-29T12:00:00+00:00",
    )

    assert record is not None
    assert record["url"] == ALJAZEERA_WRAPPER_URL
    assert record["wrapper_url"] == ALJAZEERA_WRAPPER_URL
    assert record["canonical_url"] == gaza_sources.canonicalize_url(ALJAZEERA_WRAPPER_URL)
    assert record["canonicalization_status"] == "wrapper_without_extractable_canonical"
    assert "enrichment_status" not in record


def test_unchanged_prior_board_of_peace_story_remains_suppressible(work_root):
    prior_manifest = work_root / "output" / "dispatches" / "gaza" / "editions" / "2026-08-27" / "sources_manifest.json"
    prior_manifest.parent.mkdir(parents=True, exist_ok=True)
    prior = {
        "source_record_id": "gaza-2026-08-27-board-of-peace-existing",
        "title": "Board of Peace outlines Gaza deployment roadmap",
        "url": "https://www.aljazeera.com/news/2026/8/27/board-of-peace-outlines-gaza-deployment-roadmap",
        "canonical_url": "https://www.aljazeera.com/news/2026/8/27/board-of-peace-outlines-gaza-deployment-roadmap",
        "publisher": "Al Jazeera",
        "published_at": "2026-08-27T10:00:00+00:00",
        "retrieved_at": "2026-08-27T11:00:00+00:00",
        "summary_or_snippet": "The Board of Peace outlined its existing Gaza deployment roadmap.",
        "category_hint": "conflict",
    }
    prior_manifest.write_text(json.dumps([prior], indent=2), encoding="utf-8")
    candidate = {
        **prior,
        "source_record_id": "gaza-2026-08-29-board-of-peace-unchanged",
        "retrieved_at": "2026-08-29T11:00:00+00:00",
    }

    annotated, report = gaza_sources.filter_recent_duplicate_sources(work_root, "2026-08-29", [candidate])

    assert len(annotated) == 1
    assert annotated[0]["story_selection_excluded_reason"] == "duplicate_recent_story"
    assert report["suppressed_candidate_count"] == 1
    assert report["kept_candidate_count"] == 0


def test_unrelated_live_blog_with_incidental_gaza_rejected(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Australia election live blog", "url": "https://example.com/live", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "Incidental mention of Gaza in unrelated politics thread."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 0
    diag = next(d for d in result["provider_diagnostics"] if d.get("source_id") == "test-rss")
    assert diag["rejected_counts"]["rejected_low_relevance"] >= 1


def test_provider_rejection_diagnostics_and_examples_present(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {"title": "", "url": "https://example.com/missing-title", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "x"},
                    {"title": "Gaza update missing date", "url": "https://example.com/missing-date", "published_at": "", "summary_or_snippet": "x"},
                    {"title": "Gaza aid update", "url": "https://example.com/ok", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "x"},
                ]
            ),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    diag = next(d for d in result["provider_diagnostics"] if d.get("source_id") == "test-rss")
    assert diag["raw_items"] == 3
    assert diag["accepted"] == 1
    assert diag["rejected_counts"]["rejected_missing_title"] >= 1
    assert diag["rejected_counts"]["rejected_missing_published_at"] >= 1
    assert len(diag["top_rejected_examples"]) >= 1
    assert len(result["top_rejected_examples"]) >= 1


def test_low_relevance_gaza_term_enters_review_queue_not_accepted(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Election live blog: sports star waves Palestinian flag", "url": "https://example.com/sports-live", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "Incidental Gaza mention in unrelated coverage."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 0
    assert len(result["review_candidates"]) >= 1
    assert result["review_candidates"][0]["rejection_reason"] in {"rejected_low_relevance", "rejected_off_topic"}


def test_no_palestinian_anchor_rejection_counted_and_not_in_review_queue(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {
                        "title": "UN pleads for Equatorial Guinea not to send US asylum seekers to their home countries",
                        "url": "https://example.com/equatorial-guinea-asylum",
                        "published_at": "2026-05-07T10:00:00+00:00",
                        "summary_or_snippet": "General refoulement and human rights concerns in an unrelated case.",
                    }
                ]
            ),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 0
    assert result["rejected_by_reason"]["rejected_no_palestinian_anchor"] == 1
    assert result["review_candidates"] == []

def test_off_topic_without_gaza_palestine_not_in_review_queue(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Quarterly shipping forecast", "url": "https://example.com/shipping", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "No relevant geography."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 0
    assert result["review_candidates"] == []


def test_weak_date_but_relevant_enters_review_queue(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Gaza aid corridor update", "url": "https://example.com/gaza-aid", "published_at": "unknown", "summary_or_snippet": "Humanitarian update."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 0
    assert any(item["rejection_reason"] == "rejected_weak_date_basis" for item in result["review_candidates"])


def test_generic_iran_lebanon_story_without_palestinian_relevance_rejected(work_root, monkeypatch):
    write_config(work_root)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "source_id": "test-rss",
            "url": "https://example.com/rss.xml",
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([{"title": "Iran and Lebanon trade talks expand", "url": "https://example.com/iran-lebanon", "published_at": "2026-05-07T10:00:00+00:00", "summary_or_snippet": "No local relevance."}]),
            "content_text": None,
        },
    )
    result = gaza_sources.collect_gaza_sources(work_root, "2026-05-07", min_sources=0, prefer_manual=False)
    assert result["source_count"] == 0
    assert result["review_candidates"] == []


WAFA_FIXTURE_DIR = Path(__file__).parent / "fixtures"
WAFA_WRAPPER_BASE = "https://news.google.com/rss/articles/"
WAFA_CASES = {
    "qarara": {
        "title": "שלושה תושבים נהרגו בתקיפת כוחות הכיבוש מצפון־מזרח לח'אן יונס - WAFA",
        "wrapper": f"{WAFA_WRAPPER_BASE}CBMiWafaQarara?oc=5",
        "article": "https://hebrew.wafa.ps/Pages/Details/24313",
        "published_at": "2026-08-28T12:09:40+00:00",
        "fixture": "wafa_al_qarara_abdeen.html",
        "needle": "עאבדין",
    },
    "mashahara": {
        "title": "Palestinian killed, others injured in displacement tent in Gaza City - WAFA",
        "wrapper": f"{WAFA_WRAPPER_BASE}CBMiWafaMashahara?oc=5",
        "article": "https://english.wafa.ps/Pages/Details/174161",
        "published_at": "2026-08-28T17:37:00+00:00",
        "fixture": "wafa_al_mashahara.html",
        "needle": "al-mashahara",
    },
    "tal": {
        "title": "Palestinian killed, two injured in Tal al-Hawa motorbike strike in Gaza City - WAFA",
        "wrapper": f"{WAFA_WRAPPER_BASE}CBMiWafaTalAlHawa?oc=5",
        "article": "https://english.wafa.ps/Pages/Details/174181",
        "published_at": "2026-08-29T15:00:00+00:00",
        "fixture": "wafa_tal_al_hawa.html",
        "needle": "tal al-hawa",
    },
    "aqsa": {
        "title": "Three Palestinians injured in strike on medicine warehouse at Gaza hospital - WAFA",
        "wrapper": f"{WAFA_WRAPPER_BASE}CBMiWafaAlAqsa?oc=5",
        "article": "https://english.wafa.ps/Pages/Details/174180",
        "published_at": "2026-08-29T16:21:00+00:00",
        "fixture": "wafa_al_aqsa_warehouse.html",
        "needle": "medicine warehouse inside al-aqsa martyrs hospital",
    },
}


def _wafa_source(source_id: str = "wafa-gaza-casualty-locations-query") -> gaza_sources.SourceDefinition:
    return gaza_sources.SourceDefinition(
        source_id=source_id,
        name="WAFA Gaza Test Query",
        url="",
        query="site:wafa.ps Gaza test",
        type="google_news_rss",
        enabled=True,
        publisher="WAFA",
        reliability_tier="reported-public-source",
        category_hint="conflict",
        region_scope="Gaza",
        source_group="major_ground_development",
    )


@pytest.mark.parametrize("case_name", ["qarara", "mashahara", "tal", "aqsa"])
def test_wafa_article_prose_fixtures_extract_target_material_without_template_noise(case_name):
    case = WAFA_CASES[case_name]
    article_html = (WAFA_FIXTURE_DIR / case["fixture"]).read_text(encoding="utf-8")

    excerpt = gaza_sources._extract_wafa_article_excerpt(article_html)

    assert case["needle"] in excerpt.lower()
    assert "latest news and site navigation" not in excerpt.lower()
    assert "templatenoise" not in excerpt.lower()


def test_wafa_google_wrapper_accepts_only_article_detail_in_publisher_family(monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    wrapper = WAFA_CASES["aqsa"]["wrapper"]
    article = WAFA_CASES["aqsa"]["article"]
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "final_url": wrapper,
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": b"<html></html>",
            "content_text": "<html></html>",
        },
    )
    monkeypatch.setattr(
        food_line_discovery_expansion,
        "_resolve_google_news_wrapper",
        lambda *_args, **_kwargs: (
            article,
            "",
            True,
            {"google_news_resolution_status": "resolved_same_domain", "google_news_rpc_url": article},
        ),
    )

    result = gaza_sources._resolve_wafa_google_news_wrapper(wrapper)

    assert result["resolved_url"] == article
    assert result["canonicalization_method"] == "google_news_rpc"
    assert result["canonicalization_status"] == "google_news_resolved_same_domain"


@pytest.mark.parametrize(
    ("candidate", "expected_status"),
    [
        ("https://attacker.example/Pages/Details/174180", "rejected_wrong_publisher_domain"),
        ("https://english.wafa.ps/Pages/LastNews", "rejected_non_article_url"),
        ("", "google_news_failed_no_resolved_url"),
    ],
)
def test_wafa_google_wrapper_rejects_wrong_domain_listing_and_unresolved(monkeypatch, candidate, expected_status):
    from bluefern_dispatches import food_line_discovery_expansion

    wrapper = WAFA_CASES["aqsa"]["wrapper"]
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "final_url": wrapper,
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": b"<html></html>",
            "content_text": "<html></html>",
        },
    )
    monkeypatch.setattr(
        food_line_discovery_expansion,
        "_resolve_google_news_wrapper",
        lambda *_args, **_kwargs: (
            candidate,
            "no_resolved_url" if not candidate else "",
            True,
            {
                "google_news_resolution_status": "failed_no_resolved_url" if not candidate else "resolved_same_domain",
                "google_news_rpc_url": candidate,
                "rejection_reason": "no_resolved_url" if not candidate else "",
            },
        ),
    )

    result = gaza_sources._resolve_wafa_google_news_wrapper(wrapper)

    assert result["resolved_url"] == ""
    assert result["canonicalization_status"] == expected_status


def test_wafa_bounded_queries_discover_and_enrich_all_four_regression_cases(work_root, monkeypatch):
    config = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """sources:
  - source_id: wafa-gaza-casualty-locations-query
    name: WAFA Gaza Al-Qarara Casualty Query
    query: site:hebrew.wafa.ps אל־קרארה עאבדין
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: WAFA
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
  - source_id: wafa-gaza-displacement-tent-query
    name: WAFA Gaza Displacement Tent Query
    query: site:wafa.ps Gaza City \"Al-Mashahara\" tent displaced
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: WAFA
    reliability_tier: reported-public-source
    category_hint: displacement
    region_scope: Gaza
  - source_id: wafa-gaza-motorbike-query
    name: WAFA Gaza Motorbike Query
    query: site:wafa.ps Gaza City \"Tal al-Hawa\" (motorcycle OR motorbike)
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: WAFA
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
  - source_id: wafa-gaza-health-infrastructure-query
    name: WAFA Gaza Health Infrastructure Query
    query: site:wafa.ps Gaza (\"Al-Aqsa\" OR \"Deir al-Balah\") (hospital OR warehouse)
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: WAFA
    reliability_tier: reported-public-source
    category_hint: health_infrastructure
    region_scope: Gaza
""",
        encoding="utf-8",
    )

    def feed_payload(case_names):
        items = [
            {
                "title": WAFA_CASES[name]["title"],
                "url": WAFA_CASES[name]["wrapper"],
                "published_at": WAFA_CASES[name]["published_at"],
                "summary_or_snippet": WAFA_CASES[name]["title"],
            }
            for name in case_names
        ]
        items.append(
            {
                "title": "European football result",
                "url": f"{WAFA_WRAPPER_BASE}CBMiWafaUnrelated?oc=5",
                "published_at": "2026-08-29T10:00:00+00:00",
                "summary_or_snippet": "Club match coverage without Palestinian or Gaza relevance.",
            }
        )
        return _rss_payload(items)

    feed_urls = []

    def fake_feed_fetch(_source_id, url, *_args, **_kwargs):
        feed_urls.append(url)
        if "%D7%90%D7%9C" in url:
            case_names = ["qarara"]
        elif "Al-Mashahara" in url:
            case_names = ["mashahara"]
        elif "Tal+al-Hawa" in url:
            case_names = ["tal"]
        else:
            case_names = ["aqsa"]
        return {
            "ok": True,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": feed_payload(case_names),
            "content_text": None,
        }

    by_wrapper = {case["wrapper"]: case for case in WAFA_CASES.values()}
    by_article = {case["article"]: case for case in WAFA_CASES.values()}
    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_feed_fetch)
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_wafa_google_news_wrapper",
        lambda url: {
            "resolved_url": by_wrapper[url]["article"],
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        },
    )

    def fake_article_fetch(url, *_args, **_kwargs):
        case = by_article[url]
        article_html = (WAFA_FIXTURE_DIR / case["fixture"]).read_text(encoding="utf-8")
        return {
            "ok": True,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": article_html.encode("utf-8"),
            "content_text": article_html,
        }

    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fake_article_fetch)

    result = gaza_sources.collect_gaza_sources(work_root, "2026-08-29", min_sources=0, prefer_manual=False)

    assert result["source_count"] == 4
    hebrew_feed_url = next(url for url in feed_urls if "%D7%90%D7%9C" in url)
    assert "hl=he" in hebrew_feed_url
    assert "gl=IL" in hebrew_feed_url
    assert "ceid=IL:he" in hebrew_feed_url
    assert result["rejected_by_reason"].get("rejected_low_relevance", 0) + result["rejected_by_reason"].get(
        "rejected_no_palestinian_anchor", 0
    ) == 4
    assert all(row["url"].startswith(WAFA_WRAPPER_BASE) for row in result["sources"])
    assert all(row["canonicalization_status"] == "google_news_resolved_same_domain" for row in result["sources"])
    assert all(row["article_fetch_status"] == "article_prose_extracted" for row in result["sources"])
    assert all(row["content_available"] is True for row in result["sources"])
    assert {row["canonical_url"] for row in result["sources"]} == set(by_article)
    for case in WAFA_CASES.values():
        record = next(row for row in result["sources"] if row["canonical_url"] == case["article"])
        assert record["wrapper_url"] == case["wrapper"]
        assert case["needle"] in record["content_text"].lower()
        assert case["needle"] in record["summary_or_snippet"].lower()

    normalized, warnings, errors = normalize_sources(
        result["sources"],
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )
    assert warnings == []
    assert errors == []
    assert len(normalized) == 4
    for case in WAFA_CASES.values():
        record = next(row for row in normalized if row["canonical_url"] == case["article"])
        assert case["needle"] in record["summary_or_snippet"].lower()


def test_wafa_article_fetch_failure_keeps_truthful_title_summary_fallback(monkeypatch):
    case = WAFA_CASES["aqsa"]
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_wafa_google_news_wrapper",
        lambda _url: {
            "resolved_url": case["article"],
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda *_args, **_kwargs: {
            "ok": False,
            "final_url": case["article"],
            "failure_reason": "TimeoutError: bounded article fetch timed out",
        },
    )
    item = {
        "title": case["title"],
        "url": case["wrapper"],
        "published_at": case["published_at"],
        "summary_or_snippet": "Title-only Google News summary.",
    }

    record = gaza_sources.normalize_rss_item(
        item,
        _wafa_source("wafa-gaza-health-infrastructure-query"),
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )

    assert record is not None
    assert record["summary_or_snippet"] == "Title-only Google News summary."
    assert record["feed_summary_or_snippet"] == "Title-only Google News summary."
    assert record["content_text"] is None
    assert record["content_available"] is False
    assert record["article_fetch_attempted"] is True
    assert record["article_fetch_status"] == "failed_article_fetch"
    assert "timed out" in record["article_fetch_failure_reason"]


def test_wafa_collection_fails_closed_when_wrapper_has_no_trusted_canonical(work_root, monkeypatch):
    config = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """sources:
  - source_id: wafa-gaza-health-infrastructure-query
    name: WAFA Gaza Health Infrastructure Query
    query: site:wafa.ps Gaza hospital warehouse
    type: google_news_rss
    enabled: true
    source_state: enabled
    publisher: WAFA
    reliability_tier: reported-public-source
    category_hint: health_infrastructure
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    case = WAFA_CASES["aqsa"]
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload(
                [
                    {
                        "title": case["title"],
                        "url": case["wrapper"],
                        "published_at": case["published_at"],
                        "summary_or_snippet": case["title"],
                    }
                ]
            ),
            "content_text": None,
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_wafa_google_news_wrapper",
        lambda _url: {
            "resolved_url": "",
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_failed_no_resolved_url",
            "failure_reason": "no_resolved_url",
        },
    )

    result = gaza_sources.collect_gaza_sources(work_root, "2026-08-29", min_sources=0, prefer_manual=False)

    assert result["source_count"] == 0
    assert result["rejected_by_reason"]["rejected_untrusted_canonical"] == 1
    diag = result["provider_diagnostics"][0]
    assert diag["canonicalization_failures"] == [
        {
            "title": case["title"],
            "wrapper_url": case["wrapper"],
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_failed_no_resolved_url",
            "canonicalization_failure_reason": "no_resolved_url",
        }
    ]


def test_wafa_article_fetch_uses_existing_curl_backend_after_python_tls_failure(monkeypatch):
    article = WAFA_CASES["aqsa"]["article"]
    article_html = (WAFA_FIXTURE_DIR / WAFA_CASES["aqsa"]["fixture"]).read_bytes()
    marker = b"\nBLUEFERN_WAFA_FINAL_URL:"

    monkeypatch.delenv("GAZA_FETCH_BACKEND", raising=False)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda *_args, **_kwargs: {
            "ok": False,
            "final_url": article,
            "failure_reason": "SSLCertVerificationError: certificate verify failed",
        },
    )

    class _CurlResult:
        returncode = 0
        stdout = article_html + marker + article.encode("utf-8")
        stderr = b""

    monkeypatch.setattr(gaza_sources.subprocess, "run", lambda *_args, **_kwargs: _CurlResult())

    payload = gaza_sources._fetch_wafa_article_payload(article)

    assert payload["ok"] is True
    assert payload["backend_used"] == "curl"
    assert payload["final_url"] == article
    assert "Al-Aqsa Martyrs Hospital" in payload["content_text"]


def test_wafa_target_events_are_separate_and_preserve_attribution_and_counts():
    sources = []
    for index, (case_name, case) in enumerate(WAFA_CASES.items(), start=1):
        article_html = (WAFA_FIXTURE_DIR / case["fixture"]).read_text(encoding="utf-8")
        excerpt = gaza_sources._extract_wafa_article_excerpt(article_html)
        sources.append(
            {
                "source_record_id": f"wafa-{case_name}",
                "provider_id": {
                    "qarara": "wafa-gaza-casualty-locations-query",
                    "mashahara": "wafa-gaza-displacement-tent-query",
                    "tal": "wafa-gaza-motorbike-query",
                    "aqsa": "wafa-gaza-health-infrastructure-query",
                }[case_name],
                "title": case["title"],
                "publisher": "WAFA",
                "published_at": case["published_at"],
                "retrieved_at": "2026-08-29T18:00:00+00:00",
                "summary_or_snippet": excerpt,
                "source_type": "rss",
                "region_scope": "Gaza",
                "category_hint": "conflict",
                "reliability_tier": "reported-public-source",
                "url": case["wrapper"],
                "canonical_url": case["article"],
                "candidate_score": 90 - index,
                "ranking_reasons": ["test"],
                "candidate_score_breakdown": {},
            }
        )

    stories, rejected, _ = curate_stories(sources, "2026-08-29", "2026-08-29T18:00:00+00:00")

    assert not any(item["action"] == "rejected" for item in rejected)
    assert len(stories) == 4
    assert {story["location"] for story in stories} == {
        "Al-Qarara, north of Khan Younis",
        "Al-Mashahara, east of Gaza City",
        "Tal al-Hawa, Gaza City",
        "Al-Aqsa Martyrs Hospital, Deir al-Balah",
    }
    qarara = next(story for story in stories if "Al-Qarara" in story["location"])
    mashahara = next(story for story in stories if "Al-Mashahara" in story["location"])
    aqsa = next(story for story in stories if "Al-Aqsa" in story["location"])
    tal = next(story for story in stories if "Tal al-Hawa" in story["location"])
    assert "local sources" in qarara["uncertainty"]
    assert qarara["casualty_counts"] == {"new_deaths": 3}
    assert mashahara["casualty_counts"] == {"new_deaths": 1, "injuries_reported_as": "several"}
    assert aqsa["category"] == "health_infrastructure"
    assert aqsa["affected_system"] == "hospital medicine supplies"
    assert "shortage" not in aqsa["summary"].lower()
    assert tal["casualty_counts"] == {"new_deaths": 1, "new_injuries": 2}
    assert "no cross-source consensus implied" in tal["uncertainty"]


def test_wafa_multi_incident_roundup_splits_only_supported_target_events():
    combined = " ".join(
        gaza_sources._extract_wafa_article_excerpt(
            (WAFA_FIXTURE_DIR / case["fixture"]).read_text(encoding="utf-8")
        )
        for case in WAFA_CASES.values()
    )
    source = {
        "source_record_id": "wafa-roundup",
        "provider_id": "wafa-gaza-motorbike-query",
        "title": "WAFA Gaza casualty and infrastructure roundup",
        "publisher": "WAFA",
        "published_at": "2026-08-29T16:21:00+00:00",
        "retrieved_at": "2026-08-29T18:00:00+00:00",
        "summary_or_snippet": combined,
        "source_type": "rss",
        "region_scope": "Gaza",
        "category_hint": "conflict",
        "reliability_tier": "reported-public-source",
        "url": WAFA_CASES["aqsa"]["wrapper"],
        "candidate_score": 90,
        "ranking_reasons": ["test"],
        "candidate_score_breakdown": {},
    }

    stories, rejected, _ = curate_stories([source], "2026-08-29", "2026-08-29T18:00:00+00:00")

    assert not any(item["action"] == "rejected" for item in rejected)
    assert len(stories) == 4
    assert len({story["location"] for story in stories}) == 4


def test_wafa_tal_al_hawa_conflicting_retained_count_is_explicit_not_harmonized():
    case = WAFA_CASES["tal"]
    wafa_source = {
        "source_record_id": "wafa-tal",
        "provider_id": "wafa-gaza-casualty-locations-query",
        "title": case["title"],
        "publisher": "WAFA",
        "published_at": case["published_at"],
        "retrieved_at": "2026-08-29T18:00:00+00:00",
        "summary_or_snippet": gaza_sources._extract_wafa_article_excerpt(
            (WAFA_FIXTURE_DIR / case["fixture"]).read_text(encoding="utf-8")
        ),
        "source_type": "rss",
        "region_scope": "Gaza",
        "category_hint": "conflict",
        "reliability_tier": "reported-public-source",
        "url": case["wrapper"],
        "candidate_score": 90,
        "ranking_reasons": ["test"],
        "candidate_score_breakdown": {},
    }
    other_source = {
        **wafa_source,
        "source_record_id": "other-tal",
        "provider_id": "other-source",
        "publisher": "Other retained publisher",
        "title": "Two killed and one injured in Tal al-Hawa motorcycle strike",
        "summary_or_snippet": "Two Palestinians were killed and one was injured in a strike on a motorcycle in Tal al-Hawa, Gaza City.",
        "url": "https://example.com/tal-al-hawa-report",
    }

    stories, _, _ = curate_stories([wafa_source, other_source], "2026-08-29", "2026-08-29T18:00:00+00:00")

    wafa_story = next(story for story in stories if story["publisher_names"] == ["WAFA"])
    assert wafa_story["casualty_counts"]["new_deaths"] == 1
    assert wafa_story["casualty_counts"]["new_injuries"] == 2
    assert wafa_story["casualty_counts"]["conflicting_reports"] is True
    assert "other retained publisher reported 2 killed and 1 injured" in wafa_story["summary"].lower()
    assert "conflicting counts remain unresolved" in wafa_story["summary"].lower()


AP_WRAPPER_URL = (
    "https://news.google.com/rss/articles/"
    "CBMinwFBVV95cUxPQ3FmcjlycjlqYTlNSzJueHF3d2ZkRHJ3c2ZEbFJkYzZ6ZERHdVE0WXFlV3Y0Uy10emVoZzV4dlBleGRrbTV6a0Z1WkJPVHBTM1NqdExfRUZvRGk5bHJRdnZSdEdBbXBSN2h4VmE0SzB2emV4NHdQQUZSSWRpUlNGRjNaamtWbEdUbGtoSlNtMzF6TGd0b05SdHMtRTFaYVk?oc=5"
)
AP_ARTICLE_URL = "https://apnews.com/article/middle-east-iran-israel-august-28-2026-6c8334dbec806f41666ff75e728768b8"
AP_ARTICLE_FIXTURE = Path(__file__).parent / "fixtures" / "gaza" / "ap_article_middle_east_aug_28.html"


def _ap_source() -> gaza_sources.SourceDefinition:
    return gaza_sources.SourceDefinition(
        source_id=gaza_sources.AP_ATTRIBUTION_QUERY_SOURCE_ID,
        name="AP Gaza Attribution Query",
        url="",
        query="site:apnews.com Gaza Israeli military acknowledged strikes not aware Khan Younis",
        type="google_news_rss",
        enabled=True,
        publisher="Associated Press",
        reliability_tier="reported-public-source",
        category_hint="conflict",
        region_scope="Gaza",
        source_group="accountability_secondary",
        discovery_role="secondary_accountability",
    )


def _ap_item() -> dict[str, str]:
    return {
        "title": "Israeli strikes kill 5 in Gaza and 3 in West Bank, and other news in the Middle East - AP News",
        "url": AP_WRAPPER_URL,
        "published_at": "2026-08-28T12:00:00Z",
        "summary_or_snippet": "Israeli strikes kill 5 in Gaza and 3 in West Bank, and other news in the Middle East - AP News",
    }


def _mock_ap_shared_resolution(monkeypatch, candidate: str, status: str = "resolved_same_domain") -> None:
    from bluefern_dispatches import food_line_discovery_expansion

    monkeypatch.setattr(
        food_line_discovery_expansion,
        "_resolve_google_news_wrapper",
        lambda *_args, **_kwargs: (
            candidate,
            "" if candidate else "rpc_without_article_url",
            True,
            {
                "google_news_resolution_status": status,
                "google_news_rpc_url": candidate,
                "accepted_candidate_url": candidate,
                "rejection_reason": "" if candidate else "no publisher article URL found",
            },
        ),
    )


def test_ap_opaque_wrapper_resolves_only_to_ap_article_and_preserves_wrapper(monkeypatch):
    _mock_ap_shared_resolution(monkeypatch, AP_ARTICLE_URL)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda url, timeout=20: {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": AP_ARTICLE_FIXTURE.read_bytes(),
            "content_text": AP_ARTICLE_FIXTURE.read_text(encoding="utf-8"),
        },
    )

    record = gaza_sources.normalize_rss_item(_ap_item(), _ap_source(), "2026-08-29", "2026-08-29T18:00:00Z")

    assert record is not None
    assert record["url"] == AP_WRAPPER_URL
    assert record["wrapper_url"] == AP_WRAPPER_URL
    assert record["canonical_url"] == AP_ARTICLE_URL
    assert record["resolved_canonical_url"] == AP_ARTICLE_URL
    assert record["canonicalization_method"] == "google_news_rpc"
    assert record["canonicalization_status"] == "google_news_resolved_same_domain"
    assert record["article_fetch_status"] == "article_prose_extracted"
    assert record["content_available"] is True
    assert record["article_body_length"] == len(record["content_text"])
    assert "resolved canonical publisher URL" in record["traceability_note"]


@pytest.mark.parametrize(
    ("candidate", "expected_status"),
    [
        ("https://example.com/article/ap-copy", "rejected_wrong_publisher_domain"),
        ("https://apnews.com/", "rejected_non_article_url"),
        ("https://apnews.com/hub/israel-hamas-war", "rejected_non_article_url"),
        ("", "google_news_rpc_without_article_url"),
    ],
)
def test_ap_wrapper_resolution_fails_closed_for_untrusted_or_nonarticle_candidates(monkeypatch, candidate, expected_status):
    _mock_ap_shared_resolution(monkeypatch, candidate, "rpc_without_article_url" if not candidate else "resolved_same_domain")

    result = gaza_sources._resolve_ap_google_news_wrapper(AP_WRAPPER_URL)

    assert result["resolved_url"] == ""
    assert result["canonicalization_status"] == expected_status


def test_ap_wrapper_retry_is_bounded_for_transient_network_failure(monkeypatch):
    from bluefern_dispatches import food_line_discovery_expansion

    attempts = []
    sleeps = []

    def fetch(url, timeout=20):
        attempts.append(url)
        if len(attempts) == 1:
            return {"ok": False, "failure_reason": "URLError: handshake operation timed out", "exception_type": "URLError"}
        return {"ok": True, "url": url, "final_url": url, "status_code": 200, "content_bytes": b"wrapper", "content_text": "wrapper"}

    def shared(fetcher, wrapper, **_kwargs):
        fetcher(wrapper)
        return AP_ARTICLE_URL, "", True, {
            "google_news_resolution_status": "resolved_same_domain",
            "google_news_rpc_url": AP_ARTICLE_URL,
        }

    monkeypatch.setattr(food_line_discovery_expansion, "_resolve_google_news_wrapper", shared)
    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fetch)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", sleeps.append)

    result = gaza_sources._resolve_ap_google_news_wrapper(AP_WRAPPER_URL)

    assert result["resolved_url"] == AP_ARTICLE_URL
    assert attempts == [AP_WRAPPER_URL, AP_WRAPPER_URL]
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    ("failure_reason", "expected_attempts", "expected_sleeps"),
    [
        ("URLError: timed out", 3, [1.0, 2.0]),
        ("SSLCertVerificationError: certificate verify failed", 1, []),
    ],
)
def test_ap_wrapper_retry_stops_after_three_transient_failures_and_never_retries_certificate_failures(
    monkeypatch, failure_reason, expected_attempts, expected_sleeps
):
    from bluefern_dispatches import food_line_discovery_expansion

    attempts = []
    sleeps = []

    def fetch(url, timeout=20):
        attempts.append(url)
        return {"ok": False, "failure_reason": failure_reason, "exception_type": "URLError"}

    def shared(fetcher, wrapper, **_kwargs):
        response = fetcher(wrapper)
        return "", response["error"], True, {
            "google_news_resolution_status": "failed_fetch_error",
            "rejection_reason": response["error"],
        }

    monkeypatch.setattr(food_line_discovery_expansion, "_resolve_google_news_wrapper", shared)
    monkeypatch.setattr(gaza_sources, "fetch_article_payload", fetch)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", sleeps.append)

    result = gaza_sources._resolve_ap_google_news_wrapper(AP_WRAPPER_URL)

    assert result["resolved_url"] == ""
    assert len(attempts) == expected_attempts
    assert sleeps == expected_sleeps


def test_ap_article_prose_extraction_uses_real_body_shape_excludes_noise_and_is_bounded():
    html_text = AP_ARTICLE_FIXTURE.read_text(encoding="utf-8").replace(
        "</div>\n    </div>",
        f"<p>{'bounded article prose ' * 1000}</p></div>\n    </div>",
        1,
    )

    prose = gaza_sources._extract_ap_article_prose(html_text)

    assert "two brothers and another relative" in prose.lower()
    assert "military confirmed the gaza strikes" in prose.lower()
    assert "site navigation" not in prose.lower()
    assert "caption noise" not in prose.lower()
    assert "abdeen" not in prose.lower()
    assert "al-qarara" not in prose.lower()
    assert len(prose) <= gaza_sources.AP_ARTICLE_PROSE_MAX_CHARS


def test_ap_fetch_failure_keeps_feed_fallback_and_truthful_diagnostics(monkeypatch):
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_ap_google_news_wrapper",
        lambda _url: {
            "resolved_url": AP_ARTICLE_URL,
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda *_args, **_kwargs: {"ok": False, "failure_reason": "TimeoutError: timed out"},
    )

    record = gaza_sources.normalize_rss_item(_ap_item(), _ap_source(), "2026-08-29", "2026-08-29T18:00:00Z")

    assert record is not None
    assert record["canonical_url"] == AP_ARTICLE_URL
    assert record["summary_or_snippet"] == _ap_item()["summary_or_snippet"]
    assert record["content_text"] is None
    assert record["article_fetch_attempted"] is True
    assert record["article_fetch_status"] == "failed_article_fetch"
    assert record["content_available"] is False


def test_ap_final_redirect_outside_article_domain_is_rejected(monkeypatch):
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_ap_google_news_wrapper",
        lambda _url: {
            "resolved_url": AP_ARTICLE_URL,
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda *_args, **_kwargs: {"ok": True, "final_url": "https://example.com/copied-story", "content_text": "noise"},
    )

    record = gaza_sources.normalize_rss_item(_ap_item(), _ap_source(), "2026-08-29", "2026-08-29T18:00:00Z")

    assert record is not None
    assert record["resolved_canonical_url"] is None
    assert record["canonical_url"] == gaza_sources.canonicalize_url(AP_WRAPPER_URL)
    assert record["article_fetch_status"] == "failed_untrusted_article_redirect"
    assert "canonical publisher resolution was not established" in record["traceability_note"]


def _normalized_ap_record(monkeypatch) -> tuple[dict, list[dict]]:
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_ap_google_news_wrapper",
        lambda _url: {
            "resolved_url": AP_ARTICLE_URL,
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda url, timeout=20: {
            "ok": True,
            "url": url,
            "final_url": url,
            "content_text": AP_ARTICLE_FIXTURE.read_text(encoding="utf-8"),
        },
    )
    raw = gaza_sources.normalize_rss_item(_ap_item(), _ap_source(), "2026-08-29", "2026-08-29T18:00:00Z")
    assert raw is not None
    normalized, warnings, errors = normalize_sources([raw], "2026-08-29", "2026-08-29T18:00:00Z")
    assert errors == []
    assert warnings == []
    return raw, normalized


def test_ap_body_produces_separate_corroboration_and_attribution_fragments_with_shared_provenance(monkeypatch):
    raw, normalized = _normalized_ap_record(monkeypatch)

    fragments = raw["ap_event_fragments"]
    assert [fragment["development_type"] for fragment in fragments] == [
        "casualty_corroboration",
        "military_attribution_follow_up",
    ]
    casualty, attribution = fragments
    assert casualty["casualty_counts"] == {"reported_deaths": 3}
    assert casualty["location"] == "outside Khan Younis"
    assert "Abdeen" not in casualty["summary"]
    assert "Al-Qarara" not in casualty["summary"]
    assert "did not identify" in casualty["uncertainty"]
    assert "two Gaza City strikes" in attribution["summary"]
    assert "not aware of the Khan Younis strike" in attribution["summary"]
    assert attribution["casualty_counts"] == {}
    assert attribution["category"] == "military_conduct_accountability"
    assert len(normalized[0]["ap_event_fragments"]) == 2
    assert normalized[0]["content_text"] == raw["content_text"]
    assert normalized[0]["article_body_length"] == len(raw["content_text"])

    stories, _rejected, _top = curate_stories(normalized, "2026-08-29", "2026-08-29T18:00:00Z")
    assert len(stories) == 2
    assert {story["development_type"] for story in stories} == {
        "casualty_corroboration",
        "military_attribution_follow_up",
    }
    assert all(story["source_urls"] == [AP_WRAPPER_URL] for story in stories)
    assert all(story["source_records"][0]["canonical_url"] == AP_ARTICLE_URL for story in stories)


def test_ap_attribution_follow_up_survives_alongside_overlapping_wafa_casualty(tmp_path, monkeypatch):
    _raw, normalized = _normalized_ap_record(monkeypatch)
    ap_stories, _rejected, _top = curate_stories(normalized, "2026-08-29", "2026-08-29T18:00:00Z")
    wafa_story = {
        **next(story for story in ap_stories if story["development_type"] == "casualty_corroboration"),
        "story_id": "wafa-overlapping-casualty",
        "title": "WAFA reports three Abdeen family members killed near Al-Qarara",
        "summary": "WAFA reported three Abdeen family members killed near Al-Qarara, north of Khan Younis.",
        "publisher_names": ["WAFA"],
        "source_urls": ["https://english.wafa.ps/Pages/Details/160000"],
    }

    result = dedupe_public_stories(tmp_path, "gaza", "2026-08-29", [wafa_story, *ap_stories], dry_run=True)

    assert any(story["development_type"] == "military_attribution_follow_up" for story in result.stories)


def test_ap_unresolved_wrapper_is_rejected_by_collection(tmp_path, monkeypatch):
    config = tmp_path / "data" / "dispatches" / "gaza" / "sources.yml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """sources:
  - source_id: ap-gaza-attribution-query
    name: AP Gaza Attribution Query
    query: site:apnews.com Gaza attribution
    type: google_news_rss
    enabled: true
    publisher: Associated Press
    reliability_tier: reported-public-source
    category_hint: conflict
    region_scope: Gaza
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status_code": 200,
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_payload([_ap_item()]),
            "backend_used": "python",
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_ap_google_news_wrapper",
        lambda _url: {
            "resolved_url": "",
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_rpc_without_article_url",
            "failure_reason": "no publisher article URL found",
        },
    )

    result = gaza_sources.collect_gaza_sources(tmp_path, "2026-08-29", min_sources=0, prefer_manual=False, write_output=False)

    assert result["sources"] == []
    diagnostic = result["provider_diagnostics"][0]
    assert diagnostic["rejected_counts"]["rejected_untrusted_canonical"] == 1
    assert diagnostic["canonicalization_failures"][0]["canonicalization_status"] == "google_news_rpc_without_article_url"


def test_ap_empty_article_body_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(
        gaza_sources,
        "_resolve_ap_google_news_wrapper",
        lambda _url: {
            "resolved_url": AP_ARTICLE_URL,
            "canonicalization_method": "google_news_rpc",
            "canonicalization_status": "google_news_resolved_same_domain",
            "failure_reason": "",
        },
    )
    monkeypatch.setattr(
        gaza_sources,
        "fetch_article_payload",
        lambda url, timeout=20: {"ok": True, "final_url": url, "content_text": "<html><body>feed-title only</body></html>"},
    )

    record = gaza_sources.normalize_rss_item(_ap_item(), _ap_source(), "2026-08-29", "2026-08-29T18:00:00Z")

    assert record["article_fetch_status"] == "insufficient_article_content"
    assert record["content_available"] is False
    assert record["article_body_length"] == 0


def _ocha_source() -> gaza_sources.SourceDefinition:
    return gaza_sources.SourceDefinition(
        source_id="ocha-opt-updates",
        name="OCHA OPT Situation Reports",
        url="https://www.ochaopt.org/publications/situation-reports",
        type="ocha_report_index",
        enabled=True,
        publisher="OCHA",
        reliability_tier="official-humanitarian-source",
        category_hint="humanitarian",
        region_scope="Gaza",
        source_tier="official_humanitarian",
        source_group="institutional",
    )


def _ocha_fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / "gaza" / name).read_text(encoding="utf-8")


def test_ocha_official_listing_discovers_only_report_shaped_candidates():
    items = gaza_sources._discover_ocha_report_items(
        _ocha_fixture("ocha_situation_reports_listing.html"),
        "https://www.ochaopt.org/publications/situation-reports",
    )

    assert items == [
        {
            "title": "Humanitarian Situation Report | 28 August 2026",
            "url": "https://www.ochaopt.org/content/humanitarian-situation-report-28-august-2026",
            "published_at": "2026-08-28T00:00:00+00:00",
            "summary_or_snippet": "Official OCHA humanitarian situation report.",
            "discovery_url": "https://www.ochaopt.org/publications/situation-reports",
        }
    ]
    assert gaza_sources._is_ocha_report_url("https://www.ochaopt.org/") is False
    assert gaza_sources._is_ocha_report_url("https://www.ochaopt.org/publications/situation-reports") is False
    assert gaza_sources._is_ocha_report_url(
        "https://example.com/content/humanitarian-situation-report-28-august-2026"
    ) is False


def test_ocha_healthcare_body_and_fragment_are_scoped_bounded_and_non_casualty():
    fixture = _ocha_fixture("ocha_situation_report_healthcare_aug_28.html")
    fixture = fixture.replace(
        "</div>\n    </main>",
        f"<p>{'unrelated ' * 5000}</p></div>\n    </main>",
    )
    body = gaza_sources._extract_ocha_report_body(fixture)
    excerpt = gaza_sources._extract_ocha_healthcare_excerpt(body)
    fragments = gaza_sources._extract_ocha_healthcare_fragments(excerpt, "2026-08-28T00:00:00+00:00")

    assert len(body) <= gaza_sources.OCHA_REPORT_BODY_MAX_CHARS
    assert "Subscribe Site menu Donate" not in body
    assert "Privacy Contact Careers" not in body
    assert "metadata noise" not in body
    assert "flour deliveries" not in body
    assert "IV solutions had been depleted" in excerpt
    assert "dialysis supplies" in excerpt
    assert "laboratory reagents" in excerpt
    assert "oxygen equipment" in excerpt
    assert "Four health centres" in excerpt
    assert "four to five days" in excerpt
    assert "Between 10 and 16 August" in excerpt
    assert "flour deliveries" not in excerpt
    assert len(excerpt) <= gaza_sources.OCHA_HEALTHCARE_PROSE_MAX_CHARS
    assert len(fragments) == 1
    assert fragments[0]["development_type"] == "healthcare_access_disruption"
    assert fragments[0]["casualty_counts"] == {}


@pytest.mark.parametrize(
    "candidate,expected_status",
    [
        ("https://www.ochaopt.org/", "rejected_non_report_url"),
        ("https://www.ochaopt.org/publications/situation-reports", "rejected_non_report_url"),
        (
            "https://example.com/content/humanitarian-situation-report-28-august-2026",
            "rejected_wrong_publisher_domain",
        ),
    ],
)
def test_ocha_normalization_rejects_untrusted_non_report_candidates(monkeypatch, candidate, expected_status):
    monkeypatch.setattr(
        gaza_sources,
        "_fetch_ocha_article_payload",
        lambda *_args, **_kwargs: pytest.fail("untrusted candidate must not be fetched"),
    )
    record = gaza_sources.normalize_rss_item(
        {
            "title": "Humanitarian Situation Report | 28 August 2026",
            "url": candidate,
            "published_at": "2026-08-28T00:00:00+00:00",
            "summary_or_snippet": "OCHA Gaza health report.",
            "discovery_url": "https://www.ochaopt.org/publications/situation-reports",
        },
        _ocha_source(),
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )

    assert record is not None
    assert record["canonicalization_status"] == expected_status
    assert record["resolved_canonical_url"] is None
    assert record["article_fetch_attempted"] is False
    assert record["article_fetch_status"] == "skipped_canonical_resolution_failed"


def test_ocha_report_fetch_failure_keeps_truthful_listing_fallback(monkeypatch):
    monkeypatch.setattr(
        gaza_sources,
        "_fetch_ocha_article_payload",
        lambda *_args, **_kwargs: {
            "ok": False,
            "failure_reason": "TimeoutError: timed out",
            "final_url": "https://www.ochaopt.org/content/humanitarian-situation-report-28-august-2026",
        },
    )
    record = gaza_sources.normalize_rss_item(
        {
            "title": "Humanitarian Situation Report | 28 August 2026",
            "url": "https://www.ochaopt.org/content/humanitarian-situation-report-28-august-2026",
            "published_at": "2026-08-28T00:00:00+00:00",
            "summary_or_snippet": "Official OCHA situation report.",
            "discovery_url": "https://www.ochaopt.org/publications/situation-reports",
        },
        _ocha_source(),
        "2026-08-29",
        "2026-08-29T18:00:00+00:00",
    )

    assert record is not None
    assert record["summary_or_snippet"] == "Official OCHA situation report."
    assert record["article_fetch_attempted"] is True
    assert record["article_fetch_status"] == "failed_report_fetch"
    assert record["article_fetch_failure_reason"] == "TimeoutError: timed out"
    assert record["content_available"] is False
    assert record["ocha_healthcare_fragments"] == []


def test_ocha_aug_28_report_collects_by_publication_date_and_curates_healthcare_fragment(work_root, monkeypatch):
    config = work_root / "data" / "dispatches" / "gaza" / "sources.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """sources:
  - source_id: ocha-opt-updates
    name: OCHA OPT Situation Reports
    url: https://www.ochaopt.org/publications/situation-reports
    type: ocha_report_index
    enabled: true
    source_state: enabled
    publisher: OCHA
    reliability_tier: official-humanitarian-source
    category_hint: humanitarian
    region_scope: Gaza
    source_tier: official_humanitarian
    source_group: institutional
""",
        encoding="utf-8",
    )
    listing = _ocha_fixture("ocha_situation_reports_listing.html")
    report = _ocha_fixture("ocha_situation_report_healthcare_aug_28.html")
    calls = []

    def fake_fetch(url, timeout=20):
        calls.append(url)
        body = listing if url.endswith("/publications/situation-reports") else report
        return {
            "ok": True,
            "url": url,
            "final_url": url,
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": body.encode("utf-8"),
            "content_text": body,
        }

    monkeypatch.setattr(gaza_sources, "_fetch_ocha_article_payload", fake_fetch)
    monkeypatch.setattr(gaza_sources, "utc_now", lambda: "2026-08-29T18:00:00+00:00")
    result = gaza_sources.collect_gaza_sources(
        work_root,
        "2026-08-29",
        max_sources=12,
        min_sources=1,
        prefer_manual=False,
        write_output=False,
    )

    assert result["ok"] is True
    assert result["source_count"] == 1
    assert calls == [
        "https://www.ochaopt.org/publications/situation-reports",
        "https://www.ochaopt.org/content/humanitarian-situation-report-28-august-2026",
    ]
    assert "https://www.ochaopt.org/updates/rss.xml" not in calls
    record = result["sources"][0]
    assert record["published_at"] == "2026-08-28T00:00:00+00:00"
    assert record["discovery_url"] == "https://www.ochaopt.org/publications/situation-reports"
    assert record["resolved_canonical_url"].endswith("humanitarian-situation-report-28-august-2026")
    assert record["canonicalization_status"] == "resolved_official_report_url"
    assert record["article_fetch_status"] == "healthcare_prose_extracted"
    assert record["content_available"] is True
    diag = next(row for row in result["provider_diagnostics"] if row["source_id"] == "ocha-opt-updates")
    assert diag["raw_items"] == 1
    assert diag["items_in_date_window"] == 1
    assert diag["accepted"] == 1

    normalized, warnings, errors = normalize_sources(
        result["sources"], "2026-08-29", "2026-08-29T18:00:00+00:00"
    )
    assert warnings == []
    assert errors == []
    stories, rejected, _top = curate_stories(normalized, "2026-08-29", "2026-08-29T18:00:00+00:00")
    assert rejected == []
    assert len(stories) == 1
    assert stories[0]["development_type"] == "healthcare_access_disruption"
    assert stories[0]["casualty_counts"] == {}
    assert "flour deliveries" not in stories[0]["summary"]

    late_record = {**result["sources"][0], "retrieved_at": "2026-08-30T18:00:00+00:00"}
    late_normalized, _warnings, late_errors = normalize_sources(
        [late_record], "2026-08-29", "2026-08-30T18:00:00+00:00"
    )
    assert late_errors == []
    assert late_normalized[0]["story_selection_excluded_reason"] == (
        "post-edition-date retrieval excluded from prior-date Gaza rerun"
    )
    late_stories, late_rejected, _top = curate_stories(
        late_normalized, "2026-08-29", "2026-08-30T18:00:00+00:00"
    )
    assert late_stories == []
    assert late_rejected[0]["reason"] == "post-edition-date retrieval excluded from prior-date Gaza rerun"
