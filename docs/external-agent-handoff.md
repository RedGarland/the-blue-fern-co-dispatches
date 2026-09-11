# External Agent Handoff

Food Line and Care Line accept manually supplied, private JSON envelopes through
`scripts/import_external_agent_run.py`. The bridge validates schema version 1,
archives the exact input bytes, and keys idempotency by `agent_run_id` and SHA-256.

Accepted findings remain `pending_review`; the bridge never grants editorial or
publication approval. Raw envelopes and operational receipts stay under
`data/private-agent-handoff/`. Receipts contain symbolic repository-relative
references and no raw payload or absolute local paths.

Every rejected or failed attempt also receives a durable, attempt-specific
failure receipt. Malformed input, unsupported dispatches, missing agent IDs,
archive/write failures, downstream import failures, and same-run hash conflicts
fail closed without overwriting an existing archive or mutating candidate state.
Conflict receipts are classified as `IDEMPOTENCY_CONFLICT` and include the
incoming and existing archive hashes plus the archive reference.

The bridge is intentionally not a ChatGPT listener, scheduler, source fetcher,
publication runner, or Pages writer. A manual payload must be supplied by an
operator. The read-only reconciliation command reports each finding's candidate
ID, terminal disposition, duplicate linkage, reason, and unaccounted status.

The operational handoff states are `NO_EXTERNAL_HANDOFF_EXPECTED`,
`HANDOFF_RECEIVED_SUCCESS`, `HANDOFF_FAILED`, and
`HANDOFF_STALE_UNPROCESSED`. The bridge does not implement an exporter or
change those states automatically.

For sanctioned synthetic proof runs only, use
`scripts/cleanup_external_agent_synthetic_run.py`. It defaults to a dry run and
requires one exact `synthetic-` agent run ID for `--apply`. Apply mode removes
matching active intake/review state, quarantines raw archives and receipts
under `data/private-agent-handoff/retired/`, and writes a symbolic cleanup
tombstone containing hashes and repository-relative references. It never
publishes, contacts the network, changes scheduler state, or deletes audit
evidence.

The separate Windows Source Watch jobs remain the production collection path.
ChatGPT-assisted Source Watch is a distinct manual producer and is not evidence
of a Windows scheduler run. Missing prior payloads remain unavailable rather than
being reconstructed by this bridge.
