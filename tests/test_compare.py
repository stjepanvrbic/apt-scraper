import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_gallery_store import ListingStore, listing
from test_feature_record import extraction
from rerank import pairwise_questions, rerank
from server import make_server
from schema import MIGRATIONS


def add_choices(store, count=8):
    for number in range(count):
        identifier = f'choice:{number:02}'
        saved = store.upsert_listing(listing(id=identifier, price=1800 + number * 100, photos=[]))
        store.save_extraction(identifier, saved['content_fingerprint'], extraction(0), photos=[], phrases=[])


class CompareTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ListingStore(Path(directory.name) / 'listings.sqlite3')
        add_choices(self.store)


class CompareTests(CompareTestCase):
    def test_version_five_migration_preserves_answers_and_ranks(self):
        path = self.store.path.parent / 'version-five.sqlite3'
        with closing(sqlite3.connect(path)) as connection:
            for migration in MIGRATIONS[:5]:
                migration(connection)
            connection.execute('PRAGMA user_version = 5')
            connection.commit()
        old = ListingStore.__new__(ListingStore)
        old.path = path
        add_choices(old, 2)
        rerank(old, seed=1)
        before = old.listing('choice:00')['rank']
        with old.connect(write=True) as connection:
            connection.execute("INSERT INTO pairwise_answers (chosen, other, answered_at) VALUES ('choice:00', 'choice:01', '2026-10-25')")
        migrated = ListingStore(path)
        self.assertEqual(migrated.listing('choice:00')['rank'], before)
        self.assertEqual(migrated.pairwise_answers(), [('choice:00', 'choice:01')])
        self.assertEqual(migrated.skipped_pairs(), [])

    def test_skip_survives_restart_and_excludes_the_next_twenty_questions(self):
        pair = pairwise_questions(self.store, 1)[0]['listings']
        self.store.skip_pair(*pair)
        self.store = ListingStore(self.store.path)
        for _ in range(20):
            question = pairwise_questions(self.store, 1)[0]['listings']
            self.assertNotEqual(set(question), set(pair))
            self.store.skip_pair(*question)
        self.assertEqual(set(pairwise_questions(self.store, 1)[0]['listings']), set(pair))
        self.assertEqual(self.store.pairwise_answers(), [])

    def test_answer_is_idempotent_and_conflicting_answer_is_rejected(self):
        pair = pairwise_questions(self.store, 1)[0]['listings']
        self.store.answer_pair(*pair)
        revision = self.store.revision()
        self.store.answer_pair(*pair)
        self.assertEqual(self.store.revision(), revision)
        with self.assertRaisesRegex(ValueError, 'already answered'):
            self.store.answer_pair(*reversed(pair))
        self.assertEqual(self.store.pairwise_answers(), [tuple(pair)])
        self.assertNotIn(set(pair), [set(item['listings']) for item in pairwise_questions(self.store, 20)])

    def test_ineligible_and_malformed_choices_cannot_be_saved(self):
        for action in (self.store.answer_pair, self.store.skip_pair):
            for first, second in [('choice:00', 'choice:00'), ([], 'choice:01'), ('choice:00', None)]:
                with self.subTest(action=action.__name__, first=first), self.assertRaises(ValueError):
                    action(first, second)
        self.store.set_availability('choice:00', 'unavailable')
        self.store.feedback('choice:01', dismissed=True)
        self.store.set_matches_criteria('choice:02', False)
        for identifier in ('choice:00', 'choice:01', 'choice:02'):
            for action in (self.store.answer_pair, self.store.skip_pair):
                with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                    action(identifier, 'choice:03')
        self.assertFalse({'choice:00', 'choice:01', 'choice:02'} & {
            identifier for question in pairwise_questions(self.store, 20) for identifier in question['listings']})


class CompareHttpTests(CompareTestCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.store, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path, body=None):
        request = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.server.access_token})
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_http_answer_counts_at_the_next_rerank(self):
        rerank(self.store, seed=1)
        question = self.request('/api/compare')['question']
        first, second = question['listings']
        chosen, other = sorted((first, second), key=lambda item: item['rank'], reverse=True)
        before = self.store.listing(chosen['id'])['rank']
        self.request('/api/compare/answer', {'chosen': chosen['id'], 'other': other['id']})
        self.assertEqual(self.store.listing(chosen['id'])['rank'], before)
        rerank(self.store, seed=1)
        self.assertLess(self.store.listing(chosen['id'])['rank'], self.store.listing(other['id'])['rank'])
        self.assertEqual(self.request('/api/compare')['answered'], 1)

    def test_http_skip_and_empty_state(self):
        pair = [item['id'] for item in self.request('/api/compare')['question']['listings']]
        self.request('/api/compare/skip', {'first': pair[0], 'second': pair[1]})
        self.assertNotEqual(set(pair), {item['id'] for item in self.request('/api/compare')['question']['listings']})
        for number in range(7):
            self.store.set_availability(f'choice:{number:02}', 'unavailable')
        self.assertIsNone(self.request('/api/compare')['question'])

    def test_http_rejects_invalid_input(self):
        for body in ({}, {'chosen': [], 'other': 'choice:01'}, {'chosen': 'choice:00', 'other': 'choice:00'}):
            with self.subTest(body=body), self.assertRaises(HTTPError) as error:
                self.request('/api/compare/answer', body)
            self.assertEqual(error.exception.code, 400)

    def test_compare_routes_require_authentication_and_same_origin(self):
        for path, body in [('/api/compare', None), ('/api/compare/answer', {'chosen': 'choice:00', 'other': 'choice:01'}),
                           ('/api/compare/skip', {'first': 'choice:00', 'second': 'choice:01'})]:
            for headers, status in [({}, 401), ({'Authorization': 'Bearer ' + self.server.access_token, 'Origin': 'https://example.com'}, 403)]:
                request = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                  headers={'Content-Type': 'application/json', **headers})
                with self.subTest(path=path, status=status), self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, status)
                error.exception.close()
        self.assertEqual(self.store.pairwise_answers(), [])
        self.assertEqual(self.store.skipped_pairs(), [])


if __name__ == '__main__':
    unittest.main()
