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
from bluefern_dispatches.food_line_discovery_expansion import build_food_line_discovery_query_plan, run_food_line_discovery_expansion
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


def _discovery_audit_json_path(root: Path, edition_date: str) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / edition_date / "discovery_audit.json"


def _backfill_summary_paths(root: Path, start_date: str, end_date: str) -> tuple[Path, Path]:
    folder = root / "output" / "review" / DISPATCH_SLUG / "backfill" / f"{start_date}_to_{end_date}"
    return folder / "backfill_summary.json", folder / "backfill_summary.html"


def _write_partial_summary(root: Path, summary: dict[str, Any], *, start_date: str, end_date: str, dry_run: bool) -> None:
    if dry_run:
        return
    json_path, html_path = _backfill_summary_paths(root, start_date, end_date)
    summary["backfill_summary_json_path"] = str(json_path)
    summary["backfill_summary_html_path"] = str(html_path)
    _write_json(json_path, summary)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['date']))}</td>"
        f"<td>{row['candidate_count']}</td>"
        f"<td>{row['traceable_candidate_count']}</td>"
        f"<td>{row['likely_qualifying_candidate_count']}</td>"
        f"<td>{row['watchlist_candidate_count']}</td>"
        f"<td>{row['rejected_candidate_count']}</td>"
        f"<td>{html.escape(str(row.get('status') or 'completed'))}</td>"
        "</tr>"
        for row in summary.get("per_date", [])
    )
    lanes = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in (summary.get("discovery_lanes_used") or {}).items()) or "<li>none</li>"
    reviewable_gaps = "".join(f"<li>{html.escape(day)}</li>" for day in (summary.get("dates_with_no_reviewable_candidates") or [])) or "<li>none</li>"
    public_gaps = "".join(f"<li>{html.escape(day)}</li>" for day in (summary.get("dates_with_no_public_eligible_candidates") or [])) or "<li>none</li>"
    _write_text(
        html_path,
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Food Line backfill summary</title>"
        "<style>body{font-family:Georgia,serif;margin:2rem;color:#1f2a30}table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #c8c8c8;padding:.5rem;text-align:left}th{background:#f4efe7}</style></head><body>"
        f"<h1>Food Line backfill summary - {html.escape(start_date)} to {html.escape(end_date)}</h1>"
        f"<p>Total candidates: {summary.get('total_candidates', 0)}. Public output written: false. Pages repo mutated: false.</p>"
        f"<h2>Discovery lanes used</h2><ul>{lanes}</ul>"
        f"<h2>Dates with no reviewable candidates</h2><ul>{reviewable_gaps}</ul>"
        f"<h2>Dates with no public-eligible candidates</h2><ul>{public_gaps}</ul>"
        "<h2>Per-date summary</h2><table><thead><tr><th>Date</th><th>Candidates</th><th>Traceable</th>"
        "<th>Likely qualifying</th><th>Watchlist</th><th>Rejected</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></body></html>",
    )


def _review_payload(
    edition_date: str,
    candidates: list[dict[str, Any]],
    intake: dict[str, Any],
    *,
    google_news_debug_by_candidate: dict[str, Any] | None = None,
    google_news_resolution_status_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review_counts = Counter(str(row.get("candidate_review_status") or row.get("review_status") or "needs_review") for row in candidates)
    blocker_counts = Counter()
    for row in candidates:
        blockers = [str(item).strip() for item in row.get("public_claim_blockers") or [] if str(item).strip()]
        if not blockers:
            fallback = str(row.get("exclusion_reason") or "").strip() or str(row.get("classification_status") or "").strip()
            if fallback:
                blockers = [fallback]
        for blocker in blockers:
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
        "google_news_resolution_status_counts": dict(sorted((google_news_resolution_status_counts or {}).items())),
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
                "public_claim_blockers": list(row.get("public_claim_blockers") or []),
                "google_news_resolution": dict((google_news_debug_by_candidate or {}).get(str(row.get("candidate_id") or ""), {})),
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
    audit_path = root / "output" / "review" / DISPATCH_SLUG / edition_date / "discovery_audit.json"
    audit = _read_json(audit_path) if audit_path.exists() else {}
    if not isinstance(audit, dict):
        audit = {}
    if not dry_run:
        _write_json(_candidate_copy_path(root, edition_date), candidates)
        payload = _review_payload(
            edition_date,
            [row for row in candidates if isinstance(row, dict)],
            intake,
            google_news_debug_by_candidate=dict(audit.get("google_news_resolution_debug_by_candidate") or {}),
            google_news_resolution_status_counts=dict(audit.get("google_news_resolution_status_counts") or {}),
        )
        _write_json(_review_json_path(root, edition_date), payload)
        _write_text(_review_html_path(root, edition_date), _review_html(payload))
    return [row for row in candidates if isinstance(row, dict)], intake


def run_food_line_discovery_backfill(
    root: Path,
    start_date: str,
    end_date: str,
    *,
    max_queries: int | None = 8,
    max_results_per_query: int = 5,
    query_lookback_days: int = 0,
    query_lookahead_days: int = 0,
    public_claim_lookback_days: int = 0,
    public_claim_lookahead_days: int = 0,
    resume: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    dates = _expand_dates(start_date, end_date)
    per_date: list[dict[str, Any]] = []
    lane_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    useful_source_hits: Counter[str] = Counter()
    dates_with_no_reviewable: list[str] = []
    dates_with_no_public_eligible: list[str] = []
    failed_dates: list[str] = []

    def build_summary() -> dict[str, Any]:
        summary = {
            "ok": not failed_dates,
            "generated_at": _utc_now(),
            "start_date": start_date,
            "end_date": end_date,
            "dates_scanned": dates,
            "failed_dates": failed_dates,
            "total_candidates": sum(int(row["candidate_count"]) for row in per_date),
            "candidates_by_date": {row["date"]: row["candidate_count"] for row in per_date},
            "traceable_candidates_by_date": {row["date"]: row["traceable_candidate_count"] for row in per_date},
            "likely_qualifying_candidates_by_date": {row["date"]: row["likely_qualifying_candidate_count"] for row in per_date},
            "public_eligible_candidates_by_date": {row["date"]: row["public_eligible_candidate_count"] for row in per_date},
            "watchlist_candidates_by_date": {row["date"]: row["watchlist_candidate_count"] for row in per_date},
            "rejected_candidates_by_date": {row["date"]: row["rejected_candidate_count"] for row in per_date},
            "in_window_candidates_by_date": {row["date"]: row["in_window_candidate_count"] for row in per_date},
            "out_of_window_candidates_by_date": {row["date"]: row["out_of_window_candidate_count"] for row in per_date},
            "top_blocker_reasons": dict(sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:10]),
            "discovery_lanes_used": dict(sorted(lane_counts.items())),
            "sources_with_repeated_useful_hits": dict(sorted(useful_source_hits.items(), key=lambda item: (-item[1], item[0]))[:20]),
            "dates_with_no_reviewable_candidates": dates_with_no_reviewable,
            "dates_with_no_public_eligible_candidates": dates_with_no_public_eligible,
            "dates_with_only_out_of_window_candidates": sorted({row["date"] for row in per_date if bool(row.get("only_out_of_window_candidates"))}),
            "dates_with_only_context_candidates": sorted({row["date"] for row in per_date if bool(row.get("only_context_candidates"))}),
            "configured_lanes": sorted({lane for row in per_date for lane in row.get("configured_lanes", []) if str(lane).strip()}),
            "executed_lanes": sorted({lane for row in per_date for lane in row.get("executed_lanes", []) if str(lane).strip()}),
            "skipped_lanes": sorted({lane for row in per_date for lane in row.get("skipped_lanes", []) if str(lane).strip()}),
            "candidates_by_lane": dict(sorted(Counter({}).items())),
            "candidates_by_discovery_channel": dict(sorted(Counter({}).items())),
            "candidates_by_direct_source": dict(sorted(Counter({}).items())),
            "candidates_by_direct_source_lane": dict(sorted(Counter({}).items())),
            "direct_source_count": sum(int(row.get("direct_source_count", 0)) for row in per_date),
            "direct_source_fetch_attempt_count": sum(int(row.get("direct_source_fetch_attempt_count", 0)) for row in per_date),
            "direct_source_fetch_success_count": sum(int(row.get("direct_source_fetch_success_count", 0)) for row in per_date),
            "direct_source_fetch_failure_count": sum(int(row.get("direct_source_fetch_failure_count", 0)) for row in per_date),
            "direct_article_url_count": sum(int(row.get("direct_article_url_count", 0)) for row in per_date),
            "direct_homepage_or_feed_blocked_count": sum(int(row.get("direct_homepage_or_feed_blocked_count", 0)) for row in per_date),
            "google_news_fallback_count": sum(int(row.get("google_news_fallback_count", 0)) for row in per_date),
            "duplicate_preferred_direct_count": sum(int(row.get("duplicate_preferred_direct_count", 0)) for row in per_date),
            "direct_source_fetch_failure_reasons": dict(sorted(Counter({}).items())),
            "direct_source_candidate_cap_hits": dict(sorted(Counter({}).items())),
            "dominant_source_warning": "; ".join(sorted({str(row.get("dominant_source_warning") or "").strip() for row in per_date if str(row.get("dominant_source_warning") or "").strip()})),
            "google_news_url_count": sum(int(row.get("google_news_url_count", 0)) for row in per_date),
            "google_news_resolution_attempt_count": sum(int(row.get("google_news_resolution_attempt_count", 0)) for row in per_date),
            "google_news_resolution_success_count": sum(int(row.get("google_news_resolution_success_count", 0)) for row in per_date),
            "google_news_resolution_failure_count": sum(int(row.get("google_news_resolution_failure_count", 0)) for row in per_date),
            "google_news_resolved_article_url_count": sum(int(row.get("google_news_resolved_article_url_count", 0)) for row in per_date),
            "google_news_resolved_homepage_only_count": sum(int(row.get("google_news_resolved_homepage_only_count", 0)) for row in per_date),
            "google_news_resolution_status_counts": dict(
                sorted(
                    Counter(
                        status
                        for row in per_date
                        for status, count in (row.get("google_news_resolution_status_counts") or {}).items()
                        for _ in range(int(count))
                    ).items()
                )
            ),
            "canonical_homepage_collapse_ignored_count": sum(int(row.get("canonical_homepage_collapse_ignored_count", 0)) for row in per_date),
            "article_specific_url_count": sum(int(row.get("article_specific_url_count", 0)) for row in per_date),
            "publisher_homepage_trace_only_count": sum(int(row.get("publisher_homepage_trace_only_count", 0)) for row in per_date),
            "unresolved_google_news_count": sum(int(row.get("unresolved_google_news_count", 0)) for row in per_date),
            "blocked_fetch_count": sum(int(row.get("blocked_fetch_count", 0)) for row in per_date),
            "in_window_candidate_count": sum(int(row.get("in_window_candidate_count", 0)) for row in per_date),
            "out_of_window_candidate_count": sum(int(row.get("out_of_window_candidate_count", 0)) for row in per_date),
            "public_eligible_candidate_count": sum(int(row.get("public_eligible_candidate_count", 0)) for row in per_date),
            "per_date": per_date,
            "public_output_written": False,
            "pages_repo_mutated": False,
            "dry_run": dry_run,
            "resume": resume,
            "max_queries": max_queries,
            "max_results_per_query": max_results_per_query,
            "query_lookback_days": query_lookback_days,
            "query_lookahead_days": query_lookahead_days,
            "public_claim_lookback_days": public_claim_lookback_days,
            "public_claim_lookahead_days": public_claim_lookahead_days,
        }
        json_path, html_path = _backfill_summary_paths(root, start_date, end_date)
        summary["candidates_by_lane"] = dict(
            sorted(
                Counter(
                    lane
                    for row in per_date
                    for lane, count in (row.get("candidates_by_lane") or {}).items()
                    for _ in range(int(count))
                ).items()
            )
        )
        summary["candidates_by_discovery_channel"] = dict(
            sorted(
                Counter(
                    channel
                    for row in per_date
                    for channel, count in (row.get("candidates_by_discovery_channel") or {}).items()
                    for _ in range(int(count))
                ).items()
            )
        )
        summary["candidates_by_direct_source"] = dict(
            sorted(
                Counter(
                    source_name
                    for row in per_date
                    for source_name, count in (row.get("candidates_by_direct_source") or {}).items()
                    for _ in range(int(count))
                ).items()
            )
        )
        summary["candidates_by_direct_source_lane"] = dict(
            sorted(
                Counter(
                    source_lane
                    for row in per_date
                    for source_lane, count in (row.get("candidates_by_direct_source_lane") or {}).items()
                    for _ in range(int(count))
                ).items()
            )
        )
        summary["direct_source_fetch_failure_reasons"] = dict(
            sorted(
                Counter(
                    reason
                    for row in per_date
                    for reason, count in (row.get("direct_source_fetch_failure_reasons") or {}).items()
                    for _ in range(int(count))
                ).items()
            )
        )
        summary["direct_source_candidate_cap_hits"] = dict(
            sorted(
                Counter(
                    source_name
                    for row in per_date
                    for source_name, count in (row.get("direct_source_candidate_cap_hits") or {}).items()
                    for _ in range(int(count))
                ).items()
            )
        )
        summary["backfill_summary_json_path"] = str(json_path)
        summary["backfill_summary_html_path"] = str(html_path)
        return summary

    for index, edition_date in enumerate(dates, start=1):
        print(f"[food-line-backfill] {index}/{len(dates)} {edition_date}", flush=True)
        try:
            if resume and _candidate_copy_path(root, edition_date).exists() and _review_json_path(root, edition_date).exists() and not dry_run:
                candidates = _read_json(_candidate_copy_path(root, edition_date))
                intake_payload = {"discovery_review_path": str(_review_json_path(root, edition_date))}
                expansion = {"ok": True, "discovery_audit_json_path": str(_discovery_audit_json_path(root, edition_date))}
                intake = {"ok": True, "discovery_candidates_intaked": len(candidates) if isinstance(candidates, list) else 0}
                status = "resumed_existing"
            else:
                expansion = run_food_line_discovery_expansion(
                    root,
                    edition_date,
                    edition_mode="no_current_update",
                    max_queries=max_queries,
                    max_results_per_query=max_results_per_query,
                    query_lookback_days=query_lookback_days,
                    query_lookahead_days=query_lookahead_days,
                    public_claim_lookback_days=public_claim_lookback_days,
                    public_claim_lookahead_days=public_claim_lookahead_days,
                    dry_run=dry_run,
                )
                intake = run_food_line_discovery_intake_bridge(root, edition_date, dry_run=dry_run)
                candidates, intake_payload = _copy_candidate_artifacts(root, edition_date, intake=intake, dry_run=dry_run)
                status = "completed"
        except Exception as exc:  # noqa: BLE001
            failed_dates.append(edition_date)
            per_date.append(
                {
                    "date": edition_date,
                    "ok": False,
                    "status": "failed",
                    "candidate_count": 0,
                    "traceable_candidate_count": 0,
                    "likely_qualifying_candidate_count": 0,
                    "public_eligible_candidate_count": 0,
                    "direct_source_count": 0,
                    "direct_source_fetch_attempt_count": 0,
                    "direct_source_fetch_success_count": 0,
                    "direct_source_fetch_failure_count": 0,
                    "direct_article_url_count": 0,
                    "direct_homepage_or_feed_blocked_count": 0,
                    "google_news_fallback_count": 0,
                    "duplicate_preferred_direct_count": 0,
                    "watchlist_candidate_count": 0,
                    "rejected_candidate_count": 0,
                    "needs_review_candidate_count": 0,
                    "in_window_candidate_count": 0,
                    "out_of_window_candidate_count": 0,
                    "google_news_url_count": 0,
                    "google_news_resolution_attempt_count": 0,
                    "google_news_resolution_success_count": 0,
                    "google_news_resolution_failure_count": 0,
                    "google_news_resolved_article_url_count": 0,
                    "google_news_resolved_homepage_only_count": 0,
                    "google_news_resolution_status_counts": {},
                    "canonical_homepage_collapse_ignored_count": 0,
                    "article_specific_url_count": 0,
                    "publisher_homepage_trace_only_count": 0,
                    "unresolved_google_news_count": 0,
                    "blocked_fetch_count": 0,
                    "configured_lanes": [],
                    "executed_lanes": [],
                    "skipped_lanes": [],
                    "candidates_by_lane": {},
                    "candidates_by_discovery_channel": {},
                    "candidates_by_direct_source": {},
                    "candidates_by_direct_source_lane": {},
                    "direct_source_fetch_failure_reasons": {},
                    "direct_source_candidate_cap_hits": {},
                    "dominant_source_warning": "",
                    "only_out_of_window_candidates": False,
                    "only_context_candidates": False,
                    "discovery_gap": True,
                    "errors": [str(exc)],
                    "public_output_written": False,
                    "pages_repo_mutated": False,
                }
            )
            _write_partial_summary(root, build_summary(), start_date=start_date, end_date=end_date, dry_run=dry_run)
            continue

        typed_candidates = [row for row in candidates if isinstance(row, dict)]
        review_counts = Counter(str(row.get("candidate_review_status") or row.get("review_status") or "needs_review") for row in typed_candidates)
        traceable_count = sum(1 for row in typed_candidates if str(row.get("traceability_status") or "") == "traceable")
        likely_qualifying_count = sum(
            1
            for row in typed_candidates
            if bool(row.get("public_claim_eligible")) or str(row.get("classification_status") or "") in {"qualified_pressure_signal", "manual_fallback"}
        )
        public_eligible_count = sum(1 for row in typed_candidates if bool(row.get("public_claim_eligible")))
        in_window_count = sum(1 for row in typed_candidates if "outside_backfill_date_window" not in list(row.get("public_claim_blockers") or []))
        out_of_window_count = len(typed_candidates) - in_window_count
        audit = _read_json(Path(expansion["discovery_audit_json_path"])) if expansion.get("discovery_audit_json_path") and Path(expansion["discovery_audit_json_path"]).exists() else {}
        google_news_url_count = sum(1 for row in typed_candidates if str(row.get("google_news_url") or "").strip())
        google_news_resolution_status_counts = dict(audit.get("google_news_resolution_status_counts") or {})
        google_news_resolution_attempt_count = sum(int(v) for v in google_news_resolution_status_counts.values())
        google_news_resolution_success_count = int(google_news_resolution_status_counts.get("success_article", 0))
        google_news_resolution_failure_count = sum(int(v) for k, v in google_news_resolution_status_counts.items() if str(k).startswith("failed_")) + int(google_news_resolution_status_counts.get("success_homepage_only", 0))
        google_news_resolved_article_url_count = int(google_news_resolution_status_counts.get("success_article", 0))
        google_news_resolved_homepage_only_count = int(google_news_resolution_status_counts.get("success_homepage_only", 0))
        canonical_homepage_collapse_ignored_count = sum(1 for row in typed_candidates if bool(row.get("canonical_homepage_collapse_ignored")))
        article_specific_url_count = sum(
            1
            for row in typed_candidates
            if str(row.get("traceability_status") or "").strip() == "traceable"
            and str(row.get("source_url") or row.get("final_trace_url") or "").strip()
        )
        publisher_homepage_trace_only_count = sum(
            1 for row in typed_candidates if "publisher_homepage_trace_only" in list(row.get("public_claim_blockers") or [])
        )
        unresolved_google_news_count = sum(1 for row in typed_candidates if str(row.get("traceability_status") or "").strip() == "unresolved_google_news")
        blocked_fetch_count = sum(1 for row in typed_candidates if str(row.get("fetch_status") or "").strip() not in {"ok", "manual_fallback"})
        direct_source_count = int(audit.get("direct_source_count", 0))
        direct_source_fetch_attempt_count = int(audit.get("direct_source_fetch_attempt_count", 0))
        direct_source_fetch_success_count = int(audit.get("direct_source_fetch_success_count", 0))
        direct_source_fetch_failure_count = int(audit.get("direct_source_fetch_failure_count", 0))
        direct_article_url_count = int(audit.get("direct_article_url_count", 0))
        direct_homepage_or_feed_blocked_count = int(audit.get("direct_homepage_or_feed_blocked_count", 0))
        google_news_fallback_count = int(audit.get("google_news_fallback_count", 0))
        duplicate_preferred_direct_count = int(audit.get("duplicate_preferred_direct_count", 0))
        direct_source_fetch_failure_reasons = dict(audit.get("direct_source_fetch_failure_reasons") or {})
        direct_source_candidate_cap_hits = dict(audit.get("direct_source_candidate_cap_hits") or {})
        dominant_source_warning = str(audit.get("dominant_source_warning") or "").strip()
        for row in typed_candidates:
            lane = str(row.get("discovery_lane") or "").strip()
            if lane:
                lane_counts[lane] += 1
            blockers = [str(item).strip() for item in row.get("public_claim_blockers") or [] if str(item).strip()]
            if not blockers:
                fallback = str(row.get("exclusion_reason") or row.get("classification_status") or "").strip()
                if fallback:
                    blockers = [fallback]
            for blocker in blockers:
                blocker_counts[blocker] += 1
            if likely_qualifying_count > 0 and not str(row.get("duplicate_of") or "").strip():
                publisher = str(row.get("discovered_publisher") or row.get("publisher") or "").strip()
                if publisher:
                    useful_source_hits[publisher] += 1
        if intake.get("discovery_candidates_intaked", 0) <= 0:
            dates_with_no_reviewable.append(edition_date)
        if public_eligible_count <= 0:
            dates_with_no_public_eligible.append(edition_date)
        configured_lanes = list(audit.get("configured_lanes") or [])
        if not configured_lanes:
            query_plan = build_food_line_discovery_query_plan(
                root,
                edition_date,
                lookback_days=query_lookback_days,
                lookahead_days=query_lookahead_days,
            )
            configured_lanes = sorted(
                {
                    "news_article"
                    if str(row.get("query_family") or "").strip() not in {
                        "public_radio",
                        "food_bank_provider",
                        "feeding_america_affiliate",
                        "school_meals_child_nutrition",
                        "county_city_agenda",
                        "snap_state_notice",
                        "united_way_211",
                        "nonprofit_report",
                        "social_watchlist",
                        "institutional_update",
                    }
                    else str(row.get("query_family") or "").strip()
                    for row in query_plan
                    if str(row.get("query_family") or "").strip()
                }
            )
        executed_lanes = list(audit.get("executed_lanes") or [])
        if not executed_lanes:
            executed_lanes = sorted({str(row.get("discovery_lane") or "").strip() for row in typed_candidates if str(row.get("discovery_lane") or "").strip()})
        skipped_lanes = list(audit.get("skipped_lanes") or [])
        if not skipped_lanes:
            skipped_lanes = [lane for lane in configured_lanes if lane not in executed_lanes]
        candidates_by_lane = dict(audit.get("candidates_by_lane") or {})
        if not candidates_by_lane:
            candidates_by_lane = dict(sorted(Counter(str(row.get("discovery_lane") or "").strip() for row in typed_candidates if str(row.get("discovery_lane") or "").strip()).items()))
        candidates_by_discovery_channel = dict(audit.get("candidates_by_discovery_channel") or {})
        if not candidates_by_discovery_channel:
            candidates_by_discovery_channel = dict(sorted(Counter(str(row.get("discovery_channel") or "").strip() for row in typed_candidates if str(row.get("discovery_channel") or "").strip()).items()))
        candidates_by_direct_source = dict(audit.get("candidates_by_direct_source") or {})
        if not candidates_by_direct_source:
            candidates_by_direct_source = dict(sorted(Counter(str(row.get("direct_source_name") or "").strip() for row in typed_candidates if str(row.get("direct_source_name") or "").strip()).items()))
        candidates_by_direct_source_lane = dict(audit.get("candidates_by_direct_source_lane") or {})
        if not candidates_by_direct_source_lane:
            candidates_by_direct_source_lane = dict(
                sorted(
                    Counter(
                        f"{str(row.get('direct_source_name') or '').strip()} | {str(row.get('discovery_lane') or '').strip()}"
                        for row in typed_candidates
                        if str(row.get("direct_source_name") or "").strip() and str(row.get("discovery_lane") or "").strip()
                    ).items()
                )
            )
        only_out_of_window_candidates = bool(typed_candidates) and all(
            "outside_backfill_date_window" in list(row.get("public_claim_blockers") or []) for row in typed_candidates
        )
        only_context_candidates = bool(typed_candidates) and all(
            str(row.get("classification_status") or "").strip() == "context_only" for row in typed_candidates
        )
        per_date.append(
            {
                "date": edition_date,
                "ok": bool(expansion.get("ok")) and bool(intake.get("ok")),
                "status": status,
                "candidate_count": len(typed_candidates),
                "traceable_candidate_count": traceable_count,
                "likely_qualifying_candidate_count": likely_qualifying_count,
                "public_eligible_candidate_count": public_eligible_count,
                "direct_source_count": direct_source_count,
                "direct_source_fetch_attempt_count": direct_source_fetch_attempt_count,
                "direct_source_fetch_success_count": direct_source_fetch_success_count,
                "direct_source_fetch_failure_count": direct_source_fetch_failure_count,
                "direct_article_url_count": direct_article_url_count,
                "direct_homepage_or_feed_blocked_count": direct_homepage_or_feed_blocked_count,
                "google_news_fallback_count": google_news_fallback_count,
                "duplicate_preferred_direct_count": duplicate_preferred_direct_count,
                "direct_source_fetch_failure_reasons": direct_source_fetch_failure_reasons,
                "direct_source_candidate_cap_hits": direct_source_candidate_cap_hits,
                "dominant_source_warning": dominant_source_warning,
                "watchlist_candidate_count": int(review_counts.get("watchlist", 0)),
                "rejected_candidate_count": int(review_counts.get("rejected", 0)),
                "needs_review_candidate_count": int(review_counts.get("needs_review", 0)),
                "in_window_candidate_count": in_window_count,
                "out_of_window_candidate_count": out_of_window_count,
                "google_news_url_count": google_news_url_count,
                "google_news_resolution_attempt_count": google_news_resolution_attempt_count,
                "google_news_resolution_success_count": google_news_resolution_success_count,
                "google_news_resolution_failure_count": google_news_resolution_failure_count,
                "google_news_resolved_article_url_count": google_news_resolved_article_url_count,
                "google_news_resolved_homepage_only_count": google_news_resolved_homepage_only_count,
                "google_news_resolution_status_counts": google_news_resolution_status_counts,
                "canonical_homepage_collapse_ignored_count": canonical_homepage_collapse_ignored_count,
                "article_specific_url_count": article_specific_url_count,
                "publisher_homepage_trace_only_count": publisher_homepage_trace_only_count,
                "unresolved_google_news_count": unresolved_google_news_count,
                "blocked_fetch_count": blocked_fetch_count,
                "discovery_gap": len(typed_candidates) == 0,
                "discovery_candidate_path": str(_candidate_copy_path(root, edition_date)),
                "candidate_review_json_path": str(_review_json_path(root, edition_date)),
                "candidate_review_html_path": str(_review_html_path(root, edition_date)),
                "discovery_audit_path": str(expansion.get("discovery_audit_json_path") or ""),
                "discovery_intake_path": str(intake_payload.get("discovery_review_path") or intake.get("discovery_review_path") or ""),
                "discovery_lanes_used": sorted({str(row.get("discovery_lane") or "") for row in typed_candidates if str(row.get("discovery_lane") or "")}),
                "configured_lanes": configured_lanes,
                "executed_lanes": executed_lanes,
                "skipped_lanes": skipped_lanes,
                "candidates_by_lane": candidates_by_lane,
                "candidates_by_discovery_channel": candidates_by_discovery_channel,
                "candidates_by_direct_source": candidates_by_direct_source,
                "candidates_by_direct_source_lane": candidates_by_direct_source_lane,
                "only_out_of_window_candidates": only_out_of_window_candidates,
                "only_context_candidates": only_context_candidates,
                "top_blocker_reasons": dict(sorted(Counter(blocker for row in typed_candidates for blocker in (row.get("public_claim_blockers") or [])).items(), key=lambda item: (-item[1], item[0]))[:10]),
                "errors": [],
                "public_output_written": False,
                "pages_repo_mutated": False,
            }
        )
        _write_partial_summary(root, build_summary(), start_date=start_date, end_date=end_date, dry_run=dry_run)
    summary = build_summary()
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Food Line discovery intake and review artifacts without publishing public editions.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--max-results-per-query", type=int, default=5)
    parser.add_argument("--query-lookback-days", type=int, default=0)
    parser.add_argument("--query-lookahead-days", type=int, default=0)
    parser.add_argument("--lookback-days", type=int, default=0)
    parser.add_argument("--lookahead-days", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
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
        query_lookback_days=args.query_lookback_days,
        query_lookahead_days=args.query_lookahead_days,
        public_claim_lookback_days=args.lookback_days,
        public_claim_lookahead_days=args.lookahead_days,
        resume=bool(args.resume),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
