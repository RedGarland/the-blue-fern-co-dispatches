from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_queue_runner import run_queue_poll  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one non-publishing Care Line reviewed-event queue poll.")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--max-events", type=int, default=5)
    args = parser.parse_args(argv)
    result = run_queue_poll(Path(args.repo_root), max_events=args.max_events)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
