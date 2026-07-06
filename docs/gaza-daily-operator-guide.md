# Gaza Daily Operator Guide

This guide covers the safe daily Gaza operating sequence: check readiness, repair manual source metadata, run check-only, publish, verify live output, and handle audio-only repairs.

Use this guide when you need the current-day operator path without reconstructing steps from chat history.

## 1. Preflight State

Start by confirming both repos and the local preflight state:

```powershell
git switch add/pages-repo-default
git pull --ff-only origin add/pages-repo-default

git status --short --branch
git -C ".\bluefern-dispatches-pages" status --short --branch
python scripts\preflight_repo_state.py
```

Expected:

- Source repo clean on `add/pages-repo-default`
- Pages repo clean on `gh-pages`
- Preflight result `ok`

## 2. Daily Status Check

Run the Gaza operator status check first:

```powershell
python scripts\gaza_operator_status.py --date YYYY-MM-DD --no-live
```

Likely outcomes:

- `No action needed` means the edition is already published and verified locally.
- Manual source invalid means run the manual-source repair helper.
- Pages or audio issue means inspect the audio republish helper.
- Repo dirty or risky means clean or commit the intended source changes before proceeding.

## 3. Manual-Source Repair

Use the repair helper when `manual_sources.json` is missing traceability metadata:

```powershell
python scripts\gaza_manual_source_repair.py --date YYYY-MM-DD --check
python scripts\gaza_manual_source_repair.py --date YYYY-MM-DD --apply
python scripts\gaza_operator_status.py --date YYYY-MM-DD --no-live
```

Rules:

- `--check` is read-only.
- `--apply` only fills missing local manual-source metadata.
- Do not commit `manual_sources.json`.
- Traceability notes must remain source-safe and use only fields already present in the record.

## Gaza Command Center

Use the Gaza command center when you want one entrypoint for check, dry-run planning, publish planning, production publish, Bluesky posting, email, audio, and live verification.

By default it runs in `--test` mode. In that mode, public actions become plans only. Use `--production` only when you intend the selected write-capable or public action to run for real.

Examples:

```powershell
python scripts\gaza_command_center.py --date YYYY-MM-DD --check
python scripts\gaza_command_center.py --date YYYY-MM-DD --dry-run-full
python scripts\gaza_command_center.py --date YYYY-MM-DD --publish --test
python scripts\gaza_command_center.py --date YYYY-MM-DD --publish --production
python scripts\gaza_command_center.py --date YYYY-MM-DD --post-bluesky --production
python scripts\gaza_command_center.py --date YYYY-MM-DD --email --production
python scripts\gaza_command_center.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD --check
```

Optional planning and support flags:

- `--manual-source-check`
- `--manual-source-repair`
- `--audio-check`
- `--audio-generate`
- `--audio-publish`
- `--verify-live`
- `--json`
- `--continue-on-error`

## 4. Check-Only Gate

Run the safe check-only runner path after status and repair checks pass:

```powershell
.\scripts\run_runner_dispatch.ps1 `
  -Dispatch gaza `
  -CheckOnly
```

Expected:

- Exit code `0`
- If it fails, inspect the newest runner log before rerunning

Log command:

```powershell
Get-ChildItem .\logs\runner-gaza-*.log -File |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 |
  ForEach-Object {
    "`n===== $($_.Name) ====="
    Get-Content $_.FullName -Tail 180
  }
```

## 5. Full Publish Run

Run the full Gaza publish path only after check-only passes:

```powershell
.\scripts\run_runner_dispatch.ps1 `
  -Dispatch gaza `
  -GenerateAudio `
  -Push `
  -PostBluesky
```

Rules:

- Only run after check-only passes.
- Do not rerun blindly after a failure.
- Inspect logs to determine whether email, Pages push, audio, or Bluesky already happened.
- If Bluesky posted, do not post again.
- If email was sent, avoid duplicate email unless explicitly intended.

## 6. Post-Publish Verification

After publish, confirm the live and local state:

```powershell
python scripts\gaza_operator_status.py --date YYYY-MM-DD
```

Expected:

- Source clean
- Pages clean
- Manual sources valid
- Edition live `200`
- MP3 live `200` if generated
- Audio index links MP3
- Next safe action says no action needed

## 7. Audio-Only Repair Path

Use this path only for transcript, MP3, feed, or index repair:

```powershell
python scripts\gaza_audio_republish.py --date YYYY-MM-DD --check --no-live
python scripts\gaza_audio_republish.py --date YYYY-MM-DD --generate
python scripts\gaza_audio_republish.py --date YYYY-MM-DD --publish --commit
```

Rules:

- This path does not rerun discovery, full generation, email, or Bluesky.
- Pages can be authoritative after source-side generated output is cleaned.
- Treat `--publish` as a Pages-changing action and review before pushing.

Manual Pages push command if needed:

```powershell
git -C ".\bluefern-dispatches-pages" status --short --branch
git -C ".\bluefern-dispatches-pages" log --oneline -3
git -C ".\bluefern-dispatches-pages" push origin gh-pages
```

## 8. Cleanup After Successful Publish

After a successful publish, clean only the intended generated residue:

```powershell
git restore --source=HEAD -- output/site data/records
git clean -fd -- output logs

git status --short --branch
python scripts\preflight_repo_state.py
git -C ".\bluefern-dispatches-pages" status --short --branch
```

Warnings:

- Do not run `git add .`.
- Do not commit generated output from the source repo.
- The Pages repo is the publish output repo.

## 9. PR-Only Source Workflow

Source code and docs changes must go through PRs. Do not push directly to `add/pages-repo-default`.

Use this sequence:

```powershell
git switch -c feature/<name>
git add <specific-files-only>
git commit -m "<message>"
git push -u origin feature/<name>

gh pr create `
  --base add/pages-repo-default `
  --head feature/<name> `
  --title "<title>" `
  --body "<summary>"

gh pr view --web
gh pr checks --watch
gh pr merge --squash --delete-branch

git switch add/pages-repo-default
git pull --ff-only origin add/pages-repo-default
git status --short --branch
git -C ".\bluefern-dispatches-pages" status --short --branch
```

## 10. Failure Triage

Use this quick decision table:

- Manual source invalid -> run the manual-source repair helper.
- Source repo risky or dirty -> clean generated output or commit intended source changes through PR.
- Pages repo ahead -> push the intended Pages commit, or reset only if it was unintended.
- Pages repo dirty -> inspect before publishing.
- Tests failed -> fix the code or test path; do not rerun publish blindly.
- MP3 missing but edition live -> use the audio republish helper.
- Live 404 right after push -> check GitHub Pages deployment and cache before regenerating.

## 11. Golden Path

Use this compact daily sequence:

```powershell
python scripts\gaza_operator_status.py --date YYYY-MM-DD --no-live
python scripts\gaza_manual_source_repair.py --date YYYY-MM-DD --check

.\scripts\run_runner_dispatch.ps1 `
  -Dispatch gaza `
  -CheckOnly

.\scripts\run_runner_dispatch.ps1 `
  -Dispatch gaza `
  -GenerateAudio `
  -Push `
  -PostBluesky

python scripts\gaza_operator_status.py --date YYYY-MM-DD
```
