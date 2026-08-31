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
