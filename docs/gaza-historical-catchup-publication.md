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

`approve` reads each review and decision from exact Git objects. Every binding includes the commit, repository-relative path, blob SHA-1, and raw SHA-256. The private request must be outside the repository and must bind the clean protected source head and clean `gh-pages` head. The approver must be a human independent of every bound editorial reviewer. The generated approval is deterministic; an exact replay is a no-op and conflicting content is rejected.

The approval contains a maximum of 15 ordered items. Each item is reproduced from protected reviewed evidence, including its historical event date or bounded event period, actual source publication time, attribution, uncertainty, and source URLs. Unknown event dates are never inferred. Corrections, deferred findings, already represented findings, unreviewed candidates, and prior publication authority are rejected. The public edition uses the approved later publication date and a required retrospective disclosure rather than pretending to be a historical daily edition.

`plan` is read-only. It requires the approval commit to be an approval-only ancestor of the current protected source head, re-derives all approved copy, checks owner-code drift, validates the exact clean Pages binding, rejects an occupied edition date or public collision, and reports the exact expected Pages paths.

`preview` and `stage` write only to explicit directories outside both Git worktrees. Preview contains the normal public-shaped HTML plus its private manifest. Stage contains exact edition HTML, edition/source/curation/dedupe manifests, Gaza homepage, archive, RSS, and a content-addressed release manifest. Existing root-homepage, podcast, audio-podcast, and flash surfaces are recorded only as non-mutated dependencies. Audio and social remain unauthorized.

`verify-stage` rebuilds the package from the committed approval and compares every byte, so editing approved prose and recomputing only a top-level manifest cannot authorize publication.

`publish` is the sole Pages mutation boundary. It requires `--push`, a clean source worktree, the exact clean `gh-pages` binding, an exact verified stage, and preserved homepage/archive/RSS history. It stages only the sanctioned package paths, creates one Pages commit, pushes `gh-pages`, verifies the remote head, and can perform a cache-busted live disclosure check. A pre-commit failure restores Pages bytes. A push failure is reported as a local Pages commit that was not promoted; the owner does not regenerate against a newer head.

After a successful push, the owner records a content-bound publication-state artifact and story-memory entries so the historical event cannot be selected again merely because it was published later. Planning, preview, and staging never write this state.

## Commands

Use `python scripts/manage_gaza_historical_catchup.py --help` and the subcommand help. A real approval is a separate human-authorized operation after this capability is protected. Never infer publication permission from a merged review, a passing plan, or a private stage.

The owner grants no scheduler, collection, source-configuration, daily-curation, existing-edition rewrite, podcast, flash, audio, or social authority.
