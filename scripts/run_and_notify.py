from __future__ import annotations

import argparse
import os
import shlex
import smtplib
import socket
import subprocess
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPT = ROOT / "scripts" / "run_cascadia_dispatch.py"
PUBLISH_SCRIPT = ROOT / "scripts" / "publish_github_pages.py"


def run_command(cmd: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": " ".join(shlex.quote(part) for part in cmd),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def send_email(subject: str, body: str, date_str: str, smtp_debug: bool = False) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    # If SMTP_USE_SSL is set or port is 465 we will use SMTPS (SSL-wrapped)
    smtp_use_ssl = os.getenv("SMTP_USE_SSL", "").lower() in ("1", "true", "yes")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO", "")
    email_from = os.getenv("EMAIL_FROM") or smtp_user or f"noreply@{socket.gethostname()}"

    missing: list[str] = []
    if not smtp_host:
        missing.append("SMTP_HOST")
    if not email_to.strip():
        missing.append("EMAIL_TO")
    if smtp_user and not smtp_password:
        missing.append("SMTP_PASSWORD")
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    recipients = [addr.strip() for addr in email_to.split(",") if addr.strip()]
    if not recipients:
        raise RuntimeError("EMAIL_TO did not contain any valid recipient addresses")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(body)

    # Choose SMTPS (SSL) for port 465 or when explicitly requested
    use_smtps = smtp_use_ssl or smtp_port == 465
    if use_smtps:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as smtp:
            try:
                if smtp_debug:
                    smtp.set_debuglevel(1)
                smtp.ehlo()
                if smtp_user:
                    smtp.login(smtp_user, smtp_password or "")
                smtp.send_message(msg)
                print("Email sent with SMTPS (SSL).")
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            if smtp_debug:
                smtp.set_debuglevel(1)
            smtp.ehlo()
            used_tls = False
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
                used_tls = True
            else:
                print(
                    f"Warning: SMTP server {smtp_host}:{smtp_port} does not advertise STARTTLS; sending without TLS for {date_str}.",
                    file=sys.stderr,
                )
            if smtp_user:
                smtp.login(smtp_user, smtp_password or "")
            smtp.send_message(msg)
            if used_tls:
                print("Email sent with STARTTLS.")


def build_body(date_str: str, results: list[dict[str, object]], publish_requested: bool) -> str:
    ok = all(int(item["exit_code"]) == 0 for item in results)
    gaza_url = f"https://dispatches.thebluefernco.com/gaza/editions/{date_str}/"
    cascadia_url = f"https://dispatches.thebluefernco.com/cascadia/editions/{date_str}/"
    lines = [
        f"Dispatches run date: {date_str}",
        f"Overall status: {'SUCCESS' if ok else 'FAILURE'}",
        f"Publish requested: {publish_requested}",
        "",
        "Public pages:",
        f"- {gaza_url}",
        f"- {cascadia_url}",
        "",
    ]

    for item in results:
        lines.extend(
            [
                "-" * 72,
                f"Command: {item['command']}",
                f"Exit code: {item['exit_code']}",
                "Stdout:",
                str(item["stdout"]).strip() or "<none>",
                "Stderr:",
                str(item["stderr"]).strip() or "<none>",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run dispatch pipeline and email results.")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="Edition date in YYYY-MM-DD format (default: current UTC date).")
    parser.add_argument("--publish", action="store_true", help="Run publish step after pipeline.")
    parser.add_argument("--pages-repo", help="Pages repository path used by publish step.")
    parser.add_argument("--smtp-debug", action="store_true", help="Enable smtplib debug output on the SMTP connection.")
    args = parser.parse_args(argv)

    py = sys.executable
    pipeline_cmd = [py, str(PIPELINE_SCRIPT), "--date", args.date, "--all"]
    results = [run_command(pipeline_cmd)]

    if args.publish:
        publish_cmd = [py, str(PUBLISH_SCRIPT)]
        if args.pages_repo:
            publish_cmd.extend(["--pages-repo", args.pages_repo, "--commit"])
        results.append(run_command(publish_cmd))

    success = all(int(item["exit_code"]) == 0 for item in results)
    subject = f"Dispatches publish result - {args.date} - {'SUCCESS' if success else 'FAILURE'}"
    body = build_body(args.date, results, args.publish)

    try:
        send_email(subject, body, args.date, smtp_debug=bool(args.smtp_debug))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to send email: {exc}", file=sys.stderr)
        return 2

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
