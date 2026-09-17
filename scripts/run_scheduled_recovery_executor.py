from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.recovery_execution import ExecutionOptions, run_executor  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded same-day scheduled recovery planning/execution.")
    parser.add_argument("--dispatch", choices=["food-line", "care-line", "ice"], required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--proof-root", type=Path)
    parser.add_argument("--execute", action="store_true", help="Execute one allowlisted recovery action after all gates pass.")
    parser.add_argument("--expected-branch", default="add/pages-repo-default")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_executor(
        ExecutionOptions(
            dispatch=args.dispatch,
            source_root=args.source_root,
            date=args.date,
            evaluated_at=args.evaluated_at,
            execute=args.execute,
            proof_root=args.proof_root,
            expected_branch=args.expected_branch,
            python=Path(sys.executable),
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
