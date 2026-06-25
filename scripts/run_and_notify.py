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
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import TextIO


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPT = ROOT / "scripts" / "run_daily_gaza.py"
TRUTHY = {"1", "true", "yes"}
FALSY = {"0", "false", "no"}


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


class _RedactingStream:
    def __init__(self, stream: TextIO, sensitive_values: list[str]) -> None:
        self.stream = stream
        self.sensitive_values = [value for value in sensitive_values if value]

    def write(self, data: str) -> int:
        self.stream.write(_redact_text(data, self.sensitive_values))
        return len(data)

    def flush(self) -> None:
        self.stream.flush()


def load_env_file(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in TRUTHY


def _env_bool_any(names: list[str], default: bool = False) -> bool:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        normalized = value.strip().lower()
        if normalized in TRUTHY:
            return True
        if normalized in FALSY:
            return False
    return default


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


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value
    return None


def _smtp_recipient_value() -> str:
    return _env_first("EMAIL_TO", "SMTP_TO") or ""


def _smtp_sender_value() -> str | None:
    return _env_first("EMAIL_FROM", "SMTP_FROM")


def _smtp_sensitive_values() -> list[str]:
    values = [
        os.getenv("SMTP_PASSWORD") or "",
        os.getenv("SMTP_USER") or "",
        os.getenv("SMTP_USERNAME") or "",
        _smtp_recipient_value(),
        os.getenv("EMAIL_FROM") or "",
        os.getenv("SMTP_FROM") or "",
    ]
    smtp_user = _env_first("SMTP_USER", "SMTP_USERNAME") or ""
    smtp_password = os.getenv("SMTP_PASSWORD") or ""
    auth_plain = f"\0{smtp_user}\0{smtp_password}".encode()
    if smtp_user or smtp_password:
        values.append(base64.b64encode(auth_plain).decode())
    if smtp_password:
        values.append(base64.b64encode(smtp_password.encode()).decode())
    for value in list(values):
        if value:
            values.append(base64.b64encode(value.encode()).decode())
    return values


def _redact_text(text: str, sensitive_values: list[str] | None = None) -> str:
    redacted = text
    for value in sensitive_values or _smtp_sensitive_values():
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _mask_email(value: str | None) -> str:
    if not value:
        return "<unset>"
    value = value.strip()
    if "@" not in value:
        return value[:1] + "***" if value else "<unset>"
    local, domain = value.rsplit("@", 1)
    prefix = local[:1] if local else "*"
    return f"{prefix}***@{domain}"


def _mask_recipients(value: str | None) -> str:
    if not value:
        return "<unset>"
    return ", ".join(_mask_email(item) for item in value.split(",") if item.strip()) or "<unset>"


@contextmanager
def _smtp_debug_output(sensitive_values: list[str] | None = None) -> object:
    sensitive = sensitive_values or _smtp_sensitive_values()
    debug_file = os.getenv("SMTP_DEBUG_FILE")
    if not debug_file:
        with redirect_stderr(_RedactingStream(sys.stderr, sensitive)):
            yield
        return

    path = Path(debug_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- SMTP debug {datetime.now(timezone.utc).isoformat()} ---\n")
        with redirect_stderr(_TeeStderr(_RedactingStream(sys.stderr, sensitive), _RedactingStream(handle, sensitive))):
            yield


@contextmanager
def _smtp_debug_file_only() -> object:
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


def notification_error_message(exc: BaseException) -> str:
    message = _redact_text(_smtp_error_message(exc))
    lower = message.lower()
    if "certificate_verify_failed" in lower or "sslcertverificationerror" in lower:
        return (
            "SMTP TLS certificate verification failed. "
            f"{message}. "
            "If this machine is behind intentional TLS inspection, export the inspection CA as a PEM file and set "
            "SMTP_CA_FILE=<path-to-pem> or SMTP_CA_BUNDLE=<path-to-pem>. "
            "If you want to use Windows trust roots and the optional truststore package is installed, set SMTP_TRUSTSTORE=1. "
            "Leave SMTP_TLS_VERIFY enabled for normal runs. SMTP_RELAX_X509_STRICT=1 is diagnostic only and should not be the default."
        )
    return message


def _smtp_mode(smtp_port: int, smtp_use_ssl: bool) -> str:
    if smtp_use_ssl:
        return "ssl"
    if smtp_port == 587:
        return "starttls-required"
    return "starttls-opportunistic"


def _env_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _resolve_ca_bundle_path() -> tuple[str | None, str | None]:
    # Support both env keys consistently; BUNDLE takes precedence if both are set.
    for key in ("SMTP_CA_BUNDLE", "SMTP_CA_FILE"):
        value = os.getenv(key)
        if value and value.strip():
            path = Path(value).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(
                    f"{key} is set to {value!r} but that file does not exist or is not a file. "
                    "Unset it or point it to a valid PEM file."
                )
            return str(path), key
    return None, None


def _smtp_runtime_settings() -> dict[str, object]:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_text = os.getenv("SMTP_PORT", "587")
    try:
        smtp_port = int(smtp_port_text)
    except ValueError as exc:
        raise RuntimeError(f"SMTP_PORT must be an integer, got {smtp_port_text!r}") from exc
    smtp_use_ssl = _env_bool("SMTP_USE_SSL")
    smtp_timeout = _env_float("SMTP_TIMEOUT", 30.0)
    smtp_retries = _env_int("SMTP_RETRIES", 2)
    smtp_retry_delay = _env_float("SMTP_RETRY_DELAY", 1.0)
    smtp_user = _env_first("SMTP_USER", "SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = _smtp_recipient_value()
    email_from = _smtp_sender_value() or smtp_user or f"noreply@{socket.gethostname()}"
    return {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_use_ssl": smtp_use_ssl,
        "smtp_timeout": smtp_timeout,
        "smtp_retries": smtp_retries,
        "smtp_retry_delay": smtp_retry_delay,
        "smtp_user": smtp_user,
        "smtp_password": smtp_password,
        "email_to": email_to,
        "email_from": email_from,
    }


def _validate_smtp_runtime_settings(settings: dict[str, object]) -> list[str]:
    missing: list[str] = []
    if not str(settings.get("smtp_host") or "").strip():
        missing.append("SMTP_HOST")
    if not str(settings.get("email_to") or "").strip():
        missing.append("EMAIL_TO or SMTP_TO")
    if str(settings.get("smtp_user") or "").strip() and not str(settings.get("smtp_password") or "").strip():
        missing.append("SMTP_PASSWORD")
    recipients = [addr.strip() for addr in str(settings.get("email_to") or "").split(",") if addr.strip()]
    if str(settings.get("email_to") or "").strip() and not recipients:
        raise RuntimeError("EMAIL_TO/SMTP_TO did not contain any valid recipient addresses")
    return missing


def _build_tls_context() -> tuple[ssl.SSLContext, dict[str, str | bool | None]]:
    smtp_skip_verify = _env_bool_any(["SMTP_SKIP_VERIFY", "SMTP_RELAX_X509_STRICT"], default=False)
    if _env_bool_any(["SMTP_TLS_VERIFY"], default=True) is False:
        smtp_skip_verify = True

    ca_bundle, ca_source_var = _resolve_ca_bundle_path()
    tls_source_preference = (_env_text("SMTP_TLS_CA_SOURCE") or "auto").strip().lower()
    if tls_source_preference not in {"auto", "truststore", "certifi"}:
        tls_source_preference = "auto"
    # Explicit SMTP_TLS_CA_SOURCE must win over ambient toggles.
    if tls_source_preference == "truststore":
        strategy = "truststore"
    elif tls_source_preference == "certifi":
        strategy = "certifi"
    else:
        if _env_bool("SMTP_CERTIFI", default=False):
            strategy = "certifi"
        elif _env_bool("SMTP_TRUSTSTORE", default=False):
            strategy = "truststore"
        else:
            strategy = "auto"
    context_meta: dict[str, str | bool | None] = {
        "tls_verify_enabled": not smtp_skip_verify,
        "tls_relaxed": smtp_skip_verify,
        "ca_bundle_path": ca_bundle,
        "ca_bundle_env": ca_source_var,
        "ca_source": None,
        "tls_source_preference": tls_source_preference,
        "tls_source_strategy": strategy,
    }

    if smtp_skip_verify:
        context_meta["ca_source"] = "unverified_context"
        return ssl._create_unverified_context(), context_meta

    if ca_bundle:
        context_meta["ca_source"] = "custom_bundle"
        return ssl.create_default_context(cafile=ca_bundle), context_meta

    if strategy == "truststore":
        try:
            import truststore  # type: ignore

            context_meta["ca_source"] = "truststore"
            context_meta["ca_bundle_env"] = "truststore"
            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), context_meta
        except Exception:
            context_meta["ca_source"] = "truststore_unavailable"
            if tls_source_preference == "truststore":
                context_meta["ca_source"] = "system_default"
                return ssl.create_default_context(), context_meta
            strategy = "auto"

    if strategy == "auto":
        try:
            context_meta["ca_source"] = "system_default"
            return ssl.create_default_context(), context_meta
        except Exception:
            context_meta["ca_source"] = "system_default_unavailable"

    try:
        import certifi  # type: ignore

        certifi_path = certifi.where()
        context_meta["ca_source"] = "certifi"
        context_meta["ca_bundle_path"] = certifi_path
        context_meta["ca_bundle_env"] = "certifi"
        return ssl.create_default_context(cafile=certifi_path), context_meta
    except Exception:
        context_meta["ca_source"] = "system_default"
        return ssl.create_default_context(), context_meta


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
    # Recommended normal configuration (for example Gmail):
    # SMTP_HOST=smtp.gmail.com
    # SMTP_PORT=587
    # SMTP_USE_SSL=0 (STARTTLS mode)
    # SMTP_TLS_VERIFY=1
    # Set SMTP_CA_FILE/SMTP_CA_BUNDLE only for intentional local TLS inspection.
    # SMTP_RELAX_X509_STRICT=1 is diagnostic-only and should not be a steady-state default.
    settings = _smtp_runtime_settings()
    smtp_host = str(settings["smtp_host"] or "")
    smtp_port = int(settings["smtp_port"])
    smtp_use_ssl = bool(settings["smtp_use_ssl"])
    smtp_timeout = float(settings["smtp_timeout"])
    smtp_retries = int(settings["smtp_retries"])
    smtp_retry_delay = float(settings["smtp_retry_delay"])
    smtp_user = str(settings["smtp_user"] or "") or None
    smtp_password = str(settings["smtp_password"] or "") or None
    email_to = str(settings["email_to"] or "")
    email_from = str(settings["email_from"] or "")

    missing = _validate_smtp_runtime_settings(settings)
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")

    recipients = [addr.strip() for addr in email_to.split(",") if addr.strip()]
    if not recipients:
        raise RuntimeError("EMAIL_TO/SMTP_TO did not contain any valid recipient addresses")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(body)

    mode = _smtp_mode(smtp_port, smtp_use_ssl)
    use_smtps = mode == "ssl"
    last_error: BaseException | None = None
    retryable_errors = (smtplib.SMTPException, OSError, socket.timeout)

    local_hostname = _smtp_local_hostname(email_from)

    tls_context, tls_meta = _build_tls_context()
    smtp_skip_verify = bool(tls_meta["tls_relaxed"])

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

            with _smtp_debug_output(_smtp_sensitive_values()) if smtp_debug else _smtp_debug_file_only():
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
                    elif mode == "starttls-required":
                        raise RuntimeError(f"SMTP server {smtp_host}:{smtp_port} did not advertise STARTTLS on required mode")
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


def build_test_email_body(date_str: str) -> str:
    return "\n".join(
        [
            "Blue Fern Dispatches SMTP diagnostic message.",
            f"Date: {date_str}",
            "",
            "This message was sent by scripts/run_and_notify.py --send-test-email.",
            "No Gaza pipeline was run.",
        ]
    )


def print_smtp_config_debug() -> None:
    settings = _smtp_runtime_settings()
    smtp_user = str(settings["smtp_user"] or "") or None
    email_from = str(settings["email_from"] or "")
    smtp_port_int = int(settings["smtp_port"])
    smtp_port = str(smtp_port_int)
    smtp_use_ssl = bool(settings["smtp_use_ssl"])
    mode = _smtp_mode(smtp_port_int, smtp_use_ssl)
    _tls_context, tls_meta = _build_tls_context()
    lines = [
        "SMTP diagnostic config:",
        f"- SMTP host: {str(settings['smtp_host'] or '<unset>')}",
        f"- SMTP port: {smtp_port}",
        f"- SMTP username: {_mask_email(smtp_user)}",
        f"- Email from: {_mask_email(email_from)}",
        f"- Email to: {_mask_recipients(str(settings['email_to'] or ''))}",
        f"- TLS mode: {mode}",
        f"- TLS verification: {str(bool(tls_meta['tls_verify_enabled'])).lower()}",
        f"- TLS relaxed (diagnostic): {str(bool(tls_meta['tls_relaxed'])).lower()}",
        f"- TLS source preference: {tls_meta.get('tls_source_preference')}",
        f"- CA source: {tls_meta.get('ca_source') or '<none>'}",
        f"- CA bundle path: {tls_meta.get('ca_bundle_path') or '<none>'}",
        f"- SMTP timeout: {settings['smtp_timeout']}",
        f"- SMTP retries: {settings['smtp_retries']}",
        f"- SMTP retry delay: {settings['smtp_retry_delay']}",
        f"- SMTP debug file: {os.getenv('SMTP_DEBUG_FILE') or '<unset>'}",
        f"- SMTP_TRUSTSTORE: {os.getenv('SMTP_TRUSTSTORE') or '<unset>'}",
        f"- SMTP_CA_FILE: {os.getenv('SMTP_CA_FILE') or '<unset>'}",
        f"- SMTP_CA_BUNDLE: {os.getenv('SMTP_CA_BUNDLE') or '<unset>'}",
        f"- SMTP_RELAX_X509_STRICT: {os.getenv('SMTP_RELAX_X509_STRICT') or '<unset>'}",
    ]
    print("\n".join(lines), file=sys.stderr)


def check_smtp_config() -> None:
    settings = _smtp_runtime_settings()
    missing = _validate_smtp_runtime_settings(settings)
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    print_smtp_config_debug()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run dispatch pipeline and email results.")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="Edition date in YYYY-MM-DD format (default: current UTC date).")
    parser.add_argument("--publish", action="store_true", help="Run publish step after pipeline.")
    parser.add_argument("--pages-repo", help="Pages repository path used by publish step.")
    parser.add_argument("--smtp-debug", action="store_true", help="Enable smtplib debug output on the SMTP connection.")
    parser.add_argument("--send-test-email", action="store_true", help="Send an SMTP-only diagnostic email and do not run the Gaza pipeline.")
    parser.add_argument("--check-smtp-config", action="store_true", help="Validate SMTP/TLS configuration and print masked diagnostics without sending mail or running the Gaza pipeline.")
    parser.add_argument("--email-nonfatal", "--warn-on-email-failure", dest="email_nonfatal", action="store_true", help="Pass through nonfatal email-report mode to the Gaza daily runner.")
    args = parser.parse_args(argv)

    load_env_file()

    if args.check_smtp_config:
        try:
            check_smtp_config()
        except Exception as exc:  # noqa: BLE001
            print(f"SMTP configuration check failed: {notification_error_message(exc)}", file=sys.stderr)
            return 2
        return 0

    if args.send_test_email:
        if args.smtp_debug:
            print_smtp_config_debug()
        subject = f"[Blue Fern Dispatches] SMTP diagnostic - {args.date}"
        try:
            send_email(subject, build_test_email_body(args.date), args.date, smtp_debug=bool(args.smtp_debug))
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to send test email: {notification_error_message(exc)}", file=sys.stderr)
            return 2
        return 0

    py = sys.executable
    pipeline_cmd = [py, str(PIPELINE_SCRIPT), "--date", args.date, "--email-report"]
    if not args.publish:
        pipeline_cmd.append("--dry-run")
    if args.smtp_debug:
        pipeline_cmd.append("--smtp-debug")
    if args.email_nonfatal:
        pipeline_cmd.append("--email-nonfatal")
    if args.pages_repo:
        pipeline_cmd.extend(["--pages-repo", args.pages_repo])
    result = run_command(pipeline_cmd)
    if result["stdout"]:
        print(str(result["stdout"]), end="")
    if result["stderr"]:
        print(str(result["stderr"]), end="", file=sys.stderr)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
