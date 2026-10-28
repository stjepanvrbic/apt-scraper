import copy
import json
import hashlib
import sqlite3
import sys
from pathlib import Path
from contextlib import closing
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_rerank import StoreTestCase, fill, least_cpu_seconds  # noqa: E402
from rerank import rerank  # noqa: E402
from hypotheses import prepare, save, save_judgments, pending_judgments, decide, active, assess  # noqa: E402
from scoring import fit_model  # noqa: E402
from store_copy import copy_database  # noqa: E402
from hypothesis_trial import proposal_file, judgment_files, apply_judgments  # noqa: E402
from test_gallery_store import listing  # noqa: E402
from test_scoring import entry, inputs  # noqa: E402
from learning import prediction  # noqa: E402


class HypothesisTests(StoreTestCase):
    def test_a_legacy_rerank_without_a_review_cannot_prepare_a_pass(self):
        fill(self.store, 8, 1)
        rerank(self.store, seed=1)
        with self.store.connect(write=True) as connection:
            connection.execute('DELETE FROM reviews')
        self.assertIsNone(self.store.last_rerank()['review'])
        self.assertIsNone(prepare(self.store))

    def test_feedback_before_judgments_is_not_evidence_for_survival(self):
        save(self.store, self.prepare_case())
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

    def test_judgment_tasks_include_all_stored_listing_fields_and_full_description(self):
        save(self.store, self.prepare_case())
        identifier = self.identifiers[0]
        description = 'Details. ' * 300 + 'Final evidence.'
        self.store.upsert_listing(listing(id=identifier, price=2345, address='456 New Street',
                                         rooms='3 bedrooms, 2 bathrooms', size='1234 sq ft', description=description))
        report = judgment_files(self.store, self.home / 'judgments')
        text = Path(report['tasks'][0]['task']).read_text()
        payload = json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])
        content = next(item['listing'] for item in payload if item['id'] == identifier)
        self.assertEqual(content['address'], '456 New Street')
        self.assertEqual(content['price'], '2345 USD a month')
        self.assertEqual(content['rooms'], '3 bedrooms, 2 bathrooms')
        self.assertEqual(content['size'], '1234 sq ft')
        self.assertEqual(content['description'], description)

    def test_missing_cached_files_wait_while_available_tasks_continue_and_escaping_paths_fail(self):
        value = self.prepare_case()
        value['hypotheses'][0]['kind'] = 'visual'
        save(self.store, value)
        cache = self.home / 'photos'
        for number, identifier in enumerate(self.identifiers[:2]):
            folder = cache / str(number)
            folder.mkdir(parents=True)
            (folder / 'sheet.jpg').write_bytes(b'cached sheet')
            if number:
                (folder / 'photo.jpg').write_bytes(b'cached photo')
            (folder / 'task.json').write_text(json.dumps({'id': identifier,
                'fingerprint': self.store.listing(identifier)['content_fingerprint'],
                'files': ['photo.jpg'], 'sheets': ['sheet.jpg']}))
        report = judgment_files(self.store, self.home / 'judgments', cache)
        self.assertEqual(report['visual_listing_reads'], 1)
        self.assertIn(self.identifiers[0], report['visual_waiting_for_cached_photos'])
        self.assertEqual(set(report['tasks'][0]['expected']), {self.identifiers[1]})
        manifest_file = cache / '0' / 'task.json'
        manifest = json.loads(manifest_file.read_text())
        manifest['files'] = ['../../outside.jpg']
        manifest_file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'inside the supplied cache'):
            judgment_files(self.store, self.home / 'judgments', cache)

    def test_prediction_marks_ties_and_existing_positive_labels(self):
        snapshot = inputs([entry('a', price=1000), entry('b', price=1000)])
        pair = prediction(snapshot, {'kind': 'pair', 'listing': 'a', 'other': 'b'})
        self.assertEqual(pair['value'], 0.5)
        self.assertTrue(pair['tied'])
        snapshot['listings'][0].update(favorite=True, rank=1)
        contact = prediction(snapshot, {'kind': 'contact', 'listing': 'a', 'other': None})
        self.assertTrue(contact['already_positive'])
        self.assertEqual(contact['displayed_rank'], 1)

    def prepare_case(self):
        self.identifiers = fill(self.store, 30, 1)
        rerank(self.store, seed=1)
        self.task = prepare(self.store)
        self.proposal = {'phrase': 'original wood trim', 'kind': 'textual',
                         'evidence': [{'id': self.identifiers[0], 'text': self.task['records'][0]['record']['summary']}]}
        return {'rerank': self.task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': [self.proposal]}

    def test_one_request_per_new_label_state_and_no_rank_moves(self):
        value = self.prepare_case()
        self.assertIsNone(prepare(self.store))
        before = {item['id']: item['rank'] for item in self.store.browse(page_size=100)['items']}
        with self.assertRaises(ValueError):
            save(self.store, {**value, 'moves': [{'id': self.identifiers[0], 'to_rank': 20}]})
        save(self.store, value)
        self.assertEqual(before, {item['id']: item['rank'] for item in self.store.browse(page_size=100)['items']})
        self.assertEqual(len(active(self.store)), 1)
        rerank(self.store, seed=1)
        self.assertIsNone(prepare(self.store))

    def test_pass_storage_is_plain_json_without_a_duplicate_input_snapshot(self):
        self.prepare_case()
        with self.store.connect() as connection:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(hypothesis_passes)')}
            saved = connection.execute('SELECT task FROM hypothesis_passes').fetchone()['task']
        self.assertNotIn('inputs', columns)
        self.assertIsInstance(saved, str)
        self.assertEqual(json.loads(saved), json.loads(json.dumps(self.task)))

    def test_removing_labels_does_not_advance_the_twenty_label_grace_period(self):
        value = self.prepare_case()
        save(self.store, value)
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

    def test_a_removal_after_a_completed_pass_does_not_request_another_pass(self):
        save(self.store, self.prepare_case())
        self.store.feedback(self.identifiers[1], favorite=True)
        rerank(self.store, seed=1)
        task = prepare(self.store)
        save(self.store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': []})
        self.store.feedback(self.identifiers[1], favorite=False)
        rerank(self.store, seed=1)
        self.assertIsNone(prepare(self.store))

    def test_legacy_labels_receive_one_initial_pass_and_removal_does_not_repeat_it(self):
        identifiers = fill(self.store, 8, 2)
        with self.store.connect(write=True) as connection:
            connection.execute('DELETE FROM learning_events')
        rerank(self.store, seed=1)
        task = prepare(self.store)
        save(self.store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': []})
        self.store.feedback(identifiers[1], favorite=False)
        rerank(self.store, seed=1)
        self.assertTrue(prepare(self.store) is None)

    def test_named_judgments_have_zero_prior_and_confirmation_becomes_a_preference(self):
        value = self.prepare_case()
        save(self.store, value)
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
        self.assertEqual(model.design.prior_mean[index], 0)
        assess(self.store)
        decide(self.store, hypothesis['id'], 'confirm')
        decide(self.store, hypothesis['id'], 'confirm')
        self.assertIn('original wood trim', self.store.settings()['preferences']['likes'])
        self.assertEqual(self.store.settings()['preferences']['likes'].count('original wood trim'), 1)
        self.assertEqual(active(self.store), [])
        self.assertEqual(self.store.feature_records()[self.identifiers[0]]['preferences']['original wood trim']['verdict'], 'has')

    def test_rejected_phrase_cannot_return_and_unknown_evidence_rejects_the_whole_review(self):
        value = self.prepare_case()
        bad = copy.deepcopy(value)
        bad['hypotheses'][0]['evidence'][0]['text'] = 'An unsupported claim.'
        with self.assertRaises(ValueError):
            save(self.store, bad)
        self.assertEqual(active(self.store), [])
        save(self.store, value)
        decide(self.store, active(self.store)[0]['id'], 'reject')
        self.store.feedback(self.identifiers[1], favorite=True)
        rerank(self.store, seed=1)
        task = prepare(self.store)
        self.assertIn('original wood trim', task['retired'])
        with self.assertRaises(ValueError):
            save(self.store, {**value, 'rerank': task['rerank']})

    def test_uninformative_hypothesis_retires_after_twenty_new_labels(self):
        value = self.prepare_case()
        save(self.store, value)
        for identifier in self.identifiers[1:21]:
            self.store.feedback(identifier, favorite=True)
        result = assess(self.store)
        self.assertEqual(result[0]['state'], 'retired')
        self.assertEqual(result[0]['weight'], 0)
        self.assertEqual(active(self.store), [])
        json.dumps(result, allow_nan=False)

    def test_no_survivor_after_forty_labels_stops_new_proposals(self):
        value = self.prepare_case()
        save(self.store, value)
        for index in range(40):
            self.store.answer_pair(self.identifiers[index % 20], self.identifiers[20 + index // 20])
        assess(self.store)
        rerank(self.store, seed=1)
        self.assertEqual(prepare(self.store)['max_hypotheses'], 0)

    def test_six_live_cap_and_three_proposals_per_pass(self):
        value = self.prepare_case()
        for index in range(2):
            if index:
                self.store.feedback(self.identifiers[1], favorite=True)
                rerank(self.store, seed=1)
                self.task = prepare(self.store)
            proposals = [{**self.proposal, 'phrase': f'ornamental detail {index * 3 + number}'} for number in range(3)]
            save(self.store, {**value, 'rerank': self.task['rerank'], 'hypotheses': proposals})
        self.assertEqual(len(active(self.store)), 6)
        self.store.feedback(self.identifiers[2], favorite=True)
        rerank(self.store, seed=1)
        self.assertEqual(prepare(self.store)['max_hypotheses'], 0)

    def test_visual_judgments_are_limited_to_labeled_listings_and_top_fifty(self):
        identifiers = fill(self.store, 80, 1)
        self.store.set_availability(identifiers[0], 'unavailable')
        rerank(self.store, seed=1)
        task = prepare(self.store)
        summary = next(item['record']['summary'] for item in task['records'] if item['id'] == identifiers[0])
        save(self.store, {'rerank': task['rerank'], 'reasons': [], 'flags': [], 'findings': [], 'hypotheses': [
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

    def test_judgment_files_reuse_the_preference_format_and_reject_incomplete_batches(self):
        value = self.prepare_case()
        save(self.store, value)
        directory = self.home / 'judgments'
        report = judgment_files(self.store, directory)
        self.assertEqual(report['text_batches'], 2)
        for task in report['tasks']:
            entries = [{'id': identifier, 'fingerprint': expected['fingerprint'], 'preferences': {
                phrase: {'verdict': 'has', 'evidence': 'Text.'} for phrase in expected['hypotheses']}}
                for identifier, expected in task['expected'].items()]
            Path(task['output']).write_text(json.dumps({'judgments': entries}))
        first = report['tasks'][0]
        content = Path(first['output']).read_text()
        Path(first['output']).write_text(json.dumps({'judgments': []}))
        with self.assertRaises(ValueError):
            apply_judgments(self.store, directory)
        self.assertEqual(sum(len(task['listings']) for task in pending_judgments(self.store)), 30)
        Path(first['output']).write_text(content)
        self.assertEqual(apply_judgments(self.store, directory)['saved'], 30)
        self.assertEqual(pending_judgments(self.store), [])

    def test_proposal_task_contains_no_move_output_and_only_one_is_handed(self):
        fill(self.store, 10, 1)
        rerank(self.store, seed=1)
        report = proposal_file(self.store, self.home / 'tasks')
        text = Path(report['task']).read_text()
        self.assertIn('There is no moves field', text)
        self.assertEqual(proposal_file(self.store, self.home / 'tasks')['requested_passes'], 0)

    def test_an_unlabeled_listing_does_not_trigger_another_model_pass(self):
        value = self.prepare_case()
        save(self.store, value)
        self.store.upsert_listing(listing(id='new:listing'))
        rerank(self.store, seed=1)
        self.assertIsNone(prepare(self.store))

    def test_note_labels_advance_the_retirement_clock_and_retries_do_not(self):
        value = self.prepare_case()
        save(self.store, value)
        for identifier in self.identifiers[1:21]:
            self.store.add_note('I like this one.', listing_id=identifier)
        readings = [(note['id'], {'sentiment': 'positive', 'likes': [], 'dislikes': []}) for note in self.store.unread_notes()]
        self.store.save_readings(notes=readings)
        self.store.save_readings(notes=readings)
        result = assess(self.store)
        self.assertEqual(result[0]['labels'], 20)
        self.assertEqual(result[0]['state'], 'retired')

    def test_six_hypotheses_keep_the_300_listing_50_favorite_rerank_under_two_seconds(self):
        from ranking_hypothesis_experiment import populate_benchmark
        populate_benchmark(self.store)
        seconds, result = least_cpu_seconds(lambda: rerank(self.store, seed=1))
        self.assertEqual(sum(name.startswith('hypothesis:') for name in result['weights']), 6)
        self.assertLess(seconds, 2.0)

    def test_a_predictive_fixture_hypothesis_survives_twenty_new_labels(self):
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
    def test_filesystem_copy_includes_committed_wal_without_opening_the_source_in_sqlite(self):
        connection = sqlite3.connect(self.store.path)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode = WAL')
        connection.execute('CREATE TABLE copy_marker (value TEXT)')
        connection.execute("INSERT INTO copy_marker VALUES ('committed')")
        connection.commit()
        before = hashlib.sha256(self.store.path.read_bytes()).hexdigest()
        destination = self.home / 'copy' / 'listings.sqlite3'
        connect = sqlite3.connect
        with mock.patch('store_copy.sqlite3.connect', wraps=connect) as opened:
            copy_database(self.store.path, destination)
        self.assertEqual(opened.call_args_list, [mock.call(destination)])
        self.assertEqual(hashlib.sha256(self.store.path.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(destination)) as copied:
            self.assertEqual(copied.execute('SELECT value FROM copy_marker').fetchone()[0], 'committed')
        with self.assertRaises(ValueError):
            copy_database(self.store.path, destination)
