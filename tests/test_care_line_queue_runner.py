from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from bluefern_dispatches.care_line_queue_runner import QueueRunLock, run_queue_poll


def test_active_lock_is_respected(tmp_path):
    path = tmp_path / "queue.lock"
    started = datetime(2026, 7, 26, tzinfo=timezone.utc)
    path.write_text(json.dumps({"pid": 10, "started_at": "2026-07-26T00:00:00Z", "hostname": "host"}))
    lock = QueueRunLock(path)
    assert lock.acquire(now=started) == "already_running"
    assert path.exists()


def test_stale_lock_is_recovered_and_cleaned(tmp_path):
    path = tmp_path / "queue.lock"
    started = datetime(2026, 7, 26, tzinfo=timezone.utc)
    path.write_text(json.dumps({"pid": 10, "started_at": "2026-07-25T00:00:00Z", "hostname": "host"}))
    lock = QueueRunLock(path, stale_after=timedelta(hours=6))
    assert lock.acquire(now=started) == "acquired"
    assert lock.stale_recovered is True
    lock.release()
    assert not path.exists()


def test_runner_reports_success_when_release_set_is_empty(tmp_path, monkeypatch):
    import bluefern_dispatches.care_line_queue_runner as runner

    monkeypatch.setattr(runner, "enqueue", lambda root, dry_run=False: {"added": [], "excluded": []})
    monkeypatch.setattr(runner, "inspect_queue", lambda root: {"counts_by_state": {}, "queue": []})
    monkeypatch.setattr(runner, "select_release_set", lambda root, max_events=None: {"selected_event_ids": [], "selected_queue_ids": [], "excluded": []})
    result = run_queue_poll(tmp_path, now=datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert result["ok"] is True
    assert result["status"] == "nothing_to_publish"
    assert (tmp_path / result["report_path"]).exists()
