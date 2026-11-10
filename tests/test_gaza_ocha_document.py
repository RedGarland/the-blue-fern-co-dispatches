from pathlib import Path
import hashlib
import pytest
from bluefern_dispatches import gaza_sources as g

URL = 'https://www.ochaopt.org/content/humanitarian-situation-report-heavy-rains-gaza-strip-8-november-2026'

def source():
    return g.SourceDefinition(source_id='ocha-opt-updates', name='OCHA', url=g.OCHA_REPORT_INDEX_URL, type='ocha_report_index', enabled=True, publisher='OCHA', reliability_tier='official-humanitarian-source', category_hint='humanitarian', region_scope='Gaza', source_tier='official_humanitarian', source_group='institutional')

def normalize(monkeypatch, body):
    monkeypatch.setattr(g, '_fetch_ocha_article_payload', lambda *a, **k: {'ok': True, 'final_url': URL, 'content_text': body})
    return g.normalize_rss_item({'title':'Humanitarian Situation Report: Heavy Rains in the Gaza Strip | 8 November 2026', 'url': URL, 'published_at':'2026-11-08T00:00:00+00:00', 'summary_or_snippet':'Official OCHA humanitarian situation report.'}, source(), '2026-11-10', '2026-11-10T18:34:34Z')

def test_retained_drupal_report_extraction_is_source_traceable(monkeypatch):
    raw = (Path(__file__).parent/'fixtures/ocha_rain_report_nov08.html').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '015553ff07471b1c63a45f0fcd6587270dfb2e8ca0c93e74b498a081445d6f4b'
    paragraphs = g._extract_ocha_document_paragraphs(raw.decode())
    assert paragraphs[0].startswith('Heavy rain and strong winds hit the Gaza Strip from 6 November')
    assert any('Health facilities also suffered leaks and flooding.' in p for p in paragraphs)
    assert not any('SUBSCRIBE' in p or 'Privacy' in p for p in paragraphs)
    row = normalize(monkeypatch, raw.decode())
    assert row['summary_or_snippet'] == paragraphs[0]
    assert row['content_available'] and row['editorial_eligible']
    assert row['ocha_healthcare_fragments'] == []
    assert row['article_fetch_status'] == 'report_paragraphs_extracted'
    assert row['source_excerpt_is_normalized_source_paragraph'] is True
    assert row['article_response_text_sha256'] == hashlib.sha256(raw.decode().encode()).hexdigest()

@pytest.mark.parametrize('body', ['<div class="content"><p>' + 'untrusted generic text '*30 + '</p></div>', '<div id="block-ocha-sub-impact-theme-system-main"><p>Official OCHA situation report.</p></div>', '<div class="hsu-report-body"><p>IV solutions depleted but remaining seven facts missing.</p></div>'])
def test_generic_or_incomplete_legacy_record_not_editorial(monkeypatch, body):
    row = normalize(monkeypatch, body)
    assert not row['editorial_eligible']
    assert not row['content_available']
    assert row['ocha_healthcare_fragments'] == []

def test_extraction_failure_is_error_not_no_update(tmp_path, monkeypatch):
    config=tmp_path/'data/dispatches/gaza/sources.yml';config.parent.mkdir(parents=True)
    config.write_text('sources:\n  - source_id: ocha-opt-updates\n    name: OCHA\n    url: https://www.ochaopt.org/publications/situation-reports\n    type: ocha_report_index\n    enabled: true\n    source_state: enabled\n    publisher: OCHA\n    reliability_tier: official-humanitarian-source\n    category_hint: humanitarian\n    region_scope: Gaza\n    source_tier: official_humanitarian\n    source_group: institutional\n')
    listing=f'<a href="{URL}">Humanitarian Situation Report: Heavy Rains in the Gaza Strip | 8 November 2026</a>'
    def fetch(url, **kwargs):
        body=listing if url==g.OCHA_REPORT_INDEX_URL else '<div>no body</div>'
        return {'ok':True,'url':url,'final_url':url,'status_code':200,'content_text':body,'content_bytes':body.encode()}
    monkeypatch.setattr(g,'_fetch_ocha_article_payload',fetch)
    result=g.collect_gaza_sources(tmp_path,'2026-11-10',min_sources=0,prefer_manual=False,write_output=False)
    assert not result['ok']
    assert result['source_count']==0
    assert not result['no_update_eligible']
    assert result['source_coverage_status']=='incomplete_provider_failures'
    assert result['no_match_source_ids']==[]
    assert any('ocha_substantive_report_evidence_unavailable' in e for e in result['errors'])
