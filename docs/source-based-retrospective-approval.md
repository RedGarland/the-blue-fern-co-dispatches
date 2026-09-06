# Source-based retrospective approval governance

This owner records bounded retrospective editorial approval for Food Line and
Care Line source-based findings. It stops at editorial approval and does not
authorize release, publication, Pages sync, RSS/archive generation, schedules,
audio, or social output.

## Owner and paths

- Owner module: `src/bluefern_dispatches/source_based_retrospective_approval.py`
- CLI: `scripts/manage_source_based_retrospective_approval.py`
- Request schema: `bluefern.source_based_retrospective_approval_request.v1`
- Food approval schema: `food_line_source_based_retrospective_approval_v1`
- Food path: `approvals/food-line/source-based-retrospectives/<batch-id>-approval-v1.json`
- Care approval schema: `care_line_source_based_retrospective_approval_v1`
- Care path: `approvals/care-line/source-based-retrospectives/<batch-id>-approval-v1.json`

The existing Food `food_line_retrospective_approval_v5` owner is unchanged. It
continues to govern migrated-event retrospective releases that bind committed
recovery decisions and later publication authority.

## Lifecycle separation

The source-based approval contract keeps these steps separate:

1. source evidence
2. retrospective discovery or replay result
3. editorial triage / approval-prep
4. human retrospective editorial approval
5. release authorization
6. publication

This owner performs only step 4. Approval records set
`approved_for_retrospective_editorial_use: true` and keep
`approved_for_release`, `approved_for_publication`, `release_authorized`,
`publication_authorized`, and `pages_authorized` false.

## Durable binding

Approval creation reads a private request outside the repository and a validated
approval-prep artifact. The resulting approval embeds each selected item’s
source URL, source identifier, retrospective finding lineage, prep decision
identifier, source evidence hash, item hash, rationale, uncertainty, duplicate
lineage, date, location, state, and pressure/service type.

The approval records the approval-prep artifact path and SHA-256 but does not
depend on that local `output/review` path for later audit. The item snapshots
and hashes remain in the durable approval record.

## Batch size

Food and Care source-based retrospective approval batches are limited to six
items. This preserves the repository’s existing retrospective-review safety
posture and means the August Food approval set must be split across multiple
approval batches.

## Validation

Validate an approval without mutation:

```powershell
python scripts/manage_source_based_retrospective_approval.py validate `
  --repo-root . `
  --dispatch food-line `
  --approval-path approvals/food-line/source-based-retrospectives/<batch-id>-approval-v1.json
```

Create an approval from a private request:

```powershell
python scripts/manage_source_based_retrospective_approval.py create `
  --repo-root . `
  --request C:\path\outside\repo\request.json
```

The request must include the exact current source commit, the approval-prep
artifact SHA-256, and one to six selected source/finding identifiers.

## Later release authorization

A separate future owner must explicitly authorize release/publication from these
approval records. This governance extension intentionally does not wire
source-based retrospective approvals into Food or Care publication queues.
