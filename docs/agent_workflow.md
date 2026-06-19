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

Expected outcomes:

- source changes land in PRs
- publish steps happen only after explicit approval
- Pages repo changes are isolated and checked separately

## Phase 4: Optional AI Review / Codex GitHub Integration

Goal:

- Use Codex and GitHub together for review, triage, and workflow automation.
- Keep human approval in control of publication.

Expected outcomes:

- issue templates route tasks cleanly
- PR templates capture validation evidence
- optional AI review assists without bypassing traceability rules

## Intended Development Process

1. User describes a task or opens an issue.
2. Assistant drafts a precise Codex prompt with scope, constraints, and validation requirements.
3. Codex implements the requested change in the source repo.
4. Codex runs focused tests and validation.
5. Codex reports what changed, what was tested, and what was intentionally not touched.
6. User reviews the result and sends follow-up instructions if needed.
7. Assistant decides the next prompt or next action.
8. Commits stay isolated to one task or one release step.
9. Publishing is separate from implementation.
10. Pages repo changes happen only when explicitly requested.

## Human-error Risks This Workflow Is Designed To Reduce

- stale source leakage
- future edition publication
- old label reintroduction
- accidental Pages sync
- unrelated dirty files in commits
- source-wrapper URLs replacing original source URLs
- generated artifact drift
- audio/podcast/archive mismatch
- manual prompt context loss

## Operating Rules

- Keep edits scoped to the named dispatch or workflow.
- Verify staged files before committing.
- Do not publish or push unless explicitly requested.
- Never assume dry-run output is publishable without inspecting review output and logs.
- Clearly separate pre-existing dirty files from task-created files.
- Prefer traceable, source-backed changes over broad edits.

