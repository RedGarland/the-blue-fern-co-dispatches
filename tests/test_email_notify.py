import os
import smtplib
import time
import threading
import asyncio
import base64
from pathlib import Path
import importlib.util
import pytest

spec = importlib.util.spec_from_file_location("run_and_notify", "scripts/run_and_notify.py")
run_and_notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_and_notify)


@pytest.fixture(autouse=True)
def _disable_env_file_loading(monkeypatch):
    monkeypatch.setattr(run_and_notify, "load_env_file", lambda path=None: None)


async def _smtp_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, out_list: list):
    writer.write(b"220 localhost SimpleSMTP\r\n")
    await writer.drain()
    mailfrom = None
    rcpttos = []
    data_lines = []
    in_data = False
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip("\r\n")
            if in_data:
                if text == ".":
                    in_data = False
                    out_list.append("\n".join(data_lines))
                    data_lines.clear()
                    writer.write(b"250 OK\r\n")
                    await writer.drain()
                else:
                    data_lines.append(text)
                continue

            upper = text.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                writer.write(b"250-localhost Hello\r\n250 OK\r\n")
            elif upper.startswith("MAIL FROM"):
                mailfrom = text[10:].strip()
                writer.write(b"250 OK\r\n")
            elif upper.startswith("RCPT TO"):
                rcpttos.append(text[8:].strip())
                writer.write(b"250 OK\r\n")
            elif upper == "DATA":
                writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                in_data = True
            elif upper in ("QUIT", "RSET"):
                writer.write(b"221 Bye\r\n")
                await writer.drain()
                break
            else:
                writer.write(b"250 OK\r\n")
            await writer.drain()
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def _start_async_smtp(host: str, port: int, out_list: list):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _runner():
        server = await asyncio.start_server(lambda r, w: _smtp_handler(r, w, out_list), host, port)
        try:
            await server.serve_forever()
        finally:
            server.close()
            await server.wait_closed()

    try:
        loop.run_until_complete(_runner())
    finally:
        loop.close()


def test_send_email_integration(monkeypatch):
    host = "127.0.0.1"
    port = 1025
    received = []

    thread = threading.Thread(target=_start_async_smtp, args=(host, port, received), daemon=True)
    thread.start()
    time.sleep(0.1)

    monkeypatch.setenv("SMTP_HOST", host)
    monkeypatch.setenv("SMTP_PORT", str(port))
    monkeypatch.setenv("EMAIL_TO", "recipient@example.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    monkeypatch.setattr(run_and_notify, "run_command", lambda cmd: (_ for _ in ()).throw(AssertionError("pipeline should not run")))

    rc = run_and_notify.main(["--date", "2026-05-04", "--send-test-email"])
    assert rc == 0

    # give the server a moment to process
    time.sleep(0.2)

    # Stop the server by connecting and sending QUIT
    try:
        import socket

        with socket.create_connection((host, port), timeout=1) as s:
            s.recv(1024)
            s.sendall(b"QUIT\r\n")
    except Exception:
        pass

    thread.join(timeout=2)
    assert len(received) >= 1, f"expected at least 1 message, got {len(received)}"
    data = received[0]
    if isinstance(data, bytes):
        assert b"SMTP diagnostic" in data
    else:
        assert "SMTP diagnostic" in data


class FakeSMTP:
    instances = []

    def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
        self.host = host
        self.port = port
        self.local_hostname = local_hostname
        self.timeout = timeout
        self.context = context
        self.debuglevel = 0
        self.started_tls = False
        self.ehlo_args = []
        self.logged_in = None
        self.sent_messages = []
        self.quit_called = False
        self.closed = False
        FakeSMTP.instances.append(self)

    def set_debuglevel(self, level):
        self.debuglevel = level

    def ehlo(self, name=None):
        self.ehlo_args.append(name)
        if self.debuglevel:
            print("fake SMTP debug: EHLO", file=run_and_notify.sys.stderr)
        return 250, b"OK"

    def has_extn(self, name):
        return name.lower() == "starttls"

    def starttls(self, context=None):
        self.started_tls = True
        self.starttls_context = context

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def quit(self):
        self.quit_called = True

    def close(self):
        self.closed = True


def _set_email_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "alerts@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret-app-password")
    monkeypatch.setenv("EMAIL_TO", "ops@example.test")
    monkeypatch.setenv("SMTP_RETRY_DELAY", "0")


def test_send_email_uses_starttls_and_timeout(monkeypatch):
    FakeSMTP.instances = []
    _set_email_env(monkeypatch)
    monkeypatch.setenv("SMTP_TIMEOUT", "7")
    monkeypatch.setenv("SMTP_LOCAL_HOSTNAME", "dispatches.example.test")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)

    run_and_notify.send_email("subject", "body", "2026-05-04")

    smtp = FakeSMTP.instances[0]
    assert smtp.local_hostname == "dispatches.example.test"
    assert smtp.ehlo_args == ["dispatches.example.test", "dispatches.example.test"]
    assert smtp.timeout == 7
    assert smtp.started_tls is True
    assert smtp.starttls_context is not None
    assert smtp.logged_in == ("alerts@example.test", "secret-app-password")
    assert smtp.sent_messages[0]["To"] == "ops@example.test"
    assert smtp.quit_called is True


def test_send_email_uses_smtp_ssl_when_requested(monkeypatch):
    FakeSMTP.instances = []
    _set_email_env(monkeypatch)
    monkeypatch.setenv("SMTP_USE_SSL", "yes")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP_SSL", FakeSMTP)

    run_and_notify.send_email("subject", "body", "2026-05-04")

    smtp = FakeSMTP.instances[0]
    assert smtp.port == 587
    assert smtp.context is not None
    assert smtp.started_tls is False
    assert smtp.sent_messages


def test_send_email_derives_local_hostname_from_sender_domain(monkeypatch):
    FakeSMTP.instances = []
    _set_email_env(monkeypatch)
    monkeypatch.delenv("SMTP_LOCAL_HOSTNAME", raising=False)
    monkeypatch.setenv("EMAIL_FROM", "dispatches@thebluefernco.com")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)

    run_and_notify.send_email("subject", "body", "2026-05-04")

    smtp = FakeSMTP.instances[0]
    assert smtp.local_hostname == "thebluefernco.com"
    assert smtp.ehlo_args == ["thebluefernco.com", "thebluefernco.com"]


def test_send_email_uses_stable_local_hostname_when_machine_name_is_short(monkeypatch):
    FakeSMTP.instances = []
    _set_email_env(monkeypatch)
    monkeypatch.delenv("SMTP_LOCAL_HOSTNAME", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    monkeypatch.setattr(run_and_notify.socket, "getfqdn", lambda: "DESKTOP-U5S5ND1")
    monkeypatch.setattr(run_and_notify.socket, "gethostname", lambda: "DESKTOP-U5S5ND1")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)

    run_and_notify.send_email("subject", "body", "2026-05-04")

    smtp = FakeSMTP.instances[0]
    assert smtp.local_hostname == "localhost.localdomain"
    assert smtp.ehlo_args == ["localhost.localdomain", "localhost.localdomain"]


def test_send_email_retries_after_send_failure(monkeypatch):
    _set_email_env(monkeypatch)
    attempts = {"count": 0}

    class FlakySMTP(FakeSMTP):
        def send_message(self, msg):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise smtplib.SMTPServerDisconnected("temporary drop")
            super().send_message(msg)

    FakeSMTP.instances = []
    monkeypatch.setenv("SMTP_RETRIES", "1")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FlakySMTP)

    run_and_notify.send_email("subject", "body", "2026-05-04")

    assert attempts["count"] == 2
    assert len(FakeSMTP.instances) == 2
    assert FakeSMTP.instances[0].quit_called is True
    assert FakeSMTP.instances[1].sent_messages


def test_smtp_debug_file_tees_debug_output(monkeypatch, capsys):
    FakeSMTP.instances = []
    _set_email_env(monkeypatch)
    debug_file = Path("output") / "smtp-debug-test.log"
    debug_file.unlink(missing_ok=True)
    monkeypatch.setenv("SMTP_DEBUG_FILE", str(debug_file))
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)

    try:
        run_and_notify.send_email("subject", "body", "2026-05-04", smtp_debug=True)

        captured = capsys.readouterr()
        assert "fake SMTP debug: EHLO" in captured.err
        assert "fake SMTP debug: EHLO" in debug_file.read_text(encoding="utf-8")
    finally:
        debug_file.unlink(missing_ok=True)


def test_send_test_email_returns_nonzero_when_email_send_fails(monkeypatch, capsys):
    monkeypatch.setattr(
        run_and_notify,
        "run_command",
        lambda cmd: (_ for _ in ()).throw(AssertionError("pipeline should not run")),
    )
    monkeypatch.setattr(run_and_notify, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("network down")))

    rc = run_and_notify.main(["--date", "2026-05-04", "--send-test-email"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "OSError: network down" in captured.err


def test_send_test_email_does_not_run_pipeline_or_publish(monkeypatch):
    sent = []
    monkeypatch.setattr(
        run_and_notify,
        "run_command",
        lambda cmd: (_ for _ in ()).throw(AssertionError(f"unexpected command: {cmd}")),
    )
    monkeypatch.setattr(run_and_notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = run_and_notify.main(["--date", "2026-05-09", "--publish", "--pages-repo", "pages", "--smtp-debug", "--send-test-email"])

    assert rc == 0
    assert sent == [
        (
            "[Blue Fern Dispatches] SMTP diagnostic - 2026-05-09",
            "Blue Fern Dispatches SMTP diagnostic message.\nDate: 2026-05-09\n\nThis message was sent by scripts/run_and_notify.py --send-test-email.\nNo Gaza pipeline was run.",
            "2026-05-09",
            True,
        )
    ]


def test_send_test_email_missing_smtp_env_returns_clear_failure(monkeypatch, capsys):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr(run_and_notify, "run_command", lambda cmd: (_ for _ in ()).throw(AssertionError("pipeline should not run")))

    rc = run_and_notify.main(["--date", "2026-05-09", "--send-test-email"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "Missing required env vars: SMTP_HOST, EMAIL_TO" in captured.err


def test_send_test_email_debug_redacts_smtp_password(monkeypatch, capsys, tmp_path):
    FakeSMTP.instances = []
    _set_email_env(monkeypatch)
    secret = "secret-app-password"
    debug_file = tmp_path / "smtp-debug.log"
    monkeypatch.setenv("SMTP_DEBUG_FILE", str(debug_file))
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(run_and_notify, "run_command", lambda cmd: (_ for _ in ()).throw(AssertionError("pipeline should not run")))

    rc = run_and_notify.main(["--date", "2026-05-09", "--smtp-debug", "--send-test-email"])

    captured = capsys.readouterr()
    assert rc == 0
    assert secret not in captured.out
    assert secret not in captured.err
    assert base64.b64encode(b"alerts@example.test").decode() not in captured.err
    assert secret not in debug_file.read_text(encoding="utf-8")
    assert base64.b64encode(b"alerts@example.test").decode() not in debug_file.read_text(encoding="utf-8")


def test_normal_run_invokes_gaza_daily_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(run_and_notify, "run_command", lambda cmd: calls.append(cmd) or {"command": " ".join(cmd), "exit_code": 0, "stdout": "ok", "stderr": ""})

    rc = run_and_notify.main(["--date", "2026-05-09"])

    assert rc == 0
    assert len(calls) == 1
    assert "run_daily_gaza.py" in calls[0][1]
    assert "--email-report" in calls[0]
    assert "--dry-run" in calls[0]


def test_publish_run_maps_to_gaza_publish_behavior(monkeypatch):
    calls = []
    monkeypatch.setattr(run_and_notify, "run_command", lambda cmd: calls.append(cmd) or {"command": " ".join(cmd), "exit_code": 0, "stdout": "ok", "stderr": ""})

    rc = run_and_notify.main(["--date", "2026-05-09", "--publish", "--pages-repo", "pages", "--smtp-debug"])

    assert rc == 0
    assert len(calls) == 1
    assert "run_daily_gaza.py" in calls[0][1]
    assert "--dry-run" not in calls[0]
    assert "--email-report" in calls[0]
    assert "--smtp-debug" in calls[0]
    assert calls[0][-2:] == ["--pages-repo", "pages"]


@pytest.mark.skipif(os.getenv("INTEGRATION_SMTP") != "1", reason="Integration SMTP not enabled")
def test_send_email_real_smtp_integration(monkeypatch):
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip(f"Missing real SMTP env vars: {', '.join(missing)}")

    monkeypatch.setattr(
        run_and_notify,
        "run_command",
        lambda cmd: {"command": " ".join(cmd), "exit_code": 0, "stdout": "ok", "stderr": ""},
    )

    rc = run_and_notify.main(["--date", "2026-05-04", "--send-test-email"])
    assert rc == 0
