from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import time
import re
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

PRODUCTION_BRANCH = "agent/refine-care-line-signal-wire-public-rendering"
PRIVATE_AGENT_INBOX_ROOT = ROOT / "data" / "dispatches" / "food-line" / "agent-inbox"
ALLOWED_DIRTY_CATEGORIES = {"review_output", "logs", "cache", "virtualenv", "local_run_state"}
FOOD_LINE_DISCOVERY_CANDIDATES_RE = re.compile(
    r"^data/dispatches/food-line/discovery/\d{4}-\d{2}-\d{2}/discovery_candidates\.json$"
)
FOOD_LINE_AGENT_INBOX_RE = re.compile(r"^data/dispatches/food-line/agent-inbox(?:/.*)?$")
QUALIFYING_COLLECTION_STATUSES = {"completed", "completed_with_exclusions"}
QUALIFYING_EXPORT_STATUSES = {"success", "success_with_exclusions", "no_exportable_findings"}
RESUMABLE_COLLECTION_STATUSES = {"partial", "timed_out", "cancelled", "failed"}
RUN_STATE_SCHEMA = "food_line_bounded_run_state_v1"
RUN_RECORD_SCHEMA = "food_line_scheduled_run_record_v1"
SOURCE_RECEIPT_SCHEMA = "food_line_source_watch_receipt_v1"
INTAKE_RECEIPT_SCHEMA = "food_line_current_intake_receipt_v1"
ATTENTION_SCHEMA = "food_line_operator_attention_v1"
class SchedulerError(RuntimeError):
    """A fail-closed operational error."""


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


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchedulerError(f"missing or corrupt JSON state: {path}") from exc
    if not isinstance(value, dict):
        raise SchedulerError(f"JSON state must be an object: {path}")
    return value


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
    dirty_paths = _parse_porcelain_paths(status_output)
    return sorted(dirty_paths)


def _classify_dirty_path(path_text: str) -> str:
    path = path_text.strip().replace("\\", "/")
    lower = path.lower()
    root_name = lower.split("/", 1)[0] if lower else lower
    if not path:
        return "unknown"
    if lower.startswith(".venv/") or root_name in {"venv", "env", ".venv"}:
        return "virtualenv"
    if lower.startswith("logs/") or lower.endswith(".log") or "/logs/" in lower:
        return "logs"
    if lower.startswith(".pytest_cache/") or lower.startswith(".pytest-temp") or lower.startswith(".pytest_tmp") or "/cache/" in lower or lower.startswith("cache/") or lower.startswith("tmp/") or lower.startswith(".tmp"):
        return "cache"
    if lower.startswith("tests/") or "/tests/" in lower:
        return "tests"
    if lower.startswith("docs/") or root_name in {"readme.md", "project_summary.md", "agents.md"} or lower.startswith(".github/"):
        return "docs"
    if lower.startswith("src/") or lower.startswith("scripts/") or root_name in {"pyproject.toml", "requirements.txt", ".gitignore"}:
        return "source"
    if lower == "data/dispatches/food-line/source_performance_history.json":
        return "local_run_state"
    if FOOD_LINE_DISCOVERY_CANDIDATES_RE.match(lower):
        return "local_run_state"
    if FOOD_LINE_AGENT_INBOX_RE.match(lower):
        return "local_run_state"
    if lower.startswith("output/review/") or "/review/" in lower or lower.startswith("output/dispatches/") and "/review/" in lower:
        return "review_output"
    if lower.startswith("output/site/") or lower.startswith("bluefern-dispatches-pages/"):
        return "generated_public_output"
    if lower.startswith("data/dispatches/") and ("/raw/" in lower or "/normalized/" in lower or "/curated/" in lower or "/editions/" in lower):
        return "generated_public_output"
    return "unknown"


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
        unexpected = [path for path in _unexpected_dirty_paths(status.stdout) if _classify_dirty_path(path) not in ALLOWED_DIRTY_CATEGORIES]
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
        unexpected = [path for path in _unexpected_dirty_paths(final_status.stdout) if _classify_dirty_path(path) not in ALLOWED_DIRTY_CATEGORIES]
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


@contextmanager
def source_lock(layout: Layout, edition_date: str, task: str, *, stale_minutes: int = 45):
    lock_dir = layout.lock_dir
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        age_seconds = max(0.0, time.time() - lock_dir.stat().st_mtime)
        kind = "stale_lock" if age_seconds >= stale_minutes * 60 else "overlapping_run"
        attention = write_attention(
            layout,
            edition_date,
            kind,
            "Food Line source-watch lock already exists",
            lock_path=str(lock_dir),
            lock_age_seconds=round(age_seconds, 3),
        )
        raise SchedulerError(f"source-watch lock exists ({kind}); see {attention}") from exc
    atomic_write_json(
        lock_dir / "owner.json",
        {"task": task, "pid": os.getpid(), "acquired_at": utc_now(), "edition_date": edition_date},
    )
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
    try:
        with source_lock(layout, edition_date, "source-watch", stale_minutes=args.stale_lock_minutes):
            source_commit = verify_checkout(root, args.branch, update=not args.test_mode, test_mode=args.test_mode)
            run_preflight(root, python, test_mode=args.test_mode)
            run_dir = layout.run_dir(edition_date, run_id)
            state_path = run_dir / "run-state.json"
            record = {
                "schema_version": RUN_RECORD_SCHEMA,
                "edition_date": edition_date,
                "run_id": run_id,
                "source_commit": source_commit,
                "source_branch": args.branch,
                "run_state_path": str(state_path),
                "scheduled_start_at": started_at,
                "resume_attempted": False,
            }
            atomic_write_json(layout.run_record(edition_date), record)
            result = _invoke_python(
                python,
                root,
                [
                    "scripts/run_food_line_discovery_expansion.py",
                    "--date", edition_date,
                    "--profile", "daily-current",
                    "--run-id", run_id,
                    "--max-run-minutes", "30",
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
                "exit_code": 0 if collection_qualifies(state) else (command_exit or 2),
            }
            receipt_path = _source_receipt_path(layout, edition_date, "source-watch")
            atomic_write_json(receipt_path, receipt)
            record.update({"last_status": state.get("status"), "source_receipt_path": str(receipt_path)})
            atomic_write_json(layout.run_record(edition_date), record)
            print(json.dumps({"ok": collection_qualifies(state), "receipt_path": str(receipt_path), **receipt}, indent=2))
            if collection_qualifies(state):
                return 0
            attention = write_attention(
                layout, edition_date, "source_watch_nonqualifying", "Food Line source watch did not qualify",
                run_id=run_id, final_status=state.get("status"), receipt_path=str(receipt_path),
            )
            return command_exit or 2
    except SchedulerError as exc:
        write_attention(layout, edition_date, "source_watch_failed", str(exc), run_id=run_id)
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


def _verify_same_source_commit(root: Path, branch: str, expected: str, *, test_mode: bool) -> None:
    current = verify_checkout(root, branch, update=False, test_mode=test_mode)
    if not test_mode and current != expected:
        raise SchedulerError(f"runner source commit changed after source watch: expected {expected}, found {current}")


def run_resume(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve()
    python = Path(args.python).resolve()
    edition_date = validate_date(args.edition_date)
    layout = Layout(root)
    try:
        with source_lock(layout, edition_date, "status-resume", stale_minutes=args.stale_lock_minutes):
            record, state, state_path = _load_record_and_state(layout, edition_date)
            _verify_same_source_commit(root, args.branch, str(record.get("source_commit")), test_mode=args.test_mode)
            run_preflight(root, python, test_mode=args.test_mode)
            run_id = str(record["run_id"])
            status_result = _invoke_python(
                python, root, ["scripts/run_food_line_discovery_expansion.py", "--status-run", run_id]
            )
            if status_result.returncode != 0:
                raise _command_error("source-watch status inspection", status_result)
            resume_status = "resume_not_required"
            command_exit = 0
            if not collection_qualifies(state):
                if state.get("status") not in RESUMABLE_COLLECTION_STATUSES or not bool(state.get("resumable")):
                    raise SchedulerError(f"source-watch state is not qualifying or resumable: {state.get('status')}")
                if int(state.get("resume_count") or 0) >= 1 or bool(record.get("resume_attempted")):
                    raise SchedulerError("source-watch already used its one permitted resume")
                record["resume_attempted"] = True
                record["resume_started_at"] = utc_now()
                atomic_write_json(layout.run_record(edition_date), record)
                resume_status = "resume_attempted"
                resumed = _invoke_python(
                    python,
                    root,
                    [
                        "scripts/run_food_line_discovery_expansion.py",
                        "--date", edition_date,
                        "--resume-run", run_id,
                        "--export-agent-inbox",
                        "--agent-inbox-dir", str(PRIVATE_AGENT_INBOX_ROOT),
                    ],
                )
                command_exit = int(resumed.returncode)
                inspected = _invoke_python(
                    python, root, ["scripts/run_food_line_discovery_expansion.py", "--status-run", run_id]
                )
                if inspected.returncode != 0:
                    raise _command_error("post-resume status inspection", inspected)
                state = read_json(state_path)
                validate_run_state(state, edition_date, run_id)
                resume_status = "resume_qualified" if collection_qualifies(state) else "resume_nonqualifying"

            survivors = surviving_worker_pids(state_path.parent)
            if survivors:
                raise SchedulerError(f"source watch resume left surviving worker processes: {survivors}")

            receipt = {
                "schema_version": SOURCE_RECEIPT_SCHEMA,
                "action": "status_resume",
                "task_started_at": record.get("resume_started_at") or utc_now(),
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
            record.update({"last_status": state.get("status"), "resume_status": resume_status, "resume_receipt_path": str(receipt_path)})
            atomic_write_json(layout.run_record(edition_date), record)
            print(json.dumps({"ok": collection_qualifies(state), "receipt_path": str(receipt_path), **receipt}, indent=2))
            if collection_qualifies(state):
                return 0
            write_attention(
                layout, edition_date, "resume_nonqualifying", "Food Line source watch remained nonqualifying after its bounded resume",
                run_id=run_id, final_status=state.get("status"), receipt_path=str(receipt_path),
            )
            return command_exit or 2
    except SchedulerError as exc:
        write_attention(layout, edition_date, "status_resume_failed", str(exc))
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
        print(json.dumps({"ok": True, "receipt_path": str(receipt_path), **receipt}, indent=2))
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
