# Source-based retrospective public generation

This owner implements lifecycle step 7 for source-based retrospective Food Line
and Care Line records:

1. source evidence
2. retrospective replay
3. editorial triage
4. retrospective approval
5. release authorization
6. publication authorization
7. local public generation
8. Pages sync/deployment

Step 7 consumes only durable publication-authorization records under:

- `publication-authorizations/food-line/source-based-retrospectives/`
- `publication-authorizations/care-line/source-based-retrospectives/`

It does not consume raw `output/review` files, approval-prep files, approval
records, release records without publication authorization, or arbitrary source
lists.

## Owner and CLI

- Owner module: `src/bluefern_dispatches/source_based_retrospective_public_generation.py`
- CLI: `scripts/generate_source_based_retrospective_public_artifacts.py`
- Receipt schema: `bluefern.source_based_retrospective_public_generation_manifest.v1`

Generate local artifacts:

```powershell
python scripts/generate_source_based_retrospective_public_artifacts.py generate `
  --repo-root . `
  --dispatch food-line `
  --publication-path publication-authorizations/food-line/source-based-retrospectives/<batch-id>-publication-v1.json `
  --expected-sha256 sha256:<publication-authorization-sha256>
```

Validate a generation receipt:

```powershell
python scripts/generate_source_based_retrospective_public_artifacts.py validate `
  --repo-root . `
  --manifest-path data/dispatches/food-line/review/source-based-retrospective-generations/<batch-id>.json
```

## Owned generated roots

The owner writes only:

- `output/site/<dispatch>/source-based-retrospectives/<publication-batch-id>/index.html`
- `output/site/<dispatch>/source-based-retrospectives/<publication-batch-id>/items.json`
- `data/dispatches/<dispatch>/review/source-based-retrospective-generations/<publication-batch-id>.json`

It does not update dispatch landing pages, archive pages, RSS feeds, audio,
social, queues, scheduled tasks, or the Pages checkout.

The generated public path is:

- `/<dispatch>/source-based-retrospectives/<publication-batch-id>/`

This is a parallel local public-generation surface for source-based
retrospectives. It does not replace existing Food migrated-event retrospective
rendering or Care current-publication rendering.

## Chronology rendering

The renderer preserves the authorization-bound fields:

- source publication date
- event/effective date or range
- chronology classification
- authorized public placement/date
- public wording constraints
- publisher
- original source URL

Reader-facing HTML uses public labels instead of internal chronology enum names.
Cross-month ranges are not flattened.

Specific protected framing:

- Future-effective August announcements are rendered as later effective/bound
  events, not completed August closures.
- Restorations are rendered as restorations or reopenings, not closures.
- Continuing prior losses are rendered as continuing access losses, not newly
  effective August closures.

## Generation manifest

The receipt records:

- dispatch
- source publication-authorization path and SHA-256
- generated item IDs
- generated public paths
- authorized/rendered/skipped/unauthorized counts
- chronology bindings
- source URLs and publishers
- artifact hashes

The owner fails closed unless authorized count equals rendered count, skipped
count is zero, and unauthorized item count is zero.

## Pages boundary

This workflow does not deploy. A later explicit Pages-preparation workflow must:

1. validate generated artifacts against the generation receipt;
2. validate publish scope for the exact generated public paths;
3. sync only authorized generated files into `bluefern-dispatches-pages`;
4. create a local Pages commit when explicitly requested;
5. push only after a separate explicit instruction.

## August 2026 generation after merge

After this owner is merged, generate the already-authorized August artifacts
locally with the exact committed publication-authorization hashes:

```powershell
python scripts/generate_source_based_retrospective_public_artifacts.py generate `
  --repo-root . `
  --dispatch food-line `
  --publication-path publication-authorizations/food-line/source-based-retrospectives/food-line-august-2026-source-based-publication-publication-v1.json `
  --expected-sha256 sha256:<food-publication-authorization-sha256>

python scripts/generate_source_based_retrospective_public_artifacts.py generate `
  --repo-root . `
  --dispatch care-line `
  --publication-path publication-authorizations/care-line/source-based-retrospectives/care-line-august-2026-source-based-publication-publication-v1.json `
  --expected-sha256 sha256:<care-publication-authorization-sha256>
```

Do not run Pages sync, push, social, audio, or scheduled-task changes as part of
this generation step.
