from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_signal_wire_runner import run_signal_wire_intraday


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Food Line Signal Wire intraday scanner.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--run-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_signal_wire_intraday(
        Path(args.repo_root),
        dry_run=bool(args.dry_run),
        check_only=bool(args.check_only),
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
