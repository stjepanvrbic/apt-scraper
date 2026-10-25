import struct
import tempfile
import unittest
from pathlib import Path

from test_feature_record import extraction
from test_gallery_store import ListingStore, listing

PHOTOS = ['https://example.com/living.jpg', 'https://example.com/kitchen.jpg', 'https://example.com/bath.jpg']
IMAGE, TEXT = 'clip@1', 'minilm@1'


def vector(*values):
    return struct.pack(f'<{len(values)}f', *values)


class EmbeddingStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ListingStore(Path(directory.name) / 'listings.sqlite3')
        self.fingerprint = self.store.upsert_listing(listing(photos=PHOTOS))['content_fingerprint']
        self.store.upsert_listing(listing(id='example:200'))
        self.store.save_extraction('example:100', self.fingerprint, extraction(traits=['exposed brick'], concerns=['street noise']),
                                   photos=PHOTOS, phrases=[])

    def test_a_listing_with_a_record_waits_for_embeddings_with_its_photos_room_types_and_text(self):
        self.assertEqual(self.store.embedding_inputs([IMAGE, TEXT]), [{
            'id': 'example:100', 'fingerprint': self.fingerprint, 'title': 'Courtyard apartment',
            'description': 'Bright rooms facing a shared courtyard.',
            'photos': [(PHOTOS[0], 'kitchen'), (PHOTOS[1], 'living'), (PHOTOS[2], 'living')],
            'traits': ['exposed brick'], 'concerns': ['street noise'], 'missing': [IMAGE, TEXT]}])

    def test_saved_embeddings_are_kept_per_model_for_the_current_content(self):
        parts = {'photos': (3, vector(1, 0)), 'photos:kitchen': (1, vector(0, 1))}
        self.store.save_embeddings('example:100', self.fingerprint, IMAGE, parts)
        self.assertEqual(self.store.embeddings(IMAGE), {'example:100': parts})
        self.assertEqual([entry['missing'] for entry in self.store.embedding_inputs([IMAGE, TEXT])], [[TEXT]])
        self.store.save_embeddings('example:100', self.fingerprint, TEXT, {'text': (2, vector(0.5, 0.5))})
        self.assertEqual(self.store.embedding_inputs([IMAGE, TEXT]), [])

    def test_saving_again_replaces_every_part_of_that_model(self):
        self.store.save_embeddings('example:100', self.fingerprint, IMAGE, {'photos': (3, vector(1, 0)), 'photos:kitchen': (1, vector(0, 1))})
        self.store.save_embeddings('example:100', self.fingerprint, IMAGE, {'photos': (2, vector(0, 1))})
        self.assertEqual(self.store.embeddings(IMAGE), {'example:100': {'photos': (2, vector(0, 1))}})

    def test_a_changed_listing_waits_again_and_its_old_embeddings_no_longer_count(self):
        self.store.save_embeddings('example:100', self.fingerprint, IMAGE, {'photos': (3, vector(1, 0))})
        changed = self.store.upsert_listing(listing(photos=PHOTOS, description='Renovated.'))['content_fingerprint']
        self.assertEqual(self.store.embeddings(IMAGE), {})
        self.assertEqual(self.store.embedding_inputs([IMAGE]), [])
        self.store.save_extraction('example:100', changed, extraction(), photos=PHOTOS, phrases=[])
        self.assertEqual([(entry['fingerprint'], entry['missing']) for entry in self.store.embedding_inputs([IMAGE])], [(changed, [IMAGE])])
        with self.assertRaises(ValueError):
            self.store.save_embeddings('example:100', self.fingerprint, IMAGE, {'photos': (3, vector(1, 0))})

    def test_an_embedding_needs_a_positive_count_and_float32_bytes(self):
        for parts in ({}, {'photos': (0, vector(1))}, {'photos': (1, b'abc')}, {'photos': (1, b'')}, {'photos': (1.0, vector(1))},
                      {'photos': (1, [1.0])}):
            with self.subTest(parts=parts), self.assertRaisesRegex(ValueError, 'float32'):
                self.store.save_embeddings('example:100', self.fingerprint, IMAGE, parts)
        self.assertEqual(self.store.embeddings(IMAGE), {})


if __name__ == '__main__':
    unittest.main()
