# Pages Publish Safety

Source repo: `C:\PythonProjects\Dispatches From The Blue Fern Co`

Pages repo: `C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages`

Rules:

- Do not run `git add .` in source repo.
- Do not commit `.env`, logs, `output/detail`, `output/paid`, test temp dirs, or broad generated artifacts.
- Publish push must happen only from `bluefern-dispatches-pages`.
- Pages branch must be `gh-pages`.

- Local publish behavior: the publisher copies the generated `output/site` files into the `bluefern-dispatches-pages` repository and creates a local commit by default. Pushing those commits to the remote is an explicit, separate step (the publisher skips push unless invoked with an explicit push option).
- Gaza publishes now fail closed if the new build would drop existing public-history dates from `gaza/archive.html`, `gaza/rss.xml`, `gaza/audio/index.html`, `gaza/audio/podcast.xml`, or `gaza/podcast.xml`. Use `--allow-listing-shrink` only for a deliberate, reviewed archival pruning operation.
- To publish live from this machine, either run the dispatch runner with its `--push` flag (for example `scripts\run_daily_gaza.py --push`) or run `git push origin gh-pages` from inside the `bluefern-dispatches-pages` repo. Do not push the Pages branch from the source repo.

## Codex Safe Execution Scope

Codex may prepare source-repo pull requests, but Codex must not make Pages, publish, or merge decisions.

Codex may:

- create and push a source-repo feature branch
- stage only explicitly named source, config, test, or documentation files
- verify staging with `git diff --cached --stat`, `git diff --cached --check`, and `git diff --cached --name-only`
- create a source-repo commit with a scoped message
- create a PR against the approved base branch
- watch PR checks and open the PR in the browser
- after the human confirms merge, switch back to base, pull with `--ff-only`, verify the latest commit, verify source repo status, and verify Pages repo status
- delete local and remote feature branches only after merge confirmation

Codex must not:

- merge a PR
- sync, commit, or push the Pages repo
- treat dry-run publish validation as permission to publish
- commit generated public output unless explicitly instructed
- use `git add .`
- delete broad generated folders without explicit instruction

Explicit instruction remains required for:

- dry-run publish validation
- cleaning specific generated artifacts
- any publish or Pages-sync step

Default safe source-repo PR flow:

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

Quick status command:

```powershell
.\.venv\Scripts\python.exe scripts\status_pages_repo.py
```
