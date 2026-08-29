from __future__ import annotations

import base64
import json
import urllib.error
from pathlib import Path

import scripts.run_food_line_dispatch as food_line_dispatch
from bluefern_dispatches.food_line_discovery_bridge import run_food_line_discovery_intake_bridge
from bluefern_dispatches.food_line_discovery_expansion import run_food_line_discovery_expansion


def _ensure_assets(root: Path) -> None:
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    repo_assets = Path(__file__).resolve().parent.parent / "assets"
    for asset_name in (
        "bluefern.png",
        "food-line-logo.png",
        "food-line-dispatch-social.png",
        "site.css",
        "favicon.ico",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "apple-touch-icon.png",
    ):
        source = repo_assets / asset_name
        if source.exists():
            (assets / asset_name).write_bytes(source.read_bytes())
    if not (assets / "bluefern.png").exists():
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9W2jN9kAAAAASUVORK5CYII="
        )
        (assets / "bluefern.png").write_bytes(png_bytes)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<rss version=\"2.0\"><channel>"]
    for item in items:
        parts.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Fri, 20 Jun 2026 12:00:00 GMT')}</pubDate>"
            f"<description>{item.get('description', '')}</description>"
            f"<source url=\"{item['source_url']}\">{item['publisher']}</source>"
            "</item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _candidate_row(
    *,
    candidate_id: str,
    title: str,
    publisher: str,
    google_news_url: str,
    final_trace_url: str,
    publication_date: str = "2026-06-20T12:00:00Z",
    state_or_territory: str = "NC",
    metro: str = "Charlotte",
    fetch_status: str = "blocked_403",
    fetch_error: str = "HTTPError: 403 Forbidden",
    classification_status: str = "qualified_pressure_signal",
    exclusion_reason: str = "",
    manual_review_required: bool = True,
    pressure_terms_detected: list[str] | None = None,
    location_terms_detected: list[str] | None = None,
    duplicate_of: str = "",
    review_status: str = "needs_review",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "discovery_date": "2026-06-21",
        "query_family": "metro",
        "query_text": "\"Charlotte\" food bank demand",
        "query_url": "https://news.google.com/rss/search?q=charlotte",
        "geographic_scope": "metro",
        "state_or_territory": state_or_territory,
        "metro": metro,
        "discovery_channel": "google_news_rss",
        "discovered_title": title,
        "discovered_publisher": publisher,
        "discovered_url": google_news_url,
        "canonical_url": final_trace_url,
        "google_news_url": google_news_url,
        "publication_date": publication_date,
        "fetch_status": fetch_status,
        "fetch_error": fetch_error,
        "final_trace_url": final_trace_url,
        "duplicate_of": duplicate_of,
        "review_status": review_status,
        "classification_status": classification_status,
        "exclusion_reason": exclusion_reason,
        "pressure_terms_detected": pressure_terms_detected or ["food bank", "SNAP", "school meals"],
        "location_terms_detected": location_terms_detected or [state_or_territory, metro],
        "manual_review_required": manual_review_required,
        "retrieved_at": "2026-06-21T12:00:00Z",
    }


def test_food_line_discovery_bridge_keeps_google_news_metadata_but_intakes_publisher_url(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    google_news_url = "https://news.google.com/rss/articles/CBMiAXY?oc=5"
    publisher_url = "https://www.axios.com/local/charlotte/2026/06/20/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes"
    rss_items = [
        {
            "title": "Charlotte nonprofits brace for summer hunger surge",
            "link": google_news_url,
            "source_url": publisher_url,
            "publisher": "Axios Charlotte",
            "pubDate": "Sat, 21 Jun 2026 12:00:00 GMT",
            "description": "Charlotte nonprofits expect increased need as school meals end and SNAP changes tighten access.",
        }
    ]

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(rss_items)
        if url == publisher_url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", hdrs=None, fp=None)
        raise AssertionError(f"unexpected fetch url: {url}")

    result = run_food_line_discovery_expansion(
        tmp_path,
        edition_date,
        fetcher=fetcher,
        edition_mode="no_current_update",
        max_queries=1,
        max_results_per_query=10,
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    source_rows = json.loads(Path(bridge["discovery_source_input_path"]).read_text(encoding="utf-8"))
    bridge_row = next(row for row in source_rows if row["discovered_title"] == "Charlotte nonprofits brace for summer hunger surge")

    assert result["ok"] is True
    assert bridge["discovery_expansion_used"] is True
    assert bridge_row["final_trace_url"] == publisher_url
    assert bridge_row["google_news_url"].lower() == google_news_url.lower()
    assert bridge_row["source_url"] == publisher_url
    assert bridge_row["original_source_url"] == publisher_url
    assert bridge_row["fetch_status"] == "blocked_403"
    assert bridge_row["manual_review_required"] is True
    assert bridge_row["discovered_publisher"] == "Axios Charlotte"
    assert bridge_row["classification_status"] == "blocked_fetch"
    assert bridge_row["candidate_review_status"] == "needs_review"
    assert bridge_row["discovery_lane"] == "news_article"


def test_food_line_blocked_candidates_do_not_become_public_signals_without_manual_fallback(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    audit_path = tmp_path / "output" / "review" / "food-line" / edition_date / "discovery_audit.json"
    blocked_candidate = _candidate_row(
        candidate_id="food-line-discovery-blocked",
        title="Charlotte newsletter update",
        publisher="Axios Charlotte",
        google_news_url="https://news.google.com/rss/articles/CBMiAXY?oc=5",
        final_trace_url="https://www.axios.com/local/charlotte/2026/06/19/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes",
        pressure_terms_detected=[],
        classification_status="blocked_fetch",
        exclusion_reason="fetch blocked",
    )
    _write_json(candidate_path, [blocked_candidate])
    _write_json(
        audit_path,
        {
            "discovery_confidence": "limited",
            "discovery_confidence_reason": "Candidates were discovered, but blocked fetches prevented qualification.",
            "no_current_update": True,
            "no_current_update_reason": "Candidates were discovered, but blocked fetches prevented qualification.",
            "discovery_audit_json_path": str(audit_path),
            "discovery_candidates_path": str(candidate_path),
        },
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    result = food_line_dispatch.run_food_line_dispatch(
        tmp_path,
        edition_date,
        use_discovery_candidates=True,
        generate_audio=False,
        allow_future_date=True,
    )

    assert bridge["discovery_candidates_manual_review_required"] == 1
    assert bridge["discovery_no_current_update_state"] == "candidates_found_but_fetch_blocked"
    assert result["discovery_expansion_used"] is True
    assert result["discovery_blocked_candidate_count"] == 1
    assert result["discovery_candidates_manual_review_required"] == 1
    assert result["qualified_primary_count"] == 0
    assert result["public_rendered"] is False or result["edition_mode"] == "no_current_update"


def test_food_line_manual_fallback_merges_without_losing_the_original_trace_url(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    fallback_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "manual_fallback.json"
    publisher_url = "https://www.axios.com/local/charlotte/2026/06/19/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes"
    candidate = _candidate_row(
        candidate_id="food-line-discovery-manual-fallback",
        title="Charlotte nonprofits brace for summer hunger surge",
        publisher="Axios Charlotte",
        google_news_url="https://news.google.com/rss/articles/CBMiAXY?oc=5",
        final_trace_url=publisher_url,
    )
    _write_json(candidate_path, [candidate])
    _write_json(
        fallback_path,
        [
            {
                "publisher": "Axios Charlotte",
                "canonical_url": publisher_url,
                "headline": "Charlotte nonprofits brace for summer hunger surge",
                "date": edition_date,
                "location": "Charlotte, NC",
                "manually_reviewed_summary": "The article describes a summer pressure spike tied to school meals ending and tighter SNAP access.",
                "pressure_evidence_summary": "Nonprofits expect increased need as school meals end and SNAP changes tighten access.",
                "affected_groups": ["families", "children", "SNAP households"],
                "limitations": "The publisher page returned 403 to automated fetchers, so review depends on manual inspection.",
                "extraction_quality": "manual_fallback",
                "reviewer_or_source_note": "Manually reviewed from the article and Google News discovery metadata.",
                "final_trace_url": publisher_url,
            }
        ],
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    source_rows = json.loads(Path(bridge["discovery_source_input_path"]).read_text(encoding="utf-8"))
    bridge_row = next(row for row in source_rows if row["source_record_id"] == "food-line-discovery-manual-fallback")

    assert bridge["discovery_manual_fallback_merged_count"] == 1
    assert bridge_row["manual_review_required"] is False
    assert bridge_row["review_status"] == "manual_reviewed"
    assert bridge_row["classification_status"] == "manual_fallback"
    assert bridge_row["final_trace_url"] == publisher_url
    assert bridge_row["candidate_review_status"] == "needs_review"
    assert bridge_row["traceability_status"] == "traceable"
    assert bridge_row["manual_fallback_summary"] == "The article describes a summer pressure spike tied to school meals ending and tighter SNAP access."
    assert bridge_row["summary_or_snippet"] == "Nonprofits expect increased need as school meals end and SNAP changes tighten access."


def test_food_line_no_current_update_manifest_reports_none_qualified_and_blocked_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    audit_path = tmp_path / "output" / "review" / "food-line" / edition_date / "discovery_audit.json"
    candidates = [
        _candidate_row(
            candidate_id="food-line-discovery-context-only",
            title="Community update",
            publisher="Example News",
            google_news_url="https://news.google.com/rss/articles/CBMiCTX?oc=5",
            final_trace_url="https://example.com/community-pantry-updates-hours",
            fetch_status="ok",
            fetch_error="",
            classification_status="context_only",
            exclusion_reason="no current pressure evidence",
            manual_review_required=True,
            pressure_terms_detected=[],
        ),
        _candidate_row(
            candidate_id="food-line-discovery-blocked-two",
            title="Charlotte newsletter update",
            publisher="Axios Charlotte",
            google_news_url="https://news.google.com/rss/articles/CBMiAXY?oc=5",
            final_trace_url="https://www.axios.com/local/charlotte/2026/06/19/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes",
            pressure_terms_detected=[],
            classification_status="blocked_fetch",
            exclusion_reason="fetch blocked",
        ),
    ]
    _write_json(candidate_path, candidates)
    _write_json(
        audit_path,
        {
            "discovery_confidence": "limited",
            "discovery_confidence_reason": "Candidates were discovered, but none were strong enough to avoid manual review.",
            "no_current_update": True,
            "no_current_update_reason": "Candidates were discovered, but none were strong enough to avoid manual review.",
            "discovery_audit_json_path": str(audit_path),
            "discovery_candidates_path": str(candidate_path),
        },
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    result = food_line_dispatch.run_food_line_dispatch(
        tmp_path,
        edition_date,
        use_discovery_candidates=True,
        generate_audio=False,
        allow_future_date=True,
    )

    assert result["discovery_candidate_count"] == 2
    assert result["discovery_qualified_candidate_count"] == 0
    assert result["discovery_context_candidate_count"] == 1
    assert result["discovery_blocked_candidate_count"] == 1
    assert result["discovery_candidates_manual_review_required"] == 2
    assert result["discovery_no_current_update_state"] == "candidates_found_but_none_qualified"
    assert bridge["discovery_no_current_update_reason"] == "Discovery retained candidates, but none were classified as qualified pressure signals."
    assert result["discovery_no_current_update_reason"] == ""
    assert result["discovery_review_path"].endswith("discovery_intake.json")


def test_food_line_no_current_update_manifest_reports_qualified_candidates_with_publication_check_reason(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    audit_path = tmp_path / "output" / "review" / "food-line" / edition_date / "discovery_audit.json"
    candidates = [
        _candidate_row(
            candidate_id="food-line-discovery-qualified",
            title="Charlotte nonprofits brace for summer hunger surge",
            publisher="Axios Charlotte",
            google_news_url="https://news.google.com/rss/articles/CBMiAXY?oc=5",
            final_trace_url="https://www.axios.com/local/charlotte/2026/06/19/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes",
            fetch_status="ok",
            fetch_error="",
            classification_status="qualified_pressure_signal",
            exclusion_reason="",
            manual_review_required=False,
        )
    ]
    _write_json(candidate_path, candidates)
    _write_json(
        audit_path,
        {
            "discovery_confidence": "moderate",
            "discovery_confidence_reason": "Pressure signals were found, but some candidates still need manual review or had fetch problems.",
            "no_current_update": True,
            "no_current_update_reason": "",
            "discovery_audit_json_path": str(audit_path),
            "discovery_candidates_path": str(candidate_path),
        },
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    manifest_flag, manifest_reason = food_line_dispatch._food_line_discovery_no_current_update_metadata(
        "no_current_update",
        bridge,
    )

    assert bridge["discovery_no_current_update_state"] == "qualified_candidates_found"
    assert bridge["discovery_no_current_update_reason"] == "Discovery retained qualified candidates, but none passed normal Food Line publication checks."
    assert manifest_flag is True
    assert manifest_reason == bridge["discovery_no_current_update_reason"]
    assert "no discovery candidates were retained" not in manifest_reason


def test_food_line_no_current_update_manifest_reports_no_retained_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    audit_path = tmp_path / "output" / "review" / "food-line" / edition_date / "discovery_audit.json"
    _write_json(candidate_path, [])
    _write_json(
        audit_path,
        {
            "discovery_confidence": "low",
            "discovery_confidence_reason": "No retained candidates were discovered after running the expanded query families.",
            "no_current_update": True,
            "no_current_update_reason": "",
            "discovery_audit_json_path": str(audit_path),
            "discovery_candidates_path": str(candidate_path),
        },
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)

    assert bridge["discovery_candidate_count"] == 0
    assert bridge["discovery_no_current_update_state"] == "no_candidates_found"
    assert bridge["discovery_no_current_update_reason"] == "No discovery candidates were retained."


def test_food_line_discovery_manifest_metadata_is_blank_for_current_update(tmp_path: Path):
    bridge_result = {
        "discovery_expansion_used": True,
        "discovery_no_current_update_reason": "Discovery retained qualified candidates, but none passed normal Food Line publication checks.",
    }

    manifest_flag, manifest_reason = food_line_dispatch._food_line_discovery_no_current_update_metadata(
        "current_update",
        bridge_result,
    )

    assert manifest_flag is False
    assert manifest_reason == ""


def test_food_line_google_news_wrappers_stay_out_of_public_trace_urls(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-06-21"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    publisher_url = "https://www.axios.com/local/charlotte/2026/06/19/charlotte-summer-food-insecurity-school-break-mecklenburg-nourish-up-snap-changes"
    google_news_url = "https://news.google.com/rss/articles/CBMiAXY?oc=5"
    candidate = _candidate_row(
        candidate_id="food-line-discovery-public-trace",
        title="Charlotte nonprofits brace for summer hunger surge",
        publisher="Axios Charlotte",
        google_news_url=google_news_url,
        final_trace_url=publisher_url,
        classification_status="qualified_pressure_signal",
    )
    _write_json(candidate_path, [candidate])
    _write_json(
        tmp_path / "output" / "review" / "food-line" / edition_date / "discovery_audit.json",
        {
            "discovery_confidence": "moderate",
            "discovery_confidence_reason": "Pressure signals were found, but some candidates still need manual review or had fetch problems.",
            "no_current_update": False,
            "historical_source_count": 1,
            "historical_sources": ["Historical Archive"],
            "historical_sources_with_exact_date_items": ["Historical Archive"],
            "discovery_audit_json_path": str(tmp_path / "output" / "review" / "food-line" / edition_date / "discovery_audit.json"),
            "discovery_candidates_path": str(candidate_path),
        },
    )
    bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    source_rows = json.loads(Path(bridge["discovery_source_input_path"]).read_text(encoding="utf-8"))
    bridge_row = source_rows[0]

    assert bridge["discovery_expansion_used"] is True
    assert bridge_row["url"] == publisher_url
    assert bridge_row["google_news_url"].lower() == google_news_url.lower()
    assert bridge_row["final_trace_url"] == publisher_url


def test_food_line_same_date_bridge_merges_prior_pending_findings_without_loss(tmp_path: Path):
    _ensure_assets(tmp_path)
    edition_date = "2026-08-08"
    candidate_path = tmp_path / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"

    first_candidate = _candidate_row(
        candidate_id="food-line-source-watch-first",
        title="Coastal Georgia inventory decline",
        publisher="GPB News",
        google_news_url="https://news.google.com/rss/articles/first?oc=5",
        final_trace_url="https://thecurrentga.org/2026/08/04/good-spread-peanut-butter-a-win-win-for-communities-in-u-s-abroad/",
        fetch_status="ok",
        fetch_error="",
        classification_status="qualified_pressure_signal",
        exclusion_reason="",
        manual_review_required=True,
        pressure_terms_detected=["inventory decline", "warehouse shelving"],
    )
    second_candidate = _candidate_row(
        candidate_id="food-line-source-watch-second",
        title="Maine credit unions raise $100K to help fight hunger",
        publisher="Mainebiz",
        google_news_url="https://news.google.com/rss/articles/second?oc=5",
        final_trace_url="https://mainebiz.biz/article/maine-credit-unions-raise-100k-to-help-fight-hunger",
        publication_date="2026-08-07T12:00:00Z",
        state_or_territory="ME",
        metro="Portland",
        fetch_status="ok",
        fetch_error="",
        classification_status="qualified_pressure_signal",
        exclusion_reason="",
        manual_review_required=True,
        pressure_terms_detected=["hunger", "food assistance"],
    )

    _write_json(candidate_path, [first_candidate])
    first_bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    first_rows = json.loads(Path(first_bridge["discovery_source_input_path"]).read_text(encoding="utf-8"))
    assert [row["candidate_id"] for row in first_rows] == ["food-line-source-watch-first"]

    _write_json(candidate_path, [second_candidate])
    second_bridge = run_food_line_discovery_intake_bridge(tmp_path, edition_date)
    merged_rows = json.loads(Path(second_bridge["discovery_source_input_path"]).read_text(encoding="utf-8"))

    assert {row["candidate_id"] for row in merged_rows} == {
        "food-line-source-watch-first",
        "food-line-source-watch-second",
    }
    assert second_bridge["discovery_source_watch_disposition_counts"]["pending"] == 1
    assert second_bridge["discovery_candidates_intaked"] == 1
    assert second_bridge["discovery_candidates_excluded"] == 0
