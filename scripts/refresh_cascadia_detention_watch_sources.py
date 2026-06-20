from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_detention_watch_refresh import render_review_dashboard, run_refresh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh curated Cascadia Detention Watch source metadata.")
    parser.add_argument("--date", help="Review date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--dashboard", action="store_true", help="Also render local HTML review dashboard.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_refresh(ROOT, as_of=args.date)
    if args.dashboard and result.get("ok"):
        dashboard_path = render_review_dashboard(Path(str(result["output_path"])))
        result["dashboard_path"] = str(dashboard_path)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
