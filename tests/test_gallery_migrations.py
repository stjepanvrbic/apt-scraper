import sqlite3
import tempfile
import unittest
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from test_gallery_store import listing, present
from schema import MIGRATIONS, notes_session_per_database
from store import ListingStore


class MigrationTests(unittest.TestCase):
    def test_new_store_initializes_once_and_preserves_records_on_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            store = ListingStore(path)
            _ = store.upsert_listing(listing())
            store.feedback('example:100', favorite=True)
            store.add_note('Keep the courtyard.', listing_id='example:100')
            for _ in range(2):
                store = ListingStore(path)
                self.assertEqual(store.schema_version, len(MIGRATIONS))
                saved = store.listing('example:100')
                self.assertTrue(saved['favorite'])
                self.assertEqual(saved['rental_period'], 'month')
                self.assertEqual(saved['notes'][0]['text'], 'Keep the courtyard.')

    def test_a_stored_notes_session_is_not_carried_over_and_its_running_reply_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            with closing(sqlite3.connect(path)) as connection, connection:
                for index, migration in enumerate(MIGRATIONS[:MIGRATIONS.index(notes_session_per_database)]):
                    migration(connection)
                    _ = connection.execute(f'PRAGMA user_version = {index + 1}')
                _ = connection.execute("UPDATE chat_session SET session_id = 'shared-session'")
                _ = connection.execute("INSERT INTO chat_messages (role, content, status, created_at) VALUES ('user', 'Quiet street', 'complete', '2026-10-01')")
                _ = connection.execute('''INSERT INTO chat_messages (role, content, status, created_at, admission_id)
                    VALUES ('agent', 'Noted', 'running', '2026-10-01', 'admission-in-shared-session')''')
            store = ListingStore(path)
            self.assertEqual(store.schema_version, len(MIGRATIONS))
            state = store.chat_state()
            self.assertIsNone(state['session_id'])
            self.assertEqual([message['content'] for message in state['messages']], ['Quiet street', 'Noted'])
            self.assertFalse(state['active'])
            reply = state['messages'][-1]
            self.assertEqual(reply['status'], 'error')
            self.assertIn('Send the message again', present(reply['error']))

    def test_a_session_written_by_a_process_that_predates_the_migration_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / 'listings.sqlite3')
            identity = store.start_chat('Quiet street', 'request')
            with closing(sqlite3.connect(store.path)) as connection, connection:
                _ = connection.execute("UPDATE chat_session SET session_id = 'local-shared-titled' WHERE id = 1")
            self.assertIsNone(store.chat_state()['session_id'])
            store.update_chat(identity, status='complete', session_id='local-own')
            with closing(sqlite3.connect(store.path)) as connection, connection:
                _ = connection.execute("UPDATE chat_session SET session_id = 'local-shared-titled' WHERE id = 1")
            self.assertEqual(store.chat_state()['session_id'], 'local-own')

    def test_a_store_refuses_a_database_migrated_after_it_was_opened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / 'listings.sqlite3')
            identity = store.start_chat('Quiet street', 'request')
            with closing(sqlite3.connect(store.path)) as connection:
                _ = connection.execute(f'PRAGMA user_version = {len(MIGRATIONS) + 1}')
            before = store.path.read_bytes()
            operations: tuple[Callable[[], object], ...] = (lambda: store.update_chat(identity, status='complete', session_id='local-own'),
                                                            lambda: store.start_chat('Another', 'another-request'),
                                                            lambda: store.upsert_listing(listing()),
                                                            store.chat_state)
            for operation in operations:
                with self.assertRaisesRegex(ValueError, 'migrated'):
                    _ = operation()
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(store.schema_version, len(MIGRATIONS) + 1)

    def test_newer_schema_is_rejected_without_modification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'future.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                _ = connection.execute('PRAGMA user_version = 999')
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'newer'):
                _ = ListingStore(path)
            self.assertEqual(path.read_bytes(), before)
