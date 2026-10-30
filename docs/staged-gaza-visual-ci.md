# Staged Gaza archive visual regression

## Why this exists

The ordinary browser unit tests use compact synthetic HTML and CSS. They test
validator behavior but did not expose the real, long archive layout that failed
the October 29 visual check. This additional CI job uses a complete immutable
public Pages snapshot, preserves its real HTML/history/assets, and stages only
the candidate source archive override. It runs the existing seven-route browser
validator without changing its viewport, checks or thresholds, plus the newest
dated Gaza page linked by the baseline homepage.

## Scope and limits

This is **archive-override regression coverage**, not a new-edition generator,
editorial review, publication authorization, production installation proof, or
scheduled/delivery receipt. Source CSS preceding the marked override and changed
templates are deliberately not deployed into the snapshot. They are outside
this job's candidate coverage. A full generated-candidate release job is a
separate extension; do not remove its release checks on the strength of this job.

The suffix is copied exactly from `assets/site.css`; unrelated source CSS is
not substituted for historically deployed CSS. Existing marked overrides are
replaced rather than stacked. A pure check reproduced the accepted V5 stylesheet
byte-for-byte: 25,623 bytes, SHA-256
`400985fa0d48340091d1d9d96071722c7a02723240e984809557e298597545ed`.

## Execution

- The workflow checks out a fixed 40-character Pages commit, never a moving URL.
- Public baseline checkout credentials are not persisted. The browser job has
  only `contents: read`, no secrets, no publication command, and no
  `pull_request_target` trigger.
- The candidate is fresh and private under runner temp; a collision fails closed.
- No symlink/non-file baseline entries are accepted. A clean baseline and exact
  SHA are required; before/after hashes prove baseline preservation.
- Admission requires projected copy bytes plus the existing 2 GiB reserve.
- Only `gaza/assets/site.css` may change. All candidate leaf hashes and source,
  baseline and override identities are recorded before the browser starts.
- The existing default seven routes remain intact. Every route must have
  HTTP 200, a screenshot and screenshot hash. The additional dated page does not
  replace one of the seven routes.
- Browser step has a 10-minute timeout; total job is bounded to 15 minutes.
  A running marker and stage binding are saved before browser execution, and
  artifacts upload even after failure. Timeouts are failures, not success.
- Public screenshots and reports are retained for seven days. They are attached
  only as GitHub CI artifacts; no screenshot or result is posted to the public site.
- Browser regression and core tests run in parallel. The existing `validate`
  status is success only if both complete successfully. Only the final aggregator
  has the pre-existing status-write permission; it checks out no candidate code.
  Fork PRs use the native `validate` check result; the optional duplicate commit
  status write is skipped for forks, whose token cannot write to the base repo.
  Required-check compatibility for that fork path must be verified in rollout;
  no token or permission elevation is proposed.

## Rollout

1. Review the three source/workflow files plus this document in a draft PR.
2. The immutable baseline is the accepted October 28 publication commit
   `a952b7669ea16504b953fbd933cb226b4c643101`.
3. The job requires the positive seven routes and dated page to pass, then
   adds excessive archive-intro padding only in the private candidate. It
   requires HTTP 200/screenshot evidence and specific latest/first-viewport
   layout failures, then restores the exact accepted CSS in a finally block.
   The negative screenshot and report are preserved. This proves the guard
   rejects an actual rendered spacing regression, not merely missing resources.
4. Obtain the required workflow/release-governance approval before merging.
   The proposed workflow is not installed or active merely because this patch
   exists. Do not change branch permissions or required checks separately.
5. Rollback is a narrow source PR reverting this addition, preserving all test
   artifacts and the prior validation jobs. Never delete previous receipts to
   make a rerun pass.

## Local validation so far

Fourteen pure unit cases, exact V5 CSS byte comparison, Python AST and YAML
parsing passed. Full browser/CI execution has not run in this preparation.
The cloud browser was previously denied; it was not retried. Candidate-stage
admission currently lacks the 2 GiB reserve, so no full stage was copied and
no guard was reduced.
