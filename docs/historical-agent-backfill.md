# Historical agent backfill

Historical scheduled-agent exports are preserved privately under `data/agent-history/`:

```text
food-line/{raw,normalized,reports}
care-line/{raw,normalized,reports}
gaza/{raw,normalized,reports}
ice/{raw,normalized,reports}
```

Raw records are content-addressed by SHA-256, retain the original text and original bytes (base64), and are written atomically. Reimporting identical bytes is an idempotent no-op. Historical data is never treated as current merely because it was imported today. `captured_at`, `original_run_at`, source publication/event dates, and `imported_at` remain separate.

## Commands

```powershell
python scripts/import_historical_agent_runs.py validate --domain food-line --input <export>
python scripts/import_historical_agent_runs.py dry-run --domain food-line --input <export>
python scripts/import_historical_agent_runs.py import --domain food-line --input <export>
python scripts/import_historical_agent_runs.py normalize --domain food-line --input <export>
python scripts/import_historical_agent_runs.py inventory
python scripts/import_historical_agent_runs.py report --domain food-line --input <export>
python scripts/import_historical_agent_runs.py batch-validate --domain care-line --input-dir data/agent-history-staging/care-line
python scripts/import_historical_agent_runs.py batch-dry-run --domain care-line --input-dir data/agent-history-staging/care-line
python scripts/import_historical_agent_runs.py batch-import --domain care-line --input-dir data/agent-history-staging/care-line
```

The importer accepts plain text, Markdown, JSON lists, and the Food Line agent envelope. A preserved text or Markdown alert containing exactly one labeled (`json`) or unlabeled fenced JSON object is normalized through that embedded envelope; the complete raw bytes and human-readable text remain private provenance. Multiple or malformed fences fail closed. Dry runs write nothing. No approval, publication, Pages, Bluesky, scheduler, or public output path is used.

Food Line reuses the existing AgentFinding and Food Line pressure adapter and marks findings `pending_review` and `historical_backfill: true`. Care Line preserves event/source identity and does not requeue published event IDs. Gaza records are matched by source text against existing source/edition artifacts without creating stories. ICE records remain private with pending verification and support event category, date, location, agency/facility, injury/fatality, detention/removal/legal/policy, evidence, source, severity, and verification fields when present.

## Care Line controlled historical batch

Copy each first-batch export unchanged to `data/agent-history-staging/care-line/`.

Private Care Line match targets are:

- `data/universal_events/publication-state/care-line-signal-wire.json` — authoritative published event IDs and publication state.
- `data/universal_events/shadow/care-line/phase14f-signal-wire/phase14f-source-to-event-lineage-report.json` — reviewed event, source-item, producer-record, canonical URL, and review-decision lineage.
- `data/universal_events/shadow/care-line/phase14f-signal-wire/phase14f-proposed-universal-events.json` — private event identity and reviewed event fields.
- `data/dispatches/care-line/reviewed/` and `data/dispatches/care-line/evidence-reviews/` — reviewed records and evidence decisions.
- `data/dispatches/care-line/sources/` — private source snapshots and source-record identity.
- `data/universal_events/publication-state/care-line-reviewed-event-queue.json` — queue identity/state when present.
- `data/agent-history/care-line/normalized/` — prior private historical identities.

Care normalization emits `matched_published_event`, `matched_reviewed_event`, `matched_existing_source`, `duplicate_historical`, `new_historical_candidate`, `archived_invalid`, or `needs_manual_review`. Published matches create provenance links only; reviewed/source matches are not duplicated; unmatched valid findings remain pending private review; evidence-insufficient findings are archived invalid; and no outcome is publication-ready. Reported historical queue actions are limited to `none`, `provenance_only`, and `historical_review_candidate`; historical imports never enqueue or publish.

Evidence-insufficient Food Line findings are still preserved in the private historical archive as `archived_invalid` records with `review_status: excluded`, `candidate_created: false`, and `publication_eligible: false`. They never enter current intake, queues, editions, or approval state. A later approved evidence correction creates a separate normalized revision while retaining the original invalid record and raw bytes.

## Gaza controlled historical batch

Copy complete, unchanged Gaza exports to `data/agent-history-staging/gaza/`. Structured normalization sidecars, when required, belong under `data/agent-history-staging/gaza/corrections/`.

Private Gaza matching authority is:

- `data/records/editions.json` and `output/dispatches/gaza/editions/*/edition_manifest.json` for published edition identity and edition dates.
- `data/dispatches/gaza/sources/*/manual_sources.json`, `raw/*/raw_sources.json`, `normalized/*/normalized_sources.json`, `output/dispatches/gaza/editions/*/sources_manifest.json`, and Gaza rows in `data/records/sources.json` for source URLs, source/manual IDs, publishers, source dates, roles, edition dates, and story use.
- `data/records/story_memory.json`, private Gaza dedupe reports, and private curation manifests for exact story IDs, event-cluster IDs, topic fingerprints, normalized event keys, and source-to-cluster provenance.
- `data/dispatches/gaza/editions/*/run_manifest.json` for private publication-run metadata. No separate private Gaza audio index currently exists; rendered audio pages and feeds are protected outputs, not matching authority.
- `data/agent-history/gaza/normalized/` for prior historical identities.

Gaza matching prefers canonical source URL and explicit source/story/cluster identifiers. A title match is accepted only as an exact normalized title + date + publisher composite; headline similarity alone is never sufficient. Conflicting dates, URLs, or identifiers do not get reconciled by fuzzy title matching.

Gaza historical outcomes are `matched_published_edition`, `matched_existing_source`, `matched_existing_cluster`, `duplicate_historical`, `new_historical_candidate`, `archived_context`, `archived_invalid`, and `needs_manual_review`. Published, source, and cluster matches are provenance-only and create no story. Unmatched traceable findings remain private pending-review candidates. West Bank-only or explicit non-Gaza context is archived as context; findings without exact evidence are archived invalid. Every outcome has `publication_eligible: false` and `publication_approval: false`.

Read-only first-batch checks:

```powershell
python scripts/import_historical_agent_runs.py batch-validate --domain gaza --input-dir "data/agent-history-staging/gaza"
python scripts/import_historical_agent_runs.py batch-dry-run --domain gaza --input-dir "data/agent-history-staging/gaza"
```

Do not run `batch-import` until the operator supplies and reviews a real Gaza export. Historical Gaza import writes are restricted to the private raw archive, normalized record, report, and history index. They never rebuild an edition, update source counts, create a story, alter audio or podcast artifacts, change published timestamps, or write Pages.

## Operator export procedure

1. Open the historical alert or task result.
2. Copy/export the complete result without editing.
3. Save it in a private temporary staging directory.
4. Run `validate`.
5. Run `dry-run`.
6. Review matches, duplicates, invalid records, and manual-review outcomes.
7. Run `import`.
8. Preserve the raw archive permanently.

Do not assume repository access to ChatGPT task history, and do not scan arbitrary Downloads or install a watcher. No real historical data is imported by this change.

## Controlled batch workflow

1. Copy each complete, unchanged alert directly into the domain staging folder.
2. Put approved normalization sidecars under the staging folder's `corrections/` directory when needed.
3. Run `batch-validate`.
4. Run `batch-dry-run`.
5. Review the aggregate counts, deterministic file order, sidecar matches, and every per-file outcome.
6. Run `batch-import` explicitly.
7. Rerun the identical `batch-import` command and confirm `idempotent_noop` for every previously imported file.

Batch discovery accepts `.txt`, `.md`, and `.json` files directly inside the supplied directory. Hidden, temporary, unsupported, correction, and archive-output files are ignored. Add `--recursive` only when nested alert directories are intentional.

The batch ID is a deterministic hash of the domain and ordered raw-file hashes. File order uses normalized relative paths. `batch-validate` and `batch-dry-run` write nothing. `batch-import` writes a private report to `data/agent-history/<domain>/reports/batches/<batch-id>.json`.

By default, one validation or sidecar failure blocks every import in the batch. `--allow-partial-import` explicitly permits valid files to import while invalid files remain unchanged and reported. Sidecars are matched by raw SHA-256 and checked against declared raw path, domain, and run or finding identity when present. Hash, path, domain, identity, and approval conflicts fail closed; filenames alone never establish a match, and multiple matches fail closed.

Batch imports use the existing single-file domain protections. They do not enqueue Care Line events, insert Food Line current intake, create Gaza stories, expose ICE records, grant publication approval, generate editions, or write Pages, Bluesky, or scheduler state.
