import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_gallery_store import ListingStore, listing
from store import ViewingConflict
from schema import MIGRATIONS, initial_schema


class ContactStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'listings.sqlite3'
        self.store = ListingStore(self.path)
        self.store.upsert_listing(listing())
        self.store.request_contact('example:100')

    def test_version_one_store_upgrades_keeping_contacts_and_correspondence(self):
        path = self.path.with_name('version-one.sqlite3')
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                initial_schema(connection)
                connection.execute('PRAGMA user_version = 1')
                connection.execute("INSERT INTO listings VALUES ('example:100', ?, 'available', '2026-10-20', 'f', 1)",
                                   ('{"id": "example:100", "sources": []}',))
                connection.execute("INSERT INTO contacts (listing_id, status, requested_at) VALUES ('example:100', 'contacted', '2026-10-21')")
                connection.execute("INSERT INTO correspondence (listing_id, direction, channel, text, created_at, external_message_id) "
                                   "VALUES ('example:100', 'incoming', 'email', 'Hello', '2026-10-22', 'm1')")
        store = ListingStore(path)
        self.assertEqual(store.schema_version, len(MIGRATIONS))
        saved = store.listing('example:100')
        self.assertEqual((saved['contact']['status'], saved['correspondence'][0]['text']), ('contacted', 'Hello'))
        self.assertEqual(store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'reply'}])

    def test_pending_request_is_due_until_the_first_message_goes_out(self):
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'start'}])
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.set_contact('example:100', 'contacted', channel='email')
        self.assertEqual(self.store.due_contacts(), [])

    def test_unanswered_reply_is_due_until_answered_or_marked_handled(self):
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.set_contact('example:100', 'contacted', channel='email')
        self.store.correspond('example:100', 'incoming', 'email', 'Saturday?', external_message_id='m2')
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'reply'}])
        self.store.correspond('example:100', 'outgoing', 'email', 'Saturday works.', external_message_id='m3')
        self.assertEqual(self.store.due_contacts(), [])
        self.store.correspond('example:100', 'incoming', 'email', 'See you then.', external_message_id='m4')
        self.store.mark_handled('example:100')
        self.assertEqual(self.store.due_contacts(), [])

    def test_failed_and_closed_contacts_are_never_due(self):
        self.store.set_contact('example:100', 'failed', error='Gmail is not connected.')
        self.assertEqual(self.store.due_contacts(), [])
        self.store.request_contact('example:100')
        self.store.set_contact('example:100', 'closed')
        self.assertEqual(self.store.due_contacts(), [])

    def test_open_question_holds_the_listing_until_the_user_answers(self):
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.set_contact('example:100', 'contacted', channel='email')
        self.store.correspond('example:100', 'incoming', 'email', 'Your date of birth?', external_message_id='m2')
        question = self.store.ask_user('example:100', 'The agent asks for your date of birth.',
                                       field='date of birth', purpose='rental screening')
        self.assertEqual(self.store.due_contacts(), [])
        self.assertEqual([item['id'] for item in self.store.open_questions()], [question])
        self.assertEqual(self.store.open_questions('example:100')[0]['field'], 'date of birth')
        self.store.answer_question(question, 'Yes, it is 1990-05-01.', share='1990-05-01')
        self.assertEqual(self.store.open_questions(), [])
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'reply'}])
        approval = self.store.approved_information('example:100', 'rental screening')
        self.assertEqual([(item['field'], item['value']) for item in approval], [('date of birth', '1990-05-01')])

    def test_answer_without_sharing_records_no_approval(self):
        question = self.store.ask_user('example:100', 'The form asks for your phone number.',
                                       field='phone number', purpose='contact form')
        self.store.answer_question(question, 'Do not share it.')
        self.assertEqual(self.store.approved_information('example:100', 'contact form'), [])
        with self.assertRaisesRegex(ValueError, 'answered'):
            self.store.answer_question(question, 'Again.')

    def test_sharing_needs_a_question_that_names_field_and_purpose(self):
        question = self.store.ask_user('example:100', 'Is Tuesday at 18:00 fine?')
        with self.assertRaisesRegex(ValueError, 'field and purpose'):
            self.store.answer_question(question, 'Yes', share='yes')
        self.assertEqual(len(self.store.open_questions()), 1)

    def test_question_on_a_pending_request_marks_it_in_progress(self):
        self.store.ask_user('example:100', 'The form asks for your phone number.', field='phone number', purpose='contact form')
        self.assertEqual(self.store.listing('example:100')['contact']['status'], 'in_progress')

    def test_email_subject_is_kept_on_the_contact(self):
        self.store.set_contact('example:100', 'contacted', channel='email', subject='Viewing 100 Example Street')
        self.store.set_contact('example:100', 'contacted')
        self.assertEqual(self.store.listing('example:100')['contact']['subject'], 'Viewing 100 Example Street')

    def test_agreed_viewing_waits_for_a_calendar_event_then_books(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Meet in the lobby.')
        saved = self.store.listing('example:100')['viewings'][0]
        self.assertEqual((saved['status'], saved['calendar_event_id'], saved['time']),
                         ('agreed', None, '2026-10-31T16:00:00+00:00'))
        self.assertEqual(self.store.browse('Viewings')['total'], 0)
        self.assertEqual([item['id'] for item in self.store.agreed_viewings()], [viewing])
        self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Meet in the lobby.', 'event-1')
        self.assertEqual(self.store.agreed_viewings(), [])
        self.assertEqual(self.store.browse('Viewings')['total'], 1)

    def test_answers_stay_with_the_listing_until_the_next_message_or_handled(self):
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.set_contact('example:100', 'contacted', channel='email')
        self.store.correspond('example:100', 'incoming', 'email', 'Your date of birth?', external_message_id='m2')
        question = self.store.ask_user('example:100', 'Share your date of birth?', field='date of birth', purpose='screening')
        self.store.answer_question(question, 'No, do not share it.')
        self.assertEqual([item['answer'] for item in self.store.unhandled_answers('example:100')], ['No, do not share it.'])
        self.store.correspond('example:100', 'outgoing', 'email', 'I would rather not share that.', external_message_id='m3')
        self.assertEqual(self.store.unhandled_answers('example:100'), [])

    def test_an_answer_alone_makes_the_listing_due(self):
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.set_contact('example:100', 'contacted', channel='email')
        question = self.store.ask_user('example:100', 'Should I ask about parking?')
        self.assertEqual(self.store.due_contacts(), [])
        self.store.answer_question(question, 'Yes, ask about parking.')
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'answer'}])
        self.store.mark_handled('example:100')
        self.assertEqual(self.store.due_contacts(), [])

    def test_retried_contact_with_an_unanswered_reply_is_due_again(self):
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.correspond('example:100', 'incoming', 'email', 'Saturday?', external_message_id='m2')
        self.store.set_contact('example:100', 'failed', error='Gmail is not connected.')
        self.assertEqual(self.store.due_contacts(), [])
        self.store.request_contact('example:100')
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'reply'}])

    def test_a_viewing_may_not_overlap_another_agreed_or_booked_one(self):
        self.store.upsert_listing(listing(id='other:200'))
        self.store.request_contact('other:200')
        first = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        with self.assertRaisesRegex(ViewingConflict, 'example:100'):
            self.store.agree_viewing('other:200', '2026-10-31T11:30:00-05:00', 'Door')
        self.store.record_booking(first, '2026-10-31T16:00:00+00:00', 'Lobby', 'event-1')
        with self.assertRaises(ViewingConflict):
            self.store.agree_viewing('other:200', '2026-10-31T10:30:00-05:00', 'Door')
        self.store.agree_viewing('other:200', '2026-10-31T12:00:00-05:00', 'Door')
        self.store.agree_viewing('example:100', '2026-10-31T10:00:00-05:00', 'Lobby', viewing_id=first)
        self.store.cancel_viewing(first, possible_event_id='first')
        self.store.agree_viewing('other:200', '2026-10-31T11:00:00-05:00', 'Door')

    def test_outgoing_threads_name_the_listing_that_wrote_last(self):
        self.store.upsert_listing(listing(id='other:200'))
        self.store.request_contact('other:200')
        self.store.correspond('example:100', 'outgoing', 'imessage', 'Hi', external_message_id='a', external_thread_id='chat-1')
        self.store.correspond('other:200', 'outgoing', 'imessage', 'Hi', external_message_id='b', external_thread_id='chat-1')
        self.store.correspond('other:200', 'outgoing', 'email', 'Hi', external_message_id='c', external_thread_id='thread-9')
        self.assertEqual(self.store.thread_owners('imessage'), {'chat-1': 'other:200'})
        self.assertEqual(self.store.thread_owners('email'), {'thread-9': 'other:200'})

    def test_moving_a_booked_viewing_keeps_its_event_for_the_calendar_update(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Lobby', 'event-1')
        self.store.agree_viewing('example:100', '2026-11-01T12:00:00-05:00', 'Lobby', viewing_id=viewing)
        moved = self.store.agreed_viewings()[0]
        self.assertEqual((moved['calendar_event_id'], moved['time']), ('event-1', '2026-11-01T17:00:00+00:00'))
        with self.assertRaisesRegex(ValueError, 'UTC offset'):
            self.store.agree_viewing('example:100', '2026-11-01T12:00:00', 'Lobby')

    def test_a_booking_is_recorded_only_for_the_time_and_details_it_was_written_with(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        self.assertFalse(self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Side door', 'event1'))
        self.assertFalse(self.store.record_booking(viewing, '2026-10-31T17:00:00+00:00', 'Lobby', 'event1'))
        self.assertTrue(self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Lobby', 'event1'))
        saved = self.store.listing('example:100')['viewings'][0]
        self.assertEqual((saved['status'], saved['calendar_event_id']), ('booked', 'event1'))
        self.assertFalse(self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Lobby', 'event1'))
        self.assertEqual([item['id'] for item in self.store.scheduled_viewings()], [viewing])

    def test_canceled_viewing_keeps_its_event_until_the_removal_is_recorded(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        unbooked = self.store.agree_viewing('example:100', '2026-11-01T11:00:00-05:00', 'Lobby')
        self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Lobby', 'event1')
        self.assertEqual(self.store.canceled_viewing_events(), [])
        self.store.cancel_viewing(viewing, possible_event_id='derived1')
        self.store.cancel_viewing(unbooked, possible_event_id='derived2')
        self.assertEqual([(item['id'], item['calendar_event_id']) for item in self.store.canceled_viewing_events()],
                         [(viewing, 'event1'), (unbooked, 'derived2')])
        self.assertEqual(self.store.scheduled_viewings(), [])
        self.assertFalse(self.store.record_event_removed(viewing, 'other-event'))
        self.assertTrue(self.store.record_event_removed(viewing, 'event1'))
        self.assertTrue(self.store.record_event_removed(unbooked, 'derived2'))
        self.assertEqual(self.store.canceled_viewing_events(), [])
        self.store.cancel_viewing(viewing, possible_event_id='derived1')
        self.assertEqual(self.store.canceled_viewing_events(), [])
        self.assertIsNone(self.store.listing('example:100')['viewings'][0]['calendar_event_id'])
        with self.assertRaises(KeyError):
            self.store.cancel_viewing('no-such-viewing', possible_event_id='x')

    def test_viewing_canceled_while_its_event_was_written_keeps_the_event_for_removal(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        self.store.cancel_viewing(viewing, possible_event_id='derived1')
        self.assertFalse(self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Lobby', 'event1'))
        self.assertEqual([(item['id'], item['status'], item['calendar_event_id']) for item in self.store.canceled_viewing_events()],
                         [(viewing, 'canceled', 'event1')])

    def test_a_canceled_viewing_cannot_be_moved(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        self.store.cancel_viewing(viewing, possible_event_id='derived1')
        with self.assertRaisesRegex(KeyError, 'agreed or booked'):
            self.store.agree_viewing('example:100', '2026-10-31T12:00:00-05:00', 'Lobby', viewing_id=viewing)


if __name__ == '__main__':
    unittest.main()
