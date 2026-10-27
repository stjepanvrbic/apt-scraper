import copy
import gzip
import hashlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_rerank import StoreTestCase, fill  # noqa: E402
from test_scoring import GARDEN, entry, garden_neighborhood, inputs, neighborhood  # noqa: E402
from scoring import DEFAULT_HYPERPARAMETERS, questions  # noqa: E402
from measurement import agreement, bootstrap, garden, replay, simulate, store_report, wilson  # noqa: E402
from extraction_retest import collect, prepare  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from ranking_experiments import write_report  # noqa: E402
from rerank import rerank  # noqa: E402


class MeasurementTests(unittest.TestCase):
    def test_simulation_is_seeded_and_scores_fixed_held_out_pairs(self):
        def selector(state, count, answered):
            return questions(state, DEFAULT_HYPERPARAMETERS, count, answered)
        result = simulate(inputs(neighborhood(20)), selector, runs=2, labels=5)
        self.assertEqual(result, simulate(inputs(neighborhood(20)), selector, runs=2, labels=5))
        self.assertEqual([point['labels'] for point in result['curves']], [0, 5])
        self.assertEqual(len(result['run_curves']), 2)

    def test_garden_regression_reports_the_unseen_unit_and_each_favorite(self):
        listings = garden_neighborhood()
        result = garden(inputs(listings), {'favorites': [listings[index]['id'] for index in GARDEN[:5]],
                                           'alike': [listings[GARDEN[5]]['id']]})
        self.assertEqual(result['after']['mean_rank'], 6)
        self.assertEqual(result['after']['wins'], result['after']['comparisons'])
        self.assertEqual(len(result['after']['leave_one_out']), 5)
    def test_intervals_include_uncertainty_and_empty_is_not_success(self):
        self.assertIsNone(wilson(0, 0))
        self.assertAlmostEqual(wilson(7, 10)[0], 0.396778, places=5)
        self.assertAlmostEqual(wilson(7, 10)[1], 0.892209, places=5)
        self.assertIsNone(bootstrap([]))
        self.assertEqual(bootstrap([3, 3, 3]), [3, 3])

    def test_replay_scores_before_each_label_and_ignores_unranked_candidates(self):
        snapshot = inputs(neighborhood(12))
        for listing in snapshot['listings']:
            listing['rank'] = 1 if listing['id'] == 'listing:003' else None
        events = [{'kind': 'favorite', 'listing': 'listing:003', 'other': None, 'inputs': snapshot}]
        original = copy.deepcopy(events)
        report = replay(events)
        self.assertEqual(report['held_out'][0]['rank'], 1)
        self.assertEqual(report['held_out'][0]['candidates'], 1)
        self.assertEqual(events, original)
        events[0]['listing'] = 'listing:004'
        self.assertEqual(replay(events)['excluded'], 1)

    def test_identical_records_never_form_a_question_even_when_no_others_exist(self):
        self.assertEqual(questions(inputs([entry('a'), entry('b')]), DEFAULT_HYPERPARAMETERS, 1, []), [])

    def test_extraction_agreement_measures_categories_flags_ratings_and_tags(self):
        first = entry('a')['record']
        second = copy.deepcopy(first)
        second['floor'] = 'carpet'
        second['ratings']['natural_light']['value'] = 5
        first['traits'], second['traits'] = ['brick', 'deck'], ['brick']
        result = agreement([first], [second])
        self.assertEqual(result['fields']['floor'], 0)
        self.assertEqual(result['fields']['rating:natural_light'], 0)
        self.assertEqual(result['fields']['traits'], 0.5)
        self.assertIn('floor', result['below_0.7'])


class JournalTests(StoreTestCase):
    def test_store_replay_keeps_the_rank_before_later_favorites(self):
        identifiers = fill(self.store, 12, 0)
        rerank(self.store, seed=1)
        before = self.store.listing(identifiers[3])['rank']
        self.store.feedback(identifiers[3], favorite=True)
        self.store.feedback(identifiers[8], favorite=True)
        result = store_report(self.store.path)['prequential']
        self.assertEqual(result['held_out'][0]['rank'], before)
        self.assertEqual(len(result['held_out']), 2)
        self.assertIsNotNone(result['median_interval'])

    def test_report_preserves_frozen_feedback_in_a_checksummed_artifact(self):
        feedback = {'inputs': 'snapshot', 'trajectories': [{'pairs': [['a', 'b']]}]}
        report = {'simulation': {**feedback, 'curves': []}}
        output = self.home / 'report.json'
        write_report(report, output)
        saved = json.loads(output.read_text())['simulation']
        compressed = (self.home / saved['feedback_file']).read_bytes()
        self.assertEqual(hashlib.sha256(compressed).hexdigest(), saved['feedback_sha256'])
        self.assertEqual(json.loads(gzip.decompress(compressed)), feedback)

    def test_failed_journal_write_rolls_back_the_user_action(self):
        identifier = fill(self.store, 1, 0)[0]
        with self.store.connect(write=True) as connection:
            connection.execute("CREATE TRIGGER reject_event BEFORE INSERT ON learning_events BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.feedback(identifier, favorite=True)
        self.assertFalse(self.store.listing(identifier)['favorite'])

    def test_retest_prepares_independent_tasks_from_cached_content_and_collects_them(self):
        identifier = fill(self.store, 1, 0)[0]
        listing = self.store.listing(identifier)
        cache = self.home / 'cached'
        cache.mkdir()
        (cache / 'photo.jpg').write_bytes(b'cached')
        (cache / 'sheet.jpg').write_bytes(b'cached')
        (cache / 'task.json').write_text(json.dumps({'id': identifier, 'fingerprint': listing['content_fingerprint'],
                                                    'files': ['photo.jpg'], 'sheets': ['sheet.jpg']}))
        destination = self.home / 'retest'
        result = prepare(self.store, cache, destination, count=1)
        self.assertEqual(result['calls'], 2)
        tasks = json.loads((destination / 'tasks.json').read_text())
        for task in tasks:
            Path(task['output']).write_text(json.dumps({'id': identifier, 'fingerprint': listing['content_fingerprint'], 'record': extraction(1)}))
        self.assertEqual(collect(destination)['below_0.7'], [])
        with self.assertRaisesRegex(ValueError, 'found 1'):
            prepare(self.store, cache, self.home / 'too-few', count=20)

    def test_actions_capture_atomic_pre_action_inputs_and_idempotent_retries_add_nothing(self):
        identifiers = fill(self.store, 8, 0)
        self.store.feedback(identifiers[0], favorite=True)
        self.store.feedback(identifiers[0], favorite=True)
        self.store.answer_pair(*identifiers[1:3])
        self.store.answer_pair(*identifiers[1:3])
        self.store.request_contact(identifiers[3])
        self.store.request_contact(identifiers[3])
        with self.store.connect() as connection:
            events = list(connection.execute('SELECT * FROM learning_events ORDER BY id'))
        self.assertEqual([row['kind'] for row in events], ['favorite', 'pair', 'contact'])
        self.assertFalse(json.loads(events[0]['inputs'])['listings'][0]['favorite'])
        self.assertTrue(json.loads(events[1]['inputs'])['listings'][0]['favorite'])
        self.assertEqual(json.loads(events[1]['inputs'])['pairs'], [])

    def test_measurement_reads_a_copy_and_leaves_source_bytes_unchanged(self):
        fill(self.store, 8, 2)
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        result = store_report(self.store.path)
        self.assertEqual(before, hashlib.sha256(self.store.path.read_bytes()).hexdigest())
        self.assertEqual(len(result['leave_one_out']), 2)
        self.assertEqual(result['prequential']['excluded'], 2)


if __name__ == '__main__':
    unittest.main()
