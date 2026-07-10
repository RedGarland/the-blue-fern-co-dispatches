# Runner Operations

Use a dedicated clean runner clone for scheduled Gaza and Food Line jobs. Do not point scheduled tasks at an active development worktree.

For the day-to-day Gaza operator sequence, see [docs/gaza-daily-operator-guide.md](./gaza-daily-operator-guide.md).

## Layout

Recommended Windows layout:

```text
C:\BlueFernRunner\
  Dispatches From The Blue Fern Co\            # runner source repo
  Dispatches From The Blue Fern Co\bluefern-dispatches-pages\  # runner Pages repo
```

Development layout stays separate, for example:

```text
C:\PythonProjects\Dispatches From The Blue Fern Co\            # dev source repo
C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages\  # dev Pages repo
```

Rules:

- The runner source repo tracks `add/pages-repo-default`.
- The runner Pages repo tracks `gh-pages`.
- Scheduled jobs run only from the runner clone.
- Development artifacts in the dev repo must never block the runner job.
- `REPO_DIRTY_BLOCKED` remains enforced in the runner clone.

## Setup

Example setup commands for a dedicated runner clone:

```powershell
New-Item -ItemType Directory -Force -Path C:\BlueFernRunner | Out-Null
git clone --branch add/pages-repo-default <SOURCE_REPO_URL> "C:\BlueFernRunner\Dispatches From The Blue Fern Co"
git clone --branch gh-pages <PAGES_REPO_URL> "C:\BlueFernRunner\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
Set-Location "C:\BlueFernRunner\Dispatches From The Blue Fern Co"
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .
```

Verify tracking:

```powershell
git -C "C:\BlueFernRunner\Dispatches From The Blue Fern Co" branch --show-current
git -C "C:\BlueFernRunner\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" branch --show-current
```

Expected output:

- source repo: `add/pages-repo-default`
- Pages repo: `gh-pages`

## Scheduled Commands

Recommended Task Scheduler action for Gaza:

```text
Program/script:
powershell.exe
```

```text
Arguments:
-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\Dispatches From The Blue Fern Co\scripts\run_runner_dispatch.ps1" -Dispatch gaza -Push -PostBluesky -GenerateAudio
```

The wrapper now derives `RepoRoot` from the wrapper location by default, so the scheduled task does not need to hard-code the development path. If you need to override that behavior for a one-off run, `-RepoRoot` is still available and takes precedence.

The recommended Gaza command above is the live runner form used on the dedicated runner clone. The wrapper still sends the email report by default and now only pushes Pages, posts to Bluesky, or generates Gaza audio when those switches are explicitly added.

Gaza audio generation uses the supported `openai` TTS provider. The scheduled account must have `OPENAI_API_KEY` available in its environment, and any direct `scripts\run_daily_gaza.py` invocation must pass `--tts-provider openai` alongside `--generate-audio`. Do not place the API key in Task Scheduler arguments or logs.

If `--generate-audio` is requested while the provider remains `none`, the daily runner now fails closed with `audio-generation-failed` instead of silently continuing.

Gaza publishes also preserve existing public-history dates on the archive, RSS, and audio listing/feed surfaces by default. If a reviewed archival pruning is intentional, add `--allow-listing-shrink` explicitly and inspect the resulting diff before any push.

Explicit live Gaza command:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\Dispatches From The Blue Fern Co\scripts\run_runner_dispatch.ps1" -Dispatch gaza -Push -PostBluesky
```

Add `-GenerateAudio` only when the scheduled Gaza run should also create dated audio artifacts.

Recommended Task Scheduler action for Food Line:

```text
Program/script:
powershell.exe
```

```text
Arguments:
-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\BlueFernRunner\Dispatches From The Blue Fern Co\scripts\run_runner_dispatch.ps1" -Dispatch food-line -RepoRoot "C:\BlueFernRunner\Dispatches From The Blue Fern Co"
```

Wrapper behavior:

1. Sync source repo to `origin/add/pages-repo-default`.
2. Sync Pages repo to `origin/gh-pages`.
3. Run `python scripts\preflight_repo_state.py` logic through `scripts\runner_repo_maintenance.py sync`.
4. Fail early if either repo is dirty or on the wrong branch.
5. Run the dispatch command from the clean runner clone.
6. Re-check both repos after the run.
7. Clean only approved generated/temp paths in the source repo.
8. Fail if risky drift remains.

## Smoke Test

Use this to verify tomorrow's Gaza runner state without publish, email, audio, Pages updates, or Bluesky:

```powershell
python scripts\smoke_gaza_operator.py --date YYYY-MM-DD
```

Example:

```powershell
python scripts\smoke_gaza_operator.py --date 2026-07-02
```

Smoke-test scope:

- syncs the runner source repo and runner Pages repo
- runs repo preflight after sync
- checks branch expectations
- resolves the requested date
- runs `run_gaza_daily_operator.py --manual-source-check-only`
- runs postflight drift classification and approved cleanup

The smoke test intentionally does not run the full Gaza dry-run path because the current daily/operator dry-run path still writes dated artifacts.

## Recovery

If the runner clone is dirty before a scheduled run:

1. Inspect both repos:

```powershell
git -C "C:\BlueFernRunner\Dispatches From The Blue Fern Co" status --short --branch
git -C "C:\BlueFernRunner\Dispatches From The Blue Fern Co\bluefern-dispatches-pages" status --short --branch
python "C:\BlueFernRunner\Dispatches From The Blue Fern Co\scripts\preflight_repo_state.py" --source-repo "C:\BlueFernRunner\Dispatches From The Blue Fern Co" --pages-repo "C:\BlueFernRunner\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
```

2. If drift is limited to approved generated/temp paths, run:

```powershell
python "C:\BlueFernRunner\Dispatches From The Blue Fern Co\scripts\runner_repo_maintenance.py" postflight --source-repo "C:\BlueFernRunner\Dispatches From The Blue Fern Co" --pages-repo "C:\BlueFernRunner\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
```

3. If source or data drift remains under `src/`, `scripts/`, `docs/`, `data/`, or other non-approved paths, stop and inspect manually. Do not add new ignore rules to silence it.

4. If the runner clone is confused or repeatedly dirty, rebuild it:

```powershell
Remove-Item -LiteralPath "C:\BlueFernRunner\Dispatches From The Blue Fern Co" -Recurse -Force
git clone --branch add/pages-repo-default <SOURCE_REPO_URL> "C:\BlueFernRunner\Dispatches From The Blue Fern Co"
git clone --branch gh-pages <PAGES_REPO_URL> "C:\BlueFernRunner\Dispatches From The Blue Fern Co\bluefern-dispatches-pages"
```

## Cleanup Policy

The runner postflight cleanup is intentionally narrow.

It may restore or delete only these source-repo path families:

- `logs/`
- `.pytest_cache/`
- `.pytest-temp*`
- `.pytest_tmp*`
- `output/review/`
- `output/site/`
- `output/dispatches/`
- `output/tmp-backups-pages/`

It does not auto-clean:

- `src/`
- `scripts/`
- `docs/`
- `data/`
- `bluefern-dispatches-pages/`

That keeps normal temp/generated noise out of the runner clone without masking real source or data drift.
