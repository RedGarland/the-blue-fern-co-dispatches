# AGENTS.md

## Required Project Docs

- `README.md`
- `PROJECT_SUMMARY.md`
- `docs/dispatches-project.md`
- `docs/project-contract.md`
- `docs/pages-publish-safety.md`

## Project Identity

- Project: **Dispatches From The Blue Fern Co.**
- Purpose: source-based public dispatch system with traceable reporting and reproducible static output.
- Public domain/publishing context:
  - `https://dispatches.thebluefernco.com/`
  - Gaza public root: `https://dispatches.thebluefernco.com/gaza/`
- This source repository generates public artifacts under `output/site/`.
- Publishing/deploy output is staged in a separate local Pages repository: `bluefern-dispatches-pages` (branch: `gh-pages`).

## Required Standing Rules

1. No fact without a traceable source.
2. Do not invent source claims, URLs, or unsupported narrative details.
3. Preserve source tables, provenance, and auditability across manifests and rendered public outputs.
4. Gaza content must remain daily, public, and free.
5. Only touch the dispatch area explicitly named by the user task. For example, a Gaza task should not modify Cascadia, American Pressure, FDA, or unrelated publishing logic unless the task explicitly says to do so.
6. Prefer narrow, test-backed changes.
7. Do not weaken, delete, or bypass tests to make failures pass.
8. Preserve public URLs and path conventions unless explicitly instructed to change them.
9. Keep source-repo changes distinct from generated Pages-repo output changes.
10. If public output changes, verify expected generated HTML/feed paths and that `output/detail` and `output/paid` are never exposed under `output/site`.

## VS Code / Local Workflow

- Assume work is local in VS Code for this repository.
- Prefer direct local edits in the active workspace.
- Shell conventions:
  - Use PowerShell commands.
  - Use the project virtual environment when available (`.venv`).
  - Set `PYTHONPATH` when running project modules:

```powershell
$env:PYTHONPATH="src"
```

- Testing strategy:
  - Run targeted pytest subsets first (`-k` or specific files).
  - Run broader suites only when the change is cross-cutting.
  - Keep tests focused on the edited scope.

## Final Response Format (Expected)

Use this structure in final task summaries:

1. Files changed
2. What changed
3. Tests run
4. Generated/public files checked
5. Risks or follow-up needed
6. Any files intentionally not touched

## Project-Specific Caution (Source vs Pages)

- A separate Pages repository/folder exists for generated publishing output.
- Do not edit or publish Pages output from a source-repo task unless explicitly instructed.
- If publishing is requested, stop and clearly separate:
  - source-repo commands (generation/validation)
  - Pages-repo commands (commit/push/deploy)

## Practical Guardrails

- Keep edits scoped to the user-requested area only.
- Avoid broad repository rewrites or formatting sweeps.
- When uncertain about scope boundaries, prefer asking before touching adjacent dispatch pipelines.
