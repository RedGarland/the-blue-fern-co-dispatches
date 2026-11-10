"""Read-only, non-fixture regression of the real normalized Nov10 payload."""
import json
import unittest
from pathlib import Path
from scripts.run_gaza_dispatch import normalize_sources, curate_stories
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

if __name__ == '__main__':
    unittest.main()
