from __future__ import annotations

import argparse
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
    report = operator_status.build_report(
        ROOT,
        date_text,
        pages_repo=ROOT / "bluefern-dispatches-pages",
        pages_branch="gh-pages",
        live_checks=False,
    )
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
    if any(action.get("execution") == "plan" for action in date_result.get("actions") or []):
        return "Run the planned actions with --production."
    return "No action needed."


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
    date_result: dict[str, Any] = {
        "date": date_text,
        "mode": "production" if args.production else "test",
        "selected_actions": list(selected_actions),
        "repos": {"source": source_repo, "pages": pages_repo},
        "manual_sources": manual_sources,
        "preflight": preflight_report,
        "actions": [],
        "ok": True,
        "stopped_early": False,
        "next_safe_action": "",
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
        "failed_dates": failed_dates,
        "processed_dates": [result["date"] for result in results],
        "aggregate": {
            "date_count": len(results),
            "failed_date_count": len(failed_dates),
            "succeeded_date_count": len(results) - len(failed_dates),
            "selected_action_count": len(selected_actions),
        },
    }


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
    lines.append(f"- overall: {'ok' if report['ok'] else 'needs attention'}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operator command center for Gaza daily actions.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test", action="store_true", help="Plan public actions instead of executing them.")
    mode.add_argument("--production", action="store_true", help="Allow the selected public and write-capable actions to run.")
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
