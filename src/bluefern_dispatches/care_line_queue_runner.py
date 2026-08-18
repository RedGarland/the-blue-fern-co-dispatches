"""One guarded poll of the reviewed Care Line release queue."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bluefern_dispatches.care_line_reviewed_event_queue import (
    QUEUE_PATH,
    enqueue,
    inspect_queue,
    select_release_set,
)
from bluefern_dispatches.universal_events.care_line_signal_wire import (
    build_care_line_signal_wire_publication,
)

LOCK_NAME = "care-line-reviewed-event-queue.lock"
REPORT_ROOT = Path("data/dispatches/care-line/queue-runs")


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class QueueRunLock:
    path: Path
    stale_after: timedelta = timedelta(hours=6)
    acquired: bool = False
    stale_recovered: bool = False

    def acquire(self, *, now: datetime | None = None) -> str:
        now = now or _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "started_at": _stamp(now), "hostname": socket.gethostname()}
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(_json(payload))
                self.acquired = True
                return "acquired"
            except FileExistsError:
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    started = datetime.fromisoformat(str(current.get("started_at", "")).replace("Z", "+00:00"))
                    stale = now - started > self.stale_after
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    stale = self.path.exists() and now - datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc) > self.stale_after
                if not stale:
                    return "already_running"
                self.path.unlink(missing_ok=True)
                self.stale_recovered = True
        return "already_running"

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_queue_poll(root: Path, *, max_events: int = 5, now: datetime | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    started = now or _now()
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    lock = QueueRunLock(root / QUEUE_PATH.parent / LOCK_NAME)
    lock_status = lock.acquire(now=started)
    if lock_status == "already_running":
        return {"run_id": run_id, "status": lock_status, "ok": True, "report_path": None}
    try:
        enqueue_result = enqueue(root, dry_run=False)
        inspection = inspect_queue(root)
        release = select_release_set(root, max_events=max_events)
        if release["selected_event_ids"]:
            publication = build_care_line_signal_wire_publication(
                root, selected_event_ids=set(release["selected_event_ids"])
            )
            status = "ready_for_operator_release" if publication.get("ok") else "validation_failed"
        else:
            publication = {"ok": True, "status": "nothing_to_publish", "site_artifacts": []}
            status = "nothing_to_publish"
        completed = _now()
        report = {
            "run_id": run_id,
            "started_at": _stamp(started),
            "completed_at": _stamp(completed),
            "status": status,
            "ok": bool(publication.get("ok")),
            "lock": {"path": str(lock.path), "stale_recovered": lock.stale_recovered},
            "enqueue": enqueue_result,
            "inspection": inspection,
            "release_set": release,
            "publication_dry_run": publication,
            "queue_path": str((root / QUEUE_PATH).as_posix()),
        }
        report_path = root / REPORT_ROOT / started.strftime("%Y-%m-%d") / f"{run_id}.json"
        _write_report(report_path, report)
        report["report_path"] = str(report_path)
        return report
    finally:
        lock.release()
