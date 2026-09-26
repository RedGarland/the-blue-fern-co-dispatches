from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.ice_release_bundle import IceReleaseBundleError, build_release_bundle

DEFAULT_QUEUE = Path("data/dispatches/ice/monitor/review_queue.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a private ICE reviewed-events bundle for non-public edition staging.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--event-fingerprint", action="append", required=True)
    parser.add_argument("--expected-queue-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root.resolve()
    queue_path = args.queue if args.queue.is_absolute() else root / args.queue
    try:
        result = build_release_bundle(
            root,
            queue_path,
            event_fingerprints=args.event_fingerprint,
            edition_date=args.edition_date,
            expected_queue_sha256=args.expected_queue_sha256,
        )
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, IceReleaseBundleError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
