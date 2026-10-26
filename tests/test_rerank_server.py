import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from unittest.mock import patch

from test_gallery_server import GalleryServerCase
from test_rerank import SETTINGS, fill
from rerank import rerank, save_review
from store import READ_ATTEMPTS, extraction_read
from test_gallery_store import listing
from test_feature_record import extraction


class RerankServerTests(GalleryServerCase):
    def test_rerank_returns_the_check_and_waiting_review_and_persists_the_order(self):
        fill(self.store, 30, 5)
        self.assertEqual(self.request('/api/rerank'), {'running': False, 'last_rerank': None})
        summary = self.request('/api/rerank', {})
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (30, 5))
        self.assertEqual(summary['review']['state'], 'waiting')
        self.assertEqual(self.request('/api/rerank')['last_rerank'], self.store.last_rerank())
        items = self.request('/api/listings?page_size=100')['items']
        self.assertEqual([item['rank'] for item in items[:30]], list(range(1, 31)))
        self.assertEqual(sum(item['exploration'] for item in items[:20]), 2)
        details = self.request('/api/listings/' + items[0]['id'])
        self.assertIsNotNone(details['explanation']['similar_favorite'])
        self.assertAlmostEqual(sum(part['contribution'] for part in details['explanation']['contributions']), details['explanation']['score'])
        self.store.hand_review(summary['id'])
        self.assertEqual(self.request('/api/rerank')['last_rerank']['review']['state'], 'running')
        result = save_review(self.store, {'rerank': summary['id'], 'reasons': [], 'moves': [], 'flags': [], 'findings': ['Check the feature coverage.']})
        review = self.request('/api/rerank')['last_rerank']['review']
        self.assertEqual(review['state'], 'done')
        self.assertEqual(review['result'], result)
        self.assertEqual(result['findings'], ['Check the feature coverage.'])
        self.assertIn('before', result['check'])
        self.assertIn('after', result['check'])

    def test_rerank_requires_authentication_same_origin_and_an_empty_json_object(self):
        padded = b'{}'.ljust(99000)
        for content, headers, status in [(b'', {'Authorization': ''}, '401 Unauthorized'),
                                         (b'{}', {'Authorization': ''}, '401 Unauthorized'),
                                         (padded, {'Authorization': ''}, '401 Unauthorized'),
                                         (padded, {'Origin': 'https://evil.example'}, '403 Forbidden'),
                                         (padded, {'Content-Type': 'text/plain'}, '400 Bad Request'),
                                         (b'{}'.ljust(200000), {}, '400 Bad Request'),
                                         (b'{"seed": 1}', {}, '400 Bad Request')]:
            with self.subTest(length=len(content), headers=headers):
                for _ in range(200):
                    status_line, reply = self.raw_post('/api/rerank', content, headers)
                    self.assertEqual(status_line, 'HTTP/1.0 ' + status)
                    self.assertEqual(list(reply), ['error'])
        self.assertIsNone(self.store.last_rerank())

    def test_polling_preserves_reverted_moves_flags_and_both_check_values_without_starting_work(self):
        self.store.update_settings(preferences=SETTINGS['preferences'])
        identifiers = fill(self.store, 30, 5)
        summary = self.request('/api/rerank', {})
        ranked = self.store.ranked_listings()
        favorite = min(identifiers[:5], key=lambda identifier: ranked[identifier]['rank'])
        result = save_review(self.store, {'rerank': summary['id'], 'reasons': [],
            'moves': [{'id': favorite, 'to_rank': 30, 'reason': 'The record leaves light uncertain.', 'confidence': 0.7}],
            'flags': [{'id': favorite, 'kind': 'like', 'preference': 'Lots of daylight', 'note': 'Check the light.'}],
            'findings': ['Some records leave the light unknown.']})
        self.assertEqual(result['outcome'], 'reverted')
        self.assertGreater(result['check']['after']['mean'], result['check']['before']['mean'])
        revision = self.store.revision()
        for _ in range(2):
            self.assertEqual(self.request('/api/rerank')['last_rerank']['review']['result'], result)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(self.request('/api/listings/' + favorite)['rank'], ranked[favorite]['rank'])
        next_run = self.request('/api/rerank', {})
        self.store.fail_review(next_run['id'], 'The review could not be saved.')
        self.assertEqual(self.request('/api/rerank')['last_rerank']['review']['result'], {'error': 'The review could not be saved.'})

    def test_a_second_request_is_rejected_while_scoring_and_failure_releases_the_button(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(store):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the scorer.')
            return rerank(store)

        with patch('server.rerank', side_effect=blocked), ThreadPoolExecutor() as executor:
            first = executor.submit(self.request, '/api/rerank', {})
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.request('/api/rerank')['running'])
                with self.assertRaises(HTTPError) as raised:
                    self.request('/api/rerank', {})
                self.assertEqual(raised.exception.code, 409)
            finally:
                release.set()
            first.result()
        self.assertFalse(self.request('/api/rerank')['running'])
        with patch('server.rerank', side_effect=sqlite3.OperationalError('locked')):
            with self.assertRaises(HTTPError) as raised:
                self.request('/api/rerank', {})
            self.assertEqual(raised.exception.code, 500)
            self.assertIn('error', json.load(raised.exception))
        self.assertFalse(self.request('/api/rerank')['running'])
        self.assertEqual(self.request('/api/rerank', {})['review']['state'], 'waiting')

    def test_http_rerank_and_order_read_fit_the_two_second_budget(self):
        fill(self.store, 300, 50)
        started = time.perf_counter()
        summary = self.request('/api/rerank', {})
        items = self.request('/api/listings')['items']
        elapsed = time.perf_counter() - started
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (300, 50))
        self.assertEqual(items[0]['rank'], 1)
        self.assertLess(elapsed, 2.0)

    def test_pending_reasons_follow_current_features_and_never_expose_an_old_explanation(self):
        identifiers = fill(self.store, 25, 3)
        self.request('/api/rerank', {})
        changed = self.store.upsert_listing(listing(id=identifiers[0], title='Changed content', photos=[]))
        pending = self.request('/api/listings/' + identifiers[0])
        self.assertIsNone(pending['rank'])
        self.assertIsNone(pending['explanation'])
        self.assertFalse(pending['exploration'])
        self.assertIn('waiting for its feature record', pending['reason'])
        self.store.save_extraction(identifiers[0], changed['content_fingerprint'], extraction(0), photos=[], phrases=[])
        self.assertIn('new or changed since the last rerank', self.request('/api/listings/' + identifiers[0])['reason'])
        failed = self.store.upsert_listing(listing(id='example:failed'))
        for _ in range(READ_ATTEMPTS):
            self.store.record_read_attempts([extraction_read('example:failed', failed['content_fingerprint'])], [])
        items = self.request('/api/listings?page_size=100')['items']
        self.assertTrue(all(item['rank'] for item in items[:24]))
        reasons = {item['id']: item['reason'] for item in items[24:]}
        self.assertIn('new or changed since the last rerank', reasons[identifiers[0]])
        self.assertIn('extraction failed 3 times', reasons['example:failed'])
        self.assertIn('waiting for its feature record', reasons['example:100'])

    def assert_excluded_reason(self, identifier, view, reason):
        for _ in range(2):
            self.request('/api/rerank', {})
            self.assertNotIn(identifier, [item['id'] for item in self.request('/api/listings')['items']])
            detail = self.request('/api/listings/' + identifier)
            card = next(item for item in self.request('/api/listings?view=' + view)['items'] if item['id'] == identifier)
            for item in (detail, card):
                self.assertIsNone(item['rank'])
                self.assertEqual(item['reason'], reason)
                self.assertFalse(item['exploration'])
            self.assertIsNone(detail['explanation'])

    def test_unavailable_favorite_explains_why_reranking_cannot_rank_it(self):
        identifier = fill(self.store, 2, 1)[0]
        self.request('/api/rerank', {})
        self.store.set_availability(identifier, 'unavailable')
        self.assert_excluded_reason(identifier, 'Favorites', 'Not ranked: unavailable.')

    def test_dismissed_listing_explains_why_reranking_cannot_rank_it(self):
        identifier = fill(self.store, 2, 1)[0]
        self.request('/api/rerank', {})
        self.store.feedback(identifier, dismissed=True)
        self.assert_excluded_reason(identifier, 'Dismissed', 'Not ranked: dismissed.')

    def test_contacted_listing_outside_criteria_explains_why_reranking_cannot_rank_it(self):
        identifier = fill(self.store, 2, 1)[0]
        self.request('/api/rerank', {})
        self.store.request_contact(identifier)
        self.store.set_matches_criteria(identifier, False)
        self.assert_excluded_reason(identifier, 'Contacted', 'Not ranked: outside the criteria.')

    def test_rankable_listing_with_features_waits_for_a_rerank_then_gets_ranked(self):
        identifier = fill(self.store, 1, 0)[0]
        for item in (self.request('/api/listings/' + identifier),
                     next(item for item in self.request('/api/listings')['items'] if item['id'] == identifier)):
            self.assertIsNone(item['rank'])
            self.assertEqual(item['reason'], 'Not ranked yet: new or changed since the last rerank.')
        self.request('/api/rerank', {})
        self.assertEqual(self.request('/api/listings/' + identifier)['rank'], 1)
