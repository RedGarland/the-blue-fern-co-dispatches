from __future__ import annotations

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
