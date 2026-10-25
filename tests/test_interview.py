import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zoneinfo
from pathlib import Path
from unittest.mock import patch

from test_gallery_store import ListingStore, listing
from store import TimeZoneDataMissing

SCRIPT = Path(__file__).resolve().parents[1] / 'profile/skills/interview/scripts/settings.py'

COMPLETE = {
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


def settings(**changes):
    values = copy.deepcopy(COMPLETE)
    values.update(changes)
    return values


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'listings.sqlite3'
        self.store = ListingStore(self.path)

    def test_new_store_is_missing_every_required_setting(self):
        self.assertEqual(self.store.missing_settings(), [
            'criteria.location', 'criteria.price', 'criteria.size', 'criteria.rooms', 'criteria.move_in', 'criteria.other',
            'preferences.likes', 'preferences.dislikes', 'listing_sites',
            'viewing_availability.timezone', 'viewing_availability.weekly'])

    def test_partial_answers_persist_and_only_unanswered_items_stay_missing(self):
        self.store.update_settings(criteria={'location': ['Logan Square, Chicago'], 'price': {'min': None, 'max': 2400, 'currency': 'USD', 'period': 'month'}})
        self.store.update_settings(preferences={'likes': ['Quiet street']}, listing_sites=[])
        reopened = ListingStore(self.path)
        self.assertEqual(reopened.settings()['criteria']['price']['max'], 2400)
        self.assertEqual(reopened.missing_settings(), [
            'criteria.size', 'criteria.rooms', 'criteria.move_in', 'criteria.other', 'preferences.dislikes',
            'listing_sites', 'viewing_availability.timezone', 'viewing_availability.weekly'])

    def test_complete_settings_have_nothing_missing_and_no_limit_counts_as_answered(self):
        values = settings()
        values['criteria']['size'] = {'min': None, 'max': None}
        values['criteria']['move_in'] = None
        values['criteria']['other'] = []
        values['preferences'] = {'likes': [], 'dislikes': []}
        self.store.update_settings(**values)
        self.assertEqual(self.store.missing_settings(), [])

    def test_empty_availability_and_sites_are_not_answers(self):
        self.store.update_settings(listing_sites=[], viewing_availability={'timezone': 'America/Chicago', 'weekly': []})
        self.assertIn('listing_sites', self.store.missing_settings())
        self.assertIn('viewing_availability.weekly', self.store.missing_settings())
        self.assertNotIn('viewing_availability.timezone', self.store.missing_settings())

    def test_unchanged_settings_are_preserved_by_partial_updates(self):
        self.store.update_settings(**settings())
        self.store.update_settings(preferences={'likes': ['Balcony'], 'dislikes': []})
        saved = ListingStore(self.path).settings()
        self.assertEqual(saved['preferences'], {'likes': ['Balcony'], 'dislikes': []})
        self.assertEqual(saved['listing_sites'], COMPLETE['listing_sites'])
        self.assertEqual(saved['criteria'], COMPLETE['criteria'])

    def test_criteria_change_hides_listings_outside_and_restores_matching_ones_in_one_step(self):
        self.store.upsert_listing(listing())
        self.store.upsert_listing(listing(id='example:200', price=2900))
        revision = self.store.revision()
        criteria = copy.deepcopy(COMPLETE['criteria'])
        result = self.store.update_settings(criteria=criteria, outside=['example:200'])
        self.assertEqual(result, {'outside': 1, 'matching': 1})
        self.assertGreater(self.store.revision(), revision)
        self.assertEqual(self.store.browse('Rankings')['total'], 1)
        self.assertEqual(ListingStore(self.path).settings()['criteria'], criteria)
        criteria['price']['max'] = 3000
        self.assertEqual(self.store.update_settings(criteria=criteria, outside=[]), {'outside': 0, 'matching': 2})
        self.assertEqual(self.store.browse('Rankings')['total'], 2)

    def test_unknown_outside_listing_changes_nothing(self):
        self.store.upsert_listing(listing())
        self.store.update_settings(criteria=COMPLETE['criteria'], outside=[])
        changed = settings()['criteria']
        changed['rooms'] = {'min': 3, 'max': None}
        with self.assertRaisesRegex(KeyError, 'example:999'):
            self.store.update_settings(criteria=changed, preferences={'likes': [], 'dislikes': []}, outside=['example:100', 'example:999'])
        self.assertEqual(self.store.settings(), {'criteria': COMPLETE['criteria']})
        self.assertEqual(self.store.browse('Rankings')['total'], 1)

    def test_criteria_with_stored_listings_are_saved_only_with_their_exclusions(self):
        self.store.upsert_listing(listing())
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.store.update_settings(criteria=COMPLETE['criteria'])
        self.assertEqual(self.store.settings(), {})

    def test_exclusions_without_criteria_are_rejected(self):
        self.store.upsert_listing(listing())
        with self.assertRaisesRegex(ValueError, 'criteria'):
            self.store.update_settings(preferences=COMPLETE['preferences'], outside=['example:100'])
        self.assertEqual(self.store.settings(), {})

    def test_malformed_settings_are_rejected_and_nothing_is_saved(self):
        cases = [
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
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.store.update_settings(**{name: value})
        self.assertEqual(self.store.settings(), {})

    def test_missing_time_zone_data_is_its_own_failure(self):
        self.addCleanup(zoneinfo.ZoneInfo.clear_cache)
        self.addCleanup(zoneinfo.reset_tzpath)
        with patch.dict(sys.modules, {name: None for name in [*sys.modules, 'tzdata'] if name.split('.')[0] == 'tzdata'}):
            zoneinfo.reset_tzpath([])
            zoneinfo.ZoneInfo.clear_cache()
            with self.assertRaisesRegex(TimeZoneDataMissing, 'tzdata'):
                self.store.update_settings(viewing_availability=COMPLETE['viewing_availability'])
        self.assertEqual(self.store.settings(), {})


class SettingsScriptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.path = self.home / 'workspace' / 'gallery' / 'listings.sqlite3'

    def run_script(self, *arguments, interpreter=()):
        environment = {**os.environ, 'HERMES_HOME': str(self.home)}
        return subprocess.run([sys.executable, *interpreter, str(SCRIPT), *arguments], capture_output=True, text=True, env=environment, timeout=60)

    def save(self, settings, *arguments, interpreter=()):
        file = self.home / 'cache' / 'scratch' / 'settings.json'
        file.parent.mkdir(parents=True, exist_ok=True)
        text = settings if isinstance(settings, str) else json.dumps(settings, ensure_ascii=False)
        file.write_text(text, encoding='utf-8')
        result = self.run_script('save', str(file), *arguments, interpreter=interpreter)
        if result.returncode == 0:
            self.assertFalse(file.exists())
        else:
            self.assertEqual(file.read_text(encoding='utf-8'), text)
        return result

    def test_rejected_file_is_kept(self):
        result = self.save('Just some notes, not settings.')
        self.assertEqual(result.returncode, 1)
        self.assertIn('JSON', result.stderr)

    def test_files_outside_the_scratch_directory_are_refused_and_kept(self):
        ListingStore(self.path).update_settings(preferences=COMPLETE['preferences'])
        before = self.path.read_bytes()
        outside = self.home / 'settings.json'
        outside.write_text(json.dumps({'listing_sites': COMPLETE['listing_sites']}), encoding='utf-8')
        for file in (self.path, outside, self.home / 'cache' / 'scratch' / '..' / 'settings.json'):
            with self.subTest(file=file):
                result = self.run_script('save', str(file))
                self.assertEqual(result.returncode, 1)
                self.assertIn('scratch', result.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(outside.exists())
        self.assertNotIn('listing_sites', ListingStore(self.path).settings())

    def test_missing_time_zone_data_exits_with_its_own_code_and_keeps_the_file(self):
        result = self.save({'viewing_availability': COMPLETE['viewing_availability']}, interpreter=('-c', WITHOUT_TIME_ZONE_DATA))
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('tzdata', result.stderr)
        self.assertEqual(ListingStore(self.path).settings(), {})
        self.assertEqual(self.run_script('status', interpreter=('-c', WITHOUT_TIME_ZONE_DATA)).returncode, 3)

    def test_status_reports_missing_settings_until_the_interview_is_complete(self):
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['complete'])
        self.assertIn('criteria.location', report['missing'])
        self.assertEqual(report['settings'], {})
        saved = self.save(COMPLETE)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.loads(saved.stdout)['missing'], [])
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'complete': True, 'missing': [], 'settings': COMPLETE})

    def test_settings_live_in_the_real_store_of_the_profile(self):
        self.save({'preferences': COMPLETE['preferences']})
        self.assertEqual(ListingStore(self.path).settings(), {'preferences': COMPLETE['preferences']})
        self.assertFalse((self.path.parent / 'sample.sqlite3').exists())

    def test_no_other_store_can_be_chosen(self):
        other = self.home / 'other.sqlite3'
        result = self.run_script('--database', str(other), 'status')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(other.exists())

    def test_quotes_and_letters_outside_ascii_survive(self):
        preferences = {'likes': ["Children's playground nearby", 'Altbau in Köln'], 'dislikes': ['"Cozy" studios']}
        result = self.save({'preferences': preferences})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ListingStore(self.path).settings()['preferences'], preferences)
        self.assertEqual(json.loads(self.run_script('status').stdout)['settings']['preferences'], preferences)

    def test_missing_settings_file_is_reported(self):
        result = self.run_script('save', str(self.home / 'cache' / 'scratch' / 'absent.json'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('absent.json', result.stderr)

    def test_saving_reports_what_is_still_missing(self):
        result = self.save({'listing_sites': COMPLETE['listing_sites']})
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['saved'], ['listing_sites'])
        self.assertFalse(report['complete'])
        self.assertNotIn('listing_sites', report['missing'])

    def test_criteria_change_with_stored_listings_requires_a_judgment_of_each(self):
        store = ListingStore(self.path)
        store.upsert_listing(listing())
        store.upsert_listing(listing(id='example:200', price=2900, address='5 Far Road, Evanston'))
        result = self.save({'criteria': COMPLETE['criteria']})
        self.assertEqual(result.returncode, 1)
        self.assertIn('--outside', result.stderr)
        self.assertNotIn('criteria', store.settings())
        listings = self.run_script('listings')
        self.assertEqual(listings.returncode, 0, listings.stderr)
        facts = json.loads(listings.stdout)
        self.assertEqual([item['id'] for item in facts], ['example:100', 'example:200'])
        self.assertEqual(facts[1]['price'], 2900)
        self.assertEqual(facts[1]['address'], '5 Far Road, Evanston')
        self.assertNotIn('notes', facts[1])
        result = self.save({'criteria': COMPLETE['criteria']}, '--outside', 'example:200')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['criteria'], {'outside': 1, 'matching': 1})
        self.assertEqual(store.browse('Rankings')['total'], 1)
        result = self.save({'criteria': COMPLETE['criteria']}, '--outside')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(store.browse('Rankings')['total'], 2)

    def test_criteria_without_stored_listings_need_no_judgment(self):
        result = self.save({'criteria': COMPLETE['criteria']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ListingStore(self.path).settings()['criteria'], COMPLETE['criteria'])

    def test_invalid_input_is_reported_and_saves_nothing(self):
        for text in ['not json', '[]', '{}', json.dumps({'criteria': {'budget': 1}, 'listing_sites': COMPLETE['listing_sites']}),
                     json.dumps({'search_radius': 5})]:
            with self.subTest(text=text):
                result = self.save(text)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.strip())
        self.assertEqual(ListingStore(self.path).settings(), {})

    def test_outside_without_criteria_is_rejected(self):
        result = self.save({'preferences': COMPLETE['preferences']}, '--outside', 'example:100')
        self.assertEqual(result.returncode, 1)
        self.assertIn('criteria', result.stderr)


if __name__ == '__main__':
    unittest.main()
