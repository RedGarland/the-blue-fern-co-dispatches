from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.care_line_reviewed_event_queue import (  # noqa: E402
    QUEUE_PATH, enqueue, inspect_queue, select_release_set, update_state,
)
from bluefern_dispatches.universal_events.care_line_signal_wire import build_care_line_signal_wire_publication  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect and operator-manage the Care Line reviewed-event release queue.")
    parser.add_argument("command", choices=["inspect", "enqueue", "defer", "reject", "requeue", "release-set", "dry-run-publish"])
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--source-record-id", action="append", default=[])
    parser.add_argument("--event-id", action="append", default=[])
    parser.add_argument("--queue-id")
    parser.add_argument("--max-events", type=int)
    parser.add_argument("--release-after")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--reapprove", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    if args.command == "inspect":
        result = inspect_queue(root)
    elif args.command == "enqueue":
        result = enqueue(root, source_record_ids=args.source_record_id, event_ids=args.event_id, dry_run=not args.write)
    elif args.command in {"defer", "reject", "requeue"}:
        if not args.queue_id:
            parser.error(f"{args.command} requires --queue-id")
        target = "queued" if args.command == "requeue" else args.command
        result = update_state(root, queue_id=args.queue_id, state=target, reapprove=args.reapprove)
    elif args.command == "release-set":
        result = select_release_set(root, max_events=args.max_events, event_ids=args.event_id, release_after=args.release_after)
    else:
        release = select_release_set(root, max_events=args.max_events, event_ids=args.event_id, release_after=args.release_after)
        if not release["selected_event_ids"]:
            if args.event_id:
                result = {"operation": "dry-run-publish", "ok": False, "release_set": release, "error": "requested event IDs are not eligible in the release set"}
            else:
                result = {"operation": "dry-run-publish", "ok": True, "status": "nothing_to_publish", "release_set": release}
        else:
            result = build_care_line_signal_wire_publication(root, selected_event_ids=set(release["selected_event_ids"]))
            result = {"operation": "dry-run-publish", "dry_run": True, "release_set": release, "publication": result, "queue_path": str((root / QUEUE_PATH).as_posix())}
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
