from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date as dt_date
from pathlib import Path
from typing import Any, Callable, Sequence

from bluefern_dispatches.generator import public_site_contains_blocked_public_text, public_site_contains_detail_artifacts
from scripts.validate_publish_scope import validate_publish_scope


BASE_URL = "https://dispatches.thebluefernco.com"
DEFAULT_PAGES_BRANCH = "gh-pages"
DEFAULT_SOURCE_BRANCH = "add/pages-repo-default"
DEFAULT_PAGES_REPO_NAME = "bluefern-dispatches-pages"
SUPPORTED_DISPATCHES = ("food-line",)
REQUIRED_FOOD_LINE_ROOT_FILES = ("index.html", "archive.html")
FALLBACK_TIME_OUT_SECS = 20


@dataclass(frozen=True)
class CopyPlan:
    dispatch: str
    dates: tuple[str, ...]
    source_root: Path
    pages_repo: Path
    source_paths: list[Path]
    pages_paths: list[Path]
    edition_dirs: list[tuple[Path, Path]]
    root_files: list[tuple[Path, Path]]


def _normalize_relpath(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    repo = repo.resolve()
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo}", *args],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git_stdout(repo: Path, *args: str) -> str | None:
    result = _run_git(repo, *args)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_status_paths(repo: Path) -> list[str]:
    result = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git status failed in {repo}")
    paths: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(_normalize_relpath(path))
    return paths


def _repo_clean(repo: Path) -> bool:
    return not _git_status_paths(repo)


def _repo_branch(repo: Path) -> str:
    branch = _git_stdout(repo, "branch", "--show-current")
    return branch or ""


def _repo_head(repo: Path) -> str:
    head = _git_stdout(repo, "rev-parse", "HEAD")
    return head or ""


def _repo_is_git_repo(repo: Path) -> bool:
    if not repo.exists() or not repo.is_dir():
        return False
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        return False
    result = _run_git(repo, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _repo_is_broken_gitlink(repo: Path) -> bool:
    git_dir = repo / ".git"
    return git_dir.exists() and not git_dir.is_dir()


def _parse_date(value: str) -> str:
    try:
        return dt_date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}': expected YYYY-MM-DD.") from exc


def _parse_dates(values: Sequence[str]) -> tuple[str, ...]:
    parsed: list[str] = []
    for raw_value in values:
        for token in str(raw_value).split(","):
            token = token.strip()
            if not token:
                continue
            date_text = _parse_date(token)
            if date_text not in parsed:
                parsed.append(date_text)
    return tuple(parsed)


def _allowed_pages_prefixes(dispatch: str, dates: Sequence[str]) -> list[str]:
    prefixes = [f"{dispatch}/index.html", f"{dispatch}/archive.html"]
    for date_text in dates:
        prefixes.append(f"{dispatch}/editions/{date_text}/")
    return prefixes


def _allowed_source_prefixes(dispatch: str, dates: Sequence[str]) -> list[str]:
    prefixes = [f"output/site/{dispatch}/index.html", f"output/site/{dispatch}/archive.html"]
    for date_text in dates:
        prefixes.append(f"output/site/{dispatch}/editions/{date_text}/")
    return prefixes


def _paths_within_prefixes(paths: Sequence[str], prefixes: Sequence[str]) -> list[str]:
    unexpected: list[str] = []
    for raw_path in paths:
        path = _normalize_relpath(raw_path)
        if not any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes):
            unexpected.append(path)
    return unexpected


def _edition_files(source_root: Path, dispatch: str, date_text: str) -> list[Path]:
    edition_dir = source_root / "output" / "site" / dispatch / "editions" / date_text
    if not edition_dir.exists() or not edition_dir.is_dir():
        raise FileNotFoundError(f"missing release edition directory: {edition_dir}")
    files = sorted(path for path in edition_dir.rglob("*") if path.is_file())
    if not files:
        raise FileNotFoundError(f"release edition directory is empty: {edition_dir}")
    return files


def _build_copy_plan(source_root: Path, pages_repo: Path, dispatch: str, dates: Sequence[str]) -> CopyPlan:
    if dispatch != "food-line":
        raise ValueError(f"Unsupported dispatch '{dispatch}'. Supported dispatches: {', '.join(SUPPORTED_DISPATCHES)}.")

    source_root = source_root.resolve()
    pages_repo = pages_repo.resolve()
    source_paths: list[Path] = []
    pages_paths: list[Path] = []
    edition_dirs: list[tuple[Path, Path]] = []
    root_files: list[tuple[Path, Path]] = []

    for filename in REQUIRED_FOOD_LINE_ROOT_FILES:
        source_file = source_root / "output" / "site" / dispatch / filename
        if not source_file.exists():
            raise FileNotFoundError(f"missing required source artifact: {source_file}")
        source_paths.append(source_file)
        pages_paths.append(pages_repo / dispatch / filename)
        root_files.append((source_file, pages_repo / dispatch / filename))

    for date_text in dates:
        files = _edition_files(source_root, dispatch, date_text)
        source_edition = source_root / "output" / "site" / dispatch / "editions" / date_text
        target_edition = pages_repo / dispatch / "editions" / date_text
        edition_dirs.append((source_edition, target_edition))
        for source_file in files:
            source_paths.append(source_file)
            pages_paths.append(target_edition / source_file.relative_to(source_edition))

    return CopyPlan(
        dispatch=dispatch,
        dates=tuple(dates),
        source_root=source_root,
        pages_repo=pages_repo,
        source_paths=source_paths,
        pages_paths=pages_paths,
        edition_dirs=edition_dirs,
        root_files=root_files,
    )


def _validate_declared_scope(plan: CopyPlan, pages_branch: str) -> list[str]:
    errors: list[str] = []
    for date_text in plan.dates:
        source_paths = [str(path.relative_to(plan.source_root)) for path in plan.source_paths if f"/editions/{date_text}/" in _normalize_relpath(path)]
        source_paths.extend(
            str(path.relative_to(plan.source_root))
            for path, _ in plan.root_files
            if path.exists()
        )
        pages_paths = [str(path.relative_to(plan.pages_repo)) for path in plan.pages_paths if f"/editions/{date_text}/" in _normalize_relpath(path)]
        pages_paths.extend(str(path.relative_to(plan.pages_repo)) for _, path in plan.root_files)
        errors.extend(
            validate_publish_scope(
                dispatch=plan.dispatch,
                date_text=date_text,
                source_repo_root=plan.source_root,
                pages_repo_root=plan.pages_repo,
                allow_pages=True,
                allow_audio=False,
                allow_map=False,
                allow_bluesky=False,
                strict=True,
                source_changed_paths=source_paths,
                pages_changed_paths=pages_paths,
            )
        )
    return errors


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_edition_dir(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)


def _copy_selection(plan: CopyPlan) -> None:
    for source, target in plan.root_files:
        _copy_file(source, target)
    for source_dir, target_dir in plan.edition_dirs:
        _copy_edition_dir(source_dir, target_dir)


def _pages_changed_paths(pages_repo: Path) -> list[str]:
    return _git_status_paths(pages_repo)


def _changed_paths_within_release_scope(dispatch: str, dates: Sequence[str], changed_paths: Sequence[str]) -> list[str]:
    allowed_prefixes = _allowed_pages_prefixes(dispatch, dates)
    return _paths_within_prefixes(changed_paths, allowed_prefixes)


def _expected_urls(dates: Sequence[str], cache_bust: str | None = None) -> list[str]:
    urls: list[str] = []
    suffix = f"?cache_bust={urllib.parse.quote_plus(cache_bust)}" if cache_bust else ""
    for date_text in dates:
        base = f"{BASE_URL}/food-line/editions/{date_text}"
        urls.extend(
            [
                f"{base}/{suffix}" if suffix else f"{base}/",
                f"{base}/sources_manifest.json{suffix}",
                f"{base}/curation_manifest.json{suffix}",
            ]
        )
    return urls


def _http_status(url: str, timeout: int = FALLBACK_TIME_OUT_SECS) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "DispatchesPagesReleaseSafety/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - controlled release check
            return int(getattr(response, "status", response.getcode()))
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _live_check(dates: Sequence[str], cache_bust: str | None = None, timeout: int = FALLBACK_TIME_OUT_SECS, fetch_status: Callable[[str, int], int] | None = None) -> dict[str, Any]:
    status_fn = fetch_status or _http_status
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for url in _expected_urls(dates, cache_bust=cache_bust):
        try:
            status = status_fn(url, timeout)
        except Exception as exc:  # noqa: BLE001 - surfaced as release status
            results.append({"url": url, "status": None, "error": str(exc)})
            failures.append(f"{url}: {exc}")
            continue
        result = {"url": url, "status": status}
        results.append(result)
        if status != 200:
            failures.append(f"{url}: HTTP {status}")
    return {
        "ok": not failures,
        "requested": True,
        "results": results,
        "failures": failures,
    }


def _deterministic_commit_message(dates: Sequence[str]) -> str:
    if len(dates) == 1:
        return f"Publish Food Line {dates[0]}"
    if len(dates) == 2:
        return f"Publish Food Line {dates[0]} through {dates[-1]}"
    return "Publish Food Line selected editions"


def _source_status_text(repo: Path) -> str:
    branch = _repo_branch(repo) or "<detached>"
    head = _repo_head(repo) or "<unknown>"
    dirty = _git_status_paths(repo)
    return json.dumps({"branch": branch, "head": head, "clean": not dirty, "dirty_paths": dirty}, indent=2)


def _pages_status_text(repo: Path) -> str:
    branch = _repo_branch(repo) or "<detached>"
    head = _repo_head(repo) or "<unknown>"
    dirty = _git_status_paths(repo)
    return json.dumps({"branch": branch, "head": head, "clean": not dirty, "dirty_paths": dirty}, indent=2)


def sync_pages_from_source(
    *,
    dispatch: str,
    dates: Sequence[str],
    require_source_branch: str,
    pages_branch: str = DEFAULT_PAGES_BRANCH,
    source_repo: Path | None = None,
    pages_repo: Path | None = None,
    dry_run: bool = False,
    commit: bool = False,
    push: bool = False,
    live_check: bool = False,
    live_check_only: bool = False,
    cache_bust: str | None = None,
    report_file: Path | None = None,
    fetch_status: Callable[[str, int], int] | None = None,
) -> dict[str, Any]:
    if live_check_only and (commit or push):
        return {
            "ok": False,
            "errors": ["--live-check-only cannot be combined with --commit or --push."],
        }
    if push and not commit:
        return {
            "ok": False,
            "errors": ["--push requires --commit."],
        }
    if live_check and not (push or live_check_only):
        return {
            "ok": False,
            "errors": ["--live-check requires --push or --live-check-only."],
        }
    if dispatch not in SUPPORTED_DISPATCHES:
        return {
            "ok": False,
            "errors": [f"Unsupported dispatch '{dispatch}'. Supported dispatches: {', '.join(SUPPORTED_DISPATCHES)}."],
        }
    selected_dates = _parse_dates(dates)
    if not selected_dates:
        return {"ok": False, "errors": ["At least one YYYY-MM-DD date must be provided."]}

    source_root = (source_repo or Path(__file__).resolve().parents[2]).resolve()
    pages_root = (pages_repo or (source_root / DEFAULT_PAGES_REPO_NAME)).resolve()

    if not source_root.exists() or not source_root.is_dir():
        return {"ok": False, "errors": [f"source repo root does not exist: {source_root}"]}
    if not pages_root.exists() or not pages_root.is_dir():
        return {"ok": False, "errors": [f"pages repo does not exist: {pages_root}"]}
    if _repo_is_broken_gitlink(source_root):
        return {"ok": False, "errors": [f"source repo .git is not a directory: {source_root / '.git'}"]}
    if _repo_is_broken_gitlink(pages_root):
        return {"ok": False, "errors": [f"pages repo .git is not a directory: {pages_root / '.git'}"]}
    if not _repo_is_git_repo(source_root):
        return {"ok": False, "errors": [f"source repo is not a git repository: {source_root}"]}
    if not _repo_is_git_repo(pages_root):
        return {"ok": False, "errors": [f"pages repo is not a git repository: {pages_root}"]}

    source_branch = _repo_branch(source_root)
    source_commit = _repo_head(source_root)
    pages_pre_commit = _repo_head(pages_root)
    pages_pre_branch = _repo_branch(pages_root)

    errors: list[str] = []
    warnings: list[str] = []

    if source_branch != require_source_branch:
        errors.append(f"source branch mismatch: expected {require_source_branch}, found {source_branch or '<detached>'}")
    if not dry_run and not _repo_clean(source_root):
        errors.append(f"source repo must be clean before sync: {source_root}")
    if not dry_run and not _repo_clean(pages_root):
        errors.append(f"pages repo must be clean before sync: {pages_root}")
    if pages_pre_branch != pages_branch:
        errors.append(f"pages repo branch mismatch: expected {pages_branch}, found {pages_pre_branch or '<detached>'}")

    if errors:
        return {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "errors": errors,
            "warnings": warnings,
            "copied_paths": [],
            "changed_pages_paths": [],
            "commit_hash": None,
            "commit_status": "blocked",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
        }

    try:
        plan = _build_copy_plan(source_root, pages_root, dispatch, selected_dates)
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "errors": [str(exc)],
            "warnings": warnings,
            "copied_paths": [],
            "changed_pages_paths": [],
            "commit_hash": None,
            "commit_status": "blocked",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
        }
    scope_errors = _validate_declared_scope(plan, pages_branch)
    pre_copy_errors = []
    pre_copy_errors.extend(public_site_contains_detail_artifacts(source_root / "output" / "site"))
    pre_copy_errors.extend(public_site_contains_blocked_public_text(source_root / "output" / "site"))
    if scope_errors:
        pre_copy_errors.extend(scope_errors)
    if pre_copy_errors:
        report = {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "errors": pre_copy_errors,
            "warnings": warnings,
            "copied_paths": [],
            "changed_pages_paths": [],
            "commit_hash": None,
            "commit_status": "blocked",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
            "planned_source_paths": [path.relative_to(source_root).as_posix() for path in plan.source_paths],
            "planned_pages_paths": [path.relative_to(pages_root).as_posix() for path in plan.pages_paths],
        }
        if report_file:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    planned_source_paths = [path.relative_to(source_root).as_posix() for path in plan.source_paths]
    planned_pages_paths = [path.relative_to(pages_root).as_posix() for path in plan.pages_paths]

    if dry_run or live_check_only:
        live_check_result = _live_check(selected_dates, cache_bust=cache_bust, fetch_status=fetch_status) if live_check or live_check_only else None
        report = {
            "ok": not (live_check_result and not live_check_result["ok"]),
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "copied_paths": planned_pages_paths,
            "changed_pages_paths": [],
            "commit_hash": None,
            "commit_status": "dry-run" if dry_run else "live-check-only",
            "push_status": "dry-run" if dry_run else "skipped",
            "live_check": live_check_result,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": [] if not live_check_result or live_check_result["ok"] else list(live_check_result["failures"]),
        }
        if report_file:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    _copy_selection(plan)
    changed_pages_paths = _pages_changed_paths(pages_root)
    unexpected_changes = _changed_paths_within_release_scope(dispatch, selected_dates, changed_pages_paths)
    errors.extend(public_site_contains_detail_artifacts(source_root / "output" / "site"))
    errors.extend(public_site_contains_blocked_public_text(source_root / "output" / "site"))
    if unexpected_changes:
        errors.append(
            "unexpected Pages repo changes outside the allowed Food Line scope: " + ", ".join(sorted(unexpected_changes))
        )

    if errors:
        report = {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "copied_paths": planned_pages_paths,
            "changed_pages_paths": changed_pages_paths,
            "commit_hash": None,
            "commit_status": "blocked",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": errors,
        }
        if report_file:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    commit_message = _deterministic_commit_message(selected_dates)
    stage_paths = [f"{dispatch}/index.html", f"{dispatch}/archive.html"] + [f"{dispatch}/editions/{date_text}" for date_text in selected_dates]
    add_result = _run_git(pages_root, "add", "-A", "--", *stage_paths)
    if add_result.returncode != 0:
        return {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "copied_paths": planned_pages_paths,
            "changed_pages_paths": changed_pages_paths,
            "commit_hash": None,
            "commit_status": "add-failed",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": [add_result.stderr.strip() or add_result.stdout.strip() or "git add failed"],
        }

    staged_check = _run_git(pages_root, "diff", "--cached", "--quiet")
    if staged_check.returncode == 0:
        live_check_result = _live_check(selected_dates, cache_bust=cache_bust, fetch_status=fetch_status) if live_check else None
        report = {
            "ok": not (live_check_result and not live_check_result["ok"]),
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "copied_paths": planned_pages_paths,
            "changed_pages_paths": changed_pages_paths,
            "commit_hash": None,
            "commit_status": "no-changes",
            "push_status": "skipped-no-changes",
            "live_check": live_check_result,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": [] if not live_check_result or live_check_result["ok"] else list(live_check_result["failures"]),
        }
        if report_file:
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    commit_result = _run_git(pages_root, "commit", "-m", commit_message)
    if commit_result.returncode != 0:
        return {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "copied_paths": planned_pages_paths,
            "changed_pages_paths": changed_pages_paths,
            "commit_hash": None,
            "commit_status": "commit-failed",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _source_status_text(pages_root),
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": [commit_result.stderr.strip() or commit_result.stdout.strip() or "git commit failed"],
        }

    commit_hash = _repo_head(pages_root)
    if not _repo_clean(pages_root):
        return {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "pages_branch": pages_branch,
            "pages_pre_release_commit": pages_pre_commit,
            "pages_pre_release_branch": pages_pre_branch,
            "copied_paths": planned_pages_paths,
            "changed_pages_paths": _pages_changed_paths(pages_root),
            "commit_hash": commit_hash,
            "commit_status": "dirty-after-commit",
            "push_status": "blocked",
            "live_check": None,
            "source_status": _source_status_text(source_root),
            "pages_status": _pages_status_text(pages_root),
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": [f"pages repo is dirty after commit: {pages_root}"],
        }

    pushed = False
    push_status = "skipped"
    push_error: list[str] = []
    if push:
        if _repo_branch(pages_root) != pages_branch:
            push_error = [f"push target branch mismatch: expected {pages_branch}, found {_repo_branch(pages_root) or '<detached>'}"]
        else:
            push_result = _run_git(pages_root, "push", "origin", pages_branch)
            if push_result.returncode != 0:
                push_error = [push_result.stderr.strip() or push_result.stdout.strip() or "git push failed"]
            else:
                pushed = True
                push_status = "pushed"

    live_check_result = _live_check(selected_dates, cache_bust=cache_bust, fetch_status=fetch_status) if live_check else None
    errors.extend(push_error)
    if live_check_result and not live_check_result["ok"]:
        errors.extend(list(live_check_result["failures"]))

    report = {
        "ok": not errors,
        "dispatch": dispatch,
        "dates": list(selected_dates),
        "source_branch": source_branch,
        "source_commit": source_commit,
        "pages_branch": pages_branch,
        "pages_pre_release_commit": pages_pre_commit,
        "pages_pre_release_branch": pages_pre_branch,
        "copied_paths": planned_pages_paths,
        "changed_pages_paths": changed_pages_paths,
        "commit_hash": commit_hash,
        "commit_status": "committed",
        "push_status": push_status,
        "pushed": pushed,
        "live_check": live_check_result,
        "source_status": _source_status_text(source_root),
        "pages_status": _pages_status_text(pages_root),
        "planned_source_paths": planned_source_paths,
        "planned_pages_paths": planned_pages_paths,
        "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
        "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
        "warnings": warnings,
        "errors": errors,
        "commit_message": commit_message,
    }
    if report_file:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded Pages sync from source output/site into the Pages repo.")
    parser.add_argument("--dispatch", required=True, choices=SUPPORTED_DISPATCHES, help="Dispatch to sync.")
    parser.add_argument("--dates", nargs="+", required=True, help="One or more YYYY-MM-DD dates to sync.")
    parser.add_argument("--require-source-branch", required=True, help="Exact source branch required for sync.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Expected Pages branch.")
    parser.add_argument("--source-repo", help="Optional source repo root. Defaults to the repository containing this script.")
    parser.add_argument("--pages-repo", help=f"Optional Pages repo root. Defaults to ./{DEFAULT_PAGES_REPO_NAME}.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report planned copies without mutating the Pages repo.")
    parser.add_argument("--commit", action="store_true", help="Stage and commit allowed Pages changes.")
    parser.add_argument("--push", action="store_true", help="Push the committed Pages branch to origin.")
    parser.add_argument("--live-check", action="store_true", help="Check published URLs after push or in live-check-only mode.")
    parser.add_argument("--live-check-only", action="store_true", help="Run only URL checks for the selected dates.")
    parser.add_argument("--cache-bust", help="Optional cache-bust token appended to live-check URLs.")
    parser.add_argument("--report-file", help="Optional JSON report file path.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = sync_pages_from_source(
        dispatch=args.dispatch,
        dates=args.dates,
        require_source_branch=args.require_source_branch,
        pages_branch=args.pages_branch,
        source_repo=Path(args.source_repo).resolve() if args.source_repo else None,
        pages_repo=Path(args.pages_repo).resolve() if args.pages_repo else None,
        dry_run=bool(args.dry_run),
        commit=bool(args.commit),
        push=bool(args.push),
        live_check=bool(args.live_check),
        live_check_only=bool(args.live_check_only),
        cache_bust=args.cache_bust,
        report_file=Path(args.report_file).resolve() if args.report_file else None,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1
