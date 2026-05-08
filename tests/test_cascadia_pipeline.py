import json
import urllib.error
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.cascadia_curate import curate_sources
from bluefern_dispatches.cascadia_historical_search import PROVIDER_BACKOFF_UNTIL, GDELTProvider, HistoricalProviderRateLimited, build_queries, dedupe_records, retrieve_historical_sources
from bluefern_dispatches.cascadia_ingest import ingest_sources, load_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import render_cascadia_edition, refresh_cascadia_archive_pages
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_weekly import aggregate_weekly_curation, containing_week, explicit_week, format_coverage_label, previous_completed_week
from bluefern_dispatches.generator import CASCADIA_LOGO_ASSET, build_site, publish_pages
from bluefern_dispatches.shared_records import update_shared_records

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from run_cascadia_dispatch import completed_week_windows, run_pipeline


@pytest.fixture()
def cascadia_work_root():
    PROVIDER_BACKOFF_UNTIL.clear()
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    (root / "data" / "dispatches" / "cascadia").mkdir(parents=True)
    shutil.copytree(repo / "assets", root / "assets")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "sources.yml", root / "data" / "dispatches" / "cascadia" / "sources.yml")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "historical_sources.yml", root / "data" / "dispatches" / "cascadia" / "historical_sources.yml")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "manual_sources.json", root / "data" / "dispatches" / "cascadia" / "manual_sources.json")
    return root


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_cascadia_sources_yml_loads(cascadia_work_root):
    sources = load_sources(cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources.yml")

    assert sources
    assert any(source["source_id"] == "cascadia-manual" for source in sources)
    assert all("reliability_tier" in source for source in sources)


def test_historical_search_filters_dedupes_and_writes_diagnostics(cascadia_work_root, monkeypatch):
    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            return [
                {
                    "title": "Washington bridge repairs close Spokane route",
                    "url": "https://example.com/wa-bridge?utm=1",
                    "publisher": "Example WA News",
                    "published_at": "2026-04-28T12:00:00Z",
                    "summary_or_snippet": "Infrastructure and transportation officials announced a road closure.",
                    "reliability_tier": "reputable",
                },
                {
                    "title": "Washington bridge repairs close Spokane route",
                    "url": "https://example.com/wa-bridge?utm=2",
                    "publisher": "Example WA News",
                    "published_at": "2026-04-28T12:00:00Z",
                    "summary_or_snippet": "Infrastructure and transportation officials announced a road closure.",
                    "reliability_tier": "reputable",
                },
                {
                    "title": "National sports tournament preview",
                    "url": "https://example.com/sports",
                    "publisher": "National Sports",
                    "published_at": "2026-04-28T12:00:00Z",
                    "summary_or_snippet": "Sports game coverage.",
                },
                {
                    "title": "Oregon wildfire evacuation routes updated",
                    "url": "",
                    "publisher": "Example OR News",
                    "published_at": "2026-04-29T12:00:00Z",
                    "summary_or_snippet": "Wildfire emergency notice.",
                },
            ]

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)
    result = retrieve_historical_sources(cascadia_work_root, *containing_week("2026-04-28"), edition_date="2026-05-03", run_date="2026-05-11")

    assert result["ok"] is True
    assert result["source_count"] == 1
    historical_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-27_2026-05-03" / "historical_sources.json"
    report_path = historical_path.with_name("historical_search_report.json")
    records = read_json(historical_path)
    report = read_json(report_path)
    assert records[0]["source_type"] == "historical_search"
    assert records[0]["provider_id"] == "gdelt"
    assert records[0]["query_used"]
    assert records[0]["url"]
    assert records[0]["state_hint"] == "WA"
    assert report["duplicates_removed"] >= 1
    assert report["exclusion_reasons"]["sports"] >= 1
    assert report["exclusion_reasons"]["missing_url"] >= 1
    assert "cache_hits" in report
    assert "cache_misses" in report
    assert "retry_count" in report
    assert "rate_limit_count" in report
    assert "queries_planned" in report
    assert "queries_skipped_due_to_limit" in report


def test_historical_search_builds_batched_queries(cascadia_work_root):
    config = {
        "query_groups": {
            "max_queries": 2,
            "max_region_terms_per_query": 3,
            "system_terms_per_query": 2,
            "region_terms": ["Washington", "Oregon", "Idaho", "Seattle"],
            "systems_terms": ["public health", "bridge", "wildfire", "housing"],
        }
    }

    queries = build_queries(config)

    assert len(queries) == 2
    assert queries[0].startswith("(Washington OR Oregon OR Idaho)")
    assert '"public health" OR bridge' in queries[0]
    assert "wildfire OR housing" in queries[1]
    assert all(" AND " in query for query in queries)


def test_gdelt_provider_cache_writes_hits_refreshes_and_keys_by_window(cascadia_work_root, monkeypatch):
    calls = 0

    class FakeResponse:
        status = 200

        def __init__(self, payload):
            self.payload = payload
            self.headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return FakeResponse(
            {
                "articles": [
                    {
                        "title": "Washington water utility update",
                        "url": f"https://example.com/water-{calls}",
                        "domain": "example.com",
                        "seendate": "20260428120000",
                        "snippet": "Washington water utility infrastructure update.",
                    }
                ]
            }
        )

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.urllib.request.urlopen", fake_urlopen)
    config = {
        "provider_id": "gdelt",
        "base_url": "https://api.gdeltproject.org/api/v2/doc/doc",
        "delay_seconds": 0,
        "cache_enabled": True,
        "cache_ttl_days": 14,
    }
    provider = GDELTProvider(config, root=cascadia_work_root)
    first = provider.search(*containing_week("2026-04-28"), "Washington AND water", 3)
    first_diag = provider.last_diagnostics
    second = provider.search(*containing_week("2026-04-28"), "Washington AND water", 3)
    second_diag = provider.last_diagnostics
    refreshed = GDELTProvider(config, root=cascadia_work_root, refresh_cache=True).search(*containing_week("2026-04-28"), "Washington AND water", 3)
    different_window = GDELTProvider(config, root=cascadia_work_root).search(*containing_week("2026-05-05"), "Washington AND water", 3)

    assert calls == 3
    assert first[0]["url"] == second[0]["url"]
    assert refreshed[0]["url"] != first[0]["url"]
    assert different_window[0]["url"] != first[0]["url"]
    assert first_diag["cache_miss"] is True
    assert second_diag["cache_hit"] is True
    cache_files = list((cascadia_work_root / "data" / "dispatches" / "cascadia" / "cache" / "gdelt").glob("*.json"))
    assert len(cache_files) >= 2


def test_gdelt_provider_rate_limit_retries_honor_retry_after(cascadia_work_root, monkeypatch):
    calls = 0
    sleeps = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"articles": []}).encode("utf-8")

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, None)
        return FakeResponse()

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.time.sleep", lambda seconds: sleeps.append(seconds))
    provider = GDELTProvider({"provider_id": "gdelt", "delay_seconds": 0, "max_retries": 2, "backoff_base_seconds": 1}, root=cascadia_work_root)

    assert provider.search(*containing_week("2026-04-28"), "Washington AND water", 3) == []
    assert calls == 2
    assert sleeps == [7.0]
    assert provider.last_diagnostics["rate_limit_count"] == 1
    assert provider.last_diagnostics["retry_count"] == 1


def test_gdelt_provider_rate_limit_stops_after_max_retries(cascadia_work_root, monkeypatch):
    sleeps = []

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.time.sleep", lambda seconds: sleeps.append(seconds))
    provider = GDELTProvider({"provider_id": "gdelt", "delay_seconds": 0, "max_retries": 1, "backoff_base_seconds": 2}, root=cascadia_work_root)

    with pytest.raises(HistoricalProviderRateLimited):
        provider.search(*containing_week("2026-04-28"), "Washington AND water", 3)
    assert sleeps == [2]
    assert provider.last_diagnostics["rate_limit_count"] == 2
    assert provider.last_diagnostics["retry_count"] == 1


def test_gdelt_provider_empty_and_non_json_responses_warn(cascadia_work_root, monkeypatch):
    class FakeResponse:
        status = 200

        def __init__(self, body, content_type):
            self.body = body
            self.headers = {"Content-Type": content_type}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body.encode("utf-8")

    responses = [FakeResponse("", "text/plain"), FakeResponse("<html>busy</html>", "text/html")]

    def fake_urlopen(request, timeout):
        return responses.pop(0)

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.urllib.request.urlopen", fake_urlopen)
    config = {"provider_id": "gdelt", "delay_seconds": 0, "cache_enabled": False}
    provider = GDELTProvider(config, root=cascadia_work_root)

    assert provider.search(*containing_week("2026-04-28"), "Washington AND water", 3) == []
    assert "empty response body" in provider.last_diagnostics["warnings"]
    assert provider.search(*containing_week("2026-04-28"), "Washington AND power", 3) == []
    assert any("invalid JSON response" in warning for warning in provider.last_diagnostics["warnings"])


def test_historical_provider_failure_fails_safely(cascadia_work_root, monkeypatch):
    class FailingGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            raise OSError("network unavailable")

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FailingGDELT)
    result = retrieve_historical_sources(cascadia_work_root, *containing_week("2026-04-28"), edition_date="2026-05-03", run_date="2026-05-11")

    assert result["ok"] is True
    assert result["source_count"] == 0
    assert result["warnings"]
    assert read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-27_2026-05-03" / "historical_sources.json") == []


def test_historical_provider_rate_limit_enters_cooldown(cascadia_work_root, monkeypatch):
    class RateLimitedGDELT(GDELTProvider):
        calls = 0

        def search(self, start_date, end_date, query_terms, max_results):
            RateLimitedGDELT.calls += 1
            raise HistoricalProviderRateLimited("HTTP 429 Too Many Requests")

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", RateLimitedGDELT)
    result = retrieve_historical_sources(cascadia_work_root, *containing_week("2026-04-28"), edition_date="2026-05-03", run_date="2026-05-11")
    second = retrieve_historical_sources(cascadia_work_root, *containing_week("2026-04-21"), edition_date="2026-04-26", run_date="2026-05-11")

    assert result["ok"] is True
    assert result["query_count"] == 1
    assert result["report"]["queries_run"][0]["rate_limited"] is True
    assert second["query_count"] == 0
    assert RateLimitedGDELT.calls == 1
    assert "cooling down" in second["warnings"][0]


def test_manual_and_historical_sources_merge_with_source_type_preserved(cascadia_work_root, monkeypatch):
    week_start, week_end = containing_week("2026-04-28")
    manual_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-27_2026-05-03"
    manual_dir.mkdir(parents=True)
    (manual_dir / "manual_sources.json").write_text(
        json.dumps(
            [
                {
                    "title": "Idaho hospital emergency staffing update",
                    "url": "https://example.com/idaho-hospital",
                    "publisher": "Idaho Public Source",
                    "published_at": "2026-04-30T12:00:00Z",
                    "summary_or_snippet": "Hospital and public health agencies described emergency staffing.",
                    "state_hint": "ID",
                    "category_hint": "Healthcare",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            return [
                {
                    "title": "Oregon housing services expand shelter capacity",
                    "url": "https://example.com/oregon-housing",
                    "publisher": "Oregon Public Source",
                    "published_at": "2026-04-29T12:00:00Z",
                    "summary_or_snippet": "Oregon housing and homelessness services added shelter capacity.",
                    "reliability_tier": "reputable",
                }
            ]

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)
    result = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", run_date="2026-05-11")

    assert result["source_count"] == 2
    records = read_json(manual_dir / "historical_sources.json")
    assert {record["source_type"] for record in records} == {"historical_search", "manual"}
    assert {record["provider_id"] for record in records} == {"gdelt", "manual"}


def test_historical_weekly_cli_renders_traceable_story_and_manifests(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)

    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            if start_date.isoformat() == "2026-05-04":
                return [
                    {
                        "title": "Seattle utility warns of power outage resilience work",
                        "url": "https://example.com/seattle-utility",
                        "publisher": "Seattle Public Source",
                        "published_at": "2026-05-05T12:00:00Z",
                        "summary_or_snippet": "Seattle utility crews described power outage resilience work.",
                        "reliability_tier": "reputable",
                    }
                ]
            return []

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)
    code = run_cascadia_dispatch.main(["--weekly-public", "--backfill-weeks", "4", "--date", "2026-05-11", "--historical-search"])

    assert code == 0
    edition_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-10"
    html = (edition_dir / "index.html").read_text(encoding="utf-8")
    manifest = read_json(edition_dir / "edition_manifest.json")
    sources = read_json(edition_dir / "sources_manifest.json")
    curation = read_json(edition_dir / "curation_manifest.json")
    assert "Seattle utility warns of power outage resilience work" in html
    assert "https://example.com/seattle-utility" in html
    assert manifest["historical_search"] is True
    assert manifest["providers_used"] == ["gdelt"]
    assert manifest["query_count"] >= 1
    assert sources[0]["provider_id"] == "gdelt"
    assert sources[0]["query_used"]
    assert curation[0]["source_record_ids"]
    empty_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "No qualifying source-backed Cascadia signals were identified" in empty_html
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    rss = (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")
    assert "The Cascadia Briefing - May 4\u201310, 2026" in archive
    assert "The Cascadia Briefing - Apr 27\u2013May 3, 2026" in rss
    assert "2026-05-07" not in archive


def test_dedupe_records_handles_url_title_and_compound_keys():
    records, duplicates = dedupe_records(
        [
            {"url": "https://example.com/a?x=1", "title": "WA water utility update", "publisher": "Source", "published_at": "2026-05-01T00:00:00Z"},
            {"url": "https://example.com/a?x=2", "title": "WA water utility update", "publisher": "Source", "published_at": "2026-05-01T00:00:00Z"},
            {"url": "https://example.com/b", "title": "WA water utility update", "publisher": "Source", "published_at": "2026-05-01T00:00:00Z"},
        ]
    )

    assert len(records) == 1
    assert duplicates == 2


def test_weekly_window_logic():
    assert tuple(day.isoformat() for day in previous_completed_week("2026-05-11")) == ("2026-05-04", "2026-05-10")
    assert tuple(day.isoformat() for day in previous_completed_week("2026-05-12")) == ("2026-05-04", "2026-05-10")
    assert tuple(day.isoformat() for day in previous_completed_week("2026-05-18")) == ("2026-05-11", "2026-05-17")
    assert tuple(day.isoformat() for day in containing_week("2026-05-06")) == ("2026-05-04", "2026-05-10")
    assert tuple(day.isoformat() for day in explicit_week("2026-05-04", "2026-05-10")) == ("2026-05-04", "2026-05-10")
    with pytest.raises(ValueError):
        explicit_week("2026-05-05", "2026-05-10")
    assert completed_week_windows("2026-05-11", 4) == [
        ("2026-05-04", "2026-05-10", "2026-05-10"),
        ("2026-04-27", "2026-05-03", "2026-05-03"),
        ("2026-04-20", "2026-04-26", "2026-04-26"),
        ("2026-04-13", "2026-04-19", "2026-04-19"),
    ]
    assert format_coverage_label("2026-04-13", "2026-04-19") == "Apr 13\u201319, 2026"
    assert format_coverage_label("2026-04-27", "2026-05-03") == "Apr 27\u2013May 3, 2026"
    assert format_coverage_label("2026-12-28", "2027-01-03") == "Dec 28, 2026\u2013Jan 3, 2027"


def test_ingestion_runs_with_manual_fixture(cascadia_work_root):
    result = ingest_sources(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    assert result["raw_count"] == 3
    raw = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "raw" / "2026-05-03" / "raw_sources.json")
    assert raw[0]["source_record_id"]
    assert raw[0]["url"]


def test_normalization_dedupes_records(cascadia_work_root):
    raw_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "raw" / "2026-05-03"
    raw_dir.mkdir(parents=True)
    duplicate = {
        "source_record_id": "raw-1",
        "source_id": "cascadia-manual",
        "source_name": "Manual",
        "title": "Oregon emergency management preparedness notice",
        "url": "https://www.oregon.gov/oem/",
        "published_at": "2026-05-03T00:00:00Z",
        "retrieved_at": "2026-05-03T01:00:00Z",
        "summary_or_snippet": "Emergency management notice.",
        "raw_payload": {},
        "region_scope": "OR",
        "category_hint": "Public safety",
    }
    (raw_dir / "raw_sources.json").write_text(json.dumps([duplicate, dict(duplicate, source_record_id="raw-2")]), encoding="utf-8")

    result = normalize_sources(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    assert result["normalized_count"] == 1
    assert "deduped duplicate record" in result["warnings"][0]


def test_curation_excludes_sports_and_keeps_public_source_urls(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    result = curate_sources(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    excluded = [story for story in curated if story["excluded_reason"]]
    public = [story for story in curated if story["included_in_public_summary"]]
    assert any(story["excluded_reason"] == "sports" for story in excluded)
    assert public
    assert all(story["source_urls"] for story in public)
    assert all(story["source_record_ids"] for story in public)


def test_render_writes_manifests_links_and_detail_only_outside_public(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    result = render_cascadia_edition(cascadia_work_root, "2026-05-03")

    assert result["ok"] is True
    public_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03"
    detail_dir = cascadia_work_root / "output" / "detail" / "cascadia" / "2026-05-03"
    assert (public_dir / "index.html").exists()
    assert (public_dir / "edition_manifest.json").exists()
    assert (public_dir / "sources_manifest.json").exists()
    assert (public_dir / "curation_manifest.json").exists()
    html = (public_dir / "index.html").read_text(encoding="utf-8")
    assert "The Cascadia Briefing" in html
    assert "Cascadia Signal Pack" in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    curation = read_json(public_dir / "curation_manifest.json")
    assert all("source_record_ids" in story for story in curation)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in (cascadia_work_root / "output" / "site").rglob("*") if path.suffix in {".html", ".json", ".xml", ".css"})
    assert "output/detail" not in public_text
    assert "cascadia_signal_records" not in public_text
    assert (detail_dir / "cascadia_signal_records.json").exists()
    assert (detail_dir / "cascadia_signal_records.csv").exists()
    assert (detail_dir / "cascadia_source_manifest.json").exists()
    assert (detail_dir / "cascadia_category_summary.json").exists()
    assert (detail_dir / "cascadia_category_summary.csv").exists()
    assert (detail_dir / "cascadia_run_manifest.json").exists()
    assert (detail_dir / "cascadian_detail_records.json").exists()
    assert (detail_dir / "cascadian_detail_records.csv").exists()
    edition_manifest = read_json(public_dir / "edition_manifest.json")
    assert edition_manifest["briefing_type"] == "weekly"
    assert edition_manifest["dispatch_slug"] == "cascadia"
    assert edition_manifest["public_name"] == "The Cascadia Briefing"
    public_paths = [path.relative_to(cascadia_work_root / "output" / "site").as_posix() for path in (cascadia_work_root / "output" / "site").rglob("*") if path.is_file()]
    assert not any(path.startswith("detail/") or path.startswith("paid/") for path in public_paths)


def test_weekly_aggregation_filters_dedupes_and_renders(cascadia_work_root):
    start, end = containing_week("2026-05-06")
    records = [
        {
            "source_record_id": "src-in",
            "canonical_url": "https://example.com/weekly",
            "title": "Washington bridge inspection program",
            "publisher": "Source",
            "published_at": "2026-05-04T08:00:00Z",
            "retrieved_at": "2026-05-04T09:00:00Z",
            "text": "Bridge inspection update.",
            "source_id": "cascadia-manual",
            "region_scope": "WA",
            "category_hint": "Transportation",
        },
        {
            "source_record_id": "src-out",
            "canonical_url": "https://example.com/outside",
            "title": "Outside record",
            "publisher": "Source",
            "published_at": "2026-05-11T08:00:00Z",
            "retrieved_at": "2026-05-11T09:00:00Z",
            "text": "Outside the week.",
            "source_id": "cascadia-manual",
            "region_scope": "WA",
            "category_hint": "Transportation",
        },
    ]
    story = {
        "story_id": "story-in",
        "title": "Washington bridge inspection program",
        "summary": "Bridge inspection update.",
        "category": "Transportation",
        "score": 80,
        "source_record_ids": ["src-in"],
        "source_urls": ["https://example.com/weekly"],
        "included_in_public_summary": True,
        "included_in_detail_dataset": True,
        "excluded_reason": None,
        "source_records": [records[0]],
    }
    duplicate = dict(story, story_id="story-dup", score=70)
    outside = dict(story, story_id="story-out", title="Outside record", source_record_ids=["src-out"], source_urls=["https://example.com/outside"], source_records=[records[1]])
    for day, payload in {"2026-05-04": [story, outside], "2026-05-05": [duplicate]}.items():
        normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / day
        curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / day
        normalized_dir.mkdir(parents=True)
        curated_dir.mkdir(parents=True)
        (normalized_dir / "normalized_sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        (curated_dir / "curation_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    aggregate = aggregate_weekly_curation(cascadia_work_root, "2026-05-11", start, end)
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-10",
        run_date="2026-05-11",
        coverage_start="2026-05-04",
        coverage_end="2026-05-10",
        briefing_type="weekly",
    )

    assert aggregate["curated_count"] == 1
    assert result["ok"] is True
    public_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-10"
    html = (public_dir / "index.html").read_text(encoding="utf-8")
    assert "Weekly briefing / May 4\u201310, 2026 / Coverage: 2026-05-04 through 2026-05-10" in html
    assert "https://example.com/weekly" in html
    assert "https://example.com/outside" not in html
    manifest = read_json(public_dir / "edition_manifest.json")
    assert manifest["briefing_type"] == "weekly"
    assert manifest["run_date"] == "2026-05-11"
    assert manifest["coverage_start"] == "2026-05-04"
    assert manifest["coverage_end"] == "2026-05-10"
    assert manifest["coverage_label"] == "May 4\u201310, 2026"
    assert manifest["week_label"] == "2026-W19"
    assert manifest["source_record_ids"] == ["src-in"]
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-10" in archive
    assert "Weekly source-backed regional briefings for Washington, Oregon, and Idaho." not in archive
    assert "Weekly source-backed regional briefings" in (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")


def test_cascadia_archive_recent_and_rss_list_weekly_editions_only(cascadia_work_root):
    editions_root = cascadia_work_root / "output" / "site" / "cascadia" / "editions"
    for day in ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]:
        edition_dir = editions_root / day
        edition_dir.mkdir(parents=True)
        (edition_dir / "index.html").write_text(f"<p>Daily {day}</p>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps({"dispatch_slug": "cascadia", "edition_date": day, "briefing_type": "daily"}, indent=2),
            encoding="utf-8",
        )
    weekly_dates = {
        "2026-05-10": ("2026-05-04", "2026-05-10", "2026-W19", "May 4\u201310, 2026"),
        "2026-05-03": ("2026-04-27", "2026-05-03", "2026-W18", "Apr 27\u2013May 3, 2026"),
        "2026-04-26": ("2026-04-20", "2026-04-26", "2026-W17", "Apr 20\u201326, 2026"),
        "2026-04-19": ("2026-04-13", "2026-04-19", "2026-W16", "Apr 13\u201319, 2026"),
    }
    for edition_date, (coverage_start, coverage_end, week, label) in weekly_dates.items():
        edition_dir = editions_root / edition_date
        edition_dir.mkdir(parents=True)
        (edition_dir / "index.html").write_text(f"<p>Weekly {edition_date}</p>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "cascadia",
                    "edition_date": edition_date,
                    "briefing_type": "weekly",
                    "coverage_start": coverage_start,
                    "coverage_end": coverage_end,
                    "coverage_label": label,
                    "week_label": week,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    written = []
    refresh_cascadia_archive_pages(cascadia_work_root, dry_run=False, written=written)

    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    index = (cascadia_work_root / "output" / "site" / "cascadia" / "index.html").read_text(encoding="utf-8")
    rss = (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")
    for text in [archive, index, rss]:
        for day in ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"]:
            assert day not in text
        for weekly_date in weekly_dates:
            assert weekly_date in text
        for _, _, _, label in weekly_dates.values():
            assert f"The Cascadia Briefing - {label}" in text
    assert "Weekly source-backed regional briefings for Washington, Oregon, and Idaho." not in archive


def test_shared_dispatch_records_include_gaza_cascadia_and_signal_package(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    detail = write_cascadia_signal_package(cascadia_work_root, "2026-05-03")
    result = update_shared_records(cascadia_work_root, "2026-05-03", detail["output_paths"], public_rendered=False)

    assert result["ok"] is True
    records_root = cascadia_work_root / "data" / "records"
    dispatches = read_json(records_root / "dispatches.json")
    editions = read_json(records_root / "editions.json")
    sources = read_json(records_root / "sources.json")
    records = read_json(records_root / "records.json")
    packages = read_json(records_root / "detail_packages.json")
    assert {row["slug"] for row in dispatches} >= {"gaza", "cascadia"}
    assert any(row["internal_name"] == "Cascadia Signal" for row in dispatches)
    assert all("dispatch_id" in row for row in editions)
    assert all("dispatch_id" in row for row in sources)
    assert all("source_ids" in row for row in records)
    assert packages and all(row["public_exposed"] is False for row in packages)


def test_signal_package_movement_fields_first_and_second_run(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    first = write_cascadia_signal_package(cascadia_work_root, "2026-05-03")
    assert first["ok"] is True
    first_records = read_json(cascadia_work_root / "output" / "detail" / "cascadia" / "2026-05-03" / "cascadia_signal_records.json")
    assert all(record["movement_label"] == "New" for record in first_records)
    assert all(record["trend_direction"] == "new" for record in first_records)

    ingest_sources(cascadia_work_root, "2026-05-04")
    normalize_sources(cascadia_work_root, "2026-05-04")
    curate_sources(cascadia_work_root, "2026-05-04")
    second = write_cascadia_signal_package(cascadia_work_root, "2026-05-04")
    second_records = read_json(cascadia_work_root / "output" / "detail" / "cascadia" / "2026-05-04" / "cascadia_signal_records.json")
    assert second["ok"] is True
    assert all("first_seen" in record and "last_seen" in record for record in second_records)
    assert {record["movement_label"] for record in second_records} <= {"New", "Rising", "Falling", "Stable"}
    assert {record["trend_direction"] for record in second_records} <= {"new", "up", "down", "flat"}


def test_generic_build_preserves_real_cascadia_public_edition(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(cascadia_work_root, "2026-05-03")

    result = build_site(cascadia_work_root, backup_root=cascadia_work_root / "backup")

    assert result["ok"] is True
    public_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "Washington bridge inspection program" in public_html
    assert "Oregon emergency management preparedness notice" in public_html
    assert "Launch placeholder" not in public_html
    assert "Placeholder source" not in public_html


def test_pages_publish_copies_real_cascadia_public_edition(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(cascadia_work_root, "2026-05-03")
    old_daily_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-04"
    old_daily_dir.mkdir(parents=True)
    (old_daily_dir / "index.html").write_text("<p>Old daily Cascadia edition</p>", encoding="utf-8")
    (old_daily_dir / "edition_manifest.json").write_text(
        json.dumps({"dispatch_slug": "cascadia", "edition_date": "2026-05-04", "briefing_type": "daily"}, indent=2),
        encoding="utf-8",
    )
    (old_daily_dir / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (old_daily_dir / "curation_manifest.json").write_text("[]", encoding="utf-8")
    pages_repo = cascadia_work_root / "bluefern-dispatches-pages"
    pages_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=pages_repo, check=True, capture_output=True, text=True)
    (pages_repo / ".keep").write_text("keep\n", encoding="utf-8")
    subprocess.run(["git", "add", ".keep"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "Initial Pages repo"], cwd=pages_repo, check=True, capture_output=True, text=True)
    stale_pages_daily_dir = pages_repo / "cascadia" / "editions" / "2026-05-04"
    stale_pages_daily_dir.mkdir(parents=True)
    (stale_pages_daily_dir / "index.html").write_text("<p>Stale daily page</p>", encoding="utf-8")

    result = publish_pages(cascadia_work_root, pages_repo, None, dry_run=False, commit=False, no_push=True, backup_root=cascadia_work_root / "backup")

    assert result["ok"] is True
    pages_html = (pages_repo / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "Washington bridge inspection program" in pages_html
    assert "Oregon emergency management preparedness notice" in pages_html
    assert "Launch placeholder" not in pages_html
    assert "Placeholder source" not in pages_html
    assert 'target="_blank" rel="noopener noreferrer"' in pages_html
    assert (pages_repo / "assets" / CASCADIA_LOGO_ASSET).read_bytes() == (cascadia_work_root / "assets" / CASCADIA_LOGO_ASSET).read_bytes()
    assert (pages_repo / "cascadia" / "assets" / CASCADIA_LOGO_ASSET).read_bytes() == (cascadia_work_root / "assets" / CASCADIA_LOGO_ASSET).read_bytes()
    assert not (pages_repo / "cascadia" / "editions" / "2026-05-04").exists()
    assert result["non_publishable_pages_editions_removed"]
    assert not (pages_repo / "detail").exists()
    assert not (pages_repo / "paid").exists()


def test_daily_and_weekly_public_modes_write_expected_artifacts(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    daily = run_pipeline("2026-05-03", ingest=True, normalize=True, curate=True, render=False, dry_run=False, mode="daily")
    assert daily["ok"] is True
    assert daily["mode"] == "daily"
    assert daily["public_rendered"] is False
    assert daily["raw_count"] == 3
    assert daily["normalized_count"] == 3
    assert daily["curated_count"] == 3
    assert daily["detail_count"] == 3
    assert daily["shared_record_paths"]
    assert not (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()

    weekly = run_pipeline("2026-05-03", ingest=False, normalize=False, curate=False, render=True, dry_run=False, mode="weekly-public")
    assert weekly["ok"] is True
    assert weekly["mode"] == "weekly-public"
    assert weekly["public_rendered"] is True
    assert (cascadia_work_root / "output" / "dispatches" / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").exists()


def test_archive_week_cli_uses_sunday_edition_date(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    ingest_sources(cascadia_work_root, "2026-05-04")
    normalize_sources(cascadia_work_root, "2026-05-04")
    normalized_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-04" / "normalized_sources.json"
    normalized = read_json(normalized_path)
    for record in normalized:
        record["published_at"] = "2026-05-04T08:00:00Z"
    normalized_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    curate_sources(cascadia_work_root, "2026-05-04")

    code = run_cascadia_dispatch.main(["--archive-week", "2026-05-06", "--weekly-public"])

    assert code == 0
    edition_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-10"
    assert (edition_dir / "index.html").exists()
    manifest = read_json(edition_dir / "edition_manifest.json")
    assert manifest["edition_date"] == "2026-05-10"
    assert manifest["coverage_start"] == "2026-05-04"
    assert manifest["coverage_end"] == "2026-05-10"


def test_backfill_weeks_cli_generates_completed_weekly_editions_without_sources(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)

    code = run_cascadia_dispatch.main(["--weekly-public", "--backfill-weeks", "4", "--date", "2026-05-11"])

    assert code == 0
    expected = {
        "2026-05-10": ("2026-05-04", "2026-05-10"),
        "2026-05-03": ("2026-04-27", "2026-05-03"),
        "2026-04-26": ("2026-04-20", "2026-04-26"),
        "2026-04-19": ("2026-04-13", "2026-04-19"),
    }
    for edition_date, (coverage_start, coverage_end) in expected.items():
        edition_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / edition_date
        html = (edition_dir / "index.html").read_text(encoding="utf-8")
        manifest = read_json(edition_dir / "edition_manifest.json")
        assert "Weekly briefing" in html
        assert coverage_start in html
        assert coverage_end in html
        assert "No qualifying source-backed Cascadia signals were identified" in html
        assert manifest["briefing_type"] == "weekly"
        assert manifest["edition_date"] == edition_date
        assert manifest["coverage_start"] == coverage_start
        assert manifest["coverage_end"] == coverage_end
        assert manifest["coverage_label"] == format_coverage_label(coverage_start, coverage_end)
        assert manifest["run_date"] == "2026-05-11"
        assert "source_record_ids" in manifest
        assert "source_urls" in manifest
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert all(edition_date in archive for edition_date in expected)


def test_backfill_weeks_from_existing_editions_preserves_traceability(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    old_daily = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-04-28"
    old_daily.mkdir(parents=True)
    source = {
        "source_record_id": "old-src-1",
        "title": "Idaho water infrastructure funding update",
        "url": "https://example.com/idaho-water",
        "publisher": "Example News",
        "published_at": "2026-04-28T12:00:00Z",
        "retrieved_at": "2026-04-28T13:00:00Z",
        "category_hint": "Infrastructure",
        "region_scope": "ID",
    }
    (old_daily / "sources_manifest.json").write_text(json.dumps([source, dict(source)]), encoding="utf-8")
    (old_daily / "curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "old-story-1",
                    "title": "Idaho water infrastructure funding update",
                    "summary": "Idaho water infrastructure funding update",
                    "category": "Infrastructure",
                    "score": 72,
                    "source_record_ids": ["old-src-1"],
                    "source_urls": ["https://example.com/idaho-water"],
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": True,
                    "excluded_reason": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    empty_daily = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-04-29"
    empty_daily.mkdir(parents=True)
    (empty_daily / "sources_manifest.json").write_text("[]", encoding="utf-8")

    code = run_cascadia_dispatch.main(["--weekly-public", "--backfill-weeks", "1", "--date", "2026-05-04", "--from-existing-editions"])

    assert code == 0
    edition_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03"
    sources = read_json(edition_dir / "sources_manifest.json")
    curation = read_json(edition_dir / "curation_manifest.json")
    html = (edition_dir / "index.html").read_text(encoding="utf-8")
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    rss = (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")

    assert "Idaho water infrastructure funding update" in html
    assert "https://example.com/idaho-water" in html
    assert len(sources) == 1
    assert sources[0]["source_url"] == "https://example.com/idaho-water"
    assert sources[0]["original_source_record_id"] == "old-src-1"
    assert sources[0]["source_type"] == "existing_cascadia_manifest"
    assert sources[0]["weekly_date_basis"] == "published_at"
    assert sources[0]["traceability_note"] == "Derived from prior Cascadia edition manifest; original source URL preserved."
    assert curation[0]["derived_from_edition_date"] == "2026-04-28"
    assert curation[0]["traceability_note"] == "Derived from prior Cascadia edition manifest; original source URL preserved."
    assert "The Cascadia Briefing - Apr 27\u2013May 3, 2026" in archive
    assert "The Cascadia Briefing - Apr 27\u2013May 3, 2026" in rss
    assert "2026-04-28" not in archive
