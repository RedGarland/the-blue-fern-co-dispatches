from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.run_food_line_dispatch as food_line_dispatch
from bluefern_dispatches import food_line_discovery_expansion as expansion_module
from bluefern_dispatches import food_line_sources as food_sources
from bluefern_dispatches.food_line_discovery_expansion import (
    _apply_public_readiness_gate,
    _normalize_candidate_row,
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
        max_queries=8,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 8
    assert result["early_exclusion_count"] == 8
    assert result["early_exclusion_reasons"]["outside_backfill_date_window"] == 8


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
        max_queries=8,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 8
    assert result["early_exclusion_count"] == 8
    assert result["early_exclusion_reasons"]["homepage_or_landing_url"] == 8


def test_food_line_discovery_expansion_stops_at_deadline_after_first_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    edition_date = "2026-08-19"
    (tmp_path / "data" / "dispatches" / "food-line").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dispatches" / "food-line" / "discovery_expansion_config.json").write_text("{}", encoding="utf-8")

    query_plan = [
        {
            "query_id": "q-1",
            "query_family": "core_hunger",
            "geographic_scope": "national",
            "discovery_channel": "google_news_rss",
            "query_text": '"food insecurity"',
        },
        {
            "query_id": "q-2",
            "query_family": "core_hunger",
            "geographic_scope": "national",
            "discovery_channel": "google_news_rss",
            "query_text": '"food bank"',
        },
    ]

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        assert root == tmp_path
        assert edition_date == "2026-08-19"
        return query_plan

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food costs rise for families",
                        "link": "https://example.com/food-costs-rise",
                        "source_url": "https://example.com/food-costs-rise",
                        "publisher": "Example News",
                        "description": "Families are paying more for groceries.",
                        "pubDate": "Tue, 19 Aug 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://example.com/food-costs-rise":
            return _html_article(
                title="Food costs rise for families",
                canonical=url,
                body="Families are paying more for groceries.",
            )
        raise AssertionError(f"unexpected fetch url: {url}")

    clock_calls = {"count": 0}
    start = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    deadline = start + timedelta(seconds=1)

    def runtime_clock() -> datetime:
        clock_calls["count"] += 1
        if clock_calls["count"] <= 2:
            return start
        return start + timedelta(seconds=2)

    monkeypatch.setattr(expansion_module, "build_food_line_discovery_query_plan", fake_plan)

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        runtime_deadline=deadline,
        runtime_clock=runtime_clock,
        max_queries=2,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )

    assert result["ok"] is False
    assert result["status"] == "timed_out"
    assert result["timed_out"] is True
    assert result["queries_completed"] == 1
    assert result["queries_timed_out"] == 1
    assert result["candidate_count"] == 0
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    assert candidates == []
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["no current pressure evidence"] == 1


def test_food_line_discovery_expansion_blocks_landing_trace_urls(tmp_path: Path):
    edition_date = "2026-06-21"
    landing_url = "https://www.fao.org/home/en"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Home | Food and Agriculture Organization of the United Nations",
                        "link": "https://news.google.com/rss/articles/CBMiLANDING?oc=5",
                        "source_url": landing_url,
                        "publisher": "Food and Agriculture Organization of the United Nations",
                        "description": "Food price pressure is affecting household access to food.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiLANDING?oc=5":
            return f"<html><body><a href=\"{landing_url}\">open</a></body></html>".encode("utf-8")
        if url == landing_url:
            return _html_article(
                title="Home | Food and Agriculture Organization of the United Nations",
                canonical=landing_url,
                body="Food price pressure is affecting household access to food.",
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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_count"] == 1
    assert result["early_exclusion_reasons"]["generic_or_invalid_title"] == 1


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


def test_food_line_discovery_query_plan_includes_targeted_recall_queries(tmp_path: Path):
    plan = build_food_line_discovery_query_plan(tmp_path, "2026-06-19")
    query_texts = {row["query_text"] for row in plan}

    assert any('"record demand"' in text and '"summer meals"' in text for text in query_texts)
    assert any('"meal price increase"' in text or '"school lunch price"' in text for text in query_texts)
    assert any('"health care bills"' in text or '"medical debt"' in text for text in query_texts)
    assert any('"New York Fed"' in text or '"Federal Reserve Bank of New York"' in text for text in query_texts)


def test_food_line_discovery_expansion_reports_bounded_plan_and_deferred_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    queries = [
        {
            "query_id": f"q-{index}",
            "query_family": "core_hunger",
            "geographic_scope": "national",
            "discovery_channel": "google_news_rss",
            "search_provider": "google_news_rss",
            "query_text": f'"food insecurity" term {index}',
        }
        for index in range(5)
    ]
    calls: list[str] = []

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        return list(queries)

    def fake_fetcher(url: str, timeout: int = 15):
        calls.append(url)
        return _rss_payload([])

    monkeypatch.setattr(expansion_module, "build_food_line_discovery_query_plan", fake_plan)
    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-08-19",
        fetcher=fake_fetcher,
        max_queries=2,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
    )

    assert result["query_plan_available_count"] == 5
    assert result["query_plan_bounded_count"] == 2
    assert result["query_plan_truncated"] is True
    assert result["queries_deferred"] == 3
    assert result["queries_already_completed"] == 0
    assert result["queries_processed_this_invocation"] == 2
    assert result["queries_completed"] == 2
    assert result["queries_remaining"] == 0
    assert result["query_count"] == 2
    assert len(calls) == 2


def test_food_line_discovery_expansion_resume_skips_completed_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    queries = [
        {
            "query_id": f"q-{index}",
            "query_family": "core_hunger",
            "geographic_scope": "national",
            "discovery_channel": "google_news_rss",
            "search_provider": "google_news_rss",
            "query_text": f'"food insecurity" term {index}',
        }
        for index in range(5)
    ]
    calls: list[str] = []

    def fake_plan(root: Path, edition_date: str, **kwargs: object) -> list[dict[str, object]]:
        return list(queries)

    def fake_fetcher(url: str, timeout: int = 15):
        calls.append(url)
        return _rss_payload([])

    monkeypatch.setattr(expansion_module, "build_food_line_discovery_query_plan", fake_plan)
    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-08-19",
        fetcher=fake_fetcher,
        max_queries=5,
        resume_from_query_index=3,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
    )

    assert result["query_plan_available_count"] == 5
    assert result["query_plan_bounded_count"] == 5
    assert result["queries_already_completed"] == 3
    assert result["queries_processed_this_invocation"] == 2
    assert result["queries_completed"] == 5
    assert result["queries_remaining"] == 0
    assert len(calls) == 2


def test_food_line_discovery_expansion_wowt_record_demand_story_enters_review(tmp_path: Path):
    edition_date = "2026-06-18"
    article_url = "https://www.wowt.com/2026/06/18/omaha-food-programs-see-record-demand-summer-break-eliminates-school-meals/"
    publisher_url = "https://news.google.com/publications/CAAqBwgKMPOC1Qswj4fdAw"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Omaha food programs see record demand as summer break eliminates school meals",
                        "link": "https://news.google.com/rss/articles/CBMiWOWT?oc=5",
                        "source_url": publisher_url,
                        "publisher": "WOWT",
                        "description": "Omaha food programs reported record demand after summer break eliminated school meals for many families.",
                        "pubDate": "Thu, 18 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiWOWT?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url.rstrip("/") == article_url.rstrip("/"):
            return _html_article(
                title="Omaha food programs see record demand as summer break eliminates school meals",
                canonical=article_url,
                body="Omaha food programs said they are seeing record demand as summer break eliminates school meals and more families need help.",
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

    assert candidate["classification_status"] == "qualified_pressure_signal"
    assert candidate["candidate_review_status"] == "needs_review"
    assert candidate["public_claim_eligible"] is True
    assert candidate["pressure_signal"] is True
    assert candidate["pressure_type"] in {"demand strain", "food bank demand pressure"}
    assert candidate["source_published_date"] == edition_date
    assert candidate["final_trace_url"].rstrip("/") == article_url.rstrip("/")
    assert result["google_news_resolution_status_counts"]["resolved_known_alias"] == 1
    assert not (tmp_path / "output" / "site").exists()


def test_food_line_discovery_expansion_indyweek_meal_price_story_enters_review(tmp_path: Path):
    edition_date = "2026-06-20"
    article_url = "https://indyweek.com/news/wake-school-board-passes-meal-price-increase-creates-task-force-to-address-cost/"
    publisher_url = "https://news.google.com/publications/CAAqBwgKMLjOlAsw2qu8Aw"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Wake school board passes meal price increase, creates task force to address cost",
                        "link": "https://news.google.com/rss/articles/CBMiINDY?oc=5",
                        "source_url": publisher_url,
                        "publisher": "IndyWeek",
                        "description": "Wake school board approved a meal price increase and created a task force to address meal costs for families.",
                        "pubDate": "Sat, 20 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiINDY?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url.rstrip("/") == article_url.rstrip("/"):
            return _html_article(
                title="Wake school board passes meal price increase, creates task force to address cost",
                canonical=article_url,
                body="Wake school board passed a school meal price increase and created a task force to address meal costs and affordability for families.",
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

    assert candidate["classification_status"] == "qualified_pressure_signal"
    assert candidate["candidate_review_status"] == "needs_review"
    assert candidate["public_claim_eligible"] is True
    assert candidate["pressure_type"] == "school meal price pressure"
    assert candidate["pressure_summary"]
    assert result["google_news_resolution_status_counts"]["resolved_known_alias"] == 1


def test_food_line_discovery_expansion_health_cost_food_insecurity_story_enters_review(tmp_path: Path):
    edition_date = "2026-06-20"
    article_url = "https://www.benefitspro.com/2026/06/20/health-care-bills-are-fueling-food-insecurity/"
    publisher_url = "https://news.google.com/publications/CAAqBwgKMPj6kgsw4uTtAw"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Health care bills are fueling food insecurity for more households",
                        "link": "https://news.google.com/rss/articles/CBMiBENE?oc=5",
                        "source_url": publisher_url,
                        "publisher": "BenefitsPro",
                        "description": "Health care bills and medical debt are fueling food insecurity for more households.",
                        "pubDate": "Sat, 20 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiBENE?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url.rstrip("/") == article_url.rstrip("/"):
            return _html_article(
                title="Health care bills are fueling food insecurity for more households",
                canonical=article_url,
                body="Health care bills and medical debt are fueling food insecurity for more households as families struggle to afford both care and food.",
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

    assert candidate["classification_status"] == "qualified_pressure_signal"
    assert candidate["candidate_review_status"] == "needs_review"
    assert candidate["public_claim_eligible"] is True
    assert candidate["pressure_summary"]
    assert candidate["pressure_type"] in {"household hardship", "household food insecurity pressure"}
    assert result["google_news_resolution_status_counts"]["resolved_known_alias"] == 1


def test_food_line_discovery_expansion_new_york_fed_story_enters_review_via_canonical_domain(tmp_path: Path):
    edition_date = "2026-06-20"
    article_url = "https://www.newyorkfed.org/research/survey/2026/rising-food-insecurity-households"
    publisher_url = "https://news.google.com/publications/CAAqBwgKMN2l3Asw8fS4Aw"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "New York Fed says food insecurity is rising for more households",
                        "link": "https://news.google.com/rss/articles/CBMiNYFED?oc=5",
                        "source_url": publisher_url,
                        "publisher": "New York Fed",
                        "description": "A New York Fed survey found food insecurity rising for more households.",
                        "pubDate": "Sat, 20 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiNYFED?oc=5":
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "</head><body><p>survey coverage</p></body></html>"
            ).encode("utf-8")
        if url.rstrip("/") == article_url.rstrip("/"):
            return _html_article(
                title="New York Fed says food insecurity is rising for more households",
                canonical=article_url,
                body="A New York Fed survey found food insecurity rising for more households as budgets remain strained.",
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

    assert candidate["classification_status"] == "qualified_pressure_signal"
    assert candidate["candidate_review_status"] == "needs_review"
    assert candidate["public_claim_eligible"] is True
    assert candidate["pressure_type"] in {"household hardship", "household food insecurity pressure"}
    assert result["google_news_resolution_status_counts"]["resolved_canonical_domain"] == 1


def test_food_line_discovery_expansion_google_news_listing_url_stays_blocked(tmp_path: Path):
    edition_date = "2026-06-20"
    publisher_url = "https://example.org"
    listing_url = "https://example.org/donate"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food pantry demand is rising",
                        "link": "https://news.google.com/rss/articles/CBMiLISTING?oc=5",
                        "source_url": publisher_url,
                        "publisher": "Example Pantry Network",
                        "description": "Families are facing rising food pantry demand.",
                        "pubDate": "Sat, 20 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiLISTING?oc=5":
            return f"<html><body><a href=\"{listing_url}\">donate</a></body></html>".encode("utf-8")
        if url == listing_url:
            return _html_article(title="Donate", canonical=listing_url, body="Support our pantry work.")
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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert result["google_news_resolution_status_counts"]["failed_listing_or_action_url"] == 1
    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["homepage_or_landing_url"] == 1


def test_food_line_discovery_expansion_resource_only_summer_meals_page_stays_blocked(tmp_path: Path):
    edition_date = "2026-06-20"
    article_url = "https://example.org/resources/summer-meal-sites"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Find summer meal sites for children",
                        "link": "https://news.google.com/rss/articles/CBMiRESOURCE?oc=5",
                        "source_url": article_url,
                        "publisher": "Example Resource Center",
                        "description": "Find summer meal sites and application help for families.",
                        "pubDate": "Sat, 20 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiRESOURCE?oc=5":
            return f"<html><body><a href=\"{article_url}\">resource</a></body></html>".encode("utf-8")
        if url == article_url:
            return _html_article(
                title="Find summer meal sites for children",
                canonical=article_url,
                body="Find summer meal sites, application help, and resources for families needing food assistance.",
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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["no current pressure evidence"] == 1


def test_food_line_discovery_query_plan_marks_historical_direct_sources(tmp_path: Path):
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Archive Source",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": "https://example.org/archive",
                "allowed_domains": ["example.org"],
                "enabled": True,
                "historical_capable": True,
            }
        ],
    )

    plan = build_food_line_discovery_query_plan(tmp_path, "2026-06-19")
    archive_row = next(row for row in plan if row["query_text"] == "Archive Source")

    assert archive_row["historical_capable"] is True


def test_food_line_discovery_query_plan_propagates_historical_archive_templates(tmp_path: Path):
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Archive Source",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": "https://example.org/archive",
                "allowed_domains": ["example.org"],
                "enabled": True,
                "historical_capable": True,
                "historical_archive_templates": [
                    {
                        "template_name": "monthly_archive",
                        "url_template": "https://example.org/archive/{yyyy}/{mm}",
                        "archive_granularity": "month",
                    }
                ],
            }
        ],
    )

    plan = build_food_line_discovery_query_plan(tmp_path, "2026-06-19")
    archive_row = next(row for row in plan if row["query_text"] == "Archive Source")

    assert archive_row["historical_archive_templates"] == [
        {
            "template_name": "monthly_archive",
            "url_template": "https://example.org/archive/{yyyy}/{mm}",
            "archive_granularity": "month",
        }
    ]


def test_food_line_discovery_query_plan_propagates_archive_pagination_config(tmp_path: Path):
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Archive Source",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": "https://example.org/archive",
                "allowed_domains": ["example.org"],
                "enabled": True,
                "historical_capable": True,
                "historical_archive_pagination_enabled": True,
                "archive_page_url_template": "https://example.org/archive?page={page}",
                "archive_page_start": 1,
                "archive_page_max_pages": 4,
                "archive_page_increment": 1,
                "archive_pagination_notes": "Verified broad pagination.",
            }
        ],
    )

    plan = build_food_line_discovery_query_plan(tmp_path, "2026-06-19")
    archive_row = next(row for row in plan if row["query_text"] == "Archive Source")

    assert archive_row["historical_archive_pagination_enabled"] is True
    assert archive_row["archive_page_url_template"] == "https://example.org/archive?page={page}"
    assert archive_row["archive_page_start"] == 1
    assert archive_row["archive_page_max_pages"] == 4
    assert archive_row["archive_page_increment"] == 1


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
    assert result["query_plan_available_count"] >= result["query_plan_bounded_count"]
    assert result["query_plan_truncated"] is True
    assert result["queries_deferred"] > 0


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

    assert result["google_news_url_count"] == 1
    assert result["google_news_resolution_attempt_count"] == 2
    assert result["google_news_resolution_success_count"] == 1
    assert result["google_news_resolution_failure_count"] == 1
    assert result["google_news_resolved_article_url_count"] == 1
    assert result["google_news_resolved_homepage_only_count"] == 1
    assert result["google_news_resolution_status_counts"]["resolved_same_domain"] == 1
    assert result["google_news_resolution_status_counts"]["failed_homepage_or_landing_url"] == 1
    assert result["article_specific_url_count"] == 1
    assert result["publisher_homepage_trace_only_count"] == 0
    assert result["unresolved_google_news_count"] == 0
    assert result["blocked_fetch_count"] == 0
    assert result["in_window_candidate_count"] == 1
    assert result["out_of_window_candidate_count"] == 0
    assert result["public_eligible_candidate_count"] == 1


def test_resolve_google_news_wrapper_reports_canonical_domain_resolution(monkeypatch: pytest.MonkeyPatch):
    article_url = "https://www.newyorkfed.org/research/survey/2026/rising-food-insecurity-households"
    publisher_url = "https://news.google.com/publications/CAAqBwgKMN2l3Asw8fS4Aw"

    def fetcher(url: str, timeout: int = 15):
        if url == "https://news.google.com/rss/articles/CBMiCANON?oc=5":
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "</head><body><p>survey coverage</p></body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(expansion_module, "_extract_candidate_urls", lambda text: [])

    resolved, error, attempted, debug = expansion_module._resolve_google_news_wrapper(
        fetcher,
        "https://news.google.com/rss/articles/CBMiCANON?oc=5",
        publisher_url=publisher_url,
        publisher_name="New York Fed",
    )

    assert attempted is True
    assert error == ""
    assert resolved == article_url.rstrip("/")
    assert debug["google_news_resolution_status"] == "resolved_canonical_domain"


def test_resolve_google_news_wrapper_prefers_decoded_article_url_without_html_scrape():
    article_url = "https://www.trtworld.com/middle-east/gaza-health-system-strain-officials-warn-of-collapsing-care-18273645"
    google_url = "https://news.google.com/rss/articles/CBMiT2h0dHBzOi8vd3d3LnRydHdvcmxkLmNvbS9taWRkbGUtZWFzdC9nYXphLWhlYWx0aC1zeXN0ZW0tc3RyYWluLW9mZmljaWFscy13YXJuLW9mLWNvbGxhcHNpbmctY2FyZS0xODI3MzY0NQ?oc=5"

    def fetcher(url: str, timeout: int = 15):
        raise AssertionError(f"fetch should not run for decoded wrapper: {url}")

    resolved, error, attempted, debug = expansion_module._resolve_google_news_wrapper(
        fetcher,
        google_url,
        publisher_url=article_url,
        publisher_name="TRT World",
    )

    assert attempted is True
    assert error == ""
    assert resolved == article_url
    assert debug["decoded_google_news_url"] == article_url
    assert debug["accepted_candidate_url"] == article_url
    assert debug["google_news_resolution_status"] == "resolved_same_domain"


def test_resolve_google_news_wrapper_prefers_rpc_article_url_before_html_scrape(monkeypatch: pytest.MonkeyPatch):
    article_url = "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026/"
    google_url = "https://news.google.com/rss/articles/CBMirwFBVV95cUxQaER6aU4zNlY3dWd0RC1VekROQ24wQjA4LXk5eThSYmJhU2dPRkpiY2JKWkhua0NRa3Flc0d2M0hseWFOc1ZvR29mYWk3ZzNFNE14Qkh3elVnckoydWVKdkhXWkJDQTJnTFVRcmZwOHhKb2twUi02c18yc3NXUHlZMVc0Wk1XdENQM01TelNnRzdINWlGWFVkMFRKSVR0b05uckRlLWIyU1dzMmwxY3Fr?oc=5"

    def fetcher(url: str, timeout: int = 15):
        if url == google_url:
            return (
                "<html><body>"
                '<div data-n-a-id="CBMirwFBVV95cUxQaER6aU4zNlY3dWd0RC1VekROQ24wQjA4LXk5eThSYmJhU2dPRkpiY2JKWkhua0NRa3Flc0d2M0hseWFOc1ZvR29mYWk3ZzNFNE14Qkh3elVnckoydWVKdkhXWkJDQTJnTFVRcmZwOHhKb2twUi02c18yc3NXUHlZMVc0Wk1XdENQM01TelNnRzdINWlGWFVkMFRKSVR0b05uckRlLWIyU1dzMmwxY3Fr" '
                'data-n-a-ts="1782841548" data-n-a-sg="AVvZt1FeWQBagmhL_V1m7VKOxBBR"></div>'
                '<a href="https://news.google.com">noise</a>'
                "</body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(expansion_module, "_google_news_rpc_request", lambda article_id, timestamp, signature: (article_url, ""))

    resolved, error, attempted, debug = expansion_module._resolve_google_news_wrapper(
        fetcher,
        google_url,
        publisher_url="https://www.bostonherald.com",
        publisher_name="Boston Herald",
    )

    assert attempted is True
    assert error == ""
    assert resolved == article_url
    assert debug["google_news_rpc_attempted"] is True
    assert debug["google_news_rpc_error"] == ""
    assert debug["google_news_rpc_url"] == article_url
    assert debug["google_news_resolution_status"] == "resolved_same_domain"
    assert debug["decoded_google_news_url"] == ""
    assert debug["candidate_url_count_extracted"] == 0


def test_google_news_rpc_request_parses_escaped_garturlres(monkeypatch: pytest.MonkeyPatch):
    article_url = "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026/"
    response_text = (
        ")]}'\n\n"
        '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"'
        + article_url
        + '\\",1]",null,null,null,"generic"],["di",16]]'
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit: int) -> bytes:
            return response_text.encode("utf-8")

    monkeypatch.setattr(expansion_module.urllib.request, "urlopen", lambda *args, **kwargs: _Response())

    resolved, error = expansion_module._google_news_rpc_request("token", "1782841548", "sig")

    assert resolved == article_url.rstrip("/")
    assert error == ""


def test_google_news_rpc_request_parses_garturlres_with_amp_fallback(monkeypatch: pytest.MonkeyPatch):
    response_text = (Path(__file__).parent / "fixtures" / "google_news_rpc_garturlres_with_amp.txt").read_text(encoding="utf-8")

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit: int) -> bytes:
            return response_text.encode("utf-8")

    monkeypatch.setattr(expansion_module.urllib.request, "urlopen", lambda *args, **kwargs: _Response())

    resolved, error = expansion_module._google_news_rpc_request("token", "1788047985", "sig")

    assert resolved == "https://www.aljazeera.com/news/2026/8/28/board-of-peace-envoy-mladenov-warns-gaza-ceasefire-risks-collapse"
    assert error == ""


def test_google_news_rpc_request_without_article_url_fails_safely(monkeypatch: pytest.MonkeyPatch):
    response_text = ")]}'\n\n[[\"wrb.fr\",\"Fbv4je\",\"[\\\"garturlres\\\",null,0]\",null,null,null,\"generic\"]]"

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit: int) -> bytes:
            return response_text.encode("utf-8")

    monkeypatch.setattr(expansion_module.urllib.request, "urlopen", lambda *args, **kwargs: _Response())

    resolved, error = expansion_module._google_news_rpc_request("token", "1788047985", "sig")

    assert resolved == ""
    assert error == "rpc_without_article_url"


def test_resolve_google_news_wrapper_records_bounded_rejected_candidate_sample():
    homepage_url = "https://www.kxan.com"
    listing_url = "https://www.kxan.com/news"
    unrelated_urls = [f"https://example{i}.com/story-{i}" for i in range(30)]
    html_links = "".join(f'<a href="{url}">link</a>' for url in [homepage_url, listing_url, *unrelated_urls])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://news.google.com/rss/articles/CBMiDEBUG?oc=5":
            return (
                "<html><body>"
                f"{html_links}"
                "<p>article body should not be preserved</p>"
                "</body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    resolved, error, attempted, debug = expansion_module._resolve_google_news_wrapper(
        fetcher,
        "https://news.google.com/rss/articles/CBMiDEBUG?oc=5",
        publisher_url=homepage_url,
        publisher_name="KXAN",
    )

    sample = debug["rejected_candidate_urls_sample"]

    assert attempted is True
    assert error == ""
    assert resolved == homepage_url
    assert debug["google_news_resolution_status"] == "failed_homepage_or_landing_url"
    assert len(sample) == expansion_module.GOOGLE_NEWS_REJECTED_URL_SAMPLE_LIMIT
    assert debug["rejected_candidate_urls_sample_limit"] == expansion_module.GOOGLE_NEWS_REJECTED_URL_SAMPLE_LIMIT
    assert debug["rejected_candidate_urls_sample_truncated"] is True
    assert sample[0]["candidate_url"] == homepage_url
    assert sample[0]["normalized_domain"] == "kxan.com"
    assert sample[0]["expected_publisher_domain"] == "kxan.com"
    assert sample[0]["expected_publisher_family_domains"] == ["kxan.com"]
    assert sample[0]["rejection_reason"] == "homepage_or_landing_url"
    assert sample[0]["homepage_or_landing_filter_applied"] is True
    assert any(item["rejection_reason"] == "listing_or_action_url" for item in sample)
    assert any(item["rejection_reason"] == "not_same_publisher_family" for item in sample)
    serialized_sample = json.dumps(sample)
    assert "article body should not be preserved" not in serialized_sample
    assert "<html" not in serialized_sample.lower()


def test_food_line_fetch_timeout_does_not_retry_with_longer_timeout(monkeypatch: pytest.MonkeyPatch):
    calls: list[int | float | None] = []

    class _TimeoutReason(TimeoutError):
        pass

    class _TimeoutError(urllib.error.URLError):
        def __init__(self) -> None:
            super().__init__(_TimeoutReason("timed out"))

    def fake_urlopen(*args: object, **kwargs: object):
        calls.append(kwargs.get("timeout"))
        raise _TimeoutError()

    monkeypatch.setattr(food_sources.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError):
        food_sources._fetch("https://feeds.npr.org/1001/rss.xml", timeout=15)

    assert calls == [15]


def test_project_fetch_timeout_does_not_retry_with_longer_timeout(monkeypatch: pytest.MonkeyPatch):
    calls: list[int | float | None] = []

    class _TimeoutReason(TimeoutError):
        pass

    class _TimeoutError(urllib.error.URLError):
        def __init__(self) -> None:
            super().__init__(_TimeoutReason("timed out"))

    def fake_urlopen(*args: object, **kwargs: object):
        calls.append(kwargs.get("timeout"))
        raise _TimeoutError()

    monkeypatch.setattr(expansion_module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError):
        expansion_module._project_fetch_with_metadata("https://example.com/story", timeout=15)

    assert calls == [15]


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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    audit = json.loads(Path(result["discovery_audit_json_path"]).read_text(encoding="utf-8"))
    debug = next(iter(audit["google_news_resolution_debug_by_candidate"].values()))

    assert candidates == []
    assert result["google_news_resolution_status_counts"]["failed_no_resolved_url"] == 1
    assert debug["google_news_resolution_status"] == "failed_no_resolved_url"
    assert debug["rejection_reason"] == "no_resolved_url"
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["no_resolved_url"] == 1


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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["no current pressure evidence"] == 1


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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    audit = json.loads(Path(result["discovery_audit_json_path"]).read_text(encoding="utf-8"))
    debug = next(iter(audit["google_news_resolution_debug_by_candidate"].values()))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_count"] == 1
    assert result["google_news_resolution_status_counts"]["failed_static_or_google_noise_only"] == 1
    assert debug["google_news_resolution_status"] == "failed_static_or_google_noise_only"
    assert debug["static_or_google_noise_only"] is True
    assert debug["google_news_rpc_attempted"] is False


def test_food_line_discovery_expansion_rejects_unrelated_google_news_publisher_family(tmp_path: Path):
    edition_date = "2026-06-21"
    publisher_url = "https://news.google.com/publications/CAAqBwgKMPOC1Qswj4fdAw"
    unrelated_url = "https://example.org/news/unrelated-story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Omaha food programs see record demand as summer break eliminates school meals",
                        "link": "https://news.google.com/rss/articles/CBMiWRONG?oc=5",
                        "source_url": publisher_url,
                        "publisher": "WOWT",
                        "description": "Food programs reported higher demand.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiWRONG?oc=5":
            return f"<html><body><a href=\"{unrelated_url}\">story</a></body></html>".encode("utf-8")
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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    audit = json.loads(Path(result["discovery_audit_json_path"]).read_text(encoding="utf-8"))
    debug = next(iter(audit["google_news_resolution_debug_by_candidate"].values()))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_count"] == 1
    assert result["google_news_resolution_status_counts"]["failed_no_same_publisher_family"] == 1
    assert debug["google_news_resolution_status"] == "failed_no_same_publisher_family"
    assert debug["accepted_candidate_url"] == ""
    assert debug["rejection_reason"] == "no same publisher family candidate url"
    assert result["early_exclusion_reasons"]["no_same_publisher_family"] == 1


def test_food_line_discovery_expansion_boston_herald_style_wrapper_failure_stays_non_public(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    edition_date = "2026-06-16"
    google_url = "https://news.google.com/rss/articles/CBMirwFBVV95cUxQaER6aU4zNlY3dWd0RC1VekROQ24wQjA4LXk5eThSYmJhU2dPRkpiY2JKWkhua0NRa3Flc0d2M0hseWFOc1ZvR29mYWk3ZzNFNE14Qkh3elVnckoydWVKdkhXWkJDQTJnTFVRcmZwOHhKb2twUi02c18yc3NXUHlZMVc0Wk1XdENQM01TelNnRzdINWlGWFVkMFRKSVR0b05uckRlLWIyU1dzMmwxY3Fr?oc=5"
    homepage_url = "https://www.bostonherald.com"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Greater Boston Food Bank to spend record-breaking $65M on food in 2026 - Boston Herald",
                        "link": google_url,
                        "source_url": homepage_url,
                        "publisher": "Boston Herald",
                        "description": "Food bank demand and operating strain in Boston.",
                        "pubDate": "Tue, 16 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == google_url:
            return (
                "<html><body>"
                '<div data-n-a-id="CBMirwFBVV95cUxQaER6aU4zNlY3dWd0RC1VekROQ24wQjA4LXk5eThSYmJhU2dPRkpiY2JKWkhua0NRa3Flc0d2M0hseWFOc1ZvR29mYWk3ZzNFNE14Qkh3elVnckoydWVKdkhXWkJDQTJnTFVRcmZwOHhKb2twUi02c18yc3NXUHlZMVc0Wk1XdENQM01TelNnRzdINWlGWFVkMFRKSVR0b05uckRlLWIyU1dzMmwxY3Fr" '
                'data-n-a-ts="1782841548" data-n-a-sg="AVvZt1FeWQBagmhL_V1m7VKOxBBR"></div>'
                '<a href="https://news.google.com">noise</a>'
                '<a href="https://lh3.googleusercontent.com/example=w16">img</a>'
                "</body></html>"
            ).encode("utf-8")
        if url == homepage_url:
            return _html_article(title="Boston Herald", canonical=homepage_url, body="Homepage only trace.")
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(expansion_module, "_google_news_rpc_request", lambda article_id, timestamp, signature: ("", "rpc_without_article_url"))

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
    audit = json.loads(Path(result["discovery_audit_json_path"]).read_text(encoding="utf-8"))
    debug = audit["google_news_resolution_debug_by_candidate"][candidate["candidate_id"]]

    assert candidate["final_trace_url"] == homepage_url
    assert candidate["traceability_status"] == "publisher_homepage_trace_only"
    assert candidate["public_claim_eligible"] is False
    assert "homepage_or_landing_url" in candidate["public_claim_blockers"]
    assert "publisher_homepage_trace_only" in candidate["public_claim_blockers"]
    assert debug["google_news_resolution_status"] == "failed_static_or_google_noise_only"
    assert debug["google_news_rpc_attempted"] is True
    assert debug["google_news_rpc_error"] == "rpc_without_article_url"
    assert debug["static_or_google_noise_only"] is True
    assert debug["google_news_rpc_url"] == ""


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
        "title_extraction_method",
        "raw_title_candidates",
        "selected_title",
        "title_quality_status",
        "title_quality_blocker_applied",
    ):
        assert key in candidate


@pytest.mark.parametrize(
    "generic_title",
    [
        "Skip to content",
        "Donate",
        "Our Blog",
        "Blog",
        "News",
        "Home | Example News",
        "Welcome",
        "Homepage",
        "All Songs Considered",
        "AirTalk",
        "Apply for CalFresh",
    ],
)
def test_generic_titles_are_review_only_even_when_otherwise_eligible(tmp_path: Path, generic_title: str):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": generic_title,
                        "link": "https://news.google.com/rss/articles/CBMiGENERIC?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising for families relying on SNAP and school meals.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiGENERIC?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return (
                f"<html><head><title>{generic_title}</title>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T12:00:00Z\">"
                "</head><body><article><p>Food pantry demand is rising for families relying on SNAP and school meals.</p></article></body></html>"
            ).encode("utf-8")
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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["generic_or_invalid_title"] == 1
    assert not (tmp_path / "output" / "site").exists()


def test_og_title_replaces_generic_page_chrome(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Skip to content",
                        "link": "https://news.google.com/rss/articles/CBMiOG?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiOG?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return (
                "<html><head>"
                "<title>Skip to content - Example News</title>"
                "<meta property=\"og:title\" content=\"Pantry demand rises as SNAP delays hit families\">"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T12:00:00Z\">"
                "</head><body><article><h1>Skip to content</h1><p>Food pantry demand is rising.</p></article></body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(tmp_path, edition_date, fetcher=fetcher, max_queries=1, max_results_per_query=5, query_lookback_days=0, query_lookahead_days=0, public_claim_lookback_days=0, public_claim_lookahead_days=0)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["discovered_title"] == "Pantry demand rises as SNAP delays hit families"
    assert candidate["selected_title"] == "Pantry demand rises as SNAP delays hit families"
    assert candidate["title_extraction_method"] == "og_title"
    assert candidate["title_quality_status"] == "valid_article_title"
    assert candidate["public_claim_eligible"] is True


def test_json_ld_headline_replaces_generic_page_chrome(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "News",
                        "link": "https://news.google.com/rss/articles/CBMiLD?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiLD?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return (
                "<html><head>"
                "<title>News</title>"
                "<script type=\"application/ld+json\">"
                "{\"@context\":\"https://schema.org\",\"@type\":\"NewsArticle\",\"headline\":\"School meal disruptions leave families scrambling\"}"
                "</script>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T12:00:00Z\">"
                "</head><body><p>Food pantry demand is rising.</p></body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(tmp_path, edition_date, fetcher=fetcher, max_queries=1, max_results_per_query=5, query_lookback_days=0, query_lookahead_days=0, public_claim_lookback_days=0, public_claim_lookahead_days=0)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["discovered_title"] == "School meal disruptions leave families scrambling"
    assert candidate["title_extraction_method"] == "json_ld_headline"
    assert candidate["public_claim_eligible"] is True


def test_article_h1_replaces_generic_page_chrome_when_meta_titles_are_generic(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Blog",
                        "link": "https://news.google.com/rss/articles/CBMiH1?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiH1?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return (
                "<html><head>"
                "<title>Blog</title>"
                "<meta property=\"og:title\" content=\"Blog\">"
                "<meta name=\"twitter:title\" content=\"Blog\">"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T12:00:00Z\">"
                "</head><body><article><h1>Pantries report a surge in summer demand</h1><p>Food pantry demand is rising.</p></article></body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(tmp_path, edition_date, fetcher=fetcher, max_queries=1, max_results_per_query=5, query_lookback_days=0, query_lookahead_days=0, public_claim_lookback_days=0, public_claim_lookahead_days=0)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["discovered_title"] == "Pantries report a surge in summer demand"
    assert candidate["title_extraction_method"] == "article_h1"
    assert candidate["public_claim_eligible"] is True


def test_document_title_is_only_used_after_better_headline_sources_fail(tmp_path: Path):
    edition_date = "2026-06-21"
    article_url = "https://example.com/story"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "News",
                        "link": "https://news.google.com/rss/articles/CBMiDOC?oc=5",
                        "source_url": article_url,
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiDOC?oc=5":
            return f"<html><body><a href=\"{article_url}\">story</a></body></html>".encode("utf-8")
        if url == article_url:
            return (
                "<html><head>"
                "<title>Food pantry demand rises in rural counties - Example News</title>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T12:00:00Z\">"
                "</head><body><p>Food pantry demand is rising.</p></body></html>"
            ).encode("utf-8")
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(tmp_path, edition_date, fetcher=fetcher, max_queries=1, max_results_per_query=5, query_lookback_days=0, query_lookahead_days=0, public_claim_lookback_days=0, public_claim_lookahead_days=0)
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["discovered_title"] == "Food pantry demand rises in rural counties"
    assert candidate["title_extraction_method"] == "document_title"
    assert candidate["public_claim_eligible"] is True


def test_paginated_listing_filters_action_and_listing_links_but_keeps_article_candidate(tmp_path: Path):
    edition_date = "2026-06-21"
    archive_url = "https://frac.org/blog/page/2"
    article_url = "https://frac.org/blog/2026/06/21/summer-meals-pressure-rising"
    report_url = "https://frac.org/wp-content/uploads/frac-brief.pdf"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "FRAC News",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["frac.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 2,
                "max_age_days": 30,
                "pressure_terms": ["food insecurity", "SNAP", "school meals"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == archive_url:
            return (
                "<html><head><title>FRAC News</title></head><body>"
                "<nav><a href=\"https://frac.org/action\">Legislative Action Center</a></nav>"
                "<p>June 21, 2026</p>"
                f"<a href=\"{article_url}\">Summer meals pressure rising for families</a>"
                "<a href=\"https://frac.org/donate\">Donate</a>"
                "<a href=\"https://frac.org/search?q=summer+meals\">Search</a>"
                "<a href=\"https://frac.org/category/news\">Category archive</a>"
                "<a href=\"https://frac.org/tag/snap\">SNAP tag</a>"
                "<a href=\"https://frac.org/author/editor\">Author</a>"
                "<a href=\"https://frac.org/blog\">Blog</a>"
                "<a href=\"https://frac.org/page/3\">Next</a>"
                "<a href=\"https://frac.org/resources\">Resources</a>"
                f"<a href=\"{report_url}\">Download report</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == article_url:
            return (
                "<html><head><title>Summer meals pressure rising for families</title>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T12:00:00Z\">"
                "</head><body><article><p>Food insecurity pressure is rising.</p></article></body></html>"
            ).encode("utf-8")
        if url == report_url:
            return b"%PDF-1.4 fake pdf bytes"
        raise AssertionError(url)

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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    candidate_urls = {row["final_trace_url"] for row in candidates}

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 2
    assert result["early_exclusion_reasons"]["no current pressure evidence"] == 1
    assert result["early_exclusion_reasons"]["outside_backfill_date_window"] == 1
    assert result["archive_links_rejected_count"] == 9
    assert result["archive_links_accepted_count"] == 2
    assert result["archive_links_rejected_by_source"] == {"FRAC News": 9}
    assert result["archive_links_accepted_by_source"] == {"FRAC News": 2}
    assert result["archive_links_rejected_by_reason"] == {
        "action_anchor_text": 1,
        "listing_root:blog": 1,
        "pagination_link": 1,
        "path_segment:donate": 1,
        "resource_segment:resources": 1,
        "search_listing": 1,
        "taxonomy_listing": 3,
    }


def test_archive_link_filter_rejects_unknown_nonarticle_paths(tmp_path: Path):
    edition_date = "2026-06-21"
    archive_url = "https://example.org/archive"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Archive Source",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 2,
                "max_age_days": 30,
                "pressure_terms": ["food insecurity"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == archive_url:
            return (
                "<html><body>"
                "<a href=\"https://example.org/updates\">Updates</a>"
                "<a href=\"https://example.org/press/overview\">Overview</a>"
                "</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, edition_date, fetcher=fetcher, max_queries=1, max_results_per_query=5)

    assert result["archive_links_rejected_count"] == 2
    assert result["archive_links_rejected_by_reason"]["listing_root:updates"] == 1
    assert result["archive_links_rejected_by_reason"]["weak_article_path_without_date_or_slug"] == 1


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
    assert manual_candidates[0]["public_claim_eligible"] is False
    assert "blocked_fetch" in manual_candidates[0]["public_claim_blockers"]
    assert manual_candidates[0]["pressure_summary"]
    assert "No candidates were retained" not in audit["discovery_confidence_summary"]
    assert "no_current_update" in audit["no_current_update_reason"] or audit["no_current_update_reason"]


def test_traceable_article_candidate_with_missing_public_prose_fields_is_not_public_eligible(tmp_path: Path):
    article_url = "https://example.org/news/food-assistance-brief"
    manual_fallback_record = {
        "publisher": "Example News",
        "canonical_url": article_url,
        "headline": "Food assistance brief",
        "date": "2026-06-21",
        "location": "Charlotte, NC",
        "manually_reviewed_summary": "The source discusses food assistance pressure.",
        "pressure_evidence_summary": "The source discusses food assistance pressure.",
        "affected_groups": ["families"],
        "limitations": "Manual fallback example.",
        "extraction_quality": "manual_fallback",
        "reviewer_or_source_note": "Reviewed from source text.",
        "final_trace_url": article_url,
        "geographic_scope": "metro",
    }

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload([])
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        manual_fallback_records=[manual_fallback_record],
        max_queries=1,
        max_results_per_query=5,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["traceability_status"] == "traceable"
    assert candidate["classification_status"] == "manual_fallback"
    assert candidate["candidate_review_status"] == "needs_review"
    assert candidate["public_claim_eligible"] is False
    assert "missing_public_prose_fields" in candidate["public_claim_blockers"]
    assert "pressure_type" in candidate["missing_public_prose_fields"]
    assert result["missing_public_prose_fields_count"] >= 1
    assert result["public_eligible_blocked_by_missing_public_prose_count"] >= 1
    assert not (tmp_path / "output" / "site").exists()


def test_frac_style_candidate_derives_public_prose_from_source_evidence(tmp_path: Path):
    candidate = _normalize_candidate_row(
        {
            "candidate_id": "frac-test",
            "discovered_publisher": "FRAC News",
            "discovered_title": "USDA Proposal to End Broad-Based Categorical Eligibility for SNAP Would Increase Hunger for Families and Children - Food Research & Action Center",
            "selected_title": "USDA Proposal to End Broad-Based Categorical Eligibility for SNAP Would Increase Hunger for Families and Children - Food Research & Action Center",
            "source_url": "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
            "original_source_url": "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
            "final_trace_url": "https://frac.org/blog/usda-proposal-to-end-broad-based-categorical-eligibility-for-snap-would-increase-hunger-for-families-and-children",
            "source_published_date": "2026-06-21",
            "discovery_lane": "nonprofit_report",
            "source_family": "nonprofit_report",
            "classification_status": "qualified_pressure_signal",
            "traceability_status": "traceable",
            "public_claim_eligible": True,
            "public_claim_blockers": [],
            "fetch_status": "ok",
            "candidate_review_status": "needs_review",
            "summary_or_snippet": "FRAC warned that the USDA proposal would increase hunger for families and children.",
            "evidence_text": "Published June 21, 2026. FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children.",
            "evidence_text_basis": "page_text_excerpt",
            "affected_groups": ["children", "SNAP households", "low-income households"],
        }
    )
    _apply_public_readiness_gate(candidate, edition_date="2026-06-21")

    assert candidate["discovered_publisher"] == "FRAC News"
    assert candidate["traceability_status"] == "traceable"
    assert candidate["candidate_review_status"] == "needs_review"
    assert candidate["public_claim_eligible"] is True
    assert candidate["pressure_type"] == "SNAP policy pressure"
    assert (
        candidate["pressure_summary"]
        == "FRAC warned that a USDA proposal to end broad-based categorical eligibility for SNAP would increase hunger for families and children."
    )
    assert candidate["public_claim_blockers"] == []
    assert candidate["missing_public_prose_fields"] == []
    assert candidate["public_prose_derivation_status"] == "derived_complete"
    assert candidate["pressure_summary_derivation_status"] == "derived_from_title_and_source_text"
    assert candidate["pressure_type_derivation_status"] == "derived_from_selected_title"
    assert candidate["source_role"] == "policy_analysis"
    assert candidate["source_role_derivation_status"] == "derived_from_policy_source_text"
    assert "selected_title" in candidate["public_prose_derivation_source_fields"]


def test_vague_source_text_does_not_derive_public_prose(tmp_path: Path):
    article_url = "https://example.org/news/update"
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
                "pressure_terms": ["food assistance"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return _rss_payload(
                [
                    {
                        "title": "Update",
                        "link": article_url,
                        "source_url": article_url,
                        "publisher": "Example Direct Feed",
                        "description": "Community update.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return _html_article(title="Update", canonical=article_url, body="Community update.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["no current pressure evidence"] == 1


def test_complete_source_backed_public_prose_fields_can_remain_public_eligible(tmp_path: Path):
    article_url = "https://example.com/story"
    manual_fallback_record = {
        "publisher": "Example News",
        "canonical_url": article_url,
        "headline": "Food pantry demand rises",
        "date": "2026-06-21",
        "location": "Charlotte, NC",
        "manually_reviewed_summary": "The source says food pantry demand is rising for families with children.",
        "pressure_evidence_summary": "The source says pantry demand is rising for families with children as SNAP support tightens.",
        "affected_groups": ["families", "children"],
        "pressure_type": "demand strain",
        "evidence_level": "news report",
        "freshness_role": "dated_recent_signal",
        "source_role": "local_news_report",
        "limitations": "Manual fallback example.",
        "extraction_quality": "manual_fallback",
        "reviewer_or_source_note": "Reviewed from source text.",
        "final_trace_url": article_url,
        "geographic_scope": "metro",
    }

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload([])
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        manual_fallback_records=[manual_fallback_record],
        max_queries=1,
        max_results_per_query=5,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert candidate["classification_status"] == "manual_fallback"
    assert candidate["public_claim_eligible"] is True
    assert candidate["missing_public_prose_fields"] == []
    assert candidate["pressure_summary"] == manual_fallback_record["pressure_evidence_summary"]
    assert candidate["pressure_type"] == "demand strain"
    assert candidate["evidence_level"] == "news report"
    assert candidate["freshness_role"] == "dated_recent_signal"
    assert candidate["source_role"] == "local_news_report"


def test_resource_program_page_stays_resource_context(tmp_path: Path):
    candidate = _normalize_candidate_row(
        {
            "candidate_id": "resource-test",
            "discovered_publisher": "Example Resource Center",
            "selected_title": "Find Food Near You",
            "source_url": "https://example.org/programs/find-food",
            "final_trace_url": "https://example.org/programs/find-food",
            "classification_status": "context_only",
            "fetch_status": "ok",
            "summary_or_snippet": "Find food near you and learn about meal sites.",
            "evidence_text": "Find food near you. Need help. Meal sites and programs.",
        }
    )
    _apply_public_readiness_gate(candidate, edition_date="2026-06-21")

    assert candidate["source_role"] == "resource_context"
    assert candidate["source_role_derivation_status"] == "derived_as_resource_context"
    assert candidate["public_claim_eligible"] is False


def test_local_news_article_derives_local_news_report(tmp_path: Path):
    candidate = _normalize_candidate_row(
        {
            "candidate_id": "local-news-role",
            "discovered_publisher": "Example Local News",
            "selected_title": "Food pantry demand rises in Charlotte",
            "source_url": "https://example.org/2026/06/21/food-pantry-demand-rises-charlotte",
            "final_trace_url": "https://example.org/2026/06/21/food-pantry-demand-rises-charlotte",
            "source_family": "local_news_direct_rss",
            "discovery_lane": "news_article",
            "classification_status": "qualified_pressure_signal",
            "fetch_status": "ok",
            "summary_or_snippet": "Food pantry demand rises in Charlotte.",
            "evidence_text": "Food pantry demand rises in Charlotte as more families seek help.",
            "affected_groups": ["families"],
            "evidence_level": "news report",
            "freshness_role": "fresh_daily_signal",
            "pressure_type": "food bank demand pressure",
            "pressure_summary": "Example Local News reported rising food pantry demand in Charlotte.",
        }
    )
    _apply_public_readiness_gate(candidate, edition_date="2026-06-21")

    assert candidate["source_role"] == "local_news_report"
    assert candidate["source_role_derivation_status"] == "derived_from_source_family"


def test_traceable_local_news_record_spending_article_derives_safe_public_prose():
    candidate = _normalize_candidate_row(
        {
            "candidate_id": "boston-herald-public-prose",
            "discovered_publisher": "Boston Herald",
            "selected_title": "Greater Boston Food Bank to spend record-breaking $65M on food in 2026",
            "source_url": "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026",
            "final_trace_url": "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026",
            "source_family": "local_news_direct_rss",
            "discovery_lane": "news_article",
            "classification_status": "qualified_pressure_signal",
            "traceability_status": "traceable",
            "public_claim_eligible": True,
            "public_claim_blockers": [],
            "fetch_status": "ok",
            "summary_or_snippet": "The Greater Boston Food Bank is set to invest another $5 million to break their food spending record in 2026 and distribute over 94 million meals across the region as need grows.",
            "evidence_text": "Greater Boston Food Bank to spend record-breaking $65M on food in 2026. The Greater Boston Food Bank is set to invest another $5 million to break their food spending record in 2026 and distribute over 94 million meals across the region as need grows.",
            "evidence_text_basis": "page_text_excerpt",
            "pressure_type": "food bank demand pressure",
            "evidence_level": "background context",
            "freshness_role": "fresh_daily_signal",
        }
    )
    _apply_public_readiness_gate(candidate, edition_date="2026-06-16")

    assert candidate["public_claim_eligible"] is True
    assert candidate["missing_public_prose_fields"] == []
    assert (
        candidate["pressure_summary"]
        == "Boston Herald reported that Greater Boston Food Bank expects to spend a record $65M on food in 2026 as need grows."
    )
    assert candidate["pressure_summary_derivation_status"] == "derived_from_title_and_source_text"
    assert candidate["source_role"] == "local_news_report"
    assert candidate["source_role_derivation_status"] == "derived_from_source_family"


def test_local_news_record_spending_title_without_source_support_stays_blocked():
    candidate = _normalize_candidate_row(
        {
            "candidate_id": "boston-herald-boilerplate",
            "discovered_publisher": "Boston Herald",
            "selected_title": "Greater Boston Food Bank to spend record-breaking $65M on food in 2026",
            "source_url": "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026",
            "final_trace_url": "https://www.bostonherald.com/2026/06/16/greater-boston-food-bank-to-spend-record-breaking-65m-on-food-in-2026",
            "source_family": "local_news_direct_rss",
            "discovery_lane": "news_article",
            "classification_status": "qualified_pressure_signal",
            "traceability_status": "traceable",
            "public_claim_eligible": True,
            "public_claim_blockers": [],
            "fetch_status": "ok",
            "summary_or_snippet": "The Boston Herald is the leading source of breaking news, local news, sports, politics, entertainment, opinion and weather in Boston, Massachusetts.",
            "evidence_text": "The Boston Herald is the leading source of breaking news, local news, sports, politics, entertainment, opinion and weather in Boston, Massachusetts.",
            "evidence_text_basis": "page_text_excerpt",
            "pressure_type": "food bank demand pressure",
            "evidence_level": "background context",
            "freshness_role": "fresh_daily_signal",
        }
    )
    _apply_public_readiness_gate(candidate, edition_date="2026-06-16")

    assert candidate["public_claim_eligible"] is False
    assert "missing_public_prose_fields" in candidate["public_claim_blockers"]
    assert candidate["pressure_summary"] == ""
    assert candidate["pressure_summary_derivation_status"] == "insufficient_source_support"


def test_public_radio_article_derives_public_radio_report(tmp_path: Path):
    candidate = _normalize_candidate_row(
        {
            "candidate_id": "public-radio-role",
            "discovered_publisher": "NPR National Desk",
            "selected_title": "Food bank demand rises across the region",
            "source_url": "https://www.npr.org/2026/06/21/1234567890/food-bank-demand-rises",
            "final_trace_url": "https://www.npr.org/2026/06/21/1234567890/food-bank-demand-rises",
            "source_family": "public_radio",
            "discovery_lane": "public_radio",
            "classification_status": "qualified_pressure_signal",
            "fetch_status": "ok",
            "summary_or_snippet": "Food bank demand rises across the region.",
            "evidence_text": "Food bank demand rises across the region as more households seek help.",
            "affected_groups": ["households"],
            "evidence_level": "news report",
            "freshness_role": "fresh_daily_signal",
            "pressure_type": "food bank demand pressure",
            "pressure_summary": "NPR reported rising food bank demand across the region.",
        }
    )
    _apply_public_readiness_gate(candidate, edition_date="2026-06-21")

    assert candidate["source_role"] == "public_radio_report"
    assert candidate["source_role_derivation_status"] == "derived_from_discovery_lane"


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


def test_direct_rss_prefers_feed_url_when_both_feed_and_page_are_configured(tmp_path: Path):
    article_url = "https://example.org/news/pantry-demand"
    feed_url = "https://example.org/feed.xml"
    page_url = "https://example.org/news"
    calls: list[str] = []
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Example Direct Feed",
                "source_family": "local_news_direct_rss",
                "discovery_lane": "news_article",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "source_url": page_url,
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
        calls.append(url)
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

    assert calls == [feed_url, article_url]
    assert candidate["discovery_channel"] == "direct_rss"
    assert candidate["direct_source_name"] == "Example Direct Feed"
    assert candidate["feed_url"] == feed_url
    assert candidate["source_url"] == article_url
    assert candidate["public_claim_eligible"] is True


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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["generic_or_invalid_title"] == 1


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
    assert result["direct_source_success_by_source"]["Example Direct Feed"] == 1
    assert result["direct_source_item_counts"]["Example Direct Feed"] == 1


def test_direct_source_parse_failure_reports_per_source_diagnostics(tmp_path: Path):
    feed_url = "https://example.org/broken.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Broken Feed",
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
            return {
                "payload": b"not-valid-xml",
                "response_status": 200,
                "final_response_url": feed_url,
                "content_type": "application/rss+xml; charset=UTF-8",
            }
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)

    assert result["direct_source_fetch_failure_reasons_by_source"]["Broken Feed"]["parse_failure"] == 1
    assert result["direct_sources_recommended_for_parser_fix"] == ["Broken Feed"]
    diagnostics = [row for row in result["direct_source_diagnostics"] if row["direct_source_name"] == "Broken Feed"]
    assert diagnostics
    assert diagnostics[0]["parser_attempted"] == "rss_or_atom"
    assert diagnostics[0]["failure_reason"] == "parse_failure"
    assert diagnostics[0]["recommended_action"] == "fix_parser"


def test_direct_source_blocked_403_is_marked_blocked_by_site(tmp_path: Path):
    source_url = "https://example.org/protected"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Blocked Source",
                "source_family": "snap_state_notice",
                "discovery_lane": "snap_state_notice",
                "discovery_channel": "direct_page",
                "source_url": source_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "max_age_days": 7,
                "pressure_terms": ["snap", "benefits"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == source_url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)

    diagnostics = [row for row in result["direct_source_diagnostics"] if row["direct_source_name"] == "Blocked Source"]
    assert diagnostics
    assert diagnostics[0]["failure_reason"] == "blocked_403"
    assert diagnostics[0]["recommended_action"] == "blocked_by_site"


def test_disabled_direct_sources_are_reported_and_skipped(tmp_path: Path):
    disabled_url = "https://example.org/disabled.xml"
    calls: list[str] = []
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Disabled Feed",
                "source_family": "local_news_direct_rss",
                "discovery_lane": "news_article",
                "discovery_channel": "direct_rss",
                "feed_url": disabled_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": False,
                "max_age_days": 7,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        calls.append(url)
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload([])
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=5)

    assert disabled_url not in calls
    assert result["disabled_direct_sources"] == ["Disabled Feed"]
    diagnostics = [row for row in result["direct_source_diagnostics"] if row["direct_source_name"] == "Disabled Feed"]
    assert diagnostics
    assert diagnostics[0]["source_disabled_or_skipped"] is True
    assert diagnostics[0]["recommended_action"] == "disable_source"


def test_direct_page_listing_extraction_keeps_article_and_document_links_only(tmp_path: Path):
    listing_url = "https://example.org/news"
    article_url = "https://example.org/news/article-one"
    document_url = "https://example.org/docs/food-assistance-report.pdf"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Listing Source",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": listing_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "direct_source_candidate_cap": 2,
                "max_age_days": 7,
                "pressure_terms": ["food assistance", "food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == listing_url:
            return (
                "<html><body>"
                f"<a href=\"{article_url}\">Food pantry demand article</a>"
                f"<a href=\"{document_url}\">Food assistance report PDF</a>"
                "<a href=\"https://example.org/\">Homepage</a>"
                "<a href=\"https://example.org/feed\">Feed</a>"
                "<a href=\"https://example.org/calendar\">Calendar</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        if url == document_url:
            return b"%PDF-1.7 food assistance report"
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(tmp_path, "2026-06-21", fetcher=fetcher, max_queries=1, max_results_per_query=10)
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))
    source_urls = {row["source_url"] for row in candidates}

    assert source_urls == {article_url}
    assert "https://example.org" not in source_urls
    assert "https://example.org/feed" not in source_urls
    assert "https://example.org/calendar" not in source_urls


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


def test_direct_rss_prefers_exact_date_items_before_newer_out_of_window_items(tmp_path: Path):
    feed_url = "https://example.org/feed.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Balanced Feed",
                "source_family": "food_bank_provider",
                "discovery_lane": "food_bank_provider",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
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
                        "title": "Newest but out of window",
                        "link": "https://example.org/2026/06/25/newest-story",
                        "source_url": "https://example.org/2026/06/25/newest-story",
                        "publisher": "Balanced Feed",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Thu, 25 Jun 2026 12:00:00 GMT",
                    },
                    {
                        "title": "Exact date match",
                        "link": "https://example.org/2026/06/21/exact-story",
                        "source_url": "https://example.org/2026/06/21/exact-story",
                        "publisher": "Balanced Feed",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "Sun, 21 Jun 2026 12:00:00 GMT",
                    },
                ]
            )
        if url == "https://example.org/2026/06/21/exact-story":
            return _html_article(title="Exact date match", canonical=url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert len(candidates) == 1
    assert candidates[0]["source_url"] == "https://example.org/2026/06/21/exact-story"
    assert candidates[0]["date_match_status"] == "exact_date"
    assert candidates[0]["date_basis"] == "feed_published"
    assert candidates[0]["selected_after_date_filter"] is True
    assert result["candidates_by_direct_source"] == {"Balanced Feed": 1}
    assert result["in_window_direct_candidate_count"] == 1
    assert result["out_of_window_direct_candidate_count"] == 0
    assert result["direct_candidates_by_date_match_status"]["exact_date"] == 1


def test_direct_page_prefers_exact_date_article_links_before_out_of_window_links(tmp_path: Path):
    agenda_url = "https://example.org/agenda"
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
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
                "pressure_terms": ["food assistance", "food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == agenda_url:
            return (
                "<html><body>"
                "<a href=\"https://example.org/2026/06/25/latest-food-assistance\">Latest update</a>"
                "<a href=\"https://example.org/2026/06/21/food-assistance-report\">Target-date update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == "https://example.org/2026/06/21/food-assistance-report":
            return (
                "<html><head>"
                "<link rel=\"canonical\" href=\"https://example.org/2026/06/21/food-assistance-report\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T10:00:00Z\">"
                "</head><body>Food assistance demand is rising.</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert len(candidates) == 1
    assert candidates[0]["source_url"] == "https://example.org/2026/06/21/food-assistance-report"
    assert candidates[0]["date_match_status"] == "exact_date"
    assert candidates[0]["date_basis"] == "page_meta_date"
    assert result["candidates_by_direct_source"] == {"Agenda Source": 1}
    assert result["direct_sources_with_in_window_items"] == ["Agenda Source"]


def test_direct_page_listing_context_dates_surface_historical_exact_date_items(tmp_path: Path):
    archive_url = "https://example.org/archive"
    exact_article_url = "https://example.org/posts/exact-story"
    newer_article_url = "https://example.org/posts/newer-story"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Historical Archive",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "historical_capable": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == archive_url:
            return (
                "<html><body>"
                "<p>June 25, 2026</p>"
                f"<a href=\"{newer_article_url}\">Newest archive update</a>"
                "<p>June 21, 2026</p>"
                f"<a href=\"{exact_article_url}\">Target archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == exact_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{exact_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        if url == newer_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{newer_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-25T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert len(candidates) == 1
    assert candidates[0]["source_url"] == exact_article_url
    assert candidates[0]["date_match_status"] == "exact_date"
    assert candidates[0]["date_basis"] == "page_meta_date"
    assert result["historical_source_count"] == 1
    assert result["historical_sources_with_exact_date_items"] == ["Historical Archive"]
    assert result["historical_sources_with_page_body_date_items"] == ["Historical Archive"]


def test_direct_page_prefers_targeted_historical_archive_candidates_before_broad_archive(tmp_path: Path):
    archive_url = "https://example.org/archive"
    targeted_archive_url = "https://example.org/archive/2026/06"
    exact_article_url = "https://example.org/posts/exact-story"
    broad_article_url = "https://example.org/posts/newer-story"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Historical Archive",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "historical_capable": True,
                "historical_archive_templates": [
                    {
                        "template_name": "monthly_archive",
                        "url_template": "https://example.org/archive/{yyyy}/{mm}",
                        "archive_granularity": "month",
                    }
                ],
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        calls.append(url)
        if url == targeted_archive_url:
            return (
                "<html><body>"
                "<p>June 21, 2026</p>"
                f"<a href=\"{exact_article_url}\">Target archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == archive_url:
            return (
                "<html><body>"
                "<p>June 25, 2026</p>"
                f"<a href=\"{broad_article_url}\">Broad archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == exact_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{exact_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        if url == broad_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{broad_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-25T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert calls[:2] == [targeted_archive_url, archive_url]
    assert len(candidates) == 1
    assert candidates[0]["source_url"] == exact_article_url
    assert candidates[0]["archive_url_used"] == targeted_archive_url
    assert candidates[0]["archive_template_used"] == "monthly_archive"
    assert candidates[0]["archive_granularity"] == "month"
    assert candidates[0]["archive_target_date"] == "2026-06-21"
    assert candidates[0]["archive_candidate_rank"] == 1
    assert result["historical_archive_source_count"] == 1
    assert result["historical_archive_fetch_attempt_count"] == 1
    assert result["historical_archive_fetch_success_count"] == 1
    assert result["historical_archive_fetch_failure_count"] == 0
    assert result["historical_archive_url_count"] == 1
    assert result["historical_archive_candidates_extracted_count"] == 1
    assert result["historical_archive_sources_with_templates"] == ["Historical Archive"]
    assert result["historical_archive_sources_without_templates"] == []
    assert result["historical_archive_candidates_by_source"] == {"Historical Archive": 1}
    assert result["historical_archive_exact_date_candidates_by_source"] == {"Historical Archive": 1}
    assert result["historical_archive_selected_before_broad_count"] == 1


def test_historical_direct_page_without_templates_reports_archive_diagnostics(tmp_path: Path):
    archive_url = "https://example.org/archive"
    exact_article_url = "https://example.org/posts/exact-story"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Historical Archive",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "historical_capable": True,
                "historical_archive_templates": [],
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == archive_url:
            return (
                "<html><body>"
                "<p>June 21, 2026</p>"
                f"<a href=\"{exact_article_url}\">Target archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == exact_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{exact_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )

    assert result["historical_archive_source_count"] == 1
    assert result["historical_archive_fetch_attempt_count"] == 0
    assert result["historical_archive_fetch_success_count"] == 0
    assert result["historical_archive_fetch_failure_count"] == 0
    assert result["historical_archive_url_count"] == 0
    assert result["historical_archive_candidates_extracted_count"] == 0
    assert result["historical_archive_sources_with_templates"] == []
    assert result["historical_archive_sources_without_templates"] == ["Historical Archive"]


def test_paginated_archive_pages_fetch_in_order_and_stop_on_no_new_links(tmp_path: Path):
    archive_url = "https://example.org/archive"
    page_one_url = "https://example.org/archive?page=1"
    page_two_url = "https://example.org/archive?page=2"
    exact_article_url = "https://example.org/posts/exact-story"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Historical Archive",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "historical_capable": True,
                "historical_archive_pagination_enabled": True,
                "archive_page_url_template": "https://example.org/archive?page={page}",
                "archive_page_start": 1,
                "archive_page_max_pages": 3,
                "archive_page_increment": 1,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 2,
                "max_age_days": 30,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        calls.append(url)
        if url == page_one_url:
            return (
                "<html><body>"
                "<p>June 21, 2026</p>"
                f"<a href=\"{exact_article_url}\">Target archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == page_two_url:
            return (
                "<html><body>"
                "<p>June 21, 2026</p>"
                f"<a href=\"{exact_article_url}\">Target archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == archive_url:
            return (
                "<html><body>"
                "<p>June 25, 2026</p>"
                f"<a href=\"https://example.org/posts/newer-story\">Broad archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == exact_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{exact_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-21T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        if url == "https://example.org/posts/newer-story":
            return (
                "<html><head>"
                "<link rel=\"canonical\" href=\"https://example.org/posts/newer-story\">"
                "<meta property=\"article:published_time\" content=\"2026-06-25T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidate = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))[0]

    assert calls[:3] == [page_one_url, page_two_url, archive_url]
    assert candidate["archive_page_url_used"] == page_one_url
    assert candidate["archive_page_number"] == 1
    assert candidate["archive_pagination_rank"] == 1
    assert candidate["archive_stop_context"] == "page=2"
    assert result["historical_archive_pagination_source_count"] == 1
    assert result["historical_archive_page_fetch_attempt_count"] == 2
    assert result["historical_archive_page_fetch_success_count"] == 2
    assert result["historical_archive_page_fetch_failure_count"] == 0
    assert result["historical_archive_pages_fetched_by_source"] == {"Historical Archive": 2}
    assert result["historical_archive_links_extracted_by_source"] == {"Historical Archive": 1}
    assert result["historical_archive_in_window_candidates_by_source"] == {"Historical Archive": 1}
    assert result["historical_archive_stop_reason_by_source"] == {"Historical Archive": "no_new_links"}
    assert result["historical_archive_duplicate_link_count_by_source"] == {"Historical Archive": 1}


def test_paginated_archive_source_reports_without_hits_and_keeps_out_of_window_items_non_public(tmp_path: Path):
    archive_url = "https://example.org/archive"
    page_one_url = "https://example.org/archive?page=1"
    old_article_url = "https://example.org/posts/old-story"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Historical Archive",
                "source_family": "nonprofit_report",
                "discovery_lane": "nonprofit_report",
                "discovery_channel": "direct_page",
                "source_url": archive_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "historical_capable": True,
                "historical_archive_pagination_enabled": True,
                "archive_page_url_template": "https://example.org/archive?page={page}",
                "archive_page_start": 1,
                "archive_page_max_pages": 1,
                "archive_page_increment": 1,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
                "pressure_terms": ["food pantry", "demand"],
                "exclusion_terms": [],
            }
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == page_one_url:
            return (
                "<html><body>"
                "<p>June 10, 2026</p>"
                f"<a href=\"{old_article_url}\">Old archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == archive_url:
            return (
                "<html><body>"
                "<p>June 10, 2026</p>"
                f"<a href=\"{old_article_url}\">Old archive update</a>"
                "</body></html>"
            ).encode("utf-8")
        if url == old_article_url:
            return (
                "<html><head>"
                f"<link rel=\"canonical\" href=\"{old_article_url}\">"
                "<meta property=\"article:published_time\" content=\"2026-06-10T10:00:00Z\">"
                "</head><body>Food pantry demand is rising.</body></html>"
            ).encode("utf-8")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["outside_backfill_date_window"] == 1
    assert result["historical_archive_pagination_sources_without_hits"] == ["Historical Archive"]


def test_direct_source_missing_date_items_are_diagnosed_and_non_public(tmp_path: Path):
    feed_url = "https://example.org/missing-date.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Missing Date Feed",
                "source_family": "public_radio",
                "discovery_lane": "public_radio",
                "discovery_channel": "direct_rss",
                "feed_url": feed_url,
                "allowed_domains": ["example.org"],
                "geographic_scope": "national",
                "enabled": True,
                "sampling_priority": 10,
                "direct_source_candidate_cap": 1,
                "max_age_days": 30,
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
                        "title": "Missing date story",
                        "link": "https://example.org/story-without-date",
                        "source_url": "https://example.org/story-without-date",
                        "publisher": "Missing Date Feed",
                        "description": "Food pantry demand is rising.",
                        "pubDate": "",
                    }
                ]
            )
        if url == "https://example.org/story-without-date":
            return _html_article(title="Missing date story", canonical=url, body="Food pantry demand is rising.")
        raise AssertionError(url)

    result = run_food_line_discovery_expansion(
        tmp_path,
        "2026-06-21",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        query_lookback_days=0,
        query_lookahead_days=0,
        public_claim_lookback_days=0,
        public_claim_lookahead_days=0,
    )
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["outside_backfill_date_window"] == 1
    assert result["missing_date_direct_candidate_count"] == 0
    assert result["direct_candidates_by_date_match_status"] == {}
    assert result["direct_candidates_by_date_basis"] == {}
    assert result["direct_sources_with_no_in_window_items"] == ["Missing Date Feed"]


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
    candidates = json.loads(Path(result["discovery_candidates_path"]).read_text(encoding="utf-8"))

    assert candidates == []
    assert result["candidate_count"] == 0
    assert result["raw_candidate_count"] == 1
    assert result["early_exclusion_reasons"]["outside_backfill_date_window"] == 1


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
