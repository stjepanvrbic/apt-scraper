import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Self, cast, override
from unittest import mock

import psutil

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "account-connections" / "scripts"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))

import connections  # noqa: E402

FIND_BROWSER = connections.find_browser

ALL_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

GOOGLE_API_OK = """
import json, sys
print(json.dumps([]))
"""

GOOGLE_API_HANGS = """
import time
time.sleep(30)
"""

FAKE_HERMES = """
import json, os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["hermes", *sys.argv[1:]]) + "\\n")
arguments = sys.argv[1:]
if arguments[2:4] == ["config", "set"]:
    if os.environ.get("FAKE_CONFIG_FAILS"):
        print("Error: config.yaml is not writable", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if arguments[:2] == ["send", "--list"]:
    if os.environ.get("FAKE_LIST_OUTPUT"):
        print(os.environ["FAKE_LIST_OUTPUT"])
        sys.exit(0)
    print(json.dumps({"platforms": json.loads(os.environ.get("FAKE_PLATFORMS") or "{}")}))
    sys.exit(0)
platform = arguments[arguments.index("--to") + 1]
error = json.loads(os.environ.get("FAKE_SEND_ERRORS") or "{}").get(platform)
if error:
    print(json.dumps({"error": error}))
    sys.exit(1)
print(json.dumps({"success": True}))
"""

FAKE_BROWSER = """
import http.server, json, os, socketserver, sys
arguments = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["browser", *arguments]) + "\\n")
port = int(next(argument.split("=", 1)[1] for argument in arguments if argument.startswith("--remote-debugging-port=")))
profile = next(argument.split("=", 1)[1] for argument in arguments if argument.startswith("--user-data-dir="))
os.makedirs(profile, exist_ok=True)
open(os.path.join(profile, "Cookies"), "a").close()


class Version(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"Browser": "Chrome/141.0.0.0",
                           "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/browser/fake"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


class Server(http.server.HTTPServer):
    def server_bind(self):
        # Not HTTPServer's, whose reverse lookup of the address can take a minute on macOS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]


Server(("127.0.0.1", port), Version).serve_forever()
"""

FAKE_IMSG = """
import json, os, sys, time
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["imsg", *sys.argv[1:]]) + "\\n")
command = sys.argv[1]
if command == "send" and os.environ.get("FAKE_IMSG_SEND_HANGS"):
    time.sleep(30)
error = os.environ.get("FAKE_IMSG_CHATS_ERROR" if command == "chats" else "FAKE_IMSG_SEND_ERROR")
if error:
    print(error, file=sys.stderr)
    sys.exit(1)
if command == "chats":
    print(json.dumps({"id": 1}))
"""


class ConnectionsTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.root: Path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home: Path = self.root / "home"
        self.home.mkdir()
        self.bin: Path = self.root / "bin"
        self.bin.mkdir()
        self.log: Path = self.root / "calls.jsonl"
        self.log.touch()
        self.environment: dict[str, str] = {
            "HERMES_HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_LOG": str(self.log),
        }
        for name in ("FAKE_PLATFORMS", "FAKE_SEND_ERRORS", "FAKE_IMSG_CHATS_ERROR", "FAKE_IMSG_SEND_ERROR",
                     "FAKE_IMSG_SEND_HANGS", "FAKE_LIST_OUTPUT", "FAKE_CONFIG_FAILS"):
            self.environment[name] = ""
        self.write_tool("hermes", FAKE_HERMES)
        self.install_google_skill(GOOGLE_API_OK)
        # The real browser is never started: `browser on` starts the fake browser, run by Python, in its place.
        browser = self.bin / "browser_fake.py"
        _ = browser.write_text(textwrap.dedent(FAKE_BROWSER), encoding="utf-8")
        spawn = connections.spawn

        def spawn_with_python(arguments: list[str]) -> subprocess.Popen[bytes]:
            return spawn([sys.executable, *arguments])

        for patcher in (mock.patch.object(connections, "find_browser", return_value=("brave", str(browser))),
                        mock.patch.object(connections, "spawn", spawn_with_python),
                        mock.patch.object(connections, "BROWSER_CLOSE_SECONDS", 1),
                        mock.patch.object(connections, "FIRST_DEBUG_PORT", 0)):
            _ = patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.end_fake_browsers)
        self.macos: bool = False
        self.gateway_running: bool = True
        self.imessage_context: str = "terminal"
        self.constants: dict[str, int] = {}

    def write_tool(self, name: str, source: str) -> None:
        script = self.bin / f"{name}_fake.py"
        _ = script.write_text(textwrap.dedent(source), encoding="utf-8")
        if os.name == "nt":
            _ = (self.bin / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.bin / name
            _ = launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    def install_google_skill(self, google_api: str) -> None:
        scripts = self.home / "skills" / "productivity" / "google-workspace" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        _ = (scripts / "google_api.py").write_text(textwrap.dedent(google_api), encoding="utf-8")

    def connect_google(self, scopes: list[str] | None = ALL_GOOGLE_SCOPES, google_api: str = GOOGLE_API_OK) -> None:
        token: dict[str, str | list[str]] = {"token": "t"} if scopes is None else {"token": "t", "scopes": scopes}
        _ = (self.home / "google_token.json").write_text(json.dumps(token), encoding="utf-8")
        self.install_google_skill(google_api)

    def run_main(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, self.environment))
            _ = stack.enter_context(mock.patch.object(connections, "on_macos", return_value=self.macos))
            _ = stack.enter_context(mock.patch.object(connections, "gateway_running", return_value=self.gateway_running))
            _ = stack.enter_context(mock.patch.object(connections, "imessage_context", return_value=self.imessage_context))
            for name, value in self.constants.items():
                _ = stack.enter_context(mock.patch.object(connections, name, value))
            _ = stack.enter_context(contextlib.redirect_stdout(output))
            code = connections.main(list(arguments))
        return code, output.getvalue()

    def status_file(self) -> Path:
        return self.home / "apt-scraper" / "connections.json"

    def stored(self) -> dict[str, connections.ConnectionRecord]:
        return cast(dict[str, connections.ConnectionRecord], json.loads(self.status_file().read_text(encoding="utf-8")))

    def store(self, records: dict[str, connections.ConnectionRecord]) -> None:
        self.status_file().parent.mkdir(parents=True, exist_ok=True)
        _ = self.status_file().write_text(json.dumps(records), encoding="utf-8")

    def calls(self, tool: str) -> list[list[str]]:
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [call[1:] for call in (cast(list[str], json.loads(line)) for line in lines) if call[0] == tool]

    def sends(self) -> list[list[str]]:
        return [call for call in self.calls("hermes") if "--to" in call]

    @staticmethod
    def days_ago(days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def test_unconnected_profile_reports_every_connection_and_fails(self) -> None:
        code, output = self.run_main("check")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. No Google account is connected.", output)
        self.assertIn("Google Calendar: not connected. No Google account is connected.", output)
        self.assertIn("Messaging gateway: not connected. No messaging platform is set up.", output)
        self.assertIn("iMessage: unavailable. iMessage exists only on macOS", output)
        states = {channel: record["state"] for channel, record in self.stored().items()}
        self.assertEqual(states, {
            "gmail": "not connected", "calendar": "not connected",
            "gateway": "not connected", "imessage": "unavailable",
        })

    def test_connected_google_account_passes_gmail_and_calendar(self) -> None:
        self.connect_google()

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, 0)
        self.assertIn("Gmail: connected", output)
        self.assertIn("Google Calendar: connected", output)

    def test_google_token_without_scopes_field_counts_as_every_scope(self) -> None:
        self.connect_google(scopes=None)

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, 0)
        self.assertIn("Gmail: connected", output)
        self.assertIn("Google Calendar: connected", output)

    def test_corrupt_google_token_fails_gmail_and_calendar_with_reason(self) -> None:
        self.connect_google()
        _ = (self.home / "google_token.json").write_text("{not json", encoding="utf-8")

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. The stored Google token is corrupt.", output)
        self.assertIn("Google Calendar: not connected. The stored Google token is corrupt.", output)
        self.assertEqual(self.stored()["gmail"]["state"], "not connected")

    def test_google_token_without_send_permission_fails_gmail_only(self) -> None:
        self.connect_google(scopes=[s for s in ALL_GOOGLE_SCOPES if not s.endswith("gmail.send")])

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. The Google connection lacks permission to send mail", output)
        self.assertIn("Google Calendar: connected", output)

    def test_google_api_error_is_reported(self) -> None:
        self.connect_google(google_api="""
            import sys
            print("HttpError 403: Access Not Configured. Gmail API has not been used", file=sys.stderr)
            sys.exit(1)
        """)

        code, output = self.run_main("check", "gmail")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. Gmail did not answer: HttpError 403: Access Not Configured", output)

    def test_command_that_times_out_fails_its_channel_with_reason(self) -> None:
        self.connect_google(google_api=GOOGLE_API_HANGS)
        self.constants["COMMAND_TIMEOUT_SECONDS"] = 2

        code, output = self.run_main("check", "gmail")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not checked. Gmail did not answer within 2 seconds.", output)
        self.assertEqual(self.stored()["gmail"]["state"], "not checked")

    def test_check_that_runs_out_of_time_stores_a_failure_for_every_remaining_channel(self) -> None:
        self.connect_google(google_api=GOOGLE_API_HANGS)
        self.constants["CHECK_BUDGET_SECONDS"] = 2

        code, output = self.run_main("check")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not checked. Gmail did not answer within 2 seconds.", output)
        self.assertIn("Google Calendar: not checked. The check ran out of time before reaching it.", output)
        self.assertIn("Messaging gateway: not checked. The check ran out of time before reaching it.", output)
        self.assertEqual(self.stored()["gateway"]["state"], "not checked")

    def test_gateway_passes_when_the_test_message_is_delivered_and_the_gateway_runs(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, 0)
        self.assertIn("Messaging gateway: connected through telegram", output)
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.sends()[0][:4], ["send", "--to", "telegram", "--json"])
        self.assertEqual(self.stored()["gateway"].get("platform"), "telegram")

    def test_gateway_fails_when_the_test_message_is_not_delivered(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.environment["FAKE_SEND_ERRORS"] = json.dumps({"telegram": "No home channel set for telegram"})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Messaging gateway: not connected. The test message through telegram failed: No home channel set for telegram", output)

    def test_gateway_falls_back_to_a_platform_that_delivers(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"discord": [], "telegram": []})
        self.environment["FAKE_SEND_ERRORS"] = json.dumps({"discord": "No home channel set for discord"})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, 0)
        self.assertIn("Messaging gateway: connected through telegram", output)
        self.assertEqual(self.stored()["gateway"].get("platform"), "telegram")

    def test_gateway_tries_the_platform_that_worked_before_first(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"discord": [], "telegram": []})
        self.store({"gateway": {"state": "not connected", "message": "", "checked_at": self.days_ago(0),
                                "platform": "telegram"}})

        _ = self.run_main("check", "gateway")

        self.assertEqual([send[2] for send in self.sends()], ["telegram"])

    def test_gateway_reports_every_platform_when_none_delivers(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"discord": [], "telegram": []})
        self.environment["FAKE_SEND_ERRORS"] = json.dumps({"discord": "Unauthorized", "telegram": "chat missing"})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("The test message through discord failed: Unauthorized.", output)
        self.assertIn("The test message through telegram failed: chat missing", output)

    def test_gateway_fails_when_it_is_not_running_to_receive_replies(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.gateway_running = False

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Messaging gateway: not connected. The test message went out through telegram, but the gateway is not running, so replies cannot reach the agent.", output)
        self.assertEqual(self.stored()["gateway"].get("platform"), "telegram")

    def test_imessage_on_macos_passes_after_a_test_text_to_the_users_own_handle(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, 0)
        self.assertIn("iMessage from the terminal: connected", output)
        self.assertEqual(self.calls("imsg")[0], ["chats", "--limit", "1", "--json"])
        self.assertEqual(self.calls("imsg")[1][:3], ["send", "--to", "+15551234567"])

    def test_imessage_handle_is_remembered_for_later_checks(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        _ = self.run_main("check", "imessage", "--handle", "+15551234567")

        code, _ = self.run_main("check", "imessage")

        self.assertEqual(code, 0)
        self.assertEqual(self.calls("imsg")[3][:3], ["send", "--to", "+15551234567"])

    def test_imessage_without_handle_asks_for_one(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)

        code, output = self.run_main("check", "imessage")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. The test text needs your own phone number or Apple ID", output)

    def test_imessage_without_imsg_installed_fails(self) -> None:
        self.macos = True

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. The imsg command is not installed.", output)

    def test_imessage_without_full_disk_access_fails(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_CHATS_ERROR"] = "unable to open database file"

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. Messages history is unreadable: unable to open database file", output)
        self.assertEqual(len(self.calls("imsg")), 1)

    def test_imessage_send_refused_fails(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_SEND_ERROR"] = "not authorized to send Apple events"

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. The test text failed: not authorized to send Apple events", output)

    def test_imessage_send_that_hangs_points_at_the_automation_dialog(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_SEND_HANGS"] = "1"
        self.constants["IMESSAGE_SEND_TIMEOUT_SECONDS"] = 2
        started = time.monotonic()

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not checked. The test text did not finish within 2 seconds. If macOS asked whether to allow control of Messages, press OK and check again.", output)
        self.assertEqual(self.stored()["imessage-terminal"].get("handle"), "+15551234567")

    def test_checking_one_connection_keeps_the_others(self) -> None:
        _ = self.run_main("check")
        self.connect_google()

        _ = self.run_main("check", "gmail")

        self.assertEqual(self.stored()["gmail"]["state"], "connected")
        self.assertEqual(self.stored()["calendar"]["state"], "not connected")

    def test_require_allows_a_connected_channel(self) -> None:
        self.connect_google()
        _ = self.run_main("check", "gmail")

        code, output = self.run_main("require", "gmail")

        self.assertEqual((code, output), (0, ""))

    def test_require_refuses_a_failed_channel_with_its_reason(self) -> None:
        _ = self.run_main("check")

        code, output = self.run_main("require", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use the messaging gateway: not connected. No messaging platform is set up.", output)

    def test_require_checks_a_channel_that_was_never_checked(self) -> None:
        code, output = self.run_main("require", "calendar")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use Google Calendar: not connected. No Google account is connected.", output)
        self.assertEqual(self.stored()["calendar"]["state"], "not connected")

    def test_require_rechecks_a_stale_result(self) -> None:
        self.store({"gmail": {"state": "connected", "message": "", "checked_at": self.days_ago(2)}})

        code, output = self.run_main("require", "gmail")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use Gmail: not connected. No Google account is connected.", output)

    def test_require_refuses_imessage_off_macos(self) -> None:
        _ = self.run_main("check")

        code, output = self.run_main("require", "imessage")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use iMessage: unavailable.", output)

    def test_status_reports_fresh_results_without_checking_again(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.connect_google()
        _ = self.run_main("check")
        calls_before = len(self.calls("hermes"))

        code, output = self.run_main("status")

        self.assertEqual(code, 0)
        self.assertIn("Gmail: connected", output)
        self.assertIn("iMessage: unavailable", output)
        self.assertEqual(len(self.calls("hermes")), calls_before)

    def test_status_checks_channels_that_were_never_checked(self) -> None:
        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. No Google account is connected.", output)
        self.assertIn("Messaging gateway: not connected. No messaging platform is set up.", output)

    def test_status_rechecks_a_stale_gateway_without_a_test_message(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.connect_google()
        self.store({channel: {"state": "connected", "message": "", "checked_at": self.days_ago(2)}
                    for channel in ("gmail", "calendar")})
        self.store({**self.stored(), "gateway": {"state": "connected", "message": "through telegram",
                                                 "checked_at": self.days_ago(2), "platform": "telegram"}})
        self.gateway_running = False

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual(self.sends(), [])
        self.assertIn("the gateway is not running", output)
        self.assertGreater(self.stored()["gmail"]["checked_at"], self.days_ago(1))

    def test_status_rechecks_a_stale_imessage_without_a_test_text(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.store({"imessage-terminal": {"state": "connected", "message": "", "checked_at": self.days_ago(2),
                                          "handle": "+15551234567", "texted": True}})

        _ = self.run_main("status")

        self.assertEqual([call[0] for call in self.calls("imsg")], ["chats"])
        self.assertEqual(self.stored()["imessage-terminal"]["state"], "connected")

    def test_status_sends_nothing_for_a_stale_failed_gateway_record(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.store({"gateway": {"state": "not connected", "message": "No home channel set for telegram.",
                                "checked_at": self.days_ago(2)}})

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual(self.sends(), [])
        self.assertIn("Messaging gateway: not connected. No test message has gone through yet. Check the gateway to send one.", output)

    def test_status_sends_nothing_for_a_gateway_that_was_never_checked(self) -> None:
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual(self.sends(), [])
        self.assertIn("No test message has gone through yet.", output)

    def test_require_sends_no_text_for_imessage_that_was_never_checked(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.store({"imessage-gateway": {"state": "connected", "message": "", "checked_at": self.days_ago(0),
                                         "handle": "+15551234567", "texted": True}})

        code, output = self.run_main("require", "imessage")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual([call[0] for call in self.calls("imsg")], ["chats"])
        self.assertIn("Do not use iMessage from the terminal: not connected. No test text has been sent from the terminal yet. Check iMessage to send one.", output)

    def test_status_sends_no_text_for_a_stale_failed_imessage_record(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.store({"imessage-terminal": {"state": "not connected", "message": "The test text failed: x",
                                          "checked_at": self.days_ago(2), "handle": "+15551234567"}})

        _ = self.run_main("status")

        self.assertEqual([call[0] for call in self.calls("imsg")], ["chats"])

    def test_imessage_results_are_kept_per_context(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        _ = self.run_main("check", "imessage", "--handle", "+15551234567")
        self.imessage_context = "gateway"

        refused, _ = self.run_main("require", "imessage")
        checked, output = self.run_main("check", "imessage")

        self.assertEqual(refused, connections.NOT_CONNECTED)
        self.assertEqual(checked, 0)
        self.assertIn("iMessage from the gateway: connected", output)
        self.assertEqual(self.calls("imsg")[-1][:3], ["send", "--to", "+15551234567"])
        self.assertEqual(self.stored()["imessage-terminal"]["state"], "connected")

    def test_result_that_could_not_be_checked_is_checked_again_at_once(self) -> None:
        self.connect_google()
        self.store({"gmail": {"state": "not checked", "message": "Gmail did not answer within 30 seconds.",
                              "checked_at": self.days_ago(0)}})

        code, _ = self.run_main("require", "gmail")

        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["gmail"]["state"], "connected")

    def test_corrupt_status_file_is_replaced(self) -> None:
        self.status_file().parent.mkdir(parents=True)
        _ = self.status_file().write_text("{not json", encoding="utf-8")

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. No Google account is connected.", output)
        self.assertEqual(self.stored()["gmail"]["state"], "not connected")
        self.assertEqual(sorted(path.name for path in self.status_file().parent.iterdir()), ["connections.json"])

    def test_unreadable_platform_listing_fails_the_gateway(self) -> None:
        self.environment["FAKE_LIST_OUTPUT"] = "Traceback: something broke"

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Messaging gateway: not connected. Listing messaging platforms returned unreadable output: Traceback: something broke", output)

    def test_imessage_send_cut_short_by_the_budget_reports_the_real_wait(self) -> None:
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_SEND_HANGS"] = "1"
        self.constants["CHECK_BUDGET_SECONDS"] = 3

        _, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertRegex(output, r"The test text did not finish within [23] seconds\.")

    # The account setup's browser runs, and `browser.cdp_url` points at it, only between `browser on` and
    # `browser off`, or for SETUP_BROWSER_IDLE after the last `browser on`. A fake browser stands in for the real
    # binary: it answers the debugging port's /json/version until it is ended.

    def setup_record(self) -> Path:
        return self.home / "apt-scraper" / "setup-browser"

    def setup_settings(self, url: str) -> list[list[str]]:
        """The `config set` calls that point the browser tools at url, or with "" back at their own browser."""
        allowed = ["-p", "apt-scraper", "config", "set", "browser.allow_private_urls", "true" if url else "false"]
        address = ["-p", "apt-scraper", "config", "set", "browser.cdp_url", url]
        if url:
            return [allowed, address]
        return [address, allowed, *(["-p", "apt-scraper", "config", "set", key, "false"]
                                    for key in connections.REAL_PROFILE_SETTINGS)]

    def config_calls(self) -> list[list[str]]:
        return [call for call in self.calls("hermes") if call[2:4] == ["config", "set"]]

    def launches(self) -> list[list[str]]:
        return self.calls("browser")

    def instance(self) -> connections.BrowserInstance:
        return cast(connections.BrowserInstance, json.loads((self.home / "apt-scraper" / "browser-instance.json").read_text(encoding="utf-8")))

    def browser_alive(self, pid: int) -> bool:
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def age_setup_record(self, minutes: int) -> None:
        moment = time.time() - minutes * 60
        os.utime(self.setup_record(), (moment, moment))

    def end_fake_browsers(self) -> None:
        for process in psutil.process_iter():
            try:
                if any(str(self.home) in argument for argument in process.cmdline()):
                    process.kill()
            except psutil.Error:
                continue

    def test_browser_on_starts_the_real_browser_on_its_own_profile_and_points_hermes_at_it(self) -> None:
        code, output = self.run_main("browser", "on")
        self.assertEqual(code, 0, output)
        [arguments] = self.launches()
        port = self.instance()["port"]
        self.assertEqual(arguments, [f"--remote-debugging-port={port}",
                                     f"--user-data-dir={self.home / 'apt-scraper' / 'browser'}",
                                     "--no-first-run", "--no-default-browser-check"])
        for flag in ("--enable-automation", "--headless", "--remote-debugging-pipe", "--disable-blink-features",
                     "--remote-debugging-address"):
            with self.subTest(flag):
                self.assertFalse([argument for argument in arguments if argument.startswith(flag)])
        self.assertEqual(self.config_calls(), self.setup_settings(f"http://127.0.0.1:{port}"))
        self.assertTrue(connections.debugger_answers(port))
        self.assertTrue(self.setup_record().exists())
        self.assertIn(f"Brave at http://127.0.0.1:{port}, in a new window", output)

    def test_browser_on_again_reuses_the_open_browser(self) -> None:
        _ = self.run_main("browser", "on")
        code, output = self.run_main("browser", "on")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches()), 1)
        self.assertIn("already open", output)
        self.assertEqual(self.config_calls(), self.setup_settings(f"http://127.0.0.1:{self.instance()['port']}") * 2)

    def test_browser_off_clears_the_setting_and_closes_the_browser_but_keeps_its_profile(self) -> None:
        _ = self.run_main("browser", "on")
        pid = self.instance()["pid"]
        code, output = self.run_main("browser", "off")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.config_calls()[2:], self.setup_settings(""))
        self.assertFalse(self.browser_alive(pid))
        self.assertFalse(self.setup_record().exists())
        self.assertFalse((self.home / "apt-scraper" / "browser-instance.json").exists())
        self.assertTrue((self.home / "apt-scraper" / "browser" / "Cookies").exists())

    def test_browser_on_starts_the_browser_again_on_the_same_port_after_it_was_closed(self) -> None:
        _ = self.run_main("browser", "on")
        first = self.instance()
        psutil.Process(first["pid"]).kill()
        _ = psutil.wait_procs([psutil.Process(first["pid"])], timeout=5)
        code, output = self.run_main("browser", "on")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches()), 2)
        self.assertEqual(self.instance()["port"], first["port"])
        self.assertNotEqual(self.instance()["pid"], first["pid"])

    def test_browser_off_leaves_a_process_that_took_over_the_recorded_id_alone(self) -> None:
        stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(stranger.kill)
        instance = self.home / "apt-scraper" / "browser-instance.json"
        instance.parent.mkdir(parents=True)
        _ = instance.write_text(json.dumps({"pid": stranger.pid, "created": psutil.Process(stranger.pid).create_time(),
                                            "port": 9, "browser": "brave", "binary": "brave"}), encoding="utf-8")
        code, output = self.run_main("browser", "off")
        self.assertEqual(code, 0, output)
        self.assertIsNone(stranger.poll())

    def test_browser_on_without_a_supported_browser_says_what_to_install_and_sets_nothing(self) -> None:
        with mock.patch.object(connections, "find_browser",
                               side_effect=connections.NoBrowser("No supported browser is installed. Install Brave, Google Chrome or Microsoft Edge.")):
            code, output = self.run_main("browser", "on")
        self.assertEqual(code, connections.NO_BROWSER)
        self.assertIn("Install Brave, Google Chrome or Microsoft Edge", output)
        self.assertEqual(self.config_calls(), self.setup_settings(""))
        self.assertFalse(self.setup_record().exists())

    def test_the_default_browser_is_found_as_hermes_finds_it_and_others_only_without_it(self) -> None:
        class BrowserConnect(types.ModuleType):
            """hermes_cli.browser_connect as the test stands it in: the functions find_browser imports."""
            chromium_executable: Callable[[str], str | None]
            detect_default_chromium: Callable[[], str | None]

        found = {"brave": "C:/Brave/brave.exe", "chrome": "C:/Chrome/chrome.exe"}
        detection = BrowserConnect("hermes_cli.browser_connect")
        detection.chromium_executable = found.get
        for default, expected in (("chrome", "chrome"), ("brave", "brave"), (None, "brave"),
                                  ("__unsupported_channel__", "brave")):
            detection.detect_default_chromium = lambda default=default: default
            with self.subTest(default), mock.patch.dict(sys.modules, {"hermes_cli.browser_connect": detection}):
                self.assertEqual(FIND_BROWSER(), (expected, found[expected]))
        detection.chromium_executable = lambda key: None
        with mock.patch.dict(sys.modules, {"hermes_cli.browser_connect": detection}):
            self.assertRaises(connections.NoBrowser, FIND_BROWSER)

    def test_the_browser_is_asked_to_quit_through_its_debugging_port_so_it_saves_the_sign_ins(self) -> None:
        sent: list[str | dict[str, object]] = []

        class Socket:
            def __enter__(self) -> Self:
                return self

            def __exit__(self, *details: object) -> bool:
                return False

            def send(self, message: str) -> None:
                sent.append(cast(dict[str, object], json.loads(message)))

            def recv(self, **_options: object) -> str:
                return "{}"

        def connect(url: str, **_options: object) -> Socket:
            sent.append(url)
            return Socket()

        class Client(types.ModuleType):
            """websockets.sync.client as the test stands it in: the connect function ask_to_close imports."""
            connect: Callable[[str], Socket]

        class Browser(psutil.Process):
            """This process standing in for a browser on a debugging port, which must not be ended by signal."""

            def __init__(self) -> None:
                super().__init__()
                self.ended: list[str] = []

            @override
            def cmdline(self) -> list[str]:
                return ["brave", "--remote-debugging-port=9333"]

            @override
            def terminate(self) -> None:
                self.ended.append("terminate")

            @override
            def kill(self) -> None:
                self.ended.append("kill")

        client = Client("websockets.sync.client")
        client.connect = connect
        process = Browser()
        version: connections.DebuggerVersion = {"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/x"}
        with mock.patch.dict(sys.modules, {"websockets": types.ModuleType("websockets"),
                                           "websockets.sync": types.ModuleType("websockets.sync"),
                                           "websockets.sync.client": client}), \
                mock.patch.object(connections, "debugger_version", return_value=version):
            connections.ask_to_close(process)
        self.assertEqual(sent, [version["webSocketDebuggerUrl"], {"id": 1, "method": "Browser.close"}])
        self.assertEqual(process.ended, [])

    def test_status_turns_off_a_setup_that_stopped_without_browser_off(self) -> None:
        _ = self.run_main("browser", "on")
        pid = self.instance()["pid"]
        self.age_setup_record(31)
        _ = self.run_main("status")
        self.assertEqual(self.config_calls()[2:], self.setup_settings(""))
        self.assertFalse(self.setup_record().exists())
        self.assertFalse(self.browser_alive(pid))

    def test_a_setup_of_the_real_profile_version_left_on_across_an_upgrade_is_turned_off_with_its_settings(self) -> None:
        record = self.setup_record()
        record.parent.mkdir(parents=True)
        _ = record.write_text("on", encoding="utf-8")
        self.age_setup_record(31)
        _ = self.run_main("status")
        self.assertEqual(self.config_calls(), self.setup_settings(""))
        self.assertEqual(connections.REAL_PROFILE_SETTINGS,
                         ("browser.use_real_profile", "browser.headed", "browser.real_profile_autoclose"))
        self.assertFalse(record.exists())

    def test_status_leaves_a_running_setup_and_a_profile_without_one_alone(self) -> None:
        _ = self.run_main("status")
        _ = self.run_main("browser", "on")
        self.age_setup_record(29)
        _ = self.run_main("status")
        self.assertEqual(len(self.config_calls()), 2)
        self.assertTrue(self.setup_record().exists())
        self.assertTrue(self.browser_alive(self.instance()["pid"]))

    def test_a_failed_browser_off_keeps_the_record_so_the_next_status_tries_again(self) -> None:
        _ = self.run_main("browser", "on")
        pid = self.instance()["pid"]
        self.environment["FAKE_CONFIG_FAILS"] = "1"
        code, _ = self.run_main("browser", "off")
        self.assertEqual(code, 1)
        self.assertTrue(self.setup_record().exists())
        self.age_setup_record(31)
        code, output = self.run_main("status")
        self.assertIn("Gmail", output)
        self.assertTrue(self.setup_record().exists())
        self.environment["FAKE_CONFIG_FAILS"] = ""
        _ = self.run_main("status")
        self.assertFalse(self.setup_record().exists())
        self.assertFalse(self.browser_alive(pid))

    def setting_value(self) -> str:
        """The last value `config set` gave browser.cdp_url, after checking that private addresses go with it."""
        values = {call[4]: call[5] for call in self.config_calls()}
        self.assertEqual(values["browser.allow_private_urls"], "true" if values["browser.cdp_url"] else "false")
        return values["browser.cdp_url"]

    def contact_run(self, minutes_ago: int) -> None:
        record = self.home / "apt-scraper" / "contact-run"
        record.parent.mkdir(parents=True, exist_ok=True)
        _ = record.write_text("run", encoding="utf-8")
        moment = time.time() - minutes_ago * 60
        os.utime(record, (moment, moment))

    def off_with(self, during_off: Callable[[], None]) -> bool:
        """Turns off a setup idle for 31 minutes and calls during_off once, between clearing the setting and the rest
        of the turn-off."""
        _ = self.run_main("browser", "on")
        self.age_setup_record(31)
        original = connections.set_setup_browser
        calls: list[str] = []

        def set_setup_browser(value: str) -> None:
            if value == "" and not calls:
                calls.append(value)
                during_off()
            original(value)

        with mock.patch.object(connections, "set_setup_browser", set_setup_browser):
            return connections.setup_browser_running()

    def test_browser_on_while_a_stale_setup_is_turned_off_waits_and_leaves_the_browser_on_with_its_record(self) -> None:
        turning_on = threading.Thread(target=connections.setup_browser_on)

        def start_browser_on() -> None:
            turning_on.start()
            turning_on.join(3)

        with mock.patch.dict(os.environ, self.environment):
            _ = self.off_with(start_browser_on)
            turning_on.join(60)
        self.assertTrue(self.setup_record().exists())
        self.assertEqual(self.setting_value(), f"http://127.0.0.1:{self.instance()['port']}")
        self.assertTrue(self.browser_alive(self.instance()["pid"]))

    def test_a_record_rewritten_while_a_stale_setup_is_turned_off_is_kept_and_the_browser_left_on(self) -> None:
        def rewrite_record() -> None:
            _ = self.setup_record().write_text("on again", encoding="utf-8")
            os.utime(self.setup_record())

        with mock.patch.dict(os.environ, self.environment):
            self.assertTrue(self.off_with(rewrite_record))
        self.assertTrue(self.setup_record().exists())
        self.assertEqual(self.setting_value(), f"http://127.0.0.1:{self.instance()['port']}")
        self.assertEqual(len(self.launches()), 1)
        self.assertTrue(self.browser_alive(self.instance()["pid"]))

    def test_browser_on_waits_for_a_listing_correspondence_run_until_it_ends_or_runs_too_long(self) -> None:
        self.contact_run(10)
        code, output = self.run_main("browser", "on")
        self.assertEqual(code, 4)
        self.assertIn("listing correspondence run", output)
        self.assertEqual((self.config_calls(), self.launches(), self.setup_record().exists()), ([], [], False))
        self.contact_run(61)
        code, _ = self.run_main("browser", "on")
        self.assertEqual(code, 0)
        self.assertEqual(self.config_calls(), self.setup_settings(f"http://127.0.0.1:{self.instance()['port']}"))

    def test_the_contact_job_gate_records_the_run_it_wakes_and_clears_it_at_the_next_gate(self) -> None:
        record = self.home / "apt-scraper" / "contact-run"
        with mock.patch.dict(os.environ, self.environment):
            self.assertTrue(connections.contact_job_gate(True))
            self.assertTrue(record.exists())
            self.assertFalse(connections.contact_job_gate(False))
            self.assertFalse(record.exists())
        _ = self.run_main("browser", "on")
        with mock.patch.dict(os.environ, self.environment):
            self.assertFalse(connections.contact_job_gate(True))
        self.assertFalse(record.exists())

    def test_the_contact_job_gate_closes_the_browser_of_a_setup_that_stopped(self) -> None:
        _ = self.run_main("browser", "on")
        pid = self.instance()["pid"]
        self.age_setup_record(31)
        with mock.patch.dict(os.environ, self.environment):
            self.assertTrue(connections.contact_job_gate(True))
        self.assertEqual(self.setting_value(), "")
        self.assertFalse(self.browser_alive(pid))

    def test_browser_status_exits_0_only_while_no_setup_uses_the_browser(self) -> None:
        self.assertEqual(self.run_main("browser", "status")[0], 0)
        _ = self.run_main("browser", "on")
        self.assertEqual(self.run_main("browser", "status")[0], connections.NOT_CONNECTED)
        _ = self.run_main("browser", "off")
        self.assertEqual(self.run_main("browser", "status")[0], 0)


class SkillTest(unittest.TestCase):
    """The account-connections skill drives web pages with Hermes's browser tools, never with computer use."""

    @override
    def setUp(self) -> None:
        self.text: str = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        self.sections: dict[str, str] = {part.split("\n", 1)[0]: part for part in re.split(r"^## ", self.text, flags=re.M)[1:]}

    def test_the_google_and_telegram_steps_use_the_browser_tools_and_open_nothing_in_the_users_own_windows(self) -> None:
        for name in ("Google: Gmail and Calendar", "Messaging gateway"):
            with self.subTest(name):
                section = self.sections[name]
                self.assertIn("browser_navigate", section)
                self.assertNotRegex(section, r"(?i)computer[ _-]use|cmd /c start|\bopen \"|address bar|t\.me/")

    def test_computer_use_is_named_only_for_what_no_browser_can_do(self) -> None:
        lines = [line for line in self.text.splitlines() if re.search(r"(?i)computer[ _-]use", line)]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line[:60]):
                self.assertRegex(line, r"Terminal|terminal program")
        self.assertIn("Never use `computer_use` on a web page", self.sections["The browser"])

    def test_the_users_browser_is_turned_on_before_the_browser_steps_and_off_however_the_setup_ends(self) -> None:
        browser = self.sections["The browser"]
        turn_on = browser.index('connections.py" browser on')
        self.assertLess(turn_on, browser.index('connections.py" browser off'))
        self.assertIn("before your first browser action in every turn of the setup", browser[:turn_on])
        self.assertLess(self.text.index('connections.py" browser on'), self.text.index("browser_navigate`. The consent"))
        ending = browser[browser.index("Turn it off again"):browser.index('connections.py" browser off')]
        for exit_path in ("after the final `check`", "when the user stops", "when a step fails"):
            with self.subTest(exit_path):
                self.assertIn(exit_path, ending)
        self.assertIn("run `browser off`", self.sections["Procedure"])

    def test_the_window_is_the_users_own_browser_on_its_own_profile_signed_in_once(self) -> None:
        browser = self.sections["The browser"]
        self.assertIn("user's own default browser (Brave, Chrome, Edge or Chromium)", browser)
        self.assertIn("signed out of everything the first time", browser)
        self.assertIn("they sign in to Google and Telegram there once", browser)
        self.assertIn("never in the chat", browser)
        self.assertNotRegex(self.text, r"(?i)real_profile|close-profile|browser-profile|copy of|copies the profile")

    def test_without_a_supported_browser_the_setup_says_what_to_install_and_never_uses_the_test_browser(self) -> None:
        browser = self.sections["The browser"]
        missing = browser[browser.index("- 5:"):browser.index("- Any other code")]
        self.assertIn("install Brave, Google Chrome or Microsoft Edge", missing)
        self.assertIn("stop the setup's browser steps", missing)
        self.assertIn("Never do a setup step in the browser tools unless `browser on` exited 0 in this turn", browser)

    def test_nothing_reads_the_botfather_chat_while_it_shows_the_token(self) -> None:
        self.assertIn("take no snapshot, screenshot or `browser_vision` of the window", self.sections["Messaging gateway"])


if __name__ == "__main__":
    _ = unittest.main()
