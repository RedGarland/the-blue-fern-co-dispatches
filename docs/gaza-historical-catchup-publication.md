# Gaza historical catch-up publication

This owner publishes only Gaza developments already confirmed as historical TRUE-MISS records. It does not replace daily generation or the formal-correction workflow.

## Authority flow

```text
committed historical review + committed decision
    -> independent human approval
    -> committed approval-only artifact
    -> read-only plan
    -> external private preview/stage
    -> explicit atomic Pages publication
    -> publication state + story memory
```

`approve` reads each review and decision from exact Git objects. Every binding includes the commit, repository-relative path, blob SHA-1, and raw SHA-256. The private request must be outside the repository and must bind the clean protected source head and clean `gh-pages` head. The approver must be a human independent of every bound editorial reviewer. Current v2 approvals are written only to the owner-derived path `approvals/gaza/<catchup-id>-approval-v2.json`; callers cannot select an approval path. The generated approval is deterministic; an exact replay is a no-op and conflicting content is rejected.

The approval contains a maximum of 15 ordered items. Each item is reproduced from protected reviewed evidence, including its historical event date or bounded event period, actual source publication time, attribution, uncertainty, and source URLs. Unknown event dates are never inferred. Corrections, deferred findings, already represented findings, unreviewed candidates, and prior publication authority are rejected. The public catch-up uses the approved later publication date and a required retrospective disclosure rather than pretending to be a historical daily edition. Its canonical identity is `/gaza/catchups/<catchup-id>/`, so a normal daily edition may coexist on the same date. The request and approval must bind that exact owner-derived path and URL; callers cannot select another public path.

`plan` is read-only. It requires the approval commit to be an approval-only ancestor of the current protected source head, re-derives all approved copy, checks owner-code drift, validates a clean Pages checkout, rejects an occupied catch-up path or public collision, and reports the exact expected Pages paths. A Pages head newer than the approval binding is accepted only when it is a strict descendant, all prior Gaza publication files remain unchanged, newly added publications are complete, archive and RSS history is monotonic, and the approved candidates remain absent. The plan and stage then bind the current Pages head. Non-descendant drift and relevant prior-claim changes fail closed.

`preview` and `stage` write only to explicit directories outside both Git worktrees. Preview contains the public-shaped catch-up HTML plus its private manifest. Stage contains exact catch-up HTML, edition/source/curation/dedupe manifests under `/gaza/catchups/<catchup-id>/`, Gaza homepage, archive, RSS, and a content-addressed release manifest. Archive and RSS identify the catch-up separately from any daily edition on the same publication date. Existing daily-edition files, root homepage, podcast, audio-podcast, and flash surfaces are non-mutated dependencies. Audio and social remain unauthorized.

`verify-stage` rebuilds the package from the committed approval and compares every byte, so editing approved prose and recomputing only a top-level manifest cannot authorize publication.

`publish` is the sole Pages mutation boundary. It requires `--push`, a clean source worktree, the exact clean current `gh-pages` head bound by the stage, an exact verified stage, and preserved homepage/archive/RSS history. It stages only the sanctioned package paths, creates one Pages commit, pushes `gh-pages`, verifies the remote head, and can perform a cache-busted live disclosure check at the catch-up URL. A pre-commit failure restores Pages bytes. A push failure is reported as a local Pages commit that was not promoted; the owner does not regenerate against a newer head.

The former v1 approval contract was date-keyed. Its unversioned `approvals/gaza/<catchup-id>-approval.json` files remain immutable historical authority records, but they are intentionally obsolete and cannot authorize the catch-up namespace. A renewed independent human approval using the v2 path-bound contract and versioned v2 approval path is required. Migration never renames, replaces, rewrites, or silently converts a v1 approval; the v1 and v2 artifacts may coexist.

After a successful push, the owner records a content-bound publication-state artifact and story-memory entries so the historical event cannot be selected again merely because it was published later. Planning, preview, and staging never write this state.

## Commands

Use `python scripts/manage_gaza_historical_catchup.py --help` and the subcommand help. A real approval is a separate human-authorized operation after this capability is protected. Never infer publication permission from a merged review, a passing plan, or a private stage.

The owner grants no scheduler, collection, source-configuration, daily-curation, existing-edition rewrite, podcast, flash, audio, or social authority.
