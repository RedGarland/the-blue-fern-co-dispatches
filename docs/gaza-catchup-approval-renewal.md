# Explicit renewal of an unpublished Gaza catch-up approval

This operation does not publish, merge, relax source gates, or authorize changed content. Its code and every approval artifact require human merge under AGENTS.md.

Ordinary `approve` remains immutable/idempotent and refuses conflicting replay. `renew` is a distinct operation requiring both a fresh ordinary publication request and a private renewal request. It preserves the prior approval's immutable Git commit/blob and records its exact binding in `renewal_provenance` on the successor artifact at the same canonical path.

Immutable scope includes all reader copy, ordered items and their source bindings, title, date, route, approver, and authority flags. Only source/Pages bindings, fresh consent timestamp and request hashes change. Fresh consent must be later than the superseded approval. The successor still must be committed alone and human-merged before the existing protected planning/staging/publication owner accepts it.

The private renewal request schema `gaza_historical_catchup_renewal_request_v1` requires: `prior_approval_binding` (commit/path/blob_sha1/sha256), `request_raw_sha256`, `approved_by`, `approved_at`, `renewal_authorized: true`, `stage_inventory_complete: true`, and a nonempty `stage_roots` list of absolute external directories. The approver and timestamp must match the fresh ordinary request.

The human must explicitly attest that the declared stage roots cover every location used for this catch-up. Do not infer that declaration from permission to prepare this code. Arbitrary external directories cannot be discovered globally: provenance explicitly records `global_stage_absence_verified: false`. Scans fail closed for retained stages/manifests, symlinks and inaccessible directories. Publication/output/state collisions are also rejected. Run renewal exclusively with all prior staging/publishing owners quiescent; this operation does not provide a distributed execution lock.

A successful renewal changes only the canonical working approval file. It is not publication evidence. The old Git artifact remains intact. After independently reviewing and human-merging the successor artifact, run the normal owner plan/stage/verify/publish sequence. Never delete old approval evidence, change the catch-up identity to evade replay rejection, or directly edit a fingerprint to bypass the owner.
