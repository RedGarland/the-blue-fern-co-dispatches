import base64
import importlib.util
import json
import ssl
import sys
import types
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
    assert "validation_profile: cascadia_weekly" in body
    assert "skipped_unrelated_tests: true" in body
    assert "pipeline_ok: false" not in body
    assert "email_ok: none" in body
    assert "pushed: false" in body
    assert "https://dispatches.thebluefernco.com/cascadia/archive.html" in body
    assert "https://dispatches.thebluefernco.com/cascadia/editions/2026-05-10/" in body
    assert "manual Pages push command" in body


def test_parse_args_accepts_push_flag():
    args = notify.parse_args(["--date", "2026-05-24", "--push", "--skip-tests"])
    assert args.push is True
    assert args.date == "2026-05-24"
    assert args.skip_tests is True


def test_cascadia_default_no_push_keeps_manual_push_command(monkeypatch, tmp_path, capsys):
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
    monkeypatch.setattr(notify, "send_email", lambda *args, **kwargs: None)

    rc = notify.main(["--date", "2026-05-24"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["pages_repo_updated"] is True
    assert payload["pushed"] is False
    assert payload["pages_branch"] == "gh-pages"
    assert payload["pages_commit_sha"] == "abc1234"
    assert "git push origin gh-pages" in payload["manual_push_command"]
    assert all("git -C" not in " ".join(cmd) or " push " not in f" {' '.join(cmd)} " for cmd in calls)


def test_cascadia_push_enabled_attempts_pages_push(monkeypatch, tmp_path, capsys):
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
        if "git -C" in command and " status" in command:
            return {"exit_code": 0, "stdout": "On branch gh-pages", "stderr": "", "json": {}}
        if "git -C" in command and " push origin gh-pages" in command:
            return {"exit_code": 0, "stdout": "pushed", "stderr": "", "json": {}}
        raise AssertionError(command)

    monkeypatch.setattr(notify, "LOG_DIR", tmp_path)
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(notify, "run_logged_command", fake_run)
    monkeypatch.setattr(notify, "send_email", lambda *args, **kwargs: None)

    rc = notify.main(["--date", "2026-05-24", "--push"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["pushed"] is True
    assert payload["publish_ok"] is True
    assert payload["manual_push_command"] is None
    assert any("git -C" in " ".join(cmd) and "push origin gh-pages" in " ".join(cmd) for cmd in calls)


def test_cascadia_push_report_stays_false_when_push_fails(monkeypatch, tmp_path, capsys):
    def fake_run(cmd, log_path):
        Path(log_path).write_text("line\n", encoding="utf-8")
        command = " ".join(cmd)
        if "run_cascadia_dispatch.py" in command:
            return {"exit_code": 0, "stdout": "", "stderr": "", "json": _cascadia_payload()}
        if "publish_github_pages.py" in command:
            return {"exit_code": 0, "stdout": "", "stderr": "", "json": _publish_payload()}
        if "pytest" in command or "doctor.py" in command:
            return {"exit_code": 0, "stdout": "ok", "stderr": "", "json": {}}
        if "git -C" in command and " status" in command:
            return {"exit_code": 0, "stdout": "On branch gh-pages", "stderr": "", "json": {}}
        if "git -C" in command and " push origin gh-pages" in command:
            return {"exit_code": 1, "stdout": "", "stderr": "push rejected", "json": {}}
        raise AssertionError(command)

    monkeypatch.setattr(notify, "LOG_DIR", tmp_path)
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(notify, "run_logged_command", fake_run)
    monkeypatch.setattr(notify, "send_email", lambda *args, **kwargs: None)

    rc = notify.main(["--date", "2026-05-24", "--push"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["pushed"] is False
    assert payload["publish_ok"] is False
    assert payload["publish_blocked_reason"] == "pages-push-failed"
    assert any("push rejected" in str(item) for item in payload["errors"])


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


def test_cascadia_pipeline_success_email_failure_reports_pipeline_ok(monkeypatch, tmp_path, capsys):
    def fake_run(cmd, log_path):
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
    monkeypatch.setattr(notify, "pages_ahead_of_remote", lambda pages_repo, pages_branch: False)
    monkeypatch.setattr(notify, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("smtp down")))

    rc = notify.main(["--date", "2026-05-10"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["pipeline_ok"] is True
    assert payload["generation_ok"] is True
    assert payload["publish_ok"] is True
    assert payload["email_requested"] is True
    assert payload["email_ok"] is False
    assert payload["overall_ok"] is False
    assert "smtp down" in str(payload["notification_error"])


def test_cascadia_notification_error_tls_hint_is_sanitized(monkeypatch, tmp_path, capsys):
    def fake_run(cmd, log_path):
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
    monkeypatch.setattr(notify, "pages_ahead_of_remote", lambda pages_repo, pages_branch: False)
    monkeypatch.setenv("SMTP_PASSWORD", "super-secret")
    tls_exc = ssl.SSLCertVerificationError("certificate verify failed")
    monkeypatch.setattr(notify, "send_email", lambda *args, **kwargs: (_ for _ in ()).throw(tls_exc))

    rc = notify.main(["--date", "2026-05-10"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    err = str(payload["notification_error"])
    assert "SMTP TLS certificate verification failed" in err
    assert "SMTP_CA_FILE" in err and "SMTP_CA_BUNDLE" in err
    assert "SMTP_RELAX_X509_STRICT=1" in err
    assert "super-secret" not in err


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


def test_send_email_ssl_mode_uses_smtp_ssl(monkeypatch):
    called = {"ssl": False, "starttls": False}

    class FakeSMTPSSL:
        def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
            called["ssl"] = True
            self.context = context

        def set_debuglevel(self, level):
            return None

        def ehlo(self, name=None):
            return 250, b"OK"

        def login(self, user, password):
            return None

        def send_message(self, msg):
            return None

        def quit(self):
            return None

        def close(self):
            return None

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP should not be used in SSL mode")

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USE_SSL", "1")
    monkeypatch.setenv("SMTP_USER", "alerts@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_TO", "ops@example.test")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)
    run_and_notify.send_email("subj", "body", "2026-05-10")
    assert called["ssl"] is True
    assert called["starttls"] is False


def test_send_email_starttls_mode_uses_smtp_starttls(monkeypatch):
    called = {"smtp": False, "starttls": False}

    class FakeSMTP:
        def __init__(self, host, port, local_hostname=None, timeout=None):
            called["smtp"] = True

        def set_debuglevel(self, level):
            return None

        def ehlo(self, name=None):
            return 250, b"OK"

        def has_extn(self, name):
            return name.lower() == "starttls"

        def starttls(self, context=None):
            called["starttls"] = True

        def login(self, user, password):
            return None

        def send_message(self, msg):
            return None

        def quit(self):
            return None

        def close(self):
            return None

    class FakeSMTPSSL:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP_SSL should not be used in STARTTLS mode")

    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_SSL", "0")
    monkeypatch.setenv("SMTP_USER", "alerts@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_TO", "ops@example.test")
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(run_and_notify.smtplib, "SMTP_SSL", FakeSMTPSSL)
    run_and_notify.send_email("subj", "body", "2026-05-10")
    assert called["smtp"] is True
    assert called["starttls"] is True


def test_build_tls_context_uses_custom_ca_bundle(monkeypatch, tmp_path):
    pem = tmp_path / "custom-ca.pem"
    pem.write_text("PEM", encoding="utf-8")
    monkeypatch.setenv("SMTP_CA_FILE", str(pem))
    monkeypatch.delenv("SMTP_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SMTP_RELAX_X509_STRICT", raising=False)
    monkeypatch.delenv("SMTP_SKIP_VERIFY", raising=False)
    monkeypatch.delenv("SMTP_TLS_VERIFY", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(run_and_notify.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = run_and_notify._build_tls_context()
    assert called["cafile"] == str(pem.resolve())
    assert meta["ca_source"] == "custom_bundle"
    assert meta["ca_bundle_env"] == "SMTP_CA_FILE"


def test_build_tls_context_uses_certifi_fallback_when_available(monkeypatch, tmp_path):
    certifi_pem = tmp_path / "certifi.pem"
    certifi_pem.write_text("PEM", encoding="utf-8")
    fake_certifi = types.SimpleNamespace(where=lambda: str(certifi_pem))
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    monkeypatch.setenv("SMTP_TLS_CA_SOURCE", "certifi")
    monkeypatch.delenv("SMTP_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SMTP_CA_FILE", raising=False)
    monkeypatch.delenv("SMTP_RELAX_X509_STRICT", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(run_and_notify.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = run_and_notify._build_tls_context()
    assert called["cafile"] == str(certifi_pem)
    assert meta["ca_source"] == "certifi"


def test_build_tls_context_explicit_certifi_overrides_ambient_truststore(monkeypatch, tmp_path):
    certifi_pem = tmp_path / "certifi.pem"
    certifi_pem.write_text("PEM", encoding="utf-8")
    fake_certifi = types.SimpleNamespace(where=lambda: str(certifi_pem))
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    monkeypatch.setenv("SMTP_TLS_CA_SOURCE", "certifi")
    monkeypatch.setenv("SMTP_TRUSTSTORE", "1")
    monkeypatch.delenv("SMTP_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SMTP_CA_FILE", raising=False)
    monkeypatch.delenv("SMTP_RELAX_X509_STRICT", raising=False)
    called: dict[str, str | None] = {"cafile": None}

    def fake_create_default_context(cafile=None):
        called["cafile"] = cafile
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setattr(run_and_notify.ssl, "create_default_context", fake_create_default_context)
    _ctx, meta = run_and_notify._build_tls_context()
    assert called["cafile"] == str(certifi_pem)
    assert meta["ca_source"] == "certifi"


def test_build_tls_context_prefers_truststore_when_enabled(monkeypatch):
    fake_truststore = types.SimpleNamespace(SSLContext=lambda protocol: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    monkeypatch.setitem(sys.modules, "truststore", fake_truststore)
    monkeypatch.setenv("SMTP_TRUSTSTORE", "1")
    monkeypatch.delenv("SMTP_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SMTP_CA_FILE", raising=False)
    _ctx, meta = run_and_notify._build_tls_context()
    assert meta["ca_source"] == "truststore"


def test_build_tls_context_relax_strict_only_when_explicit(monkeypatch):
    monkeypatch.delenv("SMTP_RELAX_X509_STRICT", raising=False)
    monkeypatch.delenv("SMTP_SKIP_VERIFY", raising=False)
    monkeypatch.delenv("SMTP_TLS_VERIFY", raising=False)
    _ctx_strict, strict_meta = run_and_notify._build_tls_context()
    assert strict_meta["tls_verify_enabled"] is True
    monkeypatch.setenv("SMTP_RELAX_X509_STRICT", "1")
    _ctx_relaxed, relaxed_meta = run_and_notify._build_tls_context()
    assert relaxed_meta["tls_verify_enabled"] is False
    assert relaxed_meta["tls_relaxed"] is True
