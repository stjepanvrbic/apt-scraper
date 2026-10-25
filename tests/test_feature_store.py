import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_feature_record import extraction
from test_gallery_store import ListingStore, listing
from schema import MIGRATIONS
from record import SEED_TAGS

PHOTOS = ['https://example.com/living.jpg', 'https://example.com/kitchen.jpg', 'https://example.com/bath.jpg']


class FeatureRecordStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ListingStore(Path(self.directory.name) / 'listings.sqlite3')
        self.fingerprint = self.store.upsert_listing(listing())['content_fingerprint']

    def tearDown(self):
        self.directory.cleanup()

    def save(self, value=None, *, phrases=(), fingerprint=None):
        return self.store.save_extraction('example:100', fingerprint or self.fingerprint, value or extraction(phrases=phrases),
                                          photos=PHOTOS, phrases=list(phrases))

    def test_a_valid_extraction_is_stored_for_the_current_content_with_each_photo_url(self):
        self.save(extraction(traits=['Exposed Brick']))
        record = self.store.feature_record('example:100')
        self.assertEqual(record['extraction']['traits'], ['exposed brick'])
        self.assertEqual([(photo['index'], photo['url'], photo['room']) for photo in record['extraction']['photos']],
                         [(1, PHOTOS[0], 'kitchen'), (2, PHOTOS[1], 'living'), (3, PHOTOS[2], 'living')])
        self.assertNotIn('preferences', record['extraction'])
        self.assertEqual(record['preferences'], {})
        self.assertRegex(record['extracted_at'], r'^\d{4}-\d{2}-\d{2}T')
        self.assertEqual(self.store.feature_records(), {'example:100': record})

    def test_the_tag_list_starts_with_the_seed_tags(self):
        self.assertEqual(self.store.tags(), sorted(SEED_TAGS))

    def test_new_traits_and_concerns_join_the_tag_list(self):
        self.assertEqual(self.save(extraction(traits=['exposed brick', 'pressed tin ceiling'], concerns=['steep stairs'])),
                         {'new_tags': ['pressed tin ceiling', 'steep stairs']})
        self.assertEqual(self.store.tags(), sorted({*SEED_TAGS, 'pressed tin ceiling', 'steep stairs'}))
        self.assertEqual(self.save(extraction(traits=['pressed tin ceilings', 'murphy bed'])), {'new_tags': ['murphy bed']})

    def test_a_spelling_variant_of_a_known_tag_is_stored_as_the_known_tag(self):
        self.assertEqual(self.save(extraction(traits=['Walk in closets', 'Back yard', 'claw-foot tub'])), {'new_tags': []})
        self.assertEqual(self.store.feature_record('example:100')['extraction']['traits'], ['walk-in closet', 'backyard', 'clawfoot tub'])

    def test_an_invalid_record_is_rejected_with_the_reason_and_nothing_is_stored(self):
        revision = self.store.revision()
        value = extraction(traits=['new tag'])
        value['ratings']['natural_light'] = {'value': 7, 'confidence': 0.5}
        with self.assertRaisesRegex(ValueError, 'natural_light'):
            self.save(value)
        self.assertIsNone(self.store.feature_record('example:100'))
        self.assertEqual(self.store.tags(), sorted(SEED_TAGS))
        self.assertEqual(self.store.revision(), revision)

    def test_the_photo_count_comes_from_the_prepared_photos(self):
        with self.assertRaisesRegex(ValueError, 'photo 3'):
            self.save(extraction(2))

    def test_an_extraction_of_changed_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.save(fingerprint='0' * 64)

    def test_changed_content_has_no_record_until_extracted_again(self):
        self.save()
        changed = self.store.upsert_listing(listing(description='Now with a new kitchen.'))['content_fingerprint']
        self.assertIsNone(self.store.feature_record('example:100'))
        self.assertEqual(self.store.feature_records(), {})
        self.save(extraction(summary='Renovated.'), fingerprint=changed)
        self.assertEqual(self.store.feature_record('example:100')['extraction']['summary'], 'Renovated.')
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM feature_records').fetchone()[0], 1)

    def test_unchanged_content_keeps_its_record_through_another_search_save(self):
        self.save()
        self.store.upsert_listing(listing())
        self.assertIsNotNone(self.store.feature_record('example:100'))

    def test_preferences_judged_later_join_those_judged_in_the_extraction(self):
        self.save(phrases=['Balcony'])
        self.store.judge_preferences([('example:100', self.fingerprint, {'Near a park': {'verdict': 'has', 'evidence': 'Faces a park.'}},
                                       ['Near a park'])])
        self.assertEqual(set(self.store.feature_record('example:100')['preferences']), {'Balcony', 'Near a park'})

    def test_a_preference_judgment_needs_the_extraction_of_the_current_content(self):
        with self.assertRaisesRegex(ValueError, 'no feature record'):
            self.store.judge_preferences([('example:100', self.fingerprint, {'Balcony': {'verdict': 'has', 'evidence': ''}}, ['Balcony'])])
        with self.assertRaisesRegex(ValueError, 'verdict|has, rules out'):
            self.save()
            self.store.judge_preferences([('example:100', self.fingerprint, {'Balcony': {'verdict': 'yes', 'evidence': ''}}, ['Balcony'])])

    def test_judgments_of_several_listings_are_stored_together_or_not_at_all(self):
        self.save()
        other = self.store.upsert_listing(listing(id='example:200'))['content_fingerprint']
        good = ('example:100', self.fingerprint, {'Balcony': {'verdict': 'has', 'evidence': ''}}, ['Balcony'])
        with self.assertRaisesRegex(ValueError, 'example:200 has no feature record'):
            self.store.judge_preferences([good, ('example:200', other, {'Balcony': {'verdict': 'has', 'evidence': ''}}, ['Balcony'])])
        self.assertEqual(self.store.feature_record('example:100')['preferences'], {})


class ReadAttemptTests(unittest.TestCase):
    def test_attempts_count_handed_reads_and_forget_reads_no_longer_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ListingStore(Path(directory) / 'listings.sqlite3')
            store.record_read_attempts(['a', 'b'], {'a', 'b', 'c'})
            store.record_read_attempts(['a'], {'a', 'c'})
            self.assertEqual(store.read_attempts(), {'a': 2})
            store.reset_read_attempts()
            self.assertEqual(store.read_attempts(), {})


class FeatureMigrationTests(unittest.TestCase):
    def test_a_version_3_store_gains_feature_records_and_keeps_its_listings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                for migration in MIGRATIONS[:3]:
                    migration(connection)
                connection.execute('PRAGMA user_version = 3')
                connection.commit()
            store = ListingStore(path)
            self.assertEqual(store.schema_version, 4)
            fingerprint = store.upsert_listing(listing())['content_fingerprint']
            store.save_extraction('example:100', fingerprint, extraction(), photos=PHOTOS, phrases=[])
            self.assertIn('exposed brick', store.feature_record('example:100')['extraction']['traits'])


if __name__ == '__main__':
    unittest.main()
