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

Repository-wide Codex / implementation-agent authority is defined by `AGENTS.md`. This document does not independently expand or narrow that authority.

For the reusable mechanical PR procedure, use `docs/workflows/codex_pr_workflow.md`. Routine source-repository engineering may proceed through the bounded workflow permitted by `AGENTS.md`; public publication, Pages/public release, editorial decisions, credentials/security changes, destructive operations, and other explicit human authority boundaries remain separately controlled.

The autonomous Blue Fern Operator is governed independently by `ops/operator/remediation-policy.yaml`; Codex routine PR authority must not be inferred as Operator merge or publication authority.
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
