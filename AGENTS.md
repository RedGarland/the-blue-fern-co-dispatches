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

## Required Behavior Before Editing

- Inspect the current worktree with `git status --short`.
- Identify the exact dispatch, edition date, or workflow scope requested by the user.
- Read only the files needed for that scope.
- Do not assume dry-run output is publishable without checking review output and logs.
- Do not touch unrelated dirty files.
- Do not modify generated/public artifacts unless the task explicitly requires it.
- Do not edit Pages repo output from a source-repo task unless explicitly requested.

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
- Do not publish or push unless explicitly requested.
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

## Publishing And Pushing

- Do not publish or push unless the user explicitly asks.
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
