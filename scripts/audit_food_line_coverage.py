from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_coverage_audit import (  # noqa: E402
    build_food_line_coverage_audit,
    render_food_line_coverage_markdown,
    write_food_line_coverage_audit,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Food Line discovery coverage recall without changing publication behavior.")
    parser.add_argument("--start-date", required=True, help="Start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", required=True, help="End date in YYYY-MM-DD format.")
    parser.add_argument("--benchmark-file", type=Path, help="Optional benchmark file path.")
    parser.add_argument("--write", action="store_true", help="Write JSON and Markdown audit files under output/review/food-line/coverage-audits/.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = ROOT
    report = build_food_line_coverage_audit(
        root,
        args.start_date,
        args.end_date,
        benchmark_file=args.benchmark_file,
    )
    markdown = render_food_line_coverage_markdown(report)
    if args.write:
        json_path, markdown_path = write_food_line_coverage_audit(root, report, args.start_date, args.end_date)
        print(f"Food Line coverage audit written: {json_path}")
        print(f"Food Line coverage audit markdown: {markdown_path}")
    else:
        print(markdown)
        print()
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
