from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.scheduled_recovery import dumps_report, evaluate_recovery  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only same-day scheduled recovery evaluator.")
    parser.add_argument("--dispatch", choices=["food-line", "care-line", "ice"], required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--expected-instances-json", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = json.loads(args.expected_instances_json) if args.expected_instances_json else None
    report = evaluate_recovery(
        dispatch=args.dispatch,
        source_root=args.source_root,
        date=args.date,
        evaluated_at=args.evaluated_at,
        expected_instances=expected,
    )
    print(dumps_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
