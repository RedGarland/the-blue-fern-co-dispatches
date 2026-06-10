import json
import importlib
import re
import urllib.error
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.cascadia_curate import curate_sources, deterministic_summary, why_it_matters
from bluefern_dispatches.cascadia_categories import canonical_category_id, category_label_for
from bluefern_dispatches.cascadia_dates import canonical_date_fields
from bluefern_dispatches.cascadia_fetch import curl_command, fetch_public_url
from bluefern_dispatches.cascadia_historical_search import PROVIDER_BACKOFF_UNTIL, GDELTProvider, HistoricalProviderRateLimited, build_queries, create_manual_source_template, dedupe_records, exclusion_reason, load_historical_config, retrieve_historical_sources, validate_manual_sources
from bluefern_dispatches.cascadia_ingest import ingest_sources, load_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import editorial_checklist, render_cascadia_edition, refresh_cascadia_archive_pages, render_map_html
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_source_registry import collect_registry_sources, load_source_registry, source_operational_state
from bluefern_dispatches.cascadia_weekly import aggregate_weekly_curation, containing_week, explicit_week, format_coverage_label, previous_completed_week
from bluefern_dispatches.generator import CASCADIA_LOGO_ASSET, build_site, discover_public_edition_dates, publish_pages
from bluefern_dispatches.shared_records import update_shared_records

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from run_cascadia_dispatch import completed_week_windows, run_pipeline, run_source_gap_report, write_zero_week_gap_report
import run_cascadia_dispatch
import backfill_cascadia_pressure
from bluefern_dispatches import cascadia_fetch


@pytest.fixture()
def cascadia_work_root():
    PROVIDER_BACKOFF_UNTIL.clear()
    repo = Path(__file__).resolve().parents[1]
    root = repo / "output" / "test-runs" / uuid.uuid4().hex / "repo"
    (root / "data" / "dispatches" / "cascadia").mkdir(parents=True)
    shutil.copytree(repo / "assets", root / "assets")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "sources.yml", root / "data" / "dispatches" / "cascadia" / "sources.yml")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "historical_sources.yml", root / "data" / "dispatches" / "cascadia" / "historical_sources.yml")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "source_registry.yml", root / "data" / "dispatches" / "cascadia" / "source_registry.yml")
    shutil.copy2(repo / "data" / "dispatches" / "cascadia" / "manual_sources.json", root / "data" / "dispatches" / "cascadia" / "manual_sources.json")
    registry_path = root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_lines = registry_path.read_text(encoding="utf-8").splitlines()
    updated_lines = []
    current_fetchable = False
    for line in registry_lines:
        if line.startswith("  - source_id:"):
            current_fetchable = False
        if line.strip() in {"source_type: rss", "source_type: atom", "source_type: alert_feed"}:
            current_fetchable = True
        if current_fetchable and line.strip() == "enabled: true":
            updated_lines.append(line.replace("true", "false"))
            continue
        updated_lines.append(line)
    registry_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return root


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_cascadia_sources_yml_loads(cascadia_work_root):
    sources = load_sources(cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources.yml")

    assert sources
    assert any(source["source_id"] == "cascadia-manual" for source in sources)
    assert all("reliability_tier" in source for source in sources)


def test_cascadia_source_registry_loads_free_sources(cascadia_work_root):
    registry = load_source_registry(cascadia_work_root)

    assert registry
    assert any(source["source_id"] == "wa-doh-newsroom" for source in registry)
    assert any(source["source_id"] == "king-county-news" for source in registry)
    assert any(source["source_id"] == "opb-news-feed" and source["source_type"] == "rss" for source in registry)
    assert any(source["source_id"] == "idaho-puc-news" for source in registry)
    assert any(source["source_id"] == "wa-wsf-service-alerts" for source in registry)
    assert any(source["source_id"] == "or-trimet-alerts" for source in registry)
    assert any(source["source_id"] == "id-valley-regional-transit-alerts" for source in registry)
    assert any(source["source_id"] == "manual-weekly-supplements" and source["tier"] == 4 for source in registry)
    assert all("url" in source for source in registry)
    assert not any("api_key" in json.dumps(source).lower() for source in registry)


def test_historical_query_groups_include_local_region_filters(cascadia_work_root):
    config = load_historical_config(cascadia_work_root)
    queries = build_queries(config)
    joined = "\n".join(queries)

    for region in ["WA", "Washington state", "OR", "ID", "Puget Sound", "Willamette Valley", "Treasure Valley", "Spokane", "Boise", "Portland", "Seattle"]:
        assert region in joined
    assert any("power outage" in query and "water system" in query for query in queries)
    assert any("freight" in query and "supply chain" in query for query in queries)


def test_fetch_backend_python_success(monkeypatch):
    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"articles": []}'

    monkeypatch.setenv("CASCADIA_FETCH_BACKEND", "python")
    monkeypatch.setenv("CASCADIA_ALLOW_CURL_NO_REVOKE", "0")
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())

    result = fetch_public_url("https://example.com/data.json", 5, "TestAgent")

    assert result.ok is True
    assert result.body == '{"articles": []}'
    assert result.diagnostics["fetch_backend"] == "python"
    assert result.diagnostics["fallback_used"] is False


def test_fetch_backend_auto_failure_curl_disabled(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("certificate revocation check failed")

    monkeypatch.setenv("CASCADIA_FETCH_BACKEND", "auto")
    monkeypatch.setenv("CASCADIA_ALLOW_CURL_NO_REVOKE", "0")
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.is_windows", lambda: True)

    result = fetch_public_url("https://example.com/feed.xml", 5, "TestAgent")

    assert result.ok is False
    assert result.diagnostics["fallback_used"] is False
    assert "CASCADIA_ALLOW_CURL_NO_REVOKE=1" in result.diagnostics["recommendation"]


def test_fetch_backend_auto_failure_curl_enabled(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("No connection could be made because the target machine actively refused it")

    class FakeCompleted:
        returncode = 0
        stdout = '{"articles":[{"title":"Washington public health update","url":"https://example.com/wa","domain":"example.com","seendate":"20260421120000","snippet":"Washington public health services update."}]}'
        stderr = "curl ok"

    calls = []

    def fake_run(command, capture_output, text, timeout, check, env=None):
        calls.append(command)
        return FakeCompleted()

    monkeypatch.setenv("CASCADIA_FETCH_BACKEND", "auto")
    monkeypatch.setenv("CASCADIA_ALLOW_CURL_NO_REVOKE", "1")
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.is_windows", lambda: True)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.subprocess.run", fake_run)

    result = fetch_public_url("https://example.com/data.json", 5, "TestAgent")

    assert result.ok is True
    assert result.diagnostics["fallback_used"] is True
    assert result.diagnostics["curl_exit_code"] == 0
    assert "--ssl-no-revoke" in calls[0]
    assert "curl fallback succeeded" in result.diagnostics["recommendation"]


def test_fetch_backend_does_not_fallback_on_non_windows(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("certificate revocation check failed")

    monkeypatch.setenv("CASCADIA_FETCH_BACKEND", "auto")
    monkeypatch.setenv("CASCADIA_ALLOW_CURL_NO_REVOKE", "1")
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.is_windows", lambda: False)

    result = fetch_public_url("https://example.com/feed.xml", 5, "TestAgent")

    assert result.ok is False
    assert result.diagnostics["fallback_used"] is False
    assert result.diagnostics["curl_exit_code"] is None


def test_curl_command_no_revoke_only_when_allowed():
    allowed = curl_command("https://example.com", 7, "Agent", allow_no_revoke=True)
    disallowed = curl_command("https://example.com", 7, "Agent", allow_no_revoke=False)

    assert "--ssl-no-revoke" in allowed
    assert "--ssl-no-revoke" not in disallowed


def test_registry_feed_parses_filters_dedupes_and_warns(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: official-wa-feed
    name: Official WA Feed
    tier: 1
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [transportation, infrastructure]
    reliability_tier: official-public
    publisher: Washington Example Agency
    refresh_mode: archive_limited
    notes: Test feed.
  - source_id: disabled-feed
    name: Disabled Feed
    tier: 3
    source_type: rss
    url: https://example.com/disabled.xml
    enabled: false
    state_scope: WA
    geographic_scope: Washington
    category_hints: [government]
    reliability_tier: test
    publisher: Disabled
    refresh_mode: archive_limited
    notes: Should be skipped.
""",
        encoding="utf-8",
    )
    feed = """<?xml version="1.0"?>
<rss><channel>
  <item><title>Washington bridge repair closes route</title><link>https://example.com/bridge?utm=1</link><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Transportation infrastructure road closure.</description></item>
  <item><title>Washington bridge repair closes route</title><link>https://example.com/bridge?utm=2</link><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Transportation infrastructure road closure.</description></item>
  <item><title>Washington missing url</title><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Transportation infrastructure.</description></item>
  <item><title>Washington bridge old item</title><link>https://example.com/old</link><pubDate>Tue, 21 Apr 2026 12:00:00 GMT</pubDate><description>Transportation infrastructure.</description></item>
  <item><title>Washington emergency management update</title><link>https://example.com/no-date</link><description>Emergency management update.</description></item>
</channel></rss>"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return feed.encode("utf-8")

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)

    week_start, week_end = containing_week("2026-04-28")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    cached = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T13:00:00Z")

    assert calls == ["https://example.com/feed.xml"]
    assert len(result["records"]) == 2
    assert cached["report"]["registry_cache_hits"] == 1
    assert result["records"][0]["source_id"] == "official-wa-feed"
    assert result["records"][0]["tier"] == 1
    assert result["records"][0]["source_type"] == "rss"
    assert result["report"]["registry_exclusion_reasons"]["duplicate"] == 1
    assert result["report"]["registry_exclusion_reasons"]["missing_url"] == 1
    assert result["report"]["registry_exclusion_reasons"]["outside_date_window"] == 1
    assert any("weak date basis" in warning for warning in result["warnings"])


def test_registry_cache_write_creates_nested_source_directory(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: official/wa feed
    name: Official WA Feed
    tier: 1
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [transportation, infrastructure]
    reliability_tier: official-public
    publisher: Washington Example Agency
    refresh_mode: archive_limited
    notes: Test feed.
""",
        encoding="utf-8",
    )
    feed = """<?xml version="1.0"?>
<rss><channel>
  <item><title>Washington bridge infrastructure update</title><link>https://example.com/bridge</link><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Transportation infrastructure update.</description></item>
</channel></rss>"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return feed.encode("utf-8")

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)

    week_start, week_end = containing_week("2026-04-28")
    cache_source_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "cache" / "registry" / "official-wa-feed"
    assert not cache_source_dir.exists()

    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    cached = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T13:00:00Z")

    assert len(result["records"]) == 1
    assert calls == ["https://example.com/feed.xml"]
    assert cache_source_dir.is_dir()
    assert len(list(cache_source_dir.glob("*.json"))) == 1
    assert cached["report"]["registry_cache_hits"] == 1


def test_registry_curl_fallback_output_parses_and_records_diagnostics(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: official-wa-feed
    name: Official WA Feed
    tier: 1
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [transportation, infrastructure]
    reliability_tier: official-public
    publisher: Washington Example Agency
    refresh_mode: archive_limited
    notes: Test feed.
""",
        encoding="utf-8",
    )

    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("certificate revocation check failed")

    class FakeCompleted:
        returncode = 0
        stdout = """<rss><channel><item><title>Washington bridge infrastructure update</title><link>https://example.com/bridge</link><pubDate>Tue, 21 Apr 2026 12:00:00 GMT</pubDate><description>Transportation infrastructure update.</description></item></channel></rss>"""
        stderr = "curl fallback worked"

    monkeypatch.setenv("CASCADIA_FETCH_BACKEND", "auto")
    monkeypatch.setenv("CASCADIA_ALLOW_CURL_NO_REVOKE", "1")
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.is_windows", lambda: True)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.subprocess.run", lambda command, capture_output, text, timeout, check, env=None: FakeCompleted())

    week_start, week_end = containing_week("2026-04-21")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z", refresh_cache=True)

    assert len(result["records"]) == 1
    assert result["records"][0]["url"] == "https://example.com/bridge"
    assert result["report"]["fallback_used"] is True
    assert result["report"]["curl_exit_code"] == 0
    assert result["report"]["fetch_backend"] == "auto"
    assert "curl fallback succeeded" in result["report"]["recommendation"]


def test_registry_official_page_parses_same_domain_links_and_dates(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: official-page
    name: Official Page
    tier: 1
    source_type: official_page
    url: https://example.com/news
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [government]
    reliability_tier: official-public
    publisher: Example Agency
    refresh_mode: archive_limited
    notes: Test page.
""",
        encoding="utf-8",
    )
    page = """
<html><body>
  <nav><a href="/news/archive">Archive</a></nav>
  <main>
    <a href="/news/water-system-update" data-date="2026-04-22">Washington water infrastructure update</a>
    <a href="https://other.example/news">Washington offsite emergency update</a>
    <a href="/news/no-date">Washington public health services briefing</a>
    <a href="/news/sports" data-date="2026-04-22">Washington sports game recap</a>
    <a href="#top">Top</a>
  </main>
  <footer><a href="/contact">Contact</a></footer>
</body></html>
"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return page.encode("utf-8")

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())

    week_start, week_end = containing_week("2026-04-21")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")

    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["url"] == "https://example.com/news/water-system-update"
    assert record["title"] == "Washington water infrastructure update"
    assert record["published_at"] == "2026-04-22T00:00:00Z"
    assert record["source_id"] == "official-page"
    assert record["tier"] == 1
    assert record["publisher"] == "Example Agency"
    assert record["category_hint"] == "government"
    assert record["state_hint"] == "WA"
    assert result["report"]["official_pages_planned"] == 1
    assert result["report"]["official_pages_run"] == 1
    assert result["report"]["official_links_found"] >= 3
    assert result["report"]["official_links_saved"] == 1
    assert result["report"]["same_domain_links_only"] is True
    assert result["report"]["weak_date_count"] == 1
    assert result["report"]["registry_exclusion_reasons"]["weak_date_basis"] == 1
    assert result["report"]["official_exclusion_reasons"]["off_domain"] == 1
    assert result["report"]["official_exclusion_reasons"]["sports"] == 1
    assert not any(not item.get("url") for item in result["records"])


def test_historical_search_filters_dedupes_and_writes_diagnostics(cascadia_work_root, monkeypatch):
    monkeypatch.setenv("CASCADIA_DISABLE_REGISTRY_DISCOVERY", "1")

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
    assert report["queries_run"][0]["query_group"] == "infrastructure/utilities/outages"


def test_quality_weekly_cli_writes_report_and_below_target_guidance(cascadia_work_root, monkeypatch, capsys):
    import run_cascadia_dispatch

    (cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml").write_text("sources: []\n", encoding="utf-8")

    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            if "infrastructure" in query_terms:
                return [
                    {
                        "title": "Washington bridge infrastructure update",
                        "url": "https://example.com/wa-bridge",
                        "publisher": "Example WA News",
                        "published_at": "2026-04-21T12:00:00Z",
                        "summary_or_snippet": "Washington transportation officials described a bridge infrastructure closure.",
                        "reliability_tier": "reputable",
                    }
                ]
            if "public health" in query_terms:
                return [
                    {
                        "title": "Oregon public health hospital staffing update",
                        "url": "https://example.com/or-health",
                        "publisher": "Example OR News",
                        "published_at": "2026-04-22T12:00:00Z",
                        "summary_or_snippet": "Oregon hospital leaders described public health staffing for public services.",
                        "reliability_tier": "reputable",
                    }
                ]
            return []

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)

    code = run_cascadia_dispatch.main(["--archive-week", "2026-04-21", "--weekly-public", "--historical-search", "--quality-weekly"])

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["edition_date"] == "2026-04-26"
    assert summary["coverage_start"] == "2026-04-20"
    assert summary["coverage_end"] == "2026-04-26"
    assert summary["quality_weekly"] is True
    assert "Below target story count; add manual supplement or rerun with additional providers." in summary["warnings"]
    report_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-20_2026-04-26" / "weekly_quality_report.json"
    report = read_json(report_path)
    assert report["public_story_count"] == 2
    assert report["target_public_stories"] == 5
    assert report["missing_story_count"] == 3
    assert report["below_target"] is True
    assert report["manual_supplement_path"].endswith(r"2026-04-20_2026-04-26\manual_sources.json") or report["manual_supplement_path"].endswith("2026-04-20_2026-04-26/manual_sources.json")
    assert "--create-manual-template" in report["manual_supplement_commands"]["create_template"]
    assert "--validate-manual-sources" in report["manual_supplement_commands"]["validate"]
    assert "--max-historical-queries 8" in report["manual_supplement_commands"]["rerun"]
    assert len(report["query_groups_run"]) == 8
    assert {item["query_group"] for item in report["query_groups_run"]} >= {"infrastructure/utilities/outages", "transportation/freight/supply chain", "health/emergency services"}
    assert report["source_count_by_provider"] == {"gdelt": 2}

    public_dir = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-04-26"
    html = (public_dir / "index.html").read_text(encoding="utf-8")
    assert "Weekly briefing / Apr 20\u201326, 2026" in html
    assert "No qualifying source-backed Cascadia signals were identified" not in html
    assert "Example WA News - Washington bridge infrastructure update" in html
    curation = read_json(public_dir / "curation_manifest.json")
    for story in curation:
        if story["included_in_public_summary"]:
            assert story["source_urls"]
            assert story["summary"] != story["title"]


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


def test_curation_balances_categories_and_excludes_low_signal_items(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-04-26"
    normalized_dir.mkdir(parents=True)
    records = []
    for index, state in enumerate(["WA", "OR", "ID", "WA"], start=1):
        records.append(
            {
                "source_record_id": f"src-infra-{index}",
                "canonical_url": f"https://example.com/infra-{index}",
                "title": f"{state} bridge infrastructure public services update",
                "publisher": "Example News",
                "published_at": "2026-04-21T12:00:00Z",
                "text": f"{state} transportation bridge infrastructure update.",
                "source_id": "cascadia-manual",
                "region_scope": state,
                "state_hint": state,
                "category_hint": "Infrastructure",
                "reliability_tier": "editorial-record",
            }
        )
    records.extend(
        [
            {
                "source_record_id": "src-health",
                "canonical_url": "https://example.com/health",
                "title": "Oregon public health hospital services update",
                "publisher": "Example News",
                "published_at": "2026-04-22T12:00:00Z",
                "text": "Oregon public health hospital services update.",
                "source_id": "cascadia-manual",
                "region_scope": "OR",
                "state_hint": "OR",
                "category_hint": "Healthcare",
                "reliability_tier": "editorial-record",
            },
            {
                "source_record_id": "src-sports",
                "canonical_url": "https://example.com/sports",
                "title": "Washington sports team game preview",
                "publisher": "Example Sports",
                "published_at": "2026-04-22T12:00:00Z",
                "text": "Sports game coverage.",
                "source_id": "cascadia-manual",
                "region_scope": "WA",
                "state_hint": "WA",
                "category_hint": "Infrastructure",
                "reliability_tier": "editorial-record",
            },
            {
                "source_record_id": "src-opinion",
                "canonical_url": "https://example.com/opinion",
                "title": "Oregon editorial opinion on transportation",
                "publisher": "Example Opinion",
                "published_at": "2026-04-22T12:00:00Z",
                "text": "Opinion column.",
                "source_id": "cascadia-manual",
                "region_scope": "OR",
                "state_hint": "OR",
                "category_hint": "Transportation",
                "reliability_tier": "editorial-record",
            },
        ]
    )
    (normalized_dir / "normalized_sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")

    result = curate_sources(cascadia_work_root, "2026-04-26")
    curated = read_json(Path(result["curation_path"]))
    public = [story for story in curated if story["included_in_public_summary"]]

    assert len([story for story in public if story["category"] == "Infrastructure"]) == 2
    assert any(story["category"] == "Healthcare" for story in public)
    assert not any("sports" in story["title"].lower() for story in public)
    assert not any("opinion" in story["title"].lower() for story in public)
    assert all(story["source_urls"] and story["source_record_ids"] for story in public)


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

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
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


def test_gdelt_provider_curl_fallback_records_diagnostics(cascadia_work_root, monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("certificate revocation check failed")

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps(
            {
                "articles": [
                    {
                        "title": "Washington public health services update",
                        "url": "https://example.com/wa-health",
                        "domain": "example.com",
                        "seendate": "20260421120000",
                        "snippet": "Washington public health services update.",
                    },
                    {
                        "title": "Washington missing URL",
                        "domain": "example.com",
                        "seendate": "20260421120000",
                        "snippet": "Washington public health services update.",
                    },
                ]
            }
        )
        stderr = "curl ok"

    monkeypatch.setenv("CASCADIA_FETCH_BACKEND", "auto")
    monkeypatch.setenv("CASCADIA_ALLOW_CURL_NO_REVOKE", "1")
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.is_windows", lambda: True)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.subprocess.run", lambda command, capture_output, text, timeout, check, env=None: FakeCompleted())

    week_start, week_end = containing_week("2026-04-21")
    result = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-04-26", historical_provider="gdelt", max_historical_queries=1)
    report = result["report"]

    assert result["source_count"] == 1
    assert report["fetch_backend"] == "auto"
    assert report["fallback_used"] is True
    assert report["curl_exit_code"] == 0
    assert report["exclusion_reasons"]["missing_url"] == 1
    assert "curl fallback succeeded" in report["recommendation"]


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

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
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

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
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

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    config = {"provider_id": "gdelt", "delay_seconds": 0, "cache_enabled": False}
    provider = GDELTProvider(config, root=cascadia_work_root)

    assert provider.search(*containing_week("2026-04-28"), "Washington AND water", 3) == []
    assert "empty response body" in provider.last_diagnostics["warnings"]
    assert provider.search(*containing_week("2026-04-28"), "Washington AND power", 3) == []
    assert any("invalid JSON response" in warning for warning in provider.last_diagnostics["warnings"])


def test_historical_provider_failure_fails_safely(cascadia_work_root, monkeypatch):
    monkeypatch.setenv("CASCADIA_DISABLE_REGISTRY_DISCOVERY", "1")

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
    PROVIDER_BACKOFF_UNTIL.clear()
    historical_cfg = cascadia_work_root / "data" / "dispatches" / "cascadia" / "historical_sources.yml"
    historical_text = historical_cfg.read_text(encoding="utf-8")
    historical_cfg.write_text(historical_text.replace("backoff_max_seconds: 60", "backoff_max_seconds: 3600"), encoding="utf-8")

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
    assert any("cooling down" in warning for warning in second["warnings"])


def test_manual_and_historical_sources_merge_with_source_type_preserved(cascadia_work_root, monkeypatch):
    monkeypatch.setenv("CASCADIA_DISABLE_REGISTRY_DISCOVERY", "1")

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
    report = read_json(manual_dir / "historical_search_report.json")
    assert report["source_count_by_provider"] == {"gdelt": 1, "manual": 1}
    assert report["source_count_by_type"] == {"historical_search": 1, "manual": 1}
    assert report["manual_sources_loaded"] == 1
    assert report["manual_sources_valid"] is True
    assert report["final_saved_source_count"] == 2


def test_manual_template_command_creates_week_files_without_overwrite(cascadia_work_root):
    import run_cascadia_dispatch

    run_cascadia_dispatch.ROOT = cascadia_work_root
    code = run_cascadia_dispatch.main(["--archive-week", "2026-04-21", "--create-manual-template"])

    assert code == 0
    folder = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-20_2026-04-26"
    manual_path = folder / "manual_sources.json"
    example_path = folder / "manual_sources.example.json"
    assert read_json(manual_path) == []
    assert isinstance(read_json(example_path), list)
    manual_path.write_text(json.dumps([{"url": "https://example.com/keep", "title": "Keep", "publisher": "Example"}]), encoding="utf-8")

    code = run_cascadia_dispatch.main(["--archive-week", "2026-04-21", "--create-manual-template"])

    assert code == 0
    assert read_json(manual_path)[0]["url"] == "https://example.com/keep"


def test_manual_validation_reports_errors_and_warnings(cascadia_work_root):
    week_start, week_end = containing_week("2026-04-21")
    folder = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-20_2026-04-26"
    folder.mkdir(parents=True)
    manual_path = folder / "manual_sources.json"
    manual_path.write_text("{bad json", encoding="utf-8")

    invalid = validate_manual_sources(cascadia_work_root, week_start, week_end)

    assert invalid["ok"] is False
    assert "invalid JSON" in invalid["errors"][0]

    manual_path.write_text(
        json.dumps(
            [
                {"source_record_id": "dup", "title": "Missing URL", "publisher": "Example"},
                {
                    "source_record_id": "dup",
                    "title": "Washington bridge infrastructure update",
                    "url": "https://example.com/bridge",
                    "publisher": "Example",
                    "published_at": "2026-04-29T12:00:00Z",
                    "summary_or_snippet": "Washington bridge infrastructure update.",
                },
                {
                    "source_record_id": "ok",
                    "title": "Oregon housing services update",
                    "url": "https://example.com/bridge?utm=1",
                    "publisher": "Example",
                    "published_at": "2026-04-22T12:00:00Z",
                    "summary_or_snippet": "Oregon housing services update.",
                },
            ]
        ),
        encoding="utf-8",
    )

    result = validate_manual_sources(cascadia_work_root, week_start, week_end)

    assert result["ok"] is False
    assert any("missing required url" in error for error in result["errors"])
    assert any("duplicate source_record_ids" in error for error in result["errors"])
    assert result["duplicate_urls"]
    assert any("outside coverage window" in warning for warning in result["warnings"])

    manual_path.write_text(
        json.dumps(
            [
                {
                    "source_record_id": "manual-good",
                    "title": "Washington water infrastructure update",
                    "url": "https://example.com/water",
                    "publisher": "Example",
                    "published_at": "2026-04-22T12:00:00Z",
                    "retrieved_at": "2026-04-23T12:00:00Z",
                    "summary_or_snippet": "Washington water infrastructure update.",
                    "source_type": "manual",
                    "provider_id": "manual",
                    "region_terms_matched": ["washington"],
                    "category_hint": "Infrastructure",
                    "state_hint": "WA",
                    "reliability_tier": "editorial-record",
                    "traceability_note": "Project-local manual source supplement.",
                }
            ]
        ),
        encoding="utf-8",
    )

    valid = validate_manual_sources(cascadia_work_root, week_start, week_end)

    assert valid["ok"] is True
    assert valid["source_count"] == 1


def test_historical_provider_modes_and_manual_survives_gdelt_failure(cascadia_work_root, monkeypatch):
    week_start, week_end = containing_week("2026-04-28")
    manual_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-27_2026-05-03"
    manual_dir.mkdir(parents=True)
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: registry-wa-feed
    name: Registry WA Feed
    tier: 1
    source_type: rss
    url: https://example.com/registry.xml
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [utilities]
    reliability_tier: official-public
    publisher: Registry Agency
    refresh_mode: archive_limited
    notes: Test feed.
""",
        encoding="utf-8",
    )
    (manual_dir / "manual_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "manual-wa-water",
                    "title": "Washington water utility update",
                    "url": "https://example.com/manual-water",
                    "publisher": "Example",
                    "published_at": "2026-04-28T12:00:00Z",
                    "summary_or_snippet": "Washington water utility infrastructure update.",
                }
            ]
        ),
        encoding="utf-8",
    )

    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            return [
                {
                    "title": "Oregon wildfire emergency update",
                    "url": "https://example.com/gdelt-wildfire",
                    "publisher": "Example",
                    "published_at": "2026-04-29T12:00:00Z",
                    "summary_or_snippet": "Oregon wildfire emergency management update.",
                }
            ]

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"<rss><channel><item><title>Washington utility infrastructure registry update</title><link>https://example.com/registry-utility</link><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Washington utility infrastructure update.</description></item></channel></rss>"

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())
    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)
    manual_only = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", historical_provider="manual")
    registry_only = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", historical_provider="registry", refresh_cache=True)
    gdelt_only = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", historical_provider="gdelt")
    registry_manual = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", historical_provider="registry,manual", refresh_cache=True)
    all_sources = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", refresh_cache=True)

    assert manual_only["report"]["source_count_by_provider"] == {"manual": 1}
    assert registry_only["report"]["source_count_by_provider"] == {"registry": 1}
    assert gdelt_only["report"]["source_count_by_provider"] == {"gdelt": 1}
    assert registry_manual["report"]["source_count_by_provider"] == {"manual": 1, "registry": 1}
    assert all_sources["report"]["source_count_by_provider"] == {"gdelt": 1, "manual": 1, "registry": 1}
    assert all_sources["report"]["registry_records_saved"] == 1
    assert all_sources["report"]["source_count_by_type"]["rss"] == 1

    class FailingGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            raise OSError("network down")

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FailingGDELT)
    fallback = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", historical_provider="manual,gdelt")

    assert fallback["ok"] is True
    assert fallback["source_count"] == 1
    assert fallback["report"]["source_count_by_provider"] == {"manual": 1}
    assert fallback["warnings"]


def test_source_gap_report_is_read_only_and_recommends_actions(cascadia_work_root, monkeypatch):
    import argparse
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    folder = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-05-04_2026-05-10"
    folder.mkdir(parents=True)
    (folder / "manual_sources.json").write_text("[]", encoding="utf-8")
    (folder / "historical_sources.json").write_text(
        json.dumps([{"provider_id": "gdelt", "url": "https://example.com/one"}, {"provider_id": "registry", "url": "https://example.com/two"}]),
        encoding="utf-8",
    )
    before = sorted(str(path) for path in cascadia_work_root.rglob("*") if path.is_file())

    result = run_source_gap_report(argparse.Namespace(date="2026-05-11", backfill_weeks=4, week_start=None, week_end=None, archive_week=None))
    after = sorted(str(path) for path in cascadia_work_root.rglob("*") if path.is_file())

    assert result["ok"] is True
    assert len(result["weeks"]) == 4
    assert result["weeks"][0]["registry_source_count"] == 1
    assert result["weeks"][0]["gdelt_source_count"] == 1
    assert "weak_provider_count" in result["weeks"][0]
    assert result["weeks"][0]["recommended_action"] == "provider sparse; manual supplement recommended"
    assert result["weeks"][1]["recommended_action"] == "add manual_sources.json"
    assert before == after


def test_zero_week_gap_report_documents_no_signal_result(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    aggregate = {
        "source_count": 0,
        "warnings": ["sparse week: no provider results"],
        "errors": [],
        "report": {
            "manual_sources_path": str(cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-05-04_2026-05-10" / "manual_sources.json"),
            "manual_sources_loaded": 0,
            "manual_sources_valid": True,
            "registry_sources_run": 2,
            "registry_records_raw": 0,
            "registry_records_excluded": 1,
            "registry_exclusion_reasons": {"outside_date_window": 1},
            "official_links_excluded": 1,
            "official_exclusion_reasons": {"navigation_or_footer_link": 1},
            "gdelt_queries_run": 1,
            "raw_results_count": 0,
            "records_saved": 0,
            "records_excluded": 0,
            "queries_run": [
                {
                    "provider_id": "gdelt",
                    "query_group": "infrastructure/utilities/outages",
                    "query": "(Washington OR Oregon) AND (outage)",
                    "result_count": 0,
                    "error": "timed out",
                    "cache_hit": False,
                    "fallback_used": True,
                }
            ],
            "registry_source_diagnostics": [
                {
                    "source_id": "official-page",
                    "source_name": "Official Page",
                    "source_type": "official_page",
                    "url": "https://example.com/news",
                    "raw_count": 0,
                    "errors": ["certificate verify failed"],
                    "warnings": ["TLS warning"],
                    "excluded_links": [{"url": "https://example.com/contact", "title": "Contact", "reason": "navigation_or_footer_link"}],
                },
                {
                    "source_id": "rss-source",
                    "source_name": "RSS Source",
                    "source_type": "rss",
                    "url": "https://example.com/rss.xml",
                    "raw_count": 0,
                    "bytes_read": 1200,
                    "errors": [],
                    "warnings": [],
                },
            ],
            "warnings": ["TLS warning"],
            "tls_or_revocation_hint": "certificate revocation check failed",
            "python_fetch_error": "timed out",
        },
    }
    render_result = {"public_story_count": 0, "warnings": [], "errors": []}

    report = write_zero_week_gap_report(
        "2026-05-04",
        "2026-05-10",
        "2026-05-10",
        0,
        aggregate,
        render_result,
        dry_run=False,
    )

    path = cascadia_work_root / "output" / "dispatches" / "cascadia" / "weekly_gap_reports" / "2026-05-10.json"
    saved = read_json(path)
    assert report["weekly_gap_report_path"] == str(path)
    assert saved["edition_date"] == "2026-05-10"
    assert saved["coverage_label"] == "May 4\u201310, 2026"
    assert saved["provider_queries_attempted"][0]["query_group"] == "infrastructure/utilities/outages"
    assert len(saved["official_pages_checked"]) == 1
    assert len(saved["registry_sources_checked"]) == 2
    assert saved["gdelt_queries_attempted"][0]["result_count"] == 0
    assert saved["candidate_count"] == 2
    assert saved["accepted_candidate_count"] == 0
    assert saved["rejected_candidate_count"] == 2
    assert saved["rejected_candidates"]
    assert saved["manual_source_records_added"] is False
    assert saved["source_checks_attempted"] == 3
    assert saved["source_checks_successful"] == 1
    assert saved["source_checks_failed"] == 2
    assert saved["successful_fetch_rate"] == 0.3333
    assert saved["fetch_failures_by_reason"]["tls_or_certificate"] == 1
    assert saved["fetch_failures_by_reason"]["timeout"] == 1
    assert saved["candidate_rejection_counts"]["navigation_or_footer_link"] >= 1
    assert saved["candidate_rejections_by_stage"]["official_page_parse"] == 1
    assert saved["minimum_review_threshold_met"] is False
    assert saved["public_zero_story_wording"] == "Reviewed week | No qualifying source-backed regional signals surfaced"
    assert saved["final_zero_story_result_is_credible"] is False
    assert "successful fetched/evaluated source coverage" in saved["final_reason"]
    assert not (cascadia_work_root / "output" / "site" / "weekly_gap_reports").exists()


def test_gap_report_nonzero_public_story_omits_zero_story_metadata(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    aggregate = {"source_count": 4, "warnings": [], "errors": [], "report": {"records_saved": 4}}
    render_result = {"public_story_count": 2, "warnings": [], "errors": []}

    report = write_zero_week_gap_report(
        "2026-03-30",
        "2026-04-05",
        "2026-04-05",
        0,
        aggregate,
        render_result,
        dry_run=True,
    )

    assert report["final_public_story_count"] == 2
    assert report["final_zero_story_result_is_credible"] is None
    assert report["public_zero_story_wording"] is None
    assert "survived validation" in report["final_reason"]


@pytest.mark.parametrize(
    ("minimum_review_threshold_met", "expected_wording"),
    [
        (True, "Reviewed week | No qualifying source-backed regional signals identified"),
        (False, "Reviewed week | No qualifying source-backed regional signals surfaced"),
    ],
)
def test_gap_report_zero_story_wording_follows_threshold(cascadia_work_root, monkeypatch, minimum_review_threshold_met, expected_wording):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    monkeypatch.setattr(
        run_cascadia_dispatch,
        "_successful_fetch_metrics",
        lambda _report: {
            "source_checks_attempted": 25,
            "source_checks_successful": 15,
            "source_checks_failed": 10,
            "successful_fetch_rate": 0.6,
            "successful_sources_by_group": {
                "historical_search_provider": 5,
                "local_regional_source": 5,
                "official_state_regional_source": 5,
            },
            "failed_sources_by_group": {},
            "fetch_failures_by_reason": {},
            "minimum_review_threshold_met": minimum_review_threshold_met,
            "minimum_review_threshold": {},
        },
    )
    aggregate = {"source_count": 0, "warnings": [], "errors": [], "report": {"records_saved": 0}}
    render_result = {"public_story_count": 0, "warnings": [], "errors": []}

    report = write_zero_week_gap_report(
        "2026-04-06",
        "2026-04-12",
        "2026-04-12",
        0,
        aggregate,
        render_result,
        dry_run=True,
    )

    assert report["final_public_story_count"] == 0
    assert report["public_zero_story_wording"] == expected_wording


def test_historical_weekly_cli_renders_traceable_story_and_manifests(cascadia_work_root, monkeypatch):
    import run_cascadia_dispatch

    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    monkeypatch.setenv("CASCADIA_DISABLE_REGISTRY_DISCOVERY", "1")

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
    code = run_cascadia_dispatch.main(["--weekly-public", "--backfill-weeks", "4", "--date", "2026-05-11", "--historical-search", "--no-registry-sources"])

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
    assert "The Cascadia Briefing - Apr 27\u2013May 3, 2026" not in rss
    assert "2026-05-07" not in archive


def test_historical_registry_discovery_disabled_by_env(cascadia_work_root, monkeypatch):
    monkeypatch.setenv("CASCADIA_DISABLE_REGISTRY_DISCOVERY", "1")

    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            return []

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)
    result = retrieve_historical_sources(cascadia_work_root, *containing_week("2026-04-28"), edition_date="2026-05-03", run_date="2026-05-11")

    assert result["report"]["registry_discovery_disabled"] is True
    assert result["report"]["registry_sources_planned"] == 0
    assert result["report"]["source_count_by_provider_planned"] == {"gdelt": 1, "manual": 1}


def test_historical_registry_discovery_enabled_by_default(cascadia_work_root, monkeypatch):
    monkeypatch.delenv("CASCADIA_DISABLE_REGISTRY_DISCOVERY", raising=False)
    week_start, week_end = containing_week("2026-04-28")
    calls = {"count": 0}

    def fake_collect_registry_sources(root, week_start_arg, week_end_arg, retrieved_at=None, refresh_cache=False):
        calls["count"] += 1
        return {"records": [], "warnings": [], "errors": [], "report": {"registry_sources_planned": 1, "registry_sources_run": 1}}

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.collect_registry_sources", fake_collect_registry_sources)

    class FakeGDELT(GDELTProvider):
        def search(self, start_date, end_date, query_terms, max_results):
            return []

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FakeGDELT)
    result = retrieve_historical_sources(cascadia_work_root, week_start, week_end, edition_date="2026-05-03", run_date="2026-05-11")

    assert calls["count"] == 1
    assert result["report"]["registry_discovery_disabled"] is False
    assert result["report"]["registry_sources_run"] == 1


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


def test_deterministic_summary_uses_snippet_and_safe_fallbacks():
    with_snippet = {
        "title": "Washington bridge inspection program",
        "text": "State transportation officials posted a bridge inspection update for Washington infrastructure.",
        "category_hint": "Transportation",
        "region_scope": "WA",
        "publisher": "Example Agency",
    }
    only_title = {
        "title": "Oregon public health services update",
        "text": "",
        "category_hint": "Healthcare",
        "region_scope": "OR",
    }

    snippet_summary = deterministic_summary(with_snippet)
    fallback_summary = deterministic_summary(only_title)

    assert snippet_summary != with_snippet["title"]
    assert "bridge inspection update" in snippet_summary
    assert "Washington" in snippet_summary
    assert "deaths" not in snippet_summary.lower()
    assert "cost" not in snippet_summary.lower()
    assert fallback_summary.startswith("This source was flagged as a Healthcare signal for Oregon")
    assert "based on its title and source metadata" in fallback_summary


def test_deterministic_summary_removes_internal_rationale_and_incomplete_modal_sentence():
    record = {
        "title": "Idaho rural hospitals face pressure",
        "text": (
            "Idaho’s rural hospital leaders said financial pressures are rising. "
            "In three states, Democratic lawmakers introduced bills this session that would allow. "
            "It is included because the source metadata ties it to housing in Idaho."
        ),
        "category_hint": "Housing",
        "region_scope": "ID",
    }
    summary = deterministic_summary(record)
    assert "It is included because" not in summary
    assert "source metadata ties it" not in summary
    assert "that would allow." not in summary
    assert "Idaho’s rural hospital leaders said financial pressures are rising." in summary


def test_why_it_matters_is_category_and_region_grounded():
    record = {
        "category_hint": "Transportation",
        "state_hint": "WA",
        "title": "Washington bridge inspection program",
        "summary_or_snippet": "Bridge inspection update.",
    }

    line = why_it_matters(record, "Transportation")

    assert line == "Transportation disruptions can limit work, school, and emergency access across connected communities."
    assert "deaths" not in line.lower()
    assert "closure" not in line.lower()
    assert "cost" not in line.lower()


def test_why_it_matters_uses_corrections_specific_language():
    record = {
        "category_hint": "Government and public services",
        "title": "WA’s transgender prisoner policy is target of new federal investigation",
        "summary_or_snippet": "Federal oversight review addresses correctional policy and legal exposure in state custody.",
        "text": "The investigation focuses on detention oversight and treatment standards.",
    }
    line = why_it_matters(record, "Government and public services")
    assert line == "Corrections and detention policy changes can affect state oversight, legal exposure, and the treatment of people in state custody."


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
    assert "Regional Read" in html
    assert "Coverage Quality" in html
    assert "Coverage quality:" in html
    assert "States represented:" in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert "Score:" not in html
    assert "Signal strength" not in html
    assert "Why it matters:" in html
    assert "Source: <a href=" in html
    assert "Published:" in html
    assert "Category:" in html
    assert "Blue Fern Cascadia Manual Source File - Washington bridge inspection program" in html
    assert "source_record_id" not in html
    assert "provider_id" not in html
    curation = read_json(public_dir / "curation_manifest.json")
    assert all("source_record_ids" in story for story in curation)
    assert all("score" in story for story in curation)
    assert all("why_it_matters" in story for story in curation if story["included_in_public_summary"])
    editorial = cascadia_work_root / "output" / "dispatches" / "cascadia" / "editions" / "2026-05-03" / "editorial_review.md"
    assert editorial.exists()
    assert not (public_dir / "editorial_review.md").exists()
    editorial_text = editorial.read_text(encoding="utf-8")
    assert "No public numeric scores: pass" in editorial_text
    assert "Every public story has source URL: pass" in editorial_text
    assert "Every public story has why-it-matters line: pass" in editorial_text
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
    assert edition_manifest["public_story_count"] >= 1
    assert edition_manifest["public_categories"]
    assert edition_manifest["public_state_hints"]
    assert edition_manifest["public_source_publishers"]
    assert edition_manifest["weekly_summary_bullets"]
    assert edition_manifest["public_archive_subtitle"]
    public_paths = [path.relative_to(cascadia_work_root / "output" / "site").as_posix() for path in (cascadia_work_root / "output" / "site").rglob("*") if path.is_file()]
    assert not any(path.startswith("detail/") or path.startswith("paid/") for path in public_paths)


def test_editorial_checklist_catches_public_score_missing_source_and_summary():
    stories = [
        {
            "story_id": "story-1",
            "title": "Washington bridge inspection program",
            "summary": "Washington bridge inspection program",
            "category": "Transportation",
            "source_urls": [],
            "source_records": [],
        }
    ]

    text = editorial_checklist("Apr 20-26, 2026", stories, "<p>Score: 68</p>", [])

    assert "No public numeric scores: fail" in text
    assert "No title-as-summary repeats: fail" in text
    assert "Every public story has source URL: fail" in text
    assert "Weekly summary present when stories exist: fail" in text


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
    assert "This week's signals" in html
    assert "Transportation appeared in WA source records." in html
    assert "Why it matters:" in html
    assert "Transportation disruptions can limit work, school, and emergency access across connected communities." in html
    assert "https://example.com/weekly" in html
    assert "https://example.com/outside" not in html
    assert "Score:" not in html
    assert "Source: <a href=\"https://example.com/weekly\"" in html
    assert "Source - Washington bridge inspection program" in html
    assert "Published: May 4, 2026" in html
    assert "Category: Transportation" in html
    manifest = read_json(public_dir / "edition_manifest.json")
    assert manifest["briefing_type"] == "weekly"
    assert manifest["run_date"] == "2026-05-11"
    assert manifest["coverage_start"] == "2026-05-04"
    assert manifest["coverage_end"] == "2026-05-10"
    assert manifest["coverage_label"] == "May 4\u201310, 2026"
    assert manifest["week_label"] == "2026-W19"
    assert manifest["source_record_ids"] == ["src-in"]
    assert manifest["weekly_summary_bullets"] == [
        "Transportation appeared in WA source records.",
        "This edition includes source-backed items from Source.",
        "1 public source-backed story met the current public-systems criteria.",
    ]
    assert manifest["public_archive_subtitle"] == "1 story | WA | Transportation"
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert "2026-05-10" in archive
    assert "1 story | WA | Transportation" in archive
    index = (cascadia_work_root / "output" / "site" / "cascadia" / "index.html").read_text(encoding="utf-8")
    assert "1 story | WA | Transportation" in index
    assert "Weekly source-backed regional briefings for Washington, Oregon, and Idaho." not in archive
    rss = (cascadia_work_root / "output" / "site" / "cascadia" / "rss.xml").read_text(encoding="utf-8")
    assert "1 story | WA | Transportation" in rss


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
                    "public_story_count": 0,
                    "public_archive_subtitle": "0 stories | No qualifying public signals identified",
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
            assert weekly_date not in text
        for _, _, _, label in weekly_dates.values():
            assert f"The Cascadia Briefing - {label}" not in text
        assert "Reviewed week | No qualifying source-backed regional signals surfaced" not in text
        assert "0 stories | No qualifying public signals identified" not in text
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
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )

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
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
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
        assert "This week's signals" not in html
        assert manifest["briefing_type"] == "weekly"
        assert manifest["edition_date"] == edition_date
        assert manifest["coverage_start"] == coverage_start
        assert manifest["coverage_end"] == coverage_end
        assert manifest["coverage_label"] == format_coverage_label(coverage_start, coverage_end)
        assert manifest["run_date"] == "2026-05-11"
        assert "source_record_ids" in manifest
        assert "source_urls" in manifest
        assert manifest["weekly_summary_bullets"] == []
        assert manifest["public_archive_subtitle"] == "Reviewed week | No qualifying source-backed regional signals surfaced"
    archive = (cascadia_work_root / "output" / "site" / "cascadia" / "archive.html").read_text(encoding="utf-8")
    assert all(edition_date not in archive for edition_date in expected)


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


def test_weekly_render_generates_public_map_files_and_link(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")

    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )

    assert result["ok"] is True
    site_edition = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03"
    assert (site_edition / "map_data.json").exists()
    assert (site_edition / "map.html").exists()
    assert (site_edition / "source_table.html").exists()
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "map" / "index.html").exists()
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "map" / "source_table.html").exists()
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "dashboard" / "index.html").exists()
    html = (site_edition / "index.html").read_text(encoding="utf-8")
    assert 'src="/cascadia/map/"' not in html
    assert "Open this week's interactive map" in html
    assert "Open latest Cascadia map" not in html

    map_data = read_json(site_edition / "map_data.json")
    assert map_data["markers"] or map_data["regional_reports"]
    assert isinstance(map_data["grouped_markers"], list)
    assert "regional_reports" in map_data
    local_count = len(map_data["markers"])
    regional_count = len(map_data["regional_reports"])
    expected_fallback = (local_count == 0 and regional_count > 0) or (local_count < 3 and regional_count > 0)
    assert map_data.get("show_regional_default") is expected_fallback
    expected_mode = "local"
    if local_count == 0 and regional_count > 0:
        expected_mode = "regional_fallback"
    elif local_count < 3 and regional_count > 0:
        expected_mode = "sparse_local_plus_regional"
    assert map_data.get("default_view_mode") == expected_mode
    assert map_data.get("diagnostics", {}).get("default_view_mode") == expected_mode
    assert map_data.get("diagnostics", {}).get("local_marker_count") == local_count
    assert map_data.get("diagnostics", {}).get("regional_report_count") == regional_count
    assert map_data.get("coverage_start") == "2026-04-27"
    assert map_data.get("coverage_end") == "2026-05-03"
    visible_rows = list(map_data["markers"]) + list(map_data["regional_reports"])
    assert all(marker.get("source_url", "").startswith("http") for marker in visible_rows)
    assert all(marker.get("title") and marker.get("category") for marker in visible_rows)
    assert all(marker.get("state_or_region") and marker.get("publisher") for marker in visible_rows)
    assert all(marker.get("lat") is not None and marker.get("lon") is not None for marker in visible_rows)
    assert all(marker.get("place") and marker.get("state") and marker.get("region_label") for marker in visible_rows)
    assert all(marker.get("precision_note") for marker in visible_rows)
    assert all(marker.get("pressure_type") != "Regional systems pressure" for marker in visible_rows)
    assert all("reports" in group for group in map_data["grouped_markers"])
    assert all("pressure_areas" in group for group in map_data["grouped_markers"])

    curation = read_json(site_edition / "curation_manifest.json")
    public_ids = {story["story_id"] for story in curation if story.get("included_in_public_summary")}
    excluded_ids = {story["story_id"] for story in curation if not story.get("included_in_public_summary")}
    marker_ids = {marker["story_id"] for marker in map_data["markers"]}
    assert marker_ids <= public_ids
    assert marker_ids.isdisjoint(excluded_ids)

    assert not (cascadia_work_root / "output" / "site" / "detail").exists()
    assert not (cascadia_work_root / "output" / "site" / "paid").exists()
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "map" / "index.html").read_text(encoding="utf-8")
    assert "resource-header" in map_html
    assert "desktop-map-header" in map_html
    assert "mobile-map-header" in map_html
    assert "mobile-header-details" in map_html
    assert "id=\"mobileHeaderToggle\"" in map_html
    assert "aria-controls=\"mobileHeaderDetails\"" in map_html
    assert "mobile-header-toggle" in map_html
    assert ".desktop-map-header { display:none; }" in map_html
    assert ".mobile-map-header { display:block; }" in map_html
    assert ".mobile-map-header .mobile-header-details { display:none; }" in map_html
    assert "body.map-header-expanded .mobile-map-header .mobile-header-details { display:block; }" in map_html
    assert ".mobile-map-header .resource-branding { display:none; }" in map_html
    assert "body.map-header-expanded .mobile-map-header .resource-branding { display:flex; }" in map_html
    assert "setMobileHeaderExpanded(false);" in map_html
    assert "document.body.classList.add('map-header-expanded')" in map_html
    assert "document.body.classList.remove('map-header-expanded')" in map_html
    assert "mobileHeaderToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');" in map_html
    assert "mobileHeaderToggle.textContent = expanded ? 'Less' : 'More';" in map_html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in map_html
    assert "text-align:center" in map_html
    assert "--header-bg:#1E3F4F" in map_html
    assert "--header-primary:#EFE7DA" in map_html
    assert "--header-secondary:#9BAEB5" in map_html
    assert "map-title-accent" in map_html
    assert "https://thebluefernco.com/" in map_html
    assert "/assets/bluefern.ico" in map_html
    assert "/assets/dispatches-from-blue-fern-co.png" not in map_html
    assert "resource-home" not in map_html
    assert "padding:8px 12px 10px" in map_html
    assert "Pressure area" in map_html
    assert "<summary>Filters</summary>" in map_html
    assert "details class=\"panel filters\"" in map_html
    assert "id=\"mobileFiltersToggle\"" in map_html
    assert "mobile-filter-sheet" in map_html
    assert "id=\"mobileFiltersClose\"" in map_html
    assert "data-state=\"closed\"" in map_html
    assert ".mobile-filter-sheet[data-state=\"closed\"] { display:none !important; }" in map_html
    assert ".mobile-filter-sheet[data-state=\"open\"] { display:flex !important; }" in map_html
    assert "State" in map_html
    assert "Region" in map_html
    assert "Report window" in map_html
    assert "Grouped places" in map_html
    assert "Individual reports" in map_html
    assert "Reset Map" in map_html
    assert "<summary>Legend</summary>" in map_html
    assert "details class=\"panel howto mobile-collapsible\"" in map_html
    assert "details class=\"panel legend mobile-collapsible\"" in map_html
    assert "Reports shown:" in map_html
    assert "Sources: public regional reporting and official/public sources" in map_html
    assert ">More</button>" in map_html
    assert "Map view" in map_html
    assert "Show regional/statewide reports" in map_html
    assert "min-height:44px" in map_html
    assert "@media (max-width: 900px)" in map_html
    assert "@media (max-width: 430px)" in map_html
    assert "height:72vh" in map_html
    assert "details.filters { display:none !important; }" in map_html
    assert ".mobile-filter-fab { display:inline-flex;" in map_html
    assert "body.filters-open .mobile-filter-sheet { display:flex; }" in map_html
    assert "leaflet-control-attribution" in map_html
    assert "syncFilterHost()" in map_html
    assert "document.body.classList.add('filters-open')" in map_html
    assert "document.body.classList.remove('filters-open')" in map_html
    assert "event.key === 'Escape'" in map_html
    assert "How to read this map" in map_html
    assert "regional systems weather map" in map_html
    assert "not a complete census or disaster map" in map_html
    assert "localMarkerHtml" in map_html
    assert "L.AwesomeMarkers.icon" not in map_html
    assert "function validCoordinate(item)" in map_html
    assert "function markerLatLon(item)" in map_html
    assert "const lat = Number(item.lat);" in map_html
    assert "const lon = Number(item.lon);" in map_html
    assert "if (!validCoordinate(item))" in map_html
    assert "skippedInvalidCoordinates += 1;" in map_html
    assert "const latLon = markerLatLon(item);" in map_html
    assert "L.marker(latLon, {icon})" in map_html
    assert "data-invalid-coordinate-count" in map_html
    assert "categoryIcon" in map_html
    assert "legend-list" in map_html
    assert "Housing and utility pressure" not in map_html
    assert "Health care access" not in map_html
    assert "Jobs and local economy" not in map_html
    assert "Food and household support" not in map_html
    assert "Transportation and access" not in map_html
    assert "Public safety and emergency services" not in map_html
    assert "Wildfire, drought, flood, and recovery" not in map_html
    assert "Schools and local government services" not in map_html
    assert "Pressure type:" in map_html
    assert "Location:" in map_html
    assert "Date:" in map_html
    assert "Summary:" in map_html
    assert "Why it matters:" in map_html
    assert "View on map:" in map_html
    assert "View individual reports" in map_html
    assert "Hide reports" in map_html
    assert "report-card" in map_html
    assert "toggle-button" in map_html
    assert "reports-panel" in map_html
    assert "reports-panel scrollable" in map_html
    assert "Regional reports may still be available" not in map_html
    assert "No local reports met the mapping rules for this week. Regional reports may still be available." not in map_html
    assert "No local reports met the mapping rules for this week. Showing regional/statewide reports instead." in map_html
    assert "tt-place" in map_html
    assert "region-tooltip" in map_html
    assert "max-width:280px" in map_html
    assert "Pressure areas:" in map_html
    assert "Top reports:" in map_html
    assert "Number of reports:" in map_html
    assert "Source:</strong>" in map_html
    assert "Read more" in map_html
    assert "Open Source Table" in map_html
    assert "/cascadia/map/source_table.html" in map_html
    assert "source_record_id" not in map_html
    assert "coordinate_basis" not in map_html
    edition_map_html = (site_edition / "map.html").read_text(encoding="utf-8")
    assert "source_table.html" in edition_map_html
    edition_index_html = (site_edition / "index.html").read_text(encoding="utf-8")
    assert "Open this week's interactive map" in edition_index_html
    assert "Open this week's source table" in edition_index_html
    assert "Open latest Cascadia map" not in edition_index_html
    assert edition_index_html.count("Open this week's interactive map") == 1
    assert "source_table.html" in edition_index_html


def test_cascadia_mapping_philosophy_doc_exists_and_has_required_sections():
    root = Path(__file__).resolve().parents[1]
    doc_path = root / "docs" / "cascadia-mapping-philosophy.md"
    assert doc_path.exists()
    text = doc_path.read_text(encoding="utf-8")

    required_sections = [
        "## 1. Product definition",
        "## 2. What counts as pressure",
        "## 3. Secondary indicators",
        "## 4. What is excluded",
        "## 5. Geographic philosophy",
        "## 6. Source philosophy",
        "## 7. Visual philosophy",
        "## 8. User experience philosophy",
        "## 9. Time philosophy",
        "## 10. Overlap philosophy",
        "## 11. Honest incompleteness",
        "## 12. Implementation rules",
    ]
    for section in required_sections:
        assert section in text

    required_rules = [
        'no generic "Regional systems pressure"',
        "no stale reference pages",
        "no state-only local markers",
        "regional/statewide reports should live in a separate layer",
        "no local reports qualified",
    ]
    for rule in required_rules:
        assert rule in text


def test_cascadia_source_table_links_resolve_to_generated_public_files(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    site_root = cascadia_work_root / "output" / "site"
    html_paths = [
        site_root / "cascadia" / "map" / "index.html",
        site_root / "cascadia" / "editions" / "2026-05-03" / "map.html",
        site_root / "cascadia" / "editions" / "2026-05-03" / "index.html",
    ]
    for html_path in html_paths:
        html_text = html_path.read_text(encoding="utf-8")
        hrefs = re.findall(r'href="([^"]*source_table\.html)"', html_text)
        assert hrefs, f"no source_table hrefs found in {html_path}"
        for href in hrefs:
            if href.startswith("/"):
                target = site_root / href.lstrip("/")
            else:
                target = (html_path.parent / href).resolve()
            assert target.exists(), f"missing target for {href} in {html_path}"


def _write_min_cascadia_public_edition(site_root: Path, edition_date: str, coverage_start: str, coverage_end: str):
    edition_dir = site_root / "cascadia" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dispatch_slug": "cascadia",
        "edition_date": edition_date,
        "briefing_type": "weekly",
        "cadence": "weekly",
        "edition_type": "weekly",
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "coverage_label": f"{coverage_start} to {coverage_end}",
        "source_count": 1,
        "story_count": 1,
        "public_story_count": 1,
    }
    source_row = [{
        "source_record_id": f"src-{edition_date}",
        "title": "Source title",
        "url": "https://example.com/source",
        "publisher": "Example Publisher",
        "published_at": f"{coverage_end}T12:00:00Z",
        "category_hint": "Public safety",
        "state_hint": "WA",
        "location_precision": "city",
    }]
    curation = [{"story_id": f"story-{edition_date}", "included_in_public_summary": True}]
    edition_dir.joinpath("edition_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    edition_dir.joinpath("sources_manifest.json").write_text(json.dumps(source_row, indent=2), encoding="utf-8")
    edition_dir.joinpath("curation_manifest.json").write_text(json.dumps(curation, indent=2), encoding="utf-8")
    edition_dir.joinpath("index.html").write_text('<a href="source_table.html">Sources</a>', encoding="utf-8")
    edition_dir.joinpath("map.html").write_text('<a href="source_table.html">Source table</a>', encoding="utf-8")


def _write_min_food_line_public_edition(
    site_root: Path,
    edition_date: str,
    *,
    body_html: str,
    edition_mode: str = "current_update",
    manifest_overrides: dict[str, object] | None = None,
):
    edition_dir = site_root / "food-line" / "editions" / edition_date
    edition_dir.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<html><body>Home</body></html>", encoding="utf-8")
    (site_root / "food-line" / "archive.html").parent.mkdir(parents=True, exist_ok=True)
    (site_root / "food-line" / "archive.html").write_text("<html><body>Food Line archive</body></html>", encoding="utf-8")
    edition_dir.joinpath("index.html").write_text(body_html, encoding="utf-8")
    manifest = {
        "dispatch_slug": "food-line",
        "edition_date": edition_date,
        "public_rendered": True,
        "edition_mode": edition_mode,
        "source_freshness_status": "passed_with_stale_exclusions",
        "freshness_window_days": 3,
        "stale_public_story_count": 0,
        "excluded_stale_source_count": 0,
        "stale_source_ids": [],
        "qualified_primary_count": 1 if edition_mode != "no_current_update" else 0,
        "skip_reason": "",
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    edition_dir.joinpath("edition_manifest.json").write_text(
        json.dumps(
            manifest,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_cascadia_index_uses_newest_valid_weekly_edition_and_lists_it(cascadia_work_root):
    site_root = cascadia_work_root / "output" / "site"
    _write_min_cascadia_public_edition(site_root, "2026-05-10", "2026-05-04", "2026-05-10")
    _write_min_cascadia_public_edition(site_root, "2026-05-24", "2026-05-18", "2026-05-24")

    refresh_cascadia_archive_pages(cascadia_work_root, dry_run=False, written=[])
    index_html = (site_root / "cascadia" / "index.html").read_text(encoding="utf-8")
    assert 'href="editions/2026-05-24/"' in index_html
    assert "2026-05-24" in index_html


def test_publish_pages_overwrites_existing_food_line_edition_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "repo"
    site_root = root / "output" / "site"
    pages_repo = root / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=pages_repo, check=True, capture_output=True, text=True)
    (pages_repo / ".keep").write_text("keep\n", encoding="utf-8")
    subprocess.run(["git", "add", ".keep"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "Initial Pages repo"], cwd=pages_repo, check=True, capture_output=True, text=True)
    (pages_repo / "index.html").write_text("<html><body>Old home</body></html>", encoding="utf-8")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "food-line" / "archive.html").parent.mkdir(parents=True, exist_ok=True)
    (pages_repo / "food-line" / "archive.html").write_text("<html><body>Food Line archive</body></html>", encoding="utf-8")
    old_edition_dir = pages_repo / "food-line" / "editions" / "2026-06-08"
    old_edition_dir.mkdir(parents=True, exist_ok=True)
    (old_edition_dir / "index.html").write_text("<p>Old Food Line edition</p>", encoding="utf-8")

    _write_min_food_line_public_edition(
        site_root,
        "2026-06-08",
        body_html="<p>New Food Line edition</p>",
    )

    def fake_build_site(*args, **kwargs):
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_archive_entries_written": [],
            "gaza_editions_skipped": [],
        }

    monkeypatch.setattr("bluefern_dispatches.generator.build_site", fake_build_site)

    result = publish_pages(
        root,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=root / "backup",
        expect_date="2026-06-08",
        expect_dispatches=("food-line",),
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert result["food_line_public_edition_skip_diagnostics"] == []
    assert (pages_repo / "food-line" / "editions" / "2026-06-08" / "index.html").read_text(encoding="utf-8") == "<p>New Food Line edition</p>"
    assert "Old Food Line edition" not in (pages_repo / "food-line" / "editions" / "2026-06-08" / "index.html").read_text(encoding="utf-8")


def test_publish_pages_reports_food_line_non_listable_exclusion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "repo"
    site_root = root / "output" / "site"
    pages_repo = root / "bluefern-dispatches-pages"
    pages_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=pages_repo, check=True, capture_output=True, text=True)
    (pages_repo / ".keep").write_text("keep\n", encoding="utf-8")
    subprocess.run(["git", "add", ".keep"], cwd=pages_repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "Initial Pages repo"], cwd=pages_repo, check=True, capture_output=True, text=True)
    (pages_repo / "index.html").write_text("<html><body>Old home</body></html>", encoding="utf-8")
    (pages_repo / "CNAME").write_text("dispatches.thebluefernco.com\n", encoding="utf-8")
    (pages_repo / "food-line" / "archive.html").parent.mkdir(parents=True, exist_ok=True)
    (pages_repo / "food-line" / "archive.html").write_text("<html><body>Food Line archive</body></html>", encoding="utf-8")

    _write_min_food_line_public_edition(
        site_root,
        "2026-06-08",
        body_html="<p>Non-listable Food Line edition</p>",
        manifest_overrides={"qualified_primary_count": 0, "edition_mode": "current_update"},
    )

    def fake_build_site(*args, **kwargs):
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "backfilled_public_editions": [],
            "gaza_editions_discovered": [],
            "gaza_archive_entries_written": [],
            "gaza_editions_skipped": [],
        }

    monkeypatch.setattr("bluefern_dispatches.generator.build_site", fake_build_site)

    result = publish_pages(
        root,
        pages_repo,
        None,
        dry_run=False,
        commit=False,
        no_push=True,
        backup_root=root / "backup",
        expect_date="2026-06-08",
        expect_dispatches=("food-line",),
        only_dispatches=("food-line",),
    )

    assert result["ok"] is True
    assert (pages_repo / "food-line" / "editions" / "2026-06-08" / "index.html").exists() is False
    assert result["food_line_public_edition_skip_diagnostics"]
    report = result["food_line_public_edition_skip_diagnostics"][0]
    assert report["edition_date"] == "2026-06-08"
    assert report["manifest_path"].endswith(r"food-line\editions\2026-06-08\edition_manifest.json")
    assert report["manifest_exists"] is True
    assert report["dispatch_slug"] == "food-line"
    assert report["listable"] is False
    assert report["public_rendered"] is True
    assert "qualified_primary_count" in report["false_or_invalid_fields"]
    assert any("qualified_primary_count" in reason for reason in report["reasons"])
    assert any("Food Line edition 2026-06-08 was not copied to Pages" in warning for warning in result["warnings"])


def test_every_public_cascadia_edition_source_table_link_resolves(cascadia_work_root):
    site_root = cascadia_work_root / "output" / "site"
    _write_min_cascadia_public_edition(site_root, "2026-05-10", "2026-05-04", "2026-05-10")
    _write_min_cascadia_public_edition(site_root, "2026-05-24", "2026-05-18", "2026-05-24")
    build_site(cascadia_work_root, dry_run=False, only_dispatches=("cascadia",))

    dates = discover_public_edition_dates(site_root, "cascadia")
    assert dates
    for edition_date in dates:
        edition_dir = site_root / "cascadia" / "editions" / edition_date
        for html_name in ("index.html", "map.html"):
            html_path = edition_dir / html_name
            html_text = html_path.read_text(encoding="utf-8")
            hrefs = re.findall(r'href="([^"]*source_table\.html)"', html_text)
            assert hrefs, f"no source table link in {html_path}"
            for href in hrefs:
                target = (html_path.parent / href).resolve() if not href.startswith("/") else (site_root / href.lstrip("/"))
                assert target.exists(), f"missing source table target {target}"


def test_cascadia_map_messages_hidden_by_default_and_header_not_duplicated(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    assert 'id="emptyState" class="empty-state" hidden' in map_html
    assert 'id="renderWarning" class="empty-state" hidden' in map_html
    assert "empty.hidden = !shouldShowEmpty;" in map_html
    assert "renderWarning.hidden = !shouldShowWarning;" in map_html
    assert map_html.count('class="desktop-map-header"') == 1
    assert map_html.count('class="mobile-map-header"') == 1


def test_category_sanity_mismatch_is_not_published(cascadia_work_root, monkeypatch):
    def fake_score_record(record, reliability_tier="unknown", duplicate_count=1):
        return {
            "category": "Transportation",
            "regional_relevance_score": 15,
            "systems_impact_score": 20,
            "public_consequence_score": 15,
            "recency_score": 10,
            "source_reliability_score": 14,
            "multi_source_score": 0,
            "duplicate_penalty": 0,
            "low_signal_penalty": 0,
            "total_score": 74,
            "scoring_reasons": ["category=Transportation"],
        }

    monkeypatch.setattr("bluefern_dispatches.cascadia_curate.score_record", fake_score_record)
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.joinpath("normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-mismatch-001",
                    "source_id": "cascadia-manual",
                    "title": "Regional policy meeting update",
                    "text": "Council discussed meeting process and agenda updates.",
                    "summary_or_snippet": "Administrative meeting update.",
                    "canonical_url": "https://example.com/meeting",
                    "published_at": "2026-05-02T10:00:00Z",
                    "region_scope": "WA",
                    "category_hint": "government",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = curate_sources(cascadia_work_root, "2026-05-03")
    assert result["ok"] is True
    curated_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json"
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    assert curated[0]["included_in_public_summary"] is False
    assert curated[0]["excluded_reason"] == "unsupported_category_or_weak_category_match"



def test_utah_only_story_is_excluded_from_public_cascadia_output(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.joinpath("normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-utah-001",
                    "source_id": "cascadia-manual",
                    "title": "Governor declares drought emergency as Utah dips into reservoir savings",
                    "summary_or_snippet": "Utah drought conditions worsened as reservoir storage fell.",
                    "text": "Utah drought emergency and Utah reservoir savings measures.",
                    "canonical_url": "https://example.com/utah-drought",
                    "url": "https://example.com/utah-drought",
                    "publisher": "Idaho Capital Sun Feed",
                    "published_at": "2026-05-02T10:00:00Z",
                    "category_hint": "Public safety",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = curate_sources(cascadia_work_root, "2026-05-03")
    assert result["ok"] is True
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["included_in_public_summary"] is False
    assert curated[0]["excluded_reason"] == "out_of_region_state_story_without_cascadia_impact"


def test_utah_only_story_is_excluded_from_map_marker_output(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-utah-001",
                    "title": "Utah drought emergency update",
                    "summary": "Utah drought emergency details.",
                    "category": "Public safety",
                    "score": 80,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "excluded_reason": None,
                    "source_record_ids": ["src-utah-001"],
                    "source_urls": ["https://example.com/utah-drought"],
                    "source_records": [
                        {
                            "source_record_id": "src-utah-001",
                            "source_url": "https://example.com/utah-drought",
                            "url": "https://example.com/utah-drought",
                            "publisher": "Idaho Capital Sun Feed",
                            "title": "Utah drought emergency update",
                            "summary_or_snippet": "Utah drought emergency details.",
                            "state_hint": "UT",
                            "category_hint": "Public safety",
                            "published_at": "2026-05-02T10:00:00Z",
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert not any("utah" in str(marker.get("title", "")).lower() for marker in map_data["markers"])
    assert not any("utah" in str(marker.get("title", "")).lower() for marker in map_data["regional_reports"])


def test_publisher_name_alone_does_not_make_utah_story_cascadia_relevant(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.joinpath("normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-utah-002",
                    "source_id": "cascadia-manual",
                    "title": "Utah reservoir emergency guidance",
                    "summary_or_snippet": "Utah reservoir updates and local restrictions.",
                    "canonical_url": "https://example.com/utah-reservoir",
                    "publisher": "Idaho Capital Sun Feed",
                    "published_at": "2026-05-02T10:00:00Z",
                    "category_hint": "Environment and climate",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["included_in_public_summary"] is False
    assert curated[0]["excluded_reason"] == "out_of_region_state_story_without_cascadia_impact"


def test_utah_story_with_snake_river_linkage_is_allowed_as_regional_context(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.joinpath("normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-utah-003",
                    "source_id": "cascadia-manual",
                    "title": "Utah drought pressures raise Snake River Basin agriculture concerns",
                    "summary_or_snippet": "The report describes Snake River Basin and Idaho agriculture impacts.",
                    "canonical_url": "https://example.com/utah-snake-river",
                    "publisher": "Idaho Capital Sun Feed",
                    "published_at": "2026-05-02T10:00:00Z",
                    "category_hint": "Environment and climate",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["excluded_reason"] != "out_of_region_state_story_without_cascadia_impact"
    assert curated[0]["scope_label"] == "Regional context"
    assert "snake river basin" in [item.lower() for item in curated[0].get("geography_linkage_terms", [])]


def test_cross_region_item_does_not_render_in_idaho_without_explicit_idaho_support():
    record = {
        "title": "Utah drought pressures raise Snake River Basin concerns",
        "summary_or_snippet": "Snake River Basin effects are discussed for the wider region.",
        "category_hint": "Environment and climate",
        "state_hint": "UT",
        "publisher": "Idaho Capital Sun Feed",
    }
    line = why_it_matters(record, "Environment and climate")
    assert line == "Flood and drought recovery gaps can leave households and local governments carrying costs after the immediate emergency."


def test_public_outputs_exclude_geography_sanity_mismatch_records(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-geography-mismatch",
                    "title": "Utah drought emergency update",
                    "summary": "Utah-only drought emergency details.",
                    "category": "Environment and climate",
                    "score": 80,
                    "included_in_public_summary": False,
                    "included_in_detail_dataset": False,
                    "excluded_reason": "geography_sanity_mismatch",
                    "source_record_ids": ["src-utah-x"],
                    "source_urls": ["https://example.com/utah-only"],
                    "source_records": [{"source_record_id": "src-utah-x", "url": "https://example.com/utah-only", "publisher": "Example"}],
                },
                {
                    "story_id": "story-wa-valid",
                    "title": "Washington wildfire preparedness planning expands",
                    "summary": "Washington planning update.",
                    "category": "Public safety",
                    "score": 81,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "excluded_reason": None,
                    "source_record_ids": ["src-wa-valid"],
                    "source_urls": ["https://example.com/wa-valid"],
                    "source_records": [{"source_record_id": "src-wa-valid", "url": "https://example.com/wa-valid", "publisher": "Example", "state_hint": "WA", "published_at": "2026-05-02T10:00:00Z", "category_hint": "Public safety"}],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    site_curation = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "curation_manifest.json")
    assert not any(item.get("included_in_public_summary") and item.get("excluded_reason") == "geography_sanity_mismatch" for item in site_curation)
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert not any(marker.get("excluded_reason") == "geography_sanity_mismatch" for marker in map_data["markers"])
    source_table = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "source_table.html").read_text(encoding="utf-8").lower()
    assert "utah-only" not in source_table
    assert "state / region" in source_table
    assert "pressure area" in source_table
    assert "open source" in source_table


def test_existing_wa_or_id_story_still_renders_normally(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert map_data["markers"] or map_data["regional_reports"]


def test_map_dedupes_duplicate_url_place_category(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    sources_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-27_2026-05-03"
    sources_dir.mkdir(parents=True, exist_ok=True)
    repeated = {
        "source_record_id": "hist-dup-1",
        "title": "Spokane food bank demand rises",
        "url": "https://example.com/wa-food-bank-demand",
        "publisher": "Example WA",
        "published_at": "2026-05-02T12:00:00Z",
        "summary_or_snippet": "Food bank demand and SNAP pressure increased this week.",
        "state_hint": "WA",
        "category_hint": "Food and agriculture",
    }
    (sources_dir / "historical_sources.json").write_text(json.dumps([repeated, {**repeated, "source_record_id": "hist-dup-2"}], indent=2), encoding="utf-8")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert (map_data["diagnostics"]["duplicates_removed"] + map_data["diagnostics"].get("regional_duplicates_removed", 0)) >= 1


def test_backfill_refuses_publish_with_insecure_ssl(tmp_path):
    with pytest.raises(ValueError):
        backfill_cascadia_pressure.run_backfill(
            tmp_path,
            "2026-04-21",
            "2026-05-10",
            max_per_source=10,
            write=True,
            allow_insecure_ssl=True,
        )


def test_backfill_diagnostics_rows_capture_ssl_fields(tmp_path, monkeypatch):
    def fake_retrieve(*args, **kwargs):
        return {
            "ok": False,
            "source_count": 0,
            "excluded_source_count": 1,
            "historical_sources_path": str(tmp_path / "historical_sources.json"),
            "historical_search_report_path": str(tmp_path / "historical_search_report.json"),
            "warnings": ["w"],
            "errors": ["e"],
            "report": {
                "records_saved": 0,
                "records_excluded": 1,
                "providers_used": ["gdelt"],
                "records_by_state_hint": {},
                "registry_records_raw": 2,
                "queries_run": [
                    {
                        "provider_id": "gdelt",
                        "query": "q",
                        "request_url": "https://example.com/q",
                        "result_count": 0,
                        "status_code": 500,
                        "bytes_read": 0,
                        "ssl_mode": "certifi",
                        "insecure_ssl_used": False,
                        "error": "timeout",
                    }
                ],
                "registry_source_diagnostics": [
                    {
                        "source_id": "s1",
                        "source_name": "S1",
                        "url": "https://example.com/feed",
                        "status_code": 200,
                        "bytes_read": 1200,
                        "records_raw": 2,
                        "fetch_successful": True,
                        "ssl_mode": "certifi",
                        "insecure_ssl_used": False,
                        "errors": [],
                    }
                ],
                "exclusion_reasons": {"opinion": 1},
            },
        }

    monkeypatch.setattr(backfill_cascadia_pressure, "retrieve_historical_sources", fake_retrieve)
    result = backfill_cascadia_pressure.run_backfill(
        tmp_path,
        "2026-04-21",
        "2026-05-10",
        max_per_source=10,
        weekly=True,
        write=False,
    )
    diagnostics = json.loads(Path(result["diagnostics_path"]).read_text(encoding="utf-8"))
    assert diagnostics["ssl_mode"] == "certifi"
    assert diagnostics["insecure_ssl_used"] is False
    assert len(diagnostics["rows"]) >= 2
    assert all("ssl_mode" in row and "insecure_ssl_used" in row for row in diagnostics["rows"])


def test_fetch_uses_certifi_ssl_mode_when_available(monkeypatch):
    import tempfile
    import types

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        cert_path = handle.name

    class DummyCertifi:
        @staticmethod
        def where():
            return cert_path

    dummy_module = types.ModuleType("certifi")
    dummy_module.where = DummyCertifi.where
    monkeypatch.setitem(sys.modules, "certifi", dummy_module)
    monkeypatch.setattr(
        "bluefern_dispatches.cascadia_fetch.ssl.create_default_context",
        lambda cafile=None: object(),
    )
    monkeypatch.setenv("CASCADIA_SSL_MODE", "certifi")
    monkeypatch.setenv("CASCADIA_ALLOW_INSECURE_SSL", "0")
    calls = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout, context):
        calls.append(context)
        return FakeResponse()

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", fake_urlopen)
    result = cascadia_fetch.fetch_public_url("https://example.com", 5, "Agent")
    assert result.ok is True
    assert result.diagnostics["ssl_mode"] == "certifi"
    assert result.diagnostics["insecure_ssl_used"] is False
    assert calls



def test_backfill_filter_rejects_non_pressure_record():
    system_terms = ["housing", "hospital", "wildfire", "transit", "food bank", "layoffs", "school closure", "public safety"]
    reason = exclusion_reason(
        {
            "url": "https://example.com/news/campaign-event",
            "title": "Portland campaign event draws large crowd",
            "publisher": "Example News",
            "summary_or_snippet": "Candidate speech focused on fundraising and endorsements.",
        },
        system_terms,
    )
    assert reason in {"generic_politics", "no_explicit_pressure_evidence", "no_public_systems_term"}


def test_backfill_filter_accepts_explicit_pressure_record():
    system_terms = ["housing", "hospital", "wildfire", "transit", "food bank", "layoffs", "school closure", "public safety"]
    reason = exclusion_reason(
        {
            "url": "https://example.com/news/wa-transit-cuts",
            "title": "Seattle transit cuts reduce late-night routes",
            "publisher": "Example News",
            "summary_or_snippet": "Agency cites staffing shortage and service disruption across county lines.",
        },
        system_terms,
    )
    assert reason is None


def test_cascadia_backfill_summary_writes_source_backed_counts(tmp_path, monkeypatch):
    def fake_retrieve(*args, **kwargs):
        return {
            "ok": True,
            "source_count": 3,
            "excluded_source_count": 2,
            "historical_sources_path": str(tmp_path / "historical_sources.json"),
            "historical_search_report_path": str(tmp_path / "historical_search_report.json"),
            "warnings": [],
            "errors": [],
            "report": {
                "records_saved": 3,
                "records_excluded": 2,
                "providers_used": ["registry", "gdelt"],
                "records_by_state_hint": {"WA": 1, "OR": 1, "ID": 1},
                "exclusion_reasons": {"sports": 1, "opinion": 1},
            },
        }

    monkeypatch.setattr(backfill_cascadia_pressure, "retrieve_historical_sources", fake_retrieve)

    result = backfill_cascadia_pressure.run_backfill(
        tmp_path,
        "2026-04-21",
        "2026-05-10",
        max_per_source=10,
        weekly=True,
        write=True,
    )

    assert result["ok"] is True
    assert result["total_records_saved"] == 6
    assert result["total_records_excluded"] == 4
    assert result["states_seen"] == ["ID", "OR", "WA"]
    assert "gdelt" in result["providers_seen"]
    assert "registry" in result["providers_seen"]
    assert result["accepted_by_state"] == {"ID": 2, "OR": 2, "WA": 2}
    assert result["rejected_reasons"] == {"opinion": 2, "sports": 2}
    assert result["mapped_count"] == 6
    assert result["unmapped_count"] == 4
    assert Path(result["summary_path"]).exists()
    assert Path(result["diagnostics_path"]).exists()
    diagnostics = json.loads(Path(result["diagnostics_path"]).read_text(encoding="utf-8"))
    assert "rows" in diagnostics



def test_map_coordinates_fallback_to_state_centroid_when_source_has_no_lat_lon(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-wa-1",
                    "title": "Washington bridge inspection program",
                    "summary": "Washington bridge inspection program update.",
                    "category": "Transportation",
                    "score": 88,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-wa-1"],
                    "source_urls": ["https://example.com/wa-bridge"],
                    "source_records": [
                        {
                            "source_record_id": "src-wa-1",
                            "source_id": "wa-source",
                            "title": "WA bridge source",
                            "source_url": "https://example.com/wa-bridge",
                            "url": "https://example.com/wa-bridge",
                            "publisher": "Washington State Standard Feed",
                            "published_at": "2026-05-02T12:00:00Z",
                            "retrieved_at": "2026-05-03T01:00:00Z",
                            "region_scope": "WA",
                            "state_hint": "WA",
                            "category_hint": "Transportation",
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True

    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert map_data["regional_reports"] or map_data["diagnostics"]["excluded_reasons"].get("no_specific_place", 0) >= 1
    if map_data["regional_reports"]:
        marker = map_data["regional_reports"][0]
        assert marker["source_url"] == "https://example.com/wa-bridge"
        assert marker["location_precision"] in {"statewide", "regional"}
        assert marker["precision_note"] in {"Statewide report.", "Regional report."}


def test_map_uses_address_precision_when_source_provides_address(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-or-1",
                    "title": "Portland clinic announces reduced hours",
                    "summary": "Reduced clinic hours may delay care access.",
                    "category": "Health",
                    "score": 80,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-or-1"],
                    "source_urls": ["https://example.com/or-clinic-hours"],
                    "source_records": [
                        {
                            "source_record_id": "src-or-1",
                            "source_id": "or-source",
                            "title": "OR clinic source",
                            "source_url": "https://example.com/or-clinic-hours",
                            "url": "https://example.com/or-clinic-hours",
                            "publisher": "Example Oregon",
                            "published_at": "2026-05-02T12:00:00Z",
                            "region_scope": "OR",
                            "state_hint": "OR",
                            "category_hint": "Health",
                            "address": "101 Example St, Portland, OR",
                            "lat": 45.52,
                            "lon": -122.68,
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    marker = map_data["markers"][0]
    assert marker["address"] == "101 Example St, Portland, OR"
    assert marker["location_precision"] == "address"
    assert marker["precision_note"] == "Mapped to reported address/facility."


def test_map_excludes_stale_and_generic_and_state_only_records(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-old",
                    "title": "Old public safety alert",
                    "summary": "Reference alert page.",
                    "category": "safety",
                    "score": 70,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-old"],
                    "source_urls": ["https://example.com/alerts/public-safety"],
                    "source_records": [
                        {
                            "source_record_id": "src-old",
                            "source_url": "https://example.com/alerts/public-safety",
                            "url": "https://example.com/alerts/public-safety",
                            "publisher": "Example",
                            "published_at": "2023-08-01T00:00:00Z",
                            "state_hint": "WA",
                            "geography": "WA",
                            "category_hint": "public safety",
                        }
                    ],
                },
                {
                    "story_id": "story-generic",
                    "title": "Alerts & Emergencies Currently selected",
                    "summary": "Category landing page.",
                    "category": "safety",
                    "score": 70,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-generic"],
                    "source_urls": ["https://example.com/category/public-safety"],
                    "source_records": [
                        {
                            "source_record_id": "src-generic",
                            "source_url": "https://example.com/category/public-safety",
                            "url": "https://example.com/category/public-safety",
                            "publisher": "Example",
                            "published_at": "2026-05-01T12:00:00Z",
                            "state_hint": "OR",
                            "geography": "OR",
                            "lat": 45.52,
                            "lon": -122.68,
                            "category_hint": "public safety",
                        }
                    ],
                },
                {
                    "story_id": "story-local",
                    "title": "Portland bus route reductions",
                    "summary": "Transit access changes affecting riders.",
                    "category": "transportation",
                    "score": 88,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-local"],
                    "source_urls": ["https://example.com/portland-transit"],
                    "source_records": [
                        {
                            "source_record_id": "src-local",
                            "source_url": "https://example.com/portland-transit",
                            "url": "https://example.com/portland-transit",
                            "publisher": "Example",
                            "published_at": "2026-05-02T12:00:00Z",
                            "state_hint": "OR",
                            "geography": "Portland",
                            "lat": 45.52,
                            "lon": -122.68,
                            "category_hint": "transportation",
                        }
                    ],
                },
                {
                    "story_id": "story-stateonly",
                    "title": "County operations update",
                    "summary": "Public update with only state-level place labeling.",
                    "category": "government",
                    "score": 75,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-stateonly"],
                    "source_urls": ["https://example.com/stateonly-update"],
                    "source_records": [
                        {
                            "source_record_id": "src-stateonly",
                            "source_url": "https://example.com/stateonly-update",
                            "url": "https://example.com/stateonly-update",
                            "publisher": "Example",
                            "published_at": "2026-05-01T12:00:00Z",
                            "state_hint": "WA",
                            "geography": "WA",
                            "lat": 47.52,
                            "lon": -122.67,
                            "category_hint": "government",
                        }
                    ],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert len(map_data["markers"]) == 1
    assert map_data.get("show_regional_default") is False
    assert map_data.get("default_view_mode") == "local"
    assert map_data.get("diagnostics", {}).get("default_view_mode") == "local"
    assert map_data.get("diagnostics", {}).get("local_marker_count") == 1
    assert map_data.get("diagnostics", {}).get("regional_report_count") == 0
    assert map_data["markers"][0]["place"] == "Portland"
    reasons = map_data["diagnostics"]["excluded_reasons"]
    assert reasons.get("outside_report_window", 0) >= 1
    assert reasons.get("generic_landing_page", 0) >= 1
    assert reasons.get("no_specific_place", 0) >= 1


def test_map_separates_statewide_reports_from_default_local_layer(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-wa-state",
                    "title": "Washington statewide drought warning",
                    "summary": "Drought warning affects statewide planning.",
                    "category": "wildfire",
                    "score": 80,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-wa-state"],
                    "source_urls": ["https://example.com/wa-drought"],
                    "source_records": [
                        {
                            "source_record_id": "src-wa-state",
                            "source_url": "https://example.com/wa-drought",
                            "url": "https://example.com/wa-drought",
                            "publisher": "Example",
                            "published_at": "2026-05-02T12:00:00Z",
                            "state_hint": "WA",
                            "geography": "WA",
                            "category_hint": "wildfire",
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert map_data["markers"] == []
    assert len(map_data["regional_reports"]) == 1
    assert map_data.get("show_regional_default") is True
    assert map_data.get("default_view_mode") == "regional_fallback"
    assert map_data.get("diagnostics", {}).get("default_view_mode") == "regional_fallback"
    assert map_data.get("diagnostics", {}).get("local_marker_count") == 0
    assert map_data.get("diagnostics", {}).get("regional_report_count") == 1
    assert map_data.get("diagnostics", {}).get("default_show_regional") is True
    assert map_data.get("diagnostics", {}).get("initial_render_layer") == "regional_only"
    assert map_data.get("diagnostics", {}).get("initial_visible_count", 0) >= 1
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    assert "No local reports met the mapping rules for this week. Showing regional/statewide reports instead." in map_html
    assert "function defaultNoteText()" in map_html
    assert "if (defaultViewMode === 'regional_fallback') return fallbackNote;" in map_html
    assert map_data["regional_reports"][0]["pressure_type"] == "Environment and climate"


def test_map_keeps_local_default_when_local_markers_are_three_or_more(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-local-1",
                    "title": "Portland utility pressure update",
                    "summary": "Utility pressure increased in Portland neighborhoods.",
                    "category": "housing",
                    "score": 82,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-local-1"],
                    "source_urls": ["https://example.com/local-1"],
                    "source_records": [{"source_record_id": "src-local-1", "source_url": "https://example.com/local-1", "url": "https://example.com/local-1", "publisher": "Example", "published_at": "2026-05-01T12:00:00Z", "state_hint": "OR", "geography": "Portland", "lat": 45.52, "lon": -122.67, "category_hint": "housing"}],
                },
                {
                    "story_id": "story-local-2",
                    "title": "Boise transit reliability issues",
                    "summary": "Transit delays are affecting Boise commuters.",
                    "category": "transportation",
                    "score": 80,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-local-2"],
                    "source_urls": ["https://example.com/local-2"],
                    "source_records": [{"source_record_id": "src-local-2", "source_url": "https://example.com/local-2", "url": "https://example.com/local-2", "publisher": "Example", "published_at": "2026-05-01T12:00:00Z", "state_hint": "ID", "geography": "Boise", "lat": 43.61, "lon": -116.2, "category_hint": "transportation"}],
                },
                {
                    "story_id": "story-local-3",
                    "title": "Tacoma service staffing shortage",
                    "summary": "Public service staffing strain reported in Tacoma.",
                    "category": "government",
                    "score": 78,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-local-3"],
                    "source_urls": ["https://example.com/local-3"],
                    "source_records": [{"source_record_id": "src-local-3", "source_url": "https://example.com/local-3", "url": "https://example.com/local-3", "publisher": "Example", "published_at": "2026-05-01T12:00:00Z", "state_hint": "WA", "geography": "Tacoma", "lat": 47.25, "lon": -122.44, "category_hint": "government"}],
                },
                {
                    "story_id": "story-regional-1",
                    "title": "Washington statewide drought warning",
                    "summary": "Drought warning affects statewide planning.",
                    "category": "wildfire",
                    "score": 76,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-regional-1"],
                    "source_urls": ["https://example.com/regional-1"],
                    "source_records": [{"source_record_id": "src-regional-1", "source_url": "https://example.com/regional-1", "url": "https://example.com/regional-1", "publisher": "Example", "published_at": "2026-05-01T12:00:00Z", "state_hint": "WA", "geography": "WA", "category_hint": "wildfire"}],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert len(map_data["markers"]) >= 3
    assert len(map_data["regional_reports"]) >= 1
    assert map_data.get("show_regional_default") is False
    assert map_data.get("default_view_mode") == "local"
    assert map_data.get("diagnostics", {}).get("default_view_mode") == "local"
    assert map_data.get("diagnostics", {}).get("local_marker_count") == len(map_data["markers"])
    assert map_data.get("diagnostics", {}).get("regional_report_count") == len(map_data["regional_reports"])
    assert map_data.get("diagnostics", {}).get("initial_visible_count", 0) >= len(map_data.get("grouped_markers") or map_data["markers"])
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    assert "draw(defaultRows());" in map_html
    assert "data-render-attempted-count" in map_html
    assert "data-rendered-marker-count" in map_html
    assert "data-render-error-count" in map_html
    assert "try {" in map_html
    assert "console.warn('map marker render failed', err);" in map_html
    assert "if (time !== 'all')" in map_html
    assert "const dt = publishedDate(item);" in map_html


def test_map_sparse_local_plus_regional_default_mode(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-local-1",
                    "title": "Portland transit cuts",
                    "summary": "Transit access changes affecting riders.",
                    "category": "transportation",
                    "score": 88,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-local-1"],
                    "source_urls": ["https://example.com/local-transit"],
                    "source_records": [{"source_record_id": "src-local-1", "source_url": "https://example.com/local-transit", "url": "https://example.com/local-transit", "publisher": "Example", "published_at": "2026-05-02T12:00:00Z", "state_hint": "OR", "geography": "Portland", "lat": 45.52, "lon": -122.68, "category_hint": "transportation"}],
                },
                {
                    "story_id": "story-regional-1",
                    "title": "Washington statewide drought warning",
                    "summary": "Drought warning affects statewide planning.",
                    "category": "wildfire",
                    "score": 80,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-regional-1"],
                    "source_urls": ["https://example.com/wa-drought"],
                    "source_records": [{"source_record_id": "src-regional-1", "source_url": "https://example.com/wa-drought", "url": "https://example.com/wa-drought", "publisher": "Example", "published_at": "2026-05-02T12:00:00Z", "state_hint": "WA", "geography": "WA", "category_hint": "wildfire"}],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert len(map_data["markers"]) == 1
    assert len(map_data["regional_reports"]) == 1
    assert map_data.get("show_regional_default") is True
    assert map_data.get("default_view_mode") == "sparse_local_plus_regional"
    assert map_data.get("diagnostics", {}).get("default_view_mode") == "sparse_local_plus_regional"
    assert map_data.get("diagnostics", {}).get("local_marker_count") == 1
    assert map_data.get("diagnostics", {}).get("regional_report_count") == 1
    assert map_data.get("diagnostics", {}).get("default_show_regional") is True
    assert map_data.get("diagnostics", {}).get("initial_render_layer") == "local_plus_regional"
    assert map_data.get("diagnostics", {}).get("initial_visible_count", 0) > len(map_data["grouped_markers"])
    diagnostics = map_data.get("diagnostics", {})
    assert "top_sources_local_markers" in diagnostics
    assert "sources_only_regional_reports" in diagnostics
    assert "sources_with_no_local_mappable_reports" in diagnostics
    assert "top_missing_place_reasons" in diagnostics
    assert "recommended_source_additions" in diagnostics
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    assert "Only a small number of local reports met the mapping rules this week. Regional/statewide reports are shown for context." in map_html
    assert 'id="emptyState"' in map_html
    assert "hidden" in map_html
    assert "Report count:" in map_html
    assert "function defaultNoteText()" in map_html
    assert "if (defaultViewMode === 'sparse_local_plus_regional') return sparseNote;" in map_html
    assert "function defaultRows()" in map_html
    assert "draw(defaultRows());" in map_html
    assert "L.AwesomeMarkers.icon" not in map_html
    assert "localMarkerHtml" in map_html
    assert "function validCoordinate(item)" in map_html
    assert "function markerLatLon(item)" in map_html
    assert "if (!validCoordinate(item))" in map_html
    assert "data-invalid-coordinate-count" in map_html
    assert "data-initial-visible-count" in map_html
    assert "console.warn('map marker render failed', err);" in map_html
    assert "if (time !== 'all')" in map_html
    assert "data-initial-row-count" in map_html
    assert "data-post-filter-count" in map_html
    assert "getEl('showRegional').checked = showRegionalDefault;" in map_html
    assert "function resetToDefaultView()" in map_html
    assert "const DEFAULT_CENTER = [45.8, -120.5];" in map_html
    assert "map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);" in map_html
    assert "controlsReady()" in map_html
    assert "resetBtn.addEventListener('click', resetToDefaultView);" in map_html


def test_map_pressure_category_corrections_and_precision_alignment(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-health",
                    "title": "Clinic provider cuts reduce appointment access",
                    "summary": "Hospital and clinic access issues are expanding.",
                    "category": "misc",
                    "score": 90,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-health"],
                    "source_urls": ["https://example.com/clinic-cuts"],
                    "source_records": [
                        {
                            "source_record_id": "src-health",
                            "source_url": "https://example.com/clinic-cuts",
                            "url": "https://example.com/clinic-cuts",
                            "publisher": "Example",
                            "published_at": "2026-05-02T12:00:00Z",
                            "state_hint": "ID",
                            "geography": "Boise",
                            "lat": 43.61,
                            "lon": -116.2,
                            "category_hint": "unknown",
                        }
                    ],
                },
                {
                    "story_id": "story-housing",
                    "title": "Rent and utility burden grows in county",
                    "summary": "Housing and utility costs are increasing.",
                    "category": "misc",
                    "score": 85,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-housing"],
                    "source_urls": ["https://example.com/rent-utility"],
                    "source_records": [
                        {
                            "source_record_id": "src-housing",
                            "source_url": "https://example.com/rent-utility",
                            "url": "https://example.com/rent-utility",
                            "publisher": "Example",
                            "published_at": "2026-05-02T12:00:00Z",
                            "state_hint": "WA",
                            "geography": "King County",
                            "lat": 47.6,
                            "lon": -122.3,
                            "category_hint": "unknown",
                        }
                    ],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    labels = {marker["title"]: marker["pressure_type"] for marker in map_data["markers"]}
    assert labels["Clinic provider cuts reduce appointment access"] == "Healthcare"
    assert labels["Rent and utility burden grows in county"] == "Housing and homelessness"
    for marker in map_data["markers"]:
        if marker["coordinate_basis"] == "state_centroid":
            assert marker["location_precision"] in {"statewide", "regional"}
            assert marker["precision_note"] in {"Statewide report.", "Regional report."}


def test_category_and_state_sanity_reject_weak_or_misclassified_records(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "source_record_id": "nat-school-1",
            "canonical_url": "https://example.com/national-bill",
            "title": "National school gender bill advances",
            "summary_or_snippet": "National coverage update.",
            "text": "National legislation update with no housing relevance described.",
            "publisher": "Example National",
            "source_id": "cascadia-manual",
            "state_hint": "WA",
            "category_hint": "Housing and homelessness",
        },
        {
            "source_record_id": "utah-drought-1",
            "canonical_url": "https://example.com/utah-drought",
            "title": "Utah drought worsens",
            "summary_or_snippet": "Utah drought update only.",
            "text": "Utah drought coverage without Idaho linkage.",
            "publisher": "Example",
            "source_id": "cascadia-manual",
            "state_hint": "ID",
            "category_hint": "Environment and climate",
        },
    ]
    (normalized_dir / "normalized_sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    result = curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    reasons = {item["source_record_ids"][0]: item.get("excluded_reason") for item in curated}
    assert reasons["nat-school-1"] in {"unsupported_category_or_weak_category_match", "geography_state_inferred_only_from_feed"}
    assert reasons["utah-drought-1"] in {"out_of_region_state_story_without_cascadia_impact", "geography_state_inferred_only_from_feed"}
    assert result.get("rejected_reasons")


def test_utah_story_rejected_without_explicit_cascadia_impact(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "source_record_id": "utah-plain-1",
            "canonical_url": "https://example.com/utah-drought-plain",
            "title": "Governor declares drought emergency in Utah",
            "summary_or_snippet": "Utah reservoir levels continue to decline.",
            "text": "Utah emergency declaration with no Washington, Oregon, or Idaho impact details.",
            "publisher": "Example",
            "source_id": "cascadia-manual",
            "state_hint": "ID",
            "category_hint": "Environment and climate",
        }
    ]
    (normalized_dir / "normalized_sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["excluded_reason"] == "out_of_region_state_story_without_cascadia_impact"


def test_regional_read_excludes_weak_and_dateline_joined_summaries(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-weak",
                    "title": "Weak national policy item",
                    "summary": "WASHINGTON — The U.S. House passes a national education policy bill.",
                    "category": "Government and public services",
                    "score": 90,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "excluded_reason": None,
                    "eligibility_diagnostics": ["category_not_supported_by_content"],
                    "source_record_ids": ["src-weak"],
                    "source_urls": ["https://example.com/weak"],
                    "source_records": [{"source_record_id": "src-weak", "source_url": "https://example.com/weak", "url": "https://example.com/weak", "publisher": "Example", "published_at": "2026-05-02T12:00:00Z", "state_hint": "WA", "category_hint": "government"}],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    render_cascadia_edition(cascadia_work_root, "2026-05-03", run_date="2026-05-04", coverage_start="2026-04-27", coverage_end="2026-05-03", briefing_type="weekly")
    html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "This week’s qualifying source-backed records point to limited regional systems signals. Coverage remains partial." in html
    regional_block = html.split("<h2>Regional Read</h2>", 1)[1].split("</section>", 1)[0]
    assert "WASHINGTON —" not in regional_block
    assert "move nearly 200 megawatts of power from a wind." not in regional_block


def test_is_complete_public_sentence_rejects_truncated_wind_fragment():
    from bluefern_dispatches.cascadia_render import is_complete_public_sentence

    assert is_complete_public_sentence("On Tuesday, the company signed a long-term contract with Avangrid to move nearly 200 megawatts of power from a wind.") is False
    assert is_complete_public_sentence("Washington agencies published a utility restoration planning update affecting local outage readiness.") is True
    assert is_complete_public_sentence("The utility expanded output from a solar.") is False
    assert is_complete_public_sentence("The utility expanded output from a project.") is False
    assert is_complete_public_sentence("The utility expanded output from a facility.") is False
    assert is_complete_public_sentence("In a letter to Gov.") is False
    assert is_complete_public_sentence("including several who pushed to.") is False


def test_clean_public_summary_sentences_drops_truncated_second_sentence():
    from bluefern_dispatches.cascadia_render import clean_public_summary_sentences

    text = (
        "Puget Sound Energy is adding a wind farm in Klickitat County to its clean energy portfolio, "
        "the latest move in the utility’s transition to become greenhouse gas neutral by 2030, as state law mandates. "
        "On Tuesday, the company signed a long-term contract with Avangrid to move nearly 200 megawatts of power from a wind."
    )
    cleaned = clean_public_summary_sentences(text, max_sentences=2)
    assert cleaned == (
        "Puget Sound Energy is adding a wind farm in Klickitat County to its clean energy portfolio, "
        "the latest move in the utility’s transition to become greenhouse gas neutral by 2030, as state law mandates."
    )

def test_clean_public_summary_sentences_strips_correction_lead_and_trailing_fragments():
    from bluefern_dispatches.cascadia_render import clean_public_summary_sentences

    text = (
        "Correction: This story has been corrected to fix an earlier attribution. "
        "Idaho county officials published updated voter-operations guidance for this cycle. "
        "including several who pushed to."
    )
    assert clean_public_summary_sentences(text, max_sentences=2) == (
        "Idaho county officials published updated voter-operations guidance for this cycle."
    )


def test_rendered_output_omits_old_generic_public_systems_phrase(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(cascadia_work_root, "2026-05-03", run_date="2026-05-04", coverage_start="2026-04-27", coverage_end="2026-05-03", briefing_type="weekly")
    html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    assert "Public systems signals can affect" not in html


def test_2026_05_24_live_defects_are_blocked_from_rendered_outputs(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-24"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "source_record_id": "src-utah-live-defect",
            "source_id": "cascadia-manual",
            "title": "Governor declares drought emergency as Utah dips into reservoir ‘savings’",
            "summary_or_snippet": "Gov. Spencer Cox declared a state of emergency Thursday, noting every county is in a state of severe or extreme drought.",
            "text": "Utah counties are listed under severe and extreme drought conditions.",
            "canonical_url": "https://example.com/utah-live-defect",
            "publisher": "Idaho Capital Sun Feed",
            "published_at": "2026-05-24T12:00:00Z",
            "category_hint": "Environment and climate",
            "state_hint": "ID",
        },
        {
            "source_record_id": "src-wind-fragment",
            "source_id": "cascadia-manual",
            "title": "Regional utility contract update",
            "summary_or_snippet": "On Tuesday, the company signed a long-term contract with Avangrid to move nearly 200 megawatts of power from a wind.",
            "text": "On Tuesday, the company signed a long-term contract with Avangrid to move nearly 200 megawatts of power from a wind.",
            "canonical_url": "https://example.com/wind-fragment",
            "publisher": "Example",
            "published_at": "2026-05-24T11:00:00Z",
            "category_hint": "Energy and utilities",
            "state_hint": "OR",
        },
        {
            "source_record_id": "src-valid-wa",
            "source_id": "cascadia-manual",
            "title": "Washington utility restoration planning expands",
            "summary_or_snippet": "Washington agencies published a utility restoration planning update affecting local outage readiness.",
            "text": "Washington agencies published a utility restoration planning update affecting local outage readiness.",
            "canonical_url": "https://example.com/wa-valid",
            "publisher": "Example WA",
            "published_at": "2026-05-24T10:00:00Z",
            "category_hint": "Energy and utilities",
            "state_hint": "WA",
        },
    ]
    (normalized_dir / "normalized_sources.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    curate_sources(cascadia_work_root, "2026-05-24")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-24",
        run_date="2026-05-25",
        coverage_start="2026-05-18",
        coverage_end="2026-05-24",
        briefing_type="weekly",
    )
    html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-24" / "index.html").read_text(encoding="utf-8")
    source_table = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-24" / "source_table.html").read_text(encoding="utf-8")
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-24" / "map.html").read_text(encoding="utf-8")
    manifest = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-24" / "edition_manifest.json")
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-24" / "map_data.json")
    assert "Governor declares drought emergency as Utah" not in html
    assert "Spencer Cox" not in html
    regional_block = html.split("<h2>Regional Read</h2>", 1)[1].split("</section>", 1)[0]
    assert "from a wind." not in regional_block
    assert "Public systems signals can affect" not in html
    assert "from a wind." not in html
    assert "from a solar." not in html
    assert "from a project." not in html
    assert "from a facility." not in html
    assert "from a plant." not in html
    assert "from a site." not in html
    assert "Governor declares drought emergency as Utah" not in source_table
    assert "Spencer Cox" not in source_table
    assert "Open latest Cascadia map" not in source_table
    assert "Correction:" not in html
    assert "In a letter to Gov." not in html
    assert "including several who pushed to." not in html
    assert "from a wind." not in source_table
    assert "from a solar." not in source_table
    assert "from a project." not in source_table
    assert "from a facility." not in source_table
    assert "from a plant." not in source_table
    assert "from a site." not in source_table
    map_payload_text = json.dumps(map_data)
    assert "from a wind." not in map_payload_text
    assert "from a solar." not in map_payload_text
    assert "from a project." not in map_payload_text
    assert "from a facility." not in map_payload_text
    assert "from a plant." not in map_payload_text
    assert "from a site." not in map_payload_text
    assert not any("utah" in str(item.get("title", "")).lower() for item in map_data.get("markers", []))
    assert not any("utah" in str(item.get("title", "")).lower() for item in map_data.get("regional_reports", []))
    assert f"Report count: {manifest['public_story_count']}" in map_html
    public_source_rows = max(0, source_table.count("<tr>") - 1)
    assert manifest["public_story_count"] == 1
    assert manifest["public_story_count"] == public_source_rows
    assert manifest["public_story_count"] == len(map_data.get("markers", [])) + len(map_data.get("regional_reports", []))
    map_source_table = (cascadia_work_root / "output" / "site" / "cascadia" / "map" / "source_table.html").read_text(encoding="utf-8")
    assert "Governor declares drought emergency as Utah" not in map_source_table
    assert "gender ideology" not in map_source_table.lower()


def test_rendered_story_summary_keeps_complete_first_sentence_and_drops_truncated_second(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-24"
    curated_dir.mkdir(parents=True, exist_ok=True)
    title = "Puget Sound Energy adds a large wind farm in south-central Washington to its portfolio"
    summary = (
        "Puget Sound Energy is adding a wind farm in Klickitat County to its clean energy portfolio, "
        "the latest move in the utility’s transition to become greenhouse gas neutral by 2030, as state law mandates. "
        "On Tuesday, the company signed a long-term contract with Avangrid to move nearly 200 megawatts of power from a wind."
    )
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-pse-wind",
                    "title": title,
                    "summary": summary,
                    "category": "Energy and utilities",
                    "score": 91,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "excluded_reason": None,
                    "source_record_ids": ["src-pse-wind"],
                    "source_urls": ["https://example.com/pse-wind"],
                    "source_records": [
                        {
                            "source_record_id": "src-pse-wind",
                            "source_url": "https://example.com/pse-wind",
                            "url": "https://example.com/pse-wind",
                            "publisher": "Example WA",
                            "published_at": "2026-05-24T10:00:00Z",
                            "state_hint": "WA",
                            "category_hint": "Energy and utilities",
                            "summary_or_snippet": summary,
                            "title": title,
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-24",
        run_date="2026-05-25",
        coverage_start="2026-05-18",
        coverage_end="2026-05-24",
        briefing_type="weekly",
    )
    html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-24" / "index.html").read_text(encoding="utf-8")
    expected = (
        "Puget Sound Energy is adding a wind farm in Klickitat County to its clean energy portfolio, "
        "the latest move in the utility’s transition to become greenhouse gas neutral by 2030, as state law mandates."
    )
    assert expected in html
    assert "move nearly 200 megawatts of power from a wind." not in html


def test_map_page_uses_server_rendered_report_count_and_hidden_initial_warnings(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    assert "Report count: loading" not in map_html
    assert "No matching reports" not in map_html
    assert "Some map markers are temporarily unavailable" not in map_html
    assert 'id="emptyState" class="empty-state" hidden' in map_html
    assert 'id="renderWarning" class="empty-state" hidden' in map_html


def test_edition_and_latest_map_source_tables_use_same_renderer(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    edition_table = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "source_table.html").read_text(encoding="utf-8")
    map_table = (cascadia_work_root / "output" / "site" / "cascadia" / "map" / "source_table.html").read_text(encoding="utf-8")
    assert edition_table == map_table


def test_render_cleans_stale_cascadia_map_artifacts_before_write(cascadia_work_root):
    stale_map_dir = cascadia_work_root / "output" / "site" / "cascadia" / "map"
    stale_map_dir.mkdir(parents=True, exist_ok=True)
    (stale_map_dir / "index.html").write_text("<html><body>Report count: 11</body></html>", encoding="utf-8")
    (stale_map_dir / "source_table.html").write_text(
        "<html><body>Open latest Cascadia map WA government ID government</body></html>",
        encoding="utf-8",
    )
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    map_html = (stale_map_dir / "index.html").read_text(encoding="utf-8")
    map_table = (stale_map_dir / "source_table.html").read_text(encoding="utf-8")
    assert "Report count: 11" not in map_html
    assert "Open latest Cascadia map" not in map_table
    assert "WA government" not in map_table
    assert "ID government" not in map_table


def test_landing_recent_metadata_uses_same_public_story_count_as_edition_manifest(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    manifest = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "edition_manifest.json")
    landing = (cascadia_work_root / "output" / "site" / "cascadia" / "index.html").read_text(encoding="utf-8")
    assert str(manifest.get("public_archive_subtitle") or "") in landing


def test_category_labels_match_across_edition_source_table_and_map(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    curation = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "curation_manifest.json")
    source_table = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "source_table.html").read_text(encoding="utf-8")
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    public_categories = {str(item.get("category") or "") for item in curation if item.get("included_in_public_summary") and not item.get("excluded_reason")}
    map_categories = {str(item.get("category") or "") for item in map_data.get("markers", [])} | {str(item.get("category") or "") for item in map_data.get("regional_reports", [])}
    assert public_categories
    assert map_categories.issubset(public_categories)
    for category in public_categories:
        assert category in source_table


def test_map_legend_uses_registry_labels_from_final_map_payload(cascadia_work_root):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    category_pairs = {
        (str(row.get("category_id") or ""), str(row.get("category_label") or ""))
        for row in (map_data.get("markers") or []) + (map_data.get("regional_reports") or [])
        if str(row.get("category_id") or "") and str(row.get("category_label") or "")
    }
    assert category_pairs
    for _category_id, category_label in category_pairs:
        assert category_label in map_html
    legacy = [
        "Housing and utility pressure",
        "Health care access",
        "Jobs and local economy",
        "Food and household support",
        "Transportation and access",
        "Public safety and emergency services",
        "Wildfire, drought, flood, and recovery",
        "Schools and local government services",
    ]
    for label in legacy:
        assert label not in map_html


def test_map_legend_excludes_registry_categories_not_present_in_payload():
    map_payload = {
        "markers": [
            {"category_id": "government_public_services", "category_label": "Government and public services"},
            {"category_id": "energy_utilities", "category_label": "Energy and utilities"},
            {"category_id": "public_safety", "category_label": "Public safety"},
        ],
        "regional_reports": [
            {"category_id": "environment_climate", "category_label": "Environment and climate"},
        ],
    }
    map_html = render_map_html(
        "2026-05-24",
        "note",
        "source_table.html",
        initial_report_count=5,
        map_payload=map_payload,
    )
    assert "Government and public services" in map_html
    assert "Energy and utilities" in map_html
    assert "Environment and climate" in map_html
    assert "Public safety" in map_html
    assert "Housing and homelessness" not in map_html
    assert "optionize('pressureFilter', [...new Set(markers.map((m) => m.category_id).filter(Boolean))].sort()" in map_html


def test_transgender_prisoner_policy_story_not_housing(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-prison-trans-1",
                    "source_id": "cascadia-manual",
                    "title": "WA’s transgender prisoner policy is target of new federal investigation",
                    "summary_or_snippet": "Federal investigators requested records on correctional policy and detention oversight.",
                    "text": "The inquiry focuses on correctional administration and legal oversight in Washington state agencies.",
                    "canonical_url": "https://example.com/trans-prison-policy",
                    "publisher": "Example WA",
                    "published_at": "2026-05-02T10:00:00Z",
                    "category_hint": "Housing and homelessness",
                    "state_hint": "WA",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["category"] != "Housing and homelessness"


def test_prison_story_is_not_classified_as_housing(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-prison-1",
                    "source_id": "cascadia-manual",
                    "title": "State correctional housing unit policy update",
                    "summary_or_snippet": "Prison housing unit changes affect inmate assignment.",
                    "text": "Correctional detention and prison staffing update.",
                    "canonical_url": "https://example.com/prison-housing",
                    "publisher": "Example",
                    "published_at": "2026-05-02T10:00:00Z",
                    "category_hint": "Housing and homelessness",
                    "state_hint": "WA",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["category"] != "Housing and homelessness"
    assert curated[0]["included_in_public_summary"] is False


def test_election_story_requires_service_or_budget_relevance(cascadia_work_root):
    normalized_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "normalized" / "2026-05-03"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    (normalized_dir / "normalized_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "src-election-1",
                    "source_id": "cascadia-manual",
                    "title": "County election campaign heats up",
                    "summary_or_snippet": "Candidates debated polling trends and endorsements.",
                    "text": "Election campaign coverage focused on candidate messaging.",
                    "canonical_url": "https://example.com/election-campaign",
                    "publisher": "Example",
                    "published_at": "2026-05-02T10:00:00Z",
                    "category_hint": "Government and public services",
                    "state_hint": "OR",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    curate_sources(cascadia_work_root, "2026-05-03")
    curated = read_json(cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03" / "curation_manifest.json")
    assert curated[0]["excluded_reason"] == "unsupported_category_or_weak_category_match"


def test_map_extracts_local_place_from_title_and_emits_extraction_diagnostics(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-spokane",
                    "title": "Spokane Transit service reductions this week",
                    "summary": "Service reductions may affect household and job access.",
                    "category": "transportation",
                    "score": 84,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-spokane"],
                    "source_urls": ["https://example.com/spokane-transit"],
                    "source_records": [
                        {
                            "source_record_id": "src-spokane",
                            "source_url": "https://example.com/spokane-transit",
                            "url": "https://example.com/spokane-transit",
                            "publisher": "Example",
                            "published_at": "2026-05-02T12:00:00Z",
                            "state_hint": "WA",
                            "category_hint": "transportation",
                        }
                    ],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    assert len(map_data["markers"]) >= 1
    assert map_data["markers"][0]["place"] in {"Spokane Transit", "Spokane"}
    diagnostics = map_data["diagnostics"]
    assert diagnostics["local_extraction_success_count"] >= 1
    assert diagnostics["place_extraction_attempted"] >= 1
    assert diagnostics["place_extraction_succeeded"] >= 1
    assert "candidate_diagnostics_rows" in diagnostics
    assert "extraction_fields_success" in diagnostics
    assert "extraction_fields_failed" in diagnostics


def test_map_extracts_county_and_facility_places(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-county",
                    "title": "King County shelter capacity strains continue",
                    "summary": "King County services report pressure this week.",
                    "category": "housing",
                    "score": 83,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-county"],
                    "source_urls": ["https://example.com/king-county-shelter"],
                    "source_records": [{"source_record_id": "src-county", "source_url": "https://example.com/king-county-shelter", "url": "https://example.com/king-county-shelter", "publisher": "Example", "published_at": "2026-05-02T12:00:00Z", "state_hint": "WA", "category_hint": "housing"}],
                },
                {
                    "story_id": "story-facility",
                    "title": "TriMet route adjustments affect riders",
                    "summary": "Transit service updates announced for this week.",
                    "category": "transportation",
                    "score": 81,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": False,
                    "source_record_ids": ["src-facility"],
                    "source_urls": ["https://example.com/trimet-routes"],
                    "source_records": [{"source_record_id": "src-facility", "source_url": "https://example.com/trimet-routes", "url": "https://example.com/trimet-routes", "publisher": "Example", "published_at": "2026-05-02T12:00:00Z", "state_hint": "OR", "category_hint": "transportation"}],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    places = {marker["title"]: marker["place"] for marker in map_data["markers"]}
    assert places["King County shelter capacity strains continue"] == "King County"
    assert places["TriMet route adjustments affect riders"] == "Portland"
    diagnostics = map_data["diagnostics"]
    assert diagnostics["place_match_by_type"].get("county_hint", 0) + diagnostics["place_match_by_type"].get("pattern", 0) >= 1
    assert diagnostics["place_match_by_type"].get("entity_hint", 0) >= 1


def test_registry_diagnostics_only_source_not_used_for_public_candidates(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: diag-only-feed
    name: Diagnostics Only Feed
    tier: 2
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    diagnostics_only: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [infrastructure]
    reliability_tier: test
    publisher: Example
    refresh_mode: archive_limited
""",
        encoding="utf-8",
    )
    feed = """<?xml version="1.0"?><rss><channel>
<item><title>Washington infrastructure update</title><link>https://example.com/story</link><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Infrastructure systems update.</description></item>
</channel></rss>"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return feed.encode("utf-8")

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())
    week_start, week_end = containing_week("2026-04-28")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    assert result["records"] == []
    assert result["report"]["registry_sources_run"] == 0


def test_registry_manual_only_source_skipped_in_normal_collection(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: manual-only-feed
    name: Manual Only Feed
    tier: 2
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    status: manual_only
    state_scope: WA
    geographic_scope: Washington
    category_hints: [infrastructure]
    reliability_tier: test
    publisher: Example
    refresh_mode: archive_limited
""",
        encoding="utf-8",
    )
    feed = """<?xml version="1.0"?><rss><channel>
<item><title>Washington infrastructure update</title><link>https://example.com/story</link><pubDate>Tue, 28 Apr 2026 12:00:00 GMT</pubDate><description>Infrastructure systems update.</description></item>
</channel></rss>"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return feed.encode("utf-8")

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())
    week_start, week_end = containing_week("2026-04-28")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    assert result["records"] == []
    assert result["report"]["registry_sources_run"] == 0


def test_registry_date_basis_does_not_treat_retrieved_at_as_published_date(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: feed-no-pubdate
    name: Feed Missing Published Date
    tier: 2
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [infrastructure]
    reliability_tier: test
    publisher: Example
    refresh_mode: current
""",
        encoding="utf-8",
    )
    feed = """<?xml version="1.0"?><rss><channel>
<item><title>Washington bridge alert</title><link>https://example.com/story</link><description>Infrastructure bridge systems update.</description></item>
</channel></rss>"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return feed.encode("utf-8")

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())
    week_start, week_end = containing_week("2026-04-28")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["date_basis"] == "retrieved_only"
    assert record["date_basis_confidence"] == "low"
    assert "retrieved_at is not evidence" in record["date_basis_note"]


def test_registry_operational_statuses_and_disabled_reasons_reported(cascadia_work_root):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: stale-disabled
    name: Stale Disabled
    tier: 1
    source_type: rss
    url: https://example.com/stale.xml
    enabled: true
    operational_status: disabled_stale_url
    status_reason: repeated 404
  - source_id: review-feed
    name: Review Feed
    tier: 1
    source_type: rss
    url: https://example.com/review.xml
    enabled: true
    operational_status: needs_manual_review
""",
        encoding="utf-8",
    )
    week_start, week_end = containing_week("2026-04-28")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    status_counts = result["report"]["source_status_counts"]
    assert status_counts.get("disabled_stale_url") == 1
    assert status_counts.get("needs_manual_review") == 1
    disabled_list = result["report"]["source_health_summary"]["disabled_or_replaced_sources"]
    assert any(item["source_id"] == "stale-disabled" and item["reason"] == "repeated 404" for item in disabled_list)
    assert source_operational_state({"enabled": True, "operational_status": "needs_manual_review"}) == "needs_manual_review"


def test_registry_warning_summary_dedupes_weak_date_lines(cascadia_work_root, monkeypatch):
    registry_path = cascadia_work_root / "data" / "dispatches" / "cascadia" / "source_registry.yml"
    registry_path.write_text(
        """
sources:
  - source_id: weak-date-feed
    name: Weak Date Feed
    tier: 1
    source_type: rss
    url: https://example.com/feed.xml
    enabled: true
    state_scope: WA
    geographic_scope: Washington
    category_hints: [infrastructure]
    reliability_tier: test
    publisher: Example
    refresh_mode: current
""",
        encoding="utf-8",
    )
    feed = """<?xml version="1.0"?><rss><channel>
<item><title>Washington infrastructure alert one</title><link>https://example.com/a</link><description>Infrastructure service disruption.</description></item>
<item><title>Washington infrastructure alert two</title><link>https://example.com/b</link><description>Infrastructure service disruption.</description></item>
</channel></rss>"""

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return feed.encode("utf-8")

    monkeypatch.setattr("bluefern_dispatches.cascadia_fetch.urllib.request.urlopen", lambda request, timeout: FakeResponse())
    week_start, week_end = containing_week("2026-04-28")
    result = collect_registry_sources(cascadia_work_root, week_start, week_end, retrieved_at="2026-05-08T12:00:00Z")
    assert any("weak date basis warnings (deduped): 2 item(s)" in warning for warning in result["warnings"])
    assert any("weak date basis" in warning for warning in result["report"]["warnings_detailed"])


def test_gdelt_query_guardrails_keep_queries_bounded(cascadia_work_root):
    config = load_historical_config(cascadia_work_root)
    config["query_groups"]["region_terms"] = [f"RegionTerm{i}" for i in range(1, 80)]
    config["query_groups"]["system_groups"] = [
        "oversized|" + "|".join(f"system term {i}" for i in range(1, 80)),
    ]
    queries = build_queries(config)
    assert queries
    assert all(len(query) >= 40 for query in queries)
    assert all(len(query) <= 450 for query in queries)


def test_historical_warning_summary_dedupes_repeated_provider_warnings(cascadia_work_root, monkeypatch):
    def fake_collect_registry_sources(root, week_start_arg, week_end_arg, retrieved_at=None, refresh_cache=False):
        return {
            "records": [],
            "warnings": [
                "invalid JSON response: Expecting value: line 1 column 1 (char 0)",
                "invalid JSON response: Expecting value: line 1 column 1 (char 0)",
            ],
            "errors": ["HTTP Error 404: Not Found", "HTTP Error 404: Not Found"],
            "report": {
                "registry_sources_planned": 2,
                "registry_sources_run": 2,
                "registry_cache_hits": 0,
                "registry_cache_misses": 2,
                "registry_fetch_errors": 2,
                "registry_records_raw": 0,
                "registry_records_saved": 0,
                "registry_records_excluded": 0,
                "registry_exclusion_reasons": {},
                "records_by_source_id": {},
                "records_by_tier": {},
                "records_by_category_hint": {},
                "official_pages_planned": 0,
                "official_pages_run": 0,
                "official_links_found": 0,
                "official_links_saved": 0,
                "official_links_excluded": 0,
                "official_exclusion_reasons": {},
                "weak_date_count": 0,
                "same_domain_links_only": True,
                "unsupported_source_type_count": 0,
                "diagnostics": [],
            },
        }

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.collect_registry_sources", fake_collect_registry_sources)
    week_start, week_end = containing_week("2026-05-17")
    result = retrieve_historical_sources(
        cascadia_work_root,
        week_start=week_start,
        week_end=week_end,
        run_date="2026-05-24",
        edition_date="2026-05-17",
        historical_provider="registry",
        dry_run=True,
    )
    assert any("registry fetch errors: 2" in warning for warning in result["warnings"])
    assert result["warnings"].count("invalid JSON response: Expecting value: line 1 column 1 (char 0)") == 1
    assert result["warnings"].count("registry source fetch error: HTTP Error 404: Not Found") == 1
    assert "warnings_detailed" in result


def test_canonical_date_fields_normalize_mixed_inputs():
    fields = canonical_date_fields(
        published_at="Tue, 28 Apr 2026 12:00:00 GMT",
        retrieved_at="2026-04-29T01:02:03Z",
        coverage_start_date="2026-04-27",
        coverage_end_date="2026-05-03",
    )
    assert fields["event_date"] == "2026-04-28"
    assert str(fields["event_ts"]).startswith("2026-04-28T")
    assert fields["coverage_week"]
    bad = canonical_date_fields(published_at="not-a-date", retrieved_at="2026-04-29T01:02:03Z")
    assert bad["event_date"] is None
    assert bad["date_quality_reason"]


def test_cascadia_dates_falls_back_when_zoneinfo_data_missing(monkeypatch):
    import bluefern_dispatches.cascadia_dates as cascadia_dates
    import zoneinfo

    class MissingZone:
        def __init__(self, key):
            raise zoneinfo.ZoneInfoNotFoundError(key)

    monkeypatch.setattr(zoneinfo, "ZoneInfo", MissingZone)
    reloaded = importlib.reload(cascadia_dates)
    fields = reloaded.canonical_date_fields(
        published_at="2026-05-03T12:30:00Z",
        retrieved_at="2026-05-03T13:00:00Z",
        coverage_start_date="2026-04-27",
        coverage_end_date="2026-05-03",
    )
    assert fields["event_date"] == "2026-05-03"
    assert fields["event_ts"] is not None
    assert fields["coverage_week"] is not None
    importlib.reload(cascadia_dates)


def test_category_registry_maps_legacy_labels_to_ids():
    assert canonical_category_id("Environment and climate") == "environment_climate"
    assert canonical_category_id("government_public_services") == "government_public_services"
    assert category_label_for("energy_utilities") == "Energy and utilities"


def test_provider_diagnostics_present_on_failure(cascadia_work_root, monkeypatch):
    class FailingGDELT:
        provider_id = "gdelt"
        provider_name = "Failing GDELT"
        last_diagnostics = {"provider_id": "gdelt", "warnings": [], "errors": ["forced failure"]}

        def __init__(self, config, root=None, refresh_cache=False):
            pass

        def search(self, *args, **kwargs):
            raise RuntimeError("forced")

    monkeypatch.setattr("bluefern_dispatches.cascadia_historical_search.GDELTProvider", FailingGDELT)
    result = retrieve_historical_sources(cascadia_work_root, *containing_week("2026-04-28"), edition_date="2026-05-03", run_date="2026-05-11")
    assert "provider_diagnostics" in result["report"]
    gdelt_rows = [row for row in result["report"]["provider_diagnostics"] if row.get("provider_id") == "gdelt"]
    assert gdelt_rows
    assert gdelt_rows[0]["coverage_gap_warning"] is True


def test_artifact_validation_writes_reports_and_blocks_without_override(cascadia_work_root, monkeypatch):
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    source_table = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "source_table.html"
    source_table.write_text(source_table.read_text(encoding="utf-8") + "<!-- No matching reports -->", encoding="utf-8")
    report = run_cascadia_dispatch.validate_cascadia_artifacts(cascadia_work_root, "2026-05-03", dry_run=False)
    assert report["ok"] is False
    report_path = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "artifact_validation.json"
    md_path = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "artifact_validation.md"
    assert report_path.exists()
    assert md_path.exists()
    saved = read_json(report_path)
    assert saved["ok"] is False


def test_artifact_validation_rejected_titles_do_not_leak_into_public_map_payload(cascadia_work_root):
    curated_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "curated" / "2026-05-03"
    curated_dir.mkdir(parents=True, exist_ok=True)
    rejected_title = "Rejected Backfill Story"
    rejected_story_id = "story-rejected"
    rejected_source_id = "src-rejected"
    rejected_url = "https://example.com/rejected-story"
    curated_dir.joinpath("curation_manifest.json").write_text(
        json.dumps(
            [
                {
                    "story_id": "story-public",
                    "title": "Accepted Public Story",
                    "summary": "A source-backed local update for Cascadia households.",
                    "category": "transportation",
                    "score": 85,
                    "included_in_public_summary": True,
                    "included_in_detail_dataset": True,
                    "excluded_reason": None,
                    "source_record_ids": ["src-public"],
                    "source_urls": ["https://example.com/public-story"],
                    "source_records": [
                        {
                            "source_record_id": "src-public",
                            "source_url": "https://example.com/public-story",
                            "url": "https://example.com/public-story",
                            "publisher": "Example",
                            "published_at": "2026-05-02T12:00:00Z",
                            "state_hint": "WA",
                            "category_hint": "transportation",
                        }
                    ],
                },
                {
                    "story_id": rejected_story_id,
                    "title": rejected_title,
                    "summary": "This item was rejected for public output.",
                    "category": "public safety",
                    "score": 40,
                    "included_in_public_summary": False,
                    "included_in_detail_dataset": False,
                    "excluded_reason": "geography_state_inferred_only_from_feed",
                    "source_record_ids": [rejected_source_id],
                    "source_urls": [rejected_url],
                    "source_records": [
                        {
                            "source_record_id": rejected_source_id,
                            "source_url": rejected_url,
                            "url": rejected_url,
                            "publisher": "Example",
                            "published_at": "2026-05-02T13:00:00Z",
                            "state_hint": "WA",
                            "category_hint": "public safety",
                        }
                    ],
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    weekly_sources_dir = cascadia_work_root / "data" / "dispatches" / "cascadia" / "sources" / "2026-04-27_2026-05-03"
    weekly_sources_dir.mkdir(parents=True, exist_ok=True)
    weekly_sources_dir.joinpath("historical_sources.json").write_text(
        json.dumps(
            [
                {
                    "title": rejected_title,
                    "url": "https://example.com/rejected-backfill",
                    "publisher": "Example",
                    "published_at": None,
                    "summary": "Rejected candidate that should not appear in public map payload diagnostics.",
                    "state_hint": "WA",
                    "category_hint": "public safety",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    result = render_cascadia_edition(
        cascadia_work_root,
        "2026-05-03",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    report = run_cascadia_dispatch.validate_cascadia_artifacts(cascadia_work_root, "2026-05-03", dry_run=False)
    assert "rejected story leaked to map payload" not in report.get("failures", [])
    assert report["offending_rejected_records"] == []

    map_data = read_json(cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map_data.json")
    map_payload_text = json.dumps(map_data)
    edition_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "index.html").read_text(encoding="utf-8")
    source_table_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "source_table.html").read_text(encoding="utf-8")
    map_html = (cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "map.html").read_text(encoding="utf-8")
    assert rejected_story_id not in map_payload_text
    assert rejected_source_id not in map_payload_text
    assert rejected_url not in map_payload_text
    accepted_titles = {str(row.get("title") or "") for row in map_data.get("markers", []) + map_data.get("regional_reports", [])}
    assert "Accepted Public Story" in accepted_titles
    assert rejected_title not in edition_html
    assert rejected_title not in source_table_html
    assert rejected_title not in map_html
    assert rejected_source_id not in edition_html
    assert rejected_source_id not in source_table_html
    assert rejected_source_id not in map_html

    site_edition = cascadia_work_root / "output" / "site" / "cascadia" / "editions" / "2026-05-03"
    assert (site_edition / "curation_manifest.json").exists()
    assert (site_edition / "sources_manifest.json").exists()


def test_artifact_validation_override_allows_run(cascadia_work_root, monkeypatch):
    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    monkeypatch.setattr(
        run_cascadia_dispatch,
        "validate_cascadia_artifacts",
        lambda root, edition_date, dry_run=False: {
            "ok": False,
            "edition_date": edition_date,
            "failures": ["forced artifact failure"],
            "warnings": [],
        },
    )
    code_without = run_cascadia_dispatch.main(["--archive-week", "2026-05-03", "--weekly-public"])
    code_with = run_cascadia_dispatch.main(["--archive-week", "2026-05-03", "--weekly-public", "--allow-validation-failures"])
    assert code_without == 1
    assert code_with == 0


def test_weekly_run_pipeline_writes_new_detention_watch_site_artifacts(cascadia_work_root, monkeypatch):
    monkeypatch.setattr(run_cascadia_dispatch, "ROOT", cascadia_work_root)
    repo = Path(__file__).resolve().parents[1]
    baseline_src = repo / "data" / "dispatches" / "cascadia" / "detention_watch" / "baseline_2026-05-26.json"
    baseline_dst = cascadia_work_root / "data" / "dispatches" / "cascadia" / "detention_watch" / "baseline_2026-05-26.json"
    baseline_dst.parent.mkdir(parents=True, exist_ok=True)
    baseline_dst.write_text(baseline_src.read_text(encoding="utf-8"), encoding="utf-8")
    ingest_sources(cascadia_work_root, "2026-05-03")
    normalize_sources(cascadia_work_root, "2026-05-03")
    curate_sources(cascadia_work_root, "2026-05-03")
    result = run_pipeline(
        "2026-05-03",
        ingest=False,
        normalize=False,
        curate=False,
        render=True,
        dry_run=False,
        mode="weekly-public",
        run_date="2026-05-04",
        coverage_start="2026-04-27",
        coverage_end="2026-05-03",
        briefing_type="weekly",
    )
    assert result["ok"] is True
    watch_root = cascadia_work_root / "output" / "site" / "cascadia" / "detention-watch"
    landing = (watch_root / "index.html").read_text(encoding="utf-8")
    edition = (watch_root / "editions" / "2026-05-26" / "index.html").read_text(encoding="utf-8")
    source_table = (watch_root / "editions" / "2026-05-26" / "source_table.html").read_text(encoding="utf-8")
    assert "rss.xml" not in landing
    assert "Open latest starting record" not in landing
    assert "Current indicators" not in edition
    assert "Record status" in edition
    assert "Monitoring checklist" in edition
    assert "Facility profile" in edition
    assert (watch_root / "archive.html").exists()
    assert '<th scope="col">Source type</th>' in source_table
    assert '<th scope="col">Publisher / agency</th>' in source_table
    assert '<th scope="col">What this source supports</th>' in source_table
    assert '<th scope="col">Verification status</th>' in source_table
    assert '<th scope="col">Last checked</th>' in source_table
