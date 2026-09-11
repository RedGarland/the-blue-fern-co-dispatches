from pathlib import Path

from scripts.run_operational_status_export import DEFAULT_BRANCH, DEFAULT_SOURCE_ROOT, DEFAULT_STATUS_CHECKOUT, build_parser


ROOT = Path(__file__).resolve().parents[1]


def test_export_wrapper_defaults_to_sanctioned_paths_and_status_branch() -> None:
    args = build_parser().parse_args([])
    assert args.source_root == DEFAULT_SOURCE_ROOT
    assert args.status_checkout == DEFAULT_STATUS_CHECKOUT
    assert args.prepare_branch == DEFAULT_BRANCH
    assert args.no_push is False


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
