# Dispatches From The Blue Fern Co.

Unified static dispatch site for:

- `gaza` - Dispatches From Gaza, always free/public.
- `cascadia` - The Cascadia Briefing, powered internally by the Cascadia Signal data product.

Public site output is generated under `output/site/` with URLs rooted at:

- `https://dispatches.thebluefernco.com/`
- `https://dispatches.thebluefernco.com/gaza/`
- `https://dispatches.thebluefernco.com/cascadia/`

## Build

```powershell
python scripts\publish_github_pages.py
```

Dry-run mode reports planned writes, public URLs, backup paths, push status, warnings, and paid/detail exclusion status:

```powershell
python scripts\publish_github_pages.py --dry-run
```

The script does not push, force-push, delete pages, or change DNS.

## Doctor

Check the project contract and publish safety assumptions:

```powershell
python scripts\doctor.py
```

## New Machine Setup

Clone the source project branch into the new project root, then clone the GitHub Pages deploy branch into `bluefern-dispatches-pages` beside the source files:

```powershell
git clone <source-repo-url> "C:\Users\willb\OneDrive\Desktop\Python\Dispatches From The Blue Fern Co"
cd "C:\Users\willb\OneDrive\Desktop\Python\Dispatches From The Blue Fern Co"
git clone --branch gh-pages <pages-repo-url> bluefern-dispatches-pages
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Recreate `.env` manually on the new machine. Do not copy secrets into docs, logs, task XML, or command history.

Validate the clone before publishing:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp .pytest_tmp
.\.venv\Scripts\python.exe scripts\doctor.py
```

SMTP-only diagnostic email:

```powershell
.\.venv\Scripts\python.exe scripts\run_and_notify.py --date 2026-05-09 --smtp-debug --send-test-email
```

Expected: sends a minimal diagnostic email through the same SMTP settings used by Gaza notifications. It does not run the Gaza pipeline, run tests, publish, push, or touch the Pages repo.

Full Gaza daily run with local Pages publish and email report:

```powershell
.\.venv\Scripts\python.exe scripts\run_and_notify.py --date 2026-05-09 --publish --smtp-debug
```

If doctor reports stale Cascadia weekly public links, regenerate the affected weekly public output:

```powershell
$env:CASCADIA_ALLOW_CURL_NO_REVOKE = "1"
$env:CASCADIA_FETCH_BACKEND = "auto"
.\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --archive-week 2026-04-28 --weekly-public --historical-search --quality-weekly
```

Task Scheduler fields for this machine:

```text
Program/script:
powershell.exe

Add arguments:
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\Users\willb\OneDrive\Desktop\Python\Dispatches From The Blue Fern Co'; $env:SMTP_RELAX_X509_STRICT='1'; & '.\.venv\Scripts\python.exe' 'scripts\run_and_notify.py' --date (Get-Date -Format 'yyyy-MM-dd') --publish"

Start in:
C:\Users\willb\OneDrive\Desktop\Python\Dispatches From The Blue Fern Co
```

## GitHub Pages Repo Publishing

GitHub Pages and DNS must be configured separately. This project only prepares a deployable static site root in the local Pages repo.

The source project branch (`master` or `main`) is separate from the Pages deploy branch. The live public site deploys from the Pages repo `gh-pages` branch, so Pages repo publishing targets `gh-pages` by default.

Dry-run:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --pages-branch gh-pages
```

Copy + commit locally, no push:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --pages-branch gh-pages --commit --no-push
```

Manual push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

The publisher switches the local Pages repo to `gh-pages`, copies only `output/site/` into the Pages repo, writes `CNAME` with `dispatches.thebluefernco.com`, preserves `.git/`, and never pushes automatically.

## Site Structure

- `assets/` - shared Blue Fern/Gaza/Cascadia visual assets.
- `src/bluefern_dispatches/` - static site generator and safety checks.
- `scripts/publish_github_pages.py` - build/dry-run entrypoint.
- `output/site/` - public static site output.
- `bluefern-dispatches-pages/` - local Pages repo static root after publishing.
- `output/detail/` and `output/paid/` - reserved non-public detail roots.
- `data/dispatches/`, `data/sources/`, `data/curation/`, `data/records/` - project-scoped data roots for future ingestion and records.

## Backups

Edition backups default to:

```text
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups
```

Each edition receives a per-dispatch folder such as:

```text
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups\gaza\2026-05-03\
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups\cascadia\2026-05-03\
```

Backups include rendered HTML, source manifest, curation manifest, edition manifest, and a run manifest.

## Source Traceability

The generator enforces the project rule: public factual stories must carry source IDs that resolve to source records. Editorial/admin copy may render without external reporting only when explicitly marked as `editorial_admin_copy`.

Generated edition manifests include:

- `edition_manifest.json`
- `sources_manifest.json`
- `curation_manifest.json`

## Public vs Detail/Paid Separation

Public/free artifacts are written only under `output/site/`. Detail/paid roots are reserved under `output/detail/` and `output/paid/` and are checked so they cannot be nested inside the public site output.

Gaza remains free/public. Cascadia publishes a weekly public briefing and writes private Cascadia Signal detail packages outside `output/site/`; no paid/detail files or paths are copied into public output.

Core editorial rule: **NO FACT WITHOUT A TRACEABLE SOURCE.** Every public factual story, signal, score, trend, summary, and detail/data record must resolve back to source records through manifests or the shared dispatch data layer.

## Gaza Historical Editions

Historical Gaza editions are generated inside this project from source records. They are not imported from rendered output produced by another project.

Manual source records live at:

```text
data/dispatches/gaza/sources/YYYY-MM-DD/manual_sources.json
```

Each record must include `source_record_id`, `title`, `url`, `publisher`, `published_at`, `retrieved_at`, `summary_or_snippet`, `source_type`, `region_scope`, `category_hint`, and `reliability_tier`. Do not invent source URLs. If a historical source cannot be found or provided inside this project, omit the story.

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
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
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

Then dry-run Pages publishing:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --pages-branch gh-pages --expect-date YYYY-MM-DD
```

Copy + commit locally, no push:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --pages-branch gh-pages --expect-date YYYY-MM-DD --commit --no-push
```

Manually push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

Gaza remains fully free/public. The script writes public artifacts only under `output/site/`, mirrors edition artifacts under `output/dispatches/gaza/editions/YYYY-MM-DD/`, writes source stage files under `data/dispatches/gaza/`, updates shared records under `data/records/`, refreshes `/gaza/archive.html` and `/gaza/rss.xml`, and writes backups outside the repo under:

```text
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups\gaza\YYYY-MM-DD\
```

## Daily Gaza Workflow

Daily Gaza can be run as one command. It loads project-local manual records when present, otherwise attempts conservative RSS/source collection from `data/dispatches/gaza/sources.yml` in the default `both` mode:

```powershell
python scripts\run_daily_gaza.py --date YYYY-MM-DD
```

Default behavior creates/loads `data/dispatches/gaza/sources/YYYY-MM-DD/manual_sources.json`, generates the edition, validates traceability, runs tests, dry-runs Pages publishing, updates and commits the local Pages repo on `gh-pages`, and does not push. Gaza remains free/public, and source records are created or loaded inside this project only. Old Gaza project folders and old rendered output are not used.

Task Scheduler manual/local publish action with email report:

```text
Program/script:
powershell.exe

Arguments:
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co'; & '.\.venv\Scripts\python.exe' 'scripts\run_daily_gaza.py' --date (Get-Date -Format 'yyyy-MM-dd') --email-report"

Start in:
C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co
```

Manual push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin gh-pages
```

Optional auto-push is explicit only:

```powershell
python scripts\run_daily_gaza.py --date YYYY-MM-DD --push
```

Task Scheduler arguments with push:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co'; & '.\.venv\Scripts\python.exe' 'scripts\run_daily_gaza.py' --date (Get-Date -Format 'yyyy-MM-dd') --email-report --push"
```

The daily runner fails before publishing if sources are missing or invalid, source count is below `--min-sources`, public stories lack source IDs, manifests are empty, rendered HTML has no visible source links, tests fail without `--skip-tests`, Pages dry-run fails, or paid/detail leak checks fail. Logs are written to `logs/gaza-daily-YYYY-MM-DD.log`, and the run manifest is written to `data/dispatches/gaza/editions/YYYY-MM-DD/run_manifest.json`.

Add `--email-report` to send a plain-text Gaza run report on success or failure. Exit code `0` means the pipeline succeeded and email was sent, or email was not requested. Exit code `1` means the pipeline failed but email was sent. Exit code `2` means email was requested but could not be sent; the console summary still includes pipeline errors. The report uses `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_SSL`, `SMTP_TLS_VERIFY`, `SMTP_RELAX_X509_STRICT`, `SMTP_CA_FILE`/`SMTP_CA_BUNDLE`, `SMTP_TIMEOUT`, `SMTP_RETRIES`, `SMTP_RETRY_DELAY`, `SMTP_USER`/`SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_TO`, and `EMAIL_FROM`/`SMTP_FROM`. `SMTP_HOST` and `EMAIL_TO` are required; `SMTP_PASSWORD` is required when `SMTP_USER` or `SMTP_USERNAME` is set. Do not put SMTP passwords in scheduled task arguments.

## Cascadia Pipeline

The standalone Cascadia pipeline lives inside this project and does not read from or write to the older FDA/Cascadia media pipeline.

- Public brand: The Cascadia Briefing
- Public slug and URL: `cascadia`, `/cascadia/`
- Internal data product: Cascadia Signal
- Operating model: daily internal run, weekly public briefing, weekly private detail package
- Public format: preserve the current Blue Fern/Gaza-style dispatch page and edition structure

Source config:

```text
data/dispatches/cascadia/sources.yml
data/dispatches/cascadia/historical_sources.yml
data/dispatches/cascadia/source_registry.yml
```

Run the full Cascadia dispatch for a date:

```powershell
python scripts\run_cascadia_dispatch.py --date 2026-05-03 --all
```

Daily internal run:

```powershell
python scripts\run_cascadia_dispatch.py --date YYYY-MM-DD --daily
```

Weekly public briefing:

```powershell
python scripts\run_cascadia_dispatch.py --date 2026-05-11 --weekly-public --historical-search
```

The public Cascadia mode is weekly. It runs on Monday morning and covers the previous completed Monday-Sunday window. A run on `2026-05-11` publishes the week `2026-05-04` through `2026-05-10` using the Sunday coverage-end edition convention:

```text
/cascadia/editions/2026-05-10/
```

Historical weekly runs accept any date inside the desired week, or an explicit Monday-Sunday range:

```powershell
python scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --historical-search
python scripts\run_cascadia_dispatch.py --archive-week 2026-04-21 --weekly-public --historical-search
python scripts\run_cascadia_dispatch.py --week-start 2026-04-20 --week-end 2026-04-26 --weekly-public --historical-search
powershell -ExecutionPolicy Bypass -File scripts\run_weekly_cascadia.ps1
```

Historical search uses project-local source records, not old Cascadia/FDA project artifacts. The default historical provider mode is `all`, which runs manual supplements, curated registry sources, and GDELT in that order. Each run writes `historical_sources.json`, `historical_search_report.json`, and registry diagnostics when applicable in the same weekly source folder, preserving URLs, publishers, dates, snippets, provider IDs, source IDs, queries/feed URLs, warnings, exclusions, dedupe counts, provider counts, manual validation status, registry cache status, and a recommendation. If no qualifying records are found, the weekly public page renders a clean no-source message and does not invent stories.

### Cascadia layered free-source model

The Cascadia Briefing uses a layered free-source model. Tier 1 is official/public sources such as state agencies, emergency management, transportation, public health, environment, utilities, public safety alerts, and official press-release pages. Tier 2 is free structured search providers, currently GDELT as a broad fallback. Tier 3 is public RSS or Atom feeds from regional public-media, nonprofit, and local/regional publishers where the feed is publicly accessible. Tier 4 is the project-local `manual_sources.json` weekly supplement workflow.

No paid APIs, paid API keys, old project dependencies, old rendered prose, or invented facts are required. Every public story must trace back to a source URL. Registry feed fetches are cached under `data/dispatches/cascadia/cache/registry/`; non-feed registry entries are kept as curated source inventory and reported as skipped rather than scraped.

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

Manual Cascadia weekly supplements live at:

```text
data/dispatches/cascadia/sources/YYYY-MM-DD_YYYY-MM-DD/manual_sources.json
```

Each manual record should include `source_record_id`, `title`, `url`, `publisher`, `published_at`, `retrieved_at`, `summary_or_snippet`, `source_type: manual`, `provider_id: manual`, `region_terms_matched`, `category_hint`, `state_hint`, `reliability_tier`, and `traceability_note`. URL is required. Title is required unless the URL is clearly stable and publisher is present. Do not invent summaries; leave `summary_or_snippet` blank when the source does not provide one.

Manual supplement workflow:

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

Backfill four weeks with all providers:

```powershell
python scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --historical-search --historical-provider all
```

Report sparse source windows without publishing:

```powershell
python scripts\run_cascadia_dispatch.py --weekly-public --backfill-weeks 4 --date 2026-05-11 --source-gap-report
```

Pipeline stages:

- ingest enabled source definitions and manual fallback records
- normalize and dedupe source records
- score and curate system-relevant stories
- write shared dispatch-agnostic records under `data/records/`
- write Cascadia Signal detail package files under `output/detail/cascadia/YYYY-MM-DD/`
- render the public Cascadia edition with visible source links
- keep detail records out of `output/site/`

Key outputs:

```text
data/dispatches/cascadia/raw/YYYY-MM-DD/raw_sources.json
data/dispatches/cascadia/sources/YYYY-MM-DD_YYYY-MM-DD/historical_sources.json
data/dispatches/cascadia/sources/YYYY-MM-DD_YYYY-MM-DD/historical_search_report.json
data/dispatches/cascadia/normalized/YYYY-MM-DD/normalized_sources.json
data/dispatches/cascadia/curated/YYYY-MM-DD/curation_manifest.json
output/dispatches/cascadia/editions/YYYY-MM-DD/
output/site/cascadia/editions/YYYY-MM-DD/
output/detail/cascadia/YYYY-MM-DD/
data/records/dispatches.json
data/records/editions.json
data/records/sources.json
data/records/records.json
data/records/curation_decisions.json
data/records/detail_packages.json
```

Every public Cascadia story must include source record IDs and source URLs. Paid/detail artifacts must never be copied into `output/site/`.

Weekly Cascadia manifests include `dispatch_slug`, `public_name`, `briefing_type`, `run_date`, `edition_date`, `coverage_start`, `coverage_end`, `week_label`, `source_record_ids`, `source_urls`, `historical_search`, `providers_used`, `query_count`, `included_source_count`, and `excluded_source_count`.

The shared `data/records/` structure is dispatch-agnostic. It represents dispatches, editions, sources, story/signal records, curation decisions, and private detail packages for Gaza, Cascadia, food insecurity, political actions, healthcare access, and future briefings without adding new schema files.

Cascadia Signal detail packages currently write:

```text
output/detail/cascadia/YYYY-MM-DD/cascadia_signal_records.json
output/detail/cascadia/YYYY-MM-DD/cascadia_signal_records.csv
output/detail/cascadia/YYYY-MM-DD/cascadia_source_manifest.json
output/detail/cascadia/YYYY-MM-DD/cascadia_category_summary.json
output/detail/cascadia/YYYY-MM-DD/cascadia_category_summary.csv
output/detail/cascadia/YYYY-MM-DD/cascadia_run_manifest.json
```

## Adding Future Dispatches

Add a new dispatch configuration in `src/bluefern_dispatches/generator.py` with:

- a unique slug
- a dated edition path using `YYYY-MM-DD`
- source records for every factual public story
- curation records with inclusion/detail flags
- logo assets in `assets/`

Then run tests and dry-run publishing before writing public output.

## Scheduler & Notifications

Daily jobs:

- Gaza daily pipeline
- Cascadia daily/internal collection, if used

Weekly jobs:

- Cascadia weekly public briefing

Cascadia weekly public schedule:

- Task name: `Cascadia Weekly Briefing`
- Trigger: Weekly, Monday, 7:00 AM local time
- Coverage: previous Monday through Sunday
- Start in: `C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co`
- Keep separate from the Gaza Daily Pipeline
- Does not auto-push unless explicitly configured

Task Scheduler program:

```text
powershell.exe
```

Arguments for a weekly Cascadia run without push:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Set-Location 'C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co'; & '.\.venv\Scripts\python.exe' 'scripts\run_cascadia_dispatch.py' --date (Get-Date -Format 'yyyy-MM-dd') --weekly-public --historical-search"
```

Wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_weekly_cascadia.ps1
```

Use `scripts/run_and_notify.py` for the scheduled Gaza daily workflow and SMTP diagnostics. Normal mode delegates to `scripts/run_daily_gaza.py --email-report`; `--publish` performs the local Pages publish behavior, while omitting `--publish` runs the Gaza workflow in dry-run mode. `--send-test-email` sends only the SMTP diagnostic message and does not run Gaza, tests, publish, push, or touch the Pages repo.

## Dedicated Runner Clone

Scheduled Gaza and Food Line jobs should run from a dedicated clean runner clone, not from an active development worktree. See [docs/runner-operations.md](/c:/PythonProjects/Dispatches%20From%20The%20Blue%20Fern%20Co/docs/runner-operations.md) for:

- runner folder layout
- clean-runner setup commands
- scheduled Gaza and Food Line commands
- the safe Gaza smoke test
- dirty-runner recovery

Required environment variables:

- `SMTP_HOST` (required)
- `SMTP_PORT` (optional, defaults to `587`)
- `SMTP_USE_SSL` (optional; truthy values `1`, `true`, `yes`; port `465` also uses SMTPS)
- `SMTP_TLS_VERIFY` (optional; defaults to `true`; set `false` only for temporary diagnostics)
- `SMTP_RELAX_X509_STRICT` (optional; set `1` only for temporary diagnostics on this machine)
- `SMTP_CA_FILE` or `SMTP_CA_BUNDLE` (optional path to a PEM CA bundle used for SMTP TLS verification)
- `SMTP_TIMEOUT` (optional seconds, defaults to `30`)
- `SMTP_RETRIES` (optional retry count, defaults to `2`)
- `SMTP_RETRY_DELAY` (optional seconds between retries, defaults to `1`)
- `SMTP_DEBUG_FILE` (optional path that receives SMTP debug traces when `--smtp-debug` is used)
- `SMTP_USER` or `SMTP_USERNAME` (optional)
- `SMTP_PASSWORD` (required when `SMTP_USER` or `SMTP_USERNAME` is set)
- `EMAIL_TO` (required, comma-separated recipients)
- `EMAIL_FROM` or `SMTP_FROM` (optional, defaults to SMTP username or `noreply@<hostname>`)

Example PowerShell environment setup:

```powershell
$env:SMTP_HOST = "smtp.example.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "alerts@example.com"
$env:SMTP_PASSWORD = "replace-with-app-password"
$env:EMAIL_TO = "ops@example.com,owner@example.com"
$env:EMAIL_FROM = "dispatches-bot@example.com"
```

Local test commands:

```powershell
python -m pytest -q
python scripts\run_cascadia_dispatch.py --date 2026-05-03 --daily
python scripts\run_cascadia_dispatch.py --date 2026-05-03 --weekly-public
python scripts\run_and_notify.py --date 2026-05-09 --smtp-debug --send-test-email
python scripts\run_and_notify.py --date 2026-05-09 --publish --smtp-debug
python scripts\publish_github_pages.py --dry-run
```

SMTP troubleshooting:

- Gmail on port `587` uses STARTTLS: set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, and `SMTP_USE_SSL=0`.
- Gmail on port `465` uses SMTP over SSL: set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=465`, and `SMTP_USE_SSL=1`.
- Keep `SMTP_TLS_VERIFY` unset or `true` for normal runs. If a corporate proxy or local security product inserts a private CA, export that CA as a PEM bundle and set `SMTP_CA_FILE` or `SMTP_CA_BUNDLE` to that path.
- `certificate verify failed: self-signed certificate in certificate chain` usually means local TLS inspection is replacing Gmail's certificate, or the inspecting CA is not trusted by Python. Keep verification enabled and set `SMTP_CA_FILE` to the trusted local CA bundle if inspection is intentional.
- `SMTP_TLS_VERIFY=false`, `SMTP_SKIP_VERIFY=true`, or `SMTP_RELAX_X509_STRICT=1` disables certificate verification; use only as a short-lived diagnostic.
- SMTP passwords are read from `SMTP_PASSWORD` but are not written to SMTP debug logs.

Real SMTP integration tests are skipped by default. To enable them locally or in CI, set all required SMTP variables plus `INTEGRATION_SMTP=1`:

```powershell
$env:INTEGRATION_SMTP = "1"
python -m pytest tests\test_email_notify.py -q
```

For GitHub Actions or another CI system, store SMTP values as secrets and only export them for a dedicated integration job. Do not enable `INTEGRATION_SMTP=1` on ordinary pull request test runs unless those secrets are available and intentional.

### Windows Task Scheduler

Use the wrapper script [scripts/run_dispatches.ps1](/c:/Users/Admin/Desktop/Python/Dispatches%20From%20The%20Blue%20Fern%20Co/scripts/run_dispatches.ps1) as the scheduled action so the working directory, Python executable, credentials, logs, and exit code are handled consistently.

Recommended Task Scheduler settings:

- General: run whether user is logged on or not.
- General: use a Windows account that can read this repository, the virtualenv, the Pages repo, and Credential Manager entry.
- Actions: run `powershell.exe`, not raw Python.
- Start in: repository root.
- History: enable task history so Last Run Result can be matched to `logs\dispatches-*.log`.
- Conditions/Settings: allow a retry on failure if that matches the operational schedule.

Action program:

```text
powershell.exe
```

Action arguments:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\scripts\run_dispatches.ps1" -Publish -PagesRepo "C:\path\to\pages\repo"
```

Manual test run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_dispatches.ps1 -SmtpDebug
```

The wrapper first uses `.venv\Scripts\python.exe` when present, then falls back to `python` on `PATH`. It writes logs to `logs\dispatches-YYYYMMDD-HHMMSS.log`, keeps recent logs, and exits with the same code returned by `run_and_notify.py`.

Preferred credential setup is Windows Credential Manager with target `bluefern-smtp`. Install or import a Credential Manager helper that provides `Get-StoredCredential`, then create a credential with the SMTP username and app password. The wrapper reads it like this:

```powershell
$c = Get-StoredCredential -Target 'bluefern-smtp'
$smtp_user = $c.UserName
$smtp_pass = $c.GetNetworkCredential().Password
```

If `Get-StoredCredential` is unavailable, set `SMTP_USER` and `SMTP_PASSWORD` as user or system environment variables for the scheduled account, or use an approved secrets manager. Avoid putting SMTP credentials directly in Task Scheduler action arguments because they are easy to expose in task exports and logs.
