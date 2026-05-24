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

from scripts.run_and_notify import load_env_file, notification_error_message, print_smtp_config_debug, send_email
from scripts.validation_profiles import (
    PROFILE_CASCADIA_WEEKLY,
    apply_env_profile,
    get_profile,
    make_pytest_basetemp,
    profile_names,
    pytest_command,
)

DEFAULT_PAGES_REPO = ROOT / "bluefern-dispatches-pages"
DEFAULT_PAGES_BRANCH = "gh-pages"
ARCHIVE_URL = "https://dispatches.thebluefernco.com/cascadia/archive.html"
LOG_DIR = ROOT / "logs"


def log_path_for(date_str: str) -> Path:
    return LOG_DIR / f"cascadia-weekly-notify-{date_str}.log"


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
        completed = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
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


def pages_ahead_of_remote(pages_repo: Path, pages_branch: str) -> bool:
    if not (pages_repo / ".git").exists():
        return False
    completed = subprocess.run(
        ["git", "-C", str(pages_repo), "status", "-sb"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and f"ahead" in completed.stdout and pages_branch in completed.stdout


def manual_push_command(pages_repo: Path, pages_branch: str) -> str:
    return f'cd "{pages_repo}"\ngit status\ngit push origin {pages_branch}'


def push_pages_repo(pages_repo: Path, pages_branch: str, log_path: Path) -> tuple[bool, list[str], str]:
    messages: list[str] = []
    status = run_logged_command(["git", "-C", str(pages_repo), "status"], log_path)
    status_message = status.get("stdout") or status.get("stderr") or ""
    messages.append(str(status_message).strip())
    if int(status.get("exit_code", 1)) != 0:
        return False, messages, str(status.get("stderr") or status.get("stdout") or "").strip()
    push = run_logged_command(["git", "-C", str(pages_repo), "push", "origin", pages_branch], log_path)
    push_message = push.get("stdout") or push.get("stderr") or ""
    messages.append(str(push_message).strip())
    return int(push.get("exit_code", 1)) == 0, messages, str(push.get("stdout") or push.get("stderr") or "").strip()


def edition_url(edition_date: str | None) -> str | None:
    if not edition_date:
        return None
    return f"https://dispatches.thebluefernco.com/cascadia/editions/{edition_date}/"


def build_test_email_body(date_str: str) -> str:
    return "\n".join(
        [
            "Blue Fern Dispatches Cascadia SMTP diagnostic message.",
            f"Date: {date_str}",
            "",
            "This message was sent by scripts/run_cascadia_and_notify.py --send-test-email.",
            "No Cascadia pipeline was run.",
            "No Pages publish was run.",
            "pushed: false",
        ]
    )


def build_email_body(summary: dict[str, Any], log_path: Path) -> str:
    cascadia = summary.get("cascadia") if isinstance(summary.get("cascadia"), dict) else {}
    publish = summary.get("publish") if isinstance(summary.get("publish"), dict) else {}
    warnings = list(summary.get("warnings") or [])
    errors = list(summary.get("errors") or [])
    edition_date = str(cascadia.get("edition_date") or cascadia.get("date") or summary.get("date") or "")
    local_paths = cascadia.get("output_paths") if isinstance(cascadia.get("output_paths"), dict) else {}
    coverage_label = cascadia.get("coverage_label")
    if not coverage_label and cascadia.get("coverage_start") and cascadia.get("coverage_end"):
        coverage_label = f"{cascadia.get('coverage_start')} to {cascadia.get('coverage_end')}"
    lines = [
        "Blue Fern Dispatches Cascadia weekly report.",
        f"date: {summary.get('date')}",
        f"mode: {cascadia.get('mode') or 'weekly-public'}",
        f"return_code: {summary.get('return_code')}",
        f"ok: {str(summary.get('ok')).lower()}",
        f"coverage_start: {cascadia.get('coverage_start') or '<unknown>'}",
        f"coverage_end: {cascadia.get('coverage_end') or '<unknown>'}",
        f"coverage_label: {coverage_label or '<unknown>'}",
        f"public_story_count: {cascadia.get('public_story_count', '<unknown>')}",
        f"generation_ok: {str(summary.get('generation_ok')).lower()}",
        f"archive URL: {ARCHIVE_URL}",
        f"edition URL: {edition_url(edition_date) or '<unknown>'}",
        f"local edition path: {local_paths.get('public_site_output') or '<unknown>'}",
        f"pages_repo: {summary.get('pages_repo')}",
        f"pages_branch: {summary.get('pages_branch')}",
        f"pages_repo_updated: {str(summary.get('pages_repo_updated')).lower()}",
        f"pages_commit_sha: {summary.get('pages_commit_sha') or '<none>'}",
        f"pushed: {str(summary.get('pushed')).lower()}",
        f"log path: {log_path}",
        f"tests_run: {str(summary.get('tests_run')).lower()}",
        f"tests_ok: {str(summary.get('tests_ok')).lower()}",
        f"validation_ok: {str(summary.get('validation_ok')).lower()}",
        f"validation_profile: {summary.get('validation_profile')}",
        f"tests_command: {summary.get('tests_command')}",
        f"skipped_unrelated_tests: {str(summary.get('skipped_unrelated_tests')).lower()}",
        f"pipeline_ok: {str(summary.get('pipeline_ok')).lower()}",
        f"email_requested: {str(summary.get('email_requested')).lower()}",
        f"email_ok: {str(summary.get('email_ok')).lower()}",
        f"notification_error: {summary.get('notification_error')}",
        f"overall_ok: {str(summary.get('overall_ok')).lower()}",
        f"publish_ok: {str(summary.get('publish_ok')).lower()}",
        f"publish_blocked: {str(summary.get('publish_blocked')).lower()}",
        f"publish_blocked_reason: {summary.get('publish_blocked_reason')}",
        f"doctor_ok: {str(summary.get('doctor_ok')).lower()}",
        "",
        "warnings:",
        "\n".join(f"- {item}" for item in warnings) if warnings else "- <none>",
        "",
        "errors:",
        "\n".join(f"- {item}" for item in errors) if errors else "- <none>",
    ]
    if summary.get("manual_push_command"):
        lines.extend(["", "manual Pages push command:", str(summary["manual_push_command"])])
    lines.extend(["", "last 80 log lines:", tail_log(log_path, 80)])
    return "\n".join(lines)


def build_cascadia_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_cascadia_dispatch.py"),
    ]
    if args.archive_week:
        cmd.extend(["--archive-week", args.archive_week])
    else:
        cmd.extend(["--date", args.date])
    cmd.extend(["--weekly-public", "--historical-search"])
    if args.quality_weekly:
        cmd.append("--quality-weekly")
    return cmd


def build_publish_command(args: argparse.Namespace, edition_date: str | None) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "publish_github_pages.py"),
        "--pages-repo",
        str(args.pages_repo),
        "--pages-branch",
        args.pages_branch,
        "--commit",
        "--no-push",
    ]
    if edition_date:
        cmd.extend(["--expect-date", edition_date, "--expect-dispatch", "cascadia"])
    return cmd


def build_summary(args: argparse.Namespace, log_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "date": args.date,
        "ok": False,
        "return_code": 1,
        "warnings": [],
        "errors": [],
        "pages_repo": str(args.pages_repo),
        "pages_branch": args.pages_branch,
        "pages_repo_updated": False,
        "pages_commit_sha": None,
        "pushed": False,
        "push_output": [],
        "manual_push_command": manual_push_command(Path(args.pages_repo), args.pages_branch) if not args.push else None,
        "log_path": str(log_path),
        "tests_run": False,
        "tests_ok": None,
        "validation_ok": None,
        "generation_ok": False,
        "tests_command": None,
        "validation_profile": args.validation_profile,
        "skipped_unrelated_tests": bool(get_profile(args.validation_profile).skipped_unrelated_tests),
        "pipeline_ok": False,
        "email_requested": True,
        "email_ok": None,
        "notification_error": None,
        "overall_ok": False,
        "publish_ok": False,
        "publish_blocked": False,
        "publish_blocked_reason": None,
        "doctor_ok": None,
    }

    cascadia_result = run_logged_command(build_cascadia_command(args), log_path)
    summary["cascadia_return_code"] = cascadia_result["exit_code"]
    summary["cascadia"] = cascadia_result["json"]
    cascadia_json = cascadia_result["json"]
    summary["warnings"].extend(cascadia_json.get("warnings", []))
    summary["errors"].extend(cascadia_json.get("errors", []))
    if int(cascadia_result["exit_code"]) != 0 or cascadia_json.get("ok") is False:
        summary["errors"].append(f"Cascadia command failed with exit code {cascadia_result['exit_code']}")
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "cascadia-generation-failed"
        return summary
    summary["generation_ok"] = True

    edition_date = str(cascadia_json.get("edition_date") or cascadia_json.get("date") or args.date)
    publish_result = run_logged_command(build_publish_command(args, edition_date), log_path)
    summary["publish_return_code"] = publish_result["exit_code"]
    summary["publish"] = publish_result["json"]
    publish_json = publish_result["json"]
    summary["warnings"].extend(publish_json.get("warnings", []))
    summary["errors"].extend(publish_json.get("errors", []))
    summary["pages_repo_updated"] = bool(publish_json.get("copied") or publish_json.get("committed"))
    summary["pages_commit_sha"] = publish_json.get("commit_sha")
    summary["pages_branch"] = str(publish_json.get("target_pages_branch") or args.pages_branch)
    summary["pushed"] = bool(publish_json.get("pushed"))
    if int(publish_result["exit_code"]) != 0 or publish_json.get("ok") is False:
        summary["errors"].append(f"Pages publish/update failed with exit code {publish_result['exit_code']}")
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "pages-publish-failed"
        return summary
    if publish_json.get("target_pages_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages publish targeted {publish_json.get('target_pages_branch')}, expected {args.pages_branch}")
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "pages-publish-branch-mismatch"
        return summary
    if publish_json.get("committed_branch") not in (args.pages_branch, None):
        summary["errors"].append(f"Pages commit targeted {publish_json.get('committed_branch')}, expected {args.pages_branch}")
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "pages-commit-branch-mismatch"
        return summary

    if not args.skip_tests:
        basetemp = make_pytest_basetemp("bluefern-pytest-cascadia")
        tests_cmd = pytest_command(args.validation_profile, basetemp)
        tests_result = run_logged_command(
            tests_cmd,
            log_path,
        )
        summary["tests_run"] = True
        summary["tests_command"] = " ".join(shlex.quote(part) for part in tests_cmd)
        summary["tests_ok"] = int(tests_result["exit_code"]) == 0
        if not summary["tests_ok"]:
            summary["errors"].append(f"Focused Cascadia tests failed with exit code {tests_result['exit_code']}")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "dispatch_validation_failed"
            return summary

    doctor_result = run_logged_command([sys.executable, str(ROOT / "scripts" / "doctor.py")], log_path)
    summary["doctor_ok"] = int(doctor_result["exit_code"]) == 0
    if not summary["doctor_ok"]:
        summary["errors"].append(f"doctor.py failed with exit code {doctor_result['exit_code']}")
        summary["publish_blocked"] = True
        summary["publish_blocked_reason"] = "doctor-failed"
        return summary

    if args.push:
        pushed, messages, push_result = push_pages_repo(Path(args.pages_repo), args.pages_branch, log_path)
        summary["push_output"] = messages
        summary["pushed"] = pushed
        if not pushed:
            summary["errors"].append(push_result or f"git push origin {args.pages_branch} failed")
            summary["publish_blocked"] = True
            summary["publish_blocked_reason"] = "pages-push-failed"
            return summary
    elif pages_ahead_of_remote(Path(args.pages_repo), args.pages_branch):
        summary["manual_push_command"] = manual_push_command(Path(args.pages_repo), args.pages_branch)

    summary["ok"] = not summary["errors"]
    summary["pipeline_ok"] = summary["ok"]
    summary["validation_ok"] = bool(summary["tests_ok"]) if summary["tests_run"] else True
    summary["publish_ok"] = bool(summary["pages_repo_updated"]) and (bool(summary["pushed"]) if args.push else True)
    summary["return_code"] = 0 if summary["ok"] else 1
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Cascadia weekly public update and email a confirmation report.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Run date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--archive-week", help="Archive week date passed through to Cascadia weekly generation.")
    parser.add_argument("--quality-weekly", action="store_true", help="Pass --quality-weekly to the Cascadia run.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip focused Cascadia pytest validation before sending success mail.")
    parser.add_argument(
        "--validation-profile",
        choices=list(profile_names()),
        default=PROFILE_CASCADIA_WEEKLY,
        help="Validation profile for scheduled/run gating.",
    )
    parser.add_argument("--smtp-debug", action="store_true", help="Enable smtplib debug output on the SMTP connection.")
    parser.add_argument("--send-test-email", action="store_true", help="Send an SMTP-only diagnostic email and do not run Cascadia or Pages publish.")
    parser.add_argument("--pages-repo", default=str(DEFAULT_PAGES_REPO), help="Local Pages repository path.")
    parser.add_argument("--pages-branch", default=DEFAULT_PAGES_BRANCH, help="Pages branch name.")
    parser.add_argument("--push", action="store_true", help="Push the Pages repo to origin after local publish succeeds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.validation_profile = apply_env_profile(args.validation_profile)
    try:
        _ = get_profile(args.validation_profile)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)], "validation_profile": args.validation_profile}, indent=2))
        return 1
    args.pages_repo = str(Path(args.pages_repo))
    load_env_file()
    if args.smtp_debug:
        print_smtp_config_debug()

    if args.send_test_email:
        subject = f"[Blue Fern Dispatches] Cascadia SMTP diagnostic - {args.date}"
        try:
            send_email(subject, build_test_email_body(args.date), args.date, smtp_debug=bool(args.smtp_debug))
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to send test email: {notification_error_message(exc)}", file=sys.stderr)
            return 2
        return 0

    log_path = log_path_for(args.date)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"Cascadia weekly notify run for {args.date}\n", encoding="utf-8")

    summary = build_summary(args, log_path)
    subject_status = "succeeded" if summary["ok"] else "failed"
    subject = f"[Blue Fern Dispatches] Cascadia weekly {subject_status} - {args.date}"
    body = build_email_body(summary, log_path)
    try:
        send_email(subject, body, args.date, smtp_debug=bool(args.smtp_debug))
    except Exception as exc:  # noqa: BLE001
        summary["pipeline_ok"] = summary.get("return_code", 1) == 0
        summary["email_ok"] = False
        summary["notification_error"] = notification_error_message(exc)
        summary["overall_ok"] = False
        print(json.dumps(summary, indent=2))
        print(f"Failed to send Cascadia notification email: {summary['notification_error']}", file=sys.stderr)
        return 2

    summary["email_ok"] = True
    summary["overall_ok"] = bool(summary.get("pipeline_ok"))
    summary["ok"] = bool(summary["overall_ok"])
    print(json.dumps(summary, indent=2))
    return int(summary["return_code"])


if __name__ == "__main__":
    raise SystemExit(main())
