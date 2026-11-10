"""Read-only, non-fixture regression of the real normalized Nov10 payload."""
import json
import unittest
from pathlib import Path
from scripts.run_gaza_dispatch import normalize_sources, curate_stories
from bluefern_dispatches.gaza_sources import build_gaza_collection_timing_metadata
from bluefern_dispatches.story_dedupe import collapse_within_edition, classify_against_prior, memory_row

FIXTURE = Path(__file__).parent / 'fixtures' / 'gaza-dedupe-nov10'

class NormalizedGazaDedupeTests(unittest.TestCase):
    def setUp(self):
        records = json.loads((FIXTURE / 'source_records.json').read_text())
        normalized, _, errors = normalize_sources(records, '2026-11-10', '2026-11-10T18:54:30+00:00')
        self.assertEqual(errors, [])
        self.stories, _, _ = curate_stories(normalized, '2026-11-10', '2026-11-10T18:54:30+00:00')

    def test_actual_generator_retains_publication_day_for_visit_identity(self):
        collapsed, _, _ = collapse_within_edition(self.stories, 'gaza')
        self.assertEqual(len(collapsed), 2)
        visit = next(s for s in collapsed if 'Hamilton' in s['title'])
        self.assertEqual(len(visit['source_urls']), 2)
        self.assertTrue(all('hamilton' in url for url in visit['source_urls']))

    def test_distinct_attack_not_suppressed_by_october_background(self):
        attack = next(s for s in self.stories if 'six Palestinians' in s['title'])
        prior_story = json.loads((FIXTURE / 'prior_story.json').read_text())
        prior = memory_row('gaza', '2026-10-10', prior_story)
        self.assertEqual(classify_against_prior(attack, [prior])[0], 'new')
        exact = memory_row('gaza', '2026-11-09', attack)
        self.assertEqual(classify_against_prior(attack, [exact])[0], 'duplicate_skip')


class GazaDateFidelityTests(unittest.TestCase):
    def normalized(self, **extra):
        record = json.loads((FIXTURE / 'source_records.json').read_text())[0]
        record.update(extra)
        rows, _, errors = normalize_sources([record], '2026-11-10', '2026-11-10T20:00:00Z')
        self.assertEqual(errors, [])
        return rows[0]

    def test_absent_or_none_event_date_is_not_publication_timestamp(self):
        for extra in ({}, {'event_date': None}):
            source = self.normalized(**extra)
            stories, _, _ = curate_stories([source], '2026-11-10', '2026-11-10T20:00:00Z')
            self.assertTrue(source['published_at'])
            self.assertIsNone(source['event_date'])
            self.assertEqual(source['event_date_precision'], 'unknown')
            self.assertTrue(all(not story['event_date'] for story in stories))

    def test_explicit_event_day_and_article_modification_roundtrip(self):
        source = self.normalized(event_date='2026-11-09', event_date_precision='date',
                                 event_date_basis='canonical article body', location='south of Deir al-Balah',
                                 modified_at='2026-11-10T19:31:00Z')
        stories, _, _ = curate_stories([source], '2026-11-10', '2026-11-10T20:00:00Z')
        self.assertEqual(source['event_date'], '2026-11-09')
        self.assertEqual(source['event_date_precision'], 'date')
        self.assertTrue(all(story['location'] == 'south of Deir al-Balah' for story in stories))
        self.assertEqual(source['modified_at'], '2026-11-10T19:31:00Z')
        self.assertTrue(all(story['event_date'] == '2026-11-09' for story in stories))
        self.assertTrue(all(story['source_records'][0]['modified_at'] == '2026-11-10T19:31:00Z' for story in stories))

    def test_delayed_initial_collection_is_not_scheduled_or_content_update(self):
        rows = [{'source_record_id': 'one', 'retrieved_at': '2026-11-10T20:07:00Z'}]
        result = build_gaza_collection_timing_metadata(rows, '2026-11-10', '2026-11-10T20:08:00Z')
        self.assertEqual(result['retrieval_batches'][0]['batch_classification'], 'after_scheduled_initial_batch')
        self.assertFalse(result['contains_later_same_day_update'])
        rows.append({'source_record_id': 'two', 'retrieved_at': '2026-11-10T21:00:00Z'})
        result = build_gaza_collection_timing_metadata(rows, '2026-11-10')
        self.assertEqual(result['retrieval_batches'][1]['batch_classification'], 'later_same_day_update')
        self.assertTrue(result['contains_later_same_day_update'])

    def test_scheduled_minute_and_unknown_retrieval_are_distinct(self):
        rows = [{'source_record_id': 'one', 'retrieved_at': '2026-11-10T14:00:35Z'},
                {'source_record_id': 'bad', 'retrieved_at': 'invalid'}]
        result = build_gaza_collection_timing_metadata(rows, '2026-11-10')
        self.assertEqual(result['retrieval_batches'][0]['batch_classification'], 'scheduled_or_pre_scheduled_batch')
        self.assertEqual(result['retrieval_batches'][1]['batch_classification'], 'unknown_retrieval_time')


class OchaPublicationDateTests(unittest.TestCase):
    def collect(self, body, title_day=8):
        from unittest.mock import patch
        from bluefern_dispatches import gaza_sources as g
        url = 'https://www.ochaopt.org/content/humanitarian-situation-report-heavy-rains-gaza-strip-8-november-2026'
        source = g.SourceDefinition(source_id='ocha-opt-updates', name='OCHA', url=g.OCHA_REPORT_INDEX_URL,
            type='ocha_report_index', enabled=True, publisher='OCHA', reliability_tier='official-humanitarian-source',
            category_hint='humanitarian', region_scope='Gaza', source_tier='official_humanitarian', source_group='institutional')
        listing = f'<a href="{url}">Humanitarian Situation Report: Heavy Rains in the Gaza Strip | {title_day} November 2026</a>'
        def fetch(target, **kwargs):
            text = listing if target == g.OCHA_REPORT_INDEX_URL else body
            return {'ok': True, 'url': target, 'final_url': target, 'status_code': 200,
                    'content_text': text, 'content_bytes': text.encode()}
        with patch.object(g, 'load_sources_config', return_value=[source]), patch.object(g, '_fetch_ocha_article_payload', side_effect=fetch):
            return g.collect_gaza_sources(Path('/nonexistent-readonly-test-root'), '2026-11-10',
                min_sources=0, prefer_manual=False, write_output=False)

    def body(self):
        return (Path(__file__).parent / 'fixtures/ocha_rain_report_nov08.html').read_text()

    def test_title_period_is_not_canonical_publication(self):
        result = self.collect(self.body())
        self.assertTrue(result['ok'], result['errors'])
        row = result['sources'][0]
        self.assertEqual(row['published_at'], '2026-11-09T12:00:00Z')
        self.assertEqual(row['published_at_precision'], 'timestamp')
        self.assertEqual(row['report_period_date'], '2026-11-08')
        self.assertEqual(row['publication_date_basis'], 'canonical_report_publication_field')

    def test_old_title_period_does_not_prefilter_recent_publication(self):
        result = self.collect(self.body(), title_day=1)
        self.assertEqual(result['source_count'], 1, result['errors'])
        self.assertEqual(result['sources'][0]['report_period_date'], '2026-11-01')

    def test_future_canonical_publication_rejected_after_fetch(self):
        result = self.collect(self.body().replace('2026-11-09T12:00:00Z', '2026-11-11T12:00:00Z'))
        self.assertEqual(result['source_count'], 0)
        self.assertEqual(result['provider_diagnostics'][0]['rejected_counts']['rejected_date_out_of_window'], 1)

    def test_missing_canonical_date_is_evidence_failure_not_no_update(self):
        result = self.collect(self.body().replace('class="date"', 'class="undated"').replace('class="l-field-publish-date"', 'class="undated"'))
        self.assertEqual(result['source_count'], 0)
        self.assertFalse(result['no_update_eligible'])
        self.assertTrue(any('ocha_canonical_publication_date_unavailable' in e for e in result['errors']))

    def test_nested_layout_publication_field_and_unrelated_time(self):
        from bluefern_dispatches.gaza_sources import _ocha_canonical_publication_date as parse
        body = '<div class="layout"><div class="article"><div class="date"><span><time datetime="2026-11-09T12:00:00Z">09 Nov 2026</time></span></div></div><time datetime="2026-11-10T00:00:00Z">Other</time></div>'
        self.assertEqual(parse(body), ('2026-11-09T12:00:00Z', 'timestamp'))
        self.assertEqual(parse('<div class="outer"><div class="l-field-publish-date"><span>28 August 2026</span></div></div>'), ('2026-08-28', 'date'))

    def test_conflicting_or_unclosed_publication_fields_fail_closed(self):
        from bluefern_dispatches.gaza_sources import _ocha_canonical_publication_date as parse
        self.assertEqual(parse('<div><div class="date"><time datetime="2026-11-09T12:00:00Z"></time></div><div class="date"><time datetime="2026-11-10T12:00:00Z"></time></div></div>'), ('', 'unknown'))
        self.assertEqual(parse('<div class="date"><time datetime="2026-11-09T12:00:00Z"></time>'), ('', 'unknown'))

    def test_legacy_date_only_does_not_invent_midnight(self):
        from bluefern_dispatches.gaza_sources import _ocha_canonical_publication_date
        self.assertEqual(_ocha_canonical_publication_date('<div class="l-field-publish-date">28 August 2026</div>'), ('2026-08-28', 'date'))
        self.assertEqual(_ocha_canonical_publication_date('<div class="date"><time datetime="2026-11-09T12:00:00">9 November 2026</time></div>'), ('', 'unknown'))
class GazaHistoricalLineageTests(unittest.TestCase):
    """Actual retry04 records must not inherit unrelated historical evidence."""
    def setUp(self):
        records = json.loads((FIXTURE / 'lineage_sources.json').read_text())
        normalized, _, errors = normalize_sources(records, '2026-11-10', '2026-11-10T20:08:00Z')
        self.assertEqual(errors, [])
        self.stories, _, _ = curate_stories(normalized, '2026-11-10', '2026-11-10T20:08:00Z')
        self.priors = json.loads((FIXTURE / 'lineage_prior.json').read_text())

    def test_actual_unrelated_short_history_never_attaches(self):
        self.assertEqual(len(self.stories), 3)
        for story in self.stories:
            with self.subTest(title=story['title']):
                classification, _, prior = classify_against_prior(story, self.priors)
                self.assertEqual(classification, 'new')
                self.assertIsNone(prior)

    def test_exact_repeats_and_same_url_updates_remain_recognized(self):
        for story in self.stories:
            prior = memory_row('gaza', '2026-11-09', story)
            self.assertEqual(classify_against_prior(story, [prior])[0], 'duplicate_skip')
            changed = dict(story, summary='Officials announced a changed operational response today.')
            classification, _, matched = classify_against_prior(changed, [prior])
            self.assertIn(classification, {'major_update', 'continuing_development'})
            self.assertIs(matched, prior)

    def test_common_fuel_topic_does_not_bridge_opposite_place_and_time(self):
        prior = dict(self.priors[0], title='Northern Gaza hospital fuel delivery resumes',
                     summary='A fuel delivery reached a northern Gaza hospital in July.',
                     category='health', location='Northern Gaza', event_date='2026-07-12',
                     source_dates=['2026-07-12T10:00:00Z'])
        current = dict(self.stories[0], title='Southern Gaza hospital fuel delivery resumes',
                       summary='A fuel delivery reached a southern Gaza hospital in November.',
                       category='health', location='Southern Gaza', event_date='2026-11-10')
        self.assertEqual(classify_against_prior(current, [prior])[0], 'new')

    def test_shared_topic_and_matching_unproven_event_fields_are_insufficient(self):
        prior = dict(self.priors[0], title='Hospital fuel delivery planning',
                     summary='Officials described hospital fuel delivery planning.', category='health',
                     location='Gaza', event_date='2026-11-10')
        current = dict(self.stories[0], title='Hospital fuel delivery resumes',
                       summary='Officials announced hospital fuel delivery resumes today.', category='health',
                       location='Gaza', event_date='2026-11-10')
        self.assertEqual(classify_against_prior(current, [prior])[0], 'new')
        # Same documented report identity remains eligible for continuation.
        current['title'] = prior['title']
        classification, _, matched = classify_against_prior(current, [prior])
        self.assertIn(classification, {'major_update', 'continuing_development'})
        self.assertIs(matched, prior)

if __name__ == '__main__':
    unittest.main()


def test_context_cannot_lead_or_count_as_ground_development():
    from scripts.run_gaza_dispatch import curate_stories, _build_today_read
    records = json.loads((FIXTURE / "source_records.json").read_text())
    sources, _, errors = normalize_sources(records, "2026-11-10", "2026-11-10T20:00:00Z")
    assert not errors
    for source in sources:
        if "Hamilton" in source["title"]:
            source["attribution_mode"] = "gaza_adjacent_context"
            source["candidate_score"] = 999
    stories, _, _ = curate_stories(sources, "2026-11-10", "2026-11-10T20:00:00Z")
    assert any(story["context_only"] for story in stories)
    assert stories[0]["context_only"] is False
    first_context = next(i for i, story in enumerate(stories) if story["context_only"])
    assert all(story["context_only"] for story in stories[first_context:])
    overview = _build_today_read(stories, {source["source_record_id"]: source for source in sources})
    assert "context item" in overview[0]
    assert "Hamilton" not in " ".join(overview[1:])


def test_all_context_overview_is_labelled_without_ground_development_claim():
    from scripts.run_gaza_dispatch import _build_today_read
    story = {"title": "Visit outside Gaza", "summary": "A reported context-only visit outside Gaza.", "context_only": True}
    overview = _build_today_read([story], {})
    assert "0 reported developments and 1 context item" in overview[0]
    assert overview[1] == story["summary"]
