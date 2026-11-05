# Food instrumented Windows wrapper fixture

Prepared source, not a native validation result. Do not execute candidate.ps1.

This exact-source fixture replaces external child processes and Git calls with
inert functions, and captures the final wrapper exit instead of exiting the
test host. It preserves the wrapper's original branches, validators and receipt
serialization. No real provider, helper, installed runner or production state
is an input. The hardcoded infrastructure paths in the original source remain
visible as source data; synthetic roots are supplied for all execution.

verify_ci_payload.py independently pins the eight reviewed fixture/input files and checks
reverse instrumentation without starting any PowerShell. The dedicated job
runs the instrumented fixture on PowerShell 7 and Windows PowerShell 5.1, with
14 baseline scenarios and two attributed mutation checks. The CI-only driver
hardens the original fixture by parsing mutants and requiring its call-order
assertion as the mutation failure, rather than accepting any exception.

companion-static-test-correction.patch is review input only. CI checks patch
applicability against base 3f58326266abc49546731b791b9cfad3d0d44393;
it does not apply the patch or run its full Python suite. That suite contains
a separate real PowerShell/helper chain outside this boundary-only test.

A passing job is only instrumented native orchestration evidence. It is not
full regression, actual process-launch/quoting or OS-exit validation, production
recovery, installation, publication or permission for any of those actions.
The hosted runner is not an offline sandbox. No test network call is designed,
but GitHub checkout/setup/artifact services use normal hosted connectivity.

If published to the proposed public repository, source, workflow logs and the
two path-free synthetic summary artifacts are repository-readable, not private.
No raw receipt, desktop evidence, source transport or production ledger is
included. Native results do not yet exist at preparation time.
