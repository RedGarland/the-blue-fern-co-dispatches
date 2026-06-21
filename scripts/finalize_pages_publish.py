from __future__ import annotations

import argparse
import json
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
DEFAULT_BASE_BRANCH = "add/pages-repo-default"
GITLINK_PATH = "bluefern-dispatches-pages"
DEFAULT_BRANCH_TEMPLATE = "chore/update-pages-gitlink-{dispatch}-{date}"

LIVE_URLS: dict[str, tuple[str, str]] = {
    "gaza": ("https://dispatches.thebluefernco.com/gaza/editions/{date}/", "https://dispatches.thebluefernco.com/gaza/archive.html"),
    "food-line": ("https://dispatches.thebluefernco.com/food-line/editions/{date}/", "https://dispatches.thebluefernco.com/food-line/archive.html"),
    "cascadia": ("https://dispatches.thebluefernco.com/cascadia/editions/{date}/", "https://dispatches.thebluefernco.com/cascadia/archive.html"),
    "american-pressure": ("https://dispatches.thebluefernco.com/american-pressure/editions/{date}/", "https://dispatches.thebluefernco.com/american-pressure/archive.html"),
}


@dataclass(frozen=True)
class GitStatus:
    staged: tuple[str, ...]
    tracked: tuple[str, ...]
    untracked: tuple[str, ...]


@dataclass(frozen=True)
class CleanupPlan:
    restore_paths: tuple[str, ...]
    remove_paths: tuple[str, ...]


def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _parse_date(date_text: str) -> date_type:
    try:
        return date_type.fromisoformat(str(date_text).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_text}': expected YYYY-MM-DD.") from exc


def _human_date(date_text: str) -> str:
    dt = _parse_date(date_text)
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _dispatch_display_name(dispatch: str) -> str:
    return dispatch.replace("-", " ").title()


def _edition_url(dispatch: str, date_text: str) -> str:
    template, _ = LIVE_URLS[dispatch]
    return template.format(date=date_text)


def _archive_url(dispatch: str) -> str:
    _, template = LIVE_URLS[dispatch]
    return template


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git_stdout(repo: Path, *args: str) -> str:
    result = _git(repo, *args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_name_only(repo: Path, *args: str) -> tuple[str, ...]:
    result = _git(repo, *args, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return tuple(_normalize_path(line) for line in result.stdout.splitlines() if line.strip())


def _source_gitlink_sha(source_repo: Path, path: str = GITLINK_PATH) -> str:
    output = _git_stdout(source_repo, "ls-tree", "HEAD", "--", path)
    if not output:
        raise RuntimeError(f"source repo HEAD does not record gitlink {path}")
    parts = output.split()
    if len(parts) < 3:
        raise RuntimeError(f"unexpected git ls-tree output for {path}: {output}")
    return parts[2]


def _pages_head_sha(pages_repo: Path) -> str:
    return _git_stdout(pages_repo, "rev-parse", "HEAD")


def _git_status(source_repo: Path) -> GitStatus:
    return GitStatus(
        staged=_git_name_only(source_repo, "diff", "--cached", "--name-only"),
        tracked=_git_name_only(source_repo, "diff", "--name-only"),
        untracked=_git_name_only(source_repo, "ls-files", "--others", "--exclude-standard"),
    )


def _matches_prefix(path: str, prefixes: Sequence[str]) -> bool:
    normalized = _normalize_path(path)
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in prefixes)


def _dispatch_cleanup_prefixes(dispatch: str, date_text: str) -> tuple[str, ...]:
    return (
        f"data/records/",
        f"data/dispatches/{dispatch}/editions/{date_text}/",
        f"data/dispatches/{dispatch}/sources/{date_text}/",
        f"output/dispatches/{dispatch}/editions/{date_text}/",
        f"output/site/{dispatch}/editions/{date_text}/",
        f"output/site/{dispatch}/audio/{date_text}",
        f"output/site/{dispatch}/index.html",
        f"output/site/{dispatch}/archive.html",
        f"output/site/{dispatch}/rss.xml",
        f"output/site/{dispatch}/podcast.xml",
        f"output/site/{dispatch}/flash-briefing.json",
        f"output/site/{dispatch}/dashboard.html",
        f"output/review/{dispatch}/{date_text}/",
        f"output/tmp-backups-pages/{dispatch}/{date_text}/",
        f"logs/{dispatch}/",
        f"logs/{dispatch}-",
    )


def plan_generated_cleanup(source_repo: Path, dispatch: str, date_text: str) -> CleanupPlan:
    status = _git_status(source_repo)
    prefixes = _dispatch_cleanup_prefixes(dispatch, date_text)
    restore_paths = tuple(path for path in status.tracked if _matches_prefix(path, prefixes))
    remove_paths = tuple(path for path in status.untracked if _matches_prefix(path, prefixes))
    return CleanupPlan(restore_paths=restore_paths, remove_paths=remove_paths)


def _verify_url(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "bluefern-finalizer/1.0"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=30, context=context) as response:  # nosec: B310
        body = response.read().decode("utf-8", errors="replace")
        status = int(getattr(response, "status", response.getcode()))
    return status, body


def verify_public_urls(dispatch: str, date_text: str) -> dict[str, Any]:
    edition_url = _edition_url(dispatch, date_text)
    archive_url = _archive_url(dispatch)
    edition_status, edition_body = _verify_url(edition_url)
    archive_status, archive_body = _verify_url(archive_url)
    archive_includes_date = date_text in archive_body if dispatch == "gaza" else True
    ok = edition_status == 200 and archive_status == 200 and archive_includes_date
    return {
        "ok": ok,
        "edition_url": edition_url,
        "archive_url": archive_url,
        "edition_status": edition_status,
        "archive_status": archive_status,
        "archive_includes_date": archive_includes_date,
        "edition_body_contains_date": date_text in edition_body,
    }


def _pr_title(dispatch: str, date_text: str) -> str:
    return f"Update Pages gitlink for {_dispatch_display_name(dispatch)} {_human_date(date_text)} publish"


def _pr_body(dispatch: str, date_text: str, pages_sha: str, source_gitlink_sha: str, verification: dict[str, Any]) -> str:
    lines = [
        f"Pages commit: `{pages_sha}`",
        f"Dispatch/date: `{_dispatch_display_name(dispatch)}` / `{date_text}`",
        "",
        "Verification:",
        f"- Live edition URL: {verification['edition_url']} ({verification['edition_status']})",
        f"- Archive URL: {verification['archive_url']} ({verification['archive_status']})",
    ]
    if dispatch == "gaza":
        lines.append(f"- Archive includes date: {str(bool(verification['archive_includes_date'])).lower()}")
    lines.extend(
        [
            f"- Source gitlink before update: `{source_gitlink_sha}`",
            f"- Pages HEAD: `{pages_sha}`",
            "",
            "Scope:",
            "- Only the `bluefern-dispatches-pages` gitlink is committed here.",
            "- No generated output, logs, or source records are committed in this branch.",
        ]
    )
    return "\n".join(lines)


def _build_gitlink_branch_name(dispatch: str, date_text: str, branch_name: str | None) -> str:
    return branch_name or DEFAULT_BRANCH_TEMPLATE.format(dispatch=dispatch, date=date_text)


def _ensure_clean_pages_repo(pages_repo: Path) -> None:
    status = _git_stdout(pages_repo, "status", "--short")
    if status:
        raise RuntimeError(f"Pages repo must be clean before finalizing:\n{status}")


def _ensure_source_repo_ready(source_repo: Path, *, allow_generated_tracked: bool) -> GitStatus:
    status = _git_status(source_repo)
    if status.staged:
        raise RuntimeError("Refusing to proceed: source repo has staged files.")
    disallowed = [path for path in status.tracked if path != GITLINK_PATH]
    if disallowed and not allow_generated_tracked:
        raise RuntimeError(
            "Refusing to proceed: source repo has tracked changes other than bluefern-dispatches-pages. "
            "Re-run with --allow-generated-tracked only when those changes are expected generated residue."
        )
    return status


def _git_has_branch(repo: Path, branch_name: str) -> bool:
    return _git(repo, "show-ref", "--verify", f"refs/heads/{branch_name}", check=False).returncode == 0


def _commit_gitlink_branch(source_repo: Path, branch_name: str, base_branch: str, dispatch: str, date_text: str) -> str:
    if _git_has_branch(source_repo, branch_name):
        raise RuntimeError(f"Branch already exists: {branch_name}")
    _git(source_repo, "checkout", "-b", branch_name, base_branch)
    _git(source_repo, "add", "--", GITLINK_PATH)
    staged = _git_name_only(source_repo, "diff", "--cached", "--name-only")
    if staged != (GITLINK_PATH,):
        raise RuntimeError(f"gitlink-only staging failed; expected only {GITLINK_PATH}, saw {', '.join(staged) or '<none>'}")
    message = f"Update Pages gitlink for {_dispatch_display_name(dispatch)} {date_text} publish"
    _git(source_repo, "commit", "-m", message)
    return message


def _push_branch(source_repo: Path, branch_name: str) -> None:
    _git(source_repo, "push", "-u", "origin", branch_name)


def _create_pr(source_repo: Path, branch_name: str, base_branch: str, title: str, body: str) -> None:
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub CLI (gh) is not available; cannot create a PR.")
    subprocess.run(
        [gh, "pr", "create", "--base", base_branch, "--head", branch_name, "--title", title, "--body", body, "--draft"],
        cwd=source_repo,
        check=True,
    )


def _cleanup_generated(source_repo: Path, plan: CleanupPlan) -> None:
    if plan.restore_paths:
        _git(source_repo, "restore", "--", *plan.restore_paths)
    for raw_path in plan.remove_paths:
        path = source_repo / raw_path
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _print_plan(dispatch: str, date_text: str, branch_name: str, title: str, body: str, pages_sha: str, source_gitlink_sha: str) -> None:
    print(f"Dispatch: {_dispatch_display_name(dispatch)}")
    print(f"Date: {date_text}")
    print(f"Branch: {branch_name}")
    print(f"Pages HEAD: {pages_sha}")
    print(f"Source gitlink: {source_gitlink_sha}")
    print("PR title:")
    print(title)
    print("PR body:")
    print(body)
    print("Planned commands:")
    print(f"git checkout -b {branch_name} {DEFAULT_BASE_BRANCH}")
    print(f"git add -- {GITLINK_PATH}")
    print(f'git commit -m "Update Pages gitlink for {_dispatch_display_name(dispatch)} {date_text} publish"')
    print(f"git push -u origin {branch_name}")


def _print_cleanup_verification_commands() -> None:
    print("Final verification commands:")
    print("git status --short")
    print("git -C bluefern-dispatches-pages status --short")
    print("python -m compileall src tests scripts")
    print("python scripts/validate_repo_governance.py")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize a Pages publish by creating or cleaning a gitlink-only branch.")
    parser.add_argument("--dispatch", required=True, choices=("gaza", "food-line", "cascadia", "american-pressure"))
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Nested Pages repo path.")
    parser.add_argument("--base-branch", default=DEFAULT_BASE_BRANCH, help="Source repo branch used as the gitlink PR base.")
    parser.add_argument("--branch-name", help="Optional branch name to create.")
    parser.add_argument("--create-pr", action="store_true", help="Create a PR with GitHub CLI after pushing the branch.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without changing git state.")
    parser.add_argument("--clean-generated", action="store_true", help="Restore/remove known generated residue after the gitlink update is merged and pulled.")
    parser.add_argument("--allow-generated-tracked", action="store_true", help="Allow tracked generated residue in the source repo while creating the gitlink branch.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        _parse_date(args.date)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    source_repo = Path.cwd().resolve()
    pages_repo = Path(args.pages_repo).resolve()
    if not pages_repo.exists():
        print(f"Pages repo does not exist: {pages_repo}", file=sys.stderr)
        return 1

    try:
        _ensure_clean_pages_repo(pages_repo)
        source_gitlink_sha = _source_gitlink_sha(source_repo)
        pages_sha = _pages_head_sha(pages_repo)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    branch_name = _build_gitlink_branch_name(args.dispatch, args.date, args.branch_name)
    verification = verify_public_urls(args.dispatch, args.date)
    if not verification["ok"]:
        print("Live verification failed.", file=sys.stderr)
        print(json.dumps(verification, indent=2), file=sys.stderr)
        return 1

    if args.clean_generated:
        if source_gitlink_sha != pages_sha:
            print(
                f"Refusing cleanup: source gitlink {source_gitlink_sha} does not match Pages HEAD {pages_sha}.",
                file=sys.stderr,
            )
            return 1
        try:
            _ensure_source_repo_ready(source_repo, allow_generated_tracked=True)
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr)
            return 1
        plan = plan_generated_cleanup(source_repo, args.dispatch, args.date)
        print(f"Cleanup plan for {_dispatch_display_name(args.dispatch)} {args.date}")
        print(f"restore_paths: {list(plan.restore_paths)}")
        print(f"remove_paths: {list(plan.remove_paths)}")
        if args.dry_run:
            _print_cleanup_verification_commands()
            return 0
        try:
            _cleanup_generated(source_repo, plan)
        except Exception as exc:  # noqa: BLE001
            print(str(exc), file=sys.stderr)
            return 1
        _print_cleanup_verification_commands()
        return 0

    try:
        _ensure_source_repo_ready(source_repo, allow_generated_tracked=args.allow_generated_tracked)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    if source_gitlink_sha == pages_sha:
        print(
            f"Source gitlink already matches Pages HEAD ({pages_sha}); no gitlink update is needed.",
            file=sys.stderr,
        )
        return 1

    title = _pr_title(args.dispatch, args.date)
    body = _pr_body(args.dispatch, args.date, pages_sha, source_gitlink_sha, verification)
    if args.dry_run:
        _print_plan(args.dispatch, args.date, branch_name, title, body, pages_sha, source_gitlink_sha)
        return 0

    try:
        _commit_gitlink_branch(source_repo, branch_name, args.base_branch, args.dispatch, args.date)
        _push_branch(source_repo, branch_name)
        if args.create_pr:
            _create_pr(source_repo, branch_name, args.base_branch, title, body)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Created branch: {branch_name}")
    print(f"Commit title: {title}")
    print("PR body:")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
