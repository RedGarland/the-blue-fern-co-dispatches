from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bluefern_dispatches.bluesky_post import maybe_post_gaza_dispatch_to_bluesky
from scripts import gaza_audio_republish as audio_republish
from scripts import gaza_manual_source_repair as manual_source_repair
from scripts import gaza_operator_status as operator_status
from scripts import preflight_repo_state as preflight
from scripts import run_gaza_daily_operator as daily_operator
from scripts.run_and_notify import notification_error_message, send_email


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ACTION_ORDER = (
    "check",
    "manual_source_check",
    "manual_source_repair",
    "dry_run_full",
    "audio_check",
    "audio_generate",
    "audio_publish",
    "publish",
    "post_bluesky",
    "email",
    "verify_live",
)
PLAN_ONLY_ACTIONS = {"manual_source_repair", "audio_generate", "audio_publish", "publish", "post_bluesky", "email"}
READ_ONLY_ACTIONS = {"check", "manual_source_check", "audio_check", "dry_run_full", "verify_live"}
DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
SAFE_DASHBOARD_ACTIONS = {
    "check",
    "manual_source_check",
    "audio_check",
    "dry_run_full",
    "verify_live",
}
WRITE_DASHBOARD_ACTIONS = {
    "manual_source_repair",
    "audio_generate",
    "audio_publish",
    "publish",
    "post_bluesky",
    "email",
}


def validate_date(value: str) -> str:
    text = str(value or "").strip()
    if not DATE_RE.match(text):
        raise ValueError(f"date must use YYYY-MM-DD: {value}")
    try:
        date_cls.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"date must use YYYY-MM-DD: {value}") from exc
    return text


def build_date_list(args: argparse.Namespace) -> list[str]:
    if args.date and (args.start_date or args.end_date):
        raise ValueError("--date cannot be combined with --start-date or --end-date")
    if bool(args.start_date) ^ bool(args.end_date):
        raise ValueError("supply both --start-date and --end-date when using a date range")
    if args.date:
        return [validate_date(args.date)]
    if not args.start_date or not args.end_date:
        raise ValueError("require either --date or both --start-date and --end-date")
    start = date_cls.fromisoformat(validate_date(args.start_date))
    end = date_cls.fromisoformat(validate_date(args.end_date))
    if end < start:
        raise ValueError("--end-date cannot be before --start-date")
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _selected_actions(args: argparse.Namespace) -> list[str]:
    chosen: list[str] = []
    for name in ACTION_ORDER:
        if getattr(args, name.replace("-", "_"), False):
            chosen.append(name)
    return chosen


def _preflight_report() -> dict[str, Any]:
    return preflight.build_preflight_report(ROOT, ROOT / "bluefern-dispatches-pages")


def _repo_state_from_preflight(report: dict[str, Any], key: str) -> dict[str, Any]:
    repo = report.get(key) or {}
    summary = repo.get("summary") or {}
    entries = list(repo.get("entries") or [])
    risky = list(summary.get("risky_entries") or [])
    allowed = list(summary.get("allowed_entries") or [])
    if risky:
        state = "risky"
    elif entries:
        state = "allowed-only"
    else:
        state = "clean"
    return {
        "path": repo.get("path"),
        "branch": repo.get("branch"),
        "state": state,
        "clean": state == "clean",
        "allowed_only": state == "allowed-only",
        "risky": state == "risky",
        "entry_count": int(summary.get("entry_count") or len(entries)),
        "risky_entries": risky,
        "allowed_entries": allowed,
    }


def _manual_source_status(date_text: str) -> dict[str, Any]:
    report = operator_status.summarize_manual_sources(ROOT, date_text)
    return {
        "path": report.get("path"),
        "status": report.get("status"),
        "record_count": report.get("record_count", 0),
        "errors": list(report.get("errors") or []),
        "next_action": report.get("next_action"),
    }


def _readiness_report(date_text: str) -> dict[str, Any]:
    return operator_status.build_report(
        ROOT,
        date_text,
        pages_repo=ROOT / "bluefern-dispatches-pages",
        pages_branch="gh-pages",
        live_checks=False,
    )


def _audio_args(date_text: str, *, check: bool = False, generate: bool = False, publish: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        date=date_text,
        pages_repo=ROOT / "bluefern-dispatches-pages",
        pages_branch="gh-pages",
        tts_provider=None,
        audio_model=None,
        audio_voice=None,
        audio_format="mp3",
        commit=publish,
        push=False,
        no_live=True,
        json=False,
        check=check,
        generate=generate,
        publish=publish,
    )


def _planned_result(action: str, date_text: str, command: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "action": action,
        "date": date_text,
        "execution": "plan",
        "ok": True,
        "status": "planned",
        "command": command,
        "details": details or {},
    }


def _run_dry_run_full(date_text: str) -> dict[str, Any]:
    powershell = "powershell.exe"
    cmd = [
        powershell,
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "run_runner_dispatch.ps1"),
        "-Dispatch",
        "gaza",
        "-Date",
        date_text,
        "-DryRunFull",
    ]
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    workspace_match = re.search(r"Isolated Gaza dry-run workspace(?: retained at)?:\s*(.+)", output)
    return {
        "action": "dry_run_full",
        "date": date_text,
        "execution": "run",
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": subprocess.list2cmdline(cmd),
        "details": {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "workspace": workspace_match.group(1).strip() if workspace_match else None,
        },
    }


def _run_check(date_text: str) -> dict[str, Any]:
    report = _readiness_report(date_text)
    ok = report.get("overall_status") == "healthy"
    return {
        "action": "check",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": report.get("overall_status"),
        "command": f"python scripts\\gaza_operator_status.py --date {date_text} --no-live",
        "details": report,
    }


def _manual_sources_need_attention(manual_sources: dict[str, Any]) -> bool:
    if manual_sources.get("status") != "valid":
        return True
    return int(manual_sources.get("record_count") or 0) <= 0


def _run_manual_source_check(date_text: str) -> dict[str, Any]:
    report = manual_source_repair.build_report(date_text, apply=False)
    ok = report.get("status") == "valid"
    return {
        "action": "manual_source_check",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": report.get("status"),
        "command": f"python scripts\\gaza_manual_source_repair.py --date {date_text} --check",
        "details": report,
    }


def _run_manual_source_repair(date_text: str, *, production: bool) -> dict[str, Any]:
    command = f"python scripts\\gaza_manual_source_repair.py --date {date_text} --apply"
    if not production:
        return _planned_result("manual_source_repair", date_text, command, details={"note": "test mode does not write manual source files"})
    report = manual_source_repair.build_report(date_text, apply=True)
    ok = report.get("status") in {"valid", "repaired"}
    return {
        "action": "manual_source_repair",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": report.get("status"),
        "command": command,
        "details": report,
    }


def _run_audio_check(date_text: str) -> dict[str, Any]:
    report = audio_republish.build_report(_audio_args(date_text, check=True))
    ok = bool(report.get("ok"))
    return {
        "action": "audio_check",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": "ready" if ok else "repair needed",
        "command": f"python scripts\\gaza_audio_republish.py --date {date_text} --check --no-live",
        "details": report,
    }


def _run_audio_generate(date_text: str, *, production: bool) -> dict[str, Any]:
    command = f"python scripts\\gaza_audio_republish.py --date {date_text} --generate"
    if not production:
        return _planned_result("audio_generate", date_text, command, details={"note": "test mode does not write audio outputs"})
    report = audio_republish.build_report(_audio_args(date_text, generate=True))
    ok = bool(report.get("ok"))
    return {
        "action": "audio_generate",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": "ready" if ok else "repair needed",
        "command": command,
        "details": report,
    }


def _run_audio_publish(date_text: str, *, production: bool) -> dict[str, Any]:
    command = f"python scripts\\gaza_audio_republish.py --date {date_text} --publish --commit"
    if not production:
        return _planned_result("audio_publish", date_text, command, details={"note": "test mode does not modify Pages"})
    report = audio_republish.build_report(_audio_args(date_text, publish=True))
    ok = bool(report.get("ok"))
    return {
        "action": "audio_publish",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": "published" if ok else "failed",
        "command": command,
        "details": report,
    }


def _run_publish(date_text: str, *, production: bool) -> dict[str, Any]:
    command = f"python scripts\\run_gaza_daily_operator.py --date {date_text}"
    if not production:
        return _planned_result("publish", date_text, command, details={"note": "test mode does not modify Pages"})
    args = daily_operator.parse_args(["--date", date_text])
    report = daily_operator.run_operator(args)
    ok = bool(report.get("ok"))
    return {
        "action": "publish",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": str(report.get("operator_status") or ("published" if ok else "failed")).lower(),
        "command": command,
        "details": report,
    }


def _run_post_bluesky(date_text: str, *, production: bool, publish_ok: bool | None) -> dict[str, Any]:
    public_url = f"https://dispatches.thebluefernco.com/gaza/editions/{date_text}/"
    command = f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only"
    if not production:
        planned = maybe_post_gaza_dispatch_to_bluesky(
            edition_date=date_text,
            public_url=public_url,
            run_succeeded=True,
            post_requested=True,
            project_root=ROOT,
            force_post=False,
            allow_publish=False,
        )
        return {
            "action": "post_bluesky",
            "date": date_text,
            "execution": "plan",
            "ok": True,
            "status": str(planned.get("status") or "planned"),
            "command": command,
            "details": planned,
        }
    bluesky = maybe_post_gaza_dispatch_to_bluesky(
        edition_date=date_text,
        public_url=public_url,
        run_succeeded=True if publish_ok is None else bool(publish_ok),
        post_requested=True,
        project_root=ROOT,
        force_post=False,
        allow_publish=True,
    )
    ok = str(bluesky.get("status") or "") == "success"
    return {
        "action": "post_bluesky",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": str(bluesky.get("status") or "skipped"),
        "command": command,
        "details": bluesky,
    }


def _email_subject(date_text: str, mode: str) -> str:
    return f"[Blue Fern Dispatches] Gaza command center - {date_text} ({mode})"


def _email_body(date_result: dict[str, Any]) -> str:
    lines = [
        f"GAZA COMMAND CENTER - {date_result['date']}",
        f"Mode: {date_result['mode']}",
        "",
        "Repos",
        f"- Source: {date_result['repos']['source']['state']}",
        f"- Pages: {date_result['repos']['pages']['state']}",
        "",
        "Manual sources",
        f"- {date_result['manual_sources']['status']}",
        "",
        "Actions",
    ]
    for action in date_result.get("actions") or []:
        lines.append(f"- {action['action']}: {action['status']}")
    lines.extend(["", "Next safe action", f"- {date_result['next_safe_action']}"])
    return "\n".join(lines)


def _run_email(date_result: dict[str, Any], *, production: bool) -> dict[str, Any]:
    email_to = [addr.strip() for addr in os.getenv("EMAIL_TO", "").split(",") if addr.strip()]
    subject = _email_subject(date_result["date"], date_result["mode"])
    command = f"python scripts\\run_gaza_daily_operator.py --date {date_result['date']} --email-report"
    if not production:
        return {
            "action": "email",
            "date": date_result["date"],
            "execution": "plan",
            "ok": True,
            "status": "planned",
            "command": command,
            "details": {
                "subject": subject,
                "recipients": email_to,
                "status": "planned",
            },
        }
    try:
        send_email(subject, _email_body(date_result), date_result["date"], smtp_debug=False)
    except Exception as exc:  # noqa: BLE001
        return {
            "action": "email",
            "date": date_result["date"],
            "execution": "run",
            "ok": False,
            "status": "failed",
            "command": command,
            "details": {
                "subject": subject,
                "recipients": email_to,
                "error": notification_error_message(exc),
            },
        }
    return {
        "action": "email",
        "date": date_result["date"],
        "execution": "run",
        "ok": True,
        "status": "sent",
        "command": command,
        "details": {
            "subject": subject,
            "recipients": email_to,
        },
    }


def _run_verify_live(date_text: str) -> dict[str, Any]:
    report = operator_status.build_report(
        ROOT,
        date_text,
        pages_repo=ROOT / "bluefern-dispatches-pages",
        pages_branch="gh-pages",
        live_checks=True,
    )
    ok = report.get("overall_status") == "healthy"
    return {
        "action": "verify_live",
        "date": date_text,
        "execution": "run",
        "ok": ok,
        "status": report.get("overall_status"),
        "command": f"python scripts\\gaza_operator_status.py --date {date_text}",
        "details": report,
    }


def _run_action(action: str, date_text: str, args: argparse.Namespace, *, publish_ok: bool | None) -> dict[str, Any]:
    production = bool(args.production)
    if action == "check":
        return _run_check(date_text)
    if action == "manual_source_check":
        return _run_manual_source_check(date_text)
    if action == "manual_source_repair":
        return _run_manual_source_repair(date_text, production=production)
    if action == "dry_run_full":
        return _run_dry_run_full(date_text)
    if action == "audio_check":
        return _run_audio_check(date_text)
    if action == "audio_generate":
        return _run_audio_generate(date_text, production=production)
    if action == "audio_publish":
        return _run_audio_publish(date_text, production=production)
    if action == "publish":
        return _run_publish(date_text, production=production)
    if action == "post_bluesky":
        return _run_post_bluesky(date_text, production=production, publish_ok=publish_ok)
    if action == "verify_live":
        return _run_verify_live(date_text)
    raise ValueError(f"unsupported action: {action}")


def _build_next_safe_action(date_result: dict[str, Any]) -> str:
    for action in date_result.get("actions") or []:
        if not action.get("ok") and action.get("status") != "planned":
            if action["action"] in {"check", "manual_source_check", "verify_live"}:
                return action.get("details", {}).get("next_action") or f"Inspect {action['action'].replace('_', '-')} output and rerun."
            return f"Fix the failed {action['action'].replace('_', '-')} action and rerun."
    readiness = date_result.get("readiness") or {}
    readiness_status = str(readiness.get("overall_status") or "").lower()
    manual_sources = date_result.get("manual_sources") or {}
    if any(action.get("action") == "dry_run_full" for action in date_result.get("actions") or []):
        if _manual_sources_need_attention(manual_sources):
            return "Dry-run mechanism passed. Run --check to determine publish readiness; no source-backed manual input is present for this date."
        if readiness_status and readiness_status != "healthy":
            return readiness.get("next_action") or "Dry-run mechanism passed. Run --check to determine publish readiness."
        if readiness_status == "healthy":
            return "No action needed."
        return "Dry-run mechanism passed. Run --check to determine publish readiness."
    if any(action.get("execution") == "plan" for action in date_result.get("actions") or []):
        return "Run the planned actions with --production."
    if readiness_status == "healthy":
        return "No action needed."
    if readiness.get("next_action"):
        return readiness["next_action"]
    return "Run --check to determine publish readiness."


def _render_repo_line(label: str, repo: dict[str, Any]) -> str:
    state = repo.get("state") or "unknown"
    line = f"- {label}: {state}"
    if repo.get("branch"):
        line += f" on {repo['branch']}"
    if repo.get("path"):
        line += f" ({repo['path']})"
    return line


def _render_action_line(action: dict[str, Any]) -> str:
    status = action.get("status") or ("planned" if action.get("execution") == "plan" else "unknown")
    title = action["action"].replace("_", " ").title()
    lines = [f"- {title}: {status}"]
    details = action.get("details") or {}
    if action["action"] == "dry_run_full" and details.get("workspace"):
        lines.append(f"  - isolated workspace: {details['workspace']}")
    if action["action"] == "post_bluesky" and details.get("card_title"):
        lines.append(f"  - card title: {details['card_title']}")
    if action["action"] == "email":
        if details.get("subject"):
            lines.append(f"  - subject: {details['subject']}")
        if details.get("recipients"):
            lines.append(f"  - recipients: {', '.join(details['recipients'])}")
    if action.get("command"):
        lines.append(f"  - command: {action['command']}")
    return "\n".join(lines)


def _run_date(date_text: str, args: argparse.Namespace, selected_actions: list[str]) -> dict[str, Any]:
    preflight_report = _preflight_report()
    source_repo = _repo_state_from_preflight(preflight_report, "source_repo")
    pages_repo = _repo_state_from_preflight(preflight_report, "pages_repo")
    manual_sources = _manual_source_status(date_text)
    readiness_report = _readiness_report(date_text)
    date_result: dict[str, Any] = {
        "date": date_text,
        "mode": "production" if args.production else "test",
        "selected_actions": list(selected_actions),
        "repos": {"source": source_repo, "pages": pages_repo},
        "manual_sources": manual_sources,
        "readiness": readiness_report,
        "preflight": preflight_report,
        "actions": [],
        "ok": True,
        "stopped_early": False,
        "next_safe_action": "",
        "readiness_ok": bool(readiness_report.get("overall_status") == "healthy"),
        "readiness_status": readiness_report.get("overall_status") or "unknown",
    }

    if not preflight_report.get("ok") and (source_repo.get("risky") or pages_repo.get("risky")):
        date_result["ok"] = False
        date_result["stopped_early"] = True
        date_result["next_safe_action"] = "Resolve the risky repo state before continuing."
        return date_result

    publish_ok: bool | None = None
    email_selected = "email" in selected_actions
    for action in ACTION_ORDER:
        if action not in selected_actions or action == "email":
            continue
        result = _run_action(action, date_text, args, publish_ok=publish_ok)
        date_result["actions"].append(result)
        if action == "publish":
            publish_ok = bool(result.get("ok"))
        if not result.get("ok"):
            date_result["ok"] = False
            if args.production and not args.continue_on_error:
                date_result["stopped_early"] = True
                break

    if email_selected and not date_result["stopped_early"]:
        email_result = _run_email(date_result, production=bool(args.production))
        date_result["actions"].append(email_result)
        if not email_result.get("ok"):
            date_result["ok"] = False
            if args.production and not args.continue_on_error:
                date_result["stopped_early"] = True

    date_result["next_safe_action"] = _build_next_safe_action(date_result)
    return date_result


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    dates = build_date_list(args)
    selected_actions = _selected_actions(args)
    results: list[dict[str, Any]] = []
    failed_dates: list[str] = []
    for date_text in dates:
        result = _run_date(date_text, args, selected_actions)
        results.append(result)
        if not result.get("ok"):
            failed_dates.append(date_text)
            if args.production and not args.continue_on_error:
                break
    return {
        "mode": "production" if args.production else "test",
        "date_scope": {
            "date": args.date,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "dates": dates,
        },
        "selected_actions": list(selected_actions),
        "dates": results,
        "ok": not failed_dates,
        "readiness_ok": all(bool(result.get("readiness_ok")) for result in results),
        "failed_dates": failed_dates,
        "processed_dates": [result["date"] for result in results],
        "aggregate": {
            "date_count": len(results),
            "failed_date_count": len(failed_dates),
            "succeeded_date_count": len(results) - len(failed_dates),
            "selected_action_count": len(selected_actions),
            "readiness_status": "healthy" if all(bool(result.get("readiness_ok")) for result in results) else "needs_attention",
        },
    }


def _dashboard_output_paths() -> dict[str, Path]:
    dashboard_dir = ROOT / "output" / "review" / "gaza"
    return {
        "directory": dashboard_dir,
        "html": dashboard_dir / "operator-dashboard.html",
        "json": dashboard_dir / "operator-dashboard.json",
    }


def _dashboard_command_snippets(date_text: str) -> dict[str, list[dict[str, Any]]]:
    publish_command = f"python scripts\\gaza_command_center.py --date {date_text} --publish --production"
    return {
        "website_publishing": [
            {
                "label": "Dry run",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --dry-run-full",
                "dangerous": False,
                "requires_explicit_action": False,
                "note": "Read-only planning and readiness preview.",
            },
            {
                "label": "Local publish, no push",
                "command": publish_command,
                "dangerous": True,
                "requires_explicit_action": True,
                "note": "Writes the local Pages repo only. Push remains separate.",
            },
            {
                "label": "Pages push",
                "command": f'git -C ".\\bluefern-dispatches-pages" push origin gh-pages',
                "dangerous": True,
                "requires_explicit_action": True,
                "note": "Push the already-reviewed Pages commit only after explicit approval.",
            },
        ],
        "audio": [
            {
                "label": "Audio check",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --audio-check",
                "dangerous": False,
                "requires_explicit_action": False,
                "note": "Read-only audio status and artifact inspection.",
            },
            {
                "label": "Audio generate",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --audio-generate --production",
                "dangerous": True,
                "requires_explicit_action": True,
                "note": "Generates local audio artifacts; no push by itself.",
            },
            {
                "label": "Audio publish dry run",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --audio-publish",
                "dangerous": False,
                "requires_explicit_action": False,
                "note": "Plans the audio publish path without writing Pages.",
            },
            {
                "label": "Audio publish commit/no-push",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --audio-publish --production",
                "dangerous": True,
                "requires_explicit_action": True,
                "note": "Writes the audio publish output locally only.",
            },
        ],
        "bluesky": [
            {
                "label": "Preview",
                "command": f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only --post-bluesky --dry-run",
                "dangerous": False,
                "requires_explicit_action": False,
                "note": "Preview-only run through the post-only operator path; no post is published.",
            },
            {
                "label": "Post",
                "command": f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only --post-bluesky",
                "dangerous": True,
                "requires_explicit_action": True,
                "note": "Posts through the post-only operator path after publication is ready.",
            },
            {
                "label": "Force repost",
                "command": f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only --post-bluesky --force-bluesky-post",
                "dangerous": True,
                "requires_explicit_action": True,
                "note": "Use only when a previous post was deleted or intentionally replaced.",
            },
        ],
        "verification": [
            {
                "label": "Live verification",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --verify-live",
                "dangerous": False,
                "requires_explicit_action": False,
                "note": "Read-only live URL verification.",
            }
        ],
        "safe_checklist": [
            {
                "label": "Check readiness",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --check",
                "dangerous": False,
                "requires_explicit_action": False,
            },
            {
                "label": "Manual source check",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --manual-source-check",
                "dangerous": False,
                "requires_explicit_action": False,
            },
            {
                "label": "Dry run",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --dry-run-full",
                "dangerous": False,
                "requires_explicit_action": False,
            },
            {
                "label": "Pages publish",
                "command": publish_command,
                "dangerous": True,
                "requires_explicit_action": True,
            },
            {
                "label": "Pages push",
                "command": f'git -C ".\\bluefern-dispatches-pages" push origin gh-pages',
                "dangerous": True,
                "requires_explicit_action": True,
            },
            {
                "label": "Bluesky preview",
                "command": f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only --post-bluesky --dry-run",
                "dangerous": False,
                "requires_explicit_action": False,
            },
            {
                "label": "Bluesky post",
                "command": f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only --post-bluesky",
                "dangerous": True,
                "requires_explicit_action": True,
            },
            {
                "label": "Force repost",
                "command": f"python scripts\\run_gaza_daily_operator.py --date {date_text} --post-bluesky-only --post-bluesky --force-bluesky-post",
                "dangerous": True,
                "requires_explicit_action": True,
            },
            {
                "label": "Live verification",
                "command": f"python scripts\\gaza_command_center.py --date {date_text} --verify-live",
                "dangerous": False,
                "requires_explicit_action": False,
            },
        ],
    }


def build_dashboard_state(args: argparse.Namespace) -> dict[str, Any]:
    if args.production:
        raise ValueError("--dashboard cannot be combined with --production")
    selected_actions = _selected_actions(args)
    if selected_actions:
        raise ValueError("--dashboard is read-only and cannot be combined with action flags")
    dates = build_date_list(args)
    if len(dates) != 1:
        raise ValueError("--dashboard requires a single --date value")
    date_text = dates[0]
    report = build_report(args)
    date_result = report["dates"][0]
    readiness = date_result["readiness"]
    pages_snapshot = daily_operator._pages_repo_snapshot(ROOT / "bluefern-dispatches-pages")
    source_repo = dict(date_result["repos"]["source"])
    pages_repo = dict(date_result["repos"]["pages"])
    source_repo_tracking = dict(readiness.get("source_repo") or {})
    pages_repo_tracking = dict(readiness.get("pages_repo") or {})
    source_repo["tracking"] = source_repo_tracking
    pages_repo["tracking"] = pages_repo_tracking
    pages_repo["snapshot"] = pages_snapshot
    source_repo_blocks_publish = bool(source_repo.get("risky"))
    pages_repo_blocks_publish = bool(
        pages_repo.get("risky")
        or pages_repo.get("dirty")
        or int(pages_repo_tracking.get("ahead") or 0) > 0
    )
    overall_status = str(readiness.get("overall_status") or "action_needed")
    manual_sources = dict(date_result["manual_sources"])
    source_count = int(readiness.get("source_repo", {}).get("summary", {}).get("entry_count") or 0)
    publishable_update_available = overall_status == "healthy"
    manual_source_status = str(manual_sources.get("status") or "")
    manual_source_next_action = str(manual_sources.get("next_action") or "").strip()
    if publishable_update_available:
        next_safe_action = str(date_result["next_safe_action"] or "No action needed.")
    elif manual_source_status and manual_source_status != "valid" and manual_source_next_action:
        next_safe_action = manual_source_next_action
    else:
        next_safe_action = (
            "No publishable source-backed Gaza update is currently available. "
            "Add valid manual sources or wait for the next source collection."
        )
    state = {
        "date": date_text,
        "mode": "dashboard",
        "overall_status": overall_status,
        "readiness_status": overall_status,
        "next_safe_action": next_safe_action,
        "publishable_update_available": publishable_update_available,
        "manual_source_update_available": bool(manual_source_status == "valid" and int(manual_sources.get("record_count") or 0) > 0),
        "source_repo_blocks_publish": source_repo_blocks_publish,
        "pages_repo_blocks_publish": pages_repo_blocks_publish,
        "source_repo": source_repo,
        "pages_repo": pages_repo,
        "manual_sources": manual_sources,
        "readiness": readiness,
        "command_center_report": report,
        "command_snippets": _dashboard_command_snippets(date_text),
        "output_paths": {key: str(path) for key, path in _dashboard_output_paths().items()},
        "source_count": source_count,
        "pages_commit_subject": pages_snapshot.get("head_subject"),
        "pages_commit_sha": pages_snapshot.get("head_sha"),
        "pages_repo_ahead": pages_snapshot.get("ahead"),
        "pages_repo_behind": pages_snapshot.get("behind"),
    }
    return state


def _dashboard_status_label(status: str) -> str:
    return "Ready" if status == "healthy" else "Needs attention"


def _dashboard_kv(items: list[tuple[str, Any]]) -> str:
    parts = ["<dl class=\"dashboard-kv\">"]
    for label, value in items:
        parts.append(f"<dt>{html.escape(str(label))}</dt>")
        if isinstance(value, bool):
            rendered = "yes" if value else "no"
        elif value is None:
            rendered = "<none>"
        else:
            rendered = str(value)
        parts.append(f"<dd>{html.escape(rendered)}</dd>")
    parts.append("</dl>")
    return "\n".join(parts)


def _dashboard_command_card(command: dict[str, Any]) -> str:
    classes = ["command-card"]
    if command.get("dangerous"):
        classes.append("dangerous")
    label = html.escape(str(command.get("label") or "Command"))
    note = html.escape(str(command.get("note") or ""))
    command_text = html.escape(str(command.get("command") or ""))
    badge = ""
    if command.get("dangerous"):
        badge = '<span class="danger-badge">Requires explicit operator action</span>'
    return (
        f'<article class="{" ".join(classes)}">'
        f"<h3>{label}</h3>"
        f"{badge}"
        f"<p>{note}</p>"
        f"<pre><code>{command_text}</code></pre>"
        "</article>"
    )


def _dashboard_section(title: str, body: str) -> str:
    return f"<section><h2>{html.escape(title)}</h2>{body}</section>"


def render_dashboard_html(state: dict[str, Any]) -> str:
    source_repo = state["source_repo"]
    pages_repo = state["pages_repo"]
    manual_sources = state["manual_sources"]
    readiness = state["readiness"]
    pages_snapshot = pages_repo.get("snapshot") or {}
    live = readiness.get("live") or {}
    recent_logs = readiness.get("recent_logs") or {}
    recent_fields = recent_logs.get("merged_fields") or {}
    commands = state["command_snippets"]

    header = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Gaza Operator Dashboard - {html.escape(state['date'])}</title>",
        "<style>",
        "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f5f1e8;color:#1f2328;}",
        ".wrap{max-width:1180px;margin:0 auto;padding:32px 20px 64px;}",
        "header{padding:28px 24px;border-radius:20px;background:linear-gradient(135deg,#223327,#35563e);color:#f7f5ee;box-shadow:0 14px 40px rgba(0,0,0,.14);}",
        "header h1{margin:0 0 8px;font-size:2rem;}",
        "header p{margin:6px 0;max-width:78ch;line-height:1.5;}",
        ".summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px;margin-top:16px;align-items:start;}",
        ".command-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:16px;align-items:start;}",
        "section{margin-top:28px;background:#fff;border:1px solid #dad3c7;border-radius:18px;padding:20px 20px 8px;box-shadow:0 6px 24px rgba(0,0,0,.05);}",
        "section h2{margin:0 0 12px;font-size:1.25rem;}",
        ".dashboard-kv{display:grid;grid-template-columns:minmax(160px,220px) minmax(0,1fr);gap:8px 14px;margin:0 0 12px;}",
        ".dashboard-kv dt{font-weight:700;color:#415043;}",
        ".dashboard-kv dd{margin:0;color:#1f2328;min-width:0;overflow-wrap:anywhere;word-break:normal;}",
        ".command-card{padding:14px 14px 4px;border-radius:14px;border:1px solid #d7cfbf;background:#faf8f2;}",
        ".command-card.dangerous{border-color:#c47b64;background:#fff7f3;}",
        ".command-card h3{margin:0 0 8px;font-size:1rem;}",
        ".command-card p{margin:0 0 10px;line-height:1.45;min-width:0;overflow-wrap:anywhere;}",
        ".command-card pre{margin:0 0 12px;overflow-x:auto;overflow-y:hidden;padding:12px 14px;border-radius:12px;background:#1f2328;color:#f7f5ee;white-space:pre;}",
        ".command-card code{display:block;min-width:max-content;}",
        ".summary-grid > section,.command-grid > .command-card{min-width:0;}",
        ".danger-badge{display:inline-block;margin:0 0 10px;padding:4px 8px;border-radius:999px;background:#7b3f2f;color:#fff;font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;}",
        ".muted{color:#5f665e;}",
        ".status-pill{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700;background:#dde9d7;color:#223327;}",
        ".status-pill.needs{background:#f5d9d2;color:#7b3f2f;}",
        "ul{margin:0 0 12px 20px;}",
        "li{margin:0 0 8px;line-height:1.45;}",
        "code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;}",
        "</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        "<header>",
        f"<h1>Gaza Operator Dashboard</h1>",
        f"<p><strong>Date:</strong> {html.escape(state['date'])}</p>",
        f"<p><strong>Overall status:</strong> <span class=\"status-pill{' needs' if state['overall_status'] != 'healthy' else ''}\">{html.escape(_dashboard_status_label(state['overall_status']))}</span></p>",
        f"<p><strong>Readiness:</strong> {html.escape(str(state['readiness_status']))}</p>",
        f"<p><strong>Next safe action:</strong> {html.escape(str(state['next_safe_action']))}</p>",
        f"<p class=\"muted\">Read-only dashboard output written to {html.escape(state['output_paths']['html'])} and {html.escape(state['output_paths']['json'])}.</p>",
        "</header>",
        '<div class="summary-grid">',
        _dashboard_section(
            "Overall status",
            _dashboard_kv(
                [
                    ("Date", state["date"]),
                    ("Mode", state["mode"]),
                    ("Readiness status", state["readiness_status"]),
                    ("Publishable source-backed update", state["publishable_update_available"]),
                    ("Next safe action", state["next_safe_action"]),
                ]
            ),
        ),
        _dashboard_section(
            "Repo safety",
            _dashboard_kv(
                [
                    ("Source repo branch", source_repo.get("branch")),
                    ("Source repo state", source_repo.get("state")),
                    ("Source repo blocks publish", state["source_repo_blocks_publish"]),
                    ("Source repo head", source_repo.get("tracking", {}).get("head_sha")),
                    ("Pages repo branch", pages_repo.get("branch")),
                    ("Pages repo state", pages_repo.get("state")),
                    ("Pages repo blocks publish", state["pages_repo_blocks_publish"]),
                    ("Current Pages commit", state["pages_commit_sha"]),
                    ("Current Pages subject", state["pages_commit_subject"]),
                    ("Pages ahead", state["pages_repo_ahead"]),
                    ("Pages behind", state["pages_repo_behind"]),
                ]
            ),
        ),
        _dashboard_section(
            "Manual sources",
            _dashboard_kv(
                [
                    ("Manual source path", manual_sources.get("path")),
                    ("Status", manual_sources.get("status")),
                    ("Record count", manual_sources.get("record_count")),
                    ("Manual update available", state["manual_source_update_available"]),
                    ("Next action", manual_sources.get("next_action")),
                ]
                + [("Error", error) for error in manual_sources.get("errors") or []]
            ),
        ),
        _dashboard_section(
            "Gaza readiness",
            _dashboard_kv(
                [
                    ("Overall readiness", readiness.get("overall_status")),
                    ("Source count", state["source_count"]),
                    ("Latest runner status", recent_fields.get("operator_status") or recent_logs.get("latest_status")),
                    ("Latest runner next action", recent_fields.get("next_action") or recent_logs.get("latest_next_action")),
                    ("Ready to publish", readiness.get("overall_status") == "healthy"),
                    ("Source-backed update available", state["publishable_update_available"]),
                ]
            ),
        ),
        _dashboard_section(
            "Website publishing",
            _dashboard_kv(
                [
                    ("Current publication readiness", readiness.get("overall_status")),
                    ("Live verification status", live.get("status") if live.get("enabled") else "skipped"),
                    ("Live verification healthy", live.get("ok") if live.get("enabled") else None),
                    ("Edition URL", f"https://dispatches.thebluefernco.com/gaza/editions/{state['date']}/"),
                    ("Archive URL", "https://dispatches.thebluefernco.com/gaza/archive.html"),
                    ("Homepage URL", "https://dispatches.thebluefernco.com/gaza/"),
                ]
            )
            + '<div class="command-grid">'
            + "".join(_dashboard_command_card(command) for command in commands["website_publishing"])
            + "</div>",
        ),
        _dashboard_section(
            "Audio",
            _dashboard_kv(
                [
                    ("Audio status", recent_fields.get("audio_status") or "unknown"),
                    ("Audio transcript present", readiness.get("pages_artifacts", {}).get("audio_transcript", {}).get("exists")),
                    ("Audio MP3 present", readiness.get("pages_artifacts", {}).get("audio_mp3", {}).get("exists")),
                ]
            )
            + '<div class="command-grid">'
            + "".join(_dashboard_command_card(command) for command in commands["audio"])
            + "</div>",
        ),
        _dashboard_section(
            "Bluesky",
            _dashboard_kv(
                [
                    ("Latest Bluesky status", recent_fields.get("bluesky_status") or readiness.get("recent_logs", {}).get("latest_status")),
                    ("Post URI", recent_fields.get("bluesky_post_uri")),
                    ("Force repost note", "Use only when a previous post was deleted or intentionally replaced."),
                ]
            )
            + '<div class="command-grid">'
            + "".join(_dashboard_command_card(command) for command in commands["bluesky"])
            + "</div>",
        ),
        _dashboard_section(
            "Live verification",
            _dashboard_kv(
                [
                    ("Edition URL", f"https://dispatches.thebluefernco.com/gaza/editions/{state['date']}/"),
                    ("Archive URL", "https://dispatches.thebluefernco.com/gaza/archive.html"),
                    ("Homepage URL", "https://dispatches.thebluefernco.com/gaza/"),
                    ("Live status", live.get("status") if live.get("enabled") else "skipped"),
                    ("Recent live HTTP ok", recent_fields.get("live_http_ok")),
                    ("Recent live archive ok", recent_fields.get("live_archive_ok")),
                ]
            )
            + '<div class="command-grid">'
            + "".join(_dashboard_command_card(command) for command in commands["verification"])
            + "</div>",
        ),
        _dashboard_section(
            "Safe command checklist",
            "<p class=\"muted\">Copy and paste the commands below. Items marked as dangerous require explicit operator action.</p>"
            + '<div class="command-grid">'
            + "".join(_dashboard_command_card(command) for command in commands["safe_checklist"])
            + "</div>",
        ),
        "</div>",
        "</div>",
        "</body>",
        "</html>",
    ]
    return "\n".join(header)


def write_dashboard_files(state: dict[str, Any]) -> dict[str, str]:
    paths = _dashboard_output_paths()
    paths["directory"].mkdir(parents=True, exist_ok=True)
    html_text = render_dashboard_html(state)
    json_text = json.dumps(state, indent=2)
    paths["html"].write_text(html_text, encoding="utf-8")
    paths["json"].write_text(json_text, encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def _print_dashboard_summary(state: dict[str, Any]) -> None:
    print(f"Gaza dashboard status: {_dashboard_status_label(state['overall_status'])}")
    print(f"Date: {state['date']}")
    print(f"Next safe action: {state['next_safe_action']}")
    print(f"Dashboard HTML: {state['output_paths']['html']}")
    print(f"Dashboard JSON: {state['output_paths']['json']}")


def _selected_actions_label(selected_actions: list[str]) -> str:
    return ", ".join(action.replace("_", "-") for action in selected_actions) if selected_actions else "none selected"


def render_text_report(report: dict[str, Any]) -> str:
    dates = report.get("dates") or []
    if len(dates) == 1:
        header = dates[0]["date"]
    elif dates:
        header = f"{dates[0]['date']} to {dates[-1]['date']}"
    else:
        header = report.get("date_scope", {}).get("date") or "range"
    lines = [
        f"GAZA COMMAND CENTER - {header}",
        f"Mode: {report['mode']}",
        f"Actions: {_selected_actions_label(report.get('selected_actions') or [])}",
        "",
    ]
    for date_result in dates:
        if len(dates) > 1:
            lines.append(f"Date: {date_result['date']}")
        lines.append("Repos")
        lines.append(_render_repo_line("Source", date_result["repos"]["source"]))
        lines.append(_render_repo_line("Pages", date_result["repos"]["pages"]))
        lines.append("")
        lines.append("Manual sources")
        lines.append(f"- {date_result['manual_sources']['status']}")
        if date_result["manual_sources"].get("record_count") is not None:
            lines.append(f"- records: {date_result['manual_sources']['record_count']}")
        if date_result["manual_sources"].get("errors"):
            lines.append(f"- errors: {', '.join(date_result['manual_sources']['errors'])}")
        if date_result["manual_sources"].get("next_action"):
            lines.append(f"- next action: {date_result['manual_sources']['next_action']}")
        lines.append("")
        if date_result.get("actions"):
            lines.append("Actions")
            for action in date_result["actions"]:
                lines.append(_render_action_line(action))
            lines.append("")
        else:
            lines.append("No actions selected.")
            lines.append("")
    lines.append("Next safe action")
    lines.append(f"- {date_result['next_safe_action']}")
    lines.append("")
    lines.append("Aggregate")
    lines.append(f"- dates processed: {report['aggregate']['date_count']}")
    lines.append(f"- succeeded: {report['aggregate']['succeeded_date_count']}")
    lines.append(f"- failed: {report['aggregate']['failed_date_count']}")
    lines.append(f"- execution: {'ok' if report['ok'] else 'needs attention'}")
    lines.append(f"- readiness: {report['aggregate']['readiness_status']}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operator command center for Gaza daily actions.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test", action="store_true", help="Plan public actions instead of executing them.")
    mode.add_argument("--production", action="store_true", help="Allow the selected public and write-capable actions to run.")
    parser.add_argument("--dashboard", action="store_true", help="Write the local Gaza operator dashboard under output/review/gaza.")
    parser.add_argument("--date", help="Single Gaza edition date in YYYY-MM-DD format.")
    parser.add_argument("--start-date", help="Start date for an inclusive range in YYYY-MM-DD format.")
    parser.add_argument("--end-date", help="End date for an inclusive range in YYYY-MM-DD format.")
    for action in ACTION_ORDER:
        parser.add_argument(f"--{action.replace('_', '-')}", action="store_true", help=f"Run or plan the {action.replace('_', ' ')} action.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep going after a production action fails.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.production = bool(args.production)
    args.test = not args.production
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        build_date_list(args)
        if args.dashboard:
            state = build_dashboard_state(args)
            state["output_paths"] = write_dashboard_files(state)
            if args.json:
                print(json.dumps(state, indent=2))
            else:
                _print_dashboard_summary(state)
            return 0
        report = build_report(args)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(render_text_report(report), end="")
        return 0 if report.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        if argv is not None and "--json" in argv:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
