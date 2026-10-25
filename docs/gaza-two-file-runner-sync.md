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

## Immutable two-file release design

The fast-forward controller above is a safe scope gate, but it is not by
itself an activation mechanism for the current `fa92` runner. A normal Git
checkout cannot contain two files from a later protected release while still
claiming to be a clean checkout of its protected branch. Copying the files
would make tracked source dirty; committing them would create a divergent
local branch; and replacing the checkout with a partial tree would break the
operator's `ROOT`/`SRC` import and runtime-path assumptions.

The bounded design for genuine two-file activation is an immutable release
overlay, built and reviewed before any runner change:

1. Build a complete release tree from one pinned protected source commit.
2. Replace exactly the two approved files with the reviewed blobs.
3. Compare every other tree entry byte-for-byte with the pinned base and write
   a manifest containing the base commit, both replacement blobs, every
   changed path, the derived tree/release identifier, and the test receipt.
4. Keep the existing tracked runner checkout untouched. A Gaza-only launcher
   selects the immutable release directory through an explicit, atomically
   replaced pointer. It must pass the original runner root separately to the
   operator so data, Pages checkout, logs, credentials, and receipts do not
   silently move into the release tree.
5. Before launch, verify the pointer target, manifest, exact two file hashes,
   protected source binding, target dispatch, and release directory boundary.
   Refuse missing, mutable, reparse/junction, mismatched, or expired release
   state. Write activation and rollback receipts outside the tracked checkout.
6. Rollback changes only the pointer to a previously verified immutable
   release. It never copies backup files into the Git checkout and never
   changes the protected branch.

This PR implements that source-only interface in three pieces:

- `scripts/build_gaza_two_file_release.py` materializes Git blobs from a
  pinned base commit, substitutes exactly the two reviewed blobs, and proves
  every other tracked entry is unchanged before writing the manifest.
- `scripts/run_gaza_immutable_release.py` verifies the manifest, release tree,
  runtime-root separation, and optional atomic pointer target, then supports a
  check-only receipt or a guarded process launch. It never changes the
  tracked checkout or scheduler.
- `scripts/run_gaza_daily_operator.py` now separates `SOURCE_ROOT` from
  `BLUEFERN_RUNTIME_ROOT`, and rebinds the imported daily/publish modules so
  source code comes from the immutable tree while data, output, logs, Pages,
  credentials, and receipts remain under the original runner root.

The launcher requires an explicit test root and writes its activation receipt
outside both roots. The implementation is source-only and disposable-fixture
tested; it does not create or activate a production release in this PR.

The proposed manifest is intentionally explicit and machine-checkable:

```json
{
  "schema_version": "bluefern.gaza_immutable_release.v1",
  "release_id": "<immutable-release-id>",
  "base_commit": "<installed-source-commit>",
  "replacement_commit": "<reviewed-protected-source-commit>",
  "changed_paths": [
    "scripts/run_gaza_daily_operator.py",
    "src/bluefern_dispatches/bluesky_post.py"
  ],
  "replacement_blobs": {
    "scripts/run_gaza_daily_operator.py": "<40-char-blob-sha>",
    "src/bluefern_dispatches/bluesky_post.py": "<40-char-blob-sha>"
  },
  "tree_verification": {
    "base_tree_entries": "<count>",
    "unchanged_entries_verified": true,
    "release_tree_sha256": "<sha256>"
  },
  "dispatch": "gaza",
  "runtime_root_contract": "BLUEFERN_RUNTIME_ROOT",
  "activation": "unactivated"
}
```

The builder must obtain the base tree and replacement blobs from Git object
IDs, never from a dirty runner checkout; create a new release directory; and
refuse an existing/reparse release path. It must prove that every tracked
entry except the two allowlisted paths has the same Git blob as `base_commit`.
The launcher must validate this manifest and the two file hashes, resolve the
release pointer atomically, set `BLUEFERN_RUNTIME_ROOT` to the original Gaza
runner root, and invoke the release tree's operator with its source/import
root separate from that runtime root. It must refuse a target other than
Gaza, a release outside the approved release root, a mutable/reparse path,
missing manifest evidence, or a runtime-root mismatch. Activation and
rollback receipts must include the release ID, source/base heads, file hashes,
pointer before/after, and process result.

These tools implement the source-side mechanism and check-only proof. They do
not authorize creating a release under `C:\BlueFernRunner`, changing a live
pointer, changing a scheduled task, or running the operator against production.
Those remain separate activation actions.

An isolated full release lineage is the alternative: provision a separate
Gaza checkout pinned to a reviewed full protected commit and switch the Gaza
scheduled launcher to that root. It preserves the original checkout and gives
clean Git rollback, but changes the scheduler/runner-root contract and is
broader than this two-file approval.

Consequently, the current PR's fast-forward path remains intentionally
fail-closed for `fa92`  `a0aad`: it reports
`FULL_SOURCE_ADVANCEMENT_REQUIRED`. It must not be described as an installed
two-file repair. Activation requires a future reviewed overlay/launcher
implementation or separately approved full-source lineage, followed by
runner-local preflight and deployment proof.

## Validation and CI evidence

The focused suite contains source checks, immutable builder/manifest/launcher
tests, and real temporary-Git tests for forward apply, backup byte integrity,
backup failure, descendant rollback, and non-descendant rollback refusal. The
test suite must run with an explicit temporary root; no production checkout is
used.

The PR head currently has no Actions run or commit status, while a prior head
of the same workflow has a completed successful run and `validate` status. The
workflow declares `opened`, `synchronize`, `reopened`, and
`ready_for_review` pull-request triggers, so the absence of a run is recorded
as an unresolved Actions/connector observation-not treated as a pass and not
resolved by dispatching a run or changing repository settings.

