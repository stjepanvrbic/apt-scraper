import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from test_gallery_store import ListingStore, listing
from store import viewing_time

ROOT = Path(__file__).resolve().parents[1]
CONTACT = ROOT / 'profile' / 'skills' / 'contact' / 'scripts' / 'contact.py'
GATE = ROOT / 'profile' / 'scripts' / 'contact-due.py'

FAKE_GOOGLE_API = '''
import json, os, sys
from datetime import datetime
from googleapiclient.errors import HttpError


def load(variable):
    try:
        return json.loads(open(os.environ[variable], encoding="utf-8").read())
    except FileNotFoundError:
        return {}


def log(*entry):
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as file:
        file.write(json.dumps(list(entry)) + "\\n")


def overlaps(event, start, end):
    return (datetime.fromisoformat(event["start"]) < datetime.fromisoformat(end)
            and datetime.fromisoformat(start) < datetime.fromisoformat(event["end"]))


class Request:
    def __init__(self, action, calendar, **arguments):
        self.action, self.calendar, self.arguments = action, calendar, arguments

    def execute(self):
        log("calendar_api", self.action, self.calendar, self.arguments)
        state = load("FAKE_STATE")
        identifier = self.arguments.get("eventId") or self.arguments["body"]["id"]
        failure = state.get("calendar_error") or state.get("event_errors", {}).get(identifier)
        if failure:
            raise HttpError(failure["status"], failure["reason"])
        events = load("FAKE_CALENDAR")
        if self.action == "insert":
            if identifier in events:
                raise HttpError(409, "The requested identifier already exists.")
            events[identifier] = {**self.arguments["body"], "status": "confirmed"}
        elif identifier not in events:
            raise HttpError(404, "Not Found")
        elif self.action == "patch":
            events[identifier].update(self.arguments["body"])
        elif events[identifier]["status"] == "cancelled":
            raise HttpError(410, "Resource has been deleted")
        else:
            events[identifier]["status"] = "cancelled"
        open(os.environ["FAKE_CALENDAR"], "w", encoding="utf-8").write(json.dumps(events))
        return "" if self.action == "delete" else {"id": identifier, "htmlLink": "https://calendar.example/" + identifier}


class Events:
    def insert(self, calendarId, body):
        return Request("insert", calendarId, body=body)

    def patch(self, calendarId, eventId, body):
        return Request("patch", calendarId, eventId=eventId, body=body)

    def delete(self, calendarId, eventId):
        return Request("delete", calendarId, eventId=eventId)


class Service:
    def events(self):
        return Events()


def build_service(api, version):
    assert (api, version) == ("calendar", "v3")
    return Service()


if __name__ == "__main__":
    state = load("FAKE_STATE")
    log("google_api", *sys.argv[1:])
    if state.get("google_error"):
        print(state["google_error"], file=sys.stderr)
        sys.exit(1)
    arguments = sys.argv[1:]
    if arguments[:2] == ["gmail", "send"]:
        thread = arguments[arguments.index("--thread-id") + 1] if "--thread-id" in arguments else state.get("next_thread", "thread-1")
        print(json.dumps({"status": "sent", "id": "sent-%d" % len(open(os.environ["FAKE_LOG"]).readlines()), "threadId": thread}))
    elif arguments[:2] == ["gmail", "reply"]:
        print(json.dumps({"status": "sent", "id": "reply-%d" % len(open(os.environ["FAKE_LOG"]).readlines()), "threadId": "thread-1"}))
    elif arguments[:2] == ["gmail", "search"]:
        print(json.dumps([{key: message[key] for key in ("id", "threadId", "from", "subject")} for message in state.get("inbox", [])]))
    elif arguments[:2] == ["gmail", "get"]:
        message = next(message for message in state["inbox"] if message["id"] == arguments[2])
        print(json.dumps(message))
    elif arguments[:2] == ["calendar", "list"]:
        start, end = arguments[arguments.index("--start") + 1], arguments[arguments.index("--end") + 1]
        booked = [{"id": key, "summary": event["summary"], "start": event["start"]["dateTime"], "end": event["end"]["dateTime"],
                   "status": event["status"]} for key, event in load("FAKE_CALENDAR").items()]
        print(json.dumps([event for event in state.get("events", []) + booked if overlaps(event, start, end)]))
'''

FAKE_HTTP_ERROR = '''
class HttpError(Exception):
    def __init__(self, status, reason):
        super().__init__(f'<HttpError {status} returned "{reason}">')
        self.status_code, self.reason = status, reason
'''

FAKE_IMSG = '''
import json, os, sys
state = json.loads(open(os.environ["FAKE_STATE"], encoding="utf-8").read())
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["imsg", *sys.argv[1:]]) + "\\n")
command = sys.argv[1]
if command == "send":
    print("sent")
elif command == "chats":
    for chat in state.get("chats", []):
        print(json.dumps(chat))
elif command == "history":
    chat = int(sys.argv[sys.argv.index("--chat-id") + 1])
    for message in state.get("history", {}).get(str(chat), []):
        print(json.dumps(message))
'''

FAKE_HERMES = '''
import json, os, sys
state = json.loads(open(os.environ["FAKE_STATE"], encoding="utf-8").read())
arguments = sys.argv[1:]
if "--file" in arguments:
    arguments.append(open(arguments[arguments.index("--file") + 1], encoding="utf-8").read())
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(["hermes", *arguments]) + "\\n")
if state.get("gateway_error"):
    print(json.dumps({"error": state["gateway_error"]}))
    sys.exit(1)
print(json.dumps({"success": True, "platform": "telegram", "message_id": "777"}))
'''

AVAILABILITY = {'timezone': 'America/Chicago', 'weekly': [{'day': 'saturday', 'start': '10:00', 'end': '14:00'}],
                'unavailable': [{'from': '2026-12-20', 'to': '2027-01-03'}]}


class ContactTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / 'home'
        self.scratch = self.home / 'cache' / 'scratch'
        self.scratch.mkdir(parents=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls.jsonl'
        self.log.touch()
        self.state_file = self.root / 'state.json'
        self.state = {}
        self.save_state()
        self.environment = {**os.environ, 'HERMES_HOME': str(self.home), 'FAKE_LOG': str(self.log), 'FAKE_STATE': str(self.state_file),
                            'FAKE_CALENDAR': str(self.root / 'calendar.json'),
                            'PATH': str(self.bin) + os.pathsep + os.environ.get('PATH', ''), 'PYTHONDONTWRITEBYTECODE': '1'}
        self.write_tool('hermes', FAKE_HERMES)
        self.write_tool('imsg', FAKE_IMSG)
        google = self.home / 'skills' / 'productivity' / 'google-workspace' / 'scripts'
        google.mkdir(parents=True)
        (google / 'google_api.py').write_text(textwrap.dedent(FAKE_GOOGLE_API), encoding='utf-8')
        (google / 'googleapiclient').mkdir()
        (google / 'googleapiclient' / '__init__.py').touch()
        (google / 'googleapiclient' / 'errors.py').write_text(textwrap.dedent(FAKE_HTTP_ERROR), encoding='utf-8')
        self.connect('gmail', 'calendar', 'gateway', 'imessage')
        self.store = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')
        self.store.upsert_listing(listing(agent={'name': 'Example Agent', 'email': 'agent@example.com', 'phone': '+1 (312) 555-0100'}))
        self.store.update_settings(viewing_availability=AVAILABILITY)
        self.store.request_contact('example:100')

    def write_tool(self, name, source):
        script = self.bin / f'{name}_fake.py'
        script.write_text(textwrap.dedent(source), encoding='utf-8')
        if os.name == 'nt':
            (self.bin / f'{name}.cmd').write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding='utf-8')
        else:
            launcher = self.bin / name
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8')
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    def save_state(self):
        self.state_file.write_text(json.dumps(self.state), encoding='utf-8')

    def connect(self, *channels, state='connected'):
        path = self.home / 'apt-scraper' / 'connections.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        records = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        now = datetime.now(timezone.utc).isoformat()
        for channel in channels:
            records[channel] = {'state': state, 'message': '' if state == 'connected' else 'No Google account is connected.',
                                'checked_at': now, **({'platform': 'telegram'} if channel == 'gateway' else {})}
            if channel == 'imessage':
                records['imessage-terminal'] = records['imessage-gateway'] = records[channel]
        path.write_text(json.dumps(records), encoding='utf-8')

    def file(self, value):
        path = self.scratch / f'{len(list(self.scratch.iterdir()))}-{datetime.now().timestamp()}.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def run_script(self, script, *arguments):
        return subprocess.run([sys.executable, str(script), *map(str, arguments)], capture_output=True, text=True,
                              encoding='utf-8', env=self.environment, timeout=60)

    def contact(self, *arguments):
        return self.run_script(CONTACT, *arguments)

    def calls(self, tool):
        return [call[1:] for call in map(json.loads, self.log.read_text(encoding='utf-8').splitlines()) if call[0] == tool]

    def sends(self):
        return [call for tool in ('google_api', 'imsg', 'hermes') for call in self.calls(tool)
                if call[:2] in (['gmail', 'send'], ['gmail', 'reply']) or call[:1] == ['send']]

    def correspondence(self):
        return [(item['direction'], item['channel'], item['text']) for item in self.store.listing('example:100')['correspondence']]


class SendTests(ContactTest):
    def test_first_email_goes_to_the_listing_agent_and_shows_as_contacted(self):
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing 100 Example Street',
                                                 'text': 'Hello, I would like to see the apartment.'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        send = self.calls('google_api')[-1]
        self.assertEqual(send[:2], ['gmail', 'send'])
        self.assertEqual(send[send.index('--to') + 1], 'agent@example.com')
        self.assertEqual(send[send.index('--subject') + 1], 'Viewing 100 Example Street')
        self.assertEqual(self.correspondence(), [('outgoing', 'email', 'Hello, I would like to see the apartment.')])
        saved = self.store.listing('example:100')
        self.assertEqual((saved['contact']['status'], saved['contact']['channel']), ('contacted', 'email'))
        self.assertEqual(saved['correspondence'][0]['external_thread_id'], 'thread-1')
        self.assertEqual(self.store.browse('Contacted')['total'], 1)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_disconnected_gmail_refuses_and_sends_nothing(self):
        self.connect('gmail', state='not connected')
        message = self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'})
        result = self.contact('send', message)
        self.assertEqual(result.returncode, 3)
        self.assertIn('Do not use Gmail', result.stdout + result.stderr)
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.correspondence(), [])
        self.assertTrue(message.exists())
        contact = self.store.listing('example:100')['contact']
        self.assertEqual(contact['status'], 'failed')
        self.assertIn('Gmail', contact['error'])

    def test_a_working_second_channel_clears_the_failure_of_the_first(self):
        self.connect('gmail', state='not connected')
        first = self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'})
        self.assertEqual(self.contact('send', first).returncode, 3)
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'imessage', 'text': 'Hello'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        contact = self.store.listing('example:100')['contact']
        self.assertEqual((contact['status'], contact['error']), ('contacted', None))

    def test_reply_on_a_disconnected_channel_stops_waking_the_agent(self):
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'}))
        self.store.correspond('example:100', 'incoming', 'email', 'Saturday?', external_message_id='in-1')
        self.connect('gmail', state='not connected')
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'Saturday works.'}))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.store.listing('example:100')['contact']['status'], 'failed')
        self.assertEqual(self.store.due_contacts(), [])
        self.store.request_contact('example:100')
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'reply'}])

    def test_reply_answers_the_latest_incoming_email_in_its_thread(self):
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'}))
        self.store.correspond('example:100', 'incoming', 'email', 'Saturday at 11?', external_message_id='in-1', external_thread_id='thread-1')
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'Saturday at 11 works.'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls('google_api')[-1][:3], ['gmail', 'reply', 'in-1'])
        self.assertEqual(self.store.due_contacts(), [])

    def test_follow_up_without_a_reply_stays_in_the_first_thread(self):
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'}))
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'Following up.'}))
        send = self.calls('google_api')[-1]
        self.assertEqual(send[send.index('--thread-id') + 1], 'thread-1')
        self.assertEqual(send[send.index('--subject') + 1], 'Re: Viewing')

    def test_first_email_needs_a_subject(self):
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'Hello'}))
        self.assertEqual(result.returncode, 1)
        self.assertIn('subject', result.stderr)
        self.assertEqual(self.sends(), [])

    def test_text_goes_to_the_agent_phone_and_records_the_message_guid(self):
        self.state = {'chats': [{'id': 7, 'identifier': '+13125550100', 'guid': 'iMessage;-;+13125550100', 'participants': ['+13125550100']}],
                      'history': {'7': [{'id': 9, 'guid': 'guid-9', 'chat_guid': 'iMessage;-;+13125550100', 'is_from_me': True, 'text': 'Hello'}]}}
        self.save_state()
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'imessage', 'text': 'Hello'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        send = self.calls('imsg')[0]
        self.assertEqual(send[:3], ['send', '--to', '+1 (312) 555-0100'])
        saved = self.store.listing('example:100')['correspondence'][0]
        self.assertEqual((saved['external_message_id'], saved['external_thread_id']), ('guid-9', 'iMessage;-;+13125550100'))

    def test_transport_failure_records_nothing(self):
        self.state = {'google_error': 'HttpError 500'}
        self.save_state()
        result = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'}))
        self.assertEqual(result.returncode, 1)
        self.assertIn('HttpError 500', result.stderr)
        self.assertEqual(self.correspondence(), [])
        self.assertEqual(self.store.listing('example:100')['contact']['status'], 'pending')

    def test_files_outside_the_scratch_directory_are_refused(self):
        outside = self.root / 'message.json'
        outside.write_text(json.dumps({'listing': 'example:100', 'channel': 'email', 'subject': 'S', 'text': 'Hello'}), encoding='utf-8')
        result = self.contact('send', outside)
        self.assertEqual(result.returncode, 1)
        self.assertIn('scratch directory', result.stderr)
        self.assertEqual(self.sends(), [])

    def test_submitted_contact_form_is_recorded_as_contacted(self):
        result = self.contact('submitted', self.file({'listing': 'example:100', 'text': 'Hello', 'url': 'https://example.com/listings/100'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.correspondence(), [('outgoing', 'contact_form', 'Hello')])
        self.assertEqual(self.store.listing('example:100')['contact']['channel'], 'contact_form')


class QuestionTests(ContactTest):
    def setUp(self):
        super().setUp()
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'}))
        self.store.correspond('example:100', 'incoming', 'email', 'Please send your date of birth.', external_message_id='in-1')

    def ask(self):
        return self.contact('ask', self.file({'listing': 'example:100', 'question': 'The agent asks for your date of birth. Share it?',
                                               'field': 'date of birth', 'purpose': 'rental screening'}))

    def test_question_reaches_the_user_through_the_gateway_and_holds_every_send(self):
        result = self.ask()
        self.assertEqual(result.returncode, 0, result.stderr)
        gateway = self.calls('hermes')[0]
        self.assertEqual(gateway[:5], ['send', '--to', 'telegram', '--json', '--file'])
        self.assertIn('Courtyard apartment', gateway[-1])
        self.assertIn('date of birth', gateway[-1])
        self.assertIn('https://example.com/listings/100', gateway[-1])
        blocked = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'One moment.'}))
        self.assertEqual(blocked.returncode, 4)
        self.assertIn('waiting for the user', blocked.stderr)
        self.assertEqual(len(self.calls('google_api')), 1)
        questions = json.loads(self.contact('questions').stdout)
        self.assertEqual([item['field'] for item in questions], ['date of birth'])

    def test_answer_with_the_value_approves_it_for_this_listing_and_purpose_only(self):
        question = json.loads(self.ask().stdout)['question']
        result = self.contact('answer', self.file({'question': question, 'answer': 'Sure: 1990-05-01', 'share': '1990-05-01'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.contact('questions').stdout), [])
        self.assertEqual([item['value'] for item in self.store.approved_information('example:100', 'rental screening')], ['1990-05-01'])
        self.assertEqual(self.store.due_contacts(), [{'listing_id': 'example:100', 'reason': 'reply'}])
        sent = self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'My date of birth is 1990-05-01.'}))
        self.assertEqual(sent.returncode, 0, sent.stderr)

    def test_disconnected_gateway_sends_no_question_and_shows_it_in_the_gallery(self):
        self.connect('gateway', state='not connected')
        result = self.ask()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.calls('hermes'), [])
        self.assertEqual(self.store.open_questions(), [])
        contact = self.store.listing('example:100')['contact']
        self.assertEqual(contact['status'], 'failed')
        self.assertIn('The agent asks for your date of birth. Share it?', contact['error'])
        self.assertIn('messaging gateway', contact['error'])
        self.assertEqual(self.store.due_contacts(), [])

    def test_declined_answer_reaches_the_correspondence_job(self):
        question = json.loads(self.ask().stdout)['question']
        self.contact('answer', self.file({'question': question, 'answer': 'No, please do not share it.'}))
        [item] = json.loads(self.contact('work').stdout)['due']
        self.assertEqual(item['reason'], 'reply')
        self.assertEqual([(answer['question'], answer['answer']) for answer in item['answers']],
                         [('The agent asks for your date of birth. Share it?', 'No, please do not share it.')])
        self.assertEqual(self.store.approved_information('example:100', 'rental screening'), [])
        reply = self.file({'listing': 'example:100', 'channel': 'email', 'text': 'I would rather not share that before the viewing.'})
        sent = self.contact('send', reply)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertEqual(json.loads(self.contact('work').stdout)['due'], [])
        self.assertEqual(self.store.unhandled_answers('example:100'), [])

    def test_time_the_user_confirmed_reaches_the_correspondence_job_and_is_booked(self):
        self.store.correspond('example:100', 'incoming', 'email', 'Could you do Friday at 18:00?', external_message_id='in-2')
        outside = self.contact('agree', self.file({'listing': 'example:100', 'time': '2026-10-30T18:00:00-05:00', 'details': 'Lobby'}))
        self.assertEqual(outside.returncode, 4)
        asked = self.contact('ask', self.file({'listing': 'example:100', 'question': 'The agent offers Friday, October 30 at 18:00. Does it work?'}))
        question = json.loads(asked.stdout)['question']
        self.contact('answer', self.file({'question': question, 'answer': 'Yes, Friday at 18:00 works.'}))
        [item] = json.loads(self.contact('work').stdout)['due']
        self.assertEqual([answer['answer'] for answer in item['answers']], ['Yes, Friday at 18:00 works.'])
        agreed = self.contact('agree', self.file({'listing': 'example:100', 'time': '2026-10-30T18:00:00-05:00', 'details': 'Lobby',
                                                  'user_confirmed': True}))
        self.assertEqual(agreed.returncode, 0, agreed.stderr)
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'Friday at 18:00 works, see you then.'}))
        self.assertEqual(json.loads(self.contact('work').stdout)['due'], [])
        self.assertEqual([viewing['status'] for viewing in self.store.listing('example:100')['viewings']], ['booked'])

    def test_failed_gateway_delivery_records_no_question(self):
        self.state = {'gateway_error': 'Chat not found'}
        self.save_state()
        result = self.ask()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Chat not found', result.stderr)
        self.assertEqual(self.store.open_questions(), [])


class WorkTests(ContactTest):
    def test_work_lists_due_listings_with_their_facts_and_history(self):
        approval = self.store.approve_information('example:100', 'name', 'Test Person', 'contact form')
        work = json.loads(self.contact('work').stdout)
        self.assertEqual(work['viewing_availability'], AVAILABILITY)
        [item] = work['due']
        self.assertEqual((item['reason'], item['listing']['address'], item['contact']['agent']['email']),
                         ('start', '100 Example Street, Chicago', 'agent@example.com'))
        self.assertEqual([entry['id'] for entry in item['approvals']], [approval])
        self.assertEqual(item['correspondence'], [])

    def test_done_marks_a_reply_handled_and_close_ends_the_contact(self):
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing', 'text': 'Hello'}))
        self.store.correspond('example:100', 'incoming', 'email', 'Thanks!', external_message_id='in-1')
        self.assertEqual(self.contact('done', 'example:100').returncode, 0)
        self.assertEqual(json.loads(self.contact('work').stdout)['due'], [])
        self.assertEqual(self.contact('close', 'example:100').returncode, 0)
        self.assertEqual(self.store.listing('example:100')['contact']['status'], 'closed')

    def test_tell_reaches_the_user_with_the_listing_named(self):
        result = self.contact('tell', self.file({'listing': 'example:100', 'text': 'The viewing is set for Saturday at 11:00.'}))
        self.assertEqual(result.returncode, 0, result.stderr)
        [call] = self.calls('hermes')
        self.assertIn('Courtyard apartment', call[-1])
        self.assertIn('Saturday at 11:00', call[-1])
        self.connect('gateway', state='not connected')
        self.assertEqual(self.contact('tell', self.file({'text': 'Hello'})).returncode, 3)
        self.assertEqual(len(self.calls('hermes')), 1)

    def test_fail_shows_the_error_and_allows_a_retry(self):
        self.assertEqual(self.contact('fail', 'example:100', 'The listing has no email, phone or contact form.').returncode, 0)
        contact = self.store.listing('example:100')['contact']
        self.assertEqual((contact['status'], contact['error']), ('failed', 'The listing has no email, phone or contact form.'))


class AgreeTests(ContactTest):
    def agree(self, time, **extra):
        return self.contact('agree', self.file({'listing': 'example:100', 'time': time, 'details': 'Meet in the lobby.', **extra}))

    def test_time_inside_availability_and_free_on_the_calendar_is_agreed(self):
        result = self.agree('2026-10-31T11:00:00-05:00')
        self.assertEqual(result.returncode, 0, result.stderr)
        listing_list = self.calls('google_api')[-1]
        self.assertEqual(listing_list[:2], ['calendar', 'list'])
        self.assertEqual(listing_list[listing_list.index('--start') + 1], '2026-10-31T16:00:00+00:00')
        self.assertEqual(listing_list[listing_list.index('--end') + 1], '2026-10-31T17:00:00+00:00')
        [viewing] = self.store.listing('example:100')['viewings']
        self.assertEqual((viewing['time'], viewing['details']), ('2026-10-31T16:00:00+00:00', 'Meet in the lobby.'))

    def test_time_outside_the_weekly_windows_waits_for_the_user(self):
        for time in ('2026-10-30T11:00:00-05:00', '2026-10-31T13:30:00-05:00', '2026-12-26T11:00:00-06:00'):
            result = self.agree(time)
            self.assertEqual(result.returncode, 4, time)
            self.assertIn('availability', result.stderr)
        self.assertEqual(self.store.agreed_viewings(), [])

    def test_conflicting_calendar_event_waits_for_the_user(self):
        self.state = {'events': [{'id': 'e1', 'summary': 'Dentist', 'start': '2026-10-31T11:30:00-05:00', 'end': '2026-10-31T12:00:00-05:00'}]}
        self.save_state()
        result = self.agree('2026-10-31T11:00:00-05:00')
        self.assertEqual(result.returncode, 4)
        self.assertIn('Dentist', result.stderr)
        self.assertEqual(self.store.agreed_viewings(), [])
        self.assertEqual(self.calls('calendar_api'), [])

    def test_disconnected_calendar_cannot_confirm_a_free_time(self):
        self.connect('calendar', state='not connected')
        self.assertEqual(self.agree('2026-10-31T11:00:00-05:00').returncode, 3)
        self.assertEqual(self.store.agreed_viewings(), [])

    def test_time_the_user_confirmed_is_agreed_without_checks(self):
        self.connect('calendar', state='not connected')
        result = self.agree('2026-10-30T18:00:00-05:00', user_confirmed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.store.agreed_viewings()), 1)
        self.assertEqual(self.calls('google_api'), [])

    def test_canceled_viewing_leaves_the_agreed_list(self):
        viewing = json.loads(self.agree('2026-10-31T11:00:00-05:00').stdout)['viewing']
        self.assertEqual(self.contact('cancel', viewing).returncode, 0)
        self.assertEqual(self.store.agreed_viewings(), [])
        self.assertEqual(self.store.listing('example:100')['viewings'][0]['status'], 'canceled')
        self.assertEqual(self.contact('cancel', 'no-such-viewing').returncode, 1)

    def test_time_overlapping_another_agreed_viewing_waits_for_the_user(self):
        self.store.upsert_listing(listing(id='other:200'))
        self.store.request_contact('other:200')
        self.assertEqual(self.agree('2026-10-31T11:00:00-05:00').returncode, 0)
        for confirmed in (False, True):
            result = self.contact('agree', self.file({'listing': 'other:200', 'time': '2026-10-31T11:30:00-05:00', 'details': 'Door',
                                                      'user_confirmed': confirmed}))
            self.assertEqual(result.returncode, 4)
            self.assertIn('example:100', result.stderr)
        self.assertEqual(self.store.listing('other:200')['viewings'], [])
        self.assertEqual([call[0] for call in self.calls('calendar_api')], ['insert'])

    def test_moving_an_agreed_viewing_updates_it(self):
        viewing = json.loads(self.agree('2026-10-31T11:00:00-05:00').stdout)['viewing']
        self.assertEqual(self.agree('2026-10-31T12:00:00-05:00', viewing=viewing).returncode, 0)
        self.assertEqual([item['time'] for item in self.store.listing('example:100')['viewings']], ['2026-10-31T17:00:00+00:00'])


class CalendarTests(ContactTest):
    def setUp(self):
        super().setUp()
        self.store.correspond('example:100', 'outgoing', 'email', 'Hello', external_message_id='m1')
        self.store.set_contact('example:100', 'contacted', channel='email')

    def agree(self, time, **extra):
        result = self.contact('agree', self.file({'listing': 'example:100', 'time': time, 'details': 'Meet Anna in the lobby.', **extra}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def calendar(self):
        path = self.root / 'calendar.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def viewing(self):
        [viewing] = self.store.listing('example:100')['viewings']
        return viewing

    def writes(self):
        return [(call[0], call[2].get('eventId') or call[2]['body']['id']) for call in self.calls('calendar_api')]

    def gate(self):
        result = self.run_script(GATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])['wakeAgent']

    def notices(self):
        return [call[-1] for call in self.calls('hermes') if call[0] == 'send']

    def test_agreed_time_becomes_an_event_with_the_address_listing_link_and_agent_contact(self):
        result = self.agree('2026-10-31T11:00:00-05:00')
        event_id = result['viewing'].replace('-', '')
        self.assertEqual((result['booked'], result['calendar_error']), (True, None))
        self.assertEqual(self.writes(), [('insert', event_id)])
        self.assertEqual(self.calls('calendar_api')[0][1], 'primary')
        event = self.calendar()[event_id]
        self.assertEqual(event['summary'], 'Apartment viewing: 100 Example Street, Chicago')
        self.assertEqual(event['location'], '100 Example Street, Chicago')
        self.assertEqual(event['start'], {'dateTime': '2026-10-31T11:00:00-05:00', 'timeZone': 'America/Chicago'})
        self.assertEqual(event['end'], {'dateTime': '2026-10-31T12:00:00-05:00', 'timeZone': 'America/Chicago'})
        for text in ('Courtyard apartment', 'Example Homes: https://example.com/listings/100', 'Listing agent: Example Agent',
                     'Email: agent@example.com', 'Phone: +1 (312) 555-0100', 'Meet Anna in the lobby.'):
            self.assertIn(text, event['description'])
        viewing = self.viewing()
        self.assertEqual((viewing['status'], viewing['calendar_event_id']), ('booked', event_id))
        [booked] = self.store.browse('Viewings')['items']
        self.assertEqual(booked['viewing']['time'], '2026-10-31T16:00:00+00:00')

    def test_moved_viewing_updates_its_event(self):
        viewing = self.agree('2026-10-31T11:00:00-05:00')['viewing']
        event_id = viewing.replace('-', '')
        result = self.agree('2026-10-31T11:30:00-05:00', viewing=viewing, details='Meet Ben at the side door.')
        self.assertTrue(result['booked'])
        self.assertEqual(self.writes(), [('insert', event_id), ('patch', event_id)])
        [event] = self.calendar().values()
        self.assertEqual(event['start']['dateTime'], '2026-10-31T11:30:00-05:00')
        self.assertIn('Meet Ben at the side door.', event['description'])
        self.assertEqual((self.viewing()['status'], self.viewing()['time']), ('booked', '2026-10-31T16:30:00+00:00'))

    def booked(self, listing_id, time, event_id, status='confirmed'):
        viewing = self.store.agree_viewing(listing_id, time, 'Lobby')
        self.assertTrue(self.store.record_booking(viewing, viewing_time(time), 'Lobby', event_id))
        calendar = self.calendar()
        calendar[event_id] = {'summary': 'Viewing', 'start': {'dateTime': time}, 'end': {'dateTime': time}, 'status': status}
        self.save_calendar(calendar)
        return viewing

    def save_calendar(self, calendar):
        (self.root / 'calendar.json').write_text(json.dumps(calendar), encoding='utf-8')

    def test_moved_viewing_keeps_an_event_with_another_identifier(self):
        viewing = self.booked('example:100', '2026-10-31T11:00:00-05:00', 'otherevent1')
        self.agree('2026-10-31T12:00:00-05:00', viewing=viewing)
        self.assertEqual(self.writes(), [('patch', 'otherevent1')])
        self.assertEqual(self.calendar()['otherevent1']['start']['dateTime'], '2026-10-31T12:00:00-05:00')

    def test_moving_a_viewing_whose_event_the_user_deleted_restores_the_event(self):
        viewing = self.booked('example:100', '2026-10-31T11:00:00-05:00', 'otherevent1', status='cancelled')
        self.assertTrue(self.agree('2026-10-31T12:00:00-05:00', viewing=viewing)['booked'])
        self.assertEqual(self.calendar()['otherevent1']['status'], 'confirmed')

    def test_retried_create_of_an_event_the_user_deleted_restores_it(self):
        self.connect('calendar', state='not connected')
        viewing = self.agree('2026-10-31T11:00:00-05:00', user_confirmed=True)['viewing']
        event_id = viewing.replace('-', '')
        self.save_calendar({event_id: {'summary': 'Viewing', 'start': {'dateTime': '2026-10-31T11:00:00-05:00'},
                                       'end': {'dateTime': '2026-10-31T12:00:00-05:00'}, 'status': 'cancelled'}})
        self.connect('calendar')
        self.gate()
        self.assertEqual(self.writes(), [('insert', event_id), ('patch', event_id)])
        self.assertEqual(self.calendar()[event_id]['status'], 'confirmed')
        self.assertEqual(self.viewing()['status'], 'booked')

    def test_moved_viewing_whose_event_is_gone_from_the_calendar_gets_a_new_event(self):
        viewing = self.booked('example:100', '2026-10-31T11:00:00-05:00', 'otherevent1')
        self.save_calendar({})
        self.assertTrue(self.agree('2026-10-31T12:00:00-05:00', viewing=viewing)['booked'])
        event_id = viewing.replace('-', '')
        self.assertEqual(self.writes(), [('patch', 'otherevent1'), ('insert', event_id)])
        self.assertEqual(self.calendar()[event_id]['start']['dateTime'], '2026-10-31T12:00:00-05:00')
        self.assertEqual(self.viewing()['calendar_event_id'], event_id)

    def test_one_failing_write_does_not_stop_the_others(self):
        self.store.upsert_listing(listing(id='other:200', address='200 Example Street'))
        self.store.request_contact('other:200')
        stuck = self.booked('other:200', '2026-10-31T10:00:00-05:00', 'stuckevent1')
        self.store.agree_viewing('other:200', '2026-10-31T09:00:00-05:00', 'Lobby', viewing_id=stuck)
        canceled = self.booked('example:100', '2026-11-07T10:00:00-06:00', 'canceledevent1')
        self.store.cancel_viewing(canceled, possible_event_id=canceled.replace('-', ''))
        self.state = {'event_errors': {'stuckevent1': {'status': 403, 'reason': 'Forbidden'}}}
        self.save_state()
        result = self.agree('2026-10-31T11:00:00-05:00')
        self.assertEqual((result['booked'], result['calendar_error']), (True, None))
        self.assertEqual(self.calendar()['canceledevent1']['status'], 'cancelled')
        self.assertEqual(self.store.listing('other:200')['viewings'][0]['status'], 'agreed')
        self.gate()
        [notice] = self.notices()
        self.assertIn('200 Example Street', notice)
        self.assertIn('Forbidden', notice)
        self.gate()
        self.assertEqual(len(self.notices()), 1)

    def test_a_store_error_after_agreeing_is_reported_without_failing_the_command(self):
        sys.path.insert(0, str(CONTACT.parent))
        import contact
        with patch.dict(os.environ, self.environment), \
                patch.object(ListingStore, 'record_booking', side_effect=sqlite3.OperationalError('database is locked')):
            result = contact.command_agree(self.store, {'listing': 'example:100', 'time': '2026-10-31T11:00:00-05:00', 'details': ''})
        self.assertFalse(result['booked'])
        self.assertIn('database is locked', result['calendar_error'])
        self.assertEqual(self.viewing()['status'], 'agreed')

    def test_canceled_viewing_removes_its_event(self):
        viewing = self.agree('2026-10-31T11:00:00-05:00')['viewing']
        result = self.contact('cancel', viewing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'canceled': viewing, 'calendar_error': None})
        self.assertEqual(self.calendar()[viewing.replace('-', '')]['status'], 'cancelled')
        self.assertEqual((self.viewing()['status'], self.viewing()['calendar_event_id']), ('canceled', None))
        self.assertEqual(self.store.browse('Viewings')['total'], 0)

    def test_canceling_a_viewing_without_a_recorded_event_removes_an_event_its_lost_write_made(self):
        self.connect('calendar', state='not connected')
        viewing = self.agree('2026-10-31T11:00:00-05:00', user_confirmed=True)['viewing']
        event_id = viewing.replace('-', '')
        self.save_calendar({event_id: {'summary': 'Viewing', 'start': {'dateTime': '2026-10-31T11:00:00-05:00'},
                                       'end': {'dateTime': '2026-10-31T12:00:00-05:00'}, 'status': 'confirmed'}})
        self.connect('calendar')
        self.assertEqual(json.loads(self.contact('cancel', viewing).stdout)['calendar_error'], None)
        self.assertEqual(self.writes(), [('delete', event_id)])
        self.assertEqual(self.calendar()[event_id]['status'], 'cancelled')
        self.assertIsNone(self.viewing()['calendar_event_id'])

    def test_canceling_a_viewing_that_never_got_an_event_counts_the_missing_event_as_removed(self):
        self.connect('calendar', state='not connected')
        viewing = self.agree('2026-10-31T11:00:00-05:00', user_confirmed=True)['viewing']
        self.connect('calendar')
        self.assertEqual(json.loads(self.contact('cancel', viewing).stdout)['calendar_error'], None)
        self.assertEqual(self.writes(), [('delete', viewing.replace('-', ''))])
        self.assertIsNone(self.viewing()['calendar_event_id'])
        self.assertFalse(self.gate())
        self.assertEqual(len(self.writes()), 1)

    def test_an_event_already_gone_counts_as_removed(self):
        viewing = self.agree('2026-10-31T11:00:00-05:00')['viewing']
        calendar = self.calendar()
        calendar[viewing.replace('-', '')]['status'] = 'cancelled'
        (self.root / 'calendar.json').write_text(json.dumps(calendar), encoding='utf-8')
        self.assertEqual(json.loads(self.contact('cancel', viewing).stdout)['calendar_error'], None)
        self.assertIsNone(self.viewing()['calendar_event_id'])

    def test_a_canceled_viewing_cannot_be_moved(self):
        viewing = self.agree('2026-10-31T11:00:00-05:00')['viewing']
        self.contact('cancel', viewing)
        result = self.contact('agree', self.file({'listing': 'example:100', 'time': '2026-10-31T12:00:00-05:00', 'details': '',
                                                  'viewing': viewing}))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.viewing()['status'], 'canceled')

    def test_disconnected_calendar_writes_nothing_and_books_once_it_passes(self):
        self.connect('calendar', state='not connected')
        result = self.agree('2026-10-31T11:00:00-05:00', user_confirmed=True)
        self.assertFalse(result['booked'])
        self.assertIn('Do not use Google Calendar', result['calendar_error'])
        self.assertEqual(self.calls('calendar_api'), [])
        self.assertEqual(self.viewing()['status'], 'agreed')
        self.assertFalse(self.gate())
        self.assertEqual(self.calls('calendar_api'), [])
        [notice] = self.notices()
        self.assertIn('Google Calendar', notice)
        self.assertFalse(self.gate())
        self.assertEqual(len(self.notices()), 1)
        self.connect('calendar')
        self.assertFalse(self.gate())
        self.assertEqual(self.writes(), [('insert', result['viewing'].replace('-', ''))])
        self.assertEqual(self.viewing()['status'], 'booked')
        self.assertFalse((self.home / 'apt-scraper' / 'contact-notices.json').exists())

    def test_disconnected_calendar_keeps_the_event_of_a_canceled_viewing_until_it_passes(self):
        viewing = self.agree('2026-10-31T11:00:00-05:00')['viewing']
        self.connect('calendar', state='not connected')
        self.assertIn('Do not use Google Calendar', json.loads(self.contact('cancel', viewing).stdout)['calendar_error'])
        self.assertEqual(self.calendar()[viewing.replace('-', '')]['status'], 'confirmed')
        self.connect('calendar')
        self.gate()
        self.assertEqual(self.calendar()[viewing.replace('-', '')]['status'], 'cancelled')
        self.assertIsNone(self.viewing()['calendar_event_id'])

    def test_failed_write_is_retried_without_a_second_event(self):
        self.state = {'calendar_error': {'status': 503, 'reason': 'Backend Error'}}
        self.save_state()
        result = self.agree('2026-10-31T11:00:00-05:00')
        self.assertIn('Backend Error', result['calendar_error'])
        self.assertEqual(self.viewing()['status'], 'agreed')
        event_id = result['viewing'].replace('-', '')
        (self.root / 'calendar.json').write_text(json.dumps({event_id: {
            'summary': 'Apartment viewing', 'start': {'dateTime': '2026-10-31T11:00:00-05:00'},
            'end': {'dateTime': '2026-10-31T12:00:00-05:00'}, 'status': 'confirmed'}}), encoding='utf-8')
        self.state = {}
        self.save_state()
        self.assertFalse(self.gate())
        self.assertEqual(self.writes(), [('insert', event_id), ('insert', event_id), ('patch', event_id)])
        self.assertEqual(len(self.calendar()), 1)
        self.assertEqual(self.viewing()['status'], 'booked')

    def test_viewing_moved_while_its_event_was_written_stays_agreed_for_the_next_write(self):
        viewing = self.store.agree_viewing('example:100', '2026-10-31T11:00:00-05:00', 'Lobby')
        self.assertFalse(self.store.record_booking(viewing, '2026-10-31T17:00:00+00:00', 'Lobby', viewing.replace('-', '')))
        self.assertEqual(self.viewing()['status'], 'agreed')


class GateTests(ContactTest):
    def gate(self):
        result = self.run_script(GATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        return json.loads(lines[-1])['wakeAgent'], '\n'.join(lines[:-1])

    def notices(self):
        return [call for call in self.calls('hermes') if call[0] == 'send']

    def contacted(self):
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'subject': 'Viewing 100 Example Street', 'text': 'Hello'}))

    def test_pending_request_wakes_the_agent(self):
        wake, summary = self.gate()
        self.assertTrue(wake)
        self.assertIn('1 new contact request', summary)

    def setup_browser(self, minutes_ago):
        """The record `connections.py browser on` leaves, written minutes_ago."""
        record = self.home / 'apt-scraper' / 'setup-browser'
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text('on', encoding='utf-8')
        moment = record.stat().st_mtime - minutes_ago * 60
        os.utime(record, (moment, moment))
        return record

    def setup_browser_calls(self):
        return [call[4:] for call in self.calls('hermes') if call[2:4] == ['config', 'set']]

    def test_a_running_account_setup_keeps_the_agent_asleep_while_it_uses_the_users_browser(self):
        record = self.setup_browser(5)
        wake, summary = self.gate()
        self.assertFalse(wake)
        self.assertIn('Waiting while the account setup uses the browser.', summary)
        self.assertTrue(record.exists())
        self.assertFalse((self.home / 'apt-scraper' / 'contact-run').exists())
        self.assertEqual(self.setup_browser_calls(), [])

    def test_an_account_setup_that_stopped_without_turning_its_browser_off_is_turned_off_and_the_agent_wakes(self):
        record = self.setup_browser(31)
        wake, _ = self.gate()
        self.assertTrue(wake)
        self.assertFalse(record.exists())
        self.assertTrue((self.home / 'apt-scraper' / 'contact-run').exists())
        self.assertEqual(self.setup_browser_calls(), [['browser.cdp_url', ''], ['browser.allow_private_urls', 'false']])

    def test_nothing_to_do_keeps_the_agent_asleep(self):
        self.contacted()
        wake, _ = self.gate()
        self.assertFalse(wake)
        self.assertFalse((self.home / 'apt-scraper' / 'contact-notices.json').exists())

    def test_new_email_reply_is_recorded_and_wakes_the_agent_once(self):
        self.contacted()
        self.state = {'inbox': [{'id': 'in-1', 'threadId': 'thread-1', 'from': 'Example Agent <agent@example.com>',
                                 'subject': 'Re: Viewing 100 Example Street', 'body': 'Saturday at 11?'}]}
        self.save_state()
        wake, summary = self.gate()
        self.assertTrue(wake)
        self.assertIn('1 reply to answer', summary)
        self.assertEqual(self.correspondence()[-1], ('incoming', 'email', 'Saturday at 11?'))
        search = [call for call in self.calls('google_api') if call[:2] == ['gmail', 'search']][-1]
        self.assertIn('-from:me', search[2])
        self.assertIn('from:agent@example.com', search[2])
        self.assertIn('subject:"Viewing 100 Example Street"', search[2])
        self.contact('send', self.file({'listing': 'example:100', 'channel': 'email', 'text': 'Saturday at 11 works.'}))
        gets = len([call for call in self.calls('google_api') if call[:2] == ['gmail', 'get']])
        self.state['inbox'].append({'id': self.store.listing('example:100')['correspondence'][-1]['external_message_id'],
                                    'threadId': 'thread-1', 'from': 'me', 'subject': 'Re: Viewing', 'body': 'Saturday at 11 works.'})
        self.save_state()
        wake, _ = self.gate()
        self.assertFalse(wake)
        self.assertEqual(len(self.correspondence()), 3)
        self.assertEqual(len([call for call in self.calls('google_api') if call[:2] == ['gmail', 'get']]), gets)

    def test_new_text_reply_is_recorded(self):
        self.contacted()
        self.state = {'chats': [{'id': 7, 'identifier': '+13125550100', 'guid': 'iMessage;-;+13125550100', 'participants': ['+13125550100']},
                                {'id': 8, 'identifier': '+13125550199', 'guid': 'other', 'participants': ['+13125550199']}],
                      'history': {'7': [{'id': 10, 'guid': 'guid-10', 'chat_guid': 'iMessage;-;+13125550100', 'is_from_me': False, 'text': 'Call me?'},
                                        {'id': 9, 'guid': 'guid-9', 'chat_guid': 'iMessage;-;+13125550100', 'is_from_me': True, 'text': 'Hi'}],
                                  '8': [{'id': 11, 'guid': 'guid-11', 'chat_guid': 'other', 'is_from_me': False, 'text': 'Unrelated'}]}}
        self.save_state()
        wake, _ = self.gate()
        self.assertTrue(wake)
        self.assertEqual(self.correspondence()[1:], [('incoming', 'imessage', 'Call me?')])

    def test_replies_from_a_shared_agency_address_stay_with_their_thread(self):
        self.contacted()
        self.store.upsert_listing(listing(id='other:200', agent={'email': 'agent@example.com'}))
        self.store.request_contact('other:200')
        self.state = {'next_thread': 'thread-2'}
        self.save_state()
        self.contact('send', self.file({'listing': 'other:200', 'channel': 'email', 'subject': 'Viewing 200', 'text': 'Hello'}))
        self.state = {'inbox': [{'id': 'in-2', 'threadId': 'thread-2', 'from': 'Agency <agent@example.com>', 'subject': 'Re: Viewing 200',
                                 'body': 'About 200'},
                                {'id': 'in-9', 'threadId': 'thread-9', 'from': 'Agency <AGENT@example.com>', 'subject': 'Hello',
                                 'body': 'New thread'}]}
        self.save_state()
        self.assertTrue(self.gate()[0])
        self.assertEqual(self.correspondence(), [('outgoing', 'email', 'Hello')])
        self.assertEqual([(item['direction'], item['text']) for item in self.store.listing('other:200')['correspondence']],
                         [('outgoing', 'Hello'), ('incoming', 'About 200')])

    def test_reply_from_an_address_only_one_listing_uses_is_filed_there(self):
        self.contacted()
        self.state = {'inbox': [{'id': 'in-9', 'threadId': 'thread-9', 'from': 'Example Agent <Agent@Example.com>', 'subject': 'Hello',
                                 'body': 'New thread'}]}
        self.save_state()
        self.assertTrue(self.gate()[0])
        self.assertEqual(self.correspondence()[-1], ('incoming', 'email', 'New thread'))

    def test_texts_from_a_shared_number_go_to_the_listing_that_wrote_in_that_chat(self):
        self.contacted()
        self.store.upsert_listing(listing(id='other:200', agent={'phone': '+1 312 555 0100'}))
        self.store.request_contact('other:200')
        self.store.set_contact('other:200', 'contacted')
        self.state = {'chats': [{'id': 7, 'identifier': '+13125550100', 'guid': 'chat-7', 'participants': ['+13125550100']}],
                      'history': {'7': [{'id': 10, 'guid': 'guid-10', 'chat_guid': 'chat-7', 'is_from_me': False, 'text': 'Call me?'}]}}
        self.save_state()
        self.assertFalse(self.gate()[0])
        self.assertEqual(self.correspondence(), [('outgoing', 'email', 'Hello')])
        self.assertEqual(self.store.listing('other:200')['correspondence'], [])
        self.store.correspond('other:200', 'outgoing', 'imessage', 'Hi', external_message_id='guid-9', external_thread_id='chat-7')
        self.assertTrue(self.gate()[0])
        self.assertEqual([item['text'] for item in self.store.listing('other:200')['correspondence']], ['Hi', 'Call me?'])
        self.assertEqual(self.correspondence(), [('outgoing', 'email', 'Hello')])

    def test_a_crashing_connection_check_does_not_wake_the_agent(self):
        self.contacted()
        path = self.home / 'apt-scraper' / 'connections.json'
        path.unlink()
        path.mkdir()
        wake, _ = self.gate()
        self.assertFalse(wake)

    def test_open_question_keeps_the_agent_asleep_after_a_reply(self):
        self.contacted()
        self.store.correspond('example:100', 'incoming', 'email', 'Date of birth?', external_message_id='in-1')
        self.store.ask_user('example:100', 'Share your date of birth?', field='date of birth', purpose='screening')
        wake, _ = self.gate()
        self.assertFalse(wake)

    def test_disconnected_channels_are_not_polled(self):
        self.contacted()
        self.connect('gmail', state='not connected')
        self.connect('imessage', state='unavailable')
        wake, _ = self.gate()
        self.assertFalse(wake)
        self.assertEqual([call for call in self.calls('google_api') if call[:2] == ['gmail', 'search']], [])
        self.assertEqual(self.calls('imsg'), [])

    def test_polling_failure_tells_the_user_once_per_distinct_error(self):
        self.contacted()
        self.state = {'google_error': 'HttpError 503'}
        self.save_state()
        self.assertFalse(self.gate()[0])
        self.assertEqual(len(self.notices()), 1)
        self.assertIn('HttpError 503', self.notices()[0][-1])
        self.assertIn('Gmail', self.notices()[0][-1])
        self.assertFalse(self.gate()[0])
        self.assertEqual(len(self.notices()), 1)
        self.state = {}
        self.save_state()
        self.assertFalse(self.gate()[0])
        self.state = {'google_error': 'HttpError 503'}
        self.save_state()
        self.gate()
        self.assertEqual(len(self.notices()), 2)


class ShippedJobTests(unittest.TestCase):
    def test_a_contact_form_checks_that_no_account_setup_uses_the_users_browser_before_its_first_browser_action(self):
        skill = (ROOT / 'profile' / 'skills' / 'contact' / 'SKILL.md').read_text(encoding='utf-8')
        forms = skill[skill.index('### Contact forms'):skill.index('### Replies')]
        first_step = forms[forms.index('\n1. '):forms.index('\n2. ')]
        self.assertLess(first_step.index('connections.py" browser status'), first_step.index('open the form'))
        self.assertIn('stop for this listing without `fail` or `done`', first_step)

    def test_job_runs_the_shipped_gate_with_the_contact_skill_and_delivers_nothing(self):
        jobs = json.loads((ROOT / 'profile' / 'cron' / 'jobs.json').read_text(encoding='utf-8'))['jobs']
        [job] = [job for job in jobs if job['id'] == 'apt-scraper-contact']
        self.assertTrue((ROOT / 'profile' / 'scripts' / job['script']).is_file())
        self.assertEqual(job['skills'], ['contact'])
        self.assertTrue((ROOT / 'profile' / 'skills' / 'contact' / 'SKILL.md').is_file())
        self.assertEqual(job['deliver'], 'local')
        for installer in ('install.ps1', 'install.sh'):
            self.assertIn("cron', 'resume', 'apt-scraper-contact'" if installer.endswith('ps1') else 'cron resume apt-scraper-contact',
                          (ROOT / installer).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
