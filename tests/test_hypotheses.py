import copy
import json
import hashlib
import sqlite3
import sys
from collections.abc import Generator
from pathlib import Path
from contextlib import closing, contextmanager
from typing import TypedDict, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from test_rerank import SETTINGS, StoreTestCase, cpu_budget, fill, least_cpu_seconds, speed_factor  # noqa: E402
from rerank import RerankReport, rerank  # noqa: E402
from hypotheses import Citation, HypothesisTask, prepare, save, save_judgments, pending_judgments, decide, active, assess  # noqa: E402
from learning import PairPrediction, RankPrediction  # noqa: E402
from scoring import NoteReading, fit_model  # noqa: E402
from store import ListingStore, single, select  # noqa: E402
from store_copy import copy_database  # noqa: E402
from extraction_retest import CacheManifest  # noqa: E402
from hypothesis_trial import JudgedListing, proposal_file, judgment_files, apply_judgments  # noqa: E402
from test_gallery_store import listing  # noqa: E402
from test_scoring import entry, inputs  # noqa: E402
from learning import prediction  # noqa: E402
from shapes import Settings, loads  # noqa: E402


class ProposalFixture(TypedDict):
    """A hypothesis proposal as the review file states it."""
    phrase: str
    kind: str
    evidence: list[Citation]


class ProposalReview(TypedDict):
    """A hypothesis review file: the rerank it reviews, no reasons, flags or findings, and its proposals."""
    rerank: int
    reasons: list[object]
    flags: list[object]
    findings: list[object]
    hypotheses: list[ProposalFixture]


def required[T](value: T | None) -> T:
    """A value the store has, which the test needs."""
    if value is None:
        raise AssertionError('The store lacks what the test needs.')
    return value


def likes_of(settings: Settings) -> list[str]:
    """The stated likes of settings that state preferences."""
    if 'preferences' not in settings or 'likes' not in settings['preferences']:
        raise AssertionError('The settings state no likes.')
    return settings['preferences']['likes']


def pair_of(predicted: PairPrediction | RankPrediction | None) -> PairPrediction:
    if predicted is None or predicted['kind'] != 'pair':
        raise AssertionError('The prediction is not of a pair.')
    return predicted


def rank_of(predicted: PairPrediction | RankPrediction | None) -> RankPrediction:
    if predicted is None or predicted['kind'] != 'rank':
        raise AssertionError('The prediction is not of a rank.')
    return predicted


class HypothesisTests(StoreTestCase):
    home: Path
    store: ListingStore
    identifiers: list[str]
    task: HypothesisTask
    proposal: ProposalFixture

    def test_a_concurrent_owner_decision_wins_over_an_assessment(self) -> None:
        for decision, expected in [('reject', 'retired'), ('confirm', 'confirmed'), ('new label', 'active')]:
            with self.subTest(decision=decision):
                self.store = type(self.store)(self.home / f'{decision}.sqlite3')
                _ = self.store.update_settings(**SETTINGS)
                _ = save(self.store, self.prepare_case())
                identifier = active(self.store)[0]['id']
                save_judgments(self.store, [
                    {'hypothesis': identifier, 'id': listing_id,
                     'fingerprint': self.store.listing(listing_id)['content_fingerprint'],
                     'verdict': 'has' if index % 2 == 0 else 'rules out', 'evidence': 'Fixture.'}
                    for index, listing_id in enumerate(self.identifiers)])
                _ = assess(self.store)
                original = self.store.connect
                decided = False

                @contextmanager
                def interleaved(write: bool = False) -> Generator[sqlite3.Connection]:
                    nonlocal decided
                    if write and not decided:
                        decided = True
                        if decision == 'new label':
                            self.store.feedback(self.identifiers[1], favorite=True)
                        else:
                            decide(self.store, identifier, decision)
                    with original(write=write) as connection:
                        yield connection

                with mock.patch.object(self.store, 'connect', interleaved):
                    result = assess(self.store)[0]
                with self.store.connect() as connection:
                    actual = cast(str, single(connection, 'SELECT state FROM hypotheses WHERE id = ?', (identifier,))[0])
                self.assertEqual(actual, expected)
                self.assertEqual(result['state'], expected)
                if 'applied' not in result:
                    raise AssertionError('The assessment does not say whether it was applied.')
                self.assertFalse(result['applied'])
                self.assertFalse(result['survives'])

    def test_a_legacy_rerank_without_a_review_cannot_prepare_a_pass(self) -> None:
        _ = fill(self.store, 8, 1)
        _ = rerank(self.store, seed=1)
        with self.store.connect(write=True) as connection:
            _ = connection.execute('DELETE FROM reviews')
        self.assertIsNone(required(self.store.last_rerank())['review'])
        self.assertIsNone(prepare(self.store))

    def test_feedback_before_judgments_is_not_evidence_for_survival(self) -> None:
        _ = save(self.store, self.prepare_case())
        hypothesis = active(self.store)[0]
        for identifier in self.identifiers[1:21]:
            self.store.feedback(identifier, favorite=True)
        save_judgments(self.store, [
            {'hypothesis': hypothesis['id'], 'id': identifier,
             'fingerprint': self.store.listing(identifier)['content_fingerprint'],
             'verdict': 'has' if index < 21 else 'rules out', 'evidence': 'Fixture.'}
            for index, identifier in enumerate(self.identifiers)])
        result = assess(self.store)[0]
        self.assertGreater(abs(result['weight']), 0.1)
        self.assertEqual(result['comparisons'], {'rank': [], 'pair': []})
        self.assertFalse(result['survives'])

    def test_judgment_tasks_include_all_stored_listing_fields_and_full_description(self) -> None:
        _ = save(self.store, self.prepare_case())
        identifier = self.identifiers[0]
        description = 'Details. ' * 300 + 'Final evidence.'
        _ = self.store.upsert_listing(listing(id=identifier, price=2345, address='456 New Street',
                                             rooms='3 bedrooms, 2 bathrooms', size='1234 sq ft', description=description))
        report = judgment_files(self.store, self.home / 'judgments')
        text = Path(report['tasks'][0]['task']).read_text()
        payload = cast(list[JudgedListing], loads(text.split('```json\n', 1)[1].split('\n```', 1)[0]))
        content = next(item['listing'] for item in payload if item['id'] == identifier)
        self.assertEqual(content['address'], '456 New Street')
        self.assertEqual(content['price'], '2345 USD a month')
        self.assertEqual(content['rooms'], '3 bedrooms, 2 bathrooms')
        self.assertEqual(content['size'], '1234 sq ft')
        self.assertEqual(content['description'], description)

    def test_missing_cached_files_wait_while_available_tasks_continue_and_escaping_paths_fail(self) -> None:
        value = self.prepare_case()
        value['hypotheses'][0]['kind'] = 'visual'
        _ = save(self.store, value)
        cache = self.home / 'photos'
        for number, identifier in enumerate(self.identifiers[:2]):
            folder = cache / str(number)
            folder.mkdir(parents=True)
            _ = (folder / 'sheet.jpg').write_bytes(b'cached sheet')
            if number:
                _ = (folder / 'photo.jpg').write_bytes(b'cached photo')
            _ = (folder / 'task.json').write_text(json.dumps({'id': identifier,
                'fingerprint': self.store.listing(identifier)['content_fingerprint'],
                'files': ['photo.jpg'], 'sheets': ['sheet.jpg']}))
        report = judgment_files(self.store, self.home / 'judgments', cache)
        self.assertEqual(report['visual_listing_reads'], 1)
        self.assertIn(self.identifiers[0], report['visual_waiting_for_cached_photos'])
        self.assertEqual(set(report['tasks'][0]['expected']), {self.identifiers[1]})
        manifest_file = cache / '0' / 'task.json'
        manifest = cast(CacheManifest, loads(manifest_file.read_text()))
        manifest['files'] = ['../../outside.jpg']
        _ = manifest_file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'inside the supplied cache'):
            _ = judgment_files(self.store, self.home / 'judgments', cache)

    def test_prediction_marks_ties_and_existing_positive_labels(self) -> None:
        snapshot = inputs([entry('a', price=1000), entry('b', price=1000)])
        pair = pair_of(prediction(snapshot, {'kind': 'pair', 'listing': 'a', 'other': 'b'}))
        self.assertEqual(pair['value'], 0.5)
        self.assertTrue(pair['tied'])
        snapshot['listings'][0].update(favorite=True, rank=1)
        contact = rank_of(prediction(snapshot, {'kind': 'contact', 'listing': 'a', 'other': None}))
        self.assertTrue(contact['already_positive'])
        self.assertEqual(contact['displayed_rank'], 1)

    def prepare_case(self) -> ProposalReview:
        self.identifiers = fill(self.store, 30, 1)
        _ = rerank(self.store, seed=1)
        self.task = required(prepare(self.store))
        self.proposal = {'phrase': 'original wood trim', 'kind': 'textual',
                         'evidence': [{'id': self.identifiers[0], 'text': required(self.task['records'][0]['record'])['summary']}]}
        return {'rerank': self.task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': [self.proposal]}

    def test_one_request_per_new_label_state_and_no_rank_moves(self) -> None:
        value = self.prepare_case()
        self.assertIsNone(prepare(self.store))
        before = {item['id']: item['rank'] for item in self.store.browse(page_size=100)['items']}
        with self.assertRaises(ValueError):
            _ = save(self.store, {**value, 'moves': [{'id': self.identifiers[0], 'to_rank': 20}]})
        _ = save(self.store, value)
        self.assertEqual(before, {item['id']: item['rank'] for item in self.store.browse(page_size=100)['items']})
        self.assertEqual(len(active(self.store)), 1)
        _ = rerank(self.store, seed=1)
        self.assertIsNone(prepare(self.store))

    def test_pass_storage_is_plain_json_without_a_duplicate_input_snapshot(self) -> None:
        _ = self.prepare_case()
        with self.store.connect() as connection:
            columns = {cast(str, row['name']) for row in select(connection, 'PRAGMA table_info(hypothesis_passes)')}
            saved = cast(str, single(connection, 'SELECT task FROM hypothesis_passes')['task'])
        self.assertNotIn('inputs', columns)
        self.assertIsInstance(saved, str)
        self.assertEqual(loads(saved), loads(json.dumps(self.task)))

    def test_removing_labels_does_not_advance_the_twenty_label_grace_period(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        for index in range(20):
            self.store.feedback(self.identifiers[1], favorite=index % 2 == 0)
        result = assess(self.store)
        self.assertEqual(result[0]['labels'], 10)
        self.assertEqual(result[0]['state'], 'active')
        self.store.add_note('Changed my mind.', listing_id=self.identifiers[2])
        note = self.store.unread_notes()[0]['id']
        self.store.save_readings(notes=[(note, {'sentiment': 'positive', 'likes': [], 'dislikes': []})])
        self.store.save_readings(notes=[(note, {'sentiment': 'neutral', 'likes': [], 'dislikes': []})])
        self.assertEqual(assess(self.store)[0]['labels'], 11)

    def test_a_removal_after_a_completed_pass_does_not_request_another_pass(self) -> None:
        _ = save(self.store, self.prepare_case())
        self.store.feedback(self.identifiers[1], favorite=True)
        _ = rerank(self.store, seed=1)
        task = required(prepare(self.store))
        _ = save(self.store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': []})
        self.store.feedback(self.identifiers[1], favorite=False)
        _ = rerank(self.store, seed=1)
        self.assertIsNone(prepare(self.store))

    def test_legacy_labels_receive_one_initial_pass_and_removal_does_not_repeat_it(self) -> None:
        identifiers = fill(self.store, 8, 2)
        with self.store.connect(write=True) as connection:
            _ = connection.execute('DELETE FROM learning_events')
        _ = rerank(self.store, seed=1)
        task = required(prepare(self.store))
        _ = save(self.store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': []})
        self.store.feedback(identifiers[1], favorite=False)
        _ = rerank(self.store, seed=1)
        self.assertTrue(prepare(self.store) is None)

    def test_named_judgments_have_zero_prior_and_confirmation_becomes_a_preference(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        hypothesis = active(self.store)[0]
        self.assertIsNone(hypothesis['direction'])
        with self.assertRaisesRegex(ValueError, 'learned direction'):
            decide(self.store, hypothesis['id'], 'confirm')
        tasks = pending_judgments(self.store)
        self.assertEqual(sum(len(task['listings']) for task in tasks), 30)
        entries = [{'id': identifier, 'fingerprint': self.store.listing(identifier)['content_fingerprint'],
                    'hypothesis': hypothesis['id'], 'verdict': 'has' if index % 2 == 0 else 'rules out', 'evidence': 'Original trim.'}
                   for index, identifier in enumerate(self.identifiers)]
        save_judgments(self.store, entries)
        model = fit_model(self.store.ranking_inputs())
        index = model.design.index('hypothesis:original wood trim')
        self.assertIsNotNone(index)
        self.assertEqual(model.design.prior_mean[required(index)], 0)
        _ = assess(self.store)
        decide(self.store, hypothesis['id'], 'confirm')
        decide(self.store, hypothesis['id'], 'confirm')
        self.assertIn('original wood trim', likes_of(self.store.settings()))
        self.assertEqual(likes_of(self.store.settings()).count('original wood trim'), 1)
        self.assertEqual(active(self.store), [])
        self.assertEqual(self.store.feature_records()[self.identifiers[0]]['preferences']['original wood trim']['verdict'], 'has')

    def test_rejected_phrase_cannot_return_and_unknown_evidence_rejects_the_whole_review(self) -> None:
        value = self.prepare_case()
        bad = copy.deepcopy(value)
        bad['hypotheses'][0]['evidence'][0]['text'] = 'An unsupported claim.'
        with self.assertRaises(ValueError):
            _ = save(self.store, bad)
        self.assertEqual(active(self.store), [])
        _ = save(self.store, value)
        decide(self.store, active(self.store)[0]['id'], 'reject')
        self.store.feedback(self.identifiers[1], favorite=True)
        _ = rerank(self.store, seed=1)
        task = required(prepare(self.store))
        self.assertIn('original wood trim', task['retired'])
        with self.assertRaises(ValueError):
            _ = save(self.store, {**value, 'rerank': task['rerank']})

    def test_uninformative_hypothesis_retires_after_twenty_new_labels(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        for identifier in self.identifiers[1:21]:
            self.store.feedback(identifier, favorite=True)
        result = assess(self.store)
        self.assertEqual(result[0]['state'], 'retired')
        self.assertEqual(result[0]['weight'], 0)
        self.assertEqual(active(self.store), [])
        _ = json.dumps(result, allow_nan=False)

    def test_no_survivor_after_forty_labels_stops_new_proposals(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        for index in range(40):
            self.store.answer_pair(self.identifiers[index % 20], self.identifiers[20 + index // 20])
        _ = assess(self.store)
        _ = rerank(self.store, seed=1)
        self.assertEqual(required(prepare(self.store))['max_hypotheses'], 0)

    def test_six_live_cap_and_three_proposals_per_pass(self) -> None:
        value = self.prepare_case()
        for index in range(2):
            if index:
                self.store.feedback(self.identifiers[1], favorite=True)
                _ = rerank(self.store, seed=1)
                self.task = required(prepare(self.store))
            proposals = [{**self.proposal, 'phrase': f'ornamental detail {index * 3 + number}'} for number in range(3)]
            _ = save(self.store, {**value, 'rerank': self.task['rerank'], 'hypotheses': proposals})
        self.assertEqual(len(active(self.store)), 6)
        self.store.feedback(self.identifiers[2], favorite=True)
        _ = rerank(self.store, seed=1)
        self.assertEqual(required(prepare(self.store))['max_hypotheses'], 0)

    def test_visual_judgments_are_limited_to_labeled_listings_and_top_fifty(self) -> None:
        identifiers = fill(self.store, 80, 1)
        self.store.set_availability(identifiers[0], 'unavailable')
        _ = rerank(self.store, seed=1)
        task = required(prepare(self.store))
        summary = next(required(item['record'])['summary'] for item in task['records'] if item['id'] == identifiers[0])
        _ = save(self.store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': [
            {'phrase': 'white kitchen cabinet doors', 'kind': 'visual', 'evidence': [{'id': identifiers[0], 'text': summary}]}]})
        tasks = pending_judgments(self.store)
        self.assertEqual(len(tasks), 51)
        self.assertTrue(all(task['photos_required'] for task in tasks))
        selected = {item['id'] for task in tasks for item in task['listings']}
        self.assertIn(identifiers[0], selected)
        excluded = next(identifier for identifier in identifiers if identifier not in selected)
        with self.assertRaises(ValueError):
            save_judgments(self.store, [{'hypothesis': active(self.store)[0]['id'], 'id': excluded,
                                        'fingerprint': self.store.listing(excluded)['content_fingerprint'], 'verdict': 'has', 'evidence': 'Photos.'}])

    def test_judgment_files_reuse_the_preference_format_and_reject_incomplete_batches(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        directory = self.home / 'judgments'
        report = judgment_files(self.store, directory)
        self.assertEqual(report['text_batches'], 2)
        for task in report['tasks']:
            entries = [{'id': identifier, 'fingerprint': expected['fingerprint'], 'preferences': {
                phrase: {'verdict': 'has', 'evidence': 'Text.'} for phrase in expected['hypotheses']}}
                for identifier, expected in task['expected'].items()]
            _ = Path(task['output']).write_text(json.dumps({'judgments': entries}))
        first = report['tasks'][0]
        content = Path(first['output']).read_text()
        _ = Path(first['output']).write_text(json.dumps({'judgments': []}))
        with self.assertRaises(ValueError):
            _ = apply_judgments(self.store, directory)
        self.assertEqual(sum(len(task['listings']) for task in pending_judgments(self.store)), 30)
        _ = Path(first['output']).write_text(content)
        self.assertEqual(apply_judgments(self.store, directory)['saved'], 30)
        self.assertEqual(pending_judgments(self.store), [])

    def test_proposal_task_contains_no_move_output_and_only_one_is_handed(self) -> None:
        _ = fill(self.store, 10, 1)
        _ = rerank(self.store, seed=1)
        report = proposal_file(self.store, self.home / 'tasks')
        if 'task' not in report:
            raise AssertionError('No hypothesis pass was requested.')
        text = Path(report['task']).read_text()
        self.assertIn('There is no moves field', text)
        self.assertEqual(proposal_file(self.store, self.home / 'tasks')['requested_passes'], 0)

    def test_an_unlabeled_listing_does_not_trigger_another_model_pass(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        _ = self.store.upsert_listing(listing(id='new:listing'))
        _ = rerank(self.store, seed=1)
        self.assertIsNone(prepare(self.store))

    def test_note_labels_advance_the_retirement_clock_and_retries_do_not(self) -> None:
        value = self.prepare_case()
        _ = save(self.store, value)
        for identifier in self.identifiers[1:21]:
            self.store.add_note('I like this one.', listing_id=identifier)
        readings: list[tuple[int, NoteReading]] = [(note['id'], {'sentiment': 'positive', 'likes': [], 'dislikes': []}) for note in self.store.unread_notes()]
        self.store.save_readings(notes=readings)
        self.store.save_readings(notes=readings)
        result = assess(self.store)
        self.assertEqual(result[0]['labels'], 20)
        self.assertEqual(result[0]['state'], 'retired')

    def test_six_hypotheses_keep_the_300_listing_50_favorite_rerank_under_two_seconds(self) -> None:
        from ranking_hypothesis_experiment import populate_benchmark
        populate_benchmark(self.store)
        seconds: float
        result: RerankReport
        seconds, result = least_cpu_seconds(lambda: rerank(self.store, seed=1))
        self.assertEqual(sum(name.startswith('hypothesis:') for name in result['weights']), 6)
        self.assertLess(seconds, cpu_budget(), speed_factor())

    def test_a_predictive_fixture_hypothesis_survives_twenty_new_labels(self) -> None:
        from ranking_hypothesis_experiment import run
        result = run(self.home / 'survival')
        survivors = [item for item in result['assessments'] if item['survives']]
        self.assertEqual([item['phrase'] for item in survivors], ['original wood trim'])
        self.assertGreater(survivors[0]['rank_summary']['mean_improvement'], 0)
        self.assertEqual(result['requested_passes'], result['reranks_with_new_labels'])
        store = type(self.store)(self.home / 'survival' / 'listings.sqlite3')
        store.request_contact('zillow:0000')
        self.assertEqual(len(assess(store)[0]['comparisons']['rank']), 20)


class CopyTests(StoreTestCase):
    home: Path
    store: ListingStore

    def test_filesystem_copy_includes_committed_wal_without_opening_the_source_in_sqlite(self) -> None:
        connection = sqlite3.connect(self.store.path)
        self.addCleanup(connection.close)
        _ = connection.execute('PRAGMA journal_mode = WAL')
        _ = connection.execute('CREATE TABLE copy_marker (value TEXT)')
        _ = connection.execute("INSERT INTO copy_marker VALUES ('committed')")
        connection.commit()
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        destination = self.home / 'copy' / 'listings.sqlite3'
        connect = sqlite3.connect
        with mock.patch('store_copy.sqlite3.connect', wraps=connect) as opened:
            _ = copy_database(self.store.path, destination)
        self.assertEqual(opened.call_args_list, [mock.call(destination.resolve())])
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(destination)) as copied:
            self.assertEqual(cast(tuple[str], copied.execute('SELECT value FROM copy_marker').fetchone())[0], 'committed')
        with self.assertRaises(ValueError):
            _ = copy_database(self.store.path, destination)
