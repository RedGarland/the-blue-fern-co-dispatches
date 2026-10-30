# One-shot October 29 late-edition validation

This draft-only packet is not a production source fix, scheduled-run repair, publication, or merge request.

## Binding and provenance
- Production source stays pinned to `5a6c591831654c9e9e307711b74a268ce00d8d0d`.
- The untouched Pages baseline is `a952b7669ea16504b953fbd933cb226b4c643101`.
- Manual input is a newly reconstructed, newly hashed derivative of the retained source-review metadata and the three exact approved sentences. Earlier local packet bytes could not be reread; no byte identity with that packet is claimed.
- Article titles/URLs were independently verified. The earlier observed review batch time is retained explicitly, not relabelled as a fresh retrieval. Publisher dates remain date-only because timezone was not verified.
- Approval metadata records the earlier editorial approval and the separate draft-CI authorization; the CI job has no publication authority.

## Execution and budget
Only the exact same-repository PR branch `ci/gaza-oct29-exact-copy-validation-20261030` receives the new candidate job and temporary shorter job timeouts.
One workflow run is planned; no automatic retry.
Configured job limits sum to 20 minutes: core10 + Windows2 + historical visual3 + candidate4 + aggregate1.
Other branches retain their existing limits: Windows10, historical visual15, and explicit360 for previously unspecified core/aggregate jobs (GitHub default).
Queue and cancellation/teardown time are not an elapsed-time guarantee. A timeout is failure, not permission to skip a check.
The existing `validate` gate is unchanged apart from its branch-specific timeout. Completion additionally requires the separate `oct29_candidate` job and overall workflow to succeed; a green `validate` result alone is insufficient.

## Required checks
Run unchanged production generation with no thin-edition, date-window, source/dedupe or correction override.
Inspect actual source and story dedupe results. If any of the three exact approved reports is excluded/merged or its copy changes, stop before creating a release candidate; do not insert dates/numbers to influence the heuristic.
Preserve raw output and diagnostics, then apply the documented presentation/timing late layer to a fresh private candidate.
Verify exact nine-path delta, all baseline leaf bytes outside that delta, all147 existing archive/RSS dates, existing RSS item content, unchanged Oct28/correction/audio/CSS/history, current source bindings and truthful timing.
The shared root/directory updates use existing template helpers and must preserve unrelated article cards.
Run dated Gaza scope validation for seven Gaza paths and existing sitewide scope for the two shared surfaces, with a separate exact-nine allowlist. Gaza does not support the Food/Care release-manifest option.
Run the unchanged seven-route browser validator plus the October29 dated page; retain screenshots and hashes for independent inspection.
No source files under production runtime src/scripts/assets/.github may change.
The standard core job already selects all five Gaza daily-profile suites; there is no additional unrelated full-project release requirement.

## Outputs and stop conditions
Artifacts retain input/approval hashes, activation proof, generation/dedupe diagnostics, raw edition, exact nine-file candidate payload, late-layer field deltas, timing review, complete baseline inventory and composed tree proof, and browser evidence.
Any mismatch, timeout, missing evidence or browser failure fails closed.
Do not merge this PR, adopt it on a runner, push Pages, publish audio/social, weaken a gate or start another run automatically.
