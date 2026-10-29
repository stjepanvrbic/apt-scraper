import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/search/scripts'))

from test_gallery_store import ListingStore  # noqa: E402
from state import SearchState  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'scripts/search-run.py'
specification = importlib.util.spec_from_file_location('search_run', LAUNCHER)
launcher = importlib.util.module_from_spec(specification)
specification.loader.exec_module(launcher)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
COMPLETE = {
    'criteria': {'location': ['North'], 'price': {'min': None, 'max': None}, 'size': {'min': None, 'max': None},
                 'rooms': {'min': None, 'max': None}, 'move_in': None, 'other': []},
    'preferences': {'likes': [], 'dislikes': []},
    'listing_sites': ['https://www.zillow.com'],
    'viewing_availability': {'timezone': 'America/Chicago', 'weekly': [{'day': 'saturday', 'start': '10:00', 'end': '14:00'}]},
    'places': [],
    'location_priorities': {'rapid_transit': 'low', 'areas': {'North': 'medium'}},
}


class LauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.requirements = self.home / 'skills' / 'search' / 'requirements.txt'
        self.requirements.parent.mkdir(parents=True)
        self.requirements.write_text('curl-cffi==0.16.3 --hash=sha256:abc\n', encoding='utf-8')
        self.environment = self.home / 'workspace' / 'search' / 'environment'
        self.commands = []
        self.pip_fails = False
        self.search_exit = 0
        for patcher in (patch.dict(os.environ, {'HERMES_HOME': str(self.home), 'PYTHONPATH': 'elsewhere'}),
                        patch.object(launcher, 'REQUIREMENTS', self.requirements)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_command(self, command, **options):
        self.commands.append([str(part) for part in command])
        self.assertNotIn('PYTHONPATH', options['env'])
        if command[1:3] == ['-m', 'venv']:
            python = launcher.environment_python(self.environment)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text('')
        if command[1:3] == ['-m', 'pip'] and self.pip_fails:
            raise subprocess.CalledProcessError(1, command)
        if str(command[-1]) == 'run':
            print('Search finished.')
            print(json.dumps({'wakeAgent': False}))
            return subprocess.CompletedProcess(command, self.search_exit, stderr='Traceback\nOSError: disk full\n')
        return subprocess.CompletedProcess(command, 0)

    def launch(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            code = launcher.main(list(arguments), run=self.run_command)
        return code, output.getvalue().splitlines()

    def problem(self):
        return SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3').problem()

    def complete_interview(self):
        ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3').update_settings(**COMPLETE)

    def test_install_builds_the_environment_from_hash_pinned_wheels_only(self):
        code, _ = self.launch('install')
        self.assertEqual(code, 0)
        venv, pip = self.commands
        self.assertEqual(venv[1:4], ['-m', 'venv', '--clear'])
        self.assertEqual(pip[1:4], ['-m', 'pip', 'install'])
        for flag in ('--require-hashes', '--only-binary', ':all:', str(self.requirements)):
            self.assertIn(flag, pip)

    def test_a_failed_install_has_its_own_exit_code_and_is_not_recorded_as_built(self):
        self.pip_fails = True
        code, _ = self.launch('install')
        self.assertEqual(code, launcher.ENVIRONMENT_FAILED)
        self.pip_fails = False
        self.complete_interview()
        self.launch()
        self.assertEqual(sum(command[1:3] == ['-m', 'venv'] for command in self.commands), 2)

    def test_before_the_interview_is_complete_nothing_is_built_or_searched(self):
        code, lines = self.launch()
        self.assertEqual((code, self.commands), (0, []))
        self.assertEqual(lines[-1], '{"wakeAgent": false}')

    def test_a_search_that_has_run_keeps_running_while_the_interview_has_new_questions(self):
        self.launch('install')
        settings = {name: value for name, value in COMPLETE.items() if name not in ('places', 'location_priorities')}
        ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3').update_settings(**settings)
        self.commands.clear()
        self.launch()
        self.assertEqual(self.commands, [])
        state = SearchState(self.home / 'workspace' / 'gallery' / 'search.sqlite3')
        state.finish_run(state.start_run(NOW), NOW, {'sites': [{'site': 'https://www.zillow.com', 'errors': []}]})
        self.launch()
        self.assertEqual([command[-1] for command in self.commands], ['run'])

    def test_a_missing_environment_is_recorded_for_status_without_waking_the_model(self):
        self.complete_interview()
        code, lines = self.launch()
        self.assertEqual((code, self.commands), (0, []))
        self.assertEqual(lines[-1], '{"wakeAgent": false}')
        self.assertIn('Rerun the apt-scraper installer', self.problem())

    def test_the_environment_is_rebuilt_once_when_hermes_python_changes(self):
        self.launch('install')
        self.complete_interview()
        self.commands.clear()
        self.launch()
        self.assertEqual([command[-1] for command in self.commands], ['run'])
        with patch.object(launcher.sys, 'version', '3.99.0 (other)'):
            self.commands.clear()
            self.launch()
            self.assertEqual([command[1:3] for command in self.commands][:2], [['-m', 'venv'], ['-m', 'pip']])
            self.commands.clear()
            self.launch()
            self.assertEqual([command[-1] for command in self.commands], ['run'])
            self.pip_fails = True
            self.requirements.write_text('curl-cffi==0.17.0 --hash=sha256:def\n', encoding='utf-8')
            code, lines = self.launch()
        self.assertEqual((code, lines[-1]), (0, '{"wakeAgent": false}'))
        self.assertIn('Rerun the apt-scraper installer', self.problem())

    def test_the_search_report_passes_through_and_a_failed_search_is_recorded_without_waking_the_model(self):
        self.launch('install')
        self.complete_interview()
        code, lines = self.launch()
        self.assertEqual((code, lines), (0, ['Search finished.', '{"wakeAgent": false}']))
        self.assertIsNone(self.problem())
        self.search_exit = 1
        code, lines = self.launch()
        self.assertEqual((code, lines[-1]), (0, '{"wakeAgent": false}'))
        self.assertIn('OSError: disk full', self.problem())
        self.search_exit = 0
        self.launch()
        self.assertIsNone(self.problem())


class ShippedJobTests(unittest.TestCase):
    def test_the_hourly_job_runs_this_script_with_the_search_skill_and_the_installers_build_and_turn_it_on(self):
        jobs = json.loads((ROOT / 'cron' / 'jobs.json').read_text(encoding='utf-8'))['jobs']
        [job] = [job for job in jobs if job['id'] == 'apt-scraper-search']
        self.assertEqual((job['script'], job['skills'], job['schedule'], job['deliver'], job['failure_deliver']),
                         ('search-run.py', ['search'], 'every 1h', 'local', 'origin'))
        self.assertEqual(ROOT / 'scripts' / job['script'], LAUNCHER)
        powershell, shell = ((ROOT / name).read_text(encoding='utf-8') for name in ('install.ps1', 'install.sh'))
        self.assertIn("cron', 'resume', 'apt-scraper-search'", powershell)
        self.assertIn('cron resume apt-scraper-search', shell)
        self.assertIn('search-run.py', powershell)
        self.assertIn('search-run.py', shell)
        for line in (ROOT / 'skills' / 'search' / 'requirements.txt').read_text(encoding='utf-8').splitlines():
            if line and not line.startswith(' '):
                self.assertRegex(line, r'^[a-z0-9-]+==[0-9.]+ ')


if __name__ == '__main__':
    unittest.main()
