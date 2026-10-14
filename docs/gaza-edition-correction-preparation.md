# Coordinated Gaza edition correction preparation

This private owner prepares one coherent correction to multiple projections in
one already published edition. It does not collect new stories, approve a
decision, render audio, stage a release, or mutate/publish Pages. Its separate
schemas cannot enter the legacy single-story approval or staging commands.
Those legacy operations and their default byte-preservation behavior are unchanged.

Use `scripts/gaza_edition_correction.py` with `prepare`, `propose`, or read-only
`verify`. No approval, audio-provider, scheduler or publication mode exists.

## Reasons and authority

Each target has one proposed reason:

- `scope_error`: saved evidence concerns an event outside Gaza and does not
  support the projection's placement as a core in-Gaza development.
- `insufficient_saved_evidence`: the saved input does not support its published
  projection. This does not assert that the underlying article is false,
  irrelevant, or outside Gaza. Its accuracy and relevance remain undetermined.

`prepare` leaves substantive decisions null and records the independent review
and committed audit still required for every target. Supplying paths or rendering
previews does not grant a decision or make a proposal. The output is a complete
**preparation packet**, with `packet.json`, `decision_request.json`, preserved
evidence, all prospective text/metadata replacements, and an unapproved audio
request. It is neither a formal proposal nor a staged package.

`propose` requires source-relative, committed, byte-identical review/audit pairs
for every target. The closed `gaza_edition_correction_review_v1` schema requires:

- exact story/edition/reason, lineage hash and bound review-packet hash;
- `resulting_review_state=substantively_reviewed`, reviewer, timezone-aware
  review time, substantive reason and principal source supporting passage with
  `evidence_type=source_scope` or `saved_input` matching the reason;
- `confirmed_public_scope_contamination` with an explicit false
  `assessment.materially_concerns_gaza` for a scope error;
- `confirmed_saved_evidence_insufficiency` with explicit false
  `assessment.saved_article_evidence_sufficient`, explicit null
  `assessment.materially_concerns_gaza` and
  `assessment.underlying_article_verdict=undetermined`, bound to the original
  `assessment.saved_input_sha256`, for insufficient evidence;
- explicit false archive, edition, queue, source-record, cluster, audio,
  publication and approval authorization fields.

Only then can the owner emit `gaza_edition_correction_proposal_v1` in
`proposal.json`. This remains non-authorizing. It is a separate edition-level
preapproval contract; the legacy single-story package approval/plan/stage modes
reject it. Any later package/audio approval, staging consumer and publication
operation require separate human review and authority. This preparation change
does not introduce them or imply that a proposal is deployable.

Never manufacture a scope-contamination decision to make evidence insufficiency
fit the old withdrawal contract. Actual review/approval artifacts are not
included with the implementation. Synthetic test decisions are fixtures only.

## Input and exact bindings

The request uses `gaza_edition_correction_request_v1` and supplies:

```json
{
  "schema_version": "gaza_edition_correction_request_v1",
  "edition_date": "2026-10-14",
  "correction_date": "2026-10-14",
  "correction_at": "2026-10-14T22:00:00Z",
  "expected_source_head": "EXACT_SOURCE_COMMIT",
  "expected_pages_head": "EXACT_PAGES_COMMIT",
  "review_packet_sha256": "EXACT_REVIEW_PACKET_SHA256",
  "tts_provider": "openai",
  "tts_model": "gpt-4o-mini-tts",
  "tts_voice": "alloy",
  "targets": [
    {"story_id": "gaza-story-2026-10-14-001", "reason": "scope_error"},
    {"story_id": "gaza-story-2026-10-14-002", "reason": "scope_error"},
    {"story_id": "gaza-story-2026-10-14-003", "reason": "insufficient_saved_evidence"}
  ]
}
```

Replace the example timestamp and commit/hash placeholders with the exact review
inputs. A later formal request adds each target's `review_path` and
`decision_audit_path`. Lineages must be present at
`data/agent-history/gaza/lineage/published-stories/STORY_ID.json` and pass the
existing lineage validator against pinned Git blobs. Prior-day source/event
dates are preserved; owning edition identity does not overwrite those dates.

The October 14 review packet binding is
`ba9606bc3027280519af94fffa00d0edacc0871f4db1872d46380577e277dc8c`.
That digest identifies the original review evidence, not a formal proposal.
Every saved evidence snapshot and current public binding must still match.
Source must descend from that review's checkpoint. Source and Pages must be clean
at the exact requested heads; Pages must be `gh-pages`. Head/hash drift fails
before exposing output, and heads are checked again immediately before the
atomic rename. Output must be outside both repositories. Exact replay is a
no-op; conflicting replay does not overwrite an earlier packet.

## Coherence and surfaces

All target removals are derived together from the original curation. Source
records remain as historical evidence, with only removed story links unlinked.
Every dedupe story collection, including nested, keyed and string-ID collections,
drops the removed current projections. Prior correction records are appended to,
never erased. Counts, publisher statistics and adequacy describe retained public
projections, while `saved_source_count` retains historical input coverage.
Untouched stories are not independently requalified by this correction owner.
A zero-story result explicitly says the saved evidence does not establish that
no news occurred. Preview publication metadata remains unapproved/unpublished.

Twenty text/metadata previews cover the edition HTML and four manifests,
correction page/record, RSS, historical audio metadata/transcript, correction
audio metadata/transcript, both podcast feeds, flash, audio index, Gaza index,
archive, root index and `dispatches/index.html`. A future rendered correction
MP3 would bring the staged set to 21 surfaces. No MP3 is generated here.

`source_quality_report.md` and the original dated MP3 are mandatory immutable
dependencies, copied and hash-bound. The original diagnostic's recommendation
is historical evidence, not permission to release the correction. A tracked
claim-bearing surface outside the known inventory causes rejection.

Historical audio script/transcript and original binary/enclosures remain
preserved as the historical recording, with explicit supersession and loss of
current-briefing status. The new correction script explains each reason
separately. Its provider/model/voice and script hash are deterministic and both
render/publication authority flags are false. Distinct correction feed URL/GUID
and future audio enclosure have pending-render status; zero enclosure length is
a private preview placeholder. Local rendering assets are discovered, copied and
hash-bound without fetching external assets. A later singleton flash owned by another edition
is left byte-identical and its own audio dependencies are also bound.

Existing text is patched through manifest-owned spans. JSON uses the existing
strict concrete-syntax parser and reversible edits, preserving retained row
lexemes, sibling keys/whitespace, BOM, newline convention and final-newline state.
HTML/XML spans are likewise inverse-checked. Malformed/ambiguous ownership,
unsupported encodings or mixed newline conventions fail closed. New
correction-only files use canonical UTF-8/LF.

```powershell
python scripts/gaza_edition_correction.py --mode prepare `
  --source-root SOURCE_CHECKOUT --pages-root CLEAN_PAGES_CHECKOUT `
  --request PRIVATE_REQUEST.json --review-packet ORIGINAL_REVIEW_PACKET.json `
  --output-root PRIVATE_PREPARATION_ROOT

python scripts/gaza_edition_correction.py --mode verify `
  --source-root SOURCE_CHECKOUT --pages-root CLEAN_PAGES_CHECKOUT `
  --packet PRIVATE_PREPARATION_ROOT/CORRECTION_ID/packet.json
```

Use a new output root when reviewed inputs change or when issuing a later formal
proposal. Preserve prior packets and their evidence; do not rebind them in place.
