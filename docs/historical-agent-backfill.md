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

The importer accepts plain text, Markdown, JSON lists, and the Food Line agent envelope. Dry runs write nothing. No approval, publication, Pages, Bluesky, scheduler, or public output path is used.

Food Line reuses the existing AgentFinding and Food Line pressure adapter and marks findings `pending_review` and `historical_backfill: true`. Care Line preserves event/source identity and does not requeue published event IDs. Gaza records are matched by source text against existing source/edition artifacts without creating stories. ICE records remain private with pending verification and support event category, date, location, agency/facility, injury/fatality, detention/removal/legal/policy, evidence, source, severity, and verification fields when present.

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
