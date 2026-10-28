"""Preserve one already-public correction during later local generation.

This is not a correction/release consumer. It cannot activate a registry,
materialize an unpublished proposal, approve a later edition, or write Pages.
The ordinary reconciliation refusal remains the default.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from . import gaza_corrected_manifest_reader as reader
from . import gaza_correction_activation as activation


def _destination_path(base: Path, relative: str) -> Path:
    """Validate absent destinations too; a missing leaf can conceal a redirect."""
    from .generator import _reconciliation_assert_ordinary_path

    target = base / relative
    _reconciliation_assert_ordinary_path(target)
    for parent in reversed(target.absolute().parents):
        if parent.exists():
            reader._check(parent, directory=True)
    if target.exists():
        reader._check(target, directory=False)
    return target


def _local_audio_absence(base: Path) -> None:
    # Generated replicas have no Git tree. Missing aggregate files are allowed;
    # present files must be ordinary, parseable, and contain no Oct25 identity.
    from .generator import _reconciliation_assert_ordinary_path

    for relative in (
        f'gaza/audio/{activation.DATE}.json',
        f'gaza/audio/{activation.DATE}-transcript.html',
        f'gaza/audio/{activation.DATE}.mp3',
    ):
        path = base / relative
        _reconciliation_assert_ordinary_path(path)
        if path.exists() or path.is_symlink():
            raise reader.CorrectedManifestError('local corrected history has dated audio')
    for relative in activation.edition.NO_AUDIO_AGGREGATE_PATHS:
        path = base / relative
        _reconciliation_assert_ordinary_path(path)
        if not path.exists():
            continue
        raw = reader._read_regular(base, relative)
        variants = activation.edition._decoded_aggregate_variants(raw.decode('utf-8-sig'), path)
        if any(activation.DATE in value for value in variants):
            raise reader.CorrectedManifestError('local corrected history has audio references')


def aggregate_history_proof(pages_root: Path, site_root: Path, edition_date: str | None) -> dict | None:
    """Rebind aggregate generation to current public history, not cached bytes."""
    from .generator import _reconciliation_assert_ordinary_path

    _reconciliation_assert_ordinary_path(pages_root)
    _reconciliation_assert_ordinary_path(site_root)
    pages_root = Path(pages_root).absolute()
    site_root = Path(site_root).absolute()
    marked = (pages_root / reader.CORRECTION_PATH).exists() or (pages_root / reader.CORRECTION_PATH).is_symlink()
    if pages_root.name != 'bluefern-dispatches-pages':
        if marked:
            raise reader.CorrectedManifestError('aggregate history has no verified source root')
        return None
    proof = activation.load_activation(pages_root.parent)
    if proof is None and not marked:
        return None
    if site_root == pages_root or pages_root in site_root.parents or site_root in pages_root.parents:
        raise reader.CorrectedManifestError('aggregate output overlaps or differs from verified Pages boundary')
    if proof is None or proof.get('authority_basis') != 'fixed_public_history' or proof.get('publication_authorized') is not False:
        raise reader.CorrectedManifestError('aggregate history requires committed fixed-public activation')
    for name in ('index.html', 'archive.html', 'rss.xml'):
        relative = 'gaza/' + name
        if reader._read_regular(pages_root, relative) != activation._blob(pages_root, proof['current_pages_head'], relative):
            raise reader.CorrectedManifestError('aggregate baseline differs from current committed public history')
    former = former_latest_record(reader._read_regular(pages_root, 'gaza/index.html').decode('utf-8'))
    if former is not None:
        relative = 'gaza/editions/' + former[0] + '/edition_manifest.json'
        raw = activation._blob(pages_root, proof['current_pages_head'], relative)
        manifest = activation._json(raw)
        if reader._read_regular(pages_root, relative) != raw or manifest.get('edition_date') != former[0] or manifest.get('dispatch_slug') != 'gaza':
            raise reader.CorrectedManifestError('former latest does not bind its committed public edition manifest')
    try:
        if date.fromisoformat(edition_date).isoformat() != edition_date or edition_date <= activation.DATE:
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise reader.CorrectedManifestError('aggregate history requires an exact later edition date') from exc
    if reader._replica_kind(site_root) == 'original':
        raise reader.CorrectedManifestError('aggregate history cannot accompany a stale original profile')
    _local_audio_absence(site_root)
    for name in ('index.html', 'archive.html', 'rss.xml'):
        _destination_path(site_root, 'gaza/' + name)
    return proof


def reconcile_published_profile(
    root: Path,
    site_root: Path,
    pages_root: Path | None,
    *,
    before_date: str | None,
    dry_run: bool,
    wrote: list[str],
    backup_root: Path,
) -> bool:
    """Copy only the fixed public profile; return whether it owns Oct25.

    Every invocation revalidates the committed activation, published preimages,
    exact public commit/tree, current descendant, owning projections, and audio
    absence. Unknown/partial replicas refuse before any copy or backup. An exact
    old original is retained in a private backup, never discarded or re-rendered.
    """
    from .generator import _reconciliation_assert_ordinary_path

    for path in (root, site_root, pages_root):
        if path is not None:
            _reconciliation_assert_ordinary_path(path)
    proof = activation.load_activation(root)
    if proof is None:
        reader.assert_generic_reconciliation_allowed(root, site_root, pages_root)
        return False
    if proof.get('authority_basis') != 'fixed_public_history' or proof.get('publication_authorized') is not False:
        raise reader.CorrectedManifestError('reconciliation requires fixed already-public history, not release authority')
    try:
        if not isinstance(before_date, str) or date.fromisoformat(before_date).isoformat() != before_date or before_date <= activation.DATE:
            raise ValueError()
    except ValueError as exc:
        raise reader.CorrectedManifestError('corrected history reconciliation requires an exact later edition date') from exc
    root = Path(root).absolute()
    site_root = Path(site_root).absolute()
    if site_root != root / 'output/site' or pages_root is None or Path(pages_root).absolute() != root / 'bluefern-dispatches-pages':
        raise reader.CorrectedManifestError('corrected history reconciliation roots differ from verified runtime')
    pages_root = Path(pages_root).absolute()
    states = {relative: reader._replica_kind(root / relative) for relative in reader.REPLICA_ROOTS}
    if states['bluefern-dispatches-pages'] != 'corrected':
        raise reader.CorrectedManifestError('fixed public history lacks exact corrected Pages replica')
    for relative in ('output/site', 'output/dispatches'):
        _local_audio_absence(root / relative)
    payloads = {relative: reader._read_regular(pages_root, relative) for relative in reader.PROFILE_HASHES}
    for relative in payloads:
        _destination_path(site_root, relative)
    if states['output/site'] == 'corrected':
        return True
    if dry_run:
        wrote.extend(str(site_root / relative) for relative in payloads)
        return True

    # Recheck the complete proof immediately before mutation. No cached context
    # or caller-supplied digest can authorize a changed Pages/source revision.
    if activation.load_activation(root) != proof:
        raise reader.CorrectedManifestError('public history changed during reconciliation')
    if {relative: reader._replica_kind(root / relative) for relative in reader.REPLICA_ROOTS} != states:
        raise reader.CorrectedManifestError('local correction replicas changed during reconciliation')
    if states['output/site'] == 'original':
        from .generator import _assert_reconciliation_backup_boundary, _preserve_conflicting_gaza_correction

        backup = _assert_reconciliation_backup_boundary(backup_root, root, pages_root)
        _preserve_conflicting_gaza_correction(site_root / 'gaza/editions/2026-10-25', backup, activation.DATE)
    for relative, raw in payloads.items():
        target = _destination_path(site_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        _destination_path(site_root, relative)
        # Exclusive writes never overwrite an unexpected file. Interrupted local
        # copies remain visible as partial replicas and fail closed on the next run.
        with target.open('xb') as stream:
            stream.write(raw)
        wrote.append(str(target))
    if reader._replica_kind(site_root) != 'corrected':
        raise reader.CorrectedManifestError('materialized public profile differs')
    if activation.load_activation(root) != proof:
        raise reader.CorrectedManifestError('public history changed during reconciliation')
    return True


def former_latest_record(document: str) -> tuple[str, str, str] | None:
    """Project an existing daily card into a visible row, without new prose.

    Title, summary, date, publisher/report metadata and CTA inner HTML are copied
    from the verified public aggregate, never inferred from mutable source data.
    Unknown/ambiguous linked-card shapes refuse instead of dropping history.
    """
    nodes = activation.legacy._StrictHtmlTreeParser(document).finish()
    cards = [n for n in nodes if n.tag == 'article' and n.attrs.get('class') == 'gaza-latest-card']
    if not cards:
        return None
    card = activation.edition._one(cards, 'unique former latest card')
    links = [n for n in nodes if n.tag == 'a' and activation.edition._within(n, card)]
    if not links:
        return None  # Empty bootstrap card has no public edition identity.
    def one(tag, predicate, label):
        return activation.edition._one([n for n in card.children if n.tag == tag and predicate(n)], label)
    dated = one('p', lambda n: n.attrs.get('class') == 'edition-date', 'former latest date')
    old_date = activation.edition._text(dated)
    if date.fromisoformat(old_date).isoformat() != old_date:
        raise reader.CorrectedManifestError('former latest has invalid edition date')
    href = 'editions/' + old_date + '/'
    if any(n.attrs.get('href') != href for n in links):
        raise reader.CorrectedManifestError('former latest has unsupported or mixed edition identities')
    if any(n.tag == 'li' and any(link.tag == 'a' and link.attrs.get('href') == href and activation.edition._within(link, n) for link in nodes) for n in nodes):
        return None  # Already-visible historical rows are preserved verbatim.
    title = one('h3', lambda n: True, 'former latest title')
    metadata = one('p', lambda n: n.attrs.get('class') == 'topic-badge topic-badge--gaza', 'former latest metadata')
    summaries = [n for n in card.children if n.tag == 'p' and not n.attrs and not any(activation.edition._within(link, n) for link in links)]
    if len(summaries) > 1:
        raise reader.CorrectedManifestError('former latest summary is ambiguous')
    cta = activation.edition._one([n for n in links if n.attrs.get('class') == 'button'], 'former latest CTA')
    def inner(node):
        return document[node.start_tag_end:node.end_tag_start]
    summary = '<span class="briefing-row-summary">' + inner(summaries[0]) + '</span>' if summaries else ''
    row = ('<li class="briefing-row briefing-row--readable" data-preserved-latest="' + old_date + '">'
           '<span class="edition-date">' + inner(dated) + '</span><span class="briefing-row-main">'
           '<a class="briefing-row-title" href="' + href + '">' + inner(title) + '</a>' + summary +
           '<span class="briefing-row-meta">' + inner(metadata) + '</span></span>'
           '<a class="briefing-row-cta" href="' + href + '">' + inner(cta) + '</a></li>')
    return old_date, document[card.start:card.end], row


def assert_aggregate_projections_preserved(baseline: dict[str, str], rendered: dict[str, str]) -> None:
    """Reject a new aggregate before writing if any old list/item bytes drift.

    Latest cards and page-level layout may change; all historical list entries,
    RSS items, and the correction's owning projections must remain verbatim.
    """
    def projections(text, name):
        nodes = activation.legacy._StrictHtmlTreeParser(
            text, **({'void_tags': set()} if name.endswith('.xml') else {})
        ).finish()
        result = Counter()
        for node in nodes:
            if node.tag != ('item' if name.endswith('.xml') else 'li'):
                continue
            parent = node.parent
            container = (parent.tag, tuple(sorted(parent.attrs.items()))) if parent is not None else None
            visibility = []
            ancestor = parent
            while ancestor is not None:
                flags = tuple((key, ancestor.attrs[key]) for key in ('hidden', 'inert', 'aria-hidden', 'style') if key in ancestor.attrs)
                if flags:
                    visibility.append((ancestor.tag, flags))
                ancestor = ancestor.parent
            result[(text[node.start:node.end], container, tuple(visibility))] += 1
        return result

    for name, original in baseline.items():
        current = rendered[name]
        before, after = projections(original, name), projections(current, name)
        for projection, count in before.items():
            if after[projection] != count:
                raise reader.CorrectedManifestError('existing Gaza aggregate projection drifted: ' + name)
        path = 'gaza/' + name
        if activation._owned_aggregate(original.encode(), path) != activation._owned_aggregate(current.encode(), path):
            raise reader.CorrectedManifestError('owning Oct25 aggregate projection drifted: ' + name)
        if name == 'index.html':
            former = former_latest_record(original)
            if former is not None:
                old_date, card, row = former
                nodes = activation.legacy._StrictHtmlTreeParser(current).finish()
                def visible(node):
                    while node is not None:
                        if any(key in node.attrs for key in ('hidden', 'inert', 'style')) or node.attrs.get('aria-hidden') == 'true':
                            return False
                        node = node.parent
                    return True
                retained_cards = [n for n in nodes if n.tag == 'article' and current[n.start:n.end] == card and visible(n)]
                retained_rows = [n for n in nodes if n.tag == 'li' and current[n.start:n.end] == row
                                 and n.parent is not None and n.parent.tag == 'ul'
                                 and n.parent.attrs.get('class') == 'edition-list gaza-readable-list' and visible(n)]
                if len(retained_cards) + len(retained_rows) != 1:
                    raise reader.CorrectedManifestError('former latest public edition disappeared: ' + old_date)
