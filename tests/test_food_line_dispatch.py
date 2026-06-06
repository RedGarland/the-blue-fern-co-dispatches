import csv
import base64
import json
import re
import ssl
import sys
import types
import urllib.error
from datetime import date as dt_date
from pathlib import Path

import pytest

import scripts.check_food_line_blue_fern_compliance as food_line_compliance
import scripts.run_food_line_dispatch as food_line
import scripts.test_food_line_tts as food_line_tts
import bluefern_dispatches.tts_provider as tts_provider
from scripts.discover_food_line_sources import discover_food_line_sources, load_food_line_source_discovery_queries
from scripts.run_food_line_dispatch import run_food_line_dispatch
from scripts.test_food_line_candidate_sources import cleanup_food_line_candidates, import_food_line_candidate_intake, test_food_line_candidate_sources as run_food_line_candidate_sources
from bluefern_dispatches.food_line_sources import GENERIC_PRESSURE_SUMMARIES, load_food_line_candidate_registry, load_food_line_registry
from bluefern_dispatches.tts_provider import TTSResult, TTSDiagnostics


@pytest.fixture(autouse=True)
def _food_line_suite_today(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 30))


def _ensure_assets(root: Path) -> None:
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    repo_assets = Path(__file__).parent.parent / "assets"
    for asset_name in (
        "bluefern.png",
        "food-line-logo.png",
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


def _manual_path(root: Path, date: str) -> Path:
    return root / "data" / "dispatches" / "food-line" / "sources" / date / "manual_sources.json"


def _clear_food_line_registries(root: Path) -> None:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "source_registry.json").write_text("[]", encoding="utf-8")
    (registry_dir / "pressure_source_registry.json").write_text("[]", encoding="utf-8")


def _row(
    i: int,
    family: str = "federal_official",
    state: str = "CA",
    *,
    title: str | None = None,
    summary: str | None = None,
    source_type: str = "manual",
    publisher: str = "Example News",
) -> dict:
    return {
        "source_record_id": f"food-line-src-{i:03d}",
        "title": title or f"Source {i}",
        "url": f"https://example.com/{i}",
        "publisher": publisher,
        "published_at": "2026-06-01T12:00:00Z",
        "retrieved_at": "2026-06-01T13:00:00Z",
        "summary_or_snippet": summary or "Local pantry reports elevated demand.",
        "source_type": source_type,
        "source_family": family,
        "state": state,
        "issue_tags": ["food banks / pantry capacity", "household hardship signal"],
        "map_category": "elevated demand",
        "location_name": "Sacramento",
    }


def _pressure_row(i: int, title: str, summary: str, *, family: str, state: str = "US", source_type: str = "rss") -> dict:
    row = _row(i, family=family, state=state, title=title, summary=summary, source_type=source_type)
    row["issue_tags"] = []
    row["map_category"] = "context / monitoring only"
    row["extraction_quality"] = "high"
    row["expected_text_basis"] = "rss_summary"
    row["pressure_verification_required"] = True
    return row


def _write_pressure_registry(root: Path, payload: list[dict]) -> Path:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    source_registry_path = registry_dir / "source_registry.json"
    if not source_registry_path.exists():
        source_registry_path.write_text("[]", encoding="utf-8")
    path = registry_dir / "pressure_source_registry.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_source_registry(root: Path, payload: list[dict]) -> Path:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / "source_registry.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_candidate_registry(root: Path, payload: list[dict]) -> Path:
    registry_dir = root / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / "candidate_source_registry.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _http_urls(text: str) -> list[str]:
    urls = {
        url
        for url in re.findall(r'href="(https?://[^"]+)"', text)
        if "dispatches.thebluefernco.com" not in url and "thebluefernco.com" not in url
    }
    return sorted(urls)


def _write_intake_csv(root: Path, rows: list[dict[str, str]]) -> Path:
    path = root / "data" / "dispatches" / "food-line" / "candidate_source_intake_template.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_id",
        "source_name",
        "publisher",
        "candidate_url",
        "source_family",
        "source_type",
        "state",
        "location_name",
        "location_scope",
        "candidate_reason",
        "expected_text_basis",
        "extraction_quality_guess",
        "pressure_topics_expected",
        "status",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _mock_food_line_tts(monkeypatch: pytest.MonkeyPatch, *, audio_bytes: bytes = b"fake-mp3-data") -> None:
    monkeypatch.setattr(
        food_line,
        "synthesize_speech_with_diagnostics",
        lambda **kwargs: (
            TTSResult(True, audio_bytes, "openai", kwargs.get("model"), kwargs.get("voice"), kwargs.get("audio_format"), None),
            TTSDiagnostics(
                provider="openai",
                model_requested=kwargs.get("model"),
                voice_requested=kwargs.get("voice"),
                narration_char_count=len(str(kwargs.get("text") or "")),
                output_path_attempted=str(kwargs.get("output_path") or ""),
                api_key_present=True,
                output_dir_exists=True,
                partial_mp3_exists=False,
                elapsed_seconds=0.01,
                exception_type=None,
                exception_message_sanitized=None,
                timeout_seconds=float(kwargs.get("timeout") or 90.0),
                audio_format=kwargs.get("audio_format"),
                tls_verify=True,
                ca_file_used=None,
                ca_source="system_default",
                truststore_requested=False,
                truststore_available=False,
                ssl_cert_file_env=None,
                requests_ca_bundle_env=None,
                bluefern_tts_ca_file_env=None,
                tls_workaround_warning=None,
            ),
        ),
    )


def _mock_food_line_tts_failure(monkeypatch: pytest.MonkeyPatch, *, exc: BaseException | None = None) -> None:
    exc = exc or TimeoutError("openai request timed out for sk-test-123")

    def fake_tts(**kwargs):
        diag = TTSDiagnostics(
            provider="openai",
            model_requested=kwargs.get("model"),
            voice_requested=kwargs.get("voice"),
            narration_char_count=len(str(kwargs.get("text") or "")),
            output_path_attempted=str(kwargs.get("output_path") or ""),
            api_key_present=True,
            output_dir_exists=True,
            partial_mp3_exists=False,
            elapsed_seconds=1.23,
            exception_type=exc.__class__.__name__,
            exception_message_sanitized="openai request timed out for [redacted-api-key]",
            timeout_seconds=float(kwargs.get("timeout") or 90.0),
            audio_format=kwargs.get("audio_format"),
            tls_verify=True,
            ca_file_used=None,
            ca_source="system_default",
            truststore_requested=False,
            truststore_available=False,
            ssl_cert_file_env=None,
            requests_ca_bundle_env=None,
            bluefern_tts_ca_file_env=None,
            tls_workaround_warning=None,
        )
        return TTSResult(False, None, "openai", kwargs.get("model"), kwargs.get("voice"), kwargs.get("audio_format"), "openai_tts_request_failed"), diag

    monkeypatch.setattr(food_line, "synthesize_speech_with_diagnostics", fake_tts)


def _seed_existing_food_line_audio(tmp_path: Path, date: str, data: bytes = b"existing-mp3-data") -> Path:
    audio_path = tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(data)
    return audio_path


def _rss_payload(items: list[dict[str, str]]) -> bytes:
    body = []
    for item in items:
        body.append(
            "<item>"
            f"<title>{item['title']}</title>"
            f"<link>{item['link']}</link>"
            f"<pubDate>{item.get('pubDate', 'Mon, 03 Jun 2026 12:00:00 GMT')}</pubDate>"
            f"<description>{item['description']}</description>"
            "</item>"
        )
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<rss version=\"2.0\"><channel>"
            + "".join(body)
            + "</channel></rss>").encode("utf-8")


def test_food_line_raw_list_loads_and_generates_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_row(1), _row(2, "state_official", "OR"), _row(3, "food_bank_provider", "WA")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] > 0


def test_food_line_sources_wrapper_loads_and_generates_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": [_row(1), _row(2, "state_official", "OR"), _row(3, "policy_research", "WA")]}, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] > 0


def test_food_line_utf8_bom_manual_sources_parse(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"sources": [_row(1), _row(2, "state_official", "OR"), _row(3, "policy_research", "WA")]}, indent=2)
    path.write_text(payload, encoding="utf-8-sig")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] == 3


def test_food_line_malformed_json_fails_clearly(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        run_food_line_dispatch(tmp_path, date)


def test_food_line_rejected_records_report_useful_reasons(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = _row(1)
    bad = {"id": "x", "title": "Missing url and more"}
    path.write_text(json.dumps({"sources": [good, bad]}, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] == 1
    assert result["rejected_source_records"]
    reasons = " | ".join(result["rejected_source_records"][0]["reasons"])
    assert "missing required field" in reasons or "invalid field type" in reasons


def test_food_line_alias_fields_load_map_and_source_table(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    alias_rows = [
        {
            "id": "a1",
            "title": "Alias Source One",
            "source_url": "https://example.com/alias-one",
            "publisher": "Alias Pub",
            "published_date": "2026-06-01",
            "summary": "Food bank demand increased and pantry lines grew.",
            "family": "federal_official",
            "state": "US",
            "tags": ["SNAP / benefits"],
            "signal_category": "benefit disruption",
            "location": "United States",
        },
        {
            "id": "a2",
            "title": "Alias Source Two",
            "source_url": "https://example.com/alias-two",
            "publisher": "Alias Pub",
            "published_date": "2026-06-01",
            "text": "Food bank demand increased and pantry lines grew.",
            "family": "food_bank_provider",
            "state": "US",
            "tags": ["food banks / pantry capacity"],
            "category": "elevated demand",
            "location": "United States",
        },
    ]
    path.write_text(json.dumps(alias_rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    assert result["source_count"] == 2
    map_payload = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert len(map_payload["markers"]) == 2
    source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Alias Source Two" in source_table
    assert "https://example.com/alias-two" in source_table
    assert "Alias Source One" not in source_table
    assert "https://example.com/alias-one" not in source_table


def test_food_line_map_page_is_interactive_and_not_placeholder(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sources": [
                    _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX"),
                    _row(2, "state_official", "OR"),
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    assert "Food Line Pressure Map" in map_html
    assert "leaflet" in map_html.lower()
    assert "map_data.json" in map_html
    assert "Latest mapped signals for 2026-06-01" not in map_html
    assert "<strong>What happened:</strong>" in map_html
    assert "<strong>Included in briefing:</strong>" in map_html
    assert "<div><strong>Category:</strong>" not in map_html
    assert "<div><strong>Issue tags:</strong>" not in map_html
    assert "<strong>How it was used:</strong>" in map_html
    assert "<strong>Source:</strong>" in map_html
    assert "<strong>Dispatch date:</strong>" in map_html
    assert "<strong>Coordinate basis:</strong>" in map_html


def test_food_line_map_legend_includes_required_categories(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"sources": [_pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")]},
            indent=2,
        ),
        encoding="utf-8",
    )
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    for label in (
        "acute strain / service disruption",
        "elevated demand",
        "summer meal / child nutrition",
        "senior hunger",
        "rural access",
        "benefit disruption",
        "context / monitoring only",
    ):
        assert label in map_html


def test_food_line_unknown_state_marker_is_skipped_with_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    path = _manual_path(tmp_path, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    bad = _row(1)
    bad["state"] = "XX"
    bad["location_name"] = "Unknown Place"
    bad["summary_or_snippet"] = "Pantry reduced hours and demand increase reported."
    bad["issue_tags"] = ["food banks", "pantry capacity", "service access"]
    path.write_text(json.dumps({"sources": [bad]}, indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    diagnostics = map_data.get("diagnostics") or {}
    assert diagnostics.get("marker_count") == 1
    assert diagnostics.get("plotted_marker_count") == 0
    assert diagnostics.get("skipped_marker_count") == 1
    skipped = diagnostics.get("skipped_markers") or []
    assert skipped and skipped[0].get("reason") == "missing_coordinates_and_no_supported_state_fallback"


def test_food_line_collect_writes_auto_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    _manual_path(tmp_path, date).parent.mkdir(parents=True, exist_ok=True)
    _manual_path(tmp_path, date).write_text("[]", encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date, collect=True)
    assert result["ok"] is True
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    assert auto_path.exists()
    payload = json.loads(auto_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)


def test_food_line_collect_stays_local_when_repo_registry_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    called = False

    def fail_urlopen(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be reached in test mode")

    monkeypatch.setattr("bluefern_dispatches.food_line_sources.urllib.request.urlopen", fail_urlopen)

    result = food_line.collect_food_line_auto_sources(tmp_path, date)

    assert result["ok"] is True
    assert result["source_count"] == 0
    assert called is False
    auto_path = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    assert auto_path.exists()
    assert json.loads(auto_path.read_text(encoding="utf-8")) == []


def test_food_line_merges_auto_and_manual_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    base = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    base.mkdir(parents=True, exist_ok=True)
    (base / "manual_sources.json").write_text(json.dumps([_row(1)], indent=2), encoding="utf-8")
    auto = _row(2, family="economic_data", state="US")
    auto["source_type"] = "auto"
    (base / "auto_sources.json").write_text(json.dumps([auto], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_count"] == 2


def test_food_line_manual_wins_on_duplicate_url_or_title(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    base = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    base.mkdir(parents=True, exist_ok=True)
    manual = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    auto = dict(_pressure_row(2, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX"))
    auto["source_type"] = "auto"
    auto["url"] = manual["url"]
    auto["title"] = manual["title"]
    auto["summary_or_snippet"] = "AUTO"
    (base / "manual_sources.json").write_text(json.dumps([manual], indent=2), encoding="utf-8")
    (base / "auto_sources.json").write_text(json.dumps([auto], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_count"] == 1
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["summary_or_snippet"] != "AUTO"


def test_food_line_collector_failure_does_not_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_row(1)], indent=2), encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(food_line, "collect_food_line_auto_sources", _boom)
    result = run_food_line_dispatch(tmp_path, date, collect=True)
    assert result["ok"] is True
    assert result["collector_result"]["ok"] is False
    assert "network unavailable" in result["collector_result"]["failed_sources"][0]["reason"]


def test_food_line_collect_classification_and_national_default(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[]", encoding="utf-8")
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "collect-source-1",
                "source_name": "Collect Source One",
                "publisher": "Example Pub",
                "url": "https://example.com/food-line-feed.rss",
                "source_family": "local_news",
                "source_type": "rss",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "enabled": True,
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])
    rss_payload = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand is rising and pantry lines grew.</description></item></channel></rss>"""

    result = run_food_line_dispatch(
        tmp_path,
        date,
        collect=True,
        collect_fetcher=lambda _url, timeout=15: rss_payload,
    )
    assert result["ok"] is True
    payload = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json").read_text(encoding="utf-8"))
    assert payload
    row = payload[0]
    assert isinstance(row.get("issue_tags"), list) and row["issue_tags"]
    assert row.get("map_category")
    assert row.get("location_name") == "United States"
    assert row.get("state") == "US"


def test_food_line_source_table_and_map_include_auto_sources(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-01"
    base = tmp_path / "data" / "dispatches" / "food-line" / "sources" / date
    base.mkdir(parents=True, exist_ok=True)
    (base / "manual_sources.json").write_text("[]", encoding="utf-8")
    (base / "auto_sources.json").write_text(
        json.dumps(
            [
                {
                    "source_record_id": "auto-1",
                    "title": "Auto Source Title",
                    "url": "https://example.com/auto-1",
                    "publisher": "Auto Pub",
                    "published_at": "2026-06-01T00:00:00Z",
                    "retrieved_at": "2026-06-01T00:00:00Z",
                    "summary_or_snippet": "Food bank demand increased and pantry lines grew.",
                    "source_type": "auto",
                    "source_family": "food_bank_provider",
                    "state": "CA",
                    "issue_tags": ["food banks"],
                    "map_category": "elevated demand",
                    "location_name": "Los Angeles",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    result = run_food_line_dispatch(tmp_path, date)
    assert result["ok"] is True
    table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Auto Source Title" in table
    assert "https://example.com/auto-1" in table
    assert "How it was used" in table
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert any(marker.get("source_title") == "Auto Source Title" for marker in map_data.get("markers") or [])


def test_food_line_lead_prefers_concrete_daily_signal_over_background(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    background = _row(1, family="economic_data", state="US")
    background["map_category"] = "context / monitoring only"
    background["title"] = "USDA context page"
    background["summary_or_snippet"] = "National context statistics."
    concrete = _row(2, family="food_bank_provider", state="CA")
    concrete["map_category"] = "elevated demand"
    concrete["summary_or_snippet"] = "Pantry wait times increased and one site reduced hours."
    p.write_text(json.dumps([background, concrete], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["lead_source_record_id"] == concrete["source_record_id"]


def test_food_line_background_only_becomes_monitoring_context(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(1, 4):
        row = _row(i, family="economic_data", state="US")
        row["map_category"] = "context / monitoring only"
        row["issue_tags"] = ["household food insecurity"]
        rows.append(row)
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["editorial_status"] == "monitoring/context"
    assert result["source_adequacy"]["label"] == "Monitoring/context edition"


def test_food_line_what_changed_does_not_overstate_novelty_on_context_day(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _row(1, family="economic_data", state="US")
    row["map_category"] = "context / monitoring only"
    p.write_text(json.dumps([row, dict(row, source_record_id="food-line-src-002", url="https://example.com/2"), dict(row, source_record_id="food-line-src-003", url="https://example.com/3")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    review_manifest = tmp_path / "output" / "review" / "food-line" / date / "run_manifest.json"
    data_manifest = tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json"
    assert result["public_rendered"] is False
    assert result["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert not site_edition.exists()
    assert review_manifest.exists()
    assert data_manifest.exists()
    manifest = json.loads(data_manifest.read_text(encoding="utf-8"))
    assert manifest["public_rendered"] is False
    assert manifest["qualified_primary_count"] == 0
    assert manifest["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    return
    run_food_line_dispatch(tmp_path, date)
    html_text = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "Today’s Read" in html_text
    assert "No new primary pressure signal qualified today." in html_text
    assert "What changed today" not in html_text


def test_food_line_2026_06_13_kltv_excerpt_is_cleaned_before_rendering():
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    rows = json.loads(payload_path.read_text(encoding="utf-8"))
    lead = next(row for row in rows if row.get("pressure_signal"))
    excerpt = food_line.clean_food_line_public_evidence_excerpt(str(lead.get("evidence_text") or ""), title=str(lead.get("title") or ""))

    assert excerpt != food_line.FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    assert "Skip to content" not in excerpt
    assert "Advertise With Us" not in excerpt
    assert "Watch Live" not in excerpt
    assert "Weather Extra" not in excerpt
    assert "Reception Issues" not in excerpt
    assert "Pet Project" not in excerpt
    assert "food assistance" in excerpt.lower()
    assert "17% increase" in excerpt


def test_food_line_2026_06_05_publishes_new_primary_and_records_freshness_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    review_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    review_rows = list(csv.DictReader(review_path.open(encoding="utf-8")))
    kltv = next(row for row in review_rows if row["source_record_id"] == "food-line-auto-c531de22a923a8d8")
    lead = next(row for row in review_rows if row["source_record_id"] == "food-line-auto-9013087c4ebc5f32")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["skip_reason"] == ""
    assert result["primary_disqualification_reason"] == ""
    assert result["lead_source_record_id"] == "food-line-auto-9013087c4ebc5f32"
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["selected_lead_pressure_type"] == "demand strain"
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-05/"
    assert kltv["pressure_signal"] == "true"
    assert kltv["pressure_verification_status"] == "source_text_verified"
    assert kltv["source_published_date"] == "2026-06-05"
    assert kltv["freshness_status"] == "fresh_daily_signal"
    assert kltv["primary_eligible"] == "true"
    assert kltv["primary_disqualification_reason"] == ""
    assert lead["source_title"].startswith("Local food pantries are preparing for increased demand")
    assert lead["location_name"] == "Toledo"
    assert lead["pressure_signal"] == "true"
    assert lead["pressure_verification_status"] == "source_text_verified"
    assert lead["source_published_date"] == "2026-06-05"
    assert lead["freshness_status"] == "fresh_daily_signal"
    assert lead["primary_eligible"] == "true"
    assert lead["primary_disqualification_reason"] == ""
    assert "editions/2026-06-05/" in archive_html
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Main Food Access Story" in edition_html
    assert "What Else We’re Watching" in edition_html
    assert "Context and Watch Items" not in edition_html
    assert "Sources Behind This Briefing" in edition_html
    assert "Source Mix" not in edition_html
    assert "source mix" not in edition_html.lower()
    assert "Source Note" in edition_html
    assert "What happened" in edition_html
    assert "Why it matters" in edition_html
    assert "Read the source" in edition_html
    assert "Skip to main content" not in edition_html
    assert "Here’s how you know" not in edition_html
    assert "Here&#x27;s how you know" not in edition_html
    assert "Secure .gov websites use HTTPS" not in edition_html
    assert ".gov website belongs" not in edition_html
    assert "Today’s pressure point" not in edition_html
    assert "What changed" not in edition_html
    assert "Who is exposed" not in edition_html
    assert "Field signals" not in edition_html
    assert "Where pressure is visible" not in edition_html
    assert "Map notes" not in edition_html
    assert "What to watch tomorrow" not in edition_html


def test_food_line_stale_future_edition_folders_are_pruned_from_public_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 5))

    stale_date = "2026-06-12"
    stale_dir = tmp_path / "output" / "site" / "food-line" / "editions" / stale_date
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "index.html").write_text("<html><body>stale</body></html>", encoding="utf-8")
    (stale_dir / "edition_manifest.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": stale_date,
                "public_rendered": True,
                "qualified_primary_count": 1,
                "skip_reason": "",
                "future_date_blocked": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (stale_dir / "sources_manifest.json").write_text("[]", encoding="utf-8")
    (stale_dir / "curation_manifest.json").write_text("[]", encoding="utf-8")
    audio_root = tmp_path / "output" / "site" / "food-line" / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    (audio_root / f"{stale_date}.json").write_text(
        json.dumps(
            {
                "dispatch_slug": "food-line",
                "edition_date": stale_date,
                "transcript_url": f"https://dispatches.thebluefernco.com/food-line/audio/{stale_date}-transcript.html",
                "audio_available": True,
                "audio_file": f"{stale_date}.mp3",
                "audio_url": f"/food-line/audio/{stale_date}.mp3",
                "audio_mime_type": "audio/mpeg",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (audio_root / f"{stale_date}-transcript.html").write_text("<html>stale transcript</html>", encoding="utf-8")
    (audio_root / f"{stale_date}.mp3").write_bytes(b"stale-mp3")

    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert not stale_dir.exists()
    assert not (audio_root / f"{stale_date}.json").exists()
    assert not (audio_root / f"{stale_date}-transcript.html").exists()
    assert not (audio_root / f"{stale_date}.mp3").exists()
    assert "editions/2026-06-05/" in archive_html
    assert "editions/2026-06-12/" not in archive_html
    assert stale_date not in podcast


def test_food_line_2026_06_13_is_blocked_by_default_without_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 5))

    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_feed = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is False
    assert result["future_date_blocked"] is True
    assert result["future_date_override_used"] is False
    assert result["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."
    assert result["bluesky_post_ready"] is False
    assert result["bluesky_post_text"] is None
    assert result["qualified_primary_count"] == 0
    assert site_edition.exists() is False
    assert "2026-06-13" not in archive_html
    assert "2026-06-13" not in audio_index
    assert "2026-06-13" not in podcast_feed
    assert manifest["public_rendered"] is False
    assert manifest["future_date_blocked"] is True
    assert manifest["future_date_override_used"] is False
    assert manifest["qualified_primary_count"] == 0
    assert manifest["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."


def test_food_line_2026_06_12_is_blocked_by_default_without_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-12" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 5))

    date = "2026-06-12"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    archive_html = (tmp_path / "output" / "site" / "food-line" / "archive.html").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    podcast_feed = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is False
    assert result["future_date_blocked"] is True
    assert result["future_date_override_used"] is False
    assert result["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."
    assert result["bluesky_post_ready"] is False
    assert result["bluesky_post_text"] is None
    assert result["qualified_primary_count"] == 0
    assert site_edition.exists() is False
    assert "2026-06-12" not in archive_html
    assert "2026-06-12" not in audio_index
    assert "2026-06-12" not in podcast_feed
    assert manifest["public_rendered"] is False
    assert manifest["future_date_blocked"] is True
    assert manifest["future_date_override_used"] is False
    assert manifest["qualified_primary_count"] == 0
    assert manifest["skip_reason"] == "Future-dated Food Line public editions are blocked unless explicitly allowed."


def test_food_line_2026_06_13_can_publish_when_future_override_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(food_line, "_food_line_local_today", lambda: dt_date(2026, 6, 5))

    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date, allow_future_date=True)

    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date
    manifest = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "editions" / date / "run_manifest.json").read_text(encoding="utf-8"))

    assert result["public_rendered"] is True
    assert result["future_date_blocked"] is False
    assert result["future_date_override_used"] is True
    assert result["skip_reason"] == ""
    assert result["bluesky_post_ready"] is True
    assert result["qualified_primary_count"] == 1
    assert site_edition.exists()
    assert manifest["public_rendered"] is True
    assert manifest["future_date_blocked"] is False
    assert manifest["future_date_override_used"] is True
    assert manifest["bluesky_post_ready"] is True


def test_food_line_podcast_description_varies_by_pressure_summary(tmp_path: Path):
    _ensure_assets(tmp_path)
    date_a = "2026-06-02"
    pa = _manual_path(tmp_path, date_a)
    pa.parent.mkdir(parents=True, exist_ok=True)
    a = _pressure_row(1, "Food bank demand rises", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    pa.write_text(json.dumps([a], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date_a)
    desc_a = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date_a}.json").read_text(encoding="utf-8"))["episode_summary"]
    date_b = "2026-06-03"
    pb = _manual_path(tmp_path, date_b)
    pb.parent.mkdir(parents=True, exist_ok=True)
    b = _pressure_row(2, "SNAP benefits delayed", "SNAP benefit delays are disrupting households and service centers.", family="local_news", state="TX")
    pb.write_text(json.dumps([b], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date_b)
    desc_b = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date_b}.json").read_text(encoding="utf-8"))["episode_summary"]
    assert desc_a != desc_b
    assert "Publishing note:" in desc_a
    assert "Publishing note:" in desc_b
    assert "Review summary:" not in desc_a
    assert "Review summary:" not in desc_b
    assert "Accountability note:" not in desc_a
    assert "Accountability note:" not in desc_b
    assert "matched terms" not in desc_a.lower()
    assert "matched terms" not in desc_b.lower()


def test_food_line_audio_generation_writes_clean_metadata_and_enclosure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(
        1,
        "Food bank sees rising demand from families",
        "Food bank demand increased and pantry lines grew. KLTV reported that food banks across Texas were working to keep up, and one East Texas pantry operator described a 17 percent increase over three weeks in people asking for food assistance. Skip to content Advertise With Us Weather Sports Contests Closings & Delays",
        family="local_news",
        state="TX",
    )
    row["source_name"] = "KLTV"
    row["publisher"] = "KLTV"
    row["location_name"] = "East Texas"
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")
    transcript = (tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html").read_text(encoding="utf-8")
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is True
    assert result["audio_required"] is False
    assert result["podcast_enclosure_present"] is True
    assert result["audio_mp3_url"] == "/food-line/audio/2026-06-04.mp3"
    assert Path(result["audio_mp3_path"]).exists()
    assert result["audio_story_section_count"] >= 4
    assert result["audio_story_sections"]
    assert audio_json["episode_title"] == "Food Line Dispatch - June 4, 2026"
    assert audio_json["audio_story_section_count"] >= 4
    assert audio_json["audio_story_sections"]
    assert audio_json["episode_summary"].startswith("KLTV reported")
    assert "17 percent increase" in audio_json["episode_summary"]
    assert "Publishing note: This dispatch is based on one public Food Line source record from KLTV." in audio_json["episode_summary"]
    assert "Today's pressure point:" not in audio_json["script_text"]
    assert "This local food-access story matters" in audio_json["script_text"]
    assert "Publishing note:" not in audio_json["script_text"]
    assert "Source note:" not in audio_json["script_text"]
    assert "Edition status: Daily edition" not in audio_json["script_text"]
    assert "Where pressure is visible" not in audio_json["script_text"]
    assert "For traceability" not in audio_json["script_text"]
    assert "Accountability note:" not in audio_json["script_text"]
    assert "Review summary:" not in audio_json["script_text"]
    assert "matched terms" not in audio_json["script_text"].lower()
    assert "the verified record came from" not in audio_json["script_text"].lower()
    assert "Skip to content" not in audio_json["script_text"]
    assert "Advertise With Us" not in audio_json["script_text"]
    assert transcript.index("Opening") < transcript.index("Today&apos;s Read")
    assert transcript.index("Today&apos;s Read") < transcript.index("Main Food Access Story")
    assert transcript.index("Main Food Access Story") < transcript.index("Sources Behind This Briefing")
    assert transcript.index("Sources Behind This Briefing") < transcript.index("Transcript and source links")
    assert "Review summary:" not in transcript
    assert "17 percent increase" in transcript
    assert "Edition status: Daily edition" not in transcript
    assert "Where pressure is visible" not in transcript
    assert "For traceability" not in transcript
    assert "matched terms" not in transcript.lower()
    assert "the verified record came from" not in transcript.lower()
    assert "Review summary:" not in transcript.split("Publishing note", 1)[0]
    assert "Review summary:" not in audio_index.split("Publishing note", 1)[0]
    assert audio_index.index("Opening") < audio_index.index("Today&apos;s Read")
    assert audio_index.index("Today&apos;s Read") < audio_index.index("Main Food Access Story")
    assert "17 percent increase" in audio_index
    assert "Sources Behind This Briefing" in audio_index
    assert "Where pressure is visible" not in audio_index
    assert "For traceability" not in audio_index
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["selected_lead_pressure_scope_text"] == "local/operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert "<enclosure " in podcast
    assert "Review summary:" not in podcast
    assert "matched terms" not in podcast.lower()
    assert "the verified record came from" not in podcast.lower()
    assert "Edition status: Daily edition" not in podcast
    assert "Where pressure is visible" not in podcast
    assert podcast.index("KLTV reported") < podcast.index("Publishing note:")
    assert 'src="/food-line/audio/2026-06-04.mp3"' in audio_index
    assert "<strong>Podcast enclosure:</strong> present" in audio_index
    assert "<strong>Podcast enclosure:</strong> present" in transcript


def test_food_line_audio_reuses_existing_mp3_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"existing-food-line-mp3")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["podcast_enclosure_present"] is True
    assert result["audio_mp3_path"] == str(existing_audio)
    assert result["audio_mp3_url"] == "/food-line/audio/2026-06-05.mp3"
    assert audio_json["audio_generated"] is False
    assert audio_json["audio_available"] is True
    assert audio_json["audio_reused_existing"] is True
    assert audio_json["audio_file"] == "2026-06-05.mp3"
    assert audio_json["podcast_enclosure_present"] is True
    assert "<enclosure " in podcast
    assert existing_audio.read_bytes() == b"existing-food-line-mp3"


def test_food_line_audio_force_regenerate_replaces_only_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-06"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"old-audio")
    _mock_food_line_tts(monkeypatch, audio_bytes=b"new-audio")

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, force_audio_regenerate=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))

    assert result["audio_generated"] is True
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is False
    assert result["audio_replacement_performed"] is True
    assert result["audio_mp3_path"] == str(existing_audio)
    assert existing_audio.read_bytes() == b"new-audio"
    assert audio_json["audio_generated"] is True
    assert audio_json["audio_available"] is True
    assert audio_json["audio_replacement_performed"] is True
    assert audio_json["audio_file"] == "2026-06-06.mp3"


def test_food_line_audio_force_regenerate_failure_keeps_existing_mp3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    existing_audio = _seed_existing_food_line_audio(tmp_path, date, b"preserved-audio")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, force_audio_regenerate=True)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")

    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["audio_replacement_performed"] is False
    assert result["podcast_enclosure_present"] is True
    assert result["ok"] is True
    assert existing_audio.read_bytes() == b"preserved-audio"
    assert audio_json["audio_generated"] is False
    assert audio_json["audio_available"] is True
    assert audio_json["audio_reused_existing"] is True
    assert audio_json["podcast_enclosure_present"] is True
    assert "<enclosure " in podcast


def test_food_line_audio_require_audio_passes_when_existing_mp3_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _seed_existing_food_line_audio(tmp_path, date, b"existing-audio")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, require_audio=True)

    assert result["ok"] is True
    assert result["audio_generated"] is False
    assert result["audio_available"] is True
    assert result["audio_reused_existing"] is True
    assert result["podcast_enclosure_present"] is True


def test_food_line_audio_require_audio_fails_when_missing_and_tts_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food banks see rising demand", "Food banks across Texas are working hard to keep up with rising demand.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, require_audio=True, force_audio_regenerate=True)

    assert result["ok"] is False
    assert result["audio_generated"] is False
    assert result["audio_available"] is False
    assert result["audio_reused_existing"] is False
    assert result["podcast_enclosure_present"] is False


def test_food_line_public_evidence_excerpt_strips_kltv_site_chrome():
    raw = (
        "Food bank demand increased and pantry lines grew. "
        "Teacher Tribute Health Update Aging Untold Local News Video Extra Community We the People Reception Issues About Us "
        "KLTV.com - Channel 7 News, , for East Texas - KLTV.com - Tyler, Longview, Jacksonville | ETX News. "
        "KLTV reported that food banks across Texas were working to keep up, and one East Texas pantry operator described a 17 percent increase over three weeks in people asking for food assistance."
    )
    cleaned = food_line.clean_food_line_public_evidence_excerpt(raw, title="Food bank sees rising demand from families", limit=420)
    assert "Teacher Tribute" not in cleaned
    assert "Health Update" not in cleaned
    assert "Aging Untold" not in cleaned
    assert "Local News Video" not in cleaned
    assert "Extra Community" not in cleaned
    assert "We the People" not in cleaned
    assert "Reception Issues" not in cleaned
    assert "KLTV.com - Channel 7 News" not in cleaned
    assert "ETX News" not in cleaned
    assert "Food bank demand increased and pantry lines grew" in cleaned


def test_food_line_public_evidence_excerpt_strips_usda_gov_chrome():
    raw = (
        "Summer nutrition programs can help families keep meals on the table. "
        "Skip to main content Here’s how you know An official website of the United States government "
        "Official websites use .gov A .gov website belongs to an official government organization in the United States "
        "Secure .gov websites use HTTPS A lock ( Lock Locked padlock ) or https:// means you’ve safely connected to the .gov website "
        "Share sensitive information only on official, secure websites."
    )
    cleaned = food_line.clean_food_line_public_evidence_excerpt(raw, title="Summer Nutrition Programs", limit=420)
    assert cleaned != food_line.FOOD_LINE_PUBLIC_EVIDENCE_FALLBACK
    assert "Skip to main content" not in cleaned
    assert "Here’s how you know" not in cleaned
    assert "An official website of the United States government" not in cleaned
    assert "Official websites use .gov" not in cleaned
    assert "A .gov website belongs" not in cleaned
    assert "Secure .gov websites use HTTPS" not in cleaned
    assert "Share sensitive information only on official, secure websites" not in cleaned
    assert "can help families keep meals on the table" in cleaned


def test_food_line_source_card_omits_excerpt_when_boilerplate_cleans_to_fallback():
    row = {
        "title": "USDA Summer Food Service Program",
        "publisher": "USDA FNS",
        "location_name": "United States",
        "url": "https://www.fns.usda.gov/summer",
        "source_record_id": "food-line-context-test",
        "pressure_type": "context only",
        "pressure_summary": "",
        "evidence_text": (
            "Skip to main content Here’s how you know An official website of the United States government "
            "Official websites use .gov A .gov website belongs to an official government organization in the United States "
            "Secure .gov websites use HTTPS A lock ( Lock Locked padlock ) or https:// means you’ve safely connected to the .gov website "
            "Share sensitive information only on official, secure websites."
        ),
        "affected_groups": [],
    }
    html_output = food_line._food_line_source_card_html(row, label="Context record", heading_prefix="Context:")
    assert "Evidence excerpt:" not in html_output
    assert "USDA FNS" in html_output
    assert "https://www.fns.usda.gov/summer" in html_output
    assert "Context: USDA Summer Food Service Program" in html_output


def test_food_line_audio_transcript_only_omits_enclosure(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")

    result = run_food_line_dispatch(tmp_path, date)

    audio_json = json.loads((tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}.json").read_text(encoding="utf-8"))
    podcast = (tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml").read_text(encoding="utf-8")
    audio_index = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")

    assert result["audio_generated"] is False
    assert result["podcast_enclosure_present"] is False
    assert result["audio_mp3_path"] is None
    assert result["audio_mp3_url"] is None
    assert audio_json["audio_generated"] is False
    assert audio_json["audio_required"] is False
    assert audio_json["audio_file"] is None
    assert audio_json["audio_status"] == "transcript_only"
    assert "<enclosure " not in podcast
    assert "Food Line Audio" in audio_index


def test_food_line_audio_failure_reports_sanitized_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, audio_timeout_seconds=12.0)

    assert result["audio_generated"] is False
    assert result["audio_required"] is False
    assert result["ok"] is True
    assert result["tts_provider"] == "openai"
    assert result["tts_model_requested"] == "gpt-4o-mini-tts"
    assert result["tts_voice_requested"] == "alloy"
    assert result["tts_narration_char_count"] > 0
    assert result["tts_output_path_attempted"].endswith("2026-06-04.tmp.mp3")
    assert result["tts_api_key_present"] is True
    assert result["tts_output_dir_exists"] is True
    assert result["tts_partial_mp3_exists"] is False
    assert result["tts_elapsed_seconds"] >= 0
    assert result["tts_exception_type"] == "TimeoutError"
    assert "redacted-api-key" in str(result["tts_exception_message_sanitized"])
    assert "sk-test-1234567890" not in str(result["tts_exception_message_sanitized"])
    assert result["tts_timeout_seconds"] == 12.0
    assert result["warnings"]


def test_food_line_audio_failure_blocks_when_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    _mock_food_line_tts_failure(monkeypatch)

    result = run_food_line_dispatch(tmp_path, date, generate_audio=True, require_audio=True)

    assert result["ok"] is False
    assert result["audio_required"] is True
    assert result["audio_generated"] is False
    assert result["audio_status"] == "openai_tts_request_failed"
    assert any("audio narration was not generated" in warning.lower() for warning in result["warnings"])


def test_tts_tls_context_uses_explicit_bluefern_ca_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pem = tmp_path / "corp-ca.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.setenv("BLUEFERN_TTS_CA_FILE", str(pem))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["tls_verify"] is True
    assert meta["ca_source"] == "bluefern_tts_ca_file"
    assert meta["ca_file_used"] == str(pem.resolve())
    assert meta["bluefern_tts_ca_file_env"] == str(pem)


def test_tts_tls_context_uses_ssl_cert_file_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pem = tmp_path / "ssl-cert-file.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["ca_source"] == "SSL_CERT_FILE"
    assert meta["ca_file_used"] == str(pem.resolve())
    assert meta["ssl_cert_file_env"] == str(pem)


def test_tts_tls_context_uses_requests_ca_bundle_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pem = tmp_path / "requests-ca-bundle.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(pem))
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["ca_source"] == "REQUESTS_CA_BUNDLE"
    assert meta["ca_file_used"] == str(pem.resolve())
    assert meta["requests_ca_bundle_env"] == str(pem)


def test_tts_tls_context_uses_certifi_when_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    certifi_pem = tmp_path / "certifi.pem"
    certifi_pem.write_text("PEM", encoding="utf-8")
    fake_certifi = types.SimpleNamespace(where=lambda: str(certifi_pem))
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    monkeypatch.setenv("BLUEFERN_TTS_CA_SOURCE", "certifi")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(*, cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(tts_provider.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = tts_provider._build_tls_context()
    assert called["cafile"] == str(certifi_pem)
    assert meta["ca_source"] == "certifi"
    assert meta["ca_file_used"] == str(certifi_pem)


def test_tts_tls_context_reports_truststore_requested(monkeypatch: pytest.MonkeyPatch):
    fake_truststore = types.SimpleNamespace(SSLContext=lambda protocol: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    monkeypatch.setitem(sys.modules, "truststore", fake_truststore)
    monkeypatch.setenv("BLUEFERN_TTS_USE_TRUSTSTORE", "1")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    _ctx, meta = tts_provider._build_tls_context()
    assert meta["truststore_requested"] is True
    assert meta["truststore_available"] is True
    assert meta["ca_source"] == "truststore"


def test_tts_failure_includes_tls_diagnostics_and_masks_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234567890")
    monkeypatch.delenv("BLUEFERN_TTS_CA_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_USE_TRUSTSTORE", raising=False)
    monkeypatch.delenv("BLUEFERN_TTS_CA_SOURCE", raising=False)
    monkeypatch.setattr(
        tts_provider.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("certificate verify failed")),
    )

    result, diag = tts_provider.synthesize_speech_with_diagnostics(
        text="Smoke test",
        provider="openai",
        model="gpt-4o-mini-tts",
        voice="alloy",
        audio_format="mp3",
        timeout=5.0,
    )

    assert result.ok is False
    assert diag.api_key_present is True
    assert diag.tls_verify is True
    assert diag.ca_source == "system_default"
    assert diag.ca_file_used is None
    assert diag.truststore_requested is False
    assert diag.truststore_available is False
    assert "sk-test-1234567890" not in (diag.exception_message_sanitized or "")
    assert "certificate verify failed" in (diag.exception_message_sanitized or "")


def test_food_line_tts_smoke_command_writes_fake_mp3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        food_line_tts,
        "synthesize_speech_with_diagnostics",
        lambda **kwargs: (
            TTSResult(True, b"fake-mp3-data", "openai", kwargs.get("model"), kwargs.get("voice"), kwargs.get("audio_format"), None),
            TTSDiagnostics(
                provider="openai",
                model_requested=kwargs.get("model"),
                voice_requested=kwargs.get("voice"),
                narration_char_count=len(str(kwargs.get("text") or "")),
                output_path_attempted=str(kwargs.get("output_path") or ""),
                api_key_present=True,
                output_dir_exists=True,
                partial_mp3_exists=False,
                elapsed_seconds=0.05,
                exception_type=None,
                exception_message_sanitized=None,
                timeout_seconds=float(kwargs.get("timeout") or 90.0),
                audio_format=kwargs.get("audio_format"),
                tls_verify=True,
                ca_file_used=None,
                ca_source="system_default",
                truststore_requested=False,
                truststore_available=False,
                ssl_cert_file_env=None,
                requests_ca_bundle_env=None,
                bluefern_tts_ca_file_env=None,
                tls_workaround_warning=None,
            ),
        ),
    )

    result = food_line_tts.run_food_line_tts_smoke(
        date="2026-06-04",
        sample_text="This is a Food Line Dispatch audio smoke test.",
        output=smoke_dir,
    )

    assert result["ok"] is True
    assert result["mp3_path"].endswith("tts_smoke-test.mp3")
    assert result["mp3_size_bytes"] > 0
    assert (smoke_dir / "tts_smoke_test.json").exists()
    assert (smoke_dir / "tts_smoke-test.mp3").exists()
    assert result["api_key_present"] is True
    assert result["error_type"] is None
    assert result["tls_verify"] is True
    assert result["ca_source"] == "system_default"


def test_food_line_manifest_includes_lead_and_role_counts(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    lead = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([lead, _row(2, "state_official", "OR"), _row(3, "economic_data", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "edition_manifest.json").read_text(encoding="utf-8"))
    assert result["lead_source_record_id"] in {row["source_record_id"] for row in json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))}
    assert isinstance(manifest.get("source_roles_count"), dict)
    assert manifest.get("editorial_status")
    assert manifest.get("why_this_lead")
    assert manifest.get("primary_signal_status") in {"new_primary", "continuing_only", "none"}
    assert "previous_edition_date" in manifest
    assert manifest.get("public_rendered") is True
    assert manifest.get("qualified_primary_count") == 1
    assert manifest.get("skip_reason") == ""


def test_food_line_sparse_day_labeled_clearly(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-02"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_row(1), _row(2, "state_official", "OR")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["editorial_status"] == "sparse"
    assert result["source_adequacy"]["status"] == "limited"


def test_food_line_registry_supports_state_local_entries(tmp_path: Path):
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "local-source-1",
                "source_name": "Local Source One",
                "publisher": "Local Publisher",
                "url": "https://example.com/local-source-1",
                "source_family": "local_news",
                "source_type": "page",
                "state": "OR",
                "location_name": "Portland, OR",
                "location_scope": "state_local",
                "enabled": True,
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])
    entries = load_food_line_registry(tmp_path)
    assert entries
    state_rows = [row for row in entries if str(row.get("state") or "").upper() not in {"", "US"}]
    assert state_rows
    required = {
        "source_id",
        "source_name",
        "source_family",
        "source_type",
        "url",
        "state",
        "location_name",
        "location_scope",
        "source_role_allowed",
        "pressure_required",
        "freshness_mode",
        "max_age_days",
        "positive_keywords",
        "negative_keywords",
        "affected_group_keywords",
        "default_issue_tags",
        "default_map_category",
        "enabled",
        "notes",
    }
    assert required.issubset(set(state_rows[0].keys()))


def test_food_line_state_local_classifies_as_local_signal(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    local = _row(1, family="state_official", state="WA")
    local["issue_tags"] = ["SNAP", "benefits", "service access"]
    local["published_date_basis"] = "source_published"
    p.write_text(json.dumps([local, _row(2, "economic_data", "US"), _row(3, "policy_research", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_roles_count"]["local_signal"] >= 1


def test_food_line_national_record_not_local_signal(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    national = _row(1, family="economic_data", state="US")
    national["issue_tags"] = ["household food insecurity"]
    p.write_text(json.dumps([national, _row(2, "policy_research", "US"), _row(3, "federal_official", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_roles_count"]["local_signal"] == 0


def test_food_line_local_signal_beats_background_as_lead(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    bg = _row(1, family="economic_data", state="US")
    bg["map_category"] = "context / monitoring only"
    local = _pressure_row(2, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="PA")
    local["issue_tags"] = ["SNAP", "benefits", "service access"]
    local["map_category"] = "benefit disruption"
    local["summary_or_snippet"] = "Food bank demand increased and pantry lines grew."
    p.write_text(json.dumps([bg, local, _row(3, "policy_research", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["lead_source_record_id"] == local["source_record_id"]


def test_food_line_state_centroid_basis_labeled_clearly(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _row(1, family="state_official", state="OR")
    row["location_name"] = "Oregon"
    row["summary_or_snippet"] = "SNAP benefit delay reported by county office."
    row["issue_tags"] = ["SNAP", "benefits", "service access"]
    p.write_text(json.dumps([row, _row(2, "policy_research", "US"), _row(3, "economic_data", "US")], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    mapped = map_data.get("mapped_markers") or []
    assert any(marker.get("coordinate_basis") == "state centroid" for marker in mapped)


def test_food_line_output_includes_scope_counts(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-03"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_row(1, "state_official", "WA"), _row(2, "policy_research", "US"), _row(3, "economic_data", "US")], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert "local_signal_count" in result
    assert "state_signal_count" in result
    assert "national_context_count" in result


@pytest.mark.parametrize(
    "title,summary,family,expected_pressure_type",
    [
        ("Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", "local_news", "demand strain"),
        ("Pantry cuts hours due to low inventory", "The pantry reduced hours because shelves were bare.", "food_bank_provider", "service reduction"),
        ("SNAP benefits delayed", "Households reported a SNAP delay and application backlog.", "state_official", "benefit disruption"),
        ("Summer meal site closure", "The meal site closed and children are missing meals.", "school_meals_child_nutrition", "child meal gap"),
        ("Meals on Wheels waitlist grows", "The senior meal waitlist grew and providers could not serve seniors.", "senior_meals", "senior meal strain"),
        ("Families face medical bills and food hardship", "Households are skipping meals because medical bills and prescription costs keep rising.", "local_news", "household hardship"),
        ("Grocery closure creates access gap", "A grocery closure left rural residents without nearby food access.", "local_news", "access gap"),
        ("Emergency food distribution after flood", "D-SNAP and emergency food distribution responded to flood disruption.", "disaster_emergency", "disaster disruption"),
    ],
)
def test_food_line_pressure_classification_examples(tmp_path: Path, title: str, summary: str, family: str, expected_pressure_type: str):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, title, summary, family=family)
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["pressure_signal_count"] == 1
    assert result["pressure_marker_count"] == 1
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    assert manifest[0]["pressure_signal"] is True
    assert manifest[0]["pressure_type"] == expected_pressure_type
    assert manifest[0]["pressure_summary"]
    assert manifest[0]["pressure_summary"].lower() not in {
        "source-backed food insecurity context signal",
        "food insecurity context signal",
        "source-backed pressure signal",
        "elevated demand signal",
        "context signal",
    }


def test_food_line_bluesky_ready_summary_tracks_scope_and_url(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(
        1,
        "National food insecurity rises",
        "National food insecurity and household hardship remain elevated as medical bills and prescription costs keep forcing tradeoffs.",
        family="national_news",
        state="US",
    )
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)

    assert result["selected_lead_pressure_scope_label"] == "National / systemic"
    assert result["selected_lead_pressure_scope_text"] == "national/systemic"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-04/"
    assert "Food Line Dispatch, June 4, 2026:" in result["bluesky_post_text"]
    assert "The briefing also tracks related public background sources." in result["bluesky_post_text"]


def test_food_line_13abc_style_pantry_snap_story_publishes_when_fresh_and_clean(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-05" / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    review = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    lead = next(row for row in review if row["source_record_id"] == "food-line-auto-9013087c4ebc5f32")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["skip_reason"] == ""
    assert result["primary_disqualification_reason"] == ""
    assert result["lead_source_record_id"] == "food-line-auto-9013087c4ebc5f32"
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["selected_lead_pressure_type"] == "demand strain"
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-05/"
    assert lead["pressure_signal"] == "true"
    assert lead["pressure_type"] == "demand strain"
    assert lead["pressure_verification_status"] == "source_text_verified"
    assert lead["source_published_date"] == "2026-06-05"
    assert lead["freshness_status"] == "fresh_daily_signal"
    assert lead["primary_eligible"] == "true"
    assert lead["primary_disqualification_reason"] == ""
    assert "Today’s Read" in edition_html
    assert "Main Food Access Story" in edition_html
    assert "Sources Behind This Briefing" in edition_html
    assert "Today&apos;s pressure point" not in edition_html
    assert "What changed" not in edition_html
    assert "Where pressure is visible" not in edition_html


def test_food_line_cascade_pbs_style_funding_cut_story_publishes_when_fresh_and_clean(tmp_path: Path):
    _ensure_assets(tmp_path)
    _clear_food_line_registries(tmp_path)
    date = "2026-06-13"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / "2026-06-13" / "auto_sources.json"
    payload = [
        row
        for row in json.loads(payload_path.read_text(encoding="utf-8"))
        if row["source_record_id"] == "food-line-auto-6effc522ae28d822"
    ]
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    review = list(csv.DictReader((tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8")))
    lead = next(row for row in review if row["source_record_id"] == "food-line-auto-6effc522ae28d822")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")

    assert result["public_rendered"] is True
    assert result["skip_reason"] == ""
    assert result["primary_disqualification_reason"] == ""
    assert result["lead_source_record_id"] == "food-line-auto-6effc522ae28d822"
    assert result["selected_lead_source_role"] == "local_signal"
    assert result["selected_lead_pressure_type"] == "demand strain"
    assert result["selected_lead_pressure_scope_label"] == "Local / operational"
    assert result["bluesky_post_ready"] is True
    assert result["bluesky_post_text"]
    assert len(result["bluesky_post_text"]) <= 300
    assert result["public_url"] == "https://dispatches.thebluefernco.com/food-line/editions/2026-06-13/"
    assert lead["pressure_signal"] == "true"
    assert lead["pressure_type"] == "demand strain"
    assert lead["pressure_verification_status"] == "source_text_verified"
    assert lead["source_published_date"] == "2026-06-13"
    assert lead["freshness_status"] == "fresh_daily_signal"
    assert lead["primary_eligible"] == "true"
    assert lead["primary_disqualification_reason"] == ""
    assert "Today’s Read" in edition_html
    assert "Main Food Access Story" in edition_html
    assert "Sources Behind This Briefing" in edition_html


def test_food_line_nonpressure_rss_items_are_excluded_from_pressure_map(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _pressure_row(1, "Restaurant announces new menu", "The restaurant announced a seasonal menu change.", family="national_news"),
        _pressure_row(2, "Food bank volunteer day", "The food bank thanked volunteers and shared shift times.", family="nonprofit_news"),
    ]
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "review" / "food-line" / date / "map_data.json").read_text(encoding="utf-8"))
    assert result["public_rendered"] is False
    assert result["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert map_data.get("pressure_markers") == []
    assert any(record.get("reason") for record in map_data.get("excluded_records") or [])
    assert not (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").exists()


def test_food_line_vague_provider_source_is_demoted_when_summary_cannot_be_generated(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    vague = _pressure_row(1, "Food bank update", "Volunteer day and community partners.", family="food_bank_provider", state="TX")
    vague["map_category"] = "elevated demand"
    p.write_text(json.dumps([vague], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "review" / "food-line" / date / "map_data.json").read_text(encoding="utf-8"))
    assert result["public_rendered"] is False
    assert result["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert map_data["markers"][0]["pressure_signal"] is False
    assert map_data["markers"][0]["pressure_summary"] == ""
    assert "insufficient specific pressure evidence" in map_data["markers"][0]["pressure_reason"]


def test_food_line_affected_groups_require_supporting_text_and_baseline_stays_baseline(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    pressure = _pressure_row(1, "Food bank demand increased", "Food bank demand increased sharply.", family="food_bank_provider", state="TX")
    pressure["issue_tags"] = ["food banks", "pantry capacity"]
    supported = _pressure_row(2, "SNAP benefits delayed for families", "SNAP benefits delayed for families and children.", family="state_official", state="TX")
    baseline = _row(3, family="economic_data", state="US", title="USDA context", summary="National food security context and trend information.")
    baseline["map_category"] = "context / monitoring only"
    p.write_text(json.dumps([pressure, supported, baseline], indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    by_title = {row["title"]: row for row in manifest}
    assert by_title["Food bank demand increased"]["affected_groups"] == []
    assert any(group in {"children", "SNAP households", "low-income households"} for group in by_title["SNAP benefits delayed for families"]["affected_groups"])
    assert by_title["USDA context"]["source_role"] == "baseline_condition"
    assert by_title["USDA context"]["pressure_signal"] is False
    assert result["baseline_source_count"] == 1
    source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Not clearly isolated by source" not in source_table
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "children" in edition_html.lower() or "snap households" in edition_html.lower() or "low-income households" in edition_html.lower()


def test_food_line_pressure_summary_includes_specific_evidence_for_core_cases(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX"),
        _pressure_row(2, "Pantry cuts hours due to low inventory", "The pantry reduced hours because shelves were bare.", family="food_bank_provider", state="WA"),
        _pressure_row(3, "SNAP benefits delayed", "Households reported a SNAP delay and application backlog.", family="state_official", state="OR"),
    ]
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    manifest = json.loads((tmp_path / "output" / "site" / "food-line" / "editions" / date / "sources_manifest.json").read_text(encoding="utf-8"))
    by_title = {row["title"]: row for row in manifest}
    assert "rising food-assistance demand" in by_title["Food bank sees rising demand from families"]["pressure_summary"].lower()
    assert "reduced distribution hours" in by_title["Pantry cuts hours due to low inventory"]["pressure_summary"].lower()
    assert "snap benefit delay" in by_title["SNAP benefits delayed"]["pressure_summary"].lower()


def test_food_line_source_table_includes_pressure_summary(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "What happened" in table
    assert "What the source says" in table
    assert "Record ID" in table
    assert "How it was used" in table
    assert "Verification status" in table
    assert "rising food-assistance demand" in table.lower()


def test_food_line_public_edition_uses_pressure_summary_and_cleans_public_excerpts(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    verified = _pressure_row(
        1,
        "KLTV food pantries struggle to keep up with rising demand",
        "The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand. Skip to content Advertise With Us Weather Sports Contests Closings & Delays",
        family="local_news",
        state="TX",
    )
    verified["location_name"] = "East Texas, TX"
    verified["publisher"] = "KLTV"
    noisy_context = _row(
        2,
        family="public_radio",
        state="TX",
        title="Unrelated local arts story",
        summary="Skip to content Advertise With Us Weather Sports Contests Closings & Delays",
    )
    noisy_context["map_category"] = "context / monitoring only"
    p.write_text(json.dumps([verified, noisy_context], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")

    assert "Daily briefing / June 4, 2026" in edition_html
    assert "Today’s Read" in edition_html
    assert "At A Glance" in edition_html
    assert "Main Food Access Story" in edition_html
    assert "What Else We’re Watching" not in edition_html
    assert "Context and Watch Items" not in edition_html
    assert "Sources Behind This Briefing" in edition_html
    assert "Source Mix" not in edition_html
    assert "source mix" not in edition_html.lower()
    assert "Source Note" in edition_html
    assert "No new primary pressure signal qualified today." not in edition_html
    assert "What changed today" not in edition_html
    assert "Publishing note" not in edition_html
    assert "Skip to content" not in edition_html
    assert "Advertise With Us" not in edition_html
    assert "Weather" not in edition_html
    assert "Sports" not in edition_html
    assert "Contests" not in edition_html
    assert "Closings & Delays" not in edition_html
    assert "Open the public source table for traceability and cleaned excerpts." in edition_html
    assert "Source:" in edition_html
    assert "Where:" in edition_html
    assert "What happened:" in edition_html
    assert "Why it matters:" in edition_html
    assert "Read the source:" in edition_html
    assert "Record ID" in source_table_html
    assert "What the source says" in source_table_html
    assert "Unrelated local arts story" not in source_table_html
    assert "Skip to content" not in source_table_html
    assert "Verification status" in source_table_html
    assert "Used on public page" in source_table_html
    assert "Skip to content" not in map_html
    assert "What the source says:" in map_html


def test_food_line_public_source_table_matches_rendered_public_urls(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-05"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload_path = Path(__file__).resolve().parents[1] / "data" / "dispatches" / "food-line" / "sources" / date / "auto_sources.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")

    page_urls = _http_urls(edition_html)
    table_urls = _http_urls(source_table_html)
    assert page_urls == table_urls
    assert "What Else We’re Watching" in edition_html
    assert "Sources Behind This Briefing" in edition_html
    assert "Context and Watch Items" not in edition_html
    assert "Source Mix" not in edition_html
    assert "local_signal" not in source_table_html
    assert "source_text_verified" not in source_table_html
    assert "demoted_context" not in source_table_html
    assert "context only" not in source_table_html.lower()
    assert "Cascade PBS" not in source_table_html
    assert "KLTV" not in source_table_html
    assert "Used on public page" in source_table_html
    assert "Yes" in source_table_html


def test_food_line_reuses_previous_day_lead_as_continuing_pressure(tmp_path: Path):
    _ensure_assets(tmp_path)
    kltv = _pressure_row(
        1,
        "KLTV food pantries struggle to keep up with rising demand",
        "The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand.",
        family="local_news",
        state="TX",
    )
    kltv["publisher"] = "KLTV"
    kltv["location_name"] = "East Texas, TX"
    kltv["summary_or_snippet"] = "The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand. Skip to content Advertise With Us Weather Sports Contests Closings & Delays"
    context = _row(2, family="economic_data", state="US", title="USDA context", summary="National food security context.")
    context["map_category"] = "context / monitoring only"

    date_a = "2026-06-04"
    p_a = _manual_path(tmp_path, date_a)
    p_a.parent.mkdir(parents=True, exist_ok=True)
    p_a.write_text(json.dumps([kltv, context], indent=2), encoding="utf-8")
    result_a = run_food_line_dispatch(tmp_path, date_a)
    assert result_a["primary_signal_status"] == "new_primary"
    assert result_a["lead_source_record_id"] == kltv["source_record_id"]

    date_b = "2026-06-05"
    p_b = _manual_path(tmp_path, date_b)
    p_b.parent.mkdir(parents=True, exist_ok=True)
    p_b.write_text(json.dumps([kltv, context], indent=2), encoding="utf-8")
    result_b = run_food_line_dispatch(tmp_path, date_b)
    site_edition = tmp_path / "output" / "site" / "food-line" / "editions" / date_b
    review_manifest = tmp_path / "output" / "review" / "food-line" / date_b / "run_manifest.json"
    data_manifest = tmp_path / "data" / "dispatches" / "food-line" / "editions" / date_b / "run_manifest.json"
    manifest_b = json.loads(data_manifest.read_text(encoding="utf-8"))

    assert result_b["primary_signal_status"] == "continuing_only"
    assert result_b["lead_source_record_id"] is None
    assert result_b["public_rendered"] is False
    assert result_b["qualified_primary_count"] == 0
    assert result_b["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert result_b["continuing_pressure_source_record_ids"] == [kltv["source_record_id"]]
    assert not site_edition.exists()
    assert review_manifest.exists()
    assert data_manifest.exists()
    assert manifest_b["public_rendered"] is False
    assert manifest_b["qualified_primary_count"] == 0
    assert manifest_b["skip_reason"] == "No new primary food-access signal qualified for public Food Line publication."
    assert manifest_b["continuing_pressure_source_record_ids"] == [kltv["source_record_id"]]


def test_food_line_map_popup_uses_pressure_summary_and_not_tags_as_primary_evidence(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    assert "Location:" in map_html
    assert "Verification status:" in map_html
    assert "Record ID:" in map_html
    assert "Source URL:" in map_html
    assert "What happened:" in map_html
    assert "What the source says:" in map_html
    assert "rising food-assistance demand" in map_html.lower()
    assert "Source-backed food insecurity context signal" not in map_html
    assert "<div><strong>Category:</strong>" not in map_html
    assert "<div><strong>Issue tags:</strong>" not in map_html
    assert "Not clearly isolated by source" not in map_html


def test_food_line_blank_affected_groups_render_placeholder_everywhere(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Pantry cuts hours due to low inventory", "The pantry reduced hours because shelves were bare.", family="food_bank_provider", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    assert "Not clearly isolated by source" not in map_html
    assert "Not clearly isolated by source" not in source_table
    assert "Not clearly isolated by source" not in edition_html


def test_food_line_map_and_runner_diagnostics_count_only_pressure_records(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="CA"),
        _pressure_row(2, "Restaurant announces new menu", "The restaurant announced a seasonal menu change.", family="national_news"),
        _row(3, family="economic_data", state="US", title="USDA context", summary="National food security context and trend information."),
    ]
    rows[2]["map_category"] = "context / monitoring only"
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert result["pressure_source_count_by_family"]["local_news"] == 1
    assert result["pressure_source_count_by_state"]["CA"] == 1
    assert result["news_item_count"] == 2
    assert result["baseline_source_count"] == 1
    assert map_data.get("diagnostics", {}).get("pressure_signal_count") == 1
    assert map_data.get("diagnostics", {}).get("pressure_marker_count") == 1
    assert map_data.get("diagnostics", {}).get("excluded_record_count") == 2
    assert map_data.get("diagnostics", {}).get("excluded_context_count") == 1
    assert len(map_data.get("mapped_markers") or []) == 1
    assert map_data.get("mapped_markers")[0]["source_title"] == "Food bank sees rising demand from families"
    assert map_data.get("excluded_records") and all("reason" in record for record in map_data["excluded_records"])
    public_source_table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Food bank sees rising demand from families" in public_source_table
    assert "Restaurant announces new menu" not in public_source_table
    assert "USDA context" in public_source_table
    with (tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv").open(encoding="utf-8") as handle:
        pressure_review = list(csv.DictReader(handle))
    assert len(pressure_review) == 3
    assert any(row["pressure_signal"] == "false" for row in pressure_review)


def test_food_line_collect_reports_rejected_news_reasons(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    registry_dir = tmp_path / "data" / "dispatches" / "food-line"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.joinpath("source_registry.json").write_text("[]", encoding="utf-8")
    registry_dir.joinpath("pressure_source_registry.json").write_text(
        json.dumps(
            [
                {
                    "source_id": "test-rss-feed",
                    "source_name": "Test RSS Feed",
                    "publisher": "Test Publisher",
                    "source_type": "rss",
                    "url": "https://example.com/test-rss",
                    "source_family": "national_news",
                    "state": "US",
                    "location_name": "United States",
                    "location_scope": "national",
                    "source_role_allowed": "pressure_evidence",
                    "pressure_required": True,
                    "freshness_mode": "pressure",
                    "max_age_days": 7,
                    "positive_keywords": ["food bank", "hunger", "SNAP"],
                    "negative_keywords": ["recipe", "menu", "restaurant review"],
                    "affected_group_keywords": ["families", "children"],
                    "enabled": True,
                    "notes": "Test RSS feed for rejected item diagnostics.",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    rss_payload = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\"><channel>
  <item><title>Restaurant announces new menu</title><link>https://example.com/menu</link><pubDate>Mon, 03 Jun 2026 12:00:00 GMT</pubDate><description>Restaurant review and menu update.</description></item>
  <item><title>Food bank sees rising demand from families</title><link>https://example.com/demand</link><pubDate>Mon, 03 Jun 2026 12:00:00 GMT</pubDate><description>Food bank demand increased and pantry lines grew.</description></item>
</channel></rss>"""

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    assert result["source_count"] == 1
    assert result["rejected_news_count"] == 1
    assert result["rejected_news_reasons"]
    assert any("excluded by negative filter" in reason for reason in result["rejected_news_reasons"])


def test_food_line_disabled_pressure_sources_are_skipped(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "disabled-pressure-feed",
                "source_name": "Disabled Pressure Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/disabled",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe"],
                "affected_group_keywords": ["families"],
                "enabled": False,
                "notes": "Disabled candidate should never be collected.",
            },
            {
                "source_id": "enabled-pressure-feed",
                "source_name": "Enabled Pressure Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/enabled",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Enabled test feed.",
            },
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/demand",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    fetch_calls: list[str] = []

    def fetcher(url: str, timeout: int = 15) -> bytes:  # noqa: ARG001
        fetch_calls.append(url)
        return rss_payload

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert fetch_calls == ["https://example.com/enabled"]
    assert result["source_count"] == 1
    assert result["collected_source_count_by_source_id"] == {"enabled-pressure-feed": 1}
    assert "disabled-pressure-feed" not in result["collected_source_count_by_source_id"]


def test_food_line_verified_rss_source_produces_pressure_records(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "kff-health-news-rss",
                "source_name": "KFF Health News RSS",
                "publisher": "KFF Health News",
                "source_type": "rss",
                "url": "https://kffhealthnews.org/RSS.aspx",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP", "food insecurity", "hunger"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Verified RSS feed used for pressure collection tests.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/demand",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    assert result["source_count"] == 1
    assert result["collected_source_count_by_source_id"] == {"kff-health-news-rss": 1}
    assert rows and rows[0]["pressure_signal"] is True
    assert rows[0]["pressure_summary"]
    assert rows[0]["pressure_summary"] not in GENERIC_PRESSURE_SUMMARIES
    assert "rising food-assistance demand" in rows[0]["pressure_summary"].lower()
    assert rows[0]["pressure_verification_status"] == "source_text_verified"
    assert rows[0]["evidence_text_basis"] == "rss_item_text"
    assert rows[0]["extraction_quality"] == "high"
    assert rows[0]["expected_text_basis"] == "rss_summary"
    assert rows[0]["pressure_verification_required"] is True
    assert rows[0]["evidence_text"]
    assert rows[0]["pressure_match_terms"]
    audit = json.loads(Path(result["collector_audit_path"]).read_text(encoding="utf-8"))
    assert audit and audit[0]["fetched"] is True
    assert audit[0]["accepted_pressure_count"] == 1
    assert audit[0]["extraction_basis_used"] == ["rss_item_text"]


def test_food_line_generic_page_source_is_demoted_without_verified_evidence(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "generic-page-feed",
                "source_name": "Generic Page Feed",
                "publisher": "Test Publisher",
                "source_type": "page",
                "url": "https://example.com/generic-page",
                "source_family": "food_bank_provider",
                "state": "TX",
                "location_name": "Dallas, TX",
                "location_scope": "state_local",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "low",
                "expected_text_basis": "page_text",
                "positive_keywords": ["demand", "shortage", "waitlist", "hours", "capacity", "inventory"],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala", "donation"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Generic page used to ensure registry defaults do not create pressure.",
                "summary_fallback": "Food bank demand increased and pantry lines grew.",
            }
        ],
    )
    html_payload = b"""<!doctype html>
<html>
<head><title>Generic Food Bank Page</title><meta name=\"description\" content=\"Community updates and general information.\"></head>
<body><p>Community bulletin with updates for members today.</p></body></html>"""
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: html_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    assert result["source_count"] == 1
    assert result["pressure_signal_count"] == 0
    assert result["pressure_demoted_unverified_count"] == 1
    assert result["pressure_registry_only_count"] == 0
    assert rows[0]["pressure_signal"] is False
    assert rows[0]["pressure_verification_status"] == "demoted_context"
    assert rows[0]["evidence_text_basis"] == "page_text_excerpt"
    assert rows[0]["extraction_quality"] == "low"
    assert rows[0]["expected_text_basis"] == "page_text"
    assert rows[0]["evidence_text"]
    assert rows[0]["pressure_match_terms"] == []
    assert rows[0]["pressure_summary"] == ""


def test_food_line_rejects_recipe_lifestyle_and_charity_items(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "test-rejection-feed",
                "source_name": "Test Rejection Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/reject",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP", "hunger"],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala", "donation"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Rejected-item diagnostics feed.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Restaurant announces new menu",
                "link": "https://example.com/menu",
                "description": "Restaurant review and menu update.",
            },
            {
                "title": "Recipe roundup for summer dinners",
                "link": "https://example.com/recipe",
                "description": "Lifestyle recipe ideas and cooking tips.",
            },
            {
                "title": "Food bank gala invites community donations",
                "link": "https://example.com/gala",
                "description": "Join the charity gala and fundraiser this weekend.",
            },
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    assert result["source_count"] == 0
    assert result["rejected_news_count"] == 3
    assert result["rejected_news_by_source"] == {"test-rejection-feed": 3}
    assert result["rejected_news_reasons"]
    assert all(
        "excluded by negative filter" in reason or "donation page is not current pressure evidence" in reason
        for reason in result["rejected_news_reasons"]
    )


def test_food_line_food_bank_demand_and_snap_delay_items_are_accepted(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "test-accepted-feed",
                "source_name": "Test Accepted Feed",
                "publisher": "Test Publisher",
                "source_type": "rss",
                "url": "https://example.com/accept",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "positive_keywords": ["food bank", "SNAP", "food insecurity", "hunger"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families", "children", "SNAP"],
                "enabled": True,
                "notes": "Accepted-item diagnostics feed.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/demand",
                "description": "Food bank demand increased and pantry lines grew.",
            },
            {
                "title": "State officials report SNAP benefit delay affecting households",
                "link": "https://example.com/snap-delay",
                "description": "Households in Oregon will see a SNAP benefit delay and food pantries expect extra demand.",
            },
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    rows = json.loads(Path(result["auto_sources_path"]).read_text(encoding="utf-8"))
    assert result["source_count"] == 2
    assert result["collected_source_count_by_source_id"] == {"test-accepted-feed": 2}
    assert result["rejected_news_count"] == 0
    assert result["collected_count_by_extraction_quality"] == {"high": 2}
    assert result["verified_pressure_count_by_extraction_quality"] == {"high": 2}
    assert all(row["pressure_signal"] is True for row in rows)
    assert any("rising food-assistance demand" in row["pressure_summary"].lower() for row in rows)
    assert any("snap benefit delay" in row["pressure_summary"].lower() or "benefit delay" in row["pressure_summary"].lower() for row in rows)
    assert all(row["pressure_summary"] not in GENERIC_PRESSURE_SUMMARIES for row in rows)
    assert all(row["pressure_verification_status"] == "source_text_verified" for row in rows)
    assert all(row["evidence_text"] for row in rows)
    assert all(row["pressure_match_terms"] for row in rows)


def test_food_line_map_data_includes_verification_fields(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    marker = (map_data.get("pressure_markers") or [])[0]
    assert marker["pressure_verification_status"] == "source_text_verified"
    assert marker["evidence_text"]
    assert marker["pressure_match_terms"]
    assert marker["evidence_text_basis"]
    assert marker["extraction_quality"]
    assert marker["expected_text_basis"]
    assert marker["pressure_verification_required"] is True


def test_food_line_source_table_includes_evidence_fields(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    table = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    assert "Record ID" in table
    assert "What the source says" in table
    assert "How it was used" in table
    assert "Verification status" in table
    assert "Source family" in table
    assert "rising food-assistance demand" in table.lower()


def test_food_line_logo_is_copied_and_referenced_in_generated_output(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    repo_logo = Path(__file__).parent.parent / "assets" / "food-line-logo.png"
    copied_logo = tmp_path / "output" / "site" / "food-line" / "assets" / "food-line-logo.png"
    assert copied_logo.exists()
    assert copied_logo.read_bytes() == repo_logo.read_bytes()

    index_html = (tmp_path / "output" / "site" / "food-line" / "index.html").read_text(encoding="utf-8")
    edition_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html").read_text(encoding="utf-8")
    source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html").read_text(encoding="utf-8")
    map_html = (tmp_path / "output" / "site" / "food-line" / "map" / "index.html").read_text(encoding="utf-8")
    audio_html = (tmp_path / "output" / "site" / "food-line" / "audio" / "index.html").read_text(encoding="utf-8")

    assert 'alt="The Food Line Dispatch"' in index_html
    assert 'src="assets/food-line-logo.png"' in index_html
    assert 'src="../../assets/food-line-logo.png"' in edition_html
    assert 'src="../../assets/food-line-logo.png"' in source_table_html
    assert 'src="../assets/food-line-logo.png"' in map_html
    assert 'src="../assets/food-line-logo.png"' in audio_html


def test_food_line_dispatch_refreshes_historical_source_tables(tmp_path: Path):
    _ensure_assets(tmp_path)
    current_date = "2026-06-04"
    old_dates = ["2026-06-01", "2026-06-02"]
    for old_date in old_dates:
        edition_dir = tmp_path / "output" / "site" / "food-line" / "editions" / old_date
        edition_dir.mkdir(parents=True, exist_ok=True)
        _write_source_registry(tmp_path, [_row(1, title=f"Archive Source {old_date}")])
        (edition_dir / "index.html").write_text("<html><body>archive</body></html>", encoding="utf-8")
        (edition_dir / "edition_manifest.json").write_text(
            json.dumps(
                {
                    "dispatch_slug": "food-line",
                    "edition_date": old_date,
                    "public_rendered": True,
                    "qualified_primary_count": 1,
                    "skip_reason": "",
                    "future_date_blocked": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (edition_dir / "sources_manifest.json").write_text(json.dumps([_row(1, title=f"Archive Source {old_date}")], indent=2), encoding="utf-8")
        (edition_dir / "source_table.html").write_text("<html><head></head><body><table><tr><th>Legacy</th></tr></table></body></html>", encoding="utf-8")

    p = _manual_path(tmp_path, current_date)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([_pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, current_date)

    for old_date in old_dates:
        source_table_html = (tmp_path / "output" / "site" / "food-line" / "editions" / old_date / "source_table.html").read_text(encoding="utf-8")
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in source_table_html
    assert "Record ID" in source_table_html
    assert "What the source says" in source_table_html
    assert "Verification status" in source_table_html
    assert 'src="../../assets/food-line-logo.png"' in source_table_html


def test_food_line_blue_fern_compliance_report_is_written(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)
    report_json = tmp_path / "output" / "review" / "food-line" / date / "blue_fern_compliance_report.json"
    report_md = tmp_path / "output" / "review" / "food-line" / date / "blue_fern_compliance_report.md"

    assert result["ok"] is True
    assert report_json.exists()
    assert report_md.exists()
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["logo_checks"]["asset_exists"] is True
    assert payload["logo_checks"]["podcast_artwork_exists"] is True
    assert payload["visual_checks"]["required_colors_present"] is True
    assert payload["source_table_checks"]["required_columns_present"] is True
    assert payload["mobile_basic_html_checks"]["viewport_meta_present"] is True


def test_food_line_blue_fern_compliance_missing_logo_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    (tmp_path / "output" / "site" / "food-line" / "assets" / "food-line-logo.png").unlink()
    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("missing logo asset" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_missing_blue_fern_color_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    for html_path in [
        tmp_path / "output" / "site" / "food-line" / "index.html",
        tmp_path / "output" / "site" / "food-line" / "archive.html",
        tmp_path / "output" / "site" / "food-line" / "map" / "index.html",
        tmp_path / "output" / "site" / "food-line" / "audio" / "index.html",
        tmp_path / "output" / "site" / "food-line" / "editions" / date / "index.html",
        tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html",
    ]:
        text = html_path.read_text(encoding="utf-8")
        text = text.replace("#1E3F4F", "#000000").replace("#EFE7DA", "#000000").replace("#4E6B79", "#000000")
        html_path.write_text(text, encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("blue fern palette" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_resource_directory_language_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    audio_index_path = tmp_path / "output" / "site" / "food-line" / "audio" / "index.html"
    audio_index_path.write_text(audio_index_path.read_text(encoding="utf-8") + "\n<p>Find food resources near you.</p>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("resource-directory language" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_public_chrome_and_signal_mix(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    audio_index_path = tmp_path / "output" / "site" / "food-line" / "audio" / "index.html"
    audio_index_path.write_text(audio_index_path.read_text(encoding="utf-8") + "\n<p>Skip to content Watch Live Signal mix today: daily=0, provider=0, local=24, background=0.</p>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("scraped site chrome" in failure.lower() for failure in result["failures"])
    assert any("signal mix" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_audio_transcript_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)
    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    transcript_path = tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html"
    transcript_path.write_text(transcript_path.read_text(encoding="utf-8") + "\n<p>Skip to content Watch Live Signal mix today: daily=0, provider=0, local=24, background=0.</p>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any(str(transcript_path) in failure for failure in result["failures"])
    assert any("signal mix" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_audio_podcast_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)
    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    podcast_path = tmp_path / "output" / "site" / "food-line" / "audio" / "podcast.xml"
    podcast_path.write_text(podcast_path.read_text(encoding="utf-8").replace("Food Line Dispatch - June 4, 2026", "Food Line Dispatch - June 4, 2026 Skip to content Advertise With Us"), encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any(str(podcast_path) in failure for failure in result["failures"])
    assert any("scraped site chrome" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_audio_briefing_debug_phrases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    _mock_food_line_tts(monkeypatch)
    run_food_line_dispatch(tmp_path, date, generate_audio=True)

    transcript_path = tmp_path / "output" / "site" / "food-line" / "audio" / f"{date}-transcript.html"
    transcript_path.write_text(
        transcript_path.read_text(encoding="utf-8") + "\n<p>matched terms source_text_verified the verified record came from Example News</p>\n",
        encoding="utf-8",
    )

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any(str(transcript_path) in failure for failure in result["failures"])
    assert any("internal/debug phrasing" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_fails_on_unfiltered_public_source_table_rows(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    table_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html"
    table_path.write_text(table_path.read_text(encoding="utf-8") + "\n<tr><td>false</td></tr>\n", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("excluded context records" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_ignores_orphaned_stale_edition_pages(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    stale_edition = tmp_path / "output" / "site" / "food-line" / "editions" / "2026-05-01"
    stale_edition.mkdir(parents=True, exist_ok=True)
    (stale_edition / "index.html").write_text("<p>Skip to content Signal mix today: daily=0, provider=0, local=24, background=0.</p>", encoding="utf-8")
    (stale_edition / "source_table.html").write_text("<table><tr><td>false</td></tr></table>", encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is True
    assert "2026-05-01" not in "".join(result.get("checked_files") or [])
    assert all("2026-05-01" not in failure for failure in result["failures"])


def test_food_line_blue_fern_compliance_missing_map_popup_fields_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    map_path = tmp_path / "output" / "site" / "food-line" / "map" / "index.html"
    map_text = map_path.read_text(encoding="utf-8").replace("What happened:", "Summary removed:").replace("What the source says:", "Evidence removed:")
    map_path.write_text(map_text, encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("map popup is missing required fields" in failure.lower() for failure in result["failures"])


def test_food_line_blue_fern_compliance_missing_source_table_verification_columns_fails(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)

    table_path = tmp_path / "output" / "site" / "food-line" / "editions" / date / "source_table.html"
    table_text = table_path.read_text(encoding="utf-8").replace("Verification status", "verification_status_removed")
    table_path.write_text(table_text, encoding="utf-8")

    result = food_line_compliance.run_food_line_blue_fern_compliance(tmp_path, date)

    assert result["ok"] is False
    assert any("missing required headers" in failure.lower() for failure in result["failures"])


def test_food_line_unverified_pressure_records_are_counted_in_diagnostics(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "generic-page-feed",
                "source_name": "Generic Page Feed",
                "publisher": "Test Publisher",
                "source_type": "page",
                "url": "https://example.com/generic-page",
                "source_family": "food_bank_provider",
                "state": "TX",
                "location_name": "Dallas, TX",
                "location_scope": "state_local",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "low",
                "expected_text_basis": "page_text",
                "positive_keywords": ["demand", "shortage", "waitlist", "hours", "capacity", "inventory"],
                "negative_keywords": ["recipe", "restaurant", "menu", "festival", "gala"],
                "affected_group_keywords": ["families", "children"],
                "enabled": True,
                "notes": "Generic page used to ensure registry defaults do not create pressure.",
                "summary_fallback": "Food bank demand increased and pantry lines grew.",
            }
        ],
    )
    html_payload = b"""<!doctype html>
<html>
<head><title>Generic Food Bank Page</title><meta name=\"description\" content=\"Community updates and general information.\"></head>
<body><p>Community bulletin with updates for members today.</p></body></html>"""
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: html_payload)
    assert result["pressure_signal_count"] == 0
    assert result["pressure_demoted_unverified_count"] == 1
    assert result["pressure_registry_only_count"] == 0
    assert result["pressure_evidence_basis_counts"]["page_text_excerpt"] == 1
    assert result["demoted_count_by_extraction_quality"] == {"low": 1}
    assert result["collected_count_by_extraction_quality"] == {"low": 1}


def test_food_line_collector_audit_json_is_written(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "audit-feed",
                "source_name": "Audit Feed",
                "publisher": "Audit Publisher",
                "source_type": "rss",
                "url": "https://example.com/audit",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Audit output test feed.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/audit-item",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    audit_path = Path(result["collector_audit_path"])
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit and audit[0]["source_id"] == "audit-feed"
    assert audit[0]["fetched"] is True
    assert audit[0]["accepted_pressure_count"] == 1
    assert audit[0]["top_rejection_reasons"] == []


def test_food_line_review_csv_is_written_and_includes_evidence_fields(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    p = _manual_path(tmp_path, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = _pressure_row(1, "Food bank sees rising demand from families", "Food bank demand increased and pantry lines grew.", family="local_news", state="TX")
    p.write_text(json.dumps([row], indent=2), encoding="utf-8")
    run_food_line_dispatch(tmp_path, date)
    csv_path = tmp_path / "output" / "review" / "food-line" / date / "pressure_review.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert rows[0]["evidence_text"]
    assert rows[0]["pressure_match_terms"]
    assert rows[0]["pressure_verification_status"]
    assert rows[0]["source_family"] == "local_news"


def test_food_line_fetch_failures_are_counted_by_source_id(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-04"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "failing-feed",
                "source_name": "Failing Feed",
                "publisher": "Failing Publisher",
                "source_type": "rss",
                "url": "https://example.com/failing",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Failure diagnostics feed.",
            },
            {
                "source_id": "healthy-feed",
                "source_name": "Healthy Feed",
                "publisher": "Healthy Publisher",
                "source_type": "rss",
                "url": "https://example.com/healthy",
                "source_family": "national_news",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "source_role_allowed": "pressure_evidence",
                "pressure_required": True,
                "pressure_verification_required": True,
                "freshness_mode": "pressure",
                "max_age_days": 7,
                "extraction_quality": "high",
                "expected_text_basis": "rss_summary",
                "positive_keywords": ["food bank", "SNAP"],
                "negative_keywords": ["recipe", "restaurant", "menu"],
                "affected_group_keywords": ["families"],
                "enabled": True,
                "notes": "Healthy diagnostics feed.",
            },
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/healthy-item",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )

    def fetcher(url: str, timeout: int = 15):
        if "failing" in url:
            raise OSError("network failure")
        return rss_payload

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert result["fetch_failure_count_by_source_id"] == {"failing-feed": 1}
    assert result["source_count"] == 1
    assert result["collected_count_by_extraction_quality"] == {"high": 1}


def test_food_line_candidate_registry_loads(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-one",
                "source_name": "Candidate One",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/feed.rss",
                "source_family": "public_radio",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Manual candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            }
        ],
    )
    candidates = load_food_line_candidate_registry(tmp_path)
    assert len(candidates) == 1
    assert candidates[0]["candidate_url"] == "https://example.com/feed.rss"
    assert candidates[0]["status"] == "candidate"


def test_food_line_production_collector_skips_candidate_registry(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-only",
                "source_name": "Candidate Only",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/feed.rss",
                "source_family": "public_radio",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Manual candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            }
        ],
    )
    (tmp_path / "data" / "dispatches" / "food-line").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "dispatches" / "food-line" / "source_registry.json").write_text("[]", encoding="utf-8")
    (tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").write_text("[]", encoding="utf-8")
    result = run_food_line_dispatch(tmp_path, date)
    assert result["source_count"] == 0
    assert result["pressure_signal_count"] == 0


def test_food_line_candidate_tester_writes_review_and_audit(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-good",
                "source_name": "Candidate Good",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/good.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Good candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            }
        ],
    )
    rss_payload = _rss_payload(
        [
            {
                "title": "Food bank sees rising demand from families",
                "link": "https://example.com/good-item",
                "description": "Food bank demand increased and pantry lines grew.",
            }
        ]
    )
    result = run_food_line_candidate_sources(tmp_path, date, fetcher=lambda _url, timeout=15: rss_payload)
    review_path = Path(result["candidate_review_path"])
    audit_path = Path(result["candidate_audit_path"])
    assert review_path.exists()
    assert audit_path.exists()
    with review_path.open(encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    assert review and review[0]["recommendation"] == "enable"
    assert "noise_score" in review[0]
    assert "pressure_hit_rate" in review[0]
    assert "negative_hit_count" in review[0]
    assert "useful_text_available" in review[0]
    assert review[0]["useful_text_available"] == "true"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit and audit[0]["candidate_url"] == "https://example.com/good.rss"
    assert audit[0]["raw_diagnostics"]


def test_food_line_candidate_workflow_recommendations_cover_keep_reject_cases(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-07"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-quiet",
                "source_name": "Candidate Quiet",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/quiet.rss",
                "source_family": "public_radio",
                "state": "ID",
                "location_name": "Idaho",
                "location_scope": "state_local",
                "candidate_reason": "Quiet working feed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "No current pressure item.",
            },
            {
                "source_id": "candidate-broken",
                "source_name": "Candidate Broken",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/broken.rss",
                "source_family": "public_radio",
                "state": "OR",
                "location_name": "Oregon",
                "location_scope": "state_local",
                "candidate_reason": "Broken feed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Broken.",
            },
            {
                "source_id": "candidate-recipes",
                "source_name": "Candidate Recipes",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/recipes.rss",
                "source_family": "local_news",
                "state": "CA",
                "location_name": "California",
                "location_scope": "state_local",
                "candidate_reason": "Lifestyle-heavy feed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Lifestyle noise.",
            },
        ],
    )
    def fetcher(url: str, timeout: int = 15):
        if "broken" in url:
            raise OSError("network failure")
        if "recipes" in url:
            return _rss_payload(
                [
                    {
                        "title": "Recipe roundup for summer dinners",
                        "link": "https://example.com/recipe",
                        "description": "Lifestyle recipe ideas and cooking tips.",
                    }
                ]
            )
        return _rss_payload(
            [
                {
                    "title": "Community newsletter",
                    "link": "https://example.com/quiet-item",
                    "description": "Community updates and general information.",
                }
            ]
        )

    result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher)
    review_path = Path(result["candidate_review_path"])
    with review_path.open(encoding="utf-8") as handle:
        review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert review["candidate-quiet"]["recommendation"] == "keep_candidate"
    assert review["candidate-broken"]["recommendation"] == "reject"
    assert review["candidate-recipes"]["recommendation"] == "reject"


def test_food_line_candidate_promotion_only_promotes_enable_and_is_idempotent(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-08"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-enable",
                "source_name": "Candidate Enable",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/enable.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Enable candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "tested_good",
                "notes": "Seeded candidate.",
            },
            {
                "source_id": "candidate-keep",
                "source_name": "Candidate Keep",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/keep.rss",
                "source_family": "public_radio",
                "state": "OR",
                "location_name": "Oregon",
                "location_scope": "state_local",
                "candidate_reason": "Keep candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            },
            {
                "source_id": "candidate-reject",
                "source_name": "Candidate Reject",
                "publisher": "Candidate Publisher",
                "candidate_url": "https://example.com/reject.rss",
                "source_family": "local_news",
                "state": "CA",
                "location_name": "California",
                "location_scope": "state_local",
                "candidate_reason": "Reject candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Seeded candidate.",
            },
        ],
    )
    rss_payloads = {
        "enable": _rss_payload(
            [
                {
                    "title": "Food bank sees rising demand from families",
                    "link": "https://example.com/enable-item",
                    "description": "Food bank demand increased and pantry lines grew.",
                }
            ]
        ),
        "keep": _rss_payload(
            [
                {
                    "title": "Community newsletter",
                    "link": "https://example.com/keep-item",
                    "description": "General community updates without pressure evidence.",
                }
            ]
        ),
        "reject": _rss_payload(
            [
                {
                    "title": "Recipe roundup for summer dinners",
                    "link": "https://example.com/reject-item",
                    "description": "Lifestyle recipe ideas and cooking tips.",
                }
            ]
        ),
    }

    def fetcher(url: str, timeout: int = 15):
        if "enable" in url:
            return rss_payloads["enable"]
        if "keep" in url:
            return rss_payloads["keep"]
        return rss_payloads["reject"]

    result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    review_path = Path(result["candidate_review_path"])
    promotion_path = Path(result["candidate_promotion_report_path"])
    assert review_path.exists()
    assert promotion_path.exists()
    with review_path.open(encoding="utf-8") as handle:
        review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert "noise_score" in review["candidate-enable"]
    assert "pressure_hit_rate" in review["candidate-enable"]
    assert "negative_hit_count" in review["candidate-enable"]
    assert "useful_text_available" in review["candidate-enable"]
    assert "source_purpose" in review["candidate-enable"]
    assert "current_or_evergreen" in review["candidate-enable"]
    assert "promotable" in review["candidate-enable"]
    assert "non_promotable_reason" in review["candidate-enable"]
    assert review["candidate-enable"]["source_purpose"] == "current_news"
    with promotion_path.open(encoding="utf-8") as handle:
        promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion["candidate-enable"]["promoted"] == "True"
    assert promotion["candidate-keep"]["promoted"] == "False"
    assert promotion["candidate-reject"]["promoted"] == "False"
    candidate_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    status_by_id = {row["source_id"]: row["status"] for row in candidate_registry}
    assert status_by_id["candidate-enable"] == "enabled"
    assert status_by_id["candidate-keep"] == "candidate"
    assert status_by_id["candidate-reject"] in {"rejected", "quarantined"}
    pressure_registry_path = tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json"
    first_registry = json.loads(pressure_registry_path.read_text(encoding="utf-8"))
    assert sum(1 for row in first_registry if row["source_id"] == "candidate-enable") == 1

    second_result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    second_registry = json.loads(pressure_registry_path.read_text(encoding="utf-8"))
    assert sum(1 for row in second_registry if row["source_id"] == "candidate-enable") == 1
    assert second_result["promoted_candidate_count"] == 1


def test_food_line_candidate_sources_block_non_promotable_source_purposes(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    donation_url = "https://www.feedingamerica.org/ways-to-give/monthly-giving"
    evergreen_url = "https://www.feedingamerica.org/research/hunger-and-poverty-united-states"
    resource_url = "https://www.example.org/find-food"
    current_url = "https://example.com/current.rss"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-donation",
                "source_name": "Monthly Giving & Recurring Donations",
                "publisher": "Feeding America",
                "candidate_url": donation_url,
                "source_family": "food_bank_provider",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "candidate_reason": "Donation page.",
                "expected_text_basis": "page_text",
                "extraction_quality_guess": "medium",
                "pressure_topics_expected": ["donate"],
                "status": "candidate",
                "notes": "Donation page should stay blocked.",
            },
            {
                "source_id": "candidate-evergreen",
                "source_name": "Hunger & Poverty in the United States",
                "publisher": "Feeding America",
                "candidate_url": evergreen_url,
                "source_family": "food_bank_provider",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "candidate_reason": "Evergreen explainer.",
                "expected_text_basis": "page_text",
                "extraction_quality_guess": "medium",
                "pressure_topics_expected": ["hunger facts"],
                "status": "candidate",
                "notes": "Evergreen context should stay blocked.",
            },
            {
                "source_id": "candidate-resource",
                "source_name": "Find food near you",
                "publisher": "Example Resource",
                "candidate_url": resource_url,
                "source_family": "food_bank_provider",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "candidate_reason": "Resource page.",
                "expected_text_basis": "page_text",
                "extraction_quality_guess": "medium",
                "pressure_topics_expected": ["find food"],
                "status": "candidate",
                "notes": "Resource page should stay blocked.",
            },
            {
                "source_id": "candidate-current",
                "source_name": "KLTV current demand article",
                "publisher": "KLTV",
                "candidate_url": current_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "East Texas, TX",
                "location_scope": "state_local",
                "candidate_reason": "Current pressure article.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["food bank", "demand"],
                "status": "candidate",
                "notes": "Current pressure story should remain promotable.",
            },
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == donation_url:
            return b"""<html><head><title>Monthly Giving &amp; Recurring Donations</title></head><body><p>Donate now and give monthly.</p></body></html>"""
        if url == evergreen_url:
            return b"""<html><head><title>Hunger &amp; Poverty in the United States</title></head><body><p>Hunger facts and research overview.</p></body></html>"""
        if url == resource_url:
            return b"""<html><head><title>Find food near you</title></head><body><p>Use our food bank locator and eligibility guide.</p></body></html>"""
        if url == current_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/current-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected url: {url}")

    result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    with Path(result["candidate_review_path"]).open(encoding="utf-8") as handle:
        review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert review["candidate-donation"]["source_purpose"] == "donation_page"
    assert review["candidate-donation"]["recommendation"] == "reject"
    assert review["candidate-evergreen"]["source_purpose"] == "evergreen_context"
    assert review["candidate-evergreen"]["recommendation"] == "keep_candidate"
    assert review["candidate-resource"]["source_purpose"] == "resource_page"
    assert review["candidate-resource"]["recommendation"] == "keep_candidate"
    assert review["candidate-current"]["source_purpose"] == "current_news"
    assert review["candidate-current"]["recommendation"] == "enable"
    assert result["promoted_blocked_by_source_purpose_count"] >= 3
    assert result["rejected_by_source_purpose_count"] >= 1
    with Path(result["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion["candidate-donation"]["promoted"] == "False"
    assert promotion["candidate-evergreen"]["promoted"] == "False"
    assert promotion["candidate-resource"]["promoted"] == "False"
    assert promotion["candidate-current"]["promoted"] == "True"
    candidate_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    status_by_id = {row["source_id"]: row["status"] for row in candidate_registry}
    assert status_by_id["candidate-donation"] in {"rejected", "quarantined"}
    assert status_by_id["candidate-evergreen"] in {"candidate", "tested_weak"}
    assert status_by_id["candidate-resource"] in {"candidate", "tested_weak"}
    assert status_by_id["candidate-current"] == "enabled"


def test_food_line_candidate_discovery_notes_and_intake_template_exist():
    notes_path = Path("data") / "dispatches" / "food-line" / "candidate_source_discovery_notes.md"
    intake_path = Path("data") / "dispatches" / "food-line" / "candidate_source_intake_template.csv"
    notes = notes_path.read_text(encoding="utf-8")
    header = intake_path.read_text(encoding="utf-8").strip().splitlines()[0]
    assert notes_path.exists()
    assert intake_path.exists()
    assert "## A. National recurring sources" in notes
    assert "## B. State and local public media targets" in notes
    assert "## C. Food bank and provider targets" in notes
    assert "## D. Official pressure targets" in notes
    assert '"source_type": "rss"' in notes
    assert "Manual validation checklist" in notes
    assert "Invoke-WebRequest" in notes
    assert "python scripts\\test_food_line_candidate_sources.py --date 2026-06-08" in notes
    expected_columns = [
        "source_id",
        "source_name",
        "publisher",
        "candidate_url",
        "source_family",
        "source_type",
        "state",
        "location_name",
        "location_scope",
        "candidate_reason",
        "expected_text_basis",
        "extraction_quality_guess",
        "pressure_topics_expected",
        "status",
        "notes",
    ]
    assert header.split(",") == expected_columns


def test_food_line_source_purpose_blocks_donation_evergreen_and_resource_pages(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-10"
    donation_url = "https://www.feedingamerica.org/ways-to-give/monthly-giving"
    evergreen_url = "https://www.feedingamerica.org/research/hunger-and-poverty-united-states"
    resource_url = "https://www.example.org/find-food"
    valid_url = "https://www.kltv.com/food-bank-demand"
    _write_pressure_registry(
        tmp_path,
        [
            {
                "source_id": "fa-donation",
                "source_name": "Monthly Giving & Recurring Donations",
                "publisher": "Feeding America",
                "url": donation_url,
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["donate", "monthly giving"],
                "negative_keywords": ["donate"],
                "affected_group_keywords": ["low-income households"],
                "enabled": True,
                "notes": "Donation page should not be mapped as pressure.",
            },
            {
                "source_id": "fa-evergreen",
                "source_name": "Hunger & Poverty in the United States",
                "publisher": "Feeding America",
                "url": evergreen_url,
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["hunger facts"],
                "negative_keywords": ["hunger"],
                "affected_group_keywords": ["low-income households"],
                "enabled": True,
                "notes": "Evergreen context page should not be mapped as pressure.",
            },
            {
                "source_id": "fa-resource",
                "source_name": "Find food near you",
                "publisher": "Feeding America",
                "url": resource_url,
                "source_family": "food_bank_provider",
                "source_type": "page",
                "state": "US",
                "location_name": "United States",
                "location_scope": "national",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["find food"],
                "negative_keywords": ["find food"],
                "affected_group_keywords": ["low-income households"],
                "enabled": True,
                "notes": "Resource page should not be mapped as pressure.",
            },
            {
                "source_id": "kltv-valid",
                "source_name": "KLTV",
                "publisher": "KLTV",
                "url": valid_url,
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "East Texas, TX",
                "location_scope": "state_local",
                "extraction_quality": "medium",
                "expected_text_basis": "page_text",
                "pressure_verification_required": True,
                "positive_keywords": ["food bank", "demand"],
                "negative_keywords": ["recipe"],
                "affected_group_keywords": ["SNAP households"],
                "enabled": True,
                "notes": "Current local pressure article should remain mapped.",
            },
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        if url == donation_url:
            return b"""<html><head><title>Monthly Giving &amp; Recurring Donations</title></head><body><p>Donate now and give monthly.</p></body></html>"""
        if url == evergreen_url:
            return b"""<html><head><title>Hunger &amp; Poverty in the United States</title></head><body><p>Hunger facts and research overview.</p></body></html>"""
        if url == resource_url:
            return b"""<html><head><title>Find food near you</title></head><body><p>Use our food bank locator and eligibility guide.</p></body></html>"""
        if url == valid_url:
            return b"""<html><head><title>KLTV reports rising food-bank demand</title><meta name='description' content='Food banks across Texas are working hard to keep up with rising demand.'></head><body><p>The government shutdown is now in its 4th week and food banks across Texas are working hard to keep up with rising demand. Michael Close, Chief Operating Officer at Swan Food Pantry, has seen a 17% increase in people asking for food assistance.</p></body></html>"""
        raise AssertionError(f"unexpected url: {url}")

    collect_result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert collect_result["rejected_by_source_purpose_count"] >= 3
    assert collect_result["demoted_by_source_purpose_count"] >= 3
    result = run_food_line_dispatch(tmp_path, date, collect=False)
    assert result["pressure_verified_count"] == 1
    assert result["pressure_marker_count"] == 1
    map_data = json.loads((tmp_path / "output" / "site" / "food-line" / "map" / "map_data.json").read_text(encoding="utf-8"))
    assert len(map_data["pressure_markers"]) == 1
    assert map_data["pressure_markers"][0]["source_purpose"] == "current_news"
    assert all("Feeding America" not in str(marker.get("source_title") or "") for marker in map_data["pressure_markers"])
    pressure_registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in pressure_registry}
    assert by_id["fa-donation"]["enabled"] is False
    assert by_id["fa-donation"]["source_purpose"] == "donation_page"
    assert by_id["fa-evergreen"]["enabled"] is False
    assert by_id["fa-evergreen"]["source_purpose"] == "evergreen_context"
    assert by_id["fa-resource"]["enabled"] is False
    assert by_id["fa-resource"]["source_purpose"] == "resource_page"
    assert "not current pressure evidence" in by_id["fa-donation"]["notes"].lower()


def test_food_line_discovery_prefilters_obvious_non_pressure_pages_and_dedupes(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    donation_url = "https://example.com/donate"
    recipe_url = "https://example.com/recipe"
    resource_url = "https://example.com/find-food"
    evergreen_url = "https://example.com/hunger-facts"
    valid_url = "https://example.com/pressure-story"
    _write_source_registry(
        tmp_path,
        [
            {"source_id": "donation-seed", "source_name": "Donate Monthly", "publisher": "Example Provider", "url": donation_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "recipe-seed", "source_name": "Recipe roundup", "publisher": "Example Media", "url": recipe_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "resource-seed", "source_name": "Find food near you", "publisher": "Example Provider", "url": resource_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "evergreen-seed", "source_name": "Hunger & Poverty in the United States", "publisher": "Example Provider", "url": evergreen_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "valid-seed", "source_name": "Rising demand story", "publisher": "Example News", "url": valid_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
        ],
    )
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == donation_url:
            return b"<html><head><title>Donate Monthly</title></head><body><p>Donate now to support our work.</p></body></html>"
        if url == recipe_url:
            return b"<html><head><title>Recipe roundup</title></head><body><p>Recipe ideas and cooking tips.</p></body></html>"
        if url == resource_url:
            return b"<html><head><title>Find food near you</title></head><body><p>Use our food bank locator.</p></body></html>"
        if url == evergreen_url:
            return b"<html><head><title>Hunger &amp; Poverty in the United States</title></head><body><p>Hunger facts and statistics.</p></body></html>"
        if url == valid_url:
            return b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>"
        raise AssertionError(f"unexpected url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    with Path(result["review_path"]).open(encoding="utf-8") as handle:
        review = {row["candidate_url"]: row for row in csv.DictReader(handle)}
    assert review[donation_url]["action"] == "rejected_discovery"
    assert review[donation_url]["rejected_by_prefilter"] == "true"
    assert review[recipe_url]["action"] == "rejected_discovery"
    assert review[recipe_url]["reason"]
    assert review[resource_url]["action"] == "rejected_discovery"
    assert review[evergreen_url]["action"] == "rejected_discovery"
    assert review[valid_url]["action"] == "inserted_candidate"
    assert "source_quality_score" in review[valid_url]
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry) == 1

    result_again = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    registry_again = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry_again) == 1
    assert result_again["updated_count"] >= 1


def test_food_line_candidate_quarantine_cleanup_and_include_quarantined(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    quarantined_url = "https://example.com/quarantined.rss"
    clean_url = "https://example.com/clean.rss"
    noisy_url = "https://example.com/noisy.rss"
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-quarantined",
                "source_name": "Quarantined Candidate",
                "publisher": "Candidate Publisher",
                "candidate_url": quarantined_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Quarantined candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "quarantined",
                "notes": "Should stay skipped unless explicitly included.",
                "reject_count": 3,
            },
            {
                "source_id": "candidate-clean",
                "source_name": "Clean Candidate",
                "publisher": "Candidate Publisher",
                "candidate_url": clean_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Clean candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Clean candidate.",
            },
            {
                "source_id": "candidate-noisy",
                "source_name": "Noisy Candidate",
                "publisher": "Candidate Publisher",
                "candidate_url": noisy_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Noisy candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP", "food bank"],
                "status": "candidate",
                "notes": "Noisy candidate.",
            },
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == quarantined_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/quarantined-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        if url == clean_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/clean-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        if url == noisy_url:
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/noisy-item</link><description>Food bank demand increased and pantry lines grew.</description></item><item><title>Recipe roundup for summer dinners</title><link>https://example.com/noisy-recipe</link><description>Recipe ideas and cooking tips.</description></item><item><title>Menu ideas for the weekend</title><link>https://example.com/noisy-menu</link><description>Restaurant and menu coverage.</description></item><item><title>Festival guide</title><link>https://example.com/noisy-fest</link><description>Food festival coverage.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected url: {url}")

    default_result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True)
    with Path(default_result["candidate_review_path"]).open(encoding="utf-8") as handle:
        default_review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert default_review["candidate-quarantined"]["recommendation"] == "skip_quarantined"
    assert default_result["quarantined_skipped_count"] == 1
    with Path(default_result["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert promotion["candidate-clean"]["promoted"] == "True"
    assert promotion["candidate-noisy"]["promoted"] == "False"
    registry_after_default = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_default = {row["source_id"]: row for row in registry_after_default}
    assert by_id_default["candidate-quarantined"]["status"] == "quarantined"
    assert by_id_default["candidate-clean"]["status"] == "enabled"
    assert by_id_default["candidate-noisy"]["status"] == "tested_good"

    include_result = run_food_line_candidate_sources(tmp_path, date, fetcher=fetcher, promote_enabled=True, include_quarantined=True)
    with Path(include_result["candidate_review_path"]).open(encoding="utf-8") as handle:
        include_review = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert include_review["candidate-quarantined"]["recommendation"] == "enable"
    with Path(include_result["candidate_promotion_report_path"]).open(encoding="utf-8") as handle:
        include_promotion = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert include_promotion["candidate-quarantined"]["promoted"] == "False"
    assert include_promotion["candidate-noisy"]["promoted"] == "False"
    registry_after_include = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_include = {row["source_id"]: row for row in registry_after_include}
    assert by_id_include["candidate-quarantined"]["status"] == "quarantined"
    assert by_id_include["candidate-clean"]["status"] == "enabled"
    assert by_id_include["candidate-noisy"]["status"] == "tested_good"


def test_food_line_candidate_cleanup_reports_and_changes_statuses(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-reject-three",
                "source_name": "Reject Three",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/reject-three.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Repeated rejects.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "rejected",
                "notes": "Repeated rejects.",
                "reject_count": 3,
            },
            {
                "source_id": "candidate-archive",
                "source_name": "Archive Candidate",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/archive.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Repeated failure.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "tested_failed",
                "notes": "Repeated failure.",
                "reject_count": 1,
            },
            {
                "source_id": "candidate-enabled",
                "source_name": "Enabled Candidate",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/enabled.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Enabled source.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "enabled",
                "notes": "Enabled source.",
            },
        ],
    )
    history_path = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            {
                "candidate-archive": {
                    "runs_seen": 4,
                    "runs_fetched": 1,
                    "fetch_failures": 3,
                    "items_seen": 0,
                    "verified_pressure_records": 0,
                    "demoted_records": 0,
                    "rejected_records": 0,
                    "last_verified_pressure_at": "",
                    "last_fetch_error": "HTTPError: 403",
                    "rolling_quality_score": 5,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    result = cleanup_food_line_candidates(tmp_path, mode="normal")
    report_path = Path(result["cleanup_report_path"])
    health_path = Path(result["source_registry_health_report_path"])
    assert report_path.exists()
    assert health_path.exists()
    with report_path.open(encoding="utf-8") as handle:
        report = {row["source_id"]: row for row in csv.DictReader(handle)}
    assert report["candidate-reject-three"]["new_status"] == "archived"
    assert report["candidate-archive"]["new_status"] == "archived"
    assert result["candidate_count_before"] == 3
    assert result["candidate_count_after"] == 3
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}
    assert by_id["candidate-reject-three"]["status"] == "archived"
    assert by_id["candidate-archive"]["status"] == "archived"
    assert by_id["candidate-enabled"]["status"] == "enabled"


def test_food_line_candidate_cleanup_modes_and_dry_run(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-reject-two",
                "source_name": "Reject Two",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/reject-two.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Repeated rejects.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "candidate",
                "notes": "Repeated rejects.",
                "reject_count": 2,
                "test_count": 2,
            },
            {
                "source_id": "candidate-enabled-two",
                "source_name": "Enabled Two",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/enabled-two.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Enabled source.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "enabled",
                "notes": "Enabled source.",
            },
        ],
    )
    (tmp_path / "output" / "review" / "food-line" / "2026-06-11").mkdir(parents=True, exist_ok=True)
    review_path = tmp_path / "output" / "review" / "food-line" / "2026-06-11" / "candidate_source_review.csv"
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "useful_text_available", "recommendation", "noise_score", "fetch_error"])
        writer.writeheader()
        writer.writerow({"source_id": "candidate-reject-two", "useful_text_available": "false", "recommendation": "reject", "noise_score": 95, "fetch_error": ""})
        writer.writerow({"source_id": "candidate-enabled-two", "useful_text_available": "true", "recommendation": "enable", "noise_score": 0, "fetch_error": ""})
    dry_run = cleanup_food_line_candidates(tmp_path, mode="conservative", dry_run=True)
    assert dry_run["dry_run"] is True
    assert dry_run["mode"] == "conservative"
    assert Path(dry_run["cleanup_report_path"]).exists()
    registry_after_dry_run = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_dry_run = {row["source_id"]: row for row in registry_after_dry_run}
    assert by_id_dry_run["candidate-reject-two"]["status"] == "candidate"
    normal = cleanup_food_line_candidates(tmp_path, mode="normal")
    assert normal["quarantined_count"] == 0
    assert normal["archived_count"] >= 1
    registry_after_normal = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_normal = {row["source_id"]: row for row in registry_after_normal}
    assert by_id_normal["candidate-reject-two"]["status"] == "archived"
    aggressive = cleanup_food_line_candidates(tmp_path, mode="aggressive")
    registry_after_aggressive = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id_aggressive = {row["source_id"]: row for row in registry_after_aggressive}
    assert by_id_aggressive["candidate-enabled-two"]["status"] == "enabled"


def test_food_line_candidate_cleanup_archives_repeated_broken_no_text_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-broken",
                "source_name": "Broken Candidate",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/broken.rss",
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Broken candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "low",
                "pressure_topics_expected": ["SNAP"],
                "status": "tested_failed",
                "notes": "Broken candidate.",
                "reject_count": 3,
                "test_count": 3,
            }
        ],
    )
    history_path = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            {
                "candidate-broken": {
                    "runs_seen": 4,
                    "runs_fetched": 1,
                    "fetch_failures": 3,
                    "items_seen": 0,
                    "verified_pressure_records": 0,
                    "demoted_records": 0,
                    "rejected_records": 0,
                    "last_verified_pressure_at": "",
                    "last_fetch_error": "HTTPError: 403",
                    "rolling_quality_score": 5,
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    review_dir = tmp_path / "output" / "review" / "food-line" / "2026-06-11"
    review_dir.mkdir(parents=True, exist_ok=True)
    with (review_dir / "candidate_source_review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "useful_text_available", "recommendation", "noise_score", "fetch_error"])
        writer.writeheader()
        writer.writerow({"source_id": "candidate-broken", "useful_text_available": "false", "recommendation": "reject", "noise_score": 100, "fetch_error": "HTTPError: 403"})
    result = cleanup_food_line_candidates(tmp_path, mode="normal")
    assert result["archived_count"] == 1
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}
    assert by_id["candidate-broken"]["status"] == "archived"


def test_food_line_source_performance_history_updates_on_collection(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-11"
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "history-source",
                "source_name": "History Source",
                "publisher": "Example News",
                "url": "https://example.com/history.rss",
                "source_family": "local_news",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "enabled": True,
                "pressure_verification_required": True,
                "expected_text_basis": "rss_summary",
                "extraction_quality": "high",
            }
        ],
    )
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/history.rss":
            return b"""<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel><item><title>Food bank sees rising demand</title><link>https://example.com/history-item</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected url: {url}")

    result = food_line.collect_food_line_auto_sources(tmp_path, date, fetcher=fetcher)
    assert result["collector_audit_path"]
    history_path = tmp_path / "data" / "dispatches" / "food-line" / "source_performance_history.json"
    assert history_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert "history-source" in history
    assert history["history-source"]["runs_seen"] >= 1
    assert history["history-source"]["items_seen"] >= 1


def test_food_line_candidate_intake_imports_valid_rows_and_skips_templates(tmp_path: Path):
    _ensure_assets(tmp_path)
    existing_registry = _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "candidate-update",
                "source_name": "Existing Candidate",
                "publisher": "Existing Publisher",
                "candidate_url": "https://example.com/existing.rss",
                "source_family": "public_radio",
                "state": "OR",
                "location_name": "Portland, OR",
                "location_scope": "state_local",
                "candidate_reason": "Existing candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "candidate",
                "notes": "Keep this note.",
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])
    csv_path = _write_intake_csv(
        tmp_path,
        [
            {
                "source_id": "",
                "source_name": "",
                "publisher": "",
                "candidate_url": "",
                "source_family": "",
                "source_type": "",
                "state": "",
                "location_name": "",
                "location_scope": "",
                "candidate_reason": "",
                "expected_text_basis": "",
                "extraction_quality_guess": "",
                "pressure_topics_expected": "",
                "status": "",
                "notes": "",
            },
            {
                "source_id": "candidate-new",
                "source_name": "Candidate New",
                "publisher": "New Publisher",
                "candidate_url": "https://example.com/new.rss",
                "source_family": "local_news",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "New imported candidate.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP|food bank",
                "status": "",
                "notes": "Imported candidate.",
            },
            {
                "source_id": "candidate-update",
                "source_name": "Candidate Updated",
                "publisher": "",
                "candidate_url": "https://example.com/existing.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "",
                "location_name": "",
                "location_scope": "",
                "candidate_reason": "",
                "expected_text_basis": "",
                "extraction_quality_guess": "",
                "pressure_topics_expected": "",
                "status": "tested_good",
                "notes": "",
            },
        ],
    )
    result = import_food_line_candidate_intake(tmp_path, csv_path)
    assert result["imported_count"] == 1
    assert result["updated_count"] == 1
    assert result["skipped_count"] == 1
    assert result["rejected_count"] == 0
    report_path = Path(result["report_path"])
    assert report_path.exists()
    with report_path.open(encoding="utf-8") as handle:
        report = list(csv.DictReader(handle))
    actions = {row["source_id"]: row["action"] for row in report if row["source_id"]}
    assert actions["candidate-new"] == "inserted"
    assert actions["candidate-update"] == "updated"
    registry = json.loads(existing_registry.read_text(encoding="utf-8"))
    by_id = {row["source_id"]: row for row in registry}
    assert by_id["candidate-new"]["status"] == "candidate"
    assert by_id["candidate-new"]["source_type"] == "rss"
    assert by_id["candidate-update"]["source_name"] == "Candidate Updated"
    assert by_id["candidate-update"]["notes"] == "Keep this note."
    assert json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8")) == []


def test_food_line_candidate_intake_rejects_invalid_rows(tmp_path: Path):
    _ensure_assets(tmp_path)
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    csv_path = _write_intake_csv(
        tmp_path,
        [
            {
                "source_id": "candidate-dup",
                "source_name": "Candidate Dup",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/dup.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Valid row.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-dup",
                "source_name": "Candidate Dup 2",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/dup2.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Duplicate row.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "",
                "source_name": "Missing Id",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/missing-id.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Missing source_id.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-bad",
                "source_name": "Candidate Bad",
                "publisher": "Publisher",
                "candidate_url": "ftp://example.com/bad.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Invalid URL.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-bad-type",
                "source_name": "Candidate Bad Type",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/bad-type.rss",
                "source_family": "public_radio",
                "source_type": "invalid",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Invalid source_type.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "candidate",
                "notes": "",
            },
            {
                "source_id": "candidate-bad-status",
                "source_name": "Candidate Bad Status",
                "publisher": "Publisher",
                "candidate_url": "https://example.com/bad-status.rss",
                "source_family": "public_radio",
                "source_type": "rss",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Invalid status.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": "SNAP",
                "status": "bogus",
                "notes": "",
            },
        ],
    )
    result = import_food_line_candidate_intake(tmp_path, csv_path)
    assert result["imported_count"] == 1
    assert result["updated_count"] == 0
    assert result["skipped_count"] == 0
    assert result["rejected_count"] == 5
    with Path(result["report_path"]).open(encoding="utf-8") as handle:
        report = list(csv.DictReader(handle))
    reasons = {row["source_id"]: row["reason"] for row in report}
    assert reasons["candidate-dup"] == "duplicate source_id in CSV"
    assert "missing source_id" in " ".join(row["reason"] for row in report if not row["source_id"])
    assert reasons["candidate-bad"] == "candidate_url must use http or https"
    assert reasons["candidate-bad-type"].startswith("invalid source_type")
    assert reasons["candidate-bad-status"].startswith("invalid status")
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry) == 1
    assert registry[0]["source_id"] == "candidate-dup"


def test_food_line_source_discovery_queries_load():
    queries = load_food_line_source_discovery_queries(Path(__file__).parent.parent)
    assert queries
    assert all("template" in row for row in queries)
    assert any("{state}" in row["template"] for row in queries)
    assert all("rolling_query_quality_score" in row for row in queries)
    assert any("Summer EBT" in row["template"] for row in queries)
    assert any("Feeding America" in row["template"] for row in queries)
    assert any("SNAP benefits delayed" in row["template"] for row in queries)


def test_food_line_source_discovery_writes_review_and_audit_and_inserts_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    html_payload = b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising and SNAP delays are affecting households.</p></body></html>"""

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return html_payload
        if url == "https://example.com/feeds/austin.rss":
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    review_path = Path(result["review_path"])
    audit_path = Path(result["audit_path"])
    query_report_path = Path(result["query_performance_report_path"])
    assert review_path.exists()
    assert audit_path.exists()
    assert query_report_path.exists()
    with review_path.open(encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    assert review
    assert any(row["action"] == "inserted_candidate" for row in review)
    assert any(row["candidate_url"] == "https://example.com/feeds/austin.rss" for row in review)
    assert "source_purpose" in review[0]
    assert "current_or_evergreen" in review[0]
    assert "promotable" in review[0]
    assert "non_promotable_reason" in review[0]
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert any(row["candidate_url"] == "https://example.com/feeds/austin.rss" for row in registry)
    assert json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8")) == []


def test_food_line_source_discovery_limits_skip_blocked_and_prioritize_quality(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    donation_url = "https://example.com/donate"
    recipe_url = "https://example.com/recipe"
    good_url = "https://www.kut.org/story"
    other_url = "https://example.com/other-story"
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {"source_id": "donation-seed", "source_name": "Donate Monthly", "publisher": "Example Provider", "url": donation_url, "source_family": "food_bank_provider", "source_type": "page", "state": "US", "location_name": "United States", "location_scope": "national", "enabled": True},
            {"source_id": "recipe-seed", "source_name": "Recipe roundup", "publisher": "Example Media", "url": recipe_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "good-seed", "source_name": "Rising demand story", "publisher": "KUT", "url": good_url, "source_family": "public_radio", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "other-seed", "source_name": "Other demand story", "publisher": "Example News", "url": other_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        payloads = {
            donation_url: b"<html><head><title>Donate Monthly</title></head><body><p>Donate now.</p></body></html>",
            recipe_url: b"<html><head><title>Recipe roundup</title></head><body><p>Recipe ideas and cooking tips.</p></body></html>",
            good_url: b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>",
            other_url: b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>",
        }
        if url in payloads:
            return payloads[url]
        raise AssertionError(url)

    result = discover_food_line_sources(
        tmp_path,
        date,
        fetcher=fetcher,
        write_candidates=True,
        max_insertions=5,
        max_candidates_total=10,
        min_source_quality_score=0.45,
    )
    with Path(result["review_path"]).open(encoding="utf-8") as handle:
        review = {row["candidate_url"]: row for row in csv.DictReader(handle)}
    assert review[donation_url]["action"] == "rejected_discovery"
    assert review[recipe_url]["action"] == "rejected_discovery"
    assert review[good_url]["action"] == "inserted_candidate"
    assert int(review[good_url]["priority_bonus"]) > int(review[other_url]["priority_bonus"])
    assert result["inserted_count"] >= 1
    assert result["rejected_count"] >= 2
    assert result["query_performance_report_path"]
    query_report = Path(result["query_performance_report_path"])
    assert query_report.exists()
    with query_report.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and "recommended_action" in rows[0]
    first_row = next(row for row in rows if row["query_template"].endswith("food bank demand RSS"))
    assert float(first_row["rolling_query_quality_score"]) >= 0
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert any(row["candidate_url"] == good_url for row in registry)


def test_food_line_source_discovery_respects_max_insertions(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    _write_candidate_registry(tmp_path, [])
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {"source_id": "first-seed", "source_name": "First Seed", "publisher": "Example News", "url": first_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
            {"source_id": "second-seed", "source_name": "Second Seed", "publisher": "Example News", "url": second_url, "source_family": "local_news", "source_type": "page", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "enabled": True},
        ],
    )

    def fetcher(url: str, timeout: int = 15):
        payload = b"<html><head><title>Food bank sees rising demand</title></head><body><p>Food bank demand is rising and pantry lines grew.</p></body></html>"
        if url in {first_url, second_url}:
            return payload
        raise AssertionError(url)

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, max_insertions=1, max_candidates_total=10)
    assert result["inserted_count"] == 1
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    assert len(registry) == 1


def test_food_line_source_discovery_dedupes_and_preserves_final_status(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    discovered_url = "https://example.com/feeds/austin.rss"
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    _write_candidate_registry(
        tmp_path,
        [
            {
                "source_id": "existing-final",
                "source_name": "Existing Final",
                "publisher": "Seed Publisher",
                "candidate_url": discovered_url,
                "source_family": "local_news",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "candidate_reason": "Previously reviewed.",
                "expected_text_basis": "rss_summary",
                "extraction_quality_guess": "high",
                "pressure_topics_expected": ["SNAP"],
                "status": "rejected",
                "notes": "Final status should remain.",
            }
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising.</p></body></html>"""
        if url == discovered_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True)
    assert result["discovered_candidate_count"] >= 1
    assert result["skipped_count"] >= 1
    registry = json.loads((tmp_path / "data" / "dispatches" / "food-line" / "candidate_source_registry.json").read_text(encoding="utf-8"))
    row = next(item for item in registry if item["candidate_url"] == discovered_url)
    assert row["status"] == "rejected"
    assert json.loads((tmp_path / "data" / "dispatches" / "food-line" / "pressure_source_registry.json").read_text(encoding="utf-8")) == []


def test_food_line_source_discovery_skips_quarantined_and_archived_candidates(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-12"
    source_url = "https://example.com/feeds/austin.rss"
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    _write_candidate_registry(
        tmp_path,
        [
            {"source_id": "quarantined-one", "source_name": "Quarantined One", "publisher": "Seed Publisher", "candidate_url": source_url, "source_family": "local_news", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "candidate_reason": "Old candidate.", "expected_text_basis": "rss_summary", "extraction_quality_guess": "high", "pressure_topics_expected": ["SNAP"], "status": "quarantined", "notes": "Quarantined."},
            {"source_id": "archived-one", "source_name": "Archived One", "publisher": "Seed Publisher", "candidate_url": "https://example.com/feeds/archive.rss", "source_family": "local_news", "state": "TX", "location_name": "Austin, TX", "location_scope": "state_local", "candidate_reason": "Old candidate.", "expected_text_basis": "rss_summary", "extraction_quality_guess": "high", "pressure_topics_expected": ["SNAP"], "status": "archived", "notes": "Archived."},
        ],
    )
    _write_pressure_registry(tmp_path, [])

    def fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising.</p></body></html>"""
        if url == source_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(url)

    result = discover_food_line_sources(tmp_path, date, fetcher=fetcher, write_candidates=True, skip_quarantined=True, skip_archived=True)
    assert result["skipped_quarantined_count"] >= 1
    assert result["skipped_archived_count"] >= 1


def test_food_line_discovered_candidates_are_processed_by_candidate_tester(tmp_path: Path):
    _ensure_assets(tmp_path)
    date = "2026-06-09"
    feed_url = "https://example.com/feeds/austin.rss"
    _write_pressure_registry(tmp_path, [])
    _write_source_registry(
        tmp_path,
        [
            {
                "source_id": "seed-home",
                "source_name": "Seed Home",
                "publisher": "Seed Publisher",
                "url": "https://example.com/home",
                "source_family": "local_news",
                "source_type": "page",
                "state": "TX",
                "location_name": "Austin, TX",
                "location_scope": "state_local",
                "notes": "Seed homepage.",
                "enabled": True,
            }
        ],
    )
    _write_candidate_registry(tmp_path, [])

    def discovery_fetcher(url: str, timeout: int = 15):
        if url == "https://example.com/home":
            return b"""<html><head><title>Food bank demand in Austin</title><link rel=\"alternate\" type=\"application/rss+xml\" href=\"https://example.com/feeds/austin.rss\"></head><body><p>Food bank demand is rising and SNAP delays are affecting households.</p></body></html>"""
        if url == feed_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    discovery_result = discover_food_line_sources(tmp_path, date, fetcher=discovery_fetcher, write_candidates=True)
    assert discovery_result["inserted_count"] >= 1

    def candidate_fetcher(url: str, timeout: int = 15):
        if url == feed_url:
            return b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel><item><title>Food bank sees rising demand from families</title><link>https://example.com/story</link><description>Food bank demand increased and pantry lines grew.</description></item></channel></rss>"""
        raise AssertionError(f"unexpected fetch url: {url}")

    candidate_result = run_food_line_candidate_sources(tmp_path, date, fetcher=candidate_fetcher)
    with Path(candidate_result["candidate_review_path"]).open(encoding="utf-8") as handle:
        review = list(csv.DictReader(handle))
    assert any(row["recommendation"] == "enable" for row in review)
    assert any(row["candidate_url"] == feed_url for row in review)


