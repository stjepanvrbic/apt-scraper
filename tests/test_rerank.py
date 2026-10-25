import json
import os
import sqlite3
import random
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))

from record import CATEGORIES, FLAGS, RATINGS, SEED_TAGS  # noqa: E402
from rerank import pairwise_questions, rerank  # noqa: E402
from schema import MIGRATIONS  # noqa: E402
from store import READ_ATTEMPTS, ListingStore, extraction_read  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from test_gallery_store import listing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'profile/skills/ranking/scripts/ranking.py'
SETTINGS = {
    'criteria': {'location': ['Logan Square', 'Avondale'], 'price': {'min': None, 'max': 3000, 'currency': 'USD', 'period': 'month'}},
    'preferences': {'likes': ['Lots of daylight'], 'dislikes': ['Carpet']},
    'location_priorities': {'rapid_transit': 'low', 'areas': {'Logan Square': 'high', 'Avondale': 'medium'}},
}
EXTRA_TAGS = tuple(f'local trait {number}' for number in range(40))


def unit(price, size, label):
    return {'price': price, 'period': 'month', 'bedrooms': 2, 'bathrooms': 1, 'size_sqft': size, 'available_on': None, 'label': label, 'floor': None}


def fill(store, count, favorites, *, seed=7):
    """count listings with feature records of random ratings, categories, flags and tags from a list of about 110
    tags, code features and coordinates, the first favorites of them favorited. Returns their ids."""
    generator = random.Random(seed)
    tags = [*SEED_TAGS, *EXTRA_TAGS]
    identifiers = []
    for number in range(count):
        identifier = f'zillow:{number:04}'
        photos = [f'https://photos.example.com/{number}/{index}.jpg' for index in range(4)]
        saved = store.upsert_listing(listing(id=identifier, title=f'{number} N Example Ave', price=generator.randrange(1500, 3000, 25), photos=photos))
        fingerprint = saved['content_fingerprint']
        label = '#G' if generator.random() < 0.1 else None
        store.analyze(identifier, fingerprint, {'facts': {
            'currency': 'USD', 'areas': [generator.choice(['Logan Square', 'Avondale'])], 'stories': None,
            'latitude': 41.92 + generator.uniform(-0.04, 0.04), 'longitude': -87.71 + generator.uniform(-0.04, 0.04),
            'units': [unit(generator.randrange(1500, 3000, 25), generator.randrange(600, 1400, 25), label)]}, 'must_haves': {}})
        chosen = generator.sample(tags, 8)
        value = extraction(4, ['Lots of daylight', 'Carpet'],
                           ratings={name: 'not shown' if generator.random() < 0.1 else {'value': generator.randint(1, 5), 'confidence': generator.choice((0.5, 0.8, 1.0))}
                                    for name in RATINGS},
                           **{name: generator.choice([*values, 'not shown']) for name, values in CATEGORIES.items()},
                           flags={flag: generator.choice((True, False, None)) for flag in FLAGS}, traits=chosen[:6], concerns=chosen[6:])
        value['preferences'] = {phrase: {'verdict': generator.choice(('has', 'rules out', 'does not say')), 'evidence': ''} for phrase in value['preferences']}
        store.save_extraction(identifier, fingerprint, value, photos=photos, phrases=['Lots of daylight', 'Carpet'])
        identifiers.append(identifier)
    for identifier in identifiers[:favorites]:
        store.feedback(identifier, favorite=True)
    return identifiers


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.store = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')
        self.store.update_settings(**SETTINGS)


class RerankTests(StoreTestCase):
    def test_a_rerank_ranks_explains_and_checks_every_listing_with_a_record_in_one_transaction(self):
        identifiers = fill(self.store, 30, 5)
        revision = self.store.revision()
        summary = rerank(self.store, seed=1)
        self.assertEqual(self.store.revision(), revision + 1)
        self.assertEqual(summary['ranked'], 30)
        self.assertEqual(len(summary['exploration']), 2)
        self.assertEqual(sorted(item['id'] for item in summary['held_out']), identifiers[:5])
        page = self.store.browse('Rankings', page_size=30)['items']
        self.assertEqual([item['rank'] for item in page], list(range(1, 31)))
        self.assertEqual(sum(item['exploration'] for item in page), 2)
        details = self.store.listing(page[0]['id'])
        self.assertEqual(details['reason'], page[0]['reason'])
        self.assertAlmostEqual(sum(part['contribution'] for part in details['explanation']['contributions']), details['explanation']['score'])
        self.assertEqual(self.store.last_rerank()['held_out'], summary['held_out'])

    def test_ranks_change_only_on_a_rerank(self):
        identifiers = fill(self.store, 12, 2)
        rerank(self.store, seed=1)
        before = [(item['id'], item['rank'], item['reason']) for item in self.store.browse('Rankings', page_size=20)['items']]
        for identifier in identifiers[2:6]:
            self.store.feedback(identifier, favorite=True)
        self.store.add_note('Lovely light.', listing_id=identifiers[7], role='user')
        self.store.answer_pair(identifiers[11], identifiers[0])
        self.assertEqual([(item['id'], item['rank'], item['reason']) for item in self.store.browse('Rankings', page_size=20)['items']], before)
        rerank(self.store, seed=1)
        self.assertNotEqual([(item['id'], item['rank'], item['reason']) for item in self.store.browse('Rankings', page_size=20)['items']], before)

    def test_listings_without_a_record_follow_the_ranked_ones_and_one_given_up_on_is_ranked_from_code_features(self):
        fill(self.store, 6, 1)
        waiting = self.store.upsert_listing(listing(id='zillow:waiting', title='Waiting'))
        given_up = self.store.upsert_listing(listing(id='zillow:given-up', title='Given up'))
        self.store.record_read_attempts([extraction_read('zillow:given-up', given_up['content_fingerprint'])] * 1, [])
        for _ in range(READ_ATTEMPTS - 1):
            self.store.record_read_attempts([extraction_read('zillow:given-up', given_up['content_fingerprint'])], [])
        self.assertEqual(self.store.given_up_extractions(), {'zillow:given-up'})
        summary = rerank(self.store, seed=1)
        self.assertEqual((summary['ranked'], summary['waiting_for_features']), (7, 1))
        items = self.store.browse('Rankings', page_size=20)['items']
        self.assertEqual(items[-1]['id'], 'zillow:waiting')
        self.assertIsNone(items[-1]['rank'])
        self.assertEqual(items[-1]['reason'], 'Not ranked yet: waiting for its feature record.')
        self.assertIn('extraction failed 3 times', self.store.listing('zillow:given-up')['reason'])
        self.assertIsNotNone(waiting)

    def test_a_listing_that_changed_since_the_rerank_loses_its_rank(self):
        identifiers = fill(self.store, 6, 1)
        rerank(self.store, seed=1)
        self.store.upsert_listing(listing(id=identifiers[3], title='Changed', photos=[]))
        details = self.store.listing(identifiers[3])
        self.assertFalse(details['analysis_current'])
        self.assertEqual(self.store.browse('Rankings', page_size=20)['items'][-1]['id'], identifiers[3])

    def test_a_single_pairwise_answer_ranks_the_chosen_listing_above_the_other_at_the_next_rerank(self):
        fill(self.store, 25, 3)
        rerank(self.store, seed=1)
        ranks = {item['id']: item['rank'] for item in self.store.browse('Rankings', page_size=30)['items'] if not item['exploration'] and not item['favorite']}
        lower, higher = max(ranks, key=ranks.get), min(ranks, key=ranks.get)
        self.store.answer_pair(lower, higher)
        rerank(self.store, seed=1)
        after = {item['id']: item['rank'] for item in self.store.browse('Rankings', page_size=30)['items']}
        self.assertLess(after[lower], after[higher])
        asked = pairwise_questions(self.store, 5)
        self.assertNotIn({lower, higher}, [set(question['listings']) for question in asked])

    def test_spec_rerank_timing_with_300_listings_and_50_favorites(self):
        """Spec › Acceptance "Rerank": the new order and the leave-one-out check within 2 seconds."""
        fill(self.store, 300, 50)
        started = time.perf_counter()
        summary = rerank(self.store, seed=1)
        elapsed = time.perf_counter() - started
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (300, 50))
        self.assertLess(elapsed, 2.0, summary)


class MigrationTests(unittest.TestCase):
    def test_a_version_4_store_keeps_its_ranks_and_gains_explanations_readings_and_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                for migration in MIGRATIONS[:4]:
                    migration(connection)
                connection.execute('PRAGMA user_version = 4')
                connection.commit()
            old = ListingStore.__new__(ListingStore)
            old.path = path
            with old.connect(write=True) as connection:
                connection.execute("INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES ('example:1', '{}', 'available', '2026-10-01', 'f')")
                connection.execute("INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES ('example:1', 'f', 3, 'Kept.')")
            store = ListingStore(path)
            self.assertEqual(store.schema_version, len(MIGRATIONS))
            with store.connect() as connection:
                row = connection.execute('SELECT * FROM ranking').fetchone()
            self.assertEqual((row['rank'], row['reason'], row['explanation'], row['exploration']), (3, 'Kept.', None, 0))
            self.assertIsNone(store.last_rerank())
            self.assertEqual(store.pairwise_answers(), [])


class ReadingStoreTests(StoreTestCase):
    def test_readings_are_validated_against_the_vocabulary_and_saved_all_or_nothing(self):
        identifier = fill(self.store, 1, 0)[0]
        self.store.add_note('Too dark, but I love the exposed brick.', listing_id=identifier, role='user')
        self.store.add_note('Agent note.', listing_id=identifier, role='agent')
        note = self.store.unread_notes()[0]
        self.assertEqual(len(self.store.unread_notes()), 1)
        self.assertEqual(self.store.unread_preferences(), [('like', 'Lots of daylight'), ('dislike', 'Carpet')])
        with self.assertRaisesRegex(ValueError, 'not a feature'):
            self.store.save_readings(preferences=[('dislike', 'Carpet', [{'feature': 'floor:shag', 'direction': 'dislike'}])],
                                     notes=[(note['id'], {'sentiment': 'mixed', 'likes': ['tag:exposed brick'], 'dislikes': ['rating:natural_light']})])
        self.assertEqual(self.store.note_readings(), {})
        self.store.save_readings(preferences=[('like', 'Lots of daylight', [{'feature': 'rating:natural_light', 'direction': 'like'}]),
                                              ('dislike', 'Carpet', [{'feature': 'floor:carpet', 'direction': 'dislike'}])],
                                 notes=[(note['id'], {'sentiment': 'mixed', 'likes': ['tag:exposed brick'], 'dislikes': ['rating:natural_light']})])
        self.assertEqual(self.store.unread_notes(), [])
        self.assertEqual(self.store.unread_preferences(), [])
        self.store.update_settings(preferences={'likes': ['Lots of natural light'], 'dislikes': ['Carpet']})
        self.assertEqual(self.store.unread_preferences(), [('like', 'Lots of natural light')])
        with self.assertRaisesRegex(ValueError, 'not a stated'):
            self.store.save_readings(preferences=[('like', 'Lots of daylight', [])])
        with self.assertRaisesRegex(ValueError, 'not a note by the user'):
            self.store.save_readings(notes=[(note['id'] + 1, {'sentiment': 'neutral', 'likes': [], 'dislikes': []})])


class CommandTests(StoreTestCase):
    def run_script(self, *arguments):
        environment = {**os.environ, 'HERMES_HOME': str(self.home)}
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, env=environment, timeout=120)

    def test_rerank_status_explain_and_questions(self):
        identifiers = fill(self.store, 24, 4)
        status = self.run_script('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIsNone(json.loads(status.stdout)['last_rerank'])
        result = self.run_script('rerank', '--seed', '5')
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['ranked'], 24)
        report = json.loads(self.run_script('status').stdout)
        self.assertEqual([item['id'] for item in report['last_rerank']['held_out']], identifiers[:4])
        self.assertEqual(report['last_rerank']['held_out'], summary['held_out'])
        self.assertEqual(report['not_ranked'], {'new_or_changed': 0, 'waiting_for_features': 0})
        self.store.upsert_listing(listing(id=identifiers[9], title='Changed', photos=[]))
        self.store.upsert_listing(listing(id='zillow:new', title='New'))
        self.assertEqual(json.loads(self.run_script('status').stdout)['not_ranked'], {'new_or_changed': 0, 'waiting_for_features': 2})
        explained = json.loads(self.run_script('explain', identifiers[5]).stdout)
        self.assertEqual(explained['id'], identifiers[5])
        self.assertIn('contributions', explained['explanation'])
        asked = json.loads(self.run_script('questions', '--count', '3').stdout)
        self.assertEqual(len(asked), 3)
        missing = self.run_script('explain', 'zillow:none')
        self.assertEqual(missing.returncode, 1)

    def test_no_cron_job_reranks(self):
        jobs = json.loads((ROOT / 'profile/cron/jobs.json').read_text(encoding='utf-8'))['jobs']
        self.assertNotIn('apt-scraper-ranking', [job['id'] for job in jobs])
        for job in jobs:
            self.assertNotIn('rerank', job['prompt'].lower())


if __name__ == '__main__':
    unittest.main()
