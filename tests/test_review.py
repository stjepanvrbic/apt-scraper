import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

import ranking  # noqa: E402
from rerank import REVIEW_ATTEMPTS, pending_review, rerank, save_review  # noqa: E402
from review import REVIEWED, adjustments, checked_reason, citable_values, held_out_ranks, reviewed_order  # noqa: E402
from schema import MIGRATIONS, review_pass  # noqa: E402
from store import ListingStore  # noqa: E402
from test_gallery_store import listing  # noqa: E402
from test_rerank import ROOT, SCRIPT, SETTINGS, StoreTestCase, fill  # noqa: E402


class ReasonTests(unittest.TestCase):
    def test_a_reason_cites_only_values_of_the_record_and_uses_only_connecting_words_outside_them(self):
        values = ['exposed brick', 'natural light 5 of 5', '2,450 USD a month', 'carpet floor']
        self.assertEqual(checked_reason('Strong on [exposed brick] and [natural light 5 of 5], but [2,450 USD a month] is high.', values),
                         'Strong on exposed brick and natural light 5 of 5, but 2,450 USD a month is high.')
        for text, error in [('A calm home with good bones.', 'cites nothing'),
                            ('For it: [fireplace].', 'does not state "fireplace"'),
                            ('For it: [exposed brick] and a fireplace.', 'uses "fireplace" outside'),
                            ('For it: [exposed brick] in a bright, spacious unit with laundry and AC.', 'uses "ac", "bright", "laundry", "spacious" outside'),
                            ('For it: [exposed brick], but the carpet is worn.', 'uses "carpet", "worn" outside'),
                            ('Most like a favorite with [exposed brick].', 'uses "favorite", "like" outside'),
                            ('For it: [exposed brick] under 2000 a month.', 'uses "2000", "month" outside'),
                            ('For it: [exposed brick] \u2605\u2605\u2605.', 'uses "\u2605" outside'),
                            ("For it: [exposed brick], it's a caf\u00e9.", 'uses "caf\u00e9", "it\'s" outside'),
                            ('For it: [exposed brick]] and more.', 'unmatched bracket')]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                checked_reason(text, values)


class OrderTests(unittest.TestCase):
    def test_moved_listings_land_on_their_ranks_around_the_exploration_listings(self):
        snapshot = {'order': ['a', 'b', 'e1', 'c', 'd', 'e2', 'f'], 'exploration': ['e1', 'e2']}
        order = reviewed_order(snapshot, {'f': {'to': 1}, 'a': {'to': 5}})
        self.assertEqual(order, ['f', 'b', 'e1', 'c', 'a', 'e2', 'd'])

    def test_adjustments_place_each_moved_listing_between_its_unmoved_neighbours(self):
        score = {'a': 5.0, 'b': 4.0, 'c': 3.0, 'd': 2.0, 'f': 1.0}
        shifts = adjustments(['f', 'b', 'c', 'a', 'd'], {'f', 'a'}, score)
        adjusted = {identifier: value + shifts.get(identifier, 0.0) for identifier, value in score.items()}
        self.assertEqual(sorted(adjusted, key=adjusted.get, reverse=True), ['f', 'b', 'c', 'a', 'd'])
        shifts = adjustments(['b', 'f', 'a', 'c', 'd'], {'f', 'a'}, score)
        adjusted = {identifier: value + shifts.get(identifier, 0.0) for identifier, value in score.items()}
        self.assertEqual(sorted(adjusted, key=adjusted.get, reverse=True), ['b', 'f', 'a', 'c', 'd'])
        self.assertAlmostEqual(adjusted['f'] - adjusted['a'], adjusted['a'] - adjusted['c'])


class ReviewTestCase(StoreTestCase):
    listings, favorites = 60, 5

    def setUp(self):
        super().setUp()
        os.environ['HERMES_HOME'] = str(self.home)
        self.addCleanup(os.environ.pop, 'HERMES_HOME')
        self.identifiers = fill(self.store, self.listings, self.favorites)
        self.summary = rerank(self.store, seed=1)
        self.ranked = self.store.ranked_listings()

    def review(self, **fields):
        return {'rerank': self.summary['id'], 'reasons': [], 'moves': [], 'flags': [], 'findings': [], **fields}

    def by_rank(self, rank):
        return next(identifier for identifier, entry in self.ranked.items() if entry['rank'] == rank)

    def free_rank(self, ranks):
        return next(rank for rank in ranks if not self.ranked[self.by_rank(rank)]['exploration'])

    def ranks(self):
        return {identifier: entry['rank'] for identifier, entry in self.store.ranked_listings().items()}


class ReviewPassTests(ReviewTestCase):
    def test_a_rerank_leaves_its_review_waiting_and_a_later_rerank_supersedes_it(self):
        self.assertEqual((self.summary['review']['state'], self.summary['review']['attempts']), ('waiting', 0))
        second = rerank(self.store, seed=1)
        self.assertEqual(second['review']['state'], 'waiting')
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT state FROM reviews WHERE rerank_id = ?', (self.summary['id'],)).fetchone()[0], 'superseded')
        with self.assertRaisesRegex(ValueError, f'Rerank {self.summary["id"]} was replaced by rerank {second["id"]}, .* Stop'):
            save_review(self.store, self.review())
        self.assertIsNone(self.store.review_snapshot(self.summary['id']))
        self.assertIsNone(self.store.hand_review(self.summary['id']))
        with self.store.connect() as connection:
            self.assertEqual(connection.execute('SELECT state FROM reviews WHERE rerank_id = ?', (self.summary['id'],)).fetchone()[0], 'superseded')

    def test_the_check_before_the_moves_is_the_reranks_own_leave_one_out_check(self):
        snapshot = self.store.review_snapshot(self.summary['id'])
        self.assertEqual(held_out_ranks(snapshot, {}), {item['id']: item['rank'] for item in self.summary['held_out']})

    def test_a_move_next_to_an_exploration_listing_leaves_it_at_its_rank(self):
        explored = sorted(entry['rank'] for entry in self.ranked.values() if entry['exploration'])
        moved = next(identifier for identifier in sorted(self.ranked, key=lambda item: self.ranked[item]['rank'])
                     if identifier not in self.identifiers[:self.favorites] and not self.ranked[identifier]['exploration'])
        result = save_review(self.store, self.review(moves=[{'id': moved, 'to_rank': explored[1] + 1, 'reason': 'Weaker.', 'confidence': 0.6}]))
        ranks = self.ranks()
        self.assertEqual((result['outcome'], ranks[moved]), ('kept', explored[1] + 1))
        for identifier, entry in self.ranked.items():
            if entry['exploration']:
                self.assertEqual(ranks[identifier], entry['rank'])
        self.assertEqual(sorted(ranks.values()), list(range(1, len(ranks) + 1)))

    def test_the_task_holds_the_top_listings_and_the_ranking_state_without_any_label(self):
        task = pending_review(self.store)
        self.assertEqual([item['rank'] for item in task['listings']], list(range(1, REVIEWED + 1)))
        for item in task['listings']:
            self.assertEqual(set(item), {'rank', 'id', 'title', 'exploration', 'record', 'contributions', 'reason'})
            self.assertNotIn('favorite', json.dumps(item).lower())
        favorite_titles = {f'{int(identifier.split(":")[1])} N Example Ave' for identifier in self.identifiers[:self.favorites]}
        text = json.dumps(task, ensure_ascii=False)
        for title in favorite_titles:
            self.assertEqual(text.count(json.dumps(title)), 1, title)
        state = task['state']
        self.assertEqual(state['labels']['favorites'], self.favorites)
        self.assertEqual(len(state['held_out_ranks']['ranks']), self.favorites)
        self.assertTrue(all(isinstance(rank, int) for rank in state['held_out_ranks']['ranks']))
        self.assertIn('exposed brick', state['tags'])
        self.assertEqual([item['phrase'] for item in task['preferences']], ['Lots of daylight', 'Carpet'])
        self.assertTrue(state['largest_weights'])
        for entry in state['largest_weights'] + state['weights_against_starting_weight']:
            self.assertLessEqual(set(entry), {'feature', 'weight', 'starting_weight', 'stated'})
            self.assertNotEqual(entry['feature'], 'near_favorites')
        self.assertEqual(json.dumps(state).count('favorite'), 2, 'only the count of favorites and of listings that are not favorites')

    def test_moves_that_put_favorites_lower_are_all_reverted(self):
        favorite = min(self.identifiers[:self.favorites], key=lambda identifier: self.ranked[identifier]['rank'])
        other = next(identifier for identifier in sorted(self.ranked, key=lambda item: -self.ranked[item]['rank'])
                     if identifier not in self.identifiers[:self.favorites] and not self.ranked[identifier]['exploration']
                     and self.ranked[identifier]['rank'] <= REVIEWED)
        before = self.ranks()
        result = save_review(self.store, self.review(moves=[
            {'id': favorite, 'to_rank': self.free_rank(range(45, 1, -1)), 'reason': 'Weaker than it looks.', 'confidence': 0.7},
            {'id': other, 'to_rank': self.free_rank(range(1, 50)), 'reason': 'Better than it looks.', 'confidence': 0.6}]))
        self.assertEqual(result['outcome'], 'reverted')
        self.assertIn('worse', result['reverted_because'])
        self.assertGreater(result['check']['after']['mean'], result['check']['before']['mean'])
        self.assertEqual(result['check']['before']['mean'], self.summary['held_out_mean'])
        self.assertEqual([move['id'] for move in result['moves']], [favorite, other])
        self.assertEqual(self.ranks(), before)
        review = self.store.last_rerank()['review']
        self.assertEqual((review['state'], review['result']), ('done', result))

    def test_moves_that_put_favorites_higher_stand(self):
        favorite = max(self.identifiers[:self.favorites], key=lambda identifier: self.ranked[identifier]['rank'])
        other = next(identifier for identifier in sorted(self.ranked, key=lambda item: self.ranked[item]['rank'])
                     if identifier not in self.identifiers[:self.favorites] and not self.ranked[identifier]['exploration'])
        top, bottom = self.free_rank(range(1, 50)), self.free_rank(range(REVIEWED, 1, -1))
        result = save_review(self.store, self.review(moves=[
            {'id': favorite, 'to_rank': top, 'reason': 'Has what the user asked for.', 'confidence': 0.8},
            {'id': other, 'to_rank': bottom, 'reason': 'Has what the user dislikes.', 'confidence': 0.5}]))
        self.assertEqual(result['outcome'], 'kept', result)
        self.assertLessEqual(result['check']['after']['mean'], result['check']['before']['mean'])
        ranks = self.ranks()
        self.assertEqual((ranks[favorite], ranks[other]), (top, bottom))
        self.assertEqual(sorted(ranks.values()), list(range(1, len(ranks) + 1)))
        for identifier, entry in self.ranked.items():
            if entry['exploration']:
                self.assertEqual(ranks[identifier], entry['rank'])
        page = self.store.browse('Rankings', page_size=60)['items']
        self.assertEqual(page[top - 1]['id'], favorite)

    def test_a_reason_naming_a_feature_the_record_lacks_is_rejected_and_the_listing_keeps_its_reason(self):
        first, second, third = (self.by_rank(rank) for rank in (1, 2, 3))
        stated = [part['value'] for part in self.ranked[first]['explanation']['contributions'] if part['value']][:2]
        record = self.store.feature_record(second)['extraction']
        lacking = next(tag for tag in ('fireplace', 'skylight', 'deck', 'loft') if tag not in citable_values(self.ranked[second]['explanation'], record))
        third_value = citable_values(self.ranked[third]['explanation'], None)[0]
        valid = {'id': first, 'reason': f'For it: [{stated[0]}] and [{stated[1]}].'}
        with self.assertRaises(ValueError) as raised:
            save_review(self.store, self.review(reasons=[valid, {'id': second, 'reason': f'For it: [{lacking}].'},
                                                         {'id': third, 'reason': f'For it: [{third_value}], with a fireplace.'}]))
        self.assertIn(f'The reason of {second} is rejected: the record does not state "{lacking}".', str(raised.exception))
        self.assertIn(f'The reason of {third} is rejected: it uses "fireplace" outside a citation.', str(raised.exception))
        for identifier in (first, second, third):
            self.assertEqual(self.store.listing(identifier)['reason'], self.ranked[identifier]['reason'])
        self.assertEqual(self.store.last_rerank()['review']['state'], 'waiting')
        result = save_review(self.store, self.review(reasons=[valid]))
        self.assertEqual((result['reasons'], result['outcome']), (1, 'no moves'))
        favorite = self.ranked[first]['explanation']['similar_favorite']
        expected = f'For it: {stated[0]} and {stated[1]}.' + (f' Most like your favorite {favorite["title"]}.' if favorite else '')
        self.assertEqual(self.store.listing(first)['reason'], expected)
        self.assertEqual(self.store.listing(second)['reason'], self.ranked[second]['reason'])

    def test_an_exploration_listing_keeps_its_mark_in_a_rewritten_reason(self):
        explored = next(identifier for identifier, entry in self.ranked.items() if entry['exploration'])
        value = next(part['value'] for part in self.ranked[explored]['explanation']['contributions'] if part['value'])
        save_review(self.store, self.review(reasons=[{'id': explored, 'reason': f'Worth it for [{value}].'}]))
        self.assertTrue(self.store.listing(explored)['reason'].startswith(f'Shown to learn your taste. Worth it for {value}.'))

    def test_flags_and_findings_are_in_the_result_and_in_status(self):
        flagged = self.by_rank(4)
        self.assertEqual(ranking.status(self.store)['last_rerank']['findings'], self.summary['findings'])
        result = save_review(self.store, self.review(
            flags=[{'id': flagged, 'kind': 'dislike', 'preference': 'Carpet', 'note': 'Ranks high with a carpet floor.'}],
            findings=['"local trait 3" and "local trait 4" read like the same thing.']))
        self.assertEqual(result['flags'], [{'id': flagged, 'kind': 'dislike', 'preference': 'Carpet', 'note': 'Ranks high with a carpet floor.',
                                            'title': self.store.listing(flagged)['title']}])
        self.assertEqual(ranking.status(self.store)['last_rerank']['review']['result']['findings'], result['findings'])

    def test_a_review_that_breaks_the_format_is_rejected_and_saves_nothing(self):
        explored = sorted(entry['rank'] for entry in self.ranked.values() if entry['exploration'])
        outside = self.by_rank(REVIEWED + 1)
        first, second = self.by_rank(1), self.by_rank(2)
        move = {'id': first, 'to_rank': 3, 'reason': 'Lower.', 'confidence': 0.5}
        cases = [
            ({'rerank': self.summary['id']}, 'exactly rerank'),
            (self.review(rerank=self.summary['id'] + 1), f'rerank must be {self.summary["id"]}'),
            (self.review(rerank=True), f'rerank must be {self.summary["id"]}'),
            (self.review(moves=[{**move, 'id': []}]), 'not one of the listings'),
            (self.review(flags=[{'id': first, 'kind': [], 'preference': 'Carpet', 'note': 'Carpet.'}]), 'not a stated'),
            (self.review(moves=[{**move, 'id': outside}]), 'not one of the listings'),
            (self.review(moves=[{**move, 'id': self.by_rank(explored[0])}]), 'exploration listing'),
            (self.review(moves=[{**move, 'to_rank': explored[0]}]), 'holds an exploration listing'),
            (self.review(moves=[move, {**move, 'id': second}]), 'two listings are moved'),
            (self.review(moves=[move, move]), 'moved twice'),
            (self.review(moves=[{**move, 'to_rank': 1}]), 'at rank 1 already'),
            (self.review(moves=[{**move, 'to_rank': self.listings + 1}]), 'whole number'),
            (self.review(moves=[{**move, 'confidence': 1.5}]), 'confidence'),
            (self.review(moves=[{**move, 'reason': ''}]), 'reason for moving'),
            (self.review(flags=[{'id': first, 'kind': 'dislike', 'preference': 'Noise', 'note': 'Loud.'}]), 'not a stated dislike'),
            (self.review(findings=['x'] * 11), 'at most 10'),
            (self.review(reasons=[{'id': first, 'reason': 'a'}, {'id': first, 'reason': 'b'}]), 'two reasons'),
        ]
        before = self.ranks()
        for value, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                save_review(self.store, value)
        self.assertEqual(self.ranks(), before)
        self.assertEqual(self.store.last_rerank()['review']['state'], 'waiting')

    def test_the_check_reads_the_rerank_as_the_review_saw_it_when_labels_and_listings_change_meanwhile(self):
        favorite = max(self.identifiers[:self.favorites], key=lambda identifier: self.ranked[identifier]['rank'])
        changed = next(identifier for identifier in sorted(self.ranked, key=lambda item: -self.ranked[item]['rank'])
                       if identifier not in self.identifiers[:self.favorites] and not self.ranked[identifier]['exploration']
                       and self.ranked[identifier]['rank'] <= REVIEWED)
        self.store.feedback(self.identifiers[30], favorite=True)
        self.store.upsert_listing(listing(id=changed, title='Changed', photos=[]))
        with self.assertRaisesRegex(ValueError, 'changed since the rerank'):
            save_review(self.store, self.review(reasons=[{'id': changed, 'reason': 'For it: [x].'}]))
        result = save_review(self.store, self.review(moves=[{'id': favorite, 'to_rank': self.free_rank(range(1, 50)), 'reason': 'Better.', 'confidence': 0.8}]))
        self.assertEqual(result['check']['before']['mean'], self.summary['held_out_mean'])
        self.assertEqual(result['outcome'], 'kept')
        self.assertNotIn(changed, self.store.ranked_listings())
        self.assertIsNone(self.store.review_snapshot(self.summary['id']))


class StateFindingTests(ReviewTestCase):
    listings, favorites = 30, 1

    def test_a_large_weight_that_a_single_labeled_listing_carries_is_a_finding_of_the_rerank(self):
        title = self.store.listing(self.identifiers[0])['title']
        self.assertTrue(self.summary['findings'])
        for finding in self.summary['findings']:
            self.assertRegex(finding, rf'^The weight of \S.* \([+-]\d\.\d\d\) rests on a single labeled listing, {title}\.$')


class NoFavoriteTests(ReviewTestCase):
    listings, favorites = 25, 0

    def test_without_a_favorite_to_check_against_every_move_is_reverted(self):
        moved = self.by_rank(5)
        before = self.ranks()
        result = save_review(self.store, self.review(moves=[{'id': moved, 'to_rank': 1, 'reason': 'Better.', 'confidence': 0.9}]))
        self.assertEqual((result['outcome'], result['reverted_because']), ('reverted', 'No favorite to check the moves against.'))
        self.assertEqual(self.ranks(), before)


class JobTests(ReviewTestCase):
    listings, favorites = 24, 3

    def run_job(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(ranking.run(), 0)
        return output.getvalue().strip().splitlines()

    def test_the_job_wakes_the_agent_for_a_waiting_review_and_gives_up_after_three_attempts(self):
        for attempt in range(1, REVIEW_ATTEMPTS + 1):
            lines = self.run_job()
            self.assertEqual(lines[-1], '{"wakeAgent": true}')
            task_file = Path(lines[1].removeprefix('Review task file: '))
            self.assertEqual(task_file.parent, self.store.path.parent / 'review')
            text = task_file.read_text(encoding='utf-8')
            task = json.loads(text.split('```json\n')[1].split('\n```')[0])
            self.assertEqual(task['rerank'], self.summary['id'])
            self.assertIn(f'ranking.py" review-save "{(self.home / "cache/scratch").resolve().as_posix()}/review-{self.summary["id"]}.json"', text)
            self.assertEqual(self.store.last_rerank()['review']['attempts'], attempt)
        lines = self.run_job()
        self.assertEqual(lines[-1], '{"wakeAgent": false}')
        review = self.store.last_rerank()['review']
        self.assertEqual(review['state'], 'failed')
        self.assertIn('not saved in 3 attempts', review['result']['error'])
        self.assertIsNone(self.store.review_snapshot(self.summary['id']))
        self.assertEqual(self.run_job(), ['No review waits. The latest review is failed.', '{"wakeAgent": false}'])

    def test_the_job_removes_the_task_and_review_files_of_earlier_reranks(self):
        self.run_job()
        scratch = self.home / 'cache' / 'scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / f'review-{self.summary["id"]}.json').write_text('{}', encoding='utf-8')
        later = rerank(self.store, seed=1)
        self.run_job()
        self.assertEqual(sorted(path.name for path in (self.store.path.parent / 'review').iterdir()), [f'review-{later["id"]}.md'])
        self.assertEqual(list(scratch.glob('review-*.json')), [])

    def test_the_job_does_not_wake_the_agent_without_a_waiting_review(self):
        save_review(self.store, self.review())
        self.assertEqual(self.run_job(), ['No review waits. The latest review is done.', '{"wakeAgent": false}'])

    def test_a_failure_is_recorded_on_the_review_and_wakes_nobody(self):
        with mock.patch.object(ranking, 'pending_review', side_effect=RuntimeError('disk full')):
            lines = self.run_job()
        self.assertEqual(lines[-1], '{"wakeAgent": false}')
        self.assertIn('RuntimeError: disk full', lines[0])
        review = self.store.last_rerank()['review']
        self.assertEqual(review['state'], 'failed')
        self.assertIn('disk full', review['result']['error'])


class CommandTests(ReviewTestCase):
    listings, favorites = 24, 3

    def run_script(self, *arguments):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], capture_output=True, text=True, timeout=120,
                              env={**os.environ, 'HERMES_HOME': str(self.home)})

    def test_the_job_script_and_review_save_through_the_command_line(self):
        gate = subprocess.run([sys.executable, str(ROOT / 'profile/scripts/review-run.py')], capture_output=True, text=True, timeout=120,
                              env={**os.environ, 'HERMES_HOME': str(self.home)})
        self.assertEqual(gate.returncode, 0, gate.stderr)
        self.assertEqual(gate.stdout.strip().splitlines()[-1], '{"wakeAgent": true}')
        scratch = self.home / 'cache' / 'scratch'
        scratch.mkdir(parents=True)
        file = scratch / f'review-{self.summary["id"]}.json'
        file.write_text(json.dumps(self.review(moves=[{'id': self.by_rank(1), 'to_rank': 99, 'reason': 'Lower.', 'confidence': 0.5}])), encoding='utf-8')
        rejected = self.run_script('review-save', str(file))
        self.assertEqual(rejected.returncode, 1)
        self.assertIn('whole number from 1 to 24', rejected.stderr)
        self.assertTrue(file.exists())
        file.write_text(json.dumps(self.review(findings=['Few favorites.'])), encoding='utf-8')
        saved = self.run_script('review-save', str(file))
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.loads(saved.stdout)['findings'], ['Few favorites.'])
        self.assertFalse(file.exists())
        status = json.loads(self.run_script('status').stdout)
        self.assertEqual(status['last_rerank']['review']['state'], 'done')
        outside = self.home / 'review.json'
        outside.write_text('{}', encoding='utf-8')
        self.assertIn('scratch directory', self.run_script('review-save', str(outside)).stderr)


class ShippedJobTests(unittest.TestCase):
    def test_the_profile_ships_the_review_job_and_the_installers_turn_it_on(self):
        jobs = {job['id']: job for job in json.loads((ROOT / 'profile/cron/jobs.json').read_text(encoding='utf-8'))['jobs']}
        job = jobs['apt-scraper-review']
        self.assertEqual((job['skills'], job['schedule'], job['deliver']), (['ranking'], 'every 1m', 'local'))
        self.assertEqual(set(job['enabled_toolsets']), {'terminal', 'file'})
        self.assertTrue((ROOT / 'profile/scripts' / job['script']).is_file())
        self.assertIn("cron', 'resume', 'apt-scraper-review'", (ROOT / 'install.ps1').read_text(encoding='utf-8'))
        self.assertIn('cron resume apt-scraper-review', (ROOT / 'install.sh').read_text(encoding='utf-8'))


class MigrationTests(unittest.TestCase):
    def test_a_store_before_the_review_pass_keeps_its_reranks_without_a_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listings.sqlite3'
            version = MIGRATIONS.index(review_pass)
            with closing(sqlite3.connect(path)) as connection:
                for migration in MIGRATIONS[:version]:
                    migration(connection)
                connection.execute("INSERT INTO reranks (finished_at, result) VALUES ('2026-10-20T10:00:00+00:00', '{\"ranked\": 3}')")
                connection.execute(f'PRAGMA user_version = {version}')
                connection.commit()
            store = ListingStore(path)
            self.assertEqual(store.schema_version, len(MIGRATIONS))
            self.assertEqual(store.last_rerank(), {'ranked': 3, 'id': 1, 'finished_at': '2026-10-20T10:00:00+00:00', 'review': None})
            self.assertIsNone(store.review_snapshot(1))
            store.update_settings(**SETTINGS)
            store.upsert_listing(listing(id='zillow:1'))
            self.assertIsNone(pending_review(store))


if __name__ == '__main__':
    unittest.main()
