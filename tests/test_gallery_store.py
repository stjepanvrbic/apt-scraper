import sys
import tempfile
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import override

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/gallery/scripts'))

from store import ListingStore, viewing_time


def listing(**changes: object) -> dict[str, object]:
    return {
        'id': 'example:100',
        'title': 'Courtyard apartment',
        'price': 2100,
        'currency': 'USD',
        'rental_period': 'month',
        'agent': {'name': 'Example Agent', 'email': 'agent@example.com'},
        'size': '850 sq ft',
        'rooms': '2 bedrooms, 1 bathroom',
        'address': '100 Example Street, Chicago',
        'description': 'Bright rooms facing a shared courtyard.',
        'amenities': ['Laundry', 'Balcony'],
        'photos': ['https://example.com/living.jpg', 'https://example.com/kitchen.jpg'],
        'sources': [{'label': 'Example Homes', 'url': 'https://example.com/listings/100'}],
        'first_seen': '2026-10-20',
        'status': 'available',
        **changes,
    }


def book(store: ListingStore, listing_id: str, time: str, details: str, calendar_event_id: str) -> str:
    viewing = store.agree_viewing(listing_id, time, details)
    _ = store.record_booking(viewing, viewing_time(time), details, calendar_event_id)
    return viewing


def present[T](value: T | None) -> T:
    """The value, which the test expects to be there."""
    if value is None:
        raise AssertionError('Expected a value, got None.')
    return value


class ListingStoreTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.directory: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path: Path = Path(self.directory.name) / 'listings.sqlite3'
        self.store: ListingStore = ListingStore(self.path)

    def test_imported_listing_survives_reopening_with_all_details(self) -> None:
        _ = self.store.upsert_listing(listing())
        saved: Mapping[str, object] = ListingStore(self.path).state()['listings'][0]
        for field, value in listing().items():
            self.assertEqual(saved[field], value)

    def test_partial_listing_and_price_on_request_are_preserved(self) -> None:
        _ = self.store.upsert_listing({'id': 'partial', 'sources': listing()['sources'], 'price': 'Price on request'})
        saved = self.store.listing('partial')
        self.assertIsNone(saved['price'])
        self.assertEqual(saved['price_note'], 'Price on request')
        self.assertIsNone(saved['size'])
        self.assertIsNone(saved['rooms'])
        self.assertEqual(saved['photos'], [])
        _ = self.store.upsert_listing(listing())
        self.assertEqual(len(self.store.state()['listings']), 2)

    def test_feedback_and_notes_survive_refresh_and_restart(self) -> None:
        _ = self.store.upsert_listing(listing())
        self.store.feedback('example:100', favorite=True)
        self.store.add_note('Quiet, but the bedroom is small.', listing_id='example:100')
        _ = self.store.upsert_listing(listing(price=2050))
        saved = ListingStore(self.path).state()['listings'][0]
        self.assertTrue(saved['favorite'])
        self.assertEqual(saved['price'], 2050)
        self.assertEqual(saved['notes'][0]['text'], 'Quiet, but the bedroom is small.')
        self.store.feedback('example:100', favorite=False)
        self.assertFalse(ListingStore(self.path).state()['listings'][0]['favorite'])
        self.store.feedback('example:100', favorite=True)
        self.store.feedback('example:100', dismissed=True)
        saved = ListingStore(self.path).state()['listings'][0]
        self.assertTrue(saved['dismissed'])
        self.assertFalse(saved['favorite'])
        self.assertEqual(len(saved['notes']), 1)

    def test_contact_and_viewing_history_remains_when_listing_is_gone(self) -> None:
        _ = self.store.upsert_listing(listing())
        self.store.request_contact('example:100')
        self.store.request_contact('example:100')
        self.assertEqual(present(self.store.state()['listings'][0]['contact'])['status'], 'pending')
        _ = self.store.correspond('example:100', 'outgoing', 'email', 'May I view it on Friday?', external_message_id='message-one', external_thread_id='thread-one')
        self.store.set_contact('example:100', 'contacted', channel='email')
        _ = book(self.store, 'example:100', '2026-10-30T15:00:00-05:00', 'Meet at the lobby.', 'event-100')
        _ = self.store.upsert_listing(listing(status='unavailable'))
        self.store.feedback('example:100', dismissed=True)
        saved = ListingStore(self.path).state()['listings'][0]
        self.assertEqual(present(saved['contact'])['status'], 'contacted')
        self.assertEqual(saved['correspondence'][0]['text'], 'May I view it on Friday?')
        self.assertEqual(saved['viewings'][0]['time'], '2026-10-30T20:00:00+00:00')
        self.assertEqual(saved['status'], 'unavailable')

    def test_an_import_can_decide_criteria_eligibility_in_the_same_step(self) -> None:
        _ = self.store.upsert_listing(listing(), matches_criteria=False)
        self.assertEqual(self.store.browse('Rankings')['total'], 0)
        revision = self.store.revision()
        _ = self.store.upsert_listing(listing(), matches_criteria=True)
        self.assertEqual(self.store.browse('Rankings')['total'], 1)
        self.assertGreater(self.store.revision(), revision)
        _ = self.store.upsert_listing(listing(price=2000))
        self.assertTrue(self.store.listing('example:100')['matches_criteria'])
        with self.assertRaises(ValueError):
            _ = self.store.upsert_listing(listing(), matches_criteria='yes')

    def test_contacted_listings_stay_in_contacted_and_open_when_outside_the_criteria(self) -> None:
        _ = self.store.upsert_listing(listing())
        _ = self.store.upsert_listing(listing(id='example:200'))
        self.store.request_contact('example:100')
        self.store.set_matches_criteria('example:100', False)
        self.store.set_matches_criteria('example:200', False)
        self.assertEqual([item['id'] for item in self.store.browse('Contacted')['items']], ['example:100'])
        self.assertEqual(self.store.browse('Rankings')['total'], 0)
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Meet in the lobby.')
        _ = self.store.record_booking(viewing, '2026-10-31T16:00:00+00:00', 'Meet in the lobby.', 'event-1')
        self.assertEqual([item['id'] for item in self.store.browse('Viewings')['items']], ['example:100'])
        self.assertEqual(self.store.listing('example:100', visible_only=True)['id'], 'example:100')
        with self.assertRaises(KeyError):
            _ = self.store.listing('example:200', visible_only=True)

    def test_listing_notes_keep_both_authors(self) -> None:
        _ = self.store.upsert_listing(listing())
        self.store.add_note('I prefer quieter streets.', listing_id='example:100')
        self.store.add_note('I will prioritize side streets.', listing_id='example:100', role='agent')
        notes = ListingStore(self.path).listing('example:100')['notes']
        self.assertEqual([note['role'] for note in notes], ['user', 'agent'])
        self.assertEqual(notes[0]['text'], 'I prefer quieter streets.')

    def test_invalid_import_does_not_replace_saved_listing(self) -> None:
        _ = self.store.upsert_listing(listing())
        invalid: list[dict[str, object]] = [
            {'price': -1}, {'price': float('nan')}, {'photos': ['javascript:alert(1)']},
            {'sources': [{'label': 'Unsafe', 'url': 'file:///private'}]},
            {'status': 'unknown'}, {'rank': 0}, {'amenities': 'Laundry'},
            {'first_seen': 'yesterday'}, {'first_seen': '20261020'}, {'first_seen': '2026-W43-1'},
            {'currency': 'INVALID'}, {'favorite': True},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _ = self.store.upsert_listing(listing(**changes))
        self.assertEqual(self.store.state()['listings'][0]['price'], 2100)

    def test_invalid_actions_and_missing_listings_are_reported(self) -> None:
        _ = self.store.upsert_listing(listing())
        actions: list[Callable[[], object]] = [
            lambda: self.store.add_note('   ', listing_id='example:100'),
            lambda: self.store.feedback('example:100', favorite='yes'),
            lambda: self.store.add_note('Note', listing_id='example:100', role='system'),
            lambda: self.store.agree_viewing('example:100', 'tomorrow', ''),
            lambda: self.store.correspond('example:100', 'unknown', 'email', 'Hello', external_message_id='bad'),
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(ValueError):
                _ = action()
        with self.assertRaises(KeyError):
            self.store.feedback('missing', favorite=True)


if __name__ == '__main__':
    _ = unittest.main()
