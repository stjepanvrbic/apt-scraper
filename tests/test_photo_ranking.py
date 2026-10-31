"""The photo encoder's side of the ranking, with the standard library only: the gate, the named columns, their
explanations and leave-one-out values, the readings and hypotheses that name probes, the second read, the blind test
and the rerank time with every photo column on."""

import json
import random
import sqlite3
import sys
import tempfile
from collections.abc import Container, Mapping, Sequence
from pathlib import Path
from typing import TypedDict, cast, override
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/gallery/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

import store as store_module  # noqa: E402
from blind_test import BlindTestReport, BlindTestUnavailable, blind_test, top  # noqa: E402
from hypotheses import HypothesisTask, checked_proposals, pending_judgments  # noqa: E402
from photo_catalog import GATE_PROBES, MATCH_ROOMS, PROBE_PROMPTS, vocabulary  # noqa: E402
from rerank import rerank  # noqa: E402
from scoring import Design, design, display, fit, fit_model, held_out_observations, leave_one_out, scores  # noqa: E402
from store import ListingStore, PhotoFeatures, PhotoSimilarity  # noqa: E402
from test_rerank import StoreTestCase, cpu_budget, fill, least_cpu_seconds, speed_factor  # noqa: E402

MODEL = 'model-a'
Similarity = Mapping[str, Mapping[tuple[str, str], float]]
"""Best-match similarities as {room: {(listing, other): value}}."""


class Shown(TypedDict):
    """The listings a blind test stored before any pick, the part these tests read (blind_test.py writes it)."""
    listings: list[str]


def manifest(path: Path, *, room: bool = True, probes: bool = True) -> Path:
    _ = path.write_text(json.dumps({'name': 'test tower', 'identity': MODEL, 'quality': {
        'room_passed': room, 'probes_passed': probes, 'passed': room and probes}}))
    return path


def photo_features(store: ListingStore, identifiers: Sequence[str], *, seed: int = 3, similarity: Similarity | None = None,
                   reread: Container[str] = ()) -> None:
    """Random probe matches for every listing and random best-match similarities between all of them by room, unless
    similarity gives them as {room: {(listing, other): value}}."""
    generator = random.Random(seed)
    fingerprints = {listing['id']: listing['fingerprint'] for listing in store.ranking_inputs(photos=False)['listings']}
    features: dict[str, PhotoFeatures] = {
        identifier: {'fingerprint': fingerprints[identifier], 'probes': {name: generator.uniform(-0.1, 0.2) for name in PROBE_PROMPTS},
                     'rooms': ['kitchen'], 'reread': identifier in reread} for identifier in identifiers}
    if similarity is None:
        similarity = {room: {(first, second): generator.uniform(-0.2, 0.9) for first in identifiers for second in identifiers if first != second}
                      for room in MATCH_ROOMS}
    rows: list[PhotoSimilarity] = [(room, first, second, value) for room, pairs in similarity.items() for (first, second), value in pairs.items()]
    _ = store.save_photo_features(MODEL, features, rows)


def pass_blind_test(store: ListingStore, passed: bool = True) -> None:
    test = store.begin_blind_test(MODEL, 'criterion', {'listings': []})
    store.finish_blind_test(test, [], {'passed': passed})


def position_of(model_design: Design, name: str) -> int:
    """The column's position in a design the tests know has it."""
    position = model_design.index(name)
    if position is None:
        raise AssertionError(f'The design has no column {name}.')
    return position


class EncoderTestCase(StoreTestCase):
    manifest: Path

    @override
    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.manifest = manifest(Path(directory.name) / 'model.json')
        patcher = mock.patch.object(store_module, 'ENCODER_MANIFEST', self.manifest)
        _ = patcher.start()
        self.addCleanup(patcher.stop)


class GateTests(EncoderTestCase):
    def test_photo_columns_enter_only_after_the_quality_gate_and_a_passed_blind_test(self) -> None:
        identifiers = fill(self.store, 12, 3)
        photo_features(self.store, identifiers)
        self.assertNotIn('photos', self.store.ranking_inputs())
        self.assertFalse(any(column.name.startswith('looks_like:') for column in design(self.store.ranking_inputs()).columns))
        pass_blind_test(self.store, passed=False)
        self.assertFalse(self.store.encoder_state()['enabled'])
        pass_blind_test(self.store)
        self.assertTrue(self.store.encoder_state()['enabled'])
        names = {column.name for column in design(self.store.ranking_inputs()).columns}
        self.assertEqual({name for name in names if name.startswith('looks_like:')}, {f'looks_like:{room}' for room in MATCH_ROOMS})
        _ = manifest(self.manifest, probes=False)
        self.assertFalse(self.store.encoder_state()['enabled'])
        self.assertNotIn('photos', self.store.ranking_inputs())

    def test_without_a_shipped_encoder_nothing_changes(self) -> None:
        with mock.patch.object(store_module, 'ENCODER_MANIFEST', self.manifest.with_name('missing.json')):
            self.assertEqual(self.store.encoder_state()['model'], None)
            self.assertNotIn('photos', self.store.ranking_inputs(photos=True))

    def test_features_of_changed_content_are_not_used(self) -> None:
        identifiers = fill(self.store, 6, 2)
        photo_features(self.store, identifiers)
        pass_blind_test(self.store)
        changed = self.store.listing(identifiers[3])
        _ = self.store.upsert_listing({key: changed[key] for key in ('id', 'title', 'price', 'currency', 'rental_period', 'agent', 'size',
                                                                     'rooms', 'address', 'amenities', 'photos', 'sources', 'status')}
                                      | {'description': 'Changed.'})
        inputs = self.store.ranking_inputs()
        assert 'photos' in inputs
        photos = inputs['photos']
        self.assertNotIn(identifiers[3], photos['probes'])
        self.assertNotIn(identifiers[3], photos['similarity']['kitchen'])


class ColumnTests(EncoderTestCase):
    def test_a_room_column_is_the_best_match_with_another_favorite_and_names_it(self) -> None:
        identifiers = fill(self.store, 8, 2)
        first, second, other = identifiers[0], identifiers[1], identifiers[5]
        pairs = {(a, b): 0.0 for a in identifiers for b in identifiers if a != b}
        pairs.update({(other, first): 0.9, (other, second): 0.2, (first, second): 0.5, (second, first): 0.4})
        photo_features(self.store, identifiers, similarity={'kitchen': pairs})
        pass_blind_test(self.store)
        model_design = design(self.store.ranking_inputs())
        index = {identifier: position for position, identifier in enumerate(model_design.ids)}
        self.assertEqual(model_design.raw[index[other]]['looks_like:kitchen'], 0.9)
        self.assertEqual(model_design.raw[index[first]]['looks_like:kitchen'], 0.5)
        self.assertEqual(model_design.raw[index[second]]['looks_like:kitchen'], 0.4)
        title = self.store.listing(first)['title']
        self.assertEqual(display(model_design, index[other], 'looks_like:kitchen'), f'kitchen looks like your favorite {title}')
        column = position_of(model_design, 'looks_like:kitchen')
        center, scale = model_design.columns[column].mean, model_design.columns[column].scale
        without = model_design.rows_without[first][index[other]].head[column + 1]
        self.assertAlmostEqual(without, (0.2 - center) / scale)
        self.assertEqual(model_design.rows_without[second][index[other]].head[column + 1], model_design.likelihood_rows[index[other]].head[column + 1])
        self.assertEqual(model_design.rows_without[first][index[second]].head[column + 1], 0.0)

    def test_the_leave_one_out_check_with_room_columns_matches_exact_refits(self) -> None:
        identifiers = fill(self.store, 60, 12)
        photo_features(self.store, identifiers)
        pass_blind_test(self.store)
        model = fit_model(self.store.ranking_inputs())
        self.assertTrue(any(model.design.rematched.values()))
        check = leave_one_out(model)
        mean, precision = model.prior
        for favorite in model.design.favorites:
            data = held_out_observations(model.design, model.observations, favorite, model.unlabeled_weight)
            weights = fit(data, mean, precision, model.design.dense_count, model.fit.weights).weights
            exact = scores(model.design.rows_without[favorite], weights, model.design.dense_count)
            for ours, theirs in zip(check.scores[favorite], exact):
                self.assertAlmostEqual(ours, theirs, places=4)

    def test_a_probe_is_a_column_once_a_reading_names_it_and_earlier_readings_are_read_again(self) -> None:
        identifiers = fill(self.store, 10, 2)
        self.store.save_readings(preferences=[('dislike', 'Carpet', [{'feature': 'floor:carpet', 'direction': 'dislike'}]),
                                              ('like', 'Lots of daylight', [{'feature': 'rating:natural_light', 'direction': 'like'}])])
        photo_features(self.store, identifiers)
        self.assertEqual(self.store.unread_preferences(), [])
        self.assertNotIn('photo:carpet', self.store.reading_vocabulary())
        pass_blind_test(self.store)
        self.assertEqual(set(self.store.unread_preferences()), {('dislike', 'Carpet'), ('like', 'Lots of daylight')})
        self.assertTrue(set(vocabulary()) <= set(self.store.reading_vocabulary()))
        self.store.save_readings(preferences=[('dislike', 'Carpet', [{'feature': 'floor:carpet', 'direction': 'dislike'},
                                                                      {'feature': 'photo:carpet', 'direction': 'dislike'}]),
                                              ('like', 'Lots of daylight', [{'feature': 'rating:natural_light', 'direction': 'like'}])])
        self.assertEqual(self.store.unread_preferences(), [])
        model_design = design(self.store.ranking_inputs())
        position = position_of(model_design, 'photo:carpet')
        self.assertEqual(model_design.prior_mean[position], -1.0)
        self.assertTrue(model_design.columns[position].numeric)
        self.assertIsNone(model_design.index('photo:bathtub'))
        best = max(range(len(model_design.ids)), key=lambda index: model_design.raw[index]['photo:carpet'])
        self.assertEqual(display(model_design, best, 'photo:carpet'), 'photos look like "carpet"')

    def test_a_preference_read_as_photos_only_is_judged_per_listing_while_the_encoder_is_off(self) -> None:
        identifiers = fill(self.store, 10, 2)
        photo_features(self.store, identifiers)
        pass_blind_test(self.store)
        self.store.save_readings(preferences=[('dislike', 'Carpet', [{'feature': 'photo:carpet', 'direction': 'dislike'}]),
                                              ('like', 'Lots of daylight', [{'feature': 'rating:natural_light', 'direction': 'like'},
                                                                            {'feature': 'photo:bright rooms', 'direction': 'like'}])])
        self.assertIsNotNone(design(self.store.ranking_inputs()).index('photo:carpet'))
        self.assertEqual(self.store.unread_preferences(), [])
        pass_blind_test(self.store, passed=False)
        self.assertEqual(self.store.preference_readings()[('dislike', 'Carpet')], [])
        self.assertEqual(set(self.store.unread_preferences()), {('dislike', 'Carpet'), ('like', 'Lots of daylight')})
        model_design = design(self.store.ranking_inputs())
        self.assertIsNone(model_design.index('photo:carpet'))
        self.assertIsNotNone(model_design.index('preference:Carpet'))

    def test_a_visual_hypothesis_a_probe_scores_is_a_numeric_column_without_photo_reads(self) -> None:
        identifiers = fill(self.store, 10, 3)
        photo_features(self.store, identifiers)
        pass_blind_test(self.store)
        record = self.store.feature_record(identifiers[0])
        assert record is not None
        task: HypothesisTask = {
            'max_hypotheses': 3, 'columns': [], 'records': [{'id': identifiers[0], 'record': record['extraction']}],  # pyright: ignore[reportAssignmentType]  # checked_proposals reads only these keys of the task and of its records
            'live': [], 'retired': [], 'probes': sorted(PROBE_PROMPTS)}
        evidence = [{'id': identifiers[0], 'text': record['extraction']['summary']}]
        proposals = checked_proposals([{'phrase': 'brick walls everywhere', 'kind': 'visual', 'evidence': evidence, 'probe': 'exposed brick'}], task)
        proposal = proposals[0]
        assert 'probe' in proposal
        self.assertEqual(proposal['probe'], 'exposed brick')
        for wrong in ({'kind': 'textual', 'probe': 'exposed brick'}, {'kind': 'visual', 'probe': 'a probe that does not exist'}):
            with self.assertRaises(ValueError):
                _ = checked_proposals([{'phrase': 'brick walls everywhere', 'evidence': evidence, **wrong}], task)
        with self.store.connect(write=True) as connection:
            _ = connection.execute("INSERT INTO hypotheses (id, phrase, kind, state, evidence, start_event, probe) VALUES ('h', 'brick walls everywhere', 'visual', 'active', '[]', 0, 'exposed brick')")
        model_design = design(self.store.ranking_inputs())
        column = model_design.columns[position_of(model_design, 'hypothesis:brick walls everywhere')]
        self.assertTrue(column.numeric)
        self.assertFalse(column.sparse)
        self.assertEqual(pending_judgments(self.store), [])


class RereadTests(EncoderTestCase):
    def test_a_record_the_encoder_disagrees_with_is_read_once_more(self) -> None:
        identifiers = fill(self.store, 4, 0)
        photo_features(self.store, identifiers, reread=[identifiers[1]])
        self.assertEqual(self.store.photo_rereads(), {identifiers[1]})
        _ = manifest(self.manifest, room=False)
        self.assertEqual(self.store.photo_rereads(), set())
        _ = manifest(self.manifest)
        feature_record = self.store.feature_record(identifiers[1])
        assert feature_record is not None
        record = feature_record['extraction']
        fingerprint = self.store.listing(identifiers[1])['content_fingerprint']
        preferences: dict[str, object] = {}
        reread = {key: value for key, value in record.items() if key != 'photos'} | {
            'photos': [{'index': photo['index'], 'room': photo['room']} for photo in record['photos']], 'preferences': preferences}
        _ = self.store.save_extraction(identifiers[1], fingerprint, reread, photos=self.store.listing(identifiers[1])['photos'], phrases=[])
        self.assertEqual(self.store.photo_rereads(), set())


class BlindTests(EncoderTestCase):
    identifiers: list[str]
    page: Path

    @override
    def setUp(self) -> None:
        super().setUp()
        self.identifiers = fill(self.store, 40, 4)
        photo_features(self.store, self.identifiers)
        self.page = self.home / 'blind.html'

    def test_the_blind_test_runs_end_to_end_on_a_labeled_store(self) -> None:
        lines: list[str] = []
        asked: list[dict[str, object]] = []
        store = self.store

        def ask(_prompt: str) -> str:
            with store.connect() as connection:
                row = cast(sqlite3.Row, connection.execute('SELECT * FROM encoder_blind_tests').fetchone())
            asked.append(dict(row))
            shown = cast(Shown, json.loads(cast(str, row['listings'])))['listings']
            preferred = top(store.ranking_inputs(photos=True), shown)
            return ' '.join(str(shown.index(identifier) + 1) for identifier in preferred)

        result = blind_test(self.store, self.page, seed=5, ask=ask, say=lines.append)
        self.assertEqual(len(asked), 1)
        self.assertIsNone(asked[0]['result'])
        self.assertIn('Pre-registered criterion', cast(str, asked[0]['criterion']))
        self.assertTrue(lines[0].startswith('Pre-registered criterion'))
        shown = cast(Shown, json.loads(cast(str, asked[0]['listings'])))['listings']
        self.assertEqual(len(shown), 30)
        self.assertFalse(set(shown) & set(self.identifiers[:4]))
        text = self.page.read_text(encoding='utf-8')
        self.assertIn('<img', text)
        self.assertNotIn('rank', text.lower().replace('ranked', ''))
        self.assertEqual(result['precision_with_encoder'], 1.0)
        without = len(set(result['top_without_encoder']) & set(result['picks'])) / 10
        self.assertEqual(result['precision_without_encoder'], without)
        self.assertEqual(result['passed'], without < 1.0)
        self.assertEqual(self.store.encoder_state()['enabled'], result['passed'])
        with self.store.connect() as connection:
            row = cast(sqlite3.Row, connection.execute('SELECT result FROM encoder_blind_tests').fetchone())
            stored = cast(BlindTestReport, json.loads(cast(str, row['result'])))
        self.assertEqual(stored, result)

    def test_picks_must_be_ten_different_shown_numbers_and_the_model_without_encoder_can_win(self) -> None:
        lines: list[str] = []
        answers = iter(['1 2 3', '1 1 2 3 4 5 6 7 8 9', '0 1 2 3 4 5 6 7 8 9'])
        store = self.store

        def ask(_prompt: str) -> str:
            try:
                return next(answers)
            except StopIteration:
                with store.connect() as connection:
                    row = cast(sqlite3.Row, connection.execute('SELECT listings FROM encoder_blind_tests').fetchone())
                shown = cast(Shown, json.loads(cast(str, row['listings'])))['listings']
                preferred = top(store.ranking_inputs(photos=False), shown)
                return ','.join(str(shown.index(identifier) + 1) for identifier in preferred)

        result = blind_test(self.store, self.page, seed=6, ask=ask, say=lines.append)
        self.assertEqual(sum('Give' in line for line in lines), 3)
        self.assertEqual(result['precision_without_encoder'], 1.0)
        self.assertFalse(result['passed'])
        self.assertFalse(self.store.encoder_state()['enabled'])

    def test_both_models_rank_the_snapshot_taken_before_the_picks_and_listings_without_photos_are_never_shown(self) -> None:
        with self.store.connect(write=True) as connection:
            _ = connection.executemany("UPDATE photo_features SET probes = '{}' WHERE listing_id = ?", [(identifier,) for identifier in self.identifiers[4:15]])
        with self.assertRaisesRegex(BlindTestUnavailable, '25 ranked listings'):
            _ = blind_test(self.store, self.page, picks='1 2 3 4 5 6 7 8 9 10')
        with self.store.connect(write=True) as connection:
            _ = connection.execute("UPDATE photo_features SET probes = '{\"carpet\": 0.1}' WHERE probes = '{}'")
        expected: dict[str, list[str]] = {}
        store = self.store

        def ask(_prompt: str) -> str:
            with store.connect() as connection:
                row = cast(sqlite3.Row, connection.execute('SELECT listings FROM encoder_blind_tests ORDER BY id DESC').fetchone())
            shown = cast(Shown, json.loads(cast(str, row['listings'])))['listings']
            expected['without'] = top(store.ranking_inputs(photos=False), shown)
            for identifier in shown[:3]:
                store.feedback(identifier, favorite=True)
            return '1 2 3 4 5 6 7 8 9 10'

        pass_blind_test(self.store)
        self.store.save_readings(preferences=[('dislike', 'Carpet', [{'feature': 'photo:carpet', 'direction': 'dislike'}])])
        pass_blind_test(self.store, passed=False)
        result = blind_test(self.store, self.page, seed=7, ask=ask, say=lambda line: None)
        self.assertEqual(result['top_without_encoder'], expected['without'])

    def test_the_blind_test_needs_labels_and_thirty_unlabeled_listings_with_photo_features(self) -> None:
        with self.assertRaisesRegex(BlindTestUnavailable, 'needs 40'):
            _ = blind_test(self.store, self.page, count=40, picks='1 2 3 4 5 6 7 8 9 10')
        for identifier in self.identifiers[:4]:
            self.store.feedback(identifier, favorite=False)
        with self.assertRaisesRegex(BlindTestUnavailable, 'none yet'):
            _ = blind_test(self.store, self.page, picks='1 2 3 4 5 6 7 8 9 10')
        with self.assertRaises(ValueError):
            _ = blind_test(self.store, self.page, count=29, picks='1 2 3 4 5 6 7 8 9 10')
        _ = manifest(self.manifest, probes=False)
        with self.assertRaisesRegex(BlindTestUnavailable, 'did not pass its quality measurements'):
            _ = blind_test(self.store, self.page, picks='1 2 3 4 5 6 7 8 9 10')


class TimingTests(EncoderTestCase):
    def test_the_300_listing_50_favorite_rerank_with_every_photo_column_stays_under_two_seconds(self) -> None:
        """Spec › Acceptance "Rerank" with the encoder on: four room columns and two probe columns."""
        identifiers = fill(self.store, 300, 50)
        self.store.save_readings(preferences=[('dislike', 'Carpet', [{'feature': 'floor:carpet', 'direction': 'dislike'}])])
        photo_features(self.store, identifiers)
        pass_blind_test(self.store)
        self.store.save_readings(preferences=[
            ('dislike', 'Carpet', [{'feature': 'floor:carpet', 'direction': 'dislike'}, {'feature': 'photo:carpet', 'direction': 'dislike'}]),
            ('like', 'Lots of daylight', [{'feature': 'photo:bright rooms', 'direction': 'like'}])])
        columns = {column.name for column in fit_model(self.store.ranking_inputs()).design.columns}
        self.assertTrue({'photo:carpet', 'photo:bright rooms', *(f'looks_like:{room}' for room in MATCH_ROOMS)} <= columns)
        seconds, summary = least_cpu_seconds(lambda: rerank(self.store, seed=1))
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (300, 50))
        self.assertLess(seconds, cpu_budget(), speed_factor())


assert set(GATE_PROBES) <= set(PROBE_PROMPTS)
