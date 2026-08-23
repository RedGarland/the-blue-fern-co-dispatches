# Production Readiness Proof Template

## CHANGE

- Scope:
- Dispatch / workflow:
- Edition date or run date:
- Intent:

## SOURCE

- Branch:
- Protected base:
- Source commit:
- PR:
- Files changed:

## PROMOTION

- Promotion branch:
- Validation scope:
- Required checks:
- Merge result:

## PROTECTED HEAD

- SHA:
- Date / time:

## RUNNER

- Runner path:
- Branch:
- HEAD:
- Clean state:
- Runtime state classification:

## RUNTIME PATHS

- Wrapper:
- Interpreter:
- RepoRoot:
- WorkingDirectory:
- SourceBranch:
- Other fixed paths:

## DIRECT PROOF

- Command:
- Exit code:
- Structured status:
- Child PID:
- Child exit:
- Run ID:
- Manifest / receipt path:

## SCHEDULER DEFINITIONS

- Task name:
- Task path:
- Trigger / cadence:
- Execute:
- Arguments:
- Principal:
- LogonType:
- RunLevel:
- ExecutionTimeLimit:

## TASK-SERVICE PROOF

- Launched through Task Scheduler:
- LastTaskResult:
- Receipt / log:
- Wrapper actually used:
- Interpreter actually used:
- Scheduler result:

## STATE HANDOFF

- Input state path:
- Output state path:
- Expected handoff:
- Observed handoff:

## OBSERVABILITY

- Required receipt fields present:
- Parent / child run IDs correlated:
- Stdout / stderr tail captured:
- Failure layer identifiable:

## PUBLICATION SAFETY

- Pages touched:
- Bluesky touched:
- Check-only or dry-run boundary:
- Duplicate protection:

## CLEANUP

- Locks cleaned:
- Orphans cleaned:
- Temporary canary removed:
- Runtime residue retained or classified:

## OPEN RISKS

- Known blockers:
- Unknowns:

## FINAL READINESS CHECKLIST

- [ ] source validated
- [ ] protected
- [ ] runner synced
- [ ] runtime proven
- [ ] scheduler proven
- [ ] handoff proven
- [ ] publication proven if applicable
- [ ] cleanup proven

## FINAL STATUS

- `SOURCE VALIDATED`
- `READY FOR PROMOTION`
- `PROTECTED — RUNTIME PROOF PENDING`
- `RUNTIME PROVEN — SCHEDULER PROOF PENDING`
- `SCHEDULER PROVEN — PUBLICATION PROOF PENDING`
- `PRODUCTION HEALTHY`
- `PRODUCTION INCIDENT OPEN — <reason>`
