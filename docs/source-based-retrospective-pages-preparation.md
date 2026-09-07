# Source-based retrospective Pages preparation

This owner implements lifecycle Step 8A for source-based retrospective Food
Line and Care Line artifacts:

1. source evidence
2. retrospective replay
3. editorial triage
4. retrospective approval
5. release authorization
6. publication authorization
7. local public generation
8. local Pages preparation
9. explicit Pages push/live deployment

Step 8A consumes only validated Step-7 generation receipts. It does not
reconstruct public output, publish live, push `gh-pages`, generate social/audio,
or modify scheduled tasks.

## Owner

- Module: `src/bluefern_dispatches/source_based_retrospective_pages_preparation.py`
- CLI: `scripts/prepare_source_based_retrospective_pages.py`
- Receipt schema: `bluefern.source_based_retrospective_pages_preparation.v1`

## Input contract

Required inputs:

- `--dispatch food-line|care-line`
- `--generation-receipt`
- `--publication-batch-id`
- `--pages-repo`
- explicit `--commit` when a local Pages commit should be created

No push option exists in this owner.

The owner derives the exact generated public files from the receipt. It does not
accept arbitrary file lists.

## Scope

The allowed source files are exactly:

```text
output/site/<dispatch>/source-based-retrospectives/<publication-batch-id>/index.html
output/site/<dispatch>/source-based-retrospectives/<publication-batch-id>/items.json
```

The allowed Pages destinations are exactly:

```text
<dispatch>/source-based-retrospectives/<publication-batch-id>/index.html
<dispatch>/source-based-retrospectives/<publication-batch-id>/items.json
```

The workflow refuses Gaza, Cascadia, American Pressure, unrelated Food/Care
editions, review artifacts, generation receipts, raw source data, `output/detail`,
`output/paid`, audio, social, map, or other generated files.

## Validation model

Before any Pages mutation, the owner verifies:

- generation receipt schema and mode;
- dispatch and publication batch ID;
- publication-authorization lineage fields present in the receipt;
- authorized count equals rendered count;
- skipped and unauthorized counts are zero;
- traceable source URL count equals rendered count;
- deployment authority has not escalated;
- generated public paths are under the owned retrospective root;
- generated artifact hashes match the receipt;
- narrow publish-scope validation for `source-based-retrospective`;
- Pages repo exists and is a Git repository;
- Pages repo is on `gh-pages`;
- Pages origin is the expected repository;
- no unresolved merge/rebase/cherry-pick;
- Pages repo is clean before preparation;
- CNAME exists and remains `dispatches.thebluefernco.com`.

After copy, the owner hashes every Pages destination file and requires hashes to
match the generated source artifacts. It then verifies the Pages diff contains
only the exact authorized retrospective destination files and that CNAME did not
change.

## Local commit behavior

`--commit` creates one local Pages commit when the destination files introduce a
real Pages diff:

```text
Publish <dispatch> source-based retrospective <publication-batch-id>
```

If destination bytes are already identical, `--commit` records
`prepared_already_current` and does not create an empty commit.

## Preparation receipt

The owner writes a non-public source-side receipt to:

```text
data/dispatches/<dispatch>/review/source-based-retrospective-pages-preparations/<publication-batch-id>.json
```

The receipt records:

- dispatch;
- publication batch ID;
- generation receipt path and SHA-256;
- generated artifact hashes;
- Pages HEAD before preparation;
- Pages commit SHA after preparation;
- exact destination paths and hashes;
- CNAME hash before and after;
- `pages_push_performed: false`;
- `deployment_status`.

## August 2026 workflow after merge

First regenerate or preserve the validated Step-7 artifacts for Food and Care.
Then prepare local Pages commits separately:

```powershell
python scripts/prepare_source_based_retrospective_pages.py `
  --repo-root . `
  --dispatch food-line `
  --generation-receipt data/dispatches/food-line/review/source-based-retrospective-generations/food-line-august-2026-source-based-publication.json `
  --publication-batch-id food-line-august-2026-source-based-publication `
  --pages-repo .\bluefern-dispatches-pages `
  --commit

python scripts/prepare_source_based_retrospective_pages.py `
  --repo-root . `
  --dispatch care-line `
  --generation-receipt data/dispatches/care-line/review/source-based-retrospective-generations/care-line-august-2026-source-based-publication.json `
  --publication-batch-id care-line-august-2026-source-based-publication `
  --pages-repo .\bluefern-dispatches-pages `
  --commit
```

Do not run these commands in this implementation PR.

## Step 8B push boundary

Live deployment remains a separate explicit operator action after local Pages
inspection:

```powershell
git -C .\bluefern-dispatches-pages push origin gh-pages
```

Never run that command from the source repository and never treat Step 8A as live
publication.
