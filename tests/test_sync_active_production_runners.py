from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_active_production_runners.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_sync_helper_targets_all_active_dispatch_runners() -> None:
    text = _text()
    for path in (
        r"C:\BlueFernRunner\BlueFernOperatorCurrent",
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
    assert 'if ($Apply -and $targetHeadConsistent -and $frozenTargetHead)' in text
    assert 'if (-not $Apply -and $ready.Count -gt 0)' in text
    assert '"READY_TO_FAST_FORWARD"' in text
    assert '@("merge", "--ff-only", $frozenTargetHead)' in text


def test_sync_helper_freezes_one_common_target_before_any_merge() -> None:
    text = _text()
    discovery = text.index("# Phase 1: inspect every runner")
    frozen = text.index("$frozenTargetHead")
    apply_phase = text.index("# Phase 2: apply only after all runner discovery")
    merge = text.index('@("merge", "--ff-only", $frozenTargetHead)')
    assert discovery < frozen < apply_phase < merge
    assert "Production runners fetched inconsistent protected heads; no fast-forward was attempted." in text
    assert "runner HEAD changed after discovery" in text
    assert "tracked working-tree changes appeared after discovery" in text
    assert "untracked runtime collision appeared after discovery" in text


def test_sync_helper_has_fail_closed_checkout_guards() -> None:
    text = _text()
    for fragment in (
        'branch mismatch:',
        'tracked working-tree changes are present',
        'current HEAD is not an ancestor',
        'untracked runtime paths overlap incoming tracked paths',
        'target head mismatch:',
        'Production runners fetched inconsistent protected heads; no fast-forward was attempted.',
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


def test_sync_helper_includes_operator_control_plane_checkout() -> None:
    text = _text()
    assert 'Dispatch = "operator"' in text
    assert r'C:\BlueFernRunner\BlueFernOperatorCurrent' in text


def test_sync_helper_can_prove_nonpublic_status_export_after_apply() -> None:
    text = _text()
    assert "[switch]$ProveStatusExport" in text
    assert '"-ProveStatusExport requires -Apply' in text
    assert 'scripts\\run_operational_status_export.ps1' in text
    assert 'ops\\status\\system\\latest.json' in text
    assert 'ops\\status\\gaza\\latest.json' in text
    assert '"MIGRATED"' in text
    assert 'current_runner_head' in text
    assert 'current_runner_branch' in text
    assert 'OperationalStatusMutation = [bool]$statusExportProof.Attempted' in text
    assert 'Production runners were synchronized but the non-public status exporter proof did not pass.' in text


def test_status_export_proof_does_not_trigger_collection_or_publication() -> None:
    text = _text()
    assert 'CollectionTriggered = $false' in text
    assert 'PublicationTriggered = $false' in text
    assert 'PagesMutation = $false' in text


def test_sync_helper_uses_unambiguous_powershell_refspec_interpolation() -> None:
    text = _text()
    assert '$remoteRefSpec = "+refs/heads/${TargetBranch}:refs/remotes/origin/${TargetBranch}"'.replace("\\$", "$") in text
    assert '$remoteRefSpec = "+refs/heads/$TargetBranch:refs/remotes/origin/$TargetBranch"' not in text


def test_sync_helper_temporarily_relaxes_error_action_for_native_git_stderr() -> None:
    text = _text()
    assert '$previousErrorActionPreference = $ErrorActionPreference' in text
    assert '$ErrorActionPreference = "Continue"' in text
    assert '$ErrorActionPreference = $previousErrorActionPreference' in text
    assert '$code = $LASTEXITCODE' in text
    assert 'if (-not $AllowFailure -and $code -ne 0)' in text


def test_sync_helper_wraps_empty_collections_under_strict_mode() -> None:
    text = _text()
    assert '$collisions = @(Get-UntrackedCollisions' in text
    assert 'if (@($collisions).Count -gt 0)' in text
    assert '$lateCollisions = @(Get-UntrackedCollisions' in text
    assert 'if (@($lateCollisions).Count -gt 0)' in text
    assert 'if (@($row.TrackedDirtyPaths).Count -gt 0)' in text
    assert 'if (@($currentStatus.Tracked).Count -gt 0)' in text
