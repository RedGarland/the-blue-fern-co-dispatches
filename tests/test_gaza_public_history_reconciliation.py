"""Fictional committed history only; no production registry or public writes."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from bluefern_dispatches import gaza_correction_activation as activation
from bluefern_dispatches import gaza_corrected_manifest_reader as reader
from bluefern_dispatches import gaza_public_history_reconciliation as history
from bluefern_dispatches import generator, story_dedupe, gaza_sources
from test_gaza_correction_activation import public_active, commit
from test_gaza_correction_fixture_support import make_context, git


def inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and '.git' not in p.relative_to(root).parts and not p.is_symlink()}


def reconcile(root, backup, **kwargs):
    return generator.reconcile_gaza_public_editions(
        root, root / 'output/site', kwargs.pop('dry_run', False), kwargs.pop('wrote', []),
        pages_repo=kwargs.pop('pages', root / 'bluefern-dispatches-pages'),
        backup_root=backup, max_public_date=kwargs.pop('date', '2026-10-28'), **kwargs)


@pytest.mark.parametrize('state', ['absent', 'original', 'corrected'])
def test_future_reconciliation_preserves_exact_profile_without_touching_pages(public_active, tmp_path, state):
    root = public_active
    pages = root / 'bluefern-dispatches-pages'
    site = root / 'output/site'
    if state == 'corrected':
        for relative in reader.PROFILE_HASHES:
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pages / relative, target)
    elif state == 'original':
        for name in reader.ORIGINAL_BLOBS:
            relative = 'gaza/editions/2026-10-25/' + name
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(activation._blob(pages, activation.PUBLISHED_PARENT, relative))
    unrelated = site / 'food-line/keep.bin'
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b'Unrelated generated output\x00')
    original = inventory(site)
    pages_before = inventory(pages)
    source_head = git(root, 'rev-parse', 'HEAD')
    pages_head = git(pages, 'rev-parse', 'HEAD')
    backup = tmp_path / 'backup'
    result = reconcile(root, backup)
    assert any(row['source'] == 'fixed_public_history' for row in result['backfilled'])
    assert reader._replica_kind(site) == 'corrected'
    for relative, digest in reader.PROFILE_HASHES.items():
        assert hashlib.sha256((site / relative).read_bytes()).hexdigest() == digest
    assert unrelated.read_bytes() == original['food-line/keep.bin']
    assert inventory(pages) == pages_before
    assert git(root, 'rev-parse', 'HEAD') == source_head
    assert git(pages, 'rev-parse', 'HEAD') == pages_head
    if state == 'original':
        copies = list((backup / 'gaza/conflicting-corrections').iterdir())
        assert len(copies) == 1
        assert inventory(copies[0]) == {Path(name).name: raw for name, raw in original.items()
                                      if name.startswith('gaza/editions/2026-10-25/')}
    else:
        assert not backup.exists()
    before = inventory(root)
    reconcile(root, backup)
    assert inventory(root) == before
    with pytest.raises(reader.CorrectedManifestError):
        reader.assert_generic_reconciliation_allowed(root, site, pages)


@pytest.mark.parametrize('date', [None, '', '2026-10-25', '2026-10-24', '2026-1-29', 'not-a-date'])
def test_unbounded_or_nonfuture_runs_still_refuse_before_writes(public_active, tmp_path, date):
    before = inventory(public_active)
    with pytest.raises(reader.CorrectedManifestError):
        reconcile(public_active, tmp_path / 'backup', date=date)
    assert inventory(public_active) == before
    assert not (tmp_path / 'backup').exists()


@pytest.mark.parametrize('mutation', ['partial', 'unknown', 'symlink', 'extra', 'audio', 'audio_reference'])
@pytest.mark.parametrize('replica', ['output/site', 'output/dispatches'])
def test_conflicting_local_replicas_fail_before_writes(public_active, tmp_path, mutation, replica):
    root = public_active
    base = root / replica
    for relative in reader.PROFILE_HASHES:
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / 'bluefern-dispatches-pages' / relative, target)
    target = base / reader.CORRECTION_PATH
    if mutation == 'partial':
        target.unlink()
    elif mutation == 'unknown':
        target.write_bytes(b'{}')
    elif mutation == 'symlink':
        target.unlink(); target.symlink_to('missing')
    elif mutation == 'extra':
        (target.parent / 'extra').write_bytes(b'Unexpected')
    elif mutation == 'audio':
        p = base / 'gaza/audio/2026-10-25.mp3'; p.parent.mkdir(parents=True); p.write_bytes(b'ID3')
    else:
        (base / 'gaza/podcast.xml').write_bytes(b'<rss><channel><link>2026%2D10%2D25</link></channel></rss>')
    before = inventory(root)
    with pytest.raises(ValueError):
        reconcile(root, tmp_path / 'backup')
    assert inventory(root) == before
    assert not (tmp_path / 'backup').exists()


def test_other_pages_or_site_roots_are_not_authority(public_active, tmp_path):
    other = tmp_path / 'different-pages'
    shutil.copytree(public_active / 'bluefern-dispatches-pages', other)
    with pytest.raises(reader.CorrectedManifestError, match='roots differ'):
        reconcile(public_active, tmp_path / 'backup', pages=other)
    with pytest.raises(reader.CorrectedManifestError, match='roots differ'):
        history.reconcile_published_profile(public_active, tmp_path / 'site', public_active / 'bluefern-dispatches-pages',
            before_date='2026-10-28', dry_run=False, wrote=[], backup_root=tmp_path / 'backup')


def test_dry_run_is_read_only_and_still_checks_public_history(public_active, tmp_path):
    before = inventory(public_active)
    wrote = []
    reconcile(public_active, tmp_path / 'backup', dry_run=True, wrote=wrote)
    assert set(wrote) == {str(public_active / 'output/site' / name) for name in reader.PROFILE_HASHES}
    assert inventory(public_active) == before
    assert not (tmp_path / 'backup').exists()
    p = public_active / 'bluefern-dispatches-pages' / reader.CORRECTION_PATH
    p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(reader.CorrectedManifestError):
        reconcile(public_active, tmp_path / 'backup', dry_run=True)


def test_formal_release_authority_does_not_open_new_path(tmp_path, monkeypatch):
    root = make_context(tmp_path, monkeypatch, publication=True)['source_root']
    before = inventory(root)
    with pytest.raises(reader.CorrectedManifestError, match='already-public history'):
        reconcile(root, tmp_path / 'backup')
    assert inventory(root) == before


def test_inactive_pending_correction_still_fails(public_active, tmp_path):
    # Synthetic source branch without any registry history.
    git(public_active, 'checkout', '--orphan', 'synthetic-inactive')
    git(public_active, 'rm', '-rf', '.')
    (public_active / '.gitignore').write_text('bluefern-dispatches-pages/\n')
    commit(public_active)
    with pytest.raises(reader.CorrectedManifestError, match='approval-bound'):
        reconcile(public_active, tmp_path / 'backup')


def test_preserved_profile_supplies_current_dedupe_and_source_lineage(public_active, tmp_path):
    root = public_active
    p = root / 'data/records/story_memory.json'
    p.write_text(json.dumps([{'dispatch_slug': 'gaza', 'edition_date': '2026-10-25',
        'story_id': 'synthetic-story', 'title': 'Old framing', 'summary': 'Archived claim'}]))
    before = p.read_bytes()
    reconcile(root, tmp_path / 'backup')
    rows, dates = story_dedupe.prior_memory_from_outputs(root, 'gaza', '2026-10-28')
    assert dates == ['2026-10-25']
    assert len(rows) == 1 and rows[0]['title'] == 'Fictional narrowed headline'
    assert rows[0]['latest_classification'] == reader.ASSESSMENT_STATUS
    records = list(reader.prior_source_records(root, gaza_sources._iter_prior_source_manifests(root, '2026-10-28', 7), '2026-10-28'))
    assert len(records) == 1 and records[0][1][0]['projection_state'] == 'synthetic_corrected'
    assert 'title' not in records[0][1][0]
    assert p.read_bytes() == before
    with pytest.raises(reader.CorrectedManifestError):
        reader.assert_non_site_action_allowed(root, '2026-10-25', 'audio')


def test_unrelated_descendants_remain_valid(public_active, tmp_path):
    pages = public_active / 'bluefern-dispatches-pages'
    (pages / 'unrelated.txt').write_text('Unrelated later publication')
    latest = commit(pages)
    reconcile(public_active, tmp_path / 'backup')
    assert activation.load_activation(public_active)['current_pages_head'] == latest


def test_public_or_local_drift_between_preflight_and_copy_refuses(public_active, tmp_path, monkeypatch):
    original = activation.load_activation
    calls = 0
    def racing(root):
        nonlocal calls
        result = original(root); calls += 1
        if calls == 2:
            p = root / 'output/site/gaza/editions/2026-10-25/curation_manifest.json'
            p.parent.mkdir(parents=True); p.write_bytes(b'Unrecognized concurrent writer')
        return result
    monkeypatch.setattr(activation, 'load_activation', racing)
    with pytest.raises(reader.CorrectedManifestError):
        reconcile(public_active, tmp_path / 'backup')
    assert not (public_active / 'output/site' / reader.CORRECTION_PATH).exists()
    assert not (tmp_path / 'backup').exists()


def aggregate_context(root):
    pages = root / 'bluefern-dispatches-pages'
    for name in ('index.html', 'archive.html'):
        p = pages / 'gaza' / name
        old = p.read_text()
        if name == 'index.html':
            old = old.replace('<ul>', '<article class="gaza-latest-card"><p>Old latest</p></article><ul class="edition-list gaza-readable-list">')
        else:
            old = old.replace('<ul>', '<section class="archive-latest" aria-label="Latest archive entry"><p>Old latest</p></section>\n<section class="archive-month"><h2>October 2026</h2>\n<ul class="edition-list archive-list">').replace('</ul>', '</ul>\n</section>')
        p.write_text(old)
    p = pages / 'gaza/rss.xml'
    p.write_text(p.read_text().replace('<channel>', '<channel>\n  <description>Fictional feed</description>\n'))
    commit(pages)
    site = root / 'output/site'
    p = site / 'gaza/editions/2026-10-28/edition_manifest.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({'edition_date': '2026-10-28', 'public_archive_title': 'Fictional future briefing',
        'public_archive_subtitle': 'A new distinct fictional event.', 'public_story_count': 1, 'public_source_count': 1}))
    dispatch = generator.DispatchConfig('gaza', 'Dispatches From Gaza', '2026-10-28', 'Daily briefing', 'gaza-logo.png', [], [])
    return pages, site, dispatch


def test_future_aggregate_preserves_every_owned_projection_and_is_repeatable(public_active, tmp_path):
    pages, site, dispatch = aggregate_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    reconcile(public_active, tmp_path / 'backup')
    for _ in range(2):
        assert generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])
        rendered = {name: (site / 'gaza' / name).read_text() for name in baseline}
        history.assert_aggregate_projections_preserved(baseline, rendered)
        assert rendered['rss.xml'].count('<guid>https://dispatches.thebluefernco.com/gaza/editions/2026-10-28/</guid>') == 1
        assert activation.load_activation(public_active)['publication_authorized'] is False


@pytest.mark.parametrize('include_current', [False, True])
def test_normal_runner_aggregate_entrypoint_keeps_corrected_history(public_active, include_current):
    from scripts.run_gaza_dispatch import render_archive_index_rss

    pages, site, dispatch = aggregate_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    pages_before = inventory(pages)
    render_archive_index_rss(public_active, '2026-10-28', False, [], pages_repo=pages, include_current=include_current)
    assert reader._replica_kind(site) == 'corrected'
    history.assert_aggregate_projections_preserved(baseline, {name: (site / 'gaza' / name).read_text() for name in baseline})
    assert inventory(pages) == pages_before


@pytest.mark.parametrize('surface', ['index.html', 'archive.html', 'rss.xml'])
@pytest.mark.parametrize('mutation', ['alter', 'drop', 'duplicate'])
def test_aggregate_history_tampering_is_rejected(public_active, surface, mutation):
    pages, site, dispatch = aggregate_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    rendered = dict(baseline)
    projections = activation._owned_aggregate(baseline[surface].encode(), 'gaza/' + surface)
    old = projections[0]
    new = old.replace('Fictional narrowed headline', 'Unapproved old framing') if mutation == 'alter' else '' if mutation == 'drop' else old + old
    rendered[surface] = rendered[surface].replace(old, new)
    with pytest.raises(ValueError):
        history.assert_aggregate_projections_preserved(baseline, rendered)


def test_aggregate_transform_failure_occurs_before_any_aggregate_write(public_active, monkeypatch):
    pages, site, dispatch = aggregate_context(public_active)
    # Ensure the generator invokes its existing layout transform in this fixture.
    p = pages / 'gaza/archive.html'
    p.write_text(p.read_text().replace('<html>', '<html><head></head>'))
    commit(pages)
    monkeypatch.setattr(generator, 'apply_gaza_archive_layout', lambda text: text.replace('Fictional narrowed headline', 'Unapproved old framing'))
    with pytest.raises(reader.CorrectedManifestError, match='projection'):
        generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])
    assert not any((site / 'gaza' / name).exists() for name in ('index.html', 'archive.html', 'rss.xml'))


@pytest.mark.parametrize('wrapper', ['<!--{}-->', '<div hidden>{}</div>', '<div style="display:none">{}</div>', '<div aria-hidden="true">{}</div>'])
def test_unrelated_historical_row_cannot_be_hidden_as_retained_text(public_active, wrapper):
    pages, site, dispatch = aggregate_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    row = '<li><a href="editions/2026-10-20/">Older public history</a></li>'
    baseline['archive.html'] = baseline['archive.html'].replace('</ul>', row + '</ul>')
    rendered = dict(baseline)
    rendered['archive.html'] = rendered['archive.html'].replace(row, wrapper.format(row))
    with pytest.raises(reader.CorrectedManifestError, match='projection drifted'):
        history.assert_aggregate_projections_preserved(baseline, rendered)


@pytest.mark.parametrize('ancestor', ['gaza/corrections', 'gaza/editions', 'gaza/corrections/' + activation.CORRECTION_ID])
def test_absent_profile_cannot_write_through_redirected_destination(public_active, tmp_path, ancestor):
    outside = tmp_path / 'outside'; outside.mkdir()
    link = public_active / 'output/site' / ancestor
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        reconcile(public_active, tmp_path / 'backup')
    assert inventory(outside) == {}
    assert not (tmp_path / 'backup').exists()
    assert not (public_active / 'output/site/gaza/editions/2026-10-25').exists()


@pytest.mark.parametrize('seed_date,publication_dates', [('2026-10-25', ('2026-10-28',)), ('2026-10-28', ()), ('2026-10-28', ('2026-10-29',))])
def test_general_no_update_or_wrong_seed_build_stays_closed(public_active, tmp_path, monkeypatch, seed_date, publication_dates):
    dispatch = generator.DispatchConfig('gaza', 'Fictional Gaza', seed_date, 'Daily briefing', 'gaza-logo.png', [], [])
    monkeypatch.setattr(generator, 'seed_dispatches', lambda *args, **kwargs: [dispatch])
    before = inventory(public_active)
    with pytest.raises(reader.CorrectedManifestError, match='approval-bound'):
        generator.build_site(public_active, dry_run=False, backup_root=tmp_path / 'backup',
            only_dispatches=('gaza',), pages_repo=public_active / 'bluefern-dispatches-pages',
            public_max_dates={'gaza': '2026-10-28'}, gaza_publication_dates=publication_dates)
    assert inventory(public_active) == before
    assert not (tmp_path / 'backup').exists()


def test_pages_owning_projection_drift_after_reconcile_refuses_render(public_active, tmp_path):
    pages, site, dispatch = aggregate_context(public_active)
    reconcile(public_active, tmp_path / 'backup')
    p = pages / 'gaza/index.html'
    p.write_text(p.read_text().replace('Fictional narrowed headline', 'Unapproved old framing'))
    with pytest.raises(reader.CorrectedManifestError, match='projection'):
        generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])
    assert not (site / 'gaza/index.html').exists()
    with pytest.raises(reader.CorrectedManifestError, match='projection'):
        generator._gaza_aggregate_preserve_pages_history(site, pages, dry_run=False, wrote=[], edition_date='2026-10-28')
    assert not (site / 'gaza/index.html').exists()


def test_deleted_pages_correction_marker_does_not_disable_aggregate_guard(public_active, tmp_path):
    pages, site, dispatch = aggregate_context(public_active)
    reconcile(public_active, tmp_path / 'backup')
    (pages / reader.CORRECTION_PATH).unlink()
    with pytest.raises(reader.CorrectedManifestError):
        generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])
    with pytest.raises(reader.CorrectedManifestError):
        generator._gaza_aggregate_preserve_pages_history(site, pages, dry_run=False, wrote=[], edition_date='2026-10-28')
    assert not (site / 'gaza/index.html').exists()


def test_normal_manual_generation_uses_corrected_history_without_publication(public_active, tmp_path, monkeypatch):
    from scripts import run_gaza_dispatch as runner

    monkeypatch.setattr(runner, 'BACKUP_ROOT', tmp_path / 'private-backup/gaza')
    pages, site, dispatch = aggregate_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    before = inventory(pages)
    p = public_active / 'data/dispatches/gaza/sources/2026-10-28/manual_sources.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps([{
        'source_record_id': 'synthetic-new-source',
        'title': 'Gaza hospital restores oncology service after medical supplies arrive',
        'url': 'https://fictional.example.invalid/gaza-hospital-oncology',
        'publisher': 'Fictional News', 'published_at': '2026-10-28T01:00:00Z',
        'retrieved_at': '2026-10-28T02:00:00Z',
        'summary_or_snippet': 'A Gaza hospital restored oncology treatment after a delivery of medical supplies. Patients can now resume cancer care at the hospital.',
        'source_type': 'news', 'region_scope': 'Gaza', 'category_hint': 'healthcare',
        'reliability_tier': 'reported-public-source',
    }]))
    result = runner.run_gaza_dispatch(public_active, '2026-10-28', from_manual_sources=True,
        dry_run=False, render=False, all_steps=True)
    assert result['ok'], result.get('errors')
    assert (site / 'gaza/editions/2026-10-28/index.html').is_file()
    assert reader._replica_kind(site) == 'corrected'
    history.assert_aggregate_projections_preserved(baseline, {name: (site / 'gaza' / name).read_text() for name in baseline})
    assert inventory(pages) == before


def latest_only_context(root):
    pages, site, dispatch = aggregate_context(root)
    p = pages / 'gaza/index.html'
    card = ('<article class="gaza-latest-card">'
            '<p class="edition-date">2026-10-27</p>'
            '<h3>Fictional prior hospital report</h3>'
            '<p>Exact prior summary, with unchanged attribution.</p>'
            '<p class="topic-badge topic-badge--gaza">Limited-source update &middot; 3 reports &middot; Fictional News &middot; Text only</p>'
            '<p><a class="button" href="editions/2026-10-27/">Read briefing</a></p>'
            '<span hidden><a href="editions/2026-10-27/">Read the latest briefing</a></span></article>')
    p.write_text(p.read_text().replace('<article class="gaza-latest-card"><p>Old latest</p></article>', card))
    m = pages / 'gaza/editions/2026-10-27/edition_manifest.json'
    m.parent.mkdir(parents=True)
    m.write_text(json.dumps({'dispatch_slug': 'gaza', 'edition_date': '2026-10-27', 'source_count': 3,
                            'story_count': 3, 'publishers': ['Fictional News']}))
    commit(pages)
    return pages, site, dispatch


def test_latest_only_public_edition_becomes_exact_visible_history(public_active, tmp_path):
    pages, site, dispatch = latest_only_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    old_date, card, row = history.former_latest_record(baseline['index.html'])
    reconcile(public_active, tmp_path / 'backup')
    for _ in range(2):
        generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])
        rendered = {name: (site / 'gaza' / name).read_text() for name in baseline}
        assert rendered['index.html'].count(row) == 1
        assert rendered['index.html'].count('class="gaza-latest-card"') == 1
        assert 'Limited-source update &middot; 3 reports &middot; Fictional News &middot; Text only' in row
        history.assert_aggregate_projections_preserved(baseline, rendered)


@pytest.mark.parametrize('substitute', ['', '<!--{}-->', '<div hidden>{}</div>'])
def test_latest_only_edition_cannot_disappear_or_be_hidden(public_active, substitute):
    pages, site, dispatch = latest_only_context(public_active)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    old_date, card, row = history.former_latest_record(baseline['index.html'])
    rendered = dict(baseline)
    rendered['index.html'] = rendered['index.html'].replace(card, substitute.format(card))
    with pytest.raises(reader.CorrectedManifestError, match='former latest'):
        history.assert_aggregate_projections_preserved(baseline, rendered)


def test_dirty_former_latest_metadata_is_not_published_history(public_active):
    pages, site, dispatch = latest_only_context(public_active)
    p = pages / 'gaza/index.html'
    p.write_text(p.read_text().replace('3 reports', '99 reports'))
    with pytest.raises(reader.CorrectedManifestError, match='current committed public history'):
        generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])


@pytest.mark.parametrize('include_current', [False, True])
def test_aggregate_crlf_history_is_preserved_as_bytes(public_active, tmp_path, monkeypatch, include_current):
    pages, site, dispatch = latest_only_context(public_active)
    for name in ('index.html', 'archive.html', 'rss.xml'):
        p = pages / 'gaza' / name
        p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    commit(pages)
    baseline = generator._gaza_pages_aggregate_baseline(pages)
    assert all(baseline[name].encode() == (pages / 'gaza' / name).read_bytes() for name in baseline)
    reconcile(public_active, tmp_path / 'backup')
    def text_write_forbidden(*args, **kwargs):
        raise AssertionError('aggregate writes must not use platform text-newline translation')
    monkeypatch.setattr(generator, 'write_text', text_write_forbidden)
    if include_current:
        generator._gaza_aggregate_render_from_pages_history(site, dispatch, '2026-10-28', pages, dry_run=False, wrote=[])
        rendered = {name: (site / 'gaza' / name).read_bytes().decode() for name in baseline}
        history.assert_aggregate_projections_preserved(baseline, rendered)
        assert b'\r\r\n' not in (site / 'gaza/rss.xml').read_bytes()
        assert (site / 'gaza/rss.xml').read_bytes().count(b'\r\n') == (pages / 'gaza/rss.xml').read_bytes().count(b'\r\n')
    else:
        generator._gaza_aggregate_preserve_pages_history(site, pages, dry_run=False, wrote=[], edition_date='2026-10-28')
        assert all((site / 'gaza' / name).read_bytes() == (pages / 'gaza' / name).read_bytes() for name in baseline)
