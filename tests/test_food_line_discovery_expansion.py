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


def test_food_line_discovery_query_plan_covers_state_territory_and_metro_geographies(tmp_path: Path):
    plan = build_food_line_discovery_query_plan(tmp_path, "2026-06-19")

    families = {row["query_family"] for row in plan}
    states = {row["state_or_territory"] for row in plan if row["state_or_territory"]}
    metros = {row["metro"] for row in plan if row["metro"]}

    assert {"core_hunger", "pressure", "policy_program", "cost_pressure", "state_territory", "metro"}.issubset(families)
    assert "Puerto Rico" in states
    assert "Guam" in states
    assert "U.S. Virgin Islands" in states
    assert "American Samoa" in states
    assert "Northern Mariana Islands" in states
    assert "Charlotte" in metros
    assert "Washington DC" in metros
    assert any("after:2026-06-18" in row["query_text"] and "before:2026-06-20" in row["query_text"] for row in plan)
    assert any(row["geographic_scope"] == "metro" for row in plan)


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
    assert any(row["fetch_status"] == "blocked_403" for row in axios_candidates)
    assert any("403" in row["fetch_error"] for row in axios_candidates)
    assert any(row["manual_review_required"] is True for row in axios_candidates)
    assert any(row["duplicate_of"] for row in axios_candidates)
    assert manual_candidates[0]["review_status"] == "manual_reviewed"
    assert manual_candidates[0]["manual_review_required"] is False
    assert manual_candidates[0]["extraction_quality"] == "manual_fallback"
    assert manual_candidates[0]["final_trace_url"] == axios_trace_url
    assert "No candidates were retained" not in audit["discovery_confidence_summary"]
    assert "no_current_update" in audit["no_current_update_reason"] or audit["no_current_update_reason"]


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
