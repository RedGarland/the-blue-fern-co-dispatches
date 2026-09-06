# Source-based retrospective publication authorization

This owner records publication authorization for source-based retrospective
Food Line and Care Line records that already have durable release authorization.
It implements lifecycle step 6 only.

It does not generate public artifacts, update queues, change schedules, sync
Pages, push Pages, create social output, or create audio output.

## Owner and paths

- Owner module: `src/bluefern_dispatches/source_based_retrospective_publication.py`
- CLI: `scripts/manage_source_based_retrospective_publication.py`
- Request schema: `bluefern.source_based_retrospective_publication_request.v1`
- Food schema: `food_line_source_based_retrospective_publication_authorization_v1`
- Food path: `publication-authorizations/food-line/source-based-retrospectives/<batch-id>-publication-v1.json`
- Care schema: `care_line_source_based_retrospective_publication_authorization_v1`
- Care path: `publication-authorizations/care-line/source-based-retrospectives/<batch-id>-publication-v1.json`

## Lifecycle separation

The pipeline remains:

1. source evidence
2. retrospective finding
3. editorial triage
4. retrospective approval
5. release authorization
6. publication authorization
7. public generation
8. Pages deployment

Publication authorization may set `publication_authorized: true`. It keeps
`pages_authorized`, `pages_push_authorized`, `social_authorized`,
`audio_authorized`, `schedule_authorized`,
`scheduled_task_change_authorized`, `public_generation_authorized`, and
`public_artifacts_generated` false.

Public artifact generation requires a later explicit workflow.

## Inputs

The request must be private and outside the repository. It binds durable release
records by repository-relative path and SHA-256. Each requested item must name a
release item ID and explicit human public placement.

Publication authorization cannot be created from raw retrospective findings,
approval-prep artifacts, or approval records without release authorization.

## Chronology and placement

Each item must preserve:

- chronology classification
- source publication date
- event/effective date or range
- retrospective coverage period
- public edition date or placement
- mandatory public wording constraints

Supported classifications:

- `august_event`
- `august_announcement_future_effect`
- `august_restoration`
- `august_reporting_on_continuing_prior_loss`
- `september_effective_event_with_august_source`

The public placement is explicit human input. The owner does not infer it from
source publication date.

## Commands

Create from a private request:

```powershell
python scripts/manage_source_based_retrospective_publication.py create `
  --repo-root . `
  --request C:\path\outside\repo\publication-request.json
```

Validate without mutation:

```powershell
python scripts/manage_source_based_retrospective_publication.py validate `
  --repo-root . `
  --dispatch food-line `
  --publication-path publication-authorizations/food-line/source-based-retrospectives/<batch-id>-publication-v1.json
```

## August workflow after merge

After this governance owner is merged, prepare private Food and Care publication
request JSON files that bind the durable release authorization records by hash
and include one explicit public placement decision for each release item. Then
run the create command above for each dispatch and commit only the resulting
durable publication-authorization records.
