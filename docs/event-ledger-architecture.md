# Dispatches Event Ledger Architecture

Phase 1 introduces a central event-centric ledger in shadow mode for Food Line only.

## Purpose

The ledger records underlying Food Line access-pressure events separately from article URLs. Its job is to help classify whether a discovery is a genuinely new development, a meaningful update to a known event, a duplicate/recycled source, or a historical recovery/backfill.

This is observational infrastructure. It does not approve, reject, rank, queue, publish, suppress, or migrate any existing Food Line item.

## Event-centric identity

Production discovery still preserves source URLs, but ledger identity is based on stable event components:

- dispatch
- location
- organization or facility
- pressure/event type
- effective date when known
- normalized subject as fallback

For example:

`food-line-rantoul-price-cutter-grocery-closure-20260902`

Article URLs are stored as source observations. They do not define the event ID.

## Live vs. historical recovery

The ledger distinguishes live new developments from historical recoveries. If a qualifying event predates the live monitoring window and was previously missed, it can be classified as `historical_recovery`. Historical recovery must not be represented as a fresh live development.

Phase 1 does not bulk-import old Food Line findings or rewrite any prior editorial state.

## Shadow-mode guarantees

Food Line discovery attempts a ledger write only after normal candidates and audit output have been assembled.

The shadow write:

- uses SQLite at `data/state/dispatches.sqlite`;
- writes through transactions;
- records event, source-observation, event-observation, and agent-run rows;
- records diagnostics under `shadow_event_ledger`;
- never blocks the existing discovery, review, or publication path.

If the shadow ledger fails, the production discovery result remains successful when it otherwise would have succeeded. The failure is recorded as `failed_nonblocking`.

## What Phase 1 does not change

Phase 1 does not change:

- Food Line daily schedule;
- Windows Task Scheduler definitions;
- production runner paths;
- existing production dedupe/fingerprint behavior;
- candidate qualification thresholds;
- review queue state;
- approval or publication authority;
- public pages, RSS, archives, audio, social, or deployment behavior.

The ledger is not a source of publication eligibility.

## Future migration path

The schema includes base event fields and operational tables intended to support future reviews, editions, and publications. Later phases can add explicit review and publication tables after the shadow ledger has proven stable. Those phases must remain separately reviewed because they would change authority boundaries that Phase 1 intentionally avoids.
