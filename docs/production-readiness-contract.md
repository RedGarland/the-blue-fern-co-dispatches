# Production Readiness Contract

This contract governs production-change work in the Blue Fern repository.

## Why This Exists

Recent incidents showed that lower-layer success was being mistaken for production readiness. The following failure classes were discovered only after tests, merges, clean-runner checks, or smoke runs had already passed:

- stale scheduler interpreter
- stale wrapper or runner path
- direct wrapper execution differing from Task Scheduler execution
- runtime state rejected by a cleanliness guard
- missing failure receipts or logs
- invalid subprocess contracts
- stdout / manifest classification mismatch
- parent / child run-id mismatch

These failures proved that source validation, merge validation, runner sync, and smoke checks are necessary but not sufficient.

## Readiness Is Layered

Production readiness is a chain of distinct layers. A lower layer may be true while a higher layer is still unproven.

- `SOURCE VALIDATED`
  - Local code and tests pass.
- `PROTECTED`
  - The change is merged into the protected production branch.
- `RUNNER SYNCED`
  - The production runner matches the protected head.
- `RUNTIME PROVEN`
  - The exact production wrapper, interpreter, working directory, branch, and runtime state contract have been exercised directly.
- `SCHEDULER PROVEN`
  - The operating-system scheduler or task service has launched the current protected runtime successfully.
- `HANDOFF PROVEN`
  - Required downstream state, manifest, queue, or review transitions have succeeded.
- `PUBLICATION PROVEN`
  - If publication is part of the contract, the actual publication path has been proven at the correct safety boundary.
- `PRODUCTION HEALTHY`
  - All applicable layers above are proven.

No lower-layer success may be described as `production-ready`, `production-proven`, `healthy`, `closed`, or `done` unless the applicable readiness checklist is complete.

## Required Production Chain

For scheduled pipelines, the expected proof chain is:

`protected source`
-> `production runner`
-> `exact scheduler definition`
-> `exact executable`
-> `exact wrapper`
-> `exact interpreter`
-> `exact working directory`
-> `exact RepoRoot`
-> `exact branch`
-> `runtime state / preflight`
-> `child process`
-> `structured result`
-> `downstream state handoff`
-> `cleanup`
-> `intended side-effect boundary`

Every filesystem path referenced by production configuration must be verified on the target machine after the final protected sync.

No scheduled pipeline is production healthy until the task service itself has been proven to launch the current protected runtime.

## Simulation Does Not Substitute For The Boundary

- Unit tests prove code behavior, not PowerShell behavior.
- Direct PowerShell invocation proves wrapper/runtime behavior, not Task Scheduler behavior.
- Task XML inspection proves configuration, not task-service execution.
- Check-only proves safe publication readiness, not live publication.

Each boundary must be proven independently when applicable.

## Failure Classification Before Repair

Before modifying anything, classify the primary failure layer as one of:

- `SOURCE_DEFECT`
- `RUNNER_DRIFT`
- `WRAPPER_DEFECT`
- `SCHEDULER_DEFINITION_DEFECT`
- `FILESYSTEM_PATH_DEFECT`
- `PERMISSION_ACL_DEFECT`
- `RUNTIME_STATE_CONTRACT_DEFECT`
- `HANDOFF_DEFECT`
- `OBSERVABILITY_DEFECT`
- `EXTERNAL_DEPENDENCY_FAILURE`
- `PUBLICATION_DEFECT`
- `LEGITIMATE_NO_OP`
- `LEGITIMATE_THIN_DATA`
- `UNKNOWN`

`UNKNOWN` is acceptable temporarily. Do not fix a different layer merely because the exact failure is not yet observable.

## Observability Is Part Of Readiness

A scheduled production action must leave enough durable evidence to diagnose failure.

Required evidence, where applicable:

- invocation timestamp
- run ID
- wrapper
- interpreter
- RepoRoot
- branch
- working directory
- child command
- child PID if launched
- child exit
- structured status
- stdout / stderr tail where safe
- canonical manifest or result path
- wrapper exit

A production task that can return nonzero without enough evidence to identify the failing layer is not production-ready.

## Runtime State Is Not Source Drift

Production runners may legitimately contain runtime state.

Clean checks must explicitly classify known runtime locations rather than requiring literal `git status` emptiness when production state is expected.

Rules:

- allowlists must be narrow
- arbitrary untracked files remain failures
- source / config drift remains fail-closed
- runtime state must never be moved or deleted solely to make a cleanliness guard pass

## Protected Source Is Authoritative

Never deploy an unmerged local fix directly to a production runner.

Required sequence:

local implementation
-> clean validation
-> protected promotion
-> merge
-> runner sync
-> production proof

An explicit emergency procedure may exist for exceptional cases, but it must preserve auditability and must not become the default path.

## Scheduler Mutation Rules

Before changing scheduler definitions:

- establish canonical protected runtime paths
- prove the target wrapper directly
- verify the target interpreter
- `Test-Path` every referenced path
- back up task XML
- preserve cadence, principal, RunLevel, and settings
- modify only stale fields
- re-read the live task after mutation
- prove the change through the task service immediately

Do not wait for the next scheduled run when a safe current-day proof is possible.

Do not use aliases or junctions merely to preserve obsolete paths when the scheduler itself can be corrected.

## Production-Parity Proof

Before marking a scheduled pipeline healthy, require the closest safe equivalent to the real production action.

Use:

- the actual current date or window
- the actual protected branch
- the actual runner
- the actual wrapper
- the actual interpreter
- the actual state contract

Do not accidentally test tomorrow's editorial window while diagnosing today's completed run.

When the production action has consequential side effects, use a safe boundary such as:

- check-only
- dry-run
- smoke canary
- temporary no-trigger scheduler canary

Preserve the same scheduler/runtime boundary.

## Publication Safety

Publication remains separate from collection and review when that is the pipeline contract.

Require explicit proof of:

- release-ready gating
- no editorial inference during publishing
- Pages path
- social path
- duplicate receipt protection
- check-only or dry-run safety

Never trigger a live publication merely to prove scheduler mechanics when a no-side-effect canary can prove the same boundary.

## Cleanup And Post-Run Health

Readiness requires:

- no stale locks
- no orphan child processes
- runner remains within the allowed state contract
- no accidental Pages modifications
- no unintended Bluesky or other external actions
- temporary canaries removed
- temporary proof artifacts cleaned or classified

## Language Control

Use precise intermediate statuses until every applicable readiness layer is proven.

Preferred statuses include:

- `SOURCE VALIDATED`
- `READY FOR PROMOTION`
- `PROTECTED, NOT YET RUNTIME-PROVEN`
- `RUNNER SYNCED, SCHEDULER UNPROVEN`
- `DIRECT WRAPPER PROVEN, TASK SERVICE UNPROVEN`
- `PUBLICATION PATH VALIDATED TO SAFE BOUNDARY`
- `INCIDENT OPEN — <specific blocker>`

Do not say `production-ready`, `production-proven`, `healthy`, `incident closed`, or `complete` unless the checklist is complete for the applicable pipeline.

## Production Readiness Checklist

### SOURCE

- [ ] intended diff reviewed
- [ ] focused tests pass
- [ ] broader affected tests pass

### PROTECTION

- [ ] change merged normally
- [ ] required status checks passed
- [ ] protected head recorded

### RUNNER

- [ ] correct runner identified
- [ ] runner synced to protected head
- [ ] branch correct
- [ ] runtime state classified
- [ ] risky source/config drift absent

### PATH CONTRACT

- [ ] wrapper exists
- [ ] interpreter exists
- [ ] RepoRoot exists
- [ ] WorkingDirectory exists
- [ ] downstream fixed paths exist

### DIRECT RUNTIME

- [ ] wrapper launches
- [ ] child launches
- [ ] structured result valid
- [ ] expected state written
- [ ] expected downstream state consumed

### SCHEDULER

- [ ] live definition inspected
- [ ] paths match protected runtime
- [ ] task-service launch proven
- [ ] scheduler result structurally healthy
- [ ] receipt or log captured

### OBSERVABILITY

- [ ] failures would leave durable evidence
- [ ] run IDs correlate parent, child, and manifest
- [ ] no silent failure boundary remains

### PUBLICATION

- [ ] publication boundary tested if applicable
- [ ] no unintended Pages action
- [ ] no unintended social action
- [ ] duplicate protection proven if applicable

### CLEANUP

- [ ] no stale lock
- [ ] no orphan child
- [ ] temporary task or canary removed
- [ ] runtime residue within the allowed contract

### FINAL

- [ ] every applicable box above is yes

Only then:

`PRODUCTION HEALTHY`
