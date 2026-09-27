# Pages Publish Safety

Source repo: the current checked-out source worktree; do not depend on a fixed workstation path.

Pages repo: `./bluefern-dispatches-pages` when using the standard sibling checkout.

Rules:

- Do not run `git add .` in source repo.
- Do not commit `.env`, logs, `output/detail`, `output/paid`, test temp dirs, or broad generated artifacts.
- Publish push must happen only from `bluefern-dispatches-pages`.
- Pages branch must be `gh-pages`.

- Local publish behavior: the publisher copies the generated `output/site` files into the `bluefern-dispatches-pages` repository and creates a local commit by default. Pushing those commits to the remote is an explicit, separate step (the publisher skips push unless invoked with an explicit push option).
- Gaza publishes now fail closed if the new build would drop existing public-history dates from `gaza/archive.html`, `gaza/rss.xml`, `gaza/audio/index.html`, `gaza/audio/podcast.xml`, or `gaza/podcast.xml`. Use `--allow-listing-shrink` only for a deliberate, reviewed archival pruning operation.
- A scoped Care Line publish may add an expected, listable edition that is not yet present on Pages. Before copying, the generated edition must be listable and the generated archive/RSS must preserve every published date. After copying, the expected edition and all required edition files must exist byte-for-byte on Pages, while newer Pages editions remain untouched.
- To publish live from this machine, either run the dispatch runner with its `--push` flag (for example `scripts\run_daily_gaza.py --push`) or run `git push origin gh-pages` from inside the `bluefern-dispatches-pages` repo. Do not push the Pages branch from the source repo.

## Codex Safe Execution Scope

Repository-wide Codex / implementation-agent authority is defined by `AGENTS.md`. This document does not independently expand or narrow that authority.

For the reusable mechanical PR procedure, use `docs/workflows/codex_pr_workflow.md`. Routine source-repository engineering may proceed through the bounded workflow permitted by `AGENTS.md`; public publication, Pages/public release, editorial decisions, credentials/security changes, destructive operations, and other explicit human authority boundaries remain separately controlled.

The autonomous Blue Fern Operator is governed independently by `ops/operator/remediation-policy.yaml`; Codex routine PR authority must not be inferred as Operator merge or publication authority.
