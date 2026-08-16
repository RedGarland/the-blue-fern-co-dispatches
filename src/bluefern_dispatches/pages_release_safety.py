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

from bluefern_dispatches.care_line_release import finalize_public_release_status as finalize_care_line_public_release_status
from bluefern_dispatches.food_line_approved_proposal import finalize_public_release_status as finalize_food_line_public_release_status
from bluefern_dispatches.generator import public_site_contains_blocked_public_text, public_site_contains_detail_artifacts
from bluefern_dispatches.root_homepage import discover_public_releases, render_sitewide_homepage_from_template, select_effective_latest
from scripts.food_line_runtime_paths import FOOD_LINE_ALLOWED_DIRTY_CATEGORIES, classify_food_line_runtime_path
from scripts.validate_publish_scope import validate_publish_scope


BASE_URL = "https://dispatches.thebluefernco.com"
DEFAULT_PAGES_BRANCH = "gh-pages"
DEFAULT_SOURCE_BRANCH = "add/pages-repo-default"
DEFAULT_PAGES_REPO_NAME = "bluefern-dispatches-pages"
SUPPORTED_DISPATCHES = ("food-line", "care-line")
REQUIRED_ROOT_FILES_BY_DISPATCH = {
    "food-line": ("index.html", "archive.html", "rss.xml"),
    "care-line": ("index.html", "archive.html", "rss.xml"),
}
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


def _git_status_entries(repo: Path) -> list[tuple[str, str]]:
    result = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git status failed in {repo}")
    entries: list[tuple[str, str]] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        status = line[:2]
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append((status, _normalize_relpath(path)))
    return entries


def _repo_clean(repo: Path) -> bool:
    return not _git_status_paths(repo)


def _food_line_source_repo_clean(repo: Path) -> bool:
    for status, path in _git_status_entries(repo):
        if status != "??":
            return False
        category = classify_food_line_runtime_path(path)
        if category not in FOOD_LINE_ALLOWED_DIRTY_CATEGORIES:
            return False
    return True


def _repo_branch(repo: Path) -> str:
    branch = _git_stdout(repo, "branch", "--show-current")
    return branch or ""


def _repo_head(repo: Path) -> str:
    head = _git_stdout(repo, "rev-parse", "HEAD")
    return head or ""


def _repo_is_git_repo(repo: Path) -> bool:
    if not repo.exists() or not repo.is_dir():
        return False
    inside = _git_stdout(repo, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return False
    top_level = _git_stdout(repo, "rev-parse", "--show-toplevel")
    if not top_level or Path(top_level).resolve() != repo.resolve():
        return False
    if not _git_stdout(repo, "rev-parse", "--git-dir"):
        return False
    if _run_git(repo, "status", "--porcelain=v1", "--untracked-files=all").returncode != 0:
        return False
    return bool(_git_stdout(repo, "rev-parse", "HEAD"))


def _detached_source_verification(
    repo: Path,
    required_branch: str,
    source_commit: str,
    release_manifest: Path | None,
    allow_detached: bool,
) -> tuple[bool, str, str | None]:
    if not allow_detached:
        return False, "detached_source_requires_explicit_verification", None
    if not required_branch or not source_commit:
        return False, "detached_source_verification_failed", "missing required branch or source HEAD"
    if release_manifest is None:
        return False, "detached_source_verification_failed", "release manifest provenance is required for detached source verification"
    remote_ref = f"origin/{required_branch}"
    remote_head = _git_stdout(repo, "rev-parse", remote_ref)
    if not remote_head:
        return False, "detached_source_verification_failed", f"unable to resolve {remote_ref}"
    if source_commit != remote_head:
        return False, "detached_source_verification_failed", f"HEAD {source_commit} does not equal {remote_ref} {remote_head}"
    ancestor_check = _run_git(repo, "merge-base", "--is-ancestor", source_commit, remote_ref)
    if ancestor_check.returncode != 0:
        return False, "detached_source_verification_failed", f"{source_commit} is not an ancestor of {remote_ref}"
    try:
        payload = json.loads(release_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, "detached_source_verification_failed", f"unable to read release manifest: {exc}"
    if payload.get("source_commit") != source_commit:
        return False, "detached_source_verification_failed", "release manifest source_commit does not match detached HEAD"
    return True, "detached_head_verified_against_required_remote_branch", None


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
    if dispatch in {"food-line", "care-line"}:
        prefixes.append(f"{dispatch}/rss.xml")
    if dispatch == "care-line":
        prefixes.append("care-line/rss.xml")
    for date_text in dates:
        prefixes.append(f"{dispatch}/editions/{date_text}/")
    return prefixes


def _allowed_pages_prefixes_for_shared_homepage_refresh(dispatch: str, dates: Sequence[str]) -> list[str]:
    prefixes = _allowed_pages_prefixes(dispatch, dates)
    if dispatch == "food-line":
        prefixes.append("index.html")
    return prefixes


def _allowed_source_prefixes(dispatch: str, dates: Sequence[str]) -> list[str]:
    prefixes = [f"output/site/{dispatch}/index.html", f"output/site/{dispatch}/archive.html"]
    if dispatch in {"food-line", "care-line"}:
        prefixes.append(f"output/site/{dispatch}/rss.xml")
    if dispatch == "care-line":
        prefixes.append("output/site/care-line/rss.xml")
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
    if dispatch not in SUPPORTED_DISPATCHES:
        raise ValueError(f"Unsupported dispatch '{dispatch}'. Supported dispatches: {', '.join(SUPPORTED_DISPATCHES)}.")

    source_root = source_root.resolve()
    pages_repo = pages_repo.resolve()
    source_paths: list[Path] = []
    pages_paths: list[Path] = []
    edition_dirs: list[tuple[Path, Path]] = []
    root_files: list[tuple[Path, Path]] = []

    for filename in REQUIRED_ROOT_FILES_BY_DISPATCH[dispatch]:
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


def _validate_declared_scope(
    plan: CopyPlan,
    pages_branch: str,
    release_manifest: Path | None = None,
    required_source_ref: str | None = None,
    release_manifest_commit: str | None = None,
) -> list[str]:
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
                release_manifest_path=release_manifest,
                required_source_ref=required_source_ref,
                release_manifest_commit=release_manifest_commit,
            )
        )
    return errors


def _copy_file(source: Path, target: Path, dispatch: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_target_file_bytes(source, target, dispatch))


def _copy_edition_dir(source_dir: Path, target_dir: Path, dispatch: str) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    for source_file in sorted(path for path in source_dir.rglob("*") if path.is_file()):
        target_file = target_dir / source_file.relative_to(source_dir)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(_target_file_bytes(source_file, target_file, dispatch))


def _target_file_bytes(source: Path, target: Path, dispatch: str) -> bytes:
    raw = source.read_bytes()
    if dispatch not in {"food-line", "care-line"}:
        return raw
    if source.name != "edition_manifest.json":
        return raw
    target_parts = {part.lower() for part in target.parts}
    if dispatch not in target_parts or "editions" not in target_parts:
        return raw
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw
    if not isinstance(payload, dict):
        return raw
    finalized = (
        finalize_food_line_public_release_status(payload)
        if dispatch == "food-line"
        else finalize_care_line_public_release_status(payload)
    )
    if not finalized:
        return raw
    return json.dumps(payload, indent=2).encode("utf-8")


def _copy_selection(plan: CopyPlan) -> None:
    for source, target in plan.root_files:
        _copy_file(source, target, plan.dispatch)
    for source_dir, target_dir in plan.edition_dirs:
        _copy_edition_dir(source_dir, target_dir, plan.dispatch)


def _pages_changed_paths(pages_repo: Path) -> list[str]:
    return _git_status_paths(pages_repo)


def _changed_paths_within_release_scope(
    dispatch: str,
    dates: Sequence[str],
    changed_paths: Sequence[str],
    *,
    shared_homepage_refresh: bool = False,
) -> list[str]:
    allowed_prefixes = (
        _allowed_pages_prefixes_for_shared_homepage_refresh(dispatch, dates)
        if shared_homepage_refresh
        else _allowed_pages_prefixes(dispatch, dates)
    )
    return _paths_within_prefixes(changed_paths, allowed_prefixes)


def _refresh_shared_homepage_for_food_line(*, pages_root: Path, selected_dates: Sequence[str]) -> dict[str, Any]:
    homepage_path = pages_root / "index.html"
    if not homepage_path.exists():
        return {
            "ok": False,
            "message": f"shared homepage refresh skipped; missing homepage template: {homepage_path}",
        }
    template_html = homepage_path.read_text(encoding="utf-8")
    releases = discover_public_releases(pages_root, verify_root=pages_root, homepage_html=template_html)
    latest = select_effective_latest(releases)
    if "food-line" not in latest:
        return {
            "ok": False,
            "message": "shared homepage refresh skipped; unable to discover the latest Food Line release",
        }
    release = latest["food-line"]
    expected_date = sorted(set(selected_dates))[-1] if selected_dates else release.edition_date
    if release.edition_date != expected_date:
        return {
            "ok": False,
            "message": (
                "shared homepage refresh skipped; latest Food Line release does not match the requested edition "
                f"({release.edition_date} != {expected_date})"
            ),
        }
    refreshed_html = render_sitewide_homepage_from_template(template_html, release)
    if refreshed_html != template_html:
        homepage_path.write_text(refreshed_html, encoding="utf-8")
    return {
        "ok": True,
        "message": "shared homepage refreshed from Pages inventory",
        "homepage_path": str(homepage_path),
        "release_date": release.edition_date,
        "release_url": release.public_url,
    }


def _expected_urls(dates: Sequence[str], cache_bust: str | None = None) -> list[str]:
    return _expected_urls_for_dispatch("food-line", dates, cache_bust=cache_bust)


def _expected_urls_for_dispatch(dispatch: str, dates: Sequence[str], cache_bust: str | None = None) -> list[str]:
    urls: list[str] = []
    suffix = f"?cache_bust={urllib.parse.quote_plus(cache_bust)}" if cache_bust else ""
    for date_text in dates:
        base = f"{BASE_URL}/{dispatch}/editions/{date_text}"
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
    return _live_check_for_dispatch("food-line", dates, cache_bust=cache_bust, timeout=timeout, fetch_status=fetch_status)


def _live_check_for_dispatch(
    dispatch: str,
    dates: Sequence[str],
    cache_bust: str | None = None,
    timeout: int = FALLBACK_TIME_OUT_SECS,
    fetch_status: Callable[[str, int], int] | None = None,
) -> dict[str, Any]:
    status_fn = fetch_status or _http_status
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for url in _expected_urls_for_dispatch(dispatch, dates, cache_bust=cache_bust):
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
    return _deterministic_commit_message_for_dispatch("food-line", dates)


def _deterministic_commit_message_for_dispatch(dispatch: str, dates: Sequence[str]) -> str:
    label = "Food Line" if dispatch == "food-line" else "Care Line"
    if len(dates) == 1:
        return f"Publish {label} {dates[0]}"
    if len(dates) == 2:
        return f"Publish {label} {dates[0]} through {dates[-1]}"
    return f"Publish {label} selected editions"


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
    release_manifest: Path | None = None,
    allow_detached_source_at_required_branch_head: bool = False,
    shared_homepage_refresh: bool = False,
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
    if release_manifest is not None and len(selected_dates) != 1:
        return {"ok": False, "errors": ["--release-manifest currently requires exactly one release date."]}

    source_root = (source_repo or Path(__file__).resolve().parents[2]).resolve()
    pages_root = (pages_repo or (source_root / DEFAULT_PAGES_REPO_NAME)).resolve()

    if not source_root.exists() or not source_root.is_dir():
        return {"ok": False, "errors": [f"source repo root does not exist: {source_root}"]}
    if not pages_root.exists() or not pages_root.is_dir():
        return {"ok": False, "errors": [f"pages repo does not exist: {pages_root}"]}
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
    source_branch_verification = "branch_verified"

    if source_branch == require_source_branch:
        source_branch_verification = "required_source_branch_checked_out"
    elif not source_branch and allow_detached_source_at_required_branch_head:
        verified, source_branch_verification, verification_error = _detached_source_verification(
            source_root,
            require_source_branch,
            source_commit,
            release_manifest,
            allow_detached=True,
        )
        if not verified and verification_error:
            errors.append(verification_error)
    else:
        errors.append(f"source branch mismatch: expected {require_source_branch}, found {source_branch or '<detached>'}")
    if not dry_run and release_manifest is None and not _food_line_source_repo_clean(source_root):
        errors.append(f"source repo must be clean before sync: {source_root}")
    if (not dry_run or release_manifest is not None) and not _repo_clean(pages_root):
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
            "source_branch_verification": source_branch_verification,
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
            "source_branch_verification": source_branch_verification,
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
    required_source_ref = source_branch or (f"origin/{require_source_branch}" if allow_detached_source_at_required_branch_head else require_source_branch)
    scope_errors = _validate_declared_scope(
        plan,
        pages_branch,
        release_manifest=release_manifest,
        required_source_ref=required_source_ref,
        release_manifest_commit="HEAD",
    )
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
            "source_branch_verification": source_branch_verification,
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
    delta_entries: list[dict[str, Any]] = []
    for source_path, pages_path in zip(plan.source_paths, plan.pages_paths):
        target_bytes = _target_file_bytes(source_path, pages_path, dispatch)
        if not pages_path.exists():
            action = "add"
        elif target_bytes == pages_path.read_bytes():
            action = "unchanged"
        else:
            action = "modify"
        delta_entries.append(
            {
                "source_path": source_path.relative_to(source_root).as_posix(),
                "pages_path": pages_path.relative_to(pages_root).as_posix(),
                "action": action,
            }
        )

    if dry_run or live_check_only:
        live_check_result = (
            _live_check_for_dispatch(dispatch, selected_dates, cache_bust=cache_bust, fetch_status=fetch_status)
            if live_check or live_check_only
            else None
        )
        report = {
            "ok": not (live_check_result and not live_check_result["ok"]),
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "source_branch_verification": source_branch_verification,
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
            "delta_entries": delta_entries,
            "additions": [entry["pages_path"] for entry in delta_entries if entry["action"] == "add"],
            "modifications": [entry["pages_path"] for entry in delta_entries if entry["action"] == "modify"],
            "unchanged": [entry["pages_path"] for entry in delta_entries if entry["action"] == "unchanged"],
            "deletions": [],
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
    shared_homepage_refresh_result: dict[str, Any] | None = None
    if shared_homepage_refresh:
        if dispatch != "food-line":
            errors.append("shared homepage refresh is only supported for food-line")
        else:
            shared_homepage_refresh_result = _refresh_shared_homepage_for_food_line(
                pages_root=pages_root,
                selected_dates=selected_dates,
            )
            if not shared_homepage_refresh_result["ok"]:
                errors.append(str(shared_homepage_refresh_result["message"]))
    changed_pages_paths = _pages_changed_paths(pages_root)
    unexpected_changes = _changed_paths_within_release_scope(
        dispatch,
        selected_dates,
        changed_pages_paths,
        shared_homepage_refresh=shared_homepage_refresh and dispatch == "food-line",
    )
    errors.extend(public_site_contains_detail_artifacts(source_root / "output" / "site"))
    errors.extend(public_site_contains_blocked_public_text(source_root / "output" / "site"))
    if unexpected_changes:
        errors.append(
            f"unexpected Pages repo changes outside the allowed {dispatch} scope: " + ", ".join(sorted(unexpected_changes))
        )

    if errors:
        report = {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "source_branch_verification": source_branch_verification,
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
            "shared_homepage_refresh": shared_homepage_refresh_result,
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

    commit_message = _deterministic_commit_message_for_dispatch(dispatch, selected_dates)
    stage_paths = [f"{dispatch}/index.html", f"{dispatch}/archive.html"] + [f"{dispatch}/editions/{date_text}" for date_text in selected_dates]
    if dispatch in {"food-line", "care-line"}:
        stage_paths.insert(2, f"{dispatch}/rss.xml")
    if shared_homepage_refresh and dispatch == "food-line":
        stage_paths.append("index.html")
    add_result = _run_git(pages_root, "add", "-A", "--", *stage_paths)
    if add_result.returncode != 0:
        return {
            "ok": False,
            "dispatch": dispatch,
            "dates": list(selected_dates),
            "source_branch": source_branch,
            "source_commit": source_commit,
            "source_branch_verification": source_branch_verification,
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
            "shared_homepage_refresh": shared_homepage_refresh_result,
            "planned_source_paths": planned_source_paths,
            "planned_pages_paths": planned_pages_paths,
            "allowed_pages_prefixes": _allowed_pages_prefixes(dispatch, selected_dates),
            "allowed_source_prefixes": _allowed_source_prefixes(dispatch, selected_dates),
            "warnings": warnings,
            "errors": [add_result.stderr.strip() or add_result.stdout.strip() or "git add failed"],
        }

    staged_check = _run_git(pages_root, "diff", "--cached", "--quiet")
    if staged_check.returncode == 0:
        live_check_result = (
            _live_check_for_dispatch(dispatch, selected_dates, cache_bust=cache_bust, fetch_status=fetch_status)
            if live_check
            else None
        )
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
            "shared_homepage_refresh": shared_homepage_refresh_result,
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
            "pages_status": _pages_status_text(pages_root),
            "shared_homepage_refresh": shared_homepage_refresh_result,
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
            "shared_homepage_refresh": shared_homepage_refresh_result,
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

    live_check_result = (
        _live_check_for_dispatch(dispatch, selected_dates, cache_bust=cache_bust, fetch_status=fetch_status)
        if live_check
        else None
    )
    errors.extend(push_error)
    if live_check_result and not live_check_result["ok"]:
        errors.extend(list(live_check_result["failures"]))

    report = {
        "ok": not errors,
        "dispatch": dispatch,
        "dates": list(selected_dates),
        "source_branch": source_branch,
        "source_commit": source_commit,
        "source_branch_verification": source_branch_verification,
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
        "shared_homepage_refresh": shared_homepage_refresh_result,
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
    parser.add_argument("--release-manifest", help="Exact release manifest to validate before any source-to-Pages copy.")
    parser.add_argument(
        "--allow-detached-source-at-required-branch-head",
        action="store_true",
        help="Allow a detached source only when HEAD exactly matches origin/<required-branch> and release provenance.",
    )
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
        release_manifest=Path(args.release_manifest).resolve() if args.release_manifest else None,
        allow_detached_source_at_required_branch_head=args.allow_detached_source_at_required_branch_head,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1
