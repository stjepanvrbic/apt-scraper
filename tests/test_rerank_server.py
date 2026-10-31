import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from urllib.error import HTTPError
from unittest.mock import patch

from test_gallery_server import GalleryServerCase
from test_rerank import SETTINGS, cpu_budget, fill, speed_factor
from queries import BrowseItem, Page
from rerank import RerankReport, rerank, save_review
from review import Rerank, ReviewState
from server import RerankStatus
from shapes import loads
from store import READ_ATTEMPTS, ListingDetail, ListingStore, extraction_read
from test_gallery_store import listing
from test_feature_record import extraction


class RerankServerTests(GalleryServerCase):
    def last_rerank(self) -> Rerank | None:
        return cast(RerankStatus, self.request('/api/rerank'))['last_rerank']

    def review_of(self, rerank: Rerank | None) -> ReviewState:
        review = rerank['review'] if rerank else None
        if review is None:
            self.fail('The rerank has no review.')
        return review

    def unranked_reason(self, item: BrowseItem | ListingDetail) -> str:
        reason = item.get('unranked_reason')
        if reason is None:
            self.fail(f'{item["id"]} is ranked.')
        return reason

    def test_rerank_returns_the_check_and_waiting_review_and_persists_the_order(self) -> None:
        _ = fill(self.store, 30, 5)
        self.assertEqual(self.request('/api/rerank'), {'running': False, 'last_rerank': None, 'hypotheses': []})
        summary = cast(RerankReport, self.request('/api/rerank', {}))
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (30, 5))
        self.assertEqual(self.review_of(summary)['state'], 'waiting')
        self.assertEqual(self.last_rerank(), self.store.last_rerank())
        items = cast(Page, self.request('/api/listings?page_size=100'))['items']
        self.assertEqual([item['rank'] for item in items[:30]], list(range(1, 31)))
        self.assertEqual(sum(item['exploration'] for item in items[:20]), 2)
        details = cast(ListingDetail, self.request('/api/listings/' + items[0]['id']))
        explanation = details['explanation']
        if explanation is None:
            self.fail('The first listing has no explanation.')
        self.assertIsNotNone(explanation['similar_favorite'])
        self.assertAlmostEqual(sum(part['contribution'] for part in explanation['contributions']), explanation['score'])
        _ = self.store.hand_review(summary['id'])
        self.assertEqual(self.review_of(self.last_rerank())['state'], 'running')
        result = save_review(self.store, {'rerank': summary['id'], 'reasons': [], 'moves': [], 'flags': [], 'findings': ['[rating:natural_light] weighs too little.']})
        review = self.review_of(self.last_rerank())
        self.assertEqual(review['state'], 'done')
        self.assertEqual(review['result'], result)
        self.assertEqual(result['findings'], ['rating:natural_light weighs too little.'])
        self.assertIn('before', result['check'])
        self.assertIn('after', result['check'])

    def test_rerank_requires_authentication_same_origin_and_an_empty_json_object(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            _ = self.request('/api/rerank', headers={'Authorization': ''})
        self.assertEqual(raised.exception.code, 401)
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
                    self.assertEqual(list(cast(dict[str, str], reply)), ['error'])
        self.assertIsNone(self.store.last_rerank())

    def test_polling_preserves_reverted_moves_flags_and_both_check_values_without_starting_work(self) -> None:
        _ = self.store.update_settings(preferences=SETTINGS.get('preferences'))
        identifiers = fill(self.store, 30, 5)
        summary = cast(RerankReport, self.request('/api/rerank', {}))
        ranked = self.store.ranked_listings()
        favorite = min(identifiers[:5], key=lambda identifier: ranked[identifier]['rank'])
        result = save_review(self.store, {'rerank': summary['id'], 'reasons': [],
            'moves': [{'id': favorite, 'to_rank': 30, 'reason': 'The model weighs [rating:natural_light] too much.', 'confidence': 0.7}],
            'flags': [{'id': favorite, 'kind': 'like', 'preference': 'Lots of daylight'}],
            'findings': ['[price] weighs too little.']})
        self.assertEqual(result['outcome'], 'reverted')
        after, before = result['check']['after']['mean'], result['check']['before']['mean']
        if after is None or before is None:
            self.fail('The check has no held-out mean.')
        self.assertGreater(after, before)
        revision = self.store.revision()
        for _ in range(2):
            self.assertEqual(self.review_of(self.last_rerank())['result'], result)
        self.assertEqual(self.store.revision(), revision)
        self.assertEqual(cast(ListingDetail, self.request('/api/listings/' + favorite))['rank'], ranked[favorite]['rank'])
        next_run = cast(RerankReport, self.request('/api/rerank', {}))
        self.store.fail_review(next_run['id'], 'The review could not be saved.')
        self.assertEqual(self.review_of(self.last_rerank())['result'], {'error': 'The review could not be saved.'})

    def test_a_second_request_is_rejected_while_scoring_and_failure_releases_the_button(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def blocked(store: ListingStore) -> RerankReport:
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the scorer.')
            return rerank(store)

        with patch('server.rerank', side_effect=blocked), ThreadPoolExecutor() as executor:
            first = executor.submit(self.request, '/api/rerank', {})
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(cast(RerankStatus, self.request('/api/rerank'))['running'])
                with self.assertRaises(HTTPError) as raised:
                    _ = self.request('/api/rerank', {})
                self.assertEqual(raised.exception.code, 409)
            finally:
                release.set()
            _ = first.result()
        self.assertFalse(cast(RerankStatus, self.request('/api/rerank'))['running'])
        with patch('server.rerank', side_effect=sqlite3.OperationalError('locked')):
            with self.assertRaises(HTTPError) as raised:
                _ = self.request('/api/rerank', {})
            self.assertEqual(raised.exception.code, 500)
            self.assertIn('error', cast(dict[str, str], loads(raised.exception.read())))
        self.assertFalse(cast(RerankStatus, self.request('/api/rerank'))['running'])
        self.assertEqual(self.review_of(cast(RerankReport, self.request('/api/rerank', {})))['state'], 'waiting')

    def test_http_rerank_and_order_read_fit_the_two_second_budget(self) -> None:
        _ = fill(self.store, 300, 50)
        budget = cpu_budget()
        started = time.perf_counter()
        # The budget is CPU time; a wall-clock timeout with a margin lets a slow run fail on the assertion, which
        # reports the measured values, instead of on the socket.
        summary = cast(RerankReport, self.request('/api/rerank', {}, timeout=max(10, 3 * budget)))
        items = cast(Page, self.request('/api/listings'))['items']
        elapsed = time.perf_counter() - started
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (300, 50))
        self.assertEqual(items[0]['rank'], 1)
        self.assertLess(elapsed, budget, speed_factor())

    def test_pending_reasons_follow_current_features_and_never_expose_an_old_explanation(self) -> None:
        identifiers = fill(self.store, 25, 3)
        _ = self.request('/api/rerank', {})
        changed = self.store.upsert_listing(listing(id=identifiers[0], title='Changed content', photos=[]))
        pending = cast(ListingDetail, self.request('/api/listings/' + identifiers[0]))
        self.assertIsNone(pending['rank'])
        self.assertIsNone(pending['explanation'])
        self.assertFalse(pending['exploration'])
        self.assertIsNone(pending['reason'])
        self.assertIn('waiting for its feature record', self.unranked_reason(pending))
        _ = self.store.save_extraction(identifiers[0], changed['content_fingerprint'], extraction(0), photos=[], phrases=[])
        self.assertIn('new or changed since the last rerank', self.unranked_reason(cast(ListingDetail, self.request('/api/listings/' + identifiers[0]))))
        failed = self.store.upsert_listing(listing(id='example:failed'))
        for _ in range(READ_ATTEMPTS):
            self.store.record_read_attempts([extraction_read('example:failed', failed['content_fingerprint'])], [])
        items = cast(Page, self.request('/api/listings?page_size=100'))['items']
        self.assertTrue(all(item['rank'] and item['reason'] and item.get('unranked_reason') is None for item in items[:24]))
        self.assertTrue(all(item['reason'] is None for item in items[24:]))
        reasons = {item['id']: self.unranked_reason(item) for item in items[24:]}
        self.assertIn('new or changed since the last rerank', reasons[identifiers[0]])
        self.assertIn('extraction failed 3 times', reasons['example:failed'])
        self.assertIn('waiting for its feature record', reasons['example:100'])

    def assert_excluded_reason(self, identifier: str, view: str, reason: str) -> None:
        for _ in range(2):
            _ = self.request('/api/rerank', {})
            self.assertNotIn(identifier, [item['id'] for item in cast(Page, self.request('/api/listings'))['items']])
            detail = cast(ListingDetail, self.request('/api/listings/' + identifier))
            card = next(item for item in cast(Page, self.request('/api/listings?view=' + view))['items'] if item['id'] == identifier)
            for item in (detail, card):
                self.assertIsNone(item['rank'])
                self.assertEqual((item['reason'], item.get('unranked_reason')), (None, reason))
                self.assertFalse(item['exploration'])
            self.assertIsNone(detail['explanation'])

    def test_unavailable_favorite_explains_why_reranking_cannot_rank_it(self) -> None:
        identifier = fill(self.store, 2, 1)[0]
        _ = self.request('/api/rerank', {})
        self.store.set_availability(identifier, 'unavailable')
        self.assert_excluded_reason(identifier, 'Favorites', 'Not ranked: unavailable.')

    def test_dismissed_listing_explains_why_reranking_cannot_rank_it(self) -> None:
        identifier = fill(self.store, 2, 1)[0]
        _ = self.request('/api/rerank', {})
        self.store.feedback(identifier, dismissed=True)
        self.assert_excluded_reason(identifier, 'Dismissed', 'Not ranked: dismissed.')

    def test_contacted_listing_outside_criteria_explains_why_reranking_cannot_rank_it(self) -> None:
        identifier = fill(self.store, 2, 1)[0]
        _ = self.request('/api/rerank', {})
        self.store.request_contact(identifier)
        self.store.set_matches_criteria(identifier, False)
        self.assert_excluded_reason(identifier, 'Contacted', 'Not ranked: outside the criteria.')

    def test_rankable_listing_with_features_waits_for_a_rerank_then_gets_ranked(self) -> None:
        identifier = fill(self.store, 1, 0)[0]
        for item in (cast(ListingDetail, self.request('/api/listings/' + identifier)),
                     next(item for item in cast(Page, self.request('/api/listings'))['items'] if item['id'] == identifier)):
            self.assertIsNone(item['rank'])
            self.assertEqual((item['reason'], item.get('unranked_reason')), (None, 'Not ranked yet: new or changed since the last rerank.'))
        _ = self.request('/api/rerank', {})
        self.assertEqual(cast(ListingDetail, self.request('/api/listings/' + identifier))['rank'], 1)
