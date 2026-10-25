import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_gallery_store import ListingStore, listing
from schema import MIGRATIONS


class MigrationTests(unittest.TestCase):
    def test_new_store_initializes_once_and_preserves_records_on_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            store = ListingStore(path)
            store.upsert_listing(listing())
            store.feedback('example:100', favorite=True)
            store.add_note('Keep the courtyard.', listing_id='example:100')
            for _ in range(2):
                store = ListingStore(path)
                self.assertEqual(store.schema_version, 3)
                saved = store.listing('example:100')
                self.assertTrue(saved['favorite'])
                self.assertEqual(saved['rental_period'], 'month')
                self.assertEqual(saved['notes'][0]['text'], 'Keep the courtyard.')

    def test_a_version_2_store_gains_note_readings_and_keeps_its_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.row_factory = sqlite3.Row
                for migration in MIGRATIONS[:2]:
                    migration(connection)
                connection.execute("INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES ('example:100', '{}', 'available', '2026-10-20', 'abc')")
                connection.execute("INSERT INTO notes (listing_id, role, text, created_at) VALUES ('example:100', 'user', 'Too dark.', '2026-10-21')")
                connection.execute('PRAGMA user_version = 2')
                connection.commit()
            store = ListingStore(path)
            self.assertEqual(store.schema_version, 3)
            [note] = store.unread_notes()
            store.read_note(note['id'], {'sentiment': 'negative', 'likes': [], 'dislikes': ['dim rooms']})
            self.assertEqual(store.unread_notes(), [])

    def test_newer_schema_is_rejected_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'future.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('PRAGMA user_version = 999')
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'newer'):
                ListingStore(path)
            self.assertEqual(path.read_bytes(), before)
