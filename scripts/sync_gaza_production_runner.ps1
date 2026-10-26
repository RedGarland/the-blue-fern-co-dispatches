[CmdletBinding()]
param(
    [string]$ExpectedCurrentHead = '',
    [string]$DesiredHead = '',
    [string]$ExpectedRemote = 'https://github.com/RedGarland/the-blue-fern-co-dispatches.git',
    [string]$BackupRoot = 'D:\Dispatches-Archive\GazaInstallBackups\2026-10-26',
    [switch]$Apply,
    [switch]$AllowMutation,
    [string]$RollbackReceipt
)

$ErrorActionPreference = 'Stop'
$target = 'C:\BlueFernRunner\GazaDispatchesCurrent6'
$module = Join-Path $PSScriptRoot 'gaza_sync_core.py'

# This wrapper is intentionally target-specific. It never enumerates or fetches
# the other runner roots. Apply/rollback require both explicit switches.
$arguments = @($module)
if ($RollbackReceipt) {
    if ($Apply) { throw 'Apply and RollbackReceipt cannot be combined.' }
    $arguments += @('--target', $target, '--rollback-receipt', $RollbackReceipt)
} else {
    if ([string]::IsNullOrWhiteSpace($ExpectedCurrentHead) -or [string]::IsNullOrWhiteSpace($DesiredHead)) {
        throw 'ExpectedCurrentHead and DesiredHead are required unless RollbackReceipt is supplied.'
    }
    $arguments += @('--target', $target, '--backup-root', $BackupRoot,
        '--branch', 'add/pages-repo-default',
        '--expected-current-head', $ExpectedCurrentHead,
        '--desired-head', $DesiredHead,
        '--expected-remote', $ExpectedRemote)
    if ($Apply) { $arguments += '--apply' }
}
if ($AllowMutation) { $arguments += '--allow-mutation' }

& python @arguments
exit $LASTEXITCODE
