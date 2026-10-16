# Gaza Pages history-preservation follow-up

This follow-up is based on merged PR #659 at `dfcdcbb940e10ffb4c28bc23c0cfc642a41a312a` and is intentionally separate from publication or approval work.

## Why merged PR #659 is insufficient for this release

PR #659 made Gaza correction history authoritative in its reconciliation and release-safety paths. The protected private replay exposed a remaining path-specific defect: `render_archive_index_rss` still rebuilt `gaza/archive.html`, `gaza/index.html`, and `gaza/rss.xml` from historical edition manifests when a Pages repository was supplied. That rebuild changed historical aggregate titles and summaries and could omit special correction/no-update records even when historical edition files themselves were unchanged.

The release therefore depends on this follow-up source fix. The Pages aggregate bytes are treated as the authoritative history baseline; the renderer inserts only the new edition, preserves existing correction/RSS/no-update bytes, and fails closed if the current edition date has partial identity across the three aggregate surfaces. It does not append a new edition on `include_current=False` paths, creates a missing month section safely, and date-orders a backdated recovery without replacing the actual latest archive/homepage entry.

## Existing PR conflict check

- PR #658 local continuation branch `codex/gaza-email-receipt-isolation-20261016` changes `docs/dispatches-project.md`, `scripts/run_gaza_daily_operator.py`, `src/bluefern_dispatches/operational_health.py`, and `tests/test_gaza_daily_operator.py`.
- PR #660 local continuation branch `codex/gaza-dedupe-material-update-20261016` changes `src/bluefern_dispatches/story_dedupe.py` and `tests/test_story_dedupe.py`.
- Neither branch changes the files below. Neither is required for this narrow one-new-story aggregate-preservation release. They remain separate work and are not merged or cherry-picked here.

## Exact files in this follow-up

- `src/bluefern_dispatches/generator.py`: preserve Pages aggregate bytes, add only the current edition, carry the limited-source label, and reject partial identity.
- `scripts/run_gaza_dispatch.py`: route Gaza Pages-bound aggregate rendering through the preservation helper.
- `tests/test_gaza_aggregate_preservation.py`: verify correction identity, RSS preservation, no-update notice preservation, limited-source labeling, partial-identity rejection, missing-month insertion, no-current-edition behavior, and backdated ordering/latest preservation.
- This document records the source dependency and conflict decision for review.

## Hash and line-ending note

Git records these text blobs with LF line endings. A Windows checkout may materialize CRLF depending on the repository attributes and local settings, so a raw local-file SHA256 is not interchangeable with a Git blob ID. The review package reports both the exact source commit/patch binding and raw artifact SHA256 values; any LF/CRLF comparison must normalize line endings first, while publication-byte hashes must be computed over the exact bytes that would be published.

The generated UNICEF output remains private, unpublished, and separately hash-bound to the protected source and Pages baseline. This change does not authorize Pages publication, audio release, Bluesky posting, or approval creation.
