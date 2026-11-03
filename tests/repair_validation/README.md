# Disposable Windows repair validation

Draft-only experimental validation, not a production installer or publication tool.

The core and Windows adapter preserve the private proposal's function logic; only fixed input/destination bindings are sanitized (see SANITIZATION.json). The harness supplies eight synthetic text files and exercises seven replacements, preserved backups, unchanged eighth file, drift, transaction collision, alternate streams, security mismatch before payload, and native missing-candidate failure.

Run only on independent GitHub-hosted Windows: `python test_windows_disposable.py --run-disposable`. This never invokes PowerShell. The dedicated workflow is opened/synchronize-triggered and exact-branch-scoped, with a ten-minute job timeout and read-only contents permission. The approved harness correction resolves only its newly created disposable temporary root and requires exact refusal messages; the adapter and transaction core remain unchanged. Up to three additional disposable Windows runs are approved with a thirty-minute aggregate cap; ordinary push-triggered Windows CI is counted against that budget. No automatic retry, merge, local runner changes, security changes, or publication are authorized. Only the synthetic test log is uploaded.

A pass proves behavior on disposable CI files, not production compatibility. The known production target ownership/group/DACL differs from the proposed backup parent. The adapter must refuse that mismatch; these tests do not relax it or reproduce those production ACLs. External writer quiescence is mandatory; ReplaceFileW is not an atomic compare-and-swap against uncoordinated writers. A partial failure can retain an uncertain transaction; no automatic rollback occurs.
