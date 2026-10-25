from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "sync_gaza_two_file_runner.ps1"
).read_text(encoding="utf-8")


def test_requires_pinned_protected_head_and_gaza_default():
    assert '[Parameter(Mandatory = $true)][string]$ExpectedProtectedHead' in SCRIPT
    assert 'C:\\BlueFernRunner\\GazaDispatchesCurrent6' in SCRIPT
    assert 'TargetBranch = "add/pages-repo-default"' in SCRIPT


def test_allowlist_is_exactly_two_runtime_paths():
    assert '"scripts/run_gaza_daily_operator.py"' in SCRIPT
    assert '"src/bluefern_dispatches/bluesky_post.py"' in SCRIPT
    assert 'FoodLineCurrent6' not in SCRIPT
    assert 'CareLineNationalCurrent8' not in SCRIPT
    assert 'ICEMonitorCurrent' not in SCRIPT


def test_expected_blobs_are_bound():
    assert '9ab8b65401805aec00ded19ed1939bfe949f9ac3' in SCRIPT
    assert 'aa01f332e2c017a8d003a6bd1a985a1273ef5626' in SCRIPT
    assert 'Expected blob mismatch' in SCRIPT


def test_incoming_revision_must_stay_within_allowlist():
    assert 'Protected revision is broader than the approved two-file scope' in SCRIPT
    assert '$_ -notin $AllowedPaths' in SCRIPT
    assert '"diff", "--name-only", "HEAD..$ExpectedProtectedHead"' in SCRIPT


def test_dirty_and_branch_guards_are_fail_closed():
    assert 'Target branch mismatch' in SCRIPT
    assert 'Approved source paths are already dirty' in SCRIPT
    assert 'Target HEAD is not an ancestor' in SCRIPT
    assert 'Target dirty-state paths changed before apply' in SCRIPT


def test_backup_manifest_and_reviewed_rollback_are_required():
    assert 'BackupRoot is required with -Apply' in SCRIPT
    assert 'backup-manifest.json' in SCRIPT
    assert 'Use a reviewed protected rollback head' in SCRIPT
    assert '"merge", "--ff-only", $ExpectedProtectedHead' in SCRIPT


def test_no_destructive_or_public_side_effect_commands():
    for forbidden in ('git", "reset', 'git", "clean', 'git", "checkout', 'git", "push'):
        assert forbidden not in SCRIPT
    assert 'production_state_mutated = $true' in SCRIPT
    assert 'public_side_effects = $false' in SCRIPT
    assert 'publication_triggered = $false' in SCRIPT
    assert 'collection_triggered = $false' in SCRIPT

