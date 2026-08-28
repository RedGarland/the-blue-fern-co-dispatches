from __future__ import annotations

import json
from pathlib import Path

import bluefern_dispatches.care_line_national_pipeline as pipeline


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

    monkeypatch.setattr(pipeline, "load_reviewed_records", lambda root: [object()])
    monkeypatch.setattr(pipeline, "load_follow_up_state", lambda root: {"schema_version": "test", "items": []})
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
    monkeypatch.setattr(pipeline, "_load_rows", lambda path: discovered_rows)
    monkeypatch.setattr(
        pipeline,
        "update_follow_up_state",
        lambda root, *, run_date, follow_up_queries, discovery_query_rows: captured.update(
            {
                "follow_up_queries": list(follow_up_queries),
                "discovery_query_rows": list(discovery_query_rows),
            }
        )
        or {
            "schema_version": "test",
            "updated_at": "2026-08-20T00:00:00Z",
            "run_date": run_date,
            "items": [{"status": "MATERIAL_UPDATE_FOUND"}],
        },
    )

    result = pipeline.run_national_pipeline(
        tmp_path,
        run_date="2026-08-20",
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
        review_root=Path("data/dispatches/care-line/review"),
    )

    assert captured["follow_up_queries"] == [follow_up_query]
    assert captured["discovery_query_rows"] == discovery_query_rows
    assert result["run_manifest"]["follow_up_query_count"] == 1
    assert result["run_manifest"]["follow_up_material_update_count"] == 1
    assert result["follow_up_state"]["items"][0]["status"] == "MATERIAL_UPDATE_FOUND"


def test_national_pipeline_writes_review_audit_with_dispositions(tmp_path: Path, monkeypatch) -> None:
    discovery_rows = [
        {
            "source_record_id": "care-line-1",
            "source_id": "care-line-1",
            "title": "Hospital announces new ER diversion protocol",
            "url": "https://example.org/er-diversion",
            "canonical_url": "https://example.org/er-diversion",
            "source_url": "https://example.org/er-diversion",
            "source_origin": "live_discovery",
            "source_record_origin": "automated_source_collection",
            "discovery_channel": "google_news_search",
            "source_family": "local_news",
            "source_traceability_role": "publisher_url",
            "query_text": '"ER diversion" hospital',
            "query_url": "https://news.google.com/rss/search?q=%22ER+diversion%22+hospital",
            "retrieved_at": "2026-08-20T01:02:03Z",
            "published_at": "2026-08-20T00:00:00Z",
            "source_published_date": "2026-08-20",
            "confidence": "high",
            "primary_eligible": True,
            "manual_review_required": False,
            "primary_disqualification_reason": "",
            "exclusion_reason": "",
            "freshness_role": "current_signal",
            "classification_status": "qualified_pressure_signal",
        },
        {
            "source_record_id": "care-line-2",
            "source_id": "care-line-2",
            "title": "Hospital awards gala planned",
            "url": "https://example.org/gala",
            "canonical_url": "https://example.org/gala",
            "source_url": "https://example.org/gala",
            "source_origin": "manual_fallback",
            "source_record_origin": "agent_intake",
            "discovery_channel": "manual_intake",
            "source_family": "local_news",
            "source_traceability_role": "publisher_url",
            "query_text": '"hospital" gala',
            "query_url": "https://news.google.com/rss/search?q=%22hospital%22+gala",
            "retrieved_at": "2026-08-20T01:04:03Z",
            "published_at": "2026-08-20T00:00:00Z",
            "source_published_date": "2026-08-20",
            "confidence": "low",
            "primary_eligible": False,
            "manual_review_required": True,
            "primary_disqualification_reason": "resource_only_baseline",
            "exclusion_reason": "resource_only_baseline",
            "freshness_role": "background_context",
            "classification_status": "likely_resource_only",
        },
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr(pipeline, "load_reviewed_records", lambda root: [])
    monkeypatch.setattr(pipeline, "load_follow_up_state", lambda root: {"schema_version": "test", "items": []})
    monkeypatch.setattr(pipeline, "build_follow_up_queries", lambda root, run_date, reviewed_records, state: [])
    monkeypatch.setattr(
        pipeline,
        "discover_care_line_sources",
        lambda root, run_date, **kwargs: {
            "discovered_sources_path": str(root / "data" / "dispatches" / "care-line" / "sources" / run_date / "discovered_sources.json"),
            "query_rows": [{"query": "q1", "url": "https://example.org/q1", "error": "", "results": 2}],
            "source_count": len(discovery_rows),
            "public_signal_count": 1,
        },
    )
    monkeypatch.setattr(pipeline, "_load_rows", lambda path: discovery_rows)
    monkeypatch.setattr(
        pipeline,
        "update_follow_up_state",
        lambda root, *, run_date, follow_up_queries, discovery_query_rows: {"schema_version": "test", "updated_at": "2026-08-20T00:00:00Z", "run_date": run_date, "items": []},
    )

    result = pipeline.run_national_pipeline(
        tmp_path,
        run_date="2026-08-20",
        collection_runs_root=Path("data/dispatches/care-line/collection-runs"),
        review_root=Path("data/dispatches/care-line/review"),
    )

    audit_path = Path(result["run_manifest"]["review_audit_path"])
    audit_markdown_path = Path(result["run_manifest"]["review_audit_markdown_path"])
    audit = audit_path.read_text(encoding="utf-8")
    markdown = audit_markdown_path.read_text(encoding="utf-8")
    payload = json.loads(audit)

    assert result["run_manifest"]["manual_review_count"] == 1
    assert result["run_manifest"]["review_audit_disposition_counts"] == {"included": 1, "manual_review": 1}
    assert payload["counts"]["discovered"] == 2
    assert payload["counts"]["included"] == 1
    assert payload["counts"]["manual_review"] == 1
    assert payload["counts"]["excluded"] == 1
    assert payload["disposition_counts"] == {"included": 1, "manual_review": 1}
    assert payload["source_origin_counts"] == {"live_discovery": 1, "manual_fallback": 1}
    assert payload["rows"][0]["source_traceability"]["source_origin"] == "live_discovery"
    assert payload["rows"][0]["publication_eligible"] is True
    assert payload["rows"][1]["watchlist_status"] == "watchlist_candidate"
    assert payload["rows"][1]["exclusion_reason"] == "resource_only_baseline"
    assert "Care Line review audit 2026-08-20" in markdown
    assert "- discovered: 2" in markdown
    assert audit_path.exists()
    assert audit_markdown_path.exists()
