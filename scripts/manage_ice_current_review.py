from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.ice_current_review import (
    ALLOWED_DECISIONS,
    IceReviewError,
    apply_decision,
    load_queue,
    queue_summary,
)

DEFAULT_QUEUE = Path("data/dispatches/ice/monitor/review_queue.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the private ICE current review queue; never publishes.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    sub.add_parser("validate")
    decide = sub.add_parser("decide")
    decide.add_argument("--event-fingerprint", required=True)
    decide.add_argument("--decision", required=True, choices=sorted(ALLOWED_DECISIONS))
    decide.add_argument("--decided-by", required=True)
    decide.add_argument("--rationale", required=True)
    decide.add_argument("--expected-queue-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root.resolve()
    queue_path = args.queue if args.queue.is_absolute() else root / args.queue
    try:
        if args.command in {"inspect", "validate"}:
            result = queue_summary(load_queue(queue_path))
            result["queue_path"] = str(queue_path)
            result["mutation"] = "none"
        else:
            result = apply_decision(
                root,
                queue_path,
                event_fingerprint=args.event_fingerprint,
                decision=args.decision,
                decided_by=args.decided_by,
                rationale=args.rationale,
                expected_queue_sha256=args.expected_queue_sha256,
            )
            result["queue_path"] = str(queue_path)
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, IceReviewError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
