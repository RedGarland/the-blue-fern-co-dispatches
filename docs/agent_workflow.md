# Agent Workflow

This repository uses a repo-centered workflow so Codex work stays scoped, traceable, and safe.

## Phase 1: Governance Files And Templates

Goal:

- Establish repository-level instructions for future Codex runs.
- Standardize issue intake and pull request reporting.
- Reduce dependence on manually pasted context.

Deliverables:

- `AGENTS.md`
- `docs/agent_workflow.md`
- `.github/ISSUE_TEMPLATE/dispatch_task.yml`
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/pull_request_template.md`

## Phase 2: GitHub Actions Validation Workflow

Goal:

- Move the standard validation checks into automation.
- Make source-scope validation repeatable in CI.
- Fail closed when traceability or publication safety is uncertain.

Expected outcomes:

- lint or parse checks for governance and templates
- targeted tests for the edited dispatch scope
- validation of review artifacts before any publish step

## Phase 3: PR-Only Publishing Discipline

Goal:

- Separate implementation from release.
- Require reviewable pull requests before public publication.
- Keep Pages publishing out of ordinary source-repo edits.
- Require `scripts/validate_publish_scope.py` before any Pages sync or public release.

Expected outcomes:

- source changes land in PRs
- publish steps happen only after explicit approval
- Pages repo changes are isolated and checked separately
- publish gates fail closed when the declared dispatch, edition date, or artifact family does not match the worktree
- dry-run success does not imply permission to publish
- audio, map, Bluesky, and Pages edits require explicit allow flags

## Phase 4: Optional AI Review / Codex GitHub Integration

Goal:

- Use Codex and GitHub together for review, triage, and workflow automation.
- Keep human approval in control of publication.
- Make AI assistance advisory, not authoritative.

Expected outcomes:

- issue templates route tasks cleanly
- PR templates capture validation evidence
- optional AI review assists without bypassing traceability rules
- issue-to-branch-to-PR stays the default path for implementation work
- bounded routine source PR merges may be mechanical after exact-head validation
- authority-bearing, governance, and public-release PRs retain a human merge boundary
- AI review comments are resolved through new commits, not manual untracked edits
- release and publish remain separate explicit steps after validation

Recommended roles:

- Codex implementation agent
- Assistant prompt/review coordinator
- GitHub Actions validation gate
- Optional AI reviewer
- Human release approver

## AI Review Operating Model

- AI agents may assist with implementation, review, summarization, and test suggestions.
- AI agents are advisory unless the user explicitly changes the workflow.
- AI agents must not be treated as release authority.
- Human approval remains required before public release.
- GitHub Actions and publish-scope validation remain the authoritative gates.
- Dry-run success is not permission to publish.
- Pages repo sync requires explicit instruction and publish-scope validation.
- AI tools must report what they changed, what they checked, and what they intentionally did not touch.
- When a task reveals a durable workflow rule or architecture principle, update the relevant project docs in the same PR instead of leaving the rule implicit in code or chat.
- Discovery work should be documented as wide intake first, strict vetting second, with aggregators treated as discovery surfaces rather than evidence sources.

## Codex Safe Execution Scope

Codex may carry out safe mechanical source-repo workflow steps after completing a scoped code, config, test, or documentation task. That includes merging a bounded routine source PR when it is classified `CODEX_AUTO_MERGE_ELIGIBLE`; editorial, approval, publication, release, and governance-expansion authority remain human-controlled.

For the reusable step-by-step PR procedure, including preflight, staging discipline, PR creation, checks, and post-merge cleanup, see `docs/workflows/codex_pr_workflow.md`.

Safe Codex-allowed actions:

- create a feature branch from the approved base branch
- stage only explicitly named source, config, test, or documentation files
- run `git diff --cached --stat`
- run `git diff --cached --check`
- run `git diff --cached --name-only`
- verify staged files match the intended file list
- commit with a scoped commit message
- push the feature branch
- create a GitHub pull request against the approved base branch
- run or watch PR checks
- open the PR in the browser with `gh pr view --web`
- fetch and synchronize with the current protected base before merge
- record and recheck the exact PR head immediately before merge
- merge an eligible PR only after scope, mergeability, and all required checks are proven on that exact head
- after any merge, fetch the protected branch and prove it contains the reviewed PR head before cleanup
- delete local and remote feature branches only after merge confirmation

Human-merge-required actions:

- merge a PR that creates or changes editorial, approval, publication, release, correction/withdrawal, or other human decision authority
- merge a PR that changes Codex/AI authority, repository governance boundaries, branch protection, rulesets, credentials, secrets, destructive-operation authority, or consequential external-egress policy
- merge a PR that commits or pushes Pages/public generated release content or records publication-state/story-memory release authority
- publish public editions
- sync, commit, or push the Pages repo
- post to Bluesky or other social platforms
- create or replace podcast, audio, or other public publication artifacts for release
- decide that a candidate is source-backed enough for public publication
- relax source eligibility gates
- alter editorial standards
- commit generated public output unless explicitly instructed
- use `git add .`
- delete broad generated folders without explicit instruction

Codex must never use routine merge permission to expand its own permissions. A routine source merge does not authorize publication, Pages activity, audio, social posting, editorial approval, candidate approval, or source-gate relaxation.

Safe only with explicit instruction:

- run discovery or backfill jobs that create candidate or review artifacts
- clean specific generated artifacts
- run dry-run publish validation
- update discovery or source configuration
- create commits and PRs
- delete feature branches after merge confirmation

Required staging rule before every commit:

- run `git diff --cached --stat`
- run `git diff --cached --check`
- run `git diff --cached --name-only`
- verify the staged file list contains only the intended files
- if unrelated files are staged, stop and unstage them before committing

Default safe Codex PR flow:

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

## Intended Development Process

Routine path:

1. Task is authorized and bounded.
2. Codex implements, validates, commits, and opens the source PR.
3. Codex fetches the current protected base and synchronizes the feature branch if needed.
4. Codex records the exact PR head and proves scope, open/non-draft state, mergeability, and required checks on that head.
5. Codex merges with exact-head protection and performs post-merge verification.

Authority-bearing path:

1. Task is implemented and validated in an isolated PR.
2. Codex proves exact-head checks and reports why the PR is `HUMAN_MERGE_REQUIRED`.
3. A human merges the PR.
4. Codex performs post-merge verification when requested.

In both paths, commits stay isolated to one task or release step, publishing remains separate from implementation, and Pages changes happen only when explicitly authorized.

## Human-error Risks This Workflow Is Designed To Reduce

- stale source leakage
- future edition publication
- old label reintroduction
- accidental Pages sync
- unrelated dirty files in commits
- source-wrapper URLs replacing original source URLs
- generated artifact drift
- accidental publish of the wrong dispatch family or edition date
- release-step leakage from implementation work
- audio/podcast/archive mismatch
- manual prompt context loss

## Operating Rules

- Keep edits scoped to the named dispatch or workflow.
- Verify staged files before committing.
- Do not publish or push unless explicitly requested.
- Never assume dry-run output is publishable without inspecting review output and logs.
- Clearly separate pre-existing dirty files from task-created files.
- Prefer traceable, source-backed changes over broad edits.
