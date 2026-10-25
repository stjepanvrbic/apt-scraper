import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_gallery_store import ListingStore  # noqa: E402
from test_ranking_scoring import GARDEN, HIGH_RISE, NOT_RANKED, UNANALYZED, VINTAGE  # noqa: E402
import ranking  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'profile/scripts/ranking-run.py'
SETTINGS = {
    'criteria': {'location': ['North'], 'price': {'min': None, 'max': 3000, 'currency': 'USD', 'period': 'month'},
                 'size': {'min': None, 'max': None}, 'rooms': {'min': None, 'max': None}, 'move_in': None, 'other': []},
    'preferences': {'likes': ['Lots of daylight'], 'dislikes': ['Carpet']},
    'listing_sites': ['https://www.zillow.com'],
    'viewing_availability': {'timezone': 'America/Chicago', 'weekly': [{'day': 'saturday', 'start': '10:00', 'end': '14:00'}]},
}
STYLES = {'v': VINTAGE, 'h': HIGH_RISE, 'g': GARDEN}
TERMS = {'Lots of daylight': ['bright natural light'], 'Carpet': ['carpet'], 'Doorman': ['doorman']}


def scraped(identifier, price):
    return {'id': identifier, 'title': f'Apartment {identifier}', 'price': price, 'currency': 'USD', 'rental_period': 'month',
            'address': f'{identifier} Example Street, Chicago', 'description': f'Listing {identifier}.', 'amenities': ['Dishwasher'],
            'photos': [f'https://photos.example/{identifier}/{number}.jpg' for number in range(5)],
            'sources': [{'label': 'Example', 'url': f'https://example.com/{identifier}'}]}


class RankingJobTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.scratch = self.home / 'cache' / 'scratch'
        self.scratch.mkdir(parents=True)
        patcher = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')

    def add(self, identifier, price=2200, matches=True):
        fingerprint = self.store.upsert_listing(scraped(identifier, price), matches_criteria=matches)['content_fingerprint']
        self.store.analyze(identifier, fingerprint, {'facts': {'currency': 'USD', 'areas': ['North'], 'units': [
            {'price': price, 'period': 'month', 'bedrooms': 2, 'size_sqft': 900, 'available_on': None}]}, 'must_haves': {}})
        return fingerprint

    def call(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ranking.main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def run_job(self):
        code, out, _ = self.call('run')
        self.assertEqual(code, 0)
        return out, json.loads(out.strip().splitlines()[-1])['wakeAgent']

    def pending(self):
        code, out, err = self.call('pending')
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def analyze(self, value, name='analysis.json'):
        file = self.scratch / name
        file.write_text(json.dumps(value), encoding='utf-8')
        return self.call('analyze', str(file)), file

    def profile(self, identifier):
        return {'summary': f'Summary of {identifier}', **STYLES[identifier[0]]}

    def analyze_all(self, judgment=None):
        while True:
            batch = self.pending()
            if not batch['listings'] and not batch['notes'] and not batch['preferences']:
                return
            entries = []
            for listing in batch['listings']:
                entry = {'id': listing['id'], 'fingerprint': listing['fingerprint'],
                         'preferences': {phrase: (judgment or {}).get(phrase) for phrase in listing['unjudged_preferences']}}
                if 'profile' in listing['needs']:
                    entry['profile'] = self.profile(listing['id'])
                entries.append(entry)
            notes = [{'id': note['id'], 'sentiment': 'positive', 'likes': [], 'dislikes': []} for note in batch['notes']]
            preferences = [{'phrase': item['phrase'], 'terms': TERMS.get(item['phrase'], []),
                            'reason': None if item['phrase'] in TERMS else 'No term covers it.'} for item in batch['preferences']]
            (code, _, err), _ = self.analyze({'listings': entries, 'notes': notes, 'preferences': preferences})
            self.assertEqual(code, 0, err)

    def ranks(self):
        return {item['id']: item['rank'] for item in self.store.browse('Rankings', page_size=100)['items']}

    def test_before_the_interview_is_complete_nothing_is_ranked_and_the_model_sleeps(self):
        self.add('v0')
        out, wake = self.run_job()
        self.assertFalse(wake)
        self.assertIn('interview', out)
        self.assertIsNone(self.store.listing('v0')['rank'])

    def test_listings_waiting_for_analysis_wake_the_agent_with_one_batch_of_their_text_and_photos(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for number in range(8):
            self.add(f'v{number}')
        out, wake = self.run_job()
        self.assertTrue(wake)
        self.assertIn('8 listings', out)
        batch = self.pending()
        self.assertEqual(len(batch['listings']), ranking.PROFILE_BATCH)
        self.assertEqual(batch['remaining'], {'listings': 8 - ranking.PROFILE_BATCH, 'notes': 0})
        first = batch['listings'][0]
        self.assertEqual((first['needs'], first['unjudged_preferences']), (['profile'], []))
        self.assertEqual(first['photos'], [f'https://photos.example/v0/{number}.jpg' for number in range(ranking.PHOTOS)])
        self.assertEqual(batch['preferences'], [{'phrase': 'Lots of daylight', 'kind': 'like'}, {'phrase': 'Carpet', 'kind': 'dislike'}])
        self.assertEqual(batch['vocabulary'], {key: list(value) for key, value in ranking.VOCABULARY.items()})
        self.assertIsNone(self.store.listing('v0')['rank'])

    def test_analysis_ranks_every_available_listing_and_keeps_the_search_facts(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for identifier in ('v0', 'v1', 'h0', 'h1', 'g0'):
            self.add(identifier)
        self.analyze_all()
        ranks = self.ranks()
        self.assertEqual(sorted(ranks.values()), [1, 2, 3, 4, 5])
        for identifier in ranks:
            listing = self.store.listing(identifier)
            self.assertTrue(listing['reason'])
            self.assertTrue(listing['analysis_current'])
            self.assertEqual(self.store.features(identifier)['facts']['areas'], ['North'])
        self.assertFalse(self.run_job()[1])
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_a_rejected_analysis_saves_nothing_and_keeps_its_file(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        fingerprint = self.add('v0')
        good = {'id': 'v0', 'fingerprint': fingerprint, 'profile': self.profile('v0'), 'preferences': {}}
        empty = {'listings': [], 'notes': [], 'preferences': []}
        for value, message in [({**empty, 'listings': [{**good, 'profile': {**self.profile('v0'), 'photos': ['marble']}}]}, 'marble'),
                               ({**empty, 'listings': [{**good, 'preferences': {'Pool': True}}]}, 'Pool'),
                               ({**empty, 'listings': [{**good, 'preferences': {'Carpet': True}}]}, 'Carpet'),
                               ({**empty, 'listings': [{key: good[key] for key in ('id', 'fingerprint', 'preferences')}]}, 'profile'),
                               ({**empty, 'notes': [{'id': 4, 'sentiment': 'positive', 'likes': [], 'dislikes': []}]}, 'note'),
                               ({**empty, 'preferences': [{'phrase': 'Carpet', 'terms': ['sunsets'], 'reason': None}]}, 'sunsets'),
                               ({**empty, 'preferences': [{'phrase': 'Pool', 'terms': [], 'reason': 'No term.'}]}, 'Pool'),
                               ({'listings': [good], 'notes': []}, 'preferences')]:
            with self.subTest(message=message):
                (code, _, err), file = self.analyze(value)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertTrue(file.exists())
                self.assertNotIn('profile', self.store.features('v0'))
        outside = self.home / 'analysis.json'
        outside.write_text(json.dumps({'listings': [good], 'notes': [], 'preferences': []}), encoding='utf-8')
        code, _, err = self.call('analyze', str(outside))
        self.assertEqual(code, 1)
        self.assertIn('scratch', err)

    def test_an_analysis_of_changed_content_is_skipped_and_the_listing_comes_back(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        fingerprint = self.add('v0')
        self.add('v0', price=2100)
        (code, out, err), file = self.analyze({'listings': [{'id': 'v0', 'fingerprint': fingerprint, 'profile': self.profile('v0'), 'preferences': {}}], 'notes': [], 'preferences': []})
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['stale'], ['v0'])
        self.assertFalse(file.exists())
        self.assertEqual([listing['id'] for listing in self.pending()['listings']], ['v0'])

    def test_a_favorite_or_dismissal_reranks_in_code_without_waking_the_agent(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for identifier in ('v0', 'v1', 'v2', 'h0', 'h1', 'h2', 'g0', 'g1'):
            self.add(identifier, 2150 + 10 * int(identifier[1]))
        self.analyze_all()
        self.store.feedback('h0', favorite=True)
        self.store.feedback('h1', favorite=True)
        out, wake = self.run_job()
        self.assertFalse(wake)
        ranks = self.ranks()
        self.assertLess(ranks['h2'], min(ranks[identifier] for identifier in ('v0', 'v1', 'v2', 'g0', 'g1')))
        self.assertRegex(self.store.listing('h2')['reason'], 'h[01] Example Street')
        self.store.feedback('v0', dismissed=True)
        self.assertFalse(self.run_job()[1])
        ranks = self.ranks()
        self.assertNotIn('v0', ranks)
        self.assertGreater(min(ranks['v1'], ranks['v2']), max(ranks['g0'], ranks['g1']))
        self.assertEqual(self.store.listing('v0')['reason'], NOT_RANKED['dismissed'])

    def test_a_new_preference_is_read_once_and_every_listing_is_scored_against_it_in_code(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for identifier in ('v0', 'v1', 'h0', 'h1'):
            self.add(identifier)
        self.analyze_all()
        self.store.update_settings(preferences={'likes': ['Lots of daylight', 'Doorman'], 'dislikes': []})
        self.assertTrue(self.run_job()[1])
        batch = self.pending()
        self.assertEqual((batch['listings'], batch['preferences']), ([], [{'phrase': 'Doorman', 'kind': 'like'}]))
        (code, _, err), _ = self.analyze({'listings': [], 'notes': [], 'preferences': [{'phrase': 'Doorman', 'terms': ['doorman'], 'reason': None}]})
        self.assertEqual(code, 0, err)
        self.assertFalse(self.run_job()[1])
        ranks = self.ranks()
        self.assertLess(max(ranks['h0'], ranks['h1']), min(ranks['v0'], ranks['v1']))
        self.assertIn('doorman', self.store.listing('h0')['reason'])

    def test_only_a_preference_the_vocabulary_cannot_express_is_judged_per_listing_and_says_why(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for identifier in ('v0', 'h0'):
            self.add(identifier)
        self.analyze_all()
        self.store.update_settings(preferences={'likes': ['Near my office'], 'dislikes': []})
        self.assertTrue(self.run_job()[1])
        self.assertEqual(self.pending()['listings'], [])
        (code, _, err), _ = self.analyze({'listings': [], 'notes': [], 'preferences': [{'phrase': 'Near my office', 'terms': [], 'reason': 'No term covers it.'}]})
        self.assertEqual(code, 0, err)
        code, out, _ = self.call('status')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['judged_per_listing'], {'Near my office': 'No term covers it.'})
        self.assertTrue(self.run_job()[1])
        batch = self.pending()
        self.assertEqual([(listing['needs'], listing['unjudged_preferences']) for listing in batch['listings']], [(['preferences'], ['Near my office'])] * 2)
        self.assertNotIn('photos', batch['listings'][0])
        self.assertEqual(batch['listings'][0]['profile'], self.profile(batch['listings'][0]['id']))
        self.analyze_all({'Near my office': True})
        self.assertFalse(self.run_job()[1])

    def test_a_user_note_waits_for_one_reading_and_then_feeds_the_ranking(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for identifier in ('v0', 'v1', 'h0', 'h1'):
            self.add(identifier)
        self.analyze_all()
        self.store.add_note('Loved this place after the viewing.', listing_id='h0')
        self.assertTrue(self.run_job()[1])
        [note] = self.pending()['notes']
        self.assertEqual((note['listing_id'], note['text']), ('h0', 'Loved this place after the viewing.'))
        self.analyze_all()
        self.assertFalse(self.run_job()[1])
        ranks = self.ranks()
        self.assertLess(ranks['h1'], min(ranks['v0'], ranks['v1']))

    def test_listings_that_teach_the_ranking_are_analyzed_even_when_not_ranked(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        self.add('v0')
        self.add('h0', matches=False)
        self.add('g0', matches=False)
        self.store.feedback('h0', favorite=True)
        self.assertEqual([(listing['id'], listing['needs']) for listing in self.pending()['listings']],
                         [('h0', ['profile']), ('v0', ['profile'])])

    def test_a_failing_run_is_recorded_for_status_and_never_wakes_the_agent(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        self.add('v0')
        self.assertEqual(self.call('status')[0], 0)
        with patch.object(ranking, 'rank', side_effect=RuntimeError('broken scoring')):
            out, wake = self.run_job()
        self.assertFalse(wake)
        self.assertIn('broken scoring', out)
        code, out, _ = self.call('status')
        self.assertEqual(code, ranking.ATTENTION)
        self.assertIn('broken scoring', json.loads(out)['problems'][0])
        self.run_job()
        code, out, _ = self.call('status')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['awaiting_analysis'], 1)

    def test_a_store_that_cannot_be_opened_is_recorded_and_the_run_still_exits_zero(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for error in (sqlite3.OperationalError('database is locked'), sqlite3.DatabaseError('file is not a database')):
            with self.subTest(error=error), patch.object(ranking, 'ListingStore', side_effect=error):
                out, wake = self.run_job()
                self.assertFalse(wake)
                self.assertIn(str(error), out)
                code, out, _ = self.call('status')
                self.assertEqual(code, ranking.ATTENTION)
                self.assertIn(str(error), ' '.join(json.loads(out)['problems']))
        self.run_job()
        self.assertEqual(self.call('status')[0], 0)

    def test_a_corrupt_store_file_never_fails_the_run(self):
        path = self.home / 'workspace' / 'gallery' / 'listings.sqlite3'
        path.write_bytes(b'not a database' * 100)
        out, wake = self.run_job()
        self.assertFalse(wake)
        code, out, _ = self.call('status')
        self.assertEqual(code, ranking.ATTENTION)
        self.assertIn('DatabaseError', ' '.join(json.loads(out)['problems']))
        path.unlink()
        self.assertFalse(self.run_job()[1])
        self.assertEqual(self.call('status')[0], 0)

    def test_listings_and_notes_the_agent_cannot_analyze_are_given_up_and_the_loop_ends(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        for number in range(ranking.PROFILE_BATCH + 1):
            self.add(f'v{number}', 2100 + 50 * number)
        self.store.add_note('Unreadable note.', listing_id='v0')
        wakes = []
        for _ in range(3 * ranking.ATTEMPTS):
            wakes.append(self.run_job()[1])
        self.assertEqual(wakes, [True] * (2 * ranking.ATTEMPTS) + [False] * ranking.ATTEMPTS)
        code, out, _ = self.call('status')
        self.assertEqual(code, ranking.ATTENTION)
        report = json.loads(out)
        self.assertEqual((report['awaiting_analysis'], report['unread_notes']), (0, 0))
        self.assertTrue(any('v6' in problem and 'price, size and rooms' in problem for problem in report['problems']))
        self.assertTrue(any('note' in problem for problem in report['problems']))
        listings = [self.store.listing(f'v{number}') for number in range(ranking.PROFILE_BATCH + 1)]
        self.assertEqual(sorted(listing['rank'] for listing in listings), list(range(1, ranking.PROFILE_BATCH + 2)))
        self.assertEqual({listing['reason'] for listing in listings}, {UNANALYZED})
        self.assertEqual(self.call('retry')[0], 0)
        self.assertTrue(self.run_job()[1])
        self.assertEqual(self.call('status')[0], 0)

    def test_a_listing_analyzed_after_failed_attempts_starts_counting_again_when_its_content_changes(self):
        self.store.update_settings(**SETTINGS, judge=lambda criteria, features: 'inside')
        self.add('v0')
        for _ in range(ranking.ATTEMPTS):
            self.run_job()
        self.assertFalse(self.run_job()[1])
        self.add('v0', price=2150)
        self.assertTrue(self.run_job()[1])
        self.analyze_all()
        self.assertFalse(self.run_job()[1])
        self.assertFalse([problem for problem in json.loads(self.call('status')[1])['problems'] if 'v0' in problem])
        self.assertNotEqual(self.store.listing('v0')['reason'], UNANALYZED)


class ShippedJobTests(unittest.TestCase):
    def test_the_ranking_job_runs_every_two_minutes_with_vision_and_the_installers_turn_it_on(self):
        jobs = json.loads((ROOT / 'profile' / 'cron' / 'jobs.json').read_text(encoding='utf-8'))['jobs']
        [job] = [job for job in jobs if job['id'] == 'apt-scraper-ranking']
        self.assertEqual((job['script'], job['skills'], job['schedule'], job['deliver'], job['failure_deliver'], job['enabled_toolsets']),
                         ('ranking-run.py', ['ranking'], 'every 2m', 'local', 'origin', ['terminal', 'file', 'vision']))
        self.assertEqual(ROOT / 'profile' / 'scripts' / job['script'], LAUNCHER)
        powershell, shell = ((ROOT / name).read_text(encoding='utf-8') for name in ('install.ps1', 'install.sh'))
        self.assertIn("cron', 'resume', 'apt-scraper-ranking'", powershell)
        self.assertIn('cron resume apt-scraper-ranking', shell)

    def test_the_cron_script_hands_over_to_the_ranking_run(self):
        specification = importlib.util.spec_from_file_location('ranking_run', LAUNCHER)
        launcher = importlib.util.module_from_spec(specification)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'HERMES_HOME': directory}), \
                patch.object(sys, 'argv', [str(LAUNCHER)]), redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit) as exit:
            specification.loader.exec_module(launcher)
        self.assertEqual(exit.exception.code, 0)
        self.assertEqual(json.loads(out.getvalue().strip().splitlines()[-1]), {'wakeAgent': False})


if __name__ == '__main__':
    unittest.main()
