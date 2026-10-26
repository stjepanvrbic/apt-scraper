import http.server
import json
import os
import plistlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
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
DASHBOARD_COMMAND = '-p apt-scraper dashboard --no-open --skip-build'
LOGIN_STEP_POWERSHELL = "Write-Step 'Dashboard at login and the apt-scraper shortcut'"
LOGIN_STEP_SHELL = '# The dashboard starts at login'
DASHBOARD_STEP = "# The first start builds the dashboard's web UI"
LAUNCH_AGENT_LABEL = 'io.github.stjepanvrbic.apt-scraper.dashboard'
LAUNCH_AGENT = f'Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist'
APP = 'Applications/apt-scraper.app/Contents'
# Stands in for the dashboard: answers HTTP on the port in argv[1] until the file in argv[2] exists.
FAKE_DASHBOARD = textwrap.dedent("""\
    import http.server, os, sys, threading, time
    server = http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.monotonic() + 60
    while not os.path.exists(sys.argv[2]) and time.monotonic() < deadline:
        time.sleep(0.1)
    """)


def steps(installer: str) -> list[str]:
    text = (ROOT / installer).read_text(encoding='utf-8')
    pattern = r"Write-Step '([^']+)'" if installer.endswith('.ps1') else r"^ *step '([^']+)'"
    return re.findall(pattern, text, re.MULTILINE)


def dashboard_step(installer: str, marker: str) -> str:
    text = (ROOT / installer).read_text(encoding='utf-8')
    return text[text.index(marker):]


def login_step(installer: str, marker: str, end: str) -> str:
    text = (ROOT / installer).read_text(encoding='utf-8')
    start = text.index(marker)
    return text[start:text.index(end, start)]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def wait_for(condition, seconds: float = 20) -> bool:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True


def port_answers(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


class InstallerTests(unittest.TestCase):
    def test_both_installers_run_the_same_steps_in_the_same_order(self):
        windows = [step for step in steps('install.ps1') if not step.startswith('Log in to Claude')]
        macos = [step for step in steps('install.sh')
                 if not step.startswith(('Log in to Claude', 'Grant Accessibility'))]
        self.assertEqual(windows, macos)
        self.assertEqual(windows[-2:], ['Dashboard at login and the apt-scraper shortcut', 'Hermes web dashboard'])

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


class DashboardAtLoginTests(unittest.TestCase):
    """The login entry and the shortcut, run against a fake hermes in a temporary home."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.port = free_port()
        self.chat_url = CHAT_URL.format(port=self.port)
        self.calls = self.folder / 'calls'
        self.opened = self.folder / 'opened'
        self.stop = self.folder / 'stop'
        self.dashboard = self.folder / 'dashboard.py'
        self.dashboard.write_text(FAKE_DASHBOARD)
        self.addCleanup(self.stop_dashboard)

    def stop_dashboard(self):
        self.stop.touch()
        self.assertTrue(wait_for(lambda: not port_answers(self.port)))

    def serve_dashboard(self):
        server = http.server.HTTPServer(('127.0.0.1', self.port), http.server.SimpleHTTPRequestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def calls_made(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def install_windows_entries(self, serves: bool) -> list[dict]:
        hermes = self.folder / 'hermes.cmd'
        start = f'"{sys.executable}" "{self.dashboard}" {self.port} "{self.stop}"\r\n' if serves else ''
        hermes.write_text(f'@echo %*>>"{self.calls}"\r\n{start}')
        (self.folder / 'profile').mkdir(exist_ok=True)
        script = self.folder / 'login.ps1'
        script.write_text(textwrap.dedent(f"""\
            $ErrorActionPreference = 'Stop'
            $ProfileName = 'apt-scraper'
            $DashboardAddress = 'http://127.0.0.1:{self.port}'
            $hermes = '{hermes}'
            $profileHome = '{self.folder / 'profile'}'
            $env:APPDATA = '{self.folder / 'AppData'}'
            function Write-Step([string]$Message) {{ }}
            """) + login_step('install.ps1', LOGIN_STEP_POWERSHELL, DASHBOARD_STEP) + textwrap.dedent("""\
            $shell = New-Object -ComObject WScript.Shell
            ConvertTo-Json -Compress @(Get-ChildItem -Recurse -Filter *.lnk $env:APPDATA | Sort-Object FullName | ForEach-Object {
                $link = $shell.CreateShortcut($_.FullName)
                @{ Path = $_.FullName.Substring($env:APPDATA.Length); Target = $link.TargetPath; Arguments = $link.Arguments }
            })
            """), encoding='utf-8-sig')
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def windows_shortcuts(self, serves: bool = True) -> tuple[dict, dict]:
        """The login entry and the Start menu shortcut, after the step ran twice."""
        shortcuts = self.install_windows_entries(serves)
        self.assertEqual(self.install_windows_entries(serves), shortcuts)
        launcher = f'"{self.folder / "profile" / "dashboard.vbs"}"'
        self.assertEqual([(shortcut['Path'], shortcut['Arguments']) for shortcut in shortcuts], [
            (r'\Microsoft\Windows\Start Menu\Programs\apt-scraper.lnk', f'{launcher} /open'),
            (r'\Microsoft\Windows\Start Menu\Programs\Startup\apt-scraper dashboard.lnk', launcher),
        ])
        for shortcut in shortcuts:
            self.assertTrue(shortcut['Target'].lower().endswith(r'\system32\wscript.exe'), shortcut['Target'])
        return shortcuts[1], shortcuts[0]

    def run_windows_shortcut(self, shortcut: dict, start_seconds: int = 120) -> subprocess.CompletedProcess:
        launcher = self.folder / 'profile' / 'dashboard.vbs'
        text = launcher.read_text(encoding='utf-16')
        open_chat = f'shell.Run "{self.chat_url}"'
        deadline = 'DateAdd("s", 120, Now)'
        self.assertEqual((text.count(open_chat), text.count(deadline)), (1, 1))
        text = text.replace(open_chat, f'CreateObject("Scripting.FileSystemObject").CreateTextFile("{self.opened}")'
                                       f'.Write "{self.chat_url}"')
        launcher.write_text(text.replace(deadline, f'DateAdd("s", {start_seconds}, Now)'), encoding='utf-16')
        # cscript takes the same arguments as wscript and prints a message instead of showing it. It
        # reports a script error on stderr and still exits 0.
        result = subprocess.run(f'cscript //nologo {shortcut["Arguments"]}', capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.stderr, '')
        return result

    def test_the_windows_login_entry_starts_the_dashboard_and_opens_nothing(self):
        login, _ = self.windows_shortcuts()
        result = self.run_windows_shortcut(login)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(wait_for(lambda: port_answers(self.port)))
        self.assertEqual(self.calls_made(), [DASHBOARD_COMMAND])
        self.assertFalse(self.opened.exists())

    def test_the_windows_shortcut_starts_a_stopped_dashboard_and_opens_its_chat(self):
        _, shortcut = self.windows_shortcuts()
        result = self.run_windows_shortcut(shortcut)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls_made(), [DASHBOARD_COMMAND])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_windows_shortcut_opens_the_chat_of_a_running_dashboard_without_starting_another(self):
        _, shortcut = self.windows_shortcuts()
        self.serve_dashboard()
        result = self.run_windows_shortcut(shortcut)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls_made(), [])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_windows_shortcut_reports_a_dashboard_that_does_not_start(self):
        _, shortcut = self.windows_shortcuts(serves=False)
        result = self.run_windows_shortcut(shortcut, start_seconds=2)
        self.assertEqual(result.returncode, 1)
        self.assertIn('dashboard did not start', result.stdout)
        self.assertEqual(self.calls_made(), [DASHBOARD_COMMAND])
        self.assertFalse(self.opened.exists())

    def install_macos_entries(self, bootstraps: bool) -> tuple[str, str]:
        """Runs the step twice with a fake launchctl. Returns the launchd domain and the agent's path as bash sees them."""
        tools = self.folder / 'bin'
        tools.mkdir()
        start = (f'nohup "{Path(sys.executable).as_posix()}" "{self.dashboard.as_posix()}" {self.port} '
                 f'"{self.stop.as_posix()}" </dev/null >/dev/null 2>&1 &\n')
        fakes = {
            'hermes': 'exit 0\n',
            'uname': 'echo Darwin\n',
            'launchctl': f'printf "%s\\n" "$*" >> "{self.calls.as_posix()}"\n'
                         + (f'[ "$1" = bootstrap ] || exit 5\n{start}' if bootstraps else 'exit 5\n'),
            'open': f'printf "%s" "$1" > "{self.opened.as_posix()}"\n',
            'osascript': f'printf "%s" "$2" > "{(self.folder / "alert").as_posix()}"\n',
        }
        for name, body in fakes.items():
            (tools / name).write_text(f'#!/bin/sh\n{body}', newline='\n')
            (tools / name).chmod(0o755)
        script = self.folder / 'login.sh'
        script.write_text(f'set -euo pipefail\nPROFILE_NAME=apt-scraper\nDASHBOARD_ADDRESS=http://127.0.0.1:{self.port}\n'
                          'step() { :; }\n' + login_step('install.sh', LOGIN_STEP_SHELL, DASHBOARD_STEP), newline='\n')
        self.environment = {**os.environ, 'HOME': self.folder.as_posix(),
                            'PATH': f'{tools}{os.pathsep}{os.environ["PATH"]}'}
        written = None
        for _ in range(2):
            result = subprocess.run([BASH, script.as_posix()], env=self.environment, capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            files = {path: path.read_bytes() for path in [self.folder / LAUNCH_AGENT, *(self.folder / APP).rglob('*')]
                     if path.is_file()}
            self.assertIn(written, (None, files))
            written = files
        uid, home = subprocess.run([BASH, '-c', 'printf "%s %s" "$(id -u)" "$HOME"'], env=self.environment,
                                   capture_output=True, text=True, check=True).stdout.split(' ', 1)
        return f'gui/{uid}', f'{home}/{LAUNCH_AGENT}'

    def run_macos_app(self, start_seconds: int = 120) -> subprocess.CompletedProcess:
        info = plistlib.loads((self.folder / APP / 'Info.plist').read_bytes())
        self.assertEqual((info['CFBundleExecutable'], info['CFBundlePackageType']), ('apt-scraper', 'APPL'))
        app = self.folder / APP / 'MacOS' / 'apt-scraper'
        text = app.read_text()
        self.assertEqual(text.count('SECONDS + 120'), 1)
        app.write_text(text.replace('SECONDS + 120', f'SECONDS + {start_seconds}'), newline='\n')
        return subprocess.run([BASH, app.as_posix()], env=self.environment, capture_output=True, text=True,
                              timeout=60)

    def test_the_macos_launch_agent_starts_the_dashboard_at_login(self):
        self.install_macos_entries(bootstraps=True)
        agent = plistlib.loads((self.folder / LAUNCH_AGENT).read_bytes())
        self.assertEqual(agent['Label'], LAUNCH_AGENT_LABEL)
        self.assertIs(agent['RunAtLoad'], True)
        self.assertEqual(Path(agent['ProgramArguments'][0]).name, 'hermes')
        self.assertEqual(' '.join(agent['ProgramArguments'][1:]), DASHBOARD_COMMAND)

    def test_the_macos_app_starts_a_stopped_dashboard_through_its_launch_agent_and_opens_its_chat(self):
        domain, agent = self.install_macos_entries(bootstraps=True)
        result = self.run_macos_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_made(), [f'bootstrap {domain} {agent}'])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_macos_app_opens_the_chat_of_a_running_dashboard_without_starting_another(self):
        self.install_macos_entries(bootstraps=True)
        self.serve_dashboard()
        result = self.run_macos_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_made(), [])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_macos_app_kickstarts_a_loaded_agent_and_reports_a_dashboard_that_does_not_start(self):
        domain, agent = self.install_macos_entries(bootstraps=False)
        result = self.run_macos_app(start_seconds=2)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls_made(), [f'bootstrap {domain} {agent}', f'kickstart {domain}/{LAUNCH_AGENT_LABEL}'])
        self.assertIn('dashboard did not start', (self.folder / 'alert').read_text())
        self.assertFalse(self.opened.exists())


if __name__ == '__main__':
    unittest.main()
