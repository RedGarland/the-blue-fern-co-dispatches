# Guarded production synchronization

Use the guarded synchronization helper when protected source has advanced beyond the Windows production checkouts.

## Scope

The helper covers:

- `C:\BlueFernRunner\BlueFernOperatorCurrent`
- `C:\BlueFernRunner\FoodLineCurrent6`
- `C:\BlueFernRunner\CareLineNationalCurrent8`
- `C:\BlueFernRunner\GazaDispatchesCurrent6`
- `C:\BlueFernRunner\ICEMonitorCurrent`

It does not trigger collection, publication, Pages, social posting, or scheduled tasks.

## Plan-only audit

From any current protected source checkout:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\sync_active_production_runners.ps1
```

Expected outcomes:

- exit `0`: every target is already current;
- exit `10`: one or more targets are safe and ready to fast-forward;
- exit `2`: at least one target is blocked or failed validation;
- exit `3`: targets fetched inconsistent protected heads.

The command writes a JSON report path to stdout.

## Guarded apply plus immediate non-public proof

After reviewing the plan:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\sync_active_production_runners.ps1 -Apply -ProveStatusExport
```

The helper:

1. fetches and freezes one common protected SHA;
2. validates every checkout before any production HEAD moves;
3. fast-forwards only clean, ancestor-compatible checkouts;
4. reruns preflight and doctor after each fast-forward;
5. runs the serialized operational-status exporter only after all source checkouts are synchronized;
6. requires Food, Care, Gaza, and ICE `current_runner_head` to equal the frozen SHA;
7. requires each runner branch to be `add/pages-repo-default`;
8. requires Gaza to be `MIGRATED` on the non-public status surface.

The proof mutates only the operational-status branch through the existing exporter.

Additional exit:

- exit `4`: runner synchronization completed but the non-public status proof failed.

## Fail-closed behavior

The helper refuses rollout when it finds:

- branch mismatch;
- tracked working-tree changes;
- divergent history;
- untracked/incoming path collisions;
- missing validation capability;
- preflight failure;
- doctor failure;
- inconsistent protected target SHAs.

It does not use reset, clean, stash, rebase, checkout, restore, force push, or destructive cleanup.

## Proof artifact

The JSON report includes:

- frozen target SHA;
- before/after HEAD for each checkout;
- pre/post validation;
- blocked paths and collision evidence;
- status-exporter task metadata;
- status proof result;
- whether any public or scheduler side effect occurred.

Keep this report with the stabilization incident when a rollout changes production state.
