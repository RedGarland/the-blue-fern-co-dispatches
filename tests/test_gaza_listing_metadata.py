"""Synthetic regressions: no network, real source data, or publication authority."""
from pathlib import Path
import html
import pytest
from bluefern_dispatches import generator as g


def row(title='Published headline', summary='Published description.', **changes):
    value = dict(title=title, summary=summary, public_rendered=True,
                 included_in_public_summary=True, include_decision='include')
    value.update(changes)
    return value


def article(value):
    return '<article><h3>'+html.escape(value['title'])+'</h3><p>'+html.escape(value['summary'])+'</p></article>'


def context(monkeypatch, rows, body, manifest=None, slug='gaza'):
    monkeypatch.setattr(g, '_load_json_file', lambda path: rows if path.name == 'curation_manifest.json' else [])
    monkeypatch.setattr(Path, 'read_bytes', lambda path: ('<!doctype html><html><body>'+body+'</body></html>').encode())
    monkeypatch.setattr(g, '_first_page_heading_or_title', lambda path: '')
    monkeypatch.setattr(g, 'public_edition_manifest', lambda *args: manifest or {})
    dispatch = g.DispatchConfig(slug=slug, name='Synthetic desk', edition_date='2026-11-01',
        tagline='Synthetic tagline', logo='', sources=[], stories=[])
    return g.briefing_presentation_for_edition(Path('/synthetic-site'), dispatch, '2026-11-01')


@pytest.mark.parametrize('changes', [dict(public_rendered=False), dict(public_rendered=None),
    dict(included_in_public_summary=False), dict(included_in_public_summary=None),
    dict(include_decision='skip'), dict(include_decision=None)])
def test_gaza_fallback_ignores_ineligible_first_row(monkeypatch, changes):
    excluded=row('Excluded headline', 'Excluded description.', **changes); public=row()
    result=context(monkeypatch,[excluded,public],article(excluded)+article(public))
    assert (result.title,result.summary)==(public['title'],public['summary'])


def test_gaza_fallback_uses_rendered_order_and_matching_summary(monkeypatch):
    first=row('First in page','First summary.'); second=row('First in manifest','Second summary.')
    result=context(monkeypatch,[second,first],article(first)+article(second))
    assert (result.title,result.summary)==(first['title'],first['summary'])


@pytest.mark.parametrize('body',['', '<article><h3>Published headline</h3><p>Different text.</p></article>'])
def test_gaza_fallback_requires_published_description(monkeypatch,body):
    result=context(monkeypatch,[row()],body)
    assert result.title=='2026-11-01'; assert result.summary==''


@pytest.mark.parametrize('wrapper',['<div hidden>{}</div>','<div aria-hidden="true">{}</div>',
    '<div style="display:none">{}</div>','<template>{}</template>','<article>{}</article>'])
def test_gaza_fallback_rejects_hidden_or_nested_article(monkeypatch,wrapper):
    result=context(monkeypatch,[row()],wrapper.format(article(row())))
    assert result.title=='2026-11-01'; assert result.summary==''


def test_gaza_fallback_rejects_duplicate_title(monkeypatch):
    result=context(monkeypatch,[row(),row()],article(row()))
    assert result.title=='2026-11-01'; assert result.summary==''


def test_gaza_fallback_does_not_unpack_unknown_correction_envelope(monkeypatch):
    result=context(monkeypatch,{'stories':[row()]},article(row()))
    assert result.title=='2026-11-01'; assert result.summary==''


def test_gaza_explicit_metadata_is_preserved(monkeypatch):
    result=context(monkeypatch,[row()],article(row()),{'public_archive_title':'Approved edition title','public_archive_subtitle':'Approved edition description.'})
    assert (result.title,result.summary)==('Approved edition title','Approved edition description.')


def test_gaza_explicit_title_does_not_acquire_unrelated_story_summary(monkeypatch):
    result=context(monkeypatch,[row()],article(row()),{'public_archive_title':'Different edition title'})
    assert result.title=='Different edition title'; assert result.summary==''


def test_other_desk_fallback_is_unchanged(monkeypatch):
    value=row(public_rendered=False, include_decision='skip')
    result=context(monkeypatch,[value],'',slug='food-line')
    assert (result.title,result.summary)==(value['title'],value['summary'])
