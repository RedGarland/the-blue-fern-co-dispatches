from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from bluefern_dispatches.care_line_discovery import (
    classify_care_line_discovery_candidate,
    discover_care_line_sources,
    load_care_line_discovery_queries,
)
from bluefern_dispatches.care_line_sources import atom as care_line_atom
from bluefern_dispatches.care_line_sources import rss as care_line_rss
from bluefern_dispatches.care_line_sources import no_current_update_summary, record_is_public
from bluefern_dispatches.generator import build_site


def _copy_care_line_data(repo: Path, work: Path) -> None:
    source_root = repo / "data" / "dispatches" / "care-line"
    target_root = work / "data" / "dispatches" / "care-line"
    shutil.copytree(source_root, target_root)


def _copy_assets(repo: Path, work: Path) -> None:
    shutil.copytree(repo / "assets", work / "assets")


def _work_root() -> Path:
    repo = Path(__file__).resolve().parents[1]
    work = repo / "output" / "test-runs" / uuid.uuid4().hex / "care-line-discovery"
    work.mkdir(parents=True, exist_ok=True)
    _copy_assets(repo, work)
    _copy_care_line_data(repo, work)
    return work


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<rss version=\"2.0\"><channel>"]
    for item in items:
        parts.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Thu, 19 Jun 2026 21:58:00 GMT')}</pubDate>"
            f"<description>{item.get('description', '')}</description>"
            f"<source url=\"{item['source_url']}\">{item['publisher']}</source>"
            "</item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _html_article(*, title: str, description: str, canonical: str, published: str) -> bytes:
    return f"""<!doctype html>
<html>
<head>
  <title>{title}</title>
  <meta name="description" content="{description}">
  <meta property="og:title" content="{title}">
  <meta property="article:published_time" content="{published}">
  <link rel="canonical" href="{canonical}">
</head>
<body>
  <article>
    <h1>{title}</h1>
    <p>{description}</p>
  </article>
</body>
</html>""".encode("utf-8")


def test_care_line_discovery_queries_cover_direct_and_date_bounded_families():
    repo = Path(__file__).resolve().parents[1]
    config = load_care_line_discovery_queries(repo)

    direct_queries = [row["query_template"] for row in config["queries"]]
    bounded_queries = [row["query_template"] for row in config["date_bounded_queries"]]

    assert len(direct_queries) >= 15
    assert any("hospital closure" in query for query in direct_queries)
    assert any("maternity ward closing" in query for query in direct_queries)
    assert any("site:.org" in query for query in direct_queries)
    assert any("after:{after}" in query and "before:{before}" in query for query in bounded_queries)
    assert any("pharmacy closure" in query for query in bounded_queries)


def test_care_line_discovery_classifies_direct_pressure_and_wrapper_records():
    direct_cases = [
        (
            {
                "title": "Hospital closure threatens access for patients",
                "summary_or_snippet": "The hospital closure will force patients to travel farther.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "Iowa",
                "state": "IA",
            },
            "hospital_operations_signal",
        ),
        (
            {
                "title": "Labor and delivery unit closing at local hospital",
                "summary_or_snippet": "Pregnant patients will need to travel farther for maternity care.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "New Mexico",
                "state": "NM",
            },
            "maternity_family_signal",
        ),
        (
            {
                "title": "ER diversion and boarding delay emergency care",
                "summary_or_snippet": "Ambulances were diverted after boarding pushed wait times higher.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "Texas",
                "state": "TX",
            },
            "emergency_ems_signal",
        ),
        (
            {
                "title": "Clinic reduced hours because of staffing shortage",
                "summary_or_snippet": "The appointment backlog is forcing patients to wait longer.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "Kansas",
                "state": "KS",
            },
            "clinic_operations_signal",
        ),
        (
            {
                "title": "Medical debt pressures patients at the hospital",
                "summary_or_snippet": "Patients are skipping care because of affordability pressure.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "Ohio",
                "state": "OH",
            },
            "insurance_affordability_signal",
        ),
        (
            {
                "title": "Pharmacy closure interrupts prescription access",
                "summary_or_snippet": "Patients need to travel farther to fill prescriptions.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "Arizona",
                "state": "AZ",
            },
            "clinic_operations_signal",
        ),
        (
            {
                "title": "Public health department cuts reduce services",
                "summary_or_snippet": "The cuts weaken local prevention and response systems.",
                "publisher": "Example News",
                "source_name": "Example News",
                "location_name": "Florida",
                "state": "FL",
            },
            "public_health_signal",
        ),
    ]
    for candidate, expected_role in direct_cases:
        result = classify_care_line_discovery_candidate(candidate, known_status="unknown_domain_new_article")
        assert result["classification"] == "likely_qualifying"
        assert result["source_role"] == expected_role
        assert result["public_eligible"] is True

    advice = classify_care_line_discovery_candidate(
        {
            "title": "Wellness tips and symptom tracker for busy families",
            "summary_or_snippet": "Health tips, symptoms, and a recipe roundup.",
            "publisher": "Example News",
            "source_name": "Example News",
        },
        known_status="unknown_domain_new_article",
    )
    marketing = classify_care_line_discovery_candidate(
        {
            "title": "Hospital ribbon cutting celebrates new wing",
            "summary_or_snippet": "The hospital received recognition and hosted a grand opening.",
            "publisher": "Example News",
            "source_name": "Example News",
        },
        known_status="unknown_domain_new_article",
    )
    award = classify_care_line_discovery_candidate(
        {
            "title": "Hospital earns award for excellence",
            "summary_or_snippet": "Recognition for a new technology rollout.",
            "publisher": "Example News",
            "source_name": "Example News",
        },
        known_status="unknown_domain_new_article",
    )
    wrapper = classify_care_line_discovery_candidate(
        {
            "title": "Fundraiser helps clinic after patients travel farther",
            "summary_or_snippet": "The fundraiser says patients travel farther after the clinic closure.",
            "publisher": "Example News",
            "source_name": "Example News",
            "location_name": "Horry County, SC",
            "state": "SC",
            "wrapper_kind": "donation_page",
        },
        known_status="unknown_domain_new_article",
    )
    google_news_wrapper = classify_care_line_discovery_candidate(
        {
            "title": "Google News",
            "summary_or_snippet": "Google News",
            "publisher": "news.google.com",
            "source_name": "news.google.com",
            "url": "https://news.google.com/rss/articles/CBMi-test?oc=5",
        },
        known_status="unknown_domain_new_article",
    )

    assert advice["classification"] == "likely_resource_only"
    assert marketing["classification"] == "needs_review"
    assert award["classification"] == "needs_review"
    assert wrapper["classification"] == "needs_review"
    assert wrapper["source_role"] == "discovery_lead"
    assert wrapper["public_eligible"] is False
    assert wrapper["secondary_queries_generated"]
    assert google_news_wrapper["classification"] == "needs_review"
    assert google_news_wrapper["source_role"] == "discovery_lead"
    assert google_news_wrapper["source_traceability_role"] == "wrapper_url"
    assert google_news_wrapper["public_eligible"] is False


def test_care_line_discovery_treats_date_bearing_openings_and_closures_as_current_pressure_but_keeps_planned_expansion_on_watchlist():
    future_loss = classify_care_line_discovery_candidate(
        {
            "title": "Clinic will close on September 1",
            "summary_or_snippet": "The clinic will close on September 1 and patients will need to travel farther for care.",
            "publisher": "Example News",
            "source_name": "Example News",
            "location_name": "Texas",
            "state": "TX",
        },
        known_status="unknown_domain_new_article",
    )
    same_day_opening = classify_care_line_discovery_candidate(
        {
            "title": "Clinic opens today to expand access",
            "summary_or_snippet": "The clinic opens today and will serve patients in Dallas.",
            "publisher": "Example News",
            "source_name": "Example News",
            "location_name": "Texas",
            "state": "TX",
        },
        known_status="unknown_domain_new_article",
    )
    treatment_opening = classify_care_line_discovery_candidate(
        {
            "title": "Wellness Ranch opens residential treatment campus in Franklin",
            "summary_or_snippet": "The new residential treatment campus opens today and will admit patients immediately.",
            "publisher": "Example News",
            "source_name": "Example News",
            "location_name": "Kentucky",
            "state": "KY",
        },
        known_status="unknown_domain_new_article",
    )
    planned_expansion = classify_care_line_discovery_candidate(
        {
            "title": "Hospital plans to expand behavioral health services",
            "summary_or_snippet": "The hospital plans to expand behavioral health services but has not announced an opening date.",
            "publisher": "Example News",
            "source_name": "Example News",
            "location_name": "Texas",
            "state": "TX",
        },
        known_status="unknown_domain_new_article",
    )
    ownership_announcement = classify_care_line_discovery_candidate(
        {
            "title": "Hospital names new CEO",
            "summary_or_snippet": "The hospital named a new CEO but did not announce any service or access change.",
            "publisher": "Example News",
            "source_name": "Example News",
        },
        known_status="unknown_domain_new_article",
    )

    assert future_loss["classification"] == "likely_qualifying"
    assert future_loss["public_eligible"] is True
    assert same_day_opening["classification"] == "likely_qualifying"
    assert same_day_opening["public_eligible"] is True
    assert treatment_opening["classification"] == "likely_qualifying"
    assert treatment_opening["public_eligible"] is True
    assert planned_expansion["classification"] == "likely_resource_only"
    assert planned_expansion["public_eligible"] is False
    assert ownership_announcement["classification"] == "likely_resource_only"
    assert ownership_announcement["public_eligible"] is False


def test_care_line_discovery_writes_current_signal_pack_and_builds_current_update_edition(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    work = _work_root()
    backup_root = work / "backup"
    monkeypatch.setenv("BLUEFERN_SEED_EDITION_DATE", "2026-06-19")

    current_article = "https://example.com/news/2026/06/19/hospital-closure-threatens-patients"
    wrapper_article = "https://example.com/news/2026/06/19/clinic-fundraiser"
    search_items = [
        {
            "title": "Hospital closure threatens access for patients",
            "link": current_article,
            "publisher": "Example News",
            "source_url": "https://example.com",
            "description": "The hospital closure will force patients to travel farther for care.",
            "pubDate": "Thu, 19 Jun 2026 18:00:00 GMT",
        },
        {
            "title": "Fundraiser helps clinic after patients travel farther",
            "link": wrapper_article,
            "publisher": "Example News",
            "source_url": "https://example.com",
            "description": "The fundraiser says patients travel farther after the clinic closure.",
            "pubDate": "Thu, 19 Jun 2026 18:30:00 GMT",
        },
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(search_items)
        if url == current_article:
            return _html_article(
                title="Hospital closure threatens access for patients",
                description="The hospital closure will force patients to travel farther for care.",
                canonical=current_article,
                published="2026-06-19T18:00:00Z",
            )
        if url == wrapper_article:
            return _html_article(
                title="Fundraiser helps clinic after patients travel farther",
                description="The fundraiser says patients travel farther after the clinic closure.",
                canonical=wrapper_article,
                published="2026-06-19T18:30:00Z",
            )
        raise AssertionError(f"unexpected fetch url: {url}")

    discovery = discover_care_line_sources(
        work,
        "2026-06-19",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=2,
    )
    discovered_path = Path(discovery["discovered_sources_path"])
    discovered_rows = json.loads(discovered_path.read_text(encoding="utf-8"))
    report_path = Path(discovery["discovery_report_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert discovery["ok"] is True
    assert discovery["public_signal_count"] == 1
    assert discovery["wrapper_candidate_count"] == 1
    assert discovery["secondary_query_count"] > 0
    assert discovery["source_families"] == ["local_news"]
    assert report["discovery_gap_check"]["wrapper_candidate_count"] == 1
    assert "weak" in report["no_current_update_summary"].lower()
    assert len(discovered_rows) == 2
    current_row = next(row for row in discovered_rows if row["url"] == current_article)
    wrapper_row = next(row for row in discovered_rows if row["url"] == wrapper_article)
    assert current_row["source_origin"] == "live_discovery"
    assert current_row["registry_status"] == "non_registry_discovered_source"
    assert current_row["source_traceability_role"] == "article_url"
    assert current_row["source_public_story_eligible"] is True
    assert wrapper_row["source_origin"] == "live_discovery"
    assert wrapper_row["registry_status"] == "non_registry_discovered_source"
    assert wrapper_row["source_traceability_role"] == "wrapper_url"
    assert wrapper_row["source_public_story_eligible"] is False

    result = build_site(
        work,
        dry_run=False,
        backup_root=backup_root,
        dispatch_seed_dates={"care-line": "2026-06-19"},
    )

    assert result["ok"] is True
    site_root = work / "output" / "site" / "care-line"
    edition_dir = site_root / "editions" / "2026-06-19"
    manifest = json.loads((edition_dir / "edition_manifest.json").read_text(encoding="utf-8"))
    edition_html = (edition_dir / "index.html").read_text(encoding="utf-8")
    source_table_html = (edition_dir / "source_table.html").read_text(encoding="utf-8")
    claim_ledger_html = (edition_dir / "claim_ledger.html").read_text(encoding="utf-8")

    assert manifest["edition_mode"] == "current_update"
    assert manifest["public_signal_count"] == 1
    assert manifest["wrapper_candidate_count"] == 1
    assert manifest["secondary_query_count"] > 0
    assert manifest["qualified_but_not_public_count"] == 1
    assert "Fundraiser helps clinic" in source_table_html
    assert "Fundraiser helps clinic" not in claim_ledger_html
    assert "The Care Line Dispatch" in edition_html
    for needle in ("discovery_lead", "wrapper_candidate", "stale_current_signal", "resource_only_baseline"):
        assert needle not in edition_html
        assert needle not in source_table_html
        assert needle not in claim_ledger_html


def test_care_line_no_current_update_summary_distinguishes_stale_and_weak_records():
    summary = no_current_update_summary(
        [
            {
                "source_record_id": "care-line-stale",
                "freshness_role": "stale_current_signal",
                "freshness_status": "stale",
                "exclusion_reason": "stale_current_signal",
            },
            {
                "source_record_id": "care-line-wrapper",
                "wrapper_candidate": True,
                "source_role": "discovery_lead",
                "source_public_story_eligible": False,
                "source_traceability_role": "wrapper_url",
                "source_origin": "live_discovery",
            },
        ]
    )

    assert "stale" in summary.lower()
    assert "weak, PR, marketing, or resource-only" in summary


def test_care_line_record_is_public_accepts_review_approved_records_without_discovery_precheck():
    record = {
        "source_record_id": "care-line-approved-recovery",
        "title": "MaineHealth to end labor and delivery at Lincoln Hospital",
        "publisher": "Becker's Hospital Review",
        "url": "https://example.com/mainehealth-labor-delivery",
        "pressure_signal": True,
        "qualifies_for_public_inclusion": False,
        "source_public_story_eligible": False,
        "care_line_review_status": "approved",
        "review_status": "approved",
        "included": False,
        "excluded": False,
        "exclusion_reason": "",
        "pressure_type": "maternity_care_loss",
    }

    assert record_is_public(record) is True


def test_care_line_record_is_public_still_rejects_unreviewed_source_precheck_failures():
    record = {
        "source_record_id": "care-line-unreviewed-discovery",
        "title": "Health tips and symptoms for families",
        "publisher": "Example News",
        "url": "https://example.com/wellness",
        "pressure_signal": True,
        "qualifies_for_public_inclusion": False,
        "source_public_story_eligible": False,
        "care_line_review_status": "not_reviewed",
        "review_status": "not_reviewed",
        "included": False,
        "excluded": False,
        "exclusion_reason": "resource_only_baseline",
        "pressure_type": "context_only",
    }

    assert record_is_public(record) is False


def test_care_line_rss_and_atom_parsers_fall_back_to_html_anchor_pages():
    html = b"""<!doctype html><html><body><a href="https://example.com/story">Example Story</a></body></html>"""

    rss_items = care_line_rss.parse(html)
    atom_items = care_line_atom.parse(html)

    assert rss_items == [
        {
            "url": "https://example.com/story",
            "title": "Example Story",
            "description": "",
            "source": "",
            "id": "https://example.com/story",
        }
    ]
    assert atom_items == rss_items
