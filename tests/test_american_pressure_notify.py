import json
from pathlib import Path
import importlib.util


spec = importlib.util.spec_from_file_location("run_american_pressure_and_notify", "scripts/run_american_pressure_and_notify.py")
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


def test_notify_success_email_includes_paths_and_pushed_false(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(notify, "log_path_for", lambda date_str: tmp_path / f"{date_str}.log")
    monkeypatch.setattr(
        notify,
        "run_logged_command",
        lambda cmd, log_path: {
            "command": " ".join(cmd),
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "source_count": 2,
                    "story_count": 2,
                    "generated": True,
                    "archive_updated": True,
                    "rss_updated": True,
                    "warnings": [],
                    "errors": [],
                }
            ),
            "stderr": "",
            "json": {
                "ok": True,
                "source_count": 2,
                "story_count": 2,
                "generated": True,
                "archive_updated": True,
                "rss_updated": True,
                "warnings": [],
                "errors": [],
            },
        },
    )
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = notify.main(["--date", "2026-05-12", "--publish"])

    assert rc == 0
    assert sent
    body = sent[0][1]
    assert "public archive URL: https://dispatches.thebluefernco.com/american-pressure/archive.html" in body
    assert "public edition URL: https://dispatches.thebluefernco.com/american-pressure/editions/2026-05-09/" in body
    assert "local edition path:" in body
    assert "pushed: false" in body


def test_notify_failure_email_includes_errors_and_log_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    log_path = tmp_path / "fail.log"
    log_path.write_text("line1\nline2\n", encoding="utf-8")
    monkeypatch.setattr(notify, "log_path_for", lambda date_str: log_path)
    monkeypatch.setattr(
        notify,
        "run_logged_command",
        lambda cmd, p: {
            "command": " ".join(cmd),
            "exit_code": 1,
            "stdout": json.dumps({"ok": False, "source_count": 0, "story_count": 0, "generated": False, "errors": ["bad sources"], "warnings": []}),
            "stderr": "",
            "json": {"ok": False, "source_count": 0, "story_count": 0, "generated": False, "errors": ["bad sources"], "warnings": []},
        },
    )
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = notify.main(["--date", "2026-05-12"])

    assert rc == 1
    body = sent[0][1]
    assert "- bad sources" in body
    assert "last 80 log lines:" in body
    assert "American Pressure weekly notify run for 2026-05-12" in body


def test_notify_smtp_debug_passthrough(monkeypatch):
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    monkeypatch.setattr(notify, "print_smtp_config_debug", lambda: None)
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda subject, body, date_str, smtp_debug=False: sent.append((subject, body, date_str, smtp_debug)))

    rc = notify.main(["--date", "2026-05-12", "--smtp-debug", "--send-test-email"])

    assert rc == 0
    assert sent[0][3] is True


def test_task_template_uses_project_venv_and_clean_path():
    task = Path("ops/run_american_pressure_weekly_task.xml").read_text(encoding="utf-8")
    assert "scripts\\run_american_pressure_and_notify.py" in task
    assert r"&amp; '.\.venv\Scripts\python.exe'" in task
    assert "OneDrive" not in task
    assert "C:\\Users\\Admin\\Desktop\\Python" not in task


def test_notify_rejects_push_without_publish(monkeypatch):
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    rc = notify.main(["--date", "2026-05-12", "--push"])
    assert rc == 1


def test_notify_rejects_non_default_pages_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "load_env_file", lambda path=None: None)
    rc = notify.main(["--date", "2026-05-12", "--pages-repo", str(tmp_path)])
    assert rc == 1
