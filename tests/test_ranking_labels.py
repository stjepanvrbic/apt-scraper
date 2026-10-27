import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_rerank import StoreTestCase, fill  # noqa: E402
from test_scoring import inputs, neighborhood  # noqa: E402
from test_implicit_signals import event  # noqa: E402
from learning import decode  # noqa: E402
from measurement import skip_above  # noqa: E402
from ranking_selectors import cold_questions  # noqa: E402


class ColdQuestionsTests(unittest.TestCase):
    def test_eight_cold_questions_use_the_prior_and_do_not_repeat_listings(self):
        with mock.patch('scoring.fit_model', side_effect=AssertionError('No posterior fit at cold start')):
            questions = cold_questions(inputs(neighborhood(40)), 8, [])
        identifiers = [identifier for question in questions for identifier in question['listings']]
        self.assertEqual(len(identifiers), 16)
        self.assertEqual(len(set(identifiers)), 16)


class ImpressionTests(StoreTestCase):
    def test_impressions_keep_seen_rank_without_changing_rank_or_revision(self):
        identifier = fill(self.store, 1, 0)[0]
        before = self.store.revision()
        impression = {**event(self.store, listing_id=identifier), 'kind': 'impression', 'rank': 3}
        self.assertEqual(self.store.record_signals([impression]), 1)
        self.assertEqual(self.store.record_signals([impression]), 0)
        self.assertEqual(self.store.revision(), before)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT shown_rank FROM implicit_signals').fetchone()[0], 3)
        for rank in (False, -1, 1.5, '3'):
            with self.assertRaises(ValueError):
                self.store.record_signals([{**impression, 'rank': rank}])

    def test_only_seen_unlabeled_listings_above_the_favorite_form_diagnostic_pairs(self):
        identifiers = fill(self.store, 4, 0)
        for index, identifier in enumerate(identifiers):
            impression = {**event(self.store, listing_id=identifier), 'kind': 'impression', 'rank': index + 1,
                          'visit_id': 'page', 'occurred_at': '2026-01-01T00:00:00+00:00'}
            self.store.record_signals([impression])
        self.store.feedback(identifiers[0], dismissed=True)
        self.store.feedback(identifiers[2], favorite=True)
        with self.store.connect() as connection:
            row = dict(connection.execute("SELECT * FROM learning_events WHERE kind = 'favorite'").fetchone())
        row['inputs'] = decode(row['inputs'])
        pairs = skip_above([row])
        self.assertEqual([(pair['chosen'], pair['other']) for pair in pairs], [(identifiers[2], identifiers[1])])
        self.assertEqual(self.store.pairwise_answers(), [])
        row['inputs']['impressions'][1]['occurred_at'] = '2999-01-01T00:00:00+00:00'
        self.assertEqual(skip_above([row]), [])

    def test_listing_summaries_expose_content_identity_for_impressions(self):
        identifier = fill(self.store, 1, 0)[0]
        summary = self.store.browse()['items'][0]
        self.assertEqual(summary['content_fingerprint'], self.store.listing(identifier)['content_fingerprint'])
        json.dumps(summary)


if __name__ == '__main__':
    unittest.main()
