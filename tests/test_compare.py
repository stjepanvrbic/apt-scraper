import json
import sqlite3
import tempfile
import threading
import unittest
from collections.abc import Mapping
from contextlib import closing
from http.client import HTTPResponse
from pathlib import Path
from typing import cast, override
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_gallery_store import listing
from test_feature_record import extraction
from rerank import pairwise_questions, rerank
from server import CompareQuestion, Comparison, GalleryServer, make_server
from schema import MIGRATIONS, content_fingerprint
from shapes import loads
from store import ListingStore, validate_listing


def add_choices(store: ListingStore, count: int = 8) -> None:
    for number in range(count):
        identifier = f'choice:{number:02}'
        saved = store.upsert_listing(listing(id=identifier, price=1800 + number * 100, photos=[]))
        _ = store.save_extraction(identifier, saved['content_fingerprint'], extraction(0), photos=[], phrases=[])


class CompareTestCase(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store: ListingStore = ListingStore(Path(directory.name) / 'listings.sqlite3')
        add_choices(self.store)


class CompareTests(CompareTestCase):
    def test_version_five_migration_preserves_answers_and_ranks(self) -> None:
        path = self.store.path.parent / 'version-five.sqlite3'
        with closing(sqlite3.connect(path)) as connection, connection:
            for migration in MIGRATIONS[:5]:
                migration(connection)
            _ = connection.execute('PRAGMA user_version = 5')
            for rank, identifier in enumerate(('choice:00', 'choice:01'), 1):
                data = validate_listing(listing(id=identifier, photos=[]))
                fingerprint = content_fingerprint(data)
                _ = connection.execute('INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)',
                                       (identifier, json.dumps(data), 'available', data['first_seen'], fingerprint))
                _ = connection.execute('INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES (?, ?, ?, ?)',
                                       (identifier, fingerprint, rank, 'Ranked.'))
            _ = connection.execute("INSERT INTO pairwise_answers (chosen, other, answered_at) VALUES ('choice:00', 'choice:01', '2026-10-25')")
        migrated = ListingStore(path)
        self.assertEqual(migrated.schema_version, len(MIGRATIONS))
        self.assertEqual((migrated.listing('choice:00')['rank'], migrated.listing('choice:01')['rank']), (1, 2))
        self.assertEqual(migrated.pairwise_answers(), [('choice:00', 'choice:01')])
        self.assertEqual(migrated.skipped_pairs(), [])

    def test_skip_survives_restart_and_excludes_the_next_twenty_questions(self) -> None:
        pair = pairwise_questions(self.store, 1)[0]['listings']
        self.store.skip_pair(*pair)
        self.store: ListingStore = ListingStore(self.store.path)
        for _ in range(20):
            question = pairwise_questions(self.store, 1)[0]['listings']
            self.assertNotEqual(set(question), set(pair))
            self.store.skip_pair(*question)
        self.assertEqual(set(pairwise_questions(self.store, 1)[0]['listings']), set(pair))
        self.assertEqual(self.store.pairwise_answers(), [])

    def test_answer_is_idempotent_and_conflicting_answer_is_rejected(self) -> None:
        pair = pairwise_questions(self.store, 1)[0]['listings']
        self.store.answer_pair(*pair)
        revision = self.store.revision()
        self.store.answer_pair(*pair)
        self.assertEqual(self.store.revision(), revision)
        with self.assertRaisesRegex(ValueError, 'already answered'):
            self.store.answer_pair(*reversed(pair))
        self.assertEqual(self.store.pairwise_answers(), [tuple(pair)])
        self.assertNotIn(set(pair), [set(item['listings']) for item in pairwise_questions(self.store, 20)])

    def test_answers_advance_the_skip_cooldown_but_retries_do_not(self) -> None:
        pair = tuple(sorted(pairwise_questions(self.store, 1)[0]['listings']))
        self.store.skip_pair(*pair)
        for _ in range(20):
            self.assertIn(pair, self.store.skipped_pairs())
            question = pairwise_questions(self.store, 1)[0]['listings']
            self.assertNotEqual(set(question), set(pair))
            self.store.answer_pair(*question)
            self.store.answer_pair(*question)
        self.assertNotIn(pair, self.store.skipped_pairs())

    def test_ineligible_and_malformed_choices_cannot_be_saved(self) -> None:
        malformed: list[tuple[object, object]] = [('choice:00', 'choice:00'), ([], 'choice:01'), ('choice:00', None)]
        for action in (self.store.answer_pair, self.store.skip_pair):
            for first, second in malformed:
                with self.subTest(action=action.__name__, first=first), self.assertRaises(ValueError):
                    action(first, second)  # pyright: ignore[reportArgumentType]  # answer_pair and skip_pair validate the values themselves
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
    @override
    def setUp(self) -> None:
        super().setUp()
        self.server: GalleryServer = make_server(self.store, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url: str = f'http://127.0.0.1:{self.server.server_port}'

    def request(self, path: str, body: object = None) -> object:
        request = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.server.access_token})
        with cast(HTTPResponse, urlopen(request, timeout=5)) as response:
            return loads(response.read())

    def next_question(self) -> CompareQuestion:
        question = cast(Comparison, self.request('/api/compare'))['question']
        if question is None:
            self.fail('Compare has no question.')
        return question

    def rank_of(self, identifier: str) -> int:
        rank = self.store.listing(identifier)['rank']
        if rank is None:
            self.fail(f'{identifier} is not ranked.')
        return rank

    def test_http_answer_counts_at_the_next_rerank(self) -> None:
        _ = rerank(self.store, seed=1)
        first, second = self.next_question()['listings']
        chosen, other = sorted((first, second), key=lambda item: cast(int, item['rank']), reverse=True)
        before = self.rank_of(chosen['id'])
        _ = self.request('/api/compare/answer', {'chosen': chosen['id'], 'other': other['id']})
        self.assertEqual(self.rank_of(chosen['id']), before)
        _ = rerank(self.store, seed=1)
        self.assertLess(self.rank_of(chosen['id']), self.rank_of(other['id']))
        self.assertEqual(cast(Comparison, self.request('/api/compare'))['answered'], 1)

    def test_http_skip_and_empty_state(self) -> None:
        pair = [item['id'] for item in self.next_question()['listings']]
        _ = self.request('/api/compare/skip', {'first': pair[0], 'second': pair[1]})
        self.assertNotEqual(set(pair), {item['id'] for item in self.next_question()['listings']})
        for number in range(7):
            self.store.set_availability(f'choice:{number:02}', 'unavailable')
        self.assertIsNone(cast(Comparison, self.request('/api/compare'))['question'])

    def test_http_rejects_invalid_input(self) -> None:
        bodies: list[object] = [{}, {'chosen': [], 'other': 'choice:01'}, {'chosen': 'choice:00', 'other': 'choice:00'}]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(HTTPError) as error:
                _ = self.request('/api/compare/answer', body)
            self.assertEqual(error.exception.code, 400)

    def test_compare_routes_require_authentication_and_same_origin(self) -> None:
        rejected: list[tuple[Mapping[str, str], int]] = [({}, 401), ({'Authorization': 'Bearer ' + self.server.access_token, 'Origin': 'https://example.com'}, 403)]
        for path, body in [('/api/compare', None), ('/api/compare/answer', {'chosen': 'choice:00', 'other': 'choice:01'}),
                           ('/api/compare/skip', {'first': 'choice:00', 'second': 'choice:01'})]:
            for headers, status in rejected:
                request = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                  headers={'Content-Type': 'application/json', **headers})
                with self.subTest(path=path, status=status), self.assertRaises(HTTPError) as error:
                    _ = cast(HTTPResponse, urlopen(request, timeout=5))
                self.assertEqual(error.exception.code, status)
                error.exception.close()
        self.assertEqual(self.store.pairwise_answers(), [])
        self.assertEqual(self.store.skipped_pairs(), [])


if __name__ == '__main__':
    _ = unittest.main()
