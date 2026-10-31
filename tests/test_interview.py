import copy
import contextlib
import functools
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zoneinfo
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, Unpack, cast, override
from unittest.mock import patch

from code_features import RankingFeatures
from geodata import Location
from shapes import Criteria, LocationPriorities, Place, Preferences, Settings, ViewingAvailability
from store import CriteriaApplied, CriteriaVerdict, Geocode, ListingStore, TimeZoneDataMissing
from test_gallery_store import listing

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/interview/scripts/settings.py'
sys.path.insert(0, str(SCRIPT.parent))

import geodata  # noqa: E402
import settings as settings_script  # noqa: E402
from geodata import ATTRIBUTION, SourceUnavailable  # noqa: E402
from settings import Located, SaveReport, StatusReport  # noqa: E402


class Complete(TypedDict):
    """The settings a complete interview saves."""
    criteria: Criteria
    preferences: Preferences
    listing_sites: list[str]
    viewing_availability: ViewingAvailability
    places: list[Place]
    location_priorities: LocationPriorities


class JustCriteria(TypedDict, total=False):
    """A settings update that may carry only the criteria."""
    criteria: Criteria


class Saved(TypedDict):
    """The part of what settings.py save prints for places that the tests read."""
    places: list[Located]
    attribution: str


COMPLETE: Complete = {
    'criteria': {
        'location': ['Logan Square, Chicago', 'Wicker Park, Chicago'],
        'price': {'min': None, 'max': 2400, 'currency': 'USD', 'period': 'month'},
        'size': {'min': 700, 'max': None, 'unit': 'sqft'},
        'rooms': {'min': 2, 'max': None},
        'move_in': '2026-12-01',
        'other': ['Pets allowed'],
    },
    'preferences': {'likes': ['Quiet street', 'Lots of daylight'], 'dislikes': ['Ground floor']},
    'listing_sites': ['https://www.zillow.com', 'https://www.apartments.com'],
    'viewing_availability': {
        'timezone': 'America/Chicago',
        'weekly': [{'day': 'saturday', 'start': '10:00', 'end': '14:00'}],
        'unavailable': [{'from': '2026-12-20', 'to': '2027-01-03'}],
    },
    'places': [
        {'name': 'Work', 'address': '233 S Wacker Dr, Chicago, IL 60606'},
        {'name': 'Climbing gym', 'address': 'First Ascent Avondale, Chicago'},
    ],
    'location_priorities': {
        'places': 'high',
        'rapid_transit': 'medium',
        'areas': {'Logan Square, Chicago': 'high', 'Wicker Park, Chicago': 'low'},
    },
}

PLACE: Place = {'name': 'Work', 'address': 'Loop, Chicago'}
LOCATIONS: dict[str, Geocode] = {
    '233 S Wacker Dr, Chicago, IL 60606': {'latitude': 41.8788764, 'longitude': -87.6359149, 'found': 'Willis Tower, 233 South Wacker Drive, Chicago'},
    'First Ascent Avondale, Chicago': {'latitude': 41.9443, 'longitude': -87.7093, 'found': 'First Ascent Avondale, 3516 North Spaulding Avenue, Chicago'},
    '1 N State St, Chicago, IL 60602': {'latitude': 41.8822, 'longitude': -87.6278, 'found': '1 North State Street, Chicago'},
}


WITHOUT_TIME_ZONE_DATA = '''
import runpy, sys, zoneinfo
sys.modules['tzdata'] = None
zoneinfo.reset_tzpath([])
zoneinfo.ZoneInfo.clear_cache()
script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name='__main__')
'''


def settings(**changes: Unpack[Settings]) -> Complete:
    return {**copy.deepcopy(COMPLETE), **changes}


class SettingsStoreTests(unittest.TestCase):
    judged: list[Mapping[str, object] | None]

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path: Path = Path(directory.name) / 'listings.sqlite3'
        self.store: ListingStore = ListingStore(self.path)
        self.store.save_geocodes(LOCATIONS)

    def test_new_store_is_missing_every_required_setting(self) -> None:
        self.assertEqual(self.store.missing_settings(), [
            'criteria.location', 'criteria.price', 'criteria.size', 'criteria.rooms', 'criteria.move_in', 'criteria.other',
            'preferences.likes', 'preferences.dislikes', 'listing_sites',
            'viewing_availability.timezone', 'viewing_availability.weekly',
            'places', 'location_priorities.rapid_transit'])

    def test_partial_answers_persist_and_only_unanswered_items_stay_missing(self) -> None:
        _ = self.store.update_settings(criteria={'location': ['Logan Square, Chicago'], 'price': {'min': None, 'max': 2400, 'currency': 'USD', 'period': 'month'}})
        _ = self.store.update_settings(preferences={'likes': ['Quiet street']}, listing_sites=[])
        reopened = ListingStore(self.path)
        self.assertEqual(reopened.settings().get('criteria', {}).get('price', {}).get('max'), 2400)
        self.assertEqual(reopened.missing_settings(), [
            'criteria.size', 'criteria.rooms', 'criteria.move_in', 'criteria.other', 'preferences.dislikes',
            'listing_sites', 'viewing_availability.timezone', 'viewing_availability.weekly',
            'places', 'location_priorities.rapid_transit', 'location_priorities.areas'])

    def test_complete_settings_have_nothing_missing_and_no_limit_counts_as_answered(self) -> None:
        values = settings()
        values['criteria']['size'] = {'min': None, 'max': None}
        values['criteria']['move_in'] = None
        values['criteria']['other'] = []
        values['preferences'] = {'likes': [], 'dislikes': []}
        _ = self.store.update_settings(**values)
        self.assertEqual(self.store.missing_settings(), [])

    def test_empty_availability_and_sites_are_not_answers(self) -> None:
        _ = self.store.update_settings(listing_sites=[], viewing_availability={'timezone': 'America/Chicago', 'weekly': []})
        self.assertIn('listing_sites', self.store.missing_settings())
        self.assertIn('viewing_availability.weekly', self.store.missing_settings())
        self.assertNotIn('viewing_availability.timezone', self.store.missing_settings())

    def test_no_places_is_an_answer_that_needs_no_priority_for_places(self) -> None:
        _ = self.store.update_settings(**settings(places=[], location_priorities={'rapid_transit': 'high', 'areas': COMPLETE['location_priorities'].get('areas', {})}))
        self.assertEqual(self.store.settings().get('places'), [])
        self.assertEqual(self.store.missing_settings(), [])

    def test_a_saved_place_without_coordinates_is_reported_apart_from_what_the_interview_needs(self) -> None:
        _ = self.store.update_settings(**settings(places=[*COMPLETE['places'], {**PLACE, 'name': 'Office'}]))
        self.assertEqual((self.store.missing_settings(), self.store.places_without_coordinates()), ([], ['Office']))
        _ = self.store.update_settings(places=COMPLETE['places'])
        self.assertEqual(self.store.places_without_coordinates(), [])

    def test_places_need_a_priority_and_every_area_of_the_criteria_needs_one(self) -> None:
        _ = self.store.update_settings(**settings(location_priorities={'rapid_transit': 'low', 'areas': {'Logan Square, Chicago': 'high'}}))
        self.assertEqual(self.store.missing_settings(), ['location_priorities.places', 'location_priorities.areas'])

    def test_an_interview_completed_before_places_existed_is_missing_only_places_and_location_priorities(self) -> None:
        _ = self.store.update_settings(criteria=COMPLETE['criteria'], preferences=COMPLETE['preferences'], listing_sites=COMPLETE['listing_sites'],
                                       viewing_availability=COMPLETE['viewing_availability'])
        self.assertEqual(self.store.missing_settings(), ['places', 'location_priorities.rapid_transit', 'location_priorities.areas'])

    def test_missing_settings_can_be_limited_to_the_settings_a_reader_uses(self) -> None:
        _ = self.store.update_settings(criteria={'location': ['Logan Square, Chicago']}, listing_sites=COMPLETE['listing_sites'])
        self.assertEqual(self.store.missing_settings(('criteria', 'listing_sites')),
                         ['criteria.price', 'criteria.size', 'criteria.rooms', 'criteria.move_in', 'criteria.other'])
        self.assertEqual(self.store.missing_settings(('listing_sites', 'places')), ['places'])

    def test_a_changed_place_replaces_the_stored_one(self) -> None:
        _ = self.store.update_settings(**settings())
        places = copy.deepcopy(COMPLETE['places'])
        places[0]['address'] = '1 N State St, Chicago, IL 60602'
        _ = self.store.update_settings(places=places)
        saved = ListingStore(self.path).settings()
        self.assertEqual(saved.get('places'), places)
        self.assertEqual(saved.get('location_priorities'), COMPLETE['location_priorities'])

    def test_areas_removed_from_the_criteria_lose_their_priority(self) -> None:
        _ = self.store.update_settings(**settings())
        criteria: Criteria = {**COMPLETE['criteria'], 'location': ['Logan Square, Chicago', 'Avondale, Chicago']}
        _ = self.store.update_settings(criteria=criteria)
        self.assertEqual(self.store.settings().get('location_priorities'),
                         {'places': 'high', 'rapid_transit': 'medium', 'areas': {'Logan Square, Chicago': 'high'}})
        self.assertEqual(self.store.missing_settings(), ['location_priorities.areas'])

    def test_a_priority_for_an_area_the_criteria_do_not_name_is_rejected(self) -> None:
        _ = self.store.update_settings(criteria=COMPLETE['criteria'])
        cases: list[JustCriteria] = [{}, {'criteria': COMPLETE['criteria']}]
        for criteria in cases:
            with self.subTest(criteria=criteria), self.assertRaisesRegex(ValueError, 'Avondale'):
                _ = self.store.update_settings(**criteria, location_priorities={'rapid_transit': 'low', 'areas': {'Avondale, Chicago': 'high'}})
        self.assertNotIn('location_priorities', self.store.settings())
        _ = self.store.update_settings(criteria={**COMPLETE['criteria'], 'location': ['Avondale, Chicago']},
                                       location_priorities={'rapid_transit': 'low', 'areas': {'Avondale, Chicago': 'high'}})
        self.assertEqual(self.store.settings().get('location_priorities', {}).get('areas'), {'Avondale, Chicago': 'high'})

    def test_unchanged_settings_are_preserved_by_partial_updates(self) -> None:
        _ = self.store.update_settings(**settings())
        _ = self.store.update_settings(preferences={'likes': ['Balcony'], 'dislikes': []})
        saved = ListingStore(self.path).settings()
        self.assertEqual(saved.get('preferences'), {'likes': ['Balcony'], 'dislikes': []})
        self.assertEqual(saved.get('listing_sites'), COMPLETE['listing_sites'])
        self.assertEqual(saved.get('criteria'), COMPLETE['criteria'])

    def judge_by_price(self, criteria: Criteria, features: Mapping[str, object] | None) -> CriteriaVerdict:
        """A judge of the features these tests store, {price}, against the criteria's price limit."""
        self.judged.append(features)
        if features is None:
            return 'pending'
        price, limit = features['price'], criteria.get('price', {}).get('max')
        return 'outside' if isinstance(price, int) and limit is not None and price > limit else 'inside'

    def test_criteria_are_applied_to_every_stored_listing_in_the_step_that_saves_them(self) -> None:
        self.judged = []
        for identifier, price in [('example:100', 2100), ('example:200', 2900), ('example:300', None)]:
            fingerprint = self.store.upsert_listing(listing(id=identifier))['content_fingerprint']
            if price:
                self.store.analyze(identifier, fingerprint, {'price': price})
        revision = self.store.revision()
        criteria = copy.deepcopy(COMPLETE['criteria'])
        result = self.store.update_settings(criteria=criteria, judge=self.judge_by_price)
        self.assertEqual(result, CriteriaApplied({'inside': 1, 'outside': 1, 'pending': 1}, hidden=['example:200'], awaiting=['example:300'], shown=[]))
        self.assertEqual(self.judged, [{'price': 2100}, {'price': 2900}, None])
        self.assertGreater(self.store.revision(), revision)
        self.assertEqual([item['id'] for item in self.store.browse('Rankings')['items']], ['example:100'])
        self.assertEqual(ListingStore(self.path).settings().get('criteria'), criteria)
        criteria['price'] = {**criteria.get('price', {}), 'max': 3000}
        self.assertEqual(self.store.update_settings(criteria=criteria, judge=self.judge_by_price),
                         CriteriaApplied({'inside': 2, 'pending': 1}, hidden=[], awaiting=[], shown=['example:200']))
        self.assertEqual(self.store.browse('Rankings')['total'], 2)
        revision = self.store.revision()
        self.assertEqual(self.store.apply_criteria(self.judge_by_price), CriteriaApplied({'inside': 2, 'pending': 1}, hidden=[], awaiting=[], shown=[]))
        self.assertEqual(self.store.revision(), revision)

    def test_stored_criteria_can_be_applied_again_and_nothing_is_judged_without_criteria(self) -> None:
        self.judged = []
        fingerprint = self.store.upsert_listing(listing())['content_fingerprint']
        self.assertIsNone(self.store.apply_criteria(self.judge_by_price))
        self.store.analyze('example:100', fingerprint, {'price': 2100})
        _ = self.store.update_settings(criteria=COMPLETE['criteria'], judge=self.judge_by_price)
        self.store.analyze('example:100', fingerprint, {'price': 2900})
        self.assertEqual(self.store.browse('Rankings')['total'], 1)
        self.assertEqual(self.store.apply_criteria(self.judge_by_price), CriteriaApplied({'outside': 1}, hidden=['example:100'], awaiting=[], shown=[]))
        self.assertEqual(self.store.browse('Rankings')['total'], 0)

    def test_criteria_with_stored_listings_are_saved_only_with_a_judge(self) -> None:
        _ = self.store.upsert_listing(listing())
        with self.assertRaisesRegex(ValueError, 'judge'):
            _ = self.store.update_settings(criteria=COMPLETE['criteria'])
        self.assertEqual(self.store.settings(), {})

    def test_a_failing_judge_saves_nothing(self) -> None:
        _ = self.store.upsert_listing(listing())
        def broken(_criteria: Criteria, _features: RankingFeatures | None) -> CriteriaVerdict:
            raise RuntimeError('judge failed')
        with self.assertRaisesRegex(RuntimeError, 'judge failed'):
            _ = self.store.update_settings(criteria=COMPLETE['criteria'], preferences=COMPLETE['preferences'], judge=broken)
        self.assertEqual(self.store.settings(), {})

    def test_malformed_settings_are_rejected_and_nothing_is_saved(self) -> None:
        cases: list[tuple[str, object]] = [
            ('criteria', {'location': []}),
            ('criteria', {'location': ['  ']}),
            ('criteria', {'budget': 2000}),
            ('criteria', {'price': {'max': 2000}}),
            ('criteria', {'price': {'max': 2000, 'currency': 'usd', 'period': 'month'}}),
            ('criteria', {'price': {'max': 2000, 'currency': 'USD', 'period': 'fortnight'}}),
            ('criteria', {'price': {'min': 3000, 'max': 2000, 'currency': 'USD', 'period': 'month'}}),
            ('criteria', {'price': {'max': -1, 'currency': 'USD', 'period': 'month'}}),
            ('criteria', {'price': {'max': True, 'currency': 'USD', 'period': 'month'}}),
            ('criteria', {'size': {'min': 50}}),
            ('criteria', {'size': {'min': 50, 'unit': 'acres'}}),
            ('criteria', {'rooms': {'min': '2'}}),
            ('criteria', {'move_in': 'next month'}),
            ('criteria', {'other': 'Pets allowed'}),
            ('preferences', {'likes': 'Quiet'}),
            ('preferences', {'favorites': []}),
            ('listing_sites', ['zillow.com']),
            ('listing_sites', ['https://www.zillow.com', 'https://www.zillow.com']),
            ('viewing_availability', {'timezone': 'Chicago'}),
            ('viewing_availability', {'weekly': [{'day': 'someday', 'start': '10:00', 'end': '12:00'}]}),
            ('viewing_availability', {'weekly': [{'day': 'monday', 'start': '18:00', 'end': '17:00'}]}),
            ('viewing_availability', {'weekly': [{'day': 'monday', 'start': '6pm', 'end': '8pm'}]}),
            ('viewing_availability', {'unavailable': [{'from': '2027-01-03', 'to': '2026-12-20'}]}),
            ('viewing_availability', {'notes': 'Evenings'}),
            ('places', PLACE),
            ('places', [{**PLACE, 'coordinates': [41.88, -87.63]}]),
            ('places', [{**PLACE, 'name': ' '}]),
            ('places', [{**PLACE, 'address': ''}]),
            ('places', [{**PLACE, 'travel_mode': 'transit'}]),
            ('places', [{**PLACE, 'longest_trip_minutes': 30}]),
            ('places', [{**PLACE, 'visits_per_week': 5}]),
            ('places', [PLACE, {**PLACE, 'name': 'work '}]),
            ('location_priorities', {'rapid_transit': 'very'}),
            ('location_priorities', {'places': 3}),
            ('location_priorities', {'walkability': 'high'}),
            ('location_priorities', {'areas': ['Logan Square, Chicago']}),
            ('location_priorities', {'areas': {'Logan Square, Chicago': 'top'}}),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                _ = self.store.update_settings(**{name: value}, judge=None)
        self.assertEqual(self.store.settings(), {})

    def test_missing_time_zone_data_is_its_own_failure(self) -> None:
        self.addCleanup(zoneinfo.ZoneInfo.clear_cache)
        self.addCleanup(zoneinfo.reset_tzpath)
        with patch.dict(sys.modules, {name: None for name in [*sys.modules, 'tzdata'] if name.split('.')[0] == 'tzdata'}):
            zoneinfo.reset_tzpath([])
            zoneinfo.ZoneInfo.clear_cache()
            with self.assertRaisesRegex(TimeZoneDataMissing, 'tzdata'):
                _ = self.store.update_settings(viewing_availability=COMPLETE['viewing_availability'])
        self.assertEqual(self.store.settings(), {})


class SettingsScriptTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home: Path = Path(directory.name)
        self.path: Path = self.home / 'workspace' / 'gallery' / 'listings.sqlite3'
        ListingStore(self.path).save_geocodes(LOCATIONS)

    def run_script(self, *arguments: str, interpreter: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, 'HERMES_HOME': str(self.home)}
        return subprocess.run([sys.executable, *interpreter, str(SCRIPT), *arguments], capture_output=True, text=True, env=environment, timeout=60)

    def save(self, settings: str | Mapping[str, object], *arguments: str, interpreter: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
        file = self.home / 'cache' / 'scratch' / 'settings.json'
        file.parent.mkdir(parents=True, exist_ok=True)
        text = settings if isinstance(settings, str) else json.dumps(settings, ensure_ascii=False)
        _ = file.write_text(text, encoding='utf-8')
        result = self.run_script('save', str(file), *arguments, interpreter=interpreter)
        if result.returncode == 0:
            self.assertFalse(file.exists())
        else:
            self.assertEqual(file.read_text(encoding='utf-8'), text)
        return result

    def test_rejected_file_is_kept(self) -> None:
        result = self.save('Just some notes, not settings.')
        self.assertEqual(result.returncode, 1)
        self.assertIn('JSON', result.stderr)

    def test_files_outside_the_scratch_directory_are_refused_and_kept(self) -> None:
        _ = ListingStore(self.path).update_settings(preferences=COMPLETE['preferences'])
        before = self.path.read_bytes()
        outside = self.home / 'settings.json'
        _ = outside.write_text(json.dumps({'listing_sites': COMPLETE['listing_sites']}), encoding='utf-8')
        for file in (self.path, outside, self.home / 'cache' / 'scratch' / '..' / 'settings.json'):
            with self.subTest(file=file):
                result = self.run_script('save', str(file))
                self.assertEqual(result.returncode, 1)
                self.assertIn('scratch', result.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(outside.exists())
        self.assertNotIn('listing_sites', ListingStore(self.path).settings())

    def test_missing_time_zone_data_exits_with_its_own_code_and_keeps_the_file(self) -> None:
        result = self.save({'viewing_availability': COMPLETE['viewing_availability']}, interpreter=('-c', WITHOUT_TIME_ZONE_DATA))
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('tzdata', result.stderr)
        self.assertEqual(ListingStore(self.path).settings(), {})
        self.assertEqual(self.run_script('status', interpreter=('-c', WITHOUT_TIME_ZONE_DATA)).returncode, 3)

    def test_status_reports_missing_settings_until_the_interview_is_complete(self) -> None:
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3, result.stderr)
        report = cast(StatusReport, json.loads(result.stdout))
        self.assertFalse(report['complete'])
        self.assertIn('criteria.location', report['missing'])
        self.assertEqual(report['settings'], {})
        saved = self.save(COMPLETE)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(cast(SaveReport, json.loads(saved.stdout))['missing'], [])
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'complete': True, 'missing': [], 'places_without_coordinates': [], 'settings': COMPLETE})

    def test_status_reports_a_place_without_coordinates_as_incomplete(self) -> None:
        self.assertEqual(self.save(COMPLETE).returncode, 0)
        _ = ListingStore(self.path).update_settings(places=[*COMPLETE['places'], {**PLACE, 'name': 'Office'}])
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3, result.stderr)
        report = cast(StatusReport, json.loads(result.stdout))
        self.assertEqual((report['complete'], report['missing'], report['places_without_coordinates']), (True, [], ['Office']))

    def test_settings_live_in_the_real_store_of_the_profile(self) -> None:
        _ = self.save({'preferences': COMPLETE['preferences']})
        self.assertEqual(ListingStore(self.path).settings(), {'preferences': COMPLETE['preferences']})
        self.assertFalse((self.path.parent / 'sample.sqlite3').exists())

    def test_no_other_store_can_be_chosen(self) -> None:
        other = self.home / 'other.sqlite3'
        result = self.run_script('--database', str(other), 'status')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(other.exists())

    def test_quotes_and_letters_outside_ascii_survive(self) -> None:
        preferences: Preferences = {'likes': ["Children's playground nearby", 'Altbau in Köln'], 'dislikes': ['"Cozy" studios']}
        result = self.save({'preferences': preferences})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ListingStore(self.path).settings().get('preferences'), preferences)
        self.assertEqual(cast(StatusReport, json.loads(self.run_script('status').stdout))['settings'].get('preferences'), preferences)

    def test_missing_settings_file_is_reported(self) -> None:
        result = self.run_script('save', str(self.home / 'cache' / 'scratch' / 'absent.json'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('absent.json', result.stderr)

    def test_saving_reports_what_is_still_missing(self) -> None:
        result = self.save({'listing_sites': COMPLETE['listing_sites']})
        self.assertEqual(result.returncode, 0, result.stderr)
        report = cast(SaveReport, json.loads(result.stdout))
        self.assertEqual(report['saved'], ['listing_sites'])
        self.assertFalse(report['complete'])
        self.assertNotIn('listing_sites', report['missing'])

    def test_a_criteria_change_judges_stored_listings_by_their_features(self) -> None:
        store = ListingStore(self.path)
        for identifier, price, must_haves in [('example:100', 2100, {'Pets allowed': True}), ('example:200', 2900, {'Pets allowed': True}),
                                              ('example:300', 2000, {})]:
            fingerprint = store.upsert_listing(listing(id=identifier, price=price))['content_fingerprint']
            facts = {'areas': ['Logan Square, Chicago'], 'currency': 'USD',
                     'units': [{'price': price, 'period': 'month', 'bedrooms': 2, 'size_sqft': 850, 'available_on': None}]}
            store.analyze(identifier, fingerprint, {'facts': facts, 'must_haves': must_haves})
        _ = store.upsert_listing(listing(id='example:400', price=9000, rooms='1 bedroom'))
        result = self.save({'criteria': COMPLETE['criteria']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cast(SaveReport, json.loads(result.stdout)).get('criteria'), {'outside': 1, 'matching': 1, 'awaiting_judgment': 2, 'newly_hidden': 1, 'newly_awaiting_judgment': 2, 'shown_again': 0})
        self.assertEqual([item['id'] for item in store.browse('Rankings')['items']], ['example:100'])
        open_criteria: Criteria = {**COMPLETE['criteria'], 'price': {'min': None, 'max': None}, 'other': []}
        result = self.save({'criteria': open_criteria})
        self.assertEqual(cast(SaveReport, json.loads(result.stdout)).get('criteria'), {'outside': 0, 'matching': 3, 'awaiting_judgment': 1, 'newly_hidden': 0, 'newly_awaiting_judgment': 0, 'shown_again': 2})
        self.assertNotIn('example:400', [item['id'] for item in store.browse('Rankings')['items']])

    def test_a_criteria_change_counts_the_listings_it_hid_apart_from_those_already_hidden(self) -> None:
        store = ListingStore(self.path)
        for identifier, price, cats in [('example:100', 2000, True), ('example:200', 2200, False), ('example:300', 2500, True), ('example:400', 2500, None)]:
            fingerprint = store.upsert_listing(listing(id=identifier, price=price))['content_fingerprint']
            facts = {'areas': ['Logan Square, Chicago'], 'currency': 'USD',
                     'units': [{'price': price, 'period': 'month', 'bedrooms': 2, 'size_sqft': 850, 'available_on': None}]}
            store.analyze(identifier, fingerprint, {'facts': facts, 'must_haves': {'Cats allowed': cats}})
        criteria: Criteria = {**COMPLETE['criteria'], 'price': {'min': None, 'max': 2600, 'currency': 'USD', 'period': 'month'}, 'other': ['Cats allowed']}
        result = self.save({'criteria': criteria})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cast(SaveReport, json.loads(result.stdout)).get('criteria'),
                         {'outside': 1, 'matching': 3, 'awaiting_judgment': 0, 'newly_hidden': 1, 'newly_awaiting_judgment': 0, 'shown_again': 0})
        lower: Criteria = {**criteria, 'price': {**criteria.get('price', {}), 'max': 2300}}
        result = self.save({'criteria': lower})
        self.assertEqual(cast(SaveReport, json.loads(result.stdout)).get('criteria'),
                         {'outside': 3, 'matching': 1, 'awaiting_judgment': 0, 'newly_hidden': 2, 'newly_awaiting_judgment': 0, 'shown_again': 0})
        result = self.save({'criteria': {**criteria, 'other': ['Cats allowed', 'Balcony']}})
        self.assertEqual(cast(SaveReport, json.loads(result.stdout)).get('criteria'),
                         {'outside': 1, 'matching': 0, 'awaiting_judgment': 3, 'newly_hidden': 0, 'newly_awaiting_judgment': 1, 'shown_again': 0})
        result = self.save({'criteria': criteria})
        self.assertEqual(cast(SaveReport, json.loads(result.stdout)).get('criteria'),
                         {'outside': 1, 'matching': 3, 'awaiting_judgment': 0, 'newly_hidden': 0, 'newly_awaiting_judgment': 0, 'shown_again': 3})
        self.assertEqual(sorted(item['id'] for item in store.browse('Rankings')['items']), ['example:100', 'example:300', 'example:400'])

    def test_criteria_without_stored_listings_need_no_judgment(self) -> None:
        result = self.save({'criteria': COMPLETE['criteria']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ListingStore(self.path).settings().get('criteria'), COMPLETE['criteria'])

    def test_invalid_input_is_reported_and_saves_nothing(self) -> None:
        for text in ['not json', '[]', '{}', json.dumps({'criteria': {'budget': 1}, 'listing_sites': COMPLETE['listing_sites']}),
                     json.dumps({'search_radius': 5})]:
            with self.subTest(text=text):
                result = self.save(text)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.strip())
        self.assertEqual(ListingStore(self.path).settings(), {})

    def test_stored_listings_are_no_longer_listed_or_judged_by_hand(self) -> None:
        for arguments in [('listings',), ('save', str(self.home / 'cache' / 'scratch' / 'settings.json'), '--outside', 'example:100')]:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_script(*arguments).returncode, 2)

class PlaceGeocodingTests(unittest.TestCase):
    """settings.py save in this process, with Nominatim answered by the test."""

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home: Path = Path(directory.name)
        self.enterContext(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        self.store: ListingStore = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')
        _ = self.store.update_settings(criteria=COMPLETE['criteria'], location_priorities=COMPLETE['location_priorities'])
        self.searches: list[str] = []
        self.unavailable: set[str] = set()
        geocode = functools.partial(geodata.geocode, search=self.search, sleep=lambda seconds: None)
        patcher = patch.object(settings_script, 'geocode', geocode)
        _ = patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, address: str) -> Location | None:
        self.searches.append(address)
        if address in self.unavailable or '*' in self.unavailable:
            raise SourceUnavailable('Nominatim could not be reached: timed out')
        return LOCATIONS.get(address)

    def save(self, value: Mapping[str, object]) -> tuple[int, Saved | None, str]:
        file = self.home / 'cache' / 'scratch' / 'settings.json'
        file.parent.mkdir(parents=True, exist_ok=True)
        _ = file.write_text(json.dumps(value), encoding='utf-8')
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = settings_script.main(['save', str(file)])
        self.assertEqual(file.exists(), code != 0)
        return code, cast(Saved, json.loads(output.getvalue())) if code == 0 else None, errors.getvalue()

    def test_each_place_is_geocoded_once_when_saved_and_said_back_as_found(self) -> None:
        code, result, errors = self.save({'places': COMPLETE['places']})
        self.assertEqual(code, 0, errors)
        assert result is not None
        self.assertEqual(self.searches, [place['address'] for place in COMPLETE['places']])
        self.assertEqual(result['places'], [{**place, 'found': LOCATIONS[place['address']]['found']} for place in COMPLETE['places']])
        self.assertEqual(result['attribution'], ATTRIBUTION)
        self.assertEqual(self.store.geocodes([COMPLETE['places'][0]['address']]), {COMPLETE['places'][0]['address']: LOCATIONS[COMPLETE['places'][0]['address']]})
        moved: list[Place] = [{'name': 'Work', 'address': '1 N State St, Chicago, IL 60602'}, COMPLETE['places'][1]]
        self.assertEqual(self.save({'places': moved})[0], 0)
        self.assertEqual(self.searches[2:], ['1 N State St, Chicago, IL 60602'])
        self.assertEqual(self.store.settings().get('places'), moved)

    def test_a_place_nominatim_cannot_find_is_named_nothing_is_saved_and_the_places_found_are_kept(self) -> None:
        code, _, errors = self.save({'places': [*COMPLETE['places'], {**PLACE, 'name': 'Office'}], 'preferences': COMPLETE['preferences']})
        self.assertEqual(code, settings_script.PLACE_NOT_FOUND)
        self.assertIn('Office ("Loop, Chicago")', errors)
        self.assertNotIn('places', self.store.settings())
        self.assertNotIn('preferences', self.store.settings())
        self.assertEqual(len(self.store.geocodes(LOCATIONS)), 2)
        self.searches.clear()
        self.assertEqual(self.save({'places': COMPLETE['places']})[0], 0)
        self.assertEqual(self.searches, [])

    def test_an_unreachable_nominatim_saves_nothing_with_its_own_exit_code(self) -> None:
        self.unavailable = {COMPLETE['places'][1]['address']}
        code, _, errors = self.save({'places': COMPLETE['places']})
        self.assertEqual(code, settings_script.GEOCODING_UNAVAILABLE)
        self.assertIn('Nothing was saved', errors)
        self.assertNotIn('places', self.store.settings())
        self.assertEqual(list(self.store.geocodes(LOCATIONS)), [COMPLETE['places'][0]['address']])
        self.unavailable, self.searches = set(), []
        self.assertEqual(self.save({'places': COMPLETE['places']})[0], 0)
        self.assertEqual(self.searches, [COMPLETE['places'][1]['address']])

    def test_a_malformed_place_is_rejected_before_any_request(self) -> None:
        self.assertEqual(self.save({'places': [{**PLACE, 'travel_mode': 'transit'}]})[0], 1)
        self.assertEqual(self.searches, [])

    def test_places_at_one_address_are_looked_up_once(self) -> None:
        shared: list[Place] = [COMPLETE['places'][0], {'name': 'Gym', 'address': COMPLETE['places'][0]['address']}]
        code, result, _ = self.save({'places': shared})
        self.assertEqual((code, self.searches), (0, [shared[0]['address']]))
        assert result is not None
        self.assertEqual([place['found'] for place in result['places']], [LOCATIONS[shared[0]['address']]['found']] * 2)
        self.assertEqual(self.store.places_without_coordinates(), [])

    def test_no_places_need_no_request(self) -> None:
        code, result, _ = self.save({'places': []})
        assert result is not None
        self.assertEqual((code, result['places'], self.searches), (0, [], []))


if __name__ == '__main__':
    _ = unittest.main()
