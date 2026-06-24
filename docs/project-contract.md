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
