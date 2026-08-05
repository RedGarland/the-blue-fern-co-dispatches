# Care Line editorial provenance

Care Line Phase E adds a private editorial-provenance layer between the national review queue and any later public release work.

The workflow is intentionally fail-closed:

- automated qualification does not create an approval;
- human review writes a dated immutable snapshot under `data/dispatches/care-line/review/signal-reviews/`;
- proposed editions bind to the snapshot path and SHA-256;
- private draft artifacts stay outside `output/site` and outside Pages workflows;
- publication authorization remains false until a later explicit release step.

Core records:

- `data/dispatches/care-line/review/current-review-queue.json`
  - mutable working queue from the national pipeline
- `data/dispatches/care-line/review/signal-reviews/YYYY-MM-DD.json`
  - immutable dated review snapshot for a specific edition decision set
- `data/dispatches/care-line/review/proposed-editions/YYYY-MM-DD.json`
  - private proposal bound to a snapshot hash
- `data/dispatches/care-line/review/release-readiness/YYYY-MM-DD.json`
  - private readiness record for later human approval and release preparation

Phase E private draft generation must:

- include only `APPROVE` and `APPROVE_WITH_CORRECTION` records in public-facing draft claims;
- keep `HOLD_FOR_VERIFICATION`, `EXCLUDE`, `DUPLICATE`, `SUPERSEDED`, and `CONTEXT_ONLY` records out of public claim sections;
- preserve direct HTTPS publisher URLs and exact supporting passages for every draft claim;
- avoid leaking local filesystem paths into draft HTML, claim ledgers, or source tables;
- keep publication authorization false and publication datetime null.

This workflow is separate from the existing Care Line Signal Wire publication path. Signal Wire shadow/publication artifacts remain authoritative for that product and should not be replaced by the private edition workflow.
