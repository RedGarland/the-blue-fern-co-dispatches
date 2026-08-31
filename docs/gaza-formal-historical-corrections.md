# Gaza Formal Historical Corrections

This capability packages a correction to an already published Gaza story. It is
not the daily generator, a historical-edition publisher, or an editorial approval
workflow. It cannot mutate or publish Pages.

## Authority boundary

The workflow has a non-authorizing preapproval phase and an independently
committed package-approval phase:

1. `propose` derives a private proposal and all deterministic preview surfaces
   from validator-owned evidence. The proposal is hash-bound to the
   substantively reviewed `corrected` decision and audit, every private evidence
   hash, the source commit, the exact Pages head, every prior public artifact
   hash, and every proposed preview hash. It also emits a non-authorizing approval
   request. The operator supplies no hashes.
2. A separately committed `gaza_formal_historical_correction_release_approval_v1`
   artifact, read with `git show <approval-ref>:<approval-path>`. The approval
   binds the proposal, source commit, Pages head, correction identity, complete
   preview-set fingerprint, and deterministic correction-audio request. A
   working-tree file is not accepted as authority, and the approval commit may
   contain no other source change.

The private review and decision audit must continue to say that publication,
queue, edition, archive, source-record, cluster, and audio authority are false.
The package approval authorizes only private package construction and rendering
the exact approved audio script/configuration; its
`publication_authorized` field must also be false. Publishing the staged package
therefore remains a separate human-controlled operation that this command cannot
perform.

No approval artifact or proposal for GZ-01 is included by this capability change.

## Stable identity and public history

The operation retains the original story ID, owning edition date, stable-event
fingerprint, prior-claim fingerprint, and corrected-claim fingerprint. Its
correction ID is a deterministic digest of those immutable identities. The
correction date is distinct from the event and owning-edition date.

The corrected story replaces `new_deaths: 1` with `new_deaths: 2`; it is never an
increment of two. The corrected story omits `new_injuries` because the reviewed
sources disagree. Both attributed injury reports remain in the correction record.
The edition manifest must attest that aggregates were recomputed from corrected
story versions.

Reader-facing surfaces use natural dates and ordinary correction prose. They do
not display or speak correction IDs, story IDs, version names, ISO dates, or a
pipe-delimited metadata record. Those machine identities remain in correction
manifests, structured metadata, feed GUIDs, technical links, hashes, and approval
materials. The existing edition and audio are marked as corrected or superseded
rather than silently erased. Today's Read visibly marks and links the corrected
story, and the full story retains a visible correction notice with attributed
source links and unresolved injury reports. RSS and both podcast feeds require a
readable correction item, distinct URL and GUID, and (for podcasts) a distinct
approved correction-audio enclosure.

## Audio boundary

Preapproval “correction audio” means a deterministic request containing the
reviewed script, script hash, provider, model, voice, correction identity, and
future public path. It is not a rendered binary and has both
`render_authorized` and `publication_authorized` set to false.

The prior flash briefing is bound to its owning edition by the exact generated
UID `gaza-YYYY-MM-DD` and the exact dated MP3 path
`/gaza/audio/YYYY-MM-DD.mp3`. The correction validator accepts that canonical
site path or the equivalent HTTPS URL on `dispatches.thebluefernco.com`; it does
not accept an edition-page redirect. The same date, dispatch, MP3 filename,
transcript identity, and enclosure identity must agree in the edition audio
metadata, existing MP3, transcript, and both Gaza podcast feeds. Queries,
fragments, alternate hosts, encoded paths, traversal, and filename lookalikes
fail closed.

After package/audio approval is committed, an audio worker may render that exact
request. TTS bytes can be nondeterministic, so the package approval does not claim
to bind bytes that do not yet exist. `stage` records the rendered MP3 hash and
the binary-dependent podcast enclosure length, then records the complete
19-surface package hash. A later publication reviewer must approve that staged
manifest and rendered binary. This PR does not implement that later publication
approval or any audio-provider call.

## Complete package boundary

The validator rejects a partial package. It requires replacement representations
for:

- edition HTML, curation, dedupe, and edition manifests;
- a public correction page and correction manifest;
- RSS;
- prior-audio supersession metadata and transcript;
- correction audio, metadata, and transcript;
- both podcast feeds;
- flash briefing;
- Gaza audio, dispatch, archive, and root indexes.

The edition source manifest, source-quality report, and original MP3 remain
immutable dependencies whose exact hashes are checked. The correction manifest
holds the correcting source attribution. This preserves the historical source
ledger and original spoken artifact while making the supersession explicit.

## State machine and command ownership

| Transition | Owner | Persistent output | Authority gained |
| --- | --- | --- | --- |
| reviewed → proposed | correction CLI `propose` | deterministic private proposal, 18 text/metadata previews, audio request, non-authorizing approval request | none |
| proposed → package approved | independent reviewer using `approve-package`, followed by normal Git review/commit | approval artifact | private package construction and approved-request audio rendering only |
| approved → planned | correction CLI `plan` | none | none beyond committed approval |
| planned → staged | correction CLI `stage` plus an approved-request audio render | atomic private 19-surface package and staged manifest | none; publication remains false |
| staged → verified | correction CLI `verify-staged` | none | none |
| verified → published | later publication authority and atomic Pages application | not implemented | not available in this PR |

`propose` writes only under a caller-selected root outside the source and Pages
repositories. A temporary directory and atomic rename expose the complete output.
Exact replay is an `idempotent_noop`; conflicting replay fails. `plan` validates
the committed approval and writes nothing. `stage` uses the same atomic pattern
outside both repositories. The approval ref must resolve to the approval-only
commit itself. The source checkout may be that exact commit or its exact normal
two-parent merge: the proposal source is first parent, the approval commit is
second parent, the merge and approval commits have the same tree, and the only
proposal-to-merge change is the byte-identical approval artifact. Descendants,
rewritten commits, intervening base changes, conflict edits, and unrelated files
fail closed. The validated source commit and approved Pages head remain bound
through staging, so a stale plan cannot cross that boundary. `verify-staged`
rechecks every staged hash plus source and Pages heads without writing.

The validator fails closed for absent or duplicated lineage, wrong story/date or
domain, changed fingerprints, evidence or public hash drift, altered approval,
resolved injury uncertainty, double-counted deaths, partial public surfaces,
stale audio/feed/transcript content, new-edition behavior, and second-story
behavior.

For legacy edition HTML, the correction renderer binds the target through the
stable story's unique curation position and source-manifest URLs. It requires
one matching Today’s Read projection and one matching full article, then adds
an explicit story anchor/link to the corrected preview. It does not globally
replace matching prose or reserialize the page.

Existing UTF-8 HTML/XML public text artifacts are read as bytes before patching.
The renderer preserves an existing UTF-8 BOM, CRLF-versus-LF convention, final
newline state, and all bytes outside the validated insertion or story-owned
ranges. Mixed newline conventions, bare carriage returns, unsupported encodings,
malformed markup, duplicate insertion boundaries, and ambiguous target ranges
fail before output is created. New correction-only files continue to use the
repository's canonical UTF-8/LF format.

Existing public JSON follows the same lexical-preservation rule and is not
globally reserialized. A strict concrete-syntax parser resolves bounded value or
insertion spans through the owning edition, story ID, manifest relationship, and
unique object path. Before mutation, the zero-edit representation must reproduce
the source bytes exactly. Each patch preserves the original BOM, encoding,
newline and final-newline state, sibling-key order, unrelated whitespace,
escaping, and numeric/scalar spelling. New correction-owned members have a
deterministic append order without reordering existing members. The renderer
also applies the inverse span edits in memory and requires exact reconstruction
of the original bytes before exposing a preview. Missing, duplicate, ambiguous,
type-drifted, malformed, mixed-newline, or unsupported inputs fail closed. Only
new correction-owned JSON files use canonical deterministic serialization.

Run the commands below from the repository root. The wrapper bootstraps this
checkout's local `src` directory, so no caller-supplied `PYTHONPATH` or globally
installed package is required.

Example proposal creation:

```powershell
python scripts/gaza_historical_correction.py --mode propose `
  --source-root C:\path\to\source --pages-root C:\path\to\pages `
  --story-id gaza-story-YYYY-MM-DD-NNN `
  --review-path data/agent-history/gaza/reviews/example.json `
  --decision-audit-path data/agent-history/gaza/reviews/decisions/example.json `
  --correction-date YYYY-MM-DD --proposal-root C:\private\proposals `
  --tts-provider openai --tts-model gpt-4o-mini-tts --tts-voice alloy
```

An independent reviewer creates the commit-ready approval exclusively from the
generated request:

```powershell
python scripts/gaza_historical_correction.py --mode approve-package `
  --source-root C:\path\to\source `
  --pages-root C:\path\to\pages `
  --proposal C:\private\proposals\CORRECTION_ID\proposal.json `
  --input-root C:\private\proposals\CORRECTION_ID `
  --approval-request C:\private\proposals\CORRECTION_ID\approval_request.json `
  --approval-output C:\path\to\source\approvals\gaza\example.json `
  --approval-id REVIEW-ID --approver "Reviewer Name" `
  --approved-at 2026-09-02T12:00:00+00:00
```

After that artifact is independently reviewed and committed, read-only planning
uses:

```powershell
python scripts/gaza_historical_correction.py `
  --mode plan `
  --source-root C:\path\to\source `
  --pages-root C:\path\to\pages `
  --proposal C:\private\proposals\CORRECTION_ID\proposal.json `
  --input-root C:\private\proposals\CORRECTION_ID `
  --approval-ref EXACT_APPROVAL_ONLY_COMMIT_SHA `
  --approval-path approvals/gaza/example.json
```

If protected source changes after an approval merge, that older proposal is
superseded even when its evidence files are unchanged. Hash-verify and quarantine
the old proposal without overwriting it, generate a fresh proposal against the
new protected source commit, obtain a new approval-only commit whose sole parent
is that source commit, and merge it normally with no intervening base change.
Only that fresh direct approval commit or its exact merge topology may plan.

Staging adds:

```powershell
--mode stage --rendered-audio C:\private\render.mp3 `
  --staging-root C:\private\correction-staging
```

Verification uses `--mode verify-staged --package-root <staged-package>` with
the same proposal and approval arguments.

There is intentionally no `apply`, `publish`, `push`, audio-generation, social,
email, or scheduler mode.
