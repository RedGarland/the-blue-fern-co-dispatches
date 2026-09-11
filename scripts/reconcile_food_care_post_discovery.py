from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any


def reconcile(root: Path, *, dispatch: str = "", agent_run_id: str = "", edition_date: str = "") -> dict[str, Any]:
    receipt_root = root / "data/private-agent-handoff/receipts"
    rows: list[dict[str, Any]] = []
    for path in sorted(receipt_root.glob("*/**/*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if dispatch and receipt.get("dispatch") != dispatch:
            continue
        if agent_run_id and receipt.get("agent_run_id") != agent_run_id:
            continue
        if edition_date and path.parent.name != edition_date:
            continue
        for item in receipt.get("reconciliation", []):
            if isinstance(item, dict):
                rows.append({"dispatch": receipt.get("dispatch"), "agent_run_id": receipt.get("agent_run_id"), "edition_date": path.parent.name, **item})
    return {"schema_version": "bluefern.food_care_post_discovery_reconciliation.v1", "filters": {"dispatch": dispatch, "agent_run_id": agent_run_id, "edition_date": edition_date}, "row_count": len(rows), "unaccounted": sum(1 for row in rows if row.get("unaccounted")), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only reconciliation of external Food/Care handoff receipts")
    parser.add_argument("--runner-root", type=Path, default=Path.cwd())
    parser.add_argument("--dispatch", choices=("food-line", "care-line"), default="")
    parser.add_argument("--agent-run-id", default="")
    parser.add_argument("--edition-date", default="")
    args = parser.parse_args(argv)
    if args.edition_date:
        date.fromisoformat(args.edition_date)
    print(json.dumps(reconcile(args.runner_root.resolve(), dispatch=args.dispatch, agent_run_id=args.agent_run_id, edition_date=args.edition_date), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
