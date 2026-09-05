from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_release_render import CareLineApprovedReleaseBundle, load_approved_release
from bluefern_dispatches.generator import public_edition_is_listable
from scripts.care_line_runtime_paths import CARE_LINE_ALLOWED_DIRTY_CATEGORIES, classify_care_line_runtime_path

PRODUCTION_BRANCH = "add/pages-repo-default"
PAGES_BRANCH = "gh-pages"
SCHEDULER_SCHEMA = "care_line_publication_scheduler_receipt_v1"
RECEIPT_ROOT = Path("status/care-line/publication-scheduler-runs")
LOG_ROOT = Path("logs/care-line/publication-scheduler")
LOCK_PATH = Path("status/care-line/locks/publication.lock")


class PublicationSchedulerError(RuntimeError):
    """A condition that must stop scheduled Care Line publication."""


@dataclass(frozen=True)
class ChildExecution:
    pid: int | None
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ReleaseCandidate:
    edition_date: str
    bundle: CareLineApprovedReleaseBundle
    already_published: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def _run_child(command: list[str], *, cwd: Path) -> ChildExecution:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    return ChildExecution(process.pid, int(process.returncode or 0), stdout or "", stderr or "")


def _command_error(label: str, result: subprocess.CompletedProcess[str]) -> PublicationSchedulerError:
    details = (result.stderr or result.stdout or "no command output").strip().splitlines()
    return PublicationSchedulerError(
        f"{label} failed with exit code {result.returncode}: {details[-1] if details else 'no command output'}"
    )


def _normalize_status_path(path_text: str) -> str:
    text = path_text.strip().replace("\\", "/")
    if " -> " in text:
        text = text.split(" -> ", 1)[1].strip()
    return text[2:] if text.startswith("./") else text


def unexpected_dirty_paths(status_output: str, *, allow_care_line_runtime: bool) -> list[str]:
    unexpected: list[str] = []
    for raw_line in status_output.splitlines():
        line = raw_line.rstrip()
        if not line or len(line) < 3:
            continue
        status_code = line[:2]
        path = _normalize_status_path(line[3:])
        category = classify_care_line_runtime_path(path)
        allowed = (
            allow_care_line_runtime
            and status_code == "??"
            and category in CARE_LINE_ALLOWED_DIRTY_CATEGORIES
        )
        if path and not allowed:
            unexpected.append(path)
    return sorted(dict.fromkeys(unexpected))


def verify_repo(
    root: Path,
    *,
    branch: str,
    label: str,
    allow_care_line_runtime: bool,
) -> dict[str, Any]:
    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root)
    if status.returncode != 0:
        raise _command_error(f"{label} git status", status)
    unexpected = unexpected_dirty_paths(status.stdout or "", allow_care_line_runtime=allow_care_line_runtime)
    if unexpected:
        raise PublicationSchedulerError(f"{label} contains risky dirty paths: {', '.join(unexpected)}")

    current = _run(["git", "branch", "--show-current"], cwd=root)
    if current.returncode != 0:
        raise _command_error(f"{label} git branch", current)
    actual_branch = current.stdout.strip() or "<detached>"
    if actual_branch != branch:
        raise PublicationSchedulerError(f"{label} branch mismatch: expected {branch}, found {actual_branch}")

    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    remote = _run(["git", "rev-parse", f"origin/{branch}"], cwd=root)
    if head.returncode != 0:
        raise _command_error(f"{label} HEAD", head)
    if remote.returncode != 0:
        raise _command_error(f"{label} origin/{branch}", remote)
    head_sha = head.stdout.strip()
    remote_sha = remote.stdout.strip()
    if head_sha != remote_sha:
        raise PublicationSchedulerError(
            f"{label} is not at protected origin/{branch}: HEAD {head_sha}, origin {remote_sha}"
        )
    return {
        "root": str(root),
        "branch": actual_branch,
        "head": head_sha,
        "remote_head": remote_sha,
        "unexpected_dirty_paths": unexpected,
    }


def run_preflight(source_root: Path, pages_root: Path) -> None:
    command = [
        sys.executable,
        str(source_root / "scripts" / "preflight_repo_state.py"),
        "--source-repo",
        str(source_root),
        "--pages-repo",
        str(pages_root),
    ]
    result = _run(command, cwd=source_root)
    if result.returncode != 0:
        raise _command_error("protected repository preflight", result)


def _verify_protected_file(source_root: Path, path: Path) -> None:
    relative = path.resolve().relative_to(source_root.resolve()).as_posix()
    tracked = _run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=source_root)
    if tracked.returncode != 0:
        raise PublicationSchedulerError(f"approved release artifact is not protected at HEAD: {relative}")
    protected = _run(["git", "rev-parse", f"HEAD:{relative}"], cwd=source_root)
    working = _run(["git", "hash-object", relative], cwd=source_root)
    if protected.returncode != 0 or working.returncode != 0:
        raise PublicationSchedulerError(f"unable to hash protected approved release artifact: {relative}")
    if protected.stdout.strip() != working.stdout.strip():
        raise PublicationSchedulerError(f"approved release artifact differs from protected HEAD: {relative}")


def _verify_protected_bundle(source_root: Path, bundle: CareLineApprovedReleaseBundle) -> None:
    for path in (bundle.proposal_path, bundle.review_snapshot_path):
        _verify_protected_file(source_root, path)


def _surface_contains(path: Path, marker: str) -> bool:
    try:
        return marker in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _already_published(pages_root: Path, edition_date: str) -> bool:
    listable = public_edition_is_listable(pages_root, "care-line", edition_date)
    archive = _surface_contains(pages_root / "care-line" / "archive.html", f"editions/{edition_date}/")
    rss = _surface_contains(pages_root / "care-line" / "rss.xml", f"/care-line/editions/{edition_date}/")
    states = {listable, archive, rss}
    if len(states) > 1:
        raise PublicationSchedulerError(
            f"Care Line Pages state is partial for approved edition {edition_date}: "
            f"listable={listable}, archive={archive}, rss={rss}"
        )
    return listable


def discover_release_candidates(source_root: Path, pages_root: Path) -> list[ReleaseCandidate]:
    proposal_root = source_root / "data" / "dispatches" / "care-line" / "review" / "proposed-editions"
    review_root = source_root / "data" / "dispatches" / "care-line" / "review" / "signal-reviews"
    proposal_dates = {path.stem for path in proposal_root.glob("*.json")} if proposal_root.exists() else set()
    review_dates = {path.stem for path in review_root.glob("*.json")} if review_root.exists() else set()
    candidates: list[ReleaseCandidate] = []
    for edition_date in sorted(proposal_dates | review_dates):
        try:
            datetime.strptime(edition_date, "%Y-%m-%d")
        except ValueError as exc:
            raise PublicationSchedulerError(f"invalid Care Line approved release filename date: {edition_date}") from exc
        if edition_date not in proposal_dates or edition_date not in review_dates:
            raise PublicationSchedulerError(f"incomplete Care Line approved release artifact pair: {edition_date}")
        bundle = load_approved_release(source_root, edition_date)
        if bundle is None:
            raise PublicationSchedulerError(f"unable to load Care Line approved release: {edition_date}")
        if bundle.proposal.get("release_ready") is not True or bundle.review_snapshot.get("release_ready") is not True:
            raise PublicationSchedulerError(f"Care Line approved release is not explicitly release_ready: {edition_date}")
        _verify_protected_bundle(source_root, bundle)
        candidates.append(
            ReleaseCandidate(
                edition_date=edition_date,
                bundle=bundle,
                already_published=_already_published(pages_root, edition_date),
            )
        )
    return candidates


@dataclass
class SchedulerLock:
    path: Path
    stale_after: timedelta = timedelta(hours=3)
    acquired: bool = False
    stale_recovered: bool = False

    def acquire(self, *, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "started_at": utc_now(), "hostname": socket.gethostname()}
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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


def _paths(root: Path, run_date: str, run_id: str) -> tuple[Path, Path]:
    return (
        root / LOG_ROOT / run_date / f"{run_id}.log",
        root / RECEIPT_ROOT / run_date / f"{run_id}.json",
    )


def _tail(text: str, limit: int = 20) -> list[str]:
    return [line for line in text.splitlines() if line][-limit:]


def _write_log(path: Path, receipt: dict[str, Any], child: ChildExecution | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"started_at={receipt.get('started_at')}",
        f"completed_at={receipt.get('completed_at') or ''}",
        f"status={receipt.get('status')}",
        f"ok={receipt.get('ok')}",
        f"source_head={receipt.get('source_head_before') or ''}",
        f"pages_head_before={receipt.get('pages_head_before') or ''}",
        f"pages_head_after={receipt.get('pages_head_after') or ''}",
        f"edition_date={receipt.get('edition_date') or ''}",
        f"publication_attempted={receipt.get('publication_attempted')}",
        f"pages_changed={receipt.get('pages_changed')}",
        f"failure_stage={receipt.get('failure_stage') or ''}",
        f"error={receipt.get('error') or ''}",
    ]
    if child is not None:
        lines.extend(["", "[stdout]", child.stdout, "", "[stderr]", child.stderr])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _initial_receipt(
    root: Path,
    pages_root: Path,
    *,
    source_branch: str,
    pages_branch: str,
    run_date: str,
    run_id: str,
    log_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEDULER_SCHEMA,
        "run_id": run_id,
        "run_date": run_date,
        "status": "starting",
        "ok": False,
        "started_at": utc_now(),
        "completed_at": None,
        "principal": getpass.getuser(),
        "process_id": os.getpid(),
        "repo_root": str(root),
        "pages_repo": str(pages_root),
        "working_directory": str(root),
        "source_branch": source_branch,
        "pages_branch": pages_branch,
        "source_head_before": None,
        "source_head_after": None,
        "pages_head_before": None,
        "pages_head_after": None,
        "release_candidates": [],
        "already_published_releases": [],
        "edition_date": None,
        "approved_release_sha256": None,
        "review_snapshot_sha256": None,
        "release_ready": False,
        "no_op_reason": None,
        "publication_attempted": False,
        "publication_runner_status": None,
        "publication_runner_result": None,
        "child_process_id": None,
        "child_exit_code": None,
        "child_stdout_tail": [],
        "child_stderr_tail": [],
        "pages_changed": False,
        "source_changed": False,
        "failure_stage": None,
        "error": None,
        "receipt_path": str(receipt_path),
        "log_path": str(log_path),
        "unauthorized_side_effects": {
            "editorial_approval_creation": False,
            "queue_promotion": False,
            "source_collection": False,
            "audio": False,
            "social": False,
        },
    }


def run_publication_once(
    root: Path,
    pages_root: Path,
    *,
    source_branch: str,
    pages_branch: str,
    run_date: str,
    run_id: str | None,
) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    pages_root = pages_root.resolve()
    try:
        normalized_run_date = datetime.strptime(run_date, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise PublicationSchedulerError(f"invalid Pacific run date: {run_date}") from exc
    run_id = run_id or f"{stamp()}-{os.getpid()}"
    log_path, receipt_path = _paths(root, normalized_run_date, run_id)
    receipt = _initial_receipt(
        root,
        pages_root,
        source_branch=source_branch,
        pages_branch=pages_branch,
        run_date=normalized_run_date,
        run_id=run_id,
        log_path=log_path,
        receipt_path=receipt_path,
    )
    atomic_write_json(receipt_path, receipt)
    _write_log(log_path, receipt)
    lock = SchedulerLock(root / LOCK_PATH)
    child: ChildExecution | None = None
    stage = "lock"
    try:
        lock_status = lock.acquire()
        receipt["stale_lock_recovered"] = lock.stale_recovered
        if lock_status == "already_running":
            receipt.update({"ok": True, "status": "safe_no_op", "no_op_reason": "already_running", "completed_at": utc_now()})
            atomic_write_json(receipt_path, receipt)
            _write_log(log_path, receipt)
            return 0, receipt

        stage = "source_state"
        source_before = verify_repo(
            root,
            branch=source_branch,
            label="source repo",
            allow_care_line_runtime=True,
        )
        receipt["source_head_before"] = source_before["head"]
        stage = "pages_state"
        pages_before = verify_repo(
            pages_root,
            branch=pages_branch,
            label="Pages repo",
            allow_care_line_runtime=False,
        )
        receipt["pages_head_before"] = pages_before["head"]
        atomic_write_json(receipt_path, receipt)

        stage = "preflight"
        run_preflight(root, pages_root)
        stage = "approved_release_check"
        candidates = discover_release_candidates(root, pages_root)
        pending = [candidate for candidate in candidates if not candidate.already_published]
        receipt["release_candidates"] = [candidate.edition_date for candidate in pending]
        receipt["already_published_releases"] = [
            candidate.edition_date for candidate in candidates if candidate.already_published
        ]
        if not pending:
            receipt.update(
                {
                    "ok": True,
                    "status": "safe_no_op",
                    "no_op_reason": "already_published" if candidates else "no_approved_release",
                    "publication_runner_status": "safe_no_op",
                    "source_head_after": source_before["head"],
                    "pages_head_after": pages_before["head"],
                    "completed_at": utc_now(),
                }
            )
            atomic_write_json(receipt_path, receipt)
            _write_log(log_path, receipt)
            return 0, receipt
        if len(pending) != 1:
            raise PublicationSchedulerError(
                "multiple protected unpublished Care Line approved releases require operator sequencing: "
                + ", ".join(candidate.edition_date for candidate in pending)
            )

        candidate = pending[0]
        receipt.update(
            {
                "edition_date": candidate.edition_date,
                "approved_release_sha256": candidate.bundle.proposal_sha256,
                "review_snapshot_sha256": candidate.bundle.review_snapshot_sha256,
                "release_ready": True,
            }
        )
        command = [
            sys.executable,
            str(root / "scripts" / "run_care_line_publication_runner.py"),
            "--repo-root",
            str(root),
            "--pages-repo",
            str(pages_root),
            "--source-branch",
            source_branch,
            "--pages-branch",
            pages_branch,
            "--date",
            candidate.edition_date,
            "--publish",
            "--push",
            "--isolated-source",
        ]
        receipt["publication_attempted"] = True
        receipt["publication_runner_command"] = command
        atomic_write_json(receipt_path, receipt)
        stage = "publication_runner"
        child = _run_child(command, cwd=root)
        receipt.update(
            {
                "child_process_id": child.pid,
                "child_exit_code": child.returncode,
                "child_stdout_tail": _tail(child.stdout),
                "child_stderr_tail": _tail(child.stderr),
            }
        )
        try:
            runner_result = json.loads(child.stdout)
        except json.JSONDecodeError as exc:
            raise PublicationSchedulerError(f"publication runner returned invalid JSON: {exc}") from exc
        if not isinstance(runner_result, dict):
            raise PublicationSchedulerError("publication runner returned a non-object JSON result")
        receipt["publication_runner_result"] = runner_result
        receipt["publication_runner_status"] = runner_result.get("status")
        if child.returncode != 0 or runner_result.get("ok") is not True:
            raise PublicationSchedulerError(f"publication runner failed with exit code {child.returncode}")
        if (
            runner_result.get("status") != "publication_success"
            or runner_result.get("edition_date") != candidate.edition_date
            or runner_result.get("publication_attempted") is not True
            or runner_result.get("pushed") is not True
            or (runner_result.get("bluesky_result") or {}).get("requested") is not False
        ):
            raise PublicationSchedulerError("publication runner result violated the scheduled publication contract")

        stage = "post_publication_state"
        source_after = verify_repo(
            root,
            branch=source_branch,
            label="source repo",
            allow_care_line_runtime=True,
        )
        pages_after = verify_repo(
            pages_root,
            branch=pages_branch,
            label="Pages repo",
            allow_care_line_runtime=False,
        )
        receipt.update(
            {
                "source_head_after": source_after["head"],
                "pages_head_after": pages_after["head"],
                "source_changed": source_after["head"] != source_before["head"],
                "pages_changed": pages_after["head"] != pages_before["head"],
            }
        )
        if receipt["source_changed"]:
            raise PublicationSchedulerError("scheduled publication changed the protected source HEAD")
        if not receipt["pages_changed"]:
            raise PublicationSchedulerError("publication runner reported success without advancing Pages")
        if not _already_published(pages_root, candidate.edition_date):
            raise PublicationSchedulerError("published Care Line edition failed final Pages verification")
        receipt.update({"ok": True, "status": "publication_success", "completed_at": utc_now()})
        atomic_write_json(receipt_path, receipt)
        _write_log(log_path, receipt, child)
        return 0, receipt
    except Exception as exc:  # noqa: BLE001
        receipt.update(
            {
                "ok": False,
                "status": "failure",
                "completed_at": utc_now(),
                "failure_stage": stage,
                "error": str(exc),
            }
        )
        atomic_write_json(receipt_path, receipt)
        _write_log(log_path, receipt, child)
        return 1, receipt
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one approval-gated Care Line publication scheduler cycle.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pages-repo", required=True)
    parser.add_argument("--source-branch", default=PRODUCTION_BRANCH)
    parser.add_argument("--pages-branch", default=PAGES_BRANCH)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--run-id", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        exit_code, receipt = run_publication_once(
            Path(args.repo_root),
            Path(args.pages_repo),
            source_branch=args.source_branch,
            pages_branch=args.pages_branch,
            run_date=args.run_date,
            run_id=args.run_id,
        )
    except Exception as exc:  # only argument/date failures before receipt path resolution
        receipt = {"schema_version": SCHEDULER_SCHEMA, "ok": False, "status": "failure", "error": str(exc)}
        exit_code = 1
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
