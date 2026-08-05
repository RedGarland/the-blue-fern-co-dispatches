from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bluefern_dispatches.food_line_current_intake import process_batch
from bluefern_dispatches.food_line_current_review import PRIVATE_AGENT_INBOX_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process the private Food Line current-signal inbox through editorial review.")
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--inbox", type=Path, default=Path(PRIVATE_AGENT_INBOX_ROOT))
    parser.add_argument("--build-review-queue", action="store_true")
    parser.add_argument("--build-proposed-edition", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = process_batch(Path.cwd(), edition_date=args.edition_date, inbox=args.inbox,
                           build_review_queue=args.build_review_queue or args.build_proposed_edition,
                           build_proposed=args.build_proposed_edition, dry_run=args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] in {"success", "success_with_exclusions", "partial_failure"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
