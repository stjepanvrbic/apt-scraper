import csv
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))

import geodata  # noqa: E402
from geodata import (NOMINATIM, NOMINATIM_INTERVAL_SECONDS, USER_AGENT, SourceUnavailable, fetch_stations, found_place, geocode,  # noqa: E402
                     rail_route_type, rapid_transit_stations, search_place)

WILLIS_TOWER = [{'place_id': 1, 'lat': '41.8788764', 'lon': '-87.6359149', 'display_name': 'Willis Tower, 233 South Wacker Drive, Chicago'}]


def table(rows):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator='\n').writerows(rows)
    return buffer.getvalue()


def feed_stops(latitude):
    """A stops.txt whose stations have the given latitude."""
    return table([['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'location_type', 'parent_station'],
                  ['40570', 'California', latitude, '-87.69689', '1', ''], ['40590', 'Damen', latitude, '-87.677437', '1', ''],
                  ['30001', 'California', latitude, '-87.69689', '0', '40570'], ['30002', 'Damen', latitude, '-87.67744', '0', '40590'],
                  ['30003', 'California', latitude, '-87.69690', '0', '40570'], ['30004', 'Western', latitude, '-87.687364', '0', ''],
                  ['1501', 'Bus', latitude, '-87.6874', '0', ''], ['1502', 'Bus', latitude, '-87.6775', '0', ''],
                  ['7001', 'Tram stop', latitude, '-87.63', '', '']])


def feed(**overrides):
    """A small GTFS feed of one rail line with platforms under two stations and one platform without a station, a bus
    route whose stops are not rapid transit, and a station only the bus serves."""
    files = {
        'routes.txt': table([['route_id', 'route_short_name', 'route_type'], ['Blue', '', '1'], ['56', '56', '3'], ['Tram', '', '900']]),
        'trips.txt': table([['route_id', 'service_id', 'trip_id'], ['Blue', 'weekday', 'blue-1'], ['Blue', 'weekday', 'blue-2'],
                            ['56', 'weekday', 'bus-1'], ['Tram', 'weekday', 'tram-1']]),
        'stop_times.txt': table([['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence'],
                                 ['blue-1', '08:00:00', '08:00:00', '30001', '1'], ['blue-1', '08:03:00', '08:03:00', '30002', '2'],
                                 ['blue-2', '08:10:00', '08:10:00', '30003', '1'], ['blue-2', '08:13:00', '08:13:00', '30004', '2'],
                                 ['bus-1', '08:00:00', '08:00:00', '1501', '1'], ['bus-1', '08:05:00', '08:05:00', '1502', '2'],
                                 ['tram-1', '09:00:00', '09:00:00', '7001', '1']]),
        'stops.txt': '﻿' + table([
            ['stop_id', 'stop_name', 'stop_lat', 'stop_lon', 'location_type', 'parent_station'],
            ['40570', 'California', '41.921939', '-87.69689', '1', ''],
            ['40590', 'Damen', '41.909744', '-87.677437', '1', ''],
            ['41000', 'Bus Terminal', '41.95', '-87.70', '1', ''],
            ['30001', 'California (O\'Hare-bound)', '41.92194', '-87.69689', '0', '40570'],
            ['30002', 'Damen (O\'Hare-bound)', '41.90974', '-87.67744', '0', '40590'],
            ['30003', 'California (Forest Park-bound)', '41.92194', '-87.69690', '0', '40570'],
            ['30004', 'Western', '41.916157', '-87.687364', '0', ''],
            ['1501', 'Milwaukee & Western', '41.91620', '-87.68740', '0', '41000'],
            ['1502', 'Milwaukee & Damen', '41.90980', '-87.67750', '0', ''],
            ['7001', 'Tram stop', '41.88', '-87.63', '', ''],
        ]),
        **overrides,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, text in files.items():
            archive.writestr(name, text.encode('utf-8'))
    return buffer.getvalue()


class Answer(io.BytesIO):
    """An HTTP answer as urlopen returns it, a context manager over the body."""


def answering(body, requests=None):
    def opener(url):
        if requests is not None:
            requests.append(url)
        return Answer(body)
    return opener


class GeocodingTests(unittest.TestCase):
    def test_the_first_result_gives_the_coordinates_and_the_name_nominatim_found(self):
        self.assertEqual(found_place(WILLIS_TOWER), {'latitude': 41.8788764, 'longitude': -87.6359149,
                                                     'found': 'Willis Tower, 233 South Wacker Drive, Chicago'})

    def test_no_result_is_a_place_not_found(self):
        self.assertIsNone(found_place([]))

    def test_an_answer_unlike_the_documented_one_is_the_service_failing_not_the_place(self):
        for answer in ({'error': 'Unable to geocode'}, [{'display_name': 'No coordinates'}], [{'lat': 'north', 'lon': '1', 'display_name': 'X'}],
                       [{'lat': '91', 'lon': '1', 'display_name': 'X'}], [{'lat': 'nan', 'lon': '1', 'display_name': 'X'}]):
            with self.subTest(answer=answer), self.assertRaises(SourceUnavailable):
                found_place(answer)

    def test_a_search_asks_for_the_one_best_result_of_the_address_as_the_user_said_it(self):
        requests = []
        self.assertEqual(search_place('233 S Wacker Dr, Chicago, IL', answering(json.dumps(WILLIS_TOWER).encode(), requests))['latitude'], 41.8788764)
        url = urlsplit(requests[0])
        self.assertEqual(f'{url.scheme}://{url.netloc}{url.path}', NOMINATIM)
        self.assertEqual(parse_qs(url.query), {'q': ['233 S Wacker Dr, Chicago, IL'], 'format': ['jsonv2'], 'limit': ['1']})

    def test_an_unreachable_or_garbled_service_is_reported_as_unavailable(self):
        def unreachable(url):
            raise OSError('Network is unreachable')
        for opener in (unreachable, answering(b'<html>Bandwidth limit exceeded</html>')):
            with self.subTest(opener=opener), self.assertRaises(SourceUnavailable):
                search_place('Loop, Chicago', opener)

    def test_requests_name_this_application_and_never_the_users_contact_details(self):
        with mock.patch.object(geodata.urllib.request, 'urlopen') as urlopen:
            geodata.open_url(NOMINATIM)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header('User-agent'), USER_AGENT)
        self.assertIn('apt-scraper', USER_AGENT)
        self.assertNotIn('@', USER_AGENT)

    def test_addresses_are_searched_one_at_a_time_a_second_apart_and_the_ones_not_found_are_named(self):
        events = []

        def search(address):
            events.append(('search', address))
            return None if address == 'Nowhere' else {'latitude': 1.0, 'longitude': 2.0, 'found': address}
        found, missing, unavailable = geocode(['Work address', 'Nowhere', 'Gym address'], search, lambda seconds: events.append(('sleep', seconds)))
        self.assertEqual(events, [('search', 'Work address'), ('sleep', NOMINATIM_INTERVAL_SECONDS), ('search', 'Nowhere'),
                                  ('sleep', NOMINATIM_INTERVAL_SECONDS), ('search', 'Gym address')])
        self.assertEqual(NOMINATIM_INTERVAL_SECONDS, 1.0)
        self.assertEqual(sorted(found), ['Gym address', 'Work address'])
        self.assertEqual((missing, unavailable), (['Nowhere'], None))
        self.assertEqual(geocode([], search, lambda seconds: self.fail('slept without a request')), ({}, [], None))

    def test_a_service_failing_partway_stops_the_lookups_and_keeps_what_was_found(self):
        failure = SourceUnavailable('Nominatim could not be reached: timed out')

        def search(address):
            if address == 'Gym address':
                raise failure
            return {'latitude': 1.0, 'longitude': 2.0, 'found': address}
        found, missing, unavailable = geocode(['Work address', 'Gym address', 'Home address'], search, lambda seconds: None)
        self.assertEqual((list(found), missing, unavailable), (['Work address'], [], failure))


class StationTests(unittest.TestCase):
    def test_stations_are_the_stops_rail_routes_serve_counted_at_their_parent_station(self):
        with zipfile.ZipFile(io.BytesIO(feed())) as archive:
            stations = rapid_transit_stations(archive)
        self.assertEqual(stations, [
            {'id': '30004', 'name': 'Western', 'latitude': 41.916157, 'longitude': -87.687364},
            {'id': '40570', 'name': 'California', 'latitude': 41.921939, 'longitude': -87.69689},
            {'id': '40590', 'name': 'Damen', 'latitude': 41.909744, 'longitude': -87.677437},
            {'id': '7001', 'name': 'Tram stop', 'latitude': 41.88, 'longitude': -87.63},
        ])

    def test_rail_metro_light_rail_and_monorail_count_and_buses_ferries_and_lifts_do_not(self):
        self.assertEqual([value for value in ('0', '1', '2', '3', '4', '5', '6', '7', '11', '12') if rail_route_type(value)], ['0', '1', '2', '12'])
        self.assertEqual([value for value in ('100', '109', '400', '401', '700', '702', '715', '900', '1000', '') if rail_route_type(value)],
                         ['100', '109', '400', '401', '900'])

    def test_a_feed_is_downloaded_whole_and_read_for_its_stations(self):
        requests = []
        stations = fetch_stations('https://transit.example/gtfs.zip', answering(feed(), requests))
        self.assertEqual(requests, ['https://transit.example/gtfs.zip'])
        self.assertEqual(len(stations), 4)

    def test_a_download_that_fails_or_is_not_a_gtfs_feed_is_reported(self):
        def unreachable(url):
            raise OSError('timed out')
        for opener in (unreachable, answering(b'<html>Not found</html>'), answering(feed(**{'stop_times.txt': 'trip_id,arrival_time\n'})),
                       answering(feed(**{'stops.txt': 'stop_id,stop_name\n'})), answering(feed(**{'stop_times.txt': ''})),
                       answering(feed(**{'stops.txt': feed_stops(latitude='nan')})), answering(feed(**{'stops.txt': feed_stops(latitude='97.5')})),
                       answering(feed(**{'stops.txt': 'stop_id,"stop_name\n'}))):
            with self.subTest(opener=opener), self.assertRaisesRegex(SourceUnavailable, 'transit.example'):
                fetch_stations('https://transit.example/gtfs.zip', opener)


if __name__ == '__main__':
    unittest.main()
