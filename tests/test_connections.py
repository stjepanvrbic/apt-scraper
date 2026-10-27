import contextlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "profile" / "skills" / "account-connections" / "scripts"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS))

import connections  # noqa: E402

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
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.log.touch()
        self.environment = {
            "HERMES_HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_LOG": str(self.log),
        }
        for name in ("FAKE_PLATFORMS", "FAKE_SEND_ERRORS", "FAKE_IMSG_CHATS_ERROR", "FAKE_IMSG_SEND_ERROR",
                     "FAKE_IMSG_SEND_HANGS", "FAKE_LIST_OUTPUT", "FAKE_CONFIG_FAILS"):
            self.environment[name] = ""
        self.write_tool("hermes", FAKE_HERMES)
        self.install_google_skill(GOOGLE_API_OK)
        self.macos = False
        self.gateway_running = True
        self.imessage_context = "terminal"
        self.constants = {}

    def write_tool(self, name, source):
        script = self.bin / f"{name}_fake.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        if os.name == "nt":
            (self.bin / f"{name}.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = self.bin / name
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    def install_google_skill(self, google_api):
        scripts = self.home / "skills" / "productivity" / "google-workspace" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "google_api.py").write_text(textwrap.dedent(google_api), encoding="utf-8")

    def connect_google(self, scopes=ALL_GOOGLE_SCOPES, google_api=GOOGLE_API_OK):
        token = {"token": "t"} if scopes is None else {"token": "t", "scopes": scopes}
        (self.home / "google_token.json").write_text(json.dumps(token), encoding="utf-8")
        self.install_google_skill(google_api)

    def run_main(self, *arguments):
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, self.environment))
            stack.enter_context(mock.patch.object(connections, "on_macos", return_value=self.macos))
            stack.enter_context(mock.patch.object(connections, "gateway_running", return_value=self.gateway_running))
            stack.enter_context(mock.patch.object(connections, "imessage_context", return_value=self.imessage_context))
            for name, value in self.constants.items():
                stack.enter_context(mock.patch.object(connections, name, value))
            stack.enter_context(contextlib.redirect_stdout(output))
            code = connections.main(list(arguments))
        return code, output.getvalue()

    def status_file(self):
        return self.home / "apt-scraper" / "connections.json"

    def stored(self):
        return json.loads(self.status_file().read_text(encoding="utf-8"))

    def store(self, records):
        self.status_file().parent.mkdir(parents=True, exist_ok=True)
        self.status_file().write_text(json.dumps(records), encoding="utf-8")

    def calls(self, tool):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [call[1:] for call in map(json.loads, lines) if call[0] == tool]

    def sends(self):
        return [call for call in self.calls("hermes") if "--to" in call]

    @staticmethod
    def days_ago(days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def test_unconnected_profile_reports_every_connection_and_fails(self):
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

    def test_connected_google_account_passes_gmail_and_calendar(self):
        self.connect_google()

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, 0)
        self.assertIn("Gmail: connected", output)
        self.assertIn("Google Calendar: connected", output)

    def test_google_token_without_scopes_field_counts_as_every_scope(self):
        self.connect_google(scopes=None)

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, 0)
        self.assertIn("Gmail: connected", output)
        self.assertIn("Google Calendar: connected", output)

    def test_corrupt_google_token_fails_gmail_and_calendar_with_reason(self):
        self.connect_google()
        (self.home / "google_token.json").write_text("{not json", encoding="utf-8")

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. The stored Google token is corrupt.", output)
        self.assertIn("Google Calendar: not connected. The stored Google token is corrupt.", output)
        self.assertEqual(self.stored()["gmail"]["state"], "not connected")

    def test_google_token_without_send_permission_fails_gmail_only(self):
        self.connect_google(scopes=[s for s in ALL_GOOGLE_SCOPES if not s.endswith("gmail.send")])

        code, output = self.run_main("check", "gmail", "calendar")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. The Google connection lacks permission to send mail", output)
        self.assertIn("Google Calendar: connected", output)

    def test_google_api_error_is_reported(self):
        self.connect_google(google_api="""
            import sys
            print("HttpError 403: Access Not Configured. Gmail API has not been used", file=sys.stderr)
            sys.exit(1)
        """)

        code, output = self.run_main("check", "gmail")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. Gmail did not answer: HttpError 403: Access Not Configured", output)

    def test_command_that_times_out_fails_its_channel_with_reason(self):
        self.connect_google(google_api=GOOGLE_API_HANGS)
        self.constants["COMMAND_TIMEOUT_SECONDS"] = 2

        code, output = self.run_main("check", "gmail")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not checked. Gmail did not answer within 2 seconds.", output)
        self.assertEqual(self.stored()["gmail"]["state"], "not checked")

    def test_check_that_runs_out_of_time_stores_a_failure_for_every_remaining_channel(self):
        self.connect_google(google_api=GOOGLE_API_HANGS)
        self.constants["CHECK_BUDGET_SECONDS"] = 2

        code, output = self.run_main("check")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not checked. Gmail did not answer within 2 seconds.", output)
        self.assertIn("Google Calendar: not checked. The check ran out of time before reaching it.", output)
        self.assertIn("Messaging gateway: not checked. The check ran out of time before reaching it.", output)
        self.assertEqual(self.stored()["gateway"]["state"], "not checked")

    def test_gateway_passes_when_the_test_message_is_delivered_and_the_gateway_runs(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, 0)
        self.assertIn("Messaging gateway: connected through telegram", output)
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.sends()[0][:4], ["send", "--to", "telegram", "--json"])
        self.assertEqual(self.stored()["gateway"]["platform"], "telegram")

    def test_gateway_fails_when_the_test_message_is_not_delivered(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.environment["FAKE_SEND_ERRORS"] = json.dumps({"telegram": "No home channel set for telegram"})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn(
            "Messaging gateway: not connected. The test message through telegram failed: "
            "No home channel set for telegram", output)

    def test_gateway_falls_back_to_a_platform_that_delivers(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"discord": [], "telegram": []})
        self.environment["FAKE_SEND_ERRORS"] = json.dumps({"discord": "No home channel set for discord"})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, 0)
        self.assertIn("Messaging gateway: connected through telegram", output)
        self.assertEqual(self.stored()["gateway"]["platform"], "telegram")

    def test_gateway_tries_the_platform_that_worked_before_first(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"discord": [], "telegram": []})
        self.store({"gateway": {"state": "not connected", "message": "", "checked_at": self.days_ago(0),
                                "platform": "telegram"}})

        self.run_main("check", "gateway")

        self.assertEqual([send[2] for send in self.sends()], ["telegram"])

    def test_gateway_reports_every_platform_when_none_delivers(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"discord": [], "telegram": []})
        self.environment["FAKE_SEND_ERRORS"] = json.dumps({"discord": "Unauthorized", "telegram": "chat missing"})

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("The test message through discord failed: Unauthorized.", output)
        self.assertIn("The test message through telegram failed: chat missing", output)

    def test_gateway_fails_when_it_is_not_running_to_receive_replies(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.gateway_running = False

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Messaging gateway: not connected. The test message went out through telegram, but the "
                      "gateway is not running, so replies cannot reach the agent.", output)
        self.assertEqual(self.stored()["gateway"]["platform"], "telegram")

    def test_imessage_on_macos_passes_after_a_test_text_to_the_users_own_handle(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, 0)
        self.assertIn("iMessage from the terminal: connected", output)
        self.assertEqual(self.calls("imsg")[0], ["chats", "--limit", "1", "--json"])
        self.assertEqual(self.calls("imsg")[1][:3], ["send", "--to", "+15551234567"])

    def test_imessage_handle_is_remembered_for_later_checks(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.run_main("check", "imessage", "--handle", "+15551234567")

        code, _ = self.run_main("check", "imessage")

        self.assertEqual(code, 0)
        self.assertEqual(self.calls("imsg")[3][:3], ["send", "--to", "+15551234567"])

    def test_imessage_without_handle_asks_for_one(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)

        code, output = self.run_main("check", "imessage")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. The test text needs your own phone number or Apple ID", output)

    def test_imessage_without_imsg_installed_fails(self):
        self.macos = True

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. The imsg command is not installed.", output)

    def test_imessage_without_full_disk_access_fails(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_CHATS_ERROR"] = "unable to open database file"

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. Messages history is unreadable: unable to open database file", output)
        self.assertEqual(len(self.calls("imsg")), 1)

    def test_imessage_send_refused_fails(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_SEND_ERROR"] = "not authorized to send Apple events"

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not connected. The test text failed: not authorized to send Apple events", output)

    def test_imessage_send_that_hangs_points_at_the_automation_dialog(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_SEND_HANGS"] = "1"
        self.constants["IMESSAGE_SEND_TIMEOUT_SECONDS"] = 2
        started = time.monotonic()

        code, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("iMessage from the terminal: not checked. The test text did not finish within 2 seconds. "
                      "If macOS asked whether to allow control of Messages, press OK and check again.", output)
        self.assertEqual(self.stored()["imessage-terminal"]["handle"], "+15551234567")

    def test_checking_one_connection_keeps_the_others(self):
        self.run_main("check")
        self.connect_google()

        self.run_main("check", "gmail")

        self.assertEqual(self.stored()["gmail"]["state"], "connected")
        self.assertEqual(self.stored()["calendar"]["state"], "not connected")

    def test_require_allows_a_connected_channel(self):
        self.connect_google()
        self.run_main("check", "gmail")

        code, output = self.run_main("require", "gmail")

        self.assertEqual((code, output), (0, ""))

    def test_require_refuses_a_failed_channel_with_its_reason(self):
        self.run_main("check")

        code, output = self.run_main("require", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use the messaging gateway: not connected. No messaging platform is set up.", output)

    def test_require_checks_a_channel_that_was_never_checked(self):
        code, output = self.run_main("require", "calendar")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use Google Calendar: not connected. No Google account is connected.", output)
        self.assertEqual(self.stored()["calendar"]["state"], "not connected")

    def test_require_rechecks_a_stale_result(self):
        self.store({"gmail": {"state": "connected", "message": "", "checked_at": self.days_ago(2)}})

        code, output = self.run_main("require", "gmail")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use Gmail: not connected. No Google account is connected.", output)

    def test_require_refuses_imessage_off_macos(self):
        self.run_main("check")

        code, output = self.run_main("require", "imessage")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Do not use iMessage: unavailable.", output)

    def test_status_reports_fresh_results_without_checking_again(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.connect_google()
        self.run_main("check")
        calls_before = len(self.calls("hermes"))

        code, output = self.run_main("status")

        self.assertEqual(code, 0)
        self.assertIn("Gmail: connected", output)
        self.assertIn("iMessage: unavailable", output)
        self.assertEqual(len(self.calls("hermes")), calls_before)

    def test_status_checks_channels_that_were_never_checked(self):
        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. No Google account is connected.", output)
        self.assertIn("Messaging gateway: not connected. No messaging platform is set up.", output)

    def test_status_rechecks_a_stale_gateway_without_a_test_message(self):
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

    def test_status_rechecks_a_stale_imessage_without_a_test_text(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.store({"imessage-terminal": {"state": "connected", "message": "", "checked_at": self.days_ago(2),
                                          "handle": "+15551234567", "texted": True}})

        self.run_main("status")

        self.assertEqual([call[0] for call in self.calls("imsg")], ["chats"])
        self.assertEqual(self.stored()["imessage-terminal"]["state"], "connected")

    def test_status_sends_nothing_for_a_stale_failed_gateway_record(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})
        self.store({"gateway": {"state": "not connected", "message": "No home channel set for telegram.",
                                "checked_at": self.days_ago(2)}})

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual(self.sends(), [])
        self.assertIn("Messaging gateway: not connected. No test message has gone through yet. "
                      "Check the gateway to send one.", output)

    def test_status_sends_nothing_for_a_gateway_that_was_never_checked(self):
        self.environment["FAKE_PLATFORMS"] = json.dumps({"telegram": []})

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual(self.sends(), [])
        self.assertIn("No test message has gone through yet.", output)

    def test_require_sends_no_text_for_imessage_that_was_never_checked(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.store({"imessage-gateway": {"state": "connected", "message": "", "checked_at": self.days_ago(0),
                                         "handle": "+15551234567", "texted": True}})

        code, output = self.run_main("require", "imessage")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertEqual([call[0] for call in self.calls("imsg")], ["chats"])
        self.assertIn("Do not use iMessage from the terminal: not connected. No test text has been sent from "
                      "the terminal yet. Check iMessage to send one.", output)

    def test_status_sends_no_text_for_a_stale_failed_imessage_record(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.store({"imessage-terminal": {"state": "not connected", "message": "The test text failed: x",
                                          "checked_at": self.days_ago(2), "handle": "+15551234567"}})

        self.run_main("status")

        self.assertEqual([call[0] for call in self.calls("imsg")], ["chats"])

    def test_imessage_results_are_kept_per_context(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.run_main("check", "imessage", "--handle", "+15551234567")
        self.imessage_context = "gateway"

        refused, _ = self.run_main("require", "imessage")
        checked, output = self.run_main("check", "imessage")

        self.assertEqual(refused, connections.NOT_CONNECTED)
        self.assertEqual(checked, 0)
        self.assertIn("iMessage from the gateway: connected", output)
        self.assertEqual(self.calls("imsg")[-1][:3], ["send", "--to", "+15551234567"])
        self.assertEqual(self.stored()["imessage-terminal"]["state"], "connected")

    def test_result_that_could_not_be_checked_is_checked_again_at_once(self):
        self.connect_google()
        self.store({"gmail": {"state": "not checked", "message": "Gmail did not answer within 30 seconds.",
                              "checked_at": self.days_ago(0)}})

        code, _ = self.run_main("require", "gmail")

        self.assertEqual(code, 0)
        self.assertEqual(self.stored()["gmail"]["state"], "connected")

    def test_corrupt_status_file_is_replaced(self):
        self.status_file().parent.mkdir(parents=True)
        self.status_file().write_text("{not json", encoding="utf-8")

        code, output = self.run_main("status")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Gmail: not connected. No Google account is connected.", output)
        self.assertEqual(self.stored()["gmail"]["state"], "not connected")
        self.assertEqual(sorted(path.name for path in self.status_file().parent.iterdir()), ["connections.json"])

    def test_unreadable_platform_listing_fails_the_gateway(self):
        self.environment["FAKE_LIST_OUTPUT"] = "Traceback: something broke"

        code, output = self.run_main("check", "gateway")

        self.assertEqual(code, connections.NOT_CONNECTED)
        self.assertIn("Messaging gateway: not connected. Listing messaging platforms returned unreadable output: "
                      "Traceback: something broke", output)

    def test_imessage_send_cut_short_by_the_budget_reports_the_real_wait(self):
        self.macos = True
        self.write_tool("imsg", FAKE_IMSG)
        self.environment["FAKE_IMSG_SEND_HANGS"] = "1"
        self.constants["CHECK_BUDGET_SECONDS"] = 3

        _, output = self.run_main("check", "imessage", "--handle", "+15551234567")

        self.assertRegex(output, r"The test text did not finish within [23] seconds\.")

    # The account setup's browser settings are on only between `browser on` and `browser off`, or for
    # SETUP_BROWSER_IDLE after the last `browser on`.

    def setup_record(self):
        return self.home / "apt-scraper" / "setup-browser"

    def setup_settings(self, value):
        return [["-p", "apt-scraper", "config", "set", key, value] for key in connections.SETUP_BROWSER_SETTINGS]

    def config_calls(self):
        return [call for call in self.calls("hermes") if call[2:4] == ["config", "set"]]

    def age_setup_record(self, minutes):
        moment = time.time() - minutes * 60
        os.utime(self.setup_record(), (moment, moment))

    def test_browser_on_records_the_setup_and_turns_on_the_real_profile_in_a_visible_window(self):
        code, output = self.run_main("browser", "on")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.config_calls(), self.setup_settings("true"))
        self.assertTrue(self.setup_record().exists())
        self.assertEqual(connections.SETUP_BROWSER_SETTINGS,
                         ("browser.use_real_profile", "browser.headed", "browser.real_profile_autoclose"))

    def test_browser_off_turns_the_settings_off_and_removes_the_record(self):
        self.run_main("browser", "on")
        code, output = self.run_main("browser", "off")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.config_calls(), self.setup_settings("true") + self.setup_settings("false"))
        self.assertFalse(self.setup_record().exists())

    def test_status_turns_off_a_setup_that_stopped_without_browser_off(self):
        self.run_main("browser", "on")
        self.age_setup_record(31)
        self.run_main("status")
        self.assertEqual(self.config_calls(), self.setup_settings("true") + self.setup_settings("false"))
        self.assertFalse(self.setup_record().exists())

    def test_status_leaves_a_running_setup_and_a_profile_without_one_alone(self):
        self.run_main("status")
        self.run_main("browser", "on")
        self.age_setup_record(29)
        self.run_main("status")
        self.assertEqual(self.config_calls(), self.setup_settings("true"))
        self.assertTrue(self.setup_record().exists())

    def test_a_failed_browser_off_keeps_the_record_so_the_next_status_tries_again(self):
        self.run_main("browser", "on")
        self.environment["FAKE_CONFIG_FAILS"] = "1"
        code, _ = self.run_main("browser", "off")
        self.assertEqual(code, 1)
        self.assertTrue(self.setup_record().exists())
        self.age_setup_record(31)
        code, output = self.run_main("status")
        self.assertIn("Gmail", output)
        self.assertTrue(self.setup_record().exists())
        self.environment["FAKE_CONFIG_FAILS"] = ""
        self.run_main("status")
        self.assertFalse(self.setup_record().exists())

    def setting_values(self):
        """The last value `config set` gave each setup setting."""
        values = {}
        for call in self.config_calls():
            values[call[4]] = call[5]
        return values

    def contact_run(self, minutes_ago):
        record = self.home / "apt-scraper" / "contact-run"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("run", encoding="utf-8")
        moment = time.time() - minutes_ago * 60
        os.utime(record, (moment, moment))

    def off_with(self, during_off):
        """Turns off a setup idle for 31 minutes and calls during_off once, between the first `config set ... false`
        and the rest of the turn-off."""
        self.run_main("browser", "on")
        self.age_setup_record(31)
        original = connections.set_setup_browser
        calls = []

        def set_setup_browser(value):
            if value == "false" and not calls:
                calls.append(value)
                during_off()
            original(value)

        with mock.patch.object(connections, "set_setup_browser", set_setup_browser):
            return connections.setup_browser_running()

    def test_browser_on_while_a_stale_setup_is_turned_off_waits_and_leaves_the_browser_on_with_its_record(self):
        turning_on = threading.Thread(target=connections.setup_browser_on)

        def start_browser_on():
            turning_on.start()
            turning_on.join(3)

        with mock.patch.dict(os.environ, self.environment):
            self.off_with(start_browser_on)
            turning_on.join(60)
        self.assertTrue(self.setup_record().exists())
        self.assertEqual(self.setting_values(), dict.fromkeys(connections.SETUP_BROWSER_SETTINGS, "true"))

    def test_a_record_rewritten_while_a_stale_setup_is_turned_off_is_kept_and_the_browser_left_on(self):
        def rewrite_record():
            self.setup_record().write_text("on again", encoding="utf-8")
            os.utime(self.setup_record())

        with mock.patch.dict(os.environ, self.environment):
            self.assertTrue(self.off_with(rewrite_record))
        self.assertTrue(self.setup_record().exists())
        self.assertEqual(self.setting_values(), dict.fromkeys(connections.SETUP_BROWSER_SETTINGS, "true"))

    def test_browser_on_waits_for_a_listing_correspondence_run_until_it_ends_or_runs_too_long(self):
        self.contact_run(10)
        code, output = self.run_main("browser", "on")
        self.assertEqual(code, 4)
        self.assertIn("listing correspondence run", output)
        self.assertEqual((self.config_calls(), self.setup_record().exists()), ([], False))
        self.contact_run(61)
        code, _ = self.run_main("browser", "on")
        self.assertEqual(code, 0)
        self.assertEqual(self.config_calls(), self.setup_settings("true"))

    def test_the_contact_job_gate_records_the_run_it_wakes_and_clears_it_at_the_next_gate(self):
        record = self.home / "apt-scraper" / "contact-run"
        with mock.patch.dict(os.environ, self.environment):
            self.assertTrue(connections.contact_job_gate(True))
            self.assertTrue(record.exists())
            self.assertFalse(connections.contact_job_gate(False))
            self.assertFalse(record.exists())
        self.run_main("browser", "on")
        with mock.patch.dict(os.environ, self.environment):
            self.assertFalse(connections.contact_job_gate(True))
        self.assertFalse(record.exists())

    def test_browser_status_exits_0_only_while_no_setup_uses_the_browser(self):
        self.assertEqual(self.run_main("browser", "status")[0], 0)
        self.run_main("browser", "on")
        self.assertEqual(self.run_main("browser", "status")[0], connections.NOT_CONNECTED)
        self.run_main("browser", "off")
        self.assertEqual(self.run_main("browser", "status")[0], 0)


class SkillTest(unittest.TestCase):
    """The account-connections skill drives web pages with Hermes's browser tools, never with computer use."""

    def setUp(self):
        self.text = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
        self.sections = {part.split("\n", 1)[0]: part for part in re.split(r"^## ", self.text, flags=re.M)[1:]}

    def test_the_google_and_telegram_steps_use_the_browser_tools_and_open_nothing_in_the_users_own_windows(self):
        for name in ("Google: Gmail and Calendar", "Messaging gateway"):
            with self.subTest(name):
                section = self.sections[name]
                self.assertIn("browser_navigate", section)
                self.assertNotRegex(section, r"(?i)computer[ _-]use|cmd /c start|\bopen \"|address bar|t\.me/")

    def test_computer_use_is_named_only_for_what_no_browser_can_do(self):
        lines = [line for line in self.text.splitlines() if re.search(r"(?i)computer[ _-]use", line)]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line[:60]):
                self.assertRegex(line, r"Terminal|terminal program")
        self.assertIn("Never use `computer_use` on a web page", self.sections["The browser"])

    def test_the_users_browser_is_turned_on_before_the_browser_steps_and_off_however_the_setup_ends(self):
        browser = self.sections["The browser"]
        turn_on = browser.index('connections.py" browser on')
        self.assertLess(turn_on, browser.index('connections.py" browser off'))
        self.assertIn("before your first browser action in every turn of the setup", browser[:turn_on])
        self.assertLess(self.text.index('connections.py" browser on'), self.text.index("browser_navigate`. The consent"))
        ending = browser[browser.index("Turn it off again"):browser.index('connections.py" browser off')]
        for exit_path in ("after the final `check`", "when the user stops", "when a step fails",
                          "without the user's logins"):
            with self.subTest(exit_path):
                self.assertIn(exit_path, ending)
        self.assertIn("run `browser off`", self.sections["Procedure"])

    def test_nothing_reads_the_botfather_chat_while_it_shows_the_token(self):
        self.assertIn("take no snapshot, screenshot or `browser_vision` of the window", self.sections["Messaging gateway"])


if __name__ == "__main__":
    unittest.main()
