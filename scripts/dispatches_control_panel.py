from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

DISPATCHES = ("Gaza", "Cascadia", "American Pressure")
ACTIONS = (
    "Run dispatch",
    "Run with notification",
    "Publish Pages locally, no push",
    "Run dashboard",
    "Run doctor",
)

STATUS_BANNER_OK = "OK to review"
STATUS_BANNER_WARN = "Needs attention"
STATUS_BANNER_STOP = "Do not publish"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def python_executable(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def validate_date(date_text: str) -> bool:
    try:
        date_cls.fromisoformat(date_text)
    except ValueError:
        return False
    return len(date_text) == 10


def manual_source_path(dispatch: str, date_text: str, root: Path | None = None) -> Path | None:
    base = root or project_root()
    key = dispatch.strip().lower()
    if key == "gaza":
        return base / "data" / "dispatches" / "gaza" / "sources" / date_text / "manual_sources.json"
    if key == "american pressure":
        return base / "data" / "dispatches" / "american-pressure" / "sources" / date_text / "manual_sources.json"
    return None


def expected_output_paths(dispatch: str, date_text: str, root: Path | None = None) -> list[Path]:
    base = root or project_root()
    slug = dispatch.lower().replace(" ", "-")
    return [
        base / "output" / "dispatches" / slug / "editions" / date_text,
        base / "output" / "site" / slug / "editions" / date_text,
    ]


def public_url(dispatch: str, date_text: str) -> str:
    slug = dispatch.lower().replace(" ", "-")
    return f"https://dispatches.thebluefernco.com/{slug}/editions/{date_text}/"


def _pages_publish_command(root: Path) -> list[str]:
    return [
        str(python_executable(root)),
        "scripts\\publish_github_pages.py",
        "--pages-repo",
        str(root / "bluefern-dispatches-pages"),
        "--pages-branch",
        "gh-pages",
        "--remote-url",
        "https://github.com/RedGarland/the-blue-fern-co-dispatches.git",
        "--commit",
        "--no-push",
    ]


def build_command(
    dispatch: str,
    action: str,
    date_text: str,
    options: dict[str, Any] | None = None,
    root: Path | None = None,
) -> list[str]:
    opts = options or {}
    base = root or project_root()
    py = str(python_executable(base))

    if action == "Run dashboard":
        cmd = [py, "scripts\\dispatches_status.py"]
        if opts.get("status_json"):
            cmd.append("--json")
        return cmd
    if action == "Run doctor":
        return [py, "scripts\\doctor.py"]
    if action == "Publish Pages locally, no push":
        return _pages_publish_command(base)

    if not validate_date(date_text):
        raise ValueError("Date must be YYYY-MM-DD")

    if dispatch == "Gaza":
        if action == "Run dispatch":
            cmd = [
                py,
                "scripts\\run_gaza_dispatch.py",
                "--date",
                date_text,
                "--historical",
                "--from-manual-sources",
                "--all",
            ]
        elif action == "Run with notification":
            cmd = [
                py,
                "scripts\\run_and_notify.py",
                "--date",
                date_text,
                "--publish",
            ]
        else:
            raise ValueError(f"Unsupported action for Gaza: {action}")
    elif dispatch == "Cascadia":
        if action == "Run dispatch":
            cmd = [
                py,
                "scripts\\run_cascadia_dispatch.py",
                "--date",
                date_text,
                "--weekly-public",
                "--historical-search",
                "--historical-provider",
                "all",
            ]
        elif action == "Run with notification":
            cmd = [
                py,
                "scripts\\run_cascadia_and_notify.py",
                "--date",
                date_text,
                "--publish",
            ]
        else:
            raise ValueError(f"Unsupported action for Cascadia: {action}")
    elif dispatch == "American Pressure":
        if action == "Run dispatch":
            cmd = [
                py,
                "scripts\\run_american_pressure_dispatch.py",
                "--date",
                date_text,
                "--from-manual-sources",
                "--publish",
            ]
        elif action == "Run with notification":
            cmd = [
                py,
                "scripts\\run_american_pressure_and_notify.py",
                "--date",
                date_text,
                "--publish",
            ]
        else:
            raise ValueError(f"Unsupported action for American Pressure: {action}")
    else:
        raise ValueError(f"Unsupported dispatch: {dispatch}")

    if opts.get("dry_run"):
        if dispatch in ("Gaza", "Cascadia") and "Run dispatch" == action:
            cmd.append("--dry-run")
    return cmd


def _sanitize_line(text: str) -> str:
    if "SMTP_PASSWORD" not in text:
        return text
    return text.replace("SMTP_PASSWORD", "[REDACTED_KEY]")


def run_command_streaming(
    command: list[str],
    cwd: Path,
    on_line: Callable[[str], None],
    on_done: Callable[[int], None],
) -> subprocess.Popen[str]:
    merged_env = os.environ.copy()
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        env=merged_env,
    )

    def _reader() -> None:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            on_line(_sanitize_line(raw_line.rstrip("\n")))
        proc.wait()
        on_done(proc.returncode)

    threading.Thread(target=_reader, daemon=True).start()
    return proc


def load_status_json(root: Path) -> dict[str, Any]:
    cmd = [str(python_executable(root)), "scripts\\dispatches_status.py", "--json"]
    completed = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, check=False)
    payload: dict[str, Any]
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "critical_errors": ["Could not parse status JSON"],
            "warnings": [completed.stderr.strip()] if completed.stderr.strip() else [],
        }
    payload.setdefault("_status_exit_code", completed.returncode)
    return payload


def _severity_blocked() -> str:
    return "Blocked"


def _severity_review() -> str:
    return "Review"


def _severity_growth() -> str:
    return "Growth"


def _severity_ok() -> str:
    return "OK"


def _severity_info() -> str:
    return "Informational"


def _merge_severity(*levels: str) -> str:
    order = {_severity_ok(): 0, _severity_info(): 1, _severity_growth(): 2, _severity_review(): 3, _severity_blocked(): 4}
    return max(levels, key=lambda item: order.get(item, 0))


def _status_color(level: str) -> str:
    if level == _severity_blocked():
        return "red"
    if level == _severity_review():
        return "#b58900"
    if level == _severity_info():
        return "#5b6f8f"
    return "green"


def _sanitize(value: Any) -> Any:
    if isinstance(value, str) and "SMTP_PASSWORD" in value:
        return "[REDACTED]"
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


def _count_substrings(items: list[Any], tokens: tuple[str, ...]) -> int:
    count = 0
    for item in items:
        text = str(item).lower()
        if any(token in text for token in tokens):
            count += 1
    return count


def summarize_warning_counts(status_json: dict[str, Any]) -> dict[str, int]:
    cascadia = ((status_json.get("dispatches") or {}).get("cascadia") or {})
    cascadia_warn = list(cascadia.get("latest_manifest_warnings") or [])
    weak_direct = cascadia.get("weak_date_warning_count")
    registry_direct = cascadia.get("registry_fetch_error_count")
    gdelt_direct = cascadia.get("gdelt_timeout_rate_limit_count")
    return {
        "weak_date_warning_count": int(weak_direct) if isinstance(weak_direct, int) else _count_substrings(cascadia_warn, ("weak", "date")),
        "registry_fetch_error_count": int(registry_direct) if isinstance(registry_direct, int) else _count_substrings(cascadia_warn, ("registry", "fetch", "http", "dns", "403", "404")),
        "gdelt_timeout_rate_limit_count": int(gdelt_direct) if isinstance(gdelt_direct, int) else _count_substrings(cascadia_warn, ("gdelt", "timeout", "rate limit", "429")),
    }


def classify_health(status_json: dict[str, Any]) -> dict[str, Any]:
    critical = list(status_json.get("critical_errors") or [])
    warnings = list(status_json.get("warnings") or [])
    pages = status_json.get("pages_repo") or {}
    project = status_json.get("project") or {}
    safety = status_json.get("public_safety") or {}
    dispatches = status_json.get("dispatches") or {}
    gaza = dispatches.get("gaza") or {}
    cascadia = dispatches.get("cascadia") or {}
    american = dispatches.get("american_pressure") or {}
    gap = cascadia.get("latest_weekly_gap_report") or {}
    warning_counts = summarize_warning_counts(status_json)
    fetch_rate = gap.get("successful_fetch_rate")
    if isinstance(fetch_rate, str):
        try:
            fetch_rate = float(fetch_rate)
        except ValueError:
            fetch_rate = None

    flags = {
        "do_not_publish": not bool(status_json.get("ok", False)),
        "has_critical_errors": bool(critical),
        "has_warnings": bool(warnings),
        "source_changes": bool(project.get("has_source_test_doc_changes")),
        "generated_runtime_dirt": bool(project.get("has_generated_runtime_dirt")),
        "pages_dirty": pages.get("clean") is False,
        "pages_missing_or_wrong": (not bool(pages.get("exists"))) or pages.get("branch") != "gh-pages" or not bool(pages.get("cname_ok")),
        "output_site_detail_exists": bool(safety.get("output_site_detail_exists")),
        "output_site_paid_exists": bool(safety.get("output_site_paid_exists")),
        "smtp_password_in_logs": bool(safety.get("smtp_password_in_logs")),
        "gaza_zero_source_linked": bool(gaza.get("public_linked_zero_source_dates")),
        "gaza_zero_story_linked": bool(gaza.get("public_linked_zero_story_dates")),
        "gaza_dedupe_refusal_linked": bool(gaza.get("public_linked_dedupe_refusal_dates")),
        "gaza_repeated_urls": bool(gaza.get("repeated_source_urls_recent")),
        "cascadia_fetch_rate_low": isinstance(fetch_rate, (int, float)) and float(fetch_rate) < 0.75,
        "cascadia_weak_date_warnings": warning_counts["weak_date_warning_count"] > 0,
        "manual_source_missing_ap": not bool(american.get("latest_manual_source_exists_for_latest_public_edition")),
        "gaza_stale_unlinked_folders": bool(gaza.get("stale_or_unlinked_edition_dates")),
        "gaza_undercollection_review": False,
        "ap_coverage_gaps_present": bool(_ap_coverage_gaps((american.get("registry_summary") or {}).get("enabled_by_pillar") or {})),
    }
    gaza_collection = gaza.get("latest_collection_report") or {}
    if isinstance(gaza_collection, dict) and gaza_collection:
        raw_candidates = int(gaza_collection.get("raw_candidate_count") or 0)
        kept_candidates = int(gaza_collection.get("kept_after_dedupe") or 0)
        provider_failures = list(gaza_collection.get("provider_failures") or [])
        flags["gaza_undercollection_review"] = raw_candidates == 0 or kept_candidates == 0 or bool(provider_failures)
    gaza_health = gaza.get("latest_source_health_report") or {}
    if isinstance(gaza_health, dict) and gaza_health:
        enabled = int(gaza_health.get("providers_enabled") or 0)
        failed = int(gaza_health.get("providers_failed") or 0)
        if enabled > 0 and failed >= max(1, (enabled + 1) // 2):
            flags["gaza_undercollection_review"] = True

    blocked_reasons: list[str] = []
    review_reasons: list[str] = []
    growth_reasons: list[str] = []
    info_reasons: list[str] = []
    if flags["do_not_publish"] or flags["has_critical_errors"]:
        blocked_reasons.append("Status reports blocked publish conditions.")
    if flags["output_site_detail_exists"] or flags["output_site_paid_exists"]:
        blocked_reasons.append("Public safety check failed: output/site/detail or output/site/paid exists.")
    if flags["smtp_password_in_logs"]:
        blocked_reasons.append("SMTP_PASSWORD appears in logs.")
    if flags["pages_missing_or_wrong"]:
        blocked_reasons.append("Pages repo missing or invalid branch/CNAME state.")
    if flags["gaza_zero_source_linked"] or flags["gaza_zero_story_linked"] or flags["gaza_dedupe_refusal_linked"]:
        blocked_reasons.append("Gaza linked public edition has zero-source/zero-story/dedupe-refusal issue.")
    if flags["has_warnings"]:
        review_reasons.append("General warnings are present.")
    if flags["gaza_repeated_urls"]:
        review_reasons.append("Gaza older archive entries have repeated source URLs.")
    if flags["gaza_undercollection_review"]:
        review_reasons.append("Gaza public archive is clean, but collection health indicates possible under-collection.")
    if flags["cascadia_fetch_rate_low"]:
        pct = int(float(fetch_rate) * 100) if isinstance(fetch_rate, (int, float)) else 0
        review_reasons.append(f"Cascadia fetch success rate is {pct}%, below 75% target.")
    if flags["cascadia_weak_date_warnings"]:
        review_reasons.append("Cascadia has weak-date warning noise.")
    if flags["source_changes"]:
        review_reasons.append("Source repo has source/test/doc changes.")
    if flags["pages_dirty"]:
        review_reasons.append("Pages repo has uncommitted changes.")
    if flags["manual_source_missing_ap"]:
        review_reasons.append("American Pressure latest manual source is missing.")
    if flags["ap_coverage_gaps_present"]:
        growth_reasons.append("American Pressure source coverage expansion is needed for missing pillars.")
    if flags["gaza_stale_unlinked_folders"]:
        info_reasons.append("Gaza has stale/unlinked generated folders; these are not public archive entries unless linked.")
    if flags["generated_runtime_dirt"] and not flags["source_changes"]:
        info_reasons.append("Generated/runtime dirt exists; no commit needed unless source files changed.")

    if blocked_reasons:
        overall = _severity_blocked()
    elif review_reasons or growth_reasons:
        overall = _severity_review()
    elif info_reasons:
        overall = _severity_info()
    else:
        overall = _severity_ok()

    return {
        "flags": flags,
        "warning_counts": warning_counts,
        "fetch_rate": fetch_rate,
        "blocked_reasons": blocked_reasons,
        "review_reasons": review_reasons,
        "growth_reasons": growth_reasons,
        "info_reasons": info_reasons,
        "overall": overall,
    }


def build_publish_decision(status_json: dict[str, Any]) -> str:
    health = classify_health(status_json)
    if health["overall"] == _severity_blocked():
        return "Publishing is blocked: resolve blocking safety issues first."
    if health["overall"] == _severity_review():
        if health["flags"]["gaza_repeated_urls"] and health["flags"]["cascadia_fetch_rate_low"]:
            return "Publishing is allowed, but review Gaza older-archive duplicate URLs and Cascadia source-quality warnings."
        return "Publishing is allowed, but review warning items first."
    return "Publishing is allowed: no blocking safety issues found."


def build_recommendations(status_json: dict[str, Any]) -> list[dict[str, str]]:
    health = classify_health(status_json)
    items: list[dict[str, str]] = []
    for text in health["blocked_reasons"]:
        items.append({"severity": _severity_blocked(), "text": text})
    review_priority = [
        "Cascadia fetch success rate",
        "Cascadia has weak-date warning noise",
        "American Pressure latest manual source is missing",
        "Source repo has source/test/doc changes",
        "Pages repo has uncommitted changes",
        "Gaza older archive entries have repeated source URLs",
        "General warnings are present",
    ]
    for key in review_priority:
        for text in health["review_reasons"]:
            if key in text:
                items.append({"severity": _severity_review(), "text": text})
    for text in health["review_reasons"]:
        if not any(item["text"] == text for item in items):
            items.append({"severity": _severity_review(), "text": text})
    for text in health["growth_reasons"]:
        items.append({"severity": _severity_growth(), "text": text})
    for text in health["info_reasons"]:
        items.append({"severity": _severity_info(), "text": text})
    if not health["blocked_reasons"]:
        items.insert(0, {"severity": _severity_ok(), "text": "No blocking issues found."})
    order = {_severity_blocked(): 0, _severity_review(): 1, _severity_growth(): 2, _severity_info(): 3, _severity_ok(): 4}
    return sorted(items, key=lambda i: order[i["severity"]])[:5]


def _ap_coverage_gaps(enabled_by_pillar: dict[str, Any]) -> list[str]:
    required = ("household_cost_pressure", "local_system_strain", "policy_implementation")
    gaps = []
    for pillar in required:
        if int(enabled_by_pillar.get(pillar, 0) or 0) == 0:
            gaps.append(pillar)
    return gaps


def build_health_cards(status_json: dict[str, Any]) -> dict[str, Any]:
    dispatches = status_json.get("dispatches") or {}
    pages = status_json.get("pages_repo") or {}
    project = status_json.get("project") or {}
    gaza = dispatches.get("gaza") or {}
    cascadia = dispatches.get("cascadia") or {}
    american = dispatches.get("american_pressure") or {}
    gap = cascadia.get("latest_weekly_gap_report") or {}
    health = classify_health(status_json)
    enabled_by_pillar = (american.get("registry_summary") or {}).get("enabled_by_pillar") or {}
    ap_gaps = _ap_coverage_gaps(enabled_by_pillar)

    source_status = _severity_review() if health["flags"]["source_changes"] else _severity_ok()
    pages_status = _severity_blocked() if health["flags"]["pages_missing_or_wrong"] else _severity_review() if health["flags"]["pages_dirty"] else _severity_ok()
    runtime_status = _severity_info() if health["flags"]["generated_runtime_dirt"] and not health["flags"]["source_changes"] else _severity_review() if health["flags"]["generated_runtime_dirt"] else _severity_ok()

    cards = {
        "source_pages": {
            "status": _merge_severity(source_status, pages_status, runtime_status),
            "source_repo_line": "Source Repo: OK — no source/test/doc changes." if source_status == _severity_ok() else "Source Repo: Needs Review — source/test/doc changes exist.",
            "pages_repo_line": "Pages Repo: OK — clean, up-to-date, CNAME valid." if pages_status == _severity_ok() else "Pages Repo: Needs Review — verify cleanliness/branch/CNAME before publish.",
            "runtime_line": "Runtime Artifacts: Info — generated output/log dirt exists; no commit needed." if runtime_status == _severity_info() else "Runtime Artifacts: OK.",
            "branch": pages.get("branch"),
            "sha": pages.get("head_short_sha"),
            "tracking": pages.get("tracking"),
            "clean": pages.get("clean"),
            "cname": pages.get("cname_value"),
            "source_changes": project.get("has_source_test_doc_changes"),
            "generated_runtime_dirt": project.get("has_generated_runtime_dirt"),
        },
        "gaza": {
            "status": _severity_blocked() if health["flags"]["gaza_zero_source_linked"] or health["flags"]["gaza_zero_story_linked"] or health["flags"]["gaza_dedupe_refusal_linked"] else _severity_review() if health["flags"]["gaza_repeated_urls"] or health["flags"]["gaza_undercollection_review"] else _severity_ok(),
            "latest_public_edition_date": gaza.get("latest_public_edition_date"),
            "newest_generated_folder_date": gaza.get("latest_pages_edition_date"),
            "sources": gaza.get("latest_source_count"),
            "stories": gaza.get("latest_story_count"),
            "archive_exists": gaza.get("archive_exists"),
            "rss_exists": gaza.get("rss_exists"),
            "visible_source_links": gaza.get("latest_has_visible_source_links"),
            "public_url": gaza.get("latest_public_url"),
            "main_issue": "Older public archive entries share repeated source URLs." if health["flags"]["gaza_repeated_urls"] else ("Collection may be underpowered despite clean public archive." if health["flags"]["gaza_undercollection_review"] else "No current blocking Gaza issue."),
            "impact": "Current latest Gaza edition is source-backed." if (gaza.get("latest_source_count") or 0) > 0 else "Latest edition needs source review.",
            "next_action": "Review older duplicate URLs when convenient, or run focused historical cleanup." if health["flags"]["gaza_repeated_urls"] else ("Review Gaza source providers and latest collection report diagnostics." if health["flags"]["gaza_undercollection_review"] else "No immediate Gaza action needed."),
            "public_archive_dates": gaza.get("public_archive_dates") or [],
            "stale_or_unlinked_edition_dates": gaza.get("stale_or_unlinked_edition_dates") or [],
            "repeated_source_url_count": len(gaza.get("repeated_source_urls_recent") or {}),
            "zero_source_linked_dates": gaza.get("public_linked_zero_source_dates") or [],
            "zero_story_linked_dates": gaza.get("public_linked_zero_story_dates") or [],
            "dedupe_refusal_linked_dates": gaza.get("public_linked_dedupe_refusal_dates") or [],
            "latest_collection_report": gaza.get("latest_collection_report") or {},
        },
        "cascadia": {
            "status": _severity_review() if health["flags"]["cascadia_fetch_rate_low"] or health["flags"]["cascadia_weak_date_warnings"] else _severity_ok(),
            "latest_weekly_edition_date": cascadia.get("latest_weekly_edition_date"),
            "latest_public_edition_date": cascadia.get("latest_public_edition_date"),
            "latest_pages_edition_date": cascadia.get("latest_pages_edition_date"),
            "sources": cascadia.get("latest_source_count"),
            "stories": cascadia.get("latest_story_count"),
            "archive_exists": cascadia.get("archive_exists"),
            "rss_exists": cascadia.get("rss_exists"),
            "visible_source_links": cascadia.get("latest_has_visible_source_links"),
            "public_url": cascadia.get("latest_public_url"),
            "fetch_success_rate": health["fetch_rate"],
            "target_fetch_success_rate": cascadia.get("target_fetch_success_rate", 0.75),
            "source_checks_attempted": gap.get("source_checks_attempted"),
            "source_checks_successful": gap.get("source_checks_successful"),
            "final_public_story_count": gap.get("final_public_story_count"),
            "candidate_story_pool_count": cascadia.get("latest_story_count"),
            "weak_date_warning_count": health["warning_counts"]["weak_date_warning_count"],
            "registry_fetch_error_count": health["warning_counts"]["registry_fetch_error_count"],
            "gdelt_timeout_rate_limit_count": health["warning_counts"]["gdelt_timeout_rate_limit_count"],
            "main_issue": "Discovery works, but source reliability needs cleanup." if (health["flags"]["cascadia_fetch_rate_low"] or health["flags"]["cascadia_weak_date_warnings"]) else "No blocking Cascadia issue.",
            "next_action": "Disable/deprioritize dead registry sources and reduce weak-date warning noise." if (health["flags"]["cascadia_fetch_rate_low"] or health["flags"]["cascadia_weak_date_warnings"]) else "No immediate Cascadia action needed.",
        },
        "american_pressure": {
            "status": _severity_review() if health["flags"]["manual_source_missing_ap"] else _severity_ok(),
            "latest_public_edition_date": american.get("latest_public_edition_date"),
            "latest_pages_edition_date": american.get("latest_pages_edition_date"),
            "sources": american.get("latest_source_count"),
            "stories": american.get("latest_story_count"),
            "archive_exists": american.get("archive_exists"),
            "rss_exists": american.get("rss_exists"),
            "visible_source_links": american.get("latest_has_visible_source_links"),
            "public_url": american.get("latest_public_url"),
            "latest_manual_source_date": american.get("latest_manual_source_date"),
            "manual_source_present": american.get("latest_manual_source_exists_for_latest_public_edition"),
            "registry_enabled": (american.get("registry_summary") or {}).get("enabled_sources"),
            "registry_total": (american.get("registry_summary") or {}).get("total_sources"),
            "enabled_by_pillar": enabled_by_pillar,
            "coverage_gaps": ap_gaps,
            "bad_fns_link_hits": american.get("bad_fns_hits_in_active_output") or [],
            "next_action": "Add one household-cost source next." if ap_gaps else "No immediate American Pressure action needed.",
        },
    }
    return cards


def _select_codex_prompt_goal(status_json: dict[str, Any]) -> tuple[str, str]:
    health = classify_health(status_json)
    cards = build_health_cards(status_json)
    if health["flags"]["has_critical_errors"]:
        issue = (status_json.get("critical_errors") or ["first critical error"])[0]
        return "Critical error remediation.", "critical"
    if (
        health["flags"]["gaza_zero_source_linked"]
        or health["flags"]["gaza_zero_story_linked"]
        or health["flags"]["gaza_dedupe_refusal_linked"]
    ):
        return "Gaza duplicate/public-listing cleanup.", "gaza_public_linked_issue"
    if health["flags"]["gaza_undercollection_review"]:
        return "Gaza source collection / under-collection fix.", "gaza_undercollection"
    if health["flags"]["cascadia_fetch_rate_low"] or health["warning_counts"]["registry_fetch_error_count"] > 0 or health["warning_counts"]["weak_date_warning_count"] > 0:
        return "Cascadia source reliability cleanup.", "cascadia_reliability"
    if cards["american_pressure"]["coverage_gaps"]:
        return "American Pressure source coverage expansion.", "american_pressure_coverage"
    if health["flags"]["source_changes"]:
        return "Source/test/doc commit review checklist.", "source_review"
    return "General maintenance/status cleanup.", "maintenance"


def suggested_prompt_title(status_json: dict[str, Any]) -> str:
    title, _template = _select_codex_prompt_goal(status_json)
    return title


def generate_codex_prompt(status_json: dict[str, Any]) -> str:
    health = classify_health(status_json)
    cards = build_health_cards(status_json)
    title, template_key = _select_codex_prompt_goal(status_json)
    cascadia = cards.get("cascadia", {})
    gaza = cards.get("gaza", {})
    ap = cards.get("american_pressure", {})
    warnings = health.get("warning_counts", {})
    gaza_collection = ((status_json.get("dispatches") or {}).get("gaza") or {}).get("latest_collection_report") or {}
    gaza_raw_candidates = gaza_collection.get("raw_candidate_count")
    gaza_kept_candidates = gaza_collection.get("kept_after_dedupe")
    fetch_rate = cascadia.get("fetch_success_rate")
    fetch_rate_pct = f"{int(float(fetch_rate) * 100)}%" if isinstance(fetch_rate, (int, float)) else "n/a"
    fetch_target = cascadia.get("target_fetch_success_rate")
    fetch_target_pct = f"{int(float(fetch_target) * 100)}%" if isinstance(fetch_target, (int, float)) else "75%"

    common_rules = "\n".join(
        [
            "- Read docs/project-contract.md first.",
            "- Do not violate it.",
            "- Do not push.",
            "- Do not use git add .",
            "- Report files changed.",
            "- Do not expose secrets.",
            "- Do not commit generated output/logs/runtime artifacts.",
            "- Run focused tests, full pytest, doctor, and dispatches_status.py.",
        ]
    )
    common_validation = "\n".join(
        [
            "- $bt = \"$env:TEMP\\bluefern-pytest-\" + (Get-Date -Format \"yyyyMMdd-HHmmss\")",
            "- .\\.venv\\Scripts\\python.exe -B -m pytest -q -p no:cacheprovider --basetemp \"$bt\"",
            "- .\\.venv\\Scripts\\python.exe scripts\\doctor.py",
            "- .\\.venv\\Scripts\\python.exe scripts\\dispatches_status.py",
        ]
    )

    template_map: dict[str, str] = {
        "cascadia_reliability": f"""Goal
Improve Cascadia source reliability and reduce warning noise while preserving source-backed weekly output.

Current status
- Latest public edition: {cascadia.get('latest_public_edition_date')}
- Source/story count: {cascadia.get('sources')} / {cascadia.get('stories')}
- Source checks attempted/successful: {cascadia.get('source_checks_attempted')} / {cascadia.get('source_checks_successful')}
- Fetch success rate: {fetch_rate_pct} (target {fetch_target_pct})
- Weak-date warning count: {warnings.get('weak_date_warning_count')}
- Registry fetch error count: {warnings.get('registry_fetch_error_count')}
- GDELT timeout/rate-limit count: {warnings.get('gdelt_timeout_rate_limit_count')}
- Current recommendation: {cascadia.get('next_action')}

Core rules
{common_rules}

Files likely involved
- scripts/dispatches_status.py
- scripts/dispatches_control_panel.py
- src/bluefern_dispatches/cascadia_ingest.py
- src/bluefern_dispatches/cascadia_normalize.py
- src/bluefern_dispatches/cascadia_source_registry.py
- tests/test_cascadia_pipeline.py
- tests/test_dispatches_status.py
- tests/test_dispatches_control_panel.py

Requirements
- Audit source reliability.
- Summarize registry fetch errors by source_id/status.
- Identify repeated 403/404/DNS/TLS/timeout failures.
- Mark dead sources disabled or diagnostics-only where appropriate.
- Aggregate weak-date warnings.
- Do not weaken validation.
- Do not treat retrieved_at as published_at.
- Preserve source-backed weekly output.
- Update dashboard/control panel if status fields change.

Tests
- Add/update deterministic tests for reliability summary and prompt content.

Validation commands
- $bt = \"$env:TEMP\\bluefern-pytest-\" + (Get-Date -Format \"yyyyMMdd-HHmmss\")
- .\\.venv\\Scripts\\python.exe -B -m pytest tests\\test_cascadia_pipeline.py tests\\test_dispatches_status.py tests\\test_dispatches_control_panel.py -q -p no:cacheprovider --basetemp \"$bt\"
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, key reliability decisions, test results, and whether anything was staged/committed/pushed.
""",
        "gaza_undercollection": f"""Goal
Fix Gaza under-collection before dedupe/render and improve diagnostics.

Current status
- Latest public edition: {gaza.get('latest_public_edition_date')}
- Source/story count: {gaza.get('sources')} / {gaza.get('stories')}
- repeated source URL count: {gaza.get('repeated_source_url_count')}
- Zero-source linked dates: {len(gaza.get('zero_source_linked_dates') or [])}
- Zero-story linked dates: {len(gaza.get('zero_story_linked_dates') or [])}
- Dedupe-refusal linked dates: {len(gaza.get('dedupe_refusal_linked_dates') or [])}
- Collection health: raw={gaza_raw_candidates}, kept={gaza_kept_candidates}

Core rules
{common_rules}

Files likely involved
- src/bluefern_dispatches/gaza_sources.py
- scripts/run_gaza_dispatch.py
- scripts/backfill_gaza_dispatch.py
- tests/test_gaza_sources.py
- tests/test_gaza_dispatch_generation.py
- tests/test_gaza_backfill.py
- tests/test_dispatches_status.py
- tests/test_dispatches_control_panel.py

Requirements
- Fix under-collection before dedupe.
- Do not relax dedupe.
- Expand or diagnose providers.
- Preserve canonical URL handling.
- Write collection diagnostics.
- No zero-source public editions.
- Test fresh fixture items and duplicate suppression.

Tests
- Add/update deterministic fixture tests for freshness, dedupe suppression, and zero-source safe failure.

Validation commands
- $bt = \"$env:TEMP\\bluefern-pytest-\" + (Get-Date -Format \"yyyyMMdd-HHmmss\")
- .\\.venv\\Scripts\\python.exe -B -m pytest tests\\test_gaza_sources.py tests\\test_run_daily_gaza.py tests\\test_gaza_dispatch_generation.py tests\\test_gaza_backfill.py tests\\test_dispatches_status.py tests\\test_dispatches_control_panel.py -q -p no:cacheprovider --basetemp \"$bt\"
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, provider/diagnostic updates, test results, and whether anything was staged/committed/pushed.
""",
        "gaza_public_linked_issue": f"""Goal
Remove/exclude duplicate/zero-source/dedupe-refused Gaza dates from public archive/index/RSS while preserving valid editions.

Current status
- Latest public edition: {gaza.get('latest_public_edition_date')}
- Source/story count: {gaza.get('sources')} / {gaza.get('stories')}
- repeated source URL count: {gaza.get('repeated_source_url_count')}
- Zero-source linked dates: {len(gaza.get('zero_source_linked_dates') or [])}
- Zero-story linked dates: {len(gaza.get('zero_story_linked_dates') or [])}
- Dedupe-refusal linked dates: {len(gaza.get('dedupe_refusal_linked_dates') or [])}

Core rules
{common_rules}

Files likely involved
- scripts/run_gaza_dispatch.py
- scripts/backfill_gaza_dispatch.py
- src/bluefern_dispatches/gaza_sources.py
- tests/test_gaza_dispatch_generation.py
- tests/test_gaza_backfill.py
- tests/test_dispatches_status.py
- tests/test_dispatches_control_panel.py

Requirements
- Remove or exclude duplicate/zero-source/dedupe-refused dates from public archive/RSS/index.
- Preserve valid source-backed editions.
- Treat stale folders as informational when unlinked.
- Do not invent sources and do not republish duplicate editions.

Tests
- Add/update deterministic tests for linked vs unlinked stale folders and public listability.

Validation commands
- $bt = \"$env:TEMP\\bluefern-pytest-\" + (Get-Date -Format \"yyyyMMdd-HHmmss\")
- .\\.venv\\Scripts\\python.exe -B -m pytest tests\\test_gaza_dispatch_generation.py tests\\test_gaza_backfill.py tests\\test_dispatches_status.py tests\\test_dispatches_control_panel.py -q -p no:cacheprovider --basetemp \"$bt\"
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, date-listability outcomes, test results, and whether anything was staged/committed/pushed.
""",
        "american_pressure_coverage": f"""Goal
Expand American Pressure source coverage for missing pillars with reliable, source-backed workflow.

Current status
- Latest public edition: {ap.get('latest_public_edition_date')}
- Source/story count: {ap.get('sources')} / {ap.get('stories')}
- Registry enabled/total: {ap.get('registry_enabled')} / {ap.get('registry_total')}
- Enabled by pillar: {ap.get('enabled_by_pillar')}
- Missing pillar coverage: {ap.get('coverage_gaps')}
- Latest manual source date: {ap.get('latest_manual_source_date')}

Core rules
{common_rules}

Files likely involved
- data/dispatches/american-pressure/source_registry.yml
- scripts/run_american_pressure_dispatch.py
- src/bluefern_dispatches/american_pressure_sources.py
- tests/test_american_pressure_sources.py
- tests/test_american_pressure_dispatch.py
- tests/test_dispatches_status.py
- tests/test_dispatches_control_panel.py

Requirements
- Expand registry coverage for missing pillars.
- Prioritize household_cost_pressure first.
- Do not auto-create public claims from registry only.
- Require manual source records for editions.
- Add source-health validation if needed.

Tests
- Add/update deterministic tests for missing-pillar coverage and source-health checks.

Validation commands
- $bt = \"$env:TEMP\\bluefern-pytest-\" + (Get-Date -Format \"yyyyMMdd-HHmmss\")
- .\\.venv\\Scripts\\python.exe -B -m pytest tests\\test_american_pressure_sources.py tests\\test_american_pressure_dispatch.py tests\\test_dispatches_status.py tests\\test_dispatches_control_panel.py -q -p no:cacheprovider --basetemp \"$bt\"
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, coverage expansion details, test results, and whether anything was staged/committed/pushed.
""",
        "source_review": f"""Goal
Review source/test/doc changes and prepare a safe commit checklist.

Current status
- Source/test/doc changes are present in the working tree.

Core rules
{common_rules}

Files likely involved
- src/
- scripts/
- tests/
- docs/
- assets/

Requirements
- Classify source/test/doc changes vs generated/runtime dirt.
- Ensure no generated output/log/runtime artifacts are included.
- Provide exact commit checklist and verification steps.

Tests
- Run relevant focused tests for changed areas.

Validation commands
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, safe commit scope, test results, and whether anything was staged/committed/pushed.
""",
        "maintenance": f"""Goal
General maintenance/status cleanup.

Current status
- No active blocking issue selected; perform routine maintenance validation.

Core rules
{common_rules}

Files likely involved
- scripts/dispatches_status.py
- scripts/doctor.py
- scripts/dispatches_control_panel.py
- tests/test_dispatches_status.py
- tests/test_dispatches_control_panel.py

Requirements
- Verify status/doctor outputs are clean.
- Tighten diagnostics and prompt quality where useful.
- Keep safety/reporting behavior unchanged.

Tests
- Add/update deterministic tests for any behavior changes.

Validation commands
- $bt = \"$env:TEMP\\bluefern-pytest-\" + (Get-Date -Format \"yyyyMMdd-HHmmss\")
- .\\.venv\\Scripts\\python.exe -B -m pytest tests\\test_dispatches_status.py tests\\test_dispatches_control_panel.py -q -p no:cacheprovider --basetemp \"$bt\"
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, validation results, and whether anything was staged/committed/pushed.
""",
        "critical": f"""Goal
Remediate the first blocking critical error from dashboard status.

Current status
- First critical error: {(status_json.get('critical_errors') or ['n/a'])[0]}

Core rules
{common_rules}

Files likely involved
- scripts/dispatches_status.py
- scripts/dispatches_control_panel.py
- tests/test_dispatches_status.py
- tests/test_dispatches_control_panel.py

Requirements
- Fix the first blocking critical error safely.
- Preserve all existing safety constraints.
- Keep public/detail separation and no-secret behavior.

Tests
- Add/update deterministic tests for the critical condition.

Validation commands
{common_validation}

Git rules
- Do not push.
- Do not use git add .

Final response required
- Report files changed, critical issue resolution, test results, and whether anything was staged/committed/pushed.
""",
    }
    body = template_map.get(template_key, template_map["maintenance"])
    return f"{title}\n\n{body}"


def summarize_status_for_gui(status: dict[str, Any]) -> dict[str, Any]:
    health = classify_health(status)
    cards = build_health_cards(status)
    recs = build_recommendations(status)
    overview = status.get("project") or {}
    pages = status.get("pages_repo") or {}
    critical = list(status.get("critical_errors") or [])
    warnings = list(status.get("warnings") or [])

    summary = {
        "health_summary": {
            "overall_status": health["overall"],
            "overall_label": (
                "Stable, with review items"
                if health["overall"] == _severity_review()
                else "Blocked by safety issues"
                if health["overall"] == _severity_blocked()
                else "Stable, informational notes only"
                if health["overall"] == _severity_info()
                else "Healthy"
            ),
            "publish_status_label": (
                "Blocked"
                if health["overall"] == _severity_blocked()
                else "Allowed"
            ),
            "blocking_issues_count": len(health["blocked_reasons"]),
            "review_items_count": len(health["review_reasons"]),
            "growth_items_count": len(health["growth_reasons"]),
            "housekeeping_items_count": len(health["info_reasons"]),
            "source_repo": _severity_review() if health["flags"]["source_changes"] else _severity_ok(),
            "pages_repo": _severity_blocked() if health["flags"]["pages_missing_or_wrong"] else _severity_review() if health["flags"]["pages_dirty"] else _severity_ok(),
            "gaza": cards["gaza"]["status"],
            "cascadia": cards["cascadia"]["status"],
            "american_pressure": cards["american_pressure"]["status"],
        },
        "publish_decision": build_publish_decision(status),
        "overview": {
            "ok": bool(status.get("ok", False)),
            "critical_error_count": len(critical),
            "warning_count": len(warnings),
            "project_root": overview.get("root"),
            "source_repo_branch": overview.get("branch"),
            "source_repo_head_short_sha": overview.get("head_short_sha"),
            "source_repo_tracking": overview.get("tracking"),
            "source_changes": overview.get("has_source_test_doc_changes"),
            "generated_runtime_dirt": overview.get("has_generated_runtime_dirt"),
            "python_executable": overview.get("python"),
            "status_timestamp": overview.get("timestamp"),
        },
        "pages_source_card": cards["source_pages"],
        "dispatch_cards": {
            "gaza": cards["gaza"],
            "cascadia": cards["cascadia"],
            "american_pressure": cards["american_pressure"],
        },
        "what_needs_attention": recs,
        "attention_sections": {
            "blocked": [item["text"] for item in recs if item.get("severity") == _severity_blocked()],
            "review": [item["text"] for item in recs if item.get("severity") == _severity_review()],
            "growth": [item["text"] for item in recs if item.get("severity") == _severity_growth()],
            "housekeeping": [item["text"] for item in recs if item.get("severity") == _severity_info()],
            "ok": [item["text"] for item in recs if item.get("severity") == _severity_ok()],
        },
        "recommendations": [item["text"] for item in recs],
        "flags": health["flags"],
        "warning_counts": health["warning_counts"],
        "suggested_codex_prompt_title": suggested_prompt_title(status),
        "suggested_codex_prompt": generate_codex_prompt(status),
        "raw_details": {
            "critical_errors": critical,
            "warnings": warnings,
            "status_json_excerpt": {
                "ok": status.get("ok"),
                "critical_errors": critical,
                "warnings": warnings,
                "project": overview,
                "pages_repo": pages,
                "public_safety": status.get("public_safety") or {},
                "dispatches": status.get("dispatches") or {},
            },
        },
    }
    return _sanitize(summary)


def open_path(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        return False
    return True


def _format_dispatch_card(name: str, card: dict[str, Any]) -> str:
    lines = [
        f"{name} [{card.get('status', 'OK')}]",
        f"- Latest public edition: {card.get('latest_public_edition_date')}",
        f"- Sources/Stories: {card.get('sources')} / {card.get('stories')}",
        f"- Archive/RSS: {card.get('archive_exists')} / {card.get('rss_exists')}",
        f"- Visible source links: {card.get('visible_source_links')}",
        f"- Public URL: {card.get('public_url')}",
        f"- Main issue: {card.get('main_issue')}",
        f"- Next action: {card.get('next_action')}",
    ]
    if name == "Gaza":
        lines.extend(
            [
                f"- Newest generated folder: {card.get('newest_generated_folder_date')}",
                f"- Public archive dates: {len(card.get('public_archive_dates') or [])}",
                f"- Stale/unlinked folders (not public archive entries): {len(card.get('stale_or_unlinked_edition_dates') or [])}",
                f"- Repeated source URL count: {card.get('repeated_source_url_count')}",
                f"- Zero-source linked dates: {len(card.get('zero_source_linked_dates') or [])}",
                f"- Zero-story linked dates: {len(card.get('zero_story_linked_dates') or [])}",
                f"- Dedupe-refusal linked dates: {len(card.get('dedupe_refusal_linked_dates') or [])}",
            ]
        )
    if name == "Cascadia":
        rate = card.get("fetch_success_rate")
        target = card.get("target_fetch_success_rate")
        rate_text = f"{int(float(rate) * 100)}%" if isinstance(rate, (int, float)) else "not available"
        target_text = f"{int(float(target) * 100)}%" if isinstance(target, (int, float)) else "not available"
        lines.extend(
            [
                f"- Weekly edition: {card.get('latest_weekly_edition_date')}",
                f"- Source checks attempted/successful: {card.get('source_checks_attempted')} / {card.get('source_checks_successful')}",
                f"- Fetch success rate: {rate_text} / target {target_text}",
                f"- Public story count: {card.get('final_public_story_count')}",
                f"- Candidate/accepted pool: {card.get('candidate_story_pool_count')}",
                f"- Weak-date warning count: {card.get('weak_date_warning_count')}",
                f"- Registry fetch error count: {card.get('registry_fetch_error_count')}",
                f"- GDELT timeout/rate-limit count: {card.get('gdelt_timeout_rate_limit_count')}",
            ]
        )
    if name == "American Pressure":
        gaps = card.get("coverage_gaps") or []
        lines.extend(
            [
                f"- Latest manual source date: {card.get('latest_manual_source_date')}",
                "- Manual source exists for latest public edition: "
                f"{card.get('manual_source_present')}",
                f"- Registry enabled/total: {card.get('registry_enabled')} / {card.get('registry_total')}",
                f"- Enabled by pillar: {card.get('enabled_by_pillar')}",
                f"- Source coverage gaps: {', '.join(gaps) if gaps else 'none'}",
                f"- Bad FNS link hits: {len(card.get('bad_fns_link_hits') or [])}",
            ]
        )
    return "\n".join(lines)


def format_main_summary_text(summary: dict[str, Any]) -> str:
    health = summary.get("health_summary", {})
    pages_source = summary.get("pages_source_card", {})
    cards = summary.get("dispatch_cards", {})
    lines = [
        "Health Summary",
        f"- Overall: {health.get('overall_label')}",
        f"- Publish status: {health.get('publish_status_label')}",
        f"- Blocking issues: {health.get('blocking_issues_count')}",
        f"- Review items: {health.get('review_items_count')}",
        f"- Growth items: {health.get('growth_items_count')}",
        f"- Housekeeping items: {health.get('housekeeping_items_count')}",
        f"- Source repo: {health.get('source_repo')}",
        f"- Pages repo: {health.get('pages_repo')}",
        f"- Gaza: {health.get('gaza')}",
        f"- Cascadia: {health.get('cascadia')}",
        f"- American Pressure: {health.get('american_pressure')}",
        "",
        f"Can I publish? {summary.get('publish_decision')}",
        "",
        "Pages/Source Card",
        f"- {pages_source.get('source_repo_line')}",
        f"- {pages_source.get('pages_repo_line')}",
        f"- {pages_source.get('runtime_line')}",
        f"- Branch/SHA: {pages_source.get('branch')} / {pages_source.get('sha')}",
        f"- Tracking/Clean/CNAME: {pages_source.get('tracking')} / {pages_source.get('clean')} / {pages_source.get('cname')}",
        "",
        _format_dispatch_card("Gaza", cards.get("gaza") or {}),
        "",
        _format_dispatch_card("Cascadia", cards.get("cascadia") or {}),
        "",
        _format_dispatch_card("American Pressure", cards.get("american_pressure") or {}),
        "",
        "What Needs Attention",
    ]
    sections = summary.get("attention_sections", {})
    blocked_items = sections.get("blocked") or []
    review_items = sections.get("review") or []
    growth_items = sections.get("growth") or []
    housekeeping_items = sections.get("housekeeping") or []
    lines.append("- Review")
    if review_items:
        for text in review_items:
            lines.append(f"  - {text}")
    else:
        lines.append("  - none")
    lines.append("- Growth")
    if growth_items:
        for text in growth_items:
            lines.append(f"  - {text}")
    else:
        lines.append("  - none")
    lines.append("- Housekeeping")
    if housekeeping_items:
        for text in housekeeping_items:
            lines.append(f"  - {text}")
    else:
        lines.append("  - none")
    if blocked_items:
        lines.append("- Blocked")
        for text in blocked_items:
            lines.append(f"  - {text}")
    lines.extend(
        [
            "",
            "Suggested Codex Prompt (preview)",
            summary.get("suggested_codex_prompt_title", ""),
        ]
    )
    return "\n".join(lines)


def format_raw_details_text(summary: dict[str, Any]) -> str:
    raw = summary.get("raw_details", {})
    return json.dumps(raw, indent=2)


@dataclass
class RunState:
    process: subprocess.Popen[str] | None = None
    running: bool = False


class DispatchesControlPanel:
    def __init__(self, root_win: tk.Tk):
        self.root_win = root_win
        self.root_win.title("Dispatches Control Panel")
        self.root_win.geometry("1200x760")

        self.root_dir = project_root()
        self.run_state = RunState()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.dispatch_var = tk.StringVar(value=DISPATCHES[0])
        self.action_var = tk.StringVar(value=ACTIONS[0])
        self.date_var = tk.StringVar(value=date_cls.today().isoformat())
        self.open_after_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.publish_toggle_var = tk.BooleanVar(value=True)

        self.status_banner_var = tk.StringVar(value=STATUS_BANNER_WARN)
        self.raw_details_visible = tk.BooleanVar(value=False)
        self.execution_var = tk.StringVar(value="Ready")
        self.command_var = tk.StringVar(value="")

        self._build_ui()
        self._poll_ui_queue()

    def _build_ui(self) -> None:
        tabs = ttk.Notebook(self.root_win)
        tabs.pack(fill=tk.BOTH, expand=True)

        run_tab = ttk.Frame(tabs)
        stats_tab = ttk.Frame(tabs)
        logs_tab = ttk.Frame(tabs)

        tabs.add(run_tab, text="Run Dispatches")
        tabs.add(stats_tab, text="Statistics / Health")
        tabs.add(logs_tab, text="Logs / Output")

        self._build_run_tab(run_tab)
        self._build_stats_tab(stats_tab)
        self._build_logs_tab(logs_tab)

    def _build_run_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="Dispatch").grid(row=0, column=0, sticky="w")
        ttk.Combobox(top, values=DISPATCHES, textvariable=self.dispatch_var, state="readonly", width=22).grid(
            row=0, column=1, padx=6, sticky="w"
        )

        ttk.Label(top, text="Date (YYYY-MM-DD)").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.date_var, width=16).grid(row=0, column=3, padx=6, sticky="w")

        ttk.Label(top, text="Action").grid(row=1, column=0, sticky="w")
        ttk.Combobox(top, values=ACTIONS, textvariable=self.action_var, state="readonly", width=30).grid(
            row=1, column=1, padx=6, sticky="w"
        )

        ttk.Checkbutton(top, text="Open output page after success", variable=self.open_after_var).grid(
            row=1, column=2, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(top, text="Dry-run if supported", variable=self.dry_run_var).grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(top, text="Publish toggle (where applicable)", variable=self.publish_toggle_var).grid(
            row=2, column=2, columnspan=2, sticky="w"
        )

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=10)
        self.execute_btn = ttk.Button(btn_row, text="Execute", command=self.execute_action)
        self.execute_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_row, text="Stop (best effort)", command=self.stop_action, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Clear Output", command=self.clear_output).pack(side=tk.LEFT, padx=6)

        open_row = ttk.Frame(frame)
        open_row.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(open_row, text="Open local dispatch archive", command=self.open_archive).pack(side=tk.LEFT)
        ttk.Button(open_row, text="Open latest local edition", command=self.open_latest_edition).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open source folder", command=self.open_source_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open log folder", command=lambda: self._open(self.root_dir / "logs")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open output/site", command=lambda: self._open(self.root_dir / "output" / "site")).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            open_row,
            text="Open Pages repo folder",
            command=lambda: self._open(self.root_dir / "bluefern-dispatches-pages"),
        ).pack(side=tk.LEFT, padx=6)

        ttk.Label(frame, textvariable=self.execution_var, foreground="#333366").pack(anchor="w", padx=10)
        ttk.Label(frame, textvariable=self.command_var, foreground="#444444", wraplength=1150).pack(anchor="w", padx=10)

        self.output_text = ScrolledText(frame, height=22)
        self.output_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _build_stats_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)

        self.banner_label = ttk.Label(top, textvariable=self.status_banner_var)
        self.banner_label.pack(side=tk.LEFT)
        ttk.Button(top, text="Refresh Statistics", command=self.refresh_status).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Copy status summary", command=self.copy_status_summary).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Show Raw Details", command=self.show_raw_details).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Hide Raw Details", command=self.hide_raw_details).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Generate Codex Prompt", command=self.generate_codex_prompt_ui).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Copy Codex Prompt", command=self.copy_codex_prompt).pack(side=tk.LEFT, padx=8)

        open_row = ttk.Frame(frame)
        open_row.pack(fill=tk.X, padx=10, pady=6)
        ttk.Button(open_row, text="Open latest Gaza edition", command=lambda: self.open_dispatch_latest("gaza")).pack(side=tk.LEFT)
        ttk.Button(open_row, text="Open latest Cascadia edition", command=lambda: self.open_dispatch_latest("cascadia")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open latest American Pressure edition", command=lambda: self.open_dispatch_latest("american-pressure")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open Gaza archive", command=lambda: self._open(self.root_dir / "output" / "site" / "gaza" / "archive.html")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open Cascadia archive", command=lambda: self._open(self.root_dir / "output" / "site" / "cascadia" / "archive.html")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open American Pressure archive", command=lambda: self._open(self.root_dir / "output" / "site" / "american-pressure" / "archive.html")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open output/site", command=lambda: self._open(self.root_dir / "output" / "site")).pack(side=tk.LEFT, padx=6)
        ttk.Button(open_row, text="Open Pages repo", command=lambda: self._open(self.root_dir / "bluefern-dispatches-pages")).pack(side=tk.LEFT, padx=6)

        self.stats_text = ScrolledText(frame, height=22)
        self.stats_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.raw_details_text = ScrolledText(frame, height=10)
        self.prompt_text = ScrolledText(frame, height=8)

    def _build_logs_tab(self, frame: ttk.Frame) -> None:
        ttk.Label(frame, text="Live command output is mirrored from Run Dispatches tab.").pack(anchor="w", padx=10, pady=10)
        self.logs_text = ScrolledText(frame, height=32)
        self.logs_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _poll_ui_queue(self) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                self._append_output(str(payload))
            elif kind == "done":
                self._on_command_done(int(payload))
        self.root_win.after(100, self._poll_ui_queue)

    def _append_output(self, line: str) -> None:
        self.output_text.insert(tk.END, line + "\n")
        self.output_text.see(tk.END)
        self.logs_text.insert(tk.END, line + "\n")
        self.logs_text.see(tk.END)

    def execute_action(self) -> None:
        dispatch = self.dispatch_var.get()
        action = self.action_var.get()
        date_text = self.date_var.get().strip()

        if action in ("Run dispatch", "Run with notification") and not validate_date(date_text):
            messagebox.showerror("Invalid date", "Date must be in YYYY-MM-DD format.")
            return

        warnings = self._preflight_warnings(dispatch, action, date_text)
        if warnings:
            messagebox.showwarning("Preflight warnings", "\n".join(warnings))

        opts = {
            "dry_run": self.dry_run_var.get(),
            "publish": self.publish_toggle_var.get(),
            "status_json": action == "Run dashboard",
        }
        try:
            cmd = build_command(dispatch, action, date_text, opts, root=self.root_dir)
        except ValueError as exc:
            messagebox.showerror("Unsupported selection", str(exc))
            return

        cmd_str = subprocess.list2cmdline(cmd)
        self.command_var.set(f"Command: {cmd_str}")
        self.execution_var.set(
            f"Running dispatch={dispatch} action={action} date={date_text}"
        )
        self._append_output(f"$ {cmd_str}")

        self.run_state.running = True
        self.execute_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

        self.run_state.process = run_command_streaming(
            cmd,
            self.root_dir,
            on_line=lambda line: self.ui_queue.put(("line", line)),
            on_done=lambda rc: self.ui_queue.put(("done", rc)),
        )

    def _preflight_warnings(self, dispatch: str, action: str, date_text: str) -> list[str]:
        notes: list[str] = []
        if dispatch == "Cascadia" and action in ("Run dispatch", "Run with notification"):
            notes.append("Cascadia runs weekly-public historical-search workflow for selected date.")
        if dispatch in ("Gaza", "American Pressure") and action in ("Run dispatch", "Run with notification"):
            path = manual_source_path(dispatch, date_text, root=self.root_dir)
            if path is not None and not path.exists():
                notes.append(f"Missing manual sources: {path}")
        return notes

    def _on_command_done(self, returncode: int) -> None:
        self.run_state.running = False
        self.execute_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        status = "success" if returncode == 0 else "failure"
        self.execution_var.set(f"Finished with exit code={returncode} ({status})")
        self._append_output(f"[exit code] {returncode}")

        if returncode == 0 and self.open_after_var.get():
            d = self.dispatch_var.get()
            dt = self.date_var.get().strip()
            self._open(self.root_dir / "output" / "site" / d.lower().replace(" ", "-") / "editions" / dt / "index.html")

    def stop_action(self) -> None:
        proc = self.run_state.process
        if proc is None or not self.run_state.running:
            return
        proc.terminate()
        self._append_output("[info] stop requested")

    def clear_output(self) -> None:
        self.output_text.delete("1.0", tk.END)

    def refresh_status(self) -> None:
        def _worker() -> None:
            status = load_status_json(self.root_dir)
            summary = summarize_status_for_gui(status)
            self.ui_queue.put(("line", "[status] refreshed"))
            self.root_win.after(0, lambda: self._render_status(summary, status))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_status(self, summary: dict[str, Any], raw_status: dict[str, Any]) -> None:
        health = summary.get("health_summary", {})
        overall = health.get("overall_status")
        if overall == _severity_blocked():
            self.status_banner_var.set(STATUS_BANNER_STOP)
            self.banner_label.configure(foreground="red")
        elif overall == _severity_review():
            self.status_banner_var.set(STATUS_BANNER_WARN)
            self.banner_label.configure(foreground="#b58900")
        elif overall == _severity_info():
            self.status_banner_var.set("Informational")
            self.banner_label.configure(foreground="#5b6f8f")
        else:
            self.status_banner_var.set(STATUS_BANNER_OK)
            self.banner_label.configure(foreground="green")

        self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, format_main_summary_text(summary))
        self.stats_text.see(tk.END)
        self.raw_details_text.delete("1.0", tk.END)
        self.raw_details_text.insert(tk.END, format_raw_details_text(summary))
        if self.raw_details_visible.get():
            self.raw_details_text.pack(fill=tk.BOTH, expand=False, padx=10, pady=4)
        else:
            self.raw_details_text.pack_forget()
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert(tk.END, summary.get("suggested_codex_prompt", ""))
        self.prompt_text.pack(fill=tk.BOTH, expand=False, padx=10, pady=4)

        self._latest_status_summary = summary
        self._latest_raw_status = raw_status

    def copy_status_summary(self) -> None:
        payload = getattr(self, "_latest_status_summary", None)
        if not payload:
            messagebox.showinfo("Status", "Refresh statistics first.")
            return
        self.root_win.clipboard_clear()
        self.root_win.clipboard_append(format_main_summary_text(payload))
        self._append_output("[status] copied status summary to clipboard")

    def show_raw_details(self) -> None:
        self.raw_details_visible.set(True)
        self.raw_details_text.pack(fill=tk.BOTH, expand=False, padx=10, pady=4)

    def hide_raw_details(self) -> None:
        self.raw_details_visible.set(False)
        self.raw_details_text.pack_forget()

    def generate_codex_prompt_ui(self) -> None:
        payload = getattr(self, "_latest_raw_status", None)
        if not payload:
            messagebox.showinfo("Status", "Refresh statistics first.")
            return
        prompt = generate_codex_prompt(payload)
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert(tk.END, prompt)
        self._append_output("[status] generated codex prompt")

    def copy_codex_prompt(self) -> None:
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showinfo("Prompt", "Generate prompt first.")
            return
        self.root_win.clipboard_clear()
        self.root_win.clipboard_append(prompt)
        self._append_output("[status] copied codex prompt to clipboard")

    def open_archive(self) -> None:
        slug = self.dispatch_var.get().lower().replace(" ", "-")
        self._open(self.root_dir / "output" / "site" / slug / "archive.html")

    def open_latest_edition(self) -> None:
        slug = self.dispatch_var.get().lower().replace(" ", "-")
        self.open_dispatch_latest(slug)

    def open_dispatch_latest(self, slug: str) -> None:
        editions = self.root_dir / "output" / "site" / slug / "editions"
        if not editions.exists():
            messagebox.showwarning("Missing", f"No editions folder: {editions}")
            return
        dated = sorted([p.name for p in editions.iterdir() if p.is_dir()])
        if not dated:
            messagebox.showwarning("Missing", "No edition folders found.")
            return
        self._open(editions / dated[-1] / "index.html")

    def open_source_folder(self) -> None:
        dispatch = self.dispatch_var.get()
        date_text = self.date_var.get().strip()
        slug = dispatch.lower().replace(" ", "-")
        if dispatch == "Cascadia":
            path = self.root_dir / "data" / "dispatches" / "cascadia" / "sources"
        else:
            path = self.root_dir / "data" / "dispatches" / slug / "sources" / date_text
        self._open(path)

    def _open(self, path: Path) -> None:
        if not open_path(path):
            messagebox.showwarning("Open failed", f"Could not open path: {path}")


def main() -> int:
    if "--self-check" in sys.argv:
        root = project_root()
        cmd = build_command("Gaza", "Run dispatch", date_cls.today().isoformat(), {}, root=root)
        if "--no-push" not in " ".join(_pages_publish_command(root)):
            return 1
        print("ok")
        print(" ".join(cmd[:3]))
        return 0

    win = tk.Tk()
    app = DispatchesControlPanel(win)
    app.refresh_status()
    win.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
