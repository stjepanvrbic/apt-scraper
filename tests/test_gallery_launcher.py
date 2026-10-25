import json
import os
import signal
import socket
import shutil
import subprocess
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))
from launch import api_request

SCRIPT = Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts/gallery.py'


def connection(url):
    address = urlsplit(url)
    return {'url': f'{address.scheme}://{address.netloc}', 'token': parse_qs(address.fragment)['token'][0]}


class GalleryLauncherTests(unittest.TestCase):
    def test_opener_receives_the_authenticated_url_without_opening_a_browser(self):
        from gallery import main
        output = io.StringIO()
        link = 'http://127.0.0.1:8765/#token=test-only-capability'
        with patch.object(sys, 'argv', [str(SCRIPT), '--sample', 'open']), patch('gallery.open_gallery', return_value=link), patch('gallery.webbrowser.open', return_value=True) as opener, redirect_stdout(output):
            main()
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(opener.call_args.args, (link,))
        self.assertEqual(json.loads(output.getvalue())['url'], link)

    def test_redacted_output_keeps_the_launch_secret_out_of_tool_logs(self):
        from gallery import main
        output = io.StringIO()
        link = 'http://127.0.0.1:8765/#token=test-only-capability'
        with patch.object(sys, 'argv', [str(SCRIPT), '--sample', 'open', '--redact-token']), patch('gallery.open_gallery', return_value=link), patch('gallery.webbrowser.open', return_value=True) as opener, redirect_stdout(output):
            main()
        self.assertEqual(opener.call_args.args, (link,))
        self.assertEqual(json.loads(output.getvalue())['url'], 'http://127.0.0.1:8765/')

    def test_open_returns_a_launch_secret_and_stop_ends_the_server(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / 'listings.sqlite3')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            command = [sys.executable, str(SCRIPT), '--database', database, '--port', str(port)]
            result = subprocess.run([*command, 'open', '--no-browser'], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                address = urlsplit(json.loads(result.stdout)['url'])
                self.assertGreater(len(parse_qs(address.fragment).get('token', [''])[0]), 30)
            finally:
                stopped = subprocess.run([*command, 'stop'], capture_output=True, text=True, timeout=10)
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
    def test_open_restarts_a_changed_install_and_rotates_its_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'skill'
            shutil.copytree(SCRIPT.parent, root / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            command = [sys.executable, str(root / 'scripts/gallery.py'), '--database', str(Path(directory) / 'listings.sqlite3'), '--port', str(port)]
            def open_server():
                result = subprocess.run([*command, 'open', '--no-browser'], capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return connection(json.loads(result.stdout)['url'])
            try:
                first = open_server()
                previous = api_request(first, '/api/health')
                (root / 'SKILL.md').write_text('Updated gallery instructions.', encoding='utf-8')
                second = open_server()
                current = api_request(second, '/api/health')
                self.assertNotEqual(previous['process_id'], current['process_id'])
                self.assertNotEqual(previous['install_id'], current['install_id'])
                self.assertFalse(first['token'] == second['token'])
                self.assertFalse(second['token'] in (Path(directory) / 'gallery.log').read_text())
            finally:
                result = subprocess.run([*command, 'stop'], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_open_from_virtual_environment_survives_without_a_console(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / 'environment'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment)], check=True, capture_output=True)
            interpreter = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            result = subprocess.run([str(interpreter), str(SCRIPT), '--database', str(Path(directory) / 'listings.sqlite3'),
                                     '--port', str(port), 'open', '--no-browser'], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            record = connection(json.loads(result.stdout)['url'])
            process_id = api_request(record, '/api/health')['process_id']
            try:
                if sys.platform == 'win32':
                    probe = ('import ctypes,json; kernel=ctypes.windll.kernel32; kernel.FreeConsole(); '
                             f'attached=bool(kernel.AttachConsole({process_id})); '
                             'print(json.dumps({"has_console": attached})); kernel.FreeConsole()')
                    result = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True,
                                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
                    self.assertFalse(json.loads(result.stdout)['has_console'])
                else:
                    self.assertEqual(os.getsid(process_id), process_id)
                self.assertEqual(api_request(record, '/api/listings')['items'], [])
            finally:
                stop_server(process_id)

    def test_server_starts_and_open_reuses_it_for_the_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / 'listings.sqlite3')
            process = subprocess.Popen([sys.executable, str(SCRIPT), '--database', database,
                                        '--port', '0', 'serve'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                line = process.stdout.readline()
                self.assertTrue(line, process.stderr.read() if process.poll() is not None else 'No startup URL')
                url = json.loads(line)['url']
                self.assertEqual(api_request(connection(url), '/api/health')['application'], 'apt-scraper-gallery')
                result = subprocess.run([sys.executable, str(SCRIPT), '--database', database,
                                         '--port', str(urlsplit(url).port), 'open', '--no-browser'],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['url'], url)
            finally:
                process.terminate()
                process.communicate(timeout=5)

    def test_sample_store_seeds_once_and_real_store_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, 'HERMES_HOME': directory}
            def run(*arguments):
                result = subprocess.run([sys.executable, str(SCRIPT), *arguments],
                                        env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            self.assertEqual(run('export')['listings'], [])
            self.assertFalse(run('status')['has_listings'])
            initial = run('--sample', 'export')
            self.assertGreaterEqual(len(initial['listings']), 3)
            self.assertTrue(run('--sample', 'status')['has_listings'])
            self.assertEqual(run('--sample', 'export'), initial)
            self.assertEqual(run('export')['listings'], [])
            run('--sample', 'note', 'Quiet street, puno svjetla, тихая улица.', '--listing', 'sample:lincoln')
            self.assertEqual(run('--sample', 'export')['listings'][0]['notes'][0]['text'], 'Quiet street, puno svjetla, тихая улица.')


def stop_server(process_id):
    if sys.platform == 'win32':
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        f'$galleryProcess = Get-Process -Id {process_id}; $galleryProcess.Kill(); $galleryProcess.WaitForExit()'],
                       check=True, capture_output=True, timeout=10)
    else:
        os.kill(process_id, signal.SIGTERM)
