import copy
import gzip
import hashlib
import json
import sqlite3
import sys
import unittest
import zlib
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from test_rerank import StoreTestCase, fill  # noqa: E402
from test_scoring import GARDEN, entry, garden_neighborhood, inputs, neighborhood, position, record_of  # noqa: E402
from scoring import DEFAULT_HYPERPARAMETERS, Model, Question, RankingInputs, fit_model, questions  # noqa: E402
from measurement import (PAIR_TIE_POLICY, HistoryInputs, ReplayEvent, Trajectory, agreement, bootstrap, garden, pair_accuracy, replay,  # noqa: E402
                         simulate, store_report, wilson)
from extraction_retest import RetestTask, collect, prepare  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from ranking_experiments import FrozenFeedback, ReportWithSimulation, SavedReport, SavedSimulation, write_report  # noqa: E402
from rerank import rerank  # noqa: E402
from learning import decode  # noqa: E402
from schema import MIGRATIONS, plain_learning_events  # noqa: E402
from rescore_ranking_feedback import validate_saved_curves  # noqa: E402
from shapes import loads  # noqa: E402
from store import ListingStore, select  # noqa: E402


def required[T](value: T | None) -> T:
    """A value the measurement gives, which the test needs."""
    if value is None:
        raise AssertionError('The measurement lacks what the test needs.')
    return value


class MeasurementTests(unittest.TestCase):
    def test_simulation_supplies_unmutated_pre_answer_history(self) -> None:
        seen: list[Sequence[ReplayEvent]] = []

        def fitted(snapshot: HistoryInputs) -> Model:
            seen.append(copy.deepcopy(snapshot['history']))
            return fit_model(snapshot)

        def selector(snapshot: HistoryInputs, count: int, answered: Sequence[Sequence[str]]) -> list[Question]:
            return questions(snapshot, DEFAULT_HYPERPARAMETERS, count, answered)
        _ = simulate(inputs(neighborhood(20)), selector, fitted, runs=1, labels=5)
        self.assertEqual([len(history) for history in seen], [0, 5])
        self.assertEqual([len(event['inputs']['pairs']) for event in seen[-1]], list(range(5)))
        self.assertFalse(any(listing['paired'] for listing in seen[-1][0]['inputs']['listings']))

    def test_replay_passes_only_prior_event_snapshots_to_a_history_aware_model(self) -> None:
        initial = inputs(neighborhood(6))
        for listing in initial['listings']:
            listing['rank'] = 1
        later = copy.deepcopy(initial)
        later['listings'][0]['favorite'] = True
        events: list[ReplayEvent] = [{'kind': 'favorite', 'listing': 'listing:000', 'other': None, 'inputs': initial},
                                     {'kind': 'favorite', 'listing': 'listing:001', 'other': None, 'inputs': later}]
        observed: list[Sequence[ReplayEvent]] = []

        def fitted(snapshot: HistoryInputs) -> Model:
            observed.append(snapshot['history'])
            return fit_model(snapshot)
        _ = replay(events, fitted)
        self.assertEqual(observed[0], [])
        self.assertEqual(observed[1], [events[0]])
        self.assertFalse(observed[1][0]['inputs']['listings'][0]['favorite'])
        self.assertNotIn('history', events[0]['inputs'])

    def test_refitted_and_displayed_ranks_are_distinct_for_alternative_models(self) -> None:
        snapshot = inputs([entry('a', price=1000), entry('b', price=2000), entry('c', price=3000)])
        for number, listing in enumerate(snapshot['listings'], 1):
            listing['rank'] = number
        event: ReplayEvent = {'kind': 'favorite', 'listing': 'a', 'other': None, 'inputs': snapshot}

        def expensive_first(inputs: RankingInputs) -> Model:
            model = fit_model(inputs)
            weights = [0.0] * len(model.fit.weights)
            weights[position(model.design, 'price') + 1] = 2
            return replace(model, fit=replace(model.fit, weights=weights))

        def cheap_first(inputs: RankingInputs) -> Model:
            model = expensive_first(inputs)
            return replace(model, fit=replace(model.fit, weights=[-weight for weight in model.fit.weights]))
        expensive, cheap = replay([event], expensive_first), replay([event], cheap_first)
        self.assertEqual(expensive['held_out'][0]['rank'], 3)
        self.assertEqual(cheap['held_out'][0]['rank'], 1)
        self.assertEqual(expensive['held_out'][0]['displayed_rank'], 1)
        self.assertEqual(expensive['displayed_median_rank'], cheap['displayed_median_rank'])

    def test_rescoring_rejects_truncated_or_nonfinite_saved_curves(self) -> None:
        source: SavedSimulation = {'runs': 1, 'seed': 0, 'labels': 5, 'curves': [{'labels': 0, 'accuracy': 0.5, 'interval': None},
                                                                            {'labels': 5, 'accuracy': 0.6, 'interval': None}],
                                   'run_curves': [[0.5, 0.6]], 'tie_policy': PAIR_TIE_POLICY, 'feedback_file': 'frozen.feedback.json.gz',
                                   'feedback_sha256': ''}
        trajectories: list[Trajectory] = [{'pairs': [('a', 'b')] * 5, 'held_pairs': [], 'taste': {}}]
        self.assertEqual(validate_saved_curves(source, trajectories), [0, 5])
        truncated: list[list[list[float]]] = [[], [[]], [[0.5]], [[0.5, float('nan')]]]
        for curves in truncated:
            with self.assertRaises(ValueError):
                _ = validate_saved_curves({**source, 'run_curves': curves}, trajectories)

    def test_bootstrap_uses_symmetric_tail_indices(self) -> None:
        with mock.patch('measurement.random.Random') as generator:
            generator.configure_mock(**{'return_value.choices.side_effect': [[1], [2], [3], [4]]})
            self.assertEqual(bootstrap([0], samples=4, confidence=0.5), [2, 3])

    def test_an_indifferent_compare_prediction_is_reported_separately(self) -> None:
        event: ReplayEvent = {'kind': 'pair', 'listing': 'a', 'other': 'b', 'inputs': inputs([entry('a'), entry('b')])}
        report = replay([event])
        self.assertEqual(report['pairwise_answers'], 1)
        self.assertEqual(report['pairwise_ties'], 1)
        self.assertEqual(report['pairwise_scored'], 0)
        self.assertIsNone(report['pairwise_accuracy'])
        self.assertIsNone(report['pairwise_interval'])

    def test_equal_latent_tastes_score_chance_in_either_pair_order(self) -> None:
        self.assertEqual(pair_accuracy({'a': 1, 'b': -1}, {'a': 0, 'b': 0}, [('a', 'b'), ('b', 'a')]), 0.5)
        self.assertEqual(pair_accuracy({'a': 1, 'b': 1}, {'a': 2, 'b': 0}, [('a', 'b')]), 0.5)

    def test_simulation_is_seeded_and_scores_fixed_held_out_pairs(self) -> None:
        def selector(state: HistoryInputs, count: int, answered: Sequence[Sequence[str]]) -> list[Question]:
            return questions(state, DEFAULT_HYPERPARAMETERS, count, answered)
        result = simulate(inputs(neighborhood(20)), selector, runs=2, labels=5)
        self.assertEqual(result, simulate(inputs(neighborhood(20)), selector, runs=2, labels=5))
        self.assertEqual([point['labels'] for point in result['curves']], [0, 5])
        self.assertEqual(len(result['run_curves']), 2)

    def test_garden_regression_reports_the_unseen_unit_and_each_favorite(self) -> None:
        listings = garden_neighborhood()
        result = garden(inputs(listings), {'favorites': [listings[index]['id'] for index in GARDEN[:5]],
                                           'alike': [listings[GARDEN[5]]['id']]})
        self.assertEqual(result['after']['mean_rank'], 6)
        self.assertEqual(result['after']['wins'], result['after']['comparisons'])
        self.assertEqual(len(result['after']['leave_one_out']), 5)

    def test_intervals_include_uncertainty_and_empty_is_not_success(self) -> None:
        self.assertIsNone(wilson(0, 0))
        self.assertAlmostEqual(required(wilson(7, 10))[0], 0.396778, places=5)
        self.assertAlmostEqual(required(wilson(7, 10))[1], 0.892209, places=5)
        self.assertIsNone(bootstrap([]))
        self.assertEqual(bootstrap([3, 3, 3]), [3, 3])

    def test_replay_scores_before_each_label_and_ignores_unranked_candidates(self) -> None:
        snapshot = inputs(neighborhood(12))
        for listing in snapshot['listings']:
            listing['rank'] = 1 if listing['id'] == 'listing:003' else None
        events: list[ReplayEvent] = [{'kind': 'favorite', 'listing': 'listing:003', 'other': None, 'inputs': snapshot}]
        original = copy.deepcopy(events)
        report = replay(events)
        self.assertEqual(report['held_out'][0]['rank'], 1)
        self.assertEqual(report['held_out'][0]['candidates'], 1)
        self.assertEqual(events, original)
        events[0]['listing'] = 'listing:004'
        self.assertEqual(replay(events)['excluded'], 1)

    def test_identical_records_never_form_a_question_even_when_no_others_exist(self) -> None:
        self.assertEqual(questions(inputs([entry('a'), entry('b')]), DEFAULT_HYPERPARAMETERS, 1, []), [])

    def test_extraction_agreement_measures_categories_flags_ratings_and_tags(self) -> None:
        first = record_of(entry('a'))
        second = copy.deepcopy(first)
        second['floor'] = 'carpet'
        second['ratings']['natural_light'] = {'value': 5, 'confidence': 0.8}
        first['traits'], second['traits'] = ['brick', 'deck'], ['brick']
        result = agreement([first], [second])
        self.assertEqual(result['fields']['floor'], 0)
        self.assertEqual(result['fields']['rating:natural_light'], 0)
        self.assertEqual(result['fields']['traits'], 0.5)
        self.assertIn('floor', result['below_0.7'])


class JournalTests(StoreTestCase):
    home: Path
    store: ListingStore

    def test_restore_and_favorite_capture_both_transitions_in_order(self) -> None:
        identifier = fill(self.store, 6, 0)[0]
        _ = rerank(self.store, seed=1)
        self.store.feedback(identifier, dismissed=True)
        self.store.feedback(identifier, dismissed=False, favorite=True)
        with self.store.connect() as connection:
            events = list(select(connection, 'SELECT * FROM learning_events ORDER BY id'))
        self.assertEqual([cast(str, event['kind']) for event in events], ['dismiss', 'restore', 'favorite'])
        restored = next(item for item in decode(cast(str, events[-1]['inputs']))['listings'] if item['id'] == identifier)
        self.assertFalse(restored['dismissed'])
        self.assertFalse(restored['favorite'])
        self.assertTrue(restored['rankable'])
        self.assertTrue(self.store.listing(identifier)['favorite'])
        self.store.feedback(identifier, dismissed=True, favorite=True)
        self.assertFalse(self.store.listing(identifier)['favorite'])
        with self.store.connect() as connection:
            self.assertEqual([cast(str, row[0]) for row in select(connection, 'SELECT kind FROM learning_events ORDER BY id')],
                             ['dismiss', 'restore', 'favorite', 'dismiss'])

    def test_a_contact_on_a_favorite_is_not_an_independent_held_out_rank(self) -> None:
        identifier = fill(self.store, 6, 0)[0]
        _ = rerank(self.store, seed=1)
        self.store.feedback(identifier, favorite=True)
        self.store.request_contact(identifier)
        report = store_report(self.store.path)['prequential']
        self.assertEqual(len(report['held_out']), 1)
        self.assertEqual(len(report['known_positive_actions']), 1)
        self.assertEqual(report['known_positive_actions'][0]['kind'], 'contact')
        self.assertTrue(report['known_positive_actions'][0]['already_positive'])

    def legacy_journal(self, events: Sequence[tuple[object, ...]]) -> Path:
        legacy = self.home / 'legacy.sqlite3'
        version = MIGRATIONS.index(plain_learning_events)
        with closing(sqlite3.connect(legacy)) as connection, connection:
            connection.row_factory = sqlite3.Row
            for migration in MIGRATIONS[:version]:
                migration(connection)
            _ = connection.executemany('INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?)', events)
            _ = connection.execute(f'PRAGMA user_version = {version}')
        return legacy

    def test_snapshots_are_plain_json_and_compressed_rows_migrate_without_changing_events(self) -> None:
        _ = self.store.update_settings(preferences={'likes': ['Svijetla kuhinja — šuma'], 'dislikes': []})
        _ = fill(self.store, 30, 2)
        with self.store.connect() as connection:
            events = list(select(connection, 'SELECT * FROM learning_events ORDER BY id'))
            self.assertIsInstance(cast(object, events[0]['inputs']), str)
            self.assertTrue(all(row[0] for row in select(connection, 'SELECT json_valid(inputs) FROM learning_events')))
        original: list[tuple[object, ...]] = [tuple(event) for event in events]
        older = [(*original[0][:-1], zlib.compress(cast(str, original[0][-1]).encode('utf-8'))), original[1]]
        self.store = type(self.store)(self.legacy_journal(older))
        with self.store.connect() as connection:
            migrated = list(select(connection, 'SELECT * FROM learning_events ORDER BY id'))
        self.assertEqual([tuple(event) for event in migrated], original)
        self.store = type(self.store)(self.store.path)
        with self.store.connect() as connection:
            self.assertEqual([tuple(row) for row in select(connection, 'SELECT * FROM learning_events ORDER BY id')], original)

    def test_a_corrupt_compressed_row_aborts_the_conversion_without_partial_writes(self) -> None:
        _ = fill(self.store, 2, 2)
        with self.store.connect() as connection:
            events: list[tuple[object, ...]] = [tuple(row) for row in select(connection, 'SELECT * FROM learning_events ORDER BY id')]
        older = [(*events[0][:-1], zlib.compress(cast(str, events[0][-1]).encode('utf-8'))), (*events[1][:-1], b'corrupt')]
        legacy = self.legacy_journal(older)
        with self.assertRaises(zlib.error):
            _ = type(self.store)(legacy)
        with closing(sqlite3.connect(legacy)) as connection:
            self.assertEqual(connection.execute('SELECT * FROM learning_events ORDER BY id').fetchall(), older)
            self.assertEqual(cast(tuple[int], connection.execute('PRAGMA user_version').fetchone())[0], MIGRATIONS.index(plain_learning_events))

    def test_store_replay_keeps_the_rank_before_later_favorites(self) -> None:
        identifiers = fill(self.store, 12, 0)
        _ = rerank(self.store, seed=1)
        before = self.store.listing(identifiers[3])['rank']
        self.store.feedback(identifiers[3], favorite=True)
        self.store.feedback(identifiers[8], favorite=True)
        result = store_report(self.store.path)['prequential']
        self.assertEqual(result['held_out'][0]['rank'], before)
        self.assertEqual(len(result['held_out']), 2)
        self.assertIsNotNone(result['median_interval'])

    def test_report_preserves_frozen_feedback_in_a_checksummed_artifact(self) -> None:
        feedback: FrozenFeedback = {'inputs': 'snapshot', 'trajectories': [{'pairs': [], 'held_pairs': [['a', 'b']], 'taste': {}}]}
        report: ReportWithSimulation = {'simulation': {**feedback, 'runs': 1, 'seed': 0, 'labels': 0, 'curves': [], 'run_curves': [],
                                                       'tie_policy': PAIR_TIE_POLICY}}
        output = self.home / 'report.json'
        write_report(report, output)
        saved = cast(SavedReport, loads(output.read_text()))['simulation']
        compressed = (self.home / saved['feedback_file']).read_bytes()
        self.assertEqual(hashlib.sha256(compressed).hexdigest(), saved['feedback_sha256'])
        self.assertEqual(loads(gzip.decompress(compressed).decode('utf-8')), feedback)

    def test_failed_journal_write_rolls_back_the_user_action(self) -> None:
        identifier = fill(self.store, 1, 0)[0]
        with self.store.connect(write=True) as connection:
            _ = connection.execute("CREATE TRIGGER reject_event BEFORE INSERT ON learning_events BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.feedback(identifier, favorite=True)
        self.assertFalse(self.store.listing(identifier)['favorite'])

    def test_retest_prepares_independent_tasks_from_cached_content_and_collects_them(self) -> None:
        identifier = fill(self.store, 1, 0)[0]
        listing = self.store.listing(identifier)
        cache = self.home / 'cached'
        cache.mkdir()
        _ = (cache / 'photo.jpg').write_bytes(b'cached')
        _ = (cache / 'sheet.jpg').write_bytes(b'cached')
        _ = (cache / 'task.json').write_text(json.dumps({'id': identifier, 'fingerprint': listing['content_fingerprint'],
                                                        'files': ['photo.jpg'], 'sheets': ['sheet.jpg']}))
        destination = self.home / 'retest'
        result = prepare(self.store, cache, destination, count=1)
        self.assertEqual(result['calls'], 2)
        tasks = cast(list[RetestTask], loads((destination / 'tasks.json').read_text()))
        for task in tasks:
            _ = Path(task['output']).write_text(json.dumps({'id': identifier, 'fingerprint': listing['content_fingerprint'], 'record': extraction(1, task['phrases'])}))
        self.assertEqual(collect(destination)['below_0.7'], [])
        with self.assertRaisesRegex(ValueError, 'found 1'):
            _ = prepare(self.store, cache, self.home / 'too-few', count=20)

    def test_retest_ignores_unknown_and_duplicate_listing_manifests(self) -> None:
        identifier = fill(self.store, 1, 0)[0]
        fingerprint = self.store.listing(identifier)['content_fingerprint']
        cache = self.home / 'cache'
        for name, identity in [('a-unknown', 'missing:listing'), ('b-first', identifier), ('c-duplicate', identifier)]:
            directory = cache / name
            directory.mkdir(parents=True)
            _ = (directory / 'photo.jpg').write_bytes(b'cached')
            _ = (directory / 'sheet.jpg').write_bytes(b'cached')
            _ = (directory / 'task.json').write_text(json.dumps({'id': identity, 'fingerprint': fingerprint,
                                                                'files': ['photo.jpg'], 'sheets': ['sheet.jpg']}))
        self.assertEqual(prepare(self.store, cache, self.home / 'one', count=1)['listings'], 1)
        with self.assertRaisesRegex(ValueError, 'found 1'):
            _ = prepare(self.store, cache, self.home / 'two', count=2)

    def test_actions_capture_atomic_pre_action_inputs_and_idempotent_retries_add_nothing(self) -> None:
        identifiers = fill(self.store, 8, 0)
        self.store.feedback(identifiers[0], favorite=True)
        self.store.feedback(identifiers[0], favorite=True)
        self.store.answer_pair(*identifiers[1:3])
        self.store.answer_pair(*identifiers[1:3])
        self.store.request_contact(identifiers[3])
        self.store.request_contact(identifiers[3])
        with self.store.connect() as connection:
            events = list(select(connection, 'SELECT * FROM learning_events ORDER BY id'))
        self.assertEqual([cast(str, row['kind']) for row in events], ['favorite', 'pair', 'contact'])
        self.assertFalse(decode(cast(str, events[0]['inputs']))['listings'][0]['favorite'])
        self.assertTrue(decode(cast(str, events[1]['inputs']))['listings'][0]['favorite'])
        self.assertEqual(decode(cast(str, events[1]['inputs']))['pairs'], [])

    def test_measurement_reads_a_copy_and_leaves_source_bytes_unchanged(self) -> None:
        _ = fill(self.store, 8, 2)
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        result = store_report(self.store.path)
        self.assertEqual(before, hashlib.sha256(self.store.path.read_bytes()).hexdigest())
        self.assertEqual(len(result['leave_one_out']), 2)
        self.assertEqual(result['prequential']['excluded'], 2)


if __name__ == '__main__':
    _ = unittest.main()
