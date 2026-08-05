from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_BRANCH = "agent/refine-care-line-signal-wire-public-rendering"
SCHEDULER_SCHEMA = "care_line_collection_scheduler_receipt_v1"
STATUS_ROOT = Path("status/care-line")
LOCK_PATH = STATUS_ROOT / "locks" / "national-collection.lock"
RECEIPT_ROOT = STATUS_ROOT / "scheduler-runs"
LOG_ROOT = Path("logs/care-line/collection-scheduler")
SUCCESS_STATUSES = {"success", "partial_success"}


class SchedulerError(RuntimeError):
    """Fail-closed scheduler error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _command_error(label: str, result: subprocess.CompletedProcess[str]) -> SchedulerError:
    detail = (result.stderr or result.stdout or "no command output").strip().splitlines()
    tail = detail[-1] if detail else "no command output"
    return SchedulerError(f"{label} failed with exit code {result.returncode}: {tail}")


def validate_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise SchedulerError(f"invalid Pacific run date: {value}") from exc


def verify_checkout(root: Path, branch: str) -> str:
    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root)
    if status.returncode != 0:
        raise _command_error("git status", status)
    if status.stdout.strip():
        raise SchedulerError("collection runner checkout is dirty; run failed closed")

    current = _run(["git", "branch", "--show-current"], cwd=root)
    if current.returncode != 0:
        raise _command_error("git branch", current)
    actual_branch = current.stdout.strip() or "<detached>"
    if actual_branch != branch:
        raise SchedulerError(f"collection runner branch mismatch: expected {branch}, found {actual_branch}")

    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if head.returncode != 0:
        raise _command_error("git rev-parse", head)
    return head.stdout.strip()


def run_preflight(root: Path) -> None:
    script = root / "scripts" / "preflight_repo_state.py"
    result = _run([sys.executable, str(script), "--source-repo", str(root)], cwd=root)
    if result.returncode != 0:
        raise _command_error("repository preflight", result)


@dataclass
class SchedulerLock:
    path: Path
    stale_after: timedelta = timedelta(hours=3)
    acquired: bool = False
    stale_recovered: bool = False

    def acquire(self, *, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "started_at": now.isoformat().replace("+00:00", "Z"), "hostname": socket.gethostname()}
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
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


def _write_log(root: Path, *, run_date: str, run_id: str, command: list[str], result: subprocess.CompletedProcess[str]) -> Path:
    path = root / LOG_ROOT / run_date / f"{run_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"started_at={utc_now()}",
        f"command={' '.join(command)}",
        f"exit_code={result.returncode}",
        "",
        "[stdout]",
        result.stdout or "",
        "",
        "[stderr]",
        result.stderr or "",
    ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def run_collection_once(
    root: Path,
    *,
    run_date: str,
    branch: str,
    include_partial: bool,
    include_manual_review: bool,
    allow_insecure_tls: bool,
    source_limit: int | None,
    fetch_timeout: int,
    max_items_per_source: int,
    active_queue_limit: int,
    low_priority_cap: int,
) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    edition_date = validate_date(run_date)
    run_id = f"{stamp()}-{os.getpid()}"
    started_at = utc_now()
    lock = SchedulerLock(root / LOCK_PATH)
    lock_status = lock.acquire()
    if lock_status == "already_running":
        payload = {
            "schema_version": SCHEDULER_SCHEMA,
            "run_id": run_id,
            "edition_date": edition_date,
            "status": "already_running",
            "ok": True,
            "collection_only": True,
            "started_at": started_at,
            "completed_at": utc_now(),
            "lock_path": str((root / LOCK_PATH).as_posix()),
            "stale_lock_recovered": False,
            "pipeline_exit_code": None,
            "pipeline_status": None,
            "publication_side_effects": {
                "proposal_approval": False,
                "edition_generation": False,
                "release_manifest_generation": False,
                "pages_sync": False,
                "public_site_updates": False,
                "signal_wire_publication": False,
                "social_cards": False,
                "audio": False,
                "podcast": False,
                "map": False,
                "rss_publication": False,
                "bluesky_publication": False,
            },
        }
        receipt_path = root / RECEIPT_ROOT / edition_date / f"{run_id}.json"
        atomic_write_json(receipt_path, payload)
        payload["receipt_path"] = str(receipt_path)
        return 0, payload

    try:
        source_commit = verify_checkout(root, branch)
        run_preflight(root)
        command = [
            sys.executable,
            str(root / "scripts" / "run_care_line_national_pipeline.py"),
            "--repo-root", str(root),
            "--collection-only",
            "--run-date", edition_date,
            "--fetch-timeout", str(fetch_timeout),
            "--max-items-per-source", str(max_items_per_source),
            "--active-queue-limit", str(active_queue_limit),
            "--low-priority-cap", str(low_priority_cap),
        ]
        if source_limit is not None:
            command.extend(["--source-limit", str(source_limit)])
        if include_manual_review:
            command.append("--include-manual-review")
        if not include_partial:
            command.append("--exclude-partial")
        if allow_insecure_tls:
            command.append("--allow-insecure-tls")
        result = _run(command, cwd=root)
        log_path = _write_log(root, run_date=edition_date, run_id=run_id, command=command, result=result)
        pipeline_payload: dict[str, Any] = {}
        if result.stdout.strip():
            try:
                pipeline_payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                pipeline_payload = {}
        pipeline_manifest = pipeline_payload.get("run_manifest") if isinstance(pipeline_payload, dict) else {}
        pipeline_status = str((pipeline_manifest or {}).get("status") or "")
        ok = result.returncode == 0 and pipeline_status in SUCCESS_STATUSES
        receipt = {
            "schema_version": SCHEDULER_SCHEMA,
            "run_id": run_id,
            "edition_date": edition_date,
            "status": pipeline_status or ("failure" if result.returncode else "unknown"),
            "ok": ok,
            "collection_only": True,
            "started_at": started_at,
            "completed_at": utc_now(),
            "repo_root": str(root),
            "source_branch": branch,
            "source_commit": source_commit,
            "lock_path": str((root / LOCK_PATH).as_posix()),
            "stale_lock_recovered": lock.stale_recovered,
            "pipeline_exit_code": result.returncode,
            "pipeline_status": pipeline_status,
            "pipeline_run_id": (pipeline_manifest or {}).get("run_id"),
            "successful_attempt_count": (pipeline_manifest or {}).get("successful_attempt_count"),
            "failed_source_count": (pipeline_manifest or {}).get("failed_source_count"),
            "skipped_source_count": (pipeline_manifest or {}).get("skipped_source_count"),
            "active_review_queue_count": (pipeline_manifest or {}).get("active_review_queue_count"),
            "manual_review_count": (pipeline_manifest or {}).get("manual_review_count"),
            "run_manifest_path": str((root / "data" / "dispatches" / "care-line" / "collection-runs" / edition_date / str((pipeline_manifest or {}).get("run_id") or "") / "run-manifest.json").as_posix()) if (pipeline_manifest or {}).get("run_id") else "",
            "review_queue_path": str((root / "data" / "dispatches" / "care-line" / "review" / "current-review-queue.json").as_posix()),
            "candidate_registry_path": str((root / "data" / "dispatches" / "care-line" / "review" / "candidate-registry.json").as_posix()),
            "log_path": str(log_path),
            "publication_side_effects": {
                "proposal_approval": False,
                "edition_generation": False,
                "release_manifest_generation": False,
                "pages_sync": False,
                "public_site_updates": False,
                "signal_wire_publication": False,
                "social_cards": False,
                "audio": False,
                "podcast": False,
                "map": False,
                "rss_publication": False,
                "bluesky_publication": False,
            },
            "stderr_tail": (result.stderr or "").strip().splitlines()[-10:],
        }
        receipt_path = root / RECEIPT_ROOT / edition_date / f"{run_id}.json"
        atomic_write_json(receipt_path, receipt)
        receipt["receipt_path"] = str(receipt_path)
        return (0 if ok else (result.returncode or 1)), receipt
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one guarded Care Line national collection-only scheduler cycle.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--branch", default=PRODUCTION_BRANCH)
    parser.add_argument("--include-manual-review", action="store_true")
    parser.add_argument("--exclude-partial", action="store_true")
    parser.add_argument("--allow-insecure-tls", action="store_true")
    parser.add_argument("--source-limit", type=int, default=None)
    parser.add_argument("--fetch-timeout", type=int, default=20)
    parser.add_argument("--max-items-per-source", type=int, default=25)
    parser.add_argument("--active-queue-limit", type=int, default=150)
    parser.add_argument("--low-priority-cap", type=int, default=25)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code, receipt = run_collection_once(
        Path(args.repo_root),
        run_date=args.run_date,
        branch=args.branch,
        include_partial=not args.exclude_partial,
        include_manual_review=args.include_manual_review,
        allow_insecure_tls=args.allow_insecure_tls,
        source_limit=args.source_limit,
        fetch_timeout=args.fetch_timeout,
        max_items_per_source=args.max_items_per_source,
        active_queue_limit=args.active_queue_limit,
        low_priority_cap=args.low_priority_cap,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
