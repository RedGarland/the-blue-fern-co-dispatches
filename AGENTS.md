# AGENTS.md

## Scope and Precedence

This root `AGENTS.md` provides the default instructions for the entire repository.

More specific instructions from the current user request take precedence over this file.

A nested `AGENTS.md` file governs its own directory subtree and overrides this root file within that subtree.

Authorization from an earlier request, conversation, review, test run, approval, or dry run does not automatically carry forward.

Live actions require explicit authorization in the current request.

## Repository Overview

Dispatches From The Blue Fern Co. is a source-based public dispatch system with traceable reporting and reproducible static output.

All dispatch outputs must be source-traceable, date-safe, and publication-safe.

## Documentation to Inspect First

Read these before making project changes:

- `README.md`
- `PROJECT_SUMMARY.md`
- `docs/dispatches-project.md`
- `docs/project-contract.md`
- `docs/pages-publish-safety.md`

Identify the exact dispatch, edition date, workflow, repository, and requested operation before editing.

Determine whether the request concerns source implementation, generation, review, provenance, Pages synchronization, publication, external posting, or scheduling.

Before editing, inspect the current worktree with `git status --short --branch`.

If a sibling or nested Pages repo exists, inspect its `git status --short --branch` too.

Run `scripts/preflight_repo_state.py` before making changes when Git state clarity matters.

Read only the files needed for the requested scope.

Do not modify generated or public artifacts unless the current task explicitly requires generation or artifact changes.

Do not assume dry-run output is publishable without checking review output and logs.

## Source and Pages Boundaries

Treat source and Pages repositories as separate operational and publication boundaries.

Do not edit Pages repo output from a source-repo task unless explicitly requested.

Keep source-repo generation separate from Pages-repo publishing.

Never push Pages content from the source repo.

## Working-Tree Safety

Do not touch unrelated dirty files.

Do not revert or delete unrelated user changes.

Never run `git add .`.

Never run broad destructive cleanup commands.

Never reset, clean, stash, restore, discard, overwrite, delete, or incorporate unrelated changes.

Do not assume ignored or generated files are harmless without inspection.

Stop when unexpected tracked, staged, renamed, or deleted files appear unless the current request explicitly authorizes handling them.

Use a clean isolated clone or worktree for release, provenance, publication, Pages, and other high-risk operational work.

## Authorization Boundaries

Treat these as separate actions that each require explicit authorization in the current request:

- editing source
- committing source
- pushing source
- generating public artifacts
- copying artifacts to a Pages repository
- committing Pages
- pushing Pages
- publishing a live site
- posting to Bluesky or any external service
- modifying scheduled tasks or runner configuration

Never commit, push, publish, post externally, modify schedules, or alter `gh-pages` unless the current request authorizes that exact action.

Test success, review approval, PR approval, or dry-run success is not authorization for a live action.

Never force-push unless explicitly authorized in the current request and required by the documented workflow.

An implementation agent must not self-approve its own editorial, release, or publication work.

A reviewer must not make unrelated implementation changes unless explicitly asked.

Always distinguish implementation completion, test and validation success, release readiness, publication authorization, and confirmed live publication.

## Non-Live First

Use the narrowest available safe mode before any live operation, as applicable:

- dry-run
- check-only
- review-only
- audit
- smoke-test
- validation-only
- no-push
- no-publish

Use the repository's explicit allow flags for Pages synchronization, audio, maps, Bluesky, publication, or other live-capable outputs when a script provides them; an allow flag is not authorization by itself.

Inspect the resulting artifacts, logs, manifests, and Git status rather than relying only on exit codes.

Do not assume a clean `git status` means the live site changed.

## Traceability and Evidence Requirements

Preserve full source traceability.

Validate source attribution, URLs, dates, claims, manifests, and publication-status fields when working on dispatch output.

When applicable, verify:

- original publisher URL
- source identity
- publication and retrieval dates
- exact supporting passages
- claim-to-source mapping
- source and Pages manifests
- edition date
- public release status
- Pages synchronization status

Do not expose private review queues, intake artifacts, held or rejected candidates, internal notes, local filesystem paths, unpublished detail packages, paid-only artifacts, or unsupported or untraceable claims in public output.

Do not bypass, weaken, patch around, or manually override safety validators merely to make a release pass.

A validator failure must be corrected at its actual provenance, artifact, source, or baseline cause.

## Dispatch Editing and Generation

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

## Testing and Validation

Prefer targeted tests first, then broader tests only if the change is cross-cutting.

Use isolated pytest basetemp directories.

On Windows, use a unique basetemp per run; do not reuse `$env:TEMP\bluefern-pytest`.

Run the narrowest applicable tests first.

After dry-run validation, only task-created validation changes may be reverted or removed, and only in the isolated checkout used for the task.

Use explicit file paths for any cleanup.

Never restore, delete, clean, or overwrite pre-existing or unrelated working-tree changes.

If the origin of a changed file is uncertain, stop and report it rather than modifying it.

Inspect generated artifacts directly rather than relying only on process exit codes.

Run lightweight parse checks for YAML, Markdown, and templates when creating governance or workflow files.

Do not skip or weaken tests to make failures pass.

Do not run expensive dispatch generation unless the task requires it.

Before any publish, release, or Pages-sync task, run `scripts/validate_publish_scope.py`.

## Generated-Artifact Inspection

When public output changes, verify source output, Pages repo output, and live URL as applicable.

If public output changed, verify the rendered paths and confirm `output/detail` and `output/paid` are not exposed under `output/site`.

Inspect generated artifacts directly rather than relying only on process exit codes.

## Publication and External-Posting Safety

Do not publish or push unless explicitly requested.

Do not treat implementation validation as release authorization.

Use cache-busting and direct artifact checks when validating live public output.

### Sitewide and Generated Artifacts

- Keep generated output reproducible and traceable.
- Do not add `output/detail` or `output/paid` content to public site output.
- Do not publish or push unless explicitly requested.
- When public output changes, verify source output, Pages repo output, and live URL as applicable.

## Git and Branch Practices

Do not modify `gh-pages` unless the current request explicitly authorizes it.

Never push Pages content from the source repo.

Keep source-repo commits separate from Pages-repo publish commits.

Commit only when the user asked for a commit or the task explicitly requires it.

Stage only the files that belong to the task.

Verify the staged file list before committing.

Do not assume ignored files are harmless if they sit beside tracked source or public output.

Before committing, the final working-tree and staged-file lists must contain only files intended for the current task.

Stop if unexpected files appear.

Review the staged diff and staged file list before committing.

If a fresh clone or worktree is required for release work, create one and keep it isolated from the dirty checkout.

## Required Completion Report

Always return:

1. Files changed
2. What changed
3. Commands run
4. Result of each command, including failures
5. Tests run and results
6. Validation run and results
7. Generated artifact paths
8. Generated/public artifacts directly inspected
9. Source repository:
   - path
   - branch
   - HEAD
   - upstream
   - final Git status
10. Pages repository, when applicable:
   - path
   - branch
   - HEAD
   - upstream
   - final Git status
11. Commits created, including SHA and files included
12. Pushes performed and their result
13. Publication or external posting performed and its verified result
14. Schedule or runner changes performed
15. Work skipped and the reason
16. Unresolved risks or required follow-up
17. Items intentionally not touched

Never claim that a commit, push, publication, external post, scheduled change, or live verification occurred unless it was actually performed and its result was checked.
