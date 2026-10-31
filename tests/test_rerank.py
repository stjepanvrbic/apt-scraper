import functools
import json
import os
import sqlite3
import random
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import TypedDict, cast, override

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/gallery/scripts'))

from code_features import UnitFacts  # noqa: E402
from record import CATEGORIES, FLAGS, RATINGS, SEED_TAGS  # noqa: E402
from rerank import RerankReport, pairwise_questions, rerank  # noqa: E402
from schema import MIGRATIONS, earlier_feature_text, reviews_with_inputs, structured_reasons  # noqa: E402
from scoring import Explanation, FeatureReason, Question, Reason  # noqa: E402
from shapes import LocationPriorities, Settings, loads  # noqa: E402
from review import Rerank  # noqa: E402
from store import READ_ATTEMPTS, ListingStore, extraction_read  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from test_gallery_store import listing, present  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/ranking/scripts/ranking.py'
LOCATION_PRIORITIES: LocationPriorities = {'rapid_transit': 'low', 'areas': {'Logan Square': 'high', 'Avondale': 'medium'}}
SETTINGS: Settings = {
    'criteria': {'location': ['Logan Square', 'Avondale'], 'price': {'min': None, 'max': 3000, 'currency': 'USD', 'period': 'month'}},
    'preferences': {'likes': ['Lots of daylight'], 'dislikes': ['Carpet']},
    'location_priorities': LOCATION_PRIORITIES,
}
EXTRA_TAGS = tuple(f'local trait {number}' for number in range(40))
TIMING_RUNS = 3


class NotRanked(TypedDict):
    new_or_changed: int
    waiting_for_features: int


class Status(TypedDict):
    """What `ranking.py status` prints."""
    last_rerank: Rerank | None
    not_ranked: NotRanked


class Explained(TypedDict):
    """What `ranking.py explain` prints."""
    id: str
    explanation: Explanation | None


class CronJob(TypedDict):
    id: str
    prompt: str


class CronJobs(TypedDict):
    jobs: list[CronJob]


def unit(price: int, size: int, label: str | None) -> UnitFacts:
    return {'price': price, 'period': 'month', 'bedrooms': 2, 'bathrooms': 1, 'size_sqft': size, 'available_on': None, 'label': label, 'floor': None}


def fill(store: ListingStore, count: int, favorites: int, *, seed: int = 7) -> list[str]:
    """count listings with feature records of random ratings, categories, flags and tags from a list of about 110
    tags, code features and coordinates, the first favorites of them favorited. Returns their ids."""
    generator = random.Random(seed)
    tags = [*SEED_TAGS, *EXTRA_TAGS]
    identifiers: list[str] = []
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
                           floor=generator.choice([*CATEGORIES['floor'], 'not shown']),
                           style=generator.choice([*CATEGORIES['style'], 'not shown']),
                           outdoor_space=generator.choice([*CATEGORIES['outdoor_space'], 'not shown']),
                           building_type=generator.choice([*CATEGORIES['building_type'], 'not shown']),
                           flags={flag: generator.choice((True, False, None)) for flag in FLAGS}, traits=chosen[:6], concerns=chosen[6:])
        value['preferences'] = {phrase: {'verdict': generator.choice(('has', 'rules out', 'does not say')), 'evidence': ''} for phrase in value['preferences']}
        _ = store.save_extraction(identifier, fingerprint, value, photos=photos, phrases=['Lots of daylight', 'Carpet'])
        identifiers.append(identifier)
    for identifier in identifiers[:favorites]:
        store.feedback(identifier, favorite=True)
    return identifiers


def least_cpu_seconds[T](call: Callable[[], T], runs: int = TIMING_RUNS) -> tuple[float, T]:
    """The least CPU time of runs calls and the last call's result. CPU time leaves out the time other processes hold
    the cores, and the least of several runs leaves out runs slowed further by work sharing their core."""
    times: list[float] = []
    results: list[T] = []
    for _ in range(runs):
        started = time.process_time()
        result = call()
        times.append(time.process_time() - started)
        results.append(result)
    return min(times), results[-1]


# The spec's two seconds are judged on the owner's PC, where reference_work takes REFERENCE_SECONDS of CPU time
# (Python 3.14, least of five runs). A slower machine, such as a shared CI runner or a sandbox, is given the
# budget scaled by how much slower it runs the same work, timed once per process; a faster machine keeps the
# spec's budget. The work is plain Python arithmetic, as the rerank's scoring is: a numpy workload would count
# the threads of its BLAS, which the rerank never uses.
REFERENCE_SECONDS = 0.35


def reference_work() -> float:
    total = 0.0
    for value in range(8_000_000):
        total += (value % 7) * 0.5 + value / (value + 1)
    return total


@functools.cache
def speed_factor() -> float:
    """How many times slower than the owner's PC this machine runs the reference work, at least 1."""
    return max(1.0, least_cpu_seconds(reference_work, runs=5)[0] / REFERENCE_SECONDS)


def cpu_budget(seconds: float = 2.0) -> float:
    """The spec's budget of `seconds` on the owner's PC, scaled for this machine."""
    return seconds * speed_factor()


def feature_lists(reason: Reason) -> FeatureReason:
    """The for and against lists of a reason the rerank wrote, which a review has not rewritten as text."""
    features = reason['features']
    if 'text' in features:
        raise AssertionError('A review rewrote this reason.')
    return features


class StoreTestCase(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home: Path = Path(directory.name)
        self.store: ListingStore = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')
        _ = self.store.update_settings(**SETTINGS)


class RerankTests(StoreTestCase):
    def test_a_rerank_ranks_explains_and_checks_every_listing_with_a_record_in_one_transaction(self) -> None:
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
        explanation = present(details['explanation'])
        self.assertAlmostEqual(sum(part['contribution'] for part in explanation['contributions']), explanation['score'])
        self.assertEqual(present(self.store.last_rerank())['held_out'], summary['held_out'])

    def test_ranks_change_only_on_a_rerank(self) -> None:
        identifiers = fill(self.store, 12, 2)
        _ = rerank(self.store, seed=1)
        before = [(item['id'], item['rank'], item['reason']) for item in self.store.browse('Rankings', page_size=20)['items']]
        for identifier in identifiers[2:6]:
            self.store.feedback(identifier, favorite=True)
        self.store.add_note('Lovely light.', listing_id=identifiers[7], role='user')
        self.store.answer_pair(identifiers[11], identifiers[0])
        self.assertEqual([(item['id'], item['rank'], item['reason']) for item in self.store.browse('Rankings', page_size=20)['items']], before)
        _ = rerank(self.store, seed=1)
        self.assertNotEqual([(item['id'], item['rank'], item['reason']) for item in self.store.browse('Rankings', page_size=20)['items']], before)

    def test_listings_without_a_record_follow_the_ranked_ones_and_one_given_up_on_is_ranked_from_code_features(self) -> None:
        _ = fill(self.store, 6, 1)
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
        self.assertEqual((items[-1]['reason'], items[-1].get('unranked_reason')), (None, 'Not ranked yet: waiting for its feature record.'))
        given_up = self.store.listing('zillow:given-up')
        self.assertTrue(present(given_up['reason'])['code_only'])
        self.assertIsNone(given_up.get('unranked_reason'))
        self.assertIsNotNone(waiting)

    def test_a_listing_that_changed_since_the_rerank_loses_its_rank(self) -> None:
        identifiers = fill(self.store, 6, 1)
        _ = rerank(self.store, seed=1)
        _ = self.store.upsert_listing(listing(id=identifiers[3], title='Changed', photos=[]))
        details = self.store.listing(identifiers[3])
        self.assertFalse(details['analysis_current'])
        self.assertEqual(self.store.browse('Rankings', page_size=20)['items'][-1]['id'], identifiers[3])

    def test_the_distances_to_stations_and_places_count_by_the_location_priorities(self) -> None:
        identifiers = fill(self.store, 30, 3)
        self.store.save_stations('https://www.transitchicago.com/downloads/sch_data/google_transit.zip',
                                 [{'id': '41020', 'name': 'Logan Square', 'latitude': 41.929728, 'longitude': -87.708541}])
        self.store.save_geocodes({'233 S Wacker Dr, Chicago, IL 60606': {'latitude': 41.8788764, 'longitude': -87.6359149, 'found': 'Willis Tower'}})
        _ = self.store.update_settings(places=[{'name': 'Work', 'address': '233 S Wacker Dr, Chicago, IL 60606'}],
                                       location_priorities={**LOCATION_PRIORITIES, 'places': 'high'})
        summary = rerank(self.store, seed=1)
        self.assertLess(summary['weights']['place:Work'], 0)
        self.assertLess(summary['weights']['station_distance'], 0)
        values = {part['feature']: part['value'] for part in present(self.store.listing(identifiers[0])['explanation'])['contributions']}
        self.assertRegex(present(values['place:Work']), r'^\d+\.\d km from Work$')
        self.assertRegex(present(values['station_distance']), r'^\d+\.\d km to Logan Square station$')

    def test_a_single_pairwise_answer_ranks_the_chosen_listing_above_the_other_at_the_next_rerank(self) -> None:
        _ = fill(self.store, 25, 3)
        _ = rerank(self.store, seed=1)
        ranks = {item['id']: present(item['rank']) for item in self.store.browse('Rankings', page_size=30)['items'] if not item['exploration'] and not item['favorite']}
        lower, higher = max(ranks, key=lambda identifier: ranks[identifier]), min(ranks, key=lambda identifier: ranks[identifier])
        self.store.answer_pair(lower, higher)
        _ = rerank(self.store, seed=1)
        after = {item['id']: present(item['rank']) for item in self.store.browse('Rankings', page_size=30)['items']}
        self.assertLess(after[lower], after[higher])
        asked = pairwise_questions(self.store, 5)
        self.assertNotIn({lower, higher}, [set(question['listings']) for question in asked])

    def test_spec_rerank_timing_with_300_listings_and_50_favorites(self) -> None:
        """Spec › Acceptance "Rerank": the new order and the leave-one-out check within 2 seconds."""
        _ = fill(self.store, 300, 50)
        seconds, summary = least_cpu_seconds(lambda: rerank(self.store, seed=1))
        self.assertEqual((summary['ranked'], len(summary['held_out'])), (300, 50))
        self.assertLess(seconds, cpu_budget(), (speed_factor(), summary))


class MigrationTests(unittest.TestCase):
    def test_a_version_4_store_keeps_its_ranks_and_gains_explanations_readings_and_answers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            with closing(sqlite3.connect(path)) as connection:
                for migration in MIGRATIONS[:4]:
                    migration(connection)
                _ = connection.execute('PRAGMA user_version = 4')
                _ = connection.execute("INSERT INTO listings (id, data, status, first_seen, fingerprint) VALUES ('example:1', '{}', 'available', '2026-10-01', 'f')")
                _ = connection.execute("INSERT INTO ranking (listing_id, fingerprint, rank, reason) VALUES ('example:1', 'f', 3, 'Kept.')")
                connection.commit()
            store = ListingStore(path)
            self.assertEqual(store.schema_version, len(MIGRATIONS))
            with store.connect() as connection:
                row = cast(sqlite3.Row, connection.execute('SELECT * FROM ranking').fetchone())
            self.assertEqual((cast(int, row['rank']), loads(cast(str, row['reason'])), cast(str | None, row['explanation']), cast(int, row['exploration'])),
                             (3, {'code_only': False, 'features': {'text': 'Kept.'}, 'similar_favorite': None}, None, 0))
            self.assertIsNone(store.last_rerank())
            self.assertEqual(store.pairwise_answers(), [])


class StructuredReasonMigrationTests(StoreTestCase):
    @staticmethod
    def earlier_text(reason: Reason, features: str, exploration: bool) -> str:
        """The reason as reasons were stored before they had parts."""
        sentences = ['Shown to learn your taste.'] if exploration else []
        if reason['code_only']:
            sentences.append('Ranked from code features only, because extraction failed 3 times.')
        sentences.append(features)
        if reason['similar_favorite']:
            sentences.append(f'Most like your favorite {reason["similar_favorite"]}.')
        return ' '.join(sentences)

    def test_a_reason_the_model_wrote_gets_its_lists_back_and_one_a_review_rewrote_keeps_its_text(self) -> None:
        """A listing ranked from code features only that got its record after the rerank keeps its code-only mark."""
        _ = fill(self.store, 30, 3)
        _ = self.store.upsert_listing(listing(id='zillow:waiting', title='Waiting'))
        photos = [f'https://photos.example.com/given-up/{index}.jpg' for index in range(4)]
        given_up = self.store.upsert_listing(listing(id='zillow:given-up', title='Given up', photos=photos))
        for _ in range(READ_ATTEMPTS):
            self.store.record_read_attempts([extraction_read('zillow:given-up', given_up['content_fingerprint'])], [])
        _ = rerank(self.store, seed=1)
        ranked = self.store.ranked_listings()
        reasons = {identifier: entry['reason'] for identifier, entry in ranked.items()}
        self.assertTrue(reasons['zillow:given-up']['code_only'])
        _ = self.store.save_extraction('zillow:given-up', given_up['content_fingerprint'], extraction(4, ['Lots of daylight', 'Carpet']),
                                       photos=photos, phrases=['Lots of daylight', 'Carpet'])
        reviewed = next(identifier for identifier, entry in ranked.items() if entry['exploration'])
        with self.store.connect(write=True) as connection:
            for identifier, reason in reasons.items():
                features = 'Strong on exposed brick.' if identifier == reviewed else earlier_feature_text(feature_lists(reason))
                _ = connection.execute('UPDATE ranking SET reason = ? WHERE listing_id = ?',
                                       (self.earlier_text(reason, features, ranked[identifier]['exploration']), identifier))
            _ = connection.execute("UPDATE ranking SET reason = 'Not ranked yet: waiting for its feature record.' WHERE rank IS NULL")
            structured_reasons(connection)
        migrated = {identifier: entry['reason'] for identifier, entry in self.store.ranked_listings().items()}
        self.assertEqual(migrated, {**reasons, reviewed: {**reasons[reviewed], 'features': {'text': 'Strong on exposed brick.'}}})
        with self.store.connect() as connection:
            unranked = cast(list[sqlite3.Row], connection.execute('SELECT * FROM ranking WHERE rank IS NULL').fetchall())
            self.assertEqual([cast(str | None, row['reason']) for row in unranked], [None])

    def test_a_review_waiting_from_before_fails_and_the_next_rerank_leaves_one_with_its_inputs(self) -> None:
        _ = fill(self.store, 12, 2)
        summary = rerank(self.store, seed=1)
        with self.store.connect(write=True) as connection:
            reviews_with_inputs(connection)
        review = present(present(self.store.rerank(summary['id']))['review'])
        self.assertEqual(review['state'], 'failed')
        result = review['result']
        assert result is not None and 'error' in result
        self.assertIn('Rerank to get a review', result['error'])
        self.assertIsNone(self.store.review_snapshot(summary['id']))
        later = rerank(self.store, seed=1)
        self.assertIn('inputs', present(self.store.review_snapshot(later['id'])))


class ReadingStoreTests(StoreTestCase):
    def test_readings_are_validated_against_the_vocabulary_and_saved_all_or_nothing(self) -> None:
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
        _ = self.store.update_settings(preferences={'likes': ['Lots of natural light'], 'dislikes': ['Carpet']})
        self.assertEqual(self.store.unread_preferences(), [('like', 'Lots of natural light')])
        with self.assertRaisesRegex(ValueError, 'not a stated'):
            self.store.save_readings(preferences=[('like', 'Lots of daylight', [])])
        with self.assertRaisesRegex(ValueError, 'not a note by the user'):
            self.store.save_readings(notes=[(note['id'] + 1, {'sentiment': 'neutral', 'likes': [], 'dislikes': []})])


class CommandTests(StoreTestCase):
    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, 'HERMES_HOME': str(self.home)}
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, env=environment, timeout=120)

    def test_rerank_status_explain_and_questions(self) -> None:
        identifiers = fill(self.store, 24, 4)
        status = self.run_script('status')
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIsNone(cast(Status, json.loads(status.stdout))['last_rerank'])
        result = self.run_script('rerank', '--seed', '5')
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = cast(RerankReport, json.loads(result.stdout))
        self.assertEqual(summary['ranked'], 24)
        report = cast(Status, json.loads(self.run_script('status').stdout))
        self.assertEqual([item['id'] for item in present(report['last_rerank'])['held_out']], identifiers[:4])
        self.assertEqual(present(report['last_rerank'])['held_out'], summary['held_out'])
        self.assertEqual(report['not_ranked'], {'new_or_changed': 0, 'waiting_for_features': 0})
        _ = self.store.upsert_listing(listing(id=identifiers[9], title='Changed', photos=[]))
        _ = self.store.upsert_listing(listing(id='zillow:new', title='New'))
        self.assertEqual(cast(Status, json.loads(self.run_script('status').stdout))['not_ranked'], {'new_or_changed': 0, 'waiting_for_features': 2})
        explained = cast(Explained, json.loads(self.run_script('explain', identifiers[5]).stdout))
        self.assertEqual(explained['id'], identifiers[5])
        self.assertIn('contributions', present(explained['explanation']))
        asked = cast(list[Question], json.loads(self.run_script('questions', '--count', '3').stdout))
        self.assertEqual(len(asked), 3)
        missing = self.run_script('explain', 'zillow:none')
        self.assertEqual(missing.returncode, 1)

    def test_no_cron_job_reranks(self) -> None:
        jobs = cast(CronJobs, json.loads((ROOT / 'cron/jobs.json').read_text(encoding='utf-8')))['jobs']
        self.assertNotIn('apt-scraper-ranking', [job['id'] for job in jobs])
        for job in jobs:
            self.assertNotIn('rerank', job['prompt'].lower())


if __name__ == '__main__':
    _ = unittest.main()
