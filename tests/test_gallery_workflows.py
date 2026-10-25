import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_gallery_store import ListingStore, listing


class SearchRankingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'listings.sqlite3'
        self.store = ListingStore(self.path)

    def test_search_and_ranking_preserve_each_others_data_and_detect_changes(self):
        result = self.store.upsert_listing(listing())
        self.assertTrue(result['changed'])
        self.assertTrue(result['needs_analysis'])
        fingerprint = result['content_fingerprint']
        self.assertIsNone(self.store.listing('example:100')['rank'])
        self.store.analyze('example:100', fingerprint, {'light': 0.9, 'quiet': 0.7})
        self.store.rank('example:100', fingerprint, 2, 'Bright and quiet.')
        self.assertFalse(self.store.upsert_listing(listing())['needs_analysis'])
        self.assertFalse(self.store.upsert_listing(listing())['changed'])
        self.assertEqual(self.store.listing('example:100')['rank'], 2)
        self.assertEqual(self.store.features('example:100'), {'light': 0.9, 'quiet': 0.7})
        self.store.set_availability('example:100', 'unavailable')
        self.assertEqual(self.store.listing('example:100')['status'], 'unavailable')
        self.assertEqual(self.store.features('example:100'), {'light': 0.9, 'quiet': 0.7})
        self.store.set_availability('example:100', 'available')
        self.store.rank('example:100', fingerprint, 1, 'Best fit after feedback.')
        changed = self.store.upsert_listing(listing(price=1900, first_seen='2026-10-24'))
        self.assertTrue(changed['needs_analysis'])
        saved = self.store.listing('example:100')
        self.assertEqual(saved['first_seen'], '2026-10-20')
        self.assertEqual(saved['rank'], 1)
        self.assertFalse(saved['analysis_current'])
        self.assertIsNone(self.store.features('example:100'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.analyze('example:100', fingerprint, {'quiet': 1})
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.rank('example:100', fingerprint, 1, 'Stale result.')

    def test_interview_settings_are_independent_and_persist(self):
        self.store.update_settings(criteria={'maximum_price': 2500}, preferences={'likes': ['quiet']},
                                   listing_sites=['https://example.com'], viewing_availability={'timezone': 'America/Chicago', 'windows': []})
        self.store.update_settings(criteria={'maximum_price': 2200})
        saved = type(self.store)(self.path).settings()
        self.assertEqual(saved['criteria']['maximum_price'], 2200)
        self.assertEqual(saved['preferences'], {'likes': ['quiet']})
        self.assertEqual(saved['listing_sites'], ['https://example.com'])
        self.assertEqual(saved['viewing_availability']['timezone'], 'America/Chicago')

    def test_contact_channel_agent_and_approval_are_scoped_to_request(self):
        self.store.upsert_listing(listing())
        self.store.upsert_listing(listing(id='other:200'))
        self.store.request_contact('example:100')
        self.store.set_contact('example:100', 'in_progress', channel='email', agent={'email': 'office@example.com'})
        approval = self.store.approve_information('example:100', 'name', 'Test Person', 'arrange viewing')
        self.assertEqual(self.store.approved_information('example:100', 'arrange viewing')[0]['value'], 'Test Person')
        self.assertEqual(self.store.approved_information('other:200', 'arrange viewing'), [])
        self.assertEqual(self.store.approved_information('example:100', 'application'), [])
        self.store.revoke_approval(approval)
        self.assertEqual(self.store.approved_information('example:100', 'arrange viewing'), [])
        self.store.set_contact('example:100', 'failed', channel='email', error='Mailbox unavailable')
        saved = type(self.store)(self.path).listing('example:100')['contact']
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['channel'], 'email')
        self.assertEqual(saved['agent']['email'], 'office@example.com')
        self.assertEqual(saved['error'], 'Mailbox unavailable')

    def test_multiple_viewings_move_cancel_and_remove_without_losing_event_identity(self):
        self.store.upsert_listing(listing())
        first = self.store.book_viewing('example:100', '2026-10-30T15:00:00-05:00', 'Lobby', 'calendar-first')
        second = self.store.book_viewing('example:100', '2026-11-01T10:00:00-06:00', 'Second visit', 'calendar-second')
        self.store.book_viewing('example:100', '2026-10-31T15:00:00-05:00', 'Moved', 'calendar-first', viewing_id=first)
        self.store.cancel_viewing(first)
        saved = type(self.store)(self.path).listing('example:100')['viewings']
        self.assertEqual(len(saved), 2)
        moved = next(viewing for viewing in saved if viewing['id'] == first)
        self.assertEqual(moved['calendar_event_id'], 'calendar-first')
        self.assertEqual(moved['status'], 'canceled')
        self.assertEqual(moved['time'], '2026-10-31T20:00:00+00:00')
        self.store.remove_viewing(second)
        self.assertEqual(len(self.store.listing('example:100')['viewings']), 1)

    def test_drawer_and_export_order_viewings_by_time_not_identifier(self):
        self.store.upsert_listing(listing())
        with patch('store.uuid4', side_effect=['first-id', 'second-id']):
            late = self.store.book_viewing('example:100', '2026-10-28T10:00:00Z', '', 'event-late')
            early = self.store.book_viewing('example:100', '2026-10-26T10:00:00Z', '', 'event-early')
        self.assertEqual([row['id'] for row in self.store.listing('example:100')['viewings']], [early, late])
        self.assertEqual([row['id'] for row in self.store.state()['listings'][0]['viewings']], [early, late])

    def test_chat_persists_replies_and_rejects_overlapping_turns(self):
        message = self.store.start_chat('Prefer a quiet street.', 'request-1')
        with self.assertRaisesRegex(ValueError, 'already'):
            self.store.start_chat('Another message', 'request-2')
        self.store.update_chat(message, content='I will prioritize quiet streets.', status='complete', session_id='session-one')
        saved = type(self.store)(self.path).chat_state()
        self.assertEqual([row['role'] for row in saved['messages']], ['user', 'agent'])
        self.assertEqual(saved['messages'][1]['content'], 'I will prioritize quiet streets.')
        self.assertEqual(saved['session_id'], 'session-one')

    def test_stream_updates_preserve_question_and_error_until_explicitly_cleared(self):
        identity = self.store.start_chat('Question', 'preserve-question')
        prompt = {'prompt_id': 'approval', 'kind': 'approval', 'choices': ['once', 'deny']}
        self.store.update_chat(identity, prompt=prompt, error='Earlier warning')
        self.store.update_chat(identity, content='Partial text', events=[])
        message = self.store.chat_state()['messages'][-1]
        self.assertEqual(message['prompt'], prompt)
        self.assertEqual(message['error'], 'Earlier warning')
        self.store.update_chat(identity, prompt=None, error=None)
        message = self.store.chat_state()['messages'][-1]
        self.assertIsNone(message['prompt'])
        self.assertIsNone(message['error'])

    def test_correspondence_deduplicates_external_ids_without_changing_contact_state(self):
        self.store.upsert_listing(listing())
        with self.assertRaises(KeyError):
            self.store.correspond('example:100', 'incoming', 'email', 'Hello', external_message_id='m-1')
        self.store.request_contact('example:100')
        self.store.set_contact('example:100', 'failed', channel='email', error='Delivery failed')
        for _ in range(2):
            self.store.correspond('example:100', 'incoming', 'email', 'Hello', external_message_id='m-1', external_thread_id='t-1')
        saved = self.store.listing('example:100')
        self.assertEqual(len(saved['correspondence']), 1)
        self.assertEqual(saved['correspondence'][0]['external_thread_id'], 't-1')
        self.assertEqual(saved['contact']['status'], 'failed')
        self.assertEqual(saved['contact']['error'], 'Delivery failed')
        with self.assertRaises(ValueError):
            self.store.correspond('example:100', 'incoming', 'invalid', 'Hello', external_message_id='m-2')
        self.store.request_contact('example:100')
        self.assertEqual(self.store.listing('example:100')['contact']['status'], 'pending')
