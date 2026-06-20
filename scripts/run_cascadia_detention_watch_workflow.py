from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.cascadia_detention_watch import WATCH_DATA_ROOT, build_detention_watch, load_payload
from bluefern_dispatches.cascadia_detention_watch_refresh import REVIEW_ROOT, promote_candidates, render_review_dashboard, run_refresh


def _latest_file(root: Path, pattern: str) -> Path | None:
    matches = sorted(root.glob(pattern))
    return matches[-1] if matches else None


def _json_review_status(path: Path | None) -> str:
    if not path or not path.exists():
        return "missing"
    try:
        payload = load_payload(path)
    except Exception:
        return "invalid"
    return str(payload.get("review_status") or "").strip().lower() or "missing"


def run_refresh_mode(root: Path, edition_date: str | None) -> dict[str, Any]:
    result = run_refresh(root, as_of=edition_date)
    dashboard_path = render_review_dashboard(Path(str(result["output_path"])))
    result["dashboard_path"] = str(dashboard_path)
    result["next_step"] = (
        f"1) Open dashboard: {dashboard_path}\n"
        "2) Manually review/edit candidate_claims and set review_status to approved where justified.\n"
        "3) Run promote:\n"
        f"   .\\.venv\\Scripts\\python.exe scripts\\run_cascadia_detention_watch_workflow.py promote --date {edition_date or '<YYYY-MM-DD>'} --review {result['output_path']}"
    )
    return result


def run_promote_mode(root: Path, edition_date: str, review_path: Path) -> dict[str, Any]:
    output_path = root / WATCH_DATA_ROOT / f"update_{edition_date}.json"
    result = promote_candidates(review_path.resolve(), edition_date, output_path)
    result["next_step"] = (
        "Run render:\n"
        f"  .\\.venv\\Scripts\\python.exe scripts\\run_cascadia_detention_watch_workflow.py render --date {edition_date} --update {output_path}"
    )
    return result


def run_render_mode(root: Path, edition_date: str | None, update_path: Path) -> dict[str, Any]:
    status = _json_review_status(update_path)
    if status != "approved":
        return {"ok": False, "errors": [f"update review_status must be approved, got: {status}"]}
    result = build_detention_watch(root, edition_date=edition_date, update_path=update_path.resolve())
    if result.get("ok"):
        result["message"] = "Render completed. No push performed."
    return result


def run_baseline_mode(root: Path, edition_date: str | None) -> dict[str, Any]:
    result = build_detention_watch(root, edition_date=edition_date)
    if result.get("ok"):
        result["message"] = "Baseline render completed. No push performed."
    return result


def run_status_mode(root: Path) -> dict[str, Any]:
    data_root = root / WATCH_DATA_ROOT
    review_root = root / REVIEW_ROOT
    baseline = _latest_file(data_root, "baseline_*.json")
    update = _latest_file(data_root, "update_*.json")
    review_json = _latest_file(review_root, "source_refresh_*.json")
    review_dashboard = _latest_file(review_root, "review_dashboard_*.html")
    update_status = _json_review_status(update)
    if review_json:
        next_cmd = (
            f".\\.venv\\Scripts\\python.exe scripts\\run_cascadia_detention_watch_workflow.py promote --date <YYYY-MM-DD> --review {review_json}"
        )
    elif update and update_status == "approved":
        next_cmd = (
            f".\\.venv\\Scripts\\python.exe scripts\\run_cascadia_detention_watch_workflow.py render --date <YYYY-MM-DD> --update {update}"
        )
    else:
        next_cmd = ".\\.venv\\Scripts\\python.exe scripts\\run_cascadia_detention_watch_workflow.py refresh --date <YYYY-MM-DD>"
    return {
        "ok": True,
        "latest_baseline_file": str(baseline) if baseline else None,
        "latest_update_file": str(update) if update else None,
        "latest_review_json": str(review_json) if review_json else None,
        "latest_review_dashboard": str(review_dashboard) if review_dashboard else None,
        "latest_update_is_approved": update_status == "approved",
        "suggested_next_command": next_cmd,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run safe local operator workflow for Cascadia Detention Watch.")
    sub = parser.add_subparsers(dest="mode", required=True)

    refresh = sub.add_parser("refresh", help="Refresh source review queue and dashboard.")
    refresh.add_argument("--date", help="Review date in YYYY-MM-DD format.")

    promote = sub.add_parser("promote", help="Promote approved candidates into update JSON.")
    promote.add_argument("--date", required=True, help="Update date in YYYY-MM-DD format.")
    promote.add_argument("--review", required=True, help="Path to source_refresh_YYYY-MM-DD.json")

    render = sub.add_parser("render", help="Render merged baseline + approved update edition.")
    render.add_argument("--date", help="Edition date in YYYY-MM-DD format.")
    render.add_argument("--update", required=True, help="Path to approved update JSON.")

    baseline = sub.add_parser("baseline", help="Render baseline dossier only.")
    baseline.add_argument("--date", help="Edition date in YYYY-MM-DD format.")

    sub.add_parser("status", help="Show latest baseline/update/review files and next command.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "refresh":
        result = run_refresh_mode(ROOT, args.date)
    elif args.mode == "promote":
        result = run_promote_mode(ROOT, args.date, Path(args.review))
    elif args.mode == "render":
        result = run_render_mode(ROOT, args.date, Path(args.update))
    elif args.mode == "baseline":
        result = run_baseline_mode(ROOT, args.date)
    else:
        result = run_status_mode(ROOT)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
