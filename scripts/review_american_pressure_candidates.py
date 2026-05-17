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

from scripts.scout_american_pressure_candidates import PILLARS, _load_targets, _safe_text, _validate_date


REVIEW_ROOT = ROOT / "output" / "dispatches" / "american-pressure" / "review"


def _candidate_file_path(day: str) -> Path:
    return ROOT / "data" / "dispatches" / "american-pressure" / "candidates" / day / "candidate_sources.json"


def _load_candidates(day: str) -> dict[str, Any]:
    path = _candidate_file_path(day)
    if not path.exists():
        return {
            "date": day,
            "sources": [],
            "rejected_candidates": [],
            "missing_file": True,
            "candidate_file_path": str(path),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload.setdefault("sources", [])
        payload.setdefault("rejected_candidates", [])
        payload.setdefault("date", day)
        payload.setdefault("candidate_file_path", str(path))
        return payload
    return {
        "date": day,
        "sources": [],
        "rejected_candidates": [],
        "invalid_shape": True,
        "candidate_file_path": str(path),
    }


def _bucket(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [row for row in rows if _safe_text(row.get("candidate_bucket")) == key]


def _find_missing_pillars(rows: list[dict[str, Any]]) -> list[str]:
    present = {str(row.get("pillar") or "") for row in rows if _safe_text(row.get("candidate_bucket")) in {"recommended", "maybe"}}
    return [pillar for pillar in PILLARS if pillar not in present]


def _diagnostics(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    file_path = str(payload.get("candidate_file_path") or _candidate_file_path(str(payload.get("date") or "")))
    candidate_file_exists = not bool(payload.get("missing_file"))
    raw_rows = payload.get("sources", [])
    raw_count = len(raw_rows) if isinstance(raw_rows, list) else 0
    rows = [row for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
    valid_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    skipped_no_url = 0
    skipped_no_public_pressure_angle = 0
    for row in rows:
        has_url = bool(_safe_text(row.get("url")))
        has_public_pressure = bool(_safe_text(row.get("public_pressure_angle")))
        has_title = bool(_safe_text(row.get("title")))
        has_pillar = bool(_safe_text(row.get("pillar")))
        if not has_url:
            skipped_no_url += 1
        if not has_public_pressure:
            skipped_no_public_pressure_angle += 1
        if has_url and has_public_pressure and has_title and has_pillar:
            valid_rows.append(row)
        else:
            invalid_rows.append(row)
    diagnostics = {
        "candidate_file_path": file_path,
        "candidate_file_exists": candidate_file_exists,
        "candidate_count_raw": raw_count,
        "candidate_count_valid": len(valid_rows),
        "rejected_validation_count": len(invalid_rows),
        "skipped_no_url_count": skipped_no_url,
        "skipped_no_public_pressure_angle_count": skipped_no_public_pressure_angle,
    }
    return diagnostics, valid_rows, invalid_rows


def _report_state(payload: dict[str, Any], diagnostics: dict[str, Any], recommended: list[dict[str, Any]], maybe: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> str:
    if payload.get("missing_file"):
        return "No candidate file was found for this date."
    if diagnostics["candidate_count_raw"] == 0:
        return "Candidate file exists but is empty."
    if diagnostics["candidate_count_valid"] == 0:
        return "Candidate file contains rows, but all candidates were skipped due validation."
    if diagnostics["candidate_count_valid"] > 0 and not recommended and not maybe and rejected:
        return "Candidates were found, but all valid candidates were rejected after scoring/filters."
    return "Candidates were found and parsed."


def build_review_markdown(day: str, payload: dict[str, Any]) -> str:
    diagnostics, rows, invalid_rows = _diagnostics(payload)
    recommended = sorted(_bucket(rows, "recommended"), key=lambda x: int(x.get("candidate_score") or 0), reverse=True)
    maybe = sorted(_bucket(rows, "maybe"), key=lambda x: int(x.get("candidate_score") or 0), reverse=True)
    rejected_scored = sorted(_bucket(rows, "rejected"), key=lambda x: int(x.get("candidate_score") or 0))
    rejected_raw = [row for row in payload.get("rejected_candidates", []) if isinstance(row, dict)]
    missing = _find_missing_pillars(rows)
    targets = _load_targets().get("target_groups", {})
    live_backend_message = str((payload.get("diagnostics") or {}).get("no_live_collection_backend_message") or "").strip()
    lines: list[str] = []
    lines.append(f"# American Pressure Candidate Review - {day}")
    lines.append("")
    lines.append(f"Generated at: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}")
    lines.append("")
    lines.append("## Candidate diagnostics")
    lines.append(f"- candidate_file_exists: {diagnostics['candidate_file_exists']}")
    lines.append(f"- candidate_count_raw: {diagnostics['candidate_count_raw']}")
    lines.append(f"- candidate_count_valid: {diagnostics['candidate_count_valid']}")
    lines.append(f"- rejected_validation_count: {diagnostics['rejected_validation_count']}")
    lines.append(f"- skipped_no_url_count: {diagnostics['skipped_no_url_count']}")
    lines.append(f"- skipped_no_public_pressure_angle_count: {diagnostics['skipped_no_public_pressure_angle_count']}")
    lines.append(f"- candidate_file_path: {diagnostics['candidate_file_path']}")
    lines.append(f"- review_state: {_report_state(payload, diagnostics, recommended, maybe, rejected_scored)}")
    if live_backend_message:
        lines.append(f"- collector_notice: {live_backend_message}")
    lines.append("")
    lines.append("## Recommended candidates")
    if not recommended:
        lines.append("- None")
    for row in recommended:
        lines.append(f"- [{row.get('title')}]({row.get('url')}) | pillar={row.get('pillar')} | score={row.get('candidate_score')}")
    lines.append("")
    lines.append("## Maybe candidates")
    if not maybe:
        lines.append("- None")
    for row in maybe:
        lines.append(f"- [{row.get('title')}]({row.get('url')}) | pillar={row.get('pillar')} | score={row.get('candidate_score')}")
    lines.append("")
    lines.append("## Rejected candidates")
    if not rejected_scored and not rejected_raw and not invalid_rows:
        lines.append("- None")
    for row in rejected_scored:
        reasons = ", ".join(row.get("rejection_reasons", []))
        lines.append(f"- [{row.get('title')}]({row.get('url')}) | pillar={row.get('pillar')} | reasons={reasons or 'low_score'}")
    for row in invalid_rows:
        title = str(row.get("title") or "(missing title)")
        pillar = str(row.get("pillar") or "(missing pillar)")
        reasons: list[str] = []
        if not _safe_text(row.get("url")):
            reasons.append("no_url")
        if not _safe_text(row.get("public_pressure_angle")):
            reasons.append("no_public_pressure_angle")
        if not _safe_text(row.get("title")):
            reasons.append("no_title")
        if not _safe_text(row.get("pillar")):
            reasons.append("no_pillar")
        lines.append(f"- {title} | pillar={pillar} | reasons={', '.join(reasons) or 'invalid_candidate'}")
    for row in rejected_raw:
        lines.append(f"- {row.get('title')} | pillar={row.get('pillar')} | reason={row.get('reason')}")
    lines.append("")
    lines.append("## Missing required pillars")
    if not missing:
        lines.append("- None")
    for pillar in missing:
        lines.append(f"- {pillar}")
    lines.append("")
    lines.append("## Suggested data anchors")
    for pillar in PILLARS:
        hints = targets.get(pillar, {}).get("data_anchor_hints", [])
        hint_text = ", ".join(hints) if hints else "none"
        lines.append(f"- {pillar}: {hint_text}")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review daily American Pressure candidate files.")
    parser.add_argument("--date", required=True, help="Candidate date YYYY-MM-DD")
    parser.add_argument("--write", action="store_true", help="Write review report markdown")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        day = _validate_date(args.date)
        payload = _load_candidates(day)
        markdown = build_review_markdown(day, payload)
        out_path = REVIEW_ROOT / f"{day}_candidate_review.md"
        if args.write:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(markdown, encoding="utf-8")
        print(json.dumps({"ok": True, "date": day, "review_report": str(out_path), "written": bool(args.write)}, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
