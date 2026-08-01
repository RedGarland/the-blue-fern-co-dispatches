from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bluefern_dispatches.food_line_current_review import (
    ALLOWED_DECISIONS,
    PRIVATE_QUEUE_PATH,
    apply_editorial_decision,
    load_queue,
    queue_summary,
    write_json_atomic,
    write_proposed_edition,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the private Food Line current editorial review queue")
    parser.add_argument("--queue", default=str(PRIVATE_QUEUE_PATH), help="Private current-review queue path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate the private queue without writing")
    subparsers.add_parser("inspect", help="Summarize the private queue without writing")

    decision = subparsers.add_parser("decide", help="Record an editorial decision; never grants publication eligibility")
    decision.add_argument("--review-item-id", required=True)
    decision.add_argument("--decision", required=True, choices=ALLOWED_DECISIONS)
    decision.add_argument("--decided-by", required=True)
    decision.add_argument("--editorial-note", default="")
    decision.add_argument("--headline")
    decision.add_argument("--summary")

    proposal = subparsers.add_parser("propose", help="Write a private proposed-edition JSON and Markdown preview")
    proposal.add_argument("--dry-run", action="store_true", help="Build and report the proposal without writing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path.cwd()
    queue_path = (root / args.queue).resolve() if not Path(args.queue).is_absolute() else Path(args.queue).resolve()
    try:
        queue = load_queue(queue_path)
        result: dict[str, Any]
        if args.command in {"validate", "inspect"}:
            result = queue_summary(queue)
            result["queue_path"] = str(queue_path)
            result["mutation"] = "none"
        elif args.command == "decide":
            apply_editorial_decision(
                queue,
                review_item_id=args.review_item_id,
                decision=args.decision,
                decided_by=args.decided_by,
                decided_at=_utc_now(),
                editorial_note=args.editorial_note,
                proposed_public_headline=args.headline,
                proposed_public_summary=args.summary,
            )
            write_json_atomic(queue_path, queue)
            result = queue_summary(queue)
            result.update({"queue_path": str(queue_path), "decision": args.decision, "review_item_id": args.review_item_id})
        else:
            from bluefern_dispatches.food_line_current_review import build_proposed_edition

            proposed = build_proposed_edition(queue)
            if args.dry_run:
                result = {
                    "ok": True,
                    "dry_run": True,
                    "mutation": "none",
                    "draft_status": proposed["draft_status"],
                    "selected_item_count": proposed["selected_item_count"],
                    "proposed_edition": proposed,
                }
            else:
                json_path, markdown_path, proposed = write_proposed_edition(root, queue)
                result = {
                    "ok": True,
                    "dry_run": False,
                    "draft_status": proposed["draft_status"],
                    "selected_item_count": proposed["selected_item_count"],
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                    "publication_eligible": False,
                }
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
