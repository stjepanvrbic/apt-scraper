import tempfile
import unittest
from pathlib import Path

from test_gallery_store import ListingStore, book, listing


class GalleryQueryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ListingStore(Path(directory.name) / 'listings.sqlite3')

    def add(self, identity, rank):
        result = self.store.upsert_listing(listing(id=identity, description='Long description. ' * 500))
        self.store.rank(identity, result['content_fingerprint'], rank, {'text': 'Quiet street.'})

    def test_pages_bound_grid_payload_and_include_unranked_listings(self):
        for index in range(55):
            self.add(f'site:{index}', index + 1)
        self.store.upsert_listing(listing(id='unranked'))
        page = self.store.browse('Rankings', page=2, page_size=24)
        self.assertEqual(page['total'], 56)
        self.assertEqual(len(page['items']), 24)
        self.assertEqual(page['items'][0]['id'], 'site:24')
        self.assertNotIn('description', page['items'][0])
        self.assertNotIn('notes', page['items'][0])
        self.assertEqual(len(page['items'][0]['photos']), 1)
        self.store.feedback('site:24', dismissed=True)
        self.assertEqual(self.store.browse('Dismissed')['items'][0]['id'], 'site:24')
        self.store.feedback('site:24', dismissed=False)
        self.assertEqual(self.store.browse('Dismissed')['total'], 0)

    def test_criteria_exclusion_hides_a_listing_except_in_contacted_and_viewings_and_retains_history(self):
        self.add('excluded', 1)
        self.store.feedback('excluded', favorite=True)
        self.store.request_contact('excluded')
        book(self.store, 'excluded', '2026-11-01T10:00:00Z', '', 'event')
        self.store.set_matches_criteria('excluded', False)
        self.store.upsert_listing(listing(id='excluded'))
        for view in ['Rankings', 'Favorites', 'Dismissed']:
            self.assertEqual(self.store.browse(view)['total'], 0)
        for view in ['Contacted', 'Viewings']:
            self.assertEqual(self.store.browse(view)['total'], 1)
        self.assertIsNotNone(self.store.listing('excluded', visible_only=True)['contact'])
        self.store.set_matches_criteria('excluded', True)
        self.assertEqual(self.store.browse('Rankings')['total'], 1)

    def test_viewings_use_chronological_order_and_contact_cards_include_bounded_messages(self):
        self.add('late', 1)
        self.add('early', 2)
        book(self.store, 'late', '2026-11-01T10:00:00-06:00', '', 'late-event')
        book(self.store, 'early', '2026-10-31T15:00:00+01:00', '', 'early-event')
        self.assertEqual([item['id'] for item in self.store.browse('Viewings')['items']], ['early', 'late'])
        self.store.request_contact('early')
        for index in range(60):
            self.store.correspond('early', 'incoming', 'email', f'Message {index}', external_message_id=f'message-{index}')
        card = self.store.browse('Contacted')['items'][0]
        self.assertEqual(card['correspondence_total'], 60)
        self.assertEqual([message['text'] for message in card['correspondence']], ['Message 57', 'Message 58', 'Message 59'])
        detail = self.store.listing('early')
        self.assertEqual(len(detail['correspondence']), 50)
        self.assertEqual(len(self.store.listing('early', correspondence_page=2)['correspondence']), 10)

    def test_revision_changes_only_when_data_changes(self):
        self.add('first', 1)
        revision = self.store.revision()
        self.store.upsert_listing(listing(id='first', description='Long description. ' * 500))
        self.assertEqual(self.store.revision(), revision)
        self.store.add_note('A note', listing_id='first')
        self.assertGreater(self.store.revision(), revision)
