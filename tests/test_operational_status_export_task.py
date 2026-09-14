from pathlib import Path

import pytest

import scripts.run_operational_status_export as task
from scripts.run_operational_status_export import DEFAULT_BRANCH, DEFAULT_SOURCE_ROOT, DEFAULT_STATUS_CHECKOUT, build_parser


ROOT = Path(__file__).resolve().parents[1]


def test_export_wrapper_defaults_to_sanctioned_paths_and_status_branch() -> None:
    args = build_parser().parse_args([])
    assert args.source_root == DEFAULT_SOURCE_ROOT
    assert args.status_checkout == DEFAULT_STATUS_CHECKOUT
    assert args.prepare_branch == DEFAULT_BRANCH
    assert args.care_source_root is None
    assert args.no_push is False


def test_export_wrapper_accepts_optional_care_source_root(tmp_path: Path) -> None:
    args = build_parser().parse_args(["--care-source-root", str(tmp_path / "care")])
    assert args.care_source_root == tmp_path / "care"


def test_python_wrapper_passes_care_source_root_to_export_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    care = tmp_path / "care"
    status = tmp_path / "status"
    for path in (source, care, status):
        path.mkdir()
    (source / "status" / "operational-health" / "food-line" / "2026-09-14").mkdir(parents=True)
    received: dict[str, object] = {}

    monkeypatch.setattr(task, "_git_head", lambda _root: "HEAD")
    monkeypatch.setattr(task, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task, "load_recovery_context", lambda _path: None)

    def fake_export_status(**kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {"paths": []}

    monkeypatch.setattr(task, "export_status", fake_export_status)

    result = task.main(
        [
            "--source-root",
            str(source),
            "--status-checkout",
            str(status),
            "--care-source-root",
            str(care),
            "--date",
            "2026-09-14",
            "--no-push",
        ]
    )

    assert result == 0
    assert received["source_root"] == source
    assert received["status_checkout"] == status
    assert received["care_source_root"] == care
    receipt = next((source / "logs" / "operational-status-exporter").glob("*.json"))
    assert '"care_source_configured": true' in receipt.read_text(encoding="utf-8")


def test_python_wrapper_omits_care_source_root_as_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    status = tmp_path / "status"
    source.mkdir()
    status.mkdir()
    (source / "status" / "operational-health" / "food-line" / "2026-09-14").mkdir(parents=True)
    received: dict[str, object] = {}

    monkeypatch.setattr(task, "_git_head", lambda _root: "HEAD")
    monkeypatch.setattr(task, "prepare_status_checkout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task, "load_recovery_context", lambda _path: None)

    def fake_export_status(**kwargs: object) -> dict[str, object]:
        received.update(kwargs)
        return {"paths": []}

    monkeypatch.setattr(task, "export_status", fake_export_status)

    result = task.main(
        [
            "--source-root",
            str(source),
            "--status-checkout",
            str(status),
            "--date",
            "2026-09-14",
            "--no-push",
        ]
    )

    assert result == 0
    assert received["care_source_root"] is None
    receipt = next((source / "logs" / "operational-status-exporter").glob("*.json"))
    assert '"care_source_configured": false' in receipt.read_text(encoding="utf-8")


def test_registration_uses_one_hour_offset_task_and_safe_overlap_settings() -> None:
    text = (ROOT / "scripts" / "register_operational_status_export_task.ps1").read_text(encoding="utf-8")
    assert "Blue Fern Operational Status Export" in text
    assert "\\Blue Fern Co\\" in text
    assert "-RepetitionInterval (New-TimeSpan -Hours 1)" in text
    assert "-StartWhenAvailable" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "run_operational_status_export.ps1" in text


def test_wrapper_never_names_a_production_execution_stage() -> None:
    text = (ROOT / "scripts" / "run_operational_status_export.ps1").read_text(encoding="utf-8").lower()
    for forbidden in ("source watch", "current intake", "daily publish", "resume"):
        assert forbidden not in text


def test_powershell_wrapper_conditionally_plumbs_care_source_root() -> None:
    text = (ROOT / "scripts" / "run_operational_status_export.ps1").read_text(encoding="utf-8")
    assert "[string]$SourceRoot = 'C:\\BlueFernRunner\\FoodLineCurrent6'" in text
    assert "[string]$StatusCheckout = 'C:\\BlueFernRunner\\OperationalStatusCurrent'" in text
    assert "[string]$CareSourceRoot = ''" in text
    assert "IsNullOrWhiteSpace($CareSourceRoot)" in text
    assert "$arguments += @('--care-source-root', $CareSourceRoot)" in text
