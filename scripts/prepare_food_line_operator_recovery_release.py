from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.food_line_operator_recovery_release_prep import (  # noqa: E402
    write_release_prep_artifact,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a human-reviewed Food Line operator-recovery item for private release preparation"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--item-id", required=True, help="Candidate ID, agent run ID, or item label to prepare")
    parser.add_argument("--reconciliation", required=True, help="Repository-relative recovery reconciliation artifact")
    parser.add_argument("--editorial-review", required=True, help="Repository-relative human editorial-review artifact")
    parser.add_argument("--release-decision", required=True, help="Repository-relative release-decision artifact")
    parser.add_argument("--output", help="Optional repository-relative deterministic private release-prep artifact path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    try:
        result = write_release_prep_artifact(
            root=root,
            item_id=args.item_id,
            edition_date=args.edition_date,
            reconciliation_ref=args.reconciliation,
            editorial_review_ref=args.editorial_review,
            release_decision_ref=args.release_decision,
            output_path=args.output,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps({key: value for key, value in result.items() if key != "artifact"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
