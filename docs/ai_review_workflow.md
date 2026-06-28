# AI Review Workflow

This repository allows optional AI assistance, but AI output is advisory until a human explicitly approves the next step.

## Supported Roles

- Codex implementation agent
- Assistant prompt/review coordinator
- GitHub Actions validation gate
- Optional AI reviewer
- Human release approver

## Operating Rules

- AI agents may assist with implementation, review, summarization, and test suggestions.
- AI agents must not be treated as release authority.
- AI agents must not publish, push, or sync Pages unless explicitly instructed.
- AI review is advisory unless the user explicitly changes the workflow.
- Human approval remains required before public release.
- GitHub Actions and publish-scope validation remain the authoritative gates.
- Dry-run success is not permission to publish.
- Pages repo sync requires explicit instruction and publish-scope validation.
- AI tools must report what they changed, what they checked, and what they intentionally did not touch.

## Codex Safe Execution Scope

Codex may reduce git and PR friction by carrying out safe mechanical source-repo steps after a scoped implementation task, but merge, publish, Pages, and editorial decisions remain human-controlled.

Safe Codex-allowed actions:

- create a feature branch from the approved base branch
- stage only explicitly named source, config, test, or documentation files
- run `git diff --cached --stat`
- run `git diff --cached --check`
- run `git diff --cached --name-only`
- verify staged files match the intended file list
- commit with a scoped commit message
- push the feature branch
- create a GitHub PR against the approved base branch
- run or watch PR checks
- open the PR in the browser with `gh pr view --web`
- after the human confirms the PR was merged, switch back to base, pull with `--ff-only`, verify the latest commit, verify source status, verify Pages repo status
- delete local and remote feature branches after merge confirmation

Human-only actions:

- merge a PR
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

Explicit instruction required:

- run discovery or backfill jobs that create candidate or review artifacts
- clean specific generated artifacts
- run dry-run publish validation
- update discovery or source configuration
- create commits and PRs
- delete feature branches after merge confirmation

Required staging rule:

- before every commit, run `git diff --cached --stat`
- before every commit, run `git diff --cached --check`
- before every commit, run `git diff --cached --name-only`
- verify the staged file list contains only intended files
- if unrelated files are staged, stop and unstage them

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

## Recommended Usage

1. Use the Codex implementation agent to make a scoped change.
2. Use an AI reviewer, if desired, to summarize risks and identify likely regressions.
3. Validate with local tests and GitHub Actions gates.
4. Resolve review comments with new commits.
5. Treat release and publish as a separate explicit step.

