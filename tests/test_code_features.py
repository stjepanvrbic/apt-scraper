import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from test_gallery_store import ListingStore, listing
from code_features import code_features, unit_level
from schema import MIGRATIONS, content_fingerprint
from store import validate_listing


def unit(**changes):
    return {'price': 2100, 'period': 'month', 'bedrooms': 2, 'bathrooms': 1, 'size_sqft': 850, 'available_on': None,
            'label': None, 'floor': None, **changes}


def facts(*units, **changes):
    return {'currency': 'USD', 'areas': ['Logan Square, Chicago, IL'], 'latitude': 41.927895, 'longitude': -87.723366,
            'stories': None, 'units': list(units or [unit()]), **changes}


def home(address, **changes):
    return listing(id='zillow:1', address=address, title=address.split(',')[0], **changes)


class UnitLevelTests(unittest.TestCase):
    def test_garden_and_basement_labels_are_garden(self):
        for label in ['#G', 'G', '#G2', 'G 1', '#Gn', 'GS', '#Garden', 'Unit Garden', 'Garden Unit', 'Garden Level', 'APT GDN',
                      '#Basement', 'Bsmt', 'LL', 'Lower Level', 'unit g']:
            with self.subTest(label=label):
                self.assertEqual(unit_level(label, None, None), (None, 'garden'))

    def test_a_floor_read_from_the_label_gives_ground_middle_or_top_against_the_building_height(self):
        cases = [('#1F', None, (1, 'ground')), ('APT 1R', None, (1, 'ground')), ('Unit 101', None, (1, 'ground')),
                 ('#2F', 3, (2, 'middle')), ('APT 3N', 3, (3, 'top')), ('Unit 420', 12, (4, 'middle')), ('#6H', 6, (6, 'top')),
                 ('2nd Floor', None, (2, None)), ('#3N', None, (3, None)), ('#PH2', None, (None, 'top')), ('Penthouse', 20, (None, 'top'))]
        for label, stories, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(unit_level(label, None, stories), expected)

    def test_labels_that_name_no_floor_leave_the_level_unknown(self):
        for label in [None, '', '#2', '#14', 'Unit 2418', 'A02C', '#C1', 'Ch-1', '2706-1FF', 'B', 'Unit Gh']:
            with self.subTest(label=label):
                self.assertEqual(unit_level(label, None, None), (None, None))

    def test_a_floor_stated_in_the_facts_wins_over_one_read_from_the_label(self):
        self.assertEqual(unit_level('#2F', 1, 3), (1, 'ground'))
        self.assertEqual(unit_level(None, 3, 3), (3, 'top'))
        self.assertEqual(unit_level(None, 5, 3), (5, None))
        self.assertEqual(unit_level('#G', 2, 3), (None, 'garden'))


class CodeFeatureTests(unittest.TestCase):
    def test_the_garden_units_the_model_missed_read_as_garden_from_their_address(self):
        for address in ['3835 W Wrightwood Ave #G, Chicago, IL 60647', '1940 N Humboldt Blvd #Gn, Chicago, IL 60647',
                        '2326 N Sacramento Ave #Basement, Chicago, IL 60647']:
            with self.subTest(address=address):
                features = code_features(home(address), facts())
                self.assertEqual(features['unit_level'], 'garden')
                self.assertEqual(features['unit_label'], address.split('#')[1].split(',')[0])

    def test_address_words_before_the_street_are_not_a_unit_label(self):
        features = code_features(home('Noca Blu Apartment Homes, 2340 N California Ave, Chicago, IL 60647'), facts())
        self.assertEqual((features['unit_label'], features['unit_level']), (None, None))
        features = code_features(home('2548 N California Ave APT 3N, Chicago, IL 60647'), facts(stories=3))
        self.assertEqual((features['unit_label'], features['unit_floor'], features['unit_level']), ('3N', 3, 'top'))

    def test_price_size_rooms_and_level_come_from_the_cheapest_unit_across_rental_periods(self):
        units = [unit(price=2400, size_sqft=1000, bedrooms=3, label='Unit 4B'),
                 unit(price=500, period='week', size_sqft=600, bedrooms=1, bathrooms=1.5, label='Unit G2'),
                 unit(price=1000, period='total', size_sqft=300, bedrooms=0, label='Unit 1A'),
                 unit(price=None, size_sqft=200, bedrooms=0, label='Unit 1B')]
        features = code_features(listing(address='300 W Example Ave, Chicago, IL'), facts(*units))
        self.assertAlmostEqual(features['monthly_price'], 500 * 52 / 12)
        self.assertAlmostEqual(features['price_per_sqft'], 500 * 52 / 12 / 600)
        self.assertEqual((features['size_sqft'], features['bedrooms'], features['bathrooms']), (600, 1, 1.5))
        self.assertEqual((features['unit_label'], features['unit_level']), ('Unit G2', 'garden'))
        self.assertEqual(features['currency'], 'USD')

    def test_a_unit_without_a_size_has_no_price_per_square_foot(self):
        features = code_features(home('1940 N Humboldt Blvd #Gn, Chicago, IL 60647'), facts(unit(price=2300, size_sqft=None)))
        self.assertEqual(features['monthly_price'], 2300)
        self.assertIsNone(features['size_sqft'])
        self.assertIsNone(features['price_per_sqft'])

    def test_coordinates_and_photo_count_are_kept_and_missing_coordinates_are_unknown(self):
        features = code_features(home('1 Example St, Chicago'), facts())
        self.assertEqual((features['latitude'], features['longitude'], features['photo_count']), (41.927895, -87.723366, 2))
        features = code_features(home('1 Example St, Chicago', photos=[]), facts(latitude=None, longitude=None))
        self.assertEqual((features['latitude'], features['longitude'], features['photo_count']), (None, None, 0))

    def test_a_listing_without_site_facts_is_read_from_its_own_price_and_address(self):
        features = code_features(listing(address='12 Example St #G, Chicago', price=450, rental_period='week'), None)
        self.assertAlmostEqual(features['monthly_price'], 450 * 52 / 12)
        self.assertEqual(features['unit_level'], 'garden')
        for key in ('size_sqft', 'price_per_sqft', 'bedrooms', 'bathrooms', 'latitude', 'longitude'):
            self.assertIsNone(features[key], key)
        self.assertIsNone(code_features(listing(price='Price on request', rental_period=None), None)['monthly_price'])


class StoredCodeFeatureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'listings.sqlite3'
        self.store = ListingStore(self.path)

    def save(self, listing, facts):
        fingerprint = self.store.upsert_listing(listing)['content_fingerprint']
        self.store.analyze(listing['id'], fingerprint, {'facts': facts, 'must_haves': {}})
        return fingerprint

    def test_features_are_stored_when_a_listing_is_saved_and_recomputed_when_its_content_changes(self):
        first = self.save(home('3835 W Wrightwood Ave #G, Chicago, IL 60647', price=2195), facts(unit(price=2195, size_sqft=1035)))
        features = self.store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertEqual(features['fingerprint'], first)
        self.assertEqual((features['monthly_price'], features['unit_level'], features['latitude']), (2195, 'garden', 41.927895))
        self.assertAlmostEqual(features['price_per_sqft'], 2195 / 1035)
        self.assertEqual((features['first_seen'], features['days_since_first_seen']), ('2026-10-20', 5))
        second = self.save(home('3835 W Wrightwood Ave #2F, Chicago, IL 60647', price=2300), facts(unit(price=2300, size_sqft=None)))
        features = self.store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertNotEqual(second, first)
        self.assertEqual((features['fingerprint'], features['monthly_price'], features['unit_floor'], features['price_per_sqft']),
                         (second, 2300, 2, None))

    def test_a_listing_saved_before_its_facts_has_features_from_the_listing_alone(self):
        self.store.upsert_listing(home('12 Example St #G, Chicago', price=1800))
        features = self.store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertEqual((features['monthly_price'], features['unit_level'], features['latitude']), (1800, 'garden', None))

    def test_a_store_from_before_code_features_gets_them_for_every_listing_when_opened(self):
        older = Path(self.path.parent) / 'older.sqlite3'
        data = validate_listing(home('2326 N Sacramento Ave #Basement, Chicago, IL 60647'))
        fingerprint = content_fingerprint(data)
        with closing(sqlite3.connect(older)) as connection, connection:
            for index, migration in enumerate(MIGRATIONS[:-1]):
                migration(connection)
                connection.execute(f'PRAGMA user_version = {index + 1}')
            connection.execute('INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)',
                               (data['id'], json.dumps(data), 'available', data['first_seen'], fingerprint))
            connection.execute('INSERT INTO ranking (listing_id, fingerprint, features) VALUES (?, ?, ?)',
                               (data['id'], fingerprint, json.dumps({'facts': facts(unit(price=1700, size_sqft=None))})))
        store = ListingStore(older)
        self.assertEqual(store.schema_version, len(MIGRATIONS))
        features = store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertEqual((features['fingerprint'], features['monthly_price'], features['unit_level']), (fingerprint, 1700, 'garden'))

if __name__ == '__main__':
    unittest.main()
