from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.american_pressure_review_workflow import (
    ALLOWED_REVIEW_STATUSES,
    approval_validation_issues,
    load_weekly_candidates,
    save_review_decisions,
    week_dates_for_year_week,
    week_label,
)

DISPATCHES = ("Gaza", "Cascadia", "American Pressure")
ACTIONS = (
    "Run dispatch",
    "Run American Pressure with approved candidates",
    "Run weekly American Pressure",
    "Scout American Pressure candidates",
    "Review American Pressure candidates",
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


def _pages_publish_command_scoped(root: Path, dispatch: str | None = None, expect_date: str | None = None) -> list[str]:
    cmd = _pages_publish_command(root)
    slug = (dispatch or "").strip().lower().replace(" ", "-")
    if slug in {"gaza", "cascadia", "american-pressure"}:
        cmd.extend(["--only-dispatch", slug])
    if expect_date and validate_date(expect_date):
        cmd.extend(["--expect-date", expect_date])
    return cmd


def _candidate_path(date_text: str, root: Path) -> Path:
    return root / "data" / "dispatches" / "american-pressure" / "candidates" / date_text / "candidate_sources.json"


def _review_report_path(date_text: str, root: Path) -> Path:
    return root / "output" / "dispatches" / "american-pressure" / "review" / f"{date_text}_candidate_review.md"


def _candidate_summary(date_text: str, root: Path) -> dict[str, Any]:
    path = _candidate_path(date_text, root)
    if not path.exists():
        return {
            "approved_count": 0,
            "rejected_count": 0,
            "needs_review_count": 0,
            "maybe_count": 0,
            "quarantine_count": 0,
            "approved_by_pillar": {},
            "missing_required_pillars": list(_ap_required_pillars()),
            "us_relevance_failures": 0,
            "prose_quality_failures": 0,
            "preview_rows": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    status_counts: dict[str, int] = {"approved": 0, "rejected": 0, "needs_review": 0, "maybe": 0, "quarantine": 0}
    approved_by_pillar: dict[str, int] = {}
    us_relevance_failures = 0
    prose_quality_failures = 0
    preview_rows: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("review_status") or "needs_review").strip().lower() or "needs_review"
        if status not in status_counts:
            status_counts[status] = 0
        status_counts[status] += 1
        if status == "approved":
            pillar = str(row.get("pillar") or "").strip() or "unknown"
            approved_by_pillar[pillar] = approved_by_pillar.get(pillar, 0) + 1
        if row.get("us_relevance_ok") is False:
            us_relevance_failures += 1
        if str(row.get("editorial_rejection_reason") or "").strip() == "prose_quality_failed":
            prose_quality_failures += 1
        preview_rows.append(
            {
                "source_record_id": str(row.get("source_record_id") or ""),
                "review_status": status,
                "pillar": str(row.get("pillar") or ""),
                "title": str(row.get("title") or ""),
            }
        )
    missing = [pillar for pillar in _ap_required_pillars() if approved_by_pillar.get(pillar, 0) <= 0]
    return {
        "approved_count": int(status_counts.get("approved", 0)),
        "rejected_count": int(status_counts.get("rejected", 0)),
        "needs_review_count": int(status_counts.get("needs_review", 0)),
        "maybe_count": int(status_counts.get("maybe", 0)),
        "quarantine_count": int(status_counts.get("quarantine", 0)),
        "approved_by_pillar": dict(sorted(approved_by_pillar.items())),
        "missing_required_pillars": missing,
        "us_relevance_failures": us_relevance_failures,
        "prose_quality_failures": prose_quality_failures,
        "preview_rows": preview_rows,
    }


def build_ap_review_command(action: str, date_text: str, root: Path | None = None) -> list[str]:
    if not validate_date(date_text):
        raise ValueError("Date must be YYYY-MM-DD")
    base = root or project_root()
    py = str(python_executable(base))
    if action == "Scout Candidates":
        return [py, "scripts\\scout_american_pressure_candidates.py", "--date", date_text, "--write", "--max-per-pillar", "5"]
    if action == "Generate Review Report":
        return [py, "scripts\\review_american_pressure_candidates.py", "--date", date_text, "--write"]
    if action == "Check Weekly Readiness":
        return [py, "scripts\\check_american_pressure_weekly_readiness.py", "--date", date_text]
    if action == "Run Weekly With Approved Candidates":
        return [
            py,
            "scripts\\run_weekly_american_pressure.py",
            "--date",
            date_text,
            "--source-mode",
            "both",
            "--include-approved-candidates",
            "--publish",
        ]
    raise ValueError(f"Unsupported American Pressure review action: {action}")


def _candidate_window_dates(end_date: str) -> list[str]:
    end = date_cls.fromisoformat(end_date)
    return [(end - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]


def _candidate_files_count(end_date: str, root: Path) -> int:
    return sum(1 for day in _candidate_window_dates(end_date) if _candidate_path(day, root).exists())


def _approved_candidates_count(end_date: str, root: Path) -> int:
    total = 0
    for day in _candidate_window_dates(end_date):
        path = _candidate_path(day, root)
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = payload.get("sources", []) if isinstance(payload, dict) else []
        for row in rows:
            if isinstance(row, dict) and str(row.get("review_status") or "").strip().lower() == "approved":
                total += 1
    return total


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
        scoped_date = date_text if validate_date(date_text) else None
        return _pages_publish_command_scoped(base, dispatch=dispatch, expect_date=scoped_date)

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
                "--source-mode",
                "both",
                "--include-approved-candidates",
                "--publish",
            ]
        elif action == "Run American Pressure with approved candidates":
            cmd = [
                py,
                "scripts\\run_american_pressure_dispatch.py",
                "--date",
                date_text,
                "--source-mode",
                "both",
                "--include-approved-candidates",
                "--publish",
            ]
        elif action == "Run weekly American Pressure":
            cmd = [
                py,
                "scripts\\run_weekly_american_pressure.py",
                "--date",
                date_text,
                "--source-mode",
                "both",
                "--include-approved-candidates",
                "--publish",
            ]
        elif action == "Scout American Pressure candidates":
            cmd = [
                py,
                "scripts\\scout_american_pressure_candidates.py",
                "--date",
                date_text,
                "--write",
                "--max-per-pillar",
                "5",
            ]
        elif action == "Review American Pressure candidates":
            cmd = [
                py,
                "scripts\\review_american_pressure_candidates.py",
                "--date",
                date_text,
                "--write",
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
    repeated_registry_failures = list(cascadia.get("repeated_registry_failures") or [])
    persistent_failure_counts = dict(cascadia.get("persistent_failure_type_counts") or {})
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
        "cascadia_registry_persistent_failures": bool(repeated_registry_failures),
        "cascadia_registry_errors_need_review": warning_counts["registry_fetch_error_count"] > 1 or bool(repeated_registry_failures),
        "manual_source_missing_ap": not bool(american.get("latest_manual_source_exists_for_latest_public_edition")),
        "gaza_stale_unlinked_folders": bool(gaza.get("stale_or_unlinked_edition_dates")),
        "gaza_undercollection_review": False,
        "gaza_high_raw_low_accept_review": False,
        "ap_coverage_gaps_present": bool(_ap_coverage_gaps((american.get("registry_summary") or {}).get("enabled_by_pillar") or {})),
    }
    gaza_latest_sources = int(gaza.get("latest_source_count") or 0)
    gaza_latest_stories = int(gaza.get("latest_story_count") or 0)
    gaza_archive_dates = list(gaza.get("public_archive_dates") or [])
    gaza_stale_unlinked = list(gaza.get("stale_or_unlinked_edition_dates") or [])
    flags["gaza_public_safe"] = (
        gaza_latest_sources > 0
        and gaza_latest_stories > 0
        and bool(gaza.get("archive_exists"))
        and bool(gaza.get("rss_exists"))
        and bool(gaza.get("latest_has_visible_source_links"))
        and not bool(gaza.get("repeated_source_urls_recent"))
        and not bool(gaza.get("public_linked_zero_source_dates"))
        and not bool(gaza.get("public_linked_zero_story_dates"))
        and not bool(gaza.get("public_linked_dedupe_refusal_dates"))
        and len(gaza_stale_unlinked) == 0
    )
    gaza_public_clean = (
        bool(gaza_archive_dates)
        and bool(gaza.get("archive_exists"))
        and bool(gaza.get("rss_exists"))
        and bool(gaza.get("latest_has_visible_source_links"))
        and not bool(gaza.get("repeated_source_urls_recent"))
        and not bool(gaza.get("public_linked_zero_source_dates"))
        and not bool(gaza.get("public_linked_zero_story_dates"))
        and not bool(gaza.get("public_linked_dedupe_refusal_dates"))
    )
    gaza_collection = gaza.get("latest_collection_report") or {}
    has_current_gaza_collection = False
    current_gaza_collection_viable = False
    if isinstance(gaza_collection, dict) and gaza_collection:
        latest_public = str(gaza.get("latest_public_edition_date") or "")
        report_date = str(gaza_collection.get("edition_date") or "")
        is_current_collection = bool(latest_public) and bool(report_date) and latest_public == report_date
        has_current_gaza_collection = bool(is_current_collection)
        raw_candidates = int(gaza_collection.get("raw_candidate_count") or 0)
        final_story_count_value = gaza_collection.get("final_story_count")
        final_story_count = int(final_story_count_value) if isinstance(final_story_count_value, (int, float)) else int(gaza_latest_stories if is_current_collection else 0)
        kept_candidates = int(gaza_collection.get("kept_after_dedupe") or 0)
        review_candidates = list(gaza_collection.get("review_candidates") or [])
        provider_failures = list(gaza_collection.get("provider_failures") or [])
        providers_successful_count = int(
            gaza_collection.get("providers_successful_count")
            or len(list(gaza_collection.get("providers_successful") or []))
            or 0
        )
        providers_attempted_count = int(
            gaza_collection.get("providers_attempted_count")
            or len(list(gaza_collection.get("providers_attempted") or []))
            or len(list(gaza_collection.get("enabled_auto_providers_attempted") or []))
            or 0
        )
        enabled_auto = int(gaza_collection.get("enabled_auto_provider_count") or 0)
        enabled_auto_attempted_count = len(list(gaza_collection.get("enabled_auto_providers_attempted") or []))
        enabled_auto_tls_failures = int(gaza_collection.get("enabled_auto_tls_failures") or 0)
        accepted_before = int(gaza_collection.get("accepted_candidate_count_before_dedupe") or 0)
        low_attempt_threshold = min(2, enabled_auto) if enabled_auto > 0 else 0
        providers_attempted_too_low = enabled_auto > 0 and providers_attempted_count < low_attempt_threshold
        enabled_auto_not_attempted = enabled_auto > 0 and enabled_auto_attempted_count <= 0
        enabled_all_failed_tls = enabled_auto > 0 and enabled_auto_tls_failures >= enabled_auto
        enabled_all_failed_source = enabled_auto > 0 and enabled_auto_attempted_count >= enabled_auto and providers_successful_count <= 0
        provider_failures_blocking = bool(provider_failures) and is_current_collection and final_story_count <= 0
        high_raw_low_accept = raw_candidates >= 50 and accepted_before <= 2 and bool(review_candidates)
        no_viable_story_current = (
            is_current_collection
            and gaza_public_clean
            and (kept_candidates <= 0 or final_story_count <= 0)
        )
        current_gaza_collection_viable = bool(is_current_collection and kept_candidates > 0 and final_story_count > 0)
        collection_undercollection = (
            enabled_auto_not_attempted
            or providers_attempted_too_low
            or (is_current_collection and raw_candidates <= 0)
            or (is_current_collection and accepted_before <= 0)
            or (is_current_collection and final_story_count <= 0)
            or provider_failures_blocking
            or enabled_all_failed_tls
            or enabled_all_failed_source
            or high_raw_low_accept
            or no_viable_story_current
        )
        flags["gaza_undercollection_review"] = bool(collection_undercollection)
        accepted_before = int(gaza_collection.get("accepted_candidate_count_before_dedupe") or 0)
        if high_raw_low_accept:
            flags["gaza_undercollection_review"] = True
            flags["gaza_high_raw_low_accept_review"] = True
        flags["gaza_tls_env_review"] = bool(gaza_collection.get("enabled_auto_all_failed_tls"))
    else:
        flags["gaza_tls_env_review"] = False
    gaza_health = gaza.get("latest_source_health_report") or {}
    if isinstance(gaza_health, dict) and gaza_health:
        enabled = int(gaza_health.get("providers_enabled") or 0)
        attempted = int(gaza_health.get("providers_attempted") or 0)
        failed = int(gaza_health.get("providers_failed") or 0)
        health_indicates_failure = enabled > 0 and (attempted <= 0 or failed >= max(1, (enabled + 1) // 2))
        if health_indicates_failure and not (flags["gaza_public_safe"] and has_current_gaza_collection and current_gaza_collection_viable):
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
        if flags.get("gaza_tls_env_review"):
            review_reasons.append(
                "Enabled Gaza sources were attempted but failed due TLS/certificate verification; check local fetch backend or CA trust."
            )
        elif flags.get("gaza_high_raw_low_accept_review"):
            review_reasons.append(
                "Gaza collection found many raw items, but relevance filtering accepted few. Review rejected candidate examples."
            )
        elif flags["gaza_public_safe"]:
            pass
        else:
            review_reasons.append("Gaza public archive is clean, but collection health indicates possible under-collection.")
    if flags["cascadia_fetch_rate_low"]:
        pct = int(float(fetch_rate) * 100) if isinstance(fetch_rate, (int, float)) else 0
        review_reasons.append(f"Cascadia fetch success rate is {pct}%, below 75% target.")
    if flags["cascadia_weak_date_warnings"]:
        review_reasons.append("Cascadia has weak-date warning noise.")
    if flags["cascadia_registry_errors_need_review"]:
        review_reasons.append("Cascadia has persistent registry fetch failures that need source-level action.")
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
        "repeated_registry_failures": repeated_registry_failures,
        "persistent_failure_type_counts": persistent_failure_counts,
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


def _ap_required_pillars() -> tuple[str, ...]:
    return (
        "food_pressure",
        "financial_distress_pressure",
        "housing_household_cost_pressure",
        "health_access_pressure",
        "labor_income_pressure",
        "local_system_strain",
        "environmental_pressure",
        "policy_implementation",
    )


def _resolve_story_count(dispatch_payload: dict[str, Any]) -> int | None:
    for key in ("latest_story_count", "story_count", "public_story_count"):
        value = dispatch_payload.get(key)
        if isinstance(value, int):
            return value
    for key in ("stories", "items", "briefs"):
        value = dispatch_payload.get(key)
        if isinstance(value, list):
            return len(value)
    return None


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
            "status": _severity_review() if (health["flags"]["cascadia_fetch_rate_low"] or health["flags"]["cascadia_weak_date_warnings"] or health["flags"]["cascadia_registry_errors_need_review"]) else _severity_ok(),
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
            "repeated_registry_failures": health.get("repeated_registry_failures") or [],
            "persistent_failure_type_counts": health.get("persistent_failure_type_counts") or {},
            "main_issue": (
                "Fetch success rate is below target."
                if health["flags"]["cascadia_fetch_rate_low"] and not health["flags"]["cascadia_weak_date_warnings"] and not health["flags"]["cascadia_registry_errors_need_review"]
                else "Discovery works, but source reliability needs cleanup."
                if (health["flags"]["cascadia_fetch_rate_low"] or health["flags"]["cascadia_weak_date_warnings"] or health["flags"]["cascadia_registry_errors_need_review"])
                else "No blocking Cascadia issue."
            ),
            "next_action": (
                "Raise fetch success rate to target and monitor isolated registry errors; do not disable sources unless failures are persistent."
                if health["flags"]["cascadia_fetch_rate_low"] and not health["flags"]["cascadia_weak_date_warnings"] and not health["flags"]["cascadia_registry_errors_need_review"]
                else "Disable/deprioritize dead registry sources and reduce reliability warning noise."
                if (health["flags"]["cascadia_weak_date_warnings"] or health["flags"]["cascadia_registry_errors_need_review"])
                else "No immediate Cascadia action needed."
            ),
        },
        "american_pressure": {
            "status": _severity_review() if health["flags"]["manual_source_missing_ap"] else _severity_ok(),
            "latest_public_edition_date": american.get("latest_public_edition_date"),
            "latest_pages_edition_date": american.get("latest_pages_edition_date"),
            "sources": american.get("latest_source_count"),
            "stories": _resolve_story_count(american),
            "story_plus_data_count": american.get("story_plus_data_count"),
            "baseline_only_count": american.get("baseline_only_count"),
            "missing_required_current_development_pillars": american.get("missing_required_current_development_pillars") or [],
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


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 300):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._tip_window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, _event: tk.Event[Any]) -> None:
        self._schedule()

    def _on_leave(self, _event: tk.Event[Any]) -> None:
        self._cancel()
        self._hide()

    def _schedule(self) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        if self._tip_window is not None:
            return
        self._tip_window = tk.Toplevel(self.widget)
        self._tip_window.wm_overrideredirect(True)
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip_window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self._tip_window,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            padx=6,
            pady=4,
            wraplength=360,
        )
        label.pack()

    def _hide(self) -> None:
        if self._tip_window is not None:
            self._tip_window.destroy()
            self._tip_window = None


def _format_dispatch_card(name: str, card: dict[str, Any]) -> str:
    stories = card.get("stories")
    story_text = "not reported" if stories is None else str(stories)
    lines = [
        f"{name} [{card.get('status', 'OK')}]",
        f"- Latest public edition: {card.get('latest_public_edition_date')}",
        f"- Sources/Stories: {card.get('sources')} / {story_text}",
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
        missing_current = card.get("missing_required_current_development_pillars") or []
        lines.extend(
            [
                f"- Latest manual source date: {card.get('latest_manual_source_date')}",
                "- Manual source exists for latest public edition: "
                f"{card.get('manual_source_present')}",
                f"- Story plus data count: {card.get('story_plus_data_count', 'not reported')}",
                f"- Baseline only count: {card.get('baseline_only_count', 'not reported')}",
                f"- Missing current-development pillars: {', '.join(missing_current) if missing_current else 'none'}",
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


AP_PUBLISHER_QUALITY_PRIORITY = {
    "local_reporting": 0,
    "reputable_reporting": 1,
    "official_primary": 2,
    "institutional_secondary": 3,
    "mixed_or_uncertain": 4,
    "low_confidence_aggregator": 5,
    "unknown": 6,
}


def _publisher_quality_rank(value: str) -> int:
    key = str(value or "").strip().lower()
    if not key:
        return AP_PUBLISHER_QUALITY_PRIORITY["unknown"]
    return AP_PUBLISHER_QUALITY_PRIORITY.get(key, AP_PUBLISHER_QUALITY_PRIORITY["unknown"])


def _row_score(row: dict[str, Any]) -> int:
    try:
        return int(row.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _row_raw(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw")
    return raw if isinstance(raw, dict) else {}


def _row_us_relevance_pass(row: dict[str, Any]) -> bool:
    return _row_raw(row).get("us_relevance_ok") is not False


def _row_prose_quality_pass(row: dict[str, Any]) -> bool:
    return str(_row_raw(row).get("editorial_rejection_reason") or "").strip().lower() != "prose_quality_failed"


def _row_has_location(row: dict[str, Any]) -> bool:
    return bool(str(row.get("location") or "").strip())


def _row_has_anchor(row: dict[str, Any]) -> bool:
    anchors = _row_raw(row).get("linked_data_anchor_ids")
    return isinstance(anchors, list) and len(anchors) > 0


def _row_has_public_pressure_angle(row: dict[str, Any]) -> bool:
    return bool(str(_row_raw(row).get("public_pressure_angle") or "").strip())


def _row_has_url(row: dict[str, Any]) -> bool:
    return bool(str(row.get("url") or "").strip())


def _row_status(row: dict[str, Any], status_updates: dict[str, str]) -> str:
    return str(status_updates.get(str(row.get("candidate_key") or ""), row.get("review_status") or "needs_review")).strip().lower() or "needs_review"


def ap_default_sort_key(row: dict[str, Any], status_updates: dict[str, str]) -> tuple[Any, ...]:
    return (
        -_row_score(row),
        _publisher_quality_rank(str(row.get("publisher_quality") or "")),
        0 if _row_prose_quality_pass(row) else 1,
        0 if _row_us_relevance_pass(row) else 1,
        0 if _row_has_location(row) else 1,
        str(row.get("pillar") or "").strip().lower(),
        str(row.get("reader_headline") or "").strip().lower(),
        str(row.get("candidate_key") or ""),
    )


def row_matches_ap_filters(row: dict[str, Any], status_updates: dict[str, str], filters: dict[str, Any]) -> bool:
    status = _row_status(row, status_updates)
    if str(filters.get("status") or "all") != "all" and status != str(filters.get("status")):
        return False
    if str(filters.get("pillar") or "all") != "all" and str(row.get("pillar") or "") != str(filters.get("pillar")):
        return False
    if str(filters.get("publisher_quality") or "all") != "all" and str(row.get("publisher_quality") or "") != str(filters.get("publisher_quality")):
        return False
    min_score = int(filters.get("min_score") or 0)
    if _row_score(row) < min_score:
        return False
    us_filter = str(filters.get("us_relevance") or "all")
    if us_filter == "pass" and not _row_us_relevance_pass(row):
        return False
    if us_filter == "fail" and _row_us_relevance_pass(row):
        return False
    prose_filter = str(filters.get("prose_quality") or "all")
    if prose_filter == "pass" and not _row_prose_quality_pass(row):
        return False
    if prose_filter == "fail" and _row_prose_quality_pass(row):
        return False
    if bool(filters.get("has_location")) and not _row_has_location(row):
        return False
    if bool(filters.get("has_anchor")) and not _row_has_anchor(row):
        return False
    if bool(filters.get("recommended_only")) and str(_row_raw(row).get("candidate_bucket") or "").strip().lower() != "recommended":
        return False
    if not bool(filters.get("show_quarantined")) and status == "quarantine":
        return False
    if not bool(filters.get("show_rejected")) and status == "rejected":
        return False
    return True


def build_recommended_review_queue(
    rows: list[dict[str, Any]],
    status_updates: dict[str, str],
    score_threshold: int = 45,
    max_per_pillar: int = 3,
    max_total: int = 25,
) -> list[str]:
    def _priority_score(row: dict[str, Any]) -> int:
        raw = _row_raw(row)
        text = " ".join(
            [
                str(row.get("reader_headline") or ""),
                str(raw.get("human_story_summary") or ""),
                str(raw.get("what_happened") or ""),
                str(raw.get("potential_relevance") or ""),
                str(raw.get("publisher") or ""),
            ]
        ).lower()
        score = _row_score(row)
        if "local" in text or "county" in text or "city" in text:
            score += 8
        if any(token in text for token in ("layoff", "evict", "rent", "bill", "utility", "hospital", "clinic", "wage", "food bank", "shelter", "school")):
            score += 7
        if any(token in text for token in ("policy debate", "proposal", "partisan", "talking points")):
            score -= 8
        if _publisher_quality_rank(str(row.get("publisher_quality") or "")) >= 5:
            score -= 10
        if any(token in text for token in ("aggregator", "repost", "roundup")):
            score -= 10
        return score

    eligible: list[dict[str, Any]] = []
    for row in rows:
        status = _row_status(row, status_updates)
        if status in {"quarantine", "rejected"}:
            continue
        if not _row_us_relevance_pass(row):
            continue
        if not _row_prose_quality_pass(row):
            continue
        if not _row_has_url(row):
            continue
        if not _row_has_public_pressure_angle(row):
            continue
        if _row_score(row) < int(score_threshold):
            continue
        eligible.append(row)
    eligible.sort(
        key=lambda row: (
            -_priority_score(row),
            *ap_default_sort_key(row, status_updates),
        )
    )
    per_pillar: dict[str, int] = {}
    picked: list[str] = []
    for row in eligible:
        pillar = str(row.get("pillar") or "").strip() or "unknown"
        if per_pillar.get(pillar, 0) >= int(max_per_pillar):
            continue
        picked.append(str(row.get("candidate_key") or ""))
        per_pillar[pillar] = per_pillar.get(pillar, 0) + 1
        if len(picked) >= int(max_total):
            break
    return picked


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
        today = date_cls.today()
        self.ap_review_year_var = tk.IntVar(value=today.year)
        self.ap_review_week_var = tk.IntVar(value=max(1, int(((today - date_cls(today.year, 1, 1)).days // 7) + 1)))
        self.ap_review_date_var = tk.StringVar(value=today.isoformat())
        self.ap_week_range_var = tk.StringVar(value="")
        self.ap_candidate_path_var = tk.StringVar(value="")
        self.ap_review_report_path_var = tk.StringVar(value="")
        self.ap_summary_var = tk.StringVar(value="")
        self.ap_duplicate_note_var = tk.StringVar(value="")
        self.ap_readiness_var = tk.StringVar(value="")
        self.ap_readiness_progress_var = tk.StringVar(value="")
        self.ap_filter_status_var = tk.StringVar(value="all")
        self.ap_filter_pillar_var = tk.StringVar(value="all")
        self.ap_filter_publisher_quality_var = tk.StringVar(value="all")
        self.ap_filter_min_score_var = tk.IntVar(value=45)
        self.ap_filter_us_relevance_var = tk.StringVar(value="all")
        self.ap_filter_prose_quality_var = tk.StringVar(value="all")
        self.ap_filter_has_location_var = tk.BooleanVar(value=False)
        self.ap_filter_has_anchor_var = tk.BooleanVar(value=False)
        self.ap_filter_recommended_only_var = tk.BooleanVar(value=False)
        self.ap_filter_show_quarantined_var = tk.BooleanVar(value=False)
        self.ap_filter_show_rejected_var = tk.BooleanVar(value=False)
        self.ap_recommended_queue_active_var = tk.BooleanVar(value=False)
        self.ap_candidate_rows: list[dict[str, Any]] = []
        self.ap_visible_candidate_rows: list[dict[str, Any]] = []
        self.ap_visible_candidate_keys: list[str] = []
        self.ap_recommended_queue_keys: list[str] = []
        self.ap_candidate_status_updates: dict[str, str] = {}
        self.ap_candidate_override_keys: set[str] = set()
        self.ap_sort_column = "default"
        self.ap_sort_desc = False
        self._tooltips: list[Tooltip] = []

        self._build_ui()
        self.ap_review_year_var.trace_add("write", lambda *_args: self._on_ap_week_changed())
        self.ap_review_week_var.trace_add("write", lambda *_args: self._on_ap_week_changed())
        self.ap_review_date_var.trace_add("write", lambda *_args: self.refresh_ap_review_summary())
        self._on_ap_week_changed()
        self._poll_ui_queue()

    def _build_ui(self) -> None:
        tabs = ttk.Notebook(self.root_win)
        tabs.pack(fill=tk.BOTH, expand=True)

        run_tab = ttk.Frame(tabs)
        stats_tab = ttk.Frame(tabs)
        logs_tab = ttk.Frame(tabs)
        ap_review_tab = ttk.Frame(tabs)

        tabs.add(run_tab, text="Run Dispatches")
        tabs.add(ap_review_tab, text="American Pressure Review")
        tabs.add(stats_tab, text="Statistics / Health")
        tabs.add(logs_tab, text="Logs / Output")

        self._build_run_tab(run_tab)
        self._build_ap_review_tab(ap_review_tab)
        self._build_stats_tab(stats_tab)
        self._build_logs_tab(logs_tab)

    def _build_run_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(top, text="Dispatch").grid(row=0, column=0, sticky="w")
        dispatch_combo = ttk.Combobox(top, values=DISPATCHES, textvariable=self.dispatch_var, state="readonly", width=22)
        dispatch_combo.grid(
            row=0, column=1, padx=6, sticky="w"
        )

        ttk.Label(top, text="Date (YYYY-MM-DD)").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.date_var, width=16).grid(row=0, column=3, padx=6, sticky="w")

        ttk.Label(top, text="Action").grid(row=1, column=0, sticky="w")
        action_combo = ttk.Combobox(top, values=ACTIONS, textvariable=self.action_var, state="readonly", width=30)
        action_combo.grid(
            row=1, column=1, padx=6, sticky="w"
        )

        ttk.Checkbutton(top, text="Open output page after success", variable=self.open_after_var).grid(
            row=1, column=2, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(top, text="Dry-run if supported", variable=self.dry_run_var).grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(top, text="Publish locally when supported", variable=self.publish_toggle_var).grid(
            row=2, column=2, columnspan=2, sticky="w"
        )

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=10)
        self.execute_btn = ttk.Button(btn_row, text="Execute", command=self.execute_action)
        self.execute_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(btn_row, text="Stop (best effort)", command=self.stop_action, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        clear_button = ttk.Button(btn_row, text="Clear Output", command=self.clear_output)
        clear_button.pack(side=tk.LEFT, padx=6)

        open_row = ttk.Frame(frame)
        open_row.pack(fill=tk.X, padx=10, pady=6)
        archive_button = ttk.Button(open_row, text="Open local dispatch archive", command=self.open_archive)
        archive_button.pack(side=tk.LEFT)
        latest_button = ttk.Button(open_row, text="Open latest local edition", command=self.open_latest_edition)
        latest_button.pack(side=tk.LEFT, padx=6)
        source_button = ttk.Button(open_row, text="Open source folder", command=self.open_source_folder)
        source_button.pack(side=tk.LEFT, padx=6)
        log_button = ttk.Button(open_row, text="Open log folder", command=lambda: self._open(self.root_dir / "logs"))
        log_button.pack(side=tk.LEFT, padx=6)
        output_site_button = ttk.Button(open_row, text="Open output/site", command=lambda: self._open(self.root_dir / "output" / "site"))
        output_site_button.pack(side=tk.LEFT, padx=6)
        pages_repo_button = ttk.Button(
            open_row,
            text="Open Pages repo folder",
            command=lambda: self._open(self.root_dir / "bluefern-dispatches-pages"),
        )
        pages_repo_button.pack(side=tk.LEFT, padx=6)

        ttk.Label(frame, textvariable=self.execution_var, foreground="#333366").pack(anchor="w", padx=10)
        ttk.Label(frame, textvariable=self.command_var, foreground="#444444", wraplength=1150).pack(anchor="w", padx=10)

        self.output_text = ScrolledText(frame, height=22)
        self.output_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._tooltips.extend(
            [
                Tooltip(dispatch_combo, "Choose which dispatch pipeline to operate."),
                Tooltip(action_combo, "Choose the command to run for the selected dispatch."),
                Tooltip(self.execute_btn, "Run the selected command with live stdout/stderr streaming."),
                Tooltip(self.stop_btn, "Request stop for the current process."),
                Tooltip(clear_button, "Clear only the run-tab output panel."),
                Tooltip(archive_button, "Open the local public archive HTML for the selected dispatch."),
                Tooltip(latest_button, "Open the latest local edition from status or folder fallback."),
                Tooltip(source_button, "Open source input folder for selected dispatch/date."),
                Tooltip(log_button, "Open local logs folder."),
                Tooltip(output_site_button, "Open generated output/site folder."),
                Tooltip(pages_repo_button, "Open local GitHub Pages repo checkout folder."),
            ]
        )

    def _build_stats_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)

        self.banner_label = ttk.Label(top, textvariable=self.status_banner_var)
        self.banner_label.pack(side=tk.LEFT)
        refresh_btn = ttk.Button(top, text="Refresh Statistics", command=self.refresh_status)
        refresh_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Copy status summary", command=self.copy_status_summary).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Show Raw Details", command=self.show_raw_details).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="Hide Raw Details", command=self.hide_raw_details).pack(side=tk.LEFT, padx=8)
        prompt_btn = ttk.Button(top, text="Generate Codex Prompt", command=self.generate_codex_prompt_ui)
        prompt_btn.pack(side=tk.LEFT, padx=8)
        copy_prompt_btn = ttk.Button(top, text="Copy Codex Prompt", command=self.copy_codex_prompt)
        copy_prompt_btn.pack(side=tk.LEFT, padx=8)
        self._tooltips.extend(
            [
                Tooltip(refresh_btn, "Refresh doctor/status health summary."),
                Tooltip(prompt_btn, "Generate a Codex-ready prompt from current status."),
                Tooltip(copy_prompt_btn, "Copy generated Codex prompt to clipboard."),
            ]
        )

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

    def _build_ap_review_tab(self, frame: ttk.Frame) -> None:
        top = ttk.Frame(frame)
        top.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(top, text="Year").grid(row=0, column=0, sticky="w")
        year_combo = ttk.Combobox(top, values=[str(y) for y in range(2024, 2036)], textvariable=self.ap_review_year_var, state="readonly", width=8)
        year_combo.grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(top, text="Week").grid(row=0, column=2, sticky="w")
        week_combo = ttk.Combobox(top, values=[str(w) for w in range(1, 54)], textvariable=self.ap_review_week_var, state="readonly", width=6)
        week_combo.grid(row=0, column=3, padx=6, sticky="w")
        ttk.Label(top, textvariable=self.ap_week_range_var, foreground="#1f3f55").grid(row=0, column=4, columnspan=4, sticky="w")
        ttk.Label(top, text="Step 1: Scout. Step 2: Review and approve. Step 3: Check readiness. Step 4: Generate and preview. Step 5: Publish.", foreground="#505050").grid(row=1, column=0, columnspan=8, sticky="w")

        path_frame = ttk.Frame(frame)
        path_frame.pack(fill=tk.X, padx=10, pady=4)
        ttk.Label(path_frame, text="Week-ending date").grid(row=0, column=0, sticky="w")
        ttk.Label(path_frame, textvariable=self.ap_review_date_var, foreground="#444444").grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(path_frame, text="Candidate file path").grid(row=1, column=0, sticky="w")
        ttk.Label(path_frame, textvariable=self.ap_candidate_path_var, foreground="#444444", wraplength=1120).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(path_frame, text="Review report path").grid(row=2, column=0, sticky="w")
        ttk.Label(path_frame, textvariable=self.ap_review_report_path_var, foreground="#444444", wraplength=1120).grid(row=2, column=1, sticky="w", padx=6)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=10, pady=6)
        scout_btn = ttk.Button(btn_row, text="Scout Week", command=self.scout_ap_week)
        scout_btn.pack(side=tk.LEFT)
        report_btn = ttk.Button(btn_row, text="Generate Review Report", command=self.generate_ap_review_report)
        report_btn.pack(side=tk.LEFT, padx=6)
        load_btn = ttk.Button(btn_row, text="Load Candidates", command=self.load_ap_candidates)
        load_btn.pack(side=tk.LEFT, padx=6)
        save_btn = ttk.Button(btn_row, text="Save Review Decisions", command=self.save_ap_review_decisions)
        save_btn.pack(side=tk.LEFT, padx=6)
        queue_btn = ttk.Button(btn_row, text="Show Recommended Review Queue", command=self.show_recommended_review_queue)
        queue_btn.pack(side=tk.LEFT, padx=6)
        clear_queue_btn = ttk.Button(btn_row, text="Clear Queue/Filters", command=self.clear_ap_filters)
        clear_queue_btn.pack(side=tk.LEFT, padx=6)
        readiness_btn = ttk.Button(btn_row, text="Check Weekly Readiness", command=self.check_ap_weekly_readiness)
        readiness_btn.pack(side=tk.LEFT, padx=6)
        generate_btn = ttk.Button(btn_row, text="Generate HTML", command=self.generate_ap_html)
        generate_btn.pack(side=tk.LEFT, padx=6)
        open_html_btn = ttk.Button(btn_row, text="Open Generated HTML", command=self.open_ap_generated_html)
        open_html_btn.pack(side=tk.LEFT, padx=6)
        publish_btn = ttk.Button(btn_row, text="Publish to Pages Locally", command=self.publish_ap_pages_locally)
        publish_btn.pack(side=tk.LEFT, padx=6)
        push_btn = ttk.Button(btn_row, text="Push Pages Live", command=self.push_ap_pages_live)
        push_btn.pack(side=tk.LEFT, padx=6)

        workflow_row = ttk.Frame(frame)
        workflow_row.pack(fill=tk.X, padx=10, pady=2)
        ttk.Label(
            workflow_row,
            text="Quick Editorial Pass: 1) Load week 2) Show Recommended Review Queue 3) Approve 4–8 strongest candidates 4) Bulk reject/quarantine weak items 5) Check readiness 6) Generate HTML 7) Preview 8) Publish",
            foreground="#505050",
        ).pack(anchor="w")

        filter_row = ttk.Frame(frame)
        filter_row.pack(fill=tk.X, padx=10, pady=4)
        ttk.Label(filter_row, text="Status").grid(row=0, column=0, sticky="w")
        status_filter = ttk.Combobox(filter_row, values=["all", *ALLOWED_REVIEW_STATUSES], textvariable=self.ap_filter_status_var, state="readonly", width=14)
        status_filter.grid(row=0, column=1, padx=4, sticky="w")
        ttk.Label(filter_row, text="Pillar").grid(row=0, column=2, sticky="w")
        pillar_filter = ttk.Combobox(filter_row, values=["all"], textvariable=self.ap_filter_pillar_var, state="readonly", width=22)
        pillar_filter.grid(row=0, column=3, padx=4, sticky="w")
        self.ap_pillar_filter_combo = pillar_filter
        ttk.Label(filter_row, text="Publisher quality").grid(row=0, column=4, sticky="w")
        publisher_filter = ttk.Combobox(filter_row, values=["all"], textvariable=self.ap_filter_publisher_quality_var, state="readonly", width=22)
        publisher_filter.grid(row=0, column=5, padx=4, sticky="w")
        self.ap_publisher_filter_combo = publisher_filter
        ttk.Label(filter_row, text="Min score").grid(row=0, column=6, sticky="w")
        ttk.Entry(filter_row, textvariable=self.ap_filter_min_score_var, width=6).grid(row=0, column=7, padx=4, sticky="w")
        ttk.Label(filter_row, text="U.S. relevance").grid(row=1, column=0, sticky="w")
        us_filter = ttk.Combobox(filter_row, values=["all", "pass", "fail"], textvariable=self.ap_filter_us_relevance_var, state="readonly", width=14)
        us_filter.grid(row=1, column=1, padx=4, sticky="w")
        ttk.Label(filter_row, text="Prose quality").grid(row=1, column=2, sticky="w")
        prose_filter = ttk.Combobox(filter_row, values=["all", "pass", "fail"], textvariable=self.ap_filter_prose_quality_var, state="readonly", width=14)
        prose_filter.grid(row=1, column=3, padx=4, sticky="w")
        has_location_ck = ttk.Checkbutton(filter_row, text="Has location", variable=self.ap_filter_has_location_var, command=self.apply_ap_filters_and_render)
        has_location_ck.grid(row=1, column=4, sticky="w")
        has_anchor_ck = ttk.Checkbutton(filter_row, text="Has linked data anchor", variable=self.ap_filter_has_anchor_var, command=self.apply_ap_filters_and_render)
        has_anchor_ck.grid(row=1, column=5, sticky="w")
        recommended_ck = ttk.Checkbutton(filter_row, text="Recommended only", variable=self.ap_filter_recommended_only_var, command=self.apply_ap_filters_and_render)
        recommended_ck.grid(row=1, column=6, sticky="w")
        show_quarantine_ck = ttk.Checkbutton(filter_row, text="Show quarantined", variable=self.ap_filter_show_quarantined_var, command=self.apply_ap_filters_and_render)
        show_quarantine_ck.grid(row=1, column=7, sticky="w")
        show_rejected_ck = ttk.Checkbutton(filter_row, text="Show rejected", variable=self.ap_filter_show_rejected_var, command=self.apply_ap_filters_and_render)
        show_rejected_ck.grid(row=1, column=8, sticky="w")
        for var in (
            self.ap_filter_status_var,
            self.ap_filter_pillar_var,
            self.ap_filter_publisher_quality_var,
            self.ap_filter_min_score_var,
            self.ap_filter_us_relevance_var,
            self.ap_filter_prose_quality_var,
        ):
            var.trace_add("write", lambda *_args: self.apply_ap_filters_and_render())

        bulk_row = ttk.Frame(frame)
        bulk_row.pack(fill=tk.X, padx=10, pady=4)
        ttk.Button(bulk_row, text="Reject Visible", command=lambda: self.bulk_update_visible_status("rejected")).pack(side=tk.LEFT)
        ttk.Button(bulk_row, text="Quarantine Visible", command=lambda: self.bulk_update_visible_status("quarantine")).pack(side=tk.LEFT, padx=6)
        ttk.Button(bulk_row, text="Mark Visible Maybe", command=lambda: self.bulk_update_visible_status("maybe")).pack(side=tk.LEFT, padx=6)
        ttk.Button(bulk_row, text="Approve Selected", command=lambda: self.bulk_update_selected_status("approved")).pack(side=tk.LEFT, padx=12)
        ttk.Button(bulk_row, text="Reject Selected", command=lambda: self.bulk_update_selected_status("rejected")).pack(side=tk.LEFT, padx=6)
        ttk.Button(bulk_row, text="Quarantine Selected", command=lambda: self.bulk_update_selected_status("quarantine")).pack(side=tk.LEFT, padx=6)

        ttk.Label(frame, textvariable=self.ap_summary_var, foreground="#1f3f55", justify=tk.LEFT).pack(anchor="w", padx=10, pady=4)
        ttk.Label(frame, textvariable=self.ap_duplicate_note_var, foreground="#6a4d00", justify=tk.LEFT).pack(anchor="w", padx=10, pady=2)
        ttk.Label(frame, textvariable=self.ap_readiness_progress_var, foreground="#1f3f55", justify=tk.LEFT).pack(anchor="w", padx=10, pady=2)
        ttk.Label(frame, textvariable=self.ap_readiness_var, foreground="#6a4d00", justify=tk.LEFT).pack(anchor="w", padx=10, pady=2)
        columns = ("date", "status", "pillar", "publisher_quality", "source_publisher", "reader_headline", "location", "score", "flags", "reason", "url")
        self.ap_candidates_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        for col in columns:
            self.ap_candidates_tree.heading(col, text=col.replace("_", " ").title(), command=lambda c=col: self._set_ap_sort(c))
        self.ap_candidates_tree.column("date", width=100, stretch=False)
        self.ap_candidates_tree.column("status", width=120, stretch=False)
        self.ap_candidates_tree.column("pillar", width=170, stretch=False)
        self.ap_candidates_tree.column("publisher_quality", width=130, stretch=False)
        self.ap_candidates_tree.column("source_publisher", width=170)
        self.ap_candidates_tree.column("reader_headline", width=320)
        self.ap_candidates_tree.column("location", width=130, stretch=False)
        self.ap_candidates_tree.column("score", width=65, stretch=False)
        self.ap_candidates_tree.column("flags", width=170, stretch=False)
        self.ap_candidates_tree.column("reason", width=180)
        self.ap_candidates_tree.column("url", width=280)
        self.ap_candidates_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.ap_candidates_tree.bind("<Double-1>", self._edit_ap_status_cell)
        self.ap_candidates_tree.bind("<<TreeviewSelect>>", self._on_ap_candidate_select)
        self.ap_candidates_tree.tag_configure("status-approved", background="#e8f8e8")
        self.ap_candidates_tree.tag_configure("status-maybe", background="#fff8e1")
        self.ap_candidates_tree.tag_configure("status-rejected", background="#fdeaea")
        self.ap_candidates_tree.tag_configure("status-quarantine", background="#f8eafc")
        self.ap_candidates_tree.tag_configure("recommended-row", background="#e6f3ff")
        self.ap_candidates_tree.tag_configure("flag-risk", foreground="#aa2e25")

        self.ap_details_text = ScrolledText(frame, height=8)
        self.ap_details_text.pack(fill=tk.BOTH, expand=False, padx=10, pady=6)
        self._tooltips.extend(
            [
                Tooltip(year_combo, "Select the year for Sunday-Saturday weekly review."),
                Tooltip(week_combo, "Select the week number. Edition date is that week's Saturday."),
                Tooltip(scout_btn, "Run daily candidate scouting for each day in the selected week."),
                Tooltip(report_btn, "Generate candidate review report(s) for the selected week."),
                Tooltip(load_btn, "Load candidate records for all seven days in the selected week."),
                Tooltip(save_btn, "Save only review status metadata back to candidate JSON files."),
                Tooltip(queue_btn, "Show strongest candidates with editorial queue caps."),
                Tooltip(readiness_btn, "Check weekly readiness based on approved candidates."),
                Tooltip(generate_btn, "Generate weekly edition HTML without Pages publish/push."),
                Tooltip(open_html_btn, "Open output/site american-pressure edition HTML for the selected week-ending date."),
                Tooltip(publish_btn, "Publish selected edition into local Pages repo only."),
                Tooltip(push_btn, "Push local Pages gh-pages branch live after confirmation."),
            ]
        )
        self.load_ap_candidates()

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

    def _on_ap_week_changed(self) -> None:
        try:
            year = int(self.ap_review_year_var.get())
            week = int(self.ap_review_week_var.get())
            _, end = week_dates_for_year_week(year, week)
        except Exception:
            self.ap_week_range_var.set("Invalid week selection")
            return
        self.ap_review_date_var.set(end.isoformat())
        self.ap_week_range_var.set(week_label(year, week))

    def _run_async_command(self, cmd: list[str], action_label: str) -> None:
        cmd_str = subprocess.list2cmdline(cmd)
        self.command_var.set(f"Command: {cmd_str}")
        self.execution_var.set(action_label)
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

    def _run_sync_command(self, cmd: list[str], action_label: str) -> int:
        cmd_str = subprocess.list2cmdline(cmd)
        self.command_var.set(f"Command: {cmd_str}")
        self.execution_var.set(action_label)
        self._append_output(f"$ {cmd_str}")
        completed = subprocess.run(cmd, cwd=str(self.root_dir), capture_output=True, text=True, check=False)
        if completed.stdout:
            for line in completed.stdout.splitlines():
                self._append_output(line)
        if completed.stderr:
            for line in completed.stderr.splitlines():
                self._append_output(line)
        self.execution_var.set(f"Finished with exit code={completed.returncode} ({'success' if completed.returncode == 0 else 'failure'})")
        return completed.returncode

    def _ap_week_days(self) -> list[str]:
        end = date_cls.fromisoformat(self.ap_review_date_var.get().strip())
        start = end - timedelta(days=6)
        return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]

    def scout_ap_week(self) -> None:
        for day in self._ap_week_days():
            rc = self._run_sync_command(
                [
                    str(python_executable(self.root_dir)),
                    "scripts\\scout_american_pressure_candidates.py",
                    "--date",
                    day,
                    "--write",
                    "--max-per-pillar",
                    "5",
                ],
                f"Running Scout Week for {day}",
            )
            if rc != 0:
                break

    def generate_ap_review_report(self) -> None:
        for day in self._ap_week_days():
            rc = self._run_sync_command(
                [str(python_executable(self.root_dir)), "scripts\\review_american_pressure_candidates.py", "--date", day, "--write"],
                f"Running Generate Review Report for {day}",
            )
            if rc != 0:
                break

    def load_ap_candidates(self) -> None:
        week_end = self.ap_review_date_var.get().strip()
        self.ap_candidate_rows = load_weekly_candidates(self.root_dir, week_end)
        self.ap_candidate_status_updates = {row["candidate_key"]: row["review_status"] for row in self.ap_candidate_rows}
        self.ap_candidate_override_keys = set()
        self.ap_recommended_queue_keys = []
        self.ap_recommended_queue_active_var.set(False)
        self._update_ap_filter_values()
        self.apply_ap_filters_and_render()

    def _update_ap_filter_values(self) -> None:
        pillars = sorted({str(row.get("pillar") or "").strip() for row in self.ap_candidate_rows if str(row.get("pillar") or "").strip()})
        publisher_qualities = sorted({str(row.get("publisher_quality") or "").strip() for row in self.ap_candidate_rows if str(row.get("publisher_quality") or "").strip()})
        self.ap_pillar_filter_combo.configure(values=["all", *pillars])
        self.ap_publisher_filter_combo.configure(values=["all", *publisher_qualities])
        if self.ap_filter_pillar_var.get() not in ["all", *pillars]:
            self.ap_filter_pillar_var.set("all")
        if self.ap_filter_publisher_quality_var.get() not in ["all", *publisher_qualities]:
            self.ap_filter_publisher_quality_var.set("all")

    def _ap_sort_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.ap_sort_column == "default":
            return sorted(rows, key=lambda row: ap_default_sort_key(row, self.ap_candidate_status_updates), reverse=self.ap_sort_desc)
        key_map: dict[str, Callable[[dict[str, Any]], Any]] = {
            "date": lambda row: str(row.get("date") or ""),
            "status": lambda row: _row_status(row, self.ap_candidate_status_updates),
            "pillar": lambda row: str(row.get("pillar") or ""),
            "publisher_quality": lambda row: _publisher_quality_rank(str(row.get("publisher_quality") or "")),
            "source_publisher": lambda row: str(row.get("source_publisher") or ""),
            "reader_headline": lambda row: str(row.get("reader_headline") or ""),
            "location": lambda row: str(row.get("location") or ""),
            "score": _row_score,
            "flags": lambda row: self._ap_row_flags_text(row),
            "reason": lambda row: str(row.get("reason") or ""),
            "url": lambda row: str(row.get("url") or ""),
        }
        return sorted(rows, key=key_map.get(self.ap_sort_column, lambda row: str(row.get("candidate_key") or "")), reverse=self.ap_sort_desc)

    def _set_ap_sort(self, column: str) -> None:
        if self.ap_sort_column == column:
            self.ap_sort_desc = not self.ap_sort_desc
        else:
            self.ap_sort_column = column
            self.ap_sort_desc = column == "score"
        self.apply_ap_filters_and_render()

    def _current_ap_filters(self) -> dict[str, Any]:
        return {
            "status": self.ap_filter_status_var.get(),
            "pillar": self.ap_filter_pillar_var.get(),
            "publisher_quality": self.ap_filter_publisher_quality_var.get(),
            "min_score": int(self.ap_filter_min_score_var.get() or 0),
            "us_relevance": self.ap_filter_us_relevance_var.get(),
            "prose_quality": self.ap_filter_prose_quality_var.get(),
            "has_location": bool(self.ap_filter_has_location_var.get()),
            "has_anchor": bool(self.ap_filter_has_anchor_var.get()),
            "recommended_only": bool(self.ap_filter_recommended_only_var.get()),
            "show_quarantined": bool(self.ap_filter_show_quarantined_var.get()),
            "show_rejected": bool(self.ap_filter_show_rejected_var.get()),
        }

    def apply_ap_filters_and_render(self) -> None:
        filters = self._current_ap_filters()
        rows = [row for row in self.ap_candidate_rows if row_matches_ap_filters(row, self.ap_candidate_status_updates, filters)]
        if self.ap_recommended_queue_active_var.get():
            queue_keys = set(self.ap_recommended_queue_keys)
            rows = [row for row in rows if str(row.get("candidate_key") or "") in queue_keys]
        rows = self._ap_sort_rows(rows)
        self.ap_visible_candidate_rows = rows
        self.ap_visible_candidate_keys = [str(row.get("candidate_key") or "") for row in rows]
        for item in self.ap_candidates_tree.get_children():
            self.ap_candidates_tree.delete(item)
        for row in rows:
            key = str(row.get("candidate_key") or "")
            tags: list[str] = []
            status = _row_status(row, self.ap_candidate_status_updates)
            if status in {"approved", "maybe", "rejected", "quarantine"}:
                tags.append(f"status-{status}")
            if key in self.ap_recommended_queue_keys:
                tags.append("recommended-row")
            if self._ap_row_has_risk_flag(row):
                tags.append("flag-risk")
            self.ap_candidates_tree.insert(
                "",
                tk.END,
                iid=key,
                values=(
                    row["date"],
                    status,
                    row["pillar"],
                    row["publisher_quality"],
                    row["source_publisher"],
                    row["reader_headline"],
                    row["location"],
                    row["score"],
                    self._ap_row_flags_text(row),
                    row["reason"],
                    row["url"],
                ),
                tags=tuple(tags),
            )
        self.refresh_ap_review_summary()

    def _ap_row_has_risk_flag(self, row: dict[str, Any]) -> bool:
        return (not _row_us_relevance_pass(row)) or (not _row_prose_quality_pass(row)) or (not _row_has_location(row)) or (_publisher_quality_rank(str(row.get("publisher_quality") or "")) >= 5)

    def _ap_row_flags_text(self, row: dict[str, Any]) -> str:
        flags: list[str] = []
        if not _row_us_relevance_pass(row):
            flags.append("non_us")
        if not _row_prose_quality_pass(row):
            flags.append("prose_fail")
        if not _row_has_location(row):
            flags.append("no_location")
        if _publisher_quality_rank(str(row.get("publisher_quality") or "")) >= 5:
            flags.append("low_conf_pub")
        return ",".join(flags)

    def show_recommended_review_queue(self) -> None:
        threshold = int(self.ap_filter_min_score_var.get() or 45)
        self.ap_recommended_queue_keys = build_recommended_review_queue(self.ap_candidate_rows, self.ap_candidate_status_updates, score_threshold=threshold, max_per_pillar=3, max_total=25)
        self.ap_recommended_queue_active_var.set(True)
        self.ap_filter_recommended_only_var.set(False)
        self.apply_ap_filters_and_render()

    def clear_ap_filters(self) -> None:
        self.ap_filter_status_var.set("all")
        self.ap_filter_pillar_var.set("all")
        self.ap_filter_publisher_quality_var.set("all")
        self.ap_filter_min_score_var.set(45)
        self.ap_filter_us_relevance_var.set("all")
        self.ap_filter_prose_quality_var.set("all")
        self.ap_filter_has_location_var.set(False)
        self.ap_filter_has_anchor_var.set(False)
        self.ap_filter_recommended_only_var.set(False)
        self.ap_filter_show_quarantined_var.set(False)
        self.ap_filter_show_rejected_var.set(False)
        self.ap_recommended_queue_active_var.set(False)
        self.ap_recommended_queue_keys = []
        self.ap_sort_column = "default"
        self.ap_sort_desc = False
        self.apply_ap_filters_and_render()

    def _edit_ap_status_cell(self, event: Any) -> None:
        row_id = self.ap_candidates_tree.identify_row(event.y)
        col = self.ap_candidates_tree.identify_column(event.x)
        if not row_id or col != "#2":
            return
        x, y, w, h = self.ap_candidates_tree.bbox(row_id, col)
        combo = ttk.Combobox(self.ap_candidates_tree, values=list(ALLOWED_REVIEW_STATUSES), state="readonly")
        combo.place(x=x, y=y, width=w, height=h)
        combo.set(str(self.ap_candidate_status_updates.get(row_id, "needs_review")))

        def _commit(_evt: Any | None = None) -> None:
            new_status = combo.get().strip()
            combo.destroy()
            if not new_status:
                return
            self.ap_candidate_status_updates[row_id] = new_status
            values = list(self.ap_candidates_tree.item(row_id, "values"))
            values[1] = new_status
            self.ap_candidates_tree.item(row_id, values=values)
            self.refresh_ap_review_summary()

        combo.bind("<<ComboboxSelected>>", _commit)
        combo.bind("<FocusOut>", _commit)
        combo.focus_set()

    def _confirm_bulk_change(self, count: int, label: str) -> bool:
        if count <= 10:
            return True
        return bool(messagebox.askyesno("Confirm bulk update", f"{label} will update {count} rows. Continue?"))

    def bulk_update_visible_status(self, new_status: str) -> None:
        keys = list(self.ap_visible_candidate_keys)
        if not keys:
            return
        if not self._confirm_bulk_change(len(keys), "Visible-row bulk action"):
            return
        for key in keys:
            self.ap_candidate_status_updates[key] = new_status
        self.apply_ap_filters_and_render()

    def bulk_update_selected_status(self, new_status: str) -> None:
        keys = [str(item) for item in self.ap_candidates_tree.selection()]
        if not keys:
            return
        if not self._confirm_bulk_change(len(keys), "Selected-row bulk action"):
            return
        for key in keys:
            self.ap_candidate_status_updates[key] = new_status
        self.apply_ap_filters_and_render()

    def _on_ap_candidate_select(self, _event: Any | None = None) -> None:
        selected = self.ap_candidates_tree.selection()
        if not selected:
            self.ap_details_text.delete("1.0", tk.END)
            return
        key = str(selected[0])
        by_key = {str(row.get("candidate_key") or ""): row for row in self.ap_candidate_rows}
        row = by_key.get(key)
        if not row:
            return
        raw = _row_raw(row)
        details = [
            f"Status: {_row_status(row, self.ap_candidate_status_updates)}",
            f"Reader headline: {str(row.get('reader_headline') or '').strip()}",
            f"Current development: {str(raw.get('what_happened') or raw.get('human_story_summary') or '').strip()}",
            f"Potential relevance: {str(raw.get('potential_relevance') or '').strip()}",
            f"URL: {str(row.get('url') or '').strip()}",
            f"Raw source title: {str(raw.get('title') or '').strip()}",
            f"Rejection/quarantine reason: {str(raw.get('editorial_rejection_reason') or row.get('reason') or '').strip()}",
        ]
        self.ap_details_text.delete("1.0", tk.END)
        self.ap_details_text.insert(tk.END, "\n".join(details))

    def save_ap_review_decisions(self) -> None:
        review_notes = {}
        overrides: set[str] = set()
        by_key = {row["candidate_key"]: row for row in self.ap_candidate_rows}
        for key, status in self.ap_candidate_status_updates.items():
            row = by_key.get(key)
            if not row:
                continue
            if status == "approved":
                issues = approval_validation_issues(row.get("raw", {}))
                if issues:
                    msg = "This candidate failed approval checks:\n- " + "\n- ".join(issues) + "\n\nApprove anyway?"
                    if not messagebox.askyesno("Approval override required", msg):
                        continue
                    overrides.add(key)
        save_review_decisions(self.root_dir, self.ap_review_date_var.get().strip(), self.ap_candidate_status_updates, review_notes, overrides)
        self.ap_candidate_override_keys = overrides
        self.load_ap_candidates()

    def check_ap_weekly_readiness(self) -> None:
        date_text = self.ap_review_date_var.get().strip()
        cmd = [str(python_executable(self.root_dir)), "scripts\\check_american_pressure_weekly_readiness.py", "--date", date_text]
        completed = subprocess.run(cmd, cwd=str(self.root_dir), capture_output=True, text=True, check=False)
        text = completed.stdout or completed.stderr or ""
        self.ap_readiness_var.set(text.strip())

    def generate_ap_html(self) -> None:
        date_text = self.ap_review_date_var.get().strip()
        self._run_async_command(
            [
                str(python_executable(self.root_dir)),
                "scripts\\run_weekly_american_pressure.py",
                "--date",
                date_text,
                "--source-mode",
                "both",
                "--include-approved-candidates",
            ],
            f"Generating weekly HTML for {date_text}",
        )

    def open_ap_generated_html(self) -> None:
        date_text = self.ap_review_date_var.get().strip()
        self._open(self.root_dir / "output" / "site" / "american-pressure" / "editions" / date_text / "index.html")

    def publish_ap_pages_locally(self) -> None:
        date_text = self.ap_review_date_var.get().strip()
        cmd = [str(python_executable(self.root_dir)), "scripts\\check_american_pressure_weekly_readiness.py", "--date", date_text]
        completed = subprocess.run(cmd, cwd=str(self.root_dir), capture_output=True, text=True, check=False)
        payload = json.loads(completed.stdout or "{}") if (completed.stdout or "").strip() else {}
        recommended = bool(payload.get("weekly_publish_recommended"))
        allow_thin = False
        if not recommended:
            reasons = payload.get("reasons_if_not_recommended") or []
            reason_text = "\n".join(str(item) for item in reasons)
            confirm = messagebox.askyesno("Readiness failed", f"{reason_text}\n\nThis edition failed readiness checks. Publish anyway?")
            if not confirm:
                return
            allow_thin = True
        weekly_cmd = [
            str(python_executable(self.root_dir)),
            "scripts\\run_weekly_american_pressure.py",
            "--date",
            date_text,
            "--source-mode",
            "both",
            "--include-approved-candidates",
            "--publish",
        ]
        if allow_thin:
            weekly_cmd.append("--allow-thin-edition")
        self._run_async_command(weekly_cmd, f"Publishing Pages locally for {date_text}")

    def push_ap_pages_live(self) -> None:
        status = load_status_json(self.root_dir)
        ap = ((status.get("dispatches") or {}).get("american_pressure") or {})
        latest_public = str(ap.get("latest_public_edition_date") or "n/a")
        latest_pages = str(ap.get("latest_pages_edition_date") or "n/a")
        if not messagebox.askyesno("Confirm push", f"Latest American Pressure public/pages dates: {latest_public} / {latest_pages}\n\nPush Pages live now?"):
            return
        date_text = self.ap_review_date_var.get().strip()
        self._run_async_command(
            [
                str(python_executable(self.root_dir)),
                "scripts\\run_weekly_american_pressure.py",
                "--date",
                date_text,
                "--source-mode",
                "both",
                "--include-approved-candidates",
                "--publish",
                "--push",
            ],
            f"Pushing Pages live for {date_text}",
        )

    def refresh_ap_review_summary(self) -> None:
        date_text = self.ap_review_date_var.get().strip()
        self.ap_candidate_path_var.set(str(_candidate_path(date_text, self.root_dir)))
        self.ap_review_report_path_var.set(str(_review_report_path(date_text, self.root_dir)))
        if not validate_date(date_text):
            self.ap_summary_var.set("Enter a valid week selection.")
            return
        rows = self.ap_candidate_rows or load_weekly_candidates(self.root_dir, date_text)
        source_id_counts: dict[str, int] = {}
        counts: dict[str, int] = {status: 0 for status in ALLOWED_REVIEW_STATUSES}
        for row in rows:
            status = _row_status(row, self.ap_candidate_status_updates)
            counts[status] = counts.get(status, 0) + 1
            source_record_id = str(row.get("source_record_id") or "").strip()
            if source_record_id:
                source_id_counts[source_record_id] = source_id_counts.get(source_record_id, 0) + 1
        duplicate_ids = sorted([source_id for source_id, count in source_id_counts.items() if count > 1])
        if duplicate_ids:
            self.ap_duplicate_note_var.set("Duplicate candidate IDs detected; rows were disambiguated in the review table.")
        else:
            self.ap_duplicate_note_var.set("")
        min_score_var = getattr(self, "ap_filter_min_score_var", None)
        score_threshold = int(min_score_var.get() or 45) if min_score_var is not None else 45
        recommended_count = len(build_recommended_review_queue(rows, self.ap_candidate_status_updates, score_threshold=score_threshold, max_per_pillar=3, max_total=25))
        visible_rows = getattr(self, "ap_visible_candidate_rows", [])
        visible_count = len(visible_rows or rows)
        self.ap_summary_var.set(
            f"total={len(rows)} | visible={visible_count} | recommended_queue={recommended_count} | approved={counts.get('approved', 0)} | rejected={counts.get('rejected', 0)} | quarantine={counts.get('quarantine', 0)} | maybe={counts.get('maybe', 0)} | needs_review={counts.get('needs_review', 0)}"
        )
        queue_active_var = getattr(self, "ap_recommended_queue_active_var", None)
        if queue_active_var is not None and queue_active_var.get():
            approved_candidates = [row for row in rows if _row_status(row, self.ap_candidate_status_updates) == "approved"]
            approved_current_dev = [row for row in approved_candidates if _row_us_relevance_pass(row) and _row_prose_quality_pass(row) and _row_has_url(row) and _row_has_public_pressure_angle(row)]
            approved_pillars = sorted({str(row.get("pillar") or "").strip() for row in approved_current_dev if str(row.get("pillar") or "").strip()})
            story_plus_data_est = sum(1 for row in approved_current_dev if _row_has_anchor(row))
            readiness_progress_var = getattr(self, "ap_readiness_progress_var", None)
            if readiness_progress_var is not None:
                readiness_progress_var.set(
                f"Readiness progress: {len(approved_current_dev)} approved / 4 minimum | approved pillars covered: {len(approved_pillars)} | estimated story_plus_data_count: {story_plus_data_est}"
                )
        else:
            readiness_progress_var = getattr(self, "ap_readiness_progress_var", None)
            if readiness_progress_var is not None:
                readiness_progress_var.set("")

    def _preflight_warnings(self, dispatch: str, action: str, date_text: str) -> list[str]:
        notes: list[str] = []
        if dispatch == "Cascadia" and action in ("Run dispatch", "Run with notification"):
            notes.append("Cascadia runs weekly-public historical-search workflow for selected date.")
        if dispatch == "Gaza" and action in ("Run dispatch", "Run with notification"):
            path = manual_source_path(dispatch, date_text, root=self.root_dir)
            if path is not None and not path.exists():
                notes.append(f"Missing manual sources: {path}")
        if dispatch == "American Pressure" and action in (
            "Run dispatch",
            "Run American Pressure with approved candidates",
            "Run weekly American Pressure",
            "Run with notification",
        ):
            path = manual_source_path(dispatch, date_text, root=self.root_dir)
            manual_exists = bool(path and path.exists())
            candidate_files = _candidate_files_count(date_text, self.root_dir)
            approved_count = _approved_candidates_count(date_text, self.root_dir)
            if not manual_exists and approved_count > 0:
                notes.append("No manual sources found, but approved daily candidates may still be used.")
            elif not manual_exists and approved_count == 0:
                notes.append("No manual sources or approved candidate records found for this week.")
            if candidate_files == 0:
                notes.append("No candidate files found in the 7-day window; run daily scouting.")
            elif approved_count == 0:
                notes.append("Candidate files exist, but no approved candidates are available in the 7-day window.")
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
        self.refresh_ap_review_summary()

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
        try:
            status = load_status_json(self.root_dir)
            key = slug.replace("-", "_")
            dispatch = (status.get("dispatches") or {}).get(key) or {}
            latest_public = str(dispatch.get("latest_public_edition_date") or "").strip()
            if validate_date(latest_public):
                self._open(self.root_dir / "output" / "site" / slug / "editions" / latest_public / "index.html")
                return
        except Exception:
            pass
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

