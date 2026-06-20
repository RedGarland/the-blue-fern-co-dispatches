from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_detention_watch import build_detention_watch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Cascadia Detention Watch baseline dossier.")
    parser.add_argument("--date", help="Edition date in YYYY-MM-DD format.")
    parser.add_argument("--input", help="Optional input JSON path. Defaults to data/dispatches/cascadia/detention_watch/baseline_YYYY-MM-DD.json")
    parser.add_argument("--update", help="Optional approved update JSON path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_detention_watch(
        ROOT,
        edition_date=args.date,
        input_path=Path(args.input).resolve() if args.input else None,
        update_path=Path(args.update).resolve() if args.update else None,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
