# Food Line Agent-to-Intake Bridge (Phase 1)

The repository currently has no accessible scheduled Food Line agent result file or notification payload. The observable scheduled interface is `scripts/run_runner_dispatch.ps1`, which runs the normal Food Line discovery/dispatch workflow. Phase 1 therefore accepts an operator-supplied JSON fixture at that boundary; no production agent run is claimed or imported.

Agent findings are private and review-only. The schema is `food_line_agent_finding_v1` in `bluefern_dispatches.agent_findings`. It preserves the publisher URL, canonical HTTPS URL, source publication time, exact supporting passage, separate summary, query context, run identity, and private raw payload. IDs are deterministic; duplicate keys use canonical URL, normalized title, and publisher and exclude discovery time. `review_status` is always `pending_review` on import.

The adapter reuses `evaluate_food_line_pressure` and existing Food Line source taxonomy, freshness, geography, pressure, evidence, and source-role rules. Missing evidence and invalid URLs fail closed. Nothing is approved or publication-eligible automatically.

Use the CLI only for private inspection or import:

```powershell
python scripts/import_food_line_agent_findings.py dry-run --input agent.json --edition-date 2026-07-27 --agent-run-id example-run
python scripts/import_food_line_agent_findings.py import --input agent.json --edition-date 2026-07-27 --agent-run-id example-run
```

Imports write only `data/dispatches/food-line/agent-intake/YYYY-MM-DD/<run-id>.json` and its report under `agent-intake/reports`. Writes are deterministic and atomic. Dry-run writes nothing. The bridge does not write public output, Pages files, approvals, Bluesky artifacts, or scheduler state.

## Agent-run envelope

The CLI accepts one JSON object with `schema_version`, `agent_name`, `agent_run_id`, `started_at`, `completed_at`, `search_window`, `findings`, and `coverage_notes`. Each finding may provide the fields represented by `FoodLineAgentFinding`; `finding_id` and `duplicate_key` may be null because the adapter computes them. The canonical synthetic example is `docs/examples/food-line-agent-run.example.json`.

Validate without writing:

```powershell
python scripts/import_food_line_agent_findings.py validate --input <path-to-agent-run.json>
```

Validation reports envelope validity, count, invalid URLs, missing evidence or publication dates, within-run duplicates, and fields requiring human review. The private inbox is `data/dispatches/food-line/agent-inbox/`; imported inputs are preserved under `processed/YYYY-MM-DD/`. No Downloads scan or filesystem watcher is used.

## Scheduled-agent handoff

The future scheduled agent should return a short human-readable summary followed by one fenced JSON object in the envelope format. JSON strings must contain normal escaped text only, exact source URLs, and exact supporting passages where available. Use JSON `null` for unknown values. Every finding must have `review_status` set to `pending_review`; the agent must not emit an approval decision.

Prompt amendment:

> After your short summary, return exactly one fenced JSON object using the Food Line agent-run envelope. Include all required top-level fields and one finding per candidate. Preserve exact HTTPS publisher URLs and exact supporting passages; keep passages separate from summaries. Use JSON null for values you cannot verify. Set every finding's review_status to pending_review. Do not approve, publish, post, or label anything publication-ready.
