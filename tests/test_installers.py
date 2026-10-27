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
# Where install.ps1 defines the functions it runs native commands with.
RUNNERS = 'function Invoke-OnConsole'
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
# What Hermes 0.22.5's prompt_yes_no asks before `gateway install` opens the UAC prompt
# (hermes_cli/gateway_windows.py, hermes_cli/setup.py): input() with a question that ends without a newline.
QUESTION = '  Open the UAC prompt now? [y/N]: '
# Asks QUESTION on standard input as Hermes does and writes the answer it read to the file in argv[1].
ASKING = textwrap.dedent(f"""\
    import json, sys
    answer = input({QUESTION!r})
    with open(sys.argv[1], 'a') as answers:
        answers.write(json.dumps(answer) + '\\n')
    """)
LOGIN_ENTRY = 'Hermes_Gateway'
# What Hermes 0.22.5's `gateway ensure` prints for a gateway started as administrator, seen from an unelevated
# process (hermes_cli/gateway_runtime_cli.py, gateway/runtime_bootstrap_windows.py), and its exit code.
ELEVATED_DETAIL = ('The running gateway (PID 49984) was started as administrator, so this non-elevated app cannot '
                   'reach it. Stop it from an administrator prompt (hermes gateway stop), then start Hermes without '
                   'elevation')
# The sentence of it that names the gateway, which the installer shows without Hermes's remedy.
ELEVATED_NAMED = 'The running gateway (PID 49984) was started as administrator, so this non-elevated app cannot reach it.'
ELEVATED = (json.dumps({'detail': ELEVATED_DETAIL, 'endpoint': None, 'reason_code': 'gateway_elevated',
                        'state': 'inaccessible'}), 4)
READY = (json.dumps({'endpoint': {'pid': 1}, 'reason_code': None, 'state': 'ready'}), 0)
# Stands in for hermes's Windows gateway commands as Hermes 0.22.5 runs them (hermes_cli/gateway_windows.py).
# It logs every call to the file in argv[1]. The login entry is the file in argv[2]; a gateway running as
# administrator is marked by '<entry>.elevated', one running as the user by '<entry>.running'.
# Run as administrator (FAKE_HERMES_ELEVATED set), `gateway install` adds the Scheduled Task without asking,
# starts the gateway as administrator when given --start-now, and exits with FAKE_HERMES_ELEVATED when that
# is a number. Run as the user, it asks QUESTION as Hermes does and logs the answer; on n it adds a Startup
# entry itself. With --if-missing over an existing entry, it does nothing.
# `gateway status` shows the login entry and whether a gateway process runs. `gateway start` logs whether the
# entry existed and a gateway ran as administrator; without the entry it asks to install it, and it starts a
# gateway as the user unless one runs. `gateway stop` ends any gateway after a second. `gateway ensure`
# reports an administrator gateway as Hermes does, else a ready one, unless FAKE_HERMES_ENSURE and
# FAKE_HERMES_ENSURE_EXIT say otherwise.
FAKE_GATEWAY_HERMES = textwrap.dedent(f"""\
    import json, os, subprocess, sys, time
    from pathlib import Path
    log, entry, *arguments = sys.argv[1:]
    entry, elevated, running = Path(entry), Path(entry + '.elevated'), Path(entry + '.running')
    def record(item):
        with open(log, 'a') as calls:
            calls.write(json.dumps(item) + '\\n')
    record(arguments)
    command = arguments[2:4]
    if command == ['gateway', 'install']:
        if '--if-missing' in arguments and entry.exists():
            print('✓ Gateway service already installed')
            sys.exit()
        administrator = os.environ.get('FAKE_HERMES_ELEVATED')
        if administrator:
            if administrator.isdigit():
                sys.exit(int(administrator))
            entry.touch()
            if '--start-now' in arguments:
                elevated.touch()
            print('✓ Scheduled Task registered')
            sys.exit()
        answer = input({QUESTION!r})
        record({{'answer': answer}})
        if answer != 'y':
            entry.touch()
    elif command == ['gateway', 'status']:
        print(f'✓ Scheduled Task registered: {LOGIN_ENTRY}' if entry.exists() else '✗ Gateway service not installed')
        print('✓ Gateway process running (PID: 49984)' if elevated.exists() or running.exists()
              else '✗ No gateway process detected')
    elif command == ['gateway', 'start']:
        record({{'entry': entry.exists(), 'elevated': elevated.exists()}})
        if not entry.exists():
            print('✗ Gateway service is not installed')
            input('  Install it now so the gateway starts on login? [Y/n]: ')
        if elevated.exists() or running.exists():
            print('✓ Gateway already running (PID: 49984)')
        else:
            running.touch()
    elif command == ['gateway', 'stop']:
        time.sleep(1)
        elevated.unlink(missing_ok=True)
        running.unlink(missing_ok=True)
        print('✓ Gateway stopped')
    elif command == ['gateway', 'ensure']:
        reply, code = {ELEVATED!r} if elevated.exists() else {READY!r}
        print(os.environ.get('FAKE_HERMES_ENSURE', reply))
        sys.exit(int(os.environ.get('FAKE_HERMES_ENSURE_EXIT', code)))
    """)
GATEWAY_INSTALL = ['-p', 'default', 'gateway', 'install', '--if-missing', '--no-start-now', '--start-on-login']
GATEWAY_INSTALL_SHELL = ['-p', 'default', 'gateway', 'install', '--if-missing', '--start-now', '--start-on-login']
GATEWAY_STATUS = ['-p', 'default', 'gateway', 'status']
GATEWAY_START = ['-p', 'default', 'gateway', 'start']
GATEWAY_ENSURE = ['-p', 'default', 'gateway', 'ensure', '--timeout', '60']
STARTED = {'entry': True, 'elevated': False}
GATEWAY_STOP = ['-p', 'default', 'gateway', 'stop', '--yes']
ELEVATED_STOP = "Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'stop', '--yes') -Verb RunAs"
ELEVATED_INSTALL = ("Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'install', '--if-missing', "
                    "'--no-start-now', '--start-on-login') -Verb RunAs")
# What Windows PowerShell 5.1's Start-Process -Verb RunAs throws when the user answers No.
DECLINED = ("function Deny-Elevation { throw [InvalidOperationException]::new("
            "'This command cannot be run due to the error: The operation was canceled by the user.') }\n")
GATEWAY_STEP_POWERSHELL = "Write-Step 'Hermes gateway service'"
TEST_MESSAGE_POWERSHELL = "Write-Step 'Test message"


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


def answer_when_asked(command: list[str], environment: dict[str, str], answer: str) -> tuple[bool, str, int]:
    """Runs command with its standard input and output on pipes in place of a console: Windows PowerShell hands
    either to a native command alike, and reads a native command's output line by line from either alike. Types
    answer once QUESTION shows in the output, or after 30 seconds without it. Returns whether the question showed
    before the answer was typed, the whole output and the exit code."""
    process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    output = bytearray()
    reader = threading.Thread(target=lambda: [output.extend(chunk) for chunk in iter(lambda: process.stdout.read1(4096), b'')],
                              daemon=True)
    reader.start()
    asked = wait_for(lambda: QUESTION.strip().encode() in output, 30)
    process.stdin.write(f'{answer}\n'.encode())
    process.stdin.close()
    try:
        process.wait(60)
    except subprocess.TimeoutExpired:
        process.kill()
        raise
    reader.join(10)
    return asked, output.decode(errors='replace'), process.returncode


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
                """) + section(installer, RUNNERS, 'function Get-Version')
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


class BrowserSettingsTests(unittest.TestCase):
    """The scheduled jobs share the profile's config, so neither the distribution nor the installers turn on the
    user's own browser profile or a visible window: the account setup turns them on only while it runs."""

    SETUP_ONLY = ('use_real_profile', 'headed', 'real_profile_autoclose')

    def test_the_distribution_config_and_the_installers_set_the_browser_backend_and_nothing_of_the_setup(self):
        config = (ROOT / 'profile' / 'config.yaml').read_text(encoding='utf-8')
        self.assertIn('\nbrowser:\n  backend: "off"\n', config)
        for name in ('profile/config.yaml', 'install.ps1', 'install.sh'):
            text = (ROOT / name).read_text(encoding='utf-8')
            for setting in self.SETUP_ONLY:
                with self.subTest(name, setting=setting):
                    self.assertNotIn(setting, text)
        self.assertIn("config', 'set', 'browser.backend', 'off')", (ROOT / 'install.ps1').read_text(encoding='utf-8'))
        self.assertIn('config set browser.backend off', (ROOT / 'install.sh').read_text(encoding='utf-8'))


class QuestionTests(unittest.TestCase):
    """A question that a command asks on the console shows before the installer waits for its answer."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.calls = self.folder / 'calls'
        self.entry = self.folder / LOGIN_ENTRY
        (self.folder / 'hermes.py').write_text(FAKE_GATEWAY_HERMES, encoding='utf-8')
        self.fake = [sys.executable, str(self.folder / 'hermes.py'), str(self.calls), str(self.entry)]
        # Hermes writes its ✓ and ✗ to a pipe as UTF-8 too.
        self.environment = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}

    def calls_made(self) -> list:
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def powershell_gateway_step(self, deadline_seconds: int = 120, replacements: dict[str, str] | None = None,
                                prelude: str = '') -> list[str]:
        """The command that runs install.ps1's gateway step against the fake hermes, with each of `replacements`
        made once in the step and `prelude` run before it."""
        hermes = self.folder / 'hermes.cmd'
        hermes.write_text('@' + ' '.join(f'"{part}"' for part in self.fake) + ' %*\r\n')
        step = section('install.ps1', GATEWAY_STEP_POWERSHELL, TEST_MESSAGE_POWERSHELL)
        deadline = '(Get-Date).AddMinutes(2)'
        self.assertEqual(step.count(deadline), 1)
        for old, new in (replacements or {}).items():
            self.assertEqual(step.count(old), 1, old)
            step = step.replace(old, new)
        script = self.folder / 'step.ps1'
        script.write_text(textwrap.dedent(f"""\
            $ErrorActionPreference = 'Stop'
            $hermes = '{hermes}'
            function Write-Step([string]$Message) {{ }}
            """) + section('install.ps1', RUNNERS, 'function Get-Version') + prelude
            + step.replace(deadline, f'(Get-Date).AddSeconds({deadline_seconds})'), encoding='utf-8-sig')
        return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)]

    def run_gateway_step(self, environment: dict[str, str] | None = None, **step) -> subprocess.CompletedProcess:
        return subprocess.run(self.powershell_gateway_step(**step), env=environment or self.environment,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def run_on_a_fresh_machine(self, elevated: str = 'administrator', **step) -> subprocess.CompletedProcess:
        """Runs the gateway step on a machine without the login entry. The step's elevated `gateway install` runs
        here without the elevation, which no test may ask for; the fake acts as administrator."""
        replacements = {ELEVATED_INSTALL: ELEVATED_INSTALL.replace(' -Verb RunAs', ''), **step.pop('replacements', {})}
        return self.run_gateway_step({**self.environment, 'FAKE_HERMES_ELEVATED': elevated},
                                     replacements=replacements, **step)

    def run_on_a_machine_with_an_administrator_gateway(self, **step) -> subprocess.CompletedProcess:
        """Runs the gateway step where the owner's machine was: the login entry installed, so nothing asks for
        administrator approval to add it, and a gateway running as administrator."""
        self.entry.touch()
        Path(f'{self.entry}.elevated').touch()
        return self.run_gateway_step(**step)

    def test_the_powershell_installer_adds_the_login_entry_through_the_uac_prompt_without_a_question_first(self):
        result = self.run_on_a_fresh_machine()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn(QUESTION.strip(), output)
        self.assertIn('Windows asks for administrator approval to add the gateway', output)
        self.assertEqual(self.calls_made(), [GATEWAY_STATUS, GATEWAY_INSTALL, GATEWAY_STATUS, GATEWAY_START, STARTED,
                                             GATEWAY_ENSURE])
        self.assertTrue(self.entry.exists())
        self.assertFalse(Path(f'{self.entry}.elevated').exists())
        self.assertTrue(Path(f'{self.entry}.running').exists())

    def test_the_powershell_installer_skips_the_uac_prompt_on_a_machine_that_has_the_login_entry(self):
        self.entry.touch()
        result = self.run_gateway_step()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn('administrator approval', output)
        self.assertEqual(self.calls_made(), [GATEWAY_STATUS, GATEWAY_START, STARTED, GATEWAY_ENSURE])

    def test_the_powershell_installer_reports_a_declined_uac_prompt_for_the_login_entry(self):
        result = self.run_gateway_step(
            replacements={ELEVATED_INSTALL: ELEVATED_INSTALL.replace('Start-Process -FilePath $hermes', 'Deny-Elevation')},
            prelude=DECLINED)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertEqual(self.calls_made(), [GATEWAY_STATUS])
        flat = ' '.join(output.split())
        self.assertIn('adding it needs administrator approval, which was not given', flat)
        self.assertIn('The operation was canceled by the user', flat)

    def test_the_powershell_installer_reports_a_failed_administrator_install(self):
        result = self.run_on_a_fresh_machine(elevated='3')
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn("The administrator 'hermes gateway install' failed with exit code 3", output)
        self.assertEqual(self.calls_made(), [GATEWAY_STATUS, GATEWAY_INSTALL])

    def test_the_shell_installer_shows_the_question_hermes_asks_in_the_gateway_step_and_reads_the_answer(self):
        tools = self.folder / 'bin'
        tools.mkdir()
        fake = ' '.join(f'"{Path(part).as_posix()}"' for part in self.fake)
        (tools / 'hermes').write_text(f'#!/bin/sh\nexec {fake} "$@"\n', newline='\n')
        (tools / 'hermes').chmod(0o755)
        script = self.folder / 'step.sh'
        script.write_text("set -euo pipefail\nstep() { :; }\n"
                          + section('install.sh', "step 'Hermes gateway service'", "step 'Test message"), newline='\n')
        asked, output, exit_code = answer_when_asked(
            [BASH, script.as_posix()], {**self.environment, 'PATH': f'{tools}{os.pathsep}{os.environ["PATH"]}'}, 'n')
        self.assertTrue(asked, output)
        self.assertEqual(exit_code, 0, output)
        # launchd installs in this process and never elevates, so the shell installer keeps --start-now.
        self.assertEqual(self.calls_made(), [GATEWAY_INSTALL_SHELL, {'answer': 'n'}, GATEWAY_START, STARTED])

    def test_the_powershell_installer_never_asks_hermes_to_start_the_gateway_from_its_install(self):
        text = (ROOT / 'install.ps1').read_text(encoding='utf-8')
        self.assertEqual(text.count("'--start-now'"), 0)
        self.assertEqual(text.count(ELEVATED_INSTALL), 1)

    def test_the_powershell_installer_stops_a_gateway_running_as_administrator_through_one_uac_prompt(self):
        """Approved: the step's elevated `gateway stop` runs, here without the elevation, which no test may ask
        for. Once that stop has exited and no gateway runs, the gateway is started again as the user."""
        result = self.run_on_a_machine_with_an_administrator_gateway(
            replacements={ELEVATED_STOP: ELEVATED_STOP.replace(' -Verb RunAs', '')})
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        calls = self.calls_made()
        self.assertEqual(calls[:5], [GATEWAY_STATUS, GATEWAY_START, {'entry': True, 'elevated': True}, GATEWAY_ENSURE,
                                     GATEWAY_STOP])
        self.assertEqual(calls[-4:], [GATEWAY_STATUS, GATEWAY_START, STARTED, GATEWAY_ENSURE])
        self.assertFalse(Path(f'{self.entry}.elevated').exists())
        self.assertTrue(Path(f'{self.entry}.running').exists())
        flat = ' '.join(output.split())
        self.assertIn(ELEVATED_NAMED, flat)
        self.assertNotIn('administrator prompt', flat)
        self.assertIn('Windows asks for administrator approval to stop it.', flat)

    def test_the_powershell_installer_reports_a_declined_uac_prompt_and_what_keeps_it_from_continuing(self):
        result = self.run_on_a_machine_with_an_administrator_gateway(
            replacements={ELEVATED_STOP: ELEVATED_STOP.replace('Start-Process -FilePath $hermes', 'Deny-Elevation')},
            prelude=DECLINED)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertEqual(self.calls_made(), [GATEWAY_STATUS, GATEWAY_START, {'entry': True, 'elevated': True},
                                             GATEWAY_ENSURE])
        self.assertTrue(Path(f'{self.entry}.elevated').exists())
        flat = ' '.join(output.split())
        self.assertIn(ELEVATED_NAMED, flat)
        self.assertNotIn('administrator prompt', flat)
        self.assertIn('The install cannot continue while it runs', flat)
        self.assertIn('The operation was canceled by the user', flat)

    def test_the_powershell_installer_asks_for_administrator_approval_only_through_windows_prompts(self):
        """Both elevated commands are the user's approval: the prompt itself, with no question in the terminal."""
        text = (ROOT / 'install.ps1').read_text(encoding='utf-8')
        self.assertEqual(text.count('-Verb RunAs'), 2)
        self.assertEqual(text.count(ELEVATED_INSTALL), 1)
        self.assertEqual(text.count(ELEVATED_STOP), 1)

    def test_the_powershell_installer_shows_the_output_of_a_failed_command_whose_output_it_reads(self):
        printer = self.folder / 'printer.py'
        printer.write_text("import sys\nprint('Gateway inaccessible: gateway_elevated')\n"
                           "print('on stderr', file=sys.stderr)\nsys.exit(1)\n")
        script = self.folder / 'read.ps1'
        script.write_text("$ErrorActionPreference = 'Stop'\n" + section('install.ps1', RUNNERS, 'function Get-Version')
                          + f"Get-NativeOutput '{sys.executable}' @('{printer}')\n", encoding='utf-8-sig')
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed with exit code 1', result.stderr)
        self.assertIn('Gateway inaccessible: gateway_elevated', result.stderr)
        self.assertIn('on stderr', result.stderr)

    def test_the_powershell_installer_reads_only_stdout_from_a_command_that_succeeds_and_shows_its_stderr(self):
        printer = self.folder / 'printer.py'
        printer.write_text("import sys\nprint('{\"PATH\": \"x\"}')\nprint('a warning', file=sys.stderr)\n")
        script = self.folder / 'read.ps1'
        script.write_text("$ErrorActionPreference = 'Stop'\n" + section('install.ps1', RUNNERS, 'function Get-Version')
                          + f"$read = Get-NativeOutput '{sys.executable}' @('{printer}') | ConvertFrom-Json\n"
                          + "Write-Host \"read: $($read.PATH)\"\n", encoding='utf-8-sig')
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('read: x', result.stdout)
        self.assertIn('a warning', result.stdout)

    def test_the_powershell_installer_runs_on_the_console_every_command_whose_output_it_does_not_read(self):
        """Windows PowerShell hands the console only to a native command that is a statement of its own, so every
        Invoke-OnConsole is one, and only the commands whose output the installer reads run through Get-NativeOutput."""
        script = self.folder / 'commands.ps1'
        script.write_text(textwrap.dedent("""\
            using namespace System.Management.Automation.Language
            $tree = [Parser]::ParseFile($args[0], [ref]$null, [ref]$null)
            ConvertTo-Json @($tree.FindAll({ param($node) $node -is [CommandAst] }, $true) | ForEach-Object {
                $pipeline = $_.Parent
                $function = $_.Parent
                while ($function -and $function -isnot [FunctionDefinitionAst]) { $function = $function.Parent }
                [pscustomobject]@{
                    Name = $_.GetCommandName()
                    Operator = "$($_.InvocationOperator)"
                    Text = $_.Extent.Text
                    Statement = $pipeline.PipelineElements.Count -eq 1 -and
                        ($pipeline.Parent -is [StatementBlockAst] -or $pipeline.Parent -is [NamedBlockAst])
                    Function = "$($function.Name)"
                }
            })
            """), encoding='utf-8-sig')
        commands = json.loads(subprocess.run(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), str(ROOT / 'install.ps1')],
            capture_output=True, text=True, check=True).stdout)
        def named(name: str, statement: bool | None = None) -> list[str]:
            return [command['Text'] for command in commands
                    if command['Name'] == name and statement in (None, command['Statement'])]
        self.assertEqual(named('Invoke-OnConsole', statement=False), [])
        self.assertEqual({(command['Function'], command['Statement']) for command in commands if command['Operator'] == 'Ampersand'},
                         {('Invoke-OnConsole', True), ('Get-NativeOutput', False)})
        self.assertEqual(named('Out-Host'), [])
        self.assertEqual(named('Get-NativeOutput'), [
            "Get-NativeOutput claude @('--version')",
            "Get-NativeOutput claude @('auth', 'status') -AllowFailure",
            "Get-NativeOutput $hermes @('--version')",
            "Get-NativeOutput $hermes @('-p', $ProfileName, 'config', 'get', 'browser.backend')",
            "Get-NativeOutput $hermes @('pm', 'env')",
            "Get-NativeOutput $hermes @('-p', $ProfileName, 'config', 'get', 'delegation.model')",
            "Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'status') -AllowFailure",
            "Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'status') -AllowFailure",
            "Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'ensure', '--timeout', '60') -AllowFailure",
            "Get-NativeOutput $hermes @('-p', 'default', 'gateway', 'status') -AllowFailure",
            "Get-NativeOutput $hermes @('-p', $ProfileName, 'chat', '-q', 'Reply with exactly: ready', '--oneshot', '-Q')",
        ])
        for asks in ("Invoke-OnConsole claude @('auth', 'login')",
                     "Invoke-OnConsole $hermes @('update')",
                     "Invoke-OnConsole $hermes @('profile', 'install', $ProfileSource, '--force', '--yes')",
                     "Invoke-OnConsole $hermes @('-p', 'default', 'gateway', 'start')",
                     "Invoke-OnConsole $hermes @('-p', $ProfileName, 'computer-use', 'install', '--upgrade')"):
            self.assertIn(asks, named('Invoke-OnConsole'))


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

    def shell_tools(self, git: bool = True, installer_exit: int = 0, command_line_tools: bool = False,
                    install_after_seconds: int | None = None):
        """`command_line_tools`: whether the Xcode command line tools are installed, so that their installer refuses
        to run. `install_after_seconds`: when their installer is opened, how long it takes to install them."""
        self.tools_installed = self.folder / 'command-line-tools'
        fakes = {
            # Without the tools, git fails until the marker file that Apple's installer stands in for exists.
            'git': f'exec {self.git} "$@"\n' if git else
                   f'if [ -e "{self.tools_installed.as_posix()}" ]; then exec {self.git} "$@"; fi\n'
                   'echo "xcrun: error: invalid active developer path" >&2\nexit 1\n',
            'xcode-select': f'printf "%s\\n" "xcode-select $*" >> "{self.other_calls.as_posix()}"\n'
                            + ('echo "xcode-select: error: command line tools are already installed" >&2\nexit 1\n'
                               if command_line_tools else
                               # The dialog installs the tools after a while, in the background, as Apple's does.
                               f'(sleep {install_after_seconds}; touch "{self.tools_installed.as_posix()}") &\n'
                               if install_after_seconds is not None else ''),
        }
        for name, body in fakes.items():
            (self.tools / name).write_text(f'#!/bin/sh\n{body}', newline='\n')
            (self.tools / name).chmod(0o755)
        (self.template / 'install.sh').write_text(
            f'printf "%s|%s\\n" "$0" "$*" >> "{self.installer_runs.as_posix()}"\nexit {installer_exit}\n', newline='\n')

    @property
    def shell_environment(self) -> dict[str, str]:
        return {**os.environ, 'HOME': self.folder.as_posix(), 'PATH': f'{self.tools}{os.pathsep}{os.environ["PATH"]}'}

    def run_shell_bootstrap(self, *arguments: str, poll_seconds: int = 5, wait_seconds: int = 60) -> subprocess.CompletedProcess:
        environment = {**self.shell_environment, 'COMMAND_LINE_TOOLS_POLL_SECONDS': str(poll_seconds),
                       'COMMAND_LINE_TOOLS_WAIT_SECONDS': str(wait_seconds)}
        return subprocess.run([BASH, '-s', '--', *arguments], input=(ROOT / 'bootstrap.sh').read_text(encoding='utf-8'),
                              env=environment, capture_output=True, text=True, timeout=60)

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

    def test_the_shell_bootstrap_without_the_command_line_tools_opens_their_installer_waits_for_them_and_goes_on(self):
        self.shell_tools(git=False, install_after_seconds=2)
        result = self.run_shell_bootstrap(poll_seconds=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Press Install in the dialog that opened; this continues once they are installed.', result.stdout)
        self.assertEqual(self.other_calls_made(), ['xcode-select --install'])
        self.assertEqual(self.git_calls_made()[0][:1], ['clone'])
        self.assertEqual(len(self.installer_runs_made()), 1)

    def test_the_shell_bootstrap_stops_when_the_command_line_tools_do_not_get_installed(self):
        self.shell_tools(git=False)
        result = self.run_shell_bootstrap(poll_seconds=1, wait_seconds=2)
        self.assertEqual(result.returncode, 1)
        self.assertIn('did not get installed. Install them in the dialog, then run this again.', result.stderr)
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

    def powershell_bootstrap(self, invocation: str) -> tuple[list[str], dict[str, str]]:
        """The command and environment that run the bootstrap through `invocation`, in which `$text` is the script's text."""
        text = (ROOT / 'bootstrap.ps1').read_text(encoding='utf-8')
        location = "$Repository = Join-Path $env:LOCALAPPDATA 'apt-scraper\\repo'"
        self.assertEqual(text.count(location), 1)
        script = self.folder / 'bootstrap.ps1'
        script.write_text(text.replace(location, f"$Repository = '{self.windows_repository}'"), encoding='utf-8')
        command = f"$text = Get-Content -Raw -LiteralPath '{script}'; {invocation}"
        return ([shutil.which('powershell'), '-NoProfile', '-Command', command],
                {**os.environ, 'PATH': os.pathsep.join(self.session_path)})

    def run_powershell_bootstrap(self, invocation: str) -> subprocess.CompletedProcess:
        command, environment = self.powershell_bootstrap(invocation)
        return subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)

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

    def test_the_powershell_bootstrap_shows_a_question_the_installer_asks_on_the_console_and_reads_the_answer(self):
        self.powershell_tools()
        answers = self.folder / 'answers'
        (self.folder / 'ask.py').write_text(ASKING)
        (self.template / 'install.ps1').write_text(
            "$ErrorActionPreference = 'Stop'\r\n" + section('install.ps1', RUNNERS, 'function Get-Version')
            + f"Invoke-OnConsole '{sys.executable}' @('{self.folder / 'ask.py'}', '{answers}')\r\n", encoding='utf-8-sig')
        asked, output, exit_code = answer_when_asked(*self.powershell_bootstrap('$text | Invoke-Expression'), 'y')
        self.assertTrue(asked, output)
        self.assertEqual(exit_code, 0, output)
        self.assertEqual(answers.read_text().splitlines(), ['"y"'])



# Stands in for hermes in the uninstall tests: FAKE_HERMES, plus `profile delete` as Hermes 0.22.5 runs it
# (hermes_cli/profiles.py delete_profile): it asks for the name unless given --yes, refuses a missing or deleted
# name, marks the name in profiles/.deleted and removes the directory. The dashboard prints what a fresh one
# does, `gateway status` prints FAKE_GATEWAY_STATUS, and `gateway uninstall` asks QUESTION unless
# HERMES_NONINTERACTIVE is 1, as Hermes does when Windows refuses to remove the Scheduled Task.
FAKE_UNINSTALL_HERMES = FAKE_HERMES + textwrap.dedent("""\
    elif arguments[:2] == ['profile', 'delete']:
        if '--yes' not in arguments and input(f"Type '{arguments[2]}' to confirm: ") != arguments[2]:
            sys.exit('Cancelled.')
        profile = profiles / arguments[2]
        if not profile.is_dir() or (profiles / '.deleted' / arguments[2]).exists():
            sys.exit(f"Error: Profile '{arguments[2]}' does not exist.")
        (profiles / '.deleted').mkdir(exist_ok=True)
        (profiles / '.deleted' / arguments[2]).write_text('deleted\\n')
        shutil.rmtree(profile)
    elif 'dashboard' in arguments:
        print('Routing to the machine dashboard.')
        print('HERMES_DASHBOARD_READY port=9119')
    elif arguments[-2:] == ['gateway', 'status']:
        print(os.environ.get('FAKE_GATEWAY_STATUS', 'Gateway service not installed'))
    elif arguments[-2:] == ['gateway', 'uninstall'] and os.environ.get('HERMES_NONINTERACTIVE') != '1':
        input(QUESTION)
    """).replace('QUESTION', repr(QUESTION))
PROFILE_DELETE = ['profile', 'delete', 'apt-scraper', '--yes']
GATEWAY_STATUS_DEFAULT = ['-p', 'default', 'gateway', 'status']
GATEWAY_UNINSTALL = ['-p', 'default', 'gateway', 'uninstall']
HERMES_UNINSTALL = ['uninstall', '--yes']
TASK_REGISTERED = f'Scheduled Task registered: {LOGIN_ENTRY}'
ELEVATED_UNINSTALL = "Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'uninstall') -Verb RunAs"
# The elevated `gateway uninstall`, run without the UAC prompt and marked in its arguments.
AS_ADMINISTRATOR = "Start-Process -FilePath $hermes -ArgumentList @('-p', 'default', 'gateway', 'uninstall', '--as-administrator')"
UNINSTALL_LINE = '$text | Invoke-Expression'
EVERYTHING_LINE = '& ([scriptblock]::Create($text)) -Everything'
CLAUDE_UNINSTALL = 'https://code.claude.com/docs/en/setup#uninstall-claude-code'
# A process that only waits: what stands in for the dashboard and the setup browser.
WAITING = 'import time; time.sleep(120)'
# Every line of the installers and the bootstraps that names a location outside the Hermes profile, with the line
# of the uninstaller of the same platform that removes it, or None for what is not apt-scraper's to remove.
OUTSIDE_THE_PROFILE = {
    ('install.ps1', '$HermesRoot = "$env:LOCALAPPDATA\\hermes"'): '$HermesRoot = "$env:LOCALAPPDATA\\hermes"',
    # The upstream installers' temporary directory, which the installer removes.
    ('install.ps1', '$directory = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())'): None,
    # Claude Code's, which stays.
    ('install.ps1', '$env:Path = "$env:USERPROFILE\\.local\\bin;$env:Path"'): None,
    ('install.ps1', '$hermes = "$env:LOCALAPPDATA\\hermes\\bin\\hermes.exe"'): '$hermes = "$HermesRoot\\bin\\hermes.exe"',
    # The saved config.yaml, which the installer removes.
    ('install.ps1', '$savedConfig = [IO.Path]::GetTempFileName()'): None,
    ('install.ps1', '$dashboardLog = Join-Path ([IO.Path]::GetTempPath()) '
                    '"apt-scraper-dashboard-$([IO.Path]::GetRandomFileName()).log"'):
        "$DashboardLogs = Join-Path ([IO.Path]::GetTempPath()) 'apt-scraper-dashboard-*'",
    ('install.ps1', '$programs = "$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs"'):
        '$Programs = "$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs"',
    ('bootstrap.ps1', "$Repository = Join-Path $env:LOCALAPPDATA 'apt-scraper\\repo'"):
        '$DataDirectory = "$env:LOCALAPPDATA\\apt-scraper"',
    ('install.sh', 'HERMES_ROOT="$HOME/.hermes"'): 'HERMES_ROOT="$HOME/.hermes"',
    ('install.sh', 'export PATH="$HOME/.local/bin:$PATH"'): 'export PATH="$HOME/.local/bin:$PATH"',
    ('install.sh', 'directory=$(mktemp -d)'): None,
    ('install.sh', 'saved_config=$(mktemp)'): None,
    ('install.sh', 'dashboard_log=$(mktemp "${TMPDIR:-/tmp}/apt-scraper-dashboard.XXXXXX")'):
        'DASHBOARD_LOGS="${DASHBOARD_LOGS%/}/apt-scraper-dashboard."',
    ('install.sh', 'dashboard_agent="$HOME/Library/LaunchAgents/$dashboard_label.plist"'):
        'DASHBOARD_AGENT="$HOME/Library/LaunchAgents/$DASHBOARD_LABEL.plist"',
    ('install.sh', 'app="$HOME/Applications/$PROFILE_NAME.app/Contents"'): 'APP="$HOME/Applications/$PROFILE_NAME.app"',
    ('bootstrap.sh', 'REPOSITORY="$HOME/Library/Application Support/apt-scraper/repo"'):
        'DATA_DIRECTORY="$HOME/Library/Application Support/apt-scraper"',
}
OUTSIDE = re.compile(r'\$env:(APPDATA|LOCALAPPDATA|USERPROFILE|TEMP|TMP)\b|GetTemp(Path|FileName)|\$HOME\b|mktemp|TMPDIR')


class UninstallTests(unittest.TestCase):
    """Each uninstaller, run as its one line runs it, against a fake hermes, fake login entries and fake processes
    on a machine in a temporary directory: LOCALAPPDATA, APPDATA, TEMP and USERPROFILE for Windows, HOME and TMPDIR
    for macOS. What it runs cannot reach a real hermes: on Windows its PATH holds the fakes and System32, on macOS
    the fakes and Git Bash's tools."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.machine = self.folder / 'machine'
        self.tools = self.folder / 'bin'
        self.tools.mkdir()
        self.calls = self.folder / 'calls'
        self.other_calls = self.folder / 'other-calls'
        self.processes = self.folder / 'processes'
        self.processes.write_text('')
        (self.folder / 'hermes.py').write_text(FAKE_UNINSTALL_HERMES, encoding='utf-8')
        fake = [sys.executable, str(self.folder / 'hermes.py'), str(self.calls)]
        (self.tools / 'hermes.cmd').write_text('@' + ' '.join(f'"{part}"' for part in fake) + ' %*\r\n')
        record = f'printf "%s\\n" "$(basename "$0") $*" >> "{self.other_calls.as_posix()}"\n'
        fakes = {
            'hermes': 'exec ' + ' '.join(f'"{Path(part).as_posix()}"' for part in fake) + ' "$@"\n',
            'claude': record,
            'launchctl': record,
            'uname': 'echo Darwin\n',
            'open': record,
            # Lists the processes in self.processes as `ps -A -o pid= -o command=` does.
            'ps': f'cat "{self.processes.as_posix()}"\n',
        }
        for name, body in fakes.items():
            (self.tools / name).write_text(f'#!/bin/sh\n{body}', newline='\n')
            (self.tools / name).chmod(0o755)
        (self.tools / 'claude.cmd').write_text(f'@echo claude %*>>"{self.other_calls}"\r\n')

    def calls_made(self) -> list[list[str]]:
        """The fake hermes's calls since the last time they were read."""
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        self.calls.unlink(missing_ok=True)
        return calls

    def other_calls_made(self) -> list[str]:
        """The other fakes' calls since the last time they were read."""
        calls = self.other_calls.read_text().splitlines() if self.other_calls.exists() else []
        self.other_calls.unlink(missing_ok=True)
        return calls

    def without_hermes(self):
        (self.tools / 'hermes.cmd').unlink()
        (self.tools / 'hermes').unlink()

    def places(self, platform: str) -> dict[str, Path]:
        """Where each platform keeps what apt-scraper, Hermes and Claude Code put on the machine."""
        if platform == 'windows':
            local = self.machine / 'LocalAppData'
            programs = self.machine / 'AppData' / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
            return {'hermes': local / 'hermes', 'data': local / 'apt-scraper', 'temp': self.machine / 'Temp',
                    'user': self.machine / 'UserProfile', 'login': programs / 'Startup' / 'apt-scraper dashboard.lnk',
                    'shortcut': programs / 'apt-scraper.lnk', 'log': self.machine / 'Temp' / 'apt-scraper-dashboard-x1.log'}
        home = self.machine / 'home'
        return {'hermes': home / '.hermes', 'data': home / 'Library' / 'Application Support' / 'apt-scraper',
                'temp': self.machine / 'tmp', 'user': home, 'login': home / LAUNCH_AGENT, 'shortcut': home / APP,
                'log': self.machine / 'tmp' / 'apt-scraper-dashboard.x1'}

    def profile(self, platform: str) -> Path:
        return self.places(platform)['hermes'] / 'profiles' / 'apt-scraper'

    def mark(self, platform: str) -> Path:
        return self.places(platform)['hermes'] / 'profiles' / '.deleted' / 'apt-scraper'

    def environment(self, platform: str) -> dict[str, str]:
        places = self.places(platform)
        for place in ('hermes', 'temp', 'user'):
            places[place].mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, 'HERMES_HOME': str(places['hermes']), 'PYTHONIOENCODING': 'utf-8'}
        environment.pop('HERMES_NONINTERACTIVE', None)
        if platform == 'windows':
            return {**environment, 'LOCALAPPDATA': str(self.machine / 'LocalAppData'),
                    'APPDATA': str(self.machine / 'AppData'), 'TEMP': str(places['temp']), 'TMP': str(places['temp']),
                    'USERPROFILE': str(places['user']),
                    'PATH': os.pathsep.join([str(self.tools), str(Path(os.environ['SystemRoot']) / 'System32')])}
        return {**environment, 'HOME': places['user'].as_posix(), 'TMPDIR': places['temp'].as_posix(),
                'PATH': os.pathsep.join([str(self.tools), str(Path(BASH).parent)])}

    def keep(self, platform: str) -> list[str]:
        """Puts what Hermes and Claude Code keep of their own on the machine, which no uninstall touches, and
        returns every file on the machine."""
        shutil.rmtree(self.machine, ignore_errors=True)
        places = self.places(platform)
        self.environment(platform)
        claude = places['user'] / '.local' / 'bin' / ('claude.exe' if platform == 'windows' else 'claude')
        for path in (places['hermes'] / 'config.yaml', places['hermes'] / '.env',
                     places['hermes'] / 'profiles' / 'apt-scraper-other' / 'config.yaml',
                     places['hermes'] / 'profiles' / '.deleted' / 'other', claude,
                     places['user'] / '.local' / 'share' / 'claude' / 'versions' / '2.1.284',
                     places['user'] / '.claude.json', places['user'] / '.claude' / 'settings.json',
                     places['temp'] / 'hermes-dashboard-x1.log'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('kept\n')
        return self.files()

    def install(self, platform: str):
        """Puts on the machine what the installer, the bootstrap, the profile and the gallery leave there."""
        places = self.places(platform)
        profile = self.profile(platform)
        for path in ('config.yaml', 'distribution.yaml', 'dashboard-address', 'dashboard.vbs', 'google_token.json',
                     'cron/jobs.json', 'apt-scraper/connections.json', 'apt-scraper/browser/Default/Cookies',
                     'browser-profile/brave/Default/Cookies', 'workspace/gallery/listings.sqlite3',
                     'workspace/search/environment/pyvenv.cfg', 'plugins/claude-subscription-directsdk/plugin.yaml'):
            (profile / path).parent.mkdir(parents=True, exist_ok=True)
            (profile / path).write_text('apt-scraper\n')
        for path in (places['data'] / 'listings.sqlite3', places['data'] / 'sample.sqlite3',
                     places['data'] / 'repo' / '.git' / 'HEAD', places['data'] / 'repo' / 'install.ps1',
                     places['login'], places['log'], places['log'].with_name(places['log'].name + '.err')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('apt-scraper\n')
        if platform == 'macos':
            (places['shortcut'] / 'MacOS').mkdir(parents=True)
            (places['shortcut'] / 'MacOS' / 'apt-scraper').write_text('apt-scraper\n')
        else:
            places['shortcut'].write_text('apt-scraper\n')

    def files(self) -> list[str]:
        return sorted(path.relative_to(self.machine).as_posix() for path in self.machine.rglob('*') if path.is_file())

    def run_uninstall(self, platform: str, everything: bool = False, replacements: dict[str, str] | None = None,
                      prelude: str = '', environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        """Runs the uninstaller as its one line does, with each of `replacements` made once in its text. On Windows,
        `prelude` runs before the line in the same session."""
        environment = {**self.environment(platform), **(environment or {})}
        if platform == 'windows':
            text = (ROOT / 'uninstall.ps1').read_text(encoding='utf-8')
            for old, new in (replacements or {}).items():
                self.assertEqual(text.count(old), 1, old)
                text = text.replace(old, new)
            script = self.folder / 'uninstall.ps1'
            script.write_text(text, encoding='utf-8')
            line = EVERYTHING_LINE if everything else UNINSTALL_LINE
            command = [shutil.which('powershell'), '-NoProfile', '-Command',
                       f"$text = Get-Content -Raw -LiteralPath '{script}'; {prelude}{line}"]
            return subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace', timeout=120)
        arguments = ['--everything'] if everything else []
        return subprocess.run([BASH, '-s', '--', *arguments], input=(ROOT / 'uninstall.sh').read_text(encoding='utf-8'),
                              env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=120)

    def shell_profile(self) -> str:
        """The macOS profile's path as the uninstaller spells it: Git Bash spells HOME its own way."""
        home = subprocess.run([BASH, '-c', 'printf %s "$HOME"'], env=self.environment('macos'), capture_output=True,
                              text=True, check=True).stdout
        return f'{home}/.hermes/profiles/apt-scraper'

    def assert_removed(self, result: subprocess.CompletedProcess, kept: list[str]):
        """apt-scraper is gone, asking nothing, and only what Hermes and Claude Code keep is left."""
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.files(), kept)
        self.assertNotIn(QUESTION.strip(), result.stdout + result.stderr)
        self.assertNotIn('Type ', result.stdout + result.stderr)

    def test_the_uninstallers_remove_everything_apt_scraper_put_on_the_machine_through_hermes(self):
        for platform in ('windows', 'macos'):
            with self.subTest(platform):
                kept = self.keep(platform)
                self.install(platform)
                result = self.run_uninstall(platform)
                self.assert_removed(result, kept)
                self.assertEqual(self.calls_made(), [PROFILE_DELETE])
                output = result.stdout
                self.assertLess(output.index('This removes apt-scraper from this computer:'), output.index('==> '))
                for hint in ('Hermes, the Hermes gateway service and Claude Code stay.', '  hermes gateway uninstall\n',
                             '  hermes uninstall\n', CLAUDE_UNINSTALL):
                    self.assertIn(hint, output)
                other_calls = self.other_calls_made()
                if platform == 'macos':
                    uid = subprocess.run([BASH, '-c', 'id -u'], capture_output=True, text=True, check=True).stdout.strip()
                    self.assertEqual(other_calls, [f'launchctl bootout gui/{uid}/{LAUNCH_AGENT_LABEL}'])
                else:
                    self.assertEqual(other_calls, [])

    def test_the_uninstallers_skip_what_is_missing_so_they_run_twice(self):
        for platform in ('windows', 'macos'):
            with self.subTest(platform):
                kept = self.keep(platform)
                self.install(platform)
                for calls in ([PROFILE_DELETE], []):
                    result = self.run_uninstall(platform)
                    self.assert_removed(result, kept)
                    self.assertEqual(self.calls_made(), calls)
                    self.assertEqual(result.stderr, '')
                self.other_calls_made()

    def test_the_uninstallers_remove_the_profile_and_the_rest_without_hermes(self):
        self.without_hermes()
        for platform in ('windows', 'macos'):
            with self.subTest(platform):
                kept = self.keep(platform)
                if platform == 'macos':
                    self.assertNotEqual(subprocess.run([BASH, '-c', 'command -v hermes'], env=self.environment(platform),
                                                       capture_output=True).returncode, 0)
                self.install(platform)
                self.assert_removed(self.run_uninstall(platform), kept)
                self.assert_removed(self.run_uninstall(platform, everything=True), kept)
                self.assertEqual(self.calls_made(), [])
                self.other_calls_made()

    def test_the_uninstallers_remove_what_is_left_of_a_profile_hermes_deleted_and_its_mark(self):
        for platform in ('windows', 'macos'):
            with self.subTest(platform):
                kept = self.keep(platform)
                (self.profile(platform) / 'workspace').mkdir(parents=True)
                (self.profile(platform) / 'workspace' / 'left.txt').write_text('left\n')
                self.mark(platform).write_text('deleted\n')
                self.assert_removed(self.run_uninstall(platform), kept)
                self.assertEqual(self.calls_made(), [])
                self.other_calls_made()

    def test_the_uninstallers_with_everything_also_uninstall_the_gateway_service_and_hermes_but_not_claude_code(self):
        cases = (('macos', {}, [PROFILE_DELETE, GATEWAY_UNINSTALL, HERMES_UNINSTALL]),
                 ('windows', {}, [PROFILE_DELETE, GATEWAY_STATUS_DEFAULT, GATEWAY_UNINSTALL, HERMES_UNINSTALL]),
                 ('windows', {'FAKE_GATEWAY_STATUS': TASK_REGISTERED},
                  [PROFILE_DELETE, GATEWAY_STATUS_DEFAULT, [*GATEWAY_UNINSTALL, '--as-administrator'], HERMES_UNINSTALL]))
        for platform, environment, calls in cases:
            with self.subTest(platform, **environment):
                kept = self.keep(platform)
                self.install(platform)
                replacements = {ELEVATED_UNINSTALL: AS_ADMINISTRATOR} if platform == 'windows' else None
                result = self.run_uninstall(platform, everything=True, environment=environment, replacements=replacements)
                self.assert_removed(result, kept)
                self.assertEqual(self.calls_made(), calls)
                self.assertNotIn('claude', ' '.join(self.other_calls_made()))
                self.assertLess(result.stdout.index('It also removes the Hermes gateway service and Hermes.'),
                                result.stdout.index('==> '))
                self.assertIn(CLAUDE_UNINSTALL, result.stdout)
                self.assertNotIn('or run this line again', result.stdout)

    def test_the_powershell_uninstaller_stops_before_hermes_when_the_uac_prompt_for_the_gateway_is_declined(self):
        kept = self.keep('windows')
        self.install('windows')
        result = self.run_uninstall('windows', everything=True, environment={'FAKE_GATEWAY_STATUS': TASK_REGISTERED},
                                    replacements={ELEVATED_UNINSTALL: 'Deny-Elevation'}, prelude=DECLINED)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        errors = re.sub(r'\s+', ' ', result.stderr)
        self.assertIn("apt-scraper is removed. The gateway's Scheduled Task stays", errors)
        self.assertIn('Run this again with -Everything and approve the prompt.', errors)
        self.assertEqual(self.files(), kept)
        self.assertEqual(self.calls_made(), [PROFILE_DELETE, GATEWAY_STATUS_DEFAULT])

    def test_the_powershell_uninstaller_leaves_the_session_it_runs_in_as_it_was(self):
        self.keep('windows')
        self.install('windows')
        result = self.run_uninstall('windows', environment={'HERMES_NONINTERACTIVE': 'kept'}, prelude=(
            "$ErrorActionPreference = 'Continue'; $text | Invoke-Expression | Out-Null; "
            '"session: $ErrorActionPreference $env:HERMES_NONINTERACTIVE '
            '$([bool](Get-Command Write-Step -ErrorAction SilentlyContinue)) '
            '$([bool](Get-Variable ProfileHome -ErrorAction SilentlyContinue))"; return; '))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('session: Continue kept False False', result.stdout)
        self.assertEqual(self.calls_made(), [PROFILE_DELETE])

    def waiting(self, *command: str) -> subprocess.Popen:
        process = subprocess.Popen(list(command))
        self.addCleanup(lambda: (process.kill(), process.wait()))
        return process

    def test_the_powershell_uninstaller_stops_the_dashboard_and_the_setup_browser_and_no_other_process(self):
        self.keep('windows')
        self.install('windows')
        profile = self.profile('windows')
        launcher = self.places('windows')['hermes'] / 'bin' / 'hermes.py'
        elsewhere = self.folder / 'elsewhere' / 'hermes.py'
        for path in (launcher, elsewhere):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(WAITING)
        python = sys.executable
        stopped = [self.waiting(python, '-c', WAITING, f'--user-data-dir={profile}\\apt-scraper\\browser'),
                   self.waiting(python, '-c', WAITING, '//nologo', f'{profile}\\dashboard.vbs'),
                   self.waiting(python, str(launcher), '-p', 'apt-scraper', 'dashboard', '--no-open')]
        running = [self.waiting(python, '-c', WAITING, f'--user-data-dir={profile}-other\\browser'),
                   self.waiting(python, str(elsewhere), '-p', 'apt-scraper', 'dashboard'),
                   self.waiting(python, str(launcher), '-p', 'apt-scraper-other', 'dashboard')]
        result = self.run_uninstall('windows')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for process in stopped:
            self.assertIsNotNone(process.poll(), process.args)
        for process in running:
            self.assertIsNone(process.poll(), process.args)
        self.assertFalse(profile.exists())

    def test_the_shell_uninstaller_stops_the_dashboard_and_the_setup_browser_and_no_other_process(self):
        self.keep('macos')
        self.install('macos')
        profile = self.shell_profile()
        brave = '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'
        listed = [
            (True, f'{brave} --user-data-dir={profile}/apt-scraper/browser'),
            (True, '/opt/homebrew/bin/python3 /Users/someone/.local/bin/hermes -p apt-scraper dashboard --no-open --port 9119'),
            (True, '/Users/someone/.local/bin/hermes --profile apt-scraper dashboard'),
            (False, f'{brave} --user-data-dir={profile}-other/browser'),
            (False, '/Users/someone/.local/bin/hermes -p apt-scraper-other dashboard'),
            (False, '/opt/homebrew/bin/python3 /Users/someone/.local/bin/hermes gateway run'),
        ]
        processes, lines = [], []
        for number, (stops, command) in enumerate(listed):
            pid_file = self.folder / f'pid-{number}'
            process = subprocess.Popen([BASH, '-c', f'echo $$ > "{pid_file.as_posix()}"; exec sleep 120'])
            self.assertTrue(wait_for(lambda: pid_file.exists() and pid_file.read_text().strip()))
            pid = pid_file.read_text().strip()
            self.addCleanup(lambda pid=pid, process=process: (
                subprocess.run([BASH, '-c', f'kill {pid} 2>/dev/null'], check=False), process.wait(10)))
            processes.append((pid, process, stops))
            lines.append(f'{pid:>6} {command}')
        self.processes.write_text('\n'.join(lines) + '\n', newline='\n')
        result = self.run_uninstall('macos')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for pid, process, stops in processes:
            if stops:
                self.assertIsNotNone(process.wait(15), pid)
            else:
                self.assertIsNone(process.poll(), pid)

    # The uninstallers kept in step with what the bootstraps, the installers and the gallery create.

    def test_every_location_the_installers_write_outside_the_profile_is_removed_or_left_on_purpose(self):
        found = {(name, line.strip()) for name in ('install.ps1', 'install.sh', 'bootstrap.ps1', 'bootstrap.sh')
                 for line in (ROOT / name).read_text(encoding='utf-8').splitlines() if OUTSIDE.search(line)}
        self.assertEqual(found, set(OUTSIDE_THE_PROFILE))
        for (name, _), removal in OUTSIDE_THE_PROFILE.items():
            if removal is not None:
                uninstaller = 'uninstall.ps1' if name.endswith('.ps1') else 'uninstall.sh'
                self.assertIn(removal, (ROOT / uninstaller).read_text(encoding='utf-8'), name)

    def store_paths(self, platform: str) -> list[Path]:
        """Where the gallery's store keeps its files: in the profile under Hermes, in the data directory without."""
        scripts = str(ROOT / 'profile' / 'skills' / 'gallery' / 'scripts')
        sys.path.insert(0, scripts)
        self.addCleanup(sys.path.remove, scripts)
        import store
        user = self.places(platform)['user']
        paths = []
        for hermes_home in (str(self.profile(platform)), None):
            names = ('HERMES_HOME', 'LOCALAPPDATA', 'USERPROFILE', 'HOME')
            saved, saved_platform = {name: os.environ.get(name) for name in names}, sys.platform
            try:
                for name in names:
                    os.environ.pop(name, None)
                if hermes_home:
                    os.environ['HERMES_HOME'] = hermes_home
                os.environ['LOCALAPPDATA'] = str(self.machine / 'LocalAppData')
                os.environ['USERPROFILE'] = os.environ['HOME'] = str(user)
                sys.platform = 'win32' if platform == 'windows' else 'darwin'
                paths += [store.store_path(), store.store_path(sample=True)]
            finally:
                sys.platform = saved_platform
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        return paths

    def install_through_the_installer(self, platform: str) -> subprocess.CompletedProcess:
        """Runs the bootstrap with a fake git, then the installer's own locations, its profile step, its dashboard
        step and its login step against the fake hermes, and writes the gallery's stores where the store puts them."""
        environment = self.environment(platform)
        template = self.folder / 'template'
        source = self.folder / 'checkout' / 'apt-scraper'
        for path, text in ((template / '.git' / 'HEAD', 'ref\n'), (template / 'install.ps1', 'exit 0\r\n'),
                           (template / 'install.sh', 'exit 0\n'), (source / 'config.yaml', DISTRIBUTION_CONFIG),
                           (source / 'distribution.yaml', 'name: apt-scraper\n')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, newline='')
        (self.folder / 'git.py').write_text(FAKE_GIT)
        git = [sys.executable, self.folder / 'git.py', self.folder / 'git-calls', template]
        if platform == 'windows':
            (self.tools / 'git.cmd').write_text('@' + ' '.join(f'"{part}"' for part in git) + ' %*\r\n')
            bootstrap = subprocess.run([shutil.which('powershell'), '-NoProfile', '-Command',
                                        f"Get-Content -Raw -LiteralPath '{ROOT / 'bootstrap.ps1'}' | Invoke-Expression"],
                                       env=environment, capture_output=True, text=True, timeout=60)
            self.assertEqual(bootstrap.returncode, 0, bootstrap.stdout + bootstrap.stderr)
            dashboard = section('install.ps1', "Write-Step 'Hermes web dashboard'", CLOSING_POWERSHELL)
            self.assertEqual(dashboard.count('Start-Process $chatUrl'), 1)
            script = self.folder / 'install.ps1'
            script.write_text(section('install.ps1', "$ErrorActionPreference = 'Stop'", "Write-Step 'Claude Code'")
                              + f"$ProfileSource = '{source}'\n$hermes = '{self.tools / 'hermes.cmd'}'\n"
                              + section('install.ps1', 'Write-Step "Hermes profile', '# The account connections use')
                              + dashboard.replace('Start-Process $chatUrl', ''), encoding='utf-8-sig')
            result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                    env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        else:
            (self.tools / 'git').write_text('#!/bin/sh\nexec ' + ' '.join(f'"{Path(part).as_posix()}"' for part in git)
                                            + ' "$@"\n', newline='\n')
            (self.tools / 'git').chmod(0o755)
            bootstrap = subprocess.run([BASH, '-s'], input=(ROOT / 'bootstrap.sh').read_text(encoding='utf-8'),
                                       env=environment, capture_output=True, text=True, timeout=60)
            self.assertEqual(bootstrap.returncode, 0, bootstrap.stdout + bootstrap.stderr)
            script = self.folder / 'install.sh'
            script.write_text(section('install.sh', 'set -euo pipefail', "step 'Claude Code'")
                              + f"PROFILE_SOURCE='{source.as_posix()}'\n"
                              + section('install.sh', 'step "Hermes profile', '# The account connections use')
                              + section('install.sh', "step 'Hermes web dashboard'", CLOSING_SHELL), newline='\n')
            result = subprocess.run([BASH, script.as_posix()], env=environment, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=120)
        for path in self.store_paths(platform):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('listings\n')
        return result

    def test_the_uninstallers_remove_everything_the_bootstraps_the_installers_and_the_gallery_create(self):
        for platform in ('windows', 'macos'):
            with self.subTest(platform):
                kept = self.keep(platform)
                result = self.install_through_the_installer(platform)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                created = set(self.files()) - set(kept)
                places = self.places(platform)
                for place in ('data', 'login', 'shortcut'):
                    self.assertTrue(places[place].exists(), place)
                self.assertTrue(any(path.startswith(places['temp'].relative_to(self.machine).as_posix()
                                                    + '/apt-scraper-dashboard') for path in created), created)
                self.assertTrue((self.profile(platform) / 'dashboard-address').exists())
                self.calls_made()
                self.other_calls_made()
                self.assert_removed(self.run_uninstall(platform), kept)
                self.assertEqual(self.calls_made(), [PROFILE_DELETE])
                self.other_calls_made()


if __name__ == '__main__':
    unittest.main()
