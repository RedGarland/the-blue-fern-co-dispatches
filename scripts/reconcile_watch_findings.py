from __future__ import annotations

import argparse
import json
from pathlib import Path

from bluefern_dispatches.watch_ledger import reconcile_day


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile durable ChatGPT watch findings with Dispatches production state.")
    parser.add_argument("--dispatch", required=True, choices=("gaza", "food-line", "care-line"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    report = reconcile_day(args.root, args.dispatch, args.date, write=not args.check_only)
    print(json.dumps(report, sort_keys=True))
    return 2 if report["unreconciled_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
