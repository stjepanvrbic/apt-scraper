"""The embedding step with the pinned models, which it downloads from Hugging Face into EMBEDDING_MODELS (by default a
directory in the system's temporary directory) when they are not there yet."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'profile/skills/gallery/scripts'), str(ROOT / 'profile/skills/ranking/scripts'), str(ROOT / 'tests')]

from embeddings import (IMAGE_MODEL, MODELS, TEXT_MODEL, Encoders, download, download_models, embed_store, photo_parts,  # noqa: E402
                        text_chunks)
from features import PhotosUnavailable  # noqa: E402
from store import ListingStore  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from test_gallery_store import listing  # noqa: E402

MODELS_DIRECTORY = Path(os.environ.get('EMBEDDING_MODELS') or Path(tempfile.gettempdir()) / 'apt-scraper-embedding-models')
PHOTOS = ['/media/kitchen.jpg', '/media/living.jpg', '/media/bedroom.jpg', '/media/loft.jpg']
ROOMS = [{'index': 1, 'room': 'kitchen'}, {'index': 2, 'room': 'living'}, {'index': 3, 'room': 'bedroom'}, {'index': 4, 'room': 'living'}]


def setUpModule():
    global ENCODERS
    download_models(MODELS_DIRECTORY)
    ENCODERS = Encoders(MODELS_DIRECTORY)


class EmbedStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ListingStore(Path(directory.name) / 'listings.sqlite3')
        self.fingerprint = self.add('example:100', PHOTOS)

    def add(self, identifier, photos, **changes):
        fingerprint = self.store.upsert_listing(listing(id=identifier, photos=photos, **changes))['content_fingerprint']
        self.store.save_extraction(identifier, fingerprint, extraction(len(photos), photos=ROOMS[:len(photos)], traits=['exposed brick']),
                                   photos=photos, phrases=[])
        return fingerprint

    def test_every_photo_is_embedded_and_averaged_per_room_type_and_the_text_in_chunks_once_per_content(self):
        report = embed_store(self.store, ENCODERS)
        self.assertEqual([(entry['id'], entry['photos']) for entry in report], [('example:100', 4)])
        self.assertGreaterEqual(report[0]['room_types_agreeing'], 2)
        image = self.store.embeddings(IMAGE_MODEL.name)['example:100']
        self.assertEqual({part: count for part, (count, _) in image.items()},
                         {'photos': 4, 'photos:kitchen': 1, 'photos:living': 2, 'photos:bedroom': 1})
        vectors = ENCODERS.images([Image.open(ROOT / 'profile/skills/gallery/web' / photo.lstrip('/')) for photo in PHOTOS])
        numpy.testing.assert_allclose(numpy.frombuffer(image['photos:living'][1], '<f4'), vectors[[1, 3]].mean(axis=0), atol=1e-6)
        text = self.store.embeddings(TEXT_MODEL.name)['example:100']['text']
        self.assertEqual((text[0], len(text[1])), (2, 384 * 4))
        self.assertEqual(embed_store(self.store, ENCODERS), [])
        self.add('example:100', PHOTOS[:2], description='Renovated.')
        self.assertEqual([(entry['id'], entry['photos']) for entry in embed_store(self.store, ENCODERS)], [('example:100', 2)])

    def test_a_photo_the_site_no_longer_has_is_left_out_and_one_that_cannot_load_now_leaves_the_listing_waiting(self):
        self.add('example:200', ['https://example.com/gone.jpg', '/media/loft.jpg'])
        self.add('example:300', ['https://example.com/busy.jpg', '/media/loft.jpg'])

        def fetch(url):
            if url.endswith('gone.jpg'):
                return None
            if url.endswith('busy.jpg'):
                raise PhotosUnavailable(f'{url} answered HTTP 503.')
            return download(url)

        report = {entry['id']: entry for entry in embed_store(self.store, ENCODERS, fetch=fetch)}
        self.assertEqual(report['example:200']['photos'], 1)
        self.assertIn('HTTP 503', report['example:300']['error'])
        self.assertEqual(sorted(self.store.embeddings(IMAGE_MODEL.name)), ['example:100', 'example:200'])
        self.assertEqual([entry['id'] for entry in self.store.embedding_inputs([pin.name for pin in MODELS])], ['example:300'])


class NothingToEmbedTests(unittest.TestCase):
    def test_a_listing_without_text_is_reported_and_keeps_waiting(self):
        class Store:
            saved = []

            def embedding_inputs(self, models):
                return [{'id': 'example:100', 'fingerprint': 'f', 'title': None, 'description': None, 'photos': [], 'traits': [], 'concerns': [],
                         'missing': [TEXT_MODEL.name]}]

            def save_embeddings(self, *arguments):
                self.saved.append(arguments)

        store = Store()
        report = embed_store(store, ENCODERS)
        self.assertEqual(report[0]['error'], 'It has no title, description, traits or concerns.')
        self.assertEqual(store.saved, [])


class PartsTests(unittest.TestCase):
    def test_photo_parts_average_the_unit_vectors_over_all_photos_and_each_room_type(self):
        vectors = numpy.array([[1, 0], [0, 1], [0, 1]], dtype='float32')
        parts = photo_parts(vectors, ['kitchen', 'living', 'living'])
        self.assertEqual({part: count for part, (count, _) in parts.items()}, {'photos': 3, 'photos:kitchen': 1, 'photos:living': 2})
        for part, expected in {'photos': [1 / 3, 2 / 3], 'photos:kitchen': [1, 0], 'photos:living': [0, 1]}.items():
            numpy.testing.assert_allclose(numpy.frombuffer(parts[part][1], 'float32'), expected, rtol=1e-6)

    def test_the_text_is_the_traits_and_concerns_then_the_title_and_description_in_chunks(self):
        words = ' '.join(f'word{number}' for number in range(320))
        chunks = text_chunks({'title': 'Garden unit', 'description': words, 'traits': ['exposed brick', 'bay window'], 'concerns': ['low ceiling']})
        self.assertEqual(chunks[0], 'exposed brick, bay window. low ceiling.')
        self.assertEqual([len(chunk.split()) for chunk in chunks[1:]], [150, 150, 22])
        self.assertTrue(chunks[1].startswith('Garden unit word0'))
        self.assertEqual(text_chunks({'title': None, 'description': None, 'traits': [], 'concerns': []}), [])


class DownloadTests(unittest.TestCase):
    def test_a_weight_file_without_the_pinned_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for pin in MODELS:
                for name in (*pin.files, *pin.weights):
                    path = pin.directory(directory) / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'not the weights')
            with self.assertRaisesRegex(ValueError, 'pinned SHA-256'):
                download_models(directory)


if __name__ == '__main__':
    unittest.main()
