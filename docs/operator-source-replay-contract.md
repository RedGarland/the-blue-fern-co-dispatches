# Operator Source Replay Contract

This contract enables bounded source-level retry for transient source fetch failures without rerunning a full dispatch collection or touching public publication paths.

## Enabled Dispatch

Care Line is the only enabled dispatch. The canonical entry point is:

```powershell
python scripts/source_replay.py --dispatch care-line --source-id <source_id> --logical-date <YYYY-MM-DD> --parent-run-id <run_id> --retry-id <retry_id> --repo-root <runner_root>
```

The wrapper accepts source identity only. It does not accept arbitrary URLs, source substitutions, scheduler changes, publication flags, or collection-wide replay requests.

## Care Line Behavior

The replay wrapper:

- validates the source ID against `data/dispatches/care-line/source_registry.json`;
- validates the parent collection manifest and original per-source failed attempt;
- allows only transient source failures already captured by the parent run;
- writes replay artifacts under `data/dispatches/care-line/collection-runs/<date>/<retry-run-id>/`;
- writes append-only reconciliation receipts under `data/dispatches/care-line/source-replays/<date>/<parent-run-id>/<source-id>/<retry-id>.json`;
- preserves the original parent failure artifacts;
- updates `effective-source-state.json` for the parent run;
- merges recovered candidates without marking unrelated existing candidates stale;
- rebuilds private review queue/backlog/duplicates when recovered candidates exist;
- always reports `publication_attempted=false` and `public_side_effects=false`.

Operational status consumes successful replay receipts as effective source-state evidence for the latest Care collection receipt. The original operational-health receipt remains immutable; status derives the effective failed-source count from replay receipts.

## Disabled Dispatches

Food Line remains disabled for source-level replay because its current durable recovery path is task/run-state based rather than registry-bound one-source replay.

ICE remains disabled because the monitor does not expose a source-isolated replay handler with per-source reconciliation.

Gaza remains explicitly excluded because source collection is coupled to editorial/publication workflows.
