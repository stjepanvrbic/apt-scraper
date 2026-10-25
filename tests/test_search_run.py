import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/search/scripts'))

from test_gallery_store import ListingStore  # noqa: E402
from matching import evaluate  # noqa: E402
from search import Search, SearchState  # noqa: E402
from sites.errors import Blocked, Changed, Gone  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / 'profile/skills/search/scripts/search.py'
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
SETTINGS = {
    'criteria': {
        'location': ['North', 'South'],
        'price': {'min': None, 'max': 2400, 'currency': 'USD', 'period': 'month'},
        'size': {'min': None, 'max': None},
        'rooms': {'min': 2, 'max': None},
        'move_in': None,
        'other': ['Dogs allowed'],
    },
    'preferences': {'likes': [], 'dislikes': []},
    'listing_sites': ['https://fake.example'],
    'viewing_availability': {'timezone': 'America/Chicago', 'weekly': [{'day': 'saturday', 'start': '10:00', 'end': '14:00'}]},
}


class FakeSite:
    """A listing site whose search results and listing pages the test sets, recording each request."""

    NAME = 'Fake'
    HOSTS = {'fake.example'}
    __name__ = 'sites.fake'

    def __init__(self):
        self.results = {'North': [], 'South': []}
        self.prices = {}
        self.gone = set()
        self.unreadable = set()
        self.broken = set()
        self.broken_pages = set()
        self.requests = []
        self.block = False

    def request(self, what):
        if self.block:
            raise Blocked('bot check')
        self.requests.append(what)

    def searches(self, criteria, client, regions):
        for area in criteria['location']:
            if area not in self.results:
                raise LookupError(f'Fake knows no area called "{area}".')
        return [{'area': area} for area in criteria['location']]

    def pages(self, search, client):
        cards = self.results[search['area']]
        for start in range(0, max(len(cards), 1), 2):
            self.request(f'page {search["area"]} {start // 2 + 1}')
            if (search['area'], start // 2 + 1) in self.broken_pages:
                raise KeyError('resultsPerPage')
            yield [{'id': f'fake:{number}', 'url': f'https://fake.example/{number}', 'signature': str(self.prices.get(number, 2000))}
                   for number in cards[start:start + 2]]

    def read(self, card, client):
        number = int(card['id'].split(':')[1])
        self.request(f'read {number}')
        if number in self.gone:
            raise Gone(card['url'])
        if number in self.unreadable:
            raise Changed(f'{card["url"]} has no page data.')
        if number in self.broken:
            raise ValueError('amenities must contain 1 to 20000 characters.')
        price = self.prices.get(number, 2000)
        listing = {'id': card['id'], 'title': f'Flat {number}', 'price': price, 'currency': 'USD', 'rental_period': 'month',
                   'description': f'Flat number {number}. Dogs welcome.', 'sources': [{'label': 'Fake', 'url': card['url']}]}
        return listing, {'currency': 'USD', 'units': [{'price': price, 'period': 'month', 'bedrooms': 2, 'size_sqft': None, 'available_on': None}]}

    def reread(self, listing, client):
        return self.read({'id': listing['id'], 'url': listing['sources'][0]['url']}, client)


class SearchRunTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.store = ListingStore(self.directory / 'listings.sqlite3')
        self.store.update_settings(**SETTINGS)
        self.site = FakeSite()
        self.now = NOW

    def search(self, budget=timedelta(minutes=20), reads=None):
        clock = SimpleNamespace(now=lambda: self.now)
        if reads is not None:
            original, remaining = self.site.read, [reads]

            def read(card, client):
                if remaining[0] == 0:
                    self.now += budget
                remaining[0] -= 1
                return original(card, client)
            self.site.read = read
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None,
                        site_for=lambda url: self.site if 'fake.example' in url else None,
                        site_named=lambda name: self.site if name == 'fake' else None, now=clock.now)
        return search.run(budget)

    def shown(self):
        return [item['id'] for item in self.store.browse('Rankings')['items']]

    def judge_all(self, value=True):
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=None, site_named=None, now=lambda: self.now)
        pending = search.pending()
        search.judge([{'id': item['id'], 'fingerprint': item['fingerprint'], 'must_haves': {'Dogs allowed': value}} for item in pending])
        return pending

    def test_new_listings_are_read_highest_placed_first_and_wait_for_their_judgment(self):
        self.site.results = {'North': [1, 2, 3], 'South': [4]}
        report = self.search()
        self.assertEqual(self.site.requests, ['page North 1', 'read 1', 'read 2', 'page South 1', 'read 4', 'page North 2', 'read 3'])
        self.assertEqual(report['sites'][0]['new'], 4)
        self.assertEqual(self.shown(), [])
        pending = self.judge_all()
        self.assertEqual([item['id'] for item in pending], ['fake:1', 'fake:2', 'fake:4', 'fake:3'])
        self.assertEqual(pending[0]['unjudged'], ['Dogs allowed'])
        self.assertIn('Dogs welcome', pending[0]['description'])
        self.assertEqual(sorted(self.shown()), ['fake:1', 'fake:2', 'fake:3', 'fake:4'])
        self.assertEqual(self.store.features('fake:4')['facts']['areas'], ['South'])

    def test_listings_outside_the_criteria_by_their_facts_are_hidden_without_a_judgment(self):
        self.site.results = {'North': [1, 2], 'South': []}
        self.site.prices = {2: 3000}
        self.search()
        self.assertEqual([item['id'] for item in self.judge_all()], ['fake:1'])
        self.assertEqual(self.shown(), ['fake:1'])
        self.assertFalse(self.store.listing('fake:2')['matches_criteria'])

    def test_an_unchanged_listing_is_neither_read_again_nor_judged_again(self):
        self.site.results = {'North': [1, 2], 'South': []}
        self.search()
        self.judge_all()
        self.site.requests.clear()
        report = self.search()
        self.assertEqual(self.site.requests, ['page North 1', 'page South 1'])
        self.assertEqual((report['sites'][0]['unchanged'], report['sites'][0]['new']), (2, 0))
        self.assertEqual(report['awaiting_judgment'], [])
        self.assertFalse(report['wake'])

    def test_a_changed_listing_is_read_and_judged_again(self):
        self.site.results = {'North': [1], 'South': []}
        self.search()
        self.judge_all()
        self.site.prices = {1: 2100}
        report = self.search()
        self.assertEqual(report['sites'][0]['changed'], 1)
        self.assertEqual(self.store.listing('fake:1')['price'], 2100)
        self.assertEqual([item['id'] for item in report['awaiting_judgment']], ['fake:1'])
        self.assertTrue(report['wake'])
        self.assertEqual(self.shown(), [])

    def test_found_and_changed_listings_get_code_features_from_their_facts(self):
        self.site.results = {'North': [1], 'South': []}
        self.search()
        features = self.store.code_features(today=NOW.date())['fake:1']
        self.assertEqual((features['monthly_price'], features['bedrooms']), (2000, 2))
        self.site.prices = {1: 2100}
        self.search()
        features = self.store.code_features(today=NOW.date())['fake:1']
        self.assertEqual((features['fingerprint'], features['monthly_price']), (self.store.listing('fake:1')['content_fingerprint'], 2100))

    def test_reads_left_when_the_time_budget_runs_out_continue_in_the_next_run(self):
        self.site.results = {'North': [1, 2, 3], 'South': []}
        report = self.search(reads=0)
        self.assertEqual((report['sites'][0]['new'], report['sites'][0]['deferred']), (1, 1))
        self.assertEqual(self.site.requests, ['page North 1', 'read 1'])
        self.assertEqual([item['id'] for item in self.store.state()['listings']], ['fake:1'])
        report = self.search()
        self.assertEqual((report['sites'][0]['new'], report['sites'][0]['unchanged']), (2, 1))

    def test_a_listing_missing_from_a_complete_search_is_checked_at_its_source(self):
        self.site.results = {'North': [1, 2], 'South': []}
        self.search()
        self.judge_all()
        self.store.request_contact('fake:2')
        self.store.correspond('fake:2', 'outgoing', 'email', 'Is it available?', external_message_id='m1')
        self.site.results = {'North': [1], 'South': []}
        self.site.gone = {2}
        report = self.search()
        self.assertEqual(report['sites'][0]['gone'], 1)
        listing = self.store.listing('fake:2')
        self.assertEqual(listing['status'], 'unavailable')
        self.assertEqual(listing['correspondence_total'], 1)
        self.assertEqual(self.shown(), ['fake:1'])
        self.site.results = {'North': [1, 2], 'South': []}
        self.site.gone = set()
        self.search()
        self.assertEqual(self.store.listing('fake:2')['status'], 'available')

    def test_a_missing_listing_still_for_rent_is_updated_and_judged_by_its_new_facts(self):
        self.site.results = {'North': [1, 2], 'South': []}
        self.search()
        self.judge_all()
        self.site.results = {'North': [1], 'South': []}
        self.site.prices = {2: 2900}
        report = self.search()
        self.assertEqual(report['sites'][0]['gone'], 0)
        self.assertEqual(self.store.listing('fake:2')['status'], 'available')
        self.assertEqual(self.shown(), ['fake:1'])

    def test_a_blocked_site_is_left_alone_until_its_back_off_ends(self):
        self.site.block = True
        report = self.search()
        self.assertIn('bot check', ' '.join(report['sites'][0]['errors']))
        self.site.block = False
        self.now += timedelta(minutes=10)
        report = self.search()
        self.assertEqual(self.site.requests, [])
        self.assertIn('left alone until', ' '.join(report['sites'][0]['errors']))
        self.now += timedelta(hours=1)
        self.search()
        self.assertEqual(self.site.requests[0], 'page North 1')

    def test_a_listing_page_that_cannot_be_read_is_skipped_and_reported(self):
        self.site.results = {'North': [1, 2], 'South': []}
        self.site.unreadable = {1}
        report = self.search()
        self.assertEqual((report['sites'][0]['unreadable'], report['sites'][0]['new']), (1, 1))
        self.assertIn('https://fake.example/1 has no page data', ' '.join(report['sites'][0]['errors']))
        self.site.unreadable = set()
        report = self.search()
        self.assertEqual((report['sites'][0]['new'], report['sites'][0]['errors']), (1, []))

    def test_an_area_the_site_does_not_know_is_reported_and_the_others_are_searched(self):
        self.store.update_settings(criteria={**SETTINGS['criteria'], 'location': ['Atlantis', 'North']}, judge=evaluate)
        self.site.results = {'North': [1], 'South': []}
        report = self.search()
        self.assertEqual(report['sites'][0]['new'], 1)
        self.assertIn('Atlantis', ' '.join(report['sites'][0]['errors']))

    def test_a_failing_listing_page_or_result_page_is_recorded_and_the_rest_is_read(self):
        self.site.results = {'North': [1, 2, 3, 4], 'South': [5]}
        self.site.broken = {1}
        self.site.broken_pages = {('North', 2)}
        report = self.search()
        site = report['sites'][0]
        self.assertEqual((site['new'], site['unreadable']), (2, 1))
        self.assertEqual(sorted(item['id'] for item in self.store.state()['listings']), ['fake:2', 'fake:5'])
        self.assertTrue(any('https://fake.example/1' in error and 'ValueError' in error for error in site['errors']))
        self.assertTrue(any('North' in error and 'KeyError' in error for error in site['errors']))
        self.site.broken, self.site.broken_pages = set(), set()
        self.assertEqual(self.search()['sites'][0]['errors'], [])

    def test_a_site_that_fails_unexpectedly_does_not_stop_the_next_site(self):
        self.store.update_settings(listing_sites=['https://fake.example/a', 'https://fake.example/b'])
        self.site.results = {'North': [1], 'South': []}
        searches, calls = self.site.searches, []

        def first_fails(criteria, client, regions):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('unexpected answer')
            return searches(criteria, client, regions)
        self.site.searches = first_fails
        report = self.search()
        self.assertIn('RuntimeError: unexpected answer', ' '.join(report['sites'][0]['errors']))
        self.assertEqual(report['sites'][1]['new'], 1)

    def test_a_run_that_crashes_is_recorded_for_status(self):
        self.site.results = {'North': [1], 'South': []}
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None,
                        site_for=lambda url: self.site, site_named=lambda name: self.site, now=lambda: self.now)
        search.pending = lambda: (_ for _ in ()).throw(RuntimeError('disk full'))
        report = search.run(timedelta(minutes=20))
        self.assertIn('RuntimeError: disk full', report['error'])
        self.assertFalse(report['wake'])
        status = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=None, site_named=None,
                        now=lambda: self.now).status()
        self.assertTrue(any('disk full' in problem for problem in status['problems']))

    def test_criteria_narrowed_during_a_run_hide_every_listing_outside_them_when_the_run_ends(self):
        self.store.update_settings(criteria={**SETTINGS['criteria'], 'other': []}, judge=evaluate)
        self.site.results = {'North': [1, 2, 3], 'South': []}
        self.search()
        self.assertEqual(len(self.shown()), 3)
        original = self.site.read

        def narrow_then_read(card, client):
            self.store.update_settings(criteria={**SETTINGS['criteria'], 'other': [], 'price': {'min': None, 'max': 1500, 'currency': 'USD', 'period': 'month'}},
                                       judge=evaluate)
            return original(card, client)
        self.site.read = narrow_then_read
        self.site.results = {'North': [1, 2, 3, 4], 'South': []}
        self.search()
        self.assertEqual(self.shown(), [])

    def test_a_listing_imported_without_search_facts_waits_for_a_judgment_of_every_criterion(self):
        self.store.upsert_listing({'id': 'manual:1', 'price': 9000, 'currency': 'USD', 'rental_period': 'month', 'rooms': '1 bedroom',
                                   'sources': [{'label': 'Elsewhere', 'url': 'https://elsewhere.example/1'}]})
        self.search()
        self.assertEqual(self.shown(), [])
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=None, site_named=None, now=lambda: self.now)
        [item] = search.pending()
        self.assertEqual(item['unjudged'], ['In one of these areas: North; South', 'Price at most 2,400 USD a month', 'Rooms at least 2', 'Dogs allowed'])
        judged = dict.fromkeys(item['unjudged'], True)
        result = search.judge([{'id': 'manual:1', 'fingerprint': item['fingerprint'], 'must_haves': {**judged, 'Price at most 2,400 USD a month': False}}])
        self.assertEqual((result['outside'], self.shown()), (1, []))

    def test_repeated_blocks_wait_longer_each_time(self):
        state = SearchState(self.directory / 'search.sqlite3')
        first = state.block('fake', NOW)
        second = state.block('fake', NOW)
        self.assertEqual(second - NOW, 2 * (first - NOW))
        state.unblock('fake')
        self.assertEqual(state.block('fake', NOW), first)

    def test_sites_without_a_reader_and_an_incomplete_interview_are_reported(self):
        self.store.update_settings(listing_sites=['https://www.apartments.example', 'https://fake.example'])
        report = self.search()
        self.assertIn('no reader', ' '.join(report['sites'][0]['errors']))
        self.assertEqual(report['sites'][1]['site'], 'https://fake.example')
        self.store.update_settings(listing_sites=[])
        self.site.requests.clear()
        report = self.search()
        self.assertEqual(self.site.requests, [])
        self.assertIn('listing_sites', report['missing'])

    def test_judgments_for_changed_content_or_unknown_must_haves_are_refused(self):
        self.site.results = {'North': [1], 'South': []}
        self.search()
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=None, site_named=None, now=lambda: self.now)
        [item] = search.pending()
        with self.assertRaisesRegex(ValueError, 'Elevator'):
            search.judge([{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': {'Elevator': True}}])
        with self.assertRaisesRegex(ValueError, 'true, false or null'):
            search.judge([{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': {'Dogs allowed': 'yes'}}])
        result = search.judge([{'id': 'fake:1', 'fingerprint': 'old', 'must_haves': {'Dogs allowed': True}}])
        self.assertEqual(result, {'saved': 0, 'stale': ['fake:1'], 'shown': 0, 'outside': 0})
        result = search.judge([{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': {'Dogs allowed': False}}])
        self.assertEqual(result, {'saved': 1, 'stale': [], 'shown': 0, 'outside': 1})


class SearchScriptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.store = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')

    def run_script(self, *arguments):
        environment = {**os.environ, 'HERMES_HOME': str(self.home)}
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, env=environment, timeout=60)

    def test_status_before_the_interview_is_complete_says_so(self):
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('interview', json.loads(result.stdout)['problems'][0])

    def test_status_reports_a_search_that_never_ran_and_one_that_stopped(self):
        self.store.update_settings(**SETTINGS)
        report = json.loads(self.run_script('status').stdout)
        self.assertEqual(report['state'], 'not started')
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        state.finish_run(state.start_run(datetime.now(timezone.utc) - timedelta(hours=5)), datetime.now(timezone.utc) - timedelta(hours=5), {'sites': []})
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3)
        report = json.loads(result.stdout)
        self.assertEqual(report['state'], 'stopped')
        self.assertTrue(any('gateway' in problem for problem in report['problems']))

    def test_new_listings_are_reported_once(self):
        self.store.update_settings(**{**SETTINGS, 'criteria': {**SETTINGS['criteria'], 'other': []}})
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        state.finish_run(state.start_run(datetime.now(timezone.utc)), datetime.now(timezone.utc), {'sites': []})
        self.store.upsert_listing({'id': 'fake:1', 'sources': [{'label': 'Fake', 'url': 'https://fake.example/1'}]})
        state.saw('fake:1', 'signature', ['North'], datetime.now(timezone.utc))
        report = json.loads(self.run_script('status').stdout)
        self.assertEqual(report['new_since_report'], 1)
        self.assertEqual(self.run_script('reported').returncode, 0)
        self.assertEqual(json.loads(self.run_script('status').stdout)['new_since_report'], 0)

    def test_judgments_are_saved_from_a_scratch_file_that_is_deleted(self):
        self.store.update_settings(**SETTINGS)
        self.store.upsert_listing({'id': 'fake:1', 'sources': [{'label': 'Fake', 'url': 'https://fake.example/1'}]}, matches_criteria=False)
        pending = json.loads(self.run_script('pending').stdout)
        self.assertEqual([item['id'] for item in pending['listings']], ['fake:1'])
        scratch = self.home / 'cache' / 'scratch'
        scratch.mkdir(parents=True)
        outside = self.home / 'judgments.json'
        judgments = {'judgments': [{'id': 'fake:1', 'fingerprint': pending['listings'][0]['fingerprint'], 'must_haves': dict.fromkeys(pending['listings'][0]['unjudged'])}]}
        outside.write_text(json.dumps(judgments), encoding='utf-8')
        result = self.run_script('judge', str(outside))
        self.assertEqual(result.returncode, 1)
        self.assertIn('scratch', result.stderr)
        file = scratch / 'judgments.json'
        file.write_text(json.dumps(judgments['judgments']), encoding='utf-8')
        result = self.run_script('judge', str(file))
        self.assertEqual(result.returncode, 1)
        self.assertIn('JSON object', result.stderr)
        file.write_text(json.dumps(judgments), encoding='utf-8')
        result = self.run_script('judge', str(file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['shown'], 1)
        self.assertFalse(file.exists())
        self.assertEqual(self.store.browse('Rankings')['total'], 1)


if __name__ == '__main__':
    unittest.main()
