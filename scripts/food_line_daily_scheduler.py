from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.food_line_runtime_paths import classify_food_line_runtime_path, is_food_line_mutable_tracked_runtime_path

PRODUCTION_BRANCH = "add/pages-repo-default"
PRIVATE_AGENT_INBOX_ROOT = ROOT / "data" / "dispatches" / "food-line" / "agent-inbox"
FOOD_LINE_DISCOVERY_MAX_RUN_MINUTES = 30.0
FOOD_LINE_DISCOVERY_MAX_QUERIES = 200
QUALIFYING_COLLECTION_STATUSES = {"completed", "completed_with_exclusions"}
QUALIFYING_EXPORT_STATUSES = {"success", "success_with_exclusions", "no_exportable_findings"}
RESUMABLE_COLLECTION_STATUSES = {"partial", "timed_out", "cancelled", "failed"}
ALLOWED_NONFATAL_CHILD_OUTCOMES = {"completed_with_exclusions"}
CHILD_OUTPUT_AUDIT_CHAR_LIMIT = 4096
RUN_STATE_SCHEMA = "food_line_bounded_run_state_v1"
RUN_RECORD_SCHEMA = "food_line_scheduled_run_record_v1"
SOURCE_RECEIPT_SCHEMA = "food_line_source_watch_receipt_v1"
INTAKE_RECEIPT_SCHEMA = "food_line_current_intake_receipt_v1"
ATTENTION_SCHEMA = "food_line_operator_attention_v1"
INITIALIZING_STATUS = "initializing"
RUNNING_STATUS = "running"
BLOCKED_OVERLAPPING_STATUS = "blocked_overlapping_run"
UPSTREAM_NOT_INITIALIZED_STATUS = "source_watch_not_initialized"
UPSTREAM_IN_PROGRESS_STATUS = "source_watch_in_progress"
AMBIGUOUS_STALE_LOCK_STATUS = "stale_lock_ambiguous"
UPSTREAM_BLOCKED_STATUSES = {
    BLOCKED_OVERLAPPING_STATUS,
    UPSTREAM_NOT_INITIALIZED_STATUS,
    UPSTREAM_IN_PROGRESS_STATUS,
    AMBIGUOUS_STALE_LOCK_STATUS,
}
TERMINAL_SUCCESS_STATUSES = {*QUALIFYING_COLLECTION_STATUSES}


class SchedulerError(RuntimeError):
    """A fail-closed operational error."""


class SourceLockUnavailable(SchedulerError):
    """Raised when the source-watch lock is held by another active or ambiguous owner."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        attention: Path,
        lock_path: Path,
        lock_age_seconds: float,
        owner_metadata: dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.attention = attention
        self.lock_path = lock_path
        self.lock_age_seconds = lock_age_seconds
        self.owner_metadata = owner_metadata


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def state_root(self) -> Path:
        return self.root / "status" / "food-line"

    @property
    def lock_dir(self) -> Path:
        return self.state_root / "locks" / "source-watch.lock"

    def run_record(self, edition_date: str) -> Path:
        return self.state_root / "runs" / f"{edition_date}.json"

    def run_dir(self, edition_date: str, run_id: str) -> Path:
        return self.root / "data" / "dispatches" / "food-line" / "discovery-runs" / edition_date / run_id

    def source_log_dir(self, edition_date: str) -> Path:
        return self.root / "logs" / "food-line" / "source-watch" / edition_date

    def intake_log_dir(self, edition_date: str) -> Path:
        return self.root / "logs" / "food-line" / "current-intake" / edition_date

    def attention_dir(self, edition_date: str) -> Path:
        return self.root / "logs" / "food-line" / "operator-attention" / edition_date


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def validate_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SchedulerError(f"invalid Pacific edition date: {value}") from exc
    return parsed.date().isoformat()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def terminal_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchedulerError(f"missing or corrupt JSON state: {path}") from exc
    if not isinstance(value, dict):
        raise SchedulerError(f"JSON state must be an object: {path}")
    return value


def read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return read_json(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    process_query_limited_information = 0x1000
    still_active = 259
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _read_lock_owner(lock_dir: Path) -> dict[str, Any]:
    owner = lock_dir / "owner.json"
    if not owner.exists():
        return {}
    try:
        value = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"owner_read_error": "missing_or_corrupt_owner_json"}
    return value if isinstance(value, dict) else {"owner_read_error": "owner_json_not_object"}


def _lock_owner_pid(owner: dict[str, Any]) -> int | None:
    try:
        pid = int(owner.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _lock_is_proven_stale(owner: dict[str, Any], *, age_seconds: float, stale_seconds: float) -> bool:
    if age_seconds < stale_seconds:
        return False
    pid = _lock_owner_pid(owner)
    if pid is None:
        return False
    return not process_is_running(pid)


def _reclaim_stale_lock(layout: Layout, edition_date: str, lock_dir: Path, owner: dict[str, Any], age_seconds: float) -> bool:
    recovery_dir = lock_dir.with_name(f"{lock_dir.name}.stale.{os.getpid()}.{int(time.time())}")
    try:
        lock_dir.rename(recovery_dir)
    except OSError:
        return False
    try:
        shutil.rmtree(recovery_dir)
    except OSError:
        return False
    write_attention(
        layout,
        edition_date,
        "stale_lock_reclaimed",
        "Food Line source-watch lock was proven stale and reclaimed",
        lock_path=str(lock_dir),
        lock_age_seconds=round(age_seconds, 3),
        lock_owner=owner,
    )
    return True


def surviving_worker_pids(run_dir: Path) -> list[int]:
    pids: set[int] = set()
    for path in sorted((run_dir / "partitions").glob("*.json")) if (run_dir / "partitions").exists() else []:
        try:
            artifact = read_json(path)
        except SchedulerError:
            continue
        for metadata in artifact.get("query_result_metadata") or []:
            if isinstance(metadata, dict) and metadata.get("worker_pid"):
                pids.add(int(metadata["worker_pid"]))
    return sorted(pid for pid in pids if process_is_running(pid))


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _command_error(label: str, result: subprocess.CompletedProcess[str]) -> SchedulerError:
    detail = (result.stderr or result.stdout or "no command output").strip().splitlines()
    tail = detail[-1] if detail else "no command output"
    return SchedulerError(f"{label} failed with exit code {result.returncode}: {tail}")


def _bounded_output_tail(value: str | None, *, limit: int = CHILD_OUTPUT_AUDIT_CHAR_LIMIT) -> dict[str, Any]:
    text = value or ""
    truncated = len(text) > limit
    return {
        "tail": text[-limit:] if truncated else text,
        "truncated": truncated,
        "char_count": len(text),
        "limit": limit,
    }


def _parse_child_terminal_result(stdout: str | None) -> tuple[dict[str, Any] | None, str]:
    text = (stdout or "").strip()
    if not text:
        return None, "missing"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, "malformed"
    if not isinstance(payload, dict):
        return None, "malformed"
    return payload, "parsed"


def _nonempty_text(value: Any) -> str:
    return str(value or "").strip()


def _state_requires_export(state: dict[str, Any]) -> bool:
    export = state.get("agent_export") if isinstance(state.get("agent_export"), dict) else {}
    return export.get("status") in {"success", "success_with_exclusions"}


def _required_export_exists(state: dict[str, Any]) -> bool:
    export = state.get("agent_export") if isinstance(state.get("agent_export"), dict) else {}
    if export.get("status") == "no_exportable_findings":
        return True
    if export.get("status") not in {"success", "success_with_exclusions"}:
        return False
    path_text = _nonempty_text(export.get("path"))
    if not path_text:
        return False
    return Path(path_text).exists()


def _child_state_contradiction(payload: dict[str, Any], state: dict[str, Any]) -> str:
    child_status = _nonempty_text(payload.get("status"))
    durable_status = _nonempty_text(state.get("status"))
    if child_status and durable_status and child_status != durable_status:
        return f"child status {child_status!r} does not match durable status {durable_status!r}"
    child_run_id = _nonempty_text(payload.get("run_id"))
    durable_run_id = _nonempty_text(state.get("run_id"))
    if child_run_id and durable_run_id and child_run_id != durable_run_id:
        return "child run_id does not match durable run_id"
    child_date = _nonempty_text(payload.get("edition_date"))
    durable_date = _nonempty_text(state.get("edition_date"))
    if child_date and durable_date and child_date != durable_date:
        return "child edition_date does not match durable edition_date"
    child_error = _nonempty_text(payload.get("fatal_error") or payload.get("final_error"))
    durable_error = _nonempty_text(state.get("final_error"))
    if child_error or durable_error:
        if child_error != durable_error:
            return "child fatal/final error contradicts durable final_error"
    child_export = payload.get("agent_export") if isinstance(payload.get("agent_export"), dict) else {}
    durable_export = state.get("agent_export") if isinstance(state.get("agent_export"), dict) else {}
    child_export_status = _nonempty_text(child_export.get("status"))
    durable_export_status = _nonempty_text(durable_export.get("status"))
    if child_export_status and durable_export_status and child_export_status != durable_export_status:
        return "child export status does not match durable export status"
    return ""


def child_result_audit(result: subprocess.CompletedProcess[str], state: dict[str, Any]) -> dict[str, Any]:
    payload, parse_status = _parse_child_terminal_result(result.stdout)
    stdout = _bounded_output_tail(result.stdout)
    stderr = _bounded_output_tail(result.stderr)
    terminal_found = parse_status != "missing"
    terminal_parsed = parse_status == "parsed"
    classification = "missing_terminal_result" if parse_status == "missing" else "malformed_terminal_result" if parse_status == "malformed" else "unknown"
    validation_error = "" if terminal_parsed else f"child terminal output {parse_status}"
    child_status = ""
    child_ok: bool | None = None
    child_declared_outcome = ""
    child_error_type = ""
    child_error_message = ""
    contradiction = ""
    if payload is not None:
        child_status = _nonempty_text(payload.get("status"))
        child_ok = bool(payload.get("ok"))
        child_declared_outcome = _nonempty_text(payload.get("child_outcome_classification"))
        child_error_type = _nonempty_text(payload.get("error_type"))
        child_error_message = _nonempty_text(payload.get("error_message") or payload.get("error"))
        contradiction = _child_state_contradiction(payload, state)
        if _nonempty_text(payload.get("fatal_error") or payload.get("final_error")):
            classification = "fatal"
            validation_error = "child terminal result reports a fatal/final error"
        elif contradiction:
            classification = "contradiction"
            validation_error = contradiction
        elif child_declared_outcome in ALLOWED_NONFATAL_CHILD_OUTCOMES:
            if child_ok:
                classification = f"allowed_nonfatal_{child_declared_outcome}"
                validation_error = ""
            else:
                classification = "unknown"
                validation_error = "allowed nonfatal child outcome requires ok=true"
        elif child_status in ALLOWED_NONFATAL_CHILD_OUTCOMES:
            classification = "unknown"
            validation_error = "nonfatal child status lacks an allowed outcome classification"
        elif result.returncode == 0 and child_ok:
            classification = "success"
            validation_error = ""
        else:
            classification = "unknown"
            validation_error = "child terminal result lacks an allowed outcome classification"
    return {
        "child_exit_code": int(result.returncode),
        "child_terminal_output_found": terminal_found,
        "child_terminal_output_parsed": terminal_parsed,
        "child_outcome_classification": classification,
        "child_declared_outcome": child_declared_outcome,
        "child_terminal_status": child_status,
        "child_terminal_ok": child_ok,
        "child_error_type": child_error_type,
        "child_error_message": child_error_message,
        "child_validation_error": validation_error,
        "child_stdout_tail": stdout["tail"],
        "child_stdout_truncated": stdout["truncated"],
        "child_stdout_char_count": stdout["char_count"],
        "child_stderr_tail": stderr["tail"],
        "child_stderr_truncated": stderr["truncated"],
        "child_stderr_char_count": stderr["char_count"],
        "child_output_char_limit": CHILD_OUTPUT_AUDIT_CHAR_LIMIT,
        "required_export_present": _required_export_exists(state),
        "required_export_required": _state_requires_export(state),
    }


def child_result_allows_scheduler_success(
    result: subprocess.CompletedProcess[str],
    state: dict[str, Any],
    audit: dict[str, Any],
) -> bool:
    if not collection_qualifies(state):
        return False
    if _state_requires_export(state) and not bool(audit.get("required_export_present")):
        return False
    if int(result.returncode) != 0:
        return False
    allowed_nonfatal_classifications = {
        f"allowed_nonfatal_{outcome}" for outcome in ALLOWED_NONFATAL_CHILD_OUTCOMES
    }
    classification = audit.get("child_outcome_classification")
    return classification == "success" or classification in allowed_nonfatal_classifications


def _parse_porcelain_paths(output: str) -> list[str]:
    paths: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if len(line) < 4:
            raise SchedulerError(f"unexpected git status porcelain line: {raw_line!r}")
        payload = line[3:]
        if " -> " in payload:
            raise SchedulerError("runner checkout contains rename or copy status outside the allowed operational scope")
        normalized = payload.replace("\\", "/").strip()
        if not normalized:
            raise SchedulerError(f"unexpected git status porcelain path: {raw_line!r}")
        paths.append(normalized)
    return paths


def _unexpected_dirty_paths(status_output: str) -> list[str]:
    unexpected: list[str] = []
    for raw_line in status_output.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("## "):
            continue
        if len(line) < 4:
            raise SchedulerError(f"unexpected git status porcelain line: {raw_line!r}")
        status = line[:2]
        path = _parse_porcelain_paths(raw_line)[0]
        category = classify_food_line_runtime_path(path)
        if status == " M" and is_food_line_mutable_tracked_runtime_path(path):
            continue
        if status != "??" or category not in {"review_output", "logs", "cache", "virtualenv", "local_run_state"}:
            unexpected.append(path)
    return sorted(unexpected)


def verify_checkout(root: Path, branch: str, *, update: bool, test_mode: bool = False) -> str:
    root = root.resolve()
    if not (root / ".git").exists():
        raise SchedulerError(f"runner is not a Git checkout: {root}")
    if test_mode:
        return "test-mode-source-commit"

    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root)
    if status.returncode != 0:
        raise _command_error("git status", status)
    if status.stdout.strip():
        unexpected = _unexpected_dirty_paths(status.stdout)
        if unexpected:
            raise SchedulerError("runner checkout is dirty; scheduled operation failed closed")

    current = _run(["git", "branch", "--show-current"], cwd=root)
    if current.returncode != 0:
        raise _command_error("git branch", current)
    if current.stdout.strip() != branch:
        raise SchedulerError(f"runner branch mismatch: expected {branch}, found {current.stdout.strip() or '<detached>'}")

    if update:
        fetched = _run(["git", "fetch", "origin", branch], cwd=root)
        if fetched.returncode != 0:
            raise _command_error("git fetch", fetched)
        ancestor = _run(["git", "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"], cwd=root)
        if ancestor.returncode != 0:
            raise SchedulerError("runner branch cannot fast-forward to the production branch")
        merged = _run(["git", "merge", "--ff-only", f"origin/{branch}"], cwd=root)
        if merged.returncode != 0:
            raise _command_error("git fast-forward", merged)

    final_status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root)
    if final_status.returncode != 0:
        raise SchedulerError("runner checkout is dirty after synchronization")
    if final_status.stdout.strip():
        unexpected = _unexpected_dirty_paths(final_status.stdout)
        if unexpected:
            raise SchedulerError("runner checkout is dirty after synchronization")
    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if head.returncode != 0:
        raise _command_error("git rev-parse", head)
    return head.stdout.strip()


def run_preflight(root: Path, python: Path, *, test_mode: bool) -> None:
    if test_mode:
        return
    script = root / "scripts" / "preflight_repo_state.py"
    result = _run([str(python), str(script), "--source-repo", str(root)], cwd=root)
    if result.returncode != 0:
        raise _command_error("repository preflight", result)


def write_attention(layout: Layout, edition_date: str, category: str, message: str, **details: Any) -> Path:
    path = layout.attention_dir(edition_date) / f"{stamp()}-{category}.json"
    payload = {
        "schema_version": ATTENTION_SCHEMA,
        "created_at": utc_now(),
        "edition_date": edition_date,
        "category": category,
        "message": message,
        "requires_operator_attention": True,
        "details": details,
    }
    atomic_write_json(path, payload)
    print(f"operator_attention={path}", file=sys.stderr)
    return path


def _run_record_status(record: dict[str, Any] | None) -> str:
    if not isinstance(record, dict):
        return ""
    return _nonempty_text(record.get("source_watch_status") or record.get("last_status") or record.get("status"))


def _existing_record_is_success(record: dict[str, Any] | None) -> bool:
    return _run_record_status(record) in TERMINAL_SUCCESS_STATUSES and bool(record.get("source_receipt_path") if isinstance(record, dict) else False)


def write_run_record(layout: Layout, edition_date: str, record: dict[str, Any], *, preserve_success: bool = True) -> None:
    existing: dict[str, Any] | None = None
    try:
        existing = read_optional_json(layout.run_record(edition_date))
    except SchedulerError:
        existing = None
    if preserve_success and existing and _existing_record_is_success(existing):
        return
    payload = dict(record)
    payload.setdefault("schema_version", RUN_RECORD_SCHEMA)
    payload.setdefault("edition_date", edition_date)
    payload["updated_at"] = utc_now()
    atomic_write_json(layout.run_record(edition_date), payload)


def initial_run_record(
    layout: Layout,
    edition_date: str,
    run_id: str,
    *,
    source_branch: str,
    started_at: str,
) -> dict[str, Any]:
    state_path = layout.run_dir(edition_date, run_id) / "run-state.json"
    return {
        "schema_version": RUN_RECORD_SCHEMA,
        "edition_date": edition_date,
        "run_id": run_id,
        "source_branch": source_branch,
        "run_state_path": str(state_path),
        "scheduled_start_at": started_at,
        "attempted_at": started_at,
        "source_watch_status": INITIALIZING_STATUS,
        "last_status": INITIALIZING_STATUS,
        "resume_attempted": False,
        "release_ready": False,
    }


def write_blocked_source_watch_record(
    layout: Layout,
    edition_date: str,
    run_id: str,
    *,
    source_branch: str,
    started_at: str,
    lock_error: SourceLockUnavailable,
) -> dict[str, Any]:
    record = initial_run_record(layout, edition_date, run_id, source_branch=source_branch, started_at=started_at)
    record.update(
        {
            "source_watch_status": BLOCKED_OVERLAPPING_STATUS if lock_error.kind == "overlapping_run" else AMBIGUOUS_STALE_LOCK_STATUS,
            "last_status": BLOCKED_OVERLAPPING_STATUS if lock_error.kind == "overlapping_run" else AMBIGUOUS_STALE_LOCK_STATUS,
            "error": str(lock_error),
            "attention_path": str(lock_error.attention),
            "lock_path": str(lock_error.lock_path),
            "lock_age_seconds": round(lock_error.lock_age_seconds, 3),
            "lock_owner": lock_error.owner_metadata,
        }
    )
    write_run_record(layout, edition_date, record)
    return record


def _source_noop_receipt(
    *,
    action: str,
    started_at: str,
    edition_date: str,
    source_branch: str,
    run_id: str | None,
    status: str,
    reason: str,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_RECEIPT_SCHEMA,
        "action": action,
        "task_started_at": started_at,
        "task_completed_at": utc_now(),
        "edition_date": edition_date,
        "source_branch": source_branch,
        "run_id": run_id,
        "final_status": status,
        "resume_status": status if action == "status_resume" else "not_applicable",
        "reason": reason,
        "source_watch_status": _run_record_status(record) or status,
        "command_exit_code": 0,
        "exit_code": 0,
    }


def _write_source_noop(
    layout: Layout,
    edition_date: str,
    receipt: dict[str, Any],
    *,
    receipt_action: str,
) -> Path:
    receipt_path = _source_receipt_path(layout, edition_date, receipt_action)
    atomic_write_json(receipt_path, receipt)
    return receipt_path


def _write_intake_upstream_skip(
    layout: Layout,
    edition_date: str,
    started_at: str,
    *,
    record: dict[str, Any] | None,
    status: str,
    reason: str,
) -> Path:
    receipt = {
        "schema_version": INTAKE_RECEIPT_SCHEMA,
        "task_started_at": started_at,
        "task_completed_at": utc_now(),
        "edition_date": edition_date,
        "source_commit": record.get("source_commit") if isinstance(record, dict) else None,
        "qualifying_discovery_run_id": record.get("run_id") if isinstance(record, dict) else None,
        "source_status": _run_record_status(record) or status,
        "source_export_status": None,
        "inbox_files_discovered": 0,
        "accepted_files": 0,
        "imported_findings": 0,
        "exclusions": [reason],
        "queue_item_count": 0,
        "proposal_status": status,
        "proposal_path": None,
        "operator_review_required": False,
        "publication_side_effects": {},
        "command_exit_code": 0,
        "exit_code": 0,
        "status": status,
        "reason": reason,
    }
    receipt_path = _intake_receipt_path(layout, edition_date)
    atomic_write_json(receipt_path, receipt)
    return receipt_path


@contextmanager
def source_lock(layout: Layout, edition_date: str, task: str, *, stale_minutes: int = 45, run_id: str | None = None):
    lock_dir = layout.lock_dir
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        age_seconds = max(0.0, time.time() - lock_dir.stat().st_mtime)
        owner_metadata = _read_lock_owner(lock_dir)
        stale_seconds = max(0, stale_minutes) * 60
        if _lock_is_proven_stale(owner_metadata, age_seconds=age_seconds, stale_seconds=stale_seconds):
            if _reclaim_stale_lock(layout, edition_date, lock_dir, owner_metadata, age_seconds):
                lock_dir.mkdir()
            else:
                owner_metadata = _read_lock_owner(lock_dir)
                kind = "stale_lock_ambiguous"
                attention = write_attention(
                    layout,
                    edition_date,
                    kind,
                    "Food Line source-watch lock could not be safely reclaimed",
                    lock_path=str(lock_dir),
                    lock_age_seconds=round(age_seconds, 3),
                    lock_owner=owner_metadata,
                )
                raise SourceLockUnavailable(
                    f"source-watch lock exists ({kind}); see {attention}",
                    kind=kind,
                    attention=attention,
                    lock_path=lock_dir,
                    lock_age_seconds=age_seconds,
                    owner_metadata=owner_metadata,
                ) from exc
        else:
            kind = "overlapping_run" if age_seconds < stale_seconds else "stale_lock_ambiguous"
            if _lock_owner_pid(owner_metadata) is not None and process_is_running(int(owner_metadata["pid"])):
                kind = "overlapping_run"
            message = "Food Line source-watch lock already exists"
            if kind == "stale_lock_ambiguous":
                message = "Food Line source-watch lock could not be proven stale"
            attention = write_attention(
                layout,
                edition_date,
                kind,
                message,
                lock_path=str(lock_dir),
                lock_age_seconds=round(age_seconds, 3),
                lock_owner=owner_metadata,
            )
            raise SourceLockUnavailable(
                f"source-watch lock exists ({kind}); see {attention}",
                kind=kind,
                attention=attention,
                lock_path=lock_dir,
                lock_age_seconds=age_seconds,
                owner_metadata=owner_metadata,
            ) from exc
    owner_payload = {"task": task, "pid": os.getpid(), "acquired_at": utc_now(), "edition_date": edition_date}
    if run_id:
        owner_payload["run_id"] = run_id
    atomic_write_json(lock_dir / "owner.json", owner_payload)
    try:
        yield
    finally:
        owner = lock_dir / "owner.json"
        if owner.exists():
            owner.unlink()
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def wait_for_source_lock(layout: Layout, edition_date: str, *, wait_seconds: int, poll_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + max(0, wait_seconds)
    while layout.lock_dir.exists() and time.monotonic() < deadline:
        time.sleep(max(0.05, poll_seconds))
    if layout.lock_dir.exists():
        age_seconds = max(0.0, time.time() - layout.lock_dir.stat().st_mtime)
        attention = write_attention(
            layout,
            edition_date,
            "intake_lock_timeout",
            "Food Line intake timed out waiting for source-watch/resume lock",
            lock_path=str(layout.lock_dir),
            lock_age_seconds=round(age_seconds, 3),
        )
        raise SchedulerError(f"source-watch lock did not clear before intake; see {attention}")


def validate_run_state(state: dict[str, Any], edition_date: str, run_id: str) -> None:
    if state.get("schema_version") != RUN_STATE_SCHEMA:
        raise SchedulerError("structurally invalid Food Line run-state schema")
    if state.get("edition_date") != edition_date or state.get("run_id") != run_id:
        raise SchedulerError("Food Line run-state identity mismatch")


def collection_qualifies(state: dict[str, Any]) -> bool:
    if state.get("status") not in QUALIFYING_COLLECTION_STATUSES:
        return False
    coverage = state.get("coverage") if isinstance(state.get("coverage"), dict) else {}
    options = state.get("options") if isinstance(state.get("options"), dict) else {}
    required_ratio = float(coverage.get("required_success_ratio") or 0.0)
    direct_ratio = float(coverage.get("direct_success_ratio") or 0.0)
    required_threshold = float(options.get("required_coverage_threshold") or 0.90)
    direct_threshold = float(options.get("direct_source_coverage_threshold") or 0.75)
    partitions_terminal = int(state.get("partitions_completed") or 0) == int(state.get("partitions_total") or -1)
    export = state.get("agent_export") if isinstance(state.get("agent_export"), dict) else {}
    return (
        required_ratio >= required_threshold
        and direct_ratio >= direct_threshold
        and partitions_terminal
        and export.get("status") in QUALIFYING_EXPORT_STATUSES
        and not state.get("final_error")
    )


def _invoke_python(python: Path, root: Path, arguments: Iterable[str]) -> subprocess.CompletedProcess[str]:
    return _run([str(python), *map(str, arguments)], cwd=root)


def _safe_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    coverage = state.get("coverage") if isinstance(state.get("coverage"), dict) else {}
    export = state.get("agent_export") if isinstance(state.get("agent_export"), dict) else {}
    return {
        "final_status": state.get("status"),
        "query_plan_sha256": state.get("query_plan_sha256"),
        "queries_planned": state.get("queries_total"),
        "queries_completed": state.get("queries_completed"),
        "queries_failed": state.get("queries_failed"),
        "queries_timed_out": state.get("queries_timed_out"),
        "required_query_coverage": coverage.get("required_success_ratio"),
        "direct_source_coverage": coverage.get("direct_success_ratio"),
        "candidate_count": state.get("candidates_discovered"),
        "export_status": export.get("status"),
        "inbox_export_path": export.get("path"),
        "inbox_export_sha256": export.get("sha256"),
    }


def _source_receipt_path(layout: Layout, edition_date: str, action: str) -> Path:
    return layout.source_log_dir(edition_date) / f"{stamp()}-{action}.json"


def _intake_receipt_path(layout: Layout, edition_date: str) -> Path:
    return layout.intake_log_dir(edition_date) / f"{stamp()}-current-intake.json"


def run_source_watch(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    python = Path(args.python).resolve()
    edition_date = validate_date(args.edition_date)
    run_id = str(args.run_id)
    layout = Layout(root)
    command_exit = 10
    started_at = utc_now()
    record = initial_run_record(layout, edition_date, run_id, source_branch=args.branch, started_at=started_at)
    write_run_record(layout, edition_date, record)
    try:
        with source_lock(layout, edition_date, "source-watch", stale_minutes=args.stale_lock_minutes, run_id=run_id):
            record["source_watch_status"] = RUNNING_STATUS
            record["last_status"] = RUNNING_STATUS
            record["source_watch_started_at"] = utc_now()
            write_run_record(layout, edition_date, record)
            source_commit = verify_checkout(root, args.branch, update=not args.test_mode, test_mode=args.test_mode)
            run_preflight(root, python, test_mode=args.test_mode)
            run_dir = layout.run_dir(edition_date, run_id)
            state_path = run_dir / "run-state.json"
            record.update({"source_commit": source_commit, "run_state_path": str(state_path)})
            write_run_record(layout, edition_date, record)
            result = _invoke_python(
                python,
                root,
                [
                    "scripts/run_food_line_discovery_expansion.py",
                    "--date", edition_date,
                    "--profile", "daily-current",
                    "--run-id", run_id,
                    "--max-run-minutes", f"{FOOD_LINE_DISCOVERY_MAX_RUN_MINUTES:g}",
                    "--max-queries", str(FOOD_LINE_DISCOVERY_MAX_QUERIES),
                    "--export-agent-inbox",
                    "--agent-inbox-dir", str(PRIVATE_AGENT_INBOX_ROOT),
                ],
            )
            command_exit = int(result.returncode)
            if not state_path.exists():
                raise SchedulerError("source runner returned without durable run-state")
            state = read_json(state_path)
            validate_run_state(state, edition_date, run_id)
            survivors = surviving_worker_pids(run_dir)
            if survivors:
                raise SchedulerError(f"source watch left surviving worker processes: {survivors}")
            child_audit = child_result_audit(result, state)
            scheduler_success = child_result_allows_scheduler_success(result, state, child_audit)
            plan_path = run_dir / "query-plan.json"
            plan = read_json(plan_path)
            receipt = {
                "schema_version": SOURCE_RECEIPT_SCHEMA,
                "action": "source_watch",
                "task_started_at": started_at,
                "task_completed_at": utc_now(),
                "edition_date": edition_date,
                "source_commit": source_commit,
                "source_branch": args.branch,
                "run_id": run_id,
                "configuration_sha256": plan.get("configuration_sha256"),
                **_safe_state_summary(state),
                "resume_status": "not_attempted",
                "command_exit_code": command_exit,
                **child_audit,
                "exit_code": 0 if scheduler_success else (command_exit or 2),
            }
            receipt_path = _source_receipt_path(layout, edition_date, "source-watch")
            atomic_write_json(receipt_path, receipt)
            record.update({"last_status": state.get("status"), "source_watch_status": state.get("status"), "source_receipt_path": str(receipt_path)})
            write_run_record(layout, edition_date, record, preserve_success=False)
            print(terminal_json({"ok": scheduler_success, "receipt_path": str(receipt_path), **receipt}))
            if scheduler_success:
                return 0
            attention = write_attention(
                layout, edition_date, "source_watch_nonqualifying", "Food Line source watch did not qualify",
                run_id=run_id, final_status=state.get("status"), receipt_path=str(receipt_path),
                child_validation_error=child_audit.get("child_validation_error"),
                child_outcome_classification=child_audit.get("child_outcome_classification"),
            )
            return command_exit or 2
    except SourceLockUnavailable as exc:
        blocked = write_blocked_source_watch_record(
            layout,
            edition_date,
            run_id,
            source_branch=args.branch,
            started_at=started_at,
            lock_error=exc,
        )
        receipt = _source_noop_receipt(
            action="source_watch",
            started_at=started_at,
            edition_date=edition_date,
            source_branch=args.branch,
            run_id=run_id,
            status=_run_record_status(blocked),
            reason=str(exc),
            record=blocked,
        )
        receipt.update(
            {
                "lock_path": str(exc.lock_path),
                "lock_age_seconds": round(exc.lock_age_seconds, 3),
                "lock_owner": exc.owner_metadata,
                "attention_path": str(exc.attention),
                "command_exit_code": 10,
                "exit_code": 10,
            }
        )
        receipt_path = _write_source_noop(layout, edition_date, receipt, receipt_action="source-watch")
        blocked["source_receipt_path"] = str(receipt_path)
        write_run_record(layout, edition_date, blocked, preserve_success=False)
        print(terminal_json({"ok": False, "receipt_path": str(receipt_path), **receipt}))
        return 10
    except SchedulerError as exc:
        attention = write_attention(layout, edition_date, "source_watch_failed", str(exc), run_id=run_id)
        record.update({"last_status": "failed", "source_watch_status": "failed", "error": str(exc), "attention_path": str(attention)})
        write_run_record(layout, edition_date, record, preserve_success=False)
        print(terminal_json({"ok": False, "edition_date": edition_date, "run_id": run_id, "status": "failed", "reason": str(exc), "attention_path": str(attention), "exit_code": command_exit if command_exit not in {0, 10} else 10}))
        print(str(exc), file=sys.stderr)
        return command_exit if command_exit not in {0, 10} else 10


def _load_record_and_state(layout: Layout, edition_date: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    record = read_json(layout.run_record(edition_date))
    if record.get("schema_version") != RUN_RECORD_SCHEMA or record.get("edition_date") != edition_date:
        raise SchedulerError("scheduled Food Line run record is structurally invalid")
    run_id = str(record.get("run_id") or "")
    expected = layout.run_dir(edition_date, run_id) / "run-state.json"
    recorded = Path(str(record.get("run_state_path") or "")).resolve()
    if recorded != expected.resolve():
        raise SchedulerError("scheduled Food Line run-state path is outside the expected run directory")
    state = read_json(expected)
    validate_run_state(state, edition_date, run_id)
    return record, state, expected


def _load_run_record(layout: Layout, edition_date: str) -> dict[str, Any] | None:
    record = read_optional_json(layout.run_record(edition_date))
    if record is None:
        return None
    if record.get("schema_version") != RUN_RECORD_SCHEMA or record.get("edition_date") != edition_date:
        raise SchedulerError("scheduled Food Line run record is structurally invalid")
    return record


def _expected_state_path(layout: Layout, edition_date: str, record: dict[str, Any]) -> Path:
    run_id = str(record.get("run_id") or "")
    expected = layout.run_dir(edition_date, run_id) / "run-state.json"
    recorded = Path(str(record.get("run_state_path") or "")).resolve()
    if recorded != expected.resolve():
        raise SchedulerError("scheduled Food Line run-state path is outside the expected run directory")
    return expected


def _verify_same_source_commit(root: Path, branch: str, expected: str, *, test_mode: bool) -> None:
    current = verify_checkout(root, branch, update=False, test_mode=test_mode)
    if not test_mode and current != expected:
        raise SchedulerError(f"runner source commit changed after source watch: expected {expected}, found {current}")


def run_resume(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    python = Path(args.python).resolve()
    edition_date = validate_date(args.edition_date)
    layout = Layout(root)
    started_at = utc_now()
    try:
        record = _load_run_record(layout, edition_date)
        if record is None:
            receipt = _source_noop_receipt(
                action="status_resume",
                started_at=started_at,
                edition_date=edition_date,
                source_branch=args.branch,
                run_id=None,
                status=UPSTREAM_NOT_INITIALIZED_STATUS,
                reason="source watch has not initialized the daily run record",
            )
            receipt_path = _write_source_noop(layout, edition_date, receipt, receipt_action="status-resume")
            print(terminal_json({"ok": True, "receipt_path": str(receipt_path), **receipt}))
            return 0
        status = _run_record_status(record)
        if status in UPSTREAM_BLOCKED_STATUSES | {INITIALIZING_STATUS, RUNNING_STATUS, "failed"}:
            skip_status = UPSTREAM_IN_PROGRESS_STATUS if status in {INITIALIZING_STATUS, RUNNING_STATUS} else "upstream_blocked"
            receipt = _source_noop_receipt(
                action="status_resume",
                started_at=started_at,
                edition_date=edition_date,
                source_branch=args.branch,
                run_id=str(record.get("run_id") or ""),
                status=skip_status,
                reason=f"source watch state is not resumable: {status}",
                record=record,
            )
            receipt_path = _write_source_noop(layout, edition_date, receipt, receipt_action="status-resume")
            record.update({"resume_status": receipt["resume_status"], "resume_receipt_path": str(receipt_path)})
            write_run_record(layout, edition_date, record, preserve_success=False)
            print(terminal_json({"ok": True, "receipt_path": str(receipt_path), **receipt}))
            return 0
        state_path = _expected_state_path(layout, edition_date, record)
        state = read_json(state_path)
        validate_run_state(state, edition_date, str(record.get("run_id") or ""))
        _verify_same_source_commit(root, args.branch, str(record.get("source_commit")), test_mode=args.test_mode)
        run_preflight(root, python, test_mode=args.test_mode)
        run_id = str(record["run_id"])
        status_result = _invoke_python(
            python,
            root,
            ["scripts/run_food_line_discovery_expansion.py", "--status-run", run_id, "--run-id", run_id],
        )
        if status_result.returncode != 0:
            raise _command_error("source-watch status inspection", status_result)
        command_exit = 0
        resume_status = "resume_not_required"
        if not collection_qualifies(state):
            if state.get("status") not in RESUMABLE_COLLECTION_STATUSES or not bool(state.get("resumable")):
                raise SchedulerError(f"source-watch state is not qualifying or resumable: {state.get('status')}")
            with source_lock(layout, edition_date, "status-resume", stale_minutes=args.stale_lock_minutes, run_id=str(record.get("run_id") or "")):
                record, state, state_path = _load_record_and_state(layout, edition_date)
                if collection_qualifies(state) or state.get("status") not in RESUMABLE_COLLECTION_STATUSES or not bool(state.get("resumable")):
                    resume_status = "resume_not_required"
                else:
                    if int(state.get("resume_count") or 0) >= 1 or bool(record.get("resume_attempted")):
                        raise SchedulerError("source-watch already used its one permitted resume")
                    record["resume_attempted"] = True
                    record["resume_started_at"] = utc_now()
                    write_run_record(layout, edition_date, record, preserve_success=False)
                    resumed = _invoke_python(
                        python,
                        root,
                        [
                            "scripts/run_food_line_discovery_expansion.py",
                            "--date", edition_date,
                            "--resume-run", str(record["run_id"]),
                            "--run-id", str(record["run_id"]),
                            "--max-run-minutes", f"{FOOD_LINE_DISCOVERY_MAX_RUN_MINUTES:g}",
                            "--max-queries", str(FOOD_LINE_DISCOVERY_MAX_QUERIES),
                            "--export-agent-inbox",
                            "--agent-inbox-dir", str(PRIVATE_AGENT_INBOX_ROOT),
                        ],
                    )
                    command_exit = int(resumed.returncode)
                    inspected = _invoke_python(
                        python,
                        root,
                        ["scripts/run_food_line_discovery_expansion.py", "--status-run", str(record["run_id"]), "--run-id", str(record["run_id"])],
                    )
                    if inspected.returncode != 0:
                        raise _command_error("post-resume status inspection", inspected)
                    state = read_json(state_path)
                    validate_run_state(state, edition_date, str(record["run_id"]))
                    resume_status = "resume_qualified" if collection_qualifies(state) else "resume_nonqualifying"

        survivors = surviving_worker_pids(state_path.parent)
        if survivors:
            raise SchedulerError(f"source watch resume left surviving worker processes: {survivors}")

        receipt = {
            "schema_version": SOURCE_RECEIPT_SCHEMA,
            "action": "status_resume",
            "task_started_at": record.get("resume_started_at") or started_at,
            "task_completed_at": utc_now(),
            "edition_date": edition_date,
            "source_commit": record.get("source_commit"),
            "source_branch": args.branch,
            "run_id": run_id,
            **_safe_state_summary(state),
            "resume_status": resume_status,
            "command_exit_code": command_exit,
            "exit_code": 0 if collection_qualifies(state) else (command_exit or 2),
        }
        receipt_path = _source_receipt_path(layout, edition_date, "status-resume")
        atomic_write_json(receipt_path, receipt)
        record.update({"last_status": state.get("status"), "source_watch_status": state.get("status"), "resume_status": resume_status, "resume_receipt_path": str(receipt_path)})
        write_run_record(layout, edition_date, record, preserve_success=False)
        print(terminal_json({"ok": collection_qualifies(state), "receipt_path": str(receipt_path), **receipt}))
        if collection_qualifies(state):
            return 0
        write_attention(
            layout, edition_date, "resume_nonqualifying", "Food Line source watch remained nonqualifying after its bounded resume",
            run_id=run_id, final_status=state.get("status"), receipt_path=str(receipt_path),
        )
        return command_exit or 2
    except SchedulerError as exc:
        write_attention(layout, edition_date, "status_resume_failed", str(exc))
        print(terminal_json({"ok": False, "edition_date": edition_date, "status": "status_resume_failed", "reason": str(exc), "exit_code": 10}))
        print(str(exc), file=sys.stderr)
        return 10


def run_intake(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    python = Path(args.python).resolve()
    edition_date = validate_date(args.edition_date)
    layout = Layout(root)
    started_at = utc_now()
    command_exit = 10
    try:
        wait_for_source_lock(layout, edition_date, wait_seconds=args.lock_wait_seconds, poll_seconds=args.lock_poll_seconds)
        record = _load_run_record(layout, edition_date)
        if record is None:
            receipt_path = _write_intake_upstream_skip(
                layout,
                edition_date,
                started_at,
                record=None,
                status=UPSTREAM_NOT_INITIALIZED_STATUS,
                reason="source watch has not initialized the daily run record",
            )
            print(terminal_json({"ok": True, "receipt_path": str(receipt_path), "status": UPSTREAM_NOT_INITIALIZED_STATUS, "exit_code": 0}))
            return 0
        status = _run_record_status(record)
        if status in UPSTREAM_BLOCKED_STATUSES | {INITIALIZING_STATUS, RUNNING_STATUS, "failed"}:
            skip_status = UPSTREAM_IN_PROGRESS_STATUS if status in {INITIALIZING_STATUS, RUNNING_STATUS} else "upstream_blocked"
            receipt_path = _write_intake_upstream_skip(
                layout,
                edition_date,
                started_at,
                record=record,
                status=skip_status,
                reason=f"source watch state is not intake-ready: {status}",
            )
            record.update({"intake_status": skip_status, "intake_receipt_path": str(receipt_path)})
            write_run_record(layout, edition_date, record, preserve_success=False)
            print(terminal_json({"ok": True, "receipt_path": str(receipt_path), "status": skip_status, "exit_code": 0}))
            return 0
        record, state, _ = _load_record_and_state(layout, edition_date)
        _verify_same_source_commit(root, args.branch, str(record.get("source_commit")), test_mode=args.test_mode)
        run_preflight(root, python, test_mode=args.test_mode)
        survivors = surviving_worker_pids(layout.run_dir(edition_date, str(record["run_id"])))
        if survivors:
            raise SchedulerError(f"intake blocked by surviving source-watch workers: {survivors}")
        if not collection_qualifies(state):
            raise SchedulerError(f"intake blocked by nonqualifying source-watch state: {state.get('status')}")
        result = _invoke_python(
            python,
            root,
            [
                "scripts/process_food_line_current_intake.py",
                "--edition-date", edition_date,
                "--inbox", str(PRIVATE_AGENT_INBOX_ROOT),
                "--build-review-queue",
                "--build-proposed-edition",
            ],
        )
        command_exit = int(result.returncode)
        report_path = root / "data" / "dispatches" / "food-line" / "review" / "reports" / edition_date / "current-intake.json"
        report = read_json(report_path)
        if report.get("schema_version") != "food_line_current_intake_report_v1":
            raise SchedulerError("unexpected current-intake report schema")
        errors = report.get("errors") if isinstance(report.get("errors"), list) else ["invalid errors field"]
        if command_exit != 0 or report.get("status") not in {"success", "success_with_exclusions"} or errors:
            raise SchedulerError("current-intake validation or processing failed")
        side_effects = report.get("publication_side_effects") if isinstance(report.get("publication_side_effects"), dict) else {}
        if any(bool(value) for value in side_effects.values()):
            raise SchedulerError("current-intake reported an unexpected publication side effect")
        proposal = report.get("proposal") if isinstance(report.get("proposal"), dict) else {}
        queue = report.get("queue") if isinstance(report.get("queue"), dict) else {}
        receipt = {
            "schema_version": INTAKE_RECEIPT_SCHEMA,
            "task_started_at": started_at,
            "task_completed_at": utc_now(),
            "edition_date": edition_date,
            "source_commit": record.get("source_commit"),
            "qualifying_discovery_run_id": record.get("run_id"),
            "source_status": state.get("status"),
            "source_export_status": (state.get("agent_export") or {}).get("status"),
            "inbox_files_discovered": report.get("discovered_file_count"),
            "accepted_files": report.get("accepted_file_count"),
            "imported_findings": report.get("import_count"),
            "exclusions": report.get("errors"),
            "queue_item_count": queue.get("item_count"),
            "proposal_status": proposal.get("draft_status"),
            "proposal_path": proposal.get("markdown_path"),
            "operator_review_required": True,
            "publication_side_effects": side_effects,
            "command_exit_code": command_exit,
            "exit_code": 0,
        }
        receipt_path = _intake_receipt_path(layout, edition_date)
        atomic_write_json(receipt_path, receipt)
        record.update({"intake_receipt_path": str(receipt_path), "intake_completed_at": utc_now()})
        atomic_write_json(layout.run_record(edition_date), record)
        print(terminal_json({"ok": True, "receipt_path": str(receipt_path), **receipt}))
        return 0
    except SchedulerError as exc:
        write_attention(layout, edition_date, "current_intake_failed", str(exc))
        print(str(exc), file=sys.stderr)
        return command_exit if command_exit not in {0, 10} else 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the private Food Line daily scheduler flow.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("source-watch", "resume", "intake"):
        child = subparsers.add_parser(name)
        child.add_argument("--repo-root", required=True)
        child.add_argument("--python", required=True)
        child.add_argument("--edition-date", required=True)
        child.add_argument("--branch", default=PRODUCTION_BRANCH)
        child.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
        child.add_argument("--stale-lock-minutes", type=int, default=45, help=argparse.SUPPRESS)
    subparsers.choices["source-watch"].add_argument("--run-id", required=True)
    subparsers.choices["intake"].add_argument("--lock-wait-seconds", type=int, default=300)
    subparsers.choices["intake"].add_argument("--lock-poll-seconds", type=float, default=2.0, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "source-watch":
        return run_source_watch(args)
    if args.command == "resume":
        return run_resume(args)
    return run_intake(args)


if __name__ == "__main__":
    raise SystemExit(main())
