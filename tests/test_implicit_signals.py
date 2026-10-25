import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from uuid import uuid4

from test_gallery_store import ListingStore, listing
import test_gallery_server
from test_rerank import StoreTestCase, fill
from rerank import pairwise_questions, rerank
from schema import MIGRATIONS
from implicit_signals import weak_labels
from scoring import fit_model


def event(store, kind='open', **values):
    return {'event_id': str(uuid4()), 'visit_id': 'visit-1', 'listing_id': 'example:100',
            'fingerprint': store.listing(values.get('listing_id', 'example:100'))['content_fingerprint'],
            'kind': kind, 'occurred_at': '2026-10-25T12:00:00.000Z', **values}


class SignalStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'listings.sqlite3'
        self.store = ListingStore(self.path)
        self.store.upsert_listing(listing())

    def test_atomic_idempotent_batches_survive_restart_without_refreshing_gallery(self):
        events = [event(self.store), event(self.store, 'dwell', duration_ms=1500),
                  event(self.store, 'photo', photo_url='https://example.com/kitchen.jpg'), event(self.store, 'close')]
        before = self.store.revision()
        self.assertEqual(self.store.record_signals(events), 4)
        self.assertEqual(self.store.record_signals(events), 0)
        with ListingStore(self.path).connect() as connection:
            saved = connection.execute('SELECT * FROM implicit_signals ORDER BY rowid').fetchall()
        self.assertEqual([row['kind'] for row in saved], ['open', 'dwell', 'photo', 'close'])
        self.assertEqual(saved[1]['duration_ms'], 1500)
        self.assertEqual(saved[2]['photo_url'], 'https://example.com/kitchen.jpg')
        self.assertEqual(self.store.revision(), before)
        with self.assertRaisesRegex(ValueError, 'event_id'):
            self.store.record_signals([event(self.store), {**events[1], 'duration_ms': 2000}])
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM implicit_signals').fetchone()[0], 4)

    def test_invalid_batch_is_rejected_whole(self):
        valid = event(self.store)
        invalid = [None, {}, {**valid, 'kind': 'zoom'}, {**valid, 'listing_id': 'missing'},
                   {**valid, 'kind': []}, {**valid, 'event_id': ''}, {**valid, 'visit_id': 7},
                   {**valid, 'fingerprint': 'bad'}, {**valid, 'occurred_at': 'yesterday'},
                   {**valid, 'duration_ms': 1}, event(self.store, 'dwell', duration_ms=-1),
                   event(self.store, 'dwell', duration_ms=True), event(self.store, 'dwell', duration_ms=1.5),
                   event(self.store, 'photo', photo_url='javascript:alert(1)'),
                   event(self.store, 'photo', photo_url=7)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, KeyError)):
                self.store.record_signals([valid, value])
        for batch in (None, {}, [], [valid] * 21):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.store.record_signals(batch)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM implicit_signals').fetchone()[0], 0)

    def test_migration_preserves_existing_listing_feedback_and_ranking(self):
        path = self.path.parent / 'old.sqlite3'
        connection = sqlite3.connect(path)
        with connection:
            for migration in MIGRATIONS[:6]:
                migration(connection)
            connection.execute('PRAGMA user_version = 6')
            content = listing()
            connection.execute('INSERT INTO listings VALUES (?, ?, ?, ?, ?, ?)',
                               (content['id'], json.dumps(content), 'available', content['first_seen'], 'fingerprint', 1))
            connection.execute('INSERT INTO feedback VALUES (?, 1, 0)', (content['id'],))
            connection.execute('INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES (?, ?, 1, ?)',
                               (content['id'], 'fingerprint', 'Keep this reason.'))
        connection.close()
        store = ListingStore(path)
        saved = store.listing(content['id'])
        self.assertTrue(saved['favorite'])
        self.assertEqual((saved['rank'], saved['reason']), (1, 'Keep this reason.'))
        self.assertEqual(store.record_signals([event(store, fingerprint='a' * 64)]), 1)


class SignalHTTPTests(unittest.TestCase):
    setUp = test_gallery_server.GalleryServerTests.setUp
    close_server = test_gallery_server.GalleryServerTests.close_server
    request = test_gallery_server.GalleryServerTests.request

    def test_signal_route_authentication_validation_and_retry(self):
        body = {'events': [event(self.store), event(self.store, 'dwell', duration_ms=2500)]}
        for headers, expected in [({'Authorization': ''}, 401), ({'Origin': 'https://evil.example'}, 403)]:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/signals', body, headers)
            self.assertEqual(error.exception.code, expected)
        for invalid in ({'events': []}, {'events': [event(self.store, 'dwell', duration_ms=-1)]}, {**body, 'extra': 1}):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/signals', invalid)
            self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request('/api/signals', body), {'recorded': 2})
        self.assertEqual(self.request('/api/signals', body), {'recorded': 0})


class SignalRerankTests(StoreTestCase):
    def test_rerank_with_signals_meets_the_two_second_budget(self):
        identifiers = fill(self.store, 300, 50)
        for index, identifier in enumerate(identifiers[50:]):
            values = {'listing_id': identifier, 'visit_id': identifier}
            self.store.record_signals([event(self.store, **values),
                                      event(self.store, 'dwell', duration_ms=60000 if index < 50 else 1000, **values),
                                      event(self.store, 'close', **values)])
        started = time.perf_counter()
        result = rerank(self.store, seed=1)
        elapsed = time.perf_counter() - started
        self.assertEqual(result['implicit_signals']['weak_labels'], 250)
        self.assertLess(elapsed, 2.0)

    def test_diagnostic_weak_labels_never_change_ranks_explanations_or_questions(self):
        identifiers = fill(self.store, 24, 4)
        before = rerank(self.store, seed=7)
        ranks = self.store.browse('Rankings')['items']
        explanation = self.store.listing(identifiers[10])['explanation']
        questions = pairwise_questions(self.store, 3)
        for index, identifier in enumerate(identifiers[4:]):
            values = {'listing_id': identifier, 'visit_id': identifier}
            self.store.record_signals([event(self.store, **values),
                                      event(self.store, 'dwell', duration_ms=1000 if index % 2 else 60000, **values),
                                      event(self.store, 'close', **values)])
        after = rerank(self.store, seed=7)
        self.assertEqual(self.store.browse('Rankings')['items'], ranks)
        self.assertEqual(self.store.listing(identifiers[10])['explanation'], explanation)
        self.assertEqual(pairwise_questions(self.store, 3), questions)
        for key in ('held_out', 'weights', 'exploration', 'prior_scale', 'unlabeled_weight'):
            self.assertEqual(after[key], before[key])
        report = after['implicit_signals']
        self.assertGreater(report['weak_labels'], 0)
        self.assertEqual([row['id'] for row in report['held_out']], identifiers[:4])
        self.assertEqual(report['mean_rank_improvement'], before['held_out_mean'] - report['held_out_mean'])
        self.assertEqual(self.store.last_rerank()['implicit_signals'], report)

    def test_diagnostic_excludes_actions_stale_content_and_unfinished_visits(self):
        identifiers = fill(self.store, 8, 1)
        self.store.add_note('Explicit note', listing_id=identifiers[1])
        self.store.answer_pair(identifiers[2], identifiers[3])
        for index, identifier in enumerate(identifiers):
            values = {'listing_id': identifier, 'visit_id': identifier}
            events = [event(self.store, **values), event(self.store, 'dwell', duration_ms=1000, **values)]
            if index != 7:
                events.append(event(self.store, 'close', **values))
            self.store.record_signals(events)
        self.store.upsert_listing(listing(id=identifiers[6], title='Changed content'))
        inputs = self.store.ranking_inputs(include_signals=True)
        self.assertEqual(weak_labels(inputs), {identifiers[4]: (0, 0.1), identifiers[5]: (0, 0.1)})
        self.assertEqual(self.store.ranking_inputs()['implicit_visits'], [])

    def test_short_and_long_visits_are_weak_labels_and_held_out_favorite_cannot_leak(self):
        identifiers = fill(self.store, 5, 1)
        for identifier, duration in zip(identifiers, [999999, 1000, 5000, 6000, 60000]):
            values = {'listing_id': identifier}
            self.store.record_signals([event(self.store, **values), event(self.store, 'dwell', duration_ms=duration, **values),
                                      event(self.store, 'close', **values)])
        inputs = self.store.ranking_inputs(include_signals=True)
        self.assertEqual(weak_labels(inputs), {identifiers[1]: (0, 0.1), identifiers[4]: (1, 0.1)})
        full = fit_model(inputs, (1, 0.05), weak_labels=weak_labels(inputs))
        self.assertNotEqual(full.fit.weights, fit_model(inputs, (1, 0.05)).fit.weights)
        reused = fit_model(inputs, (1, 0.05), weak_labels=weak_labels(inputs), warm_start=fit_model(inputs, (1, 0.05)))
        self.assertEqual(full.held_out, reused.held_out)
        for cold, warm in zip(full.fit.weights, reused.fit.weights):
            self.assertAlmostEqual(cold, warm, places=5)
        without_favorite_signals = {**inputs, 'implicit_visits': [visit for visit in inputs['implicit_visits'] if visit['listing_id'] != identifiers[0]]}
        other = fit_model(without_favorite_signals, (1, 0.05), weak_labels=weak_labels(without_favorite_signals))
        self.assertEqual(full.held_out, other.held_out)

    def test_empty_signals_and_no_favorites_have_explicit_reports(self):
        fill(self.store, 4, 0)
        result = rerank(self.store, seed=1)
        self.assertEqual(result['implicit_signals'], {
            'policy': 'completed_visible_dwell', 'weak_labels': 0, 'held_out': [],
            'held_out_mean': None, 'held_out_median': None, 'mean_rank_improvement': None, 'improves': None})
