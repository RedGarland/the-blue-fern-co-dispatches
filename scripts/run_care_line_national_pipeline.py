from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_national_pipeline import (  # noqa: E402
    SMOKE_COLLECTION_RUNS_ROOT,
    SMOKE_REVIEW_ROOT,
    run_national_pipeline,
)

SMOKE_SOURCE_LIMIT_CEILING = 3
SMOKE_ITEMS_PER_SOURCE_CEILING = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the canonical non-publishing Care Line national intake pipeline.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--collection-only", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--max-sources", type=int, default=None)
    parser.add_argument("--fetch-timeout", type=int, default=20)
    parser.add_argument("--max-items-per-source", type=int, default=None)
    parser.add_argument("--active-queue-limit", type=int, default=150)
    parser.add_argument("--low-priority-cap", type=int, default=25)
    parser.add_argument("--include-manual-review", action="store_true")
    parser.add_argument("--exclude-partial", action="store_true")
    parser.add_argument("--allow-insecure-tls", action="store_true")
    args = parser.parse_args(argv)
    if not args.collection_only:
        parser.error("Care Line national pipeline requires --collection-only for scheduled or wrapper execution.")
    if args.smoke_test:
        if args.allow_insecure_tls:
            parser.error("Care Line smoke-test mode rejects --allow-insecure-tls.")
        if args.max_sources is None or args.max_items_per_source is None:
            parser.error("Care Line smoke-test mode requires --max-sources and --max-items-per-source.")
        if args.max_sources <= 0 or args.max_items_per_source <= 0:
            parser.error("Care Line smoke-test limits must be positive integers.")
        if args.max_sources > SMOKE_SOURCE_LIMIT_CEILING:
            parser.error(f"Care Line smoke-test source ceiling is {SMOKE_SOURCE_LIMIT_CEILING}.")
        if args.max_items_per_source > SMOKE_ITEMS_PER_SOURCE_CEILING:
            parser.error(f"Care Line smoke-test item ceiling is {SMOKE_ITEMS_PER_SOURCE_CEILING}.")
    elif args.max_sources is not None or args.max_items_per_source is not None:
        parser.error("Care Line production mode rejects smoke-test source or item limits unless --smoke-test is set.")
    result = run_national_pipeline(
        Path(args.repo_root).resolve(),
        run_date=args.run_date,
        include_partial=not args.exclude_partial,
        include_manual_review=args.include_manual_review,
        allow_insecure_tls=args.allow_insecure_tls,
        source_limit=args.max_sources,
        fetch_timeout=args.fetch_timeout,
        max_items_per_source=args.max_items_per_source or 25,
        active_queue_limit=args.active_queue_limit,
        low_priority_cap=args.low_priority_cap,
        smoke_test=args.smoke_test,
        collection_runs_root=SMOKE_COLLECTION_RUNS_ROOT if args.smoke_test else Path("data/dispatches/care-line/collection-runs"),
        review_root=SMOKE_REVIEW_ROOT if args.smoke_test else Path("data/dispatches/care-line/review"),
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    status = str((result.get("run_manifest") or {}).get("status") or "")
    return 0 if status in {"success", "partial_success"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
