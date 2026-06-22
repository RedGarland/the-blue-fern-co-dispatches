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

from scripts import audit_gaza_source_coverage


DISPATCH_SLUG = "gaza"
OUTPUT_DIR = ROOT / "output" / "review" / DISPATCH_SLUG

PRIORITY_ORDER = ("critical", "high", "medium", "low")

PRIORITY_BY_TARGET = {
    "ocha opt": "critical",
    "unrwa": "critical",
    "wfp": "critical",
    "unicef": "critical",
    "prcs / palestinian red crescent": "critical",
    "reuters": "critical",
    "associated press": "critical",
    "ap": "critical",
    "afp": "critical",
    "msf": "high",
    "doctors without borders / msf": "high",
    "gaza health ministry": "high",
    "cogat / israeli military coordination source": "high",
    "un human rights office": "high",
    "wafa": "high",
    "times of israel": "medium",
    "haaretz": "medium",
    "+972 magazine / local call": "medium",
    "middle east eye": "medium",
    "the new arab": "medium",
}

SAFE_USE_POLICY_BY_CATEGORY = {
    "official_humanitarian": "Use for access, aid, health, displacement, field-response, and institutional updates.",
    "wire_and_major_international": "Use for corroborated current developments and breaking news.",
    "official_claim_source": "Use only with attribution-safe claim labeling; never state as independently verified.",
    "region_specialist": "Use for context and current reporting with opinion/commentary filtering and attribution safeguards.",
    "rights_accountability": "Use for legal, accountability, press-safety, and human-rights developments; do not overstate allegations as adjudicated facts.",
    "manual_supplements": "Use only as operator-reviewed backfill; never as an automatic source lane.",
    "unknown_or_uncategorized": "Use only after the source is mapped to a known category.",
}

ATTRIBUTION_POLICY_BY_CATEGORY = {
    "official_humanitarian": "Preserve publisher attribution and publication date in every public claim.",
    "wire_and_major_international": "Attribute the publisher and preserve the original reporting context.",
    "official_claim_source": "Label as official claims, agency-reported figures, or ministry-reported figures; never present as independently verified.",
    "region_specialist": "Keep publisher attribution visible and avoid mixing analysis, opinion, and fact.",
    "rights_accountability": "Attribute carefully and distinguish allegations, findings, and confirmations.",
    "manual_supplements": "Retain reviewer notes and original URL provenance for every manual record.",
    "unknown_or_uncategorized": "Do not publish until the source role is clarified.",
}

ACTION_ORDER = {
    "already_active": 0,
    "needs_endpoint_research": 1,
    "replace_blocked_endpoint": 2,
    "manual_only": 3,
    "official_claim_source": 4,
    "exclude_with_reason": 5,
}


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
        root / "output" / "review" / DISPATCH_SLUG / "source_registry_expansion_plan.json",
        root / "output" / "review" / DISPATCH_SLUG / "source_registry_expansion_plan.md",
    )


def _coverage_audit_path(root: Path) -> Path:
    return root / "output" / "review" / DISPATCH_SLUG / "source_coverage_audit.json"


def _collection_report_path(root: Path, edition_date: str) -> Path:
    return root / "data" / "dispatches" / DISPATCH_SLUG / "editions" / edition_date / "collection_report.json"


def _load_coverage_audit(root: Path, edition_date: str) -> tuple[dict[str, Any], Path]:
    audit_path = _coverage_audit_path(root)
    if audit_path.exists():
        payload = _read_json(audit_path)
        if not isinstance(payload, dict):
            raise ValueError(f"coverage audit must be a JSON object: {audit_path}")
        return payload, audit_path

    collection_report_path = _collection_report_path(root, edition_date)
    if not collection_report_path.exists():
        raise FileNotFoundError(
            "coverage audit not found and collection report is missing; run "
            f"python scripts/audit_gaza_source_coverage.py --date {edition_date}"
        )

    audit_report = audit_gaza_source_coverage.write_audit_report(root, edition_date)
    if not audit_path.exists():
        raise RuntimeError(f"failed to write coverage audit report: {audit_path}")
    return audit_report, audit_path


def _current_state(row: dict[str, Any]) -> str:
    for key in ("current_state", "source_state"):
        value = _normalize_text(row.get(key))
        if value:
            return value
    return ""


def _current_status(row: dict[str, Any]) -> str:
    for key in ("current_status", "endpoint_status", "status"):
        value = _normalize_text(row.get(key))
        if value:
            return value
    return ""


def _current_reason(row: dict[str, Any]) -> str:
    for key in ("current_reason", "reason", "rejection_reason", "diagnostics_reason", "disabled_reason"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _current_url(row: dict[str, Any]) -> str:
    for key in ("current_url", "url"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _priority_for_row(row: dict[str, Any]) -> str:
    target_name = _normalize_text(row.get("target_name") or row.get("publisher") or row.get("source_id"))
    return PRIORITY_BY_TARGET.get(target_name, "low")


def _safe_use_policy(row: dict[str, Any]) -> str:
    return SAFE_USE_POLICY_BY_CATEGORY.get(_normalize_text(row.get("coverage_category")), SAFE_USE_POLICY_BY_CATEGORY["unknown_or_uncategorized"])


def _attribution_policy(row: dict[str, Any]) -> str:
    return ATTRIBUTION_POLICY_BY_CATEGORY.get(_normalize_text(row.get("coverage_category")), ATTRIBUTION_POLICY_BY_CATEGORY["unknown_or_uncategorized"])


def _recommend_action(row: dict[str, Any]) -> str:
    current_state = _current_state(row)
    current_status = _current_status(row)
    current_reason = _normalize_text(_current_reason(row))
    coverage_category = _normalize_text(row.get("coverage_category"))
    manual_backfill_required = bool(row.get("manual_backfill_required"))

    if current_state == "enabled" and current_status in {"ok", "no_matches"}:
        return "already_active"

    if current_state == "manual_only":
        return "manual_only"

    if current_state == "diagnostics_only":
        if current_status in {"blocked_endpoint", "blocked", "401", "403"} or any(token in current_reason for token in ("blocked", "forbidden", "401", "403")):
            return "replace_blocked_endpoint"
        return "needs_endpoint_research"

    if current_state == "disabled":
        if current_status in {"dead_endpoint", "404"} or any(token in current_reason for token in ("dead", "404")):
            return "needs_endpoint_research"
        if current_status in {"blocked_endpoint", "blocked", "401", "403"} or any(token in current_reason for token in ("blocked", "forbidden", "401", "403")):
            return "replace_blocked_endpoint"
        return "needs_endpoint_research"

    if current_state == "missing_from_registry":
        if coverage_category == "official_claim_source":
            return "official_claim_source"
        if coverage_category in {"region_specialist", "rights_accountability"}:
            return "manual_only" if manual_backfill_required else "needs_endpoint_research"
        if coverage_category in {"official_humanitarian", "wire_and_major_international"}:
            return "needs_endpoint_research"
        return "exclude_with_reason"

    if current_status in {"blocked_endpoint", "blocked", "401", "403"}:
        return "replace_blocked_endpoint"

    if current_status in {"dead_endpoint", "404"}:
        return "needs_endpoint_research"

    return "exclude_with_reason"


def _why_it_matters(row: dict[str, Any], action: str) -> str:
    risk = str(row.get("risk_if_missing") or "").strip()
    if action == "already_active":
        return "The source is already active; keep it unchanged unless the endpoint health changes."
    if action == "manual_only":
        return risk or "This source should stay in human-review lanes."
    if action == "official_claim_source":
        return risk or "This source can be used only with attribution-safe claim handling."
    if action in {"needs_endpoint_research", "replace_blocked_endpoint"}:
        return risk or "The registry should not be changed until the endpoint is understood."
    if action == "exclude_with_reason":
        return risk or "The source is not suitable for automatic Gaza registry expansion."
    return risk or "This source needs further review."


def _candidate_endpoint_notes(row: dict[str, Any], action: str) -> str:
    url = _current_url(row)
    current_reason = _current_reason(row)
    if action == "already_active":
        return f"Current endpoint is usable; keep monitoring {url or 'the existing source'}."
    if action == "manual_only":
        return f"Keep this source in manual review/backfill. {current_reason}".strip()
    if action == "official_claim_source":
        return f"Treat as an attribution-sensitive claim source if added. {current_reason}".strip()
    if action == "replace_blocked_endpoint":
        return f"Current endpoint is blocked or forbidden; search for an alternate endpoint or access path. {current_reason}".strip()
    if action == "needs_endpoint_research":
        return f"Search for a stable RSS, Atom, press-release, or official updates endpoint before enabling. {current_reason}".strip()
    return f"Do not auto-enable. {current_reason}".strip()


def _implementation_notes(row: dict[str, Any], action: str) -> str:
    if action == "already_active":
        return "Leave the registry entry as-is and monitor for status drift."
    if action == "needs_endpoint_research":
        return "Research a stable endpoint or archival route; do not enable until the source is verified."
    if action == "replace_blocked_endpoint":
        return "Replace the blocked URL with a working endpoint or keep it disabled with a documented reason."
    if action == "manual_only":
        return "Keep this source in manual review or backfill workflows only."
    if action == "official_claim_source":
        return "If added later, require attribution safeguards and label reported figures as claims."
    return "Exclude this source from automatic registry expansion unless the scope changes."


def _normalize_plan_row(row: dict[str, Any]) -> dict[str, Any]:
    action = _recommend_action(row)
    current_state = _current_state(row)
    current_status = _current_status(row)
    plan_row = {
        "source_id": str(row.get("source_id") or row.get("matched_source_id") or "").strip(),
        "target_name": str(row.get("target_name") or row.get("publisher") or row.get("source_id") or "").strip(),
        "publisher": str(row.get("publisher") or "").strip(),
        "coverage_category": str(row.get("coverage_category") or "").strip(),
        "reliability_role": str(row.get("reliability_role") or row.get("coverage_category") or "").strip(),
        "current_state": current_state,
        "current_status": current_status,
        "current_reason": _current_reason(row),
        "current_url": _current_url(row),
        "checked_each_run": bool(row.get("checked_each_run")) if row.get("checked_each_run") is not None else current_state == "enabled",
        "manual_backfill_required": bool(row.get("manual_backfill_required")),
        "risk_if_missing": str(row.get("risk_if_missing") or "").strip(),
        "recommended_action": action,
        "priority": _priority_for_row(row),
        "why_it_matters": _why_it_matters(row, action),
        "safe_use_policy": _safe_use_policy(row),
        "attribution_policy": _attribution_policy(row),
        "candidate_endpoint_notes": _candidate_endpoint_notes(row, action),
        "implementation_notes": _implementation_notes(row, action),
        "do_not_auto_enable": True,
    }
    return plan_row


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            PRIORITY_ORDER.index(str(row.get("priority") or "low")) if str(row.get("priority") or "low") in PRIORITY_ORDER else len(PRIORITY_ORDER),
            ACTION_ORDER.get(str(row.get("recommended_action") or ""), 99),
            str(row.get("target_name") or row.get("source_id") or ""),
        ),
    )


def _priority_groups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {priority: [] for priority in PRIORITY_ORDER}
    for row in rows:
        priority = str(row.get("priority") or "low")
        grouped.setdefault(priority, []).append(row)
    return {
        priority: {
            "count": len(grouped.get(priority, [])),
            "target_names": [row["target_name"] for row in grouped.get(priority, [])],
        }
        for priority in PRIORITY_ORDER
    }


def _recommended_next_actions(rows: list[dict[str, Any]]) -> list[str]:
    grouped = _priority_groups(rows)
    critical = grouped["critical"]["target_names"]
    high = grouped["high"]["target_names"]
    manual_only = [row["target_name"] for row in rows if row["recommended_action"] == "manual_only"]
    research = [row["target_name"] for row in rows if row["recommended_action"] in {"needs_endpoint_research", "replace_blocked_endpoint"}]
    already_active = [row["target_name"] for row in rows if row["recommended_action"] == "already_active"]

    actions = [
        "This plan is review-only and does not enable any new Gaza sources.",
    ]
    if critical:
        actions.append("Fix critical sources first: " + ", ".join(critical) + ".")
    if high:
        actions.append("Move next on high-priority sources: " + ", ".join(high) + ".")
    if research:
        actions.append("Research or replace the blocked and dead endpoints before making registry changes: " + ", ".join(research) + ".")
    if manual_only:
        actions.append("Keep manual-only sources in human-review workflows: " + ", ".join(manual_only) + ".")
    if already_active:
        actions.append("Leave already-active sources unchanged unless endpoint health changes: " + ", ".join(already_active) + ".")
    return actions


def build_plan(root: Path, edition_date: str) -> dict[str, Any]:
    audit_report, audit_path = _load_coverage_audit(root, edition_date)
    target_rows = [row for row in list(audit_report.get("target_source_coverage") or []) if isinstance(row, dict)]
    plan_rows = _sort_rows([_normalize_plan_row(row) for row in target_rows])
    warnings = list(audit_report.get("warnings") or [])
    warnings.append("This plan is review-only and does not enable any new sources.")
    warnings.append(f"{sum(1 for row in plan_rows if row['recommended_action'] == 'already_active')} sources are already active and should be left unchanged.")
    warnings = list(dict.fromkeys(warnings))

    report = {
        "ok": True,
        "dispatch_slug": DISPATCH_SLUG,
        "edition_date": edition_date,
        "generated_at": _utc_now(),
        "coverage_audit_path": str(audit_path),
        "plan_rows": plan_rows,
        "priority_groups": _priority_groups(plan_rows),
        "recommended_next_actions": _recommended_next_actions(plan_rows),
        "warnings": warnings,
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
            else:
                text = str(value)
            values.append(text.replace("|", "\\|"))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    rows = list(report.get("plan_rows") or [])

    critical_rows = [row for row in rows if row.get("priority") == "critical" and row.get("recommended_action") != "already_active"]
    research_rows = [row for row in rows if row.get("recommended_action") in {"needs_endpoint_research", "replace_blocked_endpoint"}]
    manual_rows = [row for row in rows if row.get("recommended_action") == "manual_only"]
    claim_rows = [row for row in rows if row.get("recommended_action") == "official_claim_source"]
    active_rows = [row for row in rows if row.get("recommended_action") == "already_active"]
    exclude_rows = [row for row in rows if row.get("recommended_action") == "exclude_with_reason"]

    lines = [
        "# Gaza Source Registry Expansion Plan",
        "",
        f"- Edition date: `{report.get('edition_date')}`",
        f"- Coverage audit: `{report.get('coverage_audit_path')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Review only: `yes`",
    ]
    if report.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend([f"- {warning}" for warning in report["warnings"]])

    lines.extend(
        [
            "",
            "## Executive Summary",
            "",
            "This plan classifies reliable Gaza source targets from the coverage audit and recommends the safest next action for each one. It does not enable any sources automatically.",
            "",
            "## Critical Sources To Fix First",
            "",
            _render_table(
                critical_rows,
                [
                    ("source_id", "source_id"),
                    ("target_name", "target_name"),
                    ("current_state", "current_state"),
                    ("current_status", "current_status"),
                    ("recommended_action", "recommended_action"),
                    ("why_it_matters", "why_it_matters"),
                ],
            ),
            "",
            "## Sources That Need Endpoint Research",
            "",
            _render_table(
                [row for row in research_rows if row.get("recommended_action") == "needs_endpoint_research"],
                [
                    ("source_id", "source_id"),
                    ("target_name", "target_name"),
                    ("current_state", "current_state"),
                    ("current_status", "current_status"),
                    ("recommended_action", "recommended_action"),
                    ("candidate_endpoint_notes", "candidate_endpoint_notes"),
                ],
            ),
            "",
            "## Sources That Should Remain Manual-Only",
            "",
            _render_table(
                manual_rows,
                [
                    ("source_id", "source_id"),
                    ("target_name", "target_name"),
                    ("current_state", "current_state"),
                    ("current_status", "current_status"),
                    ("recommended_action", "recommended_action"),
                    ("implementation_notes", "implementation_notes"),
                ],
            ),
            "",
            "## Official-Claim Sources Requiring Attribution Safeguards",
            "",
            _render_table(
                claim_rows,
                [
                    ("source_id", "source_id"),
                    ("target_name", "target_name"),
                    ("current_state", "current_state"),
                    ("current_status", "current_status"),
                    ("recommended_action", "recommended_action"),
                    ("attribution_policy", "attribution_policy"),
                ],
            ),
            "",
            "## Already Active Sources",
            "",
            _render_table(
                active_rows,
                [
                    ("source_id", "source_id"),
                    ("target_name", "target_name"),
                    ("current_state", "current_state"),
                    ("current_status", "current_status"),
                    ("recommended_action", "recommended_action"),
                    ("safe_use_policy", "safe_use_policy"),
                ],
            ),
            "",
            "## Sources Recommended For Exclusion",
            "",
            _render_table(
                exclude_rows,
                [
                    ("source_id", "source_id"),
                    ("target_name", "target_name"),
                    ("current_state", "current_state"),
                    ("current_status", "current_status"),
                    ("recommended_action", "recommended_action"),
                    ("candidate_endpoint_notes", "candidate_endpoint_notes"),
                ],
            ),
            "",
            "## Concrete Next Implementation Steps",
            "",
        ]
    )
    for action in report.get("recommended_next_actions") or []:
        lines.append(f"- {action}")
    return "\n".join(lines) + "\n"


def write_plan_report(root: Path, edition_date: str) -> dict[str, Any]:
    report = build_plan(root, edition_date)
    json_path, md_path = _report_paths(root)
    _write_json(json_path, report)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Gaza source registry expansion plan from the coverage audit.")
    parser.add_argument("--date", required=True, help="Edition date in YYYY-MM-DD format.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = write_plan_report(ROOT, args.date)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
