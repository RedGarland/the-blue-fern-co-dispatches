# Food Line September 1-3 Nonoriginal Historical Release Decision

All three dates are `BLOCKED_BY_GOVERNANCE` for public historical release.
The four items passed item-level editorial review, but item approval is not
release approval and release approval is not publication authorization.

The sanctioned Food Line publication contract is the committed
`food_line_retrospective_approval_v5` migrated-event retrospective path. It
requires a V5 approval commit, migrated-event decision bindings, approved
public-copy artifacts, a bound Pages head, and explicit publication authority.
The Sep. 1-3 items are nonoriginal reconstruction records and do not satisfy
that contract. One-item editions are structurally allowed by the V5 story-count
contract, but that does not authorize these nonoriginal items.

| Date | Decision | Items | Release ready | Publication performed |
|---|---|---:|---|---|
| 2026-09-01 | `BLOCKED_BY_GOVERNANCE` | 1 | false | false |
| 2026-09-02 | `BLOCKED_BY_GOVERNANCE` | 2 | false | false |
| 2026-09-03 | `BLOCKED_BY_GOVERNANCE` | 1 | false | false |

## Required disclosure

Any future manually authorized edition must visibly state:

> This historical Food Line edition was reconstructed after the original scheduled processing for this date failed. The items were researched and reviewed retrospectively and were not captured by the original daily Source Watch run.

No publication mechanism was executed. A future release would require a
separate explicit decision and the existing guarded retrospective tooling,
with a historical date pinned per invocation, committed V5 approval consumed
only after protected merge, exact approved public-copy bindings, clean bound
Pages checkout, archive/RSS monotonicity checks, idempotent behavior, and no
current Daily Publish or scheduler path. These files are not consumable by
the current Daily Publish path.

Original runtime remains `FAILED / UPSTREAM_BLOCKED`; faithful replay remains
`UNAVAILABLE`; historical gap remains `OPEN / NONORIGINAL_RECONSTRUCTION_AVAILABLE`;
nonoriginal reconstruction remains `COMPLETE`. Publication eligibility,
publication approval, and published state remain false. Pages, archive, RSS,
homepage, scheduler, production runner, audio/social outputs, and Sep. 7-8
release states remain unchanged.
