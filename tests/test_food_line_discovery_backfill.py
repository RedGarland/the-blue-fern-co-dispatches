from __future__ import annotations

import json
from pathlib import Path

import scripts.backfill_food_line_discovery as backfill


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
                "query_family": "pressure",
                "geographic_scope": "national",
                "source_family": "local_news",
                "templates": ['"food pantry"'],
            }
        ],
        "metros": [{"name": "Charlotte"}],
    }
    (config_dir / "discovery_expansion_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


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


def test_food_line_discovery_backfill_writes_per_date_candidate_and_review_artifacts(tmp_path: Path):
    call_count = {"rss": 0}

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            call_count["rss"] += 1
            if call_count["rss"] == 1:
                return _rss_payload(
                    [
                        {
                            "title": "Food bank says demand is rising in Charlotte",
                            "link": "https://news.google.com/rss/articles/CBMiAXY?oc=5",
                            "source_url": "https://example.com/charlotte-demand",
                            "publisher": "Example Local News",
                            "description": "The food bank says demand is rising and more families are showing up.",
                        }
                    ]
                )
            return _rss_payload([])
        if url == "https://example.com/charlotte-demand":
            return b"""<html><head><title>Charlotte demand</title><link rel=\"canonical\" href=\"https://example.com/charlotte-demand\"></head><body><p>Food bank demand is rising and more families are showing up.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    # Run with monkeypatched discovery fetcher by calling lower-level functions through the module.
    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-21",
            "2026-06-22",
            max_queries=1,
            max_results_per_query=5,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "candidates" / "2026-06-21" / "candidate_sources.json"
    review_json_path = tmp_path / "output" / "review" / "food-line" / "2026-06-21" / "candidate_review.json"
    review_html_path = tmp_path / "output" / "review" / "food-line" / "2026-06-21" / "candidate_review.html"
    summary_json_path = tmp_path / "output" / "review" / "food-line" / "backfill" / "2026-06-21_to_2026-06-22" / "backfill_summary.json"
    summary_html_path = tmp_path / "output" / "review" / "food-line" / "backfill" / "2026-06-21_to_2026-06-22" / "backfill_summary.html"

    assert result["ok"] is True
    assert candidate_path.exists()
    assert review_json_path.exists()
    assert review_html_path.exists()
    assert summary_json_path.exists()
    assert summary_html_path.exists()
    review_payload = json.loads(review_json_path.read_text(encoding="utf-8"))
    summary_payload = json.loads(summary_json_path.read_text(encoding="utf-8"))
    assert review_payload["candidate_count_total"] == 1
    assert review_payload["candidate_count_traceable"] == 1
    assert summary_payload["candidates_by_date"]["2026-06-21"] == 1
    assert summary_payload["candidates_by_date"]["2026-06-22"] == 0
    assert "2026-06-22" in summary_payload["dates_with_no_reviewable_candidates"]
    assert "2026-06-22" in summary_payload["dates_with_no_public_eligible_candidates"]
    assert summary_payload["public_output_written"] is False
    assert summary_payload["pages_repo_mutated"] is False
    assert not (tmp_path / "output" / "site").exists()
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_food_line_discovery_backfill_summary_reports_watchlist_and_lane_counts(tmp_path: Path):
    call_count = {"rss": 0}

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            call_count["rss"] += 1
            if call_count["rss"] > 1:
                return _rss_payload([])
            return _rss_payload(
                [
                    {
                        "title": "Pantry demand rising",
                        "link": "https://news.google.com/rss/articles/CBMiPANTRY?oc=5",
                        "source_url": "https://example.com/pantry-demand",
                        "publisher": "Example News",
                        "description": "Food pantry demand is rising and more families need help.",
                    },
                    {
                        "title": "Social post says pantry lines are long",
                        "link": "https://x.com/example/status/1",
                        "source_url": "https://x.com/example/status/1",
                        "publisher": "Example Social",
                        "description": "Snippet only social post about long pantry lines.",
                    },
                ]
            )
        if url == "https://example.com/pantry-demand":
            return b"""<html><head><title>Pantry demand</title><link rel=\"canonical\" href=\"https://example.com/pantry-demand\"></head><body><p>Food pantry demand is rising and more families need help.</p></body></html>"""
        if url == "https://x.com/example/status/1":
            raise TimeoutError("blocked social fetch")
        raise AssertionError(f"unexpected fetch url: {url}")

    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-21",
            "2026-06-21",
            max_queries=1,
            max_results_per_query=10,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-21_to_2026-06-21"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert summary["watchlist_candidates_by_date"]["2026-06-21"] >= 1
    assert summary["discovery_lanes_used"]["news_article"] >= 1
    assert summary["discovery_lanes_used"]["social_watchlist"] >= 1
    assert summary["likely_qualifying_candidates_by_date"]["2026-06-21"] >= 1


def test_food_line_discovery_backfill_summary_reports_window_and_homepage_blockers(tmp_path: Path):
    call_count = {"rss": 0}

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            call_count["rss"] += 1
            if call_count["rss"] > 1:
                return _rss_payload([])
            return _rss_payload(
                [
                    {
                        "title": "Out of window demand story",
                        "link": "https://news.google.com/rss/articles/CBMiLATE?oc=5",
                        "source_url": "https://example.com/late-story",
                        "publisher": "Example News",
                        "description": "Food bank demand is rising.",
                        "pubDate": "Thu, 26 Jun 2026 12:00:00 GMT",
                    },
                    {
                        "title": "Homepage only trace",
                        "link": "https://news.google.com/rss/articles/CBMiHOME?oc=5",
                        "source_url": "https://www.kxan.com",
                        "publisher": "KXAN",
                        "description": "Food pantry demand rises.",
                        "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
                    },
                ]
            )
        if url == "https://news.google.com/rss/articles/CBMiLATE?oc=5":
            return b"<html><body><a href=\"https://example.com/late-story\">story</a></body></html>"
        if url == "https://news.google.com/rss/articles/CBMiHOME?oc=5":
            return b"<html><body><a href=\"https://www.kxan.com\">home</a></body></html>"
        if url == "https://example.com/late-story":
            return b"""<html><head><title>Late story</title><link rel=\"canonical\" href=\"https://example.com/late-story\"></head><body><p>Food bank demand is rising.</p></body></html>"""
        if url == "https://www.kxan.com":
            return b"""<html><head><title>KXAN</title><link rel=\"canonical\" href=\"https://www.kxan.com\"></head><body><p>Food pantry demand rises.</p></body></html>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-21",
            "2026-06-21",
            max_queries=1,
            max_results_per_query=10,
            query_lookback_days=0,
            query_lookahead_days=0,
            public_claim_lookback_days=0,
            public_claim_lookahead_days=0,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-21_to_2026-06-21"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )
    review = json.loads(
        (tmp_path / "output" / "review" / "food-line" / "2026-06-21" / "candidate_review.json").read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert summary["top_blocker_reasons"]["outside_backfill_date_window"] >= 1
    assert summary["top_blocker_reasons"]["publisher_homepage_trace_only"] >= 1
    assert summary["google_news_url_count"] == 2
    assert summary["google_news_resolution_attempt_count"] == 1
    assert summary["google_news_resolution_success_count"] == 0
    assert summary["google_news_resolution_failure_count"] == 1
    assert summary["google_news_resolved_article_url_count"] == 0
    assert summary["google_news_resolved_homepage_only_count"] == 1
    assert summary["google_news_resolution_status_counts"]["success_homepage_only"] == 1
    assert summary["publisher_homepage_trace_only_count"] == 1
    assert summary["unresolved_google_news_count"] == 0
    assert summary["public_eligible_candidate_count"] == 0
    assert "2026-06-21" in summary["dates_with_no_public_eligible_candidates"]
    assert review["top_blocker_reasons"]["outside_backfill_date_window"] >= 1
    assert review["top_blocker_reasons"]["publisher_homepage_trace_only"] >= 1
    assert review["candidates"][1]["google_news_resolution"]["google_news_resolution_status"] == "success_homepage_only"
    assert summary["public_output_written"] is False
    assert summary["pages_repo_mutated"] is False
    assert not (tmp_path / "output" / "site").exists()
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_food_line_discovery_backfill_samples_multiple_lanes_under_query_cap(tmp_path: Path):
    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            calls.append(url)
            return _rss_payload([])
        raise AssertionError(f"unexpected fetch url: {url}")

    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-24",
            "2026-06-24",
            max_queries=8,
            max_results_per_query=1,
            query_lookback_days=0,
            query_lookahead_days=0,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-24_to_2026-06-24"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert len(calls) == 8
    assert all("after%3A2026-06-24" in url and "before%3A2026-06-24" in url for url in calls)
    assert "news_article" in summary["executed_lanes"]
    assert "public_radio" in summary["executed_lanes"]
    assert "food_bank_provider" in summary["executed_lanes"]
    assert "county_city_agenda" in summary["executed_lanes"]
    assert summary["skipped_lanes"]
    assert not (tmp_path / "output" / "site").exists()
    assert not (tmp_path / "bluefern-dispatches-pages").exists()


def test_food_line_discovery_backfill_resume_uses_existing_partial_summary(tmp_path: Path):
    existing_candidates = [
        {
            "candidate_id": "existing-one",
            "discovered_title": "Existing demand story",
            "discovered_publisher": "Example News",
            "source_url": "https://example.com/story",
            "original_source_url": "https://example.com/story",
            "final_trace_url": "https://example.com/story",
            "source_published_date": "2026-06-21",
            "traceability_status": "traceable",
            "candidate_review_status": "needs_review",
            "public_claim_eligible": True,
            "classification_status": "qualified_pressure_signal",
            "discovery_lane": "news_article",
            "public_claim_blockers": [],
        }
    ]
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "candidates" / "2026-06-21" / "candidate_sources.json"
    review_path = tmp_path / "output" / "review" / "food-line" / "2026-06-21" / "candidate_review.json"
    audit_path = tmp_path / "output" / "review" / "food-line" / "2026-06-21" / "discovery_audit.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(json.dumps(existing_candidates, indent=2), encoding="utf-8")
    review_path.write_text(json.dumps({"candidate_count_total": 1}, indent=2), encoding="utf-8")
    audit_path.write_text(
        json.dumps(
            {
                "configured_lanes": ["news_article", "public_radio"],
                "executed_lanes": ["news_article"],
                "skipped_lanes": ["public_radio"],
                "candidates_by_lane": {"news_article": 1},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = backfill.run_food_line_discovery_backfill(
        tmp_path,
        "2026-06-21",
        "2026-06-21",
        resume=True,
        dry_run=False,
    )

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-21_to_2026-06-21"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert summary["per_date"][0]["status"] == "resumed_existing"
    assert summary["candidates_by_date"]["2026-06-21"] == 1
    assert summary["executed_lanes"] == ["news_article"]
    assert summary["skipped_lanes"] == ["public_radio"]
    assert summary["public_output_written"] is False
    assert summary["pages_repo_mutated"] is False


def test_food_line_discovery_backfill_records_direct_source_fetch_failure_without_failing_run(tmp_path: Path):
    feed_url = "https://example.org/feed.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Broken Direct Feed",
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
            raise TimeoutError("feed timeout")
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload([])
        raise AssertionError(url)

    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-24",
            "2026-06-24",
            max_queries=1,
            max_results_per_query=5,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-24_to_2026-06-24"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert summary["direct_source_fetch_attempt_count"] == 1
    assert summary["direct_source_fetch_failure_count"] == 1
    assert summary["google_news_fallback_count"] == 0
    assert summary["pages_repo_mutated"] is False


def test_food_line_discovery_backfill_samples_direct_source_before_google_fallback(tmp_path: Path):
    feed_url = "https://example.org/feed.xml"
    _write_direct_source_config(
        tmp_path,
        [
            {
                "source_name": "Priority Direct Feed",
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
    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        calls.append(url)
        if url == feed_url:
            return _rss_payload([])
        if url.startswith("https://news.google.com/rss/search?q="):
            raise AssertionError("google fallback should not be sampled first when direct cap is hit")
        raise AssertionError(url)

    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-24",
            "2026-06-24",
            max_queries=1,
            max_results_per_query=5,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-24_to_2026-06-24"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert calls == [feed_url]
    assert summary["direct_source_fetch_attempt_count"] == 1
    assert summary["google_news_fallback_count"] == 0


def test_backfill_reports_direct_source_lane_caps_and_out_of_window_context_dates(tmp_path: Path):
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
                "source_name": "Agenda Source",
                "source_family": "county_city_agenda",
                "discovery_lane": "county_city_agenda",
                "discovery_channel": "direct_page",
                "source_url": feed_two,
                "allowed_domains": ["example.org"],
                "geographic_scope": "state_local",
                "enabled": True,
                "sampling_priority": 500,
                "direct_source_candidate_cap": 1,
                "max_age_days": 7,
                "pressure_terms": ["food assistance"],
                "exclusion_terms": [],
            },
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == feed_one:
            return _rss_payload(
                [
                    {"title": "One", "link": "https://example.org/one", "source_url": "https://example.org/one", "publisher": "Dominant Feed", "description": "Food pantry demand is rising.", "pubDate": "Thu, 26 Jun 2026 12:00:00 GMT"},
                    {"title": "Two", "link": "https://example.org/two", "source_url": "https://example.org/two", "publisher": "Dominant Feed", "description": "Food pantry demand is rising.", "pubDate": "Thu, 26 Jun 2026 12:05:00 GMT"},
                ]
            )
        if url == "https://example.org/one":
            return b"<html><head><link rel=\"canonical\" href=\"https://example.org/one\"></head><body>background context without pressure</body></html>"
        if url == feed_two:
            return b"<html><head><title>Agenda</title><link rel=\"canonical\" href=\"https://example.org/document\"></head><body>background context without pressure</body></html>"
        if url == "https://example.org/document":
            return b"<html><head><link rel=\"canonical\" href=\"https://example.org/document\"></head><body>background context without pressure</body></html>"
        raise AssertionError(url)

    original = backfill.run_food_line_discovery_expansion
    try:
        from bluefern_dispatches import food_line_discovery_expansion as expansion_module

        def patched_run(root, edition_date, **kwargs):
            return expansion_module.run_food_line_discovery_expansion(root, edition_date, fetcher=fetcher, **kwargs)

        backfill.run_food_line_discovery_expansion = patched_run
        result = backfill.run_food_line_discovery_backfill(
            tmp_path,
            "2026-06-24",
            "2026-06-24",
            max_queries=2,
            max_results_per_query=5,
            query_lookback_days=0,
            query_lookahead_days=0,
            public_claim_lookback_days=0,
            public_claim_lookahead_days=0,
            dry_run=False,
        )
    finally:
        backfill.run_food_line_discovery_expansion = original

    summary = json.loads(
        (
            tmp_path
            / "output"
            / "review"
            / "food-line"
            / "backfill"
            / "2026-06-24_to_2026-06-24"
            / "backfill_summary.json"
        ).read_text(encoding="utf-8")
    )

    assert result["ok"] is True
    assert summary["candidates_by_direct_source_lane"]["Agenda Source | county_city_agenda"] == 1
    assert summary["candidates_by_direct_source_lane"]["Dominant Feed | food_bank_provider"] == 1
    assert summary["direct_source_candidate_cap_hits"]["Dominant Feed"] == 1
    assert "2026-06-24" in summary["dates_with_only_out_of_window_candidates"]
