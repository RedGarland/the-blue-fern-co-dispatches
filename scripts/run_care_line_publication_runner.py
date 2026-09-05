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

from bluefern_dispatches.care_line_bluesky import maybe_post_care_line_dispatch_to_bluesky
from bluefern_dispatches.care_line_release_render import load_approved_release
from bluefern_dispatches.generator import BASE_URL, build_site, publish_pages
from scripts.care_line_runtime_paths import CARE_LINE_ALLOWED_DIRTY_CATEGORIES, classify_care_line_runtime_path

DISPATCH = "care-line"
PAGES_REPO = ROOT / "bluefern-dispatches-pages"
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


def _repo_state(
    repo_root: Path,
    *,
    required_branch: str,
    label: str,
    allow_care_line_runtime: bool = False,
) -> dict[str, Any]:
    status = _run_git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        raise PublicationRunnerError(status.stderr or status.stdout or "git status failed")
    dirty_paths: list[str] = []
    unexpected_dirty_paths: list[str] = []
    for raw_line in status.stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        normalized_path = path.replace("\\", "/").lstrip("./")
        dirty_paths.append(normalized_path)
        status_code = line[:2]
        category = classify_care_line_runtime_path(normalized_path)
        if not (
            allow_care_line_runtime
            and status_code == "??"
            and category in CARE_LINE_ALLOWED_DIRTY_CATEGORIES
        ):
            unexpected_dirty_paths.append(normalized_path)
    branch = _run_git(repo_root, "branch", "--show-current")
    head = _run_git(repo_root, "rev-parse", "HEAD")
    if branch.returncode != 0 or head.returncode != 0:
        raise PublicationRunnerError(f"unable to inspect {label}")
    branch_name = branch.stdout.strip() or "<detached>"
    if branch_name != required_branch:
        raise PublicationRunnerError(f"{label} branch mismatch: expected {required_branch}, found {branch_name}")
    if unexpected_dirty_paths:
        raise PublicationRunnerError(
            f"{label} contains risky dirty paths before publication: {', '.join(unexpected_dirty_paths)}"
        )
    return {
        "root": str(repo_root),
        "branch": branch_name,
        "head": head.stdout.strip(),
        "clean": not dirty_paths,
        "dirty_paths": dirty_paths,
        "unexpected_dirty_paths": [],
    }


def _clone_repo(source_repo: Path, clone_repo: Path, *, branch: str) -> None:
    if clone_repo.exists():
        shutil.rmtree(clone_repo)
    clone_repo.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "clone", "--branch", branch, "--single-branch", str(source_repo), str(clone_repo)], capture_output=True, text=True, check=False, encoding="utf-8")
    if result.returncode != 0:
        raise PublicationRunnerError(result.stderr or result.stdout or "git clone failed")


def _approved_release(edition_date: str, repo_root: Path):
    return load_approved_release(repo_root, edition_date)


def _run_publish_flow(
    *,
    repo_root: Path,
    pages_repo: Path,
    source_branch: str,
    pages_branch: str,
    edition_date: str,
    check_only: bool,
    dry_run_full: bool,
    publish: bool,
    push: bool,
    post_bluesky: bool,
    isolated_source: bool = False,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    pages_repo = pages_repo.resolve()
    _configure_git_safe_directories(repo_root, pages_repo)
    approved_release = _approved_release(edition_date, repo_root)
    source_state = _repo_state(
        repo_root,
        required_branch=source_branch,
        label="source repo",
        allow_care_line_runtime=True,
    )
    pages_state = _repo_state(pages_repo, required_branch=pages_branch, label="Pages repo")
    if approved_release is None:
        return {
            "ok": False,
            "status": "check_only_no_release_candidate" if check_only else "release_not_ready",
            "mode": "check_only" if check_only else ("dry_run_full" if dry_run_full else "publication"),
            "dispatch": DISPATCH,
            "edition_date": edition_date,
            "repo_root": str(repo_root),
            "pages_repo": str(pages_repo),
            "source_branch": source_branch,
            "pages_branch": pages_branch,
            "source_commit": source_state["head"],
            "pages_commit": pages_state["head"],
            "approved_release_path": None,
            "approved_release_sha256": None,
            "review_snapshot_path": None,
            "review_snapshot_sha256": None,
            "release_ready": False,
            "publication_attempted": False,
            "pages_publish_copied": False,
            "pushed": False,
            "bluesky_result": {"requested": bool(post_bluesky), "status": "skipped", "reason": "release_not_ready", "post_uri": None, "post_cid": None},
            "warnings": [],
            "errors": ["care-line approved release artifacts are missing"],
        }

    build_kwargs = {
        "dry_run": bool(check_only or dry_run_full),
        "only_dispatches": (DISPATCH,),
        "dispatch_seed_dates": {DISPATCH: edition_date},
        "pages_repo": pages_repo,
    }
    if check_only:
        build = build_site(repo_root, **build_kwargs)
        release_ready = bool(build.get("ok")) and bool(approved_release.approved_items)
        status = "check_only_ready" if release_ready else "check_only_failed"
        return {
            "ok": release_ready,
            "status": status,
            "mode": "check_only",
            "dispatch": DISPATCH,
            "edition_date": edition_date,
            "repo_root": str(repo_root),
            "pages_repo": str(pages_repo),
            "source_branch": source_branch,
            "pages_branch": pages_branch,
            "source_commit": source_state["head"],
            "pages_commit": pages_state["head"],
            "approved_release_path": str(approved_release.proposal_path),
            "approved_release_sha256": approved_release.proposal_sha256,
            "review_snapshot_path": str(approved_release.review_snapshot_path),
            "review_snapshot_sha256": approved_release.review_snapshot_sha256,
            "release_ready": release_ready,
            "publication_attempted": False,
            "pages_publish_copied": False,
            "pushed": False,
            "build": build,
            "bluesky_result": {"requested": bool(post_bluesky), "status": "skipped", "reason": "not_requested", "post_uri": None, "post_cid": None},
            "warnings": list(build.get("warnings") or []),
            "errors": list(build.get("errors") or []),
        }

    if dry_run_full:
        temp_root = Path(tempfile.mkdtemp(prefix="care-line-publication-"))
        try:
            temp_source = temp_root / "source"
            temp_pages = temp_root / "pages"
            _clone_repo(repo_root, temp_source, branch=source_branch)
            _clone_repo(pages_repo, temp_pages, branch=pages_branch)
            isolated_backup_root = temp_source / "output" / "tmp-backups-pages"
            build = build_site(
                temp_source,
                dry_run=False,
                backup_root=isolated_backup_root,
                only_dispatches=(DISPATCH,),
                dispatch_seed_dates={DISPATCH: edition_date},
                pages_repo=temp_pages,
            )
            publish = publish_pages(
                temp_source,
                temp_pages,
                None,
                dry_run=False,
                commit=False,
                no_push=True,
                backup_root=isolated_backup_root,
                only_dispatches=(DISPATCH,),
                shared_homepage_dispatch=DISPATCH,
                expect_date=edition_date,
                expect_dispatches=(DISPATCH,),
            )
            ok = bool(build.get("ok")) and bool(publish.get("ok"))
            return {
                "ok": ok,
                "status": "dry_run_full_success" if ok else "dry_run_full_failed",
                "mode": "dry_run_full",
                "dispatch": DISPATCH,
                "edition_date": edition_date,
                "repo_root": str(repo_root),
                "pages_repo": str(pages_repo),
                "source_branch": source_branch,
                "pages_branch": pages_branch,
                "source_commit": source_state["head"],
                "pages_commit": pages_state["head"],
                "approved_release_path": str(approved_release.proposal_path),
                "approved_release_sha256": approved_release.proposal_sha256,
                "review_snapshot_path": str(approved_release.review_snapshot_path),
                "review_snapshot_sha256": approved_release.review_snapshot_sha256,
                "release_ready": ok,
                "publication_attempted": False,
                "pages_publish_copied": False,
                "pushed": False,
                "build": build,
                "publish": publish,
                "bluesky_result": {"requested": bool(post_bluesky), "status": "blocked", "reason": "dry_run_full", "post_uri": None, "post_cid": None},
                "warnings": list(build.get("warnings") or []) + list(publish.get("warnings") or []),
                "errors": list(build.get("errors") or []) + list(publish.get("errors") or []),
            }
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    live_temp_root: Path | None = None
    live_repo_root = repo_root
    try:
        if isolated_source:
            live_temp_root = Path(tempfile.mkdtemp(prefix="care-line-scheduled-publication-"))
            live_repo_root = live_temp_root / "source"
            _clone_repo(repo_root, live_repo_root, branch=source_branch)
            isolated_release = _approved_release(edition_date, live_repo_root)
            if isolated_release is None:
                raise PublicationRunnerError("protected approved release is absent from isolated source checkout")
            if (
                isolated_release.proposal_sha256 != approved_release.proposal_sha256
                or isolated_release.review_snapshot_sha256 != approved_release.review_snapshot_sha256
            ):
                raise PublicationRunnerError("isolated approved release hashes do not match the source checkout")

        build = build_site(
            live_repo_root,
            dry_run=False,
            only_dispatches=(DISPATCH,),
            dispatch_seed_dates={DISPATCH: edition_date},
            pages_repo=pages_repo,
        )
        if not bool(build.get("ok")):
            return {
                "ok": False,
                "status": "publication_failed",
                "mode": "publication",
                "dispatch": DISPATCH,
                "edition_date": edition_date,
                "repo_root": str(repo_root),
                "pages_repo": str(pages_repo),
                "source_branch": source_branch,
                "pages_branch": pages_branch,
                "source_commit": source_state["head"],
                "pages_commit": pages_state["head"],
                "approved_release_path": str(approved_release.proposal_path),
                "approved_release_sha256": approved_release.proposal_sha256,
                "review_snapshot_path": str(approved_release.review_snapshot_path),
                "review_snapshot_sha256": approved_release.review_snapshot_sha256,
                "release_ready": False,
                "publication_attempted": False,
                "pages_publish_copied": False,
                "pushed": False,
                "isolated_source": bool(isolated_source),
                "build": build,
                "bluesky_result": {"requested": bool(post_bluesky), "status": "blocked", "reason": "publication_failed", "post_uri": None, "post_cid": None},
                "warnings": list(build.get("warnings") or []),
                "errors": list(build.get("errors") or []),
            }

        publish_result = publish_pages(
            live_repo_root,
            pages_repo,
            None,
            dry_run=False,
            commit=True,
            no_push=not push,
            only_dispatches=(DISPATCH,),
            shared_homepage_dispatch=DISPATCH,
            expect_date=edition_date,
            expect_dispatches=(DISPATCH,),
        )
        ok = bool(publish_result.get("ok"))
        bluesky_result: dict[str, Any] = {"requested": bool(post_bluesky), "status": "skipped", "reason": "not_requested", "post_uri": None, "post_cid": None}
        if post_bluesky and ok:
            bluesky_result = maybe_post_care_line_dispatch_to_bluesky(
                edition_date=edition_date,
                public_url=(build.get("public_url") or publish_result.get("build", {}).get("public_url") or f"https://dispatches.thebluefernco.com/care-line/editions/{edition_date}/"),
                post_text=(build.get("bluesky_post_text") or publish_result.get("build", {}).get("bluesky_post_text")),
                run_succeeded=ok,
                public_rendered=bool((publish_result.get("build") or {}).get("public_rendered")),
                public_signal_count=int((publish_result.get("build") or {}).get("public_signal_count") or 0),
                post_requested=True,
                project_root=repo_root,
                force_post=False,
                allow_publish=True,
                dry_run=False,
                allow_text_only=False,
                allow_archival_bluesky_post=False,
            )
            if bluesky_result.get("status") == "failure":
                ok = False
        return {
            "ok": ok,
            "status": "publication_success" if ok else "publication_failed",
            "mode": "publication",
            "dispatch": DISPATCH,
            "edition_date": edition_date,
            "repo_root": str(repo_root),
            "pages_repo": str(pages_repo),
            "source_branch": source_branch,
            "pages_branch": pages_branch,
            "source_commit": source_state["head"],
            "pages_commit": pages_state["head"],
            "approved_release_path": str(approved_release.proposal_path),
            "approved_release_sha256": approved_release.proposal_sha256,
            "review_snapshot_path": str(approved_release.review_snapshot_path),
            "review_snapshot_sha256": approved_release.review_snapshot_sha256,
            "release_ready": ok,
            "publication_attempted": True,
            "pages_publish_copied": bool(publish_result.get("ok")),
            "pushed": bool(publish_result.get("pushed")),
            "isolated_source": bool(isolated_source),
            "publish": publish_result,
            "build": build,
            "bluesky_result": bluesky_result,
            "warnings": list(build.get("warnings") or []) + list(publish_result.get("warnings") or []),
            "errors": list(build.get("errors") or []) + list(publish_result.get("errors") or []),
        }
    finally:
        if live_temp_root is not None:
            shutil.rmtree(live_temp_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the dedicated Care Line publication runner.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pages-repo", required=True)
    parser.add_argument("--source-branch", required=True)
    parser.add_argument("--pages-branch", required=True)
    parser.add_argument("--date")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--dry-run-full", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--post-bluesky", action="store_true")
    parser.add_argument("--isolated-source", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.check_only and args.dry_run_full:
            raise ValueError("-CheckOnly and -DryRunFull cannot be combined.")
        if args.check_only and args.publish:
            raise ValueError("-Publish cannot be combined with -CheckOnly.")
        if args.check_only and args.push:
            raise ValueError("-Push cannot be combined with -CheckOnly.")
        if args.check_only and args.post_bluesky:
            raise ValueError("-PostBluesky cannot be combined with -CheckOnly.")
        if args.dry_run_full and args.push:
            raise ValueError("-Push cannot be combined with -DryRunFull.")
        if args.dry_run_full and not args.publish:
            raise ValueError("-DryRunFull requires -Publish.")
        if args.push and not args.publish:
            raise ValueError("-Push requires -Publish.")
        if args.isolated_source and (not args.publish or args.check_only or args.dry_run_full):
            raise ValueError("--isolated-source requires live --publish mode")
        if not args.date:
            raise ValueError("--date is required")
        result = _run_publish_flow(
            repo_root=Path(args.repo_root),
            pages_repo=Path(args.pages_repo),
            source_branch=str(args.source_branch),
            pages_branch=str(args.pages_branch),
            edition_date=str(args.date).strip(),
            check_only=bool(args.check_only),
            dry_run_full=bool(args.dry_run_full),
            publish=bool(args.publish),
            push=bool(args.push),
            post_bluesky=bool(args.post_bluesky),
            isolated_source=bool(args.isolated_source),
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
