from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


DISPATCH_SLUG = "gaza"

CRITICAL_TARGETS = (
    "Reuters",
    "Associated Press",
    "AFP",
    "OCHA oPt",
    "UNICEF",
    "WFP",
    "PRCS / Palestinian Red Crescent",
    "UNRWA",
)

WIRE_TARGETS = {"reuters", "associated press", "afp"}
OFFICIAL_TARGETS = {"ocha opt", "unicef", "wfp"}
MANUAL_TARGETS = {"unrwa"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", " ")
    return " ".join(part for part in text.split() if part)


def _report_paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "output" / "review" / DISPATCH_SLUG / "critical_source_endpoint_research.json",
        root / "output" / "review" / DISPATCH_SLUG / "critical_source_endpoint_research.md",
    )


def _plan_path(root: Path) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / "source_registry_expansion_plan.json"


def _load_plan(root: Path) -> dict[str, Any]:
    plan_path = _plan_path(root)
    if not plan_path.exists():
        raise FileNotFoundError("Run python scripts/plan_gaza_source_registry_expansion.py --date YYYY-MM-DD first.")
    payload = _read_json(plan_path)
    if not isinstance(payload, dict):
        raise ValueError(f"registry expansion plan must be a JSON object: {plan_path}")
    return payload


def _critical_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in list(plan.get("plan_rows") or []) if isinstance(row, dict)]
    by_target = {_normalize_text(row.get("target_name")): row for row in rows}

    missing = [target for target in CRITICAL_TARGETS if _normalize_text(target) not in by_target]
    if missing:
        raise ValueError("critical Gaza sources missing from registry expansion plan: " + ", ".join(missing))

    return [by_target[_normalize_text(target)] for target in CRITICAL_TARGETS]


def _endpoint_research_status(row: dict[str, Any]) -> str:
    current_state = _normalize_text(row.get("current_state"))
    current_status = _normalize_text(row.get("current_status"))
    current_reason = _normalize_text(row.get("current_reason"))
    target_name = _normalize_text(row.get("target_name"))

    if current_state == "manual_only" or target_name in MANUAL_TARGETS:
        return "ready_for_manual_review_only"
    if current_status in {"blocked_endpoint", "blocked", "401", "403"} or "blocked" in current_reason or "forbidden" in current_reason:
        return "blocked_or_forbidden"
    if current_status in {"dead_endpoint", "404"} or "dead" in current_reason or "404" in current_reason:
        return "has_known_dead_endpoint"
    if target_name in WIRE_TARGETS | OFFICIAL_TARGETS or current_status == "missing_from_registry":
        return "needs_live_endpoint_research"
    return "candidate_endpoint_required"


def _manual_workflow_option(row: dict[str, Any], endpoint_status: str) -> str:
    target_name = _normalize_text(row.get("target_name"))
    if endpoint_status == "ready_for_manual_review_only":
        return "Keep the source in manual review/backfill and do not auto-promote it."
    if target_name in WIRE_TARGETS:
        return "Use manual wire monitoring or licensed editorial access; do not depend on brittle public RSS."
    if target_name in OFFICIAL_TARGETS:
        return "Use manual monitoring of the official newsroom or press page while the endpoint is re-researched."
    if target_name == "prcs / palestinian red crescent":
        return "Use manual monitoring of official PRCS communications until a stable endpoint is verified."
    return "Use manual review until a verified endpoint or feed is available."


def _api_or_feed_option(row: dict[str, Any], endpoint_status: str) -> str:
    target_name = _normalize_text(row.get("target_name"))
    current_url = str(row.get("current_url") or "").strip()
    if endpoint_status == "blocked_or_forbidden":
        return "No verified public feed from the checked URL; prefer wire/API/manual access if later confirmed."
    if endpoint_status == "has_known_dead_endpoint":
        return "Replace the dead feed with a verified official updates page, RSS/Atom feed, or API if one exists."
    if target_name in WIRE_TARGETS:
        return "Prefer a licensed wire/API workflow or a verified newsroom feed; the current public endpoint was not verified."
    if target_name in OFFICIAL_TARGETS:
        return "Prefer a stable official updates feed or press-release endpoint once live verification is complete."
    if target_name == "prcs / palestinian red crescent":
        return "No verified API/feed is confirmed yet; endpoint research is required before automation."
    if endpoint_status == "ready_for_manual_review_only":
        return "No automatic feed promotion should occur until a verified feed is researched."
    return "No verified feed option is documented yet."


def _implementation_recommendation(row: dict[str, Any], endpoint_status: str) -> str:
    target_name = str(row.get("target_name") or "").strip()
    current_url = str(row.get("current_url") or "").strip()
    if endpoint_status == "ready_for_manual_review_only":
        return "Keep UNRWA manual-only unless a stable feed is explicitly verified later."
    if endpoint_status == "blocked_or_forbidden":
        return f"Do not enable {target_name} from {current_url or 'the current endpoint'}; research a safe alternate access path or licensed workflow."
    if endpoint_status == "has_known_dead_endpoint":
        return f"Treat {target_name} as a dead endpoint case and search for a verified replacement before any registry change."
    if endpoint_status == "needs_live_endpoint_research":
        return f"Research a live endpoint for {target_name} before registry edits; do not assume the current URL is usable."
    if endpoint_status == "candidate_endpoint_required":
        return f"Identify a candidate endpoint for {target_name} before any automation is considered."
    return f"Keep {target_name} in review until the endpoint status is clarified."


def _candidate_endpoints(row: dict[str, Any]) -> list[dict[str, Any]]:
    current_url = str(row.get("current_url") or "").strip()
    if not current_url:
        return []
    return [
        {
            "candidate_url": current_url,
            "endpoint_checks_performed": False,
            "http_status_code": None,
            "content_type": None,
            "final_url": None,
            "verified": False,
            "notes": "Endpoint verification was not performed in this research report.",
        }
    ]


def _critical_source_record(row: dict[str, Any]) -> dict[str, Any]:
    endpoint_research_status = _endpoint_research_status(row)
    return {
        "source_id": str(row.get("source_id") or "").strip(),
        "target_name": str(row.get("target_name") or "").strip(),
        "publisher": str(row.get("publisher") or "").strip(),
        "priority": str(row.get("priority") or "").strip(),
        "current_state": str(row.get("current_state") or "").strip(),
        "current_status": str(row.get("current_status") or "").strip(),
        "current_url": str(row.get("current_url") or "").strip(),
        "current_reason": str(row.get("current_reason") or "").strip(),
        "recommended_action_from_plan": str(row.get("recommended_action") or "").strip(),
        "endpoint_research_status": endpoint_research_status,
        "candidate_endpoints": _candidate_endpoints(row),
        "manual_workflow_option": _manual_workflow_option(row, endpoint_research_status),
        "api_or_feed_option": _api_or_feed_option(row, endpoint_research_status),
        "robots_or_access_notes": "Endpoint verification was not performed; do not scrape full pages or bypass blocked access in this report.",
        "attribution_policy": str(row.get("attribution_policy") or "").strip(),
        "safe_use_policy": str(row.get("safe_use_policy") or "").strip(),
        "implementation_recommendation": _implementation_recommendation(row, endpoint_research_status),
        "do_not_auto_enable": True,
    }


def build_report(root: Path, edition_date: str) -> dict[str, Any]:
    plan = _load_plan(root)
    critical_rows = [_critical_source_record(row) for row in _critical_rows(plan)]
    counts = Counter(row["endpoint_research_status"] for row in critical_rows)
    counts["critical_source_count"] = len(critical_rows)
    counts["endpoint_checks_performed"] = 0
    counts["do_not_auto_enable_count"] = sum(1 for row in critical_rows if row["do_not_auto_enable"])

    report = {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": _utc_now(),
        "source_registry_expansion_plan_path": str(_plan_path(root)),
        "endpoint_checks_performed": False,
        "critical_sources": critical_rows,
        "summary_counts": dict(counts),
        "recommended_next_actions": [
            "No sources were enabled by this report.",
            "Research live endpoints for AFP and PRCS before making registry changes.",
            "Treat Reuters as blocked or forbidden until a safe wire or access workflow is confirmed.",
            "Replace the dead AP, OCHA oPt, UNICEF, and WFP endpoints with verified official alternatives before automation is considered.",
            "Keep UNRWA manual-review only unless a stable feed is explicitly verified.",
        ],
        "warnings": [
            "Endpoint verification was not performed in this report.",
            "Unknown endpoints remain classified as needs_live_endpoint_research.",
            "This report does not enable any Gaza sources.",
        ],
    }
    json_path, md_path = _report_paths(root)
    report["json_report_path"] = str(json_path)
    report["markdown_report_path"] = str(md_path)
    return report


def _render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_None._"
    header = "| " + " | ".join(label for _key, label in columns) + " |"
    separator = "| " + " | ".join("---" for _key, _label in columns) + " |"
    lines = [header, separator]
    for row in rows:
        values = []
        for key, _label in columns:
            value = row.get(key)
            if isinstance(value, bool):
                text = "yes" if value else "no"
            elif value is None:
                text = ""
            elif isinstance(value, list):
                text = "; ".join(str(item.get("candidate_url") or "") for item in value) if value and isinstance(value[0], dict) else ", ".join(str(item) for item in value)
            else:
                text = str(value)
            values.append(text.replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    rows = list(report.get("critical_sources") or [])
    blocked_rows = [row for row in rows if row.get("endpoint_research_status") == "blocked_or_forbidden"]
    dead_rows = [row for row in rows if row.get("endpoint_research_status") == "has_known_dead_endpoint"]
    research_rows = [row for row in rows if row.get("endpoint_research_status") == "needs_live_endpoint_research"]
    manual_rows = [row for row in rows if row.get("endpoint_research_status") in {"manual_workflow_recommended", "ready_for_manual_review_only"}]
    candidate_rows = rows

    lines = [
        "# Gaza Critical Source Endpoint Research Report",
        "",
        f"- Edition date: `{report.get('edition_date')}`",
        f"- Source registry expansion plan: `{report.get('source_registry_expansion_plan_path')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Endpoint checks performed: `no`",
        "",
        "## Executive Summary",
        "",
        "This report reviews the Gaza critical source set from the registry expansion plan and documents the safest handling path before any registry change is made.",
        "Endpoint verification was not performed, so unknown or unverified endpoints remain classified as `needs_live_endpoint_research`.",
        "No sources were enabled.",
        "",
        "## Critical Sources By Recommended Handling",
        "",
        _render_table(
            rows,
            [
                ("source_id", "source_id"),
                ("target_name", "target_name"),
                ("endpoint_research_status", "endpoint_research_status"),
                ("recommended_action_from_plan", "recommended_action_from_plan"),
                ("manual_workflow_option", "manual_workflow_option"),
            ],
        ),
        "",
        "## Dead Or Blocked Endpoints",
        "",
        _render_table(
            blocked_rows + dead_rows,
            [
                ("source_id", "source_id"),
                ("target_name", "target_name"),
                ("endpoint_research_status", "endpoint_research_status"),
                ("current_url", "current_url"),
                ("current_reason", "current_reason"),
            ],
        ),
        "",
        "## Sources Needing Endpoint Research",
        "",
        _render_table(
            research_rows,
            [
                ("source_id", "source_id"),
                ("target_name", "target_name"),
                ("endpoint_research_status", "endpoint_research_status"),
                ("current_url", "current_url"),
                ("implementation_recommendation", "implementation_recommendation"),
            ],
        ),
        "",
        "## Sources Recommended For Manual/API Workflow",
        "",
        _render_table(
            manual_rows,
            [
                ("source_id", "source_id"),
                ("target_name", "target_name"),
                ("endpoint_research_status", "endpoint_research_status"),
                ("manual_workflow_option", "manual_workflow_option"),
                ("api_or_feed_option", "api_or_feed_option"),
            ],
        ),
        "",
        "## Candidate Endpoints Table",
        "",
        _render_table(
            candidate_rows,
            [
                ("source_id", "source_id"),
                ("target_name", "target_name"),
                ("current_url", "candidate_endpoint"),
                ("endpoint_research_status", "endpoint_research_status"),
                ("manual_workflow_option", "manual_workflow_option"),
                ("api_or_feed_option", "api_or_feed_option"),
            ],
        ),
        "",
        "## Implementation Recommendations",
        "",
    ]
    for action in report.get("recommended_next_actions") or []:
        lines.append(f"- {action}")
    lines.extend(
        [
            "",
            "## No Sources Enabled",
            "",
            "This report is research-only and did not enable, publish, or auto-promote any Gaza sources.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(root: Path, edition_date: str) -> dict[str, Any]:
    report = build_report(root, edition_date)
    json_path, md_path = _report_paths(root)
    _write_json(json_path, report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research the critical Gaza source endpoints without enabling any source.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = write_report(ROOT, args.date)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
