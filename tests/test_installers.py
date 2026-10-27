import contextlib
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
DASHBOARD_COMMAND = '-p apt-scraper dashboard --no-open --skip-build --port {port}'
LOGIN_STEP_POWERSHELL = "Write-Step 'Dashboard at login and the apt-scraper shortcut'"
LOGIN_STEP_SHELL = '# The dashboard starts at login'
CLOSING_POWERSHELL = "Write-Host ''"
CLOSING_SHELL = r"printf '\n\033[32mInstalled."
LAUNCH_AGENT_LABEL = 'io.github.stjepanvrbic.apt-scraper.dashboard'
LAUNCH_AGENT = f'Library/LaunchAgents/{LAUNCH_AGENT_LABEL}.plist'
APP = 'Applications/apt-scraper.app/Contents'
# What Hermes 0.22.5's dashboard answers on /api/health (hermes_cli/web_routers/status.py).
HERMES_HEALTH = '{"ok":true,"version":"0.22.5","displayVersion":"0.22.5","commit":"bf0203a","auth_required":false}'
# Stands in for the dashboard: answers /api/health as Hermes does on the port in argv[1] until the file in
# argv[2] exists.
FAKE_DASHBOARD = textwrap.dedent(f"""\
    import http.server, os, sys, threading, time
    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            found = self.path == '/api/health'
            body = {HERMES_HEALTH!r}.encode() if found else b''
            self.send_response(200 if found else 404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *arguments):
            pass
    http.server.HTTPServer.allow_reuse_address = False
    server = http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.monotonic() + 60
    while not os.path.exists(sys.argv[2]) and time.monotonic() < deadline:
        time.sleep(0.1)
    """)


REPOSITORY_URL = 'https://github.com/stjepanvrbic/apt-scraper.git'
# Stands in for git: it logs every call except `--version` to the file in argv[1], and `clone` copies the directory in argv[2].
FAKE_GIT = textwrap.dedent("""\
    import json, shutil, sys
    log, template, *arguments = sys.argv[1:]
    if arguments != ['--version']:
        with open(log, 'a') as calls:
            calls.write(json.dumps(arguments) + '\\n')
    if arguments[0] == 'clone':
        shutil.copytree(template, arguments[2])
    """)


# Stands in for hermes's profile commands on the Hermes root in HERMES_HOME, as Hermes 0.22.5 runs them
# (hermes_cli/profiles.py, hermes_cli/profile_distribution.py). It logs every call to the file in argv[1].
# `profile create` refuses an existing directory, clears the name's deletion mark and writes a config.yaml
# of its own. `profile install --force` copies the payload, then fails on a name marked deleted.
FAKE_HERMES = textwrap.dedent("""\
    import json, os, shutil, sys
    from pathlib import Path
    log, *arguments = sys.argv[1:]
    with open(log, 'a') as calls:
        calls.write(json.dumps(arguments) + '\\n')
    profiles = Path(os.environ['HERMES_HOME']) / 'profiles'
    if arguments[:2] == ['profile', 'create']:
        profile = profiles / arguments[2]
        if profile.exists():
            sys.exit(f"Error: A profile named '{arguments[2]}' already exists.")
        (profiles / '.deleted' / arguments[2]).unlink(missing_ok=True)
        profile.mkdir(parents=True)
        (profile / 'config.yaml').write_text('created by profile create\\n')
    elif arguments[:2] == ['profile', 'install']:
        source = Path(arguments[2])
        profile = profiles / source.name
        profile.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            if entry.is_dir():
                shutil.copytree(entry, profile / entry.name, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, profile / entry.name)
        if (profiles / '.deleted' / source.name).exists() or os.environ.get('FAKE_HERMES_INSTALL_FAILS'):
            sys.exit(f'FileNotFoundError: Named profile home does not exist: {profile}.')
    """)
DISTRIBUTION_CONFIG = 'model: from the distribution\n'
USER_CONFIG = 'model: chosen by the user\n'
CREATE = ['profile', 'create', 'apt-scraper', '--no-alias']


def steps(installer: str) -> list[str]:
    text = (ROOT / installer).read_text(encoding='utf-8')
    pattern = r"Write-Step '([^']+)'" if installer.endswith('.ps1') else r"^ *step '([^']+)'"
    return re.findall(pattern, text, re.MULTILINE)


def section(installer: str, marker: str, end: str) -> str:
    text = (ROOT / installer).read_text(encoding='utf-8')
    start = text.index(marker)
    return text[start:text.index(end, start)]


def answering(status: int, body: str) -> type[http.server.BaseHTTPRequestHandler]:
    """A handler that answers every GET with status and body."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *arguments):
            pass
    return Handler


# A listener that is not Hermes's dashboard: one that knows no /api/health, and one whose JSON lacks the version.
FOREIGN = {'404': answering(404, '{"detail":"Not Found"}'), 'no version': answering(200, '{"ok":true}')}


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
        self.assertEqual(windows[-2:], ['Hermes web dashboard', 'Dashboard at login and the apt-scraper shortcut'])

    def run_shell_step(self, lines: list[str], exit_code: int) -> tuple[subprocess.CompletedProcess, str, str]:
        """Runs the dashboard step against a fake hermes. Returns its result, the URL it opened and the address it recorded."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            opened = folder / 'opened'
            address = folder / 'profile' / 'dashboard-address'
            address.parent.mkdir()
            printed = ''.join(f"printf '%s\\n' {shlex.quote(line)}\n" for line in lines)
            (folder / 'hermes').write_text(f'#!/bin/sh\n{printed}sleep 2\nexit {exit_code}\n', newline='\n')
            for opener in ('open', 'xdg-open'):
                (folder / opener).write_text(f'#!/bin/sh\nprintf "%s" "$1" > "{opened.as_posix()}"\n', newline='\n')
            script = folder / 'step.sh'
            script.write_text("set -euo pipefail\nPROFILE_NAME=apt-scraper\nstep() { :; }\n"
                              f"profile_home='{address.parent.as_posix()}'\n"
                              + section('install.sh', "step 'Hermes web dashboard'", LOGIN_STEP_SHELL), newline='\n')
            for path in folder.iterdir():
                if path.is_file():
                    path.chmod(0o755)
            environment = {**os.environ, 'PATH': f'{folder}{os.pathsep}{os.environ["PATH"]}'}
            result = subprocess.run([BASH, script.as_posix()], env=environment, capture_output=True,
                                    text=True, timeout=60)
            return (result, opened.read_text() if opened.exists() else '',
                    address.read_text() if address.exists() else '')

    def run_powershell_step(self, lines: list[str], exit_code: int) -> tuple[subprocess.CompletedProcess, str, str]:
        """Runs the dashboard step against a fake hermes. Returns its result, the URL it opened and the address it recorded."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            opened = folder / 'opened'
            address = folder / 'profile' / 'dashboard-address'
            address.parent.mkdir()
            fake = folder / 'hermes.cmd'
            fake.write_text('@echo off\r\n' + ''.join(f'echo {line}\r\n' for line in lines)
                            + f'ping -n 3 127.0.0.1 >nul\r\nexit /b {exit_code}\r\n')
            step = section('install.ps1', "Write-Step 'Hermes web dashboard'", LOGIN_STEP_POWERSHELL)
            self.assertEqual(step.count('Start-Process $chatUrl'), 1)
            step = step.replace('Start-Process $chatUrl', f"Set-Content -Path '{opened}' -Value $chatUrl -NoNewline")
            script = folder / 'step.ps1'
            script.write_text(textwrap.dedent(f"""\
                $ErrorActionPreference = 'Stop'
                $ProfileName = 'apt-scraper'
                $hermes = '{fake}'
                $profileHome = '{address.parent}'
                function Write-Step([string]$Message) {{ }}
                """) + step, encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                    capture_output=True, text=True, timeout=60)
            return (result, opened.read_text() if opened.exists() else '',
                    address.read_text() if address.exists() else '')

    def test_the_shell_installer_records_and_opens_the_dashboard_it_starts_or_finds_on_a_moved_port(self):
        for lines, port in ((FRESH, 9119), (RUNNING, 9200)):
            result, opened, recorded = self.run_shell_step(lines, 0)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(opened, CHAT_URL.format(port=port))
            self.assertEqual(recorded, f'http://127.0.0.1:{port}')

    def test_the_shell_installer_stops_with_the_output_of_a_dashboard_that_did_not_start(self):
        result, opened, recorded = self.run_shell_step(FAILED, 1)
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not exist', result.stderr)
        self.assertEqual((opened, recorded), ('', ''))

    def test_the_powershell_installer_records_and_opens_the_dashboard_it_starts_or_finds_on_a_moved_port(self):
        for lines, port in ((FRESH, 9119), (RUNNING, 9200)):
            result, opened, recorded = self.run_powershell_step(lines, 0)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(opened, CHAT_URL.format(port=port))
            self.assertEqual(recorded, f'http://127.0.0.1:{port}')

    def test_the_powershell_installer_stops_with_the_output_of_a_dashboard_that_did_not_start(self):
        result, opened, recorded = self.run_powershell_step(FAILED, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not exist', result.stderr + result.stdout)
        self.assertEqual((opened, recorded), ('', ''))


class ProfileStepTests(unittest.TestCase):
    """Each installer's profile step, run alone against a fake hermes on a Hermes root in a temporary directory."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.root = self.folder / 'root'
        self.profile = self.root / 'profiles' / 'apt-scraper'
        self.mark = self.root / 'profiles' / '.deleted' / 'apt-scraper'
        self.source = self.folder / 'checkout' / 'apt-scraper'
        self.source.mkdir(parents=True)
        (self.source / 'config.yaml').write_text(DISTRIBUTION_CONFIG)
        (self.source / 'distribution.yaml').write_text('name: apt-scraper\n')
        (self.source / 'SOUL.md').write_text('soul\n')
        self.calls = self.folder / 'calls'
        (self.folder / 'hermes.py').write_text(FAKE_HERMES)
        self.fake = [sys.executable, str(self.folder / 'hermes.py'), str(self.calls)]

    def run_step(self, installer: str, install_fails: bool = False) -> subprocess.CompletedProcess:
        self.calls.unlink(missing_ok=True)
        environment = {**os.environ, 'HERMES_HOME': str(self.root)}
        if install_fails:
            environment['FAKE_HERMES_INSTALL_FAILS'] = '1'
        if installer == 'install.ps1':
            hermes = self.folder / 'hermes.cmd'
            hermes.write_text('@' + ' '.join(f'"{part}"' for part in self.fake) + ' %*\r\n')
            script = self.folder / 'step.ps1'
            script.write_text(textwrap.dedent(f"""\
                $ErrorActionPreference = 'Stop'
                $ProfileName = 'apt-scraper'
                $ProfileSource = '{self.source}'
                $HermesRoot = '{self.root}'
                $hermes = '{hermes}'
                function Write-Step([string]$Message) {{ }}
                """) + section(installer, 'function Invoke-Native', 'function Get-Version')
                + section(installer, 'Write-Step "Hermes profile', '# The account connections use'), encoding='utf-8-sig')
            command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
        else:
            tools = self.folder / 'bin'
            tools.mkdir(exist_ok=True)
            fake = ' '.join(f'"{Path(part).as_posix()}"' for part in self.fake)
            (tools / 'hermes').write_text(f'#!/bin/sh\nexec {fake} "$@"\n', newline='\n')
            (tools / 'hermes').chmod(0o755)
            script = self.folder / 'step.sh'
            script.write_text("set -euo pipefail\nPROFILE_NAME=apt-scraper\nstep() { :; }\n"
                              f"PROFILE_SOURCE='{self.source.as_posix()}'\nHERMES_ROOT='{self.root.as_posix()}'\n"
                              + section(installer, 'step "Hermes profile', '# The account connections use'), newline='\n')
            environment['PATH'] = f'{tools}{os.pathsep}{os.environ["PATH"]}'
            command = [BASH, script.as_posix()]
        return subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)

    def calls_made(self) -> list[list[str]]:
        """The calls, with the checkout's path spelled one way: bash on Windows passes it with forward slashes."""
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return [[str(self.source) if Path(argument) == self.source else argument for argument in call]
                for call in calls]

    def install(self) -> list[str]:
        return ['profile', 'install', str(self.source), '--force', '--yes']

    def assert_installed(self, result: subprocess.CompletedProcess, calls: list[list[str]], config: str):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls_made(), calls)
        self.assertEqual((self.profile / 'config.yaml').read_text(), config)
        self.assertTrue((self.profile / 'distribution.yaml').is_file())
        self.assertFalse(self.mark.exists())

    def mark_deleted(self):
        """What `hermes profile delete` leaves: the name marked in profiles/.deleted and its directory removed."""
        self.mark.parent.mkdir(parents=True, exist_ok=True)
        self.mark.write_text('deleted\n')
        shutil.rmtree(self.profile, ignore_errors=True)

    def installed_by_the_user(self):
        """A profile this distribution installed, whose config.yaml the user changed."""
        self.profile.mkdir(parents=True)
        (self.profile / 'distribution.yaml').write_text('name: apt-scraper\n')
        (self.profile / 'config.yaml').write_text(USER_CONFIG)

    def test_the_installers_create_a_missing_profile_and_install_the_distribution_config(self):
        for installer in ('install.ps1', 'install.sh'):
            with self.subTest(installer):
                shutil.rmtree(self.root, ignore_errors=True)
                result = self.run_step(installer)
                self.assert_installed(result, [CREATE, self.install()], DISTRIBUTION_CONFIG)

    def test_the_installers_install_the_profile_under_a_name_that_was_deleted(self):
        for installer in ('install.ps1', 'install.sh'):
            with self.subTest(installer):
                shutil.rmtree(self.root, ignore_errors=True)
                self.installed_by_the_user()
                self.mark_deleted()
                result = self.run_step(installer)
                self.assert_installed(result, [CREATE, self.install()], DISTRIBUTION_CONFIG)

    def test_the_installers_replace_what_a_failed_install_left_under_a_deleted_name(self):
        for installer in ('install.ps1', 'install.sh'):
            with self.subTest(installer):
                shutil.rmtree(self.root, ignore_errors=True)
                self.mark_deleted()
                # The earlier installer: Hermes copies the payload, then refuses the deleted name.
                bare = subprocess.run([*self.fake, *self.install()], capture_output=True, text=True,
                                      env={**os.environ, 'HERMES_HOME': str(self.root)})
                self.assertIn('Named profile home does not exist', bare.stderr)
                (self.profile / 'config.yaml').write_text(USER_CONFIG)
                (self.profile / 'leftover').write_text('')
                result = self.run_step(installer)
                self.assert_installed(result, [CREATE, self.install()], DISTRIBUTION_CONFIG)
                self.assertFalse((self.profile / 'leftover').exists())

    def test_the_installers_keep_the_config_of_a_profile_they_installed_before(self):
        for installer in ('install.ps1', 'install.sh'):
            with self.subTest(installer):
                shutil.rmtree(self.root, ignore_errors=True)
                self.installed_by_the_user()
                result = self.run_step(installer)
                self.assert_installed(result, [self.install()], USER_CONFIG)

    def test_the_installers_keep_the_config_of_a_profile_they_installed_before_when_the_install_fails(self):
        for installer in ('install.ps1', 'install.sh'):
            with self.subTest(installer):
                shutil.rmtree(self.root, ignore_errors=True)
                self.installed_by_the_user()
                result = self.run_step(installer, install_fails=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls_made(), [self.install()])
                self.assertEqual((self.profile / 'config.yaml').read_text(), USER_CONFIG)

    def test_the_installers_install_the_distribution_config_over_a_leftover_without_distribution_yaml(self):
        for installer in ('install.ps1', 'install.sh'):
            with self.subTest(installer):
                shutil.rmtree(self.root, ignore_errors=True)
                self.profile.mkdir(parents=True)
                (self.profile / 'config.yaml').write_text(USER_CONFIG)
                (self.profile / 'memories').mkdir()
                result = self.run_step(installer)
                # A name never deleted is live, so Hermes installs into it. What is there stays.
                self.assert_installed(result, [self.install()], DISTRIBUTION_CONFIG)
                self.assertTrue((self.profile / 'memories').is_dir())


class ExclusiveServer(http.server.HTTPServer):
    """Refuses a port another listener holds, as Hermes's dashboard does. Windows lets SO_REUSEADDR share it."""
    allow_reuse_address = False


@contextlib.contextmanager
def serving(port: int, handler: type[http.server.BaseHTTPRequestHandler]):
    server = ExclusiveServer(('127.0.0.1', port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


class DashboardAtLoginTests(unittest.TestCase):
    """The login entry and the shortcut, run against a fake hermes in a temporary home."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.port = free_port()
        self.chat_url = CHAT_URL.format(port=self.port)
        self.command = DASHBOARD_COMMAND.format(port=self.port)
        self.profile = self.folder / 'profile'
        self.profile.mkdir()
        # What the installer's dashboard step recorded: a dashboard on a port other than Hermes's default.
        (self.profile / 'dashboard-address').write_text(f'http://127.0.0.1:{self.port}')
        self.calls = self.folder / 'calls'
        self.opened = self.folder / 'opened'
        self.alert = self.folder / 'alert'
        self.stop = self.folder / 'stop'
        self.dashboard = self.folder / 'dashboard.py'
        self.dashboard.write_text(FAKE_DASHBOARD)
        self.addCleanup(self.stop_dashboard)

    def stop_dashboard(self):
        self.stop.touch()
        self.assertTrue(wait_for(lambda: not port_answers(self.port)))

    def forget_runs(self):
        for path in (self.calls, self.opened, self.alert):
            path.unlink(missing_ok=True)

    def calls_made(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def install_windows_entries(self, serves: bool) -> list[dict]:
        hermes = self.folder / 'hermes.cmd'
        start = f'"{sys.executable}" "{self.dashboard}" {self.port} "{self.stop}"\r\n' if serves else ''
        hermes.write_text(f'@echo %*>>"{self.calls}"\r\n{start}')
        script = self.folder / 'login.ps1'
        script.write_text(textwrap.dedent(f"""\
            $ErrorActionPreference = 'Stop'
            $ProfileName = 'apt-scraper'
            $hermes = '{hermes}'
            $profileHome = '{self.profile}'
            $env:APPDATA = '{self.folder / 'AppData'}'
            function Write-Step([string]$Message) {{ }}
            """) + section('install.ps1', LOGIN_STEP_POWERSHELL, CLOSING_POWERSHELL) + textwrap.dedent("""\
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
        launcher = f'"{self.profile / "dashboard.vbs"}"'
        self.assertEqual([(shortcut['Path'], shortcut['Arguments']) for shortcut in shortcuts], [
            (r'\Microsoft\Windows\Start Menu\Programs\apt-scraper.lnk', f'{launcher} /open'),
            (r'\Microsoft\Windows\Start Menu\Programs\Startup\apt-scraper dashboard.lnk', launcher),
        ])
        for shortcut in shortcuts:
            self.assertTrue(shortcut['Target'].lower().endswith(r'\system32\wscript.exe'), shortcut['Target'])
        return shortcuts[1], shortcuts[0]

    def run_windows_shortcut(self, shortcut: dict, start_seconds: int = 120) -> subprocess.CompletedProcess:
        launcher = self.profile / 'dashboard.vbs'
        text = launcher.read_text(encoding='utf-16')
        open_chat = 'shell.Run address & "/chat?profile=apt-scraper"'
        deadline = 'DateAdd("s", 120, Now)'
        self.assertEqual((text.count(open_chat), text.count(deadline)), (1, 1))
        text = text.replace(open_chat, f'CreateObject("Scripting.FileSystemObject").CreateTextFile("{self.opened}")'
                                       '.Write address & "/chat?profile=apt-scraper"')
        launcher.write_text(text.replace(deadline, f'DateAdd("s", {start_seconds}, Now)'), encoding='utf-16')
        # cscript takes the same arguments as wscript and prints a message instead of showing it. It
        # reports a script error on stderr and still exits 0.
        result = subprocess.run(f'cscript //nologo {shortcut["Arguments"]}', capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.stderr, '')
        return result

    def test_the_windows_login_entry_starts_the_dashboard_on_the_recorded_port_and_opens_nothing(self):
        login, _ = self.windows_shortcuts()
        result = self.run_windows_shortcut(login)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(wait_for(lambda: port_answers(self.port)))
        self.assertEqual(self.calls_made(), [self.command])
        self.assertFalse(self.opened.exists())

    def test_the_windows_shortcut_starts_a_stopped_dashboard_and_opens_its_chat(self):
        _, shortcut = self.windows_shortcuts()
        result = self.run_windows_shortcut(shortcut)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls_made(), [self.command])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_windows_shortcut_opens_the_chat_of_hermes_at_the_recorded_address_without_starting_another(self):
        _, shortcut = self.windows_shortcuts()
        with serving(self.port, answering(200, HERMES_HEALTH)):
            result = self.run_windows_shortcut(shortcut)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls_made(), [])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_windows_shortcut_starts_hermes_past_a_foreign_listener_and_reports_that_it_did_not_answer(self):
        for kind, handler in FOREIGN.items():
            with self.subTest(kind), serving(self.port, handler):
                self.forget_runs()
                _, shortcut = self.windows_shortcuts()
                result = self.run_windows_shortcut(shortcut, start_seconds=2)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f'did not answer at http://127.0.0.1:{self.port}', result.stdout)
                self.assertEqual(self.calls_made(), [self.command])
                self.assertFalse(self.opened.exists())

    def test_the_windows_shortcut_reports_a_dashboard_that_does_not_start(self):
        _, shortcut = self.windows_shortcuts(serves=False)
        result = self.run_windows_shortcut(shortcut, start_seconds=2)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'did not answer at http://127.0.0.1:{self.port}', result.stdout)
        self.assertEqual(self.calls_made(), [self.command])
        self.assertFalse(self.opened.exists())

    def install_macos_entries(self, bootstraps: bool) -> tuple[str, str]:
        """Runs the step twice with a fake launchctl. Returns the launchd domain and the agent's path as bash sees them."""
        tools = self.folder / 'bin'
        tools.mkdir(exist_ok=True)
        start = (f'nohup "{Path(sys.executable).as_posix()}" "{self.dashboard.as_posix()}" {self.port} '
                 f'"{self.stop.as_posix()}" </dev/null >/dev/null 2>&1 &\n')
        fakes = {
            'hermes': 'printf "%s\\n" "$*"\n',
            'uname': 'echo Darwin\n',
            'launchctl': f'printf "%s\\n" "$*" >> "{self.calls.as_posix()}"\n'
                         + (f'[ "$1" = bootstrap ] || exit 5\n{start}' if bootstraps else 'exit 5\n'),
            'open': f'printf "%s" "$1" > "{self.opened.as_posix()}"\n',
            'osascript': f'printf "%s" "$2" > "{self.alert.as_posix()}"\n',
        }
        for name, body in fakes.items():
            (tools / name).write_text(f'#!/bin/sh\n{body}', newline='\n')
            (tools / name).chmod(0o755)
        script = self.folder / 'login.sh'
        script.write_text(f"set -euo pipefail\nPROFILE_NAME=apt-scraper\nprofile_home='{self.profile.as_posix()}'\n"
                          f"dashboard_url='http://127.0.0.1:{self.port}'\nstep() {{ :; }}\n" + section('install.sh', LOGIN_STEP_SHELL, CLOSING_SHELL), newline='\n')
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

    def test_the_macos_launch_agent_starts_the_dashboard_on_the_recorded_port_at_login(self):
        self.install_macos_entries(bootstraps=True)
        agent = plistlib.loads((self.folder / LAUNCH_AGENT).read_bytes())
        self.assertEqual(agent['Label'], LAUNCH_AGENT_LABEL)
        self.assertIs(agent['RunAtLoad'], True)
        # Hermes counts a launchd job as the dashboard's only when its arguments start with hermes.
        self.assertEqual(Path(agent['ProgramArguments'][0]).name, 'hermes')
        self.assertEqual(' '.join(agent['ProgramArguments'][1:]), self.command)

    def test_the_macos_app_starts_a_stopped_dashboard_through_its_launch_agent_and_opens_its_chat(self):
        domain, agent = self.install_macos_entries(bootstraps=True)
        result = self.run_macos_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_made(), [f'bootstrap {domain} {agent}'])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_macos_app_opens_the_chat_of_hermes_at_the_recorded_address_without_starting_another(self):
        self.install_macos_entries(bootstraps=True)
        with serving(self.port, answering(200, HERMES_HEALTH)):
            result = self.run_macos_app()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_made(), [])
        self.assertEqual(self.opened.read_text(), self.chat_url)

    def test_the_macos_app_starts_hermes_past_a_foreign_listener_and_reports_that_it_did_not_answer(self):
        for kind, handler in FOREIGN.items():
            with self.subTest(kind), serving(self.port, handler):
                self.forget_runs()
                domain, agent = self.install_macos_entries(bootstraps=True)
                result = self.run_macos_app(start_seconds=2)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.calls_made(), [f'bootstrap {domain} {agent}'])
                self.assertIn(f'did not answer at http://127.0.0.1:{self.port}', self.alert.read_text())
                self.assertFalse(self.opened.exists())

    def test_the_macos_app_kickstarts_a_loaded_agent_and_reports_a_dashboard_that_does_not_start(self):
        domain, agent = self.install_macos_entries(bootstraps=False)
        result = self.run_macos_app(start_seconds=2)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls_made(), [f'bootstrap {domain} {agent}', f'kickstart {domain}/{LAUNCH_AGENT_LABEL}'])
        self.assertIn(f'did not answer at http://127.0.0.1:{self.port}', self.alert.read_text())
        self.assertFalse(self.opened.exists())


class BootstrapTests(unittest.TestCase):
    """Each bootstrap, run as its one line runs it, against a fake git and a fake installer in a temporary directory."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.tools = self.folder / 'bin'
        self.tools.mkdir()
        self.git_calls = self.folder / 'git-calls'
        self.installer_runs = self.folder / 'installer-runs'
        self.other_calls = self.folder / 'other-calls'
        self.template = self.folder / 'template'
        (self.template / '.git').mkdir(parents=True)
        (self.folder / 'git.py').write_text(FAKE_GIT)
        self.git = ' '.join(f'"{Path(path).as_posix()}"' for path in (sys.executable, self.folder / 'git.py', self.git_calls, self.template))

    def git_calls_made(self) -> list[list[str]]:
        return [json.loads(line) for line in self.git_calls.read_text().splitlines()] if self.git_calls.exists() else []

    def installer_runs_made(self) -> list[str]:
        return self.installer_runs.read_text().splitlines() if self.installer_runs.exists() else []

    def other_calls_made(self) -> list[str]:
        return self.other_calls.read_text().splitlines() if self.other_calls.exists() else []

    # macOS: bootstrap.sh piped into bash, with HOME in the temporary directory.

    def shell_tools(self, git: bool = True, installer_exit: int = 0, command_line_tools: bool = False):
        """`command_line_tools`: whether the Xcode command line tools are installed, so that their installer refuses to run."""
        fakes = {
            'git': f'exec {self.git} "$@"\n' if git else 'echo "xcrun: error: invalid active developer path" >&2\nexit 1\n',
            'xcode-select': f'printf "%s\\n" "xcode-select $*" >> "{self.other_calls.as_posix()}"\n'
                            + ('echo "xcode-select: error: command line tools are already installed" >&2\nexit 1\n'
                               if command_line_tools else ''),
        }
        for name, body in fakes.items():
            (self.tools / name).write_text(f'#!/bin/sh\n{body}', newline='\n')
            (self.tools / name).chmod(0o755)
        (self.template / 'install.sh').write_text(
            f'printf "%s|%s\\n" "$0" "$*" >> "{self.installer_runs.as_posix()}"\nexit {installer_exit}\n', newline='\n')

    @property
    def shell_environment(self) -> dict[str, str]:
        return {**os.environ, 'HOME': self.folder.as_posix(), 'PATH': f'{self.tools}{os.pathsep}{os.environ["PATH"]}'}

    def run_shell_bootstrap(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([BASH, '-s', '--', *arguments], input=(ROOT / 'bootstrap.sh').read_text(encoding='utf-8'),
                              env=self.shell_environment, capture_output=True, text=True, timeout=60)

    def test_the_shell_bootstrap_clones_and_runs_the_installer_then_updates_and_runs_it_again(self):
        self.shell_tools()
        repository = f'{self.folder.as_posix()}/Library/Application Support/apt-scraper/repo'
        for _ in range(2):
            result = self.run_shell_bootstrap('--example', 'two words')
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git_calls_made(), [['clone', REPOSITORY_URL, repository],
                                                 ['-C', repository, 'pull', '--ff-only']])
        # Git Bash spells HOME its own way and converts it back for Windows programs such as the fake git.
        home = subprocess.run([BASH, '-c', 'printf %s "$HOME"'], env=self.shell_environment, capture_output=True,
                              text=True, check=True).stdout
        self.assertEqual(self.installer_runs_made(),
                         [f'{home}/Library/Application Support/apt-scraper/repo/install.sh|--example two words'] * 2)

    def test_the_shell_bootstrap_stops_with_the_installer_exit_code(self):
        self.shell_tools(installer_exit=3)
        result = self.run_shell_bootstrap()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(self.installer_runs_made()), 1)

    def test_the_shell_bootstrap_without_the_command_line_tools_opens_their_installer_and_stops(self):
        self.shell_tools(git=False)
        result = self.run_shell_bootstrap()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Install them in the dialog that opened, then run this again.', result.stderr)
        self.assertEqual(self.other_calls_made(), ['xcode-select --install'])
        self.assertEqual((self.git_calls_made(), self.installer_runs_made()), ([], []))

    def test_the_shell_bootstrap_reports_why_git_does_not_run_when_the_command_line_tools_are_installed(self):
        self.shell_tools(git=False, command_line_tools=True)
        result = self.run_shell_bootstrap()
        self.assertEqual(result.returncode, 1)
        self.assertIn('command line tools are already installed', result.stderr)
        self.assertIn('Git does not run: xcrun: error: invalid active developer path', result.stderr)
        self.assertNotIn('dialog', result.stderr)
        self.assertEqual((self.git_calls_made(), self.installer_runs_made()), ([], []))

    # Windows: bootstrap.ps1 through Invoke-Expression, with its repository in the temporary directory and a PATH
    # without the machine's git.

    def powershell_tools(self, git: bool = True, installer_exit: int = 0):
        git_command = f'@{self.git} %*\r\n'
        if git:
            (self.tools / 'git.cmd').write_text(git_command)
        else:
            (self.folder / 'git.cmd').write_text(git_command)
            (self.tools / 'winget.cmd').write_text(f'@echo winget %*>>"{self.other_calls}"\r\n'
                                                   f'@copy /y "{self.folder / "git.cmd"}" "{self.tools}" >nul\r\n')
        (self.template / 'install.ps1').write_text(
            f"Add-Content -LiteralPath '{self.installer_runs}' -Value \"$PSScriptRoot|$($args -join ' ')\"\r\n"
            f"Set-Content -LiteralPath '{self.installer_path}' -Value $env:Path\r\n"
            f'exit {installer_exit}\r\n')

    @property
    def installer_path(self) -> Path:
        """The PATH the fake installer ran with."""
        return self.folder / 'installer-path'

    @property
    def session_path(self) -> list[str]:
        """The PATH of the session the bootstrap runs in: the fakes and System32, without the machine's git."""
        return [str(self.tools), str(Path(os.environ['SystemRoot']) / 'System32')]

    def run_powershell_bootstrap(self, invocation: str) -> subprocess.CompletedProcess:
        """Runs the bootstrap through `invocation`, in which `$text` is the script's text."""
        text = (ROOT / 'bootstrap.ps1').read_text(encoding='utf-8')
        location = "$Repository = Join-Path $env:LOCALAPPDATA 'apt-scraper\\repo'"
        self.assertEqual(text.count(location), 1)
        script = self.folder / 'bootstrap.ps1'
        script.write_text(text.replace(location, f"$Repository = '{self.windows_repository}'"), encoding='utf-8')
        command = f"$text = Get-Content -Raw -LiteralPath '{script}'; {invocation}"
        return subprocess.run([shutil.which('powershell'), '-NoProfile', '-Command', command],
                              env={**os.environ, 'PATH': os.pathsep.join(self.session_path)},
                              capture_output=True, text=True, timeout=60)

    @property
    def windows_repository(self) -> Path:
        return self.folder / 'LocalAppData' / 'apt-scraper' / 'repo'

    def test_the_powershell_bootstrap_clones_and_runs_the_installer_then_updates_and_runs_it_again(self):
        self.powershell_tools()
        repository = str(self.windows_repository)
        for _ in range(2):
            result = self.run_powershell_bootstrap('$text | Invoke-Expression')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git_calls_made(), [['clone', REPOSITORY_URL, repository],
                                                 ['-C', repository, 'pull', '--ff-only']])
        self.assertEqual(self.installer_runs_made(), [f'{repository}|'] * 2)

    def test_the_powershell_bootstrap_passes_its_arguments_to_the_installer(self):
        self.powershell_tools()
        result = self.run_powershell_bootstrap("& ([scriptblock]::Create($text)) -Example 'two words'")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installer_runs_made(), [f'{self.windows_repository}|-Example two words'])

    def test_the_powershell_bootstrap_installs_git_with_winget_when_it_is_missing_and_reads_the_new_path(self):
        self.powershell_tools(git=False)
        result = self.run_powershell_bootstrap('$text | Invoke-Expression')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.other_calls_made(), ['winget install --id Git.Git --exact --source winget '
                                                   '--accept-package-agreements --accept-source-agreements'])
        self.assertEqual(self.git_calls_made(), [['clone', REPOSITORY_URL, str(self.windows_repository)]])
        self.assertEqual(len(self.installer_runs_made()), 1)
        # The Git installer adds Git to the PATH in the registry only. That PATH is read here, not changed.
        registry = subprocess.run([shutil.which('powershell'), '-NoProfile', '-Command',
                                   "[Environment]::GetEnvironmentVariable('Path', 'Machine');"
                                   "[Environment]::GetEnvironmentVariable('Path', 'User')"],
                                  capture_output=True, text=True, check=True).stdout
        installer_path = self.installer_path.read_text().strip().split(';')
        self.assertEqual(installer_path[:len(self.session_path)], self.session_path)
        self.assertLessEqual({entry for entry in re.split(r'[;\n]', registry) if entry.strip()}, set(installer_path))

    def test_the_powershell_bootstrap_throws_the_installer_failure_and_leaves_the_session_as_it_was(self):
        self.powershell_tools(installer_exit=3)
        result = self.run_powershell_bootstrap(
            'try { $text | Invoke-Expression } catch { "caught: $_" }; "session: $ErrorActionPreference '
            '$([bool](Get-Command Write-Step -ErrorAction SilentlyContinue)) '
            '$([bool](Get-Variable Repository -ErrorAction SilentlyContinue))"')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r'caught: .*failed with exit code 3')
        self.assertIn('session: Continue False False', result.stdout)
        self.assertEqual(len(self.installer_runs_made()), 1)


if __name__ == '__main__':
    unittest.main()
