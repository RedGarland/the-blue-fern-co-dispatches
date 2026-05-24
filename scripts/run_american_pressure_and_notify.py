from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_and_notify import _smtp_error_message, load_env_file, print_smtp_config_debug, send_email
from scripts.validation_profiles import PROFILE_AMERICAN_PRESSURE_WEEKLY


DISPATCH_SLUG = "american-pressure"
DISPATCH_NAME = "The American Pressure Dispatch"
DEFAULT_PAGES_BRANCH = "gh-pages"
LOG_DIR = ROOT / "logs"


def log_path_for(date_str: str) -> Path:
    return LOG_DIR / f"american-pressure-weekly-notify-{date_str}.log"


def tail_log(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return "<log file not found>"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:]) if content else "<empty log>"


def _safe_log_text(text: str) -> str:
    return text.replace("SMTP_PASSWORD", "SMTP password marker")


def _json_from_stdout(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            loaded = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return loaded if isinstance(loaded, dict) else {}


def run_logged_command(cmd: list[str], log_path: Path) -> dict[str, Any]:
    command_text = " ".join(shlex.quote(part) for part in cmd)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n$ {command_text}\n")
        completed = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
        if completed.stdout:
            handle.write("\n[stdout]\n")
            handle.write(_safe_log_text(completed.stdout))
            if not completed.stdout.endswith("\n"):
                handle.write("\n")
        if completed.stderr:
            handle.write("\n[stderr]\n")
            handle.write(_safe_log_text(completed.stderr))
            if not completed.stderr.endswith("\n"):
                handle.write("\n")
        handle.write(f"\n[exit_code] {completed.returncode}\n")
    return {
        "command": command_text,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "json": _json_from_stdout(completed.stdout),
    }


def public_urls_for(edition_date: str) -> dict[str, str]:
    return {
        "archive": "https://dispatches.thebluefernco.com/american-pressure/archive.html",
        "edition": f"https://dispatches.thebluefernco.com/american-pressure/editions/{edition_date}/",
    }


def build_test_email_body(date_str: str) -> str:
    return "\n".join(
        [
            "Blue Fern Dispatches American Pressure SMTP diagnostic message.",
            f"Date: {date_str}",
            "",
            "This message was sent by scripts/run_american_pressure_and_notify.py --send-test-email.",
            "No American Pressure pipeline was run.",
            "No Pages publish was run.",
            "pushed: false",
        ]
    )


def build_email_body(summary: dict[str, Any], log_path: Path) -> str:
    public_urls = summary.get("public_urls") if isinstance(summary.get("public_urls"), dict) else {}
    local_paths = summary.get("local_paths") if isinstance(summary.get("local_paths"), dict) else {}
    warnings = list(summary.get("warnings") or [])
    errors = list(summary.get("errors") or [])
    lines = [
        f"date: {summary.get('date')}",
        f"ok: {str(summary.get('ok')).lower()}",
        f"source_count: {summary.get('source_count')}",
        f"story_count: {summary.get('story_count')}",
        f"generated: {str(summary.get('generated')).lower()}",
        f"archive_updated: {str(summary.get('archive_updated')).lower()}",
        f"rss_updated: {str(summary.get('rss_updated')).lower()}",
        f"pages_repo_updated: {str(summary.get('pages_repo_updated')).lower()}",
        f"pages_branch: {summary.get('pages_branch')}",
        f"pages_commit_sha: {summary.get('pages_commit_sha') or '<none>'}",
        f"pushed: {str(summary.get('pushed')).lower()}",
        f"validation_profile: {summary.get('validation_profile')}",
        f"tests_run: {str(summary.get('tests_run')).lower()}",
        f"tests_ok: {str(summary.get('tests_ok')).lower()}",
        f"tests_command: {summary.get('tests_command')}",
        f"skipped_unrelated_tests: {str(summary.get('skipped_unrelated_tests')).lower()}",
        f"publish_blocked: {str(summary.get('publish_blocked')).lower()}",
        f"publish_blocked_reason: {summary.get('publish_blocked_reason')}",
        f"public archive URL: {public_urls.get('archive')}",
        f"public edition URL: {public_urls.get('edition')}",
        f"local edition path: {local_paths.get('edition')}",
        f"source file path: {local_paths.get('source_file')}",
        f"log path: {local_paths.get('log')}",
        "",
        "warnings:",
        "\n".join(f"- {item}" for item in warnings) if warnings else "- <none>",
        "",
        "errors:",
        "\n".join(f"- {item}" for item in errors) if errors else "- <none>",
        "",
        "last 80 log lines:",
        tail_log(log_path, 80),
    ]
    return "\n".join(lines)


def build_summary(args: argparse.Namespace, log_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "date": args.date,
        "ok": False,
        "return_code": 1,
        "source_count": 0,
        "story_count": 0,
        "generated": False,
        "archive_updated": False,
        "rss_updated": False,
        "pages_repo_updated": False,
        "pages_branch": args.pages_branch,
        "pages_commit_sha": None,
        "pushed": False,
        "validation_profile": PROFILE_AMERICAN_PRESSURE_WEEKLY,
        "tests_run": False,
        "tests_ok": None,
        "tests_command": "run_weekly_american_pressure.py executes profile tests directly",
        "skipped_unrelated_tests": True,
        "publish_blocked": False,
        "publish_blocked_reason": None,
        "public_urls": public_urls_for(args.date),
        "local_paths": {
            "edition": str(ROOT / "output" / "site" / DISPATCH_SLUG / "editions" / args.date / "index.html"),
            "source_file": str(ROOT / "data" / "dispatches" / DISPATCH_SLUG / "sources" / args.date / "manual_sources.json"),
            "log": str(log_path),
        },
        "warnings": [],
        "errors": [],
    }
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_american_pressure_dispatch.py"),
        "--date",
        args.date,
        "--from-manual-sources",
    ]
    if args.publish:
        cmd.append("--publish")
    if args.dry_run:
        cmd.append("--dry-run")
    result = run_logged_command(cmd, log_path)
    payload = result["json"]
    summary["warnings"].extend(payload.get("warnings", []) if isinstance(payload.get("warnings"), list) else [])
    summary["errors"].extend(payload.get("errors", []) if isinstance(payload.get("errors"), list) else [])
    summary["source_count"] = int(payload.get("source_count") or 0)
    summary["story_count"] = int(payload.get("story_count") or 0)
    summary["generated"] = bool(payload.get("generated"))
    summary["archive_updated"] = bool(payload.get("archive_updated"))
    summary["rss_updated"] = bool(payload.get("rss_updated"))
    if int(result["exit_code"]) != 0 or payload.get("ok") is False:
        summary["errors"].append(f"American Pressure command failed with exit code {result['exit_code']}")
        return summary
    summary["ok"] = True
    summary["return_code"] = 0
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run weekly American Pressure manual-source update and email a report.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Edition date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--publish", action="store_true", help="Update American Pressure public index/archive/rss after edition generation.")
    parser.add_argument("--dry-run", action="store_true", help="Run generation in dry-run mode.")
    parser.add_argument("--smtp-debug", action="store_true", help="Enable smtplib debug output on the SMTP connection.")
    parser.add_argument("--send-test-email", action="store_true", help="Send SMTP diagnostic mail and do not run the pipeline.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Reported Pages branch field.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file()
    if args.smtp_debug:
        print_smtp_config_debug()
    if args.send_test_email:
        subject = f"[Blue Fern Dispatches] American Pressure SMTP diagnostic - {args.date}"
        try:
            send_email(subject, build_test_email_body(args.date), args.date, smtp_debug=bool(args.smtp_debug))
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to send test email: {_smtp_error_message(exc)}", file=sys.stderr)
            return 2
        return 0
    log_path = log_path_for(args.date)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"American Pressure weekly notify run for {args.date}\n", encoding="utf-8")
    summary = build_summary(args, log_path)
    subject_status = "succeeded" if summary["ok"] else "failed"
    subject = f"[Blue Fern Dispatches] American Pressure weekly {subject_status} - {args.date}"
    body = build_email_body(summary, log_path)
    try:
        send_email(subject, body, args.date, smtp_debug=bool(args.smtp_debug))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to send American Pressure notification email: {_smtp_error_message(exc)}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return int(summary["return_code"])


if __name__ == "__main__":
    raise SystemExit(main())
