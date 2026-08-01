# Food Line current-signal intake

This is the routine private handoff from source-watch JSON envelopes to the
Food Line current editorial queue and a proposed daily draft. It replaces the
manual sequence of finding inbox files, checking envelope/source evidence,
importing each run, refreshing the review queue, assembling a markdown preview,
and writing a status report.

## Routine unscheduled flow

Step 1 runs the existing discovery expansion source watch and explicitly exports
its qualified structured findings to the private agent inbox:

```powershell
python scripts/run_food_line_discovery_expansion.py `
  --date YYYY-MM-DD `
  --export-agent-inbox `
  --agent-inbox-dir data/dispatches/food-line/agent-inbox
```

The export flag is opt-in. Source watch writes its normal discovery candidates
and audit first, then emits one atomic `food_line_agent_run_v1` envelope from the
in-memory candidate rows. It never reconstructs evidence from rendered HTML.
Only findings with a canonical HTTPS article URL, source publication date,
preserved evidence text from an accepted evidence basis, supported geography,
and a qualified food-pressure classification enter the envelope. Duplicate,
stale, weak, missing-date, and missing-evidence candidates remain in discovery
reporting and appear in the export result as exclusions.

When no finding qualifies, no empty inbox file is written. The durable discovery
audit records `agent_inbox_export.status: no_exportable_findings`, the run ID,
counts, exclusions, and the would-be envelope hash. This avoids an inbox file
that the existing adapter cannot import while preserving coverage accountability.

Step 2 validates and consumes inbox exports:

Run it explicitly from the source repository:

```powershell
python scripts/process_food_line_current_intake.py `
  --edition-date YYYY-MM-DD `
  --inbox data/dispatches/food-line/agent-inbox `
  --build-review-queue `
  --build-proposed-edition
```

The safe wrapper is `scripts/run_food_line_current_intake.py`; it has no
scheduler activation and no publication behavior. A scheduler may invoke that
wrapper after operator review of this workflow, but scheduling is a separate
change and is not enabled here.

The batch discovers JSON envelopes outside `processed/`, rejects malformed
envelopes, invalid HTTPS/evidence/date records, duplicate run IDs, and duplicate
source URLs. Every accepted file is dry-run through the existing importer before
any import mutation. Valid files are privately imported into
`data/dispatches/food-line/agent-intake/YYYY-MM-DD/`; the current queue and
`review/proposed-editions/YYYY-MM-DD.{json,md}` are then refreshed.

The queue remains pending editorial review. Existing decisions and operator
edits are retained when source evidence and proposed wording are unchanged. A
changed fingerprint returns the item to `pending_editorial_review` and marks
`rereview_required`. The proposed draft is capped at six items and never
includes held, rejected, stale, duplicate, historical, or correction records.

The concise private batch report is written at
`data/dispatches/food-line/review/reports/YYYY-MM-DD/current-intake.json`.
Statuses are `success`, `success_with_exclusions`, `partial_failure`, or
`failed`. The report explicitly records that public output, Pages, Bluesky,
audio, maps, and schedules were untouched.

Recommended unscheduled timing is once each morning after source feeds are likely
to have published their daily updates, followed immediately by the intake command.
Do not activate Task Scheduler, cron, or GitHub Actions until an operator reviews
several live export audits and explicitly authorizes scheduling.

Operator decisions remain a separate step:

```powershell
python scripts/manage_food_line_current_review.py decide `
  --review-item-id <ID> --decision approve `
  --decided-by <OPERATOR> --editorial-note "Approved for private draft assembly only."
```

`approve_with_edit`, `hold`, and `reject` are also supported. None grants
publication approval. A future publication receipt should record the proposal
hash, queue hash, edition date, selected review IDs, decision audit identities,
source URLs, publication approval identity/time, and Pages commit/live URL;
this workflow only defines that contract and does not create a ledger.
