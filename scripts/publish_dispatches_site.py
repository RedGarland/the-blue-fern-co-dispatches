from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
DEFAULT_MESSAGE = "Publish Blue Fern dispatches site"
DEFAULT_DETENTION_WATCH_DATE = "2026-05-26"
VERIFY_TARGETS = [
    ("https://dispatches.thebluefernco.com/", "Cascadia Detention Watch"),
    ("https://dispatches.thebluefernco.com/cascadia/", "cascadia/detention-watch"),
    ("https://dispatches.thebluefernco.com/cascadia/detention-watch/", "Cascadia Detention Watch"),
    (f"https://dispatches.thebluefernco.com/cascadia/detention-watch/editions/{DEFAULT_DETENTION_WATCH_DATE}/", "Method note"),
]
LOCAL_VERIFY_TARGETS = [
    (ROOT / "output" / "site" / "index.html", "Cascadia Detention Watch"),
    (ROOT / "output" / "site" / "cascadia" / "index.html", "/cascadia/detention-watch/"),
    (ROOT / "output" / "site" / "cascadia" / "detention-watch" / "index.html", "Cascadia Detention Watch"),
    (
        ROOT / "output" / "site" / "cascadia" / "detention-watch" / "editions" / DEFAULT_DETENTION_WATCH_DATE / "index.html",
        "Method note",
    ),
]


def run_command(command: list[str], cwd: Path | None = None, check: bool = True, verbose: bool = False) -> subprocess.CompletedProcess[str]:
    if verbose:
        where = f" (cwd={cwd})" if cwd else ""
        print(f"$ {' '.join(command)}{where}")
    proc = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(command)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def build_public_site(verbose: bool = False) -> None:
    run_command([sys.executable, "scripts/publish_github_pages.py"], cwd=ROOT, verbose=verbose)


def build_detention_watch_baseline(edition_date: str, verbose: bool = False) -> None:
    run_command(
        [sys.executable, "scripts/run_cascadia_detention_watch_workflow.py", "baseline", "--date", edition_date],
        cwd=ROOT,
        verbose=verbose,
    )


def _ignore_copy(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        lower = name.lower()
        if lower in {"__pycache__", ".pytest_cache"}:
            ignored.add(name)
            continue
        if lower.endswith(".pyc") or lower.endswith(".pyo"):
            ignored.add(name)
            continue
        if "review_dashboard" in lower:
            ignored.add(name)
            continue
        if lower.startswith("source_refresh_") and lower.endswith(".json"):
            ignored.add(name)
            continue
    return ignored


def copy_public_site_to_pages(site_root: Path, pages_repo: Path, dry_run: bool = False) -> dict[str, Any]:
    if not site_root.exists():
        raise FileNotFoundError(f"site output root missing: {site_root}")
    if dry_run:
        return {"copied": False, "dry_run": True}
    shutil.copytree(site_root, pages_repo, dirs_exist_ok=True, ignore=_ignore_copy)
    return {"copied": True, "dry_run": False}


def _git_has_changes(pages_repo: Path, verbose: bool = False) -> bool:
    status = run_command(["git", "status", "--porcelain"], cwd=pages_repo, verbose=verbose)
    return bool(status.stdout.strip())


def _git_staged_changes(pages_repo: Path, verbose: bool = False) -> bool:
    diff = run_command(["git", "diff", "--cached", "--name-only"], cwd=pages_repo, verbose=verbose)
    return bool(diff.stdout.strip())


def _remote_advanced(pages_repo: Path, branch: str, verbose: bool = False) -> bool:
    local = run_command(["git", "rev-parse", "HEAD"], cwd=pages_repo, verbose=verbose).stdout.strip()
    remote = run_command(["git", "rev-parse", f"origin/{branch}"], cwd=pages_repo, verbose=verbose).stdout.strip()
    if local == remote:
        return False
    ancestor = run_command(["git", "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"], cwd=pages_repo, check=False, verbose=verbose)
    return ancestor.returncode == 0


def stage_commit_sync_push(
    pages_repo: Path,
    message: str,
    branch: str = "gh-pages",
    dry_run: bool = False,
    no_push: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "changed": False,
        "committed": False,
        "pushed": False,
        "no_push": no_push,
        "dry_run": dry_run,
        "used_force_push": False,
    }

    if dry_run:
        summary["message"] = "Dry run: no git changes applied."
        return summary

    if not _git_has_changes(pages_repo, verbose=verbose):
        summary["message"] = "No publish changes detected."
        return summary

    summary["changed"] = True
    run_command(["git", "add", "-A"], cwd=pages_repo, verbose=verbose)

    if not _git_staged_changes(pages_repo, verbose=verbose):
        summary["message"] = "No publish changes detected."
        return summary

    run_command(["git", "commit", "-m", message], cwd=pages_repo, verbose=verbose)
    summary["committed"] = True

    if no_push:
        summary["message"] = "Committed locally. Remote sync skipped (--no-push); no fetch, rebase, or push was attempted."
        return summary

    run_command(["git", "fetch", "origin", branch], cwd=pages_repo, verbose=verbose)
    if _remote_advanced(pages_repo, branch, verbose=verbose):
        rebase = run_command(["git", "pull", "--rebase", "origin", branch], cwd=pages_repo, check=False, verbose=verbose)
        if rebase.returncode != 0:
            summary["message"] = (
                "Rebase conflict detected. Resolve conflicts, run 'git rebase --continue' in the Pages repo, then push with 'git push origin gh-pages'."
            )
            summary["rebase_conflict"] = True
            return summary

    run_command(["git", "push", "origin", branch], cwd=pages_repo, verbose=verbose)
    summary["pushed"] = True
    summary["message"] = "Published to origin/gh-pages."
    return summary


def verify_urls() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True
    for url, marker in VERIFY_TARGETS:
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                status = getattr(response, "status", 200)
                body = response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - covered by mocked test branch
            ok = False
            checks.append({"url": url, "ok": False, "error": str(exc)})
            continue
        has_marker = marker in body
        entry_ok = status == 200 and has_marker
        if not entry_ok:
            ok = False
        checks.append(
            {
                "url": url,
                "ok": entry_ok,
                "status": status,
                "marker": marker,
                "marker_found": has_marker,
                "failure": None if entry_ok else ("status_non_200" if status != 200 else "content_marker_missing"),
            }
        )
    return {
        "ok": ok,
        "checks": checks,
        "note": "If verification fails immediately after push, GitHub Pages may take 1-3 minutes to refresh.",
    }


def verify_local_output() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok = True
    for path, marker in LOCAL_VERIFY_TARGETS:
        if not path.exists():
            ok = False
            checks.append({"path": str(path), "ok": False, "failure": "missing_file", "marker": marker})
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        marker_found = marker in body
        entry_ok = marker_found
        if not entry_ok:
            ok = False
        checks.append(
            {
                "path": str(path),
                "ok": entry_ok,
                "marker": marker,
                "marker_found": marker_found,
                "failure": None if entry_ok else "content_marker_missing",
            }
        )
    return {"ok": ok, "checks": checks, "note": "Local output verification only; live URL verification was skipped."}


def run_publish_workflow(args: argparse.Namespace) -> dict[str, Any]:
    pages_repo = Path(args.pages_repo).resolve()
    site_root = ROOT / "output" / "site"

    if not args.dry_run:
        build_public_site(verbose=args.verbose)
        if not args.skip_detention_watch:
            build_detention_watch_baseline(args.detention_watch_date, verbose=args.verbose)

    copy_result = copy_public_site_to_pages(site_root, pages_repo, dry_run=args.dry_run)
    git_result = stage_commit_sync_push(
        pages_repo,
        message=args.message,
        branch="gh-pages",
        dry_run=args.dry_run,
        no_push=args.no_push,
        verbose=args.verbose,
    )

    result: dict[str, Any] = {
        "ok": True,
        "pages_repo": str(pages_repo),
        "dry_run": args.dry_run,
        "no_push": args.no_push,
        "detention_watch_built": (not args.skip_detention_watch) and (not args.dry_run),
        "copy": copy_result,
        "git": git_result,
        "verification_urls": [url for url, _marker in VERIFY_TARGETS],
    }

    if args.verify:
        if args.no_push:
            result["verify"] = verify_local_output()
            result["verify_mode"] = "local"
            result["verify_live_skipped_reason"] = "Live verification skipped because --no-push was used."
        else:
            result["verify"] = verify_urls()
            result["verify_mode"] = "live"
        if not result["verify"].get("ok"):
            result["ok"] = False

    if git_result.get("rebase_conflict"):
        result["ok"] = False

    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-command local publish workflow for Dispatches site.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Local Pages repo path.")
    parser.add_argument("--message", default=DEFAULT_MESSAGE, help="Git commit message.")
    parser.add_argument("--detention-watch-date", default=DEFAULT_DETENTION_WATCH_DATE, help="Baseline detention watch date.")
    parser.add_argument("--skip-detention-watch", action="store_true", help="Skip detention watch baseline rebuild.")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify repo, commit, or push.")
    parser.add_argument("--no-push", action="store_true", help="Commit locally but skip push.")
    parser.add_argument("--verify", action="store_true", help="Run URL status/content checks after publish.")
    parser.add_argument("--verbose", action="store_true", help="Print command execution details.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_publish_workflow(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
