import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, redirect_stdout
from typing import TypedDict, cast
from unittest import mock
from urllib.request import Request, urlopen

from test_features_run import SCRIPT, FeaturesTestCase, shipped_jobs
import features
from contact import NotConnected
from geodata import Response
from schema import MIGRATIONS, compare_invitation, implicit_signals
from server import make_server
from store import CompareFailure, ListingStore, extraction_read

OFFLINE = NotConnected('gateway offline')


class InvitationShown(TypedDict):
    """The gallery's answer about the Compare invitation, the part the tests read."""
    shown: bool


class CompareInvitationTests(FeaturesTestCase):
    def prepare_records(self) -> None:
        _ = self.add(1, photos=0)
        _ = self.add(2, photos=0)
        for task in self.tasks(self.run_job()[0]):
            _ = self.save(task)

    def delivered(self) -> set[str]:
        return set(self.store.compare_invitation()['delivered'])

    def failed(self) -> CompareFailure:
        """The invitation's last failed attempt, which the test expects to be recorded."""
        failure = self.store.compare_invitation()['failed']
        if failure is None:
            raise AssertionError('No failed attempt is recorded.')
        return failure

    def asks(self, gateway: BaseException | None = OFFLINE) -> bool:
        with mock.patch('contact.require', side_effect=gateway):
            return self.features.status()['ask_to_compare']

    def status_command(self) -> tuple[int, features.Status]:
        """The exit code and report of `features.py status` as a conversation runs it, with the gateway connected."""
        output = io.StringIO()
        with mock.patch('contact.require'), redirect_stdout(output):
            code = features.main(['status'])
        return code, cast(features.Status, json.loads(output.getvalue()))

    def test_without_a_connected_gateway_the_gallery_shows_it_and_the_next_conversation_asks_once(self) -> None:
        self.assertFalse(self.store.compare_invitation()['due'])
        self.prepare_records()
        with mock.patch('contact.require', side_effect=NotConnected('gateway offline')), mock.patch('features.tell_user') as send:
            self.assertFalse(self.run_job()[1])
            self.assertFalse(self.run_job()[1])
            send.assert_not_called()
        self.assertEqual(self.delivered(), {'gallery'})
        self.assertEqual(self.features.status()['problems'], [])
        self.assertTrue(self.asks())
        result = subprocess.run([sys.executable, str(SCRIPT), 'compare-asked'], capture_output=True, text=True, env={**os.environ}, timeout=60)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {'recorded': True}), result.stderr)
        self.assertEqual(self.delivered(), {'gallery', 'conversation'})
        self.assertFalse(self.asks())
        self.assertEqual(self.features.compare_asked(), {'recorded': False})
        self.assertTrue(self.store.compare_invitation()['due'])

    def test_with_a_connected_gateway_it_is_sent_through_it_once_and_neither_the_gallery_nor_a_conversation_asks(self) -> None:
        self.prepare_records()
        with mock.patch('contact.require') as require, mock.patch('features.tell_user', return_value={'success': True}) as send:
            self.assertFalse(self.run_job()[1])
            self.assertFalse(self.run_job()[1])
            require.assert_called_with('gateway')
            send.assert_called_once()
            [sent] = cast(tuple[str], send.call_args.args)
            self.assertIn('5 to 8', sent)
            self.assertIn('Compare', sent)
        self.assertEqual(self.delivered(), {'gateway'})
        self.assertFalse(self.asks(gateway=None))

    def test_a_conversation_between_the_first_records_and_the_next_run_asks_only_without_a_gateway(self) -> None:
        self.prepare_records()
        self.assertEqual(self.delivered(), set())
        self.assertFalse(self.asks(gateway=None))
        self.assertTrue(self.asks())
        self.assertTrue(self.asks(gateway=RuntimeError('The connection check for gateway failed: timed out')))
        self.assertEqual(self.features.compare_asked(), {'recorded': True})
        self.assertFalse(self.asks())
        with mock.patch('contact.require'), mock.patch('features.tell_user') as send:
            _ = self.run_job()
        send.assert_not_called()
        self.assertEqual(self.delivered(), {'conversation', 'gallery'})

    def test_an_answer_ends_the_invitation_on_every_surface(self) -> None:
        self.prepare_records()
        with mock.patch('contact.require', side_effect=NotConnected('gateway offline')):
            _ = self.run_job()
        self.assertTrue(self.asks())
        self.store.answer_pair('zillow:1', 'zillow:2')
        self.assertFalse(self.store.compare_invitation()['due'])
        self.assertFalse(self.asks())

    def test_a_send_error_leaves_the_report_wake_and_attempts_intact_and_the_next_run_sends_again(self) -> None:
        self.prepare_records()
        waiting = self.add(3)
        with mock.patch('contact.require'), mock.patch('features.tell_user', side_effect=RuntimeError('gateway offline')):
            report, wake = self.run_cron()
        self.assertTrue(wake)
        [task] = self.tasks(report)
        self.assertEqual(self.manifest(task)['id'], waiting)
        self.assertEqual(self.store.read_attempts(), {})
        self.assertEqual(self.state.load()['handed'], [extraction_read(waiting, self.manifest(task)['fingerprint'])])
        self.assertIn('Compare tab could not be delivered', report)
        self.assertIn('gateway offline', report)
        self.assertEqual(self.features.status()['problems'], [])
        self.assertEqual(self.delivered(), set())
        self.assertIn('gateway offline', self.failed()['reason'])
        self.assertTrue(self.asks(gateway=None))
        _ = self.save(task)
        with mock.patch('contact.require'), mock.patch('features.tell_user', return_value={'success': True}) as send:
            report, wake = self.run_cron()
        send.assert_called_once()
        self.assertFalse(wake)
        self.assertEqual(self.store.read_attempts(), {})
        self.assertEqual(self.features.status()['problems'], [])
        self.assertEqual(self.delivered(), {'gateway'})
        self.assertIsNone(self.store.compare_invitation()['failed'])
        self.assertFalse(self.asks(gateway=None))

    def test_a_conversation_that_asked_after_a_failed_send_leaves_the_gallery_to_show_it_without_another_message(self) -> None:
        self.prepare_records()
        with mock.patch('contact.require'), mock.patch('features.tell_user', side_effect=RuntimeError('gateway offline')):
            _ = self.run_job()
        code, report = self.status_command()
        self.assertEqual((code, report['problems'], report['ask_to_compare']), (0, [], True))
        self.assertEqual(self.features.compare_asked(), {'recorded': True})
        self.assertIsNone(self.store.compare_invitation()['failed'])
        for _ in range(2):
            code, report = self.status_command()
            self.assertEqual((code, report['problems'], report['ask_to_compare']), (0, [], False))
        with mock.patch('contact.require'), mock.patch('features.tell_user') as send:
            _ = self.run_job()
        send.assert_not_called()
        self.assertEqual(self.delivered(), {'conversation', 'gallery'})
        self.assertFalse(self.asks())
        code, report = self.status_command()
        self.assertEqual((code, report['problems'], report['ask_to_compare']), (0, [], False))

    def test_a_failing_gateway_check_leaves_the_report_wake_and_handed_reads_intact(self) -> None:
        self.prepare_records()
        waiting = self.add(3)
        with (mock.patch('contact.require', side_effect=RuntimeError('The connection check for gateway failed: timed out')),
              mock.patch('features.tell_user') as send):
            report, wake = self.run_cron()
        send.assert_not_called()
        self.assertTrue(wake)
        [task] = self.tasks(report)
        self.assertEqual(self.manifest(task)['id'], waiting)
        self.assertEqual(self.state.load()['handed'], [extraction_read(waiting, self.manifest(task)['fingerprint'])])
        self.assertNotIn('Extraction failed', report)
        self.assertIn('Compare tab could not be delivered', report)
        self.assertEqual(self.features.status()['problems'], [])
        self.assertIn('timed out', self.failed()['reason'])
        self.assertEqual(self.delivered(), set())
        self.assertTrue(self.asks(gateway=None))
        self.assertEqual(self.features.compare_asked(), {'recorded': True})
        self.assertEqual((self.store.compare_invitation()['failed'], self.asks(gateway=None)), (None, False))

    def test_invitation_waits_for_interview_and_two_eligible_records(self) -> None:
        self.prepare_records()
        _ = self.store.update_settings(listing_sites=[])
        self.assertFalse(self.store.compare_invitation()['due'])
        _ = self.store.update_settings(listing_sites=['https://example.com/'])
        self.store.set_availability('zillow:1', 'unavailable')
        self.assertFalse(self.store.compare_invitation()['due'])
        self.store.set_availability('zillow:1', 'available')
        self.assertTrue(self.store.compare_invitation()['due'])
        self.store.answer_pair('zillow:1', 'zillow:2')
        self.assertFalse(self.store.compare_invitation()['due'])

    def test_the_gallery_shows_the_invitation_it_delivered_until_the_user_answers(self) -> None:
        server = make_server(self.store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def shown() -> bool:
            request = Request(f'http://127.0.0.1:{server.server_port}/api/compare/invitation',
                              headers={'Authorization': 'Bearer ' + server.access_token})
            with cast(Response, urlopen(request, timeout=5)) as response:
                return cast(InvitationShown, json.loads(response.read()))['shown']

        self.prepare_records()
        self.assertFalse(shown())
        _ = self.store.deliver_compare_invitation('gateway')
        self.assertFalse(shown())
        _ = self.store.deliver_compare_invitation('gallery')
        self.assertTrue(shown())
        self.store.answer_pair('zillow:1', 'zillow:2')
        self.assertFalse(shown())

    def test_an_invitation_the_job_sent_before_the_store_recorded_deliveries_counts_as_sent_through_the_gateway(self) -> None:
        self.prepare_records()
        self.state.update(compare_invited_at='2026-10-20T12:00:00+00:00')
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            _ = connection.execute('DROP TABLE compare_invitation')
            _ = connection.execute('DROP TABLE learning_events')
            _ = connection.execute('DROP TABLE hypothesis_judgments')
            _ = connection.execute('DROP TABLE hypotheses')
            _ = connection.execute('DROP TABLE hypothesis_passes')
            _ = connection.execute('DROP TABLE implicit_signals')
            for table in ('photo_features', 'photo_similarity', 'photo_rereads', 'encoder_blind_tests'):
                _ = connection.execute(f'DROP TABLE {table}')
            _ = connection.execute('ALTER TABLE preference_readings DROP COLUMN photo_model')
            implicit_signals(connection)
            _ = connection.execute(f'PRAGMA user_version = {MIGRATIONS.index(compare_invitation)}')
        self.features.store = ListingStore(self.store.path)
        self.store: ListingStore = self.features.store
        self.assertEqual(self.store.compare_invitation()['delivered'], {'gateway': '2026-10-20T12:00:00+00:00'})
        with mock.patch('contact.require'), mock.patch('features.tell_user') as send:
            _ = self.run_job()
        send.assert_not_called()
        self.assertFalse(self.asks())

    def test_installed_job_does_not_delegate_the_invitation(self) -> None:
        job = next(job for job in shipped_jobs() if job['id'] == 'apt-scraper-features')
        self.assertNotIn('compare', job['prompt'].lower())
