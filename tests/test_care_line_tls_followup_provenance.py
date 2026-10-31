from __future__ import annotations

import json
import ssl
import sys
import urllib.error
from pathlib import Path

import pytest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from bluefern_dispatches import care_line_discovery as discovery  # noqa: E402
from bluefern_dispatches import care_line_effective_date_follow_up as follow_up  # noqa: E402
from bluefern_dispatches import care_line_national_pipeline as pipeline  # noqa: E402


def _follow_up_query() -> dict[str, str]:
    return {
        "query": '"Grady Memorial" maternity',
        "event_identity": "care_line_event_20260731_001",
        "event_instance_id": "care_line_event_20260731_001_20260731",
        "follow_up_window_start": "2026-07-17",
        "follow_up_window_end": "2026-08-07",
        "follow_up_status": "pending_follow_up",
        "lifecycle_status": "PENDING_EFFECTIVE_DATE",
    }


def test_care_default_fetcher_uses_verified_tls_same_limits_and_headers(monkeypatch) -> None:
    observed: dict[str, object] = {}
    sentinel_context = object()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self, size: int) -> bytes:
            observed["read_size"] = size
            return (b"x" * (size + 100))[:size]

    def fake_urlopen(request, *, timeout, context):
        observed["request"] = request
        observed["timeout"] = timeout
        observed["context"] = context
        return Response()

    monkeypatch.setattr(discovery, "food_line_test_mode_enabled", lambda: False)
    monkeypatch.setattr(discovery.ssl, "create_default_context", lambda: sentinel_context)
    monkeypatch.setattr(discovery.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        discovery.ssl,
        "_create_unverified_context",
        lambda: pytest.fail("Care must not create an unverified TLS context"),
    )

    payload = discovery._care_fetch("https://example.test/feed")

    request = observed["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert len(payload) == discovery.CARE_FETCH_MAX_BYTES == 2_000_000
    assert observed["timeout"] == discovery.CARE_FETCH_TIMEOUT_SECONDS == 15
    assert observed["read_size"] == 2_000_000
    assert observed["context"] is sentinel_context
    assert headers["user-agent"] == discovery.CARE_FETCH_HEADERS["User-Agent"]
    assert headers["accept"] == discovery.CARE_FETCH_HEADERS["Accept"]
    assert headers["accept-language"] == discovery.CARE_FETCH_HEADERS["Accept-Language"]


def test_care_default_fetcher_does_not_downgrade_certificate_failure(monkeypatch) -> None:
    def refusing_urlopen(*args, **kwargs):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr(discovery, "food_line_test_mode_enabled", lambda: False)
    monkeypatch.setattr(discovery.urllib.request, "urlopen", refusing_urlopen)
    monkeypatch.setattr(
        discovery.ssl,
        "_create_unverified_context",
        lambda: pytest.fail("certificate failure must remain an explicit error"),
    )

    with pytest.raises(urllib.error.URLError):
        discovery._care_fetch("https://example.test/certificate-failure")


def test_discovery_default_injection_uses_care_fetcher(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_care_fetch(url: str, timeout: int = 15) -> bytes:
        calls.append((url, timeout))
        return b"<rss><channel /></rss>"

    monkeypatch.setattr(
        discovery,
        "load_care_line_discovery_queries",
        lambda root: {
            "queries": [{"query_template": "care test", "source_family": "local_news", "category": "test"}],
            "date_bounded_queries": [],
            "exclude_domains": [],
        },
    )
    monkeypatch.setattr(discovery, "_care_fetch", fake_care_fetch)

    result = discovery.discover_care_line_sources(
        tmp_path,
        "2026-08-01",
        max_queries=1,
        write=False,
        dry_run=True,
    )

    assert len(calls) == 1
    assert calls[0][1] == 15
    assert result["query_failure_count"] == 0
    assert result["query_rows"][0]["error"] == ""


def test_rss_success_with_article_tls_failure_is_retained_but_unverified(tmp_path: Path, monkeypatch) -> None:
    query = _follow_up_query()
    good_url = "https://good.example/story"
    failed_url = "https://failed.example/story"

    monkeypatch.setattr(
        discovery,
        "load_care_line_discovery_queries",
        lambda root: {"queries": [], "date_bounded_queries": [], "exclude_domains": []},
    )
    monkeypatch.setattr(
        discovery,
        "_parse_rss_items",
        lambda payload: [
            {
                "url": good_url,
                "title": "Food pantry demand rises",
                "summary_or_snippet": "Families report food access pressure and shortages.",
                "publisher": "Good News",
                "published_at": "2026-08-01",
            },
            {
                "url": failed_url,
                "title": "Food pantry demand rises",
                "summary_or_snippet": "Families report food access pressure and shortages.",
                "publisher": "Failed News",
                "published_at": "2026-08-01",
            },
        ],
    )
    monkeypatch.setattr(
        discovery,
        "_extract_page_evidence",
        lambda payload: {
            "title": "Verified food pressure",
            "summary_or_snippet": "Verified article summary.",
            "evidence_text": "Verified article evidence.",
            "evidence_text_basis": "mocked_article",
        },
    )
    monkeypatch.setattr(discovery, "_extract_page_metadata_date", lambda payload: "2026-08-01")

    def fake_fetch(url: str, timeout: int = 15) -> bytes:
        if url.startswith("https://news.google.com/rss/search"):
            return b"rss"
        if url == failed_url:
            raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        assert url == good_url
        return b"article"

    result = discovery.discover_care_line_sources(
        tmp_path,
        "2026-08-01",
        fetcher=fake_fetch,
        follow_up_queries=[query],
        max_queries=1,
        write=True,
        dry_run=False,
    )

    query_row = result["query_rows"][0]
    assert query_row["results"] == 2
    assert query_row["verified_results"] == 1
    assert query_row["article_fetch_failure_count"] == 1
    assert query_row["error_kind"] == "article_fetch_failure"
    assert "certificate verify failed" in query_row["error"]
    assert result["article_fetch_failure_count"] == 1
    assert result["article_verified_result_count"] == 1
    assert result["query_failure_count"] == 1

    source_path = Path(result["discovered_sources_path"])
    rows_by_url = {row["url"]: row for row in json.loads(source_path.read_text(encoding="utf-8"))}
    assert set(rows_by_url) == {good_url, failed_url}
    assert rows_by_url[good_url]["article_verified"] is True
    assert rows_by_url[good_url]["article_fetch_error"] == ""
    assert rows_by_url[failed_url]["article_verified"] is False
    assert "certificate verify failed" in rows_by_url[failed_url]["article_fetch_error"]
    assert rows_by_url[failed_url]["included"] is False
    assert rows_by_url[failed_url]["exclusion_reason"] == "article_fetch_failed"

    follow_up_result = follow_up.update_follow_up_state(
        tmp_path,
        run_date="2026-08-01",
        follow_up_queries=[query],
        discovery_query_rows=result["query_rows"],
        state_root=tmp_path / "review",
    )
    assert follow_up_result["items"][0]["status"] == follow_up.FOLLOW_UP_QUERY_FAILURE_STATUS
    assert follow_up_result["items"][0]["query_error"] == query_row["error"]
    assert follow_up_result["items"][0]["status"] not in follow_up.FOLLOW_UP_TERMINAL_STATUSES


def test_rss_success_with_empty_article_payload_is_explicit_failure(tmp_path: Path, monkeypatch) -> None:
    query = _follow_up_query()
    article_url = "https://empty.example/story"

    monkeypatch.setattr(
        discovery,
        "load_care_line_discovery_queries",
        lambda root: {"queries": [], "date_bounded_queries": [], "exclude_domains": []},
    )
    monkeypatch.setattr(
        discovery,
        "_parse_rss_items",
        lambda payload: [
            {
                "url": article_url,
                "title": "Food access pressure",
                "summary_or_snippet": "Families report food access pressure.",
                "publisher": "Empty News",
                "published_at": "2026-08-01",
            }
        ],
    )

    def fake_fetch(url: str, timeout: int = 15) -> bytes:
        if url.startswith("https://news.google.com/rss/search"):
            return b"rss"
        assert url == article_url
        return b""

    result = discovery.discover_care_line_sources(
        tmp_path,
        "2026-08-01",
        fetcher=fake_fetch,
        follow_up_queries=[query],
        max_queries=1,
        write=True,
        dry_run=False,
    )

    query_row = result["query_rows"][0]
    assert query_row["error_kind"] == "article_fetch_failure"
    assert query_row["article_fetch_failure_count"] == 1
    assert query_row["article_fetch_errors"] == ["empty article response"]
    source_path = Path(result["discovered_sources_path"])
    rows = json.loads(source_path.read_text(encoding="utf-8"))
    assert rows[0]["article_fetch_error"] == "empty article response"
    assert rows[0]["article_verified"] is False
    assert rows[0]["exclusion_reason"] == "article_fetch_failed"
    assert result["query_failure_count"] == 1


def test_follow_up_query_error_is_nonterminal_and_not_no_change(tmp_path: Path) -> None:
    query = _follow_up_query()
    result = follow_up.update_follow_up_state(
        tmp_path,
        run_date="2026-08-01",
        follow_up_queries=[query],
        discovery_query_rows=[
            {"query": query["query"], "url": "https://example.test/feed", "error": "URLError: TLS policy", "results": 0}
        ],
        state_root=tmp_path / "review",
    )

    item = result["items"][0]
    assert item["status"] == follow_up.FOLLOW_UP_QUERY_FAILURE_STATUS
    assert item["query_error"] == "URLError: TLS policy"
    assert result["failure_count"] == 1
    assert result["not_run_count"] == 0
    assert item["status"] not in follow_up.FOLLOW_UP_TERMINAL_STATUSES


def test_missing_follow_up_row_is_explicitly_unresolved(tmp_path: Path) -> None:
    result = follow_up.update_follow_up_state(
        tmp_path,
        run_date="2026-08-01",
        follow_up_queries=[_follow_up_query()],
        discovery_query_rows=[],
        state_root=tmp_path / "review",
    )

    item = result["items"][0]
    assert item["status"] == follow_up.FOLLOW_UP_QUERY_NOT_RUN_STATUS
    assert item["query_error"] == "follow-up query has no discovery result"
    assert result["failure_count"] == 0
    assert result["not_run_count"] == 1
    assert item["status"] not in follow_up.FOLLOW_UP_TERMINAL_STATUSES


def test_pipeline_manifest_counts_follow_up_failure_and_blocks_false_success(tmp_path: Path, monkeypatch) -> None:
    query = _follow_up_query()
    monkeypatch.setattr(pipeline, "load_canonical_registry", lambda root, include_disabled=True: object())
    monkeypatch.setattr(pipeline, "collectable_sources", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "adapt_pressure_registry", lambda root: {})
    monkeypatch.setattr(pipeline, "load_reviewed_records", lambda root: [object()])
    monkeypatch.setattr(pipeline, "load_follow_up_state", lambda root, *, state_root=None: {"items": []})
    monkeypatch.setattr(pipeline, "build_follow_up_queries", lambda *args, **kwargs: [query])
    monkeypatch.setattr(
        pipeline,
        "discover_care_line_sources",
        lambda *args, **kwargs: {
            "query_rows": [
                {
                    "query": query["query"],
                    "url": "https://example.test/feed",
                    "error": "SSLCertVerificationError: TLS policy",
                    "results": 0,
                }
            ]
        },
    )

    result = pipeline.run_national_pipeline(
        tmp_path,
        run_date="2026-08-01",
        collection_runs_root=Path("runs"),
        review_root=Path("review"),
    )

    manifest = result["run_manifest"]
    assert manifest["status"] == pipeline.RUN_STATUS_FAILURE
    assert manifest["follow_up_query_failure_count"] == 1
    assert manifest["follow_up_query_not_run_count"] == 0
    assert manifest["zero_findings_distinct_from_source_failures"] is True
    assert result["follow_up_state"]["items"][0]["status"] == follow_up.FOLLOW_UP_QUERY_FAILURE_STATUS


def test_successful_primary_source_plus_failed_follow_up_is_partial_and_retryable(tmp_path: Path, monkeypatch) -> None:
    query = _follow_up_query()
    monkeypatch.setattr(pipeline, "load_canonical_registry", lambda root, include_disabled=True: object())
    monkeypatch.setattr(pipeline, "collectable_sources", lambda *args, **kwargs: [{"source": object()}])
    monkeypatch.setattr(pipeline, "load_reviewed_records", lambda root: [object()])
    monkeypatch.setattr(pipeline, "begin_collection_run", lambda *args, **kwargs: {"run_id": "run-1", "source_ids": []})
    monkeypatch.setattr(
        pipeline,
        "run_collection_attempt",
        lambda *args, **kwargs: {
            "attempt": {"collection_status": "ok"},
            "raw_items": [],
            "event_leads": [],
            "candidates": [],
            "exclusions": [],
            "failed_extractions": [],
            "manual_review": [],
            "prefilter_diagnostics": [],
            "prefilter_discarded": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "update_candidate_registry_at_path",
        lambda *args, **kwargs: {
            "candidates": [],
            "created_this_run": 0,
            "updated_this_run": 0,
            "persistent_candidates_from_prior_runs": 0,
            "stale_candidate_count": 0,
            "superseded_candidate_count": 0,
        },
    )
    monkeypatch.setattr(
        pipeline,
        "build_review_queue",
        lambda *args, **kwargs: {
            "queue_item_count": 0,
            "backlog_item_count": 0,
            "duplicate_item_count": 0,
            "clusters": {"cluster_count": 0},
            "backlog": [],
            "duplicates": [],
        },
    )
    monkeypatch.setattr(pipeline, "_write_review_private_outputs", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "adapt_pressure_registry", lambda root: {})
    monkeypatch.setattr(pipeline, "load_follow_up_state", lambda root, *, state_root=None: {"items": []})
    monkeypatch.setattr(pipeline, "build_follow_up_queries", lambda *args, **kwargs: [query])
    monkeypatch.setattr(
        pipeline,
        "discover_care_line_sources",
        lambda *args, **kwargs: {
            "query_rows": [
                {
                    "query": query["query"],
                    "url": "https://example.test/feed",
                    "error": "article fetch failures: 1",
                    "error_kind": "article_fetch_failure",
                    "results": 1,
                    "verified_results": 0,
                    "article_fetch_failure_count": 1,
                }
            ]
        },
    )

    result = pipeline.run_national_pipeline(
        tmp_path,
        run_date="2026-08-01",
        collection_runs_root=Path("runs"),
        review_root=Path("review"),
    )

    manifest = result["run_manifest"]
    assert manifest["status"] == pipeline.RUN_STATUS_PARTIAL_SUCCESS
    assert manifest["successful_attempt_count"] == 1
    assert manifest["follow_up_query_failure_count"] == 1
    assert result["follow_up_state"]["items"][0]["status"] == follow_up.FOLLOW_UP_QUERY_FAILURE_STATUS
    assert result["follow_up_state"]["items"][0]["status"] not in follow_up.FOLLOW_UP_TERMINAL_STATUSES
