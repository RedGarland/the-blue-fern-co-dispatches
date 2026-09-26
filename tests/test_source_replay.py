from __future__ import annotations

import json
from pathlib import Path

import pytest

import bluefern_dispatches.care_line_national_pipeline as pipeline
import bluefern_dispatches.source_replay as replay


DATE = "2026-09-26"
PARENT_RUN = "collection-run-1"
SOURCE_ID = "feed-a"


def _source_payload(source_id: str = SOURCE_ID) -> dict[str, object]:
    return {
        "source_id": source_id,
        "name": source_id,
        "publisher": "Example Publisher",
        "source_type": "trade_publication",
        "feed_url": f"https://example.org/{source_id}.xml",
        "homepage_url": "https://example.org/",
        "state": "",
        "geographic_scope": "national",
        "organization_type": "trade_publication",
        "care_line_topics": ["hospital"],
        "authority_level": "secondary",
        "expected_update_frequency": "daily",
        "enabled": True,
        "adapter_type": "rss",
        "requires_html_followup": False,
        "source_role": "healthcare_access_reporting",
        "historical_depth": "current feed",
        "created_at": "2026-09-26T00:00:00Z",
        "updated_at": "2026-09-26T00:00:00Z",
    }


def _write_registry(root: Path, *, enabled: bool = True) -> None:
    source = _source_payload()
    source["enabled"] = enabled
    path = root / "data" / "dispatches" / "care-line" / "source_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "bluefern.care_line.source_registry.v1", "sources": [source]}), encoding="utf-8")


def _write_parent(root: Path, *, status: str = "failed", transient: bool = True, selected: bool = True) -> None:
    run_dir = root / pipeline.COLLECTION_RUNS_ROOT / DATE / PARENT_RUN
    run_dir.mkdir(parents=True, exist_ok=True)
    attempt = {
        "source_id": SOURCE_ID,
        "source_name": SOURCE_ID,
        "collection_status": status,
        "failure_reason": "TimeoutError: timed out" if transient else "HTTPError: 403 Forbidden",
        "raw_item_count": 0,
        "qualified_candidate_count": 0,
    }
    manifest = {
        "schema_version": pipeline.PIPELINE_SCHEMA_VERSION,
        "run_id": PARENT_RUN,
        "run_date": DATE,
        "source_ids": [SOURCE_ID] if selected else ["other"],
        "selected_source_ids": [SOURCE_ID] if selected else ["other"],
        "attempts": [attempt],
        "failed_source_count": 1 if status == "failed" else 0,
    }
    (run_dir / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / pipeline._source_attempt_filename(SOURCE_ID)).write_text(json.dumps(attempt), encoding="utf-8")
    (run_dir / pipeline._source_failure_filename(SOURCE_ID)).write_text(
        json.dumps(
            {
                "source_id": SOURCE_ID,
                "failure_class": "TimeoutError" if transient else "HTTPError",
                "failure_reason": attempt["failure_reason"],
                "transient": transient,
            }
        ),
        encoding="utf-8",
    )


def _ready_root(tmp_path: Path) -> Path:
    _write_registry(tmp_path)
    _write_parent(tmp_path)
    return tmp_path


def _ok_attempt(*, candidates: list[dict] | None = None) -> dict[str, object]:
    return {
        "attempt": {
            "source_id": SOURCE_ID,
            "source_name": SOURCE_ID,
            "collection_status": "ok",
            "raw_item_count": 2,
            "qualified_candidate_count": len(candidates or []),
            "excluded_item_count": 0,
            "failed_extraction_count": 0,
            "content_hash": "abc123",
        },
        "raw_items": [{"raw_item_id": "raw-1"}, {"raw_item_id": "raw-2"}],
        "event_leads": [],
        "candidates": candidates or [],
        "exclusions": [],
        "failed_extractions": [],
        "manual_review": [],
        "failure": "",
    }


@pytest.mark.parametrize("dispatch", ["food-line", "ice", "gaza"])
def test_non_care_dispatches_are_structurally_disabled(dispatch: str, tmp_path: Path) -> None:
    payload = replay.replay_source(
        dispatch=dispatch,
        source_id=SOURCE_ID,
        logical_date=DATE,
        parent_run_id=PARENT_RUN,
        retry_id="retry-1",
        repo_root=tmp_path,
    )
    assert payload["eligible"] is False
    assert payload["publication_attempted"] is False
    assert payload["public_side_effects"] is False


def test_check_mode_validates_eligibility_without_writing(tmp_path: Path) -> None:
    root = _ready_root(tmp_path)
    payload = replay.replay_source(
        dispatch="care-line",
        source_id=SOURCE_ID,
        logical_date=DATE,
        parent_run_id=PARENT_RUN,
        retry_id="retry-1",
        repo_root=root,
        check=True,
    )
    assert payload["outcome"] == "eligible_check_passed"
    assert payload["production_state_mutated"] is False
    assert not (root / replay.SOURCE_REPLAY_ROOT).exists()


def test_replay_recovers_source_and_writes_append_only_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _ready_root(tmp_path)
    monkeypatch.setattr(replay, "run_collection_attempt", lambda *args, **kwargs: _ok_attempt())
    monkeypatch.setattr(replay, "load_reviewed_records", lambda root: [])

    payload = replay.replay_source(
        dispatch="care-line",
        source_id=SOURCE_ID,
        logical_date=DATE,
        parent_run_id=PARENT_RUN,
        retry_id="retry-1",
        repo_root=root,
    )

    assert payload["outcome"] == "recovered"
    assert payload["effective_terminal_source_state"] == "ok"
    assert payload["publication_attempted"] is False
    assert payload["public_side_effects"] is False
    assert (root / payload["receipt_path"]).is_file()
    assert (root / pipeline.COLLECTION_RUNS_ROOT / DATE / PARENT_RUN / "effective-source-state.json").is_file()


def test_replay_is_idempotent_for_same_retry_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _ready_root(tmp_path)
    calls = {"count": 0}

    def fake_attempt(*args, **kwargs):  # noqa: ANN001
        calls["count"] += 1
        return _ok_attempt()

    monkeypatch.setattr(replay, "run_collection_attempt", fake_attempt)
    monkeypatch.setattr(replay, "load_reviewed_records", lambda root: [])
    first = replay.replay_source(dispatch="care-line", source_id=SOURCE_ID, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=root)
    second = replay.replay_source(dispatch="care-line", source_id=SOURCE_ID, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=root)

    assert calls["count"] == 1
    assert first["receipt_path"] == second["receipt_path"]
    assert second["outcome"] in {"recovered", "already_recovered", "already_completed"}


def test_source_already_successful_is_refused(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_parent(tmp_path, status="ok")
    with pytest.raises(replay.SourceReplayError, match="already terminal-successful"):
        replay.replay_source(dispatch="care-line", source_id=SOURCE_ID, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=tmp_path)


def test_non_transient_parent_failure_is_refused(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_parent(tmp_path, transient=False)
    with pytest.raises(replay.SourceReplayError, match="not an eligible transient"):
        replay.replay_source(dispatch="care-line", source_id=SOURCE_ID, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=tmp_path)


def test_unknown_source_is_refused(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_parent(tmp_path)
    with pytest.raises(replay.SourceReplayError, match="source_id is not"):
        replay.replay_source(dispatch="care-line", source_id="missing", logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=tmp_path)


def test_source_not_selected_in_parent_run_is_refused(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    _write_parent(tmp_path, selected=False)
    with pytest.raises(replay.SourceReplayError, match="not selected"):
        replay.replay_source(dispatch="care-line", source_id=SOURCE_ID, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=tmp_path)


@pytest.mark.parametrize("bad_id", ["../escape", "feed/a", "feed a", ""])
def test_replay_rejects_unsafe_identifiers(tmp_path: Path, bad_id: str) -> None:
    _write_registry(tmp_path)
    _write_parent(tmp_path)
    with pytest.raises(replay.SourceReplayError):
        replay.replay_source(dispatch="care-line", source_id=bad_id, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=tmp_path)


def test_candidate_registry_merge_does_not_stale_unrelated_candidates(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": pipeline.CANDIDATE_REGISTRY_SCHEMA_VERSION,
                "candidates": [{"candidate_id": "old", "candidate_status": "active", "normalized_record": {}}],
            }
        ),
        encoding="utf-8",
    )
    payload = pipeline.update_candidate_registry_at_path(
        path,
        edition_date=DATE,
        candidates=[{"candidate_id": "new", "normalized_record": {}}],
        mark_absent_stale=False,
    )
    by_id = {row["candidate_id"]: row for row in payload["candidates"]}
    assert by_id["old"]["candidate_status"] == "active"
    assert by_id["new"]["candidate_status"] == "active"


def test_loader_returns_successful_replay_receipts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _ready_root(tmp_path)
    monkeypatch.setattr(replay, "run_collection_attempt", lambda *args, **kwargs: _ok_attempt())
    monkeypatch.setattr(replay, "load_reviewed_records", lambda root: [])
    replay.replay_source(dispatch="care-line", source_id=SOURCE_ID, logical_date=DATE, parent_run_id=PARENT_RUN, retry_id="retry-1", repo_root=root)
    receipts = replay.load_care_line_source_replay_receipts(root, [DATE])
    recovered = replay.successful_replayed_sources_for_parent(root, logical_date=DATE, parent_run_id=PARENT_RUN)
    assert len(receipts) == 1
    assert recovered[SOURCE_ID]["effective_terminal_source_state"] == "ok"
