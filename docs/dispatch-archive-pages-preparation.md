# Dispatch archive Pages preparation

This owner prepares already-generated Food Line and Care Line `archive.html`
updates in the local Pages repository.

It closes the exact-scope archive deployment gap left after the retrospective
archive integration work. It does not generate archives, publish live, push
`gh-pages`, alter RSS, create social/audio output, or modify scheduled tasks.

## Owner

- Module: `src/bluefern_dispatches/archive_pages_preparation.py`
- CLI: `scripts/prepare_dispatch_archive_pages.py`
- Receipt schema: `bluefern.dispatch_archive_pages_preparation.v1`

## Supported dispatches

Only these dispatches are supported:

- `food-line`
- `care-line`
- `both`

This is not an arbitrary-file copier.

## Exact source and destination contract

For each selected dispatch, the owner derives paths from the dispatch name:

```text
output/site/<dispatch>/archive.html -> <dispatch>/archive.html
```

No other source path or Pages destination is owned.

## Preflight

Before any Pages mutation, the owner verifies:

- the source archive exists and is readable HTML;
- canonical/dispatch identity matches the requested dispatch;
- exact archive publish-scope validation passes with the `dispatch-archive`
  scope family;
- the Pages repo exists and is a Git worktree;
- the Pages repo is on `gh-pages`;
- the Pages origin identifies `RedGarland/the-blue-fern-co-dispatches`;
- no merge/rebase/cherry-pick operation is in progress;
- the Pages worktree is clean;
- local `gh-pages` equals `origin/gh-pages`;
- `CNAME` exists and equals `dispatches.thebluefernco.com`;
- destinations resolve inside the Pages repository.

If local Pages is behind, ahead, or diverged from `origin/gh-pages`, the owner
stops. It does not reset, clean, pull, merge, or reconcile automatically.

## Mutation and hash validation

The only permitted Pages diffs are:

```text
food-line/archive.html
care-line/archive.html
```

The owner hashes each source archive before copy and each Pages destination
after copy. The hashes must match exactly. It also records the CNAME hash before
and after and requires it to remain unchanged.

In combined mode, all source and Pages preflights complete before any copy. If a
post-copy validation fails, the owner restores the affected destination bytes.

## No-op behavior

If source and Pages archive bytes are already identical, the owner reports:

```text
prepared_already_current
```

No empty commit is created.

## Local commit behavior

Without `--commit`, the owner copies the validated archive file or files into
the local Pages repo and leaves the exact Pages diff for inspection.

With `--commit`, it stages only the exact owned archive path or paths, verifies
the staged diff, and creates one local `gh-pages` commit:

```text
Publish Food Line archive update
Publish Care Line archive update
Publish Food and Care archive updates
```

The owner has no push option.

## Preparation receipt

Each run writes a non-public source-side receipt under:

```text
data/dispatches/archive-pages-preparations/
```

The receipt records dispatches, source archive paths and SHA-256 hashes,
destination archive paths and SHA-256 hashes, source HEAD, Pages HEAD before,
Pages commit SHA if committed, CNAME hashes before/after,
`pages_push_performed: false`, and the deployment status.

## August archive deployment workflow after merge

Regenerate the current protected source archives from deployed Pages state:

```powershell
python scripts\update_source_based_retrospective_archive_links.py `
  --repo-root . `
  --pages-root "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" `
  --dispatch both
```

Prepare and locally commit exactly the Food and Care archive pages:

```powershell
python scripts\prepare_dispatch_archive_pages.py `
  --repo-root . `
  --dispatch both `
  --pages-repo "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" `
  --commit
```

Inspect the local Pages commit and changed files. The only changed Pages paths
must be:

```text
food-line/archive.html
care-line/archive.html
```

## Later push boundary

Live deployment remains a separate explicit operator action:

```powershell
git -C "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" push origin gh-pages
```

Never run that push from the source repository.
