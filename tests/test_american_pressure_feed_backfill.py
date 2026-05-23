import json
import ssl
from pathlib import Path

import scripts.backfill_american_pressure_feeds as backfill


def test_certifi_context_path_used_when_available(monkeypatch, tmp_path: Path):
    class _CertifiStub:
        @staticmethod
        def where() -> str:
            return str(tmp_path / "cacert.pem")

    created: dict[str, str] = {}

    def _fake_default_context(*, cafile=None):
        created["cafile"] = str(cafile or "")
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(backfill.ssl, "create_default_context", _fake_default_context)
    monkeypatch.setitem(__import__("sys").modules, "certifi", _CertifiStub)
    ctx, mode, insecure, msg = backfill._ssl_context(allow_insecure_ssl=False)
    assert isinstance(ctx, ssl.SSLContext)
    assert mode == "certifi"
    assert insecure is False
    assert created["cafile"].endswith("cacert.pem")
    assert msg == ""


def test_default_behavior_does_not_disable_verification():
    _ctx, mode, insecure, _msg = backfill._ssl_context(allow_insecure_ssl=False)
    assert mode in {"certifi", "default"}
    assert insecure is False


def test_insecure_flag_is_opt_in_only():
    _ctx, mode, insecure, msg = backfill._ssl_context(allow_insecure_ssl=True)
    assert mode == "insecure"
    assert insecure is True
    assert "WARNING" in msg


def test_backfill_reads_validated_feed_url_field_correctly(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(backfill, "VALIDATION_REPORT_PATH", tmp_path / "report.json")
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "source_id": "s1",
                        "feed_url": "https://validated.example/feed",
                        "feed_type": "rss",
                        "validation_status": "live_validated",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    vmap = backfill._validated_feed_url_map()
    assert vmap["s1"] == ("https://validated.example/feed", "rss")
    url, kind, field = backfill._feed_url({"source_id": "s1", "rss_url": "https://fallback.example"}, vmap)
    assert url == "https://validated.example/feed"
    assert kind == "rss"
    assert field == "validation_report.feed_url"


def test_rss_fixture_parses_entries():
    data = b"""<?xml version='1.0'?><rss><channel><item><title>A</title><link>https://e/a</link><description>x</description><pubDate>Sat, 16 May 2026 10:00:00 +0000</pubDate></item></channel></rss>"""
    entries, parser = backfill._parse_feed(data, "rss", "application/rss+xml")
    assert parser == "rss"
    assert len(entries) == 1
    assert entries[0]["title"] == "A"


def test_atom_fixture_parses_entries():
    data = b"""<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><title>B</title><link href='https://e/b'/><summary>y</summary><updated>2026-05-16T10:00:00Z</updated></entry></feed>"""
    entries, parser = backfill._parse_feed(data, "atom", "application/atom+xml")
    assert parser == "atom"
    assert len(entries) == 1
    assert entries[0]["title"] == "B"


def test_json_feed_fixture_parses_entries():
    data = json.dumps({"version": "https://jsonfeed.org/version/1", "items": [{"title": "C", "url": "https://e/c", "summary": "z", "date_published": "2026-05-16T10:00:00Z"}]}).encode("utf-8")
    entries, parser = backfill._parse_feed(data, "json", "application/feed+json")
    assert parser == "json"
    assert len(entries) == 1
    assert entries[0]["title"] == "C"


def test_ap_relevance_accepts_expected_stress_story():
    ok, pillar, reason = backfill._is_ap_relevant(
        "Food bank demand rises as SNAP delays hit county families",
        "Local pantry lines grew and families reported grocery strain.",
        [],
    )
    assert ok is True
    assert pillar == "food_pressure"
    assert reason == ""


def test_ap_relevance_rejects_non_pressure_story():
    ok, pillar, reason = backfill._is_ap_relevant(
        "Local sports team wins championship",
        "Fans celebrate downtown after game recap.",
        [],
    )
    assert ok is False
    assert pillar == ""
    assert reason in {"non_pressure_topic", "not_ap_relevant"}


def test_ap_relevance_rejects_editor_note_staff_update():
    ok, pillar, reason = backfill._is_ap_relevant(
        "Editor's note: after eight years, our reporter signs off",
        "A newsroom farewell and staff update from the publisher.",
        ["jobs_paycheck_pressure"],
    )
    assert ok is False
    assert pillar == ""
    assert reason == "editor_note_or_staff_update"


def test_ap_relevance_requires_explicit_stress_signal():
    ok, pillar, reason = backfill._is_ap_relevant(
        "City Hall update from Sacramento, CA",
        "Routine meeting coverage with no household strain details.",
        ["housing_utility_pressure"],
    )
    assert ok is False
    assert pillar == ""
    assert reason == "no_household_or_system_strain"


def test_zero_entries_in_date_range_not_fetch_failure(monkeypatch, tmp_path: Path):
    registry = [
        {
            "source_id": "ca-nonprofit-policy-news",
            "source_name": "CA Nonprofit",
            "state": "CA",
            "active": True,
            "feed_validated_live": True,
            "ingest_ready": True,
            "rss_url": "https://example.com/feed.xml",
            "pressure_pillars": ["food_grocery_pressure"],
            "coverage_scope": "statewide",
        }
    ]
    data = b"""<?xml version='1.0'?><rss><channel><item><title>Food bank update</title><link>https://example.com/a</link><description>SNAP strain.</description><pubDate>Sat, 09 May 2026 10:00:00 +0000</pubDate></item></channel></rss>"""

    monkeypatch.setattr(backfill, "REGISTRY_PATH", tmp_path / "american_pressure_sources.json")
    monkeypatch.setattr(backfill, "OUT_SUMMARY", tmp_path / "backfill_summary.json")
    monkeypatch.setattr(backfill, "OUT_FETCH_DIAGNOSTICS", tmp_path / "backfill_fetch_diagnostics.json")
    monkeypatch.setattr(backfill, "OUT_SOURCES_ROOT", tmp_path / "sources")
    monkeypatch.setattr(backfill, "VALIDATION_REPORT_PATH", tmp_path / "report.json")
    (tmp_path / "report.json").write_text(json.dumps({"results": []}), encoding="utf-8")
    (tmp_path / "american_pressure_sources.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(backfill, "_fetch", lambda _url, context: (data, 200, "application/rss+xml"))

    summary = backfill.run_backfill("2026-05-10", "2026-05-16", 10, write=True)
    assert summary["accepted_ap_records"] == 0
    assert summary["top_rejection_reasons"].get("feed_ok_no_entries_in_window", 0) >= 1
    assert "feed_fetch_or_parse_failed" not in summary["top_rejection_reasons"]


def test_failed_fetch_writes_diagnostics_row(monkeypatch, tmp_path: Path):
    registry = [
        {
            "source_id": "ca-nonprofit-policy-news",
            "source_name": "CA Nonprofit",
            "state": "CA",
            "active": True,
            "feed_validated_live": True,
            "ingest_ready": True,
            "rss_url": "https://example.com/feed.xml",
            "pressure_pillars": ["food_grocery_pressure"],
            "coverage_scope": "statewide",
        }
    ]
    monkeypatch.setattr(backfill, "REGISTRY_PATH", tmp_path / "american_pressure_sources.json")
    monkeypatch.setattr(backfill, "OUT_SUMMARY", tmp_path / "backfill_summary.json")
    monkeypatch.setattr(backfill, "OUT_FETCH_DIAGNOSTICS", tmp_path / "backfill_fetch_diagnostics.json")
    monkeypatch.setattr(backfill, "OUT_SOURCES_ROOT", tmp_path / "sources")
    monkeypatch.setattr(backfill, "VALIDATION_REPORT_PATH", tmp_path / "report.json")
    (tmp_path / "report.json").write_text(json.dumps({"results": []}), encoding="utf-8")
    (tmp_path / "american_pressure_sources.json").write_text(json.dumps(registry), encoding="utf-8")

    def _boom(_url: str, context):
        raise TimeoutError("timed out")

    monkeypatch.setattr(backfill, "_fetch", _boom)
    summary = backfill.run_backfill("2026-05-10", "2026-05-16", 10, write=False)
    assert summary["top_rejection_reasons"].get("feed_fetch_or_parse_failed", 0) >= 1
    diag = json.loads((tmp_path / "backfill_fetch_diagnostics.json").read_text(encoding="utf-8"))
    assert diag and diag[0]["fetch_status"] == "feed_fetch_or_parse_failed"
    assert "TimeoutError" in diag[0]["error_message"]
    assert diag[0]["ssl_mode"] in {"certifi", "default", "insecure"}
    assert diag[0]["insecure_ssl_used"] in {True, False}


def test_backfill_writes_source_backed_records_and_summary(monkeypatch, tmp_path: Path):
    registry = [
        {
            "source_id": "ca-nonprofit-policy-news",
            "source_name": "CA Nonprofit",
            "state": "CA",
            "active": True,
            "feed_validated_live": True,
            "ingest_ready": True,
            "rss_url": "https://example.com/feed.xml",
            "pressure_pillars": ["food_grocery_pressure", "housing_utility_pressure"],
            "coverage_scope": "statewide",
        }
    ]
    data = b"""<?xml version='1.0'?>
    <rss><channel>
      <item><title>Food bank demand rises in Sacramento, CA</title><link>https://example.com/a?utm_source=x</link><description>SNAP delays and pantry strain.</description><pubDate>Sat, 16 May 2026 10:00:00 +0000</pubDate></item>
      <item><title>Food bank demand rises in Sacramento, CA</title><link>https://example.com/a</link><description>Duplicate syndicated copy.</description><pubDate>Sat, 16 May 2026 11:00:00 +0000</pubDate></item>
      <item><title>Local sports update</title><link>https://example.com/sports</link><description>Game recap only.</description><pubDate>Sat, 16 May 2026 12:00:00 +0000</pubDate></item>
    </channel></rss>
    """

    monkeypatch.setattr(backfill, "REGISTRY_PATH", tmp_path / "american_pressure_sources.json")
    monkeypatch.setattr(backfill, "OUT_SUMMARY", tmp_path / "backfill_summary.json")
    monkeypatch.setattr(backfill, "OUT_FETCH_DIAGNOSTICS", tmp_path / "backfill_fetch_diagnostics.json")
    monkeypatch.setattr(backfill, "OUT_SOURCES_ROOT", tmp_path / "sources")
    monkeypatch.setattr(backfill, "VALIDATION_REPORT_PATH", tmp_path / "report.json")
    (tmp_path / "report.json").write_text(json.dumps({"results": []}), encoding="utf-8")
    (tmp_path / "american_pressure_sources.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(backfill, "_fetch", lambda _url, context: (data, 200, "application/rss+xml"))

    summary = backfill.run_backfill("2026-05-10", "2026-05-16", 10, write=True)

    assert summary["sources_scanned"] == 1
    assert summary["feed_entries_scanned"] == 3
    assert summary["accepted_ap_records"] == 1
    assert summary["duplicate_count"] == 1
    assert summary["rejected_entries"] >= 2
    assert summary["accepted_by_state"]["CA"] == 1
    assert summary["accepted_by_pillar"]["food_pressure"] == 1

    out_file = tmp_path / "sources" / "2026-05-16" / "feed_backfill_sources.json"
    assert out_file.exists()
    rows = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(rows) == 1
    row = rows[0]
    assert row["url"].startswith("https://example.com/")
    assert row["location"] == "Sacramento, CA"
    assert row["location_precision"] == "city_state"
    assert "latitude" not in row and "longitude" not in row

    summary_path = tmp_path / "backfill_summary.json"
    assert summary_path.exists()


def test_washington_city_paper_editor_note_is_rejected(monkeypatch, tmp_path: Path):
    registry = [
        {
            "source_id": "dc-local-news",
            "source_name": "DC Local News",
            "state": "DC",
            "active": True,
            "feed_validated_live": True,
            "ingest_ready": True,
            "rss_url": "https://example.com/feed.xml",
            "pressure_pillars": ["jobs_paycheck_pressure"],
            "coverage_scope": "city",
        }
    ]
    data = b"""<?xml version='1.0'?>
    <rss><channel>
      <item><title>Editor's note: after eight years Mitch Ryals signs off</title><link>https://washingtoncitypaper.com/article/785994/editors-note-after-eight-years-mitch-ryals-signs-off/</link><description>Staff sign-off note.</description><pubDate>Sat, 16 May 2026 10:00:00 +0000</pubDate></item>
    </channel></rss>
    """
    monkeypatch.setattr(backfill, "REGISTRY_PATH", tmp_path / "american_pressure_sources.json")
    monkeypatch.setattr(backfill, "OUT_SUMMARY", tmp_path / "backfill_summary.json")
    monkeypatch.setattr(backfill, "OUT_FETCH_DIAGNOSTICS", tmp_path / "backfill_fetch_diagnostics.json")
    monkeypatch.setattr(backfill, "OUT_SOURCES_ROOT", tmp_path / "sources")
    monkeypatch.setattr(backfill, "VALIDATION_REPORT_PATH", tmp_path / "report.json")
    (tmp_path / "report.json").write_text(json.dumps({"results": []}), encoding="utf-8")
    (tmp_path / "american_pressure_sources.json").write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(backfill, "_fetch", lambda _url, context: (data, 200, "application/rss+xml"))

    summary = backfill.run_backfill("2026-05-10", "2026-05-16", 10, write=True)
    assert summary["accepted_ap_records"] == 0
    assert summary["top_rejection_reasons"].get("editor_note_or_staff_update", 0) >= 1
    out_file = tmp_path / "sources" / "2026-05-16" / "feed_backfill_sources.json"
    if out_file.exists():
        rows = json.loads(out_file.read_text(encoding="utf-8"))
        assert not any("washingtoncitypaper.com/article/785994" in row.get("url", "") for row in rows)
