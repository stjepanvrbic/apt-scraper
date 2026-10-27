"""Checks the accounts apt-scraper works through and records which channels may be used.

Commands:
  connections.py check [CHANNEL ...] [--handle HANDLE]   run the checks with test messages, store and print
  connections.py status                                  print every channel, checking stale ones first
  connections.py require CHANNEL                         exit 0 only if CHANNEL is connected
  connections.py browser on|off|status                   turn the account setup's browser on or off, or
                                                         exit 0 only if no setup uses it

Channels: gmail, calendar, gateway, imessage. A stored result older than a day is stale, and a result
that could not be checked is always stale. status and require check stale results again and never
send a test message or test text. Exit codes: 0 every channel asked about is connected, 3 at least
one is not, 1 the script itself failed, 2 usage error. Use a channel only on exit code 0.

The account setup works in a window of the user's own browser (Brave, Chrome, Edge or Chromium) that runs on a
profile directory of apt-scraper's own, so it is signed out until the user signs in there once, and the user's own
browser profile is never touched. `browser on` records the time, starts that browser with a debugging port on
127.0.0.1 unless it runs already, and points Hermes's browser tools at it with `browser.cdp_url`, allowing them
private addresses (see set_setup_browser). `browser off` clears both settings and closes that browser; its profile
directory stays, and the sign-ins with it. Hermes reads the settings at every new browser session, from the
profile's config.yaml, which the scheduled jobs share, so they are set only while a setup runs. `status`, and the
listing correspondence job before every run, turn them off once the record is older than SETUP_BROWSER_IDLE, so a
setup that stopped without `browser off` cannot leave them on.
That job does not start a run while a setup uses the browser, `browser on` exits 4 while a run of it goes, and
its contact forms check `browser status` first. `browser on` exits 5 when no supported browser is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from functools import cache
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

NOT_CONNECTED = 3

CONNECTED = "connected"
FAILED = "not connected"
UNCHECKED = "not checked"
UNAVAILABLE = "unavailable"

LABELS = {
    "gmail": "Gmail",
    "calendar": "Google Calendar",
    "gateway": "Messaging gateway",
    "imessage": "iMessage",
}
CHANNELS = tuple(LABELS)

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READ_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
)
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

GATEWAY_TEST_MESSAGE = (
    "apt-scraper connection check: questions about listings and viewings will arrive in this chat."
)
IMESSAGE_TEST_TEXT = "apt-scraper connection check: texts to listing agents will go out from this Mac."

STALE_AFTER = timedelta(days=1)
COMMAND_TIMEOUT_SECONDS = 30
IMESSAGE_SEND_TIMEOUT_SECONDS = 90
KILL_WAIT_SECONDS = 5
# Hermes stops a terminal command after 180 seconds by default (terminal.timeout). The budget plus
# the bounded kill after a timeout stays inside that, so every channel gets a stored result.
CHECK_BUDGET_SECONDS = 150
SETUP_BROWSER_SETTING = "browser.cdp_url"
# Ticket 0052's setup turned on real-profile browsing instead, and one left on across an upgrade would give the
# contact forms a copy of the user's own browser profile, so every turn-off turns these off too.
REAL_PROFILE_SETTINGS = ("browser.use_real_profile", "browser.headed", "browser.real_profile_autoclose")
SETUP_BROWSER_IDLE = timedelta(minutes=30)
SETUP_LOCK_WAIT_SECONDS = 60
# A listing correspondence run that never reached the job's next gate, such as one the gateway's exit cut off,
# stops holding the browser back after this long.
CONTACT_RUN_LIMIT = timedelta(hours=1)
NO_BROWSER = 5
# Hermes's keys for the browsers it finds (hermes_cli/browser_connect.py). A default browser outside them, such as
# Firefox or a Beta channel, falls back to the first of FALLBACK_BROWSERS that is installed.
BROWSER_NAMES = {"brave": "Brave", "brave-origin": "Brave Origin", "chrome": "Google Chrome",
                 "edge": "Microsoft Edge", "chromium": "Chromium"}
FALLBACK_BROWSERS = ("brave", "chrome", "edge", "chromium")
FIRST_DEBUG_PORT = 9333
BROWSER_START_SECONDS = 20
BROWSER_CLOSE_SECONDS = 10
OUT_OF_TIME = "The check ran out of time before reaching it. Check this connection on its own."


class OutOfTime(Exception):
    pass


class CommandTimedOut(Exception):
    def __init__(self, name: str, seconds: int) -> None:
        super().__init__(f"{name} did not answer within {seconds} seconds.")
        self.seconds = seconds


class Runner:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + CHECK_BUDGET_SECONDS

    def ensure_time(self) -> None:
        if self.deadline - time.monotonic() <= 0:
            raise OutOfTime

    def run(self, command: list[str], name: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        allowed = min(timeout or COMMAND_TIMEOUT_SECONDS, self.deadline - time.monotonic())
        if allowed <= 0:
            raise OutOfTime
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
        try:
            stdout, stderr = process.communicate(timeout=allowed)
        except subprocess.TimeoutExpired:
            kill_tree(process)
            raise CommandTimedOut(name, max(1, round(allowed))) from None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def kill_tree(process: subprocess.Popen) -> None:
    """Launchers such as hermes.exe run the real program as a child that keeps the output pipes
    open, so the whole tree is killed. If the pipes still do not close, they are abandoned so the
    check returns in bounded time."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
                           timeout=KILL_WAIT_SECONDS, check=True)
        except (subprocess.SubprocessError, OSError):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def on_macos() -> bool:
    return sys.platform == "darwin"


def hermes_home() -> Path:
    return Path(os.environ["HERMES_HOME"])


def gateway_running() -> bool:
    """The liveness `hermes profile list` shows, including a profile served by another profile's
    gateway. Hermes has no machine-readable gateway status command."""
    from hermes_cli.profiles import list_profiles

    home = hermes_home().resolve()
    return any(Path(profile.path).resolve() == home and profile.gateway_running
               for profile in list_profiles(lazy_skill_count=True))


@cache
def imessage_context() -> str:
    """macOS grants Full Disk Access and Automation to the program that started imsg. Hermes's launchd
    gateway runs under /usr/bin/osascript (hermes_cli/gateway_launchd.py), so a command started from a
    gateway session has osascript among its ancestors and depends on osascript's grants."""
    listing = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True,
                             timeout=COMMAND_TIMEOUT_SECONDS, check=True)
    parents, names = {}, {}
    for line in listing.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            parents[int(fields[0])], names[int(fields[0])] = int(fields[1]), fields[2]
    pid = os.getpid()
    while pid in parents and pid > 1:
        if Path(names[pid].strip()).name == "osascript":
            return "gateway"
        pid = parents[pid]
    return "terminal"


def record_key(channel: str) -> str:
    if channel == "imessage" and on_macos():
        return f"imessage-{imessage_context()}"
    return channel


def label(channel: str) -> str:
    if channel == "imessage" and on_macos():
        return f"iMessage from the {imessage_context()}"
    return LABELS[channel]


def status_path() -> Path:
    return hermes_home() / "apt-scraper" / "connections.json"


def load_status() -> dict:
    try:
        status = json.loads(status_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return status if isinstance(status, dict) else {}


def save_record(key: str, record: dict) -> None:
    """Merges into the file as it is now and replaces it in one step, because a terminal session and
    a gateway session can check connections at the same time."""
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    status = load_status()
    status[key] = record
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".connections-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump(status, file, indent=2)
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                os.unlink(temporary)
                raise
            time.sleep(0.05)


def result(state: str, message: str = "", **details) -> dict:
    return {"state": state, "message": message, "checked_at": datetime.now(timezone.utc).isoformat(), **details}


def last_line(process: subprocess.CompletedProcess) -> str:
    lines = (process.stderr.strip() or process.stdout.strip() or f"exit code {process.returncode}").splitlines()
    return lines[-1].strip()


def granted_google_scopes() -> set[str] | None:
    """Scopes recorded in the google-workspace token; None when the token does not list them,
    which the google-workspace connector treats as every scope it requested. Raises ValueError
    for a token that is not a JSON object."""
    payload = json.loads((hermes_home() / "google_token.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("not a JSON object")
    raw = payload.get("scopes") or payload.get("scope")
    if not raw:
        return None
    return set(raw.split() if isinstance(raw, str) else raw)


def check_google(runner: Runner, api_arguments: list[str], service: str, has_permission,
                 permission_message: str) -> dict:
    api = hermes_home() / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py"
    if not api.exists():
        return result(FAILED, "The Hermes google-workspace skill is missing from this profile.")
    if not (hermes_home() / "google_token.json").exists():
        return result(FAILED, "No Google account is connected.")
    try:
        scopes = granted_google_scopes()
    except (OSError, ValueError) as error:
        return result(FAILED, f"The stored Google token is corrupt. Connect Google again. ({error})")
    if scopes is not None and not has_permission(scopes):
        return result(FAILED, permission_message)
    process = runner.run([sys.executable, str(api), *api_arguments], service)
    if process.returncode != 0:
        return result(FAILED, f"{service} did not answer: {last_line(process)}")
    return result(CONNECTED)


def check_gmail(runner: Runner) -> dict:
    return check_google(
        runner, ["gmail", "labels"], "Gmail",
        lambda scopes: GMAIL_SEND_SCOPE in scopes and any(scope in scopes for scope in GMAIL_READ_SCOPES),
        "The Google connection lacks permission to send mail or read replies.")


def check_calendar(runner: Runner) -> dict:
    return check_google(
        runner, ["calendar", "list", "--max", "1"], "Google Calendar",
        lambda scopes: CALENDAR_SCOPE in scopes,
        "The Google connection lacks permission to manage the calendar.")


def deliver_test_message(runner: Runner, hermes: str, platforms: list[str]) -> tuple[str | None, list[str]]:
    failures = []
    for platform in platforms:
        delivery = runner.run([hermes, "send", "--to", platform, "--json", GATEWAY_TEST_MESSAGE], "hermes send")
        if delivery.returncode == 0:
            return platform, failures
        try:
            reason = json.loads(delivery.stdout).get("error") or last_line(delivery)
        except (json.JSONDecodeError, AttributeError):
            reason = last_line(delivery)
        failures.append(f"The test message through {platform} failed: {reason.rstrip('.')}.")
    return None, failures


def check_gateway(runner: Runner, previous: dict, send_test: bool) -> dict:
    """The record's platform is where a test message last went through; a check without test
    messages relies on it."""
    hermes = shutil.which("hermes")
    if hermes is None:
        return result(FAILED, "The hermes command is not on PATH.")
    listing = runner.run([hermes, "send", "--list", "--json"], "hermes send --list")
    if listing.returncode != 0:
        return result(FAILED, f"Listing messaging platforms failed: {last_line(listing)}")
    try:
        platforms = sorted(json.loads(listing.stdout)["platforms"] or {})
    except (json.JSONDecodeError, KeyError, TypeError):
        return result(FAILED, f"Listing messaging platforms returned unreadable output: {last_line(listing)}")
    if not platforms:
        return result(FAILED, "No messaging platform is set up.")
    known = previous.get("platform")
    if send_test:
        ordered = sorted(platforms, key=lambda name: name != known)
        platform, failures = deliver_test_message(runner, hermes, ordered)
        if platform is None:
            return result(FAILED, " ".join(failures))
    elif known in platforms:
        platform = known
    else:
        return result(FAILED, "No test message has gone through yet. Check the gateway to send one.")
    if not gateway_running():
        return result(FAILED, f"The test message went out through {platform}, but the gateway is not running, "
                              f"so replies cannot reach the agent.", platform=platform)
    return result(CONNECTED, f"through {platform}", platform=platform)


def check_imessage(runner: Runner, previous: dict, send_test: bool, handle: str | None) -> dict:
    """The record's texted flag says a test text to its handle went through from this context; a
    check without test texts relies on it."""
    imsg = shutil.which("imsg")
    if imsg is None:
        return result(FAILED, "The imsg command is not installed.", handle=handle)
    chats = runner.run([imsg, "chats", "--limit", "1", "--json"], "imsg chats")
    if chats.returncode != 0:
        return result(FAILED, f"Messages history is unreadable: {last_line(chats)}", handle=handle)
    if not handle:
        return result(FAILED, "The test text needs your own phone number or Apple ID.")
    if not send_test:
        if previous.get("texted") and previous.get("handle") == handle:
            return result(CONNECTED, handle=handle, texted=True)
        return result(FAILED, f"No test text has been sent from the {imessage_context()} yet. "
                              f"Check iMessage to send one.", handle=handle)
    try:
        send = runner.run([imsg, "send", "--to", handle, "--text", IMESSAGE_TEST_TEXT], "imsg send",
                          IMESSAGE_SEND_TIMEOUT_SECONDS)
    except CommandTimedOut as timeout:
        return result(UNCHECKED, f"The test text did not finish within {timeout.seconds} seconds. "
                                 f"If macOS asked whether to allow control of Messages, press OK and check again.",
                      handle=handle)
    if send.returncode != 0:
        return result(FAILED, f"The test text failed: {last_line(send)}", handle=handle)
    return result(CONNECTED, handle=handle, texted=True)


def imessage_unavailable() -> dict:
    return result(UNAVAILABLE, "iMessage exists only on macOS, so texting is off on this computer.")


def known_handle(status: dict) -> str | None:
    return next((record["handle"] for key, record in status.items()
                 if key.startswith("imessage") and isinstance(record, dict) and record.get("handle")), None)


def check_channel(runner: Runner, channel: str, status: dict, send_test: bool, handle: str | None) -> dict:
    if channel == "imessage" and not on_macos():
        return imessage_unavailable()
    previous = status.get(record_key(channel)) or {}
    kept = {key: previous[key] for key in ("platform", "handle", "texted") if previous.get(key)}
    try:
        runner.ensure_time()
        if channel == "gmail":
            return check_gmail(runner)
        if channel == "calendar":
            return check_calendar(runner)
        if channel == "gateway":
            return check_gateway(runner, previous, send_test)
        return check_imessage(runner, previous, send_test,
                              handle or previous.get("handle") or known_handle(status))
    except OutOfTime:
        return result(UNCHECKED, OUT_OF_TIME, **kept)
    except CommandTimedOut as timeout:
        return result(UNCHECKED, str(timeout), **kept)


def stale(record: dict | None) -> bool:
    if record is None or record.get("state") == UNCHECKED:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(record["checked_at"]) > STALE_AFTER


def current_records(channels: list[str]) -> dict:
    """Stored results, with missing and stale ones checked again without test messages or texts."""
    status = load_status()
    runner = Runner()
    records = {}
    for channel in channels:
        key = record_key(channel)
        if stale(status.get(key)):
            status[key] = check_channel(runner, channel, status, False, None)
            save_record(key, status[key])
        records[channel] = status[key]
    return records


def state_text(record: dict) -> str:
    if not record["message"]:
        return record["state"]
    separator = " " if record["state"] == CONNECTED else ". "
    return f"{record['state']}{separator}{record['message']}"


def describe(channel: str, record: dict) -> str:
    return f"{label(channel)}: {state_text(record)}"


def usable(channel: str, record: dict) -> bool:
    return record["state"] == CONNECTED or (channel == "imessage" and record["state"] == UNAVAILABLE)


def setup_browser_path() -> Path:
    return hermes_home() / "apt-scraper" / "setup-browser"


def browser_profile_dir() -> Path:
    return hermes_home() / "apt-scraper" / "browser"


def browser_instance_path() -> Path:
    return hermes_home() / "apt-scraper" / "browser-instance.json"


class NoBrowser(Exception):
    pass


def psutil_module():
    """Hermes depends on psutil, so its Python has it."""
    try:
        import psutil
    except ImportError as error:
        raise RuntimeError(f"psutil is missing ({error}). Run the apt-scraper installer again.") from None
    return psutil


def find_browser() -> tuple[str, str]:
    """The default browser's key and binary, found the way Hermes finds them for `/browser connect`."""
    try:
        from hermes_cli.browser_connect import chromium_executable, detect_default_chromium
    except ImportError as error:
        raise RuntimeError(f"Hermes's browser detection is missing ({error}). Run the apt-scraper installer again.")
    for key in (detect_default_chromium(), *FALLBACK_BROWSERS):
        binary = chromium_executable(key) if key in BROWSER_NAMES else None
        if binary:
            return key, binary
    raise NoBrowser("No supported browser is installed. Install Brave, Google Chrome or Microsoft Edge.")


def browser_arguments(binary: str, port: int) -> list[str]:
    """A plain start of the real browser, with no automation or headless switch, which Google's sign-in refuses.
    Chrome 136 and later ignore a debugging port on the default profile directory, so a directory of its own is
    needed as well as wanted."""
    return [binary, f"--remote-debugging-port={port}", f"--user-data-dir={browser_profile_dir()}",
            "--no-first-run", "--no-default-browser-check"]


def spawn(arguments: list[str]) -> subprocess.Popen:
    """Detached, so the browser outlives this script and the terminal command that ran it."""
    if os.name == "nt":
        options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        options = {"start_new_session": True}
    return subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **options)


def endpoint(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def debugger_version(port: int) -> dict | None:
    """What the browser's debugging port says about it, or None when nothing there speaks the protocol."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{endpoint(port)}/json/version", timeout=1) as response:
            version = json.loads(response.read())
    except (OSError, ValueError):
        return None
    return version if isinstance(version, dict) and "webSocketDebuggerUrl" in version else None


def debugger_answers(port: int) -> bool:
    return debugger_version(port) is not None


def free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return probe.getsockname()[1]
    raise RuntimeError("No port on 127.0.0.1 is free for the browser.")


def load_instance() -> dict:
    try:
        instance = json.loads(browser_instance_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return instance if isinstance(instance, dict) else {}


def holds_browser_profile(process) -> bool:
    """Whether the process is the main process of a browser on apt-scraper's profile directory, which the user's
    own browser never is."""
    flag = f"--user-data-dir={browser_profile_dir()}"
    arguments = process.cmdline()
    if os.name == "nt":
        flag, arguments = flag.lower(), [argument.lower() for argument in arguments]
    return flag in arguments and not any(argument.startswith("--type=") for argument in arguments)


def recorded_process(instance: dict):
    """The recorded process, while its ID still belongs to the browser `browser on` started."""
    psutil = psutil_module()

    try:
        process = psutil.Process(int(instance["pid"]))
        if abs(process.create_time() - float(instance["created"])) < 1 and holds_browser_profile(process):
            return process
    except (KeyError, TypeError, ValueError, psutil.Error):
        pass
    return None


def browser_processes() -> list:
    """The recorded browser and any other one on apt-scraper's profile directory, such as one whose record was lost."""
    psutil = psutil_module()

    found = {}
    process = recorded_process(load_instance())
    if process is not None:
        found[process.pid] = process
    for process in psutil.process_iter():
        try:
            if process.pid not in found and holds_browser_profile(process):
                found[process.pid] = process
        except psutil.Error:
            continue
    return list(found.values())


def ask_to_close(process) -> None:
    """Asks the browser to quit through its own debugging port, as quitting from its menu does, so it saves the
    sign-ins first. Windows has no signal for that, and the window's close message leaves Brave running."""
    port = next((argument.split("=", 1)[1] for argument in process.cmdline()
                 if argument.startswith("--remote-debugging-port=")), None)
    version = debugger_version(int(port)) if port and port.isdigit() else None
    if version is not None:
        try:
            from websockets.sync.client import connect

            with connect(version["webSocketDebuggerUrl"], open_timeout=KILL_WAIT_SECONDS, proxy=None) as browser:
                browser.send(json.dumps({"id": 1, "method": "Browser.close"}))
                browser.recv(timeout=KILL_WAIT_SECONDS)
            return
        except Exception:
            pass
    if os.name != "nt":
        process.terminate()


def close_browser() -> None:
    """Closes the browser so that it saves the sign-ins, and ends it if it still runs after BROWSER_CLOSE_SECONDS."""
    psutil = psutil_module()

    processes = browser_processes()
    for process in processes:
        try:
            ask_to_close(process)
        except (OSError, psutil.Error):
            pass
    alive = psutil.wait_procs(processes, timeout=BROWSER_CLOSE_SECONDS)[1]
    for process in alive:
        try:
            for child in process.children(recursive=True):
                child.kill()
            process.kill()
        except psutil.Error:
            pass
    alive = psutil.wait_procs(alive, timeout=KILL_WAIT_SECONDS)[1]
    if alive:
        raise RuntimeError(f"The account setup's browser, process {alive[0].pid}, did not close.")
    browser_instance_path().unlink(missing_ok=True)


def start_browser() -> dict:
    """The browser `browser on` started, while it runs and answers, or a new one. A new one gets the last port if
    it is free, so the setting rarely changes."""
    psutil = psutil_module()

    instance = load_instance()
    if recorded_process(instance) is not None and debugger_answers(int(instance["port"])):
        return {**instance, "opened": False}
    close_browser()
    key, binary = find_browser()
    port = free_port(int(instance.get("port") or FIRST_DEBUG_PORT))
    browser_profile_dir().mkdir(parents=True, exist_ok=True)
    process = spawn(browser_arguments(binary, port))
    instance = {"pid": process.pid, "created": psutil.Process(process.pid).create_time(), "port": port,
                "browser": key, "binary": binary}
    browser_instance_path().write_text(json.dumps(instance), encoding="utf-8")
    deadline = time.monotonic() + BROWSER_START_SECONDS
    while not debugger_answers(port):
        if process.poll() is not None or time.monotonic() > deadline:
            exited = process.poll() is not None
            close_browser()
            raise RuntimeError(f"{BROWSER_NAMES[key]} {'exited before it opened' if exited else 'did not open'} "
                               f"its debugging port {port}.")
        time.sleep(0.2)
    return {**instance, "opened": True}


def set_setup_browser(url: str) -> None:
    """Points the browser tools at `url`, or with "" back at their own browser. Hermes takes a browser at a debugging
    address for one on another computer and refuses it private addresses then, such as the dashboard on 127.0.0.1
    and the end of Google's authorization at localhost, so they are allowed while the setup's browser, which runs on
    this computer, is set, and refused again once it is cleared. Clearing also turns off REAL_PROFILE_SETTINGS."""
    hermes = shutil.which("hermes")
    if hermes is None:
        raise RuntimeError("The hermes command is not on PATH.")
    allowed = ("browser.allow_private_urls", "true" if url else "false")
    settings = [allowed, (SETUP_BROWSER_SETTING, url)]
    if not url:
        settings = [*reversed(settings), *((key, "false") for key in REAL_PROFILE_SETTINGS)]
    runner = Runner()
    for key, value in settings:
        process = runner.run([hermes, "-p", "apt-scraper", "config", "set", key, value], "hermes config set")
        if process.returncode != 0:
            raise RuntimeError(f"Setting {key} failed: {last_line(process)}")


def contact_run_path() -> Path:
    return hermes_home() / "apt-scraper" / "contact-run"


class BrowserBusy(Exception):
    pass


@contextmanager
def setup_lock():
    """Held around every change of the setup record, the settings and the contact run record, because `hermes
    config set` rewrites config.yaml without a lock of its own. The operating system releases the lock of a
    process that dies, and waiting for it ends after SETUP_LOCK_WAIT_SECONDS."""
    path = hermes_home() / "apt-scraper" / "setup-browser.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        deadline = time.monotonic() + SETUP_LOCK_WAIT_SECONDS
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Another change of the account setup's browser did not finish in time.") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_age(path: Path) -> timedelta | None:
    try:
        return timedelta(seconds=time.time() - path.stat().st_mtime)
    except FileNotFoundError:
        return None


def setup_browser_on() -> dict:
    """The record comes first, so the setting is never on without one. Refused while a listing
    correspondence run goes, because its next contact form would get the same browser."""
    with setup_lock():
        age = record_age(contact_run_path())
        if age is not None and age <= CONTACT_RUN_LIMIT:
            raise BrowserBusy("A listing correspondence run is filling in contact forms. Try again in a minute.")
        path = setup_browser_path()
        path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        try:
            instance = start_browser()
        except NoBrowser:
            turn_off(None)
            raise
        set_setup_browser(endpoint(instance["port"]))
        return instance


def turn_off(seen: int | None) -> None:
    """Under the lock. The browser closes after the setting is cleared, so no new browser session reaches it, and the
    record goes last, so a failure leaves it for the next try. A record rewritten since it was seen at modification
    time `seen` belongs to a setup that turned the browser on again, which keeps it."""
    set_setup_browser("")
    try:
        current = setup_browser_path().stat().st_mtime_ns
    except FileNotFoundError:
        current = None
    if seen is not None and current not in (None, seen):
        set_setup_browser(endpoint(start_browser()["port"]))
        return
    close_browser()
    setup_browser_path().unlink(missing_ok=True)


def setup_browser_off() -> None:
    with setup_lock():
        turn_off(None)


def running_under_lock() -> bool:
    try:
        seen = setup_browser_path().stat().st_mtime_ns
    except FileNotFoundError:
        return False
    if time.time() - seen / 1e9 <= SETUP_BROWSER_IDLE.total_seconds():
        return True
    turn_off(seen)
    return setup_browser_path().exists()


def setup_browser_running() -> bool:
    """Whether a setup uses the browser now. A record older than SETUP_BROWSER_IDLE is a setup that stopped
    without `browser off`: its settings are turned off here. Raises when that fails."""
    if not setup_browser_path().exists():
        return False
    with setup_lock():
        return running_under_lock()


def contact_job_gate(due: bool) -> bool:
    """The listing correspondence job's wake decision. Hermes runs a job's pre-run script only while no run of
    that job goes, so the previous run's record is cleared first. The job wakes only with work and while no
    setup uses the browser, and the run it starts is recorded, so `browser on` waits for it to end."""
    with setup_lock():
        contact_run_path().unlink(missing_ok=True)
        if running_under_lock() or not due:
            return False
        contact_run_path().write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        return True


def command_check(channels: list[str], handle: str | None) -> int:
    runner = Runner()
    records = {}
    for channel in channels:
        key = record_key(channel)
        records[channel] = check_channel(runner, channel, load_status(), True, handle)
        save_record(key, records[channel])
        print(describe(channel, records[channel]))
    return 0 if all(usable(channel, record) for channel, record in records.items()) else NOT_CONNECTED


def command_status() -> int:
    try:
        setup_browser_running()
    except (RuntimeError, OSError, CommandTimedOut, OutOfTime) as error:
        print(f"The account setup's browser settings could not be turned off: {error}", file=sys.stderr)
    records = current_records(list(CHANNELS))
    for channel, record in records.items():
        print(describe(channel, record))
    return 0 if all(usable(channel, record) for channel, record in records.items()) else NOT_CONNECTED


def command_require(channel: str) -> int:
    record = current_records([channel])[channel]
    if record["state"] == CONNECTED:
        return 0
    name = "the messaging gateway" if channel == "gateway" else label(channel)
    print(f"Do not use {name}: {state_text(record)}")
    return NOT_CONNECTED


def command_browser(state: str) -> int:
    try:
        if state == "status":
            running = setup_browser_running()
            print("The account setup uses its browser." if running else "No account setup uses the browser.")
            return NOT_CONNECTED if running else 0
        if state == "on":
            instance = setup_browser_on()
            print(f"The account setup's browser is on: {BROWSER_NAMES[instance['browser']]} at "
                  f"{endpoint(instance['port'])}, {'in a new window' if instance['opened'] else 'already open'}.")
        else:
            setup_browser_off()
            print("The account setup's browser is off and closed.")
    except BrowserBusy as busy:
        print(busy)
        return 4
    except NoBrowser as missing:
        print(missing)
        return NO_BROWSER
    except (RuntimeError, OSError, CommandTimedOut, OutOfTime) as error:
        print(f"Turning the account setup's browser {state} failed: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check apt-scraper's account connections.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="run the checks with test messages and store the results")
    check.add_argument("channels", nargs="*", metavar="CHANNEL", help=", ".join(CHANNELS) + "; all by default")
    check.add_argument("--handle", help="your own phone number or Apple ID, for the iMessage test text")
    commands.add_parser("status", help="print every channel, checking stale results without test messages")
    require = commands.add_parser("require", help="exit 0 only if the channel is connected")
    require.add_argument("channel", choices=CHANNELS)
    browser = commands.add_parser("browser", help="turn the account setup's browser on or off, or check it")
    browser.add_argument("state", choices=("on", "off", "status"))
    arguments = parser.parse_args(argv)
    if arguments.command == "check":
        unknown = [channel for channel in arguments.channels if channel not in CHANNELS]
        if unknown:
            check.error(f"unknown channel {', '.join(unknown)}; choose from {', '.join(CHANNELS)}")
        return command_check(arguments.channels or list(CHANNELS), arguments.handle)
    if arguments.command == "status":
        return command_status()
    if arguments.command == "browser":
        return command_browser(arguments.state)
    return command_require(arguments.channel)


if __name__ == "__main__":
    sys.exit(main())
