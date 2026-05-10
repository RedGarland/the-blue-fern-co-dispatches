import base64
import importlib.util
from email.message import EmailMessage
from pathlib import Path

from scripts import run_and_notify


spec = importlib.util.spec_from_file_location("run_cascadia_and_notify", "scripts/run_cascadia_and_notify.py")
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


def _cascadia_payload() -> dict[str, object]:
    return {
        "ok": True,
        "date": "2026-05-10",
        "edition_date": "2026-05-10",
        "mode": "weekly-public",
        "coverage_start": "2026-05-04",
        "coverage_end": "2026-05-10",
        "public_story_count": 5,
        "output_paths": {
            "public_site_output": r"C:\PythonProjects\Dispatches From The Blue Fern Co\output\site\cascadia\editions\2026-05-10"
        },
        "warnings": ["sparse week warning"],
        "errors": [],
    }


def _publish_payload() -> dict[str, object]:
    return {
        "ok": True,
        "copied": True,
        "committed": True,
        "commit_sha": "abc1234",
        "pushed": False,
        "paid_detail_excluded_from_public": True,
        "warnings": [],
        "errors": [],
    }


def test_send_test_email_does_not_run_cascadia_or_publish(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(
        notify,
        "run_logged_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline should not run")),
    )
    monkeypatch.setattr(notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = notify.main(["--date", "2026-05-10", "--smtp-debug", "--send-test-email"])

    assert rc == 0
    assert sent == [
        (
            "[Blue Fern Dispatches] Cascadia SMTP diagnostic - 2026-05-10",
            "Blue Fern Dispatches Cascadia SMTP diagnostic message.\nDate: 2026-05-10\n\nThis message was sent by scripts/run_cascadia_and_notify.py --send-test-email.\nNo Cascadia pipeline was run.\nNo Pages publish was run.\npushed: false",
            "2026-05-10",
            True,
        )
    ]


def test_success_email_subject_and_body_include_cascadia_urls_and_pushed_false(monkeypatch, tmp_path):
    sent = []
    calls = []

    def fake_run(cmd, log_path):
        calls.append(cmd)
        Path(log_path).write_text("line\n", encoding="utf-8")
        command = " ".join(cmd)
        if "run_cascadia_dispatch.py" in command:
            return {"exit_code": 0, "stdout": "", "stderr": "", "json": _cascadia_payload()}
        if "publish_github_pages.py" in command:
            return {"exit_code": 0, "stdout": "", "stderr": "", "json": _publish_payload()}
        if "pytest" in command or "doctor.py" in command:
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "json": {}}
        raise AssertionError(command)

    monkeypatch.setattr(notify, "LOG_DIR", tmp_path)
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(notify, "run_logged_command", fake_run)
    monkeypatch.setattr(notify, "pages_ahead_of_remote", lambda pages_repo, pages_branch: True)
    monkeypatch.setattr(notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = notify.main(["--date", "2026-05-10", "--smtp-debug"])

    assert rc == 0
    subject, body, date_str, smtp_debug = sent[0]
    assert subject == "[Blue Fern Dispatches] Cascadia weekly succeeded - 2026-05-10"
    assert date_str == "2026-05-10"
    assert smtp_debug is True
    assert "Cascadia weekly report" in body
    assert "date: 2026-05-10" in body
    assert "ok: true" in body
    assert "public_story_count: 5" in body
    assert "pushed: false" in body
    assert "https://dispatches.thebluefernco.com/cascadia/archive.html" in body
    assert "https://dispatches.thebluefernco.com/cascadia/editions/2026-05-10/" in body
    assert "manual Pages push command" in body


def test_failure_email_subject_and_body_include_errors_and_log_tail(monkeypatch, tmp_path):
    sent = []

    def fake_run(cmd, log_path):
        Path(log_path).write_text("\n".join(f"log {i}" for i in range(100)), encoding="utf-8")
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "boom",
            "json": {"ok": False, "errors": ["provider failure"], "warnings": []},
        }

    monkeypatch.setattr(notify, "LOG_DIR", tmp_path)
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(notify, "run_logged_command", fake_run)
    monkeypatch.setattr(notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = notify.main(["--date", "2026-05-10"])

    assert rc == 1
    subject, body, *_ = sent[0]
    assert subject == "[Blue Fern Dispatches] Cascadia weekly failed - 2026-05-10"
    assert "ok: false" in body
    assert "provider failure" in body
    assert "Cascadia command failed with exit code 1" in body
    assert "last 80 log lines" in body
    assert "log 20" in body
    assert "log 19" not in body


def test_logged_command_sanitizes_smtp_password_marker(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = "OK SMTP_PASSWORD logs\n"
        stderr = "SMTP_PASSWORD should not be written literally\n"

    monkeypatch.setattr(notify.subprocess, "run", lambda *args, **kwargs: Completed())

    log_path = tmp_path / "notify.log"
    result = notify.run_logged_command(["python", "doctor.py"], log_path)

    text = log_path.read_text(encoding="utf-8")
    assert result["exit_code"] == 0
    assert "SMTP_PASSWORD" not in text
    assert "SMTP password marker" in text


class FakeSMTP:
    instances = []

    def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
        self.host = host
        self.port = port
        self.local_hostname = local_hostname
        self.timeout = timeout
        self.context = context
        self.debuglevel = 0
        self.sent_messages: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def set_debuglevel(self, level):
        self.debuglevel = level

    def ehlo(self, name=None):
        if self.debuglevel:
            print("AUTH " + base64.b64encode(b"\0alerts@example.test\0secret-app-password").decode(), file=run_and_notify.sys.stderr)
        return 250, b"OK"

    def has_extn(self, name):
        return name.lower() == "starttls"

    def starttls(self, context=None):
        self.context = context

    def login(self, user, password):
        if self.debuglevel:
            print(f"login {user} {password}", file=run_and_notify.sys.stderr)

    def send_message(self, msg):
        self.sent_messages.append(msg)

    def quit(self):
        return None

    def close(self):
        return None


def test_cascadia_smtp_debug_redacts_credentials(monkeypatch, capsys, tmp_path):
    FakeSMTP.instances = []
    debug_file = tmp_path / "smtp-debug.log"
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "alerts@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret-app-password")
    monkeypatch.setenv("EMAIL_TO", "ops@example.test")
    monkeypatch.setenv("SMTP_DEBUG_FILE", str(debug_file))
    monkeypatch.setenv("SMTP_RETRY_DELAY", "0")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)

    rc = notify.main(["--date", "2026-05-10", "--smtp-debug", "--send-test-email"])

    captured = capsys.readouterr()
    debug_text = debug_file.read_text(encoding="utf-8")
    assert rc == 0
    assert "secret-app-password" not in captured.err
    assert "alerts@example.test" not in captured.err
    assert base64.b64encode(b"\0alerts@example.test\0secret-app-password").decode() not in captured.err
    assert "secret-app-password" not in debug_text
    assert "alerts@example.test" not in debug_text
