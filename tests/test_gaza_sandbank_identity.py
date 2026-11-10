"""Strong storm-event identity, not flood-topic or casualty-count matching."""
from copy import deepcopy
import pytest
from bluefern_dispatches.story_dedupe import collapse_within_edition
from scripts.run_gaza_dispatch import _build_today_read


def record(identifier, publisher, title, summary):
    return {
        'story_id': identifier, 'title': title, 'summary': summary, 'score': 80,
        'category': 'humanitarian', 'event_date': '2026-11-08',
        'location': 'Camp south of Deir al-Balah',
        'source_record_ids': [identifier], 'source_ids': [identifier],
        'source_urls': ['https://example.org/' + identifier], 'publisher_names': [publisher],
        'source_records': [{'source_record_id': identifier, 'publisher': publisher,
                            'title': title, 'summary_or_snippet': summary,
                            'published_at': '2026-11-09T00:00:00Z'}],
    }


def reports():
    return [
        record('bbc', 'BBC News', 'Four killed after sand embankment collapses onto tents',
               'BBC reports that a sandbank collapsed onto tents at a camp south of Deir al-Balah on November 8. Civil Defence initially reported five deaths; the Health Ministry subsequently reported four.'),
        record('guardian', 'The Guardian', 'Witnesses recount Gaza landslide',
               'The Guardian reports five deaths in the November 8 sand bank collapse at a camp for displaced people south of Deir al-Balah.'),
    ]


def test_same_explicit_event_retains_separate_attributed_counts_and_sources():
    inputs = reports()
    included, skipped, groups = collapse_within_edition(inputs, 'gaza')
    assert len(included) == len(skipped) == len(groups) == 1
    result = included[0]
    assert set(result['source_record_ids']) == {'bbc', 'guardian'}
    assert len(result['source_records']) == 2
    assert len(result['attributed_event_accounts']) == 2
    for source in inputs:
        assert source['summary'] in result['summary']
        assert source['publisher_names'][0] + ':' in result['summary']
    assert 'four' in result['summary'] and 'five' in result['summary']
    assert result['title'] == 'Reports on the 2026-11-08 sandbank collapse south of Deir al-Balah'
    today = ' '.join(_build_today_read(included, {}))
    assert 'not added together' in today
    assert 'Multiple outlets reported it' not in today
    assert '9 deaths' not in today and 'nine deaths' not in today


@pytest.mark.parametrize('field,value', [
    ('event_date', ''), ('event_date', '2026-11-09'),
    ('event_date', '2026-11-08T12:00:00Z'),
    ('location', 'Camp north of Deir al-Balah'),
    ('location', 'Different named camp south of Deir al-Balah'),
    ('location', ''),
])
def test_no_join_without_matching_explicit_day_and_location(field, value):
    inputs = reports()
    inputs[1][field] = value
    if field == 'location':
        inputs[1]['summary'] = inputs[1]['summary'].replace('south of', 'north of')
        inputs[1]['source_records'][0]['summary_or_snippet'] = inputs[1]['summary']
    included, _, _ = collapse_within_edition(inputs, 'gaza')
    assert len(included) == 2


def test_broad_ocha_flood_report_is_not_the_collapse_event():
    inputs = reports()
    broad = deepcopy(inputs[1])
    broad['story_id'] = 'ocha'; broad['title'] = 'Humanitarian Situation Report: Heavy Rains in the Gaza Strip'
    broad['source_record_ids'] = ['ocha']; broad['source_ids'] = ['ocha']
    broad['source_urls'] = ['https://example.org/ocha']
    broad['source_records'][0]['title'] = broad['title']
    included, _, _ = collapse_within_edition([inputs[0], broad], 'gaza')
    assert len(included) == 2


def test_third_matching_report_does_not_discard_first_two_accounts():
    inputs = reports()
    third = deepcopy(inputs[0]); third['story_id'] = 'third'
    third['source_record_ids'] = ['third']; third['source_ids'] = ['third']
    third['source_urls'] = ['https://example.org/third']; third['publisher_names'] = ['Third']
    third['source_records'][0]['source_record_id'] = 'third'
    included, _, _ = collapse_within_edition(inputs + [third], 'gaza')
    assert len(included) == 1
    assert len(included[0]['attributed_event_accounts']) == 3
