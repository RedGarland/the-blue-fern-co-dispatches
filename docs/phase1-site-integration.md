# Phase 1 site integration

## Ownership model

The canonical generator owns dispatch evidence, prose, manifests, edition URLs, archives, feeds, and dispatch-specific assets. `public_site_shell.py` owns the shared Blue Fern header, navigation, footer, stylesheet, root homepage, `/dispatches/`, `/methodology/`, and `/about/` surfaces. The shell reads only rendered public output after dispatch bodies have been generated.

Private queues, proposed editions, scheduler state, operational logs, raw payloads, detail/paid roots, and historical review records are excluded from the shared model.

## Baseline failures

At untouched `29a3dab28b8f079206d8a875e86c2bcaeac66532`, the five `tests/test_care_line_signal_wire_publication.py` failures reproduce before integration:

- missing approved Care Line social-card template;
- generated social-card specification key mismatch (`event_type_label`);
- missing deterministic reviewed-record fixture;
- missing deterministic publication manifest/signal index artifacts.

The combined focused run also exposed existing test-harness permission failures under `output/test-runs`; those are unrelated to the site shell. A separate Gaza API mismatch remains a pre-existing source/test contract issue and was not changed by this integration.

## Release plan (not executed)

Source commit: the integration commit created for this work.

Pages baseline: `b9f5cf910a2ce989efdb6f28fd9df53fd1db3b83`.

Expected Pages destinations:

- `index.html`
- `dispatches/index.html`
- `methodology/index.html`
- `about/index.html`
- `assets/site.css`, `assets/bluefern.png`, `assets/bluefern.ico`, approved favicon assets
- shared-shell updates under `gaza/`, `food-line/`, and `care-line/`

Expected deletions: none. Pages sync was not run.

Validation before release: targeted generator tests, dispatch rendering tests, Care Line Signal Wire tests, publication-scope validation, `git diff --check`, route checks, UTF-8/mojibake scan, and responsive screenshots.

Rollback: revert the integration commit in the source repository; for a Pages release, restore the Pages checkout to the recorded baseline SHA before syncing. No rollback action was executed here.

Recommended deployment window: after a successful non-publishing Gaza/Food Line dry run and a separately reviewed Pages diff; do not pause the Gaza daily run unless validation finds a public-content regression.

## Phase 1 correction recovery

The correction boundary is intentionally limited to `generator.py`, `public_site_shell.py`, `phase1_site.py`, `tests/test_phase1_site.py`, and this document. Shared responsive CSS and global navigation live in the shell; public currentness and provenance are derived from rendered public manifests; Care Line’s logo is rendered from the canonical dispatch asset path. Food Line’s current landing is model-driven and omits RSS when no public RSS route exists. No-update editions remain archive records and are excluded from homepage developments.

The recovery preview is private and is not a Pages artifact. It must be compared with the integrated reference on the homepage, dispatch directory, and Gaza, Food Line, and Care Line landings before any later release decision.