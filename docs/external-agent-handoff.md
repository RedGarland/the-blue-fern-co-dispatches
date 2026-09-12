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

## Operator-Recovered Source Watch Evidence

When a scheduled production Food Line Source Watch run failed closed before
discovery, a complete operator-preserved Source Watch payload may be ingested
through `scripts/recover_food_line_operator_evidence.py`. This is a narrow
recovery path for source-backed payloads that contain the original agent run ID,
canonical source URL, publisher, supporting evidence, source role, confidence,
review status, and recovery context.

Operator recovery is explicitly labeled
`operator_recovered_source_watch_evidence`. It must not be relabeled as
`original_production_source_watch_artifact`, because the original production
runner artifact is absent. The persisted provenance records
`original_production_artifact_present: false`,
`production_collection_failed_before_discovery: true`, the recovery reason, the
original run ID, the source URL, source verification status, and
`eligible_for_automatic_publication: false`.

This path admits recovered evidence only to human review. It does not publish,
modify Pages, change schedules, create public artifacts, or infer release
authorization. Prose-only summaries and synthetic/test handoffs are rejected for
operator recovery.

After human review, a Food Line item with this provenance may advance only to
the private state `OPERATOR_RECOVERY_RELEASE_PREP_ELIGIBLE`, and only through
the narrow validator owned by
`scripts/prepare_food_line_operator_recovery_release.py`. This state means the
item has passed lineage, source, editorial, dedupe, currentness, geography, and
materiality checks for controlled private release preparation. It is not
publication approval and it is not original production evidence.

The private release-preparation validator fails closed unless all of these are
true:

- `provenance_class` is `operator_recovered_source_watch_evidence`
- `original_production_artifact_present` is `false`
- `production_collection_failed_before_discovery` is `true`
- the original agent run ID is present
- the source URL is HTTPS and traceable
- publisher/source identity is present
- source-backed supporting evidence is present
- recovery reconciliation disposition is `retained_for_review`
- item-level editorial disposition is `APPROVE` or `APPROVE_WITH_EDIT`
- `HOLD` and `REJECT` items are rejected
- source verification is valid
- duplicate state is resolved and nonblocking
- event/currentness date is explicitly bound
- geography is in Food Line scope
- material Food Line relevance is confirmed
- reviewed headline and summary are present
- `eligible_for_automatic_publication` is `false`
- `publication_approval` is `false`
- `publication_performed` is `false`
- the original failed production runtime remains unchanged
- operational-health state is not modified

The validator writes only deterministic private artifacts under
`data/private-agent-handoff/operator-recovery/food-line/<YYYY-MM-DD>/release-prep/`.
It must reject synthetic/test evidence, retired synthetic evidence, prose-only
recovery, missing reconciliation, missing editorial review, missing source
evidence, missing original run ID, unresolved duplicates, fabricated
original-production lineage, `eligible_for_automatic_publication: true`,
`publication_approval: true`, missing event dates, and missing
geography/materiality confirmation.

This governance extension authorizes only human-reviewed operator-recovered
evidence to enter controlled private release preparation. It does not authorize
public generation, archive/RSS/homepage changes, Pages changes, publication
state changes, schedules, social output, audio, or operational-health recovery.

Operator-recovery release authorization is a separate private governance owner
after release preparation. It is managed by
`scripts/authorize_food_line_operator_recovery_release.py`, consumes only
committed protected release-prep artifacts with exact SHA-256 bindings, and
writes a deterministic batch artifact under
`releases/food-line/operator-recovery/<release-id>-release-v1.json` using schema
`bluefern.food_line.operator_recovery_release_authorization.v1`.

The release-authorization owner may set `release_authorized: true` only for the
exact hash-bound operator-recovered items in the request. It must keep
`eligible_for_automatic_publication`, `publication_eligible`,
`publication_approval`, `publication_authorized`,
`public_generation_authorized`, `pages_authorized`, `social_authorized`,
`audio_authorized`, `schedule_authorized`, and `publication_performed` false.
The authorization means only that the reviewed private items may advance to a
separate publication-authorization decision. It is not directly consumable by
public generation and does not modify public output, archive/RSS/homepage,
Pages, scheduler state, or operational-health state.
