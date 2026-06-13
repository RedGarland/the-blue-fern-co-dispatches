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


def test_food_line_discovery_gap_url_normalization_detects_duplicates():
    one = food_line_gap._gap_normalize_url("https://example.com/story/?utm_source=rss&utm_medium=email")
    two = food_line_gap._gap_normalize_url("https://example.com/story")
    assert one == two


def test_food_line_discovery_gap_url_resolution_preserves_article_paths():
    item = _gap_item(
        title="WPDE reports higher food insecurity in Horry County",
        publisher="WPDE / ABC 15",
        source_url="https://wpde.com",
        link="https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14",
        description="Food insecurity is rising and some mobile distributions served 185 families.",
    )
    rows = food_line_gap._gap_parse_rss_items(_rss_payload([item]))
    assert rows[0]["candidate_url"] == "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    assert rows[0]["google_news_url"] == "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    assert rows[0]["publisher_url"] == "https://wpde.com"
    assert rows[0]["candidate_url"] != "https://wpde.com"


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
    assert wpde_result["classification"] == "likely_qualifying"
    assert tulsa_result["classification"] == "likely_qualifying"
    assert resource_result["classification"] == "likely_resource_only"
    assert food_drive_result["classification"] in {"likely_resource_only", "needs_review"}
    assert food_drive_result["classification"] != "likely_qualifying"
    assert empty_shelves_result["classification"] == "likely_qualifying"
    assert fuel_costs_result["classification"] == "likely_qualifying"
    assert cant_keep_on_shelf_result["classification"] == "likely_qualifying"
    assert wpde_result["score"] > resource_result["score"]
    assert tulsa_result["score"] > resource_result["score"]
    assert empty_shelves_result["score"] >= 4
    assert fuel_costs_result["score"] >= 4
    assert cant_keep_on_shelf_result["score"] >= 4


def test_food_line_discovery_gap_report_writes_json_and_markdown_and_skips_publish_and_bluesky(tmp_path: Path):
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
            link="https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14/",
            description="Lowcountry Food Bank says demand is rising, about 20% of children are food insecure, and some distributions served 185 families.",
        ),
        _gap_item(
            title="Tulsa food bank fuel costs force more difficult meal delivery",
            publisher="Tulsa Flyer",
            source_url="https://tulsaflyer.org",
            link="https://tulsaflyer.org/news/tulsa-food-bank-fuel-costs/",
            description="Diesel costs of $24,000-$26,000 and $12,000-$14,000 are taking away meals across 24 counties; 750,000 meals are at risk.",
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

    result = food_line_gap.run_food_line_discovery_gap_check(root, "2026-06-12", fetcher=fetcher)
    report_path = Path(result["report_path"])
    report_md_path = Path(result["report_markdown_path"])
    assert report_path.exists()
    assert report_md_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rows = {row["title"]: row for row in report["candidates"]}
    assert report["candidate_count"] == 4
    assert report["likely_qualifying_count"] == 2
    assert report["likely_resource_only_count"] == 1
    assert report["duplicate_or_known_count"] == 1
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["url"] == "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    assert rows["Tulsa food bank fuel costs force more difficult meal delivery"]["url"] == "https://tulsaflyer.org/news/tulsa-food-bank-fuel-costs"
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["google_news_url"] == "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14"
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["classification"] == "likely_qualifying"
    assert rows["New data show food insecurity higher than during COVID-19 with Horry County at 14%"]["known_status"] == "known_domain_new_article"
    assert rows["Tulsa food bank fuel costs force more difficult meal delivery"]["classification"] == "likely_qualifying"
    assert rows["Summer meal schedule for families in June"]["classification"] == "likely_resource_only"
    assert rows["Existing excluded story"]["classification"] == "duplicate_or_known"
    assert rows["Existing excluded story"]["known_status"] == "already_excluded"
    markdown = report_md_path.read_text(encoding="utf-8")
    assert "Food Line Discovery Gap Check — 2026-06-12" in markdown
    assert "Likely qualifying candidates" in markdown
    assert "Duplicate or already known" in markdown
    assert "WPDE / ABC 15" in markdown
    assert "Tulsa Flyer" in markdown
    assert "https://wpde.com/news/local/new-data-show-food-insecurity-higher-than-during-covid-19-with-horry-county-at-14" in markdown
    assert "https://tulsaflyer.org/news/tulsa-food-bank-fuel-costs" in markdown
    assert not (root / "output" / "site").exists()
    assert not (root / "bluefern-dispatches-pages").exists()
    assert result["published_pages"] is False
    assert result["bluesky_posted"] is False
