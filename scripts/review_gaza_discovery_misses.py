from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.gaza_discovery_review import summarize_gaza_discovery_miss_report
from bluefern_dispatches.gaza_discovery_review import write_gaza_discovery_miss_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review Gaza manual sources that automated discovery missed.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = write_gaza_discovery_miss_report(ROOT, args.date)
    print(summarize_gaza_discovery_miss_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
