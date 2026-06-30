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

from scripts.run_food_line_dispatch import render_food_line_review_only


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a local Food Line review-only edition from candidate_review.json.")
    parser.add_argument("--date", required=True, help="Edition date YYYY-MM-DD")
    parser.add_argument("--candidate-review", required=True, help="Path to candidate_review.json")
    parser.add_argument(
        "--public-eligible-only",
        action="store_true",
        help="Render only rows where public_claim_eligible is true.",
    )
    parser.add_argument(
        "--source-url",
        help="Render only the candidate matching this source URL; fails closed on zero or ambiguous matches.",
    )
    parser.add_argument(
        "--output-root",
        default=str(Path("output") / "site-review-only" / "food-line"),
        help="Isolated local output root for review-only render artifacts.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = render_food_line_review_only(
            Path.cwd(),
            date=str(args.date),
            candidate_review_path=Path(str(args.candidate_review)),
            public_eligible_only=bool(args.public_eligible_only),
            source_url=str(args.source_url) if args.source_url is not None else None,
            output_root=Path(str(args.output_root)),
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
