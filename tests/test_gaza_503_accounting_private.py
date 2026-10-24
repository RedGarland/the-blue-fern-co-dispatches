import argparse
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from bluefern_dispatches import gaza_sources
from scripts import run_daily_gaza as daily


class _Response:
    def __init__(self, body: bytes = b"<rss/>"):
        self.status = 200
        self.headers = {"Content-Type": "application/rss+xml"}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def _write_config(root: Path) -> None:
    path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: transient-google
    name: Transient Google
    url: https://news.google.com/rss/search?q=Gaza
    type: google_news_rss
    enabled: true
    publisher: Google News
    reliability_tier: discovery
    category_hint: humanitarian
    region_scope: Gaza
  - source_id: direct-filtered
    name: Direct Filtered
    url: https://example.test/direct.xml
    type: rss
    enabled: true
    publisher: Direct Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
""",
        encoding="utf-8",
    )


def _write_three_lane_config(root: Path) -> None:
    path = root / "data" / "dispatches" / "gaza" / "sources.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """sources:
  - source_id: accepted-rss
    name: Accepted RSS
    url: https://example.test/accepted.xml
    type: rss
    enabled: true
    publisher: Accepted Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
  - source_id: filtered-rss
    name: Filtered RSS
    url: https://example.test/filtered.xml
    type: rss
    enabled: true
    publisher: Filtered Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
  - source_id: failed-rss
    name: Failed RSS
    url: https://example.test/failed.xml
    type: rss
    enabled: true
    publisher: Failed Publisher
    reliability_tier: test
    category_hint: humanitarian
    region_scope: Gaza
""",
        encoding="utf-8",
    )


def _rss_body(*, gaza: bool) -> bytes:
    if gaza:
        item = "<item><title>Gaza hospital update</title><link>https://example.test/gaza</link><pubDate>Thu, 22 Oct 2026 08:00:00 GMT</pubDate><description>Humanitarian Gaza update.</description></item>"
    else:
        item = "<item><title>Local weather</title><link>https://example.test/weather</link><pubDate>Thu, 22 Oct 2026 08:00:00 GMT</pubDate><description>Regional forecast only.</description></item>"
    return ("<?xml version='1.0'?><rss><channel>" + item + "</channel></rss>").encode("utf-8")


def _manual_record() -> dict:
    return {
        "source_record_id": "manual-record-1",
        "source_id": "manual-supplement",
        "provider_id": "manual-supplement",
        "title": "Manual Gaza update",
        "url": "https://example.test/manual-gaza",
        "canonical_url": "https://example.test/manual-gaza",
        "publisher": "Manual Publisher",
        "published_at": "2026-10-22T08:00:00+00:00",
        "retrieved_at": "2026-10-22T09:00:00+00:00",
        "summary_or_snippet": "Manual Gaza update.",
        "content_text": "Manual Gaza update.",
        "source_type": "rss",
        "collector_source_type": "rss",
        "region_scope": "Gaza",
        "category_hint": "humanitarian",
        "reliability_tier": "manual",
        "source_tier": "manual_supplements",
        "source_group": "manual_supplements",
        "discovery_role": "manual_operator",
        "attribution_mode": "manual_operator",
        "claim_status": "manual_operator",
    }


def _daily_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        date="2026-10-22",
        source_mode="both",
        min_sources=0,
        max_sources=12,
    )


def _collected_auto_result(*, status: str, records: list[dict] | None = None) -> dict:
    records = list(records or [])
    failed = [] if not status.startswith("incomplete_") else [{"source_id": "google-1", "reason": "HTTPError: 503", "status_code": 503}]
    return {
        "ok": not bool(failed),
        "source_file": None,
        "source_count": len(records),
        "sources": records,
        "warnings": [],
        "errors": [],
        "failed_source_ids": failed,
        "no_match_source_ids": [] if records else [{"source_id": "direct-1", "reason": "no matching Gaza items"}],
        "providers_configured": ["google-1", "direct-1"],
        "providers_attempted": ["google-1", "direct-1"],
        "providers_successful": ["google-1"] if records else [],
        "providers_fetch_succeeded": ["google-1", "direct-1"] if not failed else ["direct-1"],
        "providers_no_matches": [] if records else ["direct-1"],
        "provider_diagnostics": [],
        "stage_counts": {
            "enabled_providers_configured": 2,
            "providers_attempted": 2,
            "providers_successful": len(records),
            "providers_fetch_succeeded": 2 if not failed else 1,
            "providers_no_matches": 0 if records else 1,
            "providers_failed": len(failed),
            "raw_candidates": len(records),
            "accepted_before_rank": len(records),
        },
        "source_coverage_status": status,
        "collection_completion_status": status,
        "no_update_eligible": status == "complete_no_qualifying_records",
        "global_source_cap_exclusions": [],
        "skipped_providers": [],
        "working_providers": ["google-1"] if records else [],
        "rejected_by_reason": {},
        "top_rejected_examples": [],
        "review_candidates": [],
    }


def test_google_503_retries_same_verified_feed_and_records_recovery(monkeypatch):
    calls = []
    sleeps = []

    def fake_open(request, timeout):
        calls.append((request.full_url, timeout))
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)
        return _Response(b"<rss><channel/></rss>")

    monkeypatch.setenv("GAZA_FETCH_BACKEND", "python")
    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", lambda delay: sleeps.append(delay))

    result = gaza_sources.fetch_feed_payload(
        "google-test", "https://news.google.com/rss/search?q=Gaza", timeout=7
    )

    assert result["ok"] is True
    assert result["attempt_count"] == 3
    assert result["retry_count"] == 2
    assert result["transient_retry_status"] == "recovered"
    assert sleeps == [1.0, 2.0]
    assert [url for url, _timeout in calls] == [calls[0][0]] * 3
    assert all(timeout == 7 for _url, timeout in calls)


def test_successful_fetch_with_zero_qualifying_items_is_not_provider_failure(tmp_path, monkeypatch):
    _write_config(tmp_path)

    def fake_fetch(source_id, url, timeout=20):
        if source_id == "transient-google":
            return {
                "ok": False,
                "source_id": source_id,
                "url": url,
                "status_code": 503,
                "failure_reason": "HTTPError: 503",
                "exception_type": "HTTPError",
                "tls_error": False,
                "backend_used": "python",
                "content_bytes": None,
                "content_text": None,
                "attempt_count": 3,
                "retry_count": 2,
                "transient_retry_status": "exhausted",
            }
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": b"<?xml version='1.0'?><rss><channel><item><title>Local weather</title><link>https://example.test/weather</link><pubDate>2026-10-22T08:00:00+00:00</pubDate><description>Regional forecast only.</description></item></channel></rss>",
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_fetch)
    result = gaza_sources.collect_gaza_sources(tmp_path, "2026-10-22", min_sources=0)

    assert result["source_count"] == 0
    assert result["failed_source_ids"] == [
        {"source_id": "transient-google", "reason": "HTTPError: 503", "status_code": 503, "tls_error": False, "backend_used": "python", "exception_type": "HTTPError"}
    ]
    assert result["no_match_source_ids"] == [
        {"source_id": "direct-filtered", "reason": "no matching Gaza items for 2026-10-22"}
    ]
    assert result["providers_fetch_succeeded"] == ["direct-filtered"]
    assert result["providers_no_matches"] == ["direct-filtered"]
    assert result["stage_counts"]["providers_failed"] == 1
    assert result["stage_counts"]["providers_fetch_succeeded"] == 1
    assert result["stage_counts"]["providers_no_matches"] == 1
    assert result["source_coverage_status"] == "incomplete_provider_failures"
    assert result["collection_completion_status"] == "incomplete_provider_failures"
    assert result["no_update_eligible"] is False
    direct_diag = next(item for item in result["provider_diagnostics"] if item["source_id"] == "direct-filtered")
    assert direct_diag["status"] == "no_matches"


def test_google_503_retry_exhaustion_is_bounded_and_remains_failed(monkeypatch):
    calls = []
    sleeps = []

    def always_503(request, timeout):
        calls.append((request.full_url, timeout))
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setenv("GAZA_FETCH_BACKEND", "python")
    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", always_503)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", lambda delay: sleeps.append(delay))

    result = gaza_sources.fetch_feed_payload(
        "google-test", "https://news.google.com/rss/search?q=Gaza", timeout=4
    )

    assert result["ok"] is False
    assert result["status_code"] == 503
    assert result["attempt_count"] == 3
    assert result["retry_count"] == 2
    assert result["transient_retry_status"] == "exhausted"
    assert sleeps == [1.0, 2.0]
    assert len(calls) == 3


def test_google_503_to_404_records_terminal_non_transient_retry_status(monkeypatch):
    calls = []
    sleeps = []

    def sequence(request, timeout):
        calls.append((request.full_url, timeout))
        code = 503 if len(calls) == 1 else 404
        raise urllib.error.HTTPError(request.full_url, code, "HTTP failure", {}, None)

    monkeypatch.setenv("GAZA_FETCH_BACKEND", "python")
    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", sequence)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", lambda delay: sleeps.append(delay))

    result = gaza_sources.fetch_feed_payload(
        "google-test", "https://news.google.com/rss/search?q=Gaza", timeout=4
    )

    assert result["ok"] is False
    assert result["status_code"] == 404
    assert result["attempt_count"] == 2
    assert result["retry_count"] == 1
    assert result["transient_retry_status"] == "terminal_non_transient"
    assert sleeps == [1.0]


def test_tls_curl_fallback_preserves_fetch_attempt_telemetry(monkeypatch):
    monkeypatch.setenv("GAZA_FETCH_BACKEND", "auto")

    def tls_failure(request, timeout):
        raise OSError("certificate verification failed")

    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", tls_failure)
    monkeypatch.setattr(
        gaza_sources,
        "_curl_fetch",
        lambda url, timeout, allow_no_revoke: (b"<rss><channel/></rss>", "curl"),
    )

    result = gaza_sources.fetch_feed_payload(
        "direct-test", "https://example.test/direct.xml", timeout=4
    )

    assert result["ok"] is True
    assert result["backend_used"] == "curl"
    assert result["attempt_count"] == 2
    assert result["retry_count"] == 0
    assert result["transient_retry_status"] == "fallback_recovered"


def test_google_503_then_tls_then_curl_preserves_retry_and_fallback_telemetry(monkeypatch):
    calls = []

    def sequence(request, timeout):
        calls.append((request.full_url, timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)
        raise OSError("certificate verification failed")

    monkeypatch.setenv("GAZA_FETCH_BACKEND", "auto")
    monkeypatch.setattr(gaza_sources.urllib.request, "urlopen", sequence)
    monkeypatch.setattr(gaza_sources.time_module, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        gaza_sources,
        "_curl_fetch",
        lambda url, timeout, allow_no_revoke: (b"<rss><channel/></rss>", "curl"),
    )

    result = gaza_sources.fetch_feed_payload(
        "google-test", "https://news.google.com/rss/search?q=Gaza", timeout=4
    )

    assert result["ok"] is True
    assert result["backend_used"] == "curl"
    assert result["attempt_count"] == 3
    assert result["retry_count"] == 1
    assert result["transient_retry_status"] == "fallback_recovered"


def test_mixed_accepted_no_match_and_failed_lanes_keep_distinct_terminal_accounting(tmp_path, monkeypatch):
    _write_three_lane_config(tmp_path)

    def fake_fetch(source_id, url, timeout=20):
        if source_id == "failed-rss":
            return {
                "ok": False,
                "source_id": source_id,
                "url": url,
                "status_code": 503,
                "failure_reason": "HTTPError: 503",
                "exception_type": "HTTPError",
                "tls_error": False,
                "backend_used": "python",
                "content_bytes": None,
                "content_text": None,
            }
        return {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_body(gaza=source_id == "accepted-rss"),
            "content_text": None,
        }

    monkeypatch.setattr(gaza_sources, "fetch_feed_payload", fake_fetch)
    result = gaza_sources.collect_gaza_sources(tmp_path, "2026-10-22", min_sources=0)

    assert result["source_count"] == 1
    assert result["providers_successful"] == ["accepted-rss"]
    assert result["providers_fetch_succeeded"] == ["accepted-rss", "filtered-rss"]
    assert result["providers_no_matches"] == ["filtered-rss"]
    assert [item["source_id"] for item in result["failed_source_ids"]] == ["failed-rss"]
    assert result["stage_counts"]["providers_with_accepted_records"] == 1
    assert result["stage_counts"]["providers_fetch_succeeded"] == 2
    assert result["stage_counts"]["providers_no_matches"] == 1
    assert result["stage_counts"]["providers_failed"] == 1
    assert result["source_coverage_status"] == "incomplete_provider_failures"
    assert result["no_update_eligible"] is False


def test_zero_accepted_items_are_no_update_eligible_only_after_complete_coverage(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda source_id, url, timeout=20: {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_body(gaza=False),
            "content_text": None,
        },
    )

    result = gaza_sources.collect_gaza_sources(tmp_path, "2026-10-22", min_sources=0)

    assert result["source_count"] == 0
    assert result["failed_source_ids"] == []
    assert result["stage_counts"]["enabled_providers_configured"] == 2
    assert result["providers_configured"] == ["transient-google", "direct-filtered"]
    assert result["providers_no_matches"] == ["direct-filtered", "transient-google"]
    assert result["source_coverage_status"] == "complete_no_qualifying_records"
    assert result["collection_completion_status"] == "complete_no_qualifying_records"
    assert result["no_update_eligible"] is True


def test_ocha_provider_is_in_configured_denominator(tmp_path, monkeypatch):
    definition = gaza_sources.SourceDefinition(
        source_id="ocha-test",
        name="OCHA Test",
        url="https://example.test/not-ocha",
        type=gaza_sources.OCHA_REPORT_INDEX_TYPE,
        enabled=True,
        publisher="OCHA",
        reliability_tier="official",
        category_hint="humanitarian",
        region_scope="Gaza",
    )
    monkeypatch.setattr(gaza_sources, "load_sources_config", lambda _path: [definition])

    result = gaza_sources.collect_gaza_sources(tmp_path, "2026-10-22", min_sources=0, prefer_manual=False)

    assert result["stage_counts"]["enabled_providers_configured"] == 1
    assert result["providers_configured"] == ["ocha-test"]
    assert result["source_coverage_status"] == "incomplete_provider_failures"


def test_accepted_records_keep_successful_publication_eligibility(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr(
        gaza_sources,
        "fetch_feed_payload",
        lambda source_id, url, timeout=20: {
            "ok": True,
            "source_id": source_id,
            "url": url,
            "status_code": 200,
            "failure_reason": None,
            "exception_type": None,
            "tls_error": False,
            "backend_used": "python",
            "content_type": "application/rss+xml",
            "content_encoding": "",
            "content_bytes": _rss_body(gaza=True),
            "content_text": None,
        },
    )

    result = gaza_sources.collect_gaza_sources(tmp_path, "2026-10-22", min_sources=1)

    assert result["ok"] is True
    assert result["source_count"] == 1
    assert result["source_coverage_status"] == "complete_with_qualifying_records"
    assert result["no_update_eligible"] is False


def test_daily_context_preserves_incomplete_collection_and_blocks_no_update(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    date_text = "2026-10-22"
    log_path = root / "run.log"
    log_path.write_text("", encoding="utf-8")
    args = argparse.Namespace(
        date=date_text,
        source_mode="auto",
        min_sources=1,
        max_sources=12,
    )
    summary = {"warnings": [], "errors": [], "failed_source_ids": []}
    collected = {
        "ok": True,
        "source_count": 0,
        "source_file": None,
        "sources": [],
        "warnings": [],
        "errors": [],
        "failed_source_ids": [{"source_id": "google-1", "reason": "HTTPError: 503", "status_code": 503}],
        "no_match_source_ids": [{"source_id": "direct-1", "reason": "no matching Gaza items for 2026-10-22"}],
        "providers_configured": ["google-1", "direct-1"],
        "providers_attempted": ["google-1", "direct-1"],
        "providers_successful": [],
        "providers_fetch_succeeded": ["direct-1"],
        "providers_no_matches": ["direct-1"],
        "provider_diagnostics": [],
        "stage_counts": {"providers_failed": 1, "providers_fetch_succeeded": 1, "providers_no_matches": 1},
        "source_coverage_status": "incomplete_provider_failures",
        "collection_completion_status": "incomplete_provider_failures",
        "no_update_eligible": False,
        "global_source_cap_exclusions": [],
        "skipped_providers": [],
        "working_providers": [],
        "rejected_by_reason": {"rejected_off_topic": 1},
        "top_rejected_examples": [],
        "review_candidates": [],
    }
    monkeypatch.setattr(daily, "ROOT", root)
    monkeypatch.setattr(daily, "ensure_source_folder", lambda _date: None)
    monkeypatch.setattr(daily, "source_file_for", lambda _date: root / "manual_sources.json")
    monkeypatch.setattr(daily, "_existing_collection_context", lambda _date: None)
    monkeypatch.setattr(daily, "collect_gaza_sources", lambda *args, **kwargs: collected)

    source_path, records, _ = daily.collect_or_load_sources(args, summary, log_path)

    context_path = root / "data" / "dispatches" / "gaza" / "editions" / date_text / "source_collection_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert source_path == root / "manual_sources.json"
    assert records == []
    assert summary["source_coverage_status"] == "incomplete_provider_failures"
    assert summary["no_update_eligible"] is False
    assert context["provider_failures"][0]["status_code"] == 503
    assert context["no_match_source_ids"] == collected["no_match_source_ids"]
    assert context["providers_fetch_succeeded"] == ["direct-1"]
    assert context["source_coverage_status"] == "incomplete_provider_failures"


def test_both_mode_auto_empty_plus_manual_valid_is_complete_with_records(tmp_path, monkeypatch):
    manual_path = tmp_path / "manual_sources.json"
    manual_path.write_text("manual", encoding="utf-8")
    manual_records = [_manual_record()]
    args = _daily_args(tmp_path)
    summary = {"warnings": [], "errors": [], "failed_source_ids": []}
    log_path = tmp_path / "run.log"
    log_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "ensure_source_folder", lambda _date: None)
    monkeypatch.setattr(daily, "source_file_for", lambda _date: manual_path)
    monkeypatch.setattr(daily, "_existing_collection_context", lambda _date: None)
    monkeypatch.setattr(daily, "validate_source_file", lambda _path, _minimum: (manual_records, []))
    monkeypatch.setattr(daily, "collect_gaza_sources", lambda *args, **kwargs: _collected_auto_result(status="complete_no_qualifying_records"))
    monkeypatch.setattr(daily, "_preserve_manual_governance_fields", lambda records, preserved: (records, [], []))
    monkeypatch.setattr(daily, "write_source_records", lambda *args, **kwargs: manual_path)

    source_path, records, _ = daily.collect_or_load_sources(args, summary, log_path)

    context = json.loads((tmp_path / "data" / "dispatches" / "gaza" / "editions" / args.date / "source_collection_context.json").read_text(encoding="utf-8"))
    assert source_path == manual_path
    assert len(records) == 1
    assert summary["source_coverage_status"] == "complete_with_qualifying_records"
    assert summary["no_update_eligible"] is False
    assert context["source_coverage_status"] == "complete_with_qualifying_records"
    assert context["no_update_eligible"] is False


def test_both_mode_auto_failure_plus_manual_valid_remains_incomplete(tmp_path, monkeypatch):
    manual_path = tmp_path / "manual_sources.json"
    manual_path.write_text("manual", encoding="utf-8")
    manual_records = [_manual_record()]
    args = _daily_args(tmp_path)
    summary = {"warnings": [], "errors": [], "failed_source_ids": []}
    log_path = tmp_path / "run.log"
    log_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "ensure_source_folder", lambda _date: None)
    monkeypatch.setattr(daily, "source_file_for", lambda _date: manual_path)
    monkeypatch.setattr(daily, "_existing_collection_context", lambda _date: None)
    monkeypatch.setattr(daily, "validate_source_file", lambda _path, _minimum: (manual_records, []))
    monkeypatch.setattr(daily, "collect_gaza_sources", lambda *args, **kwargs: _collected_auto_result(status="incomplete_provider_failures"))
    monkeypatch.setattr(daily, "_preserve_manual_governance_fields", lambda records, preserved: (records, [], []))
    monkeypatch.setattr(daily, "write_source_records", lambda *args, **kwargs: manual_path)

    source_path, records, _ = daily.collect_or_load_sources(args, summary, log_path)

    context = json.loads((tmp_path / "data" / "dispatches" / "gaza" / "editions" / args.date / "source_collection_context.json").read_text(encoding="utf-8"))
    assert source_path == manual_path
    assert len(records) == 1
    assert summary["source_coverage_status"] == "incomplete_provider_failures"
    assert summary["no_update_eligible"] is False
    assert context["source_coverage_status"] == "incomplete_provider_failures"
    assert context["no_update_eligible"] is False
    assert context["provider_failures"] == _collected_auto_result(status="incomplete_provider_failures")["failed_source_ids"]




def test_auto_collection_exception_sets_incomplete_terminal_status(tmp_path, monkeypatch):
    args = argparse.Namespace(date="2026-10-22", source_mode="auto", min_sources=1, max_sources=12)
    summary = {"warnings": [], "errors": [], "failed_source_ids": []}
    log_path = tmp_path / "run.log"
    log_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "ensure_source_folder", lambda _date: None)
    monkeypatch.setattr(daily, "source_file_for", lambda _date: tmp_path / "manual_sources.json")
    monkeypatch.setattr(daily, "_existing_collection_context", lambda _date: None)
    monkeypatch.setattr(daily, "collect_gaza_sources", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("collector unavailable")))

    source_path, records, restore = daily.collect_or_load_sources(args, summary, log_path)

    assert (source_path, records, restore) == (None, [], None)
    assert summary["source_coverage_status"] == "incomplete_auto_collection"
    assert summary["collection_completion_status"] == "incomplete_auto_collection"
    assert summary["no_update_eligible"] is False


def test_manual_invalid_input_preserves_three_value_return_contract(tmp_path, monkeypatch):
    manual_path = tmp_path / "manual_sources.json"
    manual_path.write_text("invalid", encoding="utf-8")
    args = argparse.Namespace(date="2026-10-22", source_mode="manual", min_sources=1, max_sources=12)
    summary = {"warnings": [], "errors": [], "failed_source_ids": []}
    log_path = tmp_path / "run.log"
    log_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "ensure_source_folder", lambda _date: None)
    monkeypatch.setattr(daily, "source_file_for", lambda _date: manual_path)
    monkeypatch.setattr(daily, "_existing_collection_context", lambda _date: None)
    monkeypatch.setattr(daily, "validate_source_file", lambda _path, _minimum: (_ for _ in ()).throw(ValueError("invalid manual")))

    result = daily.collect_or_load_sources(args, summary, log_path)

    assert len(result) == 3
    assert result == (manual_path, [], None)
    assert summary["source_coverage_status"] == "incomplete_manual_input"
    assert summary["no_update_eligible"] is False


def test_main_auto_failure_writes_incomplete_terminal_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(
        daily,
        "collect_gaza_sources",
        lambda *args, **kwargs: {
            "ok": False,
            "source_file": None,
            "source_count": 0,
            "sources": [],
            "warnings": [],
            "errors": [],
            "failed_source_ids": [{"source_id": "google-1", "reason": "HTTPError: 503", "status_code": 503}],
            "no_match_source_ids": [{"source_id": "direct-1", "reason": "no matching Gaza items"}],
            "providers_configured": ["google-1", "direct-1"],
            "providers_attempted": ["google-1", "direct-1"],
            "providers_successful": [],
            "providers_fetch_succeeded": ["direct-1"],
            "providers_no_matches": ["direct-1"],
            "provider_diagnostics": [],
            "stage_counts": {"providers_failed": 1, "providers_fetch_succeeded": 1, "providers_no_matches": 1},
            "source_coverage_status": "incomplete_provider_failures",
            "collection_completion_status": "incomplete_provider_failures",
            "no_update_eligible": False,
            "global_source_cap_exclusions": [],
            "skipped_providers": [],
            "working_providers": [],
            "rejected_by_reason": {},
            "top_rejected_examples": [],
            "review_candidates": [],
        },
    )

    exit_code = daily.main(
        [
            "--date",
            "2026-10-22",
            "--source-mode",
            "auto",
            "--skip-tests",
            "--dry-run",
        ]
    )

    receipt_path = tmp_path / "data" / "dispatches" / "gaza" / "editions" / "2026-10-22" / "run_manifest.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert receipt["source_coverage_status"] == "incomplete_provider_failures"
    assert receipt["collection_completion_status"] == "incomplete_provider_failures"
    assert receipt["no_update_eligible"] is False
    assert any("no-update status is not justified" in error for error in receipt["errors"])


def test_main_both_mode_manual_records_are_not_blocked_by_auto_failure(tmp_path, monkeypatch, capsys):
    from bluefern_dispatches import gaza_audio

    manual_path = tmp_path / "manual_sources.json"
    manual_path.write_text("manual", encoding="utf-8")
    record = _manual_record()

    def fake_collect_gaza_sources(*args, **kwargs):
        return {
            "ok": False,
            "source_file": None,
            "source_count": 0,
            "sources": [],
            "warnings": [],
            "errors": [],
            "failed_source_ids": [{"source_id": "google-1", "reason": "HTTPError: 503", "status_code": 503}],
            "no_match_source_ids": [{"source_id": "direct-1", "reason": "no matching Gaza items"}],
            "providers_configured": ["google-1", "direct-1"],
            "providers_attempted": ["google-1", "direct-1"],
            "providers_successful": [],
            "providers_fetch_succeeded": ["direct-1"],
            "providers_no_matches": ["direct-1"],
            "provider_diagnostics": [],
            "stage_counts": {"providers_failed": 1, "providers_fetch_succeeded": 1, "providers_no_matches": 1},
            "source_coverage_status": "incomplete_provider_failures",
            "collection_completion_status": "incomplete_provider_failures",
            "no_update_eligible": False,
            "global_source_cap_exclusions": [],
            "skipped_providers": [],
            "working_providers": [],
            "rejected_by_reason": {},
            "top_rejected_examples": [],
            "review_candidates": [],
        }

    def fake_run(command, cwd=None):
        if any("run_gaza_dispatch.py" in str(item) for item in command):
            payload = {"ok": True, "publisher_count": 1, "publishers": ["Manual Publisher"], "source_adequacy_status": "qualifying"}
        else:
            payload = {"ok": True, "errors": [], "paid_detail_excluded_from_public": True, "target_pages_branch": "gh-pages"}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(daily, "ROOT", tmp_path)
    monkeypatch.setattr(daily, "source_file_for", lambda _date: manual_path)
    monkeypatch.setattr(daily, "validate_source_file", lambda _path, _minimum: ([record], []))
    monkeypatch.setattr(daily, "collect_gaza_sources", fake_collect_gaza_sources)
    monkeypatch.setattr(daily, "write_source_records", lambda *args, **kwargs: manual_path)
    monkeypatch.setattr(daily, "build_gaza_collection_timing_metadata", lambda records, edition_date: {})
    monkeypatch.setattr(
        daily,
        "validate_generated_output",
        lambda _date: {
            "archive_updated": True,
            "rss_updated": True,
            "source_count": 1,
            "public_story_count": 1,
            "warnings": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(daily, "validate_public_site_has_no_detail_files", lambda: [])
    monkeypatch.setattr(gaza_audio, "refresh_gaza_audio_public_surfaces", lambda _root: None)
    monkeypatch.setattr(daily, "run_command", fake_run)

    code = daily.main(
        [
            "--date",
            "2026-10-22",
            "--source-mode",
            "both",
            "--skip-tests",
            "--dry-run",
            "--pages-repo",
            str(tmp_path / "bluefern-dispatches-pages"),
        ]
    )

    summary = json.loads(capsys.readouterr().out)
    assert code == 0
    assert summary["errors"] == []
    assert summary["source_coverage_status"] == "incomplete_provider_failures"
    assert summary["collection_completion_status"] == "incomplete_provider_failures"
    assert summary["no_update_eligible"] is False
    assert summary["publish_ok"] is True

