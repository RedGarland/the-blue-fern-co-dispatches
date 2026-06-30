# Codex PR Workflow

This workflow is the standard source-repo path for Codex implementation work in this project. It is PR-only, source-repo only, and stops before merge unless the user explicitly asks for a post-merge cleanup step.

## Scope

- Source repo: `C:\PythonProjects\Dispatches From The Blue Fern Co`
- Base branch: `add/pages-repo-default`
- Pages repo: `C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages`
- Pages branch: `gh-pages`

## Human Boundary

- Codex does not merge PRs.
- A human reviews the PR in GitHub and clicks Merge.
- Codex may resume only after the human confirms that the PR was merged.

## Hard Prohibitions

- Do not publish unless explicitly asked.
- Do not sync Pages unless explicitly asked.
- Do not push Pages unless explicitly asked.
- Do not update Bluesky unless explicitly asked.
- Do not generate or publish audio unless explicitly asked.
- Do not commit generated artifacts.
- Do not use `git add .`.

## Keep Out Of Commits

Treat the paths below as generated or local-only unless a task explicitly says otherwise:

- `output/site/`
- `output/review/`
- `output/detail/`
- `output/paid/`
- `output/site-review-only/`
- `data/dispatches/*/candidates/`
- `data/dispatches/*/discovery/`
- `logs/`
- `cache/`
- `.pytest-temp*`
- `.env`

## Standard Source-Code PR Workflow

### 1. Start From The Approved Base

Run:

```powershell
git switch add/pages-repo-default
git pull --ff-only origin add/pages-repo-default
git status --short --branch
git -C ".\bluefern-dispatches-pages" status --short --branch
python scripts/preflight_repo_state.py
```

Expectations:

- Source repo is on `add/pages-repo-default`.
- Pages repo is on `gh-pages`.
- Pre-existing dirty files are identified before any edits.
- If Pages repo files changed unexpectedly, stop and report before continuing.

### 2. Clean Generated Artifacts Only

Clean only the generated or local validation artifacts that are explicitly in scope for the task.

Rules:

- Do not delete or revert unrelated user changes.
- Do not broad-delete output roots.
- Do not touch Pages repo files during source-repo implementation work.
- Treat generated artifact cleanup as a narrow mechanical step, not a reset.

### 3. Create A Feature Branch

Create a task-scoped feature branch from the updated base branch.

Example:

```powershell
git switch -c feature/<scoped-branch-name>
```

### 4. Make The Narrow Change

During implementation:

- Keep edits scoped to the named dispatch, date, or workflow.
- Prefer the smallest defensible change.
- Update tests and docs in the same PR when the task reveals a durable rule.
- Do not modify Pages unless the task explicitly requests a Pages step.

### 5. Run Targeted Validation First

Run the narrowest useful validation for the changed scope before broader checks.

Examples:

- targeted pytest file or test selection
- focused script dry-run
- markdown or template parse check when creating docs or workflow files

### 6. Run The Safe Suite

After targeted validation passes, run the requested broader safe suite for the task.

Typical pattern:

```powershell
.\scripts\run_pytest_safe.ps1 <task-scoped tests> -q -p no:cacheprovider
```

### 7. Run Doctor

When the task changes source logic, workflow behavior, or other project-wide assumptions, run:

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
```

### 8. Clean Validation Artifacts

After validation:

- remove task-created temp or validation artifacts that should not stay in the worktree
- leave unrelated pre-existing local artifacts alone
- verify generated output paths are not accidentally staged

### 9. Stage Explicit Files Only

Stage only the intended source, test, config, or documentation files.

Example:

```powershell
git add `
  <explicit-file-1> `
  <explicit-file-2> `
  <explicit-file-3>
```

Never use:

```powershell
git add .
```

### 10. Run Staged Diff Checks

Before commit, always run:

```powershell
git diff --cached --stat
git diff --cached --check
git diff --cached --name-only
```

Verify:

- the staged file list matches the intended files only
- no generated artifacts are staged
- no Pages repo files are staged from the source repo

If unrelated files are staged, stop and unstage them before committing.

### 11. Commit

Create one scoped commit for the task.

Example:

```powershell
git commit -m "<scoped commit message>"
```

### 12. Push The Feature Branch

Push only the feature branch:

```powershell
git push -u origin feature/<scoped-branch-name>
```

### 13. Create The Pull Request

Create a PR against `add/pages-repo-default`.

Example:

```powershell
gh pr create `
  --base add/pages-repo-default `
  --head feature/<scoped-branch-name> `
  --title "<PR title>" `
  --body "<PR body with validation results and no publish/no Pages sync statement>"
```

The PR body should include:

- scope of change
- tests and validation run
- explicit statement that no publish, no Pages sync, and no Pages push occurred

### 14. Watch Checks And Open The PR

Run:

```powershell
gh pr checks --watch
gh pr view --web
```

Then stop.

Stop condition:

- Codex stops before merge.
- Human review and merge happen in GitHub.

## Post-Merge Cleanup Workflow

Only run this after the human explicitly confirms the PR was merged.

### 1. Return To Base

```powershell
git switch add/pages-repo-default
git pull --ff-only origin add/pages-repo-default
```

### 2. Verify The Merge Landed

Confirm:

- the latest local base matches the expected merge result
- the intended PR commit or merge commit is now in `HEAD`

If a command reports a branch not merged to `HEAD`, stop immediately and report.

### 3. Verify Both Repo States

Run:

```powershell
git status --short --branch
git -C ".\bluefern-dispatches-pages" status --short --branch
python scripts/preflight_repo_state.py
```

Confirm:

- source repo status is as expected
- Pages repo status is unchanged or explicitly understood

### 4. Delete The Local Feature Branch Only If Merged To HEAD

Delete the local branch only after verifying it is merged to the updated base.

Example:

```powershell
git branch -d feature/<scoped-branch-name>
```

### 5. Delete The Remote Feature Branch

Example:

```powershell
git push origin --delete feature/<scoped-branch-name>
```

### 6. Final Status Check

Run one final verification:

```powershell
git status --short --branch
git -C ".\bluefern-dispatches-pages" status --short --branch
```

## Reusable Prompt Template

Use:

```text
Follow docs/workflows/codex_pr_workflow.md. Task: ...
```
