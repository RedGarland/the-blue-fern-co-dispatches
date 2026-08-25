from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit

from bluefern_dispatches.care_line_discovery import discover_care_line_sources
from bluefern_dispatches.incident_discovery import build_incident_follow_up_queries, discover_incident_seeds, load_incident_seeds
import scripts.discover_food_line_sources as food_line_gap
import scripts.run_care_line_dispatch as care_line_dispatch
import scripts.run_food_line_dispatch as food_line_dispatch


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    parts = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<rss version=\"2.0\"><channel>"]
    for item in items:
        parts.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Thu, 24 Aug 2026 21:58:00 GMT')}</pubDate>"
            f"<description>{item.get('description', '')}</description>"
            f"<source url=\"{item['source_url']}\">{item['publisher']}</source>"
            "</item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _care_line_seed() -> dict[str, str]:
    return {
        "incident_id": "gary-power-outage-care-line",
        "place": "Gary",
        "state": "IN",
        "incident_type": "power_outage",
        "severity_evidence": "multiple days without power and an emergency declaration",
        "source_url": "https://example.com/gary-outage",
        "source_date": "2026-08-23",
    }


def _food_line_seed() -> dict[str, str]:
    return {
        "incident_id": "gary-power-outage-food-line",
        "place": "Gary",
        "state": "IN",
        "incident_type": "power_outage",
        "severity_evidence": "multiple days without power and an emergency declaration",
        "source_url": "https://example.com/gary-outage",
        "source_date": "2026-08-23",
    }


def _severe_incident_source() -> dict[str, str]:
    return {
        "source_id": "gary-power-outage-source",
        "title": "Gary, Indiana prolonged power outage closes Methodist care sites",
        "summary_or_snippet": "Multiple days without power and an emergency declaration closed clinics and rescheduled appointments.",
        "source_url": "https://example.com/gary-outage",
        "publisher": "Example News",
        "published_at": "2026-08-23T12:00:00Z",
        "city": "Gary",
        "state": "IN",
        "incident_type": "power_outage",
        "severity_evidence": "multiple days without power and an emergency declaration",
    }


def _weak_incident_source() -> dict[str, str]:
    return {
        "source_id": "gary-brief-outage-source",
        "title": "Gary outage restored quickly after brief interruption",
        "summary_or_snippet": "A brief outage lasting a few hours was resolved the same day.",
        "source_url": "https://example.com/gary-brief-outage",
        "publisher": "Example News",
        "published_at": "2026-08-23T12:00:00Z",
        "city": "Gary",
        "state": "IN",
        "incident_type": "power_outage",
        "severity_evidence": "brief outage lasting a few hours",
    }


def _polluted_food_bank_source() -> dict[str, str]:
    return {
        "source_record_id": "food-line-auto-150e59d4733e69e1",
        "source_id": "ktal-kmss-food-bank-summer-feeding",
        "title": "Food bank in Shreveport aids families amid school break",
        "url": "https://www.ktalnews.com/news/food-bank-summer-feeding/",
        "source_name": "KTAL / KMSS Food Bank Summer Feeding",
        "publisher": "KTAL / KMSS",
        "published_at": "2026-08-25T00:00:00Z",
        "page_metadata_date": "2026-06-10T23:51:07+00:00",
        "summary_or_snippet": (
            "Food bank in Shreveport aids families amid school break Skip to content KTALnews.com "
            "Shreveport 82° Watch KTAL Now stream 24/7 Account Profile Log Out Shreveport 82° "
            "Sponsored By Toggle Menu Open Navigation Close Navigation Search Please enter a search term. "
            "Primary Menu Live📺 Weather Local Views: submit pics & videos here Severe Weather Weather Cameras "
            "Closings & Delays Futurecast Kid’s Weathercast Interactive Radar Earthquakes Power Outages "
            "Road Conditions Drought Watch Burn Bans Lake Levels and Forecasts Tracking the Tropics Almanac WeatheRate"
        ),
        "exact_supporting_passage": (
            "Local food banks in Northwest Louisiana are offering summer feeding programs to ensure no child goes hungry, "
            "despite rising food costs and low donations"
        ),
        "evidence_text": (
            "Food bank in Shreveport aids families amid school break Local food banks in Northwest Louisiana are offering "
            "summer feeding programs to ensure no child goes hungry, despite rising food costs and low donations."
        ),
        "pressure_signal": False,
        "pressure_type": "service reduction",
        "pressure_reason": "insufficient specific pressure evidence",
        "pressure_summary": "",
    }


def _exact_support_outage_source() -> dict[str, str]:
    return {
        "source_record_id": "gary-power-outage-source",
        "source_id": "gary-power-outage-source",
        "title": "Gary, Indiana prolonged power outage closes Methodist care sites",
        "url": "https://example.com/gary-outage",
        "source_name": "Example News",
        "publisher": "Example News",
        "published_at": "2026-08-23T12:00:00Z",
        "page_metadata_date": "2026-08-23T12:00:00Z",
        "summary_or_snippet": "Skip to content Power Outages navigation text that should not drive seeding.",
        "exact_supporting_passage": (
            "Multiple days without power and an emergency declaration closed clinics and rescheduled appointments."
        ),
        "evidence_text": (
            "Multiple days without power and an emergency declaration closed clinics and rescheduled appointments."
        ),
        "city": "Gary",
        "state": "IN",
        "incident_type": "power_outage",
        "severity_evidence": "multiple days without power and an emergency declaration",
    }


def test_build_incident_follow_up_queries_triggers_for_severe_gary_care_line_seed():
    result = build_incident_follow_up_queries(_care_line_seed(), dispatch_slug="care-line", max_queries=2)

    assert result["ok"] is True
    assert result["query_count"] == 2
    assert result["place"] == "Gary, IN"
    assert result["trigger_reason"] == "severe_incident_evidence"
    assert all(query["consequence_domain"] == "care-line" for query in result["queries"])
    first_query = result["queries"][0]["query"].lower()
    assert "gary" in first_query
    assert "power outage" in first_query
    assert any(term in first_query for term in ("hospital closed", "clinic closed", "emergency department closed"))


def test_build_incident_follow_up_queries_triggers_for_severe_gary_food_line_seed():
    result = build_incident_follow_up_queries(_food_line_seed(), dispatch_slug="food-line", max_queries=2)

    assert result["ok"] is True
    assert result["query_count"] == 2
    assert result["place"] == "Gary, IN"
    assert result["trigger_reason"] == "severe_incident_evidence"
    assert all(query["consequence_domain"] == "food-line" for query in result["queries"])
    first_query = result["queries"][0]["query"].lower()
    assert "gary" in first_query
    assert "power outage" in first_query
    assert any(term in first_query for term in ("food spoilage", "refrigeration loss", "pantry demand", "snap disruption"))


def test_build_incident_follow_up_queries_rejects_weak_or_out_of_scope_seeds():
    weak_result = build_incident_follow_up_queries(
        {
            "incident_id": "weak-seed",
            "place": "Gary",
            "state": "IN",
            "incident_type": "power_outage",
            "severity_evidence": "brief outage lasting a few hours",
        },
        dispatch_slug="care-line",
    )
    out_of_scope_result = build_incident_follow_up_queries(
        {
            "incident_id": "wrong-kind",
            "place": "Gary",
            "state": "IN",
            "incident_type": "parade",
            "severity_evidence": "multiple days and emergency declaration",
        },
        dispatch_slug="food-line",
    )

    assert weak_result["ok"] is False
    assert weak_result["query_count"] == 0
    assert weak_result["trigger_reason"] == "insufficient_severity_evidence"
    assert weak_result["queries"] == []
    assert out_of_scope_result["ok"] is False
    assert out_of_scope_result["query_count"] == 0
    assert out_of_scope_result["trigger_reason"] == "incident_type_out_of_scope"
    assert out_of_scope_result["queries"] == []


def test_load_incident_seeds_reads_dispatch_file(tmp_path: Path):
    root = tmp_path
    path = root / "data" / "dispatches" / "care-line" / "incident_seeds.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"incident_seeds": [_care_line_seed()]}, indent=2),
        encoding="utf-8",
    )

    seeds = load_incident_seeds(root, "care-line")

    assert len(seeds) == 1
    assert seeds[0]["incident_id"] == "gary-power-outage-care-line"


def test_discover_incident_seeds_creates_shared_ledger_from_severe_source_record(tmp_path: Path):
    path = tmp_path / "data" / "dispatches" / "care-line" / "sources" / "2026-08-23" / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_severe_incident_source()], indent=2), encoding="utf-8")

    report = discover_incident_seeds(tmp_path, source_paths=[path])
    care_line_seeds = load_incident_seeds(tmp_path, "care-line")
    food_line_seeds = load_incident_seeds(tmp_path, "food-line")

    assert report["ok"] is True
    assert report["incident_seed_count"] == 1
    assert report["incident_seed_dispatch_counts"] == {"care-line": 1, "food-line": 1}
    assert (tmp_path / "data" / "dispatches" / "incidents" / "incident_seeds.json").exists()
    assert len(care_line_seeds) == 1
    assert len(food_line_seeds) == 1
    assert care_line_seeds[0]["incident_type"] == "power_outage"
    assert care_line_seeds[0]["place"] == "Gary"
    assert care_line_seeds[0]["state"] == "IN"
    assert care_line_seeds[0]["provenance"] == "source_record"


def test_discover_incident_seeds_rejects_weak_incident_source_record(tmp_path: Path):
    path = tmp_path / "data" / "dispatches" / "care-line" / "sources" / "2026-08-23" / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_weak_incident_source()], indent=2), encoding="utf-8")

    report = discover_incident_seeds(tmp_path, source_paths=[path])

    assert report["ok"] is True
    assert report["incident_seed_count"] == 0
    assert load_incident_seeds(tmp_path, "care-line") == []


def test_discover_incident_seeds_ignores_polluted_summary_when_exact_support_is_clean(tmp_path: Path):
    path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / "2026-08-23" / "auto_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_polluted_food_bank_source()], indent=2), encoding="utf-8")

    report = discover_incident_seeds(tmp_path, source_paths=[path])

    assert report["ok"] is True
    assert report["incident_seed_count"] == 0
    assert load_incident_seeds(tmp_path, "care-line") == []
    assert load_incident_seeds(tmp_path, "food-line") == []


def test_discover_incident_seeds_uses_exact_support_passage_for_legitimate_outage(tmp_path: Path):
    path = tmp_path / "data" / "dispatches" / "care-line" / "sources" / "2026-08-23" / "manual_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_exact_support_outage_source()], indent=2), encoding="utf-8")

    report = discover_incident_seeds(tmp_path, source_paths=[path])
    care_line_seeds = load_incident_seeds(tmp_path, "care-line")
    food_line_seeds = load_incident_seeds(tmp_path, "food-line")

    assert report["ok"] is True
    assert report["incident_seed_count"] == 1
    assert care_line_seeds[0]["incident_type"] == "power_outage"
    assert care_line_seeds[0]["source_record_id"] == "gary-power-outage-source"
    assert food_line_seeds[0]["incident_type"] == "power_outage"


def test_discover_incident_seeds_dedupes_repeated_updates_and_refreshes_dates(tmp_path: Path):
    path_one = tmp_path / "data" / "dispatches" / "care-line" / "sources" / "2026-08-23" / "manual_sources.json"
    path_two = tmp_path / "data" / "dispatches" / "food-line" / "sources" / "2026-08-24" / "manual_sources.json"
    path_one.parent.mkdir(parents=True, exist_ok=True)
    path_two.parent.mkdir(parents=True, exist_ok=True)
    first = _severe_incident_source()
    second = dict(_severe_incident_source(), summary_or_snippet="The outage continued into the next day with appointments rescheduled again.", published_at="2026-08-24T12:00:00Z")
    path_one.write_text(json.dumps([first], indent=2), encoding="utf-8")
    path_two.write_text(json.dumps([second], indent=2), encoding="utf-8")

    report = discover_incident_seeds(tmp_path, source_paths=[path_one, path_two])
    seeds = load_incident_seeds(tmp_path, "care-line")

    assert report["incident_seed_count"] == 1
    assert report["incident_seed_deduped_count"] == 1
    assert len(seeds) == 1
    assert seeds[0]["source_date"] == "2026-08-24"
    assert seeds[0]["last_seen_date"] == "2026-08-24"


def test_load_incident_seeds_filters_expired_auto_seed(tmp_path: Path):
    ledger = tmp_path / "data" / "dispatches" / "incidents" / "incident_seeds.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                "incident_seeds": [
                    {
                        "incident_id": "expired-seed",
                        "seed_key": "power_outage|gary|in|2026-08-01",
                        "place": "Gary",
                        "state": "IN",
                        "incident_type": "power_outage",
                        "source_date": "2026-08-01",
                        "incident_start_date": "2026-08-01",
                        "severity_basis": "multi-day outage",
                        "discovered_at": "2026-08-01T12:00:00Z",
                        "provenance": "source_record",
                        "dispatch_targets": ["care-line", "food-line"],
                        "incident_status": "active",
                        "expires_on": "2026-08-10",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    assert load_incident_seeds(tmp_path, "care-line") == []
    assert load_incident_seeds(tmp_path, "food-line") == []


def test_care_line_discovery_prepends_incident_seed_queries(tmp_path: Path):
    seed = _care_line_seed()
    seen_search_queries: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            seen_search_queries.append(unquote_plus(parse_qs(urlsplit(url).query)["q"][0]))
            return _rss_payload([])
        raise AssertionError(f"unexpected fetch url: {url}")

    report = discover_care_line_sources(
        tmp_path,
        "2026-08-23",
        fetcher=fetcher,
        max_queries=1,
        incident_seeds=[seed],
        write=False,
        dry_run=True,
    )

    assert report["incident_seed_count"] == 1
    assert report["incident_seed_query_count"] == 8
    assert report["incident_seed_diagnostics"][0]["ok"] is True
    assert report["incident_seed_diagnostics"][0]["query_count"] == 8
    assert report["query_rows"][0]["error"] == ""
    assert seen_search_queries
    assert "Gary" in seen_search_queries[0]
    assert "power outage" in seen_search_queries[0]
    assert "hospital closed" in seen_search_queries[0] or "clinic closed" in seen_search_queries[0]


def test_care_line_incident_seed_can_surface_follow_up_candidate(tmp_path: Path):
    seed = _care_line_seed()
    article_url = "https://example.com/methodist-power-outage"
    article_html = (
        "<html><head><title>Methodist Hospitals reschedules appointments after outage</title></head>"
        "<body><p>Methodist Hospitals closed a clinic and rescheduled appointments after a prolonged power outage in Gary.</p></body></html>"
    ).encode("utf-8")

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Methodist Hospitals reschedules appointments after outage",
                        "link": article_url,
                        "publisher": "Example News",
                        "source_url": article_url,
                        "description": "Clinic closures and appointments rescheduled after power outage in Gary.",
                        "pubDate": "Thu, 24 Aug 2026 21:58:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return article_html
        raise AssertionError(f"unexpected fetch url: {url}")

    report = discover_care_line_sources(
        tmp_path,
        "2026-08-23",
        fetcher=fetcher,
        max_queries=1,
        max_results_per_query=1,
        incident_seeds=[seed],
        write=False,
        dry_run=True,
    )

    assert report["incident_seed_count"] == 1
    assert report["incident_seed_query_count"] == 8
    assert report["source_count"] >= 1
    assert report["public_signal_count"] >= 1


def test_food_line_discovery_gap_prepends_incident_seed_queries(tmp_path: Path):
    seed = _food_line_seed()
    seen_search_queries: list[str] = []

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            seen_search_queries.append(unquote_plus(parse_qs(urlsplit(url).query)["q"][0]))
            return _rss_payload([])
        raise AssertionError(f"unexpected fetch url: {url}")

    result = food_line_gap.run_food_line_discovery_gap_check(
        tmp_path,
        "2026-08-23",
        fetcher=fetcher,
        max_queries=1,
        incident_seeds=[seed],
        fast=True,
    )
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))

    assert result["incident_seed_count"] == 1
    assert result["incident_seed_query_count"] == 8
    assert result["incident_seed_diagnostics"][0]["ok"] is True
    assert result["incident_seed_diagnostics"][0]["query_count"] == 8
    assert seen_search_queries
    assert "Gary" in seen_search_queries[0]
    assert "power outage" in seen_search_queries[0]
    assert "food" in seen_search_queries[0] or "pantry" in seen_search_queries[0] or "SNAP" in seen_search_queries[0]
    assert report["query_count"] == 1
    assert report["incident_seed_count"] == 1
    assert report["incident_seed_query_count"] == 8


def test_food_line_incident_seed_can_surface_follow_up_candidate(tmp_path: Path):
    seed = _food_line_seed()
    article_url = "https://example.com/gary-food-response"
    article_html = (
        "<html><head><title>Food bank demand is rising after outage</title></head>"
        "<body><p>Food spoilage and refrigeration loss forced emergency meal distribution in Gary after the prolonged outage as empty shelves and rising demand strained providers.</p></body></html>"
    ).encode("utf-8")

    def fetcher(url: str, timeout: int = 15):
        if url.startswith("https://news.google.com/rss/search?q="):
            return _rss_payload(
                [
                    {
                        "title": "Food bank demand is rising after outage",
                        "link": article_url,
                        "publisher": "Example News",
                        "source_url": article_url,
                        "description": "Food spoilage, empty shelves, and rising demand after the Gary outage.",
                        "pubDate": "Thu, 24 Aug 2026 21:58:00 GMT",
                    }
                ]
            )
        if url == article_url:
            return article_html
        raise AssertionError(f"unexpected fetch url: {url}")

    result = food_line_gap.run_food_line_discovery_gap_check(
        tmp_path,
        "2026-08-23",
        fetcher=fetcher,
        max_queries=1,
        incident_seeds=[seed],
        fast=True,
    )

    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))

    assert result["incident_seed_count"] == 1
    assert result["incident_seed_query_count"] == 8
    assert result["candidate_count"] >= 1
    assert result["likely_qualifying_count"] >= 1
    assert report["query_count"] == 1


def test_care_line_dispatch_wrapper_loads_incident_seeds(tmp_path: Path, monkeypatch):
    seed = _care_line_seed()
    captured: dict[str, object] = {}

    monkeypatch.setattr(care_line_dispatch, "discover_incident_seeds", lambda root: {"ok": True, "incident_seed_count": 1})
    monkeypatch.setattr(care_line_dispatch, "load_incident_seeds", lambda root, slug: [seed])

    def fake_discover(root: Path, edition_date: str, **kwargs):
        captured["root"] = root
        captured["edition_date"] = edition_date
        captured["incident_seeds"] = kwargs.get("incident_seeds")
        return {"ok": True}

    monkeypatch.setattr(care_line_dispatch, "discover_care_line_sources", fake_discover)
    monkeypatch.setattr(care_line_dispatch, "build_site", lambda *args, **kwargs: {"ok": True})

    result = care_line_dispatch._run_one_day(
        tmp_path,
        "2026-08-23",
        discover=True,
        publish=False,
        push=False,
        max_results_per_query=1,
        max_queries=1,
        max_candidates=1,
    )

    assert result["ok"] is True
    assert captured["edition_date"] == "2026-08-23"
    assert captured["incident_seeds"] == [seed]


def test_food_line_dispatch_wrapper_loads_incident_seeds(tmp_path: Path, monkeypatch):
    seed = _food_line_seed()
    captured: dict[str, object] = {}

    monkeypatch.setattr(food_line_dispatch, "discover_incident_seeds", lambda root: {"ok": True, "incident_seed_count": 1})
    monkeypatch.setattr(food_line_dispatch, "load_incident_seeds", lambda root, slug: [seed])
    monkeypatch.setattr(
        food_line_dispatch,
        "_food_line_default_discovery_gap_summary",
        lambda root, date: {"public_no_qualifying_update_validated": False},
    )
    monkeypatch.setattr(food_line_dispatch, "_food_line_should_auto_run_discovery_gap_check", lambda **kwargs: True)
    monkeypatch.setattr(
        food_line_dispatch,
        "_food_line_discovery_gap_summary",
        lambda root, date, public_story_rows: {"public_no_qualifying_update_validated": True, "summary": "ok"},
    )

    def fake_gap_check(root: Path, date: str, **kwargs):
        captured["root"] = root
        captured["date"] = date
        captured["incident_seeds"] = kwargs.get("incident_seeds")
        return {"ok": True, "public_no_qualifying_update_validated": True}

    monkeypatch.setattr(food_line_dispatch, "run_food_line_discovery_gap_check", fake_gap_check)

    result = food_line_dispatch._food_line_resolve_discovery_gap_summary(
        root=tmp_path,
        date="2026-08-23",
        public_story_rows=[],
        include_discovery_gap_summary=False,
        no_current_update_candidate=True,
        future_date_blocked=False,
        collector_result={"ok": True},
        discovery_bridge_result={},
        news_item_count=5,
        local_signal_count=5,
        state_signal_count=0,
    )

    assert result["public_no_qualifying_update_validated"] is True
    assert captured["date"] == "2026-08-23"
    assert captured["incident_seeds"] == [seed]
