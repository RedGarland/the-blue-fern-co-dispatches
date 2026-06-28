from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

import scripts.run_food_line_dispatch as food_line_dispatch
from bluefern_dispatches.food_line_discovery_expansion import (
    build_food_line_discovery_query_plan,
    read_food_line_discovery_expansion_audit,
    run_food_line_discovery_expansion,
    validate_food_line_manual_fallback_record,
)


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<rss version=\"2.0\"><channel>"]
    for item in items:
        parts.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Fri, 19 Jun 2026 12:00:00 GMT')}</pubDate>"
            f"<description>{item.get('description', '')}</description>"
            f"<source url=\"{item['source_url']}\">{item['publisher']}</source>"
            "</item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _html_article(*, title: str, canonical: str, body: str) -> bytes:
    return f"""<!doctype html>
<html>
<head>
  <title>{title}</title>
  <meta property="og:title" content="{title}">
  <link rel="canonical" href="{canonical}">
</head>
<body>
  <article><p>{body}</p></article>
</body>
</html>""".encode("utf-8")


def _write_direct_source_config(tmp_path: Path, direct_sources: list[dict[str, object]]) -> None:
    config_dir = tmp_path / "data" / "dispatches" / "food-line"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "search": {
            "provider": "google_news_rss",
            "rss_url_template": "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
        },
        "direct_sources": direct_sources,
        "query_families": [
            {
                "query_family": "public_radio",
                "geographic_scope": "national",
                "source_family": "public_radio",
                "templates": ['"food bank"'],
            },
            {
                "query_family": "pressure",
                "geographic_scope": "national",
                "source_family": "local_news",
                "templates": ['"food pantry"'],
            },
        ],
        "metros": [{"name": "Charlotte"}],
    }
    (config_dir / "discovery_expansion_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


def test_food_line_discovery_expansion_blocks_out_of_window_candidates_for_public_claims(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/food-bank-demand"
    google_url = "https://news.google.com/rss/articles/CBMiOUTSIDE?oc=5"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food bank says demand is rising",
                        "link": google_url,
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food bank demand is rising and more families are showing up.",
                        "pubDate": "Wed, 25 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return _html_article(
                title="Food bank says demand is rising",
                canonical=article_url,
                body="Food bank demand is rising and more families are showing up.",
            )
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["source_published_date"] == "2026-06-25"
    assert candidate["public_claim_eligible"] is False
    assert "outside_backfill_date_window" in candidate["public_claim_blockers"]
    assert candidate["traceability_status"] == "traceable"


def test_food_line_discovery_expansion_blocks_homepage_only_trace_urls(tmp_path: Path):
    edition_date = "2026-06-21"
    homepage_url = "https://www.kxan.com"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food pantry demand rises",
                        "link": "https://news.google.com/rss/articles/CBMiHOME?oc=5",
                        "source_url": homepage_url,
                        "publisher": "KXAN",
                        "description": "Food pantry demand rises and more families need help.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiHOME?oc=5":
            return f"<html><body><a href=\"{homepage_url}\">open</a></body></html>".encode("utf-8")
        if url == homepage_url:
            return _html_article(
                title="KXAN homepage",
                canonical=homepage_url,
                body="Food pantry demand rises and more families need help.",
            )
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["source_url"] == homepage_url
    assert candidate["original_source_url"] == homepage_url
    assert candidate["public_claim_eligible"] is False
    assert candidate["traceability_status"] == "publisher_homepage_trace_only"
    assert "publisher_homepage_trace_only" in candidate["public_claim_blockers"]
    assert candidate["google_news_url"] == "https://news.google.com/rss/articles/CBMiHOME?oc=5"


def test_food_line_discovery_expansion_preserves_article_trace_when_canonical_collapses_to_homepage(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://coloradosun.com/2026/06/21/pantry-demand-rising/"
    homepage_url = "https://coloradosun.com"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Pantry demand rising",
                        "link": "https://news.google.com/rss/articles/CBMiARTICLE?oc=5",
                        "source_url": article_url,
                        "publisher": "Colorado Sun",
                        "description": "Pantry demand is rising and more families need help.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiARTICLE?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url.rstrip("/") == article_url.rstrip("/"):
            return _html_article(
                title="Pantry demand rising",
                canonical=homepage_url,
                body="Pantry demand is rising and more families need help.",
            )
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["google_news_url"] == "https://news.google.com/rss/articles/CBMiARTICLE?oc=5"
    assert candidate["source_url"] == article_url.rstrip("/")
    assert candidate["original_source_url"] == article_url.rstrip("/")
    assert candidate["final_trace_url"] == article_url.rstrip("/")
    assert candidate["traceability_status"] == "traceable"
    assert candidate["public_claim_eligible"] is True
    assert candidate["canonical_homepage_collapse_ignored"] is True


def test_food_line_discovery_expansion_resolves_google_news_wrapper_to_article_url(tmp_path: Path):
    edition_date = "2026-06-21"
    homepage_url = "https://fox56.com"
    article_url = "https://fox56.com/news/local/summer-hunger-relief-food-banks-prepare"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Summer hunger relief: Local food banks prepare for increased need",
                        "link": "https://news.google.com/rss/articles/CBMiFOX56?oc=5",
                        "source_url": homepage_url,
                        "publisher": "FOX56",
                        "description": "Food banks prepare for increased need as school lets out.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiFOX56?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return _html_article(
                title="Summer hunger relief",
                canonical=article_url,
                body="Food banks prepare for increased need as school lets out.",
            )
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["google_news_url"] == "https://news.google.com/rss/articles/CBMiFOX56?oc=5"
    assert candidate["discovered_url"] == "https://news.google.com/rss/articles/CBMiFOX56?oc=5"
    assert candidate["source_url"] == article_url
    assert candidate["original_source_url"] == article_url
    assert candidate["final_trace_url"] == article_url
    assert candidate["traceability_status"] == "traceable"
    assert candidate["public_claim_eligible"] is True


def test_food_line_discovery_expansion_resolves_meta_refresh_wrapper_to_article_url(tmp_path: Path):
    edition_date = "2026-06-21"
    homepage_url = "https://example.com"
    article_url = "https://example.com/news/food-bank-demand-rises"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food bank demand rises",
                        "link": "https://news.google.com/rss/articles/CBMiMETA?oc=5",
                        "source_url": homepage_url,
                        "publisher": "Example News",
                        "description": "Food bank demand rises.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiMETA?oc=5":
            return f"<html><head><meta http-equiv=\"refresh\" content=\"0; url={article_url}\"></head><body></body></html>".encode("utf-8")
        if url == article_url:
            return _html_article(title="Food bank demand rises", canonical=article_url, body="Food bank demand rises.")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["final_trace_url"] == article_url
    assert candidate["traceability_status"] == "traceable"


def test_food_line_discovery_query_plan_covers_state_territory_and_metro_geographies(tmp_path: Path):
    plan = build_food_line_discovery_query_plan(tmp_path, "2026-06-19")

    families = {row["query_family"] for row in plan}
    states = {row["state_or_territory"] for row in plan if row["state_or_territory"]}
    metros = {row["metro"] for row in plan if row["metro"]}

    assert {
        "core_hunger",
        "pressure",
        "policy_program",
        "cost_pressure",
        "public_radio",
        "food_bank_provider",
        "feeding_america_affiliate",
        "school_meals_child_nutrition",
        "county_city_agenda",
        "snap_state_notice",
        "united_way_211",
        "nonprofit_report",
        "institutional_update",
        "social_watchlist",
        "state_territory",
        "metro",
    }.issubset(families)
    assert "Puerto Rico" in states
    assert "Guam" in states
    assert "U.S. Virgin Islands" in states
    assert "American Samoa" in states
    assert "Northern Mariana Islands" in states
    assert "Charlotte" in metros
    assert "Washington DC" in metros
    assert all("after:2026-06-18" in row["query_text"] and "before:2026-06-20" in row["query_text"] for row in plan)
    assert any(row["geographic_scope"] == "metro" for row in plan)


def test_food_line_discovery_expansion_caps_queries_across_multiple_lanes(tmp_path: Path):
    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            calls.append(url)
            return _rss_payload([])
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=8,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
    )

    assert result["query_count"] == 8
    assert len(calls) == 8
    assert "news_article" in result["executed_lanes"]
    assert "public_radio" in result["executed_lanes"]
    assert "food_bank_provider" in result["executed_lanes"]
    assert "county_city_agenda" in result["executed_lanes"]
    assert "snap_state_notice" in result["executed_lanes"]
    assert "social_watchlist" in result["skipped_lanes"] or "social_watchlist" in result["executed_lanes"]


def test_food_line_discovery_expansion_reports_url_resolution_diagnostics(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"
    article_homepage_url = "https://example.com"
    homepage_url = "https://www.kxan.com"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Traceable article",
                        "link": "https://news.google.com/rss/articles/CBMiTRACE?oc=5",
                        "source_url": article_homepage_url,
                        "publisher": "Example News",
                        "description": "Food bank demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    },
                    {
                        "title": "Homepage only trace",
                        "link": "https://news.google.com/rss/articles/CBMiHOME?oc=5",
                        "source_url": homepage_url,
                        "publisher": "KXAN",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    },
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiTRACE?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == "https://news.google.com/rss/articles/CBMiHOME?oc=5":
            return f"<html><body><a href=\"{homepage_url}\">home</a></body></html>".encode("utf-8")
        if url == article_url:
            return _html_article(title="Traceable article", canonical=article_url, body="Food bank demand is rising.")
        if url == homepage_url:
            return _html_article(title="Homepage only trace", canonical=homepage_url, body="Food pantry demand is rising.")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )

    assert result["google_news_url_count"] == 2
    assert result["google_news_resolution_attempt_count"] == 2
    assert result["google_news_resolution_success_count"] == 1
    assert result["google_news_resolution_failure_count"] == 1
    assert result["google_news_resolved_article_url_count"] == 1
    assert result["google_news_resolved_homepage_only_count"] == 1
    assert result["google_news_resolution_status_counts"]["success_article"] == 1
    assert result["google_news_resolution_status_counts"]["success_homepage_only"] == 1
    assert result["article_specific_url_count"] == 1
    assert result["publisher_homepage_trace_only_count"] == 1
    assert result["unresolved_google_news_count"] == 0
    assert result["blocked_fetch_count"] == 0
    assert result["in_window_candidate_count"] == 2
    assert result["out_of_window_candidate_count"] == 0
    assert result["public_eligible_candidate_count"] == 1


def test_food_line_discovery_expansion_failed_google_news_resolution_stays_non_public(tmp_path: Path):
    edition_date = "2026-06-21"
    google_url = "https://news.google.com/rss/articles/CBMiFAIL?oc=5"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Summer hunger relief: Local food banks prepare for increased need",
                        "link": google_url,
                        "source_url": "",
                        "publisher": "Fox 56",
                        "description": "Food bank hunger relief and increased need.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == google_url:
            return b"<html><body><p>No article URL exposed here.</p></body></html>"
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["google_news_url"] == google_url
    assert candidate["discovered_url"] == google_url
    assert candidate["final_trace_url"] == ""
    assert candidate["traceability_status"] == "unresolved_google_news"
    assert candidate["public_claim_eligible"] is False
    assert "unresolved_google_news" in candidate["public_claim_blockers"]
    assert result["google_news_resolution_status_counts"]["failed_no_candidate_urls"] == 1


def test_food_line_discovery_expansion_context_only_stays_blocked_with_traceable_url(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/background-story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Background report on community volunteering",
                        "link": "https://news.google.com/rss/articles/CBMiCTX?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "A community profile without current pressure evidence.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiCTX?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return _html_article(title="Background report", canonical=article_url, body="A community profile without current pressure evidence.")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["traceability_status"] == "traceable"
    assert candidate["classification_status"] == "context_only"
    assert candidate["public_claim_eligible"] is False
    assert "context_only" in candidate["public_claim_blockers"]


def test_food_line_discovery_expansion_rejects_google_static_and_schema_urls(tmp_path: Path):
    edition_date = "2026-06-21"
    homepage_url = "https://example.com"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food bank demand rises",
                        "link": "https://news.google.com/rss/articles/CBMiBAD?oc=5",
                        "source_url": homepage_url,
                        "publisher": "Example News",
                        "description": "Food bank demand rises.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiBAD?oc=5":
            return b"""<html><body>
            <a href=\"https://lh3.googleusercontent.com/example=w16\">img</a>
            <a href=\"https://www.google-analytics.com/analytics.js\">ga</a>
            <a href=\"http://www.w3.org/2000/svg\">svg</a>
            </body></html>"""
        if url == homepage_url:
            return _html_article(title="Example", canonical=homepage_url, body="Food bank demand rises.")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["final_trace_url"] == homepage_url
    assert candidate["traceability_status"] == "publisher_homepage_trace_only"
    assert result["google_news_resolution_status_counts"]["failed_no_same_publisher_family"] == 1


def test_food_line_discovery_candidate_sources_are_plain_array_with_inspectable_fields(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food bank says demand is rising",
                        "link": "https://news.google.com/rss/articles/CBMiFIELDS?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food bank demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiFIELDS?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return _html_article(title="Food bank says demand is rising", canonical=article_url, body="Food bank demand is rising.")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    payload = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert isinstance(payload, list)
    assert payload
    candidate = payload[0]
    for key in (
        "discovered_title",
        "discovered_publisher",
        "discovered_url",
        "google_news_url",
        "source_url",
        "original_source_url",
        "final_trace_url",
        "traceability_status",
        "public_claim_eligible",
        "public_claim_blockers",
    ):
        assert key in candidate


def test_food_line_discovery_expansion_retains_blocked_fetches_and_manual_fallbacks(tmp_path: Path):
    edition_date = "2026-06-19"
    query_url_prefix = "https://news.google.com/rss/search?q="
    axios_google_url = "https://news.google.com/rss/articles/CBMiAXY?oc=5"
    duplicate_google_url = "https://news.google.com/rss/articles/CBMiDUP?oc=5"
    axios_trace_url = "https://www.axios.com/local/charlotte/2026/06/19/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes"

    items = [
        {
            "title": "Charlotte nonprofits brace for summer hunger surge",
            "link": axios_google_url,
            "source_url": axios_trace_url,
            "publisher": "Axios Charlotte",
            "description": "Charlotte nonprofits expect increased need as school meals end and SNAP changes tighten access.",
            "pubDate": "Fri, 19 Jun 2026 12:00:00 GMT",
        },
        {
            "title": "Charlotte nonprofits brace for summer hunger surge",
            "link": duplicate_google_url,
            "source_url": axios_trace_url,
            "publisher": "Axios Charlotte",
            "description": "Charlotte nonprofits expect increased need as school meals end and SNAP changes tighten access.",
            "pubDate": "Fri, 19 Jun 2026 12:05:00 GMT",
        },
    ]

    manual_fallback_record = {
        "publisher": "Axios Charlotte",
        "canonical_url": axios_trace_url,
        "headline": "Charlotte nonprofits brace for summer hunger surge",
        "date": edition_date,
        "location": "Charlotte, NC",
        "manually_reviewed_summary": "The article describes a summer pressure spike tied to school meals ending and tighter SNAP access.",
        "pressure_evidence_summary": "Nonprofits expect increased need as school meals end, grocery costs rise, and SNAP changes tighten access.",
        "affected_groups": ["families", "children", "SNAP households"],
        "limitations": "The publisher page returned 403 to automated fetchers, so review depends on manual inspection.",
        "extraction_quality": "manual_fallback",
        "reviewer_or_source_note": "Manually reviewed from the article and Google News discovery metadata.",
        "final_trace_url": axios_trace_url,
        "geographic_scope": "metro",
    }

    def fetcher(url: str, timeout: int = 15):
        if url.startswith(query_url_prefix):
            return _rss_payload(items)
        if url == axios_trace_url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        manual_fallback_records=[manual_fallback_record],
        edition_mode="no_current_update",
        max_queries=1,
        max_results_per_query=10,
    )

    candidate_path = Path(result["discovery_candidates_path"])
    audit_path = Path(result["discovery_audit_json_path"])
    audit_md_path = Path(result["discovery_audit_md_path"])
    candidates = json.loads(candidate_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))

    axios_candidates = [row for row in candidates if row["discovered_title"] == "Charlotte nonprofits brace for summer hunger surge"]
    manual_candidates = [row for row in candidates if row["classification_status"] == "manual_fallback"]

    assert candidate_path.exists()
    assert audit_path.exists()
    assert audit_md_path.exists()
    assert result["discovery_confidence"] == "limited"
    assert result["no_current_update"] is True
    assert audit["discovery_confidence_reason"]
    assert audit["candidate_count"] == len(candidates)
    assert audit["duplicate_count"] >= 1
    assert audit["blocked_fetch_count"] >= 1
    assert audit["qualified_pressure_signals"] == 0
    assert audit["manual_fallback_count"] == 1
    assert axios_candidates
    assert manual_candidates
    assert any(row["google_news_url"] == axios_google_url for row in axios_candidates)
    assert any(row["final_trace_url"] == axios_trace_url for row in axios_candidates)
    assert any(row["source_url"] == axios_trace_url for row in axios_candidates)
    assert any(row["original_source_url"] == axios_trace_url for row in axios_candidates)
    assert any(row["discovery_query"] for row in axios_candidates)
    assert any(row["discovery_source_type"] == "rss_discovery" for row in axios_candidates)
    assert any(row["fetch_status"] == "blocked_403" for row in axios_candidates)
    assert any("403" in row["fetch_error"] for row in axios_candidates)
    assert any(row["manual_review_required"] is True for row in axios_candidates)
    assert any(row["candidate_review_status"] == "needs_review" for row in axios_candidates)
    assert all(row["public_claim_eligible"] is False for row in axios_candidates)
    assert any(row["duplicate_of"] for row in axios_candidates)
    assert manual_candidates[0]["review_status"] == "manual_reviewed"
    assert manual_candidates[0]["manual_review_required"] is False
    assert manual_candidates[0]["extraction_quality"] == "manual_fallback"
    assert manual_candidates[0]["final_trace_url"] == axios_trace_url
    assert manual_candidates[0]["discovery_lane"] == "news_article"
    assert manual_candidates[0]["traceability_status"] == "traceable"
    assert "No candidates were retained" not in audit["discovery_confidence_summary"]
    assert "no_current_update" in audit["no_current_update_reason"] or audit["no_current_update_reason"]


def test_direct_rss_item_with_article_url_becomes_traceable(tmp_path: Path):
    article_url = "https://example.org/news/pantry-demand"
    feed_url = "https://example.org/feed.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Example Direct Feed",
                "source_family": "local_news_direct_rss",
                "discovery_lane": "news_article",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": ["recipe"],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return _rss_payload(
                [
                    {
                        "title": "Pantry demand rising",
                        "link": article_url,
                        "source_url": article_url,
                        "publisher": "Example Direct Feed",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return _html_article(title="Pantry demand rising", canonical=article_url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["discovery_channel"] == "direct_rss"
    assert candidate["direct_source_name"] == "Example Direct Feed"
    assert candidate["feed_url"] == feed_url
    assert candidate["traceability_status"] == "traceable"
    assert candidate["public_claim_eligible"] is True
    assert result["direct_source_count"] == 1
    assert result["direct_article_url_count"] == 1
    assert result["candidates_by_discovery_channel"]["direct_rss"] == 1


def test_direct_rss_item_with_feed_or_homepage_url_is_blocked(tmp_path: Path):
    feed_url = "https://example.org/feed.xml"
    homepage_url = "https://example.org"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Example Direct Feed",
                "source_family": "local_news_direct_rss",
                "discovery_lane": "news_article",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return _rss_payload(
                [
                    {
                        "title": "Homepage listing only",
                        "link": homepage_url,
                        "source_url": homepage_url,
                        "publisher": "Example Direct Feed",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["discovery_channel"] == "direct_rss"
    assert candidate["direct_fetch_status"] == "blocked_listing_url"
    assert candidate["traceability_status"] in {"publisher_homepage_trace_only", "non_article_trace_url"}
    assert candidate["public_claim_eligible"] is False
    assert result["direct_homepage_or_feed_blocked_count"] == 1


def test_direct_source_candidate_is_preferred_over_duplicate_google_news_candidate(tmp_path: Path):
    article_url = "https://example.org/news/pantry-demand"
    feed_url = "https://example.org/feed.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Example Direct Feed",
                "source_family": "local_news_direct_rss",
                "discovery_lane": "news_article",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return _rss_payload(
                [
                    {
                        "title": "Pantry demand rising",
                        "link": article_url,
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Pantry demand rising",
                        "link": "https://news.google.com/rss/articles/CBMIDUP?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return _html_article(title="Pantry demand rising", canonical=article_url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=2, max_results_per_query=5)
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    direct = next(row for row in candidates if row["discovery_channel"] == "direct_rss")
    google = next(row for row in candidates if row["discovery_channel"] == "google_news_rss")

    assert direct["duplicate_of"] == ""
    assert google["duplicate_of"] == direct["candidate_id"]
    assert result["duplicate_preferred_direct_count"] == 1


def test_direct_source_diagnostics_and_sampling_appear_in_backfill_summary(tmp_path: Path):
    article_url = "https://example.org/news/pantry-demand"
    feed_url = "https://example.org/feed.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Example Direct Feed",
                "source_family": "local_news_direct_rss",
                "discovery_lane": "news_article",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return _rss_payload(
                [
                    {
                        "title": "Pantry demand rising",
                        "link": article_url,
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 24 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return _html_article(title="Pantry demand rising", canonical=article_url, body="Food pantry demand is rising.")
        if url.startswith("https://news.google.com/rss/search?q="):
            raise AssertionError("direct source should be sampled before google fallback under cap")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-24",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )

    assert result["query_count"] == 1
    assert result["direct_source_fetch_attempt_count"] == 1
    assert result["direct_source_fetch_success_count"] == 1
    assert result["google_news_fallback_count"] == 0
    assert result["candidates_by_direct_source"]["Example Direct Feed"] == 1


def test_direct_sources_are_balanced_by_source_cap_and_lane_reporting(tmp_path: Path):
    feed_one = "https://example.org/feed-one.xml"
    feed_two = "https://example.org/feed-two.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Dominant Feed",
                "source_family": "food_bank_provider",
                "discovery_lane": "food_bank_provider",
                "discovery_channel": "direct_rss",
                "feed_url": feed_one,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            },
            {
                "source_name": "Second Feed",
                "source_family": "public_radio",
                "discovery_lane": "public_radio",
                "discovery_channel": "direct_rss",
                "feed_url": feed_two,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 20,
                "direct_source_candidate_cap": 1,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            },
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_one:
            return _rss_payload(
                [
                    {"title": "One", "link": "https://example.org/one", "source_url": "https://example.org/one", "publisher": "Dominant Feed", "description": "Food pantry demand is rising.", "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT"},
                    {"title": "Two", "link": "https://example.org/two", "source_url": "https://example.org/two", "publisher": "Dominant Feed", "description": "Food pantry demand is rising.", "pubDate": "Sat, 21 Jun 2026 12:05:00 GMT"},
                ]
            )
        if url == feed_two:
            return _rss_payload(
                [
                    {"title": "Three", "link": "https://example.org/three", "source_url": "https://example.org/three", "publisher": "Second Feed", "description": "Food pantry demand is rising.", "pubDate": "Sat, 21 Jun 2026 12:10:00 GMT"},
                ]
            )
        if url in {"https://example.org/one", "https://example.org/two", "https://example.org/three"}:
            return _html_article(title="Story", canonical=url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=2, max_results_per_query=5)

    assert result["candidates_by_direct_source"] == {"Dominant Feed": 1, "Second Feed": 1}
    assert result["candidates_by_direct_source_lane"]["Dominant Feed | food_bank_provider"] == 1
    assert result["candidates_by_direct_source_lane"]["Second Feed | public_radio"] == 1
    assert result["direct_source_candidate_cap_hits"]["Dominant Feed"] == 1
    assert result["dominant_source_warning"] == ""


def test_dominant_source_warning_appears_when_one_source_supplies_majority(tmp_path: Path):
    feed_url = "https://example.org/feed-one.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Dominant Feed",
                "source_family": "food_bank_provider",
                "discovery_lane": "food_bank_provider",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 3,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return _rss_payload(
                [
                    {"title": "One", "link": "https://example.org/one", "source_url": "https://example.org/one", "publisher": "Dominant Feed", "description": "Food pantry demand is rising.", "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT"},
                    {"title": "Two", "link": "https://example.org/two", "source_url": "https://example.org/two", "publisher": "Dominant Feed", "description": "Food pantry demand is rising.", "pubDate": "Sat, 21 Jun 2026 12:05:00 GMT"},
                ]
            )
        if url in {"https://example.org/one", "https://example.org/two"}:
            return _html_article(title="Story", canonical=url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)

    assert "Dominant Feed contributed 2 of 2 candidates." == result["dominant_source_warning"]


def test_direct_rss_sources_are_preferred_over_broad_agenda_pages_in_capped_runs(tmp_path: Path):
    calls: list[str] = []
    feed_url = "https://example.org/feed.xml"
    agenda_url = "https://example.org/calendar"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Agenda Source",
                "source_family": "county_city_agenda",
                "discovery_lane": "county_city_agenda",
                "discovery_channel": "direct_page",
                "source_url": agenda_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "state_local",
                "enabled": True,
                "sampling_priority": 500,
                "direct_source_candidate_cap": 1,
                "max_age_days": 7,
                "pressure_terms": ["food assistance"],
                "exclusion_terms": [],
            },
            {
                "source_name": "RSS Source",
                "source_family": "food_bank_provider",
                "discovery_lane": "food_bank_provider",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            },
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        calls.append(url)
        if url == feed_url:
            return _rss_payload(
                [
                    {"title": "RSS story", "link": "https://example.org/rss-story", "source_url": "https://example.org/rss-story", "publisher": "RSS Source", "description": "Food pantry demand is rising.", "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT"},
                ]
            )
        if url == "https://example.org/rss-story":
            return _html_article(title="RSS story", canonical=url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)

    assert calls == [feed_url, "https://example.org/rss-story"]
    assert result["candidates_by_direct_source"] == {"RSS Source": 1}


def test_cook_county_agenda_listing_pages_stay_non_public(tmp_path: Path):
    agenda_url = "https://cook-county.legistar.com/Calendar.aspx"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Cook County Board Agenda",
                "source_family": "county_city_agenda",
                "discovery_lane": "county_city_agenda",
                "discovery_channel": "direct_page",
                "source_url": agenda_url,
                "allowed_domains": ["cook-county.legistar.com"],
                "geographic_scope": "state_local",
                "enabled": True,
                "sampling_priority": 500,
                "direct_source_candidate_cap": 1,
                "max_age_days": 7,
                "pressure_terms": ["food assistance", "contract"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == agenda_url:
            return b"<html><head><title>Calendar</title><link rel=\"canonical\" href=\"https://cook-county.legistar.com/Calendar.aspx\"></head><body>Food assistance committee calendar.</body></html>"
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["direct_source_name"] == "Cook County Board Agenda"
    assert candidate["public_claim_eligible"] is False
    assert "publisher_homepage_trace_only" in candidate["public_claim_blockers"] or "non_article_trace_url" in candidate["public_claim_blockers"]


def test_food_line_manual_fallback_validation_rejects_missing_required_fields():
    valid = {
        "publisher": "Axios Charlotte",
        "canonical_url": "https://www.axios.com/local/charlotte/story",
        "headline": "Charlotte nonprofits brace for summer hunger surge",
        "date": "2026-06-19",
        "location": "Charlotte, NC",
        "manually_reviewed_summary": "Manual review summary.",
        "pressure_evidence_summary": "Pressure evidence summary.",
        "affected_groups": ["families"],
        "limitations": "Automated fetch blocked.",
        "extraction_quality": "manual_fallback",
        "reviewer_or_source_note": "Manual review note.",
        "final_trace_url": "https://www.axios.com/local/charlotte/story",
    }
    invalid = dict(valid)
    invalid.pop("final_trace_url")

    assert validate_food_line_manual_fallback_record(valid) == []
    errors = validate_food_line_manual_fallback_record(invalid)
    assert errors
    assert any("final_trace_url" in error for error in errors)


def test_food_line_discovery_expansion_helper_reads_no_current_update_metadata(tmp_path: Path):
    audit_dir = tmp_path / "output" / "review" / "food-line" / "2026-06-19"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_payload = {
        "discovery_confidence": "limited",
        "discovery_confidence_reason": "Candidates were discovered, but none were strong enough to avoid manual review.",
        "discovery_candidates_path": str(tmp_path / "data" / "dispatches" / "food-line" / "discovery" / "2026-06-19" / "discovery_candidates.json"),
        "discovery_audit_json_path": str(audit_dir / "discovery_audit.json"),
        "discovery_audit_md_path": str(audit_dir / "discovery_audit.md"),
        "no_current_update": True,
        "no_current_update_reason": "Candidates were discovered, but fetch failures kept the day from supporting a stronger conclusion.",
    }
    (audit_dir / "discovery_audit.json").write_text(json.dumps(audit_payload, indent=2), encoding="utf-8")

    helper_result = food_line_dispatch._food_line_discovery_expansion_audit(tmp_path, "2026-06-19")

    assert helper_result["discovery_confidence"] == "limited"
    assert helper_result["no_current_update"] is True
    assert helper_result["discovery_audit_json_path"].endswith("discovery_audit.json")
    assert helper_result["discovery_audit_md_path"].endswith("discovery_audit.md")
