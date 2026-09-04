# Pages Release Safety

`scripts/sync_pages_from_source.py` is the guarded Pages sync script for source-to-Pages releases. It copies only approved public Food Line artifacts from `output/site/food-line/` into `bluefern-dispatches-pages`, validates scope, and fails closed before commit or push if anything looks wrong.

## Purpose

- Reduce manual Git handling during release.
- Keep Pages syncs source-backed and branch-checked.
- Prevent private, paid, detail, audio, map, or podcast artifacts from being copied by default.
- Preserve a human approval step before any push.

## Dry Run

```powershell
python scripts\sync_pages_from_source.py --dispatch food-line --dates 2026-06-19 2026-06-20 --require-source-branch add/pages-repo-default --pages-branch gh-pages --dry-run
```

Dry-run behavior:

- validates source and Pages repo branch state
- validates required Food Line source artifacts
- prints the exact Pages paths that would be copied
- does not commit
- does not push
- does not leave the Pages repo dirty

For an approved-proposal Food Line release, use the generated exact-delta manifest:

```powershell
python scripts\validate_publish_scope.py --dispatch food-line --date YYYY-MM-DD --release-manifest "data/dispatches/food-line/review/releases/YYYY-MM-DD.json" --source-repo-root . --pages-repo-root .\bluefern-dispatches-pages --allow-pages --strict

python scripts\sync_pages_from_source.py --dispatch food-line --dates YYYY-MM-DD --require-source-branch BRANCH --pages-branch gh-pages --source-repo . --pages-repo .\bluefern-dispatches-pages --release-manifest "data/dispatches/food-line/review/releases/YYYY-MM-DD.json" --dry-run
```

Release-manifest validation hashes every source and pre-sync Pages file, verifies the exact source-to-Pages mapping and action, requires a clean Pages checkout, and fails if a generated edition file is missing from the manifest. Unrelated source-repository dirt is ignored only because it is absent from the hashed copy plan; it is neither copied nor treated as release input.

Approved migrated-event retrospectives use the same owner with an additional
internal `include_rss` gate. Their generated-output role is accepted only when
the release manifest resolves an exact committed retrospective approval from
an exact V2-approval-only commit, proves that commit was normally merged behind the
current source commit, validates its authority flags and SHA-256 against the
raw Git blob, and matches the clean pre-publish Pages HEAD. This exception does
not change the default daily copy plan: ordinary releases still copy only the
homepage, archive, and selected edition directories.

The current owner accepts only
`approvals/food-line/<batch-id>-approval-v2.json` with schema
`food_line_retrospective_approval_v2`. Unversioned V1 approval files are
immutable historical records and produce a renewal-required error if supplied
to planning or publish-scope validation. A V2 approval does not mutate Pages;
its exact Pages binding is checked later by the separately authorized publish
operation.

## Commit And Push

```powershell
python scripts\sync_pages_from_source.py --dispatch food-line --dates 2026-06-19 2026-06-20 --require-source-branch add/pages-repo-default --pages-branch gh-pages --commit --push
```

Recommended release flow:

1. Merge the source PR first.
2. Run the sync script with `--commit`.
3. Inspect the release report.
4. Push only if the report is clean and the Pages repo is on `gh-pages`.

`--push` requires `--commit`. `--live-check` is also opt-in.

## Live Check

```powershell
python scripts\sync_pages_from_source.py --dispatch food-line --dates 2026-06-19 2026-06-20 --require-source-branch add/pages-repo-default --pages-branch gh-pages --commit --push --live-check
```

Live-check URLs:

- `https://dispatches.thebluefernco.com/food-line/editions/YYYY-MM-DD/`
- `https://dispatches.thebluefernco.com/food-line/editions/YYYY-MM-DD/sources_manifest.json`
- `https://dispatches.thebluefernco.com/food-line/editions/YYYY-MM-DD/curation_manifest.json`

You can add `--cache-bust TOKEN` to force fresh URL checks.

## Allowed Food Line Paths

The script only copies these Food Line paths:

- `output/site/food-line/index.html`
- `output/site/food-line/archive.html`
- `output/site/food-line/editions/YYYY-MM-DD/`

The retrospective publication owner may additionally include
`output/site/food-line/rss.xml` when its committed approval and exact release
manifest validate. RSS remains outside the default daily copy plan.

When two or more committed retrospective approvals bind the same clean Pages
HEAD, the batch owner supplies one exact manifest per date and the guarded sync
copies all selected edition directories and the final shared roots in one
Pages commit. Publishing those approvals sequentially is intentionally
rejected after the first commit advances Pages.

Before either dry-run or publication, retrospective release validation compares
the candidate archive and RSS with that bound Pages HEAD. Every existing archive
edition identity and canonical RSS item must remain present, and an approved
edition destination must be vacant. The preparation owner performs the same
check, so incomplete source-generated history cannot silently shrink public
history even when the release manifest otherwise matches.

That means it refuses to copy:

- `output/site/assets/`
- `output/site/food-line/audio/`
- `output/site/food-line/map/`
- `output/site/food-line/podcast.xml`
- any `output/detail/` or `output/paid/` content
- Gaza or Cascadia paths
- the site root `index.html`

## What It Refuses To Do

- run from a wrong source branch
- sync from a dirty source repo unless an exact, hash-validated release manifest excludes the unrelated dirt
- sync into a dirty Pages repo
- sync from the wrong Pages branch
- copy unexpected Pages paths
- commit or push without explicit flags
- push without a commit
- live-check without an explicit request

## Recovery If It Fails

If the script stops before commit, inspect the report and run:

```powershell
git status
```

Then either fix the source or Pages repo issue and rerun, or discard the partial Pages changes manually if you want to reset the Pages checkout.

## Why This Helps

The mechanical parts of a Pages release are now inside one guarded script instead of scattered across copy, stage, commit, push, and URL-check steps. That reduces branch mistakes, accidental scope creep, and accidental publication of the wrong artifact family.

