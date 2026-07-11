from __future__ import annotations

import json
from pathlib import Path

import scripts.discover_food_line_sources as food_line_gap


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<rss version=\"2.0\"><channel>"]
    for item in items:
        parts.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Thu, 12 Jun 2026 21:58:00 GMT')}</pubDate>"
            f"<description>{item.get('description', '')}</description>"
            f"<source url=\"{item['source_url']}\">{item['publisher']}</source>"
            "</item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _gap_item(
    *,
    title: str,
    publisher: str,
    source_url: str,
    description: str,
    link: str | None = None,
) -> dict[str, str]:
    return {
        "title": title,
        "publisher": publisher,
        "source_url": source_url,
        "description": description,
        "link": link or source_url,
    }


class _FakeResponse:
    def __init__(self, *, final_url: str, body: str = ""):
        self._final_url = final_url
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self._final_url

    def read(self, size: int = -1):
        if size is None or size < 0:
            return self._body
        return self._body[:size]


def _fake_gap_resolve_google_news_url(
    url: str,
    *,
    title: str = "",
    summary: str = "",
    source_url: str = "",
    **kwargs,
) -> tuple[str, str, str]:
    normalized = food_line_gap._gap_normalize_url(url)
    if normalized == "https://news.google.com/rss/articles/CBMiWPDE?oc=5":
        return (
            "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
            "resolved_google_news_url",
            "",
        )
    if normalized == "https://news.google.com/rss/articles/CBMiTULSA?oc=5":
        return (
            "https://tulsaflyer.org/2026/06/12/your-money/post/food-bank-fuel-costs",
            "resolved_google_news_url",
            "",
        )
    if normalized == "https://news.google.com/rss/articles/CBMiKTAL?oc=5":
        return (
            "",
            "rejected_unrelated_resolved_url",
            "resolved URL terms do not match title terms",
        )
    if normalized == "https://news.google.com/rss/articles/CBMiAOL?oc=5":
        return (
            "",
            "rejected_unrelated_resolved_url",
            "resolved URL terms do not match title terms",
        )
    if normalized == "https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5":
        return (
            "",
            "unresolved_google_news_url",
            "no acceptable article URL found",
        )
    if normalized.startswith("http://") or normalized.startswith("https://"):
        if "news.google.com" in normalized:
            return "", "unresolved_google_news_url", "no acceptable article URL found"
        return normalized, "direct_article_url", ""
    return "", "empty_url", "empty url"


def test_food_line_discovery_gap_queries_load_from_config():
    config = food_line_gap.load_food_line_discovery_gap_queries(Path(__file__).parent.parent)
    assert config["queries"][:4] == [
        "food insecurity",
        "food bank demand",
        "food pantry demand",
        "food bank shelves",
    ]
    assert "facebook.com" in config["exclude_domains"]
    assert "youtube.com" in config["exclude_domains"]


def test_food_line_discovery_gap_adds_date_bounded_google_news_queries():
    rows = food_line_gap._date_bounded_queries("2026-06-12")
    assert len(rows) >= 3
    assert rows[0]["query"].endswith("after:2026-06-11 before:2026-06-13")
    assert all("{after}" in row["query_template"] and "{before}" in row["query_template"] for row in rows)
    assert all(row["category"] == "date_bounded" for row in rows)


def test_food_line_discovery_gap_url_normalization_detects_duplicates():
    one = food_line_gap._gap_normalize_url("https://example.com/story/?utm_source=rss&utm_medium=email")
    two = food_line_gap._gap_normalize_url("https://example.com/story")
    assert one == two


def test_food_line_discovery_gap_url_resolution_preserves_article_paths(monkeypatch):
    google_link = "https://news.google.com/rss/articles/CBMiWPDE?oc=5"
    final_url = "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=final_url)
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    item = _gap_item(
        title="WPDE reports higher food insecurity in Horry County",
        publisher="WPDE / ABC 15",
        source_url="https://wpde.com",
        link=google_link,
        description="Food insecurity is rising and some mobile distributions served 185 families.",
    )
    rows = food_line_gap._gap_parse_rss_items(_rss_payload([item]))
    assert rows[0]["candidate_url"] == final_url
    assert rows[0]["resolved_url"] == final_url
    assert rows[0]["url_resolution_status"] == "resolved_google_news_url"
    assert rows[0]["url_resolution_reason"] == ""
    assert rows[0]["google_news_url"] == google_link
    assert rows[0]["publisher_url"] == "https://wpde.com"
    assert rows[0]["domain"] == "wpde.com"
    assert rows[0]["candidate_url"] != "https://wpde.com"


def test_food_line_discovery_gap_unresolved_google_news_url_is_preserved(monkeypatch):
    google_link = "https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5"

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=google_link, body="<html><body>No canonical article link here.</body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    item = _gap_item(
        title="Unresolved Google News item",
        publisher="Example News",
        source_url="https://example.com",
        link=google_link,
        description="General update.",
    )
    rows = food_line_gap._gap_parse_rss_items(_rss_payload([item]))
    assert rows[0]["candidate_url"] == google_link
    assert rows[0]["resolved_url"] == ""
    assert rows[0]["url_resolution_status"] == "unresolved_google_news_url"
    assert rows[0]["url_resolution_reason"] == "no acceptable article URL found"
    assert rows[0]["google_news_url"] == google_link


def test_food_line_discovery_gap_publisher_sitemap_fallback_finds_article_url(monkeypatch):
    google_link = "https://news.google.com/rss/articles/CBMiWPDE?oc=5"
    source_url = "https://wpde.com"
    final_url = "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    sitemap_xml = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
      <url><loc>{final_url}</loc></url>
      <url><loc>https://wpde.com/archive</loc></url>
    </urlset>
    """

    monkeypatch.setattr(
        food_line_gap,
        "_gap_collect_sitemap_urls",
        lambda origin, **kwargs: (final_url, "https://wpde.com/archive"),
    )
    monkeypatch.setattr(
        food_line_gap.urllib.request,
        "urlopen",
        lambda req, timeout=15, context=None: _FakeResponse(final_url=google_link, body=""),
    )
    resolved, status, reason = food_line_gap._gap_resolve_google_news_url(
        google_link,
        title="New data show food insecurity higher than during COVID-19 with Horry County at 14%",
        summary="Lowcountry Food Bank says demand is rising and 185 families were served.",
        source_url=source_url,
    )
    assert resolved == final_url
    assert status == "resolved_publisher_sitemap_url"
    assert reason == ""


def test_food_line_discovery_gap_rejects_unrelated_ktal_canonical_url(monkeypatch):
    google_link = "https://news.google.com/rss/articles/CBMiKTAL?oc=5"
    unrelated_url = "https://www.ktalnews.com/news/u-s-world/rising-travel-demand-pushing-us-hotel-rates-to-new-highs-expert-says"

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=unrelated_url)
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", lambda origin, **kwargs: ())
    resolved, status, reason = food_line_gap._gap_resolve_google_news_url(
        google_link,
        title="Food bank struggles to meet rising demand amid low inventory",
        summary="The pantry is serving more households and lines are long.",
        source_url="https://ktalnews.com",
    )
    assert resolved == ""
    assert status == "rejected_unrelated_resolved_url"
    assert "resolved URL slug does not preserve enough candidate terms" in reason


def test_food_line_discovery_gap_rejects_unrelated_aol_canonical_url(monkeypatch):
    google_link = "https://news.google.com/rss/articles/CBMiAOL?oc=5"
    unrelated_url = "https://www.aol.com/2013/07/10/hubzu-expands-sales-team-to-meet-rising-demand-for"

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=unrelated_url)
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", lambda origin, **kwargs: ())
    resolved, status, reason = food_line_gap._gap_resolve_google_news_url(
        google_link,
        title="Food bank struggles to meet rising demand amid low inventory",
        summary="The pantry is serving more households and lines are long.",
        source_url="https://aol.com",
    )
    assert resolved == ""
    assert status == "rejected_unrelated_resolved_url"
    assert "resolved URL slug does not preserve enough candidate terms" in reason


def test_food_line_discovery_gap_resolver_timeout_returns_unresolved_status(monkeypatch):
    google_link = "https://news.google.com/rss/articles/CBMiTIMEOUT?oc=5"

    def fake_urlopen(req, timeout=15, context=None):
        raise TimeoutError("resolver timed out")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    resolved, status, reason = food_line_gap._gap_resolve_google_news_url(
        google_link,
        title="Food bank struggles to meet rising demand amid low inventory",
        summary="The pantry is serving more households and lines are long.",
        source_url="https://ktalnews.com",
    )
    assert resolved == ""
    assert status == "url_resolution_timeout"
    assert "timed out" in reason.lower()


def test_food_line_discovery_gap_max_candidates_limits_resolution_work(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food insecurity"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )

    items = [
        _gap_item(
            title="New data show food insecurity higher than during COVID-19 with Horry County at 14%",
            publisher="WPDE / ABC 15",
            source_url="https://wpde.com",
            link="https://news.google.com/rss/articles/CBMiWPDE?oc=5",
            description="Lowcountry Food Bank says demand is rising and some distributions served 185 families.",
        ),
        _gap_item(
            title="Tulsa food bank fuel costs force more difficult meal delivery",
            publisher="Tulsa Flyer",
            source_url="https://tulsaflyer.org",
            link="https://news.google.com/rss/articles/CBMiTULSA?oc=5",
            description="Diesel costs are taking away meals across 24 counties.",
        ),
    ]
    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_resolver(url: str, **kwargs):
        calls.append(url)
        return _fake_gap_resolve_google_news_url(url, **kwargs)

    monkeypatch.setattr(food_line_gap, "_gap_resolve_google_news_url", fake_resolver)
    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-06-12",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=1,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}
    assert len(calls) == 1
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["url_resolution_status"] == "resolved_google_news_url"
    assert rows["Tulsa food bank fuel costs force more difficult meal delivery"]["url_resolution_status"] == "resolution_skipped_max_candidates"
    assert rows["Tulsa food bank fuel costs force more difficult meal delivery"]["resolved_url"] == ""
    assert report["resolved_url_count"] == 1
    assert report["unresolved_url_count"] >= 1


def test_food_line_discovery_gap_reserves_soft_block_exact_title_resolution_even_when_general_budget_is_exhausted(
    tmp_path: Path, monkeypatch
):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food bank demand"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )

    exact_title_url = "https://www.ksbw.com/news/local/2026/07/09/food-bank-demand-surges-in-santa-cruz-county-as-costs-strain-families/"
    high_priority_google_link = "https://news.google.com/rss/articles/CBMiKSBW?oc=5"
    lower_priority_google_link = "https://news.google.com/rss/articles/CBMiLOWER?oc=5"
    wrapper_google_link = "https://news.google.com/rss/articles/CBMiWRAP?oc=5"

    items = [
        _gap_item(
            title="Food bank demand surges in Santa Cruz County as costs strain families and children - KSBW",
            publisher="KSBW",
            source_url="https://www.ksbw.com",
            link=high_priority_google_link,
            description="Children and households are seeing higher demand, low inventory, and rising fuel costs.",
        ),
        _gap_item(
            title="Food bank sees rising demand amid low inventory",
            publisher="Example News",
            source_url="https://example.com",
            link=lower_priority_google_link,
            description="Need is rising and the pantry says inventory is low.",
        ),
        _gap_item(
            title="Food pantry fundraiser helps families",
            publisher="Wrapper News",
            source_url="https://wrapper.example",
            link=wrapper_google_link,
            description="A donation page encourages readers to support the pantry.",
        ),
    ]

    exact_title_calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url in {high_priority_google_link, lower_priority_google_link, wrapper_google_link}:
            return _FakeResponse(final_url=url, body="<html><body></body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    def fake_collect_sitemap_urls(origin: str, **kwargs):
        if origin == "https://www.ksbw.com":
            return (exact_title_url,)
        return ()

    def fake_fetch_url_text(url: str, *, timeout_seconds: int = 20):
        if url == exact_title_url:
            exact_title_calls.append(url)
            return (
                "<html><head>"
                "<title>Food bank demand surges in Santa Cruz County as costs strain families and children - KSBW</title>"
                '<meta property="article:published_time" content="2026-07-09T12:00:00Z">'
                "</head><body></body></html>"
            )
        return ""

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", fake_collect_sitemap_urls)
    monkeypatch.setattr(food_line_gap, "_gap_fetch_url_text", fake_fetch_url_text)

    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-07-09",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=1,
        max_results_per_query=5,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    high_priority_row = next(row for row in report["candidates"] if row["title"].startswith("Food bank demand surges in Santa Cruz County as costs strain families"))
    low_priority_row = next(row for row in report["candidates"] if row["title"].startswith("Food bank sees rising demand amid low inventory"))
    wrapper_row = next(row for row in report["candidates"] if row["title"].startswith("Food pantry fundraiser helps families"))

    assert report["general_resolution_attempt_count"] == 0
    assert report["reserved_soft_block_budget"] == 1
    assert report["reserved_soft_block_resolution_attempt_count"] == 1
    assert report["reserved_soft_block_exact_title_attempt_count"] == 1
    assert report["reserved_soft_block_resolution_skipped_count"] >= 1
    assert exact_title_calls == [exact_title_url]
    assert high_priority_row["resolved_url"] == exact_title_url
    assert high_priority_row["url_resolution_status"] == "resolved_publisher_exact_title_url"
    assert high_priority_row["blocking_candidate_severity"] == "hard_block"
    assert high_priority_row["direct_url_date_verified"] is True
    assert low_priority_row["url_resolution_status"] == "resolution_skipped_max_candidates"
    assert wrapper_row["url_resolution_status"] == "resolution_skipped_max_candidates"
    assert wrapper_row["resolved_url"] == ""
    assert all("news.google.com" not in str(row.get("direct_url") or "") for row in report["candidates"])


def test_food_line_discovery_gap_reserves_full_high_priority_soft_block_pool_and_writes_unresolved_review_artifact(
    tmp_path: Path, monkeypatch
):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food bank demand"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )

    items: list[dict[str, str]] = []
    exact_title_urls: dict[str, str] = {}
    exact_title_calls: list[str] = []
    for index in range(12):
        source_url = f"https://site{index}.example"
        exact_title_url = (
            f"{source_url}/2026/07/09/food-bank-demand-surges-in-city-{index}-as-families-face-rising-need/"
        )
        exact_title_urls[source_url] = exact_title_url
        items.append(
            _gap_item(
                title=f"Food bank demand surges in city {index} as families face rising need - Site {index}",
                publisher=f"Site {index}",
                source_url=source_url,
                link=f"https://news.google.com/rss/articles/CBMiRESERVE{index:02d}?oc=5",
                description="Children and families are seeing rising demand, low inventory, and food insecurity is rising.",
            )
        )
    wrapper_google_link = "https://news.google.com/rss/articles/CBMiWRAPPER?oc=5"
    items.append(
        _gap_item(
            title="Food pantry fundraiser helps families",
            publisher="Wrapper News",
            source_url="https://wrapper.example",
            link=wrapper_google_link,
            description="A donation page encourages readers to support the pantry.",
        )
    )

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url.startswith("https://news.google.com/rss/articles/"):
            return _FakeResponse(final_url=url, body="<html><body></body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    def fake_collect_sitemap_urls(origin: str, **kwargs):
        exact_title_url = exact_title_urls.get(origin.rstrip("/"))
        if exact_title_url:
            return (exact_title_url,)
        return ()

    def fake_fetch_url_text(url: str, *, timeout_seconds: int = 20):
        if url in exact_title_urls.values():
            exact_title_calls.append(url)
            index = url.split("site", 1)[1].split(".example", 1)[0]
            return (
                "<html><head>"
                f"<title>Food bank demand surges in city {index} as families face rising need - Site {index}</title>"
                '<meta property="article:published_time" content="2026-07-09T12:00:00Z">'
                "</head><body></body></html>"
            )
        return ""

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", fake_collect_sitemap_urls)
    monkeypatch.setattr(food_line_gap, "_gap_fetch_url_text", fake_fetch_url_text)

    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-07-09",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=12,
        max_results_per_query=20,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    review_path = Path(result["unresolved_review_path"])
    review_markdown_path = Path(result["unresolved_review_markdown_path"])
    high_priority_rows = [
        row
        for row in report["candidates"]
        if row["title"].startswith("Food bank demand surges in city ") and "Site " in row["title"]
    ]
    wrapper_row = next(row for row in report["candidates"] if row["title"] == "Food pantry fundraiser helps families")

    assert report["reserved_soft_block_budget"] == 12
    assert report["general_resolution_attempt_count"] == 0
    assert report["reserved_soft_block_resolution_attempt_count"] == 12
    assert report["reserved_soft_block_exact_title_attempt_count"] == 12
    assert len(exact_title_calls) == 12
    assert all(row["url_resolution_status"] == "resolved_publisher_exact_title_url" for row in high_priority_rows)
    assert wrapper_row["url_resolution_status"] == "resolution_skipped_max_candidates"
    assert wrapper_row["resolved_url"] == ""
    assert review_path.exists()
    assert review_markdown_path.exists()
    assert str(review_path).replace("\\", "/").endswith("/output/review/food-line/2026-07-09/unresolved_candidates_review.json")
    assert str(review_markdown_path).replace("\\", "/").endswith(
        "/output/review/food-line/2026-07-09/unresolved_candidates_review.md"
    )


def test_food_line_discovery_gap_prioritizes_high_confidence_candidates_and_reports_severity_for_20260709_case(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food bank demand"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )
    case_report = json.loads(
        Path("data/dispatches/food-line/discovery_gap/2026-07-09/discovery_gap_report.json").read_text(encoding="utf-8")
    )
    high_confidence_wrapper = next(
        row
        for row in case_report["candidates"]
        if row["title"].startswith("Food bank demand surges in Santa Cruz County as costs strain families")
    )
    lower_value_wrapper = next(
        row
        for row in case_report["candidates"]
        if row["title"].startswith("Food bank officials respond to rising needs following SNAP cuts")
    )
    calls: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            items = [
                _gap_item(
                    title=lower_value_wrapper["title"],
                    publisher=lower_value_wrapper["publisher"],
                    source_url="https://" + lower_value_wrapper["publisher_domain"],
                    link=lower_value_wrapper["google_news_url"],
                    description=lower_value_wrapper["summary_or_snippet"],
                ),
                _gap_item(
                    title=high_confidence_wrapper["title"],
                    publisher=high_confidence_wrapper["publisher"],
                    source_url="https://" + high_confidence_wrapper["publisher_domain"],
                    link=high_confidence_wrapper["google_news_url"],
                    description=high_confidence_wrapper["summary_or_snippet"],
                ),
            ]
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    exact_title_url = "https://www.ksbw.com/news/local/2026/07/09/food-bank-demand-surges-in-santa-cruz-county-as-costs-strain-families/"
    def fake_collect_sitemap_urls(origin: str, **kwargs):
        if origin == "https://www.ksbw.com":
            return (exact_title_url,)
        return ()

    def fake_fetch_url_text(url: str, *, timeout_seconds: int = 20):
        if url == exact_title_url:
            return (
                "<html><head>"
                "<title>Food bank demand surges in Santa Cruz County as costs strain families - KSBW</title>"
                '<meta property="article:published_time" content="2026-07-09T12:00:00Z">'
                "</head><body></body></html>"
            )
        return ""

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url.startswith("https://news.google.com/rss/articles/"):
            calls.append(url)
            return _FakeResponse(final_url=url, body="<html><body></body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", fake_collect_sitemap_urls)
    monkeypatch.setattr(food_line_gap, "_gap_fetch_url_text", fake_fetch_url_text)
    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)

    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-07-09",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=1,
        max_results_per_query=4,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}

    assert calls[0] == high_confidence_wrapper["google_news_url"]
    assert report["resolved_url_count"] == 1
    assert report["unresolved_url_count"] >= 1
    assert rows[high_confidence_wrapper["title"]]["resolved_url"] == exact_title_url
    assert rows[high_confidence_wrapper["title"]]["url"] == food_line_gap._gap_normalize_url(exact_title_url)
    assert rows[high_confidence_wrapper["title"]]["google_news_url"] == high_confidence_wrapper["google_news_url"]
    assert rows[high_confidence_wrapper["title"]]["blocking_candidate_severity"] == "hard_block"
    assert rows[high_confidence_wrapper["title"]]["direct_url_date_verified"] is True
    assert rows[high_confidence_wrapper["title"]]["date_confidence"] >= 90
    assert rows[lower_value_wrapper["title"]]["url_resolution_status"] == "resolution_skipped_max_candidates"
    assert rows[lower_value_wrapper["title"]]["blocking_candidate_severity"] == "soft_block"
    assert rows[lower_value_wrapper["title"]]["direct_url"] == ""
    assert all("news.google.com" not in str(row.get("direct_url") or "") for row in report["candidates"])


def test_food_line_discovery_gap_rejects_false_exact_title_fallback_for_unrelated_same_domain_url(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food bank demand"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )

    bad_url = "https://www.3newsnow.com/news/local-news/gallery-gross-medical-photos-topic-of-omaha-science-cafe-tuesday"
    google_link = "https://news.google.com/rss/articles/CBMiKMTV?oc=5"
    item = _gap_item(
        title="Food bank demand surges in north Omaha as SNAP cuts and rising grocery costs strain families - KMTV 3 News Now",
        publisher="KMTV 3 News Now",
        source_url="https://www.3newsnow.com",
        link=google_link,
        description="Food bank demand surges and SNAP cuts are straining families as grocery costs rise.",
    )

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload([item])
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=google_link, body="<html><body></body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    def fake_collect_sitemap_urls(origin: str, **kwargs):
        if origin == "https://www.3newsnow.com":
            return (bad_url,)
        return ()

    def fake_fetch_url_text(url: str, *, timeout_seconds: int = 20):
        if url == bad_url:
            return (
                "<html><head>"
                "<title>Gallery gross medical photos topic of Omaha Science Cafe Tuesday</title>"
                '<meta property="article:published_time" content="2026-06-10T12:00:00Z">'
                "</head><body><p>Gallery coverage from the Omaha Science Cafe.</p></body></html>"
            )
        return ""

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", fake_collect_sitemap_urls)
    monkeypatch.setattr(food_line_gap, "_gap_fetch_url_text", fake_fetch_url_text)

    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-07-09",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=1,
        max_results_per_query=4,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    row = report["candidates"][0]

    assert row["title"] == item["title"]
    assert row["resolved_url"] == ""
    assert row["direct_url"] == ""
    assert row["direct_url_date_verified"] is False
    assert row["blocking_candidate_severity"] in {"soft_block", "review_only"}
    assert row["blocking_candidate_severity"] != "hard_block"
    assert row["url_resolution_status"] in {"unresolved_google_news_url", "rejected_unrelated_resolved_url"}
    assert report["hard_block_count"] == 0
    assert report["direct_url_date_verified_count"] == 0
    assert report["resolved_url_count"] == 0
    assert report["unresolved_url_count"] >= 1


def test_food_line_discovery_gap_marks_same_day_direct_pressure_hard_block_and_stale_direct_pressure_soft_block(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food bank demand"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )
    same_day_direct_url = "https://www.news10.com/news/local-news/2026/07/09/food-bank-officials-respond-to-rising-needs-following-snap-cuts/"
    stale_direct_url = "https://www.news10.com/news/local-news/2026/06/01/food-bank-officials-respond-to-rising-needs-following-snap-cuts/"

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            items = [
                _gap_item(
                    title="Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (same day)",
                    publisher="NEWS10 ABC",
                    source_url=same_day_direct_url,
                    description="Food bank officials respond to rising needs following SNAP cuts and report clear pressure on families.",
                ),
                _gap_item(
                    title="Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (stale)",
                    publisher="NEWS10 ABC",
                    source_url=stale_direct_url,
                    description="Food bank officials respond to rising needs following SNAP cuts and report clear pressure on families.",
                ),
            ]
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_fetch_url_text(url: str, *, timeout_seconds: int = 20):
        if url == same_day_direct_url:
            return (
                "<html><head>"
                "<title>Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (same day)</title>"
                '<meta property="article:published_time" content="2026-07-09T10:00:00Z">'
                "</head><body></body></html>"
            )
        if url == stale_direct_url:
            return (
                "<html><head>"
                "<title>Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (stale)</title>"
                '<meta property="article:published_time" content="2026-06-01T10:00:00Z">'
                "</head><body></body></html>"
            )
        return ""

    monkeypatch.setattr(food_line_gap, "_gap_fetch_url_text", fake_fetch_url_text)
    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-07-09",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=2,
        max_results_per_query=2,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}

    assert rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (same day)"]["blocking_candidate_severity"] == "hard_block"
    assert rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (same day)"]["direct_url_date_verified"] is True
    assert rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (same day)"]["date_confidence"] >= 90
    assert rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (stale)"]["blocking_candidate_severity"] == "soft_block"
    assert rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (stale)"]["direct_url_date_verified"] is False
    assert rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (stale)"]["date_confidence"] < rows["Food bank officials respond to rising needs following SNAP cuts - NEWS10 ABC (same day)"]["date_confidence"]


def test_food_line_discovery_gap_sitemap_lookup_is_cached_per_domain(monkeypatch):
    state: dict[str, object] = {}
    calls: list[str] = []
    sitemap_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
    <urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
      <url><loc>https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14</loc></url>
    </urlset>
    """

    def fake_fetch(url: str, *, timeout_seconds: int = 20):
        calls.append(url)
        if url == "https://wpde.com/sitemap.xml":
            return sitemap_xml
        return ""

    monkeypatch.setattr(food_line_gap, "_gap_fetch_url_text", fake_fetch)
    first = food_line_gap._gap_collect_sitemap_urls(
        "https://wpde.com",
        resolver_state=state,
        timeout_seconds=5,
        max_sitemap_lookups_per_domain=2,
        max_sitemap_urls_per_domain=10,
    )
    second = food_line_gap._gap_collect_sitemap_urls(
        "https://wpde.com",
        resolver_state=state,
        timeout_seconds=5,
        max_sitemap_lookups_per_domain=2,
        max_sitemap_urls_per_domain=10,
    )
    assert first == second
    assert state["sitemap_lookup_count"] == 1
    assert state["sitemap_cache_hit_count"] == 1
    assert calls.count("https://wpde.com/sitemap.xml") == 1


def test_food_line_discovery_gap_report_writes_when_some_urls_timeout(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food insecurity"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )
    items = [
        _gap_item(
            title="New data show food insecurity higher than during COVID-19 with Horry County at 14%",
            publisher="WPDE / ABC 15",
            source_url="https://wpde.com",
            link="https://news.google.com/rss/articles/CBMiWPDE?oc=5",
            description="Lowcountry Food Bank says demand is rising and some distributions served 185 families.",
        ),
        _gap_item(
            title="Food bank struggles to meet rising demand amid low inventory",
            publisher="KTALnews.com",
            source_url="https://www.ktalnews.com",
            link="https://news.google.com/rss/articles/CBMiKTAL?oc=5",
            description="The pantry is serving more households and lines are long.",
        ),
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == "https://news.google.com/rss/articles/CBMiWPDE?oc=5":
            return _FakeResponse(final_url="https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14")
        if url == "https://news.google.com/rss/articles/CBMiKTAL?oc=5":
            raise TimeoutError("resolver timed out")
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-06-12",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=10,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}
    assert report["url_resolution_timeout_count"] >= 1
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["url_resolution_status"] == "url_resolution_timeout"
    assert report["candidate_count"] == 2


def test_food_line_discovery_gap_fast_mode_preserves_unresolved_urls(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food insecurity"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )
    items = [
        _gap_item(
            title="Food bank struggles to meet rising demand amid low inventory",
            publisher="KTALnews.com",
            source_url="https://www.ktalnews.com",
            link="https://news.google.com/rss/articles/CBMiKTAL?oc=5",
            description="The pantry is serving more households and lines are long.",
        )
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_urlopen(req, timeout=15, context=None):
        raise TimeoutError("resolver timed out")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-06-12",
        fetcher=fetcher,
        fast=True,
        max_queries=1,
        max_candidates=5,
        resolver_timeout_seconds=1,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    row = report["candidates"][0]
    assert result["ok"] is True
    assert row["url_resolution_status"] in {"url_resolution_timeout", "unresolved_google_news_url"}
    assert report["candidate_count"] == 1


def test_food_line_discovery_gap_reports_high_confidence_resolution_diagnostics_when_exact_title_fallback_fails(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food bank demand"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )
    google_link = "https://news.google.com/rss/articles/CBMiKSBKSBW?oc=5"
    items = [
        _gap_item(
            title="Food bank demand surges in Santa Cruz County as SNAP cuts strain families - KSBW",
            publisher="KSBW",
            source_url="https://www.ksbw.com",
            link=google_link,
            description="SNAP cuts and rising demand are putting families under pressure.",
        )
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=google_link, body="<html><body></body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", lambda origin, **kwargs: ())

    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-07-09",
        fetcher=fetcher,
        fast=True,
        max_queries=1,
        max_candidates=1,
        max_results_per_query=1,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    markdown = Path(result["report_markdown_path"]).read_text(encoding="utf-8")
    attempts = report["high_confidence_url_resolution_attempts"]
    row = report["candidates"][0]

    assert report["high_confidence_url_resolution_attempt_count"] == 1
    assert len(attempts) == 1
    assert attempts[0]["publisher_domain"] == "www.ksbw.com"
    assert attempts[0]["url_resolution_status"] == "unresolved_google_news_url"
    assert "High-confidence URL resolution attempts" in markdown
    assert "KSBW" in markdown
    assert result["ok"] is True


def test_food_line_discovery_gap_exact_title_failure_diagnostics_record_no_sitemap_url_cause(monkeypatch):
    state: dict[str, object] = {}
    google_link = "https://news.google.com/rss/articles/CBMiKSBKSBW?oc=5"

    def fake_urlopen(req, timeout=15, context=None):
        url = getattr(req, "full_url", str(req))
        if url == google_link:
            return _FakeResponse(final_url=google_link, body="<html><body></body></html>")
        raise AssertionError(f"unexpected urlopen call: {url}")

    monkeypatch.setattr(food_line_gap.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(food_line_gap, "_gap_collect_sitemap_urls", lambda origin, **kwargs: ())

    resolved, status, reason = food_line_gap._gap_resolve_google_news_url(
        google_link,
        title="Food bank demand surges in Santa Cruz County as SNAP cuts strain families - KSBW",
        summary="SNAP cuts and rising demand are putting families under pressure.",
        source_url="https://www.ksbw.com",
        resolver_state=state,
        skip_sitemap_fallback=True,
        allow_exact_title_fallback=True,
    )

    diagnostics = state["resolution_diagnostics"][food_line_gap._gap_normalize_url(google_link)]
    assert resolved == ""
    assert status == "unresolved_google_news_url"
    assert "exact title fallback failed" in reason
    assert diagnostics["exact_title_attempted"] is True
    assert diagnostics["exact_title_failure_cause"] == "no_sitemap_urls_found"
    assert diagnostics["source_url"] == "https://www.ksbw.com"


def test_food_line_discovery_gap_report_distinguishes_traceable_blocking_and_unresolved_likely_candidates(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "discovery_gap_queries.json").write_text(
        json.dumps({"queries": ["food insecurity"], "exclude_domains": []}, indent=2),
        encoding="utf-8",
    )
    items = [
        _gap_item(
            title="New data show food insecurity higher than during COVID-19 with Horry County at 14%",
            publisher="WPDE / ABC 15",
            source_url="https://wpde.com",
            link="https://news.google.com/rss/articles/CBMiWPDE?oc=5",
            description="Lowcountry Food Bank says demand is rising and some distributions served 185 families.",
        ),
        _gap_item(
            title="Food Bank of Eastern Oklahoma is grappling with record fuel costs as it races to feed kids this summer",
            publisher="Tulsa Flyer",
            source_url="https://tulsaflyer.org",
            link="https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5",
            description="Record fuel costs are taking away meals and deliveries are affected.",
        ),
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(food_line_gap, "_gap_resolve_google_news_url", _fake_gap_resolve_google_news_url)
    result = food_line_gap.run_food_line_discovery_gap_check(
        root,
        "2026-06-12",
        fetcher=fetcher,
        max_queries=1,
        max_candidates=10,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}

    assert report["likely_qualifying_count"] == 2
    assert report["blocking_likely_qualifying_count"] == 1
    assert report["unresolved_likely_qualifying_count"] == 1
    assert report["manual_review_only_count"] == 0
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["publication_blocking_candidate"] is True
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["traceable_review_url"] == "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    assert rows["Food Bank of Eastern Oklahoma is grappling with record fuel costs as it races to feed kids this summer"]["publication_blocking_candidate"] is False
    assert rows["Food Bank of Eastern Oklahoma is grappling with record fuel costs as it races to feed kids this summer"]["review_traceability_status"] == "unresolved_google_news"
    assert rows["Food Bank of Eastern Oklahoma is grappling with record fuel costs as it races to feed kids this summer"]["traceable_review_url"] == ""


def test_food_line_discovery_gap_scoring_classifies_pressure_and_resource_items():
    wpde = {
        "title": "New data show food insecurity higher than during COVID-19 with Horry County at 14%",
        "summary_or_snippet": "Lowcountry Food Bank says demand is rising, families with children are affected, and some mobile distributions that normally served about 100 families had 185.",
        "publisher": "WPDE / ABC 15",
        "candidate_url": "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
    }
    tulsa = {
        "title": "Tulsa Flyer reports diesel costs are forcing food banks to cut meal delivery",
        "summary_or_snippet": "Fuel costs of $24,000-$26,000 and $12,000-$14,000 are taking away meals across 24 counties, with 750,000 meals at risk.",
        "publisher": "Tulsa Flyer",
        "candidate_url": "https://tulsaflyer.org/news/tulsa-food-bank-fuel-costs",
    }
    resource_only = {
        "title": "Summer meal schedule for families in June",
        "summary_or_snippet": "Find food distribution locations and hours for summer meals.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/summer-meals-locations",
    }
    food_drive = {
        "title": "Fill-A-Bus food drive to stock shelves at local food bank",
        "summary_or_snippet": "Donate canned goods and join the community campaign.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/fill-a-bus-food-drive",
    }
    wrapper_pressure = {
        "title": "Food drive helps families after pantry lines grow long",
        "summary_or_snippet": "A food drive is underway, but the pantry says more families rely on food pantries and lines are getting longer.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/food-drive-with-pressure",
    }
    empty_shelves = {
        "title": "Why Roanoke's St. Francis House is facing its tightest food shortage ever this summer",
        "summary_or_snippet": "St. Francis House had empty shelves in May, the June USDA delivery was smaller than May's, and the pantry is down 64% compared with January.",
        "publisher": "WSLS",
        "candidate_url": "https://www.wsls.com/news/local/2026/06/10/why-roanokes-st-francis-house-is-facing-its-tightest-food-shortage-ever-this-summer/",
    }
    fuel_costs = {
        "title": "Food Bank of Eastern Oklahoma is grappling with record fuel costs as it races to feed kids this summer",
        "summary_or_snippet": "Record fuel costs are taking away meals and deliveries are affected.",
        "publisher": "Tulsa Flyer",
        "candidate_url": "https://tulsaflyer.org/news/tulsa-food-bank-fuel-costs",
    }
    cant_keep_on_shelf = {
        "title": "KC food pantries can't keep food on the shelf as demand rises",
        "summary_or_snippet": "Pantries are running out of food and empty shelves are common.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/kc-food-pantries-demand",
    }
    record_visits = {
        "title": "Food bank sees record number of visits as summer demand rises",
        "summary_or_snippet": "The pantry is serving more households than usual and lines are long.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/record-number-of-visits",
    }
    fuel_strain = {
        "title": "Rising fuel prices put further strain on local food bank deliveries",
        "summary_or_snippet": "Delivery costs are higher and meals are being cut.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/fuel-strain",
    }
    fuel_hard = {
        "title": "Fuel prices are hitting the food bank hard",
        "summary_or_snippet": "The food bank says transportation costs are taking away meals.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/fuel-prices-hitting-hard",
    }
    federal_cuts = {
        "title": "Already reeling from federal cuts, the food bank faces another difficult week",
        "summary_or_snippet": "The pantry is under pressure from reduced support and higher costs.",
        "publisher": "Example News",
        "candidate_url": "https://example.com/federal-cuts-pressure",
    }
    wpde_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        wpde,
        known_status="known_domain_new_article",
        known_local_domain=True,
    )
    tulsa_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        tulsa,
        known_status="known_domain_new_article",
        known_local_domain=True,
    )
    resource_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        resource_only,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    food_drive_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        food_drive,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    wrapper_pressure_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        wrapper_pressure,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    empty_shelves_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        empty_shelves,
        known_status="known_domain_new_article",
        known_local_domain=True,
    )
    fuel_costs_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        fuel_costs,
        known_status="known_domain_new_article",
        known_local_domain=True,
    )
    cant_keep_on_shelf_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        cant_keep_on_shelf,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    record_visits_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        record_visits,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    fuel_strain_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        fuel_strain,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    fuel_hard_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        fuel_hard,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    federal_cuts_result = food_line_gap.classify_food_line_discovery_gap_candidate(
        federal_cuts,
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    assert wpde_result["classification"] == "likely_qualifying"
    assert tulsa_result["classification"] == "likely_qualifying"
    assert resource_result["classification"] == "likely_resource_only"
    assert food_drive_result["classification"] in {"likely_resource_only", "needs_review"}
    assert food_drive_result["classification"] != "likely_qualifying"
    assert food_drive_result["source_role"] == "discovery_lead"
    assert food_drive_result["donation_wrapper"] is True
    assert food_drive_result["public_eligible"] is False
    assert wrapper_pressure_result["classification"] == "needs_review"
    assert empty_shelves_result["classification"] == "likely_qualifying"
    assert fuel_costs_result["classification"] == "likely_qualifying"
    assert cant_keep_on_shelf_result["classification"] == "likely_qualifying"
    assert record_visits_result["classification"] != "likely_resource_only"
    assert fuel_strain_result["classification"] != "likely_resource_only"
    assert fuel_hard_result["classification"] != "likely_resource_only"
    assert federal_cuts_result["classification"] != "likely_resource_only"
    assert wpde_result["score"] > resource_result["score"]
    assert tulsa_result["score"] > resource_result["score"]
    assert empty_shelves_result["score"] >= 4
    assert fuel_costs_result["score"] >= 4
    assert cant_keep_on_shelf_result["score"] >= 4
    assert wrapper_pressure_result["classification"] == "needs_review"
    assert wrapper_pressure_result["score"] >= 4
    assert "wrapper lead detected: donation_page" in wrapper_pressure_result["reason"]
    assert wrapper_pressure_result["source_role"] == "discovery_lead"
    assert wrapper_pressure_result["donation_wrapper"] is True
    assert wrapper_pressure_result["public_eligible"] is False


def test_food_line_discovery_gap_wrapper_pages_generate_secondary_queries():
    wrapper = {
        "title": "Food drive helps families after pantry lines grow long",
        "summary_or_snippet": "A food drive is underway, but the pantry says more families rely on food pantries and the lines keep getting longer.",
        "publisher": "Lowcountry Food Bank",
        "source_name": "Lowcountry Food Bank",
        "location_name": "Horry County, SC",
        "county_name": "Horry County",
        "state": "SC",
        "candidate_url": "https://example.com/food-drive-with-pressure",
    }
    wrapper_kind = food_line_gap._gap_wrapper_kind(wrapper)
    secondary_queries = food_line_gap._gap_secondary_queries_from_wrapper(wrapper)
    result = food_line_gap.classify_food_line_discovery_gap_candidate(
        {
            **wrapper,
            "wrapper_kind": wrapper_kind,
            "secondary_queries_generated": secondary_queries,
        },
        known_status="unknown_domain_new_article",
        known_local_domain=False,
    )
    assert wrapper_kind == "donation_page"
    assert secondary_queries
    assert any("Lowcountry Food Bank" in query for query in secondary_queries)
    assert any("Horry County" in query or "SC" in query for query in secondary_queries)
    assert any(
        clue in query
        for query in secondary_queries
        for clue in ("food bank demand", "food insecurity", "more families rely on food pantries")
    )
    assert result["classification"] == "needs_review"
    assert "wrapper framing" in result["reason"]
    assert result["source_role"] == "discovery_lead"
    assert result["donation_wrapper"] is True
    assert result["public_eligible"] is False


def test_food_line_discovery_gap_candidate_fields_include_registry_metadata():
    row = food_line_gap._candidate_fields_from_discovery(
        discovered_url="https://example.com/story",
        source_name="Example Source",
        publisher="Example Source",
        source_family="local_news",
        source_type="rss",
        state="TX",
        location_name="Austin, TX",
        location_scope="state_local",
        reason="matched food bank pressure",
        pressure_terms=["food bank", "demand"],
        notes="discovered from Google News",
        source_purpose="current_news",
        current_or_evergreen="current",
        promotable=True,
        non_promotable_reason="",
        source_quality_score=8,
        source_quality_tier="high",
        auto_discovered=True,
        first_discovered_at="2026-06-12T00:00:00Z",
        last_discovered_at="2026-06-12T00:00:00Z",
        discovery_count=1,
        last_recommendation="enable",
        last_recommendation_reason="matched food pressure",
    )
    assert row["source_origin"] == "google_news_discovery"
    assert row["registry_status"] == "non_registry_discovered_source"
    assert row["source_seed_url"] == ""
    assert row["discovery_seed_url"] == ""


def test_food_line_discovery_gap_report_writes_json_and_markdown_and_skips_publish_and_bluesky(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    source_registry = [
        {
            "source_id": "wpde-grand-strand-local-news",
            "source_name": "WPDE / ABC 15",
            "publisher": "WPDE / ABC 15",
            "url": "https://wpde.com/news/local",
            "source_family": "local_news",
            "source_type": "page",
            "state": "SC",
            "location_name": "Horry County, SC",
            "location_scope": "local",
            "enabled": True,
        }
    ]
    candidate_registry = [
        {
            "source_id": "existing-excluded",
            "source_name": "Existing Excluded",
            "publisher": "Example News",
            "candidate_url": "https://example.com/existing-story/",
            "source_family": "local_news",
            "state": "TX",
            "location_name": "Austin, TX",
            "location_scope": "state_local",
            "status": "rejected",
            "notes": "Previously excluded.",
        }
    ]
    (data_dir / "source_registry.json").write_text(json.dumps(source_registry, indent=2), encoding="utf-8")
    (data_dir / "candidate_source_registry.json").write_text(json.dumps(candidate_registry, indent=2), encoding="utf-8")
    (data_dir / "discovery_gap_queries.json").write_text(
        Path("data/dispatches/food-line/discovery_gap_queries.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    items = [
        _gap_item(
            title="New data show food insecurity higher than during COVID-19 with Horry County at 14%",
            publisher="WPDE / ABC 15",
            source_url="https://wpde.com",
            link="https://news.google.com/rss/articles/CBMiWPDE?oc=5",
            description="Lowcountry Food Bank says demand is rising, about 20% of children are food insecure, and some distributions served 185 families.",
        ),
        _gap_item(
            title="Tulsa food bank fuel costs force more difficult meal delivery",
            publisher="Tulsa Flyer",
            source_url="https://tulsaflyer.org",
            link="https://news.google.com/rss/articles/CBMiTULSA?oc=5",
            description="Diesel costs of $24,000-$26,000 and $12,000-$14,000 are taking away meals across 24 counties; 750,000 meals are at risk.",
        ),
        _gap_item(
            title="Food bank struggles to meet rising demand amid low inventory",
            publisher="KTALnews.com",
            source_url="https://www.ktalnews.com",
            link="https://news.google.com/rss/articles/CBMiKTAL?oc=5",
            description="A community event brings more donations but the pantry remains short on food.",
        ),
        _gap_item(
            title="Food bank struggles to meet rising demand amid low inventory",
            publisher="AOL",
            source_url="https://www.aol.com",
            link="https://news.google.com/rss/articles/CBMiAOL?oc=5",
            description="A roundup mentions food bank demand and short inventory.",
        ),
        _gap_item(
            title="Unresolved Google News item",
            publisher="Example News",
            source_url="https://example.com",
            link="https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5",
            description="General update.",
        ),
        _gap_item(
            title="Summer meal schedule for families in June",
            publisher="Example News",
            source_url="https://example.com/summer-meals-locations/",
            description="Find food distribution locations and hours for summer meals.",
        ),
        _gap_item(
            title="Existing excluded story",
            publisher="Example News",
            source_url="https://example.com/existing-story/?utm_source=rss",
            description="General update.",
        ),
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(food_line_gap, "_gap_resolve_google_news_url", _fake_gap_resolve_google_news_url)

    result = food_line_gap.run_food_line_discovery_gap_check(root, "2026-06-12", fetcher=fetcher)
    report_path = Path(result["report_path"])
    report_md_path = Path(result["report_markdown_path"])
    assert report_path.exists()
    assert report_md_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}
    assert report["candidate_count"] == 9
    assert report["likely_qualifying_count"] == 5
    assert report["needs_review_count"] == 1
    assert report["likely_resource_only_count"] == 2
    assert report["duplicate_or_known_count"] == 1
    assert report["wrapper_candidate_count"] == 1
    assert report["secondary_query_count"] >= 1
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["google_news_url"] == "https://news.google.com/rss/articles/CBMiWPDE?oc=5"
    assert rows["Tulsa food bank fuel costs force more difficult meal delivery"]["google_news_url"] == "https://news.google.com/rss/articles/CBMiTULSA?oc=5"
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["url_resolution_status"] == "rejected_unrelated_resolved_url"
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["resolved_url"] == ""
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["url_resolution_reason"] == "resolved URL terms do not match title terms"
    assert rows["Unresolved Google News item"]["url_resolution_status"] == "unresolved_google_news_url"
    assert rows["Unresolved Google News item"]["url_resolution_reason"] == "no acceptable article URL found"
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["classification"] == "likely_qualifying"
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["known_status"] == "known_domain_new_article"
    assert rows["Tulsa food bank fuel costs force more difficult meal delivery"]["classification"] == "likely_qualifying"
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["classification"] == "needs_review"
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["source_role"] == "discovery_lead"
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["donation_wrapper"] is True
    assert rows["Food bank struggles to meet rising demand amid low inventory"]["public_eligible"] is False
    assert rows["Unresolved Google News item"]["classification"] in {"needs_review", "likely_resource_only"}
    assert rows["Summer meal schedule for families in June"]["classification"] == "likely_resource_only"
    assert rows["Existing excluded story"]["classification"] == "duplicate_or_known"
    assert rows["Existing excluded story"]["known_status"] == "already_excluded"
    markdown = report_md_path.read_text(encoding="utf-8")
    assert "Food Line Discovery Gap Check — 2026-06-12" in markdown
    assert "Likely qualifying candidates" in markdown
    assert "Duplicate or already known" in markdown
    assert "WPDE / ABC 15" in markdown
    assert "Tulsa Flyer" in markdown
    assert "rejected_unrelated_resolved_url" in markdown
    assert "unresolved_google_news_url" in markdown
    assert "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14" in markdown
    assert "https://tulsaflyer.org/2026/06/12/your-money/post/food-bank-fuel-costs" in markdown
    assert "https://news.google.com/rss/articles/CBMiKTAL?oc=5" in markdown
    assert "https://news.google.com/rss/articles/CBMiUNRESOLVED?oc=5" in markdown
    assert not (root / "output" / "site").exists()
    assert not (root / "bluefern-dispatches-pages").exists()
    assert result["published_pages"] is False
    assert result["bluesky_posted"] is False


def test_food_line_discovery_gap_duplicate_detection_uses_resolved_urls(tmp_path: Path, monkeypatch):
    root = tmp_path
    data_dir = root / "data" / "dispatches" / "food-line"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "source_registry.json").write_text(
        json.dumps(
            [
                {
                    "source_id": "wpde-grand-strand-local-news",
                    "source_name": "WPDE / ABC 15",
                    "publisher": "WPDE / ABC 15",
                    "url": "https://wpde.com/news/local",
                    "source_family": "local_news",
                    "source_type": "page",
                    "state": "SC",
                    "location_name": "Horry County, SC",
                    "location_scope": "local",
                    "enabled": True,
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (data_dir / "candidate_source_registry.json").write_text(
        json.dumps(
            [
                {
                    "source_id": "existing-wpde-story",
                    "source_name": "WPDE / ABC 15",
                    "publisher": "WPDE / ABC 15",
                    "candidate_url": "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
                    "source_family": "local_news",
                    "state": "SC",
                    "location_name": "Horry County, SC",
                    "location_scope": "local",
                    "status": "rejected",
                    "notes": "Previously reviewed.",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (data_dir / "discovery_gap_queries.json").write_text(
        Path("data/dispatches/food-line/discovery_gap_queries.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    google_link = "https://news.google.com/rss/articles/CBMiWPDE?oc=5"
    final_url = "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    items = [
        _gap_item(
            title="New data show food insecurity higher than during COVID-19 with Horry County at 14%",
            publisher="WPDE / ABC 15",
            source_url="https://wpde.com",
            link=google_link,
            description="Lowcountry Food Bank says demand is rising, about 20% of children are food insecure, and some distributions served 185 families.",
        )
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(items)
        raise AssertionError(f"unexpected fetch url: {url}")

    monkeypatch.setattr(food_line_gap, "_gap_resolve_google_news_url", _fake_gap_resolve_google_news_url)
    result = food_line_gap.run_food_line_discovery_gap_check(root, "2026-06-12", fetcher=fetcher)
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    row = report["candidates"][0]
    duplicate_row = next(row for row in report["candidates"] if row["classification"] == "duplicate_or_known")
    assert report["duplicate_or_known_count"] == 1
    assert row["classification"] == "likely_qualifying"
    assert duplicate_row["known_status"] in {"already_included", "already_excluded", "duplicate"}
    assert duplicate_row["url"] == final_url
    assert duplicate_row["resolved_url"] == final_url
    assert duplicate_row["google_news_url"] == google_link
    assert row["google_news_url"] == google_link
