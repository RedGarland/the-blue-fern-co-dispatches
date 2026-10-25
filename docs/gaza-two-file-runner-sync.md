# Gaza two-file runner synchronization

`scripts/sync_gaza_two_file_runner.ps1` is the narrow deployment controller for
the guarded Gaza runtime pair:

- `scripts/run_gaza_daily_operator.py`
- `src/bluefern_dispatches/bluesky_post.py`

It is intentionally separate from `sync_active_production_runners.ps1`, which
enumerates the active production-runner fleet. The Gaza controller fetches only
the Gaza checkout's protected branch, requires a full expected protected SHA,
and refuses a revision whose `HEAD..expected` path set contains anything beyond
the two allowlisted files.

The controller preserves the existing protections:

- exact branch and protected-head checks;
- ancestor and incoming-path checks;
- dirty tracked-path and untracked collision checks;
- an immediate pre-apply recheck of branch, HEAD, status, and remote head;
- fast-forward only, with no reset, clean, checkout, push, collection, or
  publication operation.

With `-Apply`, an explicit backup directory is required. The controller writes
exact file hashes and a manifest before the fast-forward. The backup is
forensic and rollback evidence; it is not copied back automatically. Rollback
must use a separately reviewed protected source head through the same guarded
controller.

Example plan invocation from the approved source checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\sync_gaza_two_file_runner.ps1 `
  -ExpectedProtectedHead <full-protected-sha> `
  -ReportPath <report-path>
```

Apply is permitted only after the plan proves the exact two-file incoming
scope, the target is on the expected branch, and all dirty-state checks pass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\sync_gaza_two_file_runner.ps1 `
  -ExpectedProtectedHead <full-protected-sha> `
  -BackupRoot <separate-backup-directory> `
  -ReportPath <report-path> `
  -Apply
```

The controller does not run a dispatch or post to Bluesky. Those remain
separate, explicitly gated operations.

