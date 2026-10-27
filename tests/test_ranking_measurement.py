import copy
import gzip
import hashlib
import json
import sqlite3
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_rerank import StoreTestCase, fill  # noqa: E402
from test_scoring import GARDEN, entry, garden_neighborhood, inputs, neighborhood  # noqa: E402
from scoring import DEFAULT_HYPERPARAMETERS, questions  # noqa: E402
from measurement import agreement, bootstrap, garden, pair_accuracy, replay, simulate, store_report, wilson  # noqa: E402
from extraction_retest import collect, prepare  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from ranking_experiments import write_report  # noqa: E402
from rerank import rerank  # noqa: E402
from learning import decode, encode  # noqa: E402
from schema import MIGRATIONS, compress_learning_events  # noqa: E402


class MeasurementTests(unittest.TestCase):
    def test_bootstrap_uses_symmetric_tail_indices(self):
        with mock.patch('measurement.random.Random') as generator:
            generator.return_value.choices.side_effect = [[1], [2], [3], [4]]
            self.assertEqual(bootstrap([0], samples=4, confidence=0.5), [2, 3])

    def test_an_indifferent_compare_prediction_is_reported_separately(self):
        event = {'kind': 'pair', 'listing': 'a', 'other': 'b', 'inputs': inputs([entry('a'), entry('b')])}
        report = replay([event])
        self.assertEqual(report['pairwise_answers'], 1)
        self.assertEqual(report['pairwise_ties'], 1)
        self.assertEqual(report['pairwise_scored'], 0)
        self.assertIsNone(report['pairwise_accuracy'])
        self.assertIsNone(report['pairwise_interval'])

    def test_equal_latent_tastes_score_chance_in_either_pair_order(self):
        self.assertEqual(pair_accuracy({'a': 1, 'b': -1}, {'a': 0, 'b': 0}, [('a', 'b'), ('b', 'a')]), 0.5)
        self.assertEqual(pair_accuracy({'a': 1, 'b': 1}, {'a': 2, 'b': 0}, [('a', 'b')]), 0.5)
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
    def test_restore_and_favorite_capture_both_transitions_in_order(self):
        identifier = fill(self.store, 6, 0)[0]
        rerank(self.store, seed=1)
        self.store.feedback(identifier, dismissed=True)
        self.store.feedback(identifier, dismissed=False, favorite=True)
        with self.store.connect() as connection:
            events = list(connection.execute('SELECT * FROM learning_events ORDER BY id'))
        self.assertEqual([event['kind'] for event in events], ['dismiss', 'restore', 'favorite'])
        restored = next(item for item in decode(events[-1]['inputs'])['listings'] if item['id'] == identifier)
        self.assertFalse(restored['dismissed'])
        self.assertFalse(restored['favorite'])
        self.assertTrue(restored['rankable'])
        self.assertTrue(self.store.listing(identifier)['favorite'])
        self.store.feedback(identifier, dismissed=True, favorite=True)
        self.assertFalse(self.store.listing(identifier)['favorite'])
        with self.store.connect() as connection:
            self.assertEqual([row[0] for row in connection.execute('SELECT kind FROM learning_events ORDER BY id')],
                             ['dismiss', 'restore', 'favorite', 'dismiss'])

    def test_a_contact_on_a_favorite_is_not_an_independent_held_out_rank(self):
        identifier = fill(self.store, 6, 0)[0]
        rerank(self.store, seed=1)
        self.store.feedback(identifier, favorite=True)
        self.store.request_contact(identifier)
        report = store_report(self.store.path)['prequential']
        self.assertEqual(len(report['held_out']), 1)
        self.assertEqual(len(report['known_positive_actions']), 1)
        self.assertEqual(report['known_positive_actions'][0]['kind'], 'contact')
        self.assertTrue(report['known_positive_actions'][0]['already_positive'])

    def test_snapshots_are_compressed_and_existing_text_rows_migrate(self):
        fill(self.store, 30, 2)
        with self.store.connect(write=True) as connection:
            events = list(connection.execute('SELECT * FROM learning_events ORDER BY id'))
            self.assertIsInstance(events[0]['inputs'], bytes)
            self.assertLess(len(events[0]['inputs']), len(encode(decode(events[0]['inputs'])).encode()) / 2)
            original = [(event['id'], decode(event['inputs'])) for event in events]
            for identifier, snapshot in original:
                connection.execute('UPDATE learning_events SET inputs = ? WHERE id = ?', (encode(snapshot), identifier))
            connection.execute(f'PRAGMA user_version = {MIGRATIONS.index(compress_learning_events)}')
        self.store = type(self.store)(self.store.path)
        with self.store.connect() as connection:
            migrated = list(connection.execute('SELECT * FROM learning_events ORDER BY id'))
        self.assertTrue(all(isinstance(event['inputs'], bytes) for event in migrated))
        self.assertEqual([(event['id'], decode(event['inputs'])) for event in migrated], original)
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
            Path(task['output']).write_text(json.dumps({'id': identifier, 'fingerprint': listing['content_fingerprint'], 'record': extraction(1, task['phrases'])}))
        self.assertEqual(collect(destination)['below_0.7'], [])
        with self.assertRaisesRegex(ValueError, 'found 1'):
            prepare(self.store, cache, self.home / 'too-few', count=20)

    def test_retest_ignores_unknown_and_duplicate_listing_manifests(self):
        identifier = fill(self.store, 1, 0)[0]
        fingerprint = self.store.listing(identifier)['content_fingerprint']
        cache = self.home / 'cache'
        for name, identity in [('a-unknown', 'missing:listing'), ('b-first', identifier), ('c-duplicate', identifier)]:
            directory = cache / name
            directory.mkdir(parents=True)
            (directory / 'photo.jpg').write_bytes(b'cached')
            (directory / 'sheet.jpg').write_bytes(b'cached')
            (directory / 'task.json').write_text(json.dumps({'id': identity, 'fingerprint': fingerprint,
                                                            'files': ['photo.jpg'], 'sheets': ['sheet.jpg']}))
        self.assertEqual(prepare(self.store, cache, self.home / 'one', count=1)['listings'], 1)
        with self.assertRaisesRegex(ValueError, 'found 1'):
            prepare(self.store, cache, self.home / 'two', count=2)

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
        self.assertFalse(decode(events[0]['inputs'])['listings'][0]['favorite'])
        self.assertTrue(decode(events[1]['inputs'])['listings'][0]['favorite'])
        self.assertEqual(decode(events[1]['inputs'])['pairs'], [])

    def test_measurement_reads_a_copy_and_leaves_source_bytes_unchanged(self):
        fill(self.store, 8, 2)
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        result = store_report(self.store.path)
        self.assertEqual(before, hashlib.sha256(self.store.path.read_bytes()).hexdigest())
        self.assertEqual(len(result['leave_one_out']), 2)
        self.assertEqual(result['prequential']['excluded'], 2)


if __name__ == '__main__':
    unittest.main()
