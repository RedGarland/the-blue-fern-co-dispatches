from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr
import os
import shlex
import smtplib
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import TextIO


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPT = ROOT / "scripts" / "run_cascadia_dispatch.py"
PUBLISH_SCRIPT = ROOT / "scripts" / "publish_github_pages.py"
TRUTHY = {"1", "true", "yes"}


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


class _TeeStderr:
    def __init__(self, *streams: TextIO) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUTHY


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {value!r}") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return parsed


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {value!r}") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return parsed


@contextmanager
def _smtp_debug_output() -> object:
    debug_file = os.getenv("SMTP_DEBUG_FILE")
    if not debug_file:
        yield
        return

    path = Path(debug_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- SMTP debug {datetime.now(timezone.utc).isoformat()} ---\n")
        with redirect_stderr(_TeeStderr(sys.stderr, handle)):
            yield


def _smtp_error_message(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _smtp_local_hostname(email_from: str) -> str:
    configured = os.getenv("SMTP_LOCAL_HOSTNAME")
    if configured and configured.strip():
        return configured.strip()

    if "@" in email_from:
        domain = email_from.rsplit("@", 1)[1].strip()
        if "." in domain and " " not in domain:
            return domain

    fqdn = socket.getfqdn().strip()
    if "." in fqdn and " " not in fqdn:
        return fqdn

    return "localhost.localdomain"


def _smtp_ehlo(smtp: smtplib.SMTP, local_hostname: str) -> None:
    code, response = smtp.ehlo(local_hostname)
    if code >= 400:
        raise smtplib.SMTPHeloError(code, response)


def send_email(subject: str, body: str, date_str: str, smtp_debug: bool = False) -> None:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_use_ssl = _env_bool("SMTP_USE_SSL")
    smtp_timeout = _env_float("SMTP_TIMEOUT", 30.0)
    smtp_retries = _env_int("SMTP_RETRIES", 2)
    smtp_retry_delay = _env_float("SMTP_RETRY_DELAY", 1.0)
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

    use_smtps = smtp_use_ssl or smtp_port == 465
    last_error: BaseException | None = None
    retryable_errors = (smtplib.SMTPException, OSError, socket.timeout)

    local_hostname = _smtp_local_hostname(email_from)

    smtp_ca_bundle = os.getenv("SMTP_CA_BUNDLE")
    if smtp_ca_bundle and smtp_ca_bundle.strip():
        smtp_ca_bundle_path = Path(smtp_ca_bundle).expanduser().resolve()
        if not smtp_ca_bundle_path.is_file():
            raise RuntimeError(
                f"SMTP_CA_BUNDLE is set to {smtp_ca_bundle!r} but that file does not exist or is not a file. "
                "Unset SMTP_CA_BUNDLE or point it to a valid PEM file."
            )
        cafile = str(smtp_ca_bundle_path)
    else:
        cafile = None
    smtp_skip_verify = _env_bool("SMTP_SKIP_VERIFY", default=False)

    if smtp_skip_verify:
        tls_context = ssl._create_unverified_context()
    else:
        try:
            if cafile:
                tls_context = ssl.create_default_context(cafile=cafile)
            else:
                try:
                    import certifi
                    tls_context = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    tls_context = ssl.create_default_context()
        except FileNotFoundError:
            raise RuntimeError(
                "SMTP_CA_BUNDLE referenced file not found. Unset SMTP_CA_BUNDLE or point it to a valid PEM file."
            ) from None
        except Exception:
            tls_context = ssl.create_default_context()

    for attempt in range(1, smtp_retries + 2):
        smtp = None
        try:
            if use_smtps:
                smtp = smtplib.SMTP_SSL(
                    smtp_host,
                    smtp_port,
                    local_hostname=local_hostname,
                    timeout=smtp_timeout,
                    context=tls_context,
                )
            else:
                smtp = smtplib.SMTP(smtp_host, smtp_port, local_hostname=local_hostname, timeout=smtp_timeout)

            if smtp_debug:
                smtp.set_debuglevel(1)

            with _smtp_debug_output():
                if use_smtps:
                    _smtp_ehlo(smtp, local_hostname)
                    connection_label = "SMTPS (SSL)"
                else:
                    _smtp_ehlo(smtp, local_hostname)
                    connection_label = "plain SMTP"
                    if smtp.has_extn("starttls"):
                        smtp.starttls(context=tls_context)
                        _smtp_ehlo(smtp, local_hostname)
                        connection_label = "STARTTLS"
                    else:
                        print(
                            f"Warning: SMTP server {smtp_host}:{smtp_port} does not advertise STARTTLS; sending without TLS for {date_str}.",
                            file=sys.stderr,
                        )

                if smtp_user:
                    smtp.login(smtp_user, smtp_password or "")
                smtp.send_message(msg)

            print(f"Email sent with {connection_label}.")
            return
        except retryable_errors as exc:
            last_error = exc

            # If cert verification failed and user explicitly enabled skipping, retry once with unverified context
            if isinstance(exc, ssl.SSLCertVerificationError) and smtp_skip_verify:
                print("Warning: SSL cert verification failed; SMTP_SKIP_VERIFY=true — retrying with unverified TLS context", file=sys.stderr)
                tls_context = ssl._create_unverified_context()
                if attempt <= smtp_retries:
                    if smtp is not None:
                        try:
                            smtp.quit()
                        except Exception:
                            try:
                                smtp.close()
                            except Exception:
                                pass
                    time.sleep(smtp_retry_delay)
                    continue

            if attempt > smtp_retries:
                break

            print(
                f"SMTP attempt {attempt} failed ({_smtp_error_message(exc)}); retrying in {smtp_retry_delay:g}s.",
                file=sys.stderr,
            )
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    try:
                        smtp.close()
                    except Exception:
                        pass
            if smtp_retry_delay:
                time.sleep(smtp_retry_delay)
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    try:
                        smtp.close()
                    except Exception:
                        pass

    if last_error is not None:
        raise RuntimeError(f"SMTP send failed after {smtp_retries + 1} attempt(s): {_smtp_error_message(last_error)}") from last_error


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
        print(f"Failed to send email: {_smtp_error_message(exc)}", file=sys.stderr)
        return 2

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
