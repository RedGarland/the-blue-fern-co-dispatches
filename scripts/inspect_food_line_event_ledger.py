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

from bluefern_dispatches.event_ledger import DEFAULT_LEDGER_PATH, inspect_ledger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the shadow Food Line event ledger without mutating publication state.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to the current directory.")
    parser.add_argument("--ledger-path", help="Optional explicit SQLite ledger path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    ledger_path = Path(args.ledger_path).resolve() if args.ledger_path else root / DEFAULT_LEDGER_PATH
    print(json.dumps(inspect_ledger(ledger_path), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
