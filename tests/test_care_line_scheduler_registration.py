from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "register_care_line_reviewed_event_queue_task.ps1"
DOC = ROOT / "docs" / "care-line-reviewed-event-queue-scheduler.md"


def test_care_line_scheduler_registration_targets_scheduler_root() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    assert '$TaskPath = "\\"' in helper
    assert 'Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName' in helper
    assert 'Set-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName' in helper
    assert 'Register-ScheduledTask `\n        -TaskPath $TaskPath' in helper
    assert 'Get-ScheduledTask -TaskName $TaskName -ErrorAction' in helper


def test_care_line_scheduler_documentation_names_full_root_task_path() -> None:
    documentation = DOC.read_text(encoding="utf-8")
    assert "`\\Blue Fern Care Line Reviewed Event Queue`" in documentation
    assert '`-TaskPath "\\" -TaskName "Blue Fern Care Line Reviewed Event Queue"`' in documentation
