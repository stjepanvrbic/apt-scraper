import json
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from test_rerank import StoreTestCase, fill  # noqa: E402
from test_scoring import inputs, neighborhood  # noqa: E402
from test_implicit_signals import event  # noqa: E402
from learning import decode  # noqa: E402
from measurement import skip_above  # noqa: E402
from ranking_selectors import cold_questions  # noqa: E402
from ranking_selector_experiment import threshold_uncertainty, stop_condition  # noqa: E402
from measure_snapshot_cost import measure  # noqa: E402


class ColdQuestionsTests(unittest.TestCase):
    def test_a_censored_candidate_fires_the_stop_when_the_baseline_reached_the_target(self):
        self.assertEqual(stop_condition(20, None), 'fired')
        self.assertEqual(stop_condition(20, 25), 'fired')
        self.assertEqual(stop_condition(20, 20), 'fired')
        self.assertEqual(stop_condition(20, 15), 'not fired')
        self.assertEqual(stop_condition(0, 0), 'fired')
        self.assertTrue(stop_condition(None, None).startswith('not established'))

    def test_threshold_intervals_preserve_censoring_and_pair_users(self):
        report = threshold_uncertainty([[0.5, 0.8]], [[0.8, 0.9]])
        self.assertEqual(report['baseline_interval'], [5, 5])
        self.assertEqual(report['candidate_interval'], [0, 0])
        self.assertEqual(report['reduction_interval'], [1, 1])
        censored = threshold_uncertainty([[0.5, 0.6]], [[0.5, 0.6]])
        self.assertEqual(censored['baseline_interval'], [None, None])
        self.assertEqual(censored['candidate_interval'], [None, None])
        self.assertIsNone(censored['reduction_interval'])
        self.assertEqual(censored['censored_draws'], 2000)

    def test_eight_cold_questions_use_the_prior_and_do_not_repeat_listings(self):
        with mock.patch('scoring.fit_model', side_effect=AssertionError('No posterior fit at cold start')):
            questions = cold_questions(inputs(neighborhood(40)), 8, [])
        identifiers = [identifier for question in questions for identifier in question['listings']]
        self.assertEqual(len(identifiers), 16)
        self.assertEqual(len(set(identifiers)), 16)


class ImpressionTests(StoreTestCase):
    def test_cost_harness_keeps_the_supplied_copy_unchanged(self):
        fill(self.store, 4, 0)
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        report = measure(self.store.path, visits=3, samples=2)
        self.assertEqual(report['synthetic_impressions_added'], 12)
        self.assertEqual(report['original_store']['impression_rows'], 0)
        self.assertEqual(report['stress']['impression_rows'], 4)
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), before)
        output = self.home / 'cost.json'
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('measure_snapshot_cost.py')),
                                 str(self.store.path), str(output)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text())['stress']['impression_rows'], 4)
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), before)

    def test_snapshots_keep_only_the_last_seen_rank_per_listing_from_the_favorites_visit(self):
        first, second, third = fill(self.store, 3, 0)
        for visit in range(30):
            for rank, identifier in enumerate((first, second, third), 1):
                self.store.record_signals([{**event(self.store, listing_id=identifier), 'kind': 'impression',
                                           'rank': rank, 'visit_id': str(visit),
                                           'occurred_at': f'2026-01-01T00:00:{visit:02}+00:00'}])
        self.store.record_signals([{**event(self.store, listing_id=first), 'kind': 'impression', 'rank': 5,
                                   'visit_id': '29', 'occurred_at': '2026-01-01T01:01:00+01:00'}])
        self.store.record_signals([{**event(self.store, listing_id=third), 'kind': 'impression', 'rank': 1,
                                   'visit_id': 'future', 'occurred_at': '2999-01-01T00:00:00+00:00'}])
        self.store.feedback(third, favorite=True)
        self.store.feedback(second, dismissed=True)
        with self.store.connect() as connection:
            rows = [{**dict(row), 'inputs': decode(row['inputs'])} for row in connection.execute('SELECT * FROM learning_events ORDER BY id')]
            self.assertEqual(connection.execute("SELECT count(*) FROM implicit_signals").fetchone()[0], 92)
        self.assertEqual(len(rows[0]['inputs']['impressions']), 3)
        self.assertEqual({item['visit_id'] for item in rows[0]['inputs']['impressions']}, {'29'})
        self.assertEqual([(pair['chosen'], pair['other']) for pair in skip_above(rows)], [(third, second)])
        self.assertEqual(rows[1]['inputs'].get('impressions', []), [])

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
