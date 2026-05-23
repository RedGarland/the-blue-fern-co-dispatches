# Next Two-Week Implementation Plan

## Current system assessment (as of 2026-05-23)

- Governance/safety baseline is strong: contract docs are explicit, Pages branch/CNAME checks exist, and public/detail separation is enforced in tooling.
- Current branch is `add/pages-repo-default`; source repo status matches expected local-only dirt pattern (`.env`, `.tmp`, temp pytest/output junk).
- `dispatches_status.py --json` reports overall `ok: true` and source repo tracking `up-to-date`.
- Doctor/status tooling is already the practical safety gate, but it is split between `doctor.py` and `dispatches_status.py` with partially overlapping checks.

### Product health snapshot

- American Pressure:
  - `latest_public_edition_date: null`
  - `archive_exists: false`, `rss_exists: false`
  - Latest Pages date exists (`2026-05-16`) but public output chain is not complete as a stable weekly product.
- Cascadia:
  - Weekly format is present, but stability signals show high warning noise (`weak_date_warning_count`, registry/GDELT fetch issues).
  - Recommended next action from status already points to dead-source cleanup and warning quality.
- Gaza:
  - Daily pipeline safeguards are mature, but there is public recency drift (`latest_public_edition_date` behind recent run date).
  - Reliability warning indicates provider failures should be normalized into expected/triaged behavior rather than recurring operator noise.

## Product priorities (next 14 days)

1. American Pressure weekly maturity to stable, publishable baseline.
2. Cascadia weekly stability with simpler, clearer map/source behavior.
3. Gaza daily reliability hardening without net feature expansion.
4. Doctor/status consolidation as the release gate.
5. Public/private artifact boundary clarity and enforceability.

## Engineering priorities

- Make one authoritative pre-publish gate command for operators.
- Reduce false-positive warning volume (especially Cascadia source failures and weak-date noise).
- Add deterministic checks for edition recency consistency across manifests/archive/rss/pages.
- Tighten artifact hygiene guardrails for runtime temp outputs.

## Editorial/source priorities

- American Pressure: ensure required weekly pillars can be covered with approved candidate + manual source flow, with no internal labels leaking publicly.
- Cascadia: prioritize source quality and place precision over source count growth; remove/disable obviously dead or non-actionable registry sources.
- Gaza: maintain strict source traceability while preventing publication lag between generated editions and public-linked editions.

## Publish/ops priorities

- Treat publish as two-step always: local Pages update + explicit push.
- Promote `dispatches_status.py` + `doctor.py` into a standard preflight/postflight checklist for every weekly/daily run.
- Keep local-only artifacts local (`.tmp`, candidates/backfill intake, temp outputs).

## Risks

- American Pressure appears partially generated but not fully publicized; risk is operator confusion and inconsistent weekly expectations.
- Cascadia warning noise can mask real regressions if not triaged by severity/actionability.
- Gaza provider failures may continue to create recurring warnings even when output is valid, reducing trust in alerts.
- Divergent logic between status and doctor can create pass/fail disagreement.

## What not to build yet

- No new paid/private product surfaces under public routes.
- No map UI expansion (time slider/playback/accumulation layers) until weekly source reliability baseline is stable.
- No new multi-product orchestration UI features before gate unification and reliability cleanup.
- No broad ingestion/provider expansion until dead/unstable sources are triaged.

## Concrete task list ordered by value

1. Unify release gate: define and adopt one primary gate command (`doctor` + status strict profile).
- Why now: Highest leverage for preventing bad publishes across all products.
- Implementation:
  - Add a documented gate wrapper script (for example `scripts/release_gate.py`) or equivalent single-command flow.
  - Reconcile duplicate/overlapping checks between `scripts/doctor.py` and `scripts/dispatches_status.py`.
  - Ensure gate emits explicit block/warn/info categories and clear operator action text.
- Recommended checks:
  - `\.venv\Scripts\python.exe scripts\doctor.py`
  - `\.venv\Scripts\python.exe scripts\dispatches_status.py --strict --run-doctor`
  - Confirm non-zero exit on intentional failure fixtures.

2. Complete American Pressure weekly public chain stability.
- Why now: It is the largest visible product maturity gap.
- Implementation:
  - Ensure weekly run always produces coherent public edition + archive + RSS + pages alignment.
  - Add/strengthen checks that block publish when latest public edition metadata is missing or inconsistent.
  - Validate that public mini-brief prose sections match contract language and internal labels never leak.
- Recommended checks:
  - `\.venv\Scripts\python.exe scripts\check_american_pressure_weekly_readiness.py --date YYYY-MM-DD`
  - `\.venv\Scripts\python.exe scripts\run_weekly_american_pressure.py --date YYYY-MM-DD --source-mode both --include-approved-candidates`
  - `\.venv\Scripts\python.exe scripts\dispatches_status.py --json` and verify AP `latest_public_edition_date`, `archive_exists`, `rss_exists`.

3. Cascadia stability pass: dead-source triage + warning simplification.
- Why now: Current noise reduces operational trust and map simplicity.
- Implementation:
  - Run and review reliability audit output; classify sources by disable/manual/keep.
  - Deprioritize or disable repeatedly dead sources (persistent 404/dns failures) per existing diagnostics model.
  - Collapse repetitive weak-date warnings into summarized, source-level diagnostics.
- Recommended checks:
  - `\.venv\Scripts\python.exe scripts\dispatches_status.py --write-cascadia-audit --json`
  - `\.venv\Scripts\python.exe scripts\run_cascadia_dispatch.py --weekly-public --quality-weekly --date YYYY-MM-DD --historical-search --historical-provider all`
  - Verify lower `registry_fetch_error_count` / `weak_date_warning_count` trend in status output.

4. Gaza daily reliability hardening (no feature expansion).
- Why now: Daily product needs predictable recency and low-noise failures.
- Implementation:
  - Add explicit recency/link consistency check: latest generated eligible edition should match latest publicly linked edition unless blocked by a documented condition.
  - Reclassify expected provider limitations into diagnostics-only where appropriate to avoid recurring high-signal warnings.
  - Preserve strict no-source-no-publish behavior.
- Recommended checks:
  - `\.venv\Scripts\python.exe scripts\run_daily_gaza.py --date YYYY-MM-DD --dry-run`
  - `\.venv\Scripts\python.exe scripts\dispatches_status.py --json` and inspect Gaza `stale_or_unlinked_edition_dates`, provider failure reporting, and public linked edition dates.
  - `\.venv\Scripts\python.exe scripts\doctor.py`

5. Public/private artifact boundary enforcement hardening.
- Why now: Prevents accidental exposure and keeps future paid/private paths safe.
- Implementation:
  - Expand checks for accidental publication of non-public roots and temp outputs beyond current `detail/paid` checks.
  - Add guardrails for `output/tmp` and similar runtime folders to stay untracked/unpublished.
  - Update docs with explicit artifact classes: public, durable internal, local ephemeral.
- Recommended checks:
  - `\.venv\Scripts\python.exe scripts\doctor.py`
  - `\.venv\Scripts\python.exe scripts\dispatches_status.py --json`
  - `git status --short` review to confirm only intended tracked files.

## Suggested two-week sequencing

### Week 1 (Safety + baseline reliability)

1. Task 1: unified gate command and severity model.
2. Task 3: Cascadia dead-source and warning-noise triage.
3. Task 4: Gaza recency/link consistency check.

### Week 2 (Product completion + policy hardening)

1. Task 2: American Pressure weekly public chain completion and blocking checks.
2. Task 5: artifact boundary hardening and docs update.
3. End-of-week regression pass across all three products using unified gate.

## Definition of done for this plan window

- One documented, deterministic gate command is adopted for pre-publish checks.
- American Pressure reports non-null latest public edition and valid archive/RSS in status.
- Cascadia warning volume is reduced and more actionable without hiding real failures.
- Gaza daily runs preserve strict reliability while reducing non-actionable warning churn.
- Public/private/local artifact boundaries are enforced by both code and docs.
