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
- sync from a dirty source repo
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

