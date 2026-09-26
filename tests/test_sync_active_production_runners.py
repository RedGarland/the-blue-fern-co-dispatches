from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_active_production_runners.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_sync_helper_targets_all_active_dispatch_runners() -> None:
    text = _text()
    for path in (
        r"C:\BlueFernRunner\FoodLineCurrent6",
        r"C:\BlueFernRunner\CareLineNationalCurrent8",
        r"C:\BlueFernRunner\GazaDispatchesCurrent6",
        r"C:\BlueFernRunner\ICEMonitorCurrent",
    ):
        assert path in text
    assert 'TargetBranch = "add/pages-repo-default"' in text


def test_sync_helper_is_plan_only_by_default_and_requires_apply_for_merge() -> None:
    text = _text()
    assert "[switch]$Apply" in text
    assert 'if (-not $Apply)' in text
    assert '"READY_TO_FAST_FORWARD"' in text
    assert '@("merge", "--ff-only", $targetRef)' in text


def test_sync_helper_has_fail_closed_checkout_guards() -> None:
    text = _text()
    for fragment in (
        'branch mismatch:',
        'tracked working-tree changes are present',
        'current HEAD is not an ancestor',
        'untracked runtime paths overlap incoming tracked paths',
        'target head mismatch:',
        'Production runners fetched inconsistent protected heads.',
    ):
        assert fragment in text


def test_sync_helper_preserves_runtime_state_and_never_uses_destructive_git() -> None:
    text = _text().lower()
    for forbidden in (
        '"reset"',
        '"clean"',
        '"stash"',
        '"rebase"',
        '"checkout"',
        '"restore"',
        '"push"',
        '"commit"',
    ):
        assert forbidden not in text
    assert '"merge", "--ff-only"' in text


def test_sync_helper_validates_before_and_after_rollout() -> None:
    text = _text()
    assert 'scripts\\preflight_repo_state.py' in text
    assert 'scripts\\doctor.py' in text
    assert '$pre = Invoke-RunnerValidation -Root $root' in text
    assert '$post = Invoke-RunnerValidation -Root $root' in text
    assert '"FAST_FORWARDED_VALIDATION_FAILED"' in text


def test_sync_helper_does_not_trigger_production_or_scheduler_mutation() -> None:
    text = _text()
    assert 'StatusExporterTask = Get-StatusExporterTaskEvidence' in text
    assert 'PublicSideEffects = $false' in text
    assert 'SchedulerMutation = $false' in text
    assert 'CollectionTriggered = $false' in text
    assert 'PublicationTriggered = $false' in text
    assert 'PagesMutation = $false' in text
    assert "Register-ScheduledTask" not in text
    assert "Set-ScheduledTask" not in text
    assert "Start-ScheduledTask" not in text
