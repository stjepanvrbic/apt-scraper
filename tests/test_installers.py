import os
import re
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Windows starts the first bash.exe it finds in System32, which is WSL's, before searching PATH.
BASH = shutil.which('bash')
CHAT_URL = 'http://127.0.0.1:{port}/chat?profile=apt-scraper'
FRESH = ['Routing to the machine dashboard.', 'HERMES_DASHBOARD_READY port=9119']
RUNNING = ['Hermes dashboard already running on this host: PID 1, port 9200.',
           "  Managing profile 'apt-scraper': http://127.0.0.1:9200/?profile=apt-scraper"]
FAILED = ["Error: Profile 'apt-scraper' does not exist."]


def steps(installer: str) -> list[str]:
    text = (ROOT / installer).read_text(encoding='utf-8')
    pattern = r"Write-Step '([^']+)'" if installer.endswith('.ps1') else r"^step '([^']+)'"
    return re.findall(pattern, text, re.MULTILINE)


def dashboard_step(installer: str, marker: str) -> str:
    text = (ROOT / installer).read_text(encoding='utf-8')
    return text[text.index(marker):]


class InstallerTests(unittest.TestCase):
    def test_both_installers_run_the_same_steps_in_the_same_order(self):
        windows = [step for step in steps('install.ps1') if not step.startswith('Log in to Claude')]
        macos = [step for step in steps('install.sh')
                 if not step.startswith(('Log in to Claude', 'Grant Accessibility'))]
        self.assertEqual(windows, macos)
        self.assertEqual(windows[-1], 'Hermes web dashboard')

    def run_shell_step(self, lines: list[str], exit_code: int) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            opened = folder / 'opened'
            printed = ''.join(f"printf '%s\\n' {shlex.quote(line)}\n" for line in lines)
            (folder / 'hermes').write_text(f'#!/bin/sh\n{printed}sleep 2\nexit {exit_code}\n', newline='\n')
            for opener in ('open', 'xdg-open'):
                (folder / opener).write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{opened.as_posix()}"\n', newline='\n')
            script = folder / 'step.sh'
            script.write_text("set -euo pipefail\nPROFILE_NAME=apt-scraper\nstep() { :; }\n"
                              + dashboard_step('install.sh', "step 'Hermes web dashboard'"), newline='\n')
            for path in folder.iterdir():
                path.chmod(0o755)
            environment = {**os.environ, 'PATH': f'{folder}{os.pathsep}{os.environ["PATH"]}'}
            result = subprocess.run([BASH, script.as_posix()], env=environment, capture_output=True,
                                    text=True, timeout=60)
            return result, opened.read_text() if opened.exists() else ''

    def run_powershell_step(self, lines: list[str], exit_code: int) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            opened = folder / 'opened'
            fake = folder / 'hermes.cmd'
            fake.write_text('@echo off\r\n' + ''.join(f'echo {line}\r\n' for line in lines)
                            + f'ping -n 3 127.0.0.1 >nul\r\nexit /b {exit_code}\r\n')
            step = dashboard_step('install.ps1', "Write-Step 'Hermes web dashboard'")
            self.assertEqual(step.count('Start-Process $chatUrl'), 1)
            step = step.replace('Start-Process $chatUrl', f"Set-Content -Path '{opened}' -Value $chatUrl -NoNewline")
            script = folder / 'step.ps1'
            script.write_text(textwrap.dedent(f"""\
                $ErrorActionPreference = 'Stop'
                $ProfileName = 'apt-scraper'
                $hermes = '{fake}'
                function Write-Step([string]$Message) {{ }}
                """) + step, encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                    capture_output=True, text=True, timeout=60)
            return result, opened.read_text() if opened.exists() else ''

    def test_the_shell_installer_opens_the_chat_of_a_dashboard_it_starts_or_finds_running(self):
        for lines, port in ((FRESH, 9119), (RUNNING, 9200)):
            result, opened = self.run_shell_step(lines, 0)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(opened, CHAT_URL.format(port=port))

    def test_the_shell_installer_stops_with_the_output_of_a_dashboard_that_did_not_start(self):
        result, opened = self.run_shell_step(FAILED, 1)
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not exist', result.stderr)
        self.assertEqual(opened, '')

    def test_the_powershell_installer_opens_the_chat_of_a_dashboard_it_starts_or_finds_running(self):
        for lines, port in ((FRESH, 9119), (RUNNING, 9200)):
            result, opened = self.run_powershell_step(lines, 0)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(opened, CHAT_URL.format(port=port))

    def test_the_powershell_installer_stops_with_the_output_of_a_dashboard_that_did_not_start(self):
        result, opened = self.run_powershell_step(FAILED, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not exist', result.stderr + result.stdout)
        self.assertEqual(opened, '')


if __name__ == '__main__':
    unittest.main()
