# Production Certification And Same-Day Recovery

## Deployment State Model

Use precise state labels during production rollout:

1. `CODE_VALIDATED`
2. `DEPLOYED`
3. `PRODUCTION_PROOF_PASSED`
4. `AWAITING_NATURAL_CERTIFICATION`
5. `NATURAL_RUNTIME_CERTIFIED`

Isolated certification is immediate engineering proof after a source change or
runner sync. Natural scheduled execution remains the final production
certification because it proves the operating-system scheduler boundary.

## Certification Harness

Run source-level, non-public certification with an explicit isolated proof root:

```powershell
python scripts\run_production_certification.py `
  --dispatch care-line `
  --source-root <source-or-runner-copy> `
  --proof-root <isolated-proof-root>
```

The harness emits `bluefern_production_certification_v1`. It checks source
state, preflight, imports/compile, configuration, receipt compatibility,
dependency evaluation, and isolated operational-status simulation. It does not
publish, write Pages, update RSS, send social posts, generate public audio,
consume editorial approval, modify production queues, commit, push, or alter
scheduled tasks.

Level 2 isolated runtime proof is reported as `UNSUPPORTED_ISOLATED_EXECUTION`
unless every write can be redirected into the proof root. Do not weaken
production guards to make certification pass.

## Same-Day Recovery Evaluator

Run the read-only evaluator:

```powershell
python scripts\evaluate_scheduled_recovery.py `
  --dispatch ice `
  --source-root <runner-or-fixture-root> `
  --date 2026-09-16 `
  --evaluated-at 2026-09-17T04:30:00Z
```

The evaluator recommends only. It never executes production tasks or installs
retry jobs.

Implemented recommendation states:

- `NO_ACTION`
- `WAITING_FOR_SCHEDULE`
- `WAITING_FOR_GRACE`
- `RETRY_ELIGIBLE`
- `UPSTREAM_BLOCKED`
- `MANUAL_ATTENTION`
- `MISSED`
- `RECOVERY_WINDOW_EXPIRED`

Publication tasks default to `MANUAL_ATTENTION`; publication retries require a
separate operator-controlled idempotency and authorization model.

## ICE Operational-Status Migration

The serialized external status exporter remains the single exporter.

When `--ice-source-root` / `-IceSourceRoot` is omitted, ICE remains:

```text
migration_status = NOT_MIGRATED
aggregate_status = UNKNOWN
```

When an ICE source root is explicitly supplied, the exporter consumes local
`ice_monitor` operational-health receipts and writes:

```text
ops/status/ice/latest.json
ops/status/ice/history/YYYY-MM-DD.json
```

System latest then marks ICE as `MIGRATED` and uses the evaluated ICE status.
The exporter sanitizes task summaries and does not export raw provider payloads,
article text, review queue content, credentials, or local absolute paths.

The dedicated ops branch remains the runtime-health authority. The source
branch must not be treated as live operational status.
