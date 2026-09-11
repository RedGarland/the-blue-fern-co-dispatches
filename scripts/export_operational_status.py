from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bluefern_dispatches.operational_status_exporter import (  # noqa: E402
    ExportError,
    commit_and_push_status,
    export_status,
    load_recovery_context,
    prepare_status_checkout,
)


def _default_date(source_root: Path) -> str:
    root = source_root / "status" / "operational-health" / "food-line"
    dates = sorted(path.name for path in root.iterdir() if path.is_dir() and len(path.name) == 10) if root.exists() else []
    if not dates:
        raise ExportError("no Food Line operational-health date directories found")
    return dates[-1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serialize sanitized operational status for the Scheduled Dispatch Watch.")
    parser.add_argument("--source-root", type=Path, required=True, help="Production runner containing local receipts.")
    parser.add_argument("--care-source-root", type=Path, help="Optional Care Line runner containing shared Care receipts.")
    parser.add_argument("--status-checkout", type=Path, required=True, help="Dedicated operational-status checkout.")
    parser.add_argument("--date", help="Receipt date in YYYY-MM-DD form; defaults to the newest local date.")
    parser.add_argument("--evaluated-at", help="UTC evaluation timestamp; defaults to current UTC time.")
    parser.add_argument("--exported-at", help="UTC export timestamp; defaults to current UTC time.")
    parser.add_argument("--recovery-context", type=Path, help="Optional sanitized recovery lifecycle context JSON.")
    parser.add_argument("--prepare-branch", help="Dedicated status checkout branch to fast-forward before export.")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--push", action="store_true", help="Commit and push only ops/status artifacts.")
    parser.add_argument("--commit-message", default="Export operational status")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        date = args.date or _default_date(args.source_root)
        evaluated_at = args.evaluated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if args.push and not args.prepare_branch:
            raise ExportError("--push requires --prepare-branch")
        if args.prepare_branch:
            prepare_status_checkout(args.status_checkout, branch=args.prepare_branch, remote=args.remote)
        result = export_status(
            source_root=args.source_root,
            status_checkout=args.status_checkout,
            date=date,
            evaluated_at=evaluated_at,
            exported_at=args.exported_at,
            recovery=load_recovery_context(args.recovery_context),
            care_source_root=args.care_source_root,
        )
        if args.push:
            commit_and_push_status(
                args.status_checkout,
                paths=result["paths"],
                message=args.commit_message,
                remote=args.remote,
                branch=args.prepare_branch,
            )
    except ExportError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "paths": result["paths"], "food_line": result["food_line"], "care_line": result.get("care_line")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
