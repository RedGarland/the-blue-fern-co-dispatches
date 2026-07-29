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
