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

from bluefern_dispatches.adapters.food_line_agent import adapt_food_line_agent_output, map_finding_to_food_line_candidate
from bluefern_dispatches.food_line_current_review import (
    ALLOWED_DECISIONS,
    CURRENT_PRODUCTION_SCOPE,
    HISTORICAL_ROOTS,
    PRIVATE_QUEUE_PATH,
    QUEUE_SCHEMA_VERSION,
    build_proposed_edition,
    load_queue,
    write_json_atomic,
    write_proposed_edition,
)


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


def _queue_source_paths(root: Path, inbox: Path, edition_date: str) -> list[Path]:
    discovery_candidates = root / "data" / "dispatches" / "food-line" / "discovery" / edition_date / "discovery_candidates.json"
    paths: list[Path] = []
    if inbox.exists():
        paths.extend(
            path
            for path in sorted(inbox.rglob("*.json"))
            if path.is_file() and "processed" not in path.parts
        )
    if not paths and discovery_candidates.exists():
        paths.append(discovery_candidates)
    return paths


def _build_review_queue(root: Path, edition_date: str, inbox: Path) -> dict[str, Any]:
    queue_path = root / PRIVATE_QUEUE_PATH
    items: list[dict[str, Any]] = []
    seen_duplicate_keys: set[str] = set()
    for source_path in _queue_source_paths(root, inbox, edition_date):
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        findings = adapt_food_line_agent_output(
            payload,
            agent_name=str(payload.get("agent_name") or "Food Line Source Watch") if isinstance(payload, dict) else "Food Line Source Watch",
            agent_run_id=str(payload.get("agent_run_id") or edition_date) if isinstance(payload, dict) else edition_date,
        )
        for finding in findings:
            row = map_finding_to_food_line_candidate(finding, edition_date=edition_date)
            row["source_artifact_path"] = str(source_path.relative_to(root)).replace("\\", "/")
            for private_key in ("raw_agent_payload", "private_text_provenance", "chain_of_custody", "hidden_instructions"):
                row.pop(private_key, None)
            duplicate_key = str(row.get("agent_duplicate_key") or row.get("candidate_id") or "")
            if duplicate_key and duplicate_key in seen_duplicate_keys:
                continue
            if duplicate_key:
                seen_duplicate_keys.add(duplicate_key)
            items.append(row)
    queue = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "queue_id": f"food-line-current-review-{edition_date}",
        "edition_date": edition_date,
        "production_scope": CURRENT_PRODUCTION_SCOPE,
        "historical_roots_excluded": list(HISTORICAL_ROOTS),
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "items": sorted(
            items,
            key=lambda item: (int(item.get("proposed_rank") or 0), str(item.get("review_item_id") or "")),
        ),
    }
    write_json_atomic(queue_path, queue)
    return queue


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
        queue_path = root / "data" / "dispatches" / "food-line" / "review" / "current-signal-review.json"
        if args.build_review_queue or not queue_path.exists():
            _build_review_queue(root, args.edition_date, inbox)
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
