from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_ROOT = ROOT / "data" / "dispatches" / "american-pressure" / "candidates"
REQUIRED_CURRENT_DEVELOPMENT_PILLARS = (
    "food_pressure",
    "financial_distress_pressure",
    "housing_household_cost_pressure",
    "health_access_pressure",
    "labor_income_pressure",
    "local_system_strain",
    "environmental_pressure",
    "policy_implementation",
)


def _completed_saturday_from(today: date) -> date:
    days_since_saturday = (today.weekday() - 5) % 7
    if days_since_saturday == 0:
        days_since_saturday = 7
    return today - timedelta(days=days_since_saturday)


def _validate_date(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _resolve_week_end(raw_date: str | None, raw_week_ending: str | None) -> str:
    if raw_date and raw_week_ending:
        raise ValueError("use either --date or --week-ending, not both")
    if raw_date:
        return _validate_date(raw_date)
    if raw_week_ending:
        if raw_week_ending == "previous-saturday":
            return _completed_saturday_from(date.today()).isoformat()
        return _validate_date(raw_week_ending)
    return _completed_saturday_from(date.today()).isoformat()


def _candidate_path(day: str) -> Path:
    return CANDIDATES_ROOT / day / "candidate_sources.json"


def _window_dates(week_end: str) -> tuple[str, list[str]]:
    end = datetime.strptime(week_end, "%Y-%m-%d").date()
    start = end - timedelta(days=6)
    return start.isoformat(), [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def _load_rows(week_end: str) -> tuple[str, list[dict[str, Any]]]:
    week_start, days = _window_dates(week_end)
    rows: list[dict[str, Any]] = []
    for day in days:
        path = _candidate_path(day)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        for row in payload.get("sources", []):
            if isinstance(row, dict):
                rows.append(row)
    return week_start, rows


def build_readiness_report(week_end: str) -> dict[str, Any]:
    week_start, rows = _load_rows(week_end)
    approved = [row for row in rows if str(row.get("review_status") or "").strip().lower() == "approved"]
    approved_by_pillar: Counter[str] = Counter()
    story_plus_data = 0
    for row in approved:
        pillar = str(row.get("pillar") or "").strip() or "unknown"
        approved_by_pillar[pillar] += 1
        anchors = row.get("linked_data_anchor_ids")
        if isinstance(anchors, list) and anchors:
            story_plus_data += 1
    missing = [pillar for pillar in REQUIRED_CURRENT_DEVELOPMENT_PILLARS if approved_by_pillar.get(pillar, 0) <= 0]
    reasons: list[str] = []
    if len(approved) <= 0:
        reasons.append("No approved candidates in the weekly window.")
    if missing:
        reasons.append(f"Missing required current-development pillars: {', '.join(missing)}")
    if story_plus_data <= 0:
        reasons.append("No approved story_plus_data candidates (linked_data_anchor_ids missing).")
    publish_recommended = len(reasons) == 0
    return {
        "ok": True,
        "week_start_date": week_start,
        "week_end_date": week_end,
        "approved_candidate_count": len(approved),
        "approved_by_pillar": dict(sorted(approved_by_pillar.items())),
        "missing_required_current_development_pillars": missing,
        "estimated_story_plus_data_potential": story_plus_data,
        "weekly_publish_recommended": publish_recommended,
        "reasons_if_not_recommended": reasons,
        "published": False,
        "pushed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check American Pressure weekly readiness from approved candidates.")
    parser.add_argument("--date", help="Alias for week-ending date YYYY-MM-DD.")
    parser.add_argument("--week-ending", help="Week-ending date YYYY-MM-DD or previous-saturday.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        week_end = _resolve_week_end(args.date, args.week_ending)
        print(json.dumps(build_readiness_report(week_end), indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)], "published": False, "pushed": False}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
