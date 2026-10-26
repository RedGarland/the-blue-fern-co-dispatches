# Gaza-only source synchronizer (private draft)

This draft adds a target-scoped, fail-closed source synchronizer for the Gaza
runner. It is not an installation and has no production defaults that mutate a
checkout.

Files intended for a source-only review PR:

- `scripts/gaza_sync_core.py` — preflight, exact incoming-path collision checks,
  named-source fetch, hash-bound preinstall manifest, guarded apply, and exact
  rollback.
- `scripts/sync_gaza_production_runner.ps1` — PowerShell entry point hard-coded
  to `C:\BlueFernRunner\GazaDispatchesCurrent6`; it never enumerates the other
  desk roots.
- `tests/test_gaza_production_runner_sync.py` - disposable real-Git tests for
  plan-only behavior, dirty-path rejection, ignored-file collision protection,
  apply/backup capacity, rollback, dirty-byte preservation, merge uncertainty,
  post-mutation verification uncertainty, wrapper CLI behavior, and
  branch/object drift. Dirty snapshots bind both worktree bytes and the
  staged index object identity; rollback re-verifies HEAD and dirty/index state
  and records an unknown result if post-reset verification fails.

The implementation requires explicit `--apply --allow-mutation` or
`--allow-mutation` for rollback. Plan-only is the default. It does not set
`DISPATCHES_TEST_ROOT`; that remains a separate production configuration and
Windows-runtime proof gate for PR #686.

Private validation performed in this task: `18 passed` using temporary Git
repositories, with no production paths, credentials, network fetches, public
output, collection, or scheduler changes. PowerShell syntax parsing also
passed. The package remains private and is not a production installation.
