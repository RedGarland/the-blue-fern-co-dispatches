from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_discovery import discover_care_line_sources
from bluefern_dispatches.generator import build_site, publish_pages

PAGES_REPO = ROOT / "bluefern-dispatches-pages"


def _run_one_day(
    root: Path,
    edition_date: str,
    *,
    discover: bool,
    publish: bool,
    push: bool,
    max_results_per_query: int,
    max_queries: int | None,
    max_candidates: int | None,
) -> dict[str, Any]:
    discovery_result = {"ok": True, "skipped": True}
    if discover:
        discovery_result = discover_care_line_sources(
            root,
            edition_date,
            max_results_per_query=max_results_per_query,
            max_queries=max_queries,
            max_candidates=max_candidates,
        )
    build_result = build_site(
        root,
        dry_run=False,
        dispatch_seed_dates={"care-line": edition_date},
        only_dispatches=("care-line",),
    )
    result: dict[str, Any] = {
        "ok": bool(discovery_result.get("ok", True)) and bool(build_result.get("ok")),
        "discovery_result": discovery_result,
        "build_result": build_result,
        "edition_date": edition_date,
    }
    if publish and result["ok"]:
        publish_result = publish_pages(
            root,
            PAGES_REPO,
            None,
            dry_run=False,
            commit=True,
            no_push=not push,
            only_dispatches=("care-line",),
            expect_date=edition_date,
            expect_dispatches=("care-line",),
        )
        result["publish_result"] = publish_result
        result["ok"] = bool(publish_result.get("ok"))
        if push and result["ok"]:
            pushed = subprocess.run(
                ["git", "push", "origin", "gh-pages"],
                cwd=str(PAGES_REPO),
                capture_output=True,
                text=True,
                check=False,
            )
            result["push"] = {
                "returncode": pushed.returncode,
                "stdout": pushed.stdout,
                "stderr": pushed.stderr,
            }
            result["ok"] = pushed.returncode == 0
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Care Line discovery and edition generation.")
    parser.add_argument("--date", help="Edition date YYYY-MM-DD")
    parser.add_argument("--start-date", help="Start date YYYY-MM-DD for a narrow back-check window")
    parser.add_argument("--end-date", help="End date YYYY-MM-DD for a narrow back-check window")
    parser.add_argument("--no-discover", action="store_true", help="Skip live discovery and only render from existing source files.")
    parser.add_argument("--publish", action="store_true", help="Copy the published site into the local Pages repo and create a local commit.")
    parser.add_argument("--push", action="store_true", help="Push the local Pages repo gh-pages branch after a successful publish.")
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None)
    args = parser.parse_args(argv)
    if args.push and not args.publish:
        raise ValueError("--push requires --publish")
    if args.start_date or args.end_date:
        if not args.start_date or not args.end_date:
            raise ValueError("--start-date and --end-date are required together")
        start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("--end-date must be on or after --start-date")
        results = []
        day = start
        while day <= end:
            results.append(
                _run_one_day(
                    Path.cwd(),
                    day.isoformat(),
                    discover=not args.no_discover,
                    publish=args.publish,
                    push=args.push,
                    max_results_per_query=args.max_results_per_query,
                    max_queries=args.max_queries,
                    max_candidates=args.max_candidates,
                )
            )
            day += timedelta(days=1)
        output = {"ok": all(bool(item.get("ok")) for item in results), "runs": results}
    else:
        if not args.date:
            raise ValueError("--date is required when no date range is supplied")
        output = _run_one_day(
            Path.cwd(),
            args.date,
            discover=not args.no_discover,
            publish=args.publish,
            push=args.push,
            max_results_per_query=args.max_results_per_query,
            max_queries=args.max_queries,
            max_candidates=args.max_candidates,
        )
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if output.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
