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

Persist to:

`data/private-agent-handoff/watch-ledger/<dispatch>/<YYYY-MM-DD>/<run-id>.json`

Identical retries are safe no-ops. A reused run ID with different content fails closed.

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
