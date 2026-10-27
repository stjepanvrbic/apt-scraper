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

The account setup works in a visible window of the user's own browser, on a copy of its profile with their
logins. Hermes reads the settings for that at every new browser session, from the profile's config.yaml, which
the scheduled jobs share, so they are on only while a setup runs. `browser on` records the time and turns them
on, `browser off` turns them off. `status`, and the listing correspondence job before every run, turn them off
once the record is older than SETUP_BROWSER_IDLE, so a setup that stopped without `browser off` cannot leave
them on. That job does not start a run while a setup uses the browser, `browser on` exits 4 while a run of it
goes, and its contact forms check `browser status` first.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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
SETUP_BROWSER_SETTINGS = ("browser.use_real_profile", "browser.headed", "browser.real_profile_autoclose")
SETUP_BROWSER_IDLE = timedelta(minutes=30)
SETUP_LOCK_WAIT_SECONDS = 60
# A listing correspondence run that never reached the job's next gate, such as one the gateway's exit cut off,
# stops holding the browser back after this long.
CONTACT_RUN_LIMIT = timedelta(hours=1)
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


def set_setup_browser(value: str) -> None:
    hermes = shutil.which("hermes")
    if hermes is None:
        raise RuntimeError("The hermes command is not on PATH.")
    runner = Runner()
    for key in SETUP_BROWSER_SETTINGS:
        process = runner.run([hermes, "-p", "apt-scraper", "config", "set", key, value], "hermes config set")
        if process.returncode != 0:
            raise RuntimeError(f"Setting {key} to {value} failed: {last_line(process)}")


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


def setup_browser_on() -> None:
    """The record comes first, so the settings are never on without one. Refused while a listing
    correspondence run goes, because its next contact form would get the same browser."""
    with setup_lock():
        age = record_age(contact_run_path())
        if age is not None and age <= CONTACT_RUN_LIMIT:
            raise BrowserBusy("A listing correspondence run is filling in contact forms. Try again in a minute.")
        path = setup_browser_path()
        path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        set_setup_browser("true")


def turn_off(seen: int | None) -> None:
    """Under the lock. The record goes last, so a failure leaves it for the next try. A record rewritten since it
    was seen at modification time `seen` belongs to a setup that turned the browser on again, which keeps it."""
    set_setup_browser("false")
    try:
        current = setup_browser_path().stat().st_mtime_ns
    except FileNotFoundError:
        return
    if seen is not None and current != seen:
        set_setup_browser("true")
        return
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
            print("The account setup uses the user's browser." if running else "No account setup uses the browser.")
            return NOT_CONNECTED if running else 0
        if state == "on":
            setup_browser_on()
        else:
            setup_browser_off()
    except BrowserBusy as busy:
        print(busy)
        return 4
    except (RuntimeError, OSError, CommandTimedOut, OutOfTime) as error:
        print(f"Turning the account setup's browser {state} failed: {error}", file=sys.stderr)
        return 1
    print(f"The account setup's browser is {state}.")
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
