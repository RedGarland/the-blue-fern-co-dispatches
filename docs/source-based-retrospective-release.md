# Source-based retrospective release authorization

This owner records release authorization for source-based retrospective Food
Line and Care Line approvals. It performs lifecycle step 5 only: release
authorization. It does not authorize publication, Pages sync, RSS/archive
changes, queues, schedules, social output, or audio output.

## Owner and paths

- Owner module: `src/bluefern_dispatches/source_based_retrospective_release.py`
- CLI: `scripts/manage_source_based_retrospective_release.py`
- Request schema: `bluefern.source_based_retrospective_release_request.v1`
- Food release schema: `food_line_source_based_retrospective_release_v1`
- Food path: `releases/food-line/source-based-retrospectives/<batch-id>-release-v1.json`
- Care release schema: `care_line_source_based_retrospective_release_v1`
- Care path: `releases/care-line/source-based-retrospectives/<batch-id>-release-v1.json`

## Authority model

Release records set `release_authorized: true` and keep
`publication_authorized`, `pages_authorized`, `social_authorized`,
`audio_authorized`, and `schedule_authorized` false. Publication requires a
separate future owner.

## Inputs

The request binds:

- committed source-based retrospective approval records;
- the SHA-256 of each approval file;
- a release-readiness review file and SHA-256;
- expected item count and coverage month.

The resulting release record embeds item-level approval hashes, readiness
snapshots, date-binding classification, recommended public date/range, wording
constraints, duplicate status, and lineage status. The durable record remains
auditable if local review output is later removed.

## Date binding

Supported chronology categories are:

- `august_event`
- `august_announcement_future_effect`
- `august_restoration`
- `august_reporting_on_continuing_prior_loss`
- `september_effective_event_with_august_source`

Release authorization does not force a public edition date to match the source
publication date.

## Commands

Create from a private request outside the repository:

```powershell
python scripts/manage_source_based_retrospective_release.py create `
  --repo-root . `
  --request C:\path\outside\repo\release-request.json
```

Validate without mutation:

```powershell
python scripts/manage_source_based_retrospective_release.py validate `
  --repo-root . `
  --dispatch food-line `
  --release-path releases/food-line/source-based-retrospectives/<batch-id>-release-v1.json
```
