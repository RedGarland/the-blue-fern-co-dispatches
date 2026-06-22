# Food Line Discovery Intake Bridge

This bridge connects the broad discovery layer to the normal Food Line daily intake path without changing publication rules.

## Discovery Versus Intake

- Discovery is broad and retention-friendly.
- Intake is source-shaped and still runs through the normal Food Line qualification checks.
- Publication remains strict and unchanged.

Discovery candidates are written to:

- `data/dispatches/food-line/discovery/YYYY-MM-DD/discovery_candidates.json`

The daily intake bridge writes source-shaped records to:

- `data/dispatches/food-line/sources/YYYY-MM-DD/discovery_sources.json`

and a review summary to:

- `output/review/food-line/YYYY-MM-DD/discovery_intake.json`

## How The Bridge Works

When `--use-discovery-candidates` is passed to `scripts/run_food_line_dispatch.py`, the runner:

1. Reads the discovery candidate file.
2. Optionally merges manual fallback records from:
   - `data/dispatches/food-line/discovery/YYYY-MM-DD/manual_fallback.json`
3. Writes source-shaped intake records for the normal Food Line pipeline.
4. Preserves discovery metadata in the intake review artifact and run manifest.

The bridge keeps discovery metadata such as:

- `discovered_title`
- `discovered_publisher`
- `canonical_url`
- `final_trace_url`
- `google_news_url`
- `publication_date`
- `state_or_territory`
- `metro`
- `query_family`
- `fetch_status`
- `fetch_error`
- `manual_review_required`
- `classification_status`
- `exclusion_reason`
- `pressure_terms_detected`
- `location_terms_detected`
- `duplicate_of`

## Google News Handling

Google News is discovery metadata only.

- `google_news_url` stays in the discovery/intake artifacts.
- `final_trace_url` stays on the publisher URL when one exists.
- Public source tables continue to point to the publisher URL, not the Google News wrapper.

## Blocked Fetches

Blocked fetches remain reviewable.

Examples:

- `403`
- `401`
- timeout
- paywall
- script-blocked
- parse failure

Blocked records keep:

- `final_trace_url`
- `fetch_status`
- `fetch_error`
- `manual_review_required = true`

They are not automatically publishable unless a manual fallback record is present and merged.

## Manual Fallback

Manual fallback records let a reviewer preserve a valid source even when automation cannot fetch it cleanly.

Required fields:

- `publisher`
- `canonical_url`
- `headline`
- `date`
- `location`
- `manually_reviewed_summary`
- `pressure_evidence_summary`
- `affected_groups`
- `limitations`
- `extraction_quality`
- `reviewer_or_source_note`
- `final_trace_url`

Manual fallback should not invent facts. It should preserve the publisher URL as the final trace URL and keep the original discovery trail in the intake artifact.

## Daily Run Example

Non-publishing validation flow:

```powershell
python scripts\run_food_line_discovery_expansion.py --date 2026-06-21
python scripts\run_food_line_dispatch.py --date 2026-06-21 --use-discovery-candidates --collect --no-generate-audio --dry-run
```

If discovery input is malformed, the bridge fails closed.
