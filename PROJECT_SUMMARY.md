# Dispatches From The Blue Fern Co — Project Summary

## Purpose

Dispatches From The Blue Fern Co. is a source-backed reporting and monitoring system. Public factual output must remain traceable to durable source records and publication is separated from collection, review, operational recovery, and source-repository deployment.

## Current operational families

- `gaza` — daily public Gaza dispatch. Public/free. Publication has explicit release safeguards.
- `food-line` — U.S. food-pressure monitoring and dispatch workflow with source watch, intake, review, and guarded publication.
- `care-line` — U.S. and territories healthcare-access monitoring and dispatch workflow with source-specific collection evidence, review, and guarded publication.
- `ice` — ICE activity/consequences monitor. Current production design is monitor/staging-oriented; absence of public publication is not by itself a failure.
- `american-pressure` — weekly household/system-pressure workflow with daily intake and separately reviewed weekly public output.
- `cascadia` — intentionally inactive. Historical public archive remains available, but there is no active Cascadia production schedule and no runner should be enabled without explicit operator authorization.

## Operations and autonomy

The Blue Fern Operator coordinates operational health, incidents, bounded remediation, receipts, and engineering handoffs.

Current project authority is intentionally split:

- `AGENTS.md` governs Codex / implementation-agent behavior.
- `ops/operator/remediation-policy.yaml` governs actions the autonomous Blue Fern Operator may execute.
- `docs/project-contract.md`, production-readiness, operational-health, and publication-safety documents define system, editorial, traceability, and public-release invariants.

Routine source-repository engineering can proceed through normal PR validation and guarded deployment under `AGENTS.md`. Public publication, editorial decisions, credentials/security changes, destructive operations, and other explicit authority boundaries remain separately controlled.

## Runtime health sources

For migrated dispatches, prefer exported operational-status artifacts and canonical runtime receipts over inference from recent commits or Pages activity.

Key exported status roots include:

- `ops/status/food-line/latest.json`
- `ops/status/system/latest.json`

Dispatch-specific receipts live under the production runners' `status/operational-health/` trees and are consumed by the operational-status exporter.

## Repository structure

- `src/bluefern_dispatches/` — core collection, normalization, status, rendering, and replay modules.
- `scripts/` — production wrappers, operator, guarded sync, diagnostics, publishing, and workflow entry points.
- `data/dispatches/` — source registries and durable dispatch-specific records.
- `ops/operator/` — Operator configuration/policy plus sanctioned runtime state in production checkouts.
- `ops/status/` — exported operational-status artifacts.
- `output/site/` — generated public site source output.
- `bluefern-dispatches-pages/` — separate local Pages checkout when present; not the source repository.
- `docs/` — current operating contracts and workflow documentation.

## Core invariants

- No public factual claim without a traceable source.
- Missing proof is not success.
- Collection/review/recovery and publication are separate stages.
- `output/detail/` and `output/paid/` must never leak into `output/site/`.
- Production runners must use guarded synchronization and preserve sanctioned runtime evidence.
- A merged source PR is not proof of production deployment or public publication.
- Cascadia remains inactive unless explicitly reactivated by the operator.
- Persistent external source restrictions remain visible rather than being hidden to create a green status.

## Start here

Agents and maintainers should read:

1. `AGENTS.md`
2. `README.md`
3. `docs/project-contract.md`
4. `docs/production-readiness-contract.md`
5. `docs/operational-health-receipts.md`
6. `docs/pages-publish-safety.md`

For current implementation details, prefer source code, tests, protected-branch state, and current operational receipts over historical bootstrap prompts or old generated logs.
