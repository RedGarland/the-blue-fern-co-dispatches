# AGENTS.md

## Required Docs

Read these before making project changes:

- `README.md`
- `PROJECT_SUMMARY.md`
- `docs/dispatches-project.md`
- `docs/project-contract.md`
- `docs/pages-publish-safety.md`

## Project Purpose

Dispatches From The Blue Fern Co. is a source-based public dispatch system with traceable reporting and reproducible static output.

## Prime Directive

All dispatch outputs must be source-traceable, date-safe, and publication-safe.

## Autonomous Working Authority

For this repository, absence of additional operator input is permission to continue through all safe, reversible, non-destructive work required to complete the current authorized objective. Do not stop merely because an intermediate step is complete.

Proceed autonomously through routine work including:

- diagnosis and root-cause analysis
- implementation and refactoring
- tests and validation
- correction of routine test and CI failures
- documentation
- branch creation and commits
- normal source feature-branch pushes required for authorized PR work
- pull-request creation and ordinary PR remediation
- bounded routine protected-branch merge when the merge rules below permit it
- guarded production-runner synchronization
- non-public production proof
- operational-status refresh
- evidence and receipt generation
- directly related follow-on remediation needed to complete the objective

When a technically stronger safe approach becomes evident, take it rather than asking the operator to choose among routine implementation options.

### Stop Only At Genuine Authority Boundaries

Require operator input only for:

- destructive or irreversible actions
- force push, destructive reset/clean, or evidence loss
- credentials, secrets, or security changes
- persistent host/network configuration changes
- material infrastructure cost
- public-content publication when explicit approval is required
- editorial-policy changes
- source removal or substitution that materially changes coverage
- materially ambiguous evidence where available choices have materially different consequences

Do not request approval for routine code changes, testing, CI corrections, documentation, authorized PR mechanics, permitted routine merges, guarded synchronization, reversible remediation, or non-public validation.

### Completion Rule

Continue until either:

1. the objective is implemented, deployed where applicable, and proven at the strongest safe level available; or
2. a genuine authority boundary above is reached.

A locally working change or merged PR alone is not completion when deployment or production proof is part of the objective. If one part is blocked by an authority boundary, complete every independent safe step before escalating.

When escalation is genuinely required, report:

- what has been completed
- current evidence
- exact blocker
- why it crosses an authority boundary
- smallest decision or authorization required

### Authority Sources

- `AGENTS.md` governs Codex and implementation-agent behavior.
- `ops/operator/remediation-policy.yaml` governs the autonomous Blue Fern Operator's machine-executable remediation actions.
- Existing project contracts govern detailed production, editorial, source-traceability, and publishing invariants.

Codex routine PR merge authority is not Operator merge authority. The Operator must keep `MERGE_PR` approval-gated unless its own architecture, policy, and tests explicitly prove a narrower safe machine-merge path. Codex must not use routine merge permission to expand its own authority or to expand Operator authority.

## Production Readiness

Read `docs/production-readiness-contract.md` before any production-change task.

- Do not equate tests, merges, clean runners, or dry-runs with production readiness.
- Scheduled systems require actual task-service proof against the protected runtime.
- Every production filesystem path must be verified after the final protected sync.
- Runtime state must be explicitly classified instead of being treated as generic dirtiness.
- Observability is a readiness requirement, not a nice-to-have.
- Never deploy an unmerged fix directly to a production runner.
- Use precise intermediate status language until every applicable readiness layer is proven.
- Be conservative about scope, not iteration.
- Optimize for production integrity, not task completion.

## Required Behavior Before Editing

- Inspect the current worktree with `git status --short`.
- Identify the exact dispatch, edition date, or workflow scope requested by the user.
- Read only the files needed for that scope.
- Do not assume dry-run output is publishable without checking review output and logs.
- Do not touch unrelated dirty files.
- Do not modify generated/public artifacts unless the task explicitly requires it.
- Do not edit Pages repo output from a source-repo task unless explicitly requested.
- When a bounded repair is explicitly authorized, complete the mechanical path end-to-end without repeatedly stopping for obvious local dependencies, then report only after diagnosis, fix, validation, and rerun are complete.
- Stop early only for behavior-changing, destructive, out-of-scope, credential, or consequential external-egress decisions that are not already established by the current contract.
- A production failure alone does not authorize redesign; restore the intended state before improving anything.
- Keep production runners clean and pinned to verified commits and verified environment setup.
- Do not make the user relay routine intermediate debugging when the next step is mechanically clear.

## Required Behavior After Editing

- Run the narrowest useful validation first.
- Verify any staged files before committing.
- Separate source changes from generated Pages-repo changes.
- Run `scripts/validate_publish_scope.py` before any publish, release, or Pages-sync task.
- Treat dry-run success as a check, not permission to publish.
- Require explicit allow flags for Pages sync, audio, map, and Bluesky artifacts.
- If acting as an implementation agent, do not self-approve your own work.
- If acting as a reviewer, do not make unrelated edits.
- If asked to review, focus on source traceability, stale-source leakage, future-edition leakage, generated artifact drift, Pages sync safety, audio/transcript/podcast consistency, map and Bluesky gating, and unrelated dirty files.
- Always distinguish implementation findings from release/publish readiness.
- Never infer publish permission from PR approval, test success, or dry-run success.
- If public output changed, verify the rendered paths and confirm `output/detail` and `output/paid` are not exposed under `output/site`.
- Report clearly whether the task is complete, blocked, or needs follow-up.
- If a clean production runner is being provisioned, prefer a reusable environment-only mechanism that works for future clean runners instead of hard-coding a one-off path.
- Preserve the dirty development worktree unless the user explicitly authorizes edits there for the current task.

## Mandatory Git Preflight

- Before coding, run `git status --short --branch` in the source repo.
- If a sibling or nested Pages repo exists, inspect its `git status --short --branch` too.
- Run `scripts/preflight_repo_state.py` before making changes when Git state clarity matters.
- Treat any source, test, doc, public output, or unknown dirty path as risky until explicitly reviewed.
- Treat review output, logs, cache, and virtualenv paths as local friction signals, not as proof that the worktree is safe.
- Do not assume ignored files are harmless if they sit beside tracked source or public output.
- Report the current dirty-state split between risky files and allowed local/generated files before editing when the task begins with repo hygiene or drift reduction.

## Dispatch-Specific Rules

### Gaza

- Gaza is daily, public, and free.
- Gaza content must remain source-backed and traceable.
- Do not introduce future-dated or stale public stories.
- Do not expose private or detail-only artifacts in public output.
- Audio, transcript, podcast XML, and flash briefing artifacts must stay consistent with the edition date and source records.

### Food Line

- Food Line is a pressure dispatch, not a resource map.
- Distinguish pressure signals from resource-only stories.
- Do not allow stale, background, context-only, or resource-only sources into current-story sections.
- Preserve source tables, claim ledgers, manifests, cleaned excerpts, and source traceability.
- Prefer original publisher/article/report URLs over wrapper, redirect, or search-result URLs when available.
- Never publish a Food Line edition without inspecting the review output and logs.

### Care Line

- Care Line must follow the same source-traceability and pressure-signal discipline as Food Line.
- Do not let wrapper-like source rows, marketing pages, or untraceable snippets become public claims.
- Public claims must support healthcare-access pressure, not just a general healthcare story.

### Cascadia

- Cascadia is intentionally inactive. Do not register, enable, repair,
  reprovision, or execute a Cascadia production runner without explicit
  operator authorization.
- Cascadia remains separate from Gaza, Food Line, Care Line, and American Pressure.
- Keep weekly public output distinct from private detail packages.
- Never copy detail-only records into public site output.
- Preserve source IDs, source URLs, and coverage windows in manifests.

### American Pressure

- Keep intake/review artifacts separate from durable source records.
- Do not merge unapproved candidates into public weekly output.
- Treat source selection, story selection, and publishing as separate steps.

### Sitewide and Generated Artifacts

- Keep generated output reproducible and traceable.
- Do not add `output/detail` or `output/paid` content to public site output.
- Do not publish or push public/generated output unless explicitly requested.
- Routine source feature-branch pushes for an explicitly requested standard PR
  workflow are governed by the Pull Request Merge Authority and Publishing And
  Pushing sections below.
- Do not assume a clean `git status` means the live site changed.
- When public output changes, verify source output, Pages repo output, and live URL as applicable.

## Dirty Worktree Rules

- Never run `git add .`.
- Never run broad destructive cleanup commands.
- Do not revert or delete unrelated user changes.
- If unrelated dirty files exist, leave them untouched and clearly identify them in the final report.
- Keep task-created files isolated from pre-existing dirty files.

## Testing And Validation

- Prefer targeted tests first, then broader tests only if the change is cross-cutting.
- Use isolated pytest basetemp directories.
- On Windows, use a unique basetemp per run; do not reuse `$env:TEMP\bluefern-pytest`.
- After dry-run validation, restore tracked `output/site` changes and remove untracked validation artifacts unless they are explicitly part of the task.
- Before commit, final status should show only intended source/test/doc/helper files.
- Run lightweight parse checks for YAML/markdown/templates when creating governance or workflow files.
- Do not skip or weaken tests to make failures pass.
- Do not run expensive dispatch generation unless the task requires it.

## Commits

- Commit only when the user asked for a commit or the task explicitly requires it.
- Stage only the files that belong to the task.
- Verify the staged file list before committing.
- Keep source-repo commits separate from Pages-repo publish commits.

## Pull Request Merge Authority

- Be conservative about scope, not iteration.
- Codex may merge a bounded routine source PR only when the task is authorized, the branch is synchronized with the current protected base, the changed-file inventory is in scope, the PR is open, non-draft, and mergeable, and every required check has succeeded on the exact PR head immediately before merge.
- If the protected base or reviewed PR head changes, stop, synchronize or re-review as applicable, and rerun exact-head validation before merging.
- Use exact-head protection such as `gh pr merge --merge --match-head-commit <EXACT_PR_HEAD>`; never use admin, bypass, or force merge.
- Human merge is required when the PR creates or changes editorial, approval, publication, release, correction/withdrawal, governance, credential, external-egress, destructive-operation, or other human decision authority, or causes a consequential public side effect.
- Codex must never use routine merge permission to expand its own authority or repository governance permissions.
- A source PR merge does not authorize Pages sync, publication, audio, social posting, source-gate relaxation, or any other public release action; those remain separately authorized.
- For `CODEX_AUTO_MERGE_ELIGIBLE`, do not hand routine merge work back to the operator after checks pass. Continue through exact-head merge and normal post-merge verification unless the PR becomes human-required, validation fails, the base or head changes, a review blocker appears, or the user's authorization excluded merge.
- Human merge remains mandatory for the existing governance, editorial, approval, publication, release, correction/withdrawal, credential, destructive-operation, external-egress, and consequential public side-effect categories.
- `AWAITING_NATURAL_CERTIFICATION` is not automatically a development stop. Natural runtime proof certifies the scheduler/runtime boundary; it does not replace immediate engineering proof.
- Continue safe source work after immediate production proof unless the pending natural result is a necessary design dependency, immediate proof exposed an unresolved defect, further work would mutate uncertain production state, or the next task would activate behavior whose safety depends on the pending natural proof.

## Publishing And Pushing

- Do not publish unless the user explicitly asks.
- A task that explicitly instructs Codex to implement a change and open a PR under the standard Codex PR workflow authorizes the routine source feature-branch push required to create or update that PR, subject to any external-egress approval required by the execution environment.
- Routine source PR branch pushes do not authorize Pages pushes, release pushes, social/audio publication, public output publication, credentials or secrets export, unrelated branch pushes, or force-pushes.
- Do not treat implementation validation as release authorization.
- Keep source-repo generation separate from Pages-repo publishing.
- Never push Pages content from the source repo.
- Use cache-busting and direct artifact checks when validating live public output.

## Response Format

Always return:

1. Files changed
2. What changed
3. Commands run
4. Test results
5. Generated/public files checked
6. Publish/push status
7. Risks or follow-up needed
8. Intentionally not touched
