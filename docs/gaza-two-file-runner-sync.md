# Gaza two-file runner synchronization

`scripts/sync_gaza_two_file_runner.ps1` is a narrow, fail-closed fast-forward
controller for a reviewed Gaza runtime pair:

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

The protected commit and both expected Git blob SHAs are mandatory inputs. This
allows a separately reviewed forward release or rollback release to bind its
own exact source bytes; the controller never assumes that the current release
blobs are valid for another protected commit.

This is not a mechanism for copying two files out of an arbitrary full-source
release. If `HEAD..ExpectedProtectedHead` contains any other path, the report
is `FULL_SOURCE_ADVANCEMENT_REQUIRED` and the controller stops before backup or
mutation. An older, non-descendant rollback is likewise blocked by the
ancestor/fast-forward guard and requires a separately reviewed full-source
release or a future overlay deployment model. Backups are evidence and are
never copied back automatically.

For the current installed Gaza baseline, `fa92c79dd279a897be787db87727905e7cf9b057`
to protected head `a0aad140a1bee5eab002b0a797d6b5279a1b01ad` is a 29-commit,
23-path divergence. Therefore this controller correctly reports that a
two-file deployment is not currently possible under the existing branch and
dirty-state protections; it does not claim to install that release.

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
  -ExpectedOperatorBlob <operator-blob-sha> `
  -ExpectedBlueskyBlob <bluesky-blob-sha> `
  -ReportPath <report-path>
```

Apply is permitted only after the plan proves the exact two-file incoming
scope, the target is on the expected branch, and all dirty-state checks pass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\sync_gaza_two_file_runner.ps1 `
  -ExpectedProtectedHead <full-protected-sha> `
  -ExpectedOperatorBlob <operator-blob-sha> `
  -ExpectedBlueskyBlob <bluesky-blob-sha> `
  -BackupRoot <separate-backup-directory> `
  -ReportPath <report-path> `
  -Apply
```

The controller does not run a dispatch or post to Bluesky. Those remain
separate, explicitly gated operations.

