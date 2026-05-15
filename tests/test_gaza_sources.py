import json
import shutil
import uuid
import gzip
import urllib.error
from pathlib import Path

import pytest

from bluefern_dispatches import gaza_sources
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

    assert kept == []
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

    assert kept == []
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
    assert kept == []
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

    assert kept == []
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

    assert kept == []
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

    assert kept == []
    assert report["suppressed_candidate_count"] == 1
    assert report["suppressed_candidates"][0]["matched_prior_edition"] == "2026-05-10"


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
