import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict, Unpack, cast, override
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

from test_gallery_store import listing, present
from test_gallery_server import GalleryServerCase
from test_rerank import StoreTestCase, cpu_budget, fill, least_cpu_seconds, speed_factor
from rerank import pairwise_questions, rerank
from schema import MIGRATIONS
from implicit_signals import weak_labels
from scoring import RankingCheck, RankingInputs, fit_model, leave_one_out
from store import ListingStore, validate_listing


class EventValues(TypedDict, total=False):
    """The parts of an event a test sets, the ones the rejection tests put invalid values in loose."""
    listing_id: str
    visit_id: str
    fingerprint: str
    duration_ms: object
    photo_url: object


def event(store: ListingStore, kind: str = 'open', **values: Unpack[EventValues]) -> dict[str, object]:
    return {'event_id': str(uuid4()), 'visit_id': 'visit-1', 'listing_id': 'example:100',
            'fingerprint': store.listing(values.get('listing_id', 'example:100'))['content_fingerprint'],
            'kind': kind, 'occurred_at': '2026-10-25T12:00:00.000Z', **values}


class SignalStoreTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path: Path = Path(directory.name) / 'listings.sqlite3'
        self.store: ListingStore = ListingStore(self.path)
        _ = self.store.upsert_listing(listing())

    def test_atomic_idempotent_batches_survive_restart_without_refreshing_gallery(self) -> None:
        events = [event(self.store), event(self.store, 'dwell', duration_ms=1500),
                  event(self.store, 'photo', photo_url='https://example.com/kitchen.jpg'), event(self.store, 'close')]
        before = self.store.revision()
        self.assertEqual(self.store.record_signals(events), 4)
        self.assertEqual(self.store.record_signals(events), 0)
        with ListingStore(self.path).connect() as connection:
            saved = cast(list[sqlite3.Row], connection.execute('SELECT * FROM implicit_signals ORDER BY rowid').fetchall())
        self.assertEqual([cast(str, row['kind']) for row in saved], ['open', 'dwell', 'photo', 'close'])
        self.assertEqual(cast(int | None, saved[1]['duration_ms']), 1500)
        self.assertEqual(cast(str | None, saved[2]['photo_url']), 'https://example.com/kitchen.jpg')
        self.assertEqual(self.store.revision(), before)
        with self.assertRaisesRegex(ValueError, 'event_id'):
            _ = self.store.record_signals([event(self.store), {**events[1], 'duration_ms': 2000}])
        with self.store.connect() as connection:
            self.assertEqual(cast(tuple[int], connection.execute('SELECT count(*) FROM implicit_signals').fetchone())[0], 4)

    def test_invalid_batch_is_rejected_whole(self) -> None:
        valid = event(self.store)
        invalid: list[object] = [None, {}, {**valid, 'kind': 'zoom'}, {**valid, 'listing_id': 'missing'},
                                 {**valid, 'kind': []}, {**valid, 'event_id': ''}, {**valid, 'visit_id': 7},
                                 {**valid, 'fingerprint': 'bad'}, {**valid, 'occurred_at': 'yesterday'},
                                 {**valid, 'duration_ms': 1}, event(self.store, 'dwell', duration_ms=-1),
                                 event(self.store, 'dwell', duration_ms=True), event(self.store, 'dwell', duration_ms=1.5),
                                 event(self.store, 'photo', photo_url='javascript:alert(1)'),
                                 event(self.store, 'photo', photo_url=7)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, KeyError)):
                _ = self.store.record_signals([valid, value])
        batches: tuple[object, ...] = (None, {}, [], [valid] * 21)
        for batch in batches:
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                _ = self.store.record_signals(batch)
        with self.store.connect() as connection:
            self.assertEqual(cast(tuple[int], connection.execute('SELECT count(*) FROM implicit_signals').fetchone())[0], 0)

    def test_migration_preserves_existing_listing_feedback_and_ranking(self) -> None:
        path = self.path.parent / 'old.sqlite3'
        connection = sqlite3.connect(path)
        with connection:
            for migration in MIGRATIONS[:6]:
                migration(connection)
            _ = connection.execute('PRAGMA user_version = 6')
            content = validate_listing(listing())
            _ = connection.execute('INSERT INTO listings VALUES (?, ?, ?, ?, ?, ?)',
                                   (content['id'], json.dumps(content), 'available', content['first_seen'], 'fingerprint', 1))
            _ = connection.execute('INSERT INTO feedback VALUES (?, 1, 0)', (content['id'],))
            _ = connection.execute('INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES (?, ?, 1, ?)',
                                   (content['id'], 'fingerprint', 'Keep this reason.'))
        connection.close()
        store = ListingStore(path)
        saved = store.listing(content['id'])
        self.assertTrue(saved['favorite'])
        self.assertEqual((saved['rank'], saved['reason']), (1, {'code_only': False, 'features': {'text': 'Keep this reason.'}, 'similar_favorite': None}))
        self.assertEqual(store.record_signals([event(store, fingerprint='a' * 64)]), 1)

    def test_migration_preserves_distance_features_from_version_seven(self) -> None:
        path = self.path.parent / 'version-seven.sqlite3'
        connection = sqlite3.connect(path)
        with connection:
            for migration in MIGRATIONS[:7]:
                migration(connection)
            _ = connection.execute('PRAGMA user_version = 7')
        connection.close()
        old = ListingStore.__new__(ListingStore)
        old.path = path
        with patch('store.MIGRATIONS', MIGRATIONS[:7]):
            _ = old.upsert_listing(listing())
            with old.connect(write=True) as connection:
                _ = connection.execute('''UPDATE code_features SET nearest_station = 'Central', station_distance_km = 0.25,
                    place_distances_km = '{"Work": 1.5}' WHERE listing_id = 'example:100' ''')
            before = old.code_features()
        migrated = ListingStore(path)
        self.assertEqual(migrated.schema_version, len(MIGRATIONS))
        with migrated.connect() as connection:
            self.assertEqual(cast(tuple[int], connection.execute('SELECT count(*) FROM reviews').fetchone())[0], 0)
        self.assertEqual(migrated.code_features(), before)
        self.assertEqual(migrated.record_signals([event(migrated)]), 1)

    def test_a_review_waiting_at_version_eight_fails_at_migration_because_its_snapshot_lacks_the_reranks_inputs(self) -> None:
        path = self.path.parent / 'version-eight.sqlite3'
        connection = sqlite3.connect(path)
        snapshot: RankingCheck = {'order': [], 'exploration': [], 'scores': [], 'held_out': {}}
        with connection:
            for migration in MIGRATIONS[:8]:
                migration(connection)
            _ = connection.execute('PRAGMA user_version = 8')
            _ = connection.execute("INSERT INTO reranks VALUES (1, '2026-10-25T12:00:00+00:00', '{\"ranked\": 0}')")
            _ = connection.execute("INSERT INTO reviews VALUES (1, 'waiting', 0, '2026-10-25T12:00:00+00:00', ?, NULL)",
                                   (json.dumps(snapshot),))
        connection.close()
        migrated = ListingStore(path)
        self.assertEqual(migrated.schema_version, len(MIGRATIONS))
        review = present(present(migrated.last_rerank())['review'])
        self.assertEqual((review['state'], review['result']), ('failed', {'error': 'apt-scraper was updated after this rerank. Rerank to get a review.'}))
        self.assertIsNone(migrated.review_snapshot(1))
        _ = migrated.upsert_listing(listing())
        self.assertEqual(migrated.record_signals([event(migrated)]), 1)


class SignalHTTPTests(GalleryServerCase):
    def test_signal_route_authentication_validation_and_retry(self) -> None:
        body = {'events': [event(self.store), event(self.store, 'dwell', duration_ms=2500)]}
        for headers, expected in [({'Authorization': ''}, 401), ({'Origin': 'https://evil.example'}, 403)]:
            with self.assertRaises(HTTPError) as error:
                _ = self.request('/api/signals', body, headers)
            self.assertEqual(error.exception.code, expected)
        bodies: tuple[dict[str, object], ...] = ({'events': []}, {'events': [event(self.store, 'dwell', duration_ms=-1)]}, {**body, 'extra': 1})
        for invalid in bodies:
            with self.assertRaises(HTTPError) as error:
                _ = self.request('/api/signals', invalid)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request('/api/signals', body), {'recorded': 2})
        self.assertEqual(self.request('/api/signals', body), {'recorded': 0})


class SignalRerankTests(StoreTestCase):
    def test_rerank_with_signals_meets_the_two_second_cpu_budget(self) -> None:
        identifiers = fill(self.store, 300, 50)
        for index, identifier in enumerate(identifiers[50:]):
            values = {'listing_id': identifier, 'visit_id': identifier}
            _ = self.store.record_signals([event(self.store, **values),
                                          event(self.store, 'dwell', duration_ms=60000 if index < 50 else 1000, **values),
                                          event(self.store, 'close', **values)])
        seconds, result = least_cpu_seconds(lambda: rerank(self.store, seed=1))
        self.assertEqual(present(result.get('implicit_signals'))['weak_labels'], 250)
        self.assertLess(seconds, cpu_budget(), speed_factor())

    def test_diagnostic_weak_labels_never_change_ranks_explanations_or_questions(self) -> None:
        identifiers = fill(self.store, 24, 4)
        before = rerank(self.store, seed=7)
        snapshot = self.store.review_snapshot(before['id'])
        ranks = self.store.browse('Rankings')['items']
        explanation = self.store.listing(identifiers[10])['explanation']
        questions = pairwise_questions(self.store, 3)
        for index, identifier in enumerate(identifiers[4:]):
            values = {'listing_id': identifier, 'visit_id': identifier}
            _ = self.store.record_signals([event(self.store, **values),
                                          event(self.store, 'dwell', duration_ms=1000 if index % 2 else 60000, **values),
                                          event(self.store, 'close', **values)])
        after = rerank(self.store, seed=7)
        review = present(after['review'])
        self.assertEqual((review['state'], review['attempts']), ('waiting', 0))
        self.assertEqual(self.store.review_snapshot(after['id']), snapshot)
        self.assertEqual(self.store.browse('Rankings')['items'], ranks)
        self.assertEqual(self.store.listing(identifiers[10])['explanation'], explanation)
        self.assertEqual(pairwise_questions(self.store, 3), questions)
        for key in ('held_out', 'weights', 'exploration', 'prior_scale', 'unlabeled_weight'):
            self.assertEqual(after[key], before[key])
        report = present(after.get('implicit_signals'))
        self.assertGreater(report['weak_labels'], 0)
        self.assertEqual([row['id'] for row in report['held_out']], identifiers[:4])
        self.assertEqual(report['mean_rank_improvement'], present(before['held_out_mean']) - present(report['held_out_mean']))
        self.assertEqual(present(self.store.last_rerank()).get('implicit_signals'), report)

    def test_diagnostic_excludes_actions_stale_content_and_unfinished_visits(self) -> None:
        identifiers = fill(self.store, 8, 1)
        self.store.add_note('Explicit note', listing_id=identifiers[1])
        self.store.answer_pair(identifiers[2], identifiers[3])
        for index, identifier in enumerate(identifiers):
            values = {'listing_id': identifier, 'visit_id': identifier}
            events = [event(self.store, **values), event(self.store, 'dwell', duration_ms=1000, **values)]
            if index != 7:
                events.append(event(self.store, 'close', **values))
            _ = self.store.record_signals(events)
        _ = self.store.upsert_listing(listing(id=identifiers[6], title='Changed content'))
        inputs = self.store.ranking_inputs(include_signals=True)
        self.assertEqual(weak_labels(inputs), {identifiers[4]: (0, 0.1), identifiers[5]: (0, 0.1)})
        self.assertEqual(self.store.ranking_inputs().get('implicit_visits'), [])

    def test_short_and_long_visits_are_weak_labels_and_held_out_favorite_cannot_leak(self) -> None:
        identifiers = fill(self.store, 5, 1)
        for identifier, duration in zip(identifiers, [999999, 1000, 5000, 6000, 60000]):
            values = {'listing_id': identifier}
            _ = self.store.record_signals([event(self.store, **values), event(self.store, 'dwell', duration_ms=duration, **values),
                                          event(self.store, 'close', **values)])
        inputs = self.store.ranking_inputs(include_signals=True)
        self.assertEqual(weak_labels(inputs), {identifiers[1]: (0, 0.1), identifiers[4]: (1, 0.1)})
        full = fit_model(inputs, (1, 0.05), weak_labels=weak_labels(inputs))
        self.assertNotEqual(full.fit.weights, fit_model(inputs, (1, 0.05)).fit.weights)
        reused = fit_model(inputs, (1, 0.05), weak_labels=weak_labels(inputs), warm_start=fit_model(inputs, (1, 0.05)))
        self.assertEqual(leave_one_out(full).ranks, leave_one_out(reused).ranks)
        for cold, warm in zip(full.fit.weights, reused.fit.weights):
            self.assertAlmostEqual(cold, warm, places=5)
        without_favorite_signals: RankingInputs = {**inputs, 'implicit_visits': [visit for visit in inputs.get('implicit_visits', []) if visit['listing_id'] != identifiers[0]]}
        other = fit_model(without_favorite_signals, (1, 0.05), weak_labels=weak_labels(without_favorite_signals))
        self.assertEqual(leave_one_out(full).ranks, leave_one_out(other).ranks)

    def test_empty_signals_and_no_favorites_have_explicit_reports(self) -> None:
        _ = fill(self.store, 4, 0)
        result = rerank(self.store, seed=1)
        self.assertEqual(result.get('implicit_signals'), {
            'policy': 'completed_visible_dwell', 'weak_labels': 0, 'held_out': [],
            'held_out_mean': None, 'held_out_median': None, 'mean_rank_improvement': None, 'improves': None})
