# Food Line historical event recovery

This private workflow handles aggregate Food Line agent handoffs that contain
multiple fenced agent-run envelopes. It does not publish, create a current
intake item, enqueue a story, approve a release, or write Pages.

The ordinary historical importer remains the owner for a single export. Use
this recovery workflow only when one preserved input contains multiple runs
that must be reconciled and consolidated into event clusters.

## Safety model

- The complete original bytes and decoded text are stored in a
  content-addressed private archive.
- Every valid JSON fence is parsed. Malformed fences and invalid envelopes are
  reported by fence number, line, hash, and error while remaining present in
  the complete raw archive.
- An optional `--run-month YYYY-MM` scope retains only findings from runs in
  that month. Out-of-scope occurrences remain in the raw archive and are
  enumerated in validation diagnostics.
- Tracking parameters are removed with the existing Food Line canonical URL
  helper. Distinct paths and meaningful query parameters remain distinct.
- Exact repeated finding records are consolidated without losing their
  occurrence identities.
- A reviewed cluster specification must assign every retained finding exactly
  once. Matching stable event identities must be consolidated.
- Event identity uses location, organization, event period, pressure category,
  and underlying development. Title or URL alone never defines an event.
- `condition`, `causal`, and `severity` uncertainty remain separate. An
  unresolved condition blocks confirmation. Causal or severity uncertainty is
  preserved but does not independently block a demonstrated consequence.
- Turnaway counts are not required. A confirmed candidate does require a
  source-backed, actual access consequence and cannot be risk-only.
- Freshness is preserved against the original run and source dates; the
  workflow never compares a historical record with today's date.

## Private artifacts

An import writes only:

```text
data/agent-history/food-line/recoveries/sha256-<32-character-input-digest-prefix>/
  raw_archive.json
  normalized_unique_sources.json
  normalized_findings.json
  event_cluster_manifest.json
  live_site_reconciliation_report.json
  disposition_matrix.json
  import_validation_report.json
  priority_confirmed_candidates.json
  recovery_manifest.json
```

The recovery manifest retains the complete input SHA-256 and content-binds the
cluster specification and every artifact. Exact replay returns
`idempotent_noop`; a conflicting replay or digest-prefix collision fails.

Historical reconciliation excludes only the exact content-addressed recovery
currently being created or replayed. The exclusion is derived internally from
the validated 64-character input SHA-256 and the resolved private recovery
root; the CLI exposes no arbitrary path-exclusion option. An existing target
must be a real direct-child directory with a manifest that binds the same full
input digest. Parent records, sibling and later recoveries, and lookalike paths
remain visible. This keeps the pre-import reconciliation snapshot stable on an
exact replay while allowing genuine later historical-record drift to fail
closed.

Confirmed candidates use four reporting tiers only:

1. closures, suspensions, and direct service reductions;
2. measured benefit loss combined with emergency-food demand;
3. quantified inventory, supply, or capacity strain;
4. all other demonstrated access, affordability, school/grocery, or
   disaster-related food losses.

The tier is private prioritization metadata. It does not change qualification,
disposition, review state, or publication authority.

The four-tier contract applies only to confirmed-candidate ranking. A legacy
complete event manifest may also contain the internal value `6` for a
`risk_or_mitigation_only` event that was deferred, excluded, or retained only
as duplicate/corroboration evidence. That value is an excluded-event sentinel,
not a fifth candidate tier. It must never appear in
`priority_confirmed_candidates.json` and grants no eligibility, approval, queue,
or publication authority.

## Five-tier recovery migration

Recoveries created before the four-tier policy was merged remain immutable
evidence. Do not edit or replay-import them with newer priority semantics. The
sanctioned `migrate` operation instead derives the predecessor from the complete
input digest, validates all eight payload artifacts and the recovery manifest,
and writes a distinct
successor under:

```text
data/agent-history/food-line/recovery-migrations/
  sha256-<32-character-successor-identity-prefix>/
```

The complete successor identity binds the migration and recovery schemas, full
input digest, predecessor artifact-set identity, old and new priority-policy
versions, and the four-tier semantic labels. The successor manifest also binds
the exact predecessor path and artifact hashes, cluster-specification hash,
implementation source commit, new artifact hashes, and new artifact-set
identity. A digest-prefix collision or any path alias, link, reparse point,
unexpected file, schema drift, hash drift, input drift, or specification drift
fails closed.

The migration copies the predecessor reconciliation snapshot and artifacts in
memory. Priorities `1` through `4` remain unchanged, and only a priority value
of `5` on a `disaster_household_food_loss` row may become `4`. A legacy priority
`6` may remain `6` only in the complete event manifest when the row proves the
bounded risk-only, non-candidate state described above; it is rejected from the
candidate report, from confirmed or authority-bearing rows, and from any other
consequence or transition. Event and candidate membership, identity,
disposition, evidence, uncertainty, qualification, URLs, dates, descriptions,
and authority fields must compare exactly. Tier summaries are recomputed in
migration lineage. The complete event manifest can contain one more migrated
disaster-loss row than the confirmed-candidate report when that event has a
non-confirmed disposition; this does not change its disposition.

The successor is created atomically. Exact replay compares every expected byte
and returns `idempotent_noop`; a changed file or lineage argument is a conflict.
The operation accepts no predecessor path, Pages output path, queue path, or
publication option and grants no approval authority.

After synchronizing to the protected commit that contains this operation, run:

```powershell
python scripts/import_food_line_historical_recovery.py migrate `
  --input <exact-original-aggregate.md> `
  --cluster-spec <exact-reviewed-cluster-spec.json> `
  --captured-at <exact-original-capture-time> `
  --run-month <YYYY-MM> `
  --repo-root . `
  --predecessor-artifact-set sha256:<64-character-artifact-set-digest> `
  --implementation-source-commit <40-character-protected-source-commit>
```

Repeat that command unchanged to prove non-mutating replay. Migration remains a
private evidence operation: it does not write Food Line intake, review or
publication queues, generated output, Pages, feeds, audio, social output, or
scheduled tasks.

## Workflow

Inspect without writing:

```powershell
python scripts/import_food_line_historical_recovery.py inspect `
  --input <aggregate.md> `
  --run-month <YYYY-MM>
```

Create an unassigned private cluster template. This operation makes no event
or disposition decision:

```powershell
python scripts/import_food_line_historical_recovery.py template `
  --input <aggregate.md> `
  --run-month <YYYY-MM> `
  --repo-root . `
  --template-output data/agent-history-staging/food-line/<name>-clusters.json
```

After a reviewer consolidates the findings into event clusters and supplies
the five closed dispositions, validate and dry-run:

```powershell
python scripts/import_food_line_historical_recovery.py validate `
  --input <aggregate.md> `
  --run-month <YYYY-MM> `
  --cluster-spec data/agent-history-staging/food-line/<name>-clusters.json `
  --captured-at <ISO-8601-time> `
  --repo-root . `
  --pages-root <clean-gh-pages-checkout>

python scripts/import_food_line_historical_recovery.py dry-run `
  --input <aggregate.md> `
  --run-month <YYYY-MM> `
  --cluster-spec data/agent-history-staging/food-line/<name>-clusters.json `
  --captured-at <same-time> `
  --repo-root . `
  --pages-root <clean-gh-pages-checkout>
```

Only after reviewing those results, import privately and repeat the exact
command to prove idempotency:

```powershell
python scripts/import_food_line_historical_recovery.py import `
  --input <aggregate.md> `
  --run-month <YYYY-MM> `
  --cluster-spec data/agent-history-staging/food-line/<name>-clusters.json `
  --captured-at <same-time> `
  --repo-root . `
  --pages-root <clean-gh-pages-checkout>
```

The cluster specification has `publication_approval: false` and uses exactly
one of:

- `already_published`
- `duplicate_or_corroboration`
- `confirmed_historical_review_candidate`
- `deferred_specific_evidence_gap`
- `excluded_under_existing_rules`

Deferred clusters require an exact `unresolved_requirement`; excluded clusters
require an exact `exclusion_rule`. Generic "insufficient evidence" labels do
not satisfy the review contract.

## Migrated-event editorial review

Recovery and migration stop before substantive editorial review. The
sanctioned `review` operation records one independent decision for an event in
an exact four-tier successor. It derives the successor path from its complete
content identity, validates every immutable artifact and the artifact-set
identity, requires the event to resolve exactly once in both the event and
confirmed-candidate manifests, and binds a clean Pages checkout and source
HEAD. It never accepts an arbitrary recovery path.

Review submissions belong under:

```text
data/agent-history/food-line/reviews/recovery-submissions/
```

The owner writes deterministic decisions under:

```text
data/agent-history/food-line/reviews/recovery-decisions/
  <32-character-successor-identity-prefix>/<event-id>.json
```

The closed decisions are `confirmed`, `deferred_specific_evidence_gap`,
`excluded_under_existing_rules`, `duplicate_or_corroboration`, and
`already_published`. A submission binds exact recovery, artifact-set, event,
source, Pages, evidence, date, event-fact, uncertainty, and dedupe state. It
must show checks across Pages history, source/generated output, story memory
and claim ledgers, current intake, review/publication queues, and prior
historical records. Duplicate and already-published decisions require a real
repository-bound matched artifact. Confirmed decisions alone may include
reader-facing copy and one of the six ordered slots in a clearly named August
2026 retrospective batch.

The submission and decision must keep archive, intake, queue, generation,
approval, publication, Pages, audio, social, and scheduled-task authority
false. Historical dates do not pass through current-intake freshness checks.
Exact replay returns `idempotent_noop`; a changed submission or decision fails
closed. Validate without writing by adding `--review-dry-run`.

```powershell
python scripts/import_food_line_historical_recovery.py review `
  --repo-root . `
  --pages-root <clean-gh-pages-checkout> `
  --successor-identity sha256:<64-character-successor-identity> `
  --artifact-set sha256:<64-character-artifact-set-identity> `
  --event-id food-line-event-<24-hex-characters> `
  --decision confirmed `
  --review-artifact data/agent-history/food-line/reviews/recovery-submissions/<review>.json `
  --review-artifact-sha256 <64-character-review-digest> `
  --operator <operator>
```

This transition records private editorial state only. It does not itself
approve, queue, generate, or publish a retrospective edition.

## Migrated-event retrospective publication

`scripts/manage_food_line_retrospective.py` is the separate authority owner
for a bounded migrated-event retrospective. It accepts only confirmed
decisions and correction overlays read as exact blobs from committed Git
history. The approval request stays outside the repository; the deterministic
approval JSON is the only artifact committed by the approval PR. A protected
merge must place that approval-only commit strictly behind the current source
HEAD before `plan`, preview, generation, or publication will accept it.

The owner enforces a maximum of six ordered stories, one recovery and artifact
set, exact submission/decision/evidence/source/public-copy hashes, an
independent approver, a clean bound `gh-pages` checkout, and an unoccupied
edition identity. Authority is limited to the named batch and nine public
representations: edition HTML, source table, claim ledger, source/curation/
edition manifests, Food Line homepage, archive, and RSS. Daily collection,
source configuration, schedules, social output, and unrelated candidates stay
unauthorized. Food Line audio remains optional under the existing publication
rule and is explicitly not authorized by this retrospective owner.

An encoding correction never edits the protected decision. The
`correct-public-copy` command binds its commit, path, blob, SHA-256, event,
field, prior Unicode text and prior UTF-8 bytes, and permits one exact
replacement. Missing, repeated, changed, already-corrected, or broader edits
fail closed. Exact replay returns `idempotent_noop`.

Approvals keep `edition_date` separate from the actual later
`publication_timestamp`. Reader-facing copy must disclose that the edition is
a retrospective recovery of previously missed August 2026 reporting, and RSS
uses the real publication timestamp. Plan validation searches source output,
Pages, feeds, release and publication state, queues, source manifests, and
story memory for date reuse or prior publication/dedupe collisions.

The sanctioned sequence is:

1. Create and merge any required correction-overlay-only PR.
2. Create both approvals from private request JSON, replay them, and merge the
   approval-only PR normally.
3. Run `plan` twice.
4. Create and verify a deterministic preview under a private directory outside
   the repository.
5. Run the normal Food Line generator with the committed approval and actual
   publication timestamp.
6. Publish with `--publish --push`; the existing guarded Pages owner validates
   the exact release manifest, copies the edition plus homepage/archive/RSS,
   commits, pushes, and live-checks the edition and manifests.
7. Only after successful live verification, record the exact Pages commit in
   Food Line publication state and append the approved event fingerprints and
   source URLs to story memory.

The publish command refuses `--publish` without `--push`, any social flag, any
audio flag, source/Pages drift, partial output, or a stale/different approval.
No scheduler behavior is changed.
