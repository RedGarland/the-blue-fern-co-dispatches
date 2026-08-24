from __future__ import annotations

import builtins
import json
import sys
import types
from pathlib import Path

from bluefern_dispatches.care_line_source_registry import CareLineSource

import bluefern_dispatches.care_line_national_pipeline as pipeline


class _FakeResponse:
    def __init__(self, body: bytes = b"<rss><channel /></rss>", *, status: int = 200, url: str = "https://example.org/feed") -> None:
        self._body = body
        self.status = status
        self._url = url
        self.headers = {"Content-Type": "application/rss+xml; charset=utf-8"}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url


def test_national_pipeline_threads_follow_up_queries_into_discovery_and_state_update(tmp_path: Path, monkeypatch) -> None:
    follow_up_query = {
        "query": '"Grady Memorial" maternity',
        "event_identity": "care_line_event_20260731_001",
        "event_instance_id": "care_line_event_20260731_001_20260731",
        "follow_up_window_start": "2026-07-17",
        "follow_up_window_end": "2026-08-07",
        "follow_up_status": "pending_follow_up",
        "lifecycle_status": "PENDING_EFFECTIVE_DATE",
    }
    discovery_query_rows = [
        {
            "query": '"Grady Memorial" maternity',
            "url": "https://example.org/follow-up",
            "error": "",
            "results": 1,
        },
        {
            "query": '"routine care access" hospital',
            "url": "https://example.org/routine",
            "error": "",
            "results": 0,
        },
    ]
    discovered_rows = [
        {
            "source_record_id": "care-line-future-1",
            "source_id": "care-line-future-1",
            "primary_eligible": True,
            "confidence": "high",
        }
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline, "load_canonical_registry", lambda root, include_disabled=True: object())
    monkeypatch.setattr(pipeline, "collectable_sources", lambda registry, include_partial=True, include_manual_review=False: [])
    monkeypatch.setattr(pipeline, "adapt_pressure_registry", lambda root: {})
    monkeypatch.setattr(pipeline, "load_reviewed_records", lambda root: [object()])
    monkeypatch.setattr(pipeline, "load_follow_up_state", lambda root, *, state_root=None: {"schema_version": "test", "items": []})
    monkeypatch.setattr(pipeline, "build_follow_up_queries", lambda root, run_date, reviewed_records, state: [follow_up_query])
    monkeypatch.setattr(
        pipeline,
        "discover_care_line_sources",
        lambda root, run_date, **kwargs: {
            "discovered_sources_path": str(root / "data" / "dispatches" / "care-line" / "sources" / run_date / "discovered_sources.json"),
            "query_rows": discovery_query_rows,
            "source_count": len(discovered_rows),
            "public_signal_count": len(discovered_rows),
        },
    )
    monkeypatch.setattr(
        pipeline,
        "update_follow_up_state",
        lambda root, *, run_date, follow_up_queries, discovery_query_rows, state_root=None: captured.update(
            {
                "follow_up_queries": list(follow_up_queries),
                "discovery_query_rows": list(discovery_query_rows),
                "state_root": state_root,
            }
        )
        or {
            "schema_version": "test",
            "updated_at": "2026-08-20T00:00:00Z",
            "run_date": run_date,
            "items": [{"status": "MATERIAL_UPDATE_FOUND"}],
        },
    )

    monkeypatch.setattr(
        pipeline,
        "begin_collection_run",
        lambda root, *, run_date, source_rows, settings, run_id=None, collection_runs_root=None: (
            (root / collection_runs_root / run_date / (run_id or "scheduled-001")).mkdir(parents=True, exist_ok=True)
            or {
                "schema_version": "test",
                "run_id": run_id or "scheduled-001",
                "run_key": "test-run-key",
                "run_date": run_date,
                "started_at": "2026-08-20T00:00:00Z",
                "source_count": len(source_rows),
                "source_ids": [],
                "status": "running",
                "settings": dict(settings),
                "attempts": [],
            }
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_atomic_write",
        lambda path, payload: (path.parent.mkdir(parents=True, exist_ok=True), path.touch()),
    )

    result = pipeline.run_national_pipeline(
        tmp_path,
        run_date="2026-08-20",
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
        review_root=Path("data/dispatches/care-line/review"),
    )

    assert captured["follow_up_queries"] == [follow_up_query]
    assert captured["discovery_query_rows"] == discovery_query_rows
    assert captured["state_root"] == Path("data/dispatches/care-line/review")
    assert result["run_manifest"]["follow_up_query_count"] == 1
    assert result["run_manifest"]["follow_up_material_update_count"] == 1
    assert result["follow_up_state"]["items"][0]["status"] == "MATERIAL_UPDATE_FOUND"


def test_national_pipeline_uses_an_explicit_run_id_when_provided(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline, "load_canonical_registry", lambda root, include_disabled=True: object())
    monkeypatch.setattr(pipeline, "collectable_sources", lambda registry, include_partial=True, include_manual_review=False: [])
    monkeypatch.setattr(pipeline, "adapt_pressure_registry", lambda root: {})
    monkeypatch.setattr(pipeline, "load_reviewed_records", lambda root: [])
    monkeypatch.setattr(pipeline, "load_follow_up_state", lambda root, *, state_root=None: {"schema_version": "test", "items": []})
    monkeypatch.setattr(pipeline, "build_follow_up_queries", lambda root, run_date, reviewed_records, state: [])
    monkeypatch.setattr(
        pipeline,
        "discover_care_line_sources",
        lambda root, run_date, **kwargs: {"discovered_sources_path": "", "query_rows": [], "source_count": 0, "public_signal_count": 0},
    )
    monkeypatch.setattr(
        pipeline,
        "update_follow_up_state",
        lambda root, *, run_date, follow_up_queries, discovery_query_rows, state_root=None: {
            "schema_version": "test",
            "updated_at": "2026-08-20T00:00:00Z",
            "run_date": run_date,
            "items": [],
            "state_path": str(root / "data" / "dispatches" / "care-line" / "review" / "effective-date-follow-up-state.json"),
        },
    )

    def fake_begin_collection_run(root, *, run_date, source_rows, settings, run_id=None, collection_runs_root=None):  # noqa: ANN001
        captured["run_id"] = run_id
        (root / collection_runs_root / run_date / (run_id or "scheduled-123")).mkdir(parents=True, exist_ok=True)
        return {
            "schema_version": "test",
            "run_id": run_id,
            "run_key": "test-run-key",
            "run_date": run_date,
            "started_at": "2026-08-20T00:00:00Z",
            "source_count": 0,
            "source_ids": [],
            "status": "running",
            "settings": dict(settings),
            "attempts": [],
        }

    monkeypatch.setattr(pipeline, "begin_collection_run", fake_begin_collection_run)
    monkeypatch.setattr(
        pipeline,
        "_atomic_write",
        lambda path, payload: (path.parent.mkdir(parents=True, exist_ok=True), path.touch()),
    )

    result = pipeline.run_national_pipeline(
        tmp_path,
        run_date="2026-08-20",
        run_id="scheduled-123",
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
        review_root=Path("data/dispatches/care-line/review"),
    )

    assert captured["run_id"] == "scheduled-123"
    assert result["run_manifest"]["run_id"] == "scheduled-123"
    assert result["run_manifest"]["run_manifest_path"].endswith("scheduled-123/run-manifest.json")


def test_care_line_access_prefilter_discards_obvious_noise_before_formal_exclusion(tmp_path: Path, monkeypatch) -> None:
    source = CareLineSource.model_validate(
        {
            "source_id": "noise-source",
            "name": "Noise Source",
            "publisher": "Example News",
            "source_type": "trade_publication",
            "feed_url": "https://example.org/feed",
            "homepage_url": "https://example.org/",
            "state": "OH",
            "geographic_scope": "local",
            "organization_type": "trade_publication",
            "care_line_topics": ["hospital", "clinic"],
            "authority_level": "secondary",
            "expected_update_frequency": "daily",
            "enabled": True,
            "adapter_type": "rss",
            "requires_html_followup": False,
            "source_role": "healthcare_access_reporting",
            "historical_depth": "current feed",
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:00Z",
        }
    )

    def fake_fetch_source(_source, timeout=20, allow_insecure_tls=False):  # noqa: ANN001
        return b"<rss><channel></channel></rss>", {"final_url": "https://example.org/feed"}

    def fake_parse_source_items(_source, _payload, *, source_url, fetch_timeout, allow_insecure_tls, max_items_per_source):  # noqa: ANN001
        return [
            {
                "title": "Hospital opens new wing and urgent care service",
                "url": "https://example.org/news/new-wing",
                "published_at": "2026-08-23T08:00:00Z",
                "description": "The hospital hosted a grand opening and marketing event.",
                "source": "Example News",
                "id": "noise-1",
            }
        ]

    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch_source)
    monkeypatch.setattr(pipeline, "parse_source_items", fake_parse_source_items)

    def fake_atomic_write(path, payload):  # noqa: ANN001
        return None

    monkeypatch.setattr(pipeline, "_atomic_write", fake_atomic_write)

    (tmp_path / "data" / "dispatches" / "care-line" / "collection-runs" / "2026-08-23" / "run-1").mkdir(
        parents=True,
        exist_ok=True,
    )

    result = pipeline.run_collection_attempt(
        tmp_path,
        run_date="2026-08-23",
        run_id="run-1",
        source_row={"source": source},
        historical_reviewed_records=[],
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
    )

    assert len(result["prefilter_diagnostics"]) == 1
    assert result["prefilter_diagnostics"][0]["prefilter_decision"] == "discard"
    assert result["prefilter_discarded"][0]["normalized_reason"] in {"marketing_announcement", "construction_without_access_consequence", "general_healthcare_news", "non_care_line", "service_expansion_without_prior_loss_context"}
    assert result["exclusions"] == []
    assert result["candidates"] == []


def test_care_line_access_prefilter_discards_pure_service_expansion_without_prior_loss_context() -> None:
    raw_item = {
        "raw_item_id": "care-line-expansion-no-history-001",
        "source_id": "expansion-source",
        "source_name": "Example News",
        "item_url": "https://example.org/new-service",
        "title": "Mount Carmel Franklinton opens new urgent care service",
        "description": "The hospital is adding a new urgent care service after a prior closure was not documented in the reviewed state.",
        "source_publication_date": "2026-08-23T08:00:00Z",
        "facility_name": "Mount Carmel Franklinton Emergency Department",
        "provider_name": "Mount Carmel Franklinton Emergency Department",
        "city": "Columbus",
        "state": "OH",
    }
    lead = pipeline.event_lead_from_raw_item(raw_item)

    prefilter = pipeline._care_line_access_prefilter(raw_item, lead, reviewed_records=[])

    assert prefilter["prefilter_decision"] == "discard"
    assert prefilter["normalized_reason"] == "service_expansion_without_prior_loss_context"
    assert prefilter["history_match_count"] == 0
    assert prefilter["escalated_to_full_review"] is False


def test_care_line_access_prefilter_escalates_after_prior_loss_context():
    reviewed_records = [
        {
            "producer_record_id": "care-line-closure-001",
            "source_url": "https://example.org/mount-carmel-closure",
            "source_title": "Mount Carmel Franklinton Emergency Department closure announced",
            "source_publisher": "Example News",
            "source_publication_date": "2026-06-24T00:00:00Z",
            "event_type": "facility_closure",
            "facility_name": "Mount Carmel Franklinton Emergency Department",
            "provider_name": "Mount Carmel Franklinton Emergency Department",
            "city": "Columbus",
            "state": "OH",
            "service_line": "emergency_care",
            "supporting_passage": "The emergency department will close on August 22.",
            "raw_payload_hash": "hash-001",
            "review_status": "approved",
            "public_status": "public_approved",
            "care_line_public_eligible": True,
        }
    ]
    raw_item = {
        "raw_item_id": "care-line-expansion-001",
        "source_id": "noise-source",
        "source_name": "Example News",
        "item_url": "https://example.org/new-service",
        "title": "Mount Carmel Franklinton opens new urgent care service",
        "description": "The hospital is adding a new urgent care service after the prior emergency department closure.",
        "source_publication_date": "2026-08-23T08:00:00Z",
        "facility_name": "Mount Carmel Franklinton Emergency Department",
        "provider_name": "Mount Carmel Franklinton Emergency Department",
        "city": "Columbus",
        "state": "OH",
    }
    lead = pipeline.event_lead_from_raw_item(raw_item)

    prefilter = pipeline._care_line_access_prefilter(raw_item, lead, reviewed_records=reviewed_records)

    assert prefilter["prefilter_decision"] == "escalate_to_full_review"
    assert prefilter["normalized_reason"] == "prior_loss_context_detected"
    assert prefilter["history_match_count"] == 1
    assert prefilter["escalated_to_full_review"] is True


def test_care_line_feed_parsers_preserve_inline_content_fallbacks() -> None:
    rss_payload = b"""<rss><channel><item><title>Mount Carmel updates</title><link>https://example.org/rss</link><description></description><content:encoded xmlns:content=\"http://purl.org/rss/1.0/modules/content/\">Emergency department closure announced for August 22.</content:encoded><pubDate>Mon, 23 Aug 2026 08:00:00 GMT</pubDate><guid>rss-1</guid></item></channel></rss>"""
    atom_payload = b"""<feed xmlns=\"http://www.w3.org/2005/Atom\"><entry><title>Santa Paula update</title><id>atom-1</id><link rel=\"alternate\" href=\"https://example.org/atom\"/><summary></summary><content type=\"text\">Planned hospital closure remains under review.</content><published>2026-08-23T08:00:00Z</published></entry></feed>"""
    json_feed_payload = json.dumps(
        {
            "title": "Care Line feed",
            "items": [
                {
                    "id": "json-1",
                    "title": "Care Line update",
                    "url": "https://example.org/json",
                    "summary": "",
                    "content_text": "Replacement service still lacks emergency care.",
                    "content_html": "<p>Replacement service still lacks emergency care.</p>",
                    "date_published": "2026-08-23T08:00:00Z",
                }
            ],
        }
    ).encode("utf-8")

    rss_items = pipeline._rss_items(rss_payload)
    atom_items = pipeline._atom_items(atom_payload)
    json_items = pipeline._json_feed_items(json_feed_payload)

    assert rss_items[0]["description"] == "Emergency department closure announced for August 22."
    assert rss_items[0]["content_text"] == "Emergency department closure announced for August 22."
    assert atom_items[0]["description"] == ""
    assert atom_items[0]["content_text"] == "Planned hospital closure remains under review."
    assert json_items[0]["content_text"] == "Replacement service still lacks emergency care."
    assert json_items[0]["content_html"] == "<p>Replacement service still lacks emergency care.</p>"


def test_care_line_article_content_recovers_page_metadata_date_without_json_ld_date() -> None:
    source = CareLineSource.model_validate(
        {
            "source_id": "metadata-source",
            "name": "Metadata Source",
            "publisher": "Example News",
            "source_type": "trade_publication",
            "feed_url": "https://example.org/feed",
            "homepage_url": "https://example.org/",
            "state": "OH",
            "geographic_scope": "local",
            "organization_type": "trade_publication",
            "care_line_topics": ["hospital", "clinic"],
            "authority_level": "secondary",
            "expected_update_frequency": "daily",
            "enabled": True,
            "adapter_type": "rss",
            "requires_html_followup": False,
            "source_role": "healthcare_access_reporting",
            "historical_depth": "current feed",
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:00Z",
        }
    )
    html_payload = b"""
    <html>
      <head>
        <meta property=\"article:published_time\" content=\"2026-08-23T07:30:00Z\"/>
        <script type=\"application/ld+json\">
          {"@context":"https://schema.org","@type":"NewsArticle","headline":"Example Hospital emergency department closure","articleBody":"Example Hospital emergency department closure effective August 22 was announced after a June 24 notice, with access consequences for Columbus residents."}
        </script>
      </head>
      <body>
        <article>
          <p>Example Hospital emergency department closure effective August 22 was announced after a June 24 notice, with access consequences for Columbus residents.</p>
        </article>
      </body>
    </html>
    """

    extracted = pipeline._extract_article_content(
        source,
        html_payload,
        source_url="https://example.org/story",
        response_meta={"content_type": "text/html; charset=utf-8"},
    )

    assert extracted["published_at"] == "2026-08-23"
    assert extracted["published_at_state"] == "source_dated"
    assert extracted["published_at_basis"] == "page_metadata"
    assert extracted["title"] == "Example Hospital emergency department closure"
    assert "Example Hospital emergency department closure effective August 22 was announced after a June 24 notice" in extracted["text"]


def test_care_line_article_content_leaves_date_blank_when_no_date_metadata_present() -> None:
    source = CareLineSource.model_validate(
        {
            "source_id": "metadata-source-blank",
            "name": "Metadata Source Blank",
            "publisher": "Example News",
            "source_type": "trade_publication",
            "feed_url": "https://example.org/feed",
            "homepage_url": "https://example.org/",
            "state": "OH",
            "geographic_scope": "local",
            "organization_type": "trade_publication",
            "care_line_topics": ["hospital", "clinic"],
            "authority_level": "secondary",
            "expected_update_frequency": "daily",
            "enabled": True,
            "adapter_type": "rss",
            "requires_html_followup": False,
            "source_role": "healthcare_access_reporting",
            "historical_depth": "current feed",
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:00Z",
        }
    )
    html_payload = b"""
    <html>
      <head>
        <script type=\"application/ld+json\">
          {"@context":"https://schema.org","@type":"NewsArticle","headline":"Fallback story","articleBody":"Example Hospital emergency department closure effective August 22 was announced after a June 24 notice, with access consequences for Columbus residents."}
        </script>
      </head>
      <body>
        <article>
          <p>Example Hospital emergency department closure effective August 22 was announced after a June 24 notice, with access consequences for Columbus residents.</p>
        </article>
      </body>
    </html>
    """

    extracted = pipeline._extract_article_content(
        source,
        html_payload,
        source_url="https://example.org/story",
        response_meta={"content_type": "text/html; charset=utf-8"},
    )

    assert extracted["published_at"] == ""
    assert extracted["published_at_state"] == "missing"
    assert extracted["published_at_basis"] == ""


def test_care_line_access_blocked_item_can_still_qualify_when_feed_evidence_is_sufficient(tmp_path: Path, monkeypatch) -> None:
    source = CareLineSource.model_validate(
        {
            "source_id": "blocked-source",
            "name": "Blocked Source",
            "publisher": "Example News",
            "source_type": "trade_publication",
            "feed_url": "https://example.org/feed",
            "homepage_url": "https://example.org/",
            "state": "OH",
            "geographic_scope": "local",
            "organization_type": "trade_publication",
            "care_line_topics": ["hospital", "clinic"],
            "authority_level": "secondary",
            "expected_update_frequency": "daily",
            "enabled": True,
            "adapter_type": "rss",
            "requires_html_followup": False,
            "source_role": "healthcare_access_reporting",
            "historical_depth": "current feed",
            "item_permalink_available": True,
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:00Z",
        }
    )

    def fake_fetch_source(_source, timeout=20, allow_insecure_tls=False):  # noqa: ANN001
        return b"<rss><channel></channel></rss>", {"final_url": "https://example.org/feed"}

    def fake_parse_source_items(_source, _payload, *, source_url, fetch_timeout, allow_insecure_tls, max_items_per_source):  # noqa: ANN001
        return [
            {
                "title": "Example Hospital closure update",
                "url": "https://example.org/story",
                "published_at": "2026-08-23T08:00:00Z",
                "description": "The closure was announced and the emergency department will close on August 22, affecting access in Columbus.",
                "content_text": "The closure was announced and the emergency department will close on August 22, affecting access in Columbus.",
                "facility_name": "Example Hospital",
                "provider_name": "Example Hospital",
                "city": "Columbus",
                "state": "OH",
                "source": "Example News",
                "id": "blocked-1",
            }
        ]

    def fake_fetch_url(*args, **kwargs):  # noqa: ANN001
        raise TimeoutError("simulated access block")

    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch_source)
    monkeypatch.setattr(pipeline, "parse_source_items", fake_parse_source_items)
    monkeypatch.setattr(pipeline, "fetch_url", fake_fetch_url)
    monkeypatch.setattr(pipeline, "_atomic_write", lambda path, payload: None)

    (tmp_path / "data" / "dispatches" / "care-line" / "collection-runs" / "2026-08-23" / "run-1").mkdir(
        parents=True,
        exist_ok=True,
    )

    result = pipeline.run_collection_attempt(
        tmp_path,
        run_date="2026-08-23",
        run_id="run-1",
        source_row={"source": source},
        historical_reviewed_records=[],
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
    )

    assert result["failed_extractions"] == []
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["normalized_record"]["source_publication_date"] == "2026-08-23"
    assert result["candidates"][0]["normalized_record"]["supporting_passage"]
    assert result["attempt"]["failed_extraction_count"] == 0


def test_care_line_access_blocked_item_without_evidence_still_fails_extraction(tmp_path: Path, monkeypatch) -> None:
    source = CareLineSource.model_validate(
        {
            "source_id": "blocked-source-no-evidence",
            "name": "Blocked Source No Evidence",
            "publisher": "Example News",
            "source_type": "trade_publication",
            "feed_url": "https://example.org/feed",
            "homepage_url": "https://example.org/",
            "state": "OH",
            "geographic_scope": "local",
            "organization_type": "trade_publication",
            "care_line_topics": ["hospital", "clinic"],
            "authority_level": "secondary",
            "expected_update_frequency": "daily",
            "enabled": True,
            "adapter_type": "rss",
            "requires_html_followup": False,
            "source_role": "healthcare_access_reporting",
            "historical_depth": "current feed",
            "item_permalink_available": True,
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:00Z",
        }
    )

    def fake_fetch_source(_source, timeout=20, allow_insecure_tls=False):  # noqa: ANN001
        return b"<rss><channel></channel></rss>", {"final_url": "https://example.org/feed"}

    def fake_parse_source_items(_source, _payload, *, source_url, fetch_timeout, allow_insecure_tls, max_items_per_source):  # noqa: ANN001
        return [
            {
                "title": "Example Hospital update",
                "url": "https://example.org/story",
                "published_at": "2026-08-23T08:00:00Z",
                "description": "",
                "content_text": "",
                "facility_name": "Example Hospital",
                "provider_name": "Example Hospital",
                "city": "Columbus",
                "state": "OH",
                "source": "Example News",
                "id": "blocked-2",
            }
        ]

    def fake_fetch_url(*args, **kwargs):  # noqa: ANN001
        raise TimeoutError("simulated access block")

    monkeypatch.setattr(pipeline, "fetch_source", fake_fetch_source)
    monkeypatch.setattr(pipeline, "parse_source_items", fake_parse_source_items)
    monkeypatch.setattr(pipeline, "fetch_url", fake_fetch_url)
    monkeypatch.setattr(pipeline, "_atomic_write", lambda path, payload: None)

    (tmp_path / "data" / "dispatches" / "care-line" / "collection-runs" / "2026-08-23" / "run-1").mkdir(
        parents=True,
        exist_ok=True,
    )

    result = pipeline.run_collection_attempt(
        tmp_path,
        run_date="2026-08-23",
        run_id="run-1",
        source_row={"source": source},
        historical_reviewed_records=[],
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
    )

    assert len(result["failed_extractions"]) == 1
    assert result["failed_extractions"][0]["exclusion_reason"] in {"needs_full_article", "insufficient_bounded_evidence"}
    assert result["candidates"] == []


def test_care_line_fetch_url_uses_certifi_trust_when_available(monkeypatch) -> None:
    fake_cafile = r"C:\fake\cacert.pem"
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == "truststore":
            raise ModuleNotFoundError("No module named 'truststore'")
        return original_import(name, globals, locals, fromlist, level)

    certifi_module = types.ModuleType("certifi")
    certifi_module.where = lambda: fake_cafile  # type: ignore[assignment]
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setitem(sys.modules, "certifi", certifi_module)

    captured: dict[str, object] = {}

    def fake_create_default_context(*, cafile=None):  # noqa: ANN001
        captured["cafile"] = cafile
        return object()

    def fake_urlopen(request, timeout, context):  # noqa: ANN001
        captured["request_url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return _FakeResponse()

    monkeypatch.setattr(pipeline.ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)

    payload, meta = pipeline.fetch_url("https://example.org/feed")

    assert payload == b"<rss><channel /></rss>"
    assert captured["cafile"] == fake_cafile
    assert captured["request_url"] == "https://example.org/feed"
    assert meta["ssl_mode"] == "certifi"
    assert meta["insecure_ssl_used"] is False
    assert "ssl_warning" not in meta


def test_care_line_fetch_url_prefers_truststore_when_available(monkeypatch) -> None:
    captured: dict[str, object] = {}
    truststore_module = types.ModuleType("truststore")
    sentinel_context = object()
    truststore_module.SSLContext = lambda protocol: sentinel_context  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "truststore", truststore_module)

    def fake_create_default_context(*, cafile=None):  # noqa: ANN001
        raise AssertionError("certifi/default fallback should not be used when truststore is available")

    def fake_urlopen(request, timeout, context):  # noqa: ANN001
        captured["request_url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return _FakeResponse()

    monkeypatch.setattr(pipeline.ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)

    payload, meta = pipeline.fetch_url("https://example.org/feed")

    assert payload == b"<rss><channel /></rss>"
    assert captured["context"] is sentinel_context
    assert meta["ssl_mode"] == "truststore"
    assert meta["insecure_ssl_used"] is False


def test_care_line_fetch_url_falls_back_to_default_trust_when_certifi_missing(monkeypatch) -> None:
    captured: dict[str, object] = {}
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name in {"certifi", "truststore"}:
            raise ModuleNotFoundError(f"No module named '{name}'")
        return original_import(name, globals, locals, fromlist, level)

    def fake_create_default_context(*, cafile=None):  # noqa: ANN001
        captured["cafile"] = cafile
        return object()

    def fake_urlopen(request, timeout, context):  # noqa: ANN001
        captured["request_url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return _FakeResponse()

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(pipeline.ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)

    payload, meta = pipeline.fetch_url("https://example.org/feed")

    assert payload == b"<rss><channel /></rss>"
    assert captured["cafile"] is None
    assert captured["request_url"] == "https://example.org/feed"
    assert meta["ssl_mode"] == "default"
    assert meta["insecure_ssl_used"] is False
    assert meta["ssl_warning"] == "truststore and certifi are unavailable; using the system default trust store."


def test_care_line_fetch_url_uses_unverified_context_only_for_explicit_insecure_tls(monkeypatch) -> None:
    captured: dict[str, object] = {}
    sentinel_context = object()

    def fake_unverified_context():  # noqa: ANN001
        captured["insecure_context_requested"] = True
        return sentinel_context

    def fake_create_default_context(*, cafile=None):  # noqa: ANN001
        raise AssertionError("verified trust context should not be created when allow_insecure_tls=True")

    def fake_urlopen(request, timeout, context):  # noqa: ANN001
        captured["request_url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return _FakeResponse()

    monkeypatch.setattr(pipeline.ssl, "_create_unverified_context", fake_unverified_context)
    monkeypatch.setattr(pipeline.ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(pipeline.urllib.request, "urlopen", fake_urlopen)

    payload, meta = pipeline.fetch_url("https://example.org/feed", allow_insecure_tls=True)

    assert payload == b"<rss><channel /></rss>"
    assert captured["insecure_context_requested"] is True
    assert captured["context"] is sentinel_context
    assert meta["ssl_mode"] == "insecure"
    assert meta["insecure_ssl_used"] is True
