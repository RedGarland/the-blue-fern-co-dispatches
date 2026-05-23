import json
import urllib.error
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from bluefern_dispatches.cascadia_curate import curate_sources, deterministic_summary, why_it_matters
from bluefern_dispatches.cascadia_fetch import curl_command, fetch_public_url
from bluefern_dispatches.cascadia_historical_search import PROVIDER_BACKOFF_UNTIL, GDELTProvider, HistoricalProviderRateLimited, build_queries, create_manual_source_template, dedupe_records, load_historical_config, retrieve_historical_sources, validate_manual_sources
from bluefern_dispatches.cascadia_ingest import ingest_sources, load_sources
from bluefern_dispatches.cascadia_normalize import normalize_sources
from bluefern_dispatches.cascadia_render import editorial_checklist, render_cascadia_edition, refresh_cascadia_archive_pages
from bluefern_dispatches.cascadia_signal import write_cascadia_signal_package
from bluefern_dispatches.cascadia_source_registry import collect_registry_sources, load_source_registry
from bluefern_dispatches.cascadia_weekly import aggregate_weekly_curation, containing_week, explicit_week, format_coverage_label, previous_completed_week
from bluefern_dispatches.generator import CASCADIA_LOGO_ASSET, build_site, publish_pages
from bluefern_dispatches.shared_records import update_shared_records

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from run_cascadia_dispatch import completed_week_windows, run_pipeline, run_source_gap_report, write_zero_week_gap_report


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


def test_why_it_matters_is_category_and_region_grounded():
    record = {
        "category_hint": "Transportation",
        "state_hint": "WA",
        "title": "Washington bridge inspection program",
        "summary_or_snippet": "Bridge inspection update.",
    }

    line = why_it_matters(record, "Transportation")

    assert line == "In Washington, Transportation signals can affect mobility, emergency access, freight movement, and infrastructure maintenance."
    assert "deaths" not in line.lower()
    assert "closure" not in line.lower()
    assert "cost" not in line.lower()


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
    assert "In Washington, Transportation signals can affect mobility" in html
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
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "map" / "index.html").exists()
    assert (cascadia_work_root / "output" / "site" / "cascadia" / "dashboard" / "index.html").exists()
    html = (site_edition / "index.html").read_text(encoding="utf-8")
    assert 'src="/cascadia/map/"' not in html
    assert "Open this week's interactive map" in html
    assert "Open latest Cascadia map" in html

    map_data = read_json(site_edition / "map_data.json")
    assert map_data["markers"]
    assert all(marker.get("source_url", "").startswith("http") for marker in map_data["markers"])
    assert all(marker.get("title") and marker.get("category") for marker in map_data["markers"])
    assert all(marker.get("state_or_region") and marker.get("publisher") for marker in map_data["markers"])
    assert all(marker.get("lat") is not None and marker.get("lon") is not None for marker in map_data["markers"])

    curation = read_json(site_edition / "curation_manifest.json")
    public_ids = {story["story_id"] for story in curation if story.get("included_in_public_summary")}
    excluded_ids = {story["story_id"] for story in curation if not story.get("included_in_public_summary")}
    marker_ids = {marker["story_id"] for marker in map_data["markers"]}
    assert marker_ids <= public_ids
    assert marker_ids.isdisjoint(excluded_ids)

    assert not (cascadia_work_root / "output" / "site" / "detail").exists()
    assert not (cascadia_work_root / "output" / "site" / "paid").exists()


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
    assert len(map_data["markers"]) == 1
    marker = map_data["markers"][0]
    assert marker["coordinate_basis"] == "source_default"
    assert marker["source_url"] == "https://example.com/wa-bridge"


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
