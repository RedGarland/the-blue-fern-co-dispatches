from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.preflight_repo_state import build_preflight_report
from scripts.runner_repo_maintenance import (
    DEFAULT_PAGES_BRANCH,
    DEFAULT_SOURCE_BRANCH,
    postflight_runner_repos,
    sync_runner_repos,
)


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date(value: str) -> str:
    if not DATE_RE.match(value):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    return value


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


def _parse_json_tail(text: str) -> dict[str, Any]:
    match = re.search(r"(\{[\s\S]*\})\s*$", text)
    if not match:
        return {}
    return json.loads(match.group(1))


def run_smoke(
    *,
    edition_date: str,
    source_repo: Path,
    pages_repo: Path,
    source_branch: str,
    pages_branch: str,
) -> dict[str, Any]:
    sync_result = sync_runner_repos(
        source_repo,
        pages_repo,
        source_branch=source_branch,
        pages_branch=pages_branch,
    )
    result: dict[str, Any] = {
        "ok": False,
        "date": edition_date,
        "smoke_mode": "gate_only",
        "source_repo": str(source_repo),
        "pages_repo": str(pages_repo),
        "source_branch": source_branch,
        "pages_branch": pages_branch,
        "sync_result": sync_result,
        "preflight_after_sync": None,
        "operator_command": None,
        "operator_returncode": None,
        "operator_result": {},
        "postflight_result": None,
        "source_repo_status_final": None,
        "pages_repo_status_final": None,
        "errors": [],
        "warnings": [
            "This smoke test verifies runner sync, repo gates, branch expectations, date resolution, and manual-source gating only.",
            "It intentionally does not run the full Gaza dry-run path because the current dry-run implementation still writes dated artifacts.",
        ],
    }
    if not sync_result.get("ok"):
        result["errors"].extend(sync_result.get("errors") or ["runner sync failed"])
        return result

    preflight_after_sync = build_preflight_report(source_repo, pages_repo)
    result["preflight_after_sync"] = preflight_after_sync
    if not preflight_after_sync.get("ok"):
        result["errors"].append("preflight failed after sync")
        return result

    command = [
        sys.executable,
        "scripts\\run_gaza_daily_operator.py",
        "--date",
        edition_date,
        "--manual-source-check-only",
        "--pages-repo",
        str(pages_repo),
        "--pages-branch",
        pages_branch,
        "--expected-source-branch",
        source_branch,
    ]
    result["operator_command"] = subprocess.list2cmdline(command)
    done = _run_command(command, cwd=source_repo)
    result["operator_returncode"] = done.returncode
    result["operator_result"] = _parse_json_tail(done.stdout)
    if done.returncode != 0:
        result["errors"].append(done.stderr.strip() or done.stdout.strip() or "operator smoke check failed")

    postflight_result = postflight_runner_repos(source_repo, pages_repo)
    result["postflight_result"] = postflight_result
    result["source_repo_status_final"] = postflight_result.get("source_status_after")
    result["pages_repo_status_final"] = postflight_result.get("pages_status_after")
    if not postflight_result.get("ok"):
        result["errors"].append("postflight runner cleanup/check failed")

    operator_status = str((result["operator_result"] or {}).get("operator_status") or "")
    result["ok"] = (
        done.returncode == 0
        and operator_status == "MANUAL_SOURCE_VALID"
        and bool(postflight_result.get("ok"))
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the production-safe no-side-effect Gaza operator smoke test from a clean runner clone.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--source-repo", default=str(ROOT))
    parser.add_argument("--pages-repo", default=str(ROOT / "bluefern-dispatches-pages"))
    parser.add_argument("--source-branch", default=DEFAULT_SOURCE_BRANCH)
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH)
    args = parser.parse_args(argv)
    args.date = validate_date(args.date)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_smoke(
        edition_date=args.date,
        source_repo=Path(args.source_repo).resolve(),
        pages_repo=Path(args.pages_repo).resolve(),
        source_branch=args.source_branch,
        pages_branch=args.pages_branch,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
