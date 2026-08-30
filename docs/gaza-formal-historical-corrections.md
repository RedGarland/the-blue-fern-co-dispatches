# Gaza Formal Historical Corrections

This capability packages a correction to an already published Gaza story. It is
not the daily generator, a historical-edition publisher, or an editorial approval
workflow. It cannot mutate or publish Pages.

## Authority boundary

Planning and staging require two independent inputs:

1. A proposal hash-bound to the private published-story lineage, the
   substantively reviewed `corrected` decision and audit, every private evidence
   hash, the source commit, the exact Pages head, every prior public artifact
   hash, and every proposed replacement hash.
2. A separately committed `gaza_formal_historical_correction_release_approval_v1`
   artifact, read with `git show <approval-ref>:<approval-path>`. The approval
   binds the proposal, source commit, Pages head, correction identity, complete
   artifact-set fingerprint, and correction audio. A working-tree file is not
   accepted as authority.

The private review and decision audit must continue to say that publication,
queue, edition, archive, source-record, cluster, and audio authority are false.
The later approval authorizes only package construction and audio inclusion; its
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

Every corrected representation must show the prior claim, corrected claim,
correction ID, story ID, and correction date. The existing edition and audio are
marked as corrected or superseded rather than silently erased. RSS and both
podcast feeds require a distinct correction item, URL, GUID, and (for podcasts) a
distinct approved correction-audio enclosure.

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

## Modes and failure behavior

`plan` validates everything and writes nothing. `stage` first performs the same
validation, then copies the complete approved representation set into a temporary
directory under a caller-supplied staging root outside the source and Pages
repositories. A single atomic rename exposes the completed package. Failed copies
remove the temporary directory. Exact replay is an `idempotent_noop`; any content
or manifest conflict is rejected.

The validator fails closed for absent or duplicated lineage, wrong story/date or
domain, changed fingerprints, evidence or public hash drift, altered approval,
resolved injury uncertainty, double-counted deaths, partial public surfaces,
stale audio/feed/transcript content, new-edition behavior, and second-story
behavior.

Example plan invocation after a later approval exists:

```powershell
python scripts/gaza_historical_correction.py `
  --source-root C:\path\to\source `
  --pages-root C:\path\to\pages `
  --proposal C:\outside\reviewed-proposal.json `
  --input-root C:\outside\reviewed-artifacts `
  --approval-ref refs/remotes/origin/add/pages-repo-default `
  --approval-path approvals/gaza/example.json `
  --mode plan
```

Staging adds `--mode stage --staging-root C:\outside\correction-staging`.
There is intentionally no `apply`, `publish`, `push`, audio-generation, social,
email, or scheduler mode.
