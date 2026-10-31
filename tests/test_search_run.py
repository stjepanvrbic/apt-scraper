import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict, cast, override
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/search/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/gallery/scripts'))

from client import Fetcher  # noqa: E402
from matching import Facts  # noqa: E402
import search as search_script  # noqa: E402
from search import AreaSearch, JudgeResult, PendingListing, Report, Search, SiteCard, StatusReport, TasksReport, describe, judge  # noqa: E402
from shapes import Criteria, LocationPriorities, Place, Preferences, ViewingAvailability  # noqa: E402
from sites.errors import Blocked, Changed, Gone  # noqa: E402
from sites.zillow import SiteListing, SourcedListing  # noqa: E402
from state import SearchState, SiteReport  # noqa: E402
from store import ListingStore  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/search/scripts/search.py'
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
BUDGET = timedelta(minutes=20)


class Complete(TypedDict):
    """The settings a complete interview saves."""
    criteria: Criteria
    preferences: Preferences
    listing_sites: list[str]
    viewing_availability: ViewingAvailability
    places: list[Place]
    location_priorities: LocationPriorities


class Places(TypedDict):
    """Where a Search keeps its inbox and its judgment task files."""
    inbox: Path
    workspace: Path


class Applied(TypedDict):
    """What search.py apply prints, with the judgments' results."""
    saved: dict[str, JudgeResult]
    rejected: dict[str, str]


class Job(TypedDict):
    """The part of a shipped cron job the tests read."""
    id: str
    enabled_toolsets: list[str]


class Jobs(TypedDict):
    jobs: list[Job]


SETTINGS: Complete = {
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
    'places': [],
    'location_priorities': {'rapid_transit': 'low', 'areas': {'North': 'medium', 'South': 'medium'}},
}


def forget_settings(store: ListingStore, *names: str) -> None:
    """Leaves the store as an installed profile whose interview predates the named settings."""
    connection = sqlite3.connect(store.path)
    with connection:
        _ = connection.executemany('DELETE FROM settings WHERE name = ?', [(name,) for name in names])
    connection.close()


class FakeSite:
    """A listing site whose search results and listing pages the test sets, recording each request."""

    NAME: str = 'Fake'
    HOSTS: set[str] = {'fake.example'}
    __name__: str = 'sites.fake'

    def __init__(self) -> None:
        self.results: dict[str, list[int]] = {'North': [], 'South': []}
        self.prices: dict[int, int] = {}
        self.gone: set[int] = set()
        self.unreadable: set[int] = set()
        self.broken: set[int] = set()
        self.broken_pages: set[tuple[str, int]] = set()
        self.requests: list[str] = []
        self.block: bool = False

    def request(self, what: str) -> None:
        if self.block:
            raise Blocked('bot check')
        self.requests.append(what)

    def searches(self, criteria: Criteria, _client: Fetcher | None, _regions: dict[str, object], /) -> list[AreaSearch]:
        for area in criteria.get('location', []):
            if area not in self.results:
                raise LookupError(f'Fake knows no area called "{area}".')
        return [{'area': area} for area in criteria.get('location', [])]

    def pages(self, search: AreaSearch, _client: Fetcher | None, /) -> Iterator[list[SiteCard]]:
        cards = self.results[search['area']]
        for start in range(0, max(len(cards), 1), 2):
            self.request(f'page {search["area"]} {start // 2 + 1}')
            if (search['area'], start // 2 + 1) in self.broken_pages:
                raise KeyError('resultsPerPage')
            yield [{'id': f'fake:{number}', 'url': f'https://fake.example/{number}', 'signature': str(self.prices.get(number, 2000))}
                   for number in cards[start:start + 2]]

    def read(self, card: SiteCard, _client: Fetcher | None, /) -> tuple[SiteListing, Facts]:
        number = int(card['id'].split(':')[1])
        self.request(f'read {number}')
        if number in self.gone:
            raise Gone(card['url'])
        if number in self.unreadable:
            raise Changed(f'{card["url"]} has no page data.')
        if number in self.broken:
            raise ValueError('amenities must contain 1 to 20000 characters.')
        price = self.prices.get(number, 2000)
        listing: SiteListing = {'id': card['id'], 'title': f'Flat {number}', 'address': None, 'price': price, 'currency': 'USD', 'rental_period': 'month',
                                'description': f'Flat number {number}. Dogs welcome.', 'amenities': [], 'rooms': None, 'size': None, 'photos': [],
                                'sources': [{'label': 'Fake', 'url': card['url']}], 'agent': {}}
        return listing, {'currency': 'USD', 'units': [{'price': price, 'period': 'month', 'bedrooms': 2, 'size_sqft': None, 'available_on': None}]}

    def reread(self, listing: SourcedListing, client: Fetcher | None, /) -> tuple[SiteListing, Facts]:
        return self.read({'id': listing['id'], 'url': listing['sources'][0]['url'], 'signature': ''}, client)


class SearchRunTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory: Path = Path(directory.name)
        self.store: ListingStore = ListingStore(self.directory / 'listings.sqlite3')
        _ = self.store.update_settings(**SETTINGS)
        self.site: FakeSite = FakeSite()
        self.now: datetime = NOW

    def site_for(self, url: str) -> FakeSite | None:
        return self.site if 'fake.example' in url else None

    def site_named(self, name: str) -> FakeSite | None:
        return self.site if name == 'fake' else None

    def search(self, budget: timedelta = BUDGET, reads: int | None = None) -> Report:
        if reads is not None:
            original, remaining = self.site.read, [reads]

            def read(card: SiteCard, client: Fetcher | None) -> tuple[SiteListing, Facts]:
                if remaining[0] == 0:
                    self.now += budget
                remaining[0] -= 1
                return original(card, client)
            self.site.read = read
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None,
                        site_for=self.site_for, site_named=self.site_named, now=lambda: self.now, **self.places())
        return search.run(budget)

    def places(self) -> Places:
        return {'inbox': self.directory / 'inbox', 'workspace': self.directory / 'must-haves'}

    def task_listings(self, report: Report) -> list[list[PendingListing]]:
        return [cast(list[PendingListing], json.loads(Path(task).read_text(encoding='utf-8').split('\n## The listings\n', 1)[1].split('```json\n', 1)[1].split('\n```', 1)[0]))
                for task in report['tasks']]

    def write_judgments(self, name: str, listings: list[PendingListing], value: bool | None = True, phrase: str = 'Dogs allowed') -> Path:
        file = self.directory / 'inbox' / name
        file.parent.mkdir(exist_ok=True)
        _ = file.write_text(json.dumps({'judgments': [{'id': item['id'], 'fingerprint': item['fingerprint'], 'must_haves': {phrase: value}}
                                                      for item in listings]}), encoding='utf-8')
        return file

    def shown(self) -> list[str]:
        return [item['id'] for item in self.store.browse('Rankings')['items']]

    def judge_all(self, value: bool = True) -> list[PendingListing]:
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=self.site_for, site_named=self.site_named,
                        now=lambda: self.now, **self.places())
        pending = search.pending()
        _ = search.judge([{'id': item['id'], 'fingerprint': item['fingerprint'], 'must_haves': {'Dogs allowed': value}} for item in pending])
        return pending

    def test_new_listings_are_read_highest_placed_first_and_wait_for_their_judgment(self) -> None:
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
        self.assertEqual((self.store.features('fake:4') or {}).get('facts', {}).get('areas'), ['South'])

    def test_listings_outside_the_criteria_by_their_facts_are_hidden_without_a_judgment(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        self.site.prices = {2: 3000}
        _ = self.search()
        self.assertEqual([item['id'] for item in self.judge_all()], ['fake:1'])
        self.assertEqual(self.shown(), ['fake:1'])
        self.assertFalse(self.store.listing('fake:2')['matches_criteria'])

    def test_an_unchanged_listing_is_neither_read_again_nor_judged_again(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        _ = self.search()
        _ = self.judge_all()
        self.site.requests.clear()
        report = self.search()
        self.assertEqual(self.site.requests, ['page North 1', 'page South 1'])
        self.assertEqual((report['sites'][0]['unchanged'], report['sites'][0]['new']), (2, 0))
        self.assertEqual(report['tasks'], [])
        self.assertFalse(report['wake'])

    def test_a_changed_listing_is_read_and_judged_again(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        _ = self.search()
        _ = self.judge_all()
        self.site.prices = {1: 2100}
        report = self.search()
        self.assertEqual(report['sites'][0]['changed'], 1)
        self.assertEqual(self.store.listing('fake:1')['price'], 2100)
        self.assertEqual([[item['id'] for item in listings] for listings in self.task_listings(report)], [['fake:1']])
        self.assertTrue(report['wake'])
        self.assertEqual(self.shown(), [])

    def test_found_and_changed_listings_get_code_features_from_their_facts(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        _ = self.search()
        features = self.store.code_features(today=NOW.date())['fake:1']
        self.assertEqual((features['monthly_price'], features['bedrooms']), (2000, 2))
        self.site.prices = {1: 2100}
        _ = self.search()
        features = self.store.code_features(today=NOW.date())['fake:1']
        self.assertEqual((features['fingerprint'], features['monthly_price']), (self.store.listing('fake:1')['content_fingerprint'], 2100))

    def test_reads_left_when_the_time_budget_runs_out_continue_in_the_next_run(self) -> None:
        self.site.results = {'North': [1, 2, 3], 'South': []}
        report = self.search(reads=0)
        self.assertEqual((report['sites'][0]['new'], report['sites'][0]['deferred']), (1, 1))
        self.assertEqual(self.site.requests, ['page North 1', 'read 1'])
        self.assertEqual([item['id'] for item in self.store.state()['listings']], ['fake:1'])
        report = self.search()
        self.assertEqual((report['sites'][0]['new'], report['sites'][0]['unchanged']), (2, 1))

    def test_a_listing_missing_from_a_complete_search_is_checked_at_its_source(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        _ = self.search()
        _ = self.judge_all()
        self.store.request_contact('fake:2')
        _ = self.store.correspond('fake:2', 'outgoing', 'email', 'Is it available?', external_message_id='m1')
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
        _ = self.search()
        self.assertEqual(self.store.listing('fake:2')['status'], 'available')

    def test_a_missing_listing_still_for_rent_is_updated_and_judged_by_its_new_facts(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        _ = self.search()
        _ = self.judge_all()
        self.site.results = {'North': [1], 'South': []}
        self.site.prices = {2: 2900}
        report = self.search()
        self.assertEqual(report['sites'][0]['gone'], 0)
        self.assertEqual(self.store.listing('fake:2')['status'], 'available')
        self.assertEqual(self.shown(), ['fake:1'])

    def test_a_blocked_site_is_left_alone_until_its_back_off_ends(self) -> None:
        self.site.block = True
        report = self.search()
        self.assertIn('bot check', ' '.join(report['sites'][0]['errors']))
        self.site.block = False
        self.now += timedelta(minutes=10)
        report = self.search()
        self.assertEqual(self.site.requests, [])
        self.assertIn('left alone until', ' '.join(report['sites'][0]['errors']))
        self.now += timedelta(hours=1)
        _ = self.search()
        self.assertEqual(self.site.requests[0], 'page North 1')

    def test_a_listing_page_that_cannot_be_read_is_skipped_and_reported(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        self.site.unreadable = {1}
        report = self.search()
        self.assertEqual((report['sites'][0]['unreadable'], report['sites'][0]['new']), (1, 1))
        self.assertIn('https://fake.example/1 has no page data', ' '.join(report['sites'][0]['errors']))
        self.site.unreadable = set()
        report = self.search()
        self.assertEqual((report['sites'][0]['new'], report['sites'][0]['errors']), (1, []))

    def test_an_area_the_site_does_not_know_is_reported_and_the_others_are_searched(self) -> None:
        _ = self.store.update_settings(criteria={**SETTINGS['criteria'], 'location': ['Atlantis', 'North']},
                                       location_priorities={'rapid_transit': 'low', 'areas': {'Atlantis': 'low', 'North': 'medium'}}, judge=judge)
        self.site.results = {'North': [1], 'South': []}
        report = self.search()
        self.assertEqual(report['sites'][0]['new'], 1)
        self.assertIn('Atlantis', ' '.join(report['sites'][0]['errors']))

    def test_a_failing_listing_page_or_result_page_is_recorded_and_the_rest_is_read(self) -> None:
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

    def test_a_site_that_fails_unexpectedly_does_not_stop_the_next_site(self) -> None:
        _ = self.store.update_settings(listing_sites=['https://fake.example/a', 'https://fake.example/b'])
        self.site.results = {'North': [1], 'South': []}
        searches = self.site.searches
        calls: list[int] = []

        def first_fails(criteria: Criteria, client: Fetcher | None, regions: dict[str, object]) -> list[AreaSearch]:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('unexpected answer')
            return searches(criteria, client, regions)
        self.site.searches = first_fails
        report = self.search()
        self.assertIn('RuntimeError: unexpected answer', ' '.join(report['sites'][0]['errors']))
        self.assertEqual(report['sites'][1]['new'], 1)

    def test_a_run_that_crashes_is_recorded_for_status(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None,
                        site_for=self.site_for, site_named=self.site_named, now=lambda: self.now, **self.places())
        search.pending = lambda: (_ for _ in ()).throw(RuntimeError('disk full'))
        report = search.run(timedelta(minutes=20))
        self.assertIn('RuntimeError: disk full', report['error'] or '')
        self.assertFalse(report['wake'])
        status = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=self.site_for, site_named=self.site_named,
                        now=lambda: self.now, **self.places()).status()
        self.assertTrue(any('disk full' in problem for problem in status['problems']))
        self.assertEqual(status['state'], 'failed')

    def test_criteria_narrowed_during_a_run_hide_every_listing_outside_them_when_the_run_ends(self) -> None:
        _ = self.store.update_settings(criteria={**SETTINGS['criteria'], 'other': []}, judge=judge)
        self.site.results = {'North': [1, 2, 3], 'South': []}
        _ = self.search()
        self.assertEqual(len(self.shown()), 3)
        original = self.site.read

        def narrow_then_read(card: SiteCard, client: Fetcher | None) -> tuple[SiteListing, Facts]:
            _ = self.store.update_settings(criteria={**SETTINGS['criteria'], 'other': [], 'price': {'min': None, 'max': 1500, 'currency': 'USD', 'period': 'month'}},
                                           judge=judge)
            return original(card, client)
        self.site.read = narrow_then_read
        self.site.results = {'North': [1, 2, 3, 4], 'South': []}
        _ = self.search()
        self.assertEqual(self.shown(), [])

    def test_a_listing_imported_without_search_facts_waits_for_a_judgment_of_every_criterion(self) -> None:
        _ = self.store.upsert_listing({'id': 'manual:1', 'price': 9000, 'currency': 'USD', 'rental_period': 'month', 'rooms': '1 bedroom',
                                       'sources': [{'label': 'Elsewhere', 'url': 'https://elsewhere.example/1'}]})
        _ = self.search()
        self.assertEqual(self.shown(), [])
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=self.site_for, site_named=self.site_named,
                        now=lambda: self.now, **self.places())
        [item] = search.pending()
        self.assertEqual(item['unjudged'], ['In one of these areas: North; South', 'Price at most 2,400 USD a month', 'Rooms at least 2', 'Dogs allowed'])
        judged = dict.fromkeys(item['unjudged'], True)
        result = search.judge([{'id': 'manual:1', 'fingerprint': item['fingerprint'], 'must_haves': {**judged, 'Price at most 2,400 USD a month': False}}])
        self.assertEqual((result['outside'], self.shown()), (1, []))

    def test_repeated_blocks_wait_longer_each_time(self) -> None:
        state = SearchState(self.directory / 'search.sqlite3')
        first = state.block('fake', NOW)
        second = state.block('fake', NOW)
        self.assertEqual(second - NOW, 2 * (first - NOW))
        state.unblock('fake')
        self.assertEqual(state.block('fake', NOW), first)

    def test_sites_without_a_reader_and_an_incomplete_interview_are_reported(self) -> None:
        _ = self.store.update_settings(listing_sites=['https://www.apartments.example', 'https://fake.example'])
        report = self.search()
        self.assertIn('no reader', ' '.join(report['sites'][0]['errors']))
        self.assertEqual(report['sites'][1]['site'], 'https://fake.example')
        _ = self.store.update_settings(listing_sites=[])
        self.site.requests.clear()
        report = self.search()
        self.assertEqual(self.site.requests, [])
        self.assertIn('listing_sites', report['missing'])

    def test_the_first_search_waits_for_the_whole_interview(self) -> None:
        forget_settings(self.store, 'places', 'location_priorities')
        self.site.results = {'North': [1], 'South': []}
        report = self.search()
        self.assertEqual(self.site.requests, [])
        self.assertIn('places', report['missing'])

    def test_a_search_that_has_run_keeps_running_while_the_interview_has_new_questions(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        _ = self.search()
        forget_settings(self.store, 'places', 'location_priorities')
        self.site.results = {'North': [1, 2], 'South': []}
        report = self.search()
        self.assertEqual(report['missing'], [])
        self.assertEqual(report['sites'][0]['new'], 1)
        self.assertEqual(self.store.missing_settings(), ['places', 'location_priorities.rapid_transit', 'location_priorities.areas'])
        _ = self.store.update_settings(listing_sites=[])
        self.site.requests.clear()
        self.assertEqual(self.search()['missing'], ['listing_sites'])
        self.assertEqual(self.site.requests, [])

    def test_judgments_for_changed_content_or_unknown_must_haves_are_refused(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        _ = self.search()
        search = Search(self.store, SearchState(self.directory / 'search.sqlite3'), client=None, site_for=self.site_for, site_named=self.site_named,
                        now=lambda: self.now, **self.places())
        [item] = search.pending()
        with self.assertRaisesRegex(ValueError, 'Elevator'):
            _ = search.judge([{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': {'Elevator': True}}])
        with self.assertRaisesRegex(ValueError, 'true, false or null'):
            _ = search.judge([{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': {'Dogs allowed': 'yes'}}])
        result = search.judge([{'id': 'fake:1', 'fingerprint': '0' * 64, 'must_haves': {'Dogs allowed': True}}])
        self.assertEqual(result, {'saved': 0, 'stale': ['fake:1'], 'shown': 0, 'outside': 0})
        result = search.judge([{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': {'Dogs allowed': False}}])
        self.assertEqual(result, {'saved': 1, 'stale': [], 'shown': 0, 'outside': 1})

    def test_the_wake_gets_task_files_and_the_next_run_saves_the_judgments_written_into_the_inbox(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        report = self.search()
        self.assertTrue(report['wake'])
        [task] = report['tasks']
        [listings] = self.task_listings(report)
        self.assertEqual([item['id'] for item in listings], ['fake:1', 'fake:2'])
        self.assertIn('Dogs welcome', listings[0]['description'])
        text = describe(report)
        self.assertIn(f'2 listings wait for a judgment of the must-haves. Judgment task files, in order:\n{task}\n', text)
        self.assertNotIn('Dogs welcome', text)
        task_text = Path(task).read_text(encoding='utf-8')
        file = self.directory / 'inbox' / 'judgments-batch-1.json'
        self.assertIn(f'`write_file` to `{file.as_posix()}`', task_text)
        self.assertNotIn('terminal', task_text)
        _ = self.write_judgments(file.name, listings)
        report = self.search()
        self.assertEqual((report['saved'], report['rejected'], report['tasks'], report['wake']), (1, [], [], False))
        self.assertIn('From the last wake: 1 files saved, 0 rejected.', describe(report))
        self.assertFalse(file.exists())
        self.assertEqual(sorted(self.shown()), ['fake:1', 'fake:2'])

    def test_a_rejected_judgments_file_stays_and_its_listings_are_handed_again_first_with_the_error(self) -> None:
        self.site.results = {'North': [1, 2], 'South': []}
        with mock.patch.object(search_script, 'BATCH', 1):
            report = self.search()
            _, second = self.task_listings(report)
            file = self.write_judgments('judgments-batch-2.json', second, phrase='Elevator')
            report = self.search()
            self.assertEqual((report['saved'], report['rejected'], report['wake']), (0, ['judgments-batch-2.json'], True))
            self.assertIn('From the last wake: 0 files saved, 1 rejected (judgments-batch-2.json).', describe(report))
            self.assertTrue(file.exists())
            self.assertEqual([Path(task).name for task in report['tasks']], ['batch-2.md', 'batch-1.md'])
            self.assertEqual([[item['id'] for item in listings] for listings in self.task_listings(report)], [['fake:2'], ['fake:1']])
            text = Path(report['tasks'][0]).read_text(encoding='utf-8')
            self.assertIn('```json\n' + json.dumps('"Elevator" is not a phrase to judge for fake:2. Its phrases are: ["Dogs allowed"].') + '\n```', text)
            self.assertIn(f'The rejected file is still `{file.as_posix()}`', text)
            self.assertNotIn('The rejected save', Path(report['tasks'][1]).read_text(encoding='utf-8'))
            _ = self.write_judgments(file.name, second)
            report = self.search()
        self.assertEqual((report['saved'], report['rejected']), (1, []))
        self.assertFalse(file.exists())
        self.assertEqual(self.shown(), ['fake:2'])

    def test_a_judgments_file_with_values_of_the_wrong_type_is_rejected_and_the_search_goes_on(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        [listings] = self.task_listings(self.search())
        file = self.directory / 'inbox' / 'judgments-batch-1.json'
        file.parent.mkdir()
        _ = file.write_text(json.dumps({'judgments': [{'id': [listings[0]['id']], 'fingerprint': listings[0]['fingerprint'],
                                                       'must_haves': {'Dogs allowed': True}}]}), encoding='utf-8')
        report = self.search()
        self.assertEqual((report['error'], report['rejected'], report['wake']), (None, ['judgments-batch-1.json'], True))
        self.assertIn("id and fingerprint are the text the task file gives", Path(report['tasks'][0]).read_text(encoding='utf-8'))

    def test_a_fingerprint_no_path_can_hold_is_rejected_and_handed_again_until_corrected(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        [listings] = self.task_listings(self.search())
        file = self.write_judgments('judgments-batch-1.json', [{**listings[0], 'fingerprint': 'ab:c*?'}])
        report = self.search()
        self.assertEqual((report['error'], report['rejected'], report['wake']), (None, ['judgments-batch-1.json'], True))
        self.assertTrue(file.exists())
        text = Path(report['tasks'][0]).read_text(encoding='utf-8')
        self.assertIn(json.dumps('Each judgment: "ab:c*?" is not a fingerprint. Copy the fingerprint from the task file.'), text)
        self.assertIn(f'The rejected file is still `{file.as_posix()}`', text)
        _ = self.write_judgments(file.name, listings)
        report = self.search()
        self.assertEqual((report['saved'], report['rejected'], report['error']), (1, [], None))
        self.assertFalse(file.exists())
        self.assertEqual(self.shown(), ['fake:1'])

    def test_a_folder_written_at_the_judgments_files_name_is_removed_and_the_task_handed_again(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        [listings] = self.task_listings(self.search())
        file = self.directory / 'inbox' / 'judgments-batch-1.json'
        (file / 'x').parent.mkdir(parents=True)
        _ = (file / 'x').write_text('{}', encoding='utf-8')
        report = self.search()
        self.assertEqual((report['error'], report['rejected'], report['wake']), (None, ['judgments-batch-1.json'], True))
        self.assertFalse(file.exists())
        text = Path(report['tasks'][0]).read_text(encoding='utf-8')
        self.assertIn('judgments-batch-1.json was a folder, not a file', text)
        self.assertIn(f'Nothing is left at `{file.as_posix()}`. Write the whole file there with `write_file`', text)
        _ = self.write_judgments(file.name, listings)
        report = self.search()
        self.assertEqual((report['saved'], report['rejected'], report['error']), (1, [], None))
        self.assertEqual(self.shown(), ['fake:1'])

    def test_a_rejected_judgments_file_whose_listings_no_longer_wait_is_deleted(self) -> None:
        self.site.results = {'North': [1], 'South': []}
        [listings] = self.task_listings(self.search())
        file = self.write_judgments('judgments-batch-1.json', listings, phrase='Elevator')
        _ = self.judge_all()
        report = self.search()
        self.assertEqual((report['rejected'], report['tasks']), (['judgments-batch-1.json'], []))
        self.assertFalse(file.exists())


class SearchScriptTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home: Path = Path(directory.name)
        self.store: ListingStore = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, 'HERMES_HOME': str(self.home)}
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, env=environment, timeout=60)

    def test_status_before_the_interview_is_complete_says_so(self) -> None:
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn('interview', cast(StatusReport, json.loads(result.stdout))['problems'][0])

    def test_status_waits_for_new_interview_questions_only_before_the_first_search(self) -> None:
        _ = self.store.update_settings(**SETTINGS)
        forget_settings(self.store, 'places', 'location_priorities')
        report = cast(StatusReport, json.loads(self.run_script('status').stdout))
        self.assertEqual(report['state'], 'waiting for the interview')
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        now = datetime.now(timezone.utc)
        site: SiteReport = {'site': 'https://fake.example', 'errors': [], 'new': 0, 'changed': 0, 'unchanged': 0, 'gone': 0, 'deferred': 0, 'unreadable': 0}
        state.finish_run(state.start_run(now), now, {'sites': [site]})
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(cast(StatusReport, json.loads(result.stdout))['state'], 'finished')

    def test_status_reports_a_search_that_never_ran_and_one_that_stopped(self) -> None:
        _ = self.store.update_settings(**SETTINGS)
        report = cast(StatusReport, json.loads(self.run_script('status').stdout))
        self.assertEqual(report['state'], 'not started')
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        state.finish_run(state.start_run(datetime.now(timezone.utc) - timedelta(hours=5)), datetime.now(timezone.utc) - timedelta(hours=5), {'sites': []})
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3)
        report = cast(StatusReport, json.loads(result.stdout))
        self.assertEqual(report['state'], 'stopped')
        self.assertTrue(any('gateway' in problem for problem in report['problems']))
        _ = state.start_run(datetime.now(timezone.utc) - timedelta(hours=4))
        report = cast(StatusReport, json.loads(self.run_script('status').stdout))
        self.assertEqual((report['state'], report['running_since']), ('stopped', None))
        self.assertTrue(any('gateway' in problem for problem in report['problems']))

    def test_status_reports_a_first_run_in_progress_without_a_problem(self) -> None:
        _ = self.store.update_settings(**SETTINGS)
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        started = datetime.now(timezone.utc) - timedelta(seconds=97)
        _ = state.start_run(started)
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stdout)
        report = cast(StatusReport, json.loads(result.stdout))
        self.assertEqual((report['state'], report['problems'], report['running_since'], report['last_run']), ('running', [], started.isoformat(), None))

    def test_status_reports_a_run_that_has_gone_on_longer_than_twice_its_budget(self) -> None:
        _ = self.store.update_settings(**SETTINGS)
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        _ = state.start_run(datetime.now(timezone.utc) - timedelta(minutes=51))
        result = self.run_script('status')
        self.assertEqual(result.returncode, 3, result.stdout)
        report = cast(StatusReport, json.loads(result.stdout))
        self.assertEqual(report['state'], 'running')
        [problem] = report['problems']
        self.assertIn('has not finished after 51 minutes', problem)

    def test_status_follows_the_latest_run_past_one_that_never_finished(self) -> None:
        _ = self.store.update_settings(**SETTINGS)
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        now = datetime.now(timezone.utc)
        _ = state.start_run(now - timedelta(hours=2))
        state.finish_run(state.start_run(now - timedelta(minutes=10)), now - timedelta(minutes=5), {'sites': []})
        self.assertIsNone(state.running_since())
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stdout)
        report = cast(StatusReport, json.loads(result.stdout))
        self.assertEqual((report['state'], report['running_since']), ('finished', None))
        started = now - timedelta(minutes=1)
        _ = state.start_run(started)
        self.assertEqual(state.running_since(), started)
        report = cast(StatusReport, json.loads(self.run_script('status').stdout))
        assert report['last_run'] is not None
        self.assertEqual((report['state'], report['running_since'], report['last_run']['finished_at']),
                         ('running', started.isoformat(), (now - timedelta(minutes=5)).isoformat()))

    def test_new_listings_are_reported_once(self) -> None:
        settings: Complete = {**SETTINGS, 'criteria': {**SETTINGS['criteria'], 'other': []}}
        _ = self.store.update_settings(**settings)
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        state.finish_run(state.start_run(datetime.now(timezone.utc)), datetime.now(timezone.utc), {'sites': []})
        _ = self.store.upsert_listing({'id': 'fake:1', 'sources': [{'label': 'Fake', 'url': 'https://fake.example/1'}]})
        state.saw('fake:1', 'signature', ['North'], datetime.now(timezone.utc))
        report = cast(StatusReport, json.loads(self.run_script('status').stdout))
        self.assertEqual(report['new_since_report'], 1)
        self.assertEqual(self.run_script('reported').returncode, 0)
        self.assertEqual(cast(StatusReport, json.loads(self.run_script('status').stdout))['new_since_report'], 0)

    def test_in_a_conversation_tasks_writes_the_task_files_and_apply_saves_the_judgments_at_once(self) -> None:
        _ = self.store.update_settings(**SETTINGS)
        _ = self.store.upsert_listing({'id': 'fake:1', 'sources': [{'label': 'Fake', 'url': 'https://fake.example/1'}]}, matches_criteria=False)
        result = self.run_script('tasks')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = cast(TasksReport, json.loads(result.stdout))
        self.assertEqual(output['listings'], 1)
        [task] = output['tasks']
        text = Path(task).read_text(encoding='utf-8')
        [item] = cast(list[PendingListing], json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0]))
        written = re.search(r'with `write_file` to `([^`]+)`', text)
        assert written is not None
        file = Path(written.group(1))
        self.assertEqual(file.parent, (self.home / 'cache' / 'inbox' / 'search-conversation').resolve())
        job_file = self.home / 'cache' / 'inbox' / 'search' / file.name
        job_file.parent.mkdir(parents=True)
        _ = job_file.write_text('[]', encoding='utf-8')
        judgments = {'judgments': [{'id': 'fake:1', 'fingerprint': item['fingerprint'], 'must_haves': dict.fromkeys(item['unjudged'])}]}
        file.parent.mkdir(parents=True)
        _ = file.write_text(json.dumps(judgments['judgments']), encoding='utf-8')
        result = self.run_script('apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('JSON object', cast(Applied, json.loads(result.stdout))['rejected'][file.name])
        self.assertTrue(file.exists())
        _ = file.write_text(json.dumps(judgments), encoding='utf-8')
        result = self.run_script('apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cast(Applied, json.loads(result.stdout))['saved'][file.name]['shown'], 1)
        self.assertFalse(file.exists())
        self.assertEqual(self.store.browse('Rankings')['total'], 1)
        self.assertEqual(cast(TasksReport, json.loads(self.run_script('tasks').stdout)), {'listings': 0, 'tasks': [], 'saved': 0, 'rejected': []})
        self.assertEqual(job_file.read_text(encoding='utf-8'), '[]')

    def test_the_search_session_has_the_file_tools_and_no_terminal(self) -> None:
        jobs = cast(Jobs, json.loads((SCRIPT.parents[3] / 'cron/jobs.json').read_text(encoding='utf-8')))['jobs']
        job = next(job for job in jobs if job['id'] == 'apt-scraper-search')
        self.assertEqual(job['enabled_toolsets'], ['file', 'no_mcp'])
        self.assertNotIn('terminal', search_script.JUDGMENT_TEMPLATE.read_text(encoding='utf-8'))


if __name__ == '__main__':
    _ = unittest.main()
