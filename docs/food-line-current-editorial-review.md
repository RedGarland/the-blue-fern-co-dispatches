# Food Line current editorial review

The current-signal review queue is a private editorial boundary between current Food Line intake and any proposed reader-facing edition. It does not replace source qualification in `scripts/run_food_line_dispatch.py`, and it does not grant publication authority.

Private paths:

- Queue: `data/dispatches/food-line/review/current-signal-review.json`
- Proposed edition JSON: `data/dispatches/food-line/review/proposed-editions/YYYY-MM-DD.json`
- Operator preview: `data/dispatches/food-line/review/proposed-editions/YYYY-MM-DD.md`

Only current production inputs may support queue items:

- `data/dispatches/food-line/agent-inbox/`
- `data/dispatches/food-line/agent-intake/`
- `data/dispatches/food-line/discovery/`
- `data/dispatches/food-line/sources/`
- `output/review/food-line/`

The validator rejects `data/agent-history/` and `data/agent-history-staging/`. Historical review status, even `substantively_reviewed`, never authorizes entry into this queue.

Validate or inspect without writing:

```powershell
python scripts/manage_food_line_current_review.py validate
python scripts/manage_food_line_current_review.py inspect
```

Record an editorial decision:

```powershell
python scripts/manage_food_line_current_review.py decide `
  --review-item-id <REVIEW_ITEM_ID> `
  --decision approve `
  --decided-by <OPERATOR> `
  --editorial-note "Approved for private draft assembly only."
```

Allowed decisions are `approve`, `approve_with_edit`, `hold`, and `reject`. Use `--headline` or `--summary` with `approve_with_edit`. Every decision leaves `publication_eligible` false.

Generate a private preview from pending or approved queue items (`hold` and `reject` are excluded):

```powershell
python scripts/manage_food_line_current_review.py propose --dry-run
python scripts/manage_food_line_current_review.py propose
```

The real `propose` command writes only the private proposed-edition JSON and Markdown paths above. It never invokes public rendering, Pages synchronization, audio, maps, Bluesky, publication ledgers, or schedulers.

After every selected item has an `approve` or `approve_with_edit` decision, canonical local generation is a separate guarded step:

```powershell
python scripts/run_food_line_dispatch.py --date YYYY-MM-DD --approved-proposal "data/dispatches/food-line/review/proposed-editions/YYYY-MM-DD.json" --skip-bluesky --no-generate-audio
```

This command verifies the proposal and current-review queue hashes, dates, identities, decisions, operator audit fields, source evidence, HTTPS URLs, freshness, duplicate state, and nonhistorical intake boundary. It generates unpublished source output and a private release manifest. It does not grant publication approval, sync Pages, post to Bluesky, generate audio or maps, or change a schedule.
