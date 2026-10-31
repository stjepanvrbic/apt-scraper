import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Unpack, override

from test_gallery_store import listing, present
from code_features import DEFAULT_TRANSIT_FEED, Coordinates, Facts, Landmark, Surroundings, UnitFacts, code_features, distance_km, unit_level
from schema import MIGRATIONS, code_derived_features, content_fingerprint, distances
from shapes import Listing, Place
from store import Geocode, ListingStore, Station, validate_listing


def unit(**changes: Unpack[UnitFacts]) -> UnitFacts:
    return {'price': 2100, 'period': 'month', 'bedrooms': 2, 'bathrooms': 1, 'size_sqft': 850, 'available_on': None,
            'label': None, 'floor': None, **changes}


def facts(*units: UnitFacts, **changes: Unpack[Facts]) -> Facts:
    return {'currency': 'USD', 'areas': ['Logan Square, Chicago, IL'], 'latitude': 41.927895, 'longitude': -87.723366,
            'stories': None, 'units': list(units or [unit()]), **changes}


def home(address: str, **changes: object) -> Listing:
    return validate_listing(listing(id='zillow:1', address=address, title=address.split(',')[0], **changes))


LOGAN_SQUARE: Landmark = ('Logan Square', 41.929728, -87.708541)
CALIFORNIA: Landmark = ('California', 41.921939, -87.69689)
STATIONS: list[Station] = [{'id': '41020', 'name': 'Logan Square', 'latitude': 41.929728, 'longitude': -87.708541},
                           {'id': '40570', 'name': 'California', 'latitude': 41.921939, 'longitude': -87.69689}]
WORK: Place = {'name': 'Work', 'address': '233 S Wacker Dr, Chicago, IL 60606'}
WILLIS_TOWER: Geocode = {'latitude': 41.8788764, 'longitude': -87.6359149, 'found': 'Willis Tower, 233 South Wacker Drive, Chicago'}
HERE: Coordinates = (41.927895, -87.723366)


class UnitLevelTests(unittest.TestCase):
    def test_garden_and_basement_labels_are_garden(self) -> None:
        for label in ['#G', 'G', '#G2', 'G 1', '#Gn', 'GS', '#Garden', 'Unit Garden', 'Garden Unit', 'Garden Level', 'APT GDN',
                      '#Basement', 'Bsmt', 'LL', 'Lower Level', 'unit g']:
            with self.subTest(label=label):
                self.assertEqual(unit_level(label, None, None), (None, 'garden'))

    def test_a_floor_read_from_the_label_gives_ground_middle_or_top_against_the_building_height(self) -> None:
        cases = [('#1F', None, (1, 'ground')), ('APT 1R', None, (1, 'ground')), ('Unit 101', None, (1, 'ground')),
                 ('#2F', 3, (2, 'middle')), ('APT 3N', 3, (3, 'top')), ('Unit 420', 12, (4, 'middle')), ('#6H', 6, (6, 'top')),
                 ('2nd Floor', None, (2, None)), ('#3N', None, (3, None)), ('#PH2', None, (None, 'top')), ('Penthouse', 20, (None, 'top'))]
        for label, stories, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(unit_level(label, None, stories), expected)

    def test_labels_that_name_no_floor_leave_the_level_unknown(self) -> None:
        for label in [None, '', '#2', '#14', 'Unit 2418', 'A02C', '#C1', 'Ch-1', '2706-1FF', 'B', 'Unit Gh']:
            with self.subTest(label=label):
                self.assertEqual(unit_level(label, None, None), (None, None))

    def test_a_floor_stated_in_the_facts_wins_over_one_read_from_the_label(self) -> None:
        self.assertEqual(unit_level('#2F', 1, 3), (1, 'ground'))
        self.assertEqual(unit_level(None, 3, 3), (3, 'top'))
        self.assertEqual(unit_level(None, 5, 3), (5, None))
        self.assertEqual(unit_level('#G', 2, 3), (None, 'garden'))


class CodeFeatureTests(unittest.TestCase):
    def test_the_garden_units_the_model_missed_read_as_garden_from_their_address(self) -> None:
        for address in ['3835 W Wrightwood Ave #G, Chicago, IL 60647', '1940 N Humboldt Blvd #Gn, Chicago, IL 60647',
                        '2326 N Sacramento Ave #Basement, Chicago, IL 60647']:
            with self.subTest(address=address):
                features = code_features(home(address), facts())
                self.assertEqual(features['unit_level'], 'garden')
                self.assertEqual(features['unit_label'], address.split('#')[1].split(',')[0])

    def test_address_words_before_the_street_are_not_a_unit_label(self) -> None:
        features = code_features(home('Noca Blu Apartment Homes, 2340 N California Ave, Chicago, IL 60647'), facts())
        self.assertEqual((features['unit_label'], features['unit_level']), (None, None))
        features = code_features(home('2548 N California Ave APT 3N, Chicago, IL 60647'), facts(stories=3))
        self.assertEqual((features['unit_label'], features['unit_floor'], features['unit_level']), ('3N', 3, 'top'))

    def test_price_size_rooms_and_level_come_from_the_cheapest_unit_across_rental_periods(self) -> None:
        units = [unit(price=2400, size_sqft=1000, bedrooms=3, label='Unit 4B'),
                 unit(price=500, period='week', size_sqft=600, bedrooms=1, bathrooms=1.5, label='Unit G2'),
                 unit(price=1000, period='total', size_sqft=300, bedrooms=0, label='Unit 1A'),
                 unit(price=None, size_sqft=200, bedrooms=0, label='Unit 1B')]
        features = code_features(validate_listing(listing(address='300 W Example Ave, Chicago, IL')), facts(*units))
        self.assertAlmostEqual(present(features['monthly_price']), 500 * 52 / 12)
        self.assertAlmostEqual(present(features['price_per_sqft']), 500 * 52 / 12 / 600)
        self.assertEqual((features['size_sqft'], features['bedrooms'], features['bathrooms']), (600, 1, 1.5))
        self.assertEqual((features['unit_label'], features['unit_level']), ('Unit G2', 'garden'))
        self.assertEqual(features['currency'], 'USD')

    def test_a_unit_without_a_size_has_no_price_per_square_foot(self) -> None:
        features = code_features(home('1940 N Humboldt Blvd #Gn, Chicago, IL 60647'), facts(unit(price=2300, size_sqft=None)))
        self.assertEqual(features['monthly_price'], 2300)
        self.assertIsNone(features['size_sqft'])
        self.assertIsNone(features['price_per_sqft'])

    def test_coordinates_and_photo_count_are_kept_and_missing_coordinates_are_unknown(self) -> None:
        features = code_features(home('1 Example St, Chicago'), facts())
        self.assertEqual((features['latitude'], features['longitude'], features['photo_count']), (41.927895, -87.723366, 2))
        features = code_features(home('1 Example St, Chicago', photos=[]), facts(latitude=None, longitude=None))
        self.assertEqual((features['latitude'], features['longitude'], features['photo_count']), (None, None, 0))

    def test_a_listing_without_site_facts_is_read_from_its_own_price_and_address(self) -> None:
        features = code_features(validate_listing(listing(address='12 Example St #G, Chicago', price=450, rental_period='week')), None)
        self.assertAlmostEqual(present(features['monthly_price']), 450 * 52 / 12)
        self.assertEqual(features['unit_level'], 'garden')
        for key in ('size_sqft', 'price_per_sqft', 'bedrooms', 'bathrooms', 'latitude', 'longitude'):
            self.assertIsNone(features[key], key)
        self.assertIsNone(code_features(validate_listing(listing(price='Price on request', rental_period=None)), None)['monthly_price'])


class DistanceTests(unittest.TestCase):
    def test_distance_is_the_great_circle_distance(self) -> None:
        self.assertAlmostEqual(distance_km((41.0, -87.0), (42.0, -87.0)), 111.195, places=3)
        self.assertAlmostEqual(distance_km((0.0, 0.0), (0.0, 1.0)), 111.195, places=3)
        self.assertAlmostEqual(distance_km((0.0, 0.0), (0.0, 180.0)), 20015.114, places=3)
        self.assertAlmostEqual(distance_km(HERE, (41.8788764, -87.6359149)), distance_km((41.8788764, -87.6359149), HERE))

    def test_a_listing_gets_its_nearest_station_and_its_distance_to_each_place(self) -> None:
        surroundings = Surroundings((CALIFORNIA, LOGAN_SQUARE), (('Work', 41.8788764, -87.6359149), ('Gym', 41.9443, -87.7093)))
        features = code_features(home('1 Example St, Chicago'), facts(), surroundings)
        self.assertEqual(features['nearest_station'], 'Logan Square')
        self.assertAlmostEqual(present(features['station_distance_km']), distance_km(HERE, LOGAN_SQUARE[1:]))
        self.assertAlmostEqual(present(features['station_distance_km']), 1.243, places=3)
        self.assertEqual(sorted(features['place_distances_km']), ['Gym', 'Work'])
        self.assertAlmostEqual(features['place_distances_km']['Work'], distance_km(HERE, (41.8788764, -87.6359149)))

    def test_without_coordinates_stations_or_places_the_distances_are_unknown(self) -> None:
        surroundings = Surroundings((CALIFORNIA,), (('Work', 41.8788764, -87.6359149),))
        for features in (code_features(home('1 Example St, Chicago'), facts(latitude=None, longitude=None), surroundings),
                         code_features(home('1 Example St, Chicago'), None, surroundings),
                         code_features(home('1 Example St, Chicago'), facts())):
            self.assertEqual((features['nearest_station'], features['station_distance_km'], features['place_distances_km']), (None, None, {}))


class StoredCodeFeatureTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path: Path = Path(directory.name) / 'listings.sqlite3'
        self.store: ListingStore = ListingStore(self.path)

    def save(self, listing: Listing, facts: Facts) -> str:
        fingerprint = self.store.upsert_listing(listing)['content_fingerprint']
        self.store.analyze(listing['id'], fingerprint, {'facts': facts, 'must_haves': {}})
        return fingerprint

    def test_features_are_stored_when_a_listing_is_saved_and_recomputed_when_its_content_changes(self) -> None:
        first = self.save(home('3835 W Wrightwood Ave #G, Chicago, IL 60647', price=2195), facts(unit(price=2195, size_sqft=1035)))
        features = self.store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertEqual(features['fingerprint'], first)
        self.assertEqual((features['monthly_price'], features['unit_level'], features['latitude']), (2195, 'garden', 41.927895))
        self.assertAlmostEqual(present(features['price_per_sqft']), 2195 / 1035)
        self.assertEqual((features['first_seen'], features['days_since_first_seen']), ('2026-10-20', 5))
        second = self.save(home('3835 W Wrightwood Ave #2F, Chicago, IL 60647', price=2300), facts(unit(price=2300, size_sqft=None)))
        features = self.store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertNotEqual(second, first)
        self.assertEqual((features['fingerprint'], features['monthly_price'], features['unit_floor'], features['price_per_sqft']),
                         (second, 2300, 2, None))

    def test_a_listing_saved_before_its_facts_has_features_from_the_listing_alone(self) -> None:
        _ = self.store.upsert_listing(home('12 Example St #G, Chicago', price=1800))
        features = self.store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertEqual((features['monthly_price'], features['unit_level'], features['latitude']), (1800, 'garden', None))

    def test_a_store_from_before_code_features_gets_them_for_every_listing_when_opened(self) -> None:
        older = Path(self.path.parent) / 'older.sqlite3'
        data = home('2326 N Sacramento Ave #Basement, Chicago, IL 60647')
        fingerprint = content_fingerprint(data)
        with closing(sqlite3.connect(older)) as connection, connection:
            for index, migration in enumerate(MIGRATIONS[:MIGRATIONS.index(code_derived_features)]):
                migration(connection)
                _ = connection.execute(f'PRAGMA user_version = {index + 1}')
            _ = connection.execute('INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)',
                                   (data['id'], json.dumps(data), 'available', data['first_seen'], fingerprint))
            _ = connection.execute('INSERT INTO ranking (listing_id, fingerprint, features) VALUES (?, ?, ?)',
                                   (data['id'], fingerprint, json.dumps({'facts': facts(unit(price=1700, size_sqft=None))})))
        store = ListingStore(older)
        self.assertEqual(store.schema_version, len(MIGRATIONS))
        features = store.code_features(today=date(2026, 10, 25))['zillow:1']
        self.assertEqual((features['fingerprint'], features['monthly_price'], features['unit_level']), (fingerprint, 1700, 'garden'))


class StoredDistanceTests(StoredCodeFeatureTests):
    @override
    def setUp(self) -> None:
        super().setUp()
        _ = self.save(home('3835 W Wrightwood Ave, Chicago, IL 60647'), facts())
        _ = self.store.upsert_listing(listing(id='zillow:2', address='12 Example St, Chicago'))

    def distances(self, listing_id: str = 'zillow:1') -> tuple[str | None, float | None, dict[str, float]]:
        features = self.store.code_features()[listing_id]
        return features['nearest_station'], features['station_distance_km'], features['place_distances_km']

    def test_saved_stations_and_geocoded_places_give_every_listing_its_distances(self) -> None:
        self.assertEqual(self.distances(), (None, None, {}))
        self.store.save_stations(DEFAULT_TRANSIT_FEED, STATIONS)
        self.assertEqual(self.distances()[:2], ('Logan Square', distance_km(HERE, LOGAN_SQUARE[1:])))
        self.store.save_geocodes({WORK['address']: WILLIS_TOWER})
        _ = self.store.update_settings(places=[WORK])
        self.assertEqual(self.distances()[2], {'Work': distance_km(HERE, (WILLIS_TOWER['latitude'], WILLIS_TOWER['longitude']))})
        self.assertEqual(self.distances('zillow:2'), (None, None, {}))
        _ = self.store.update_settings(places=[])
        self.assertEqual(self.distances()[2], {})
        self.assertEqual(self.store.ranking_inputs()['listings'][0]['code']['place_distances_km'], {})

    def test_a_listing_saved_later_gets_its_distances_when_it_is_saved(self) -> None:
        self.store.save_stations(DEFAULT_TRANSIT_FEED, STATIONS)
        self.store.save_geocodes({WORK['address']: WILLIS_TOWER})
        _ = self.store.update_settings(places=[WORK])
        _ = self.save(home('2326 N Sacramento Ave, Chicago, IL 60647'), facts(latitude=41.9235, longitude=-87.7027))
        nearest, distance, places = self.distances()
        self.assertEqual(nearest, 'California')
        self.assertAlmostEqual(present(distance), distance_km((41.9235, -87.7027), CALIFORNIA[1:]))
        self.assertEqual(list(places), ['Work'])

    def test_stations_count_only_for_the_transit_feed_they_were_fetched_from(self) -> None:
        self.store.save_stations(DEFAULT_TRANSIT_FEED, STATIONS)
        self.assertEqual(len(self.store.transit_feed()['stations']), 2)
        _ = self.store.update_settings(transit_feed='https://transit.example/gtfs.zip')
        self.assertEqual(self.store.transit_feed(), {'url': 'https://transit.example/gtfs.zip', 'fetched_at': None, 'stations': []})
        self.assertEqual(self.distances()[:2], (None, None))
        self.store.save_stations('https://transit.example/gtfs.zip', STATIONS[1:])
        self.assertEqual(self.distances()[0], 'California')

    def test_a_place_without_coordinates_is_reported_and_has_no_distance(self) -> None:
        _ = self.store.update_settings(places=[WORK, {'name': 'Gym', 'address': 'First Ascent Avondale, Chicago'}])
        self.store.save_geocodes({WORK['address']: WILLIS_TOWER})
        _ = self.store.update_settings(places=[WORK, {'name': 'Gym', 'address': 'First Ascent Avondale, Chicago'}])
        self.assertEqual(self.store.places_without_coordinates(), ['Gym'])
        self.assertEqual(list(self.distances()[2]), ['Work'])
        _ = self.store.update_settings(places=[WORK])
        self.assertEqual(self.store.places_without_coordinates(), [])

    def test_places_at_one_address_share_its_coordinates(self) -> None:
        self.store.save_geocodes({WORK['address']: WILLIS_TOWER})
        _ = self.store.update_settings(places=[WORK, {'name': 'Gym', 'address': WORK['address']}])
        self.assertEqual(self.store.places_without_coordinates(), [])
        self.assertEqual(self.distances()[2]['Gym'], self.distances()[2]['Work'])

    def test_a_store_from_before_distances_keeps_each_place_as_its_name_and_address(self) -> None:
        older = Path(self.path.parent) / 'older.sqlite3'
        data = home('3835 W Wrightwood Ave, Chicago, IL 60647')
        fingerprint = content_fingerprint(data)
        with closing(sqlite3.connect(older)) as connection, connection:
            for index, migration in enumerate(MIGRATIONS[:MIGRATIONS.index(distances)]):
                migration(connection)
                _ = connection.execute(f'PRAGMA user_version = {index + 1}')
            _ = connection.execute('INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES (?, ?, ?, ?, ?)',
                                   (data['id'], json.dumps(data), 'available', data['first_seen'], fingerprint))
            _ = connection.execute('INSERT INTO ranking (listing_id, fingerprint, features) VALUES (?, ?, ?)',
                                   (data['id'], fingerprint, json.dumps({'facts': facts()})))
            _ = connection.execute('''INSERT INTO code_features (listing_id, fingerprint, monthly_price, photo_count, latitude, longitude)
                VALUES (?, ?, 2100, 3, ?, ?)''', (data['id'], fingerprint, *HERE))
            _ = connection.execute("INSERT INTO settings VALUES ('places', ?)", (json.dumps([
                {**WORK, 'travel_mode': 'transit', 'longest_trip_minutes': 40, 'visits_per_week': 5}]),))
        store = ListingStore(older)
        self.assertEqual(store.schema_version, len(MIGRATIONS))
        self.assertEqual(store.settings().get('places'), [WORK])
        self.assertEqual(store.places_without_coordinates(), ['Work'])
        self.assertEqual(store.missing_settings(('places',)), [])
        features = store.code_features()['zillow:1']
        self.assertEqual((features['monthly_price'], features['nearest_station'], features['place_distances_km']), (2100, None, {}))
        store.save_geocodes({WORK['address']: WILLIS_TOWER})
        _ = store.update_settings(places=[WORK])
        self.assertEqual(list(store.code_features()['zillow:1']['place_distances_km']), ['Work'])


if __name__ == '__main__':
    _ = unittest.main()
