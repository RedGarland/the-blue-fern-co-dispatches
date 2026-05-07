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

## GitHub Pages Repo Publishing

GitHub Pages and DNS must be configured separately. This project only prepares a deployable static site root in the local Pages repo.

Dry-run:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
```

Copy + commit locally, no push:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches/" --commit --no-push
```

Manual push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git remote -v
git push origin main
```

The publisher copies only `output/site/` into the Pages repo, writes `CNAME` with `dispatches.thebluefernco.com`, preserves `.git/`, and never pushes automatically.

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

Generate a historical Gaza edition:

```powershell
python scripts\run_gaza_dispatch.py --date YYYY-MM-DD --historical --from-manual-sources --all
```

Review:

```text
output/site/gaza/editions/YYYY-MM-DD/index.html
```

Then dry-run Pages publishing:

```powershell
python scripts\publish_github_pages.py --dry-run --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
```

Copy + commit locally, no push:

```powershell
python scripts\publish_github_pages.py --pages-repo "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" --remote-url "https://github.com/RedGarland/the-blue-fern-co-dispatches.git" --commit --no-push
```

Manually push after inspection:

```powershell
cd "C:\Users\Admin\Desktop\Python\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
git status
git push origin main
```

Gaza remains fully free/public. The script writes public artifacts only under `output/site/`, mirrors edition artifacts under `output/dispatches/gaza/editions/YYYY-MM-DD/`, writes source stage files under `data/dispatches/gaza/`, updates shared records under `data/records/`, refreshes `/gaza/archive.html` and `/gaza/rss.xml`, and writes backups outside the repo under:

```text
C:\Users\Admin\Desktop\Python\dispatches-bluefern-backups\gaza\YYYY-MM-DD\
```

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
python scripts\run_cascadia_dispatch.py --date YYYY-MM-DD --weekly-public
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

Use `scripts/run_and_notify.py` to run the Cascadia pipeline and optionally publish, then always send an email report. The script exits with `0` when the pipeline and email succeed, `1` when the pipeline or publish step fails but the email report was sent, and `2` when the email report cannot be sent.

Required environment variables:

- `SMTP_HOST` (required)
- `SMTP_PORT` (optional, defaults to `587`)
- `SMTP_USE_SSL` (optional; truthy values `1`, `true`, `yes`; port `465` also uses SMTPS)
- `SMTP_TIMEOUT` (optional seconds, defaults to `30`)
- `SMTP_RETRIES` (optional retry count, defaults to `2`)
- `SMTP_RETRY_DELAY` (optional seconds between retries, defaults to `1`)
- `SMTP_DEBUG_FILE` (optional path that receives SMTP debug traces when `--smtp-debug` is used)
- `SMTP_USER` (optional)
- `SMTP_PASSWORD` (required when `SMTP_USER` is set)
- `EMAIL_TO` (required, comma-separated recipients)
- `EMAIL_FROM` (optional, defaults to `SMTP_USER` or `noreply@<hostname>`)

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
python scripts\run_and_notify.py --date 2026-05-04 --publish --pages-repo "C:\path\to\pages\repo"
python scripts\run_and_notify.py --date 2026-05-04 --smtp-debug
python scripts\publish_github_pages.py --dry-run
```

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
