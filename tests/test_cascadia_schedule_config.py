from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = r"C:\PythonProjects\Dispatches From The Blue Fern Co"
OLD_ADMIN_ROOT = r"C:\Users\Admin"
OLD_ONEDRIVE_ROOT = r"C:\Users\willb\OneDrive"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_cascadia_task_xml_uses_clean_project_runtime_without_push():
    text = _read(ROOT / "ops" / "run_cascadia_weekly_task.xml")

    assert OLD_ADMIN_ROOT not in text
    assert OLD_ONEDRIVE_ROOT not in text
    assert "<Command>powershell.exe</Command>" in text
    assert PROJECT_ROOT in text
    assert r".\.venv\Scripts\python.exe" in text
    assert "'scripts\\run_cascadia_and_notify.py'" in text
    assert "CASCADIA_ALLOW_CURL_NO_REVOKE='1'" in text
    assert "CASCADIA_FETCH_BACKEND='auto'" in text
    assert "SMTP_RELAX_X509_STRICT='1'" in text
    assert "--date (Get-Date -Format 'yyyy-MM-dd')" in text
    assert "--push" not in text


def test_cascadia_task_xml_does_not_call_raw_dispatch_scheduler_command():
    text = _read(ROOT / "ops" / "run_cascadia_weekly_task.xml")

    assert "'scripts\\run_cascadia_dispatch.py'" not in text
    assert "--weekly-public" not in text
    assert "--historical-search" not in text


def test_cascadia_notify_script_exists():
    assert (ROOT / "scripts" / "run_cascadia_and_notify.py").is_file()


def test_cascadia_schedule_docs_use_clean_runtime_fields_without_push():
    text = _read(ROOT / "docs" / "dispatches-project.md")
    section = text.split("Task Scheduler setup for Cascadia:", 1)[1].split("Stage outputs:", 1)[0]

    assert OLD_ADMIN_ROOT not in section
    assert OLD_ONEDRIVE_ROOT not in section
    assert "Program/script: `powershell.exe`" in section
    assert f"Start in: `{PROJECT_ROOT}`" in section
    assert f"Set-Location '{PROJECT_ROOT}'" in section
    assert r"& '.\.venv\Scripts\python.exe' 'scripts\run_cascadia_and_notify.py'" in section
    assert "CASCADIA_ALLOW_CURL_NO_REVOKE='1'" in section
    assert "CASCADIA_FETCH_BACKEND='auto'" in section
    assert "SMTP_RELAX_X509_STRICT='1'" in section
    assert "--push" not in section
