import shutil
import uuid
from pathlib import Path

from scripts import doctor


ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = ROOT / "output" / "doctor-test-runs"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_contract_root() -> Path:
    root = SCRATCH_ROOT / uuid.uuid4().hex
    for folder in [
        ".venv",
        "assets",
        "data/dispatches/gaza",
        "data/dispatches/american-pressure",
        "data/dispatches/cascadia",
        "docs",
        "logs",
        "ops",
        "output/site",
        "scripts",
        "src/bluefern_dispatches",
        "tests",
    ]:
        (root / folder).mkdir(parents=True, exist_ok=True)

    _write(root / "scripts" / "run_daily_gaza.py", "print('daily')\n")
    _write(
        root / "ops" / "generate_and_notify_task.xml",
        rf"<Task><Actions><Exec><Arguments>Set-Location '{root}'; &amp; '.\.venv\Scripts\python.exe' 'scripts\run_and_notify.py'</Arguments><WorkingDirectory>{root}</WorkingDirectory></Exec></Actions></Task>",
    )
    _write(root / "src" / "bluefern_dispatches" / "__init__.py", "")
    _write(root / "output" / "site" / "gaza" / "archive.html", "<a href=\"editions/2026-05-08/\">Gaza</a>")

    label = "May 4-10, 2026"
    for name in ["archive.html", "index.html", "rss.xml"]:
        _write(
            root / "output" / "site" / "cascadia" / name,
            f'<a href="editions/2026-05-10/">The Cascadia Briefing - {label}</a>',
        )
    _write(
        root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "edition_manifest.json",
        """{
  "briefing_type": "weekly",
  "edition_date": "2026-05-10",
  "coverage_start": "2026-05-04",
  "coverage_end": "2026-05-10",
  "coverage_label": "May 4-10, 2026"
}
""",
    )
    _write(root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-08" / "manual_sources.json", "[]\n")
    _write(
        root / "data" / "dispatches" / "cascadia" / "sources" / "2026-05-04_2026-05-10" / "historical_search_report.json",
        '{"queries": []}\n',
    )
    _write(root / "logs" / "publish-2026-05-08.log", "clean log\n")
    return root


def _cleanup_contract_root(root: Path) -> None:
    resolved = root.resolve()
    scratch = SCRATCH_ROOT.resolve()
    if scratch in resolved.parents:
        shutil.rmtree(resolved, ignore_errors=True)


def _result_map(root: Path) -> dict[str, doctor.CheckResult]:
    return {result.name: result for result in doctor.run_checks(root)}


def test_doctor_passes_clean_project_contract_fixture():
    root = _make_contract_root()
    try:
        results = doctor.run_checks(root)

        assert all(result.ok for result in results), [result for result in results if not result.ok]
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_public_detail_folder():
    root = _make_contract_root()
    try:
        (root / "output" / "site" / "detail").mkdir()

        result = _result_map(root)["public detail/paid exclusion"]

        assert not result.ok
        assert "detail" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_scheduled_task_without_project_venv():
    root = _make_contract_root()
    try:
        _write(
            root / "ops" / "generate_and_notify_task.xml",
            r"<Task><Actions><Exec><Arguments>&amp; 'C:\path\to\venv\Scripts\python.exe' 'scripts\run_and_notify.py'</Arguments></Exec></Actions></Task>",
        )

        result = _result_map(root)["scheduled task .venv"]

        assert not result.ok
        assert "project .venv" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_scheduled_task_with_old_absolute_python_path():
    root = _make_contract_root()
    try:
        _write(
            root / "ops" / "generate_and_notify_task.xml",
            rf"<Task><Actions><Exec><Arguments>Set-Location '{root}'; &amp; 'C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\.venv\Scripts\python.exe' 'scripts\run_and_notify.py'</Arguments><WorkingDirectory>{root}</WorkingDirectory></Exec></Actions></Task>",
        )

        result = _result_map(root)["scheduled task .venv"]

        assert not result.ok
        assert "non-project Python path" in result.message
        assert "Admin" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_scheduled_task_missing_project_working_directory():
    root = _make_contract_root()
    try:
        _write(
            root / "ops" / "generate_and_notify_task.xml",
            r"<Task><Actions><Exec><Arguments>&amp; '.\.venv\Scripts\python.exe' 'scripts\run_and_notify.py'</Arguments></Exec></Actions></Task>",
        )

        result = _result_map(root)["scheduled task .venv"]

        assert not result.ok
        assert "working directory" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_checks_cascadia_scheduled_task_template():
    root = _make_contract_root()
    try:
        _write(
            root / "ops" / "run_cascadia_weekly_task.xml",
            r"<Task><Actions><Exec><Arguments>&amp; 'C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\.venv\Scripts\python.exe' 'scripts\run_cascadia_dispatch.py' --weekly-public --historical-search</Arguments></Exec></Actions></Task>",
        )

        result = _result_map(root)["scheduled task .venv"]

        assert not result.ok
        assert "run_cascadia_weekly_task.xml" in result.message
        assert "working directory" in result.message
        assert "non-project Python path" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_cascadia_transitional_date_links():
    root = _make_contract_root()
    try:
        _write(
            root / "output" / "site" / "cascadia" / "archive.html",
            '<a href="editions/2026-05-08/">The Cascadia Briefing - May 8, 2026</a>',
        )

        result = _result_map(root)["Cascadia transitional dates excluded"]

        assert not result.ok
        assert "2026-05-08" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_excludes_all_known_cascadia_transitional_daily_dates():
    root = _make_contract_root()
    try:
        for day in ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08", "2026-05-09"]:
            _write(
                root / "output" / "site" / "cascadia" / "archive.html",
                f'<a href="editions/{day}/">The Cascadia Briefing - {day}</a>',
            )

            result = _result_map(root)["Cascadia transitional dates excluded"]

            assert not result.ok
            assert day in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_non_weekly_cascadia_manifest():
    root = _make_contract_root()
    try:
        _write(
            root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "edition_manifest.json",
            """{
  "briefing_type": "daily",
  "edition_date": "2026-05-10",
  "coverage_start": "2026-05-04",
  "coverage_end": "2026-05-09",
  "coverage_label": "May 4-9, 2026"
}
""",
        )

        result = _result_map(root)["Cascadia weekly public links"]

        assert not result.ok
        assert "not weekly" in result.message
        assert "coverage_end is not edition date" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_accepts_cascadia_2026_05_03_weekly_public_links():
    root = _make_contract_root()
    try:
        label = "Apr 27-May 3, 2026"
        for name in ["archive.html", "index.html", "rss.xml"]:
            _write(
                root / "output" / "site" / "cascadia" / name,
                f'<a href="editions/2026-05-03/">The Cascadia Briefing - {label}</a>',
            )
        _write(
            root / "output" / "site" / "cascadia" / "editions" / "2026-05-03" / "edition_manifest.json",
            """{
  "briefing_type": "weekly",
  "edition_date": "2026-05-03",
  "coverage_start": "2026-04-27",
  "coverage_end": "2026-05-03",
  "coverage_label": "Apr 27-May 3, 2026"
}
""",
        )

        results = _result_map(root)

        assert results["Cascadia weekly public links"].ok
        assert results["Cascadia transitional dates excluded"].ok
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_bad_json_and_smtp_password_log_marker():
    root = _make_contract_root()
    try:
        _write(root / "data" / "dispatches" / "gaza" / "sources" / "2026-05-09" / "manual_sources.json", "{bad")
        _write(root / "logs" / "dispatches-20260509.log", "SMTP_PASSWORD leaked\n")

        results = _result_map(root)

        assert not results["manual source JSON"].ok
        assert "2026-05-09" in results["manual source JSON"].message
        assert not results["SMTP_PASSWORD logs"].ok
        assert "dispatches-20260509.log" in results["SMTP_PASSWORD logs"].message
    finally:
        _cleanup_contract_root(root)


def test_doctor_flags_public_html_mechanical_or_incomplete_prose():
    root = _make_contract_root()
    try:
        _write(
            root / "output" / "site" / "cascadia" / "editions" / "2026-05-10" / "index.html",
            "<p>It is included because the source metadata ties it to housing in Idaho.</p>"
            "<p>In three states, Democratic lawmakers introduced bills this session that would allow.</p>",
        )
        result = _result_map(root)["public HTML prose quality"]
        assert not result.ok
        assert "contains banned phrase: it is included because" in result.message
        assert "contains incomplete modal ending: would allow." in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_passes_after_smtp_password_log_marker_is_sanitized():
    root = _make_contract_root()
    try:
        _write(root / "logs" / "dispatches-20260509.log", "SMTP password marker removed\n")

        result = _result_map(root)["SMTP_PASSWORD logs"]

        assert result.ok
    finally:
        _cleanup_contract_root(root)


def test_doctor_checks_pages_repo_branch_and_cname(monkeypatch):
    root = _make_contract_root()
    try:
        (root / "bluefern-dispatches-pages" / ".git").mkdir(parents=True)
        _write(root / "bluefern-dispatches-pages" / "CNAME", "wrong.example\n")
        monkeypatch.setattr(doctor, "_git_branch", lambda repo: "main")

        results = _result_map(root)

        assert not results["Pages repo branch"].ok
        assert "main" in results["Pages repo branch"].message
        assert not results["Pages CNAME"].ok
        assert "wrong.example" in results["Pages CNAME"].message
    finally:
        _cleanup_contract_root(root)


def test_classify_ap_artifact_path_groups_paths():
    assert doctor.classify_ap_artifact_path("data/source_registry/american_pressure_sources.json") == "durable_source_registry"
    assert (
        doctor.classify_ap_artifact_path(
            "data/dispatches/american-pressure/sources/2026-05-16/manual_sources.json"
        )
        == "durable_manual_sources"
    )
    assert (
        doctor.classify_ap_artifact_path(
            "data/dispatches/american-pressure/candidates/2026-05-22/candidate_sources.json"
        )
        == "deferred_intake_candidates"
    )
    assert (
        doctor.classify_ap_artifact_path(
            "data/dispatches/american-pressure/sources/2026-05-16/feed_backfill_sources.json"
        )
        == "deferred_feed_backfill"
    )
    assert doctor.classify_ap_artifact_path("data/dispatches/american-pressure/sources/2026-05-16/unknown.json") is None


def test_doctor_flags_tracked_deferred_ap_artifacts(monkeypatch):
    root = _make_contract_root()
    try:
        monkeypatch.setattr(
            doctor,
            "_git_tracked_files",
            lambda _: [
                "data/source_registry/american_pressure_sources.json",
                "data/dispatches/american-pressure/sources/2026-05-16/manual_sources.json",
                "data/dispatches/american-pressure/candidates/2026-05-22/candidate_sources.json",
                "data/dispatches/american-pressure/sources/2026-05-16/feed_backfill_sources.json",
            ],
        )

        result = _result_map(root)["American Pressure artifact retention"]

        assert not result.ok
        assert "candidate_sources.json" in result.message
        assert "feed_backfill_sources.json" in result.message
    finally:
        _cleanup_contract_root(root)


def test_doctor_passes_when_only_durable_ap_sources_are_tracked(monkeypatch):
    root = _make_contract_root()
    try:
        monkeypatch.setattr(
            doctor,
            "_git_tracked_files",
            lambda _: [
                "data/source_registry/american_pressure_sources.json",
                "data/dispatches/american-pressure/sources/2026-05-16/manual_sources.json",
            ],
        )

        result = _result_map(root)["American Pressure artifact retention"]

        assert result.ok
        assert "not tracked" in result.message
    finally:
        _cleanup_contract_root(root)
