import json
from unittest import mock

from test_features_run import ROOT, FeaturesTestCase
from contact import NotConnected


class CompareInvitationTests(FeaturesTestCase):
    def prepare_records(self):
        self.add(1, photos=0)
        self.add(2, photos=0)
        for task in self.tasks(self.run_job()[0]):
            self.save(task)

    def test_pending_invitation_without_a_connected_gateway_neither_wakes_nor_sends(self):
        self.assertFalse(self.features.compare_invitation_pending())
        self.prepare_records()
        with mock.patch('contact.require', side_effect=NotConnected('gateway offline')), mock.patch('features.tell_user') as send:
            self.assertFalse(self.run_job()[1])
            self.assertFalse(self.run_job()[1])
            send.assert_not_called()
        self.assertTrue(self.features.compare_invitation_pending())

    def test_connected_gateway_sends_once_without_waking_the_agent(self):
        self.prepare_records()
        with mock.patch('contact.require') as require, mock.patch('features.tell_user', return_value={'success': True}) as send:
            self.assertFalse(self.run_job()[1])
            self.assertFalse(self.run_job()[1])
            require.assert_called_with('gateway')
            send.assert_called_once()
            self.assertIn('5 to 8', send.call_args.args[0])
            self.assertIn('Compare', send.call_args.args[0])
        self.assertFalse(self.features.compare_invitation_pending())

    def test_failed_delivery_stays_pending_until_delivery_succeeds(self):
        self.prepare_records()
        with mock.patch('features.tell_user', side_effect=RuntimeError('gateway offline')):
            with self.assertRaisesRegex(RuntimeError, 'gateway offline'):
                self.features.invite_compare()
        self.assertTrue(self.features.compare_invitation_pending())
        with mock.patch('features.tell_user', return_value={'success': True}) as send:
            self.assertTrue(self.features.invite_compare()['sent'])
            self.assertIn('5 to 8', send.call_args.args[0])
            self.assertIn('Compare', send.call_args.args[0])
            self.assertFalse(self.features.invite_compare()['sent'])
            send.assert_called_once()
        self.assertFalse(self.run_job()[1])

    def test_invitation_waits_for_interview_and_two_eligible_records(self):
        self.prepare_records()
        self.store.update_settings(listing_sites=[])
        self.assertFalse(self.features.compare_invitation_pending())
        self.store.update_settings(listing_sites=['https://example.com/'])
        self.store.set_availability('zillow:1', 'unavailable')
        self.assertFalse(self.features.compare_invitation_pending())
        self.store.set_availability('zillow:1', 'available')
        self.assertTrue(self.features.compare_invitation_pending())
        self.store.answer_pair('zillow:1', 'zillow:2')
        self.assertFalse(self.features.compare_invitation_pending())

    def test_installed_job_does_not_delegate_the_gateway_invitation(self):
        job = next(job for job in json.loads((ROOT / 'profile/cron/jobs.json').read_text())['jobs']
                   if job['id'] == 'apt-scraper-features')
        self.assertNotIn('invite-compare', job['prompt'])
