# Dispatches From The Blue Fern Co. Project Contract

These are the current non-negotiable operating rules for this project.

- No fact without a traceable source.
- Source intake starts wide and filters down. Aggregators such as Google News are discovery surfaces only, and the canonical publisher URL is the evidence URL when available.
- Missing relevant sources are intake failures and must be logged with explicit skip or miss reasons.
- The diagnostic ladder is intake, canonicalization, dedupe, classification, scoring, rendering, publishing.
- This wide-discovery, strict-vetting pattern must remain reusable across Gaza, Food Line, and Care Line.
- Gaza is daily, public, and free.
- Cascadia is weekly.
- Cascadia edition date is the Sunday `coverage_end`.
- Cascadia public labels use the coverage range.
- Cascadia archive, recent editions, and RSS are weekly-only.
- `output/detail` and `output/paid` must never appear under `output/site`.
- The GitHub Pages deploy branch is `gh-pages`.
- The Pages repo is publish output only.
  - Publishing behavior: the publisher copies `output/site` into the `bluefern-dispatches-pages` repository and creates a local commit by default; pushing those commits to the remote is an explicit, separate step (use the dispatch runner `--push` or push from the Pages repo).
- No old project runtime dependencies.
- Scheduled tasks use the project `.venv`.
- `SMTP_PASSWORD` is never logged.

## Codex Safe Execution Scope

Mechanical source merge authority is distinct from editorial, publication, Pages, and release authority. Codex may prepare and merge a bounded routine source PR after proving the current-base, exact-head, scope, mergeability, and required-check conditions below. Codex is never permitted to infer or create human decision authority from that mechanical merge.

Codex may:

- create a feature branch from the approved base branch
- stage only explicitly named source, config, test, or documentation files
- run `git diff --cached --stat`
- run `git diff --cached --check`
- run `git diff --cached --name-only`
- verify the staged file list matches the intended files only
- commit with a scoped commit message
- push the feature branch
- create a PR against the approved base branch
- run or watch PR checks
- open the PR in the browser with `gh pr view --web`
- classify the PR as `CODEX_AUTO_MERGE_ELIGIBLE` or `HUMAN_MERGE_REQUIRED`
- merge a `CODEX_AUTO_MERGE_ELIGIBLE` PR with exact-head protection only after synchronizing it with the current protected base, recording the exact PR head, and proving all required checks succeeded on that head
- after any merge, fetch the protected branch, prove the reviewed PR head is contained in the protected result, and verify source and Pages status
- delete local and remote feature branches only after merge confirmation

Codex must not:

- publish a public edition
- sync, commit, or push the Pages repo
- post to Bluesky or other social platforms
- create or replace podcast, audio, or other public publication artifacts for release
- decide that a candidate is source-backed enough for public publication
- relax source eligibility gates
- alter editorial standards
- commit generated public output unless explicitly instructed
- use `git add .`
- delete broad generated folders without explicit instruction

`HUMAN_MERGE_REQUIRED` applies when the PR itself:

- introduces or changes `approvals/**` or other editorial, approval, publication, release, correction, or withdrawal authority
- records a substantive human editorial decision or publication-state/story-memory release handoff
- commits or pushes Pages/public generated release content as a release action
- changes branch protection, repository rulesets, credentials, secrets, destructive-operation authority, or consequential external-egress policy
- expands or materially changes Codex/AI authority or any repository governance boundary
- exceeds the task's existing authorization or has an unresolved review issue or blocker

Codex must never use routine merge permission to merge a change that expands its own permissions. Publication remains a separately authorized boundary: a successful source PR merge does not authorize Pages sync, public release, audio, social posting, editorial approval, candidate approval, or source-gate relaxation.

Codex may do the following only with explicit instruction:

- run discovery or backfill jobs that create candidate or review artifacts
- clean specific generated artifacts
- run dry-run publish validation
- update discovery or source configuration
- create commits and PRs
- delete feature branches after merge confirmation

Required staging rule before every commit:

- run `git diff --cached --stat`
- run `git diff --cached --check`
- run `git diff --cached --name-only`
- verify the staged file list contains only the intended files
- if unrelated files are staged, stop and unstage them before committing

Default safe PR command pattern:

```powershell
git switch -c feature/<scoped-branch-name>

git add `
  <explicit-file-1> `
  <explicit-file-2> `
  <explicit-file-3>

git diff --cached --stat
git diff --cached --check
git diff --cached --name-only

git commit -m "<scoped commit message>"
git push -u origin feature/<scoped-branch-name>

gh pr create `
  --base add/pages-repo-default `
  --head feature/<scoped-branch-name> `
  --title "<PR title>" `
  --body "<PR body with validation results and no publish/no Pages sync statement>"

gh pr checks --watch
gh pr view --web
```

## American Pressure Operating Model

1. Public cadence:
- American Pressure is a weekly public briefing.
- Default edition date is the completed week-ending Saturday.
- Public labels must show the full date range, for example `Weekly briefing / May 3-May 9, 2026`.

2. Collection cadence:
- Candidate intake may run daily.
- Candidate files live under `data/dispatches/american-pressure/candidates/YYYY-MM-DD/candidate_sources.json`.
- Weekly manual/curated source records may live under `data/dispatches/american-pressure/sources/YYYY-MM-DD/manual_sources.json`.
- Daily scouting is intake-only and must not publish.
- Candidate review status defaults to `needs_review`; only explicitly `approved` candidates may flow into weekly merge.

3. Editorial model:
- Public briefs pair relatable current developments with reliable data anchors.
- The public unit is a mini-brief, not a raw source record.
- The strongest brief type is human story plus data anchor.
- Baseline-only gauges are allowed as data context and must not be framed as proof of what changed that week.

4. Source roles:
- `human_story`: real-world development affecting people, services, jobs, costs, access, or local systems.
- `data_anchor`: official or institutional data used for context, scale, or trend monitoring.
- `watchlist_signal`: potentially relevant source that does not yet support a public claim.

5. Public rendering:
- Internal labels must not appear in public output: `story_plus_data`, `baseline_gauge`, `current_week_development`, `source_role`, `brief_quality`.
- Public mini-brief sections are plain English:
  `Current Development`, `Data Context`, `Potential Relevance`, `Who May Feel It`, `What to Watch Next`, `Sources`.

6. Statistics:
- A mini-brief may include one key statistic only when directly source-backed.
- Do not invent statistics.
- Do not infer numeric trends unless explicitly supported by source records.
- Key statistic fields must identify the supporting source.

7. Coverage diagnostics:
- Weekly manifests report:
  `week_start_date`, `week_end_date`, `display_date_range`, `source_count`, `story_count`, `story_plus_data_count`, `baseline_only_count`, `current_development_count_by_pillar`, `human_story_count_by_pillar`, `missing_required_current_development_pillars`, `collection_gap_pillars`.

8. Required current-development search targets:
- food pressure
- financial distress / bankruptcy / debt
- housing and monthly bills
- health access
- jobs and paychecks
- local system strain
- weather/disaster/environmental pressure
- policy implementation when available

If a pillar has no human story, report it as a collection gap. Do not imply that no relevant news existed.

9. Candidate safety and approvals:
- No candidate without a traceable source URL.
- No invented summaries or generated prose as source material.
- Reject or downrank investor-only, opinion-only, duplicate/stale, and no-public-impact items.
- Approved-candidate merge is opt-in (`--include-approved-candidates`) and must exclude unapproved/rejected candidates.

10. American Pressure artifact retention:
- Durable records (commit-eligible):
  - `data/source_registry/american_pressure_sources.json`
  - `data/dispatches/american-pressure/sources/YYYY-MM-DD/manual_sources.json` after editorial review.
- Deferred local intake/backfill artifacts (local-only by default, not tracked):
  - `data/dispatches/american-pressure/candidates/YYYY-MM-DD/candidate_sources.json`
  - `data/dispatches/american-pressure/sources/YYYY-MM-DD/feed_backfill_sources.json`
- Intake/backfill artifacts may be escalated into durable records only by explicit review workflow and a scoped commit.
