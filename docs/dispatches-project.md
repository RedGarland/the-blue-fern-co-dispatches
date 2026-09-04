# Dispatches Project Notes

This project is intentionally separate from the existing Gaza and FDA/Cascadia pipeline folders. It borrows the Gaza GitHub Pages visual theme and the Cascadia source/curation philosophy, but writes its own outputs, records, manifests, and backups.

Current dispatch slugs:

- `gaza`
- `american-pressure`
- `cascadia`

Current generated edition dates:

- `gaza`: `2026-05-19`
- `american-pressure`: `2026-05-19`
- `cascadia`: `2026-05-10`

Safety defaults:

- dry-run does not write files
- publisher reports `would_push: false`
- paid/detail artifacts are excluded from `output/site`
- Pages repo publishing copies only `output/site`
- Pages repo publishing targets the `gh-pages` deploy branch by default
- Pages repo publishing preserves `.git/` and writes/validates `CNAME`
- backups are outside the repository by default
- no destructive git or DNS behavior is implemented

Pages repo dry-run:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --pages-branch gh-pages
```

Copy + commit locally, no push:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --pages-branch gh-pages --commit --no-push
```

Manual push after inspection:

```powershell
cd "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

GitHub Pages and DNS must be configured separately. Do not force-push. The source project branch (`master` or `main`) is separate from the Pages repo deploy branch; the public site deploys from `gh-pages`, and the local Pages repo should be checked and committed on `gh-pages` before publishing.

## Gaza Historical Generation

Gaza historical editions are self-contained in this repository. The workflow starts from project-local source records and never depends on rendered output from another project.

Manual source input:

```text
data/dispatches/gaza/sources/YYYY-MM-DD/manual_sources.json
```

Required fields:

- `source_record_id`
- `title`
- `url`
- `publisher`
- `published_at`
- `retrieved_at`
- `summary_or_snippet`
- `source_type`
- `region_scope`
- `category_hint`
- `reliability_tier`

## Source Discovery Policy

Source intake must start wide and filter down. The diagnostic ladder is:

1. intake
2. canonicalization
3. dedupe
4. classification
5. scoring
6. rendering
7. publishing

Aggregators such as Google News are discovery surfaces, not final evidence sources. When a canonical publisher URL is available, that publisher URL is the evidence URL of record. Missing relevant sources are intake failures and must be logged with explicit miss or skip reasons.

This wide-discovery, strict-vetting pattern should be reusable across Gaza, Food Line, and Care Line. If a task reveals a durable workflow rule or architecture principle, update the relevant project docs in the same PR.

## Codex Safe Execution Scope

Source-repo work should follow a PR workflow. Codex may mechanically merge a bounded routine source PR after synchronization with the current protected base and successful scope, mergeability, required-check, and exact-head validation. Human authority remains required for editorial, approval, publication, release, Pages, and governance-expansion decisions.

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
- merge only a `CODEX_AUTO_MERGE_ELIGIBLE` source PR with exact-head protection
- after any merge, fetch the protected branch, prove the reviewed PR head landed, and verify source and Pages status
- delete local and remote feature branches only after merge confirmation

Codex must not:

- publish public editions
- sync, commit, or push the Pages repo
- post to Bluesky or other social platforms
- create or replace podcast, audio, or other public publication files for release
- decide that a candidate is source-backed enough for public publication
- relax source eligibility gates
- alter editorial standards
- commit generated public output unless explicitly instructed
- use `git add .`
- delete broad generated folders without explicit instruction

Human merge is required for authority-bearing, governance-expanding, editorial, approval, correction/withdrawal, release, publication-state, public-output, credential, ruleset, and consequential external-egress changes. Codex must never use routine merge permission to expand its own authority, and source merge success does not authorize publication or Pages activity.

Explicit instruction remains required for:

- running discovery or backfill jobs that create candidate or review artifacts
- cleaning specific generated artifacts
- dry-run publish validation
- updating discovery or source configuration
- creating commits and PRs
- deleting feature branches after merge confirmation

Required staging rule before every commit:

- run `git diff --cached --stat`
- run `git diff --cached --check`
- run `git diff --cached --name-only`
- verify the staged file list contains only intended files
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

### Publishing a historical Gaza edition

1. Create:

```text
data/dispatches/gaza/sources/YYYY-MM-DD/manual_sources.json
```

2. Run:

```powershell
python scripts\publish_gaza_historical.py --date YYYY-MM-DD
```

3. Inspect:

```text
bluefern-dispatches-pages/gaza/archive.html
bluefern-dispatches-pages/gaza/editions/YYYY-MM-DD/index.html
```

4. Push when ready:

```powershell
cd "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

Optional one-line publish with push:

```powershell
python scripts\publish_gaza_historical.py --date YYYY-MM-DD --push
```

`--push` is opt-in only. The default command generates from project-local source records, validates source traceability, runs tests, dry-runs Pages publishing, updates and commits the local Pages repo, and prints the manual push command.

Underlying generation command:

```powershell
python scripts\run_gaza_dispatch.py --date YYYY-MM-DD --historical --from-manual-sources --all
```

Review:

```text
output/site/gaza/editions/YYYY-MM-DD/index.html
```

Dry-run publish:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --pages-branch gh-pages --expect-date YYYY-MM-DD
```

Commit to the local Pages repo without pushing:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --pages-branch gh-pages --expect-date YYYY-MM-DD --commit --no-push
```

Manual push after inspection:

```powershell
cd "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

The generator writes:

```text
data/dispatches/gaza/raw/YYYY-MM-DD/raw_sources.json
data/dispatches/gaza/normalized/YYYY-MM-DD/normalized_sources.json
data/dispatches/gaza/curated/YYYY-MM-DD/curation_manifest.json
output/dispatches/gaza/editions/YYYY-MM-DD/
output/site/gaza/editions/YYYY-MM-DD/
C:\PythonProjects\dispatches-bluefern-backups\gaza\YYYY-MM-DD\
```

Gaza remains fully free/public. Every story must list source record IDs, publisher names, and visible source links. If source detail is insufficient, write a shorter story or omit it.

## Daily Gaza One-Command Run

The daily runner is the scheduled/manual wrapper for Gaza:

```powershell
python scripts\run_daily_gaza.py --date YYYY-MM-DD
```

The new-machine notification wrapper uses the Gaza daily runner for normal reports:

```powershell
.\.venv\Scripts\python.exe scripts\run_and_notify.py --date 2026-05-09 --publish --smtp-debug
```

SMTP-only diagnostic mode:

```powershell
.\.venv\Scripts\python.exe scripts\run_and_notify.py --date 2026-05-09 --smtp-debug --send-test-email
```

Expected: the diagnostic command sends a minimal SMTP test email, redacts config/debug output, and does not run the Gaza pipeline, run tests, publish, push, or touch the Pages repo.

It supports `--source-mode auto|manual|both`; the default is `both`. Manual records live at `data/dispatches/gaza/sources/YYYY-MM-DD/manual_sources.json`. Auto collection reads only `data/dispatches/gaza/sources.yml` and writes project-local source records back into this repository. It does not use old Gaza project folders, old rendered output, DNS settings, or GitHub Pages settings.

Task Scheduler action without push:

```text
Program/script:
powershell.exe

Arguments:
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\PythonProjects\Dispatches From The Blue Fern Co'; & '.\.venv\Scripts\python.exe' 'scripts\run_daily_gaza.py' --date (Get-Date -Format 'yyyy-MM-dd') --email-report"

Start in:
C:\PythonProjects\Dispatches From The Blue Fern Co
```

Optional scheduled publish with push:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\PythonProjects\Dispatches From The Blue Fern Co'; & '.\.venv\Scripts\python.exe' 'scripts\run_daily_gaza.py' --date (Get-Date -Format 'yyyy-MM-dd') --email-report --push"
```

Manual push after inspection:

```powershell
cd "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

Default behavior updates and commits the local Pages repo on `gh-pages` without pushing. `--push` is opt-in only. Gaza remains free/public. The hard rule is **NO FACT WITHOUT A TRACEABLE SOURCE**: if no valid source records exist or collection fails, the daily runner stops before publishing.

Daily logs and manifests:

```text
logs/gaza-daily-YYYY-MM-DD.log
data/dispatches/gaza/editions/YYYY-MM-DD/run_manifest.json
```

`--email-report` sends a plain-text report whether the run succeeds or fails, including source count, generation and validation status, public URLs, local paths, warnings, errors, manual push command when push is skipped, and the last 80 lines of the daily log. Email uses the project SMTP environment pattern:

- `SMTP_HOST` required
- `SMTP_PORT` optional, defaults to `587`
- `SMTP_USE_SSL` optional
- `SMTP_TLS_VERIFY` optional, defaults to `true`
- `SMTP_RELAX_X509_STRICT` optional; set to `1` only for temporary diagnostics
- `SMTP_TRUSTSTORE` optional; set to `1` to prefer truststore-backed verification when available
- `SMTP_TLS_CA_SOURCE` optional (`auto`, `truststore`, `certifi`); defaults to `auto`
- `SMTP_CA_FILE` or `SMTP_CA_BUNDLE` optional path to a PEM CA bundle used for SMTP TLS verification
- `SMTP_TIMEOUT` optional
- `SMTP_RETRIES` optional
- `SMTP_RETRY_DELAY` optional
- `SMTP_USER` or `SMTP_USERNAME` optional
- `SMTP_PASSWORD` required when `SMTP_USER` or `SMTP_USERNAME` is set
- `EMAIL_TO` required, comma-separated recipients
- `EMAIL_FROM` or `SMTP_FROM` optional

For Gmail on port `587`, use STARTTLS with `SMTP_USE_SSL=0`. For Gmail on port `465`, use SMTP over SSL with `SMTP_USE_SSL=1`. Keep TLS verification enabled for normal runs. A self-signed certificate chain error usually means local TLS inspection is replacing the SMTP server certificate, or Python is missing the trusted local CA; export that CA as PEM and set `SMTP_CA_FILE`/`SMTP_CA_BUNDLE` when inspection is intentional, or enable `SMTP_TRUSTSTORE=1` if truststore-backed system trust is available. `SMTP_RELAX_X509_STRICT=1` is a temporary diagnostic escape hatch only.

Exit codes: `0` means the pipeline succeeded and email was sent, or email was not requested. `1` means the pipeline failed but email was sent. `2` means email was requested but could not be sent. SMTP passwords must stay in environment variables or a credential manager, never in scheduled task arguments or logs.

## Cascadia Dispatch Pipeline

The Cascadia pipeline is standalone inside this repository. It is separate from the older FDA/Cascadia media pipeline and must not depend on that project structure.

## American Pressure Data Retention

Retention policy for American Pressure collection artifacts:

- Durable source data (commit when reviewed):
  - `data/source_registry/american_pressure_sources.json`
  - `data/dispatches/american-pressure/sources/YYYY-MM-DD/manual_sources.json`

- Local intake/backfill artifacts (do not track by default):
  - `data/dispatches/american-pressure/candidates/YYYY-MM-DD/candidate_sources.json`
  - `data/dispatches/american-pressure/sources/YYYY-MM-DD/feed_backfill_sources.json`

Decision rule:

- If a record is needed for public claims, move it into reviewed manual source packs before committing.
- If a file is intake-only/quarantine or feed supplement output, keep local for workflow/audit and exclude from git by default.

Deterministic guard:

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
```

`doctor.py` checks that deferred candidate/backfill files are not tracked while durable manual/registry files remain commit-eligible.

Region scope:

- Washington
- Oregon
- Idaho

Source configuration:

```text
data/dispatches/cascadia/sources.yml
data/dispatches/cascadia/historical_sources.yml
data/dispatches/cascadia/source_registry.yml
```

Full run:

```powershell
python scripts\run_cascadia_dispatch.py --date YYYY-MM-DD --all
```

Operational cadence:

```powershell
python scripts\run_cascadia_dispatch.py --date YYYY-MM-DD --daily
python scripts\run_cascadia_dispatch.py --date 2026-05-11 --weekly-public --historical-search
python scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --historical-search
python scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --weekly-public --historical-search
python scripts\run_cascadia_dispatch.py --week-start 2026-04-20 --week-end 2026-04-26 --weekly-public --historical-search
powershell -ExecutionPolicy Bypass -File scripts\run_weekly_cascadia.ps1
python scripts\publish_github_pages.py --pages-repo "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --pages-branch gh-pages --commit --no-push
```

The public Cascadia edition is weekly. Monday runs cover the previous completed Monday-Sunday window. The project uses the Sunday coverage-end as the public edition date for weekly archives, so a `2026-05-11` run covers `2026-05-04` through `2026-05-10` and writes `/cascadia/editions/2026-05-10/`.

Historical search is a retrieval feature, not a migration from earlier Cascadia/FDA project records. It searches public provider material for the exact coverage window, writes source records under `data/dispatches/cascadia/sources/YYYY-MM-DD_YYYY-MM-DD/`, merges optional `manual_sources.json` supplements, dedupes, normalizes, scores, curates, and renders only source-backed weekly public stories. Supported modes are `--historical-provider all`, `--historical-provider manual`, `--historical-provider registry`, `--historical-provider gdelt`, and comma-separated combinations such as `registry,manual` or `gdelt,registry,manual`. Sparse weeks are explained by `historical_search_report.json`, including provider counts, manual validation status, registry cache/fetch diagnostics, GDELT cache/rate-limit diagnostics, dedupe counts, final saved source count, and a recommendation. Unsupported stories are omitted.

Quality weekly runs also write local zero-week QA reports under `output/dispatches/cascadia/weekly_gap_reports/` when rerun through the documented supplement workflow. These reports stay out of `output/site` and summarize manual supplement status, registry and official-page checks, GDELT query attempts, candidate/rejection counts, fetch warnings, TLS/network warnings, and whether a zero-story result is credible.

### Cascadia layered free-source model

The Cascadia source portfolio is intentionally layered and free:

- Tier 1 official/public sources: state agencies, county/city emergency management, transportation departments, health departments, ecology/environment agencies, utilities/public infrastructure feeds, public safety alerts, and official press releases.
- Tier 2 free structured search providers: GDELT and any future free/no-key provider only after it is safe and reliable enough for this project.
- Tier 3 public RSS/local-regional sources: public radio, nonprofit/local news, statehouse/public-policy outlets, and stable local/regional feeds where publicly accessible.
- Tier 4 manual supplements: `manual_sources.json`, which remains first-class and validated.

The model requires no paid APIs or paid API keys, has no old project dependency, and follows the project rule: no fact without a traceable source URL. Registry sources live in `data/dispatches/cascadia/source_registry.yml`; registry feed cache files live under `data/dispatches/cascadia/cache/registry/`. Page-only official sources are retained as curated source inventory and diagnostics, but the collector only fetches RSS, Atom, and alert feeds automatically.

Run weekly with all free providers:

```powershell
.\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --date 2026-05-11 --weekly-public --historical-search --historical-provider all
```

Backfill four weeks with all providers:

```powershell
.\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --historical-search --historical-provider all
```

Registry-only test:

```powershell
.\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --weekly-public --historical-search --historical-provider registry
```

Manual plus registry:

```powershell
.\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --weekly-public --historical-search --historical-provider registry,manual
```

Gap report:

```powershell
.\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --source-gap-report
```

Manual Cascadia supplement workflow:

```powershell
python scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --create-manual-template
```

Edit:

```text
data\dispatches\cascadia\sources\2026-04-20_2026-04-26\manual_sources.json
```

Validate without publishing:

```powershell
python scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --validate-manual-sources
```

Generate with manual plus registry plus GDELT:

```powershell
python scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --weekly-public --historical-search --historical-provider all
```

Generate manual-only:

```powershell
python scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --weekly-public --historical-search --historical-provider manual
```

Backfill four weeks:

```powershell
python scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --historical-search --historical-provider all
```

Find sparse weeks without publishing:

```powershell
python scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --source-gap-report
```

Manual records should include `source_record_id`, `title`, `url`, `publisher`, `published_at`, `retrieved_at`, `summary_or_snippet`, `source_type: manual`, `provider_id: manual`, `region_terms_matched`, `category_hint`, `state_hint`, `reliability_tier`, and `traceability_note`. URL is required. Leave `summary_or_snippet` blank rather than inventing source text.

Daily jobs:

- Gaza daily pipeline
- Cascadia daily/internal collection, if used

Weekly jobs:

- Cascadia weekly public briefing

Task Scheduler setup for Cascadia:

- Task name: `Cascadia Weekly Briefing`
- Trigger: Weekly, Monday, 7:00 AM local time
- Program/script: `powershell.exe`
- Start in: `C:\PythonProjects\Dispatches From The Blue Fern Co`
- Keep separate from Gaza Daily Pipeline

Arguments for the weekly Cascadia run with confirmation email and without push:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\PythonProjects\Dispatches From The Blue Fern Co'; $env:CASCADIA_ALLOW_CURL_NO_REVOKE='1'; $env:CASCADIA_FETCH_BACKEND='auto'; $env:SMTP_RELAX_X509_STRICT='1'; & '.\.venv\Scripts\python.exe' 'scripts\run_cascadia_and_notify.py' --date (Get-Date -Format 'yyyy-MM-dd')"
```

Manual push after inspection:

```powershell
cd "C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

Stage outputs:

```text
data/dispatches/cascadia/raw/YYYY-MM-DD/raw_sources.json
data/dispatches/cascadia/sources/YYYY-MM-DD_YYYY-MM-DD/historical_sources.json
data/dispatches/cascadia/sources/YYYY-MM-DD_YYYY-MM-DD/historical_search_report.json
data/dispatches/cascadia/normalized/YYYY-MM-DD/normalized_sources.json
data/dispatches/cascadia/curated/YYYY-MM-DD/curation_manifest.json
output/dispatches/cascadia/editions/YYYY-MM-DD/
output/site/cascadia/editions/YYYY-MM-DD/
output/detail/cascadia/YYYY-MM-DD/
data/records/
```

The public Cascadia edition includes only stories with traceable source records and visible source links. Detail records are written only to `output/detail/cascadia/YYYY-MM-DD/` and are not published publicly. Weekly manifests include `dispatch_slug`, `public_name`, `briefing_type`, `run_date`, `edition_date`, `coverage_start`, `coverage_end`, `week_label`, `source_record_ids`, `source_urls`, `historical_search`, `providers_used`, `query_count`, `included_source_count`, and `excluded_source_count`.

Public brand: The Cascadia Briefing.

Internal data product: Cascadia Signal.

The public URL slug remains `/cascadia/`. The public page and edition format should stay consistent with the existing Blue Fern/Gaza-style dispatch format unless a small label change is required.

Shared dispatch records live under `data/records/`:

- `dispatches.json`
- `editions.json`
- `sources.json`
- `records.json`
- `curation_decisions.json`
- `detail_packages.json`

This structure is dispatch-agnostic and should support Gaza, Cascadia, food insecurity, political actions, healthcare access, and future briefings.

Core rule: **NO FACT WITHOUT A TRACEABLE SOURCE.** Every public factual story, signal, score, trend, summary, and detail/data record must trace back to source records through manifests or the shared dispatch data layer.

Paid/detail artifacts are private. Do not expose `output/detail/`, `output/paid/`, raw source dumps, or Cascadia Signal package files under `output/site/`.

## American Pressure Weekly Operating Model

- Public product: weekly dispatch.
- Intake model: daily candidate files plus weekly manual/curated sources.
- Source mode: both, using current-week human-story records plus official data anchors.
- Edition date: completed week-ending Saturday.
- Display date range: full date range, for example `May 3-May 9, 2026`.
- Public output: lay-reader mini-briefs with real-life story sources and data/context sources.
- Live push: opt-in only.

Candidate intake file path:

```text
data/dispatches/american-pressure/candidates/YYYY-MM-DD/candidate_sources.json
```

Manual/curated weekly source file path:

```text
data/dispatches/american-pressure/sources/YYYY-MM-DD/manual_sources.json
```

1. Initialize daily candidates:

```powershell
.\.venv\Scripts\python.exe scripts\run_weekly_american_pressure.py `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --init-candidates
```

Daily scout intake (no publish):

```powershell
.\.venv\Scripts\python.exe scripts\scout_american_pressure_candidates.py `
  --start-date YYYY-MM-DD `
  --end-date YYYY-MM-DD `
  --write `
  --max-per-pillar 4
```

Daily candidate review report:

```powershell
.\.venv\Scripts\python.exe scripts\review_american_pressure_candidates.py `
  --date YYYY-MM-DD `
  --write
```

Review model:
- New candidate records are written with `review_status: needs_review`.
- Approved merge is explicit: only records marked `review_status: approved` are eligible for weekly intake.
- Rejected candidates are never merged into weekly generation.

2. Generate weekly edition:

```powershell
.\.venv\Scripts\python.exe scripts\run_weekly_american_pressure.py `
  --week-ending YYYY-MM-DD `
  --source-mode both `
  --include-approved-candidates
```

3. Publish locally:

```powershell
.\.venv\Scripts\python.exe scripts\run_weekly_american_pressure.py `
  --week-ending YYYY-MM-DD `
  --source-mode both `
  --include-approved-candidates `
  --publish
```

4. Publish live:

```powershell
.\.venv\Scripts\python.exe scripts\run_weekly_american_pressure.py `
  --week-ending YYYY-MM-DD `
  --source-mode both `
  --include-approved-candidates `
  --publish `
  --push
```

5. Task Scheduler command:

Program/script:
`powershell.exe`

Arguments:
`-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\PythonProjects\Dispatches From The Blue Fern Co'; & '.\.venv\Scripts\python.exe' 'scripts\run_weekly_american_pressure.py' --week-ending previous-saturday --source-mode both --include-approved-candidates --publish --push"`

- `--publish` updates the local Pages repo.
- `--push` pushes live from `bluefern-dispatches-pages` on `gh-pages`.
- Never run `git push origin gh-pages` from the source repo.
- Source repo branch and Pages repo branch are separate.
- Command examples in this section use direct script invocation style (`python scripts\...`) consistently.
