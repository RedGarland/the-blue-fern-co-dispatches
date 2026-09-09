from __future__ import annotations

from pathlib import Path

from bluefern_dispatches.ice_dispatch import (
    FetchResult,
    collect_live_candidates,
    fetch_url_secure,
    run_diagnostic,
)


def source(**overrides):
    data = {
        "source_id": "ice-newsroom",
        "publisher": "U.S. Immigration and Customs Enforcement",
        "source_type": "official_agency_newsroom",
        "url": "https://www.ice.gov/newsroom",
        "enabled": True,
        "tier": 1,
        "geography": "US_and_territories",
        "categories": ["enforcement"],
        "collection_mechanism": "html",
        "expected_cadence": "daily_monitor",
    }
    data.update(overrides)
    return data


def test_live_collection_extracts_traceable_candidate(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if url.endswith("/newsroom"):
            return FetchResult(url, True, 200, '<a href="/news/releases/ice-arrests-12-texas">ICE arrests 12 in Texas enforcement operation</a><a href="/news/releases/cbp-border-only">CBP reports border seizure</a>', None, "test")
        return FetchResult(url, True, 200, "<p>ICE said 12 people were arrested in Texas during an enforcement operation.</p>", None, "test")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, fetches = collect_live_candidates([source()], max_per_source=2)
    assert len(raw) == 1
    assert raw[0]["sources"][0]["source_url"].endswith("ice-arrests-12-texas")
    assert raw[0]["impact"]["arrests_count"] == 12
    assert raw[0]["location"]["state_or_territory"] == "TX"
    assert health[0].accepted_records == 1
    assert len(fetches) == 2


def test_live_collection_does_not_match_ice_inside_police(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if url.endswith("/newsroom"):
            return FetchResult(url, True, 200, '<a href="/news/local-police-report">Local police report robbery arrest</a>', None, "test")
        raise AssertionError("article fetch should not run for a non-ICE title")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, fetches = collect_live_candidates([source()], max_per_source=2)
    assert raw == []
    assert exclusions == []
    assert health[0].accepted_records == 0
    assert len(fetches) == 1


def test_live_collection_excludes_static_or_non_article_links_before_fetch(monkeypatch):
    fetched = []
    def fake_fetch(url: str, *, timeout: float = 15.0):
        fetched.append(url)
        if url.endswith("/newsroom"):
            return FetchResult(url, True, 200, '<a href="/topics/eow">ICE Fallen Officers</a><a href="tel:1-866-347-2423">1-866-DHS-2-ICE</a>', None, "test")
        raise AssertionError("static links should be excluded before article fetch")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, fetches = collect_live_candidates([source()], max_per_source=2)
    assert raw == []
    assert [item["reason"] for item in exclusions] == ["non_article_or_static_link", "non_article_or_static_link"]
    assert fetched == ["https://www.ice.gov/newsroom"]
    assert len(fetches) == 1


def test_live_collection_requires_event_level_ice_signal(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if url.endswith("/newsroom"):
            return FetchResult(url, True, 200, '<a href="/news/releases/ice-overview">ICE overview and history</a>', None, "test")
        return FetchResult(url, True, 200, "<p>ICE is an agency within the Department of Homeland Security.</p>", None, "test")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, fetches = collect_live_candidates([source()], max_per_source=2)
    assert raw == []
    assert exclusions[0]["reason"] == "no_event_level_ice_signal"
    assert health[0].accepted_records == 0
    assert len(fetches) == 2


def test_live_collection_retains_ice_shooting_accountability_event(monkeypatch):
    def fake_fetch(url: str, *, timeout: float = 15.0):
        if url.endswith("/newsroom"):
            return FetchResult(url, True, 200, '<a href="/article/ice-officer-shooting">ICE officer charged in Minnesota shooting released on bond</a>', None, "test")
        return FetchResult(url, True, 200, "<p>An ICE officer charged after a Minnesota shooting was released on bond, according to court records.</p>", None, "test")
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", fake_fetch)
    raw, exclusions, health, fetches = collect_live_candidates([source()], max_per_source=2)
    assert exclusions == []
    assert len(raw) == 1
    assert raw[0]["primary_category"] == "use_of_force"
    assert health[0].accepted_records == 1
    assert len(fetches) == 2


def test_live_collection_isolates_provider_fetch_failure(monkeypatch):
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.fetch_url_secure", lambda url, timeout=15.0: FetchResult(url, False, 503, "", "service unavailable", "test"))
    raw, exclusions, health, fetches = collect_live_candidates([source()], max_per_source=2)
    assert raw == []
    assert exclusions == []
    assert health[0].attempted is True
    assert health[0].success is False
    assert health[0].http_status == 503
    assert fetches[0]["error"] == "service unavailable"


def test_windows_fetch_fallback_keeps_tls_verification(monkeypatch):
    def failing_urlopen(*args, **kwargs):
        raise OSError("certificate verify failed")
    class Completed:
        stdout = '{"ok":true,"status":200,"url":"https://example.test/","content":"<html>ICE</html>","error":null}'
    calls = []
    kwargs_seen = []
    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)
    monkeypatch.setattr("subprocess.run", lambda args, **kwargs: calls.append(args) or kwargs_seen.append(kwargs) or Completed())
    result = fetch_url_secure("https://example.test/")
    assert result.ok is True
    assert result.fetch_stack == "windows_invoke_webrequest_after_urllib_failure"
    joined = " ".join(calls[0])
    assert "Invoke-WebRequest" in joined
    assert "verify=False" not in joined
    assert kwargs_seen[0]["env"]["BLUEFERN_ICE_FETCH_URL"] == "https://example.test/"
    assert kwargs_seen[0]["env"]["BLUEFERN_ICE_FETCH_TIMEOUT"] == "15"


def test_live_diagnostic_writes_only_explicit_output_dir(tmp_path, monkeypatch):
    def fake_collect(sources, *, max_per_source=3, window_hours=72):
        return [], [], [], []
    monkeypatch.setattr("bluefern_dispatches.ice_dispatch.collect_live_candidates", fake_collect)
    persistent = Path("output/test-runs")
    before = sorted(p.relative_to(persistent) for p in persistent.rglob("*") if persistent.exists())
    result = run_diagnostic(Path("data/dispatches/ice/sources.yml"), live=True)
    assert result["public_side_effects"] is False
    assert sorted(p.relative_to(persistent) for p in persistent.rglob("*") if persistent.exists()) == before
    out = tmp_path / "ice-live"
    run_diagnostic(Path("data/dispatches/ice/sources.yml"), live=True, output_dir=out)
    assert (out / "fetch_results.json").exists()
    assert sorted(p.relative_to(persistent) for p in persistent.rglob("*") if persistent.exists()) == before
