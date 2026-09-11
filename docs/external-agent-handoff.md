# External Agent Handoff

Food Line and Care Line accept manually supplied, private JSON envelopes through
`scripts/import_external_agent_run.py`. The bridge validates schema version 1,
archives the exact input bytes, and keys idempotency by `agent_run_id` and SHA-256.

Accepted findings remain `pending_review`; the bridge never grants editorial or
publication approval. Raw envelopes and operational receipts stay under
`data/private-agent-handoff/`. Receipts contain symbolic repository-relative
references and no raw payload or absolute local paths.

The bridge is intentionally not a ChatGPT listener, scheduler, source fetcher,
publication runner, or Pages writer. A manual payload must be supplied by an
operator. The read-only reconciliation command reports each finding's candidate
ID, terminal disposition, duplicate linkage, reason, and unaccounted status.

The separate Windows Source Watch jobs remain the production collection path.
ChatGPT-assisted Source Watch is a distinct manual producer and is not evidence
of a Windows scheduler run. Missing prior payloads remain unavailable rather than
being reconstructed by this bridge.
