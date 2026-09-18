# Food Line date reconciliation

`python scripts/reconcile_food_line_date.py --date YYYY-MM-DD` is the private Food Line front door for one-date completeness checks.

The command begins in read-only mode. It inventories original runtime evidence, retained Source Watch inputs, Current Intake artifacts, publication-decision receipts, durable coverage-gap state, historical intake, historical events, historical agent archive records, and prior date-reconciliation records. It emits one final JSON object with a single terminal state.

Use `--apply` only when private repair is authorized:

```powershell
python scripts/reconcile_food_line_date.py --date 2026-09-09 --apply
```

Apply mode may write only private evidence under Food Line reconciliation, historical-intake, historical-reconstruction, and coverage-gap paths. It never publishes, writes Pages, consumes approval, posts to social channels, generates audio, or mutates scheduled tasks.

## What Complete Means

A Food Line date is complete only when every expected stage is accounted for by one of these bounded explanations:

- original runtime evidence is present and internally consistent;
- the stage was a legitimate safe no-op or not applicable;
- retained original evidence allowed deterministic private replay;
- nonoriginal historical reconstruction accounted for the date with explicit provenance and terminal accounting;
- the date is explicitly evidence-exhausted, which is not complete.

A public edition is neither required nor sufficient. The daily publication stage is closed by a real publication receipt, a safe no-op such as `skipped_not_release_ready`, or another legitimate terminal decision. Missing publication-decision evidence remains a gap.

## Terminal States

The controller returns exactly one high-level state:

- `COMPLETE_ORIGINAL`: all required stages are accounted for by original runtime evidence.
- `COMPLETE_REPAIRED`: an original gap was closed from retained original evidence, such as deterministic historical Current Intake replay.
- `COMPLETE_RECONSTRUCTED`: original evidence was incomplete, but bounded nonoriginal reconstruction established complete terminal accounting.
- `REVIEW_REQUIRED`: recovered or reconstructed source-backed material needs editorial disposition before the date is complete.
- `EVIDENCE_EXHAUSTED`: bounded evidence could not establish a complete evidence set.
- `BLOCKED_ERROR`: malformed, contradictory, or structurally unsafe evidence prevents reconciliation.

Only the first three states set `date_complete: true`.

## Original, Repaired, Reconstructed

Original evidence is always preferred. If retained Source Watch evidence contains one exact run, run state, query plan, discovery candidates, and matching dated handoff, the controller can orchestrate the existing historical Current Intake replay in `--apply` mode. The replay is private, date-bound, network-free, and preserves the live current queue.

If the original Source Watch run or handoff is absent, the controller does not fabricate original evidence or perform web research itself. It can write a nonoriginal historical reconstruction record only after a Codex/operator research layer supplies a validated `food_line_historical_reconstruction_input_v1` packet under:

```text
data/dispatches/food-line/historical-reconstruction-inputs/<date>.json
```

Accepted packets are copied immutably into:

```text
data/dispatches/food-line/historical-reconstruction/<date>/
```

Reconstruction records declare `reconstruction_mode = nonoriginal_historical_research`, preserve the packet `research_mode`, `network_access`, and `researched_at`, keep source publication dates, event dates, and reconstruction timestamps distinct, and account for every finding through a terminal disposition. If no validated packet exists, `--apply` does not write reconstruction evidence and the next action remains `perform_bounded_historical_research`.

## Retained Evidence Preference

The controller checks retained historical evidence before treating a date as exhausted. Relevant surfaces include:

- `data/dispatches/food-line/historical-intake/<date>/`
- `data/dispatches/food-line/historical-events/<date>/`
- `data/dispatches/food-line/coverage-gaps/<date>.json`
- `data/agent-history/food-line/`
- prior private historical reconstruction and date-reconciliation records

Reviewed recovered events and retained historical archive records are provenance; they are not copied into current queues or public output.

## Reconstruction Limits

Historical reconstruction is date-bounded. Today's page state, today's pantry status, or today's publication timestamp cannot substitute for evidence about the target date. A reconstructed zero-finding result is complete only when bounded coverage is sufficient and terminal accounting is explicit. Otherwise the date remains `EVIDENCE_EXHAUSTED`.

## Editorial And Publication Boundary

The controller never publishes. Reconstructed candidates are written only to private review artifacts with publication and approval flags false. A date with pending reconstructed candidates remains `REVIEW_REQUIRED` until a separate human editorial workflow disposes of those candidates.

## Reruns And Idempotency

Rerunning a completed date should produce an idempotent no-op unless new evidence appears. Apply mode appends transition history instead of silently replacing prior decisions, and late new evidence can reopen a previously complete date.

## Codex Historical Research Packet

The repository CLI is deterministic and is not a general-purpose web crawler. For a request such as `Reconcile Food Line for YYYY-MM-DD`, the intended Codex workflow is:

1. Run `python scripts/reconcile_food_line_date.py --date YYYY-MM-DD`.
2. If original or retained evidence is sufficient, stop or use deterministic private repair.
3. If the output asks for bounded historical research, Codex performs target-date web research outside the repository controller.
4. Codex writes a reviewed packet at `data/dispatches/food-line/historical-reconstruction-inputs/YYYY-MM-DD.json`.
5. The packet is checked for target-date fidelity, traceable URLs, supporting evidence, terminal disposition for every discovery, and coverage accounting.
6. Run `python scripts/reconcile_food_line_date.py --date YYYY-MM-DD --apply`.
7. The controller validates and archives the packet, writes private reconstruction/reconciliation evidence, and returns `COMPLETE_RECONSTRUCTED`, `REVIEW_REQUIRED`, or `EVIDENCE_EXHAUSTED`.
8. Nothing is published automatically.

The packet schema is `food_line_historical_reconstruction_input_v1`. It must include `target_date`, `researched_at`, `research_mode`, `network_access`, query/source attempts, coverage summaries, findings, and limitations. Codex web research packets use `research_mode = codex_bounded_historical_web_research` and set `network_access` to the actual research value. The controller rejects wrong dates, future dates, missing or malformed schemas, unsupported dispositions, contradictory source/event dates, and duplicate candidate IDs.

Accepted packets are immutable provenance. Exact reruns are idempotent. A changed packet for the same date fails closed rather than silently replacing the archived research input.

## Archive Supplementation

Historical reconciliation may supplement the private archive, but it must never rewrite original runtime history. Keep provenance distinct:

- `ORIGINAL_RUNTIME`: evidence captured by the original scheduled run.
- `RETAINED_RECOVERY`: retained original evidence replayed or reconciled later.
- `HISTORICAL_RECONSTRUCTION`: nonoriginal bounded research added later with explicit packet provenance.

A later reconstruction can make the archive more complete without pretending the material was captured during the original Source Watch run.
