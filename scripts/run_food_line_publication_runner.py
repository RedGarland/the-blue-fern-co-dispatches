from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_approved_proposal import ApprovedProposalBundle, load_approved_proposal
from bluefern_dispatches.pages_release_safety import sync_pages_from_source
from scripts.food_line_runtime_paths import FOOD_LINE_ALLOWED_DIRTY_CATEGORIES, classify_food_line_runtime_path
from scripts.run_food_line_dispatch import run_food_line_dispatch
from scripts.validate_publish_scope import validate_publish_scope


DISPATCH = "food-line"
RELEASE_READINESS_DIR = "data/dispatches/food-line/review/release-readiness"
PROPOSED_EDITION_DIR = "data/dispatches/food-line/review/proposed-editions"
APPROVED_STATUS = "approved_current_review_ready_for_source_generation"


class PublicationRunnerError(RuntimeError):
    pass


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
    sys.stdout.write(text)


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False, encoding="utf-8")


def _source_repo_dirty_path_is_allowed(status: str, path: str) -> bool:
    if status != "??":
        return False
    category = classify_food_line_runtime_path(path)
    return category in FOOD_LINE_ALLOWED_DIRTY_CATEGORIES


def _repo_state(repo_root: Path, *, required_branch: str, label: str) -> dict[str, Any]:
    status = _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        raise PublicationRunnerError(status.stderr or status.stdout or "git status failed")
    dirty_paths: list[str] = []
    for raw_line in status.stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        normalized = path.replace("\\", "/").lstrip("./")
        if label == "source repo" and _source_repo_dirty_path_is_allowed(line[:2], normalized):
            continue
        dirty_paths.append(normalized)
    branch = _run_git(repo_root, "branch", "--show-current")
    head = _run_git(repo_root, "rev-parse", "HEAD")
    if branch.returncode != 0 or head.returncode != 0:
        raise PublicationRunnerError(f"unable to inspect {label}")
    branch_name = branch.stdout.strip() or "<detached>"
    if branch_name != required_branch:
        raise PublicationRunnerError(f"{label} branch mismatch: expected {required_branch}, found {branch_name}")
    if dirty_paths:
        raise PublicationRunnerError(f"{label} must be clean before publication")
    return {"root": str(repo_root), "branch": branch_name, "head": head.stdout.strip(), "clean": True, "dirty_paths": []}


def _clone_repo(source_repo: Path, clone_repo: Path, *, branch: str) -> None:
    if clone_repo.exists():
        shutil.rmtree(clone_repo)
    clone_repo.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "clone", "--branch", branch, "--single-branch", str(source_repo), str(clone_repo)], capture_output=True, text=True, check=False, encoding="utf-8")
    if result.returncode != 0:
        raise PublicationRunnerError(result.stderr or result.stdout or "git clone failed")


def _approved_proposal_path(root: Path, edition_date: str) -> Path:
    return root / PROPOSED_EDITION_DIR / f"{edition_date}.json"


def _release_readiness_path(root: Path, edition_date: str) -> Path:
    return root / RELEASE_READINESS_DIR / f"{edition_date}.json"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
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
    return {"path": readiness_path, "payload": payload}


def _validated_private_release_inputs(bundle: ApprovedProposalBundle, readiness_path: Path) -> tuple[Path, ...]:
    return (bundle.proposal_path, bundle.queue_path, readiness_path)


def _copy_validated_private_release_inputs_for_dry_run(*, repo_root: Path, working_source: Path, bundle: ApprovedProposalBundle, readiness_path: Path) -> list[str]:
    copied: list[str] = []
    validated_paths = _validated_private_release_inputs(bundle, readiness_path)
    for source_path in validated_paths:
        resolved_source = source_path.resolve()
        try:
            resolved_source.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise PublicationRunnerError(f"validated private release input is outside the source repository: {source_path}") from exc
        if not resolved_source.exists():
            raise PublicationRunnerError(f"validated private release input is missing: {resolved_source}")
        destination = working_source / resolved_source.relative_to(repo_root.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved_source, destination)
        copied.append(destination.relative_to(working_source).as_posix())
    return copied


def _validate_scope(*, root: Path, pages_repo: Path, edition_date: str, source_branch: str, release_manifest: Path) -> list[str]:
    return validate_publish_scope(
        dispatch=DISPATCH,
        date_text=edition_date,
        source_repo_root=root,
        pages_repo_root=pages_repo,
        allow_pages=True,
        strict=True,
        release_manifest_path=release_manifest,
        source_changed_paths=[],
        pages_changed_paths=[],
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

    for attempt in range(5):
        try:
            shutil.rmtree(temp_root, onerror=_retry_remove_readonly)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
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
    post_bluesky: bool = False,
) -> dict[str, Any]:
    if check_only and dry_run_full:
        raise PublicationRunnerError("-CheckOnly and -DryRunFull cannot be combined.")
    if check_only and push:
        raise PublicationRunnerError("-Push cannot be combined with -CheckOnly.")
    if check_only and post_bluesky:
        raise PublicationRunnerError("-PostBluesky cannot be combined with -CheckOnly.")
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
        "bluesky_result": {"requested": bool(post_bluesky), "status": "skipped" if not post_bluesky else "blocked", "reason": "not_requested" if not post_bluesky else "publication_not_run", "post_uri": None, "post_cid": None},
        "scope_validation_errors": [],
        "errors": [],
        "copied_private_inputs": [],
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
    if dry_run_full:
        temp_root = Path(tempfile.mkdtemp(prefix="food-line-publication-"))
        working_source = temp_root / "source"
        working_pages = temp_root / "pages"
        try:
            _clone_repo(repo_root, working_source, branch=source_branch)
            _clone_repo(pages_repo, working_pages, branch=pages_branch)
            try:
                copied_private_inputs = _copy_validated_private_release_inputs_for_dry_run(
                    repo_root=repo_root,
                    working_source=working_source,
                    bundle=bundle,
                    readiness_path=readiness["path"],
                )
            except Exception as exc:  # noqa: BLE001
                base_result.update(
                    {
                        "ok": False,
                        "status": "dry_run_full_failed",
                        "errors": [str(exc)],
                        "temp_workspace": str(temp_root),
                        "temp_workspace_removed": False,
                        "copied_private_inputs": [],
                        "bluesky_result": {
                            "requested": bool(post_bluesky),
                            "status": "blocked",
                            "reason": "publication_failed",
                            "post_uri": None,
                            "post_cid": None,
                        },
                    }
                )
                return base_result
            generated = run_food_line_dispatch(
                working_source,
                edition_date,
                generate_audio=False,
                approved_proposal_path=bundle.proposal_path.relative_to(repo_root).as_posix(),
            )
            release_manifest = Path(generated["release_manifest_path"]).resolve()
            scope_errors = _validate_scope(root=working_source, pages_repo=working_pages, edition_date=edition_date, source_branch=source_branch, release_manifest=release_manifest)
            if not bool(generated.get("ok")) or scope_errors:
                base_result.update({"ok": False, "status": "dry_run_full_failed", "source_commit": generated.get("generator_source_commit") or source_state["head"], "scope_validation_errors": list(scope_errors), "errors": list(generated.get("errors") or []) + list(scope_errors), "temp_workspace": str(temp_root), "temp_workspace_removed": False, "push_performed": False, "bluesky_result": {"requested": bool(post_bluesky), "status": "blocked", "reason": "publication_failed", "post_uri": None, "post_cid": None}, "copied_private_inputs": copied_private_inputs})
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
            base_result.update({"ok": dry_run_ok, "status": "dry_run_full_success" if dry_run_ok else "dry_run_full_failed", "source_commit": generated.get("generator_source_commit") or source_state["head"], "publication_report": publication_report, "proposed_modified_paths": list(publication_report.get("modifications") or []) + list(publication_report.get("additions") or []), "proposed_deleted_paths": list(publication_report.get("deletions") or []), "scope_validation_errors": list(scope_errors), "errors": list(generated.get("errors") or []) + list(scope_errors) + list(publication_report.get("errors") or []), "temp_workspace": str(temp_root), "temp_workspace_removed": False, "push_performed": False, "bluesky_result": {"requested": bool(post_bluesky), "status": "blocked", "reason": "dry_run_full", "post_uri": None, "post_cid": None}, "copied_private_inputs": copied_private_inputs})
            return base_result
        finally:
            base_result["temp_workspace_removed"] = _clean_temp_workspace(temp_root)

    raise PublicationRunnerError("publication mode not implemented in this test-focused fix")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the dedicated Food Line publication runner.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pages-repo", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--pages-branch", required=True)
    parser.add_argument("--date")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run-full", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--post-bluesky", action="store_true")
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
            post_bluesky=bool(args.post_bluesky),
        )
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "status": "publication_failed", "mode": "check_only" if args.check_only else ("dry_run_full" if args.dry_run_full else "publication"), "dispatch": DISPATCH, "errors": [str(exc)]}
    _json_write(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
