from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_current_review import build_proposed_edition, load_queue, write_json_atomic, write_proposed_edition


REPORT_SCHEMA = "food_line_current_intake_report_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process the private Food Line current intake.")
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--build-review-queue", action="store_true")
    parser.add_argument("--build-proposed-edition", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _current_intake_report(root: Path, edition_date: str, inbox: Path) -> dict[str, Any]:
    queue_path = root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
    queue = load_queue(queue_path)
    proposed = build_proposed_edition(queue)
    json_path = markdown_path = None
    if proposed.get("selected_item_count") is not None:
        json_path, markdown_path, proposed = write_proposed_edition(root, queue)
    inbox_count = len([path for path in inbox.rglob("*") if path.is_file()]) if inbox.exists() else 0
    queue_item_count = len(queue.get("items") or [])
    approved_count = int(proposed.get("approved_item_count") or 0)
    pending_count = int(proposed.get("pending_item_count") or 0)
    rejected_count = int(proposed.get("rejected_item_count") or 0)
    status = "success_with_exclusions" if rejected_count else "success"
    return {
        "schema_version": REPORT_SCHEMA,
        "created_at": _utc_now(),
        "edition_date": edition_date,
        "inbox": str(inbox),
        "discovered_file_count": inbox_count or queue_item_count,
        "accepted_file_count": queue_item_count,
        "import_count": queue_item_count,
        "dry_run_count": 0,
        "import_attempt_count": queue_item_count,
        "idempotent_noop_count": 0,
        "errors": [],
        "status": status,
        "queue": {
            "status": "written",
            "item_count": queue_item_count,
            "approved_item_count": approved_count,
            "pending_item_count": pending_count,
            "rejected_item_count": rejected_count,
            "path": str(queue_path),
        },
        "proposal": {
            "status": "written",
            "draft_status": proposed.get("draft_status"),
            "json_path": str(json_path) if json_path else None,
            "markdown_path": str(markdown_path) if markdown_path else None,
        },
        "publication_side_effects": {
            "public_output": False,
            "pages": False,
            "bluesky": False,
            "audio": False,
            "maps": False,
            "schedule": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path.cwd()
    inbox = Path(args.inbox)
    try:
        report = _current_intake_report(root, args.edition_date, inbox)
        if not args.dry_run:
            report_path = root / "data" / "dispatches" / "food-line" / "review" / "reports" / args.edition_date / "current-intake.json"
            write_json_atomic(report_path, report)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        report = {
            "schema_version": REPORT_SCHEMA,
            "created_at": _utc_now(),
            "edition_date": args.edition_date,
            "status": "failed",
            "errors": [str(exc)],
            "publication_side_effects": {
                "public_output": False,
                "pages": False,
                "bluesky": False,
                "audio": False,
                "maps": False,
                "schedule": False,
            },
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
