# Codex PR Workflow

This workflow is the standard source-repo path for Codex implementation work in this project. It is source-repo only and classifies each validated PR before merge.

## Scope

- Source repo: `C:\PythonProjects\Dispatches From The Blue Fern Co`
- Base branch: `add/pages-repo-default`
- Pages repo: `C:\PythonProjects\Dispatches From The Blue Fern Co\bluefern-dispatches-pages`
- Pages branch: `gh-pages`

## Authority Boundary

- A mechanical source merge is not editorial or publication authority.
- Codex may merge only a bounded routine source PR classified `CODEX_AUTO_MERGE_ELIGIBLE` after current-base synchronization and exact-head validation.
- A PR classified `HUMAN_MERGE_REQUIRED` stops for human merge with the exact reason reported.
- Codex must never merge a PR that expands its own authority or materially changes repository governance permissions.
- No source merge authorizes Pages activity, publication, audio, social posting, candidate approval, or source-gate relaxation.

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

### 14. Watch Checks And Classify The PR

Run:

```powershell
gh pr checks --watch
```

Then apply the merge classification below. Opening a browser is optional and is not required for an eligible routine merge.

## Merge Classification

Classify exactly one:

- `CODEX_AUTO_MERGE_ELIGIBLE`
- `HUMAN_MERGE_REQUIRED`

### CODEX_AUTO_MERGE_ELIGIBLE

All conditions are mandatory:

1. The task is authorized and bounded.
2. The PR contains only in-scope routine source-repository implementation material such as application code, tests, documentation, schemas, or non-public configuration.
3. The merge creates no editorial, approval, publication, release, correction/withdrawal, or other human decision authority and causes no consequential public side effect.
4. No Pages or public generated artifacts are committed or pushed.
5. The base is `add/pages-repo-default`.
6. Fetch the current protected base and verify the feature branch is synchronized with it. If the base advanced, synchronize safely and rerun validation on the resulting exact feature head.
7. Record the exact PR head immediately before merge and require `reviewed_head == head_immediately_before_merge`.
8. Verify the PR is open, non-draft, and mergeable/clean.
9. Verify `validate`, GitGuardian/security when present, and every other required check succeeded on that exact head.
10. Recheck the changed-file inventory against the authorized scope.
11. Require no unresolved review issue or newly discovered blocker.
12. Merge with exact-head protection and without admin, bypass, or force options:

```powershell
gh pr merge <PR_NUMBER> --merge --match-head-commit <EXACT_PR_HEAD>
```

If the protected base or PR head changed, do not merge. Re-inspect the diff, synchronize when needed, and rerun checks before taking another exact-head snapshot.

After merge, fetch the protected branch, verify the PR is merged, prove the protected result contains the exact reviewed PR head, verify source and Pages status, and only then clean up the feature branch.

### HUMAN_MERGE_REQUIRED

Stop before merge and report `READY FOR HUMAN MERGE` with the exact reason when any condition applies:

- the PR introduces or changes `approvals/**` or other editorial, approval, publication, release, correction/withdrawal, or human decision authority
- the PR records a substantive editorial decision or publication-state/story-memory release handoff
- the PR commits or pushes Pages/public generated content as a release action
- the PR changes branch protection, repository rulesets, credentials, secrets, destructive-operation authority, or consequential external-egress policy
- the PR expands or materially changes Codex/AI authority or repository governance boundaries
- the task has become behavior-changing or scope-expanding beyond its authorization
- an unresolved review issue, blocker, or other explicit human decision remains

Codex must never use `CODEX_AUTO_MERGE_ELIGIBLE` to expand its own permissions. This governance PR is therefore `HUMAN_MERGE_REQUIRED`.

## Post-Merge Cleanup Workflow

Run this after an eligible mechanical merge or after a human confirms a required human merge.

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
