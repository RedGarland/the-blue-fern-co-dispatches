from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.preflight_repo_state import build_preflight_report, classify_status_line


DEFAULT_SOURCE_BRANCH = "add/pages-repo-default"
DEFAULT_PAGES_BRANCH = "gh-pages"
SAFE_CLEANUP_PREFIXES = (
    "logs/",
    ".pytest_cache/",
    ".pytest-temp",
    ".pytest_tmp",
    "output/review/",
    "output/site/",
    "output/dispatches/",
    "output/tmp-backups-pages/",
)


def _run_command(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_output(repo: Path, *args: str) -> str:
    result = _run_command(["git", *args], cwd=repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _git_status_entries(repo: Path) -> list[dict[str, Any]]:
    output = _git_output(repo, "status", "--short", "--branch", "--untracked-files=all")
    entries: list[dict[str, Any]] = []
    for line in output.splitlines():
        entry = classify_status_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def _git_status_branch(repo: Path) -> str:
    return _git_output(repo, "status", "--short", "--branch", "--untracked-files=all")


def _git_current_branch(repo: Path) -> str:
    return _git_output(repo, "branch", "--show-current")


def _is_safe_cleanup_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip()
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in SAFE_CLEANUP_PREFIXES
    )


def build_cleanup_plan(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    restore_paths: list[str] = []
    clean_paths: list[str] = []
    skipped_paths: list[str] = []
    for entry in entries:
        path = str(entry.get("path") or "").replace("\\", "/").strip()
        if not path:
            continue
        if not _is_safe_cleanup_path(path):
            skipped_paths.append(path)
            continue
        if bool(entry.get("is_untracked")):
            clean_paths.append(path)
        else:
            restore_paths.append(path)
    return {
        "restore_paths": sorted(dict.fromkeys(restore_paths)),
        "clean_paths": sorted(dict.fromkeys(clean_paths)),
        "skipped_paths": sorted(dict.fromkeys(skipped_paths)),
    }


def _run_git_restore(repo: Path, restore_paths: list[str]) -> tuple[bool, str | None, str | None]:
    if not restore_paths:
        return True, None, None
    result = _run_command(
        ["git", "restore", "--source=HEAD", "--staged", "--worktree", "--", *restore_paths],
        cwd=repo,
    )
    command = "git restore --source=HEAD --staged --worktree -- " + " ".join(restore_paths)
    message = result.stderr.strip() or result.stdout.strip() or None
    return result.returncode == 0, command, message


def _run_git_clean(repo: Path, clean_paths: list[str]) -> tuple[bool, str | None, str | None]:
    if not clean_paths:
        return True, None, None
    result = _run_command(["git", "clean", "-fd", "--", *clean_paths], cwd=repo)
    command = "git clean -fd -- " + " ".join(clean_paths)
    message = result.stderr.strip() or result.stdout.strip() or None
    return result.returncode == 0, command, message


def apply_cleanup_plan(repo: Path, plan: dict[str, list[str]]) -> dict[str, Any]:
    commands: list[str] = []
    messages: list[str] = []
    restore_ok, restore_command, restore_message = _run_git_restore(repo, plan.get("restore_paths", []))
    if restore_command:
        commands.append(restore_command)
    if restore_message:
        messages.append(restore_message)
    clean_ok, clean_command, clean_message = _run_git_clean(repo, plan.get("clean_paths", []))
    if clean_command:
        commands.append(clean_command)
    if clean_message:
        messages.append(clean_message)
    return {
        "ok": restore_ok and clean_ok,
        "commands": commands,
        "messages": messages,
    }


def _preflight_summary(source_repo: Path, pages_repo: Path) -> dict[str, Any]:
    return build_preflight_report(source_repo, pages_repo)


def sync_runner_repos(
    source_repo: Path,
    pages_repo: Path,
    *,
    source_branch: str = DEFAULT_SOURCE_BRANCH,
    pages_branch: str = DEFAULT_PAGES_BRANCH,
) -> dict[str, Any]:
    report_before = _preflight_summary(source_repo, pages_repo)
    result: dict[str, Any] = {
        "ok": False,
        "source_repo": str(source_repo),
        "pages_repo": str(pages_repo),
        "source_branch": source_branch,
        "pages_branch": pages_branch,
        "preflight_before": report_before,
        "preflight_after": None,
        "commands_run": [],
        "errors": [],
    }
    if not report_before.get("ok"):
        result["errors"].append("runner repo state is dirty before sync")
        return result

    current_source_branch = _git_current_branch(source_repo)
    current_pages_branch = _git_current_branch(pages_repo)
    if current_source_branch != source_branch:
        result["errors"].append(f"source repo must be on {source_branch}; found {current_source_branch or '<detached>'}")
        return result
    if current_pages_branch != pages_branch:
        result["errors"].append(f"pages repo must be on {pages_branch}; found {current_pages_branch or '<detached>'}")
        return result

    commands = [
        (source_repo, ["git", "fetch", "origin", source_branch]),
        (source_repo, ["git", "reset", "--hard", f"origin/{source_branch}"]),
        (pages_repo, ["git", "fetch", "origin", pages_branch]),
        (pages_repo, ["git", "reset", "--hard", f"origin/{pages_branch}"]),
    ]
    for cwd, command in commands:
        done = _run_command(command, cwd=cwd)
        rendered = " ".join(command)
        result["commands_run"].append(f"{cwd}: {rendered}")
        if done.returncode != 0:
            result["errors"].append(done.stderr.strip() or done.stdout.strip() or f"{rendered} failed")
            return result

    report_after = _preflight_summary(source_repo, pages_repo)
    result["preflight_after"] = report_after
    if not report_after.get("ok"):
        result["errors"].append("runner repo state is dirty after sync")
        return result
    result["ok"] = True
    return result


def postflight_runner_repos(source_repo: Path, pages_repo: Path) -> dict[str, Any]:
    entries_before = _git_status_entries(source_repo)
    cleanup_plan = build_cleanup_plan(entries_before)
    cleanup_result = apply_cleanup_plan(source_repo, cleanup_plan)
    report_after = _preflight_summary(source_repo, pages_repo)
    source_entries_after = _git_status_entries(source_repo)
    pages_status_after = _git_status_branch(pages_repo)
    result = {
        "ok": bool(report_after.get("ok")),
        "source_repo": str(source_repo),
        "pages_repo": str(pages_repo),
        "cleanup_plan": cleanup_plan,
        "cleanup_result": cleanup_result,
        "source_status_after": _git_status_branch(source_repo),
        "pages_status_after": pages_status_after,
        "remaining_source_entries": source_entries_after,
        "preflight_after": report_after,
    }
    if not cleanup_result.get("ok"):
        result["ok"] = False
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain a dedicated clean runner source repo and Pages repo.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--source-repo", default=str(ROOT))
        subparser.add_argument("--pages-repo", default=str(ROOT / "bluefern-dispatches-pages"))
        subparser.add_argument("--source-branch", default=DEFAULT_SOURCE_BRANCH)
        subparser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH)

    sync_parser = subparsers.add_parser("sync", help="Fetch and hard-reset a clean runner source repo and Pages repo to their tracked branches.")
    add_common(sync_parser)

    postflight_parser = subparsers.add_parser("postflight", help="Classify drift after a runner job and clean only approved generated/temp paths.")
    add_common(postflight_parser)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_repo = Path(args.source_repo).resolve()
    pages_repo = Path(args.pages_repo).resolve()
    if args.command == "sync":
        result = sync_runner_repos(
            source_repo,
            pages_repo,
            source_branch=args.source_branch,
            pages_branch=args.pages_branch,
        )
    else:
        result = postflight_runner_repos(source_repo, pages_repo)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
