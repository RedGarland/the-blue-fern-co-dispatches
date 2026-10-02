# Scheduled watch ledger and reconciliation

The ChatGPT Gaza, Food Line, and Care Line watches are supplementary discovery systems. They are not evidence that the Windows production collectors ran and they have no publication authority.

Every scheduled watch execution must now leave one durable run record, including runs that find nothing.

## Run contract

Use schema `bluefern.watch_run.v1` with:

- `dispatch`: `gaza`, `food-line`, or `care-line`
- `watch_name`
- `run_id`: stable unique identifier
- `scheduled_for`, `started_at`, `completed_at`: ISO 8601
- `status`: `SUCCESS`, `DEGRADED`, or `FAILED`
- `outcome`: `findings`, `no_findings`, or `failed`
- `search_window`: date_from/date_to/edition_date
- `findings`: array; empty is valid for `no_findings`
- `coverage_notes`

Persist to the authoritative ledger path:

`ops/watch-ledger/<dispatch>/<YYYY-MM-DD>/<run-id>.json`

Identical retries are safe no-ops. A reused run ID with different content fails closed.

## Persistence architecture

Scheduled watches must not commit directly from their runtime worktrees. The
runner submits the validated heartbeat payload to the repository-side workflow
`watch-heartbeat-ingest.yml`. That workflow checks out the dedicated
`ops/watch-ledger` branch, appends exactly one validated
`bluefern.watch_run.v1` file under `ops/watch-ledger/`, commits only that
subtree, and pushes the final ref update.

`ops/watch-ledger` is authoritative for scheduled watch heartbeats. Historical
local files under `data/private-agent-handoff/watch-ledger/` may remain as
preserved evidence, but new success accounting must come from
`ops/watch-ledger`.

Temporary `automation/...` branch commits are diagnostic evidence only. They do
not prove heartbeat persistence and must not be treated as successful scheduled
watch accounting.

Heartbeat persistence failures never authorize disabling, pausing, or mutating a
watch schedule. If ingestion or the final ref update fails, classify that watch
run as `FAILED` or `DEGRADED` according to the watch result, preserve the local
receipt/error evidence, and leave the schedule untouched.

## Reconciliation

Run:

`python scripts/reconcile_watch_findings.py --dispatch care-line --date 2026-09-25`

The reconciliation scans production data/public output for the finding ID, canonical source URL, or title and writes:

`data/private-agent-handoff/watch-reconciliation/<dispatch>/<YYYY-MM-DD>.json`

A finding is either `ACCOUNTED` or `UNRECONCILED`. Reconciliation never approves, publishes, or changes editorial state. An unreconciled finding is an operational recall signal requiring review.

## Source-family corrections from the Sep. 11-25 audit

- Care: CMS Public Notices is an explicit primary source for Medicare terminations and CLIA limitations.
- Gaza: OCHA/WHO health-access and ambulance/fuel/health-system changes receive explicit targeted discovery.
- Food: essential local food-access disruption queries explicitly cover sole/full-service grocery loss, disaster closure, and canceled food distribution.

## Stop condition

The reliability gap is closed when every scheduled watch execution leaves a durable heartbeat, every positive finding is durable before editorial filtering, and daily reconciliation can show either production accounting or an explicit unreconciled finding.
