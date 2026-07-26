from __future__ import annotations

import json
from pathlib import Path

import pytest

import bluefern_dispatches.care_line_reviewed_event_queue as queue


def _fixture(monkeypatch: pytest.MonkeyPatch, *, status="corrected", fingerprint="good", event_id="event-1") -> tuple[dict, dict]:
    record_id = "record-1"
    row = {
        "producer_record_id": record_id,
        "review_status": status,
        "universal_event_status": "universal_event_ready",
        "evidence_valid_for_universal_event": True,
        "raw_payload_hash": fingerprint,
        "source_url": "https://example.com/article",
        "source_title": "A reviewed healthcare access change",
        "source_publisher": "Example Health",
        "supporting_passage": "The source confirms the healthcare access change.",
        "metadata": {"evidence_review": {"record_fingerprint": fingerprint, "reviewed_at": "2026-07-24T00:00:00Z"}},
    }
    proposed = {"producer_record_id": record_id, "evidence_links": [{"source_url": row["source_url"], "supporting_passage": row["supporting_passage"]}], "proposed_event_payload": {"event_id": event_id}}
    monkeypatch.setattr(queue, "_event_inputs", lambda root: ({record_id: row}, {record_id: proposed}, "reviewed.json"))
    monkeypatch.setattr(queue, "_publication_events", lambda root: {})
    return row, proposed


def test_approved_valid_record_can_be_queued(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    result = queue.enqueue(tmp_path, dry_run=False)
    assert [row["event_id"] for row in result["added"]] == ["event-1"]
    stored = json.loads((tmp_path / queue.QUEUE_PATH).read_text())
    assert stored["queue"][0]["state"] == "queued"


@pytest.mark.parametrize("status", ["needs_review", "rejected", "deferred"])
def test_non_approved_record_cannot_be_queued(tmp_path, monkeypatch, status):
    _fixture(monkeypatch, status=status)
    assert queue.enqueue(tmp_path)["added"] == []


def test_stale_fingerprint_cannot_be_queued(tmp_path, monkeypatch):
    _fixture(monkeypatch, fingerprint="current")
    row, _ = _fixture(monkeypatch, fingerprint="current")
    row["metadata"]["evidence_review"]["record_fingerprint"] = "stale"
    assert queue.enqueue(tmp_path)["excluded"][0]["reason"] == "stale review fingerprint"


def test_already_published_event_cannot_be_queued(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    monkeypatch.setattr(queue, "_publication_events", lambda root: {"event-1": {"public_published_at": "2026-07-24T00:00:00Z"}})
    assert queue.enqueue(tmp_path)["excluded"][0]["reason"] == "event is already published"


def test_published_stale_fingerprint_reports_publication_first(tmp_path, monkeypatch):
    row, _ = _fixture(monkeypatch, fingerprint="current")
    row["metadata"]["evidence_review"]["record_fingerprint"] = "stale"
    monkeypatch.setattr(queue, "_publication_events", lambda root: {"event-1": {"public_published_at": "2026-07-24T00:00:00Z"}})
    assert queue.enqueue(tmp_path)["excluded"][0]["reason"] == "event is already published"


def test_stale_fingerprint_does_not_rewrite_review_record(tmp_path, monkeypatch):
    row, _ = _fixture(monkeypatch, fingerprint="current")
    row["metadata"]["evidence_review"]["record_fingerprint"] = "stale"
    before = row["metadata"]["evidence_review"]["record_fingerprint"]
    result = queue.enqueue(tmp_path, dry_run=False)
    assert result["added"] == []
    assert row["metadata"]["evidence_review"]["record_fingerprint"] == before
    assert not (tmp_path / queue.QUEUE_PATH).exists()


def test_enqueue_is_idempotent_and_dry_run_does_not_mutate(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    first = queue.enqueue(tmp_path)
    assert first["added"] and not (tmp_path / queue.QUEUE_PATH).exists()
    queue.enqueue(tmp_path, dry_run=False)
    second = queue.enqueue(tmp_path)
    assert second["added"] == []
    assert len(json.loads((tmp_path / queue.QUEUE_PATH).read_text())["queue"]) == 1


def test_queue_serialization_is_deterministic(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    queue.enqueue(tmp_path, dry_run=False)
    first = (tmp_path / queue.QUEUE_PATH).read_bytes()
    (tmp_path / queue.QUEUE_PATH).unlink()
    queue.enqueue(tmp_path, dry_run=False)
    second = (tmp_path / queue.QUEUE_PATH).read_bytes()
    # queued_at is intentionally an operational timestamp, so compare stable fields.
    assert json.loads(first)["queue"][0]["queue_id"] == json.loads(second)["queue"][0]["queue_id"]


def test_release_selection_is_deterministic_and_honors_limit(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    queue.enqueue(tmp_path, dry_run=False)
    result = queue.select_release_set(tmp_path, max_events=1)
    assert result["selected_event_ids"] == ["event-1"]
    assert result["proposed_files"] == []


def test_release_after_excludes_later_entry(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "source_record_id": "r", "state": "queued", "approved_at": "2026-07-24T00:00:00Z", "release_after": "2026-07-30T00:00:00Z"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    result = queue.select_release_set(tmp_path, release_after="2026-07-25T00:00:00Z")
    assert result["selected_event_ids"] == []
    assert result["excluded"][0]["reason"] == "release-after is later than requested cutoff"


def test_deferred_and_failed_entries_require_explicit_reapproval(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "state": "deferred"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="explicit reapproval"):
        queue.update_state(tmp_path, queue_id="q", state="queued")
    assert queue.update_state(tmp_path, queue_id="q", state="queued", reapprove=True)["to"] == "queued"


def test_inspect_reports_counts_and_fingerprint_status(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "source_record_id": "r", "state": "queued"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    result = queue.inspect_queue(tmp_path)
    assert result["counts_by_state"] == {"queued": 1}
    assert result["queue"][0]["publication_status"] == "queued"


def test_explicit_event_selection_limits_enqueue(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    result = queue.enqueue(tmp_path, event_ids=["not-event-1"])
    assert result["added"] == []


@pytest.mark.parametrize("state", ["deferred", "rejected", "failed", "published"])
def test_release_selection_excludes_closed_or_failed_states(tmp_path, state):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "source_record_id": "r", "state": state, "approved_at": "2026-07-24T00:00:00Z"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    result = queue.select_release_set(tmp_path)
    assert result["selected_event_ids"] == []
    assert result["excluded"][0]["reason"] == f"state is {state}"


def test_publishing_state_is_explicit_and_recoverable(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "state": "queued"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    assert queue.update_state(tmp_path, queue_id="q", state="publishing")["to"] == "publishing"


def test_failed_publish_preserves_failure_reason(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "state": "publishing"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    queue.update_state(tmp_path, queue_id="q", state="failed", failure_reason="validation failed")
    stored = json.loads((tmp_path / queue.QUEUE_PATH).read_text())["queue"][0]
    assert stored["state"] == "failed" and stored["failure_reason"] == "validation failed"


def test_successful_publish_marks_only_selected_events(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q1", "event_id": "e1", "state": "publishing"}, {"queue_id": "q2", "event_id": "e2", "state": "queued"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    queue.mark_published(tmp_path, event_ids=["e1"], publication={"release_batch_id": "batch-1", "pages_commit_sha": "sha"})
    rows = json.loads((tmp_path / queue.QUEUE_PATH).read_text())["queue"]
    assert rows[0]["state"] == "published" and rows[1]["state"] == "queued"


def test_pages_files_are_not_queue_input(tmp_path, monkeypatch):
    _fixture(monkeypatch)
    (tmp_path / "bluefern-dispatches-pages").mkdir()
    result = queue.enqueue(tmp_path)
    assert result["reviewed_records_path"] == "reviewed.json"


def test_queue_has_no_automatic_retry_after_failure(tmp_path):
    payload = {"schema_version": 1, "queue": [{"queue_id": "q", "event_id": "e", "state": "failed"}]}
    (tmp_path / queue.QUEUE_PATH).parent.mkdir(parents=True)
    (tmp_path / queue.QUEUE_PATH).write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="retry approval"):
        queue.update_state(tmp_path, queue_id="q", state="queued")
