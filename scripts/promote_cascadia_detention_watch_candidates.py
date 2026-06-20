from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_detention_watch_refresh import WATCH_DATA_ROOT, promote_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote approved Cascadia Detention Watch candidate claims into an update JSON.")
    parser.add_argument("--input", required=True, help="Path to source refresh review JSON.")
    parser.add_argument("--date", required=True, help="Target edition date in YYYY-MM-DD format.")
    parser.add_argument(
        "--output",
        help="Optional output JSON path. Default: data/dispatches/cascadia/detention_watch/update_YYYY-MM-DD.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output).resolve() if args.output else (ROOT / WATCH_DATA_ROOT / f"update_{args.date}.json")
    result = promote_candidates(Path(args.input).resolve(), args.date, output)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
