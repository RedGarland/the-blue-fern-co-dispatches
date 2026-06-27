from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.food_line_discovery_bridge import run_food_line_discovery_intake_bridge
from bluefern_dispatches.food_line_discovery_expansion import run_food_line_discovery_expansion
from bluefern_dispatches.food_line_sources import validate_date


DISPATCH_SLUG = "food-line"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _expand_dates(start_date: str, end_date: str) -> list[str]:
    start = datetime.strptime(validate_date(start_date), "%Y-%m-%d").date()
    end = datetime.strptime(validate_date(end_date), "%Y-%m-%d").date()
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    out: list[str] = []
    day = start
    while day <= end:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _candidate_copy_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "candidates" / edition_date / "candidate_sources.json"


def _review_json_path(root: Path, edition_date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / edition_date / "candidate_review.json"


def _review_html_path(root: Path, edition_date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / edition_date / "candidate_review.html"


def _backfill_summary_paths(root: Path, start_date: str, end_date: str) -> tuple[Path, Path]:
    folder = root / "output" / "review" / DISPATCH_SLUG / "backfill" / f"{start_date}_to_{end_date}"
    return folder / "backfill_summary.json", folder / "backfill_summary.html"


def _review_payload(edition_date: str, candidates: list[dict[str, Any]], intake: dict[str, Any]) -> dict[str, Any]:
    review_counts = Counter(str(row.get("candidate_review_status") or row.get("review_status") or "needs_review") for row in candidates)
    blocker_counts = Counter()
    for row in candidates:
        blocker = str(row.get("exclusion_reason") or "").strip() or str(row.get("classification_status") or "").strip()
        if blocker:
            blocker_counts[blocker] += 1
    return {
        "generated_at": _utc_now(),
        "edition_date": edition_date,
        "candidate_count_total": len(candidates),
        "candidate_count_traceable": sum(1 for row in candidates if str(row.get("traceability_status") or "") == "traceable"),
        "candidate_count_likely_qualifying": sum(
            1
            for row in candidates
            if bool(row.get("public_claim_eligible")) or str(row.get("classification_status") or "") in {"qualified_pressure_signal", "manual_fallback"}
        ),
        "candidate_count_needs_review": int(review_counts.get("needs_review", 0)),
        "candidate_count_watchlist": int(review_counts.get("watchlist", 0)),
        "candidate_count_rejected": int(review_counts.get("rejected", 0)),
        "discovery_lane_counts": dict(sorted(Counter(str(row.get("discovery_lane") or "") for row in candidates if str(row.get("discovery_lane") or "")).items())),
        "top_blocker_reasons": dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:10]),
        "intake_review_path": str(intake.get("discovery_review_path") or ""),
        "candidates": [
            {
                "title": str(row.get("discovered_title") or row.get("title") or ""),
                "publisher": str(row.get("discovered_publisher") or row.get("publisher") or ""),
                "source_url": str(row.get("source_url") or row.get("final_trace_url") or row.get("url") or ""),
                "original_source_url": str(row.get("original_source_url") or ""),
                "discovery_lane": str(row.get("discovery_lane") or ""),
                "discovery_query": str(row.get("discovery_query") or row.get("query_text") or ""),
                "discovery_source_type": str(row.get("discovery_source_type") or ""),
                "source_published_date": str(row.get("source_published_date") or row.get("publication_date") or ""),
                "date_basis": str(row.get("date_basis") or ""),
                "location_scope": str(row.get("location_scope") or row.get("geographic_scope") or ""),
                "state_hint": str(row.get("state_hint") or row.get("state_or_territory") or ""),
                "pressure_signal_hint": str(row.get("pressure_signal_hint") or ""),
                "pressure_signal_type_hint": str(row.get("pressure_signal_type_hint") or ""),
                "traceability_status": str(row.get("traceability_status") or ""),
                "candidate_review_status": str(row.get("candidate_review_status") or row.get("review_status") or ""),
                "public_claim_eligible": bool(row.get("public_claim_eligible")),
                "classification_status": str(row.get("classification_status") or ""),
                "exclusion_reason": str(row.get("exclusion_reason") or ""),
            }
            for row in candidates
        ],
    }


def _review_html(payload: dict[str, Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['title']))}</td>"
        f"<td>{html.escape(str(row['publisher']))}</td>"
        f"<td>{html.escape(str(row['discovery_lane']))}</td>"
        f"<td>{html.escape(str(row['candidate_review_status']))}</td>"
        f"<td>{'true' if row['public_claim_eligible'] else 'false'}</td>"
        f"<td>{html.escape(str(row['traceability_status']))}</td>"
        f"<td><a href=\"{html.escape(str(row['source_url']))}\">source</a></td>"
        f"<td>{html.escape(str(row['pressure_signal_hint']))}</td>"
        "</tr>"
        for row in payload["candidates"]
    )
    blockers = "".join(
        f"<li>{html.escape(reason)}: {count}</li>"
        for reason, count in (payload.get("top_blocker_reasons") or {}).items()
    ) or "<li>none</li>"
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Food Line discovery review</title>"
        "<style>body{font-family:Georgia,serif;margin:2rem;color:#1f2a30}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #c8c8c8;padding:.5rem;vertical-align:top;text-align:left}th{background:#f4efe7}</style>"
        "</head><body>"
        f"<h1>Food Line discovery review - {html.escape(str(payload['edition_date']))}</h1>"
        f"<p>Total candidates: {payload['candidate_count_total']} | Traceable: {payload['candidate_count_traceable']} | "
        f"Likely qualifying: {payload['candidate_count_likely_qualifying']}</p>"
        f"<h2>Top blocker reasons</h2><ul>{blockers}</ul>"
        "<h2>Candidates</h2>"
        "<table><thead><tr><th>Title</th><th>Publisher</th><th>Lane</th><th>Review</th><th>Public eligible</th>"
        "<th>Traceability</th><th>URL</th><th>Pressure hint</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></body></html>"
    )


def _copy_candidate_artifacts(
    root: Path,
    edition_date: str,
    *,
    intake: dict[str, Any] | None = None,
    dry_run: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    discovery_candidate_path = root / "data" / "dispatches" / DISPATCH_SLUG / "discovery" / edition_date / "discovery_candidates.json"
    candidates = _read_json(discovery_candidate_path) if discovery_candidate_path.exists() else []
    if not isinstance(candidates, list):
        raise ValueError(f"{discovery_candidate_path.name} must contain a JSON list")
    if not isinstance(intake, dict):
        intake_path = root / "output" / "review" / DISPATCH_SLUG / edition_date / "discovery_intake.json"
        intake = _read_json(intake_path) if intake_path.exists() else {}
        if not isinstance(intake, dict):
            intake = {}
    if not dry_run:
        _write_json(_candidate_copy_path(root, edition_date), candidates)
        payload = _review_payload(edition_date, [row for row in candidates if isinstance(row, dict)], intake)
        _write_json(_review_json_path(root, edition_date), payload)
        _write_text(_review_html_path(root, edition_date), _review_html(payload))
    return [row for row in candidates if isinstance(row, dict)], intake


def run_food_line_discovery_backfill(
    root: Path,
    start_date: str,
    end_date: str,
    *,
    max_queries: int | None = None,
    max_results_per_query: int = 10,
    dry_run: bool = False,
) -> dict[str, Any]:
    dates = _expand_dates(start_date, end_date)
    per_date: list[dict[str, Any]] = []
    lane_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    useful_source_hits: Counter[str] = Counter()
    dates_with_no_reviewable: list[str] = []

    for edition_date in dates:
        expansion = run_food_line_discovery_expansion(
            root,
            edition_date,
            edition_mode="no_current_update",
            max_queries=max_queries,
            max_results_per_query=max_results_per_query,
            dry_run=dry_run,
        )
        intake = run_food_line_discovery_intake_bridge(root, edition_date, dry_run=dry_run)
        candidates, intake_payload = _copy_candidate_artifacts(root, edition_date, intake=intake, dry_run=dry_run)
        typed_candidates = [row for row in candidates if isinstance(row, dict)]
        review_counts = Counter(str(row.get("candidate_review_status") or row.get("review_status") or "needs_review") for row in typed_candidates)
        traceable_count = sum(1 for row in typed_candidates if str(row.get("traceability_status") or "") == "traceable")
        likely_qualifying_count = sum(
            1
            for row in typed_candidates
            if bool(row.get("public_claim_eligible")) or str(row.get("classification_status") or "") in {"qualified_pressure_signal", "manual_fallback"}
        )
        for row in typed_candidates:
            lane = str(row.get("discovery_lane") or "").strip()
            if lane:
                lane_counts[lane] += 1
            reason = str(row.get("exclusion_reason") or row.get("classification_status") or "").strip()
            if reason:
                blocker_counts[reason] += 1
            if likely_qualifying_count > 0 and not str(row.get("duplicate_of") or "").strip():
                publisher = str(row.get("discovered_publisher") or row.get("publisher") or "").strip()
                if publisher:
                    useful_source_hits[publisher] += 1
        if intake.get("discovery_candidates_intaked", 0) <= 0:
            dates_with_no_reviewable.append(edition_date)
        per_date.append(
            {
                "date": edition_date,
                "ok": bool(expansion.get("ok")) and bool(intake.get("ok")),
                "candidate_count": len(typed_candidates),
                "traceable_candidate_count": traceable_count,
                "likely_qualifying_candidate_count": likely_qualifying_count,
                "watchlist_candidate_count": int(review_counts.get("watchlist", 0)),
                "rejected_candidate_count": int(review_counts.get("rejected", 0)),
                "needs_review_candidate_count": int(review_counts.get("needs_review", 0)),
                "discovery_gap": len(typed_candidates) == 0,
                "discovery_candidate_path": str(_candidate_copy_path(root, edition_date)),
                "candidate_review_json_path": str(_review_json_path(root, edition_date)),
                "candidate_review_html_path": str(_review_html_path(root, edition_date)),
                "discovery_audit_path": str(expansion.get("discovery_audit_json_path") or ""),
                "discovery_intake_path": str(intake_payload.get("discovery_review_path") or intake.get("discovery_review_path") or ""),
                "discovery_lanes_used": sorted({str(row.get("discovery_lane") or "") for row in typed_candidates if str(row.get("discovery_lane") or "")}),
                "public_output_written": False,
                "pages_repo_mutated": False,
            }
        )

    summary = {
        "ok": True,
        "generated_at": _utc_now(),
        "start_date": start_date,
        "end_date": end_date,
        "dates_scanned": dates,
        "total_candidates": sum(int(row["candidate_count"]) for row in per_date),
        "candidates_by_date": {row["date"]: row["candidate_count"] for row in per_date},
        "traceable_candidates_by_date": {row["date"]: row["traceable_candidate_count"] for row in per_date},
        "likely_qualifying_candidates_by_date": {row["date"]: row["likely_qualifying_candidate_count"] for row in per_date},
        "watchlist_candidates_by_date": {row["date"]: row["watchlist_candidate_count"] for row in per_date},
        "rejected_candidates_by_date": {row["date"]: row["rejected_candidate_count"] for row in per_date},
        "top_blocker_reasons": dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:10]),
        "discovery_lanes_used": dict(sorted(lane_counts.items())),
        "sources_with_repeated_useful_hits": dict(sorted(useful_source_hits.items(), key=lambda item: (-item[1], item[0]))[:20]),
        "dates_with_no_reviewable_candidates": dates_with_no_reviewable,
        "per_date": per_date,
        "public_output_written": False,
        "pages_repo_mutated": False,
        "dry_run": dry_run,
    }
    json_path, html_path = _backfill_summary_paths(root, start_date, end_date)
    summary["backfill_summary_json_path"] = str(json_path)
    summary["backfill_summary_html_path"] = str(html_path)
    if not dry_run:
        _write_json(json_path, summary)
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(row['date']))}</td>"
            f"<td>{row['candidate_count']}</td>"
            f"<td>{row['traceable_candidate_count']}</td>"
            f"<td>{row['likely_qualifying_candidate_count']}</td>"
            f"<td>{row['watchlist_candidate_count']}</td>"
            f"<td>{row['rejected_candidate_count']}</td>"
            "</tr>"
            for row in per_date
        )
        lanes = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in summary["discovery_lanes_used"].items()) or "<li>none</li>"
        gaps = "".join(f"<li>{html.escape(day)}</li>" for day in dates_with_no_reviewable) or "<li>none</li>"
        _write_text(
            html_path,
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>Food Line backfill summary</title>"
            "<style>body{font-family:Georgia,serif;margin:2rem;color:#1f2a30}table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #c8c8c8;padding:.5rem;text-align:left}th{background:#f4efe7}</style></head><body>"
            f"<h1>Food Line backfill summary - {html.escape(start_date)} to {html.escape(end_date)}</h1>"
            f"<p>Total candidates: {summary['total_candidates']}. Public output written: false. Pages repo mutated: false.</p>"
            f"<h2>Discovery lanes used</h2><ul>{lanes}</ul>"
            f"<h2>Dates still showing no reviewable candidates</h2><ul>{gaps}</ul>"
            "<h2>Per-date summary</h2><table><thead><tr><th>Date</th><th>Candidates</th><th>Traceable</th>"
            "<th>Likely qualifying</th><th>Watchlist</th><th>Rejected</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></body></html>",
        )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Food Line discovery intake and review artifacts without publishing public editions.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--max-results-per-query", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_food_line_discovery_backfill(
        ROOT,
        args.start_date,
        args.end_date,
        max_queries=args.max_queries,
        max_results_per_query=args.max_results_per_query,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
