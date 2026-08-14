from __future__ import annotations

import argparse
import os
import json
import stat
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.food_line_runtime_paths import FOOD_LINE_ALLOWED_DIRTY_CATEGORIES, classify_food_line_runtime_path
from bluefern_dispatches.food_line_approved_proposal import load_approved_proposal
from bluefern_dispatches.pages_release_safety import sync_pages_from_source
from scripts.run_food_line_dispatch import run_food_line_dispatch
from scripts.validate_publish_scope import validate_publish_scope


DISPATCH = "food-line"
RELEASE_READINESS_DIR = "data/dispatches/food-line/review/release-readiness"
PROPOSED_EDITION_DIR = "data/dispatches/food-line/review/proposed-editions"
APPROVED_STATUS = "approved_current_review_ready_for_source_generation"
ALLOWED_LOCAL_DIR_PREFIXES = (
    "logs/",
    ".venv/",
    ".pytest_cache/",
    "bluefern-dispatches-pages/",
)


class PublicationRunnerError(RuntimeError):
    """Fail-closed Food Line publication runner error."""


def _utc_date_text() -> str:
    return datetime.now().astimezone().date().isoformat()


def _configure_git_safe_directories(*repo_roots: Path) -> None:
    safe_directories: list[str] = []
    for repo_root in repo_roots:
        resolved_root = repo_root.resolve()
        for candidate in (resolved_root, resolved_root / ".git"):
            safe_value = str(candidate).replace("\\", "/")
            if safe_value not in safe_directories:
                safe_directories.append(safe_value)
    os.environ["GIT_CONFIG_COUNT"] = str(len(safe_directories))
    for index, repo_root in enumerate(safe_directories):
        os.environ[f"GIT_CONFIG_KEY_{index}"] = "safe.directory"
        os.environ[f"GIT_CONFIG_VALUE_{index}"] = repo_root


def _json_write(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def _source_repo_dirty_path_is_allowed(status: str, path: str) -> bool:
    if status != "??":
        return False
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized.startswith(ALLOWED_LOCAL_DIR_PREFIXES):
        return True
    category = classify_food_line_runtime_path(normalized)
    return category in FOOD_LINE_ALLOWED_DIRTY_CATEGORIES


def _repo_state(repo_root: Path, *, required_branch: str, label: str) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise PublicationRunnerError(f"{label} does not exist: {repo_root}")
    status = _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        raise PublicationRunnerError(f"unable to inspect {label} state: {status.stderr or status.stdout or 'git status failed'}")
    dirty_paths: list[str] = []
    for raw_line in status.stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        status_code = line[:2]
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if label == "source repo" and _source_repo_dirty_path_is_allowed(status_code, path):
            continue
        dirty_paths.append(path.replace("\\", "/").lstrip("./"))
    clean = not dirty_paths
    branch = _run_git(repo_root, "branch", "--show-current")
    if branch.returncode != 0:
        raise PublicationRunnerError(f"unable to inspect {label} branch: {branch.stderr or branch.stdout or 'git branch failed'}")
    branch_name = branch.stdout.strip() or "<detached>"
    head = _run_git(repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise PublicationRunnerError(f"unable to inspect {label} HEAD: {head.stderr or head.stdout or 'git rev-parse failed'}")
    if branch_name != required_branch:
        raise PublicationRunnerError(f"{label} branch mismatch: expected {required_branch}, found {branch_name}")
    if not clean:
        raise PublicationRunnerError(f"{label} must be clean before publication")
    return {
        "root": str(repo_root),
        "branch": branch_name,
        "head": head.stdout.strip(),
        "clean": clean,
        "dirty_paths": dirty_paths,
    }


def _clone_repo(source_repo: Path, clone_repo: Path, *, branch: str) -> None:
    if clone_repo.exists():
        shutil.rmtree(clone_repo)
    clone_repo.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "git",
            "clone",
            "--branch",
            branch,
            "--single-branch",
            str(source_repo),
            str(clone_repo),
        ],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise PublicationRunnerError(result.stderr or result.stdout or "git clone failed")


def _approved_proposal_path(root: Path, edition_date: str) -> Path:
    return root / PROPOSED_EDITION_DIR / f"{edition_date}.json"


def _release_readiness_path(root: Path, edition_date: str) -> Path:
    return root / RELEASE_READINESS_DIR / f"{edition_date}.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicationRunnerError(f"unable to read JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PublicationRunnerError(f"JSON file must contain an object: {path}")
    return payload


def _load_release_readiness(root: Path, edition_date: str, approved_proposal_path: Path) -> dict[str, Any]:
    readiness_path = _release_readiness_path(root, edition_date)
    payload = _read_json(readiness_path)
    if payload.get("schema_version") != "food_line_release_readiness_v1":
        raise PublicationRunnerError("release readiness schema mismatch")
    if str(payload.get("approved_proposal_path") or "").replace("\\", "/") != approved_proposal_path.relative_to(root).as_posix():
        raise PublicationRunnerError("release readiness approved proposal path does not match the requested edition")
    if payload.get("edition_date") != edition_date:
        raise PublicationRunnerError("release readiness edition date does not match the requested edition")
    if payload.get("status") != APPROVED_STATUS:
        raise PublicationRunnerError(f"release readiness status must be {APPROVED_STATUS}")
    return {
        "path": readiness_path,
        "payload": payload,
    }


def _validate_scope(
    *,
    root: Path,
    pages_repo: Path,
    edition_date: str,
    source_branch: str,
    release_manifest: Path,
) -> list[str]:
    return validate_publish_scope(
        dispatch=DISPATCH,
        date_text=edition_date,
        source_repo_root=root,
        pages_repo_root=pages_repo,
        allow_pages=True,
        strict=True,
        release_manifest_path=release_manifest,
        required_source_ref=source_branch,
        release_manifest_commit="HEAD",
    )


def _clean_temp_workspace(temp_root: Path) -> bool:
    def _retry_remove_readonly(func: Any, path: str, exc_info: Any) -> None:
        exception = exc_info[1]
        if isinstance(exception, PermissionError):
            try:
                os.chmod(path, stat.S_IWRITE)
            except OSError:
                pass
            try:
                func(path)
                return
            except OSError:
                pass
        raise exception

    last_error: Exception | None = None
    for attempt in range(5):
        try:
            shutil.rmtree(temp_root, onerror=_retry_remove_readonly)
            return True
        except FileNotFoundError:
            return True
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return False


def run_publication(
    *,
    repo_root: Path,
    pages_repo: Path,
    source_branch: str,
    pages_branch: str,
    date: str | None = None,
    check_only: bool = False,
    dry_run_full: bool = False,
    push: bool = False,
) -> dict[str, Any]:
    if check_only and dry_run_full:
        raise PublicationRunnerError("-CheckOnly and -DryRunFull cannot be combined.")
    if check_only and push:
        raise PublicationRunnerError("-Push cannot be combined with -CheckOnly.")
    if dry_run_full and push:
        raise PublicationRunnerError("-Push cannot be combined with -DryRunFull.")

    edition_date = date or _utc_date_text()
    repo_root = repo_root.resolve()
    pages_repo = pages_repo.resolve()
    _configure_git_safe_directories(repo_root, pages_repo)
    approved_proposal_path = _approved_proposal_path(repo_root, edition_date)
    readiness = _load_release_readiness(repo_root, edition_date, approved_proposal_path)

    source_state = _repo_state(repo_root, required_branch=source_branch, label="source repo")
    pages_state = _repo_state(pages_repo, required_branch=pages_branch, label="Pages repo")

    bundle = load_approved_proposal(repo_root, approved_proposal_path, edition_date)
    base_result: dict[str, Any] = {
        "ok": True,
        "status": "check_only_success" if check_only else ("dry_run_full_success" if dry_run_full else "publication_success"),
        "mode": "check_only" if check_only else ("dry_run_full" if dry_run_full else "publication"),
        "dispatch": DISPATCH,
        "edition_date": edition_date,
        "repo_root": str(repo_root),
        "pages_repo": str(pages_repo),
        "source_branch": source_branch,
        "pages_branch": pages_branch,
        "source_commit": source_state["head"],
        "pages_commit": pages_state["head"],
        "approved_proposal_path": str(approved_proposal_path),
        "approved_proposal_sha256": bundle.proposal_sha256,
        "release_readiness_path": str(readiness["path"]),
        "release_readiness_status": readiness["payload"].get("status"),
        "proposed_modified_paths": [],
        "proposed_deleted_paths": [],
        "temp_workspace": None,
        "temp_workspace_removed": False,
        "push_performed": False,
        "publication_report": None,
        "scope_validation_errors": [],
        "errors": [],
    }

    if check_only:
        scope_errors = validate_publish_scope(
            dispatch=DISPATCH,
            date_text=edition_date,
            source_repo_root=repo_root,
            pages_repo_root=pages_repo,
            allow_pages=True,
            strict=True,
            source_changed_paths=[],
            pages_changed_paths=[],
        )
        if scope_errors:
            base_result["ok"] = False
            base_result["status"] = "check_only_failed"
            base_result["scope_validation_errors"] = list(scope_errors)
            base_result["errors"] = list(scope_errors)
            return base_result
        return base_result

    temp_root: Path | None = None
    working_source = repo_root
    working_pages = pages_repo
    if dry_run_full:
        temp_root = Path(tempfile.mkdtemp(prefix="food-line-publication-"))
        working_source = temp_root / "source"
        working_pages = temp_root / "pages"
        try:
            _clone_repo(repo_root, working_source, branch=source_branch)
            _clone_repo(pages_repo, working_pages, branch=pages_branch)
            dry_run_ok = False
            publication_report: dict[str, Any] = {}
            generated = run_food_line_dispatch(
                working_source,
                edition_date,
                generate_audio=False,
                approved_proposal_path=approved_proposal_path.relative_to(repo_root).as_posix(),
            )
            release_manifest = Path(generated["release_manifest_path"]).resolve()
            scope_errors = _validate_scope(
                root=working_source,
                pages_repo=working_pages,
                edition_date=edition_date,
                source_branch=source_branch,
                release_manifest=release_manifest,
            )
            if not bool(generated.get("ok")) or scope_errors:
                base_result.update(
                    {
                        "ok": False,
                        "status": "dry_run_full_failed",
                        "source_commit": generated.get("generator_source_commit") or source_state["head"],
                        "scope_validation_errors": list(scope_errors),
                        "errors": list(generated.get("errors") or []) + list(scope_errors),
                        "temp_workspace": str(temp_root),
                        "temp_workspace_removed": False,
                        "push_performed": False,
                    }
                )
                return base_result

            publication_report = sync_pages_from_source(
                dispatch=DISPATCH,
                dates=[edition_date],
                require_source_branch=source_branch,
                pages_branch=pages_branch,
                source_repo=working_source,
                pages_repo=working_pages,
                dry_run=True,
                release_manifest=release_manifest,
            )
            dry_run_ok = bool(publication_report.get("ok"))
            base_result.update(
                {
                    "ok": dry_run_ok,
                    "status": "dry_run_full_success" if dry_run_ok else "dry_run_full_failed",
                    "source_commit": generated.get("generator_source_commit") or source_state["head"],
                    "publication_report": publication_report,
                    "proposed_modified_paths": list(publication_report.get("modifications") or [])
                    + list(publication_report.get("additions") or []),
                    "proposed_deleted_paths": list(publication_report.get("deletions") or []),
                    "scope_validation_errors": list(scope_errors),
                    "errors": list(generated.get("errors") or []) + list(scope_errors) + list(publication_report.get("errors") or []),
                    "temp_workspace": str(temp_root),
                    "temp_workspace_removed": False,
                    "push_performed": False,
                }
            )
            return base_result
        finally:
            base_result["temp_workspace_removed"] = _clean_temp_workspace(temp_root)

    generated = run_food_line_dispatch(
        working_source,
        edition_date,
        generate_audio=False,
        approved_proposal_path=approved_proposal_path.relative_to(repo_root).as_posix(),
    )
    release_manifest = Path(generated["release_manifest_path"]).resolve()
    scope_errors = _validate_scope(
        root=working_source,
        pages_repo=working_pages,
        edition_date=edition_date,
        source_branch=source_branch,
        release_manifest=release_manifest,
    )
    if not bool(generated.get("ok")) or scope_errors:
        base_result.update(
            {
                "ok": False,
                "status": "publication_failed",
                "source_commit": generated.get("generator_source_commit") or source_state["head"],
                "scope_validation_errors": list(scope_errors),
                "errors": list(generated.get("errors") or []) + list(scope_errors),
            }
        )
        return base_result
    publication_report = sync_pages_from_source(
        dispatch=DISPATCH,
        dates=[edition_date],
        require_source_branch=source_branch,
        pages_branch=pages_branch,
        source_repo=working_source,
        pages_repo=working_pages,
        commit=True,
        push=push,
        release_manifest=release_manifest,
    )
    ok = bool(generated.get("ok")) and not scope_errors and bool(publication_report.get("ok"))
    base_result.update(
        {
            "ok": ok,
            "status": "publication_success" if ok else "publication_failed",
            "source_commit": generated.get("generator_source_commit") or source_state["head"],
            "publication_report": publication_report,
            "proposed_modified_paths": list(publication_report.get("modifications") or [])
            + list(publication_report.get("additions") or []),
            "proposed_deleted_paths": list(publication_report.get("deletions") or []),
            "scope_validation_errors": list(scope_errors),
            "errors": list(generated.get("errors") or []) + list(scope_errors) + list(publication_report.get("errors") or []),
            "push_performed": bool(publication_report.get("pushed")),
        }
    )
    return base_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the dedicated Food Line publication runner.")
    parser.add_argument("--repo-root", required=True, help="Source repository root.")
    parser.add_argument("--pages-repo", required=True, help="Local Pages repository root.")
    parser.add_argument("--source-branch", required=True, help="Expected source branch.")
    parser.add_argument("--pages-branch", required=True, help="Expected Pages branch.")
    parser.add_argument("--date", help="Food Line edition date in YYYY-MM-DD format. Defaults to the current Pacific date.")
    parser.add_argument("--check-only", action="store_true", help="Validate readiness without generating or publishing.")
    parser.add_argument("--dry-run-full", action="store_true", help="Run the full publication flow in an isolated temp workspace.")
    parser.add_argument("--push", action="store_true", help="Push the local Pages commit after a successful publication run.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        result = run_publication(
            repo_root=Path(args.repo_root),
            pages_repo=Path(args.pages_repo),
            source_branch=str(args.source_branch),
            pages_branch=str(args.pages_branch),
            date=str(args.date).strip() if args.date else None,
            check_only=bool(args.check_only),
            dry_run_full=bool(args.dry_run_full),
            push=bool(args.push),
        )
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "status": "publication_failed",
            "mode": "check_only" if args.check_only else ("dry_run_full" if args.dry_run_full else "publication"),
            "dispatch": DISPATCH,
            "errors": [str(exc)],
        }
    _json_write(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
